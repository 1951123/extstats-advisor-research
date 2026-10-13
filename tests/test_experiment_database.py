from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Self

import pytest

from extstats_advisor_research.postgres.experiment_database import (
    DEFAULT_SAMPLE_ROWS,
    ExperimentDatabase,
    ExperimentDatabaseCleanupError,
    ExperimentDatabaseError,
    ExperimentDatabaseSpec,
)

ADMIN_DSN = "host=127.0.0.1 port=55433 dbname=postgres user=research"


class _Result:
    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self._row = row

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._row


@dataclass
class _Cluster:
    database: str = "exp_foundations_test"
    owner: str = "research"
    present: bool = False
    fail_create: bool = False
    fail_drop: bool = False

    def __post_init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.connections: list[_Connection] = []

    def connect(self, dsn: str, application_name: str) -> _Connection:
        connection = _Connection(self, dsn, application_name)
        self.connections.append(connection)
        return connection


class _Connection:
    def __init__(self, cluster: _Cluster, dsn: str, application_name: str) -> None:
        self.cluster = cluster
        self.dsn = dsn
        self.application_name = application_name
        self.closed = False

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> _Result:
        self.cluster.calls.append((self.application_name, query))
        normalized = " ".join(query.split()).lower()
        if normalized == "select current_database()":
            return _Result(("postgres",))
        if normalized == "select current_user":
            return _Result((self.cluster.owner,))
        if "pg_get_userbyid" in normalized:
            return _Result(
                (self.cluster.database, self.cluster.owner) if self.cluster.present else None
            )
        if normalized.startswith("select datname from pg_catalog.pg_database"):
            return _Result((self.cluster.database,) if self.cluster.present else None)
        if normalized.startswith("create database"):
            if self.cluster.fail_create:
                raise RuntimeError("create failed")
            self.cluster.present = True
            return _Result(None)
        if normalized.startswith("drop database"):
            if self.cluster.fail_drop:
                raise RuntimeError("drop failed")
            self.cluster.present = False
            return _Result(None)
        raise AssertionError(f"unexpected SQL: {query!r}")

    def close(self) -> None:
        self.closed = True


def _spec(**kwargs: Any) -> ExperimentDatabaseSpec:
    return ExperimentDatabaseSpec(admin_dsn=ADMIN_DSN, **kwargs)


def test_create_propagates_database_dsn_and_cleans_up() -> None:
    cluster = _Cluster()
    lifecycle = ExperimentDatabase(
        _spec(database_name=cluster.database), connect_factory=cluster.connect
    )

    with lifecycle as database:
        assert database.database_name == cluster.database
        assert f"dbname={cluster.database}" in database.database_dsn
        with database.connection() as connection:
            assert connection.application_name == "extstats-research-experiment"
            assert not connection.closed
        assert connection.closed

    assert not cluster.present
    assert any(
        query.startswith('CREATE DATABASE "exp_foundations_test"') for _, query in cluster.calls
    )
    assert any(
        query.startswith('DROP DATABASE "exp_foundations_test"') for _, query in cluster.calls
    )
    assert all("FORCE" not in query and "IF EXISTS" not in query for _, query in cluster.calls)


def test_exception_during_experiment_still_cleans_up() -> None:
    cluster = _Cluster()
    lifecycle = ExperimentDatabase(
        _spec(database_name=cluster.database), connect_factory=cluster.connect
    )

    with pytest.raises(ValueError, match="experiment failed"), lifecycle:
        raise ValueError("experiment failed")

    assert not cluster.present


def test_creation_failure_does_not_attempt_unowned_drop() -> None:
    cluster = _Cluster(fail_create=True)
    lifecycle = ExperimentDatabase(
        _spec(database_name=cluster.database), connect_factory=cluster.connect
    )

    with pytest.raises(RuntimeError, match="create failed"), lifecycle:
        pass

    assert not any(query.startswith("DROP DATABASE") for _, query in cluster.calls)


def test_existing_database_is_rejected_without_reuse_or_drop() -> None:
    cluster = _Cluster(present=True)
    lifecycle = ExperimentDatabase(
        _spec(database_name=cluster.database), connect_factory=cluster.connect
    )

    with pytest.raises(ExperimentDatabaseError, match="already exists"), lifecycle:
        pass

    assert not any(query.startswith("CREATE DATABASE") for _, query in cluster.calls)
    assert not any(query.startswith("DROP DATABASE") for _, query in cluster.calls)


def test_cleanup_refuses_database_when_ownership_no_longer_matches() -> None:
    cluster = _Cluster()
    lifecycle = ExperimentDatabase(
        _spec(database_name=cluster.database), connect_factory=cluster.connect
    )

    lifecycle.__enter__()
    lifecycle._owner = "different-owner"
    with pytest.raises(ExperimentDatabaseCleanupError, match="could not safely clean up"):
        lifecycle.cleanup()

    assert cluster.present
    assert not any(query.startswith("DROP DATABASE") for _, query in cluster.calls)


def test_cleanup_failure_preserves_original_execution_error() -> None:
    cluster = _Cluster(fail_drop=True)
    lifecycle = ExperimentDatabase(
        _spec(database_name=cluster.database), connect_factory=cluster.connect
    )

    with pytest.raises(BaseExceptionGroup) as caught, lifecycle:
        raise ValueError("original failure")

    assert any(isinstance(error, ValueError) for error in caught.value.exceptions)
    assert any(
        isinstance(error, ExperimentDatabaseCleanupError) for error in caught.value.exceptions
    )
    assert cluster.present


def test_sample_size_defaults_and_allows_explicit_override() -> None:
    assert _spec().sample_rows == DEFAULT_SAMPLE_ROWS == 30_000
    assert _spec(sample_rows=12).sample_rows == 12
    with pytest.raises(ExperimentDatabaseError, match="positive integer"):
        _spec(sample_rows=0)


def test_protected_database_names_are_rejected() -> None:
    with pytest.raises(ExperimentDatabaseError, match="protected"):
        _spec(database_name="extstats_lab")
