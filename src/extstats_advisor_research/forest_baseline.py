"""Independent stock PostgreSQL reproduction for the AreCELearnedYet Forest10 baseline."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

from . import FROZEN_RESEARCH_REPOSITORY
from .datasets import forest10
from .paper_baseline import percentile
from .provenance import reject_credentials, semantic_digest, sha256_file, write_json

FORMAT_VERSION = "arecel-paper-postgres-baseline-v1"
STATISTICS_TARGET = 10_000
SEED = 123
QERROR_CONTRACT = "qerror-cardinality-floor-1-v1"
PAPER_REFERENCES = {
    "postgresql": {"p50": 1.21, "p95": 17.0, "p99": 71.0, "max": 9374.0},
    "mscn": {"p50": 1.14, "p95": 7.62, "p99": 20.6, "max": 377.0},
    "lw_xgb": {"p50": 1.10, "p95": 3.00, "p99": 7.00, "max": 220.0},
    "lw_nn": {"p50": 1.13, "p95": 3.10, "p99": 7.00, "max": 1370.0},
    "naru": {"p50": 1.06, "p95": 3.30, "p99": 9.00, "max": 153.0},
    "deepdb": {"p50": 1.06, "p95": 5.00, "p99": 14.00, "max": 1293.0},
}


def paper_references() -> dict[str, dict[str, float]]:
    return {name: dict(values) for name, values in PAPER_REFERENCES.items()}


def compare_paper_postgres(reproduced: dict[str, Any]) -> dict[str, Any]:
    reference = PAPER_REFERENCES["postgresql"]
    return {
        metric: {
            "paper_reference": expected,
            "reproduced": reproduced[metric],
            "difference": reproduced[metric] - expected,
            "ratio": reproduced[metric] / expected if expected else None,
        }
        for metric, expected in reference.items()
    }


def _git_sha(repository: Path) -> str:
    status = subprocess.run(
        ["git", "-C", str(repository), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if status:
        raise ValueError("Forest paper baseline requires a clean research working tree")
    return subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _load_csv(conn: Any, csv_file: Path) -> None:
    relation = forest10.RELATION
    columns = ", ".join(f'"{name}"' for name, _ in forest10.COLUMNS)
    definitions = ", ".join(f'"{name}" {type_}' for name, type_ in forest10.COLUMNS)
    with conn.cursor() as cursor:
        cursor.execute(f"DROP TABLE IF EXISTS {relation}")
        cursor.execute(f"CREATE TABLE {relation} ({definitions})")
        with (
            cursor.copy(
                f"COPY {relation} ({columns}) FROM STDIN WITH (FORMAT csv, HEADER true)"
            ) as copy,
            csv_file.open("rb") as stream,
        ):
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                copy.write(block)


def _server_metadata(conn: Any) -> dict[str, Any]:
    with conn.cursor() as cursor:
        cursor.execute("SELECT version(), current_setting('server_version_num')")
        version, version_num = cursor.fetchone()
    return {"server_version": version.split(",", 1)[0], "server_version_num": int(version_num)}


def _physical_schema(conn: Any) -> dict[str, Any]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT a.attname, pg_catalog.format_type(a.atttypid, a.atttypmod), a.attnotnull
            FROM pg_catalog.pg_attribute AS a
            WHERE a.attrelid = 'public.forest10'::pg_catalog.regclass
              AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY a.attnum
            """
        )
        columns = [
            {"name": name, "postgres_type": postgres_type, "not_null": not_null}
            for name, postgres_type, not_null in cursor.fetchall()
        ]
        cursor.execute("SELECT count(*) FROM public.forest10")
        row_count = int(cursor.fetchone()[0])
        cursor.execute(
            """
            SELECT count(*)
            FROM pg_catalog.pg_statistic_ext AS s
            WHERE s.stxrelid = 'public.forest10'::pg_catalog.regclass
            """
        )
        extended_statistics_count = int(cursor.fetchone()[0])
    expected = [
        {"name": name, "postgres_type": type_, "not_null": False}
        for name, type_ in forest10.COLUMNS
    ]
    if columns != expected:
        raise ValueError(f"Forest10 physical schema mismatch: {columns!r}")
    if row_count != forest10.EXPECTED_ROWS:
        raise ValueError(f"Forest10 row count mismatch: {row_count}")
    if extended_statistics_count != 0:
        raise ValueError("Forest10 reproduction relation unexpectedly has extended statistics")
    return {
        "row_count": row_count,
        "column_count": len(columns),
        "columns": columns,
        "extended_statistics_count": extended_statistics_count,
        "verified": True,
    }


def _statistics_target(conn: Any) -> list[dict[str, Any]]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT a.attname, a.attstattarget
            FROM pg_catalog.pg_attribute AS a
            WHERE a.attrelid = 'public.forest10'::pg_catalog.regclass
              AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY a.attnum
            """
        )
        result = [{"name": name, "target": target} for name, target in cursor.fetchall()]
    if any(item["target"] != STATISTICS_TARGET for item in result):
        raise ValueError(f"Forest10 statistics target mismatch: {result!r}")
    return result


def representative_indices(records: list[dict[str, Any]]) -> list[int]:
    """Select deterministic checks covering order, arity, size, and operators."""
    selected = {0, len(records) // 2, len(records) - 1}
    by_arity: dict[int, int] = {}
    by_operator: dict[str, int] = {}
    for record in records:
        predicates = record["source_query"]["predicates"]
        by_arity.setdefault(len(predicates), record["source_index"])
        for predicate in predicates:
            by_operator.setdefault(predicate["operator"], record["source_index"])
    selected.update(by_arity.values())
    selected.update(by_operator.values())
    selected.add(min(records, key=lambda record: record["truth"])["source_index"])
    selected.add(max(records, key=lambda record: record["truth"])["source_index"])
    return sorted(selected)


def _spot_check(conn: Any, records: list[dict[str, Any]]) -> dict[str, Any]:
    indices = representative_indices(records)
    by_index = {record["source_index"]: record for record in records}
    checks = []
    with conn.cursor() as cursor:
        for index in indices:
            record = by_index[index]
            cursor.execute(
                "SELECT count(*) FROM (" + record["sql"].rstrip("; ") + ") AS truth_check"
            )
            observed = int(cursor.fetchone()[0])
            if observed != record["truth"]:
                raise ValueError(
                    f"Forest10 truth mismatch at {record['source_index']}: "
                    f"observed={observed}, source={record['truth']}"
                )
            checks.append(
                {
                    "source_index": index,
                    "query_id": f"arecel_forest10_test_{index:06d}",
                    "source_query_id": record["source_query_id"],
                    "truth": record["truth"],
                    "observed": observed,
                    "predicate_arity": len(record["source_query"]["predicates"]),
                    "operators": sorted(
                        {p["operator"] for p in record["source_query"]["predicates"]}
                    ),
                }
            )
    return {
        "rule": "indices {0, floor(query_count/2), query_count-1}, first query per predicate arity/operator, plus min/max truth",
        "count": len(checks),
        "all_match": True,
        "checks": checks,
    }


def _summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    values = [record["qerror"] for record in records]
    return {
        "query_count": len(records),
        "mean": sum(values) / len(values),
        "weighted_objective": sum(record["weight"] * record["qerror"] for record in records)
        / sum(record["weight"] for record in records),
        "p50": percentile(values, 0.50),
        "p75": percentile(values, 0.75),
        "p90": percentile(values, 0.90),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
        "max": max(values),
        "max_query_id": max(records, key=lambda record: record["qerror"])["query_id"],
        "quantile_method": "linear-interpolation-n-minus-1",
    }


def run_forest_baseline(
    dsn: str,
    *,
    data_root: Path | None = None,
    output_directory: Path = Path("paper-baselines/arecel-forest10"),
    repository: Path | None = None,
) -> dict[str, Any]:
    if not dsn.strip():
        raise ValueError("stock PostgreSQL DSN is required")
    output_directory = output_directory.expanduser().resolve()
    if output_directory.exists() and any(output_directory.iterdir()):
        raise FileExistsError(f"Forest baseline output already exists: {output_directory}")
    repository = repository or Path(__file__).resolve().parents[2]
    research_sha = _git_sha(repository)
    dataset = forest10.inspect(data_root)
    records = forest10.load_test_records(data_root)

    from extstats_advisor.utility import QErrorLoss

    loss = QErrorLoss()
    started = time.time()
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        server = _server_metadata(conn)
        _load_csv(conn, forest10.csv_path(data_root))
        physical = _physical_schema(conn)
        spot_checks = _spot_check(conn, records)
        with conn.cursor() as cursor:
            cursor.execute("SELECT setseed(%s)", (1.0 / SEED,))
            for name, _ in forest10.COLUMNS:
                cursor.execute(
                    f'ALTER TABLE {forest10.RELATION} ALTER COLUMN "{name}" SET STATISTICS {STATISTICS_TARGET}'
                )
            cursor.execute(f"ANALYZE {forest10.RELATION}")
        statistics_targets = _statistics_target(conn)

        measured: list[dict[str, Any]] = []
        with conn.cursor() as cursor:
            for record in records:
                cursor.execute("EXPLAIN (FORMAT JSON) " + record["sql"])
                explain = cursor.fetchone()[0]
                if isinstance(explain, str):
                    explain = json.loads(explain)
                estimate = int(explain[0]["Plan"]["Plan Rows"])
                qerror = float(loss.loss(estimate, record["truth"]))
                measured.append(
                    {
                        "query_id": f"arecel_forest10_test_{record['source_index']:06d}",
                        "source_index": record["source_index"],
                        "source_query_id": record["source_query_id"],
                        "source_query_sha256": record["source_query_sha256"],
                        "original_sql": record["original_sql"],
                        "sql": record["sql"],
                        "true_rows": record["truth"],
                        "estimated_rows": estimate,
                        "qerror": qerror,
                        "weight": 1.0,
                    }
                )

    summary = _summary(measured)
    summary["paper_reference_comparison"] = compare_paper_postgres(summary)
    output_directory.mkdir(parents=True, exist_ok=True)
    per_query_path = output_directory / "postgres-per-query-v1.jsonl"
    with per_query_path.open("w", encoding="utf-8") as stream:
        for record in measured:
            stream.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
    artifact = {
        "format_version": FORMAT_VERSION,
        "research_repository": FROZEN_RESEARCH_REPOSITORY,
        "research_commit_sha": research_sha,
        "upstream": {
            "repository": forest10.UPSTREAM_URL,
            "commit": forest10.UPSTREAM_COMMIT,
            "shared_archive_sha256": forest10.ARCHIVE_SHA256,
        },
        "dataset": dataset,
        "source_files": dataset["source_file_sha256"],
        "dataset_content_identity": dataset["dataset_content_identity"],
        "workload": {
            "identity": "AreCELearnedYet base:test",
            "source": "data/forest10/workload/base.pkl",
            "label_source": "data/forest10/workload/base-original-label.pkl",
            "workload_sha256": dataset["source_file_sha256"]["workload_pickle"],
            "label_sha256": dataset["source_file_sha256"]["label_pickle"],
            "canonical_workload_sha256": dataset["source_file_sha256"]["canonical_workload"],
            "query_count": len(records),
            "query_id_mapping": "arecel:forest10:test:<index> -> arecel_forest10_test_<index>",
            "projection": "count-star-to-select-star-v1",
            "relation_substitution": "safe exact source relation substitution",
        },
        "stock_postgresql": server,
        "relation": forest10.RELATION,
        "row_count": forest10.EXPECTED_ROWS,
        "schema_contract_id": forest10.SCHEMA_CONTRACT_ID,
        "schema_verification": physical,
        "null_audit": dataset["null_audit"],
        "statistics": {
            "target": STATISTICS_TARGET,
            "seed": SEED,
            "seed_expression": "1.0 / 123",
            "setseed_executed": True,
            "analyze_count": 1,
            "analyze_statement": "ANALYZE public.forest10",
            "column_targets_verified": statistics_targets,
        },
        "truth": {
            "source": "frozen audited AreCEL base-original-label.pkl via canonical workload",
            "label_sha256": dataset["source_file_sha256"]["label_pickle"],
            "canonical_workload_sha256": dataset["source_file_sha256"]["canonical_workload"],
            "authoritative_for_full_workload": True,
            "full_table_count_scans_for_truth": False,
            "spot_checks": spot_checks,
        },
        "planner_estimates": {
            "explain": "EXPLAIN (FORMAT JSON)",
            "analyze": False,
            "query_count": len(measured),
            "root_field": "Plan Rows",
        },
        "qerror": {
            "provider": "extstats_advisor.utility.QErrorLoss",
            "contract": QERROR_CONTRACT,
        },
        "weights": {
            "scheme": "uniform-per-query",
            "per_query": 1.0,
            "total_weight": float(len(measured)),
            "weighted_objective": summary["weighted_objective"],
        },
        "summary": summary,
        "paper_references": paper_references(),
        "per_query_path": per_query_path.name,
        "per_query_sha256": sha256_file(per_query_path),
        "elapsed_seconds": time.time() - started,
    }
    reject_credentials(artifact)
    artifact["semantic_digest"] = semantic_digest(artifact)
    write_json(output_directory / "postgres-v1.json", artifact)
    return {
        "artifact": str(output_directory / "postgres-v1.json"),
        "per_query": str(per_query_path),
        "semantic_digest": artifact["semantic_digest"],
        "summary": summary,
    }
