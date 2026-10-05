"""Load the audited dataset into a disposable stock PostgreSQL database."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..datasets import census13


def _psycopg() -> Any:
    import psycopg

    return psycopg


def _text(value: Any) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _sql_identifier(psycopg: Any, name: str) -> Any:
    return psycopg.sql.Identifier(name)


def _relation_stats(connection: Any) -> list[tuple[str, str]]:
    rows = connection.execute(
        """
        SELECT n.nspname, s.stxname
        FROM pg_statistic_ext AS s
        JOIN pg_namespace AS n ON n.oid = s.stxnamespace
        JOIN pg_class AS c ON c.oid = s.stxrelid
        WHERE n.nspname = 'public' AND c.relname = 'census13'
        ORDER BY s.stxname
        """
    ).fetchall()
    return [(str(schema), str(name)) for schema, name in rows]


def _physical_schema(connection: Any) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT a.attnum, a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull
        FROM pg_catalog.pg_attribute AS a
        JOIN pg_catalog.pg_class AS c ON c.oid = a.attrelid
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relname = 'census13'
          AND a.attnum > 0 AND NOT a.attisdropped
        ORDER BY a.attnum
        """
    ).fetchall()
    return [
        {
            "name": str(name),
            "postgres_type": str(postgres_type),
            "not_null": bool(not_null),
        }
        for _, name, postgres_type, not_null in rows
    ]


def _canonical_type(value: str) -> str:
    aliases = {
        "double precision": "DOUBLE PRECISION",
        "character varying(64)": "VARCHAR(64)",
    }
    return aliases.get(value.lower(), value.upper())


def _validate_physical_schema(connection: Any) -> list[dict[str, Any]]:
    observed = _physical_schema(connection)
    expected = [
        {"name": name, "postgres_type": postgres_type, "not_null": True}
        for name, postgres_type in census13.COLUMNS
    ]
    normalized = [
        {**column, "postgres_type": _canonical_type(column["postgres_type"])} for column in observed
    ]
    if normalized != expected:
        raise ValueError(
            "public.census13 physical schema does not match "
            f"{census13.SCHEMA_CONTRACT_ID}: observed={normalized!r}, expected={expected!r}"
        )
    return normalized


def _assert_or_reset(connection: Any, *, reset_disposable: bool) -> None:
    exists = bool(
        connection.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname='census13' AND c.relkind='r')"
        ).fetchone()[0]
    )
    if not exists:
        return
    stats = _relation_stats(connection)
    if not reset_disposable:
        raise RuntimeError(
            "public.census13 already exists; use a disposable database or explicit "
            "--reset-disposable (existing extended statistics are never silently reconciled)"
        )
    connection.execute("DROP TABLE public.census13 CASCADE")
    if stats:
        connection.commit()


def load_census13(
    dsn: str,
    *,
    data_root: Path | None = None,
    reset_disposable: bool = False,
    statistics_target: int = 100,
) -> dict[str, Any]:
    """Create public.census13 and validate row/content metadata.

    ``reset_disposable`` is an explicit destructive operation intended only for
    a disposable database. It is never used by canonical advisor runs.
    """
    if not dsn or not dsn.strip():
        raise ValueError("a DSN is required and is never written to an artifact")
    source = census13.csv_path(data_root)
    if isinstance(statistics_target, bool) or statistics_target < 1:
        raise ValueError("statistics_target must be positive")
    metadata = census13.inspect(data_root)
    psycopg = _psycopg()
    with psycopg.connect(dsn, autocommit=True) as connection:
        _assert_or_reset(connection, reset_disposable=reset_disposable)
        server_version = _text(connection.execute("SHOW server_version").fetchone()[0])
        server_version_num = int(connection.execute("SHOW server_version_num").fetchone()[0])
        if not 160_000 <= server_version_num < 170_000:
            raise RuntimeError(
                f"simulated production must be stock PostgreSQL 16, got {server_version}"
            )
        connection.execute("CREATE SCHEMA IF NOT EXISTS public")
        definitions = ", ".join(f'"{name}" {kind} NOT NULL' for name, kind in census13.COLUMNS)
        connection.execute(f"CREATE TABLE public.census13 ({definitions})")
        columns = ", ".join(f'"{name}"' for name, _ in census13.COLUMNS)
        copy_sql = f"COPY public.census13 ({columns}) FROM STDIN WITH (FORMAT csv, HEADER true)"
        with (
            source.open("rb") as stream,
            connection.cursor() as cursor,
            cursor.copy(copy_sql) as copy,
        ):
            while block := stream.read(1024 * 1024):
                copy.write(block)
        physical_schema = _validate_physical_schema(connection)
        for name, _ in census13.COLUMNS:
            connection.execute(
                f'ALTER TABLE public.census13 ALTER COLUMN "{name}" SET STATISTICS {statistics_target}'
            )
        connection.execute("ANALYZE public.census13")
        count = int(connection.execute("SELECT count(*) FROM public.census13").fetchone()[0])
        if count != census13.EXPECTED_ROWS:
            raise ValueError(
                f"loaded census13 row count {count}, expected {census13.EXPECTED_ROWS}"
            )
        nulls = connection.execute(
            "SELECT "
            + ", ".join(f'count(*) FILTER (WHERE "{name}" IS NULL)' for name, _ in census13.COLUMNS)
            + " FROM public.census13"
        ).fetchone()
        if any(int(value) != 0 for value in nulls):
            raise ValueError("loaded census13 contains unexpected NULL values")
        distinct = connection.execute(
            "SELECT "
            + ", ".join(f'count(DISTINCT "{name}")' for name, _ in census13.COLUMNS)
            + " FROM public.census13"
        ).fetchone()
        expected_distinct = [
            int(column["distinct_count_including_blank"])
            for column in census13._audit(data_root)["datasets"]["census13"]["csv"][
                "columns_detail"
            ]
        ]
        observed_distinct = [int(value) for value in distinct]
        if observed_distinct != expected_distinct:
            raise ValueError(
                f"loaded census13 distinct counts {observed_distinct} do not match audited "
                f"counts {expected_distinct}"
            )
        extended_statistics = _relation_stats(connection)
        if extended_statistics:
            raise ValueError(
                "public.census13 has unexpected extended statistics after load: "
                f"{extended_statistics!r}"
            )
    return {
        "benchmark_id": census13.BENCHMARK_ID,
        "relation": census13.RELATION,
        "rows": count,
        "source_csv_sha256": metadata["csv_sha256"],
        "validated": True,
        "dsn_recorded": False,
        "schema_contract_id": census13.SCHEMA_CONTRACT_ID,
        "physical_schema": physical_schema,
        "physical_extended_statistics_count": len(extended_statistics),
        "server_version": server_version,
        "server_version_num": server_version_num,
        "statistics_target": statistics_target,
        "distinct_counts_observed": observed_distinct,
    }
