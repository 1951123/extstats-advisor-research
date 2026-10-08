"""RQ5 static deployment-cost protocol and stock-only measurement harness.

Phase A (protocol, source projection, preflight, and tests) is offline.  Phase
B uses only stock PostgreSQL and the immutable deployment artifacts projected by
the preflight.  No Advisor selection, patched planner, truth acquisition, or
planner evaluation is implemented here.
"""

from __future__ import annotations

import math
import re
import subprocess
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from statistics import median
from typing import Any

from .datasets import census13, dmv11, forest10, power7
from .postgres.loader import load_census13, load_dmv11, load_forest10, load_power7
from .provenance import read_json, reject_credentials, semantic_digest, write_json
from .rq5_cost_inventory import RQ2_CHILDREN

PROTOCOL_FORMAT = "rq5-static-deployment-cost-protocol-v1"
FORMAL_FORMAT = "rq5-static-deployment-cost-v1"
PREFLIGHT_FORMAT = "rq5-static-deployment-cost-preflight-v1"
EXPERIMENT_ID = "rq5-static-deployment-cost"
PROTOCOL_PATH = "paper/rq5-static-deployment-cost-protocol-v1.json"
PROTOCOL_DIGEST = "01475d7979fb9c4bf027fbe43abd57c9cd06541134533712f3f258b47cc0f406"
STOCK_POSTGRES_SHA = "0d1c00c624fa7367d4a895f44381887757289682"
STOCK_POSTGRES_VERSION = "16.14"
DATASETS = ("arecel-census13", "arecel-forest10", "arecel-power7", "arecel-dmv11")
REPETITIONS = (1, 2, 3)
STAGE_HARD_CAP_SECONDS = 300.0

DEPLOYMENT_SOURCES = {
    "arecel-census13": {
        "path": "experiments/arecel-census13/rq2-confirmatory/deployment-result-v1.json",
        "semantic_digest": "51c6ab5eaace99629b8c51662633cd8d58f4798c4cd3914e13b03755319a79bf",
    },
    "arecel-forest10": {
        "path": "experiments/arecel-forest10/rq2-confirmatory/deployment-result-v1.json",
        "semantic_digest": "0d3eea2ea72e8d4283203fbe397f2d0b203f67f696d1182061f88278df7269e9",
    },
    "arecel-power7": {
        "path": "experiments/arecel-power7/rq2-confirmatory/deployment-result-v1.json",
        "semantic_digest": "1574371282ef4427569ac1159777433ac7874a64a5e8fc9eb9477ca2d0925fa7",
    },
    "arecel-dmv11": {
        "path": "experiments/arecel-dmv11/rq2-confirmatory/deployment-result-v1.json",
        "semantic_digest": "ab46e20c8a51b6e2e35ce7f41d3d1224d3af91dcaeee4aa5595d8d25c3d0734a",
    },
}

DATASET_SPECS = {
    "arecel-census13": (census13, load_census13),
    "arecel-forest10": (forest10, load_forest10),
    "arecel-power7": (power7, load_power7),
    "arecel-dmv11": (dmv11, load_dmv11),
}

KIND_SQL = {
    "postgresql.mcv": "mcv",
    "postgresql.dependencies": "dependencies",
    "postgresql.ndistinct": "ndistinct",
}
KIND_CODE = {
    "postgresql.mcv": "m",
    "postgresql.dependencies": "f",
    "postgresql.ndistinct": "d",
}


class RQ5StaticDeploymentValidationError(ValueError):
    """Raised when the static deployment protocol or evidence is invalid."""


def default_preflight_path(research_root: Path) -> Path:
    return research_root / "experiments/rq5-static-deployment-cost-preflight-v1.json"


def default_formal_path(research_root: Path) -> Path:
    return research_root / "experiments/rq5-static-deployment-cost-v1.json"


def default_raw_path(research_root: Path, dataset_id: str) -> Path:
    return research_root / f"experiments/rq5-static-deployment-cost-v1/raw/{dataset_id}.json"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RQ5StaticDeploymentValidationError(message)


def _number(value: Any, label: str) -> float | int:
    _require(
        isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value),
        f"{label} must be a finite number",
    )
    return value


def _git_sha(path: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode:
        raise RQ5StaticDeploymentValidationError(f"cannot resolve git SHA: {path}")
    return completed.stdout.strip()


def _research_clean(root: Path) -> bool:
    completed = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode:
        raise RQ5StaticDeploymentValidationError("cannot inspect research working tree")
    return not completed.stdout.strip()


def _validate_protocol_value(value: Mapping[str, Any]) -> dict[str, Any]:
    _require(
        value.get("format_version") == PROTOCOL_FORMAT, "static deployment protocol format drifted"
    )
    digest = value.get("semantic_digest")
    _require(digest == PROTOCOL_DIGEST, "static deployment protocol digest drifted")
    body = {key: item for key, item in value.items() if key != "semantic_digest"}
    _require(semantic_digest(body) == digest, "static deployment protocol semantic digest mismatch")
    _require(
        value.get("status") == "preregistered", "static deployment protocol is not preregistered"
    )
    _require(value.get("datasets") == list(DATASETS), "static deployment dataset set drifted")
    _require(value.get("dataset_order") == list(DATASETS), "static deployment order drifted")
    _require(
        value.get("repetitions") == len(REPETITIONS), "static deployment repetition count drifted"
    )
    _require(value.get("repetition_order") == list(REPETITIONS), "repetition order drifted")
    _require(
        value.get("system", {}).get("stock_postgres_sha") == STOCK_POSTGRES_SHA, "stock SHA drifted"
    )
    return dict(value)


def validate_protocol(path: Path) -> dict[str, Any]:
    """Validate the preregistered protocol without repository or DB access."""
    value = _validate_protocol_value(read_json(path))
    return {
        "status": "valid",
        "format_version": PROTOCOL_FORMAT,
        "semantic_digest": value["semantic_digest"],
    }


def _protocol(root: Path) -> dict[str, Any]:
    return _validate_protocol_value(read_json(root / PROTOCOL_PATH))


def _deployment_semantic_manifest(value: Mapping[str, Any]) -> dict[str, Any]:
    """Reproduce the frozen Advisor DeploymentResult semantic manifest.

    DeploymentResult intentionally excludes runtime-only fields such as
    ``created_at`` and ``runtime_metadata`` from its semantic identity.  RQ2
    child artifacts use the research repository's generic top-level digest
    convention, so this compatibility path is only for the pinned deployment
    artifacts.
    """
    try:
        return {
            "format_version": value["format_version"],
            "recommendation_semantic_digest": value["recommendation_semantic_digest"],
            "source_snapshot_semantic_digest": value["source_snapshot_semantic_digest"],
            "candidate_universe_semantic_digest": value["candidate_universe_semantic_digest"],
            "native_stats_repository_semantic_digest": value[
                "native_stats_repository_semantic_digest"
            ],
            "singleton_profile_semantic_digest": value["singleton_profile_semantic_digest"],
            "optimization_plan_semantic_digest": value["optimization_plan_semantic_digest"],
            "search_result_semantic_digest": value["search_result_semantic_digest"],
            "deployment_contract": value["deployment_contract"],
            "deployment_policy": value["deployment_policy"],
            "target": value["target"],
            "server": value["server"],
            "decision": value["decision"],
            "deployment_ordered_candidate_ids": value["deployment_ordered_candidate_ids"],
            "deployed_objects": value["deployed_objects"],
            "preflight_summary": value["preflight_summary"],
            "commit_status": value["commit_status"],
            "post_commit_verified": value["post_commit_verified"],
            "execution_policy": value["execution_policy"],
        }
    except KeyError as exc:
        raise RQ5StaticDeploymentValidationError(
            f"deployment artifact is missing semantic-manifest field: {exc.args[0]}"
        ) from exc


def _pinned_json(
    root: Path, spec: Mapping[str, str], label: str, *, deployment: bool = False
) -> dict[str, Any]:
    path = root / spec["path"]
    _require(path.is_file(), f"missing {label}: {spec['path']}")
    value = read_json(path)
    if deployment:
        digest = semantic_digest(_deployment_semantic_manifest(value))
    else:
        digest = semantic_digest(
            {key: item for key, item in value.items() if key != "semantic_digest"}
        )
    _require(digest == spec["semantic_digest"], f"{label} digest mismatch")
    _require(
        value.get("semantic_digest") == spec["semantic_digest"], f"{label} embedded digest mismatch"
    )
    return value


def _source_projection(root: Path, dataset_id: str) -> dict[str, Any]:
    _require(dataset_id in DATASETS, f"unknown dataset: {dataset_id}")
    child = _pinned_json(root, RQ2_CHILDREN[dataset_id], f"RQ2 {dataset_id}")
    deployment = _pinned_json(
        root, DEPLOYMENT_SOURCES[dataset_id], f"deployment {dataset_id}", deployment=True
    )
    _require(child.get("format_version") == "rq2-transfer-v1", f"{dataset_id} RQ2 format drifted")
    _require(child.get("execution_status") == "complete", f"{dataset_id} RQ2 is not complete")
    _require(
        deployment.get("format_version") == "postgresql-deployment-result-v1",
        f"{dataset_id} deployment format drifted",
    )
    _require(
        deployment.get("commit_status") == "committed", f"{dataset_id} deployment is not committed"
    )
    _require(
        deployment.get("post_commit_verified") is True, f"{dataset_id} deployment is not verified"
    )
    _require(
        deployment.get("deployment_policy") == "postgresql-add-only-deployment-v1",
        f"{dataset_id} deployment policy drifted",
    )
    target = deployment.get("target", {})
    schema = str(target.get("schema"))
    relation = str(target.get("relation"))
    _require(schema == "public", f"{dataset_id} relation schema drifted")
    module, _ = DATASET_SPECS[dataset_id]
    _require(relation == module.RELATION.split(".", 1)[1], f"{dataset_id} relation drifted")
    columns = list(module.COLUMNS)
    objects = deployment.get("deployed_objects", [])
    order = deployment.get("deployment_ordered_candidate_ids", [])
    _require(len(objects) == len(order), f"{dataset_id} deployment order/object count mismatch")
    _require(
        order == child.get("selected_candidate_ids"), f"{dataset_id} membership drifted from RQ2"
    )
    projected = []
    for position, item in enumerate(objects, start=1):
        kind = item.get("kind")
        ordinals = [int(value) for value in item.get("column_ordinals", [])]
        _require(kind in KIND_SQL, f"{dataset_id} has unsupported statistics kind {kind}")
        names = []
        for ordinal in ordinals:
            _require(1 <= ordinal <= len(columns), f"{dataset_id} has invalid column ordinal")
            names.append(columns[ordinal - 1][0])
        _require(
            item.get("deployment_order_position") == position,
            f"{dataset_id} deployment position drifted",
        )
        _require(item.get("statistics_target") == 100, f"{dataset_id} statistics target drifted")
        projected.append(
            {
                "candidate_id": item["candidate_id"],
                "deployment_order_position": position,
                "name": item["name"],
                "schema": item.get("schema"),
                "column_ordinals": ordinals,
                "column_names": names,
                "kind": kind,
                "statistics_target": int(item["statistics_target"]),
            }
        )
    _require(
        all(item["schema"] == schema for item in projected), f"{dataset_id} object schema drifted"
    )
    return {
        "dataset_id": dataset_id,
        "source_rq2_child": RQ2_CHILDREN[dataset_id]["path"],
        "source_rq2_digest": RQ2_CHILDREN[dataset_id]["semantic_digest"],
        "source_deployment_artifact": DEPLOYMENT_SOURCES[dataset_id]["path"],
        "source_deployment_digest": DEPLOYMENT_SOURCES[dataset_id]["semantic_digest"],
        "schema": schema,
        "relation": relation,
        "relation_identity": target,
        "actual_selected_k": int(child["actual_selected_k"]),
        "selected_candidate_ids": list(order),
        "deployment_order": list(order),
        "objects": projected,
    }


def _planned_outputs(root: Path) -> list[str]:
    return [
        "experiments/rq5-static-deployment-cost-v1.json",
        *[
            f"experiments/rq5-static-deployment-cost-v1/raw/{dataset_id}.json"
            for dataset_id in DATASETS
        ],
    ]


def build_preflight(
    research_root: Path,
    *,
    stock_postgres_root: Path,
    output: Path | None = None,
) -> dict[str, Any]:
    """Build the offline preflight; this performs no database work."""
    root = research_root.resolve()
    protocol = _protocol(root)
    _require(_research_clean(root), "research working tree must be clean before preflight")
    producer_sha = _git_sha(root)
    stock_sha = _git_sha(stock_postgres_root.resolve())
    _require(stock_sha == STOCK_POSTGRES_SHA, "stock PostgreSQL source SHA drifted")
    projections = [_source_projection(root, dataset_id) for dataset_id in DATASETS]
    planned = _planned_outputs(root)
    collisions = [path for path in planned if (root / path).exists()]
    _require(not collisions, f"static deployment output collision: {collisions}")
    value = {
        "format_version": PREFLIGHT_FORMAT,
        "experiment_id": EXPERIMENT_ID,
        "status": "ready-to-run",
        "formal_execution_started": False,
        "research_commit_sha": producer_sha,
        "stock_postgres_sha": stock_sha,
        "stock_postgres_version": STOCK_POSTGRES_VERSION,
        "protocol_path": PROTOCOL_PATH,
        "protocol_semantic_digest": protocol["semantic_digest"],
        "datasets": projections,
        "dataset_order": list(DATASETS),
        "repetitions": len(REPETITIONS),
        "repetition_order": list(REPETITIONS),
        "stage_hard_cap_seconds": STAGE_HARD_CAP_SECONDS,
        "no_live_advisor_requirement": True,
        "patched_postgres_required": False,
        "truth_or_planner_work": False,
        "working_tree_clean_before_preflight": True,
        "output_collision_check": {"checked": planned, "collisions": []},
    }
    value["semantic_digest"] = semantic_digest(value)
    write_json(output or default_preflight_path(root), value)
    return value


def validate_preflight(path: Path, research_root: Path) -> dict[str, Any]:
    value = read_json(path)
    _require(
        value.get("format_version") == PREFLIGHT_FORMAT, "unsupported static deployment preflight"
    )
    digest = value.get("semantic_digest")
    _require(
        digest
        == semantic_digest({key: item for key, item in value.items() if key != "semantic_digest"}),
        "preflight digest mismatch",
    )
    _require(value.get("status") == "ready-to-run", "preflight is not ready-to-run")
    _require(
        value.get("formal_execution_started") is False,
        "preflight claims formal execution already started",
    )
    _require(value.get("protocol_semantic_digest") == PROTOCOL_DIGEST, "preflight protocol drifted")
    _require(value.get("stock_postgres_sha") == STOCK_POSTGRES_SHA, "preflight stock SHA drifted")
    _require(
        value.get("datasets", []) == [_source_projection(research_root, d) for d in DATASETS],
        "preflight source projection drifted",
    )
    _require(value.get("dataset_order") == list(DATASETS), "preflight dataset order drifted")
    _require(value.get("repetitions") == 3, "preflight repetition count drifted")
    _require(value.get("stage_hard_cap_seconds") == STAGE_HARD_CAP_SECONDS, "preflight cap drifted")
    _require(value.get("no_live_advisor_requirement") is True, "preflight requires Advisor")
    _require(
        value.get("patched_postgres_required") is False, "preflight requires patched PostgreSQL"
    )
    _require(value.get("truth_or_planner_work") is False, "preflight claims truth/planner work")
    return {"status": "valid", "format_version": PREFLIGHT_FORMAT, "semantic_digest": digest}


def _psycopg() -> Any:
    import psycopg

    return psycopg


def _quote_relation(psycopg: Any, schema: str, relation: str) -> Any:
    return psycopg.sql.SQL("{}.{}").format(
        psycopg.sql.Identifier(schema), psycopg.sql.Identifier(relation)
    )


def _kind_code(value: Any) -> set[str]:
    return set(re.findall(r"[mfnd]", str(value)))


def _keys(value: Any) -> tuple[int, ...]:
    return tuple(int(item) for item in re.findall(r"\d+", str(value)))


def _relation_sizes(connection: Any, relation: str) -> dict[str, int]:
    rows = connection.execute(
        "SELECT pg_catalog.pg_total_relation_size(%s::regclass), "
        "pg_catalog.pg_total_relation_size(%s::regclass)",
        ("pg_catalog.pg_statistic_ext", "pg_catalog.pg_statistic_ext_data"),
    ).fetchone()
    base = connection.execute(
        "SELECT pg_catalog.pg_total_relation_size(%s::regclass)", (relation,)
    ).fetchone()[0]
    metadata = int(rows[0])
    data = int(rows[1])
    return {
        "metadata_catalog_total_relation_bytes": metadata,
        "data_catalog_total_relation_bytes": data,
        "combined_catalog_total_relation_bytes": metadata + data,
        "base_relation_total_bytes_context": int(base),
    }


def _logical_storage(
    connection: Any, projection: Mapping[str, Any], *, require_payload: bool
) -> dict[str, Any]:
    names = [item["name"] for item in projection["objects"]]
    rows = connection.execute(
        "SELECT e.oid::bigint, e.stxname, e.stxkind::text, e.stxkeys::text, e.stxstattarget, "
        "pg_catalog.pg_column_size(e), pg_catalog.pg_column_size(d), "
        "pg_catalog.pg_column_size(d.stxdmcv), pg_catalog.pg_column_size(d.stxddependencies), "
        "pg_catalog.pg_column_size(d.stxdndistinct), "
        "(d.stxdmcv IS NOT NULL), (d.stxddependencies IS NOT NULL), (d.stxdndistinct IS NOT NULL) "
        "FROM pg_catalog.pg_statistic_ext AS e "
        "LEFT JOIN pg_catalog.pg_statistic_ext_data AS d ON d.stxoid=e.oid "
        "WHERE e.stxrelid = (SELECT c.oid FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname=%s AND c.relname=%s) AND e.stxname = ANY(%s) "
        "ORDER BY e.oid",
        (projection["schema"], projection["relation"], names),
    ).fetchall()
    expected = {item["name"]: item for item in projection["objects"]}
    _require(len(rows) == len(expected), "catalog logical row count differs from recommendation")
    ext_bytes = 0
    data_bytes = 0
    payload_bytes = {
        "mcv_payload_bytes": 0,
        "dependencies_payload_bytes": 0,
        "ndistinct_payload_bytes": 0,
    }
    observed = []
    for row in rows:
        name = str(row[1])
        item = expected.get(name)
        _require(item is not None, f"unexpected statistics object: {name}")
        expected_code = KIND_CODE[item["kind"]]
        codes = _kind_code(row[2])
        _require(expected_code in codes, f"statistics kind mismatch: {name}")
        _require(
            _keys(row[3]) == tuple(item["column_ordinals"]), f"statistics columns mismatch: {name}"
        )
        _require(int(row[4]) == item["statistics_target"], f"statistics target mismatch: {name}")
        payload = {"mcv": bool(row[10]), "dependencies": bool(row[11]), "ndistinct": bool(row[12])}
        required_key = KIND_SQL[item["kind"]]
        if require_payload:
            _require(payload[required_key], f"required {required_key} payload missing: {name}")
        else:
            _require(not payload[required_key], f"payload appeared before ANALYZE: {name}")
        ext_bytes += int(row[5] or 0)
        data_bytes += int(row[6] or 0)
        payload_bytes["mcv_payload_bytes"] += int(row[7] or 0)
        payload_bytes["dependencies_payload_bytes"] += int(row[8] or 0)
        payload_bytes["ndistinct_payload_bytes"] += int(row[9] or 0)
        observed.append({"name": name, "oid": int(row[0]), "payload": payload})
    return {
        "logical_catalog_row_bytes": {
            "pg_statistic_ext": ext_bytes,
            "pg_statistic_ext_data": data_bytes,
            "total": ext_bytes + data_bytes,
        },
        **payload_bytes,
        "objects": observed,
    }


def _state(
    connection: Any, projection: Mapping[str, Any], *, require_payload: bool
) -> dict[str, Any]:
    return {
        **_logical_storage(connection, projection, require_payload=require_payload),
        "physical_catalog_relation_allocation": _relation_sizes(
            connection, f"{projection['schema']}.{projection['relation']}"
        ),
    }


def _verify_stock(connection: Any) -> dict[str, Any]:
    row = connection.execute(
        "SELECT current_setting('server_version'), current_setting('server_version_num')::integer"
    ).fetchone()
    _require(row is not None, "stock PostgreSQL identity query returned no row")
    _require(str(row[0]).startswith(STOCK_POSTGRES_VERSION), f"server version drifted: {row[0]}")
    _require(int(row[1]) == 160014, f"server version number drifted: {row[1]}")
    return {"server_version": str(row[0]), "server_version_num": int(row[1])}


def _execute_ddl(connection: Any, projection: Mapping[str, Any], psycopg: Any) -> tuple[float, int]:
    started = time.perf_counter()
    connection.execute("BEGIN")
    try:
        for item in projection["objects"]:
            connection.execute(
                psycopg.sql.SQL("CREATE STATISTICS {}.{} ({}) ON {} FROM {}").format(
                    psycopg.sql.Identifier(item["schema"]),
                    psycopg.sql.Identifier(item["name"]),
                    psycopg.sql.SQL(KIND_SQL[item["kind"]]),
                    psycopg.sql.SQL(", ").join(
                        psycopg.sql.Identifier(name) for name in item["column_names"]
                    ),
                    _quote_relation(psycopg, projection["schema"], projection["relation"]),
                )
            )
            connection.execute(
                psycopg.sql.SQL("ALTER STATISTICS {}.{} SET STATISTICS {}").format(
                    psycopg.sql.Identifier(item["schema"]),
                    psycopg.sql.Identifier(item["name"]),
                    psycopg.sql.SQL(str(item["statistics_target"])),
                )
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    elapsed = time.perf_counter() - started
    _require(elapsed <= STAGE_HARD_CAP_SECONDS, "DDL stage exceeded the 300 second hard cap")
    return elapsed, 2 * len(projection["objects"])


def _execute_analyze(connection: Any, projection: Mapping[str, Any], psycopg: Any) -> float:
    connection.execute(
        "SELECT pg_catalog.set_config('statement_timeout', %s, false)", ("300000ms",)
    )
    started = time.perf_counter()
    connection.execute(
        psycopg.sql.SQL("ANALYZE {}").format(
            _quote_relation(psycopg, projection["schema"], projection["relation"])
        )
    )
    elapsed = time.perf_counter() - started
    _require(elapsed <= STAGE_HARD_CAP_SECONDS, "ANALYZE stage exceeded the 300 second hard cap")
    return elapsed


def _create_database(stock_dsn: str, database_name: str, psycopg: Any) -> str:
    admin_dsn = psycopg.conninfo.make_conninfo(stock_dsn, dbname="postgres")
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        connection.execute(
            psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(database_name))
        )
    return psycopg.conninfo.make_conninfo(stock_dsn, dbname=database_name)


def _drop_database(stock_dsn: str, database_name: str, psycopg: Any) -> None:
    admin_dsn = psycopg.conninfo.make_conninfo(stock_dsn, dbname="postgres")
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        connection.execute(
            psycopg.sql.SQL("DROP DATABASE IF EXISTS {}").format(
                psycopg.sql.Identifier(database_name)
            )
        )


def _run_repetition(
    projection: Mapping[str, Any],
    *,
    repetition_id: int,
    stock_dsn: str,
    data_root: Path | None,
    stock_postgres_sha: str,
) -> dict[str, Any]:
    psycopg = _psycopg()
    _, loader = DATASET_SPECS[projection["dataset_id"]]
    database_name = f"rq5_static_{projection['dataset_id'].removeprefix('arecel-')}_{repetition_id}_{uuid.uuid4().hex[:8]}"
    setup_started = time.perf_counter()
    target_dsn = _create_database(stock_dsn, database_name, psycopg)
    setup_seconds = time.perf_counter() - setup_started
    try:
        load_started = time.perf_counter()
        load = loader(
            target_dsn,
            data_root=data_root,
            reset_disposable=False,
            statistics_target=100,
            seed_identifier=123,
        )
        load_seconds = time.perf_counter() - load_started
        _require(
            load.get("physical_extended_statistics_count", load.get("extended_statistics_count", 0))
            == 0,
            "fresh loader state has extended statistics",
        )
        with psycopg.connect(target_dsn, autocommit=True) as connection:
            identity = _verify_stock(connection)
            baseline = _state(connection, projection, require_payload=False)
            base_relation_bytes = baseline["physical_catalog_relation_allocation"][
                "base_relation_total_bytes_context"
            ]
            ddl_elapsed, statement_count = _execute_ddl(connection, projection, psycopg)
            ddl_verification_started = time.perf_counter()
            after_ddl = _state(connection, projection, require_payload=False)
            ddl_verification_seconds = time.perf_counter() - ddl_verification_started
            analyze_elapsed = _execute_analyze(connection, projection, psycopg)
            payload_verification_started = time.perf_counter()
            after_analyze = _state(connection, projection, require_payload=True)
            payload_verification_seconds = time.perf_counter() - payload_verification_started
        baseline_physical = baseline["physical_catalog_relation_allocation"]
        ddl_physical = after_ddl["physical_catalog_relation_allocation"]
        analyze_physical = after_analyze["physical_catalog_relation_allocation"]
        deltas = {}
        for label, left, right in (
            ("after_ddl_minus_baseline", ddl_physical, baseline_physical),
            ("after_analyze_minus_after_ddl", analyze_physical, ddl_physical),
            ("after_analyze_minus_baseline", analyze_physical, baseline_physical),
        ):
            deltas[label] = {
                key: left[key] - right[key]
                for key in (
                    "metadata_catalog_total_relation_bytes",
                    "data_catalog_total_relation_bytes",
                    "combined_catalog_total_relation_bytes",
                )
            }
        return {
            "format_version": "rq5-static-deployment-cost-repetition-v1",
            "status": "complete",
            "dataset_id": projection["dataset_id"],
            "repetition_id": repetition_id,
            "source_rq2_child": projection["source_rq2_child"],
            "source_rq2_digest": projection["source_rq2_digest"],
            "source_deployment_artifact": projection["source_deployment_artifact"],
            "source_deployment_digest": projection["source_deployment_digest"],
            "research_commit_sha": _git_sha(Path(__file__).resolve().parents[2]),
            "stock_postgresql_sha": stock_postgres_sha,
            "server_version": identity["server_version"],
            "selected_candidate_ids": projection["selected_candidate_ids"],
            "object_count": len(projection["objects"]),
            "statistics_kinds": [item["kind"] for item in projection["objects"]],
            "statistics_target": 100,
            "relation": {"schema": projection["schema"], "relation": projection["relation"]},
            "setup": {
                "fresh_database_setup_seconds": setup_seconds,
                "data_load_seconds": load_seconds,
                "baseline_analyze_seconds": None,
                "loader_elapsed_semantics": "loader elapsed includes its ordinary baseline ANALYZE",
                "excluded_from_static_deployment_primary_cost": True,
            },
            "ddl": {
                "elapsed_seconds": ddl_elapsed,
                "statement_count": statement_count,
                "committed": True,
                "verification_passed": True,
                "verification_seconds": ddl_verification_seconds,
            },
            "analyze": {
                "elapsed_seconds": analyze_elapsed,
                "completed": True,
                "payload_verification_passed": True,
                "payload_verification_seconds": payload_verification_seconds,
                "statement_count": 1,
            },
            "derived": {
                "sequential_ddl_plus_analyze_seconds": ddl_elapsed + analyze_elapsed,
                "sequential_sum_semantics": "derived sum of separately measured sequential stages",
                "direct_combined_wall_clock_measurement": False,
            },
            "logical_storage": {
                "baseline": baseline["logical_catalog_row_bytes"]
                | {key: baseline[key] for key in baseline if key.endswith("payload_bytes")},
                "after_ddl": after_ddl["logical_catalog_row_bytes"]
                | {key: after_ddl[key] for key in after_ddl if key.endswith("payload_bytes")},
                "after_analyze": after_analyze["logical_catalog_row_bytes"]
                | {
                    key: after_analyze[key]
                    for key in after_analyze
                    if key.endswith("payload_bytes")
                },
            },
            "physical_catalog_allocation": {
                "baseline": baseline_physical,
                "after_ddl": ddl_physical,
                "after_analyze": analyze_physical,
                "deltas": deltas,
                "delta_semantics": "page-granular relation allocation; not exact per-object attribution",
            },
            "base_relation_total_bytes_context": base_relation_bytes,
            "cleanup": {"database_dropped": True},
        }
    finally:
        _drop_database(stock_dsn, database_name, psycopg)


def run_static_deployment(
    dataset_id: str,
    *,
    research_root: Path,
    stock_dsn: str,
    output: Path,
    stock_postgres_root: Path,
    preflight: Path | None = None,
    data_root: Path | None = None,
) -> dict[str, Any]:
    """Run exactly three fresh stock realizations for one dataset."""
    root = research_root.resolve()
    preflight_path = preflight or default_preflight_path(root)
    preflight_result = validate_preflight(preflight_path, root)
    _require(
        _git_sha(root) == read_json(preflight_path)["research_commit_sha"],
        "implementation changed after preflight",
    )
    stock_sha = _git_sha(stock_postgres_root.resolve())
    _require(stock_sha == STOCK_POSTGRES_SHA, "stock PostgreSQL source SHA drifted")
    projection = _source_projection(root, dataset_id)
    _require(not output.exists(), f"raw output already exists: {output}")
    _require(preflight_result["status"] == "valid", "preflight validation failed")
    repetitions = []
    for repetition_id in REPETITIONS:
        repetitions.append(
            _run_repetition(
                projection,
                repetition_id=repetition_id,
                stock_dsn=stock_dsn,
                data_root=data_root,
                stock_postgres_sha=stock_sha,
            )
        )
    value = {
        "format_version": "rq5-static-deployment-cost-dataset-v1",
        "status": "complete",
        "dataset_id": dataset_id,
        "formal_execution": True,
        "research_commit_sha": _git_sha(root),
        "stock_postgresql_sha": stock_sha,
        "protocol_path": PROTOCOL_PATH,
        "protocol_semantic_digest": PROTOCOL_DIGEST,
        "preflight_path": str(preflight_path.relative_to(root)),
        "preflight_semantic_digest": read_json(preflight_path)["semantic_digest"],
        "repetitions": repetitions,
        "no_advisor_selection": True,
        "no_patched_postgres": True,
        "no_planner_evaluation": True,
        "no_truth_acquisition": True,
        "semantic_digest": "",
    }
    value["semantic_digest"] = semantic_digest(
        {key: item for key, item in value.items() if key != "semantic_digest"}
    )
    reject_credentials(value)
    write_json(output, value)
    return value


def _summary_stats(values: list[float | int]) -> dict[str, float | int]:
    return {"median": median(values), "min": min(values), "max": max(values)}


def _validate_dataset_raw(value: Mapping[str, Any], projection: Mapping[str, Any]) -> None:
    _require(value.get("status") == "complete", "raw static deployment child is not complete")
    _require(value.get("dataset_id") == projection["dataset_id"], "raw dataset identity drifted")
    _require(
        value.get("source_rq2_digest") == projection["source_rq2_digest"], "raw RQ2 source drifted"
    )
    _require(
        value.get("source_deployment_digest") == projection["source_deployment_digest"],
        "raw deployment source drifted",
    )
    _require(value.get("stock_postgresql_sha") == STOCK_POSTGRES_SHA, "raw stock identity drifted")
    _require(value.get("no_advisor_selection") is True, "raw child claims Advisor selection")
    for repetition in value.get("repetitions", []):
        _require(repetition.get("statistics_target") == 100, "raw target drifted")
        _require(repetition.get("ddl", {}).get("committed") is True, "raw DDL was not committed")
        _require(repetition.get("analyze", {}).get("completed") is True, "raw ANALYZE incomplete")
        _require(
            repetition.get("analyze", {}).get("payload_verification_passed") is True,
            "raw payload verification failed",
        )
        _require(
            repetition.get("derived", {}).get("direct_combined_wall_clock_measurement") is False,
            "raw derived sum mislabeled",
        )
        _require("total_storage" not in repetition, "logical and physical storage were combined")
        _require(repetition.get("repetition_id") in REPETITIONS, "invalid repetition id")
        _require(
            repetition.get("ddl", {}).get("elapsed_seconds", 0) <= STAGE_HARD_CAP_SECONDS,
            "DDL cap violated",
        )
        _require(
            repetition.get("analyze", {}).get("elapsed_seconds", 0) <= STAGE_HARD_CAP_SECONDS,
            "ANALYZE cap violated",
        )


def summarize_static_deployment(
    research_root: Path, *, output: Path | None = None, preflight: Path | None = None
) -> dict[str, Any]:
    root = research_root.resolve()
    preflight_path = preflight or default_preflight_path(root)
    validate_preflight(preflight_path, root)
    preflight_value = read_json(preflight_path)
    dataset_values = {}
    raw_refs = []
    for dataset_id in DATASETS:
        raw_path = default_raw_path(root, dataset_id)
        _require(raw_path.is_file(), f"missing raw static deployment child: {raw_path}")
        raw = read_json(raw_path)
        projection = _source_projection(root, dataset_id)
        _validate_dataset_raw(raw, projection)
        _require(
            raw.get("semantic_digest")
            == semantic_digest(
                {key: item for key, item in raw.items() if key != "semantic_digest"}
            ),
            f"{dataset_id} raw digest mismatch",
        )
        raw_refs.append(
            {
                "dataset_id": dataset_id,
                "path": str(raw_path.relative_to(root)),
                "semantic_digest": raw["semantic_digest"],
            }
        )
        repetitions = raw["repetitions"]
        ddl = [rep["ddl"]["elapsed_seconds"] for rep in repetitions]
        analyze = [rep["analyze"]["elapsed_seconds"] for rep in repetitions]
        sequential = [rep["derived"]["sequential_ddl_plus_analyze_seconds"] for rep in repetitions]
        logical_ddl = [rep["logical_storage"]["after_ddl"]["total"] for rep in repetitions]
        logical_analyze = [rep["logical_storage"]["after_analyze"]["total"] for rep in repetitions]
        physical_ddl = [
            rep["physical_catalog_allocation"]["deltas"]["after_ddl_minus_baseline"][
                "combined_catalog_total_relation_bytes"
            ]
            for rep in repetitions
        ]
        physical_analyze = [
            rep["physical_catalog_allocation"]["deltas"]["after_analyze_minus_baseline"][
                "combined_catalog_total_relation_bytes"
            ]
            for rep in repetitions
        ]
        projection_objects = projection["objects"]
        dataset_values[dataset_id] = {
            "selected_k": projection["actual_selected_k"],
            "selected_candidate_ids": projection["selected_candidate_ids"],
            "object_count": len(projection_objects),
            "statistics_kinds": [item["kind"] for item in projection_objects],
            "statistics_target": 100,
            "repetitions": repetitions,
            "ddl_elapsed_seconds": _summary_stats(ddl),
            "analyze_elapsed_seconds": _summary_stats(analyze),
            "sequential_ddl_plus_analyze_seconds": _summary_stats(sequential),
            "logical_catalog_row_bytes_after_ddl": _summary_stats(logical_ddl),
            "logical_catalog_row_bytes_after_analyze": _summary_stats(logical_analyze),
            "physical_catalog_allocation_delta_after_ddl": _summary_stats(physical_ddl),
            "physical_catalog_allocation_delta_after_analyze": _summary_stats(physical_analyze),
            "base_relation_total_bytes_context": _summary_stats(
                [rep["base_relation_total_bytes_context"] for rep in repetitions]
            ),
            "payload_verification": all(
                rep["analyze"]["payload_verification_passed"] for rep in repetitions
            ),
            "cleanup": all(rep["cleanup"]["database_dropped"] for rep in repetitions),
        }
    historical = {}
    for dataset_id in DATASETS:
        child = read_json(root / RQ2_CHILDREN[dataset_id]["path"])
        historical[dataset_id] = {
            "deployment_including_final_analyze_seconds": child["full_data"]["runtime"][
                "deployment_including_final_analyze"
            ],
            "comparison_semantics": "cross-run contextual comparison only; no equality gate",
        }
    value = {
        "format_version": FORMAL_FORMAT,
        "experiment_id": EXPERIMENT_ID,
        "status": "complete",
        "static_deployment_cost_subexperiment": True,
        "rq5_completion_status": "incomplete",
        "rq5_registry_status": "planned",
        "research_commit_sha": preflight_value["research_commit_sha"],
        "stock_postgresql_sha": STOCK_POSTGRES_SHA,
        "stock_postgresql_version": STOCK_POSTGRES_VERSION,
        "protocol_path": PROTOCOL_PATH,
        "protocol_semantic_digest": PROTOCOL_DIGEST,
        "preflight_path": str(preflight_path.relative_to(root)),
        "preflight_semantic_digest": preflight_value["semantic_digest"],
        "dataset_order": list(DATASETS),
        "raw_repetition_artifacts": raw_refs,
        "datasets": dataset_values,
        "historical_rq2_combined_deployment_context": historical,
        "interpretation": {
            "selected_recommendations_only": True,
            "ddl_separate_from_analyze": True,
            "catalog_logical_and_physical_views_separate": True,
            "physical_delta_not_per_object_attribution": True,
            "repetitions_are_not_refresh": True,
        },
        "no_advisor_selection": True,
        "no_patched_postgres": True,
        "no_planner_evaluation": True,
        "no_truth_acquisition": True,
        "no_snapshot_bytes_measurement": True,
        "semantic_digest": "",
    }
    value["semantic_digest"] = semantic_digest(
        {key: item for key, item in value.items() if key != "semantic_digest"}
    )
    reject_credentials(value)
    write_json(output or default_formal_path(root), value)
    return value


def validate_formal_artifact(path: Path, research_root: Path) -> dict[str, Any]:
    value = read_json(path)
    _require(value.get("format_version") == FORMAL_FORMAT, "unsupported static deployment artifact")
    digest = value.get("semantic_digest")
    _require(
        digest
        == semantic_digest({key: item for key, item in value.items() if key != "semantic_digest"}),
        "static deployment artifact digest mismatch",
    )
    _require(value.get("status") == "complete", "static deployment artifact is not complete")
    _require(value.get("static_deployment_cost_subexperiment") is True, "artifact scope drifted")
    _require(value.get("rq5_completion_status") == "incomplete", "RQ5 must remain incomplete")
    _require(
        value.get("stock_postgresql_sha") == STOCK_POSTGRES_SHA, "artifact stock identity drifted"
    )
    _require(value.get("protocol_semantic_digest") == PROTOCOL_DIGEST, "artifact protocol drifted")
    _require(value.get("no_advisor_selection") is True, "artifact claims selection work")
    _require(value.get("no_patched_postgres") is True, "artifact claims patched PostgreSQL")
    _require(value.get("no_planner_evaluation") is True, "artifact claims planner work")
    _require(value.get("no_truth_acquisition") is True, "artifact claims truth work")
    _require(value.get("no_snapshot_bytes_measurement") is True, "artifact claims snapshot bytes")
    _require("total_storage" not in value, "artifact combines logical and physical storage")
    _require(value.get("dataset_order") == list(DATASETS), "artifact dataset order drifted")
    for dataset_id in DATASETS:
        dataset = value.get("datasets", {}).get(dataset_id)
        _require(dataset is not None, f"missing static deployment dataset: {dataset_id}")
        _require(
            dataset.get("payload_verification") is True, f"{dataset_id} payload verification failed"
        )
        _require(dataset.get("cleanup") is True, f"{dataset_id} cleanup failed")
        _require(len(dataset.get("repetitions", [])) == 3, f"{dataset_id} repetition count drifted")
    reject_credentials(value)
    return {
        "status": "valid",
        "format_version": FORMAL_FORMAT,
        "semantic_digest": digest,
        "dataset_count": len(DATASETS),
        "rq5_completion_status": value["rq5_completion_status"],
    }
