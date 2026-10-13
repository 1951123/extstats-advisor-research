"""Own one disposable PostgreSQL database for an isolated experiment.

This module owns only the database lifecycle.  It deliberately does not know
how an Advisor snapshot, native repository, or planner configuration is
prepared.  A caller passes :attr:`database_dsn` to those existing APIs and
keeps their connections inside this context.

No connection is opened at import time.  Live access starts only when the
context is entered.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Self

DEFAULT_SAMPLE_ROWS = 30_000
_DATABASE_NAME = re.compile(r"^exp_[a-z0-9][a-z0-9_]{0,61}$")
_PROTECTED_DATABASES = frozenset(
    {"postgres", "template0", "template1", "extstats_lab", "extstats_stock"}
)


class ExperimentDatabaseError(RuntimeError):
    """Raised when an experiment database cannot be safely managed."""


class ExperimentDatabaseCleanupError(ExperimentDatabaseError):
    """Raised when owned resources remain after cleanup was attempted."""

    def __init__(self, database_name: str, errors: list[BaseException]) -> None:
        self.database_name = database_name
        self.errors = tuple(errors)
        detail = "; ".join(str(error) for error in errors)
        super().__init__(
            f"could not safely clean up experiment database {database_name!r}; "
            f"the database may remain: {detail}"
        )


def _psycopg() -> Any:
    import psycopg

    return psycopg


def _validate_admin_dsn(admin_dsn: str) -> dict[str, str]:
    if not isinstance(admin_dsn, str) or not admin_dsn.strip():
        raise ExperimentDatabaseError("an explicit administrative DSN is required")
    try:
        from psycopg.conninfo import conninfo_to_dict

        fields = conninfo_to_dict(admin_dsn)
    except Exception as exc:  # pragma: no cover - parser-specific failure
        raise ExperimentDatabaseError(
            "administrative DSN is not valid psycopg connection info"
        ) from exc
    required = ("host", "port", "dbname")
    if any(not fields.get(key) for key in required):
        raise ExperimentDatabaseError("administrative DSN must include host, port, and dbname")
    try:
        port = int(fields["port"])
    except (TypeError, ValueError) as exc:
        raise ExperimentDatabaseError("administrative DSN has an invalid port") from exc
    if not 1 <= port <= 65535:
        raise ExperimentDatabaseError("administrative DSN has an invalid port")
    return {str(key): str(value) for key, value in fields.items()}


def _make_conninfo(admin_dsn: str, database_name: str) -> str:
    from psycopg.conninfo import make_conninfo

    fields = _validate_admin_dsn(admin_dsn)
    fields["dbname"] = database_name
    return make_conninfo(**fields)


def _validate_database_name(name: str) -> str:
    if isinstance(name, str) and name in _PROTECTED_DATABASES:
        raise ExperimentDatabaseError(f"database {name!r} is protected")
    if not isinstance(name, str) or _DATABASE_NAME.fullmatch(name) is None:
        raise ExperimentDatabaseError(
            "experiment database name must match the explicit exp_<lowercase-name> namespace"
        )
    return name


def _quote_identifier(name: str) -> str:
    # Names are validated to lowercase ASCII identifiers before reaching SQL.
    return '"' + name + '"'


def _fetchone(connection: Any, query: str, params: tuple[Any, ...] = ()) -> Any:
    return connection.execute(query, params).fetchone()


def _connect(dsn: str, application_name: str) -> Any:
    return _psycopg().connect(dsn, autocommit=True, application_name=application_name)


@dataclass(frozen=True, slots=True)
class ExperimentDatabaseSpec:
    """Runtime-only lifecycle inputs.

    ``sample_rows`` is metadata for the experiment's immutable sample input;
    this lifecycle never materializes or changes that sample.
    """

    admin_dsn: str
    database_name: str | None = None
    name_prefix: str = "exp_foundations"
    sample_rows: int = DEFAULT_SAMPLE_ROWS

    def __post_init__(self) -> None:
        _validate_admin_dsn(self.admin_dsn)
        if self.database_name is not None:
            _validate_database_name(self.database_name)
        if not isinstance(self.name_prefix, str) or not re.fullmatch(
            r"exp_[a-z0-9][a-z0-9_]{0,48}", self.name_prefix
        ):
            raise ExperimentDatabaseError("name_prefix must be an exp_ lowercase namespace")
        if (
            isinstance(self.sample_rows, bool)
            or not isinstance(self.sample_rows, int)
            or self.sample_rows < 1
        ):
            raise ExperimentDatabaseError("sample_rows must be a positive integer")


class ExperimentDatabase:
    """Create and own one database for exactly one experiment invocation."""

    def __init__(
        self,
        spec: ExperimentDatabaseSpec,
        *,
        connect_factory: Callable[[str, str], Any] | None = None,
    ) -> None:
        self.spec = spec
        self._connect_factory = connect_factory or _connect
        self._database_name = self._choose_name()
        self._database_dsn: str | None = None
        self._owner: str | None = None
        self._created = False
        self._cleaned = False
        self._connections: set[Any] = set()

    def _choose_name(self) -> str:
        if self.spec.database_name is not None:
            return _validate_database_name(self.spec.database_name)
        suffix = uuid.uuid4().hex[:16]
        return _validate_database_name(f"{self.spec.name_prefix}_{suffix}")

    @property
    def database_name(self) -> str:
        return self._database_name

    @property
    def database_dsn(self) -> str:
        if not self._created or self._database_dsn is None:
            raise ExperimentDatabaseError("experiment database is not available")
        return self._database_dsn

    @property
    def sample_rows(self) -> int:
        return self.spec.sample_rows

    def __enter__(self) -> Self:
        if self._created or self._cleaned:
            raise ExperimentDatabaseError("an experiment database lifecycle is single-use")
        try:
            self._create()
        except BaseException as experiment_error:
            cleanup_error = self._cleanup_after_partial_create()
            if cleanup_error is not None:
                raise BaseExceptionGroup(
                    "experiment database creation and cleanup both failed",
                    [experiment_error, cleanup_error],
                ) from cleanup_error
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        cleanup_error: Exception | None = None
        try:
            self.cleanup()
        except Exception as error:  # noqa: BLE001 - preserve arbitrary cleanup failures
            cleanup_error = error
        if cleanup_error is not None and exc_value is not None:
            raise BaseExceptionGroup(
                "experiment execution and database cleanup both failed",
                [exc_value, cleanup_error],
            ) from cleanup_error
        if cleanup_error is not None:
            raise cleanup_error
        return False

    def _create(self) -> None:
        fields = _validate_admin_dsn(self.spec.admin_dsn)
        if fields["dbname"] == self._database_name:
            raise ExperimentDatabaseError(
                "administrative connection cannot target experiment database"
            )
        with self._connect_factory(
            self.spec.admin_dsn, "extstats-research-experiment-admin"
        ) as connection:
            current_database = str(_fetchone(connection, "SELECT current_database()")[0])
            if current_database != fields["dbname"]:
                raise ExperimentDatabaseError(
                    "administrative connection database does not match its explicit DSN"
                )
            existing = _fetchone(
                connection,
                "SELECT datname FROM pg_catalog.pg_database WHERE datname = %s",
                (self._database_name,),
            )
            if existing is not None:
                raise ExperimentDatabaseError(
                    f"experiment database {self._database_name!r} already exists; refusing reuse"
                )
            self._owner = str(_fetchone(connection, "SELECT current_user")[0])
            connection.execute(
                f"CREATE DATABASE {_quote_identifier(self._database_name)} TEMPLATE template0"
            )
            self._created = True
            record = _fetchone(
                connection,
                "SELECT datname, pg_catalog.pg_get_userbyid(datdba) "
                "FROM pg_catalog.pg_database WHERE datname = %s",
                (self._database_name,),
            )
            if (
                record is None
                or str(record[0]) != self._database_name
                or str(record[1]) != self._owner
            ):
                raise ExperimentDatabaseError(
                    "created database ownership could not be established safely"
                )
        self._database_dsn = _make_conninfo(self.spec.admin_dsn, self._database_name)

    def _cleanup_after_partial_create(self) -> Exception | None:
        if not self._created:
            return None
        try:
            self.cleanup()
        except Exception as error:  # noqa: BLE001 - preserve partial-create cleanup failures
            return error
        return None

    @contextmanager
    def connection(self) -> Iterator[Any]:
        """Open a tracked experiment connection and close it before lifecycle cleanup."""

        connection = self._connect_factory(self.database_dsn, "extstats-research-experiment")
        self._connections.add(connection)
        try:
            yield connection
        finally:
            connection.close()
            self._connections.discard(connection)

    def _close_connections(self) -> list[Exception]:
        errors: list[Exception] = []
        for connection in tuple(self._connections):
            try:
                connection.close()
            except Exception as error:  # noqa: BLE001 - preserve connection-close failures
                errors.append(error)
            else:
                self._connections.discard(connection)
        return errors

    def cleanup(self) -> None:
        """Close tracked connections and drop only this invocation's database."""

        if self._cleaned:
            return
        if not self._created:
            self._cleaned = True
            return
        errors = self._close_connections()
        if errors:
            raise ExperimentDatabaseCleanupError(self._database_name, errors)
        try:
            with self._connect_factory(
                self.spec.admin_dsn, "extstats-research-experiment-cleanup"
            ) as connection:
                record = _fetchone(
                    connection,
                    "SELECT datname, pg_catalog.pg_get_userbyid(datdba) "
                    "FROM pg_catalog.pg_database WHERE datname = %s",
                    (self._database_name,),
                )
                if record is None:
                    self._created = False
                    self._cleaned = True
                    return
                if (
                    str(record[0]) != self._database_name
                    or self._owner is None
                    or str(record[1]) != self._owner
                ):
                    raise ExperimentDatabaseError(
                        "refusing cleanup because database ownership cannot be re-established"
                    )
                if self._database_name in _PROTECTED_DATABASES:
                    raise ExperimentDatabaseError(
                        f"refusing cleanup of protected database {self._database_name!r}"
                    )
                connection.execute(f"DROP DATABASE {_quote_identifier(self._database_name)}")
                remaining = _fetchone(
                    connection,
                    "SELECT datname FROM pg_catalog.pg_database WHERE datname = %s",
                    (self._database_name,),
                )
                if remaining is not None:
                    raise ExperimentDatabaseError(
                        f"database {self._database_name!r} remains after DROP DATABASE"
                    )
        except ExperimentDatabaseCleanupError:
            raise
        except Exception as error:
            raise ExperimentDatabaseCleanupError(self._database_name, [error]) from error
        self._created = False
        self._cleaned = True
