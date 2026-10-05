"""Load the audited dataset into a disposable stock PostgreSQL database."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..datasets import census13, forest10, power7


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


def _forest_relation_stats(connection: Any) -> list[tuple[str, str]]:
    rows = connection.execute(
        """
        SELECT n.nspname, s.stxname
        FROM pg_catalog.pg_statistic_ext AS s
        JOIN pg_catalog.pg_namespace AS n ON n.oid = s.stxnamespace
        JOIN pg_catalog.pg_class AS c ON c.oid = s.stxrelid
        WHERE n.nspname = 'public' AND c.relname = 'forest10'
        ORDER BY s.stxname
        """
    ).fetchall()
    return [(str(schema), str(name)) for schema, name in rows]


def _forest_physical_schema(connection: Any) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT a.attnum, a.attname, pg_catalog.format_type(a.atttypid, a.atttypmod), a.attnotnull
        FROM pg_catalog.pg_attribute AS a
        JOIN pg_catalog.pg_class AS c ON c.oid = a.attrelid
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relname = 'forest10'
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


def validate_forest_physical_schema(
    observed: list[dict[str, Any]], *, row_count: int, extended_statistics_count: int
) -> dict[str, Any]:
    expected = [
        {"name": name, "postgres_type": type_, "not_null": False}
        for name, type_ in forest10.COLUMNS
    ]
    normalized = [
        {**column, "postgres_type": _canonical_type(column["postgres_type"])} for column in observed
    ]
    if normalized != expected:
        raise ValueError(
            "public.forest10 physical schema does not match "
            f"{forest10.SCHEMA_CONTRACT_ID}: observed={normalized!r}, expected={expected!r}"
        )
    if row_count != forest10.EXPECTED_ROWS:
        raise ValueError(
            f"loaded forest10 row count {row_count}, expected {forest10.EXPECTED_ROWS}"
        )
    if extended_statistics_count != 0:
        raise ValueError(
            "public.forest10 has unexpected extended statistics after load: "
            f"{extended_statistics_count}"
        )
    return {
        "row_count": row_count,
        "column_count": len(normalized),
        "columns": normalized,
        "extended_statistics_count": extended_statistics_count,
        "verified": True,
    }


def load_forest10(
    dsn: str,
    *,
    data_root: Path | None = None,
    reset_disposable: bool = False,
    statistics_target: int = 100,
) -> dict[str, Any]:
    """Load the audited nullable Forest10 relation into a disposable stock database."""
    if not dsn or not dsn.strip():
        raise ValueError("a DSN is required and is never written to an artifact")
    if isinstance(statistics_target, bool) or statistics_target < 1:
        raise ValueError("statistics_target must be positive")
    source = forest10.csv_path(data_root)
    metadata = forest10.inspect(data_root)
    started = time.monotonic()
    psycopg = _psycopg()
    with psycopg.connect(dsn, autocommit=True) as connection:
        exists = bool(
            connection.execute(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_catalog.pg_class AS c
                    JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public' AND c.relname = 'forest10' AND c.relkind = 'r'
                )
                """
            ).fetchone()[0]
        )
        if exists:
            if not reset_disposable:
                raise RuntimeError(
                    "public.forest10 already exists; use a disposable database or explicit "
                    "--reset-disposable"
                )
            connection.execute("DROP TABLE public.forest10 CASCADE")
        server_version = _text(connection.execute("SHOW server_version").fetchone()[0])
        server_version_num = int(connection.execute("SHOW server_version_num").fetchone()[0])
        if not 160_000 <= server_version_num < 170_000:
            raise RuntimeError(
                f"simulated production must be stock PostgreSQL 16, got {server_version}"
            )
        definitions = ", ".join(f'"{name}" {kind}' for name, kind in forest10.COLUMNS)
        connection.execute(f"CREATE TABLE {forest10.RELATION} ({definitions})")
        columns = ", ".join(f'"{name}"' for name, _ in forest10.COLUMNS)
        copy_sql = f"COPY {forest10.RELATION} ({columns}) FROM STDIN WITH (FORMAT csv, HEADER true)"
        with (
            source.open("rb") as stream,
            connection.cursor() as cursor,
            cursor.copy(copy_sql) as copy,
        ):
            while block := stream.read(1024 * 1024):
                copy.write(block)
        row_count = int(
            connection.execute(f"SELECT count(*) FROM {forest10.RELATION}").fetchone()[0]
        )
        null_counts = connection.execute(
            "SELECT "
            + ", ".join(f'count(*) FILTER (WHERE "{name}" IS NULL)' for name, _ in forest10.COLUMNS)
            + f" FROM {forest10.RELATION}"
        ).fetchone()
        expected_null_counts = [
            int(metadata["null_audit"]["source_csv_null_counts"][source_name])
            for source_name in forest10.SOURCE_COLUMNS
        ]
        observed_null_counts = [int(value) for value in null_counts]
        if observed_null_counts != expected_null_counts:
            raise ValueError(
                f"loaded forest10 NULL counts {observed_null_counts} do not match audited "
                f"counts {expected_null_counts}"
            )
        observed_schema = _forest_physical_schema(connection)
        physical = validate_forest_physical_schema(
            observed_schema,
            row_count=row_count,
            extended_statistics_count=len(_forest_relation_stats(connection)),
        )
        for name, _ in forest10.COLUMNS:
            connection.execute(
                f'ALTER TABLE {forest10.RELATION} ALTER COLUMN "{name}" '
                f"SET STATISTICS {statistics_target}"
            )
        connection.execute(f"ANALYZE {forest10.RELATION}")
        targets = connection.execute(
            """
            SELECT a.attname, a.attstattarget
            FROM pg_catalog.pg_attribute AS a
            WHERE a.attrelid = 'public.forest10'::pg_catalog.regclass
              AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY a.attnum
            """
        ).fetchall()
        statistics_targets = [{"name": name, "target": target} for name, target in targets]
        if any(item["target"] != statistics_target for item in statistics_targets):
            raise ValueError(f"Forest10 statistics target mismatch: {statistics_targets!r}")
        extended_statistics = _forest_relation_stats(connection)
        if extended_statistics:
            raise ValueError(
                "public.forest10 has unexpected extended statistics after ANALYZE: "
                f"{extended_statistics!r}"
            )
    return {
        "benchmark_id": forest10.BENCHMARK_ID,
        "relation": forest10.RELATION,
        "rows": row_count,
        "source_csv_sha256": metadata["source_file_sha256"]["csv"],
        "validated": True,
        "dsn_recorded": False,
        "schema_contract_id": forest10.SCHEMA_CONTRACT_ID,
        "physical_schema": physical,
        "null_counts": dict(zip(forest10.SOURCE_COLUMNS, observed_null_counts, strict=True)),
        "physical_extended_statistics_count": len(extended_statistics),
        "server_version": server_version,
        "server_version_num": server_version_num,
        "statistics_target": statistics_target,
        "analyze_count": 1,
        "statistics_targets": statistics_targets,
        "elapsed_seconds": round(time.monotonic() - started, 6),
    }


def load_power7(
    dsn: str,
    *,
    data_root: Path | None = None,
    reset_disposable: bool = False,
    statistics_target: int = 100,
) -> dict[str, Any]:
    """Load the audited nullable Power7 relation into stock PostgreSQL."""
    if not dsn or not dsn.strip():
        raise ValueError("a DSN is required and is never written to an artifact")
    if isinstance(statistics_target, bool) or statistics_target < 1:
        raise ValueError("statistics_target must be positive")
    source = power7.csv_path(data_root)
    metadata = power7.inspect(data_root)
    started = time.monotonic()
    psycopg = _psycopg()
    with psycopg.connect(dsn, autocommit=True) as connection:
        exists = bool(
            connection.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM pg_catalog.pg_class AS c
                    JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public' AND c.relname = 'power7' AND c.relkind = 'r'
                )
                """
            ).fetchone()[0]
        )
        if exists:
            if not reset_disposable:
                raise RuntimeError(
                    "public.power7 already exists; use a disposable database or explicit "
                    "--reset-disposable"
                )
            connection.execute("DROP TABLE public.power7 CASCADE")
        server_version = _text(connection.execute("SHOW server_version").fetchone()[0])
        server_version_num = int(connection.execute("SHOW server_version_num").fetchone()[0])
        if not 160_000 <= server_version_num < 170_000:
            raise RuntimeError(
                f"simulated production must be stock PostgreSQL 16, got {server_version}"
            )
        definitions = ", ".join(f'"{name}" {kind}' for name, kind in power7.COLUMNS)
        connection.execute(f"CREATE TABLE {power7.RELATION} ({definitions})")
        columns = ", ".join(f'"{name}"' for name, _ in power7.COLUMNS)
        copy_sql = f"COPY {power7.RELATION} ({columns}) FROM STDIN WITH (FORMAT csv, HEADER true)"
        with (
            source.open("rb") as stream,
            connection.cursor() as cursor,
            cursor.copy(copy_sql) as copy,
        ):
            while block := stream.read(1024 * 1024):
                copy.write(block)
        row_count = int(connection.execute(f"SELECT count(*) FROM {power7.RELATION}").fetchone()[0])
        null_counts = connection.execute(
            "SELECT "
            + ", ".join(f'count(*) FILTER (WHERE "{name}" IS NULL)' for name, _ in power7.COLUMNS)
            + f" FROM {power7.RELATION}"
        ).fetchone()
        observed_null_counts = [int(value) for value in null_counts]
        expected_null_counts = [
            int(metadata["null_audit"]["source_csv_null_counts"][source_name])
            for source_name in power7.SOURCE_COLUMNS
        ]
        if observed_null_counts != expected_null_counts:
            raise ValueError(
                f"loaded Power7 NULL counts {observed_null_counts} do not match audited "
                f"counts {expected_null_counts}"
            )
        observed_schema = connection.execute(
            """
            SELECT a.attname, pg_catalog.format_type(a.atttypid, a.atttypmod), a.attnotnull
            FROM pg_catalog.pg_attribute AS a
            WHERE a.attrelid = 'public.power7'::pg_catalog.regclass
              AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY a.attnum
            """
        ).fetchall()
        physical_schema = [
            {"name": name, "postgres_type": postgres_type, "not_null": bool(not_null)}
            for name, postgres_type, not_null in observed_schema
        ]
        expected_schema = [
            {"name": name, "postgres_type": type_, "not_null": False}
            for name, type_ in power7.COLUMNS
        ]
        normalized = [
            {**column, "postgres_type": _canonical_type(column["postgres_type"])}
            for column in physical_schema
        ]
        if normalized != expected_schema:
            raise ValueError(f"public.power7 physical schema mismatch: {normalized!r}")
        for name, _ in power7.COLUMNS:
            connection.execute(
                f'ALTER TABLE {power7.RELATION} ALTER COLUMN "{name}" '
                f"SET STATISTICS {statistics_target}"
            )
        connection.execute(f"ANALYZE {power7.RELATION}")
        targets = connection.execute(
            """
            SELECT a.attname, a.attstattarget
            FROM pg_catalog.pg_attribute AS a
            WHERE a.attrelid = 'public.power7'::pg_catalog.regclass
              AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY a.attnum
            """
        ).fetchall()
        statistics_targets = [{"name": name, "target": target} for name, target in targets]
        if any(item["target"] != statistics_target for item in statistics_targets):
            raise ValueError(f"Power7 statistics target mismatch: {statistics_targets!r}")
        extended_statistics_count = int(
            connection.execute(
                "SELECT count(*) FROM pg_catalog.pg_statistic_ext "
                "WHERE stxrelid = 'public.power7'::regclass"
            ).fetchone()[0]
        )
        if extended_statistics_count:
            raise ValueError("public.power7 has unexpected extended statistics after ANALYZE")
    return {
        "benchmark_id": power7.BENCHMARK_ID,
        "relation": power7.RELATION,
        "rows": row_count,
        "source_csv_sha256": metadata["source_file_sha256"]["csv"],
        "validated": True,
        "dsn_recorded": False,
        "schema_contract_id": power7.SCHEMA_CONTRACT_ID,
        "physical_schema": {"row_count": row_count, "columns": normalized, "verified": True},
        "null_counts": dict(zip(power7.SOURCE_COLUMNS, observed_null_counts, strict=True)),
        "physical_extended_statistics_count": extended_statistics_count,
        "server_version": server_version,
        "server_version_num": server_version_num,
        "statistics_target": statistics_target,
        "analyze_count": 1,
        "statistics_targets": statistics_targets,
        "elapsed_seconds": round(time.monotonic() - started, 6),
    }
