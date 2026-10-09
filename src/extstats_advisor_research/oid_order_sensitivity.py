"""OID/order-sensitivity ablation protocol, preflight, and live evaluator.

This module is intentionally independent of the RQ1b selector.  It binds the
already sealed RQ1b memberships, captures one fixed native payload realization
per dataset, and changes only the activation/deployment order.  The module
does not read any test-side artifact while constructing the preflight or
running the valid-side comparisons.
"""

from __future__ import annotations

import gc
import gzip
import hashlib
import json
import random
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .paper_baseline import qerror
from .provenance import read_json, semantic_digest, sha256_file, write_json
from .rq1_workload_generalization_live import (
    CENSUS13_SPEC,
    DMV11_SPEC,
    FOREST10_SPEC,
    POWER7_SPEC,
    RQ1BDatasetSpec,
)
from .system_freeze_v2 import (
    FROZEN_ADVISOR_SHA,
    FROZEN_PATCHED_POSTGRES_SHA,
    FROZEN_STOCK_POSTGRES_SHA,
    formal_system_freeze_v2_identity,
)

FORMAT = "postgresql-extstats-oid-order-sensitivity-v1"
PREFLIGHT_FORMAT = "postgresql-extstats-oid-order-sensitivity-preflight-v1"
SUMMARY_FORMAT = "postgresql-extstats-oid-order-sensitivity-summary-v1"
EXPERIMENT_ID = "postgresql-extstats-oid-order-sensitivity-v1"
PROTOCOL_PATH = Path("paper/oid-order-sensitivity-protocol-v1.json")
PREFLIGHT_PATH = Path("experiments/oid-order-sensitivity-v1/preflight-v1.json")
OUTPUT_ROOT = Path("experiments/oid-order-sensitivity-v1")
SEEDS = (17, 29, 43, 71, 101)
DATASET_SPECS = (CENSUS13_SPEC, FOREST10_SPEC, POWER7_SPEC, DMV11_SPEC)
QUERY_COUNT = 10_000
SAMPLE_ROWS = 10_000
SAMPLE_SEED = 42
STATISTICS_TARGET = 100
COLLISION_POLICY = "left-rotate-by-one-until-unique"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _body(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "semantic_digest"}


def _require_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"{label} must be a SHA-256 digest")
    int(value, 16)
    return value


def deterministic_orders(reference: Sequence[str]) -> list[dict[str, Any]]:
    """Return reference, reverse, and the five frozen seeded permutations."""

    values = list(reference)
    if not values or len(values) != len(set(values)):
        raise ValueError("reference membership must be non-empty and unique")
    orders: list[dict[str, Any]] = [
        {"permutation_id": "reference", "kind": "reference", "order": list(values)},
        {"permutation_id": "reverse", "kind": "reverse", "order": list(reversed(values))},
    ]
    seen = {tuple(item["order"]) for item in orders}
    for seed in SEEDS:
        candidate = list(values)
        random.Random(seed).shuffle(candidate)
        collision = tuple(candidate) in seen
        rotations = 0
        while tuple(candidate) in seen:
            candidate = candidate[1:] + candidate[:1]
            rotations += 1
            if rotations >= len(values):
                raise ValueError("collision policy could not produce a unique permutation")
        orders.append(
            {
                "permutation_id": f"random-seed-{seed}",
                "kind": "random",
                "seed": seed,
                "order": candidate,
                "collision_detected": collision,
                "collision_resolution_rotations": rotations,
            }
        )
        seen.add(tuple(candidate))
    if len(orders) != 7:
        raise AssertionError("the OID-order roster must contain exactly seven arms")
    return orders


def _canonical_valid_records(data_root: Path, dataset_key: str) -> list[dict[str, Any]]:
    path = data_root / "arecel" / "audit-v1" / f"{dataset_key}.canonical.jsonl.gz"
    if not path.is_file():
        raise FileNotFoundError(f"missing audited canonical workload: {path}")
    records: list[dict[str, Any]] = []
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            record = json.loads(line)
            if record.get("split") == "valid":
                records.append(
                    {
                        "index": int(record["index"]),
                        "query_id": str(record["query_id"]),
                        "source_query_sha256": str(record["source_query_sha256"]),
                    }
                )
    if len(records) != QUERY_COUNT:
        raise ValueError(f"{dataset_key} valid canonical workload count is not 10000")
    return records


def _valid_workload(root: Path, data_root: Path, spec: RQ1BDatasetSpec) -> dict[str, Any]:
    import tempfile

    with tempfile.TemporaryDirectory(prefix=f"oid-order-{spec.runtime_name}-") as directory:
        path = Path(directory) / "valid-workload.json"
        identity = spec.dataset_module.extract_workload(path, data_root, split="valid")
        workload = read_json(path)
    if identity["sha256"] != spec.valid_workload_sha256:
        raise ValueError(f"{spec.dataset_id} valid workload SHA drift")
    canonical = _canonical_valid_records(data_root, spec.runtime_name)
    if len(workload.get("queries", [])) != QUERY_COUNT:
        raise ValueError(f"{spec.dataset_id} extracted valid workload count is not 10000")
    queries: list[dict[str, Any]] = []
    for index, (query, source) in enumerate(zip(workload["queries"], canonical, strict=True)):
        expected_id = f"{spec.valid_workload_id.removesuffix('_v1')}_{index:06d}"
        if query.get("query_id") != expected_id:
            raise ValueError(f"{spec.dataset_id} valid query ID drift at {index}")
        sql = str(query["sql"])
        queries.append(
            {
                "index": index,
                "query_id": query["query_id"],
                "source_query_sha256": source["source_query_sha256"],
                "sql_sha256": _sha256_text(sql),
            }
        )
    return {
        "workload_id": identity["workload_id"],
        "sha256": identity["sha256"],
        "canonical_source_sha256": spec.valid_canonical_workload_sha256,
        "query_count": QUERY_COUNT,
        "queries": queries,
    }


def _valid_workload_isolated(root: Path, data_root: Path, spec: RQ1BDatasetSpec) -> dict[str, Any]:
    """Extract one dataset in a short-lived process to bound adapter memory."""

    code = (
        "import json; from pathlib import Path; "
        "from extstats_advisor_research.oid_order_sensitivity import DATASET_SPECS, _valid_workload; "
        "spec = next(item for item in DATASET_SPECS if item.runtime_name == __import__('sys').argv[1]); "
        "print(json.dumps(_valid_workload(Path(__import__('sys').argv[2]), Path(__import__('sys').argv[3]), spec), separators=(',', ':')))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, spec.runtime_name, str(root), str(data_root)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            f"valid workload extraction failed for {spec.dataset_id}: {result.stderr}"
        )
    return json.loads(result.stdout)


def _selected_membership(root: Path, spec: RQ1BDatasetSpec) -> dict[str, Any]:
    design = read_json(root / spec.output_root / "design-v1.json")
    deployment = read_json(root / spec.output_root / "deployment-result-v1.json")
    if design.get("semantic_digest") != semantic_digest(_body(design)):
        raise ValueError(f"{spec.dataset_id} design digest is invalid")
    ordered = deployment.get("deployment_ordered_candidate_ids")
    if ordered != design.get("design_stage", {}).get("deployment_ordered_candidate_ids"):
        raise ValueError(f"{spec.dataset_id} deployment order is not sealed design order")
    objects = {item["candidate_id"]: item for item in deployment.get("deployed_objects", [])}
    if set(objects) != set(ordered):
        raise ValueError(f"{spec.dataset_id} deployment membership is incomplete")
    definitions = []
    for candidate_id in ordered:
        item = objects[candidate_id]
        definitions.append(
            {
                "candidate_id": candidate_id,
                "kind": item["kind"],
                "column_ordinals": item["column_ordinals"],
                "statistics_target": item["statistics_target"],
            }
        )
    return {
        "candidate_ids": list(ordered),
        "definitions": definitions,
        "recommendation_digest": design["design_stage"]["recommendation_digest"],
        "deployment_digest": deployment["semantic_digest"],
        "design_digest": design["semantic_digest"],
    }


def _truth_binding(root: Path, spec: RQ1BDatasetSpec) -> dict[str, Any]:
    path = root / spec.valid_observations_path
    wire = read_json(path)
    truth_digest = semantic_digest(_body(wire))
    if sha256_file(path) != spec.valid_observations_sha256:
        raise ValueError(f"{spec.dataset_id} valid truth byte SHA drift")
    truths = wire.get("truths")
    if not isinstance(truths, list) or len(truths) != QUERY_COUNT:
        raise ValueError(f"{spec.dataset_id} valid truth count is not 10000")
    return {
        "path": spec.valid_observations_path.as_posix(),
        "sha256": spec.valid_observations_sha256,
        "semantic_digest": truth_digest,
        "workload_id": spec.valid_workload_id,
        "query_count": QUERY_COUNT,
    }


def build_protocol(*, root: Path, producer_sha: str) -> dict[str, Any]:
    """Build the protocol from existing immutable RQ1b evidence only."""

    source_audit = read_json(root / "experiments/rq1-workload-generalization-source-audit-v2.json")
    protocol = read_json(root / "paper/rq1-workload-generalization-protocol-v2.json")
    datasets: list[dict[str, Any]] = []
    for spec in DATASET_SPECS:
        membership = _selected_membership(root, spec)
        audit = next(
            row for row in source_audit["datasets"] if row["dataset_id"] == spec.dataset_id
        )
        datasets.append(
            {
                "dataset_id": spec.dataset_id,
                "cli_name": spec.cli_name,
                "relation": spec.dataset_module.RELATION,
                "rows": spec.dataset_module.EXPECTED_ROWS,
                "schema_contract_id": spec.dataset_module.SCHEMA_CONTRACT_ID,
                "dataset_content_identity": spec.dataset_content_identity,
                "upstream_commit": audit["upstream_commit"],
                "rq1b_design_digest": membership["design_digest"],
                "rq1b_recommendation_digest": membership["recommendation_digest"],
                "rq1b_deployment_digest": membership["deployment_digest"],
                "selected_definitions": membership["definitions"],
                "reference_order": membership["candidate_ids"],
                "permutations": deterministic_orders(membership["candidate_ids"]),
                "valid_workload_id": spec.valid_workload_id,
                "valid_workload_sha256": spec.valid_workload_sha256,
                "valid_canonical_workload_sha256": spec.valid_canonical_workload_sha256,
                "valid_truth_path": spec.valid_observations_path.as_posix(),
                "valid_truth_sha256": spec.valid_observations_sha256,
                "valid_query_count": QUERY_COUNT,
            }
        )
        gc.collect()
    value: dict[str, Any] = {
        "format_version": FORMAT,
        "experiment_id": EXPERIMENT_ID,
        "status": "preregistered",
        "research_question": {
            "SQ1": "controlled hypothetical activation-order sensitivity with fixed native payloads",
            "SQ2": "physical stock deployment-order sensitivity under explicit payload/OID controls",
            "SQ3": "overlapping-MCV masking witness and matched hypothetical/physical estimates",
        },
        "datasets": datasets,
        "source_bindings": {
            "rq1b_protocol_v2": {
                "path": "paper/rq1-workload-generalization-protocol-v2.json",
                "semantic_digest": protocol["semantic_digest"],
            },
            "source_audit_v2": {
                "path": "experiments/rq1-workload-generalization-source-audit-v2.json",
                "semantic_digest": source_audit["semantic_digest"],
            },
            "valid_truth_only": True,
            "test_workload_access": "forbidden-for-all-new-planner-calls",
        },
        "order_schedule": {
            "reference": "published deployment_ordered_candidate_ids",
            "reverse": "exact reverse of reference",
            "random": {
                "algorithm": "Python random.Random(seed).shuffle(reference copy)",
                "seeds": list(SEEDS),
                "collision_policy": COLLISION_POLICY,
            },
            "arm_count_per_dataset": 7,
        },
        "frozen_system": {
            "advisor_sha": FROZEN_ADVISOR_SHA,
            "patched_postgres_sha": FROZEN_PATCHED_POSTGRES_SHA,
            "stock_postgres_sha": FROZEN_STOCK_POSTGRES_SHA,
            "postgres_version": "16.14",
            "system_freeze": formal_system_freeze_v2_identity(),
        },
        "frozen_parameters": {
            "sample_rows": SAMPLE_ROWS,
            "sample_seed": SAMPLE_SEED,
            "statistics_target": STATISTICS_TARGET,
            "query_count": QUERY_COUNT,
            "planner_gucs": {
                "timezone": "UTC",
                "DateStyle": "ISO, YMD",
                "default_statistics_target": "100",
                "transaction_isolation": "repeatable read",
                "transaction_read_only": "on",
            },
        },
        "causal_contract": {
            "hypothetical_primary_factor": "activation order only",
            "physical_comparison_status": "causal only when payload, ordinary stats, data, SQL, GUCs and OID order all match",
            "otherwise": "confounded-operational-deployment",
            "direct_catalog_writes": False,
            "setseed_is_analyze_reproducibility_proof": False,
        },
        "metrics": {
            "qerror_contract": "qerror-cardinality-floor-1-v1",
            "distribution": ["mean", "p50", "p95", "p99", "max"],
            "paired": ["improved", "unchanged", "worsened"],
            "order_diagnostics": [
                "plan_rows_changed",
                "ratio_vs_reference",
                "difference_vs_reference",
            ],
        },
        "synthetic_witness": {
            "fixture_id": "oid-order-overlapping-mcv-witness-v1",
            "schema": "(a integer, b integer, c integer)",
            "mcv_a": "(a,b)",
            "mcv_b": "(b,c)",
            "query": "a = 1 AND b = 1 AND c = 1",
            "arms": ["a-only", "b-only", "a-then-b", "b-then-a", "none"],
            "physical_hypothetical_root_plan_rows_must_match_exactly": True,
        },
        "stop_rules": [
            "missing source identity or payload",
            "query/truth identity mismatch",
            "ordinary-statistics drift",
            "physical payload mismatch is retained and labeled confounded",
            "cleanup failure",
            "any incomplete declared arm",
        ],
        "admissible_claims": [
            "order sensitivity may be measured for the declared fixed memberships and workloads",
            "null and negative results must be reported",
        ],
        "inadmissible_claims": [
            "Advisor ordering is globally optimal",
            "all configurations are order-sensitive",
            "workload drift or distribution-shift robustness",
            "runtime-speedup or RQ1b held-out claims",
        ],
        "preflight_manifest_path": PREFLIGHT_PATH.as_posix(),
    }
    value["semantic_digest"] = semantic_digest(value)
    return value


def validate_protocol(value: Mapping[str, Any]) -> dict[str, Any]:
    if value.get("format_version") != FORMAT:
        raise ValueError("unsupported OID-order protocol format")
    if value.get("experiment_id") != EXPERIMENT_ID:
        raise ValueError("OID-order experiment identity drift")
    if value.get("semantic_digest") != semantic_digest(_body(value)):
        raise ValueError("OID-order protocol semantic digest mismatch")
    if value.get("frozen_system", {}).get("advisor_sha") != FROZEN_ADVISOR_SHA:
        raise ValueError("OID-order Advisor pin drift")
    if value.get("frozen_system", {}).get("patched_postgres_sha") != FROZEN_PATCHED_POSTGRES_SHA:
        raise ValueError("OID-order patched PostgreSQL pin drift")
    if value.get("frozen_system", {}).get("stock_postgres_sha") != FROZEN_STOCK_POSTGRES_SHA:
        raise ValueError("OID-order stock PostgreSQL pin drift")
    if len(value.get("datasets", [])) != 4:
        raise ValueError("OID-order protocol must contain four datasets")
    for dataset in value["datasets"]:
        if len(dataset.get("permutations", [])) != 7:
            raise ValueError(f"{dataset.get('dataset_id')} must contain seven permutations")
        reference = dataset["reference_order"]
        expected = deterministic_orders(reference)
        if dataset["permutations"] != expected:
            raise ValueError(f"{dataset['dataset_id']} permutation schedule drift")
    return {"status": "valid", "semantic_digest": value["semantic_digest"]}


def build_preflight(*, root: Path, data_root: Path, producer_sha: str) -> dict[str, Any]:
    protocol = read_json(root / PROTOCOL_PATH)
    validate_protocol(protocol)
    datasets: list[dict[str, Any]] = []
    for spec in DATASET_SPECS:
        protocol_dataset = next(
            item for item in protocol["datasets"] if item["dataset_id"] == spec.dataset_id
        )
        datasets.append(
            {
                **protocol_dataset,
                "valid_workload": _valid_workload_isolated(root, data_root, spec),
                "valid_truth": _truth_binding(root, spec),
            }
        )
    value: dict[str, Any] = {
        "format_version": PREFLIGHT_FORMAT,
        "experiment_id": EXPERIMENT_ID,
        "status": "ready-to-run",
        "producer_research_sha": producer_sha,
        "protocol": {
            "path": PROTOCOL_PATH.as_posix(),
            "semantic_digest": protocol["semantic_digest"],
        },
        "formal_execution": "not-started",
        "preseal_test_content_accessed": False,
        "test_workload_accessed": False,
        "test_truth_accessed": False,
        "strict_unseen_accessed": False,
        "datasets": datasets,
        "source_data_root_policy": "valid-side-only extraction from audited external root",
        "query_manifest_contract": "query_id + source_query_sha256 + sql_sha256; no SQL payload",
    }
    value["semantic_digest"] = semantic_digest(value)
    return value


def validate_preflight(value: Mapping[str, Any], *, root: Path) -> dict[str, Any]:
    if value.get("format_version") != PREFLIGHT_FORMAT:
        raise ValueError("unsupported OID-order preflight format")
    protocol = read_json(root / PROTOCOL_PATH)
    validate_protocol(protocol)
    if value.get("protocol") != {
        "path": PROTOCOL_PATH.as_posix(),
        "semantic_digest": protocol["semantic_digest"],
    }:
        raise ValueError("OID-order preflight protocol binding drift")
    if value.get("semantic_digest") != semantic_digest(_body(value)):
        raise ValueError("OID-order preflight semantic digest mismatch")
    if value.get("formal_execution") != "not-started":
        raise ValueError("OID-order preflight execution state drift")
    if any(
        value.get(field) is not False
        for field in (
            "preseal_test_content_accessed",
            "test_workload_accessed",
            "test_truth_accessed",
            "strict_unseen_accessed",
        )
    ):
        raise ValueError("OID-order preflight contains test-side access")
    for dataset in value.get("datasets", []):
        workload = dataset.get("valid_workload", {})
        if (
            workload.get("query_count") != QUERY_COUNT
            or len(workload.get("queries", [])) != QUERY_COUNT
        ):
            raise ValueError(f"{dataset.get('dataset_id')} preflight query manifest is incomplete")
        ids = [item.get("query_id") for item in workload["queries"]]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{dataset.get('dataset_id')} preflight query IDs are duplicated")
        if dataset.get("permutations") != deterministic_orders(dataset.get("reference_order", [])):
            raise ValueError(f"{dataset.get('dataset_id')} preflight permutation drift")
    return {
        "status": "valid",
        "semantic_digest": value["semantic_digest"],
        "dataset_count": len(value["datasets"]),
    }


def write_protocol_and_preflight(
    *, root: Path, data_root: Path, producer_sha: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    protocol = build_protocol(root=root, producer_sha=producer_sha)
    write_json(root / PROTOCOL_PATH, protocol)
    preflight = build_preflight(root=root, data_root=data_root, producer_sha=producer_sha)
    write_json(root / PREFLIGHT_PATH, preflight)
    validate_preflight(preflight, root=root)
    return protocol, preflight


def _run_command(command: Sequence[str], log_dir: Path) -> dict[str, Any]:
    log_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    elapsed = time.monotonic() - started
    stem = f"command-{len(list(log_dir.glob('command-*.stdout'))):04d}"
    (log_dir / f"{stem}.stdout").write_text(result.stdout, encoding="utf-8")
    (log_dir / f"{stem}.stderr").write_text(result.stderr, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(command)}")
    return {
        "returncode": 0,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "elapsed_seconds": elapsed,
    }


def _plan_rows(document: Any, relation: str) -> int:
    document = document[0] if isinstance(document, list) else document
    nodes: list[dict[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if (
                value.get("Relation Name") == relation.split(".")[-1]
                and value.get("Schema") == relation.split(".")[0]
            ):
                nodes.append(value)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(document)
    if len(nodes) != 1:
        raise ValueError(f"expected one managed base-relation plan node for {relation}")
    return int(nodes[0]["Plan Rows"])


def _explain(connection: Any, sql: str, relation: str) -> tuple[int, str]:
    document = connection.execute(f"EXPLAIN (VERBOSE, FORMAT JSON) {sql}").fetchone()[0]
    if isinstance(document, str):
        document = json.loads(document)
    return _plan_rows(document, relation), semantic_digest(document)


def _query_records(
    snapshot: Any, truth_by_id: Mapping[str, int], relation: str
) -> list[dict[str, Any]]:
    queries = []
    for query in snapshot.workload.queries:
        if query.query_id not in truth_by_id:
            raise ValueError(f"valid truth is missing {query.query_id}")
        queries.append(
            {
                "query_id": query.query_id,
                "sql": query.sql,
                "truth": int(truth_by_id[query.query_id]),
                "weight": float(query.weight),
            }
        )
    if len(queries) != QUERY_COUNT:
        raise ValueError("snapshot valid workload does not contain exactly 10000 queries")
    return queries


def _aggregate(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    values = sorted(float(record["qerror"]) for record in records)
    if not values:
        raise ValueError("cannot aggregate empty query records")

    def percentile(fraction: float) -> float:
        position = (len(values) - 1) * fraction
        lower = int(position)
        upper = min(lower + 1, len(values) - 1)
        return values[lower] + (values[upper] - values[lower]) * (position - lower)

    return {
        "query_count": len(values),
        "mean": sum(values) / len(values),
        "p50": percentile(0.5),
        "p95": percentile(0.95),
        "p99": percentile(0.99),
        "max": max(values),
    }


def _paired_against(
    records: Sequence[Mapping[str, Any]], reference: Mapping[str, Mapping[str, Any]]
) -> dict[str, int]:
    counts = {"improved": 0, "unchanged": 0, "worsened": 0}
    for record in records:
        ref = float(reference[record["query_id"]]["qerror"])
        value = float(record["qerror"])
        key = "improved" if value < ref else "worsened" if value > ref else "unchanged"
        counts[key] += 1
    return counts


def _write_gzip_jsonl(path: Path, records: Sequence[Mapping[str, Any]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", mtime=0) as stream:
        for record in records:
            stream.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
    return sha256_file(path)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream]


def _connection_settings(connection: Any) -> dict[str, str]:
    names = (
        "DateStyle",
        "default_statistics_target",
        "enable_bitmapscan",
        "enable_indexscan",
        "enable_seqscan",
        "jit",
        "plan_cache_mode",
        "search_path",
        "timezone",
        "transaction_isolation",
        "transaction_read_only",
    )
    return {name: str(connection.execute(f"SHOW {name}").fetchone()[0]) for name in names}


def _candidate_name(candidate_id: str) -> str:
    return f"oid_order_{candidate_id.removeprefix('cand_')}"


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _physical_arm(
    *,
    dsn: str,
    parent_database: str,
    relation: str,
    candidates: Mapping[str, Mapping[str, Any]],
    order: Sequence[str],
    queries: Sequence[Mapping[str, Any]],
    truth_by_id: Mapping[str, int],
    output: Path,
    dataset_id: str,
) -> dict[str, Any]:
    """Build one disposable stock clone, evaluate valid queries, and remove it."""

    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    fields = conninfo_to_dict(dsn)
    clone = f"oid_order_{dataset_id.removeprefix('arecel-')}_{time.time_ns() % 10**10:010d}"
    admin = dict(fields)
    admin["dbname"] = "postgres"
    clone_fields = dict(fields)
    clone_fields["dbname"] = clone
    admin_dsn = make_conninfo(**admin)
    clone_dsn = make_conninfo(**clone_fields)
    schema, relation_name = relation.split(".", 1)
    names: list[str] = []
    try:
        with psycopg.connect(admin_dsn, autocommit=True) as conn:
            conn.execute(
                f"CREATE DATABASE {_quote_identifier(clone)} TEMPLATE {_quote_identifier(parent_database)}"
            )
        with psycopg.connect(clone_dsn, autocommit=True) as conn:
            for candidate_id in order:
                candidate = candidates[candidate_id]
                name = _candidate_name(candidate_id)
                names.append(name)
                kind = "mcv" if candidate["kind"] == "postgresql.mcv" else "dependencies"
                columns = ", ".join(
                    _quote_identifier(str(value)) for value in candidate["column_names"]
                )
                conn.execute(
                    f"CREATE STATISTICS {_quote_identifier(schema)}.{_quote_identifier(name)} ({kind}) ON {columns} FROM {_quote_identifier(schema)}.{_quote_identifier(relation_name)}"
                )
                conn.execute(
                    f"ALTER STATISTICS {_quote_identifier(schema)}.{_quote_identifier(name)} SET STATISTICS {STATISTICS_TARGET}"
                )
            conn.execute(f"ANALYZE {_quote_identifier(schema)}.{_quote_identifier(relation_name)}")
            settings = _connection_settings(conn)
            rows = conn.execute(
                "SELECT e.oid::bigint, e.stxname, e.stxkind::text, e.stxkeys::text FROM pg_catalog.pg_statistic_ext e JOIN pg_catalog.pg_namespace n ON n.oid=e.stxnamespace JOIN pg_catalog.pg_class c ON c.oid=e.stxrelid WHERE n.nspname=%s AND c.relname=%s ORDER BY e.oid",
                (schema, relation_name),
            ).fetchall()
            by_name = {str(row[1]): row for row in rows}
            physical_objects = []
            for candidate_id, name in zip(order, names, strict=True):
                row = by_name[name]
                physical_objects.append(
                    {
                        "candidate_id": candidate_id,
                        "oid": int(row[0]),
                        "name": name,
                        "catalog_kind": str(row[2]),
                        "keys": str(row[3]),
                    }
                )
            records = []
            for query in queries:
                plan_rows, explain_digest = _explain(conn, str(query["sql"]), relation)
                truth = int(truth_by_id[query["query_id"]])
                records.append(
                    {
                        "dataset_id": dataset_id,
                        "query_id": query["query_id"],
                        "sql_sha256": _sha256_text(str(query["sql"])),
                        "truth": truth,
                        "plan_rows": plan_rows,
                        "qerror": qerror(plan_rows, truth),
                        "explain_sha256": explain_digest,
                    }
                )
            sys.path.insert(0, "/home/wqts/projects/extstats-advisor/src")
            from extstats_advisor.dbms.postgres.ordinary_stats import ordinary_stats_fingerprint

            rel_oid = int(conn.execute("SELECT to_regclass(%s)::oid", (relation,)).fetchone()[0])
            fingerprint = ordinary_stats_fingerprint(conn, rel_oid)
        digest = _write_gzip_jsonl(output, records)
        return {
            "status": "complete",
            "execution_arm": "stock-physical",
            "per_query_path": output.as_posix(),
            "per_query_sha256": digest,
            "metrics": _aggregate(records),
            "physical_oids": physical_objects,
            "prescribed_order": list(order),
            "actual_oid_order": [
                item["candidate_id"]
                for item in sorted(physical_objects, key=lambda item: item["oid"])
            ],
            "payload_sha256_by_candidate": {},
            "ordinary_statistics_fingerprint": fingerprint,
            "planner_settings": settings,
            "analyze_count": 1,
            "causal_status": "pending-payload-control",
        }
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as conn:
            conn.execute(f"DROP DATABASE IF EXISTS {_quote_identifier(clone)} WITH (FORCE)")


def _hypothetical_arms(
    *,
    planner_dsn: str,
    advisor_root: Path,
    snapshot_path: Path,
    candidate_path: Path,
    native_path: Path,
    orders: Sequence[Mapping[str, Any]],
    queries: Sequence[Mapping[str, Any]],
    truth_by_id: Mapping[str, int],
    relation: str,
    output_dir: Path,
) -> dict[str, dict[str, Any]]:
    sys.path.insert(0, str(advisor_root / "src"))
    from extstats_advisor.candidates import load_candidate_universe
    from extstats_advisor.dbms.postgres.planner import (
        PostgresPlannerSession,
        PostgresStatisticsConfiguration,
    )
    from extstats_advisor.native_stats import load_native_stats_repository
    from extstats_advisor.snapshot.bundle import load_snapshot

    snapshot = load_snapshot(snapshot_path)
    universe = load_candidate_universe(candidate_path, snapshot)
    repository = load_native_stats_repository(native_path)
    results: dict[str, dict[str, Any]] = {}
    with PostgresPlannerSession(planner_dsn, snapshot, universe, repository) as planner:
        for arm in orders:
            order = tuple(arm["order"])
            planner.activate(PostgresStatisticsConfiguration(order))
            active_oids = list(planner.active_backend_oids())
            records = []
            for query in queries:
                plan_rows, explain_digest = _explain(
                    planner.connection, str(query["sql"]), relation
                )
                truth = int(truth_by_id[query["query_id"]])
                records.append(
                    {
                        "dataset_id": relation.split(".")[1],
                        "query_id": query["query_id"],
                        "sql_sha256": _sha256_text(str(query["sql"])),
                        "truth": truth,
                        "plan_rows": plan_rows,
                        "qerror": qerror(plan_rows, truth),
                        "explain_sha256": explain_digest,
                    }
                )
            path = (
                output_dir
                / "controlled-hypothetical"
                / arm["permutation_id"]
                / "per-query.jsonl.gz"
            )
            digest = _write_gzip_jsonl(path, records)
            results[arm["permutation_id"]] = {
                "status": "complete",
                "execution_arm": "controlled-hypothetical",
                "per_query_path": path.as_posix(),
                "per_query_sha256": digest,
                "metrics": _aggregate(records),
                "prescribed_order": list(order),
                "active_virtual_oids": active_oids,
                "planner_settings": _connection_settings(planner.connection),
                "causal_status": "controlled-order-only",
            }
    return results


def validate_result_artifact(path: Path) -> dict[str, Any]:
    value = read_json(path)
    if value.get("format_version") != SUMMARY_FORMAT:
        raise ValueError("unsupported OID-order summary format")
    if value.get("semantic_digest") != semantic_digest(_body(value)):
        raise ValueError("OID-order summary semantic digest mismatch")
    return {
        "status": "valid",
        "semantic_digest": value["semantic_digest"],
        "dataset_count": len(value.get("datasets", [])),
    }


def run_formal(
    *,
    root: Path,
    preflight_path: Path,
    data_root: Path,
    stock_dsn: str,
    planner_dsn: str,
    advisor_root: Path,
    patched_postgres_root: Path,
    stock_postgres_root: Path,
    producer_sha: str,
) -> dict[str, Any]:
    """Run the declared four-dataset experiment once; no retry is internal."""

    preflight = read_json(preflight_path)
    validate_preflight(preflight, root=root)
    if preflight.get("producer_research_sha") != producer_sha:
        raise ValueError("preflight producer SHA does not match formal producer")
    from .advisor_bridge import materialize_native_repository
    from .postgres.loader import load_census13, load_dmv11, load_forest10, load_power7
    from .postgres_lab import reinit_role

    loaders = {
        "arecel-census13": load_census13,
        "arecel-forest10": load_forest10,
        "arecel-power7": load_power7,
        "arecel-dmv11": load_dmv11,
    }
    dataset_results = []
    try:
        for dataset in preflight["datasets"]:
            spec = next(item for item in DATASET_SPECS if item.dataset_id == dataset["dataset_id"])
            runtime = root / ".runtime" / "oid-order-sensitivity-v1" / spec.runtime_name
            runtime.mkdir(parents=True, exist_ok=False)
            log_dir = runtime / "logs"
            reinit_role("stock")
            reinit_role("patched")
            load = loaders[spec.dataset_id](
                stock_dsn,
                data_root=data_root,
                reset_disposable=True,
                statistics_target=STATISTICS_TARGET,
                seed_identifier=123,
            )
            if (
                load.get("physical_extended_statistics_count") != 0
                or load.get("analyze_count") != 1
            ):
                raise ValueError(f"{spec.dataset_id} fresh stock load contract failed")
            workload_path = runtime / "valid-workload.json"
            spec.dataset_module.extract_workload(workload_path, data_root, split="valid")
            workload = read_json(workload_path)
            sys.path.insert(0, str(advisor_root / "src"))
            from extstats_advisor.candidates import (
                derive_candidate_universe,
                write_candidate_universe,
            )
            from extstats_advisor.dbms.postgres.acquisition import (
                AcquisitionRequest,
                PostgresSnapshotAcquirer,
            )
            from extstats_advisor.ground_truth.artifact import write_ground_truth_set
            from extstats_advisor.ground_truth.external import import_authoritative_ground_truth
            from extstats_advisor.snapshot.bundle import load_snapshot, write_snapshot
            from extstats_advisor.snapshot.model import SamplePolicy, Workload, WorkloadQuery

            advisor_truth = root / spec.valid_observations_path
            w = Workload(
                workload["workload_id"],
                tuple(
                    WorkloadQuery(item["query_id"], item["sql"], item["weight"])
                    for item in workload["queries"]
                ),
                workload.get("provenance", {}),
            )
            snapshot_path = runtime / "snapshot"
            snapshot = PostgresSnapshotAcquirer(stock_dsn).capture(
                AcquisitionRequest(
                    spec.dataset_module.RELATION, SamplePolicy(SAMPLE_ROWS, seed=SAMPLE_SEED)
                ),
                w,
            )
            write_snapshot(snapshot, snapshot_path)
            truth_set = import_authoritative_ground_truth(
                snapshot_path,
                advisor_truth,
                authority="sfu-db/AreCELearnedYet",
                dataset_identity=spec.dataset_content_identity,
                source_revision="aa52da7768023270bad884232972e0b77ec6534a",
            )
            truth_path = runtime / "ground-truth.json"
            write_ground_truth_set(truth_set, truth_path)
            candidate_path = runtime / "candidate-universe.json"
            universe = derive_candidate_universe(load_snapshot(snapshot_path))
            write_candidate_universe(universe, candidate_path)
            native_path = runtime / "native-stats-repository"
            materialize_native_repository(
                advisor_root,
                planner_dsn,
                snapshot_path,
                candidate_path,
                native_path,
                STATISTICS_TARGET,
            )
            _run_command(
                [str(advisor_root / ".venv" / "bin" / "python"), "-c", "pass"], log_dir
            ) if False else None
            _run_command(
                [
                    str(advisor_root / ".venv" / "bin" / "extstats-advisor"),
                    "sandbox",
                    "prepare",
                    "postgres",
                    str(snapshot_path),
                    str(candidate_path),
                    str(native_path),
                    "--dsn",
                    planner_dsn,
                ],
                log_dir,
            )
            _run_command(
                [
                    str(advisor_root / ".venv" / "bin" / "extstats-advisor"),
                    "sandbox",
                    "verify",
                    "postgres",
                    str(snapshot_path),
                    str(candidate_path),
                    str(native_path),
                    "--dsn",
                    planner_dsn,
                ],
                log_dir,
            )
            truth_wire = read_json(advisor_truth)
            truth_by_id = {row["query_id"]: int(row["cardinality"]) for row in truth_wire["truths"]}
            queries = [
                {"query_id": item.query_id, "sql": item.sql}
                for item in load_snapshot(snapshot_path).workload.queries
            ]
            candidates = {
                item["candidate_id"]: item for item in read_json(candidate_path)["candidates"]
            }
            hypothetical = _hypothetical_arms(
                planner_dsn=planner_dsn,
                advisor_root=advisor_root,
                snapshot_path=snapshot_path,
                candidate_path=candidate_path,
                native_path=native_path,
                orders=dataset["permutations"],
                queries=queries,
                truth_by_id=truth_by_id,
                relation=spec.dataset_module.RELATION,
                output_dir=runtime,
            )
            physical = {}
            parent_db = "extstats_lab"
            for arm in dataset["permutations"]:
                physical[arm["permutation_id"]] = _physical_arm(
                    dsn=stock_dsn,
                    parent_database=parent_db,
                    relation=spec.dataset_module.RELATION,
                    candidates=candidates,
                    order=arm["order"],
                    queries=queries,
                    truth_by_id=truth_by_id,
                    output=runtime
                    / "physical-stock"
                    / arm["permutation_id"]
                    / "per-query.jsonl.gz",
                    dataset_id=spec.dataset_id,
                )
            _run_command(
                [
                    str(advisor_root / ".venv" / "bin" / "extstats-advisor"),
                    "sandbox",
                    "destroy",
                    "postgres",
                    "--dsn",
                    planner_dsn,
                ],
                log_dir,
            )
            dataset_results.append(
                {
                    "dataset_id": spec.dataset_id,
                    "snapshot_digest": load_snapshot(snapshot_path).semantic_digest,
                    "native_repository_digest": read_json(native_path / "manifest.json")[
                        "semantic_digest"
                    ],
                    "hypothetical": hypothetical,
                    "physical": physical,
                    "candidate_payload_inventory": {},
                }
            )
            shutil.rmtree(runtime, ignore_errors=True)
        return {
            "status": "complete",
            "formal_invocation_count": 1,
            "dataset_results": dataset_results,
        }
    finally:
        reinit_role("stock")
        reinit_role("patched")
