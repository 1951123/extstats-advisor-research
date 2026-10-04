"""Controlled decomposition of the AreCELearnedYet baseline gap."""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from . import (
    FROZEN_ADVISOR_REPOSITORY,
    FROZEN_ADVISOR_SHA,
    FROZEN_PATCHED_POSTGRES_REPOSITORY,
    FROZEN_PATCHED_POSTGRES_SHA,
    FROZEN_RESEARCH_REPOSITORY,
)
from .datasets import census13
from .paper_baseline import (
    PAPER_COLUMNS,
    _frozen_advisor_qerror,
    _load_source_queries,
    percentile,
    qerror,
)
from .pins import verify_frozen_systems
from .provenance import reject_credentials, semantic_digest, sha256_file, write_json

FORMAT_VERSION = "arecel-census13-baseline-gap-v1"
SEED = 123
SAMPLE_RELATION_SQL = 'public."arecel_paper_census13"'
TAIL_QUERY_IDS = (
    "arecel_census13_test_005803",
    "arecel_census13_test_007775",
    "arecel_census13_test_008202",
    "arecel_census13_test_007718",
    "arecel_census13_test_007098",
    "arecel_census13_test_008766",
    "arecel_census13_test_005549",
    "arecel_census13_test_004272",
    "arecel_census13_test_002620",
    "arecel_census13_test_001940",
)


def relation_sql(name: str, schema: str = "public") -> str:
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", name) or not re.fullmatch(r"[a-z_][a-z0-9_]*", schema):
        raise ValueError("diagnostic relation identifiers must be fixed lowercase identifiers")
    return f'"{schema}"."{name}"'


def diagnostic_query_sql(paper_sql: str, relation: str) -> str:
    return paper_sql.replace(SAMPLE_RELATION_SQL, relation)


def scaled_sample_rows(sample_matches: int, sample_rows: int, population_rows: float) -> float:
    if sample_rows <= 0:
        raise ValueError("frozen sample row count must be positive")
    if sample_matches < 0 or sample_matches > sample_rows:
        raise ValueError("sample matches must be within the frozen sample")
    return float(sample_matches) / float(sample_rows) * float(population_rows)


def summary_for(records: list[dict[str, Any]], field: str) -> dict[str, float | int | str]:
    values = [float(record[field]) for record in records]
    weights = [float(record["weight"]) for record in records]
    total_weight = sum(weights)
    return {
        "mean": sum(values) / len(values),
        "weighted_objective": sum(value * weight for value, weight in zip(values, weights))
        / total_weight,
        "p50": percentile(values, 0.50),
        "p90": percentile(values, 0.90),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
        "max": max(values),
        "query_count": len(values),
        "quantile_method": "linear-interpolation-n-minus-1",
    }


def comparison_ladder(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    fields = {
        "A": "paper_target10000_qerror",
        "B": "paper_target100_qerror",
        "C": "advisor_schema_full_target100_qerror",
        "D": "advisor_schema_full_target10000_qerror",
        "E": "sample_oracle_qerror",
        "F": "advisor_sandbox_qerror",
        "G": "advisor_final_qerror",
    }
    return {
        label: {"qerror_field": field, **summary_for(records, field)}
        for label, field in fields.items()
        if all(field in record for record in records)
    }


def zero_sample_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    selected = [
        record for record in records if record["true_rows"] > 0 and record["sample_true_rows"] == 0
    ]
    denominator = sum(record["weight"] * record["advisor_sandbox_qerror"] for record in records)
    contribution = sum(record["weight"] * record["advisor_sandbox_qerror"] for record in selected)
    largest = sorted(selected, key=lambda record: record["true_rows"], reverse=True)[:10]
    return {
        "count": len(selected),
        "fraction": len(selected) / len(records),
        "baseline_objective_contribution": contribution / denominator,
        "largest_production_cardinalities": [
            {
                "query_id": record["query_id"],
                "true_rows": record["true_rows"],
                "advisor_sandbox_estimate": record["advisor_sandbox_estimate"],
                "advisor_sandbox_qerror": record["advisor_sandbox_qerror"],
            }
            for record in largest
        ],
    }


def tail_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {record["query_id"]: record for record in records}
    missing = [query_id for query_id in TAIL_QUERY_IDS if query_id not in by_id]
    if missing:
        raise ValueError(f"canonical tail queries are missing: {missing}")
    fields = (
        "query_id",
        "true_rows",
        "paper_target10000_estimate",
        "paper_target10000_qerror",
        "paper_target100_estimate",
        "paper_target100_qerror",
        "advisor_schema_full_target100_estimate",
        "advisor_schema_full_target100_qerror",
        "advisor_schema_full_target10000_estimate",
        "advisor_schema_full_target10000_qerror",
        "sample_true_rows",
        "scaled_sample_rows",
        "sample_oracle_qerror",
        "advisor_sandbox_estimate",
        "advisor_sandbox_qerror",
        "advisor_final_estimate",
        "advisor_final_qerror",
    )
    return [{field: by_id[query_id].get(field) for field in fields} for query_id in TAIL_QUERY_IDS]


def _git_sha(repository: Path) -> str:
    status = subprocess.run(
        ["git", "-C", str(repository), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if status:
        raise ValueError("baseline-gap diagnostic requires a clean research working tree")
    return subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _relation_extstats_count(connection: Any, relation: str) -> int:
    return int(
        connection.execute(
            "SELECT count(*) FROM pg_catalog.pg_statistic_ext WHERE stxrelid = %s::regclass",
            (relation,),
        ).fetchone()[0]
    )


def _load_full_relation(
    connection: Any, csv_path: Path, relation: str, columns: tuple[tuple[str, str], ...]
) -> None:
    column_sql = ", ".join(f'"{name}"' for name, _ in columns)
    definitions = ", ".join(f'"{name}" {type_}' for name, type_ in columns)
    connection.execute(f"CREATE TABLE {relation} ({definitions})")
    with (
        connection.cursor().copy(
            f"COPY {relation} ({column_sql}) FROM STDIN WITH (FORMAT csv, HEADER true)"
        ) as copy,
        csv_path.open("rb") as stream,
    ):
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            copy.write(block)


def _measure_full_relation(
    connection: Any,
    *,
    relation: str,
    relation_label: str,
    columns: tuple[tuple[str, str], ...],
    csv_path: Path,
    queries: list[dict[str, Any]],
    truths: dict[str, int],
    statistics_target: int,
) -> tuple[dict[str, int], dict[str, Any]]:
    _load_full_relation(connection, csv_path, relation, columns)
    connection.execute("SELECT setseed(%s)", (1.0 / SEED,))
    for name, _ in columns:
        connection.execute(
            f'ALTER TABLE {relation} ALTER COLUMN "{name}" SET STATISTICS {statistics_target}'
        )
    connection.execute(f"ANALYZE {relation}")
    extstats_count = _relation_extstats_count(connection, relation)
    if extstats_count != 0:
        raise ValueError(f"full-table diagnostic relation {relation} has extended statistics")
    row_count = int(connection.execute(f"SELECT count(*) FROM {relation}").fetchone()[0])
    if row_count != census13.EXPECTED_ROWS:
        raise ValueError(
            f"{relation_label} loaded {row_count} rows, expected {census13.EXPECTED_ROWS}"
        )

    exact_counts: dict[str, int] = {}
    estimates: dict[str, int] = {}
    for query in queries:
        sql = diagnostic_query_sql(query["sql"], relation)
        observed = int(
            connection.execute(
                "SELECT count(*) FROM (" + sql.rstrip("; ") + ") AS diagnostic_rows"
            ).fetchone()[0]
        )
        expected = truths[query["query_id"]]
        if observed != expected:
            raise ValueError(
                f"truth mismatch for {relation_label} {query['query_id']}: "
                f"observed={observed}, canonical={expected}, sql={sql}"
            )
        exact_counts[query["query_id"]] = observed
        plan = connection.execute("EXPLAIN (FORMAT JSON) " + sql).fetchone()[0][0]["Plan"]
        estimates[query["query_id"]] = int(plan["Plan Rows"])

    targets = connection.execute(
        "SELECT attname, attstattarget FROM pg_catalog.pg_attribute "
        "WHERE attrelid = %s::regclass AND attnum > 0 ORDER BY attnum",
        (relation,),
    ).fetchall()
    metadata = {
        "label": relation_label,
        "relation": relation,
        "schema_contract": [{"name": name, "postgres_type": type_} for name, type_ in columns],
        "statistics_target": statistics_target,
        "setseed_expression": "1.0 / 123",
        "analyze_count": 1,
        "row_count": row_count,
        "physical_extended_statistics_count": extstats_count,
        "observed_statistics_targets": [
            {"name": str(name), "target": int(target)} for name, target in targets
        ],
    }
    return estimates, metadata


def _load_paper_and_advisor_records(
    paper_path: Path, audit_path: Path, queries: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    paper = {row["query_id"]: row for row in _load_jsonl(paper_path)}
    audit = {row["query_id"]: row for row in _load_jsonl(audit_path)}
    if set(paper) != set(audit) or set(paper) != {query["query_id"] for query in queries}:
        raise ValueError("paper, advisor audit, and source workload query IDs do not align")
    records = []
    for query in queries:
        query_id = query["query_id"]
        paper_row = paper[query_id]
        audit_row = audit[query_id]
        if int(paper_row["true_rows"]) != int(audit_row["true_rows"]):
            raise ValueError(f"paper/advisor truth mismatch for {query_id}")
        records.append(
            {
                "query_id": query_id,
                "weight": float(audit_row["weight"]),
                "true_rows": int(audit_row["true_rows"]),
                "paper_target10000_estimate": int(paper_row["estimated_rows"]),
                "paper_target10000_qerror": float(paper_row["qerror"]),
                "advisor_sandbox_estimate": int(audit_row["baseline_estimated_rows"]),
                "advisor_sandbox_qerror": float(audit_row["baseline_qerror"]),
                "advisor_final_estimate": int(audit_row["final_estimated_rows"]),
                "advisor_final_qerror": float(audit_row["final_qerror"]),
            }
        )
    return records


def _sample_records(
    records: list[dict[str, Any]],
    sample_counts: dict[str, int],
    sample_rows: int,
    population_rows: float,
) -> None:
    provider: str | None = None
    for record in records:
        sample_matches = sample_counts[record["query_id"]]
        scaled = scaled_sample_rows(sample_matches, sample_rows, population_rows)
        loss, provider = _frozen_advisor_qerror(scaled, record["true_rows"])
        # The planner estimate is integral while the oracle cardinality is not;
        # QErrorLoss accepts the exact scaled value, so calculate it directly.
        planner_loss = qerror(record["advisor_sandbox_estimate"], scaled)
        record.update(
            {
                "sample_true_rows": sample_matches,
                "scaled_sample_rows": scaled,
                "sample_oracle_qerror": loss,
                "planner_sample_qerror": planner_loss,
                "qerror_provider": provider,
            }
        )


def run_baseline_gap(
    *,
    stock_dsn: str,
    planner_dsn: str,
    canonical_run: Path = Path("runs/197e9b890ac58bc4fbcfb218"),
    paper_baseline_directory: Path = Path("paper-baselines/arecel-census13"),
    output_directory: Path = Path("diagnostics/arecel-census13-baseline-gap"),
    data_root: Path | None = None,
    advisor_root: Path = Path("/home/wqts/projects/extstats-advisor"),
    patched_postgres_root: Path = Path("/home/wqts/projects/postgresql-src-pgextadv"),
) -> dict[str, Any]:
    if not stock_dsn or not planner_dsn:
        raise ValueError("both stock and patched planner DSNs are required")
    repository = Path(__file__).resolve().parents[2]
    research_sha = _git_sha(repository)
    if output_directory.exists() and any(output_directory.iterdir()):
        raise FileExistsError(f"baseline-gap output already exists: {output_directory}")

    source_queries, workload_sha = _load_source_queries(census13.data_root(data_root))
    truths_artifact = json.loads((canonical_run / "ground-truth-v1.json").read_text())
    truths = {item["query_id"]: int(item["cardinality"]) for item in truths_artifact["truths"]}
    records = _load_paper_and_advisor_records(
        paper_baseline_directory / "postgres-per-query-v1.jsonl",
        canonical_run / "analysis" / "per-query-v1.jsonl",
        source_queries,
    )
    source_by_id = {query["query_id"]: query for query in source_queries}
    for query_id, truth in truths.items():
        if query_id not in source_by_id or truth != next(
            record["true_rows"] for record in records if record["query_id"] == query_id
        ):
            raise ValueError(f"canonical truth alignment failed for {query_id}")

    import psycopg

    started = time.time()
    full_metadata: dict[str, Any] = {}
    with psycopg.connect(stock_dsn, autocommit=True) as connection:
        configurations = (
            (
                "B",
                'public."baseline_paper_t100"',
                PAPER_COLUMNS,
                100,
            ),
            (
                "C",
                'public."baseline_advisor_t100"',
                census13.COLUMNS,
                100,
            ),
            (
                "D",
                'public."baseline_advisor_t10000"',
                census13.COLUMNS,
                10_000,
            ),
        )
        for label, relation, columns, target in configurations:
            estimates, metadata = _measure_full_relation(
                connection,
                relation=relation,
                relation_label=label,
                columns=columns,
                csv_path=census13.csv_path(data_root),
                queries=source_queries,
                truths=truths,
                statistics_target=target,
            )
            full_metadata[label] = metadata
            for record in records:
                estimate = estimates[record["query_id"]]
                field = {
                    "B": "paper_target100",
                    "C": "advisor_schema_full_target100",
                    "D": "advisor_schema_full_target10000",
                }[label]
                loss, _ = _frozen_advisor_qerror(estimate, record["true_rows"])
                record[f"{field}_estimate"] = estimate
                record[f"{field}_qerror"] = loss
        stock_version = connection.execute(
            "SELECT version(), current_setting('server_version_num')"
        ).fetchone()

    run_manifest = json.loads((canonical_run / "manifest.json").read_text())
    snapshot_manifest = json.loads(
        (canonical_run / "advisor-snapshot" / "manifest.json").read_text()
    )
    systems = verify_frozen_systems(advisor_root, patched_postgres_root)
    for key in ("advisor_commit_sha", "patched_postgres_commit_sha"):
        if run_manifest.get(key) != systems[key]:
            raise ValueError(f"canonical run {key} does not match the frozen system")
    snapshot_path = canonical_run / "advisor-snapshot"
    candidate_path = canonical_run / "candidate-universe.json"
    native_path = canonical_run / "native-stats-repository"
    from extstats_advisor.candidates import load_candidate_universe
    from extstats_advisor.dbms.postgres.sandbox import (
        destroy_postgres_planner_sandbox,
        prepare_postgres_planner_sandbox,
    )
    from extstats_advisor.native_stats.repository import load_native_stats_repository
    from extstats_advisor.snapshot.bundle import load_snapshot

    snapshot = load_snapshot(snapshot_path)
    universe = load_candidate_universe(candidate_path, snapshot)
    native_repository = load_native_stats_repository(native_path)
    prepared = prepare_postgres_planner_sandbox(planner_dsn, snapshot, universe, native_repository)
    sample_counts: dict[str, int] = {}
    try:
        frozen = prepared.metadata.frozen_relation_name
        frozen_relation = relation_sql(frozen.name, frozen.schema or "public")
        with psycopg.connect(planner_dsn, autocommit=True) as connection:
            for query in source_queries:
                sql = diagnostic_query_sql(query["sql"], frozen_relation)
                sample_counts[query["query_id"]] = int(
                    connection.execute(
                        "SELECT count(*) FROM (" + sql.rstrip("; ") + ") AS sample_rows"
                    ).fetchone()[0]
                )
            physical_extstats = int(
                connection.execute(
                    "SELECT count(*) FROM pg_catalog.pg_statistic_ext "
                    "WHERE stxrelid = %s::regclass",
                    (
                        relation_sql(
                            snapshot.schemas[0].relation_name.name,
                            snapshot.schemas[0].relation_name.schema or "public",
                        ),
                    ),
                ).fetchone()[0]
            )
            planner_version = connection.execute(
                "SELECT version(), current_setting('server_version_num')"
            ).fetchone()
    finally:
        destroy_postgres_planner_sandbox(planner_dsn)

    sample_rows = int(prepared.metadata.sample_row_count)
    population_rows = float(prepared.metadata.population_row_count)
    _sample_records(records, sample_counts, sample_rows, population_rows)
    ladder = comparison_ladder(records)
    transitions = {
        "A_to_B_statistics_target": ladder["B"]["weighted_objective"]
        - ladder["A"]["weighted_objective"],
        "B_to_C_schema": ladder["C"]["weighted_objective"] - ladder["B"]["weighted_objective"],
        "C_to_D_statistics_target": ladder["D"]["weighted_objective"]
        - ladder["C"]["weighted_objective"],
        "E_to_F_planner_over_sample": ladder["F"]["weighted_objective"]
        - ladder["E"]["weighted_objective"],
    }
    deterioration = max(transitions.items(), key=lambda item: item[1])
    recovery = {
        "objective_absolute_reduction": ladder["F"]["weighted_objective"]
        - ladder["G"]["weighted_objective"],
        "objective_relative_reduction": (
            (ladder["F"]["weighted_objective"] - ladder["G"]["weighted_objective"])
            / ladder["F"]["weighted_objective"]
        ),
        "p95_absolute_reduction": ladder["F"]["p95"] - ladder["G"]["p95"],
    }
    per_query_path = output_directory / "per-query-v1.jsonl"
    output_directory.mkdir(parents=True, exist_ok=True)
    with per_query_path.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")

    sampling = snapshot_manifest["semantic_provenance"]["sampling"]
    artifact = {
        "format_version": FORMAT_VERSION,
        "research_repository": FROZEN_RESEARCH_REPOSITORY,
        "research_commit_sha": research_sha,
        "advisor_repository": FROZEN_ADVISOR_REPOSITORY,
        "advisor_commit_sha": FROZEN_ADVISOR_SHA,
        "patched_postgres_repository": FROZEN_PATCHED_POSTGRES_REPOSITORY,
        "patched_postgres_commit_sha": FROZEN_PATCHED_POSTGRES_SHA,
        "canonical_run": "runs/197e9b890ac58bc4fbcfb218",
        "immutable_source_artifacts": {
            "paper_baseline": "paper-baselines/arecel-census13/postgres-v1.json",
            "paper_per_query": "paper-baselines/arecel-census13/postgres-per-query-v1.jsonl",
            "advisor_audit": "runs/197e9b890ac58bc4fbcfb218/analysis/audit-v1.json",
            "advisor_per_query": "runs/197e9b890ac58bc4fbcfb218/analysis/per-query-v1.jsonl",
        },
        "dataset": census13.inspect(data_root),
        "workload": {
            "identity": "AreCELearnedYet base:test",
            "sha256": workload_sha,
            "query_count": len(records),
        },
        "full_table_configurations": full_metadata,
        "stock_postgresql": {
            "server_version": str(stock_version[0]),
            "server_version_num": int(stock_version[1]),
        },
        "fixed_sample": {
            "snapshot_semantic_digest": snapshot_manifest["semantic_digest"],
            "sample_relation": prepared.metadata.frozen_relation_name.to_dict(),
            "sample_rows": sample_rows,
            "population_rows": population_rows,
            "population_quality": prepared.metadata.population_quality,
            "physical_extended_statistics_count": physical_extstats,
            "sampling_provenance": sampling,
            "implementation_shape": "TABLESAMPLE SYSTEM -> deterministic reservoir",
            "exact_count_executed": True,
            "sample_oracle_is_not_postgresql_estimator": True,
        },
        "planner_postgresql": {
            "server_version": str(planner_version[0]),
            "server_version_num": int(planner_version[1]),
        },
        "qerror_provider": "extstats_advisor.utility.QErrorLoss",
        "comparison_ladder": ladder,
        "transitions": transitions,
        "largest_measured_deterioration": {
            "transition": deterioration[0],
            "delta": deterioration[1],
        },
        "extended_statistics_recovery": recovery,
        "zero_sample_matches": zero_sample_summary(records),
        "tail_queries": tail_records(records),
        "terminology": {
            "E": "fixed-sample oracle diagnostic, not a PostgreSQL estimator or optimization configuration",
            "F": "advisor sandbox ordinary baseline over the frozen sample",
            "G": "advisor final selected extended statistics over the frozen sample",
            "causality": "no sampling-causality claim is made beyond these controlled measurements",
        },
        "per_query_path": per_query_path.name,
        "per_query_sha256": sha256_file(per_query_path),
        "elapsed_seconds": time.time() - started,
    }
    reject_credentials(artifact)
    artifact["semantic_digest"] = semantic_digest(artifact)
    write_json(output_directory / "baseline-gap-v1.json", artifact)
    return {
        "artifact": str(output_directory / "baseline-gap-v1.json"),
        "per_query": str(per_query_path),
        "semantic_digest": artifact["semantic_digest"],
        "comparison_ladder": ladder,
        "zero_sample_matches": artifact["zero_sample_matches"],
    }
