"""Producer-bound formal runner for Native ANALYZE Stability v1.

This module is intentionally narrow.  It reuses the validated RQ4 stock
shared-realization executor and adds only the missing formal-campaign
boundaries: source preflight, invocation-scoped database preparation, raw
gzip evidence, realization accounting, and independent offline validation.
It is never called by an offline dry-run and requires an explicit DSN for a
dedicated stock lab instance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .native_analyze_stability import (
    METHOD_ORDER,
    PRIMARY_DATASETS,
    _dataset_binding,
    validate_protocol,
)
from .native_analyze_stability_harness import write_query_records
from .provenance import read_json, semantic_digest, sha256_file, write_json
from .rq4_physical import build_shared_stock_realization

FORMAL_PREFLIGHT_FORMAT = "native-analyze-stability-formal-preflight-v1"
FORMAL_INVOCATION_FORMAT = "native-analyze-stability-formal-invocation-v1"
FORMAL_REALIZATION_FORMAT = "native-analyze-stability-formal-realization-v1"
FORMAL_SUMMARY_FORMAT = "native-analyze-stability-formal-summary-v1"
FORMAL_FAILURE_FORMAT = "native-analyze-stability-formal-failure-v1"

SOURCE_RUNS = {
    "arecel-forest10": ".runtime/rq1-forest10-matched/advisor-design/2d6213e57c49975ef7a70c9c",
    "arecel-census13": ".runtime/rq2-formal-v7/census13/canonical/6fab9c14bec4c26ad44ff42f",
    "arecel-dmv11": ".runtime/rq2-formal-v4/dmv11/canonical/8af4a1d328125d01258bb894",
}


def _body(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "semantic_digest"}


def _digest(value: dict[str, Any]) -> dict[str, Any]:
    value["semantic_digest"] = semantic_digest(_body(value))
    return value


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sql_digest(sql: str) -> str:
    return _sha256_bytes(sql.encode("utf-8"))


def _source_paths(root: Path, dataset_id: str) -> dict[str, Path]:
    source = root / SOURCE_RUNS[dataset_id]
    return {
        "root": source,
        "manifest": source / "manifest.json",
        "snapshot": source / "advisor-snapshot",
        "candidate_universe": source / "candidate-universe.json",
        "native_repository": source / "native-stats-repository",
        "ground_truth": source / "ground-truth-v1.json",
    }


def _advisor_modules(advisor_root: Path) -> dict[str, Any]:
    import sys

    source = str(advisor_root.resolve() / "src")
    if source not in sys.path:
        sys.path.insert(0, source)
    from extstats_advisor.candidates import load_candidate_universe
    from extstats_advisor.ground_truth import load_ground_truth_set
    from extstats_advisor.native_stats import load_native_stats_repository
    from extstats_advisor.snapshot.bundle import load_snapshot

    return {
        "load_snapshot": load_snapshot,
        "load_candidate_universe": load_candidate_universe,
        "load_native_stats_repository": load_native_stats_repository,
        "load_ground_truth_set": load_ground_truth_set,
    }


def _load_source(root: Path, dataset_id: str, advisor_root: Path) -> dict[str, Any]:
    paths = _source_paths(root, dataset_id)
    if not all(path.exists() for path in paths.values()):
        missing = [str(path) for path in paths.values() if not path.exists()]
        raise FileNotFoundError(f"frozen source bundle is incomplete: {missing}")
    modules = _advisor_modules(advisor_root)
    snapshot = modules["load_snapshot"](paths["snapshot"])
    universe = modules["load_candidate_universe"](paths["candidate_universe"], snapshot)
    native = modules["load_native_stats_repository"](paths["native_repository"])
    truth = modules["load_ground_truth_set"](paths["ground_truth"], snapshot)
    binding = _dataset_binding(root, dataset_id)
    if len(snapshot.workload.queries) != 10000 or len(truth.truths) != 10000:
        raise ValueError(f"{dataset_id} does not bind exactly 10,000 queries/truths")
    truth_by_id = {item.query_id: int(item.cardinality) for item in truth.truths}
    query_ids = [query.query_id for query in snapshot.workload.queries if query.weight > 0]
    if len(query_ids) != 10000 or len(set(query_ids)) != 10000:
        raise ValueError(f"{dataset_id} workload query identity is incomplete")
    if set(query_ids) != set(truth_by_id):
        raise ValueError(f"{dataset_id} workload/truth query identity mismatch")
    selected = binding["method_memberships"]
    source_candidates = {item.candidate_id: item for item in universe.candidates}
    missing = sorted(
        {candidate for ids in selected.values() for candidate in ids} - set(source_candidates)
    )
    if missing:
        raise ValueError(
            f"{dataset_id} selected candidates missing from source universe: {missing}"
        )
    artifact_digests = {
        "snapshot": snapshot.semantic_digest,
        "candidate_universe": universe.semantic_digest,
        "native_repository": native.semantic_digest,
        "ground_truth": truth.computed_semantic_digest,
    }
    manifest = read_json(paths["manifest"])
    expected_manifest = {
        "benchmark_id": dataset_id,
        "workload_id": snapshot.workload.workload_id,
        "advisor_commit_sha": manifest.get("advisor_commit_sha"),
        "patched_postgres_commit_sha": manifest.get("patched_postgres_commit_sha"),
        "stock_postgres_commit_sha": manifest.get("stock_postgres_commit_sha"),
    }
    return {
        "dataset_id": dataset_id,
        "source_paths": {key: str(path.relative_to(root)) for key, path in paths.items()},
        "source_manifest_sha256": sha256_file(paths["manifest"]),
        "source_manifest_semantic_digest": manifest.get("semantic_digest"),
        "source_artifact_digests": artifact_digests,
        "source_manifest_identity": expected_manifest,
        "workload": {
            "workload_id": snapshot.workload.workload_id,
            "query_count": len(query_ids),
            "query_id_digest": semantic_digest(query_ids),
            "sql_digest": semantic_digest(
                [
                    _sql_digest(str(query.sql))
                    for query in snapshot.workload.queries
                    if query.weight > 0
                ]
            ),
            "first_query_id": query_ids[0],
            "last_query_id": query_ids[-1],
        },
        "truth": {
            "source_kind": str(truth.source),
            "truth_semantic_digest": truth.computed_semantic_digest,
            "cardinality_vector_digest": semantic_digest(
                [(qid, truth_by_id[qid]) for qid in query_ids]
            ),
            "query_count": len(truth_by_id),
            "source_sha256": manifest.get("truth_capture", {}).get("observations_sha256"),
        },
        "method_memberships": {method: list(selected[method]) for method in METHOD_ORDER},
        "union_candidate_ids": sorted(
            {candidate for ids in selected.values() for candidate in ids}
        ),
        "candidate_definition_digest": semantic_digest(
            {
                candidate_id: {
                    "candidate_id": candidate_id,
                    "kind": source_candidates[candidate_id].kind,
                    "column_names": list(source_candidates[candidate_id].column_names),
                    "column_ordinals": list(source_candidates[candidate_id].column_ordinals),
                }
                for candidate_id in sorted(
                    {candidate for ids in selected.values() for candidate in ids}
                )
            }
        ),
        "system_freeze_path": binding["system_freeze_artifact"]["path"],
        "system_freeze_semantic_digest": binding["system_freeze_semantic_digest"],
        "comparability_stratum": binding["comparability_stratum"],
        "statistics_target": 100,
    }


def build_formal_preflight(
    root: Path, *, producer_sha: str, invocation_id: str, advisor_root: Path
) -> dict[str, Any]:
    protocol = read_json(root / "paper/native-analyze-stability-protocol-v1.json")
    validate_protocol(protocol, root)
    from .pins import verify_git_sha

    verify_git_sha(advisor_root, "e0aa1ad736deb77cf0c05e3befb2b1e772bc7da3")
    freeze = read_json(root / "paper/system-freeze-v2.json")
    datasets = {
        dataset_id: _load_source(root, dataset_id, advisor_root) for dataset_id in PRIMARY_DATASETS
    }
    for dataset_id, source in datasets.items():
        expected_advisor = (
            "0865c5a6afb8bc176bd7d3b10b13b3da83f1f641"
            if dataset_id == "arecel-forest10"
            else "e0aa1ad736deb77cf0c05e3befb2b1e772bc7da3"
        )
        if source["source_manifest_identity"]["advisor_commit_sha"] != expected_advisor:
            raise ValueError(f"{dataset_id} source manifest has unexpected Advisor revision")
        frozen_stock_sha = freeze["stock_postgresql"].get(
            "source_commit_sha", freeze["stock_postgresql"].get("commit_sha")
        )
        source_stock_sha = source["source_manifest_identity"]["stock_postgres_commit_sha"]
        if source_stock_sha is None:
            if dataset_id != "arecel-forest10":
                raise ValueError(f"{dataset_id} source manifest lacks stock revision")
            historical_freeze = read_json(root / "paper/system-freeze-v1.json")
            historical_stock = historical_freeze["stock_postgresql"].get(
                "source_commit_sha", historical_freeze["stock_postgresql"].get("commit_sha")
            )
            if historical_stock != frozen_stock_sha:
                raise ValueError(
                    f"{dataset_id} historical freeze does not bind frozen stock revision"
                )
            historical_patched = historical_freeze["patched_postgresql"].get(
                "source_commit_sha", historical_freeze["patched_postgresql"].get("commit_sha")
            )
            if (
                source["source_manifest_identity"]["patched_postgres_commit_sha"]
                != historical_patched
            ):
                raise ValueError(f"{dataset_id} historical planner binding is inconsistent")
            source["source_manifest_identity"]["stock_postgres_commit_sha"] = frozen_stock_sha
            source["source_manifest_identity"]["stock_binding_origin"] = (
                "paper/system-freeze-v1.json"
            )
        elif source_stock_sha != frozen_stock_sha:
            raise ValueError(f"{dataset_id} source manifest has unexpected stock revision")
    value: dict[str, Any] = {
        "format_version": FORMAL_PREFLIGHT_FORMAT,
        "preflight_id": f"{invocation_id}-preflight-v1",
        "invocation_id": invocation_id,
        "status": "passed-before-formal-sql",
        "producer_sha": producer_sha,
        "protocol": {
            "path": "paper/native-analyze-stability-protocol-v1.json",
            "semantic_digest": protocol["semantic_digest"],
            "sha256": sha256_file(root / "paper/native-analyze-stability-protocol-v1.json"),
        },
        "system_freeze": {
            "v2_path": "paper/system-freeze-v2.json",
            "v2_semantic_digest": semantic_digest(_body(freeze)),
            "stock_postgresql": freeze["stock_postgresql"],
        },
        "formal_scope": {
            "datasets": list(PRIMARY_DATASETS),
            "realizations_per_dataset": 5,
            "methods_per_realization": 9,
            "queries_per_method": 10000,
            "parent_analyze_calls": 15,
            "clone_analyze_calls": 0,
            "physical_explain_calls": 1_350_000,
            "rq1b_w_test_accessed": False,
            "power7_included": False,
        },
        "datasets": datasets,
        "source_verification": {
            "historical_artifacts_unchanged": True,
            "membership_reselection": False,
            "union_order": "lexicographic candidate_id order",
            "method_roster": list(METHOD_ORDER),
            "truth_policy": "authoritative valid fixed-k truth imported from frozen source bundle",
        },
    }
    return _digest(value)


def validate_formal_preflight(
    path: Path, root: Path, *, expected_producer_sha: str | None = None
) -> dict[str, Any]:
    value = read_json(path)
    if value.get("format_version") != FORMAL_PREFLIGHT_FORMAT:
        raise ValueError("unsupported formal preflight")
    if value.get("semantic_digest") != semantic_digest(_body(value)):
        raise ValueError("formal preflight semantic digest mismatch")
    if expected_producer_sha is not None and value.get("producer_sha") != expected_producer_sha:
        raise ValueError("formal preflight producer mismatch")
    protocol_path = root / value["protocol"]["path"]
    protocol = read_json(protocol_path)
    validate_protocol(protocol, root)
    if value["protocol"] != {
        "path": value["protocol"]["path"],
        "semantic_digest": protocol["semantic_digest"],
        "sha256": sha256_file(protocol_path),
    }:
        raise ValueError("formal preflight protocol binding mismatch")
    if (
        value["formal_scope"]["parent_analyze_calls"] != 15
        or value["formal_scope"]["physical_explain_calls"] != 1_350_000
    ):
        raise ValueError("formal scope count drift")
    for dataset_id, binding in value["datasets"].items():
        current = _load_source(root, dataset_id, Path("/home/wqts/projects/extstats-advisor"))
        for key in (
            "workload",
            "truth",
            "method_memberships",
            "union_candidate_ids",
            "candidate_definition_digest",
            "source_artifact_digests",
        ):
            if binding[key] != current[key]:
                raise ValueError(f"formal preflight source binding drifted: {dataset_id}:{key}")
    return {
        "status": "valid",
        "semantic_digest": value["semantic_digest"],
        "datasets": list(value["datasets"]),
    }


def _database_name(prefix: str, invocation_id: str, dataset_id: str, suffix: str) -> str:
    token = hashlib.sha256(f"{invocation_id}:{dataset_id}:{suffix}".encode()).hexdigest()[:12]
    return f"nas_{prefix}_{token}"[:sixty_three]


sixty_three = 63


def _connect(dsn: str) -> Any:
    import psycopg

    return psycopg.connect(
        dsn, autocommit=True, application_name="extstats-research-native-stability"
    )


def _admin_dsn(dsn: str, database: str = "postgres") -> str:
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    fields = conninfo_to_dict(dsn)
    fields["dbname"] = database
    return make_conninfo(**fields)


def _quote(value: str) -> str:
    if not value or "\x00" in value:
        raise ValueError("invalid SQL identifier")
    return '"' + value.replace('"', '""') + '"'


def _create_database(admin_dsn: str, database: str, template: str | None = None) -> None:
    conn = _connect(admin_dsn)
    try:
        statement = f"CREATE DATABASE {_quote(database)}"
        if template is not None:
            statement += f" TEMPLATE {_quote(template)}"
        conn.execute(statement)
    finally:
        conn.close()


def _drop_database(admin_dsn: str, database: str) -> None:
    conn = _connect(admin_dsn)
    try:
        conn.execute(f"DROP DATABASE IF EXISTS {_quote(database)} WITH (FORCE)")
    finally:
        conn.close()


def _load_without_analyze(dsn: str, dataset_id: str, data_root: Path) -> dict[str, Any]:
    from .datasets import census13, dmv11, forest10

    datasets = {item.BENCHMARK_ID: item for item in (forest10, census13, dmv11)}
    dataset = datasets[dataset_id]
    metadata = dataset.inspect(data_root)
    csv_path = dataset.csv_path(data_root)
    relation = dataset.RELATION
    schema, relation_name = relation.split(".", 1)
    conn = _connect(dsn)
    try:
        columns = dataset.COLUMNS
        definitions = ", ".join(
            f"{_quote(name)} {type_}{' NOT NULL' if dataset_id == 'arecel-census13' else ''}"
            for name, type_ in columns
        )
        conn.execute(f"CREATE TABLE {_quote(schema)}.{_quote(relation_name)} ({definitions})")
        column_sql = ", ".join(_quote(name) for name, _ in columns)
        with (
            csv_path.open("rb") as stream,
            conn.cursor() as cursor,
            cursor.copy(
                f"COPY {_quote(schema)}.{_quote(relation_name)} ({column_sql}) FROM STDIN WITH (FORMAT csv, HEADER true)"
            ) as copy,
        ):
            while block := stream.read(1024 * 1024):
                copy.write(block)
        for name, _ in columns:
            conn.execute(
                f"ALTER TABLE {_quote(schema)}.{_quote(relation_name)} ALTER COLUMN {_quote(name)} SET STATISTICS 100"
            )
        count = int(
            conn.execute(
                f"SELECT count(*) FROM {_quote(schema)}.{_quote(relation_name)}"
            ).fetchone()[0]
        )
        if count != dataset.EXPECTED_ROWS:
            raise ValueError(
                f"{dataset_id} loaded row count {count}, expected {dataset.EXPECTED_ROWS}"
            )
        stats = conn.execute(
            "SELECT count(*) FROM pg_catalog.pg_statistic_ext AS e "
            "JOIN pg_catalog.pg_class AS c ON c.oid=e.stxrelid "
            "JOIN pg_catalog.pg_namespace AS n ON n.oid=c.relnamespace "
            "WHERE n.nspname=%s AND c.relname=%s",
            (schema, relation_name),
        ).fetchone()[0]
        if int(stats) != 0:
            raise ValueError("data preparation created unexpected extended statistics")
        return {
            "dataset_id": dataset_id,
            "relation": relation,
            "source_csv_sha256": metadata.get("csv_sha256")
            or metadata.get("source_file_sha256", {}).get("csv"),
            "row_count": count,
            "pre_realization_analyze_count": 0,
            "extended_statistics_count": int(stats),
        }
    finally:
        conn.close()


def _query_control(dsn: str, dataset_id: str) -> dict[str, Any]:
    from .datasets import census13, dmv11, forest10

    relation = {x.BENCHMARK_ID: x.RELATION for x in (forest10, census13, dmv11)}[dataset_id]
    conn = _connect(dsn)
    try:
        row = conn.execute("SELECT to_regclass(%s)::oid", (relation,)).fetchone()
        if row is None or row[0] is None:
            raise ValueError(f"missing relation {relation}")
        return {"relation": relation, "relation_oid": int(row[0]), "statistics": []}
    finally:
        conn.close()


def _strip_runtime_result(result: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(result)
    value.pop("plan_rows", None)
    return value


def run_dataset(
    *,
    root: Path,
    preflight_path: Path,
    output_root: Path,
    invocation_id: str,
    dataset_id: str,
    stock_dsn: str,
    data_root: Path,
    advisor_root: Path,
) -> dict[str, Any]:
    validate_formal_preflight(preflight_path, root)
    preflight = read_json(preflight_path)
    source = preflight["datasets"][dataset_id]
    if output_root.exists():
        raise FileExistsError(f"formal output namespace exists: {output_root}")
    output_root.mkdir(parents=True)
    admin_dsn = _admin_dsn(stock_dsn)
    dataset_tag = dataset_id.removeprefix("arecel-")
    base_db = _database_name(dataset_tag[:12], invocation_id, dataset_id, "base")
    created: list[str] = []
    parent_analyze = 0
    physical_explain = 0
    realization_records: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    try:
        _create_database(admin_dsn, base_db)
        created.append(base_db)
        base_dsn = _admin_dsn(stock_dsn, base_db)
        preparation = _load_without_analyze(base_dsn, dataset_id, data_root)
        for realization in range(1, 6):
            realization_id = f"{invocation_id}::{dataset_id}::realization-{realization:02d}"
            parent_db = _database_name(
                dataset_tag[:12], invocation_id, dataset_id, f"parent-{realization}"
            )
            events.append({"phase": "realization-started", "realization_id": realization_id})
            _create_database(admin_dsn, parent_db, base_db)
            created.append(parent_db)
            parent_dsn = _admin_dsn(stock_dsn, parent_db)
            relation_control = _query_control(parent_dsn, dataset_id)
            selected = source["method_memberships"]
            try:
                physical = build_shared_stock_realization(
                    stock_dsn=parent_dsn,
                    advisor_root=advisor_root,
                    snapshot_path=root / source["source_paths"]["snapshot"],
                    candidate_universe_path=root / source["source_paths"]["candidate_universe"],
                    ground_truth_path=root / source["source_paths"]["ground_truth"],
                    selected_by_method=selected,
                    system_freeze=read_json(root / source["system_freeze_path"]),
                    research_commit_sha=preflight["producer_sha"],
                    statistics_target=100,
                    query_count=10000,
                    dataset_id=dataset_id,
                    formal_confirmatory_experiment=True,
                    experiment_id=FORMAL_REALIZATION_FORMAT,
                    artifact_format="native-analyze-stability-physical-source-v1",
                    shared_realization_format="native-analyze-stability-shared-parent-v1",
                    expected_advisor_sha=source["source_manifest_identity"]["advisor_commit_sha"],
                )
            except Exception as exc:
                failure = _digest(
                    {
                        "format_version": FORMAL_FAILURE_FORMAT,
                        "failure_id": f"failure-{realization_id}",
                        "invocation_id": invocation_id,
                        "dataset_id": dataset_id,
                        "realization_id": realization_id,
                        "phase": "shared-realization",
                        "exception": {"class": type(exc).__name__, "message": str(exc)},
                        "events": events,
                        "analyze_count": parent_analyze,
                        "physical_explain_count": physical_explain,
                        "status": "failed",
                    }
                )
                write_json(output_root / f"failure-realization-{realization:02d}.json", failure)
                raise
            parent_analyze += 1
            methods: dict[str, Any] = {}
            raw_refs: dict[str, Any] = {}
            parent_stats = physical["shared_realization"].get("physical_statistics", [])
            oid_by_candidate = {item["candidate_id"]: item["oid"] for item in parent_stats}
            oid_sequence = [
                oid_by_candidate[candidate] for candidate in source["union_candidate_ids"]
            ]
            order_status = (
                "verified" if oid_sequence == sorted(oid_sequence) else "invalid-control-evidence"
            )
            for method in METHOD_ORDER:
                result = physical["methods"][method]
                records = result["plan_rows"]
                raw_path = (
                    output_root / "raw" / f"realization-{realization:02d}" / f"{method}.jsonl.gz"
                )
                raw_sha = write_query_records(raw_path, records)
                raw_refs[method] = {
                    "path": str(raw_path.relative_to(output_root)),
                    "sha256": raw_sha,
                    "query_count": len(records),
                }
                methods[method] = {
                    "method_id": method,
                    "selected_membership": list(result["selected_membership"]),
                    "metrics": result["metrics"],
                    "controls": {
                        "payloads_exactly_preserved_after_drop": result[
                            "payloads_exactly_preserved_after_drop"
                        ],
                        "ordinary_statistics_equal_to_parent": result[
                            "ordinary_statistics_equal_to_parent"
                        ],
                        "analyze_after_drop": result["analyze_after_drop"],
                        "post_drop_analyze_count": result["post_drop_analyze_count"],
                        "selected_deployed_membership_equal": result[
                            "selected_deployed_membership_equal"
                        ],
                    },
                    "query_evidence": raw_refs[method],
                    "source_result": _strip_runtime_result(result),
                }
                physical_explain += len(records)
            realization_record = _digest(
                {
                    "format_version": FORMAL_REALIZATION_FORMAT,
                    "invocation_id": invocation_id,
                    "dataset_id": dataset_id,
                    "realization_id": realization_id,
                    "status": "complete"
                    if order_status == "verified"
                    else "invalid-control-evidence",
                    "source_physical_semantic_digest": physical["semantic_digest"],
                    "relation_control": relation_control,
                    "canonical_union": source["union_candidate_ids"],
                    "observed_parent_oid_sequence": oid_sequence,
                    "oid_order_status": order_status,
                    "shared_parent": physical["shared_realization"],
                    "methods": methods,
                    "raw_evidence": raw_refs,
                    "analyze_count": physical["shared_realization"]["analyze_count"],
                    "physical_explain_count": sum(
                        item["query_evidence"]["query_count"] for item in methods.values()
                    ),
                    "cleanup_status": "complete",
                }
            )
            _drop_database(admin_dsn, parent_db)
            created.remove(parent_db)
            write_json(output_root / f"realization-{realization:02d}.json", realization_record)
            realization_records.append(realization_record)
        summary = _digest(
            {
                "format_version": FORMAL_SUMMARY_FORMAT,
                "invocation_id": invocation_id,
                "dataset_id": dataset_id,
                "status": "complete"
                if len(realization_records) == 5
                and all(item["status"] == "complete" for item in realization_records)
                else "invalid-control-evidence",
                "formal_confirmatory_experiment": True,
                "protocol_semantic_digest": preflight["protocol"]["semantic_digest"],
                "producer_sha": preflight["producer_sha"],
                "source_binding": source,
                "preparation": preparation,
                "realizations": realization_records,
                "completed_realizations": len(realization_records),
                "completed_method_arms": sum(len(item["methods"]) for item in realization_records),
                "analyze_count": parent_analyze,
                "clone_analyze_count": 0,
                "physical_explain_count": physical_explain,
                "valid_raw_observations": physical_explain,
                "rq1b_w_test_accessed": False,
                "scientific_eligibility": "eligible-only-if-offline-validator-passes-and-all-controls-verified",
            }
        )
        write_json(output_root / "summary-v1.json", summary)
        return summary
    finally:
        for database in reversed(created):
            _drop_database(admin_dsn, database)


def validate_formal_summary(path: Path) -> dict[str, Any]:
    value = read_json(path)
    if value.get("format_version") != FORMAL_SUMMARY_FORMAT:
        raise ValueError("unsupported Native stability formal summary")
    if value.get("semantic_digest") != semantic_digest(_body(value)):
        raise ValueError("formal summary semantic digest mismatch")
    realizations = value.get("realizations", [])
    if len(realizations) != 5 or value.get("completed_realizations") != 5:
        raise ValueError("formal dataset summary is incomplete")
    if (
        value.get("completed_method_arms") != 45
        or value.get("analyze_count") != 5
        or value.get("clone_analyze_count") != 0
        or value.get("physical_explain_count") != 450000
    ):
        raise ValueError("formal dataset accounting is incomplete")
    for realization in realizations:
        if (
            realization.get("status") != "complete"
            or realization.get("oid_order_status") != "verified"
        ):
            raise ValueError("formal realization is not causally eligible")
        if realization.get("physical_explain_count") != 90000:
            raise ValueError("formal realization query accounting mismatch")
        for method in METHOD_ORDER:
            arm = realization.get("methods", {}).get(method)
            if arm is None or arm.get("query_evidence", {}).get("query_count") != 10000:
                raise ValueError(f"formal arm is incomplete: {method}")
            raw = path.parent / arm["query_evidence"]["path"]
            if sha256_file(raw) != arm["query_evidence"]["sha256"]:
                raise ValueError(f"raw evidence hash mismatch: {raw}")
    return {
        "status": "valid",
        "semantic_digest": value["semantic_digest"],
        "dataset_id": value["dataset_id"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one formal Native ANALYZE Stability dataset")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--invocation", required=True)
    parser.add_argument("--dataset", choices=PRIMARY_DATASETS, required=True)
    parser.add_argument("--stock-dsn", required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--advisor-root", type=Path, required=True)
    parser.add_argument(
        "--authorize-formal-execution",
        action="store_true",
        help="required explicit live-execution gate for the authorized formal campaign",
    )
    args = parser.parse_args()
    if not args.authorize_formal_execution:
        parser.error("formal execution requires --authorize-formal-execution")
    result = run_dataset(
        root=args.root,
        preflight_path=args.preflight,
        output_root=args.output,
        invocation_id=args.invocation,
        dataset_id=args.dataset,
        stock_dsn=args.stock_dsn,
        data_root=args.data_root,
        advisor_root=args.advisor_root,
    )
    print(json.dumps({"status": result["status"], "semantic_digest": result["semantic_digest"]}))
    return 0 if result["status"] == "complete" else 2


__all__ = [
    "FORMAL_PREFLIGHT_FORMAT",
    "FORMAL_SUMMARY_FORMAT",
    "SOURCE_RUNS",
    "build_formal_preflight",
    "run_dataset",
    "validate_formal_preflight",
    "validate_formal_summary",
]


if __name__ == "__main__":
    raise SystemExit(main())
