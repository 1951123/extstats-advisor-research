"""Deterministic offline fixture for the RQ5 live-correctness preflight.

This module only constructs and validates sealed Advisor artifacts.  It never
opens a PostgreSQL connection.  The generated fixture is deliberately separate
from the Forest10 timing manifests and is classified as integration-readiness
evidence, not as a scientific timing result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pyarrow as pa

from .provenance import semantic_digest, sha256_file, write_json

ADVISOR_SHA = "0865c5a6afb8bc176bd7d3b10b13b3da83f1f641"
STOCK_POSTGRES_SHA = "0d1c00c624fa7367d4a895f44381887757289682"
PATCHED_POSTGRES_SHA = "6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6"
FIXTURE_ID = "rq5-tiny-cost-fixture-v1"
RELATION_ID = "rel_rq5_tiny_cost_v1"
TABLE_NAME = "rq5_tiny_cost_fixture"
WORKLOAD_ID = "rq5_tiny_cost_workload_v1"
MANIFEST_FORMAT = "rq5-whatif-cost-integration-preflight-manifest-v1"

_ROWS = (
    (1, "north", "gold", "enterprise"),
    (2, "north", "gold", "enterprise"),
    (3, "north", "gold", "small"),
    (4, "north", "silver", "small"),
    (5, "south", "silver", "small"),
    (6, "south", "silver", "small"),
    (7, "south", "silver", "enterprise"),
    (8, "south", "bronze", "small"),
    (9, "west", "bronze", "small"),
    (10, "west", "bronze", "enterprise"),
    (11, "west", "gold", "enterprise"),
    (12, "west", "gold", "enterprise"),
)


def _advisor_root_sha(advisor_root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(advisor_root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(f"cannot resolve Advisor Git identity: {advisor_root}")
    observed = result.stdout.strip()
    if observed != ADVISOR_SHA:
        raise ValueError(f"Advisor root is {observed}, expected historical pin {ADVISOR_SHA}")
    return observed


def _advisor_imports(advisor_root: Path | None) -> tuple[Any, ...]:
    if advisor_root is not None:
        _advisor_root_sha(advisor_root)
        source = str(advisor_root.resolve() / "src")
        if source not in sys.path:
            sys.path.insert(0, source)
    from extstats_advisor.candidates.universe import (
        derive_candidate_universe,
        load_candidate_universe,
        validate_candidate_universe,
        write_candidate_universe,
    )
    from extstats_advisor.snapshot.bundle import (
        load_snapshot,
        validate_snapshot,
        write_snapshot,
    )
    from extstats_advisor.snapshot.model import (
        AdvisorSnapshot,
        ColumnSchema,
        DBMSIdentity,
        PopulationMetadata,
        RelationName,
        RelationSchema,
        Workload,
        WorkloadQuery,
    )

    return (
        AdvisorSnapshot,
        ColumnSchema,
        DBMSIdentity,
        PopulationMetadata,
        RelationName,
        RelationSchema,
        Workload,
        WorkloadQuery,
        derive_candidate_universe,
        load_candidate_universe,
        validate_candidate_universe,
        write_candidate_universe,
        load_snapshot,
        validate_snapshot,
        write_snapshot,
    )


def fixture_workload(workload_query_type: Any) -> Any:
    """Build the fixed five-query single-table workload using Advisor's model."""

    queries = (
        workload_query_type(
            "rq5_tiny_q01",
            f"SELECT * FROM public.{TABLE_NAME} WHERE region = 'north' AND tier = 'gold'",
        ),
        workload_query_type(
            "rq5_tiny_q02",
            f"SELECT * FROM public.{TABLE_NAME} WHERE tier = 'gold' AND segment = 'enterprise'",
        ),
        workload_query_type(
            "rq5_tiny_q03",
            f"SELECT * FROM public.{TABLE_NAME} WHERE region = 'south' AND segment = 'small'",
        ),
        workload_query_type(
            "rq5_tiny_q04",
            f"SELECT * FROM public.{TABLE_NAME} "
            "WHERE region = 'north' AND tier = 'gold' AND segment = 'enterprise'",
        ),
        workload_query_type(
            "rq5_tiny_q05",
            f"SELECT * FROM public.{TABLE_NAME} WHERE region = 'west' AND tier = 'gold'",
        ),
    )
    return queries


def build_snapshot() -> Any:
    """Construct the deterministic sealed-snapshot model without filesystem I/O."""

    (
        AdvisorSnapshot,
        ColumnSchema,
        DBMSIdentity,
        PopulationMetadata,
        RelationName,
        RelationSchema,
        Workload,
        WorkloadQuery,
        *_rest,
    ) = _advisor_imports(None)
    relation = RelationSchema(
        RELATION_ID,
        RelationName(TABLE_NAME, schema="public"),
        (
            ColumnSchema("id", 1, "int32", False, "integer"),
            ColumnSchema("region", 2, "string", False, "text"),
            ColumnSchema("tier", 3, "string", False, "text"),
            ColumnSchema("segment", 4, "string", False, "text"),
        ),
    )
    workload = Workload(
        WORKLOAD_ID,
        tuple(fixture_workload(WorkloadQuery)),
        {
            "fixture_id": FIXTURE_ID,
            "selection_contract": "fixed-five-single-table-equality-queries-v1",
        },
    )
    schema = pa.schema(
        [
            pa.field("id", pa.int32(), nullable=False),
            pa.field("region", pa.string(), nullable=False),
            pa.field("tier", pa.string(), nullable=False),
            pa.field("segment", pa.string(), nullable=False),
        ]
    )
    table = pa.Table.from_arrays(
        [
            pa.array([row[0] for row in _ROWS], type=pa.int32()),
            pa.array([row[1] for row in _ROWS], type=pa.string()),
            pa.array([row[2] for row in _ROWS], type=pa.string()),
            pa.array([row[3] for row in _ROWS], type=pa.string()),
        ],
        schema=schema,
    )
    return AdvisorSnapshot(
        schemas=(relation,),
        populations=(PopulationMetadata(RELATION_ID, len(_ROWS), "exact", "fixture-rows-v1"),),
        workload=workload,
        samples={RELATION_ID: table},
        dbms=DBMSIdentity("postgresql", "16.14"),
        semantic_provenance={
            "fixture_id": FIXTURE_ID,
            "source_view_token": "rq5-tiny-cost-fixture-v1",
            "row_generation": "12-fixed-correlated-tuples-v1",
            "advisor_source_commit": ADVISOR_SHA,
        },
        runtime_metadata={"generated_by": "rq5_tiny_fixture.py"},
        sensitivity={
            "contains_production_sensitive_values": False,
            "anonymized_or_encrypted_by_this_repository": False,
        },
    )


def _candidate_definitions(universe: Any) -> list[dict[str, Any]]:
    return [
        {
            "candidate_id": candidate.candidate_id,
            "group_id": candidate.group_id,
            "relation_id": candidate.relation_id,
            "kind": candidate.kind,
            "column_ordinals": list(candidate.column_ordinals),
            "column_names": list(candidate.column_names),
            "static_precedence_rank": candidate.static_precedence_rank,
        }
        for candidate in universe.candidates
    ]


def _configuration(ordinal: int, label: str, candidate_ids: list[str]) -> dict[str, Any]:
    return {
        "analysis": "fixture",
        "ordinal": ordinal,
        "configuration_id": label,
        "candidate_ids": list(candidate_ids),
        "declared_order": list(candidate_ids),
        "configuration_size": len(candidate_ids),
    }


def build_manifest(
    *,
    producer_commit_sha: str,
    snapshot_digest: str,
    candidate_digest: str,
    workload_sha256: str,
    candidate_definitions: list[dict[str, Any]],
    workload: dict[str, Any],
) -> dict[str, Any]:
    candidate_ids = [item["candidate_id"] for item in candidate_definitions]
    queries = [
        {
            "subset_ordinal": index,
            "source_index": index,
            "query_id": query["query_id"],
            "sql_sha256": hashlib.sha256(query["sql"].encode("utf-8")).hexdigest(),
            "weight": float(query.get("weight", 1.0)),
        }
        for index, query in enumerate(workload["queries"])
    ]
    configurations = [
        _configuration(1, "fixture-singleton-01", candidate_ids[:1]),
        _configuration(2, "fixture-overlap-pair-02", candidate_ids[:2]),
    ]
    material = {
        "fixture_id": FIXTURE_ID,
        "candidate_definitions": candidate_definitions,
        "workload_id": workload["workload_id"],
        "query_subset": queries,
        "configurations": configurations,
    }
    body = {
        "format_version": MANIFEST_FORMAT,
        "experiment_id": FIXTURE_ID,
        "status": "offline-fixture-ready",
        "classification": "integration-readiness-only",
        "producer_commit_sha": producer_commit_sha,
        "dataset": {
            "dataset_id": FIXTURE_ID,
            "relation_id": RELATION_ID,
            "relation": {"schema": "public", "name": TABLE_NAME},
            "sample_rows": len(_ROWS),
            "population_rows": len(_ROWS),
            "statistics_target": 100,
            "candidate_count": len(candidate_definitions),
        },
        "source_bindings": {
            "advisor_sha": ADVISOR_SHA,
            "stock_postgres_sha": STOCK_POSTGRES_SHA,
            "patched_postgres_sha": PATCHED_POSTGRES_SHA,
            "snapshot_semantic_digest": snapshot_digest,
            "candidate_universe_semantic_digest": candidate_digest,
        },
        "snapshot": {"path": "snapshot", "semantic_digest": snapshot_digest},
        "candidate_universe": {
            "path": "candidate-universe.json",
            "semantic_digest": candidate_digest,
            "count": len(candidate_definitions),
            "definitions": candidate_definitions,
        },
        "workload": {
            "path": "workload.json",
            "sha256": workload_sha256,
            "workload_id": workload["workload_id"],
            "query_count": len(workload["queries"]),
            "selection_rule": "all-five-canonical-fixture-queries-v1",
        },
        "query_subset": queries,
        "query_subset_size": len(queries),
        "analysis_a": {"evaluation_count": 2, "configurations": configurations},
        "fixture": {
            "database_names": {
                "stock_template": "rq5_tiny_stock_template",
                "patched_template": "rq5_tiny_patched_template",
            },
            "ports": {"stock": 55432, "patched": 55433},
            "row_count": len(_ROWS),
            "scientific_status": "not-scientific-timing-evidence",
        },
        "binding_digest": semantic_digest(material),
    }
    body["semantic_digest"] = semantic_digest(body)
    return body


def generate_fixture(
    destination: Path,
    *,
    advisor_root: Path | None = None,
    producer_commit_sha: str | None = None,
) -> dict[str, Any]:
    """Generate all fixture files once; refuse to overwrite any existing root."""

    destination = Path(destination).expanduser().resolve()
    if destination.exists():
        raise FileExistsError(f"fixture destination already exists: {destination}")
    if advisor_root is not None:
        _advisor_root_sha(advisor_root)
    destination.mkdir(parents=True)
    (
        _AdvisorSnapshot,
        _ColumnSchema,
        _DBMSIdentity,
        _PopulationMetadata,
        _RelationName,
        _RelationSchema,
        _Workload,
        _WorkloadQuery,
        _derive,
        _load_candidates,
        _validate_candidates,
        write_candidates,
        _load_snapshot,
        _validate_snapshot,
        write_snapshot,
    ) = _advisor_imports(advisor_root)
    snapshot = build_snapshot()
    snapshot_dir = destination / "snapshot"
    snapshot_digest = write_snapshot(snapshot, snapshot_dir)
    sealed = _load_snapshot(snapshot_dir)
    _validate_snapshot(snapshot_dir)
    universe = _derive(sealed)
    candidate_path = destination / "candidate-universe.json"
    candidate_digest = write_candidates(universe, candidate_path)
    _validate_candidates(candidate_path, sealed)
    workload = sealed.workload.to_dict()
    workload_path = destination / "workload.json"
    write_json(workload_path, workload)
    manifest = build_manifest(
        producer_commit_sha=producer_commit_sha or _research_head(destination),
        snapshot_digest=snapshot_digest,
        candidate_digest=candidate_digest,
        workload_sha256=sha256_file(workload_path),
        candidate_definitions=_candidate_definitions(universe),
        workload=workload,
    )
    write_json(destination / "manifest-v1.json", manifest)
    (destination / "fixture-rows.sql").write_text(_rows_sql(), encoding="utf-8")
    (destination / "fixture-rows-stock.sql").write_text(_rows_sql(), encoding="utf-8")
    (destination / "fixture-rows-patched.sql").write_text(_rows_sql(), encoding="utf-8")
    return validate_fixture(destination)


def _research_head(destination: Path) -> str:
    for parent in (destination, *destination.parents):
        result = subprocess.run(
            ["git", "-C", str(parent), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    raise ValueError("fixture must be generated inside a Git worktree")


def _rows_sql() -> str:
    lines = [
        f"-- {FIXTURE_ID}; execute only in an explicitly owned empty template database.",
        (
            f"CREATE TABLE public.{TABLE_NAME} (id integer NOT NULL, region text NOT NULL, "
            "tier text NOT NULL, segment text NOT NULL);"
        ),
        f"INSERT INTO public.{TABLE_NAME} (id, region, tier, segment) VALUES",
    ]
    values = [f"  ({row[0]}, '{row[1]}', '{row[2]}', '{row[3]}')" for row in _ROWS]
    lines.append(",\n".join(values) + ";")
    lines.append(f"ANALYZE public.{TABLE_NAME};")
    return "\n".join(lines) + "\n"


def validate_fixture(destination: Path) -> dict[str, Any]:
    """Validate committed fixture artifacts using Advisor's offline validators."""

    destination = Path(destination).expanduser().resolve()
    (
        _AdvisorSnapshot,
        _ColumnSchema,
        _DBMSIdentity,
        _PopulationMetadata,
        _RelationName,
        _RelationSchema,
        _Workload,
        _WorkloadQuery,
        _derive,
        load_candidates,
        validate_candidates,
        _write_candidates,
        load_snapshot,
        validate_snapshot,
        _write_snapshot,
    ) = _advisor_imports(None)
    snapshot_path = destination / "snapshot"
    snapshot_report = validate_snapshot(snapshot_path)
    snapshot = load_snapshot(snapshot_path)
    candidate_path = destination / "candidate-universe.json"
    candidate_report = validate_candidates(candidate_path, snapshot)
    universe = load_candidates(candidate_path, snapshot)
    workload_path = destination / "workload.json"
    workload = json.loads(workload_path.read_text(encoding="utf-8"))
    if (
        sha256_file(workload_path)
        != json.loads((destination / "manifest-v1.json").read_text(encoding="utf-8"))["workload"][
            "sha256"
        ]
    ):
        raise ValueError("tiny fixture workload digest does not match manifest")
    manifest = json.loads((destination / "manifest-v1.json").read_text(encoding="utf-8"))
    if manifest.get("semantic_digest") != semantic_digest(
        {key: value for key, value in manifest.items() if key != "semantic_digest"}
    ):
        raise ValueError("tiny fixture manifest semantic digest mismatch")
    if (
        manifest["source_bindings"]["snapshot_semantic_digest"]
        != snapshot_report["semantic_digest"]
    ):
        raise ValueError("tiny fixture snapshot binding mismatch")
    if (
        manifest["source_bindings"]["candidate_universe_semantic_digest"]
        != candidate_report["semantic_digest"]
    ):
        raise ValueError("tiny fixture candidate binding mismatch")
    if workload != snapshot.workload.to_dict():
        raise ValueError("external workload differs from sealed snapshot workload")
    definitions = _candidate_definitions(universe)
    if manifest["candidate_universe"]["definitions"] != definitions:
        raise ValueError("manifest candidate definitions differ from sealed universe")
    configs = manifest["analysis_a"]["configurations"]
    candidate_ids = [item["candidate_id"] for item in definitions]
    if len(candidate_ids) != 6 or len(configs) != 2:
        raise ValueError("tiny fixture treatment roster is incomplete")
    if any(item["candidate_ids"] != item["declared_order"] for item in configs):
        raise ValueError("tiny fixture configuration order is not explicit")
    if set(configs[1]["candidate_ids"]) - set(candidate_ids):
        raise ValueError("tiny fixture configuration references an unknown candidate")
    if len(workload.get("queries", [])) != 5:
        raise ValueError("tiny fixture workload must contain five queries")
    return {
        "status": "valid-offline-fixture",
        "fixture_id": FIXTURE_ID,
        "snapshot_semantic_digest": snapshot_report["semantic_digest"],
        "candidate_universe_semantic_digest": candidate_report["semantic_digest"],
        "workload_sha256": sha256_file(workload_path),
        "candidate_count": len(candidate_ids),
        "query_count": len(workload["queries"]),
        "configuration_count": len(configs),
        "row_count": len(_ROWS),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("generate", "validate"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--advisor-root", type=Path)
    parser.add_argument("--producer-sha")
    args = parser.parse_args(argv)
    if args.command == "generate":
        result = generate_fixture(
            args.output,
            advisor_root=args.advisor_root,
            producer_commit_sha=args.producer_sha,
        )
    else:
        result = validate_fixture(args.output)
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
