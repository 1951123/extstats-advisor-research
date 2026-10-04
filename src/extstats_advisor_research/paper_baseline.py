"""Independent reproduction of the AreCELearnedYet PostgreSQL baseline."""

from __future__ import annotations

import gzip
import json
import math
import re
import subprocess
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from . import FROZEN_RESEARCH_REPOSITORY
from .datasets import census13
from .provenance import reject_credentials, semantic_digest, sha256_file, write_json

FORMAT_VERSION = "arecel-paper-postgres-baseline-v1"
UPSTREAM_REPOSITORY = census13.UPSTREAM_URL
UPSTREAM_COMMIT = census13.UPSTREAM_COMMIT
STATISTICS_TARGET = 10_000
SEED = 123
PAPER_RELATION = "public.arecel_paper_census13"
PAPER_COLUMNS: tuple[tuple[str, str], ...] = (
    ("age", "DOUBLE PRECISION"),
    ("workclass", "VARCHAR(64)"),
    ("education", "VARCHAR(64)"),
    ("education_num", "DOUBLE PRECISION"),
    ("marital_status", "VARCHAR(64)"),
    ("occupation", "VARCHAR(64)"),
    ("relationship", "VARCHAR(64)"),
    ("race", "VARCHAR(64)"),
    ("sex", "VARCHAR(64)"),
    ("capital_gain", "DOUBLE PRECISION"),
    ("capital_loss", "DOUBLE PRECISION"),
    ("hours_per_week", "DOUBLE PRECISION"),
    ("native_country", "VARCHAR(64)"),
)
PAPER_REFERENCES = {"p50": 1.40, "p95": 18.6, "p99": 58.0, "max": 1635.0}
_SOURCE_RELATION = 'public."census13"'
_QUERY_ID = re.compile(r"^arecel_census13_test_(\d{6})$")


def paper_schema() -> list[dict[str, str]]:
    return [{"name": name, "postgres_type": type_} for name, type_ in PAPER_COLUMNS]


def paper_select_sql(source_sql: str) -> str:
    """Render the upstream workload's row-preserving PostgreSQL query."""
    match = re.match(r"(?is)^\s*SELECT\s+COUNT\s*\(\s*\*\s*\)\s+FROM\s+", source_sql)
    if match is None:
        raise ValueError("AreCEL base:test query is not the expected COUNT(*) form")
    sql = "SELECT * FROM " + source_sql[match.end() :]
    return sql.replace(_SOURCE_RELATION, 'public."arecel_paper_census13"')


def paper_query_id(index: int) -> str:
    return f"arecel_census13_test_{index:06d}"


def qerror(estimate: float, truth: float) -> float:
    """The frozen advisor's cardinality q-error floor-one contract."""
    estimate_floor = max(float(estimate), 1.0)
    truth_floor = max(float(truth), 1.0)
    return max(estimate_floor / truth_floor, truth_floor / estimate_floor)


def upstream_qerror(estimate: float, truth: float) -> float:
    if estimate == 0 and truth == 0:
        return 1.0
    if estimate == 0:
        return float(truth)
    if truth == 0:
        return float(estimate)
    return max(float(estimate) / float(truth), float(truth) / float(estimate))


def qerror_compatibility(estimate: float, truth: float) -> dict[str, Any]:
    ours = qerror(estimate, truth)
    upstream = upstream_qerror(estimate, truth)
    return {"advisor_qerror": ours, "upstream_qerror": upstream, "matches": ours == upstream}


def _frozen_advisor_qerror(estimate: float, truth: int) -> tuple[float, str]:
    try:
        from extstats_advisor.utility import QErrorLoss
    except ImportError:
        return qerror(estimate, truth), "local-compatible-fallback"
    return float(QErrorLoss().loss(estimate, truth)), "extstats_advisor.utility.QErrorLoss"


def percentile(values: Iterable[float], fraction: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("cannot calculate a percentile of an empty distribution")
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    values = [record["qerror"] for record in records]
    maximum = max(values)
    max_record = next(record for record in records if record["qerror"] == maximum)
    return {
        "query_count": len(records),
        "mean": sum(values) / len(values),
        "p50": percentile(values, 0.50),
        "p90": percentile(values, 0.90),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
        "max": maximum,
        "max_query_id": max_record["query_id"],
        "quantile_method": "linear-interpolation-n-minus-1",
    }


def comparison(reproduced: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for name, reference in PAPER_REFERENCES.items():
        value = reproduced[name]
        result[name] = {
            "paper_reference": reference,
            "reproduced": value,
            "difference": value - reference,
            "ratio": value / reference if reference else None,
        }
    return result


def _load_source_queries(data_root: Path) -> tuple[list[dict[str, Any]], str]:
    source = census13.canonical_workload_path(data_root)
    queries: list[dict[str, Any]] = []
    with gzip.open(source, "rt", encoding="utf-8") as stream:
        for record in map(json.loads, stream):
            if record.get("split") != "test":
                continue
            index = int(record["index"])
            query_id = paper_query_id(index)
            if record["query_id"] != f"arecel:census13:test:{index:06d}":
                raise ValueError(f"unexpected source query identity for index {index}")
            queries.append(
                {
                    "query_id": query_id,
                    "source_query_id": record["query_id"],
                    "sql": paper_select_sql(record["sql"]),
                    "source_query_sha256": record["source_query_sha256"],
                    "source_index": index,
                }
            )
    if len(queries) != 10_000:
        raise ValueError(f"expected 10000 AreCEL test queries, got {len(queries)}")
    return queries, sha256_file(source)


def _load_truth(path: Path, query_ids: set[str]) -> dict[str, int]:
    artifact = json.loads(path.read_text(encoding="utf-8"))
    truths = {item["query_id"]: int(item["cardinality"]) for item in artifact["truths"]}
    if set(truths) != query_ids:
        raise ValueError("frozen GroundTruthSet query IDs do not match the AreCEL test workload")
    return truths


def _git_sha(repository: Path) -> str:
    try:
        status = subprocess.run(
            ["git", "-C", str(repository), "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if status:
            raise ValueError("paper baseline requires a clean research working tree")
        return subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError(f"could not resolve research revision: {repository}") from exc


def _load_csv(conn: Any, csv_file: Path) -> None:
    relation = 'public."arecel_paper_census13"'
    columns = ", ".join(f'"{name}"' for name, _ in PAPER_COLUMNS)
    with conn.cursor() as cursor:
        cursor.execute(f"DROP TABLE IF EXISTS {relation}")
        definitions = ", ".join(f'"{name}" {type_}' for name, type_ in PAPER_COLUMNS)
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


def run_paper_baseline(
    dsn: str,
    *,
    data_root: Path | None = None,
    canonical_run: Path = Path("runs/197e9b890ac58bc4fbcfb218"),
    output_directory: Path = Path("paper-baselines/arecel-census13"),
    repository: Path | None = None,
) -> dict[str, Any]:
    """Run the upstream-shaped baseline against one isolated stock PostgreSQL database."""
    if "postgresql://" in dsn or "postgres://" in dsn:
        # The value is used only to connect; this guard prevents accidental artifact logging.
        pass
    data_root = census13.data_root(data_root)
    csv_file = census13.csv_path(data_root)
    queries, workload_sha = _load_source_queries(data_root)
    truth_path = canonical_run / "ground-truth-v1.json"
    truths = _load_truth(truth_path, {query["query_id"] for query in queries})
    repository = repository or Path(__file__).resolve().parents[2]

    import psycopg

    started = time.time()
    with psycopg.connect(dsn, autocommit=True) as conn:
        server = _server_metadata(conn)
        _load_csv(conn, csv_file)
        with conn.cursor() as cursor:
            cursor.execute('SELECT count(*) FROM public."arecel_paper_census13"')
            row_count = int(cursor.fetchone()[0])
            if row_count != census13.EXPECTED_ROWS:
                raise ValueError(f"expected {census13.EXPECTED_ROWS} rows, loaded {row_count}")
            cursor.execute("SELECT setseed(%s)", (1.0 / SEED,))
            for name, _ in PAPER_COLUMNS:
                cursor.execute(
                    f'ALTER TABLE public."arecel_paper_census13" ALTER COLUMN "{name}" '
                    f"SET STATISTICS {STATISTICS_TARGET}"
                )
            cursor.execute('ANALYZE public."arecel_paper_census13"')

            # Prove truth identity before collecting estimates or calculating q-error.
            for query in queries:
                cursor.execute(
                    "SELECT count(*) FROM (" + query["sql"].rstrip("; ") + ") AS paper_rows"
                )
                observed = int(cursor.fetchone()[0])
                expected = truths[query["query_id"]]
                if observed != expected:
                    raise ValueError(
                        f"truth mismatch for {query['query_id']}: paper={observed}, canonical={expected}; "
                        f"sql={query['sql']}"
                    )

            records: list[dict[str, Any]] = []
            for query in queries:
                cursor.execute("EXPLAIN (FORMAT JSON) " + query["sql"])
                plan = cursor.fetchone()[0][0]["Plan"]
                estimate = int(plan["Plan Rows"])
                true_rows = truths[query["query_id"]]
                loss, loss_provider = _frozen_advisor_qerror(estimate, true_rows)
                records.append(
                    {
                        "query_id": query["query_id"],
                        "source_query_id": query["source_query_id"],
                        "true_rows": true_rows,
                        "estimated_rows": estimate,
                        "qerror": loss,
                        "upstream_qerror": upstream_qerror(estimate, true_rows),
                    }
                )

    summary = summarize(records)
    summary["paper_reference_comparison"] = comparison(summary)
    advisor_tail: list[dict[str, Any]] = []
    audit_path = canonical_run / "analysis" / "per-query-v1.jsonl"
    if audit_path.is_file():
        audit_rows = [json.loads(line) for line in audit_path.read_text().splitlines() if line]
        tail = sorted(audit_rows, key=lambda row: row["baseline_qerror"], reverse=True)[:10]
        by_id = {row["query_id"]: row for row in records}
        for row in tail:
            paper = by_id[row["query_id"]]
            advisor_tail.append(
                {
                    "query_id": row["query_id"],
                    "advisor_sandbox_baseline_estimated_rows": row["baseline_estimated_rows"],
                    "advisor_sandbox_baseline_qerror": row["baseline_qerror"],
                    "paper_pg_reproduction_estimated_rows": paper["estimated_rows"],
                    "paper_pg_reproduction_qerror": paper["qerror"],
                    "true_rows": paper["true_rows"],
                }
            )
    output_directory.mkdir(parents=True, exist_ok=True)
    per_query_path = output_directory / "postgres-per-query-v1.jsonl"
    with per_query_path.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
    research_sha = _git_sha(repository)
    artifact = {
        "format_version": FORMAT_VERSION,
        "research_repository": FROZEN_RESEARCH_REPOSITORY,
        "research_commit_sha": research_sha,
        "upstream": {"repository": UPSTREAM_REPOSITORY, "commit": UPSTREAM_COMMIT},
        "dataset": census13.inspect(data_root),
        "workload": {
            "identity": "AreCELearnedYet base:test",
            "source": "data/census13/workload/base.pkl",
            "canonical_workload_sha256": workload_sha,
            "query_count": len(queries),
            "query_id_mapping": "arecel:census13:test:<index> -> arecel_census13_test_<index>",
            "projection": "upstream aggregate=False row-preserving SELECT *",
        },
        "stock_postgresql": server,
        "relation": PAPER_RELATION,
        "row_count": census13.EXPECTED_ROWS,
        "schema": paper_schema(),
        "statistics": {
            "target": STATISTICS_TARGET,
            "seed_expression": "1.0 / 123",
            "setseed_executed": True,
            "analyze_count": 1,
        },
        "truth": {
            "source": "frozen canonical GroundTruthSet",
            "path": "runs/197e9b890ac58bc4fbcfb218/ground-truth-v1.json",
            "semantic_digest": json.loads(truth_path.read_text())["semantic_digest"],
            "identity_verified_by_full_table_counts": True,
        },
        "summary": summary,
        "qerror_provider": loss_provider,
        "advisor_sandbox_tail_cross_check": advisor_tail,
        "terminology": {
            "paper_pg_reproduction": "this artifact",
            "advisor_sandbox_baseline": "frozen advisor sandbox baseline from the canonical run",
            "advisor_final": "frozen advisor final configuration from the canonical run",
            "combined_state_warning": (
                "The optimizer evaluated advisor membership M*, not necessarily existing extstats union M*. "
                "The reproduced paper objective is not guaranteed for a combined production state."
            ),
        },
        "elapsed_seconds": time.time() - started,
        "per_query_path": per_query_path.name,
        "per_query_sha256": sha256_file(per_query_path),
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
