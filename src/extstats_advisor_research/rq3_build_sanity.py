"""Patched-versus-stock physical-statistics sanity evidence for RQ3.

This is deliberately separate from ``rq3-fidelity-v1``.  The primary RQ3
question compares physical and hypothetical realization on one patched binary;
this artifact only checks that the patched binary's ordinary physical path is
consistent with stock PostgreSQL when the hypothetical overlay is inactive.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from . import FROZEN_ADVISOR_SHA, FROZEN_PATCHED_POSTGRES_SHA
from .pins import verify_frozen_systems, verify_research_repository
from .postgres_lab import (
    _read_identity,
    role_spec,
    source_identity,
    validate_source_identity,
)
from .provenance import read_json, reject_credentials, semantic_digest, write_json
from .rq3_fidelity import (
    SYNTHETIC_CONFIGURATIONS,
    _active_oids,
    _fixture_queries,
    _physical_count,
    _physical_payload,
    _plan_rows_and_document,
    _settings,
)

BUILD_SANITY_FORMAT = "rq3-build-sanity-v1"
EXPERIMENT_ID = "rq3-secondary-build-sanity"
FIXTURE_ID = "rq3-synthetic-build-sanity-v1"
GATE_STATES = ("pass", "fail", "inconclusive")
STOCK_SOURCE_SHA = "0d1c00c624fa7367d4a895f44381887757289682"
STATISTICS_TARGET = 100
SEED_IDENTIFIER = 123

_CONTROL_FIELDS = (
    "identical_relation_contents",
    "identical_schema",
    "identical_workload",
    "ordinary_stats_equal",
    "identical_statistics_target",
    "equivalent_design",
    "payload_correspondence_verified",
    "identical_planner_settings",
    "patched_overlay_inactive",
    "physical_catalog_objects_present",
)


def _semantic_payload(artifact: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in artifact.items() if key != "semantic_digest"}


def _gate(controls: dict[str, bool], paired_queries: list[dict[str, Any]]) -> str:
    if not all(controls.get(field) is True for field in _CONTROL_FIELDS):
        return "inconclusive"
    return "pass" if all(item["exact_match"] for item in paired_queries) else "fail"


def _summary(configurations: list[dict[str, Any]]) -> dict[str, Any]:
    paired = [item for configuration in configurations for item in configuration["paired_queries"]]
    exact = sum(item["exact_match"] for item in paired)
    categories: dict[str, int] = {}
    for item in paired:
        category = item["mismatch_category"]
        categories[category] = categories.get(category, 0) + 1
    gates = {configuration["gate"] for configuration in configurations}
    gate = "inconclusive" if "inconclusive" in gates else ("fail" if "fail" in gates else "pass")
    return {
        "configuration_count": len(configurations),
        "query_count": len(paired),
        "exact_match_count": exact,
        "exact_match_fraction": exact / len(paired) if paired else 0.0,
        "mismatch_count": len(paired) - exact,
        "mismatch_categories": dict(sorted(categories.items())),
        "gate": gate,
    }


def build_build_sanity_artifact(
    *, system: dict[str, Any], fixture: dict[str, Any], configurations: list[dict[str, Any]]
) -> dict[str, Any]:
    """Normalize paired rows, classify mismatches, and seal the artifact."""

    if not configurations:
        raise ValueError("at least one build-sanity configuration is required")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in configurations:
        configuration = dict(source)
        configuration_id = configuration.get("configuration_id")
        if not isinstance(configuration_id, str) or not configuration_id:
            raise ValueError("each build-sanity configuration needs an ID")
        if configuration_id in seen:
            raise ValueError(f"duplicate build-sanity configuration: {configuration_id}")
        seen.add(configuration_id)
        controls = configuration.get("controls")
        if not isinstance(controls, dict):
            raise TypeError(f"controls missing for {configuration_id}")
        controls = {key: value for key, value in controls.items()}
        for field in _CONTROL_FIELDS:
            if not isinstance(controls.get(field), bool):
                raise TypeError(f"{configuration_id} control {field} must be boolean")
        source_pairs = configuration.pop("paired_queries", None)
        if not isinstance(source_pairs, list) or not source_pairs:
            raise ValueError(f"{configuration_id} needs paired query records")
        pairs: list[dict[str, Any]] = []
        query_ids: set[str] = set()
        for source_pair in source_pairs:
            pair = dict(source_pair)
            query_id = pair.get("query_id")
            stock_rows = pair.get("stock_plan_rows")
            patched_rows = pair.get("patched_plan_rows")
            if not isinstance(query_id, str) or not query_id or query_id in query_ids:
                raise ValueError(f"duplicate or invalid query ID in {configuration_id}")
            if any(
                not isinstance(value, int) or isinstance(value, bool) or value < 0
                for value in (stock_rows, patched_rows)
            ):
                raise ValueError(f"invalid Plan Rows in {configuration_id}/{query_id}")
            query_ids.add(query_id)
            exact = stock_rows == patched_rows
            if exact:
                category = "exact-match"
            elif _gate(controls, []) == "inconclusive":
                category = "state-mismatch"
            else:
                category = "plan-rows-mismatch"
            pair.update(
                {
                    "exact_match": exact,
                    "absolute_delta": abs(patched_rows - stock_rows),
                    "mismatch_category": category,
                }
            )
            pairs.append(pair)
        configuration["controls"] = controls
        configuration["paired_queries"] = pairs
        configuration["gate"] = _gate(controls, pairs)
        normalized.append(configuration)
    artifact: dict[str, Any] = {
        "format": BUILD_SANITY_FORMAT,
        "experiment_id": EXPERIMENT_ID,
        "paper_specification": {"identity": "paper-experiment-v1", "experiment_id": EXPERIMENT_ID},
        "system": system,
        "fixture": fixture,
        "configurations": normalized,
        "summary": _summary(normalized),
    }
    artifact["semantic_digest"] = semantic_digest(artifact)
    validate_build_sanity_artifact(artifact)
    return artifact


def _require_sha(value: Any, label: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 40
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{label} must be a full lowercase SHA-1")


def validate_build_sanity_artifact(artifact: Any) -> dict[str, Any]:
    if not isinstance(artifact, dict):
        raise TypeError("build-sanity artifact must be an object")
    required = {
        "format",
        "experiment_id",
        "paper_specification",
        "system",
        "fixture",
        "configurations",
        "summary",
        "semantic_digest",
    }
    missing = sorted(required - set(artifact))
    if missing:
        raise ValueError(f"build-sanity artifact is missing fields: {missing}")
    if artifact["format"] != BUILD_SANITY_FORMAT or artifact["experiment_id"] != EXPERIMENT_ID:
        raise ValueError("unsupported build-sanity artifact identity")
    if artifact["paper_specification"] != {
        "identity": "paper-experiment-v1",
        "experiment_id": EXPERIMENT_ID,
    }:
        raise ValueError("build-sanity artifact is not bound to its paper experiment")
    if artifact["semantic_digest"] != semantic_digest(_semantic_payload(artifact)):
        raise ValueError("build-sanity artifact semantic digest mismatch")
    system = artifact["system"]
    if not isinstance(system, dict):
        raise TypeError("build-sanity system identity must be an object")
    for field in (
        "research_commit_sha",
        "advisor_commit_sha",
        "stock_postgres_commit_sha",
        "patched_postgres_commit_sha",
        "stock_build_identity",
        "patched_build_identity",
    ):
        if field not in system:
            raise ValueError(f"build-sanity system identity is missing {field}")
    for field in (
        "research_commit_sha",
        "advisor_commit_sha",
        "stock_postgres_commit_sha",
        "patched_postgres_commit_sha",
    ):
        _require_sha(system[field], field)
    configurations = artifact["configurations"]
    if not isinstance(configurations, list) or not configurations:
        raise ValueError("build-sanity artifact needs configurations")
    ids: set[str] = set()
    for configuration in configurations:
        if not isinstance(configuration, dict):
            raise TypeError("build-sanity configuration must be an object")
        configuration_id = configuration.get("configuration_id")
        if not isinstance(configuration_id, str) or configuration_id in ids:
            raise ValueError("build-sanity configuration IDs must be unique")
        ids.add(configuration_id)
        controls = configuration.get("controls")
        if not isinstance(controls, dict):
            raise TypeError(f"controls missing for {configuration_id}")
        for field in _CONTROL_FIELDS:
            if not isinstance(controls.get(field), bool):
                raise TypeError(f"{configuration_id} control {field} must be boolean")
        patched = configuration.get("patched")
        if not isinstance(patched, dict):
            raise TypeError(f"{configuration_id} is missing patched physical evidence")
        if (
            patched.get("overlay_inactive") is not True
            or controls["patched_overlay_inactive"] is not True
        ):
            raise ValueError(
                "secondary artifact contains active or unverified hypothetical overlay"
            )
        pairs = configuration.get("paired_queries")
        if not isinstance(pairs, list) or not pairs:
            raise ValueError(f"{configuration_id} has no paired queries")
        for pair in pairs:
            if not isinstance(pair, dict):
                raise TypeError("paired query must be an object")
            stock_rows = pair.get("stock_plan_rows")
            patched_rows = pair.get("patched_plan_rows")
            exact = stock_rows == patched_rows
            if pair.get("exact_match") is not exact:
                raise ValueError("paired Plan Rows exact_match is inconsistent")
            expected_category = (
                "exact-match"
                if exact
                else (
                    "state-mismatch"
                    if _gate(controls, []) == "inconclusive"
                    else "plan-rows-mismatch"
                )
            )
            if pair.get("mismatch_category") != expected_category:
                raise ValueError("paired Plan Rows mismatch taxonomy is inconsistent")
            if pair.get("absolute_delta") != abs(patched_rows - stock_rows):
                raise ValueError("paired Plan Rows absolute_delta is inconsistent")
        expected_gate = _gate(controls, pairs)
        if configuration.get("gate") != expected_gate:
            raise ValueError(f"{configuration_id} gate is inconsistent")
    expected_summary = _summary(configurations)
    if artifact["summary"] != expected_summary:
        raise ValueError("build-sanity summary is inconsistent")
    if artifact["summary"]["gate"] not in GATE_STATES:
        raise ValueError("unsupported build-sanity gate state")
    reject_credentials(artifact)
    return expected_summary


def inspect_build_sanity_artifact(path: Path) -> dict[str, Any]:
    artifact = read_json(path)
    summary = validate_build_sanity_artifact(artifact)
    return {
        "artifact": str(path.resolve()),
        "summary": summary,
        "semantic_digest": artifact["semantic_digest"],
    }


def _session_setup(connection: Any) -> None:
    connection.execute("SET timezone = 'UTC'")
    connection.execute("SET DateStyle = 'ISO, YMD'")
    connection.execute(f"SET default_statistics_target = {STATISTICS_TARGET}")
    connection.execute("SET enable_bitmapscan = on")
    connection.execute("SET enable_indexscan = on")
    connection.execute("SET enable_seqscan = on")
    connection.execute("SET jit = off")
    connection.execute("SET plan_cache_mode = auto")


def _arm(
    connection: Any,
    *,
    role: str,
    configuration_id: str,
    object_specs: tuple[tuple[str, str], ...],
) -> dict[str, Any]:
    from extstats_advisor.dbms.postgres.ordinary_stats import ordinary_stats_fingerprint

    suffix = configuration_id.replace("-", "_")
    table = f"public.rq3_build_sanity_{suffix}"
    names = tuple(
        f"rq3_build_{suffix}_{candidate.rsplit('-', 1)[-1]}" for candidate, _ in object_specs
    )
    connection.execute(f"DROP TABLE IF EXISTS {table}")
    connection.execute(f"CREATE TABLE {table} (a integer, b integer, c integer)")
    connection.execute(
        f"INSERT INTO {table} SELECT i % 7, i % 7, i % 3 FROM generate_series(1, 1000) AS s(i)"
    )
    relation_oid = int(connection.execute(f"SELECT '{table}'::regclass::oid").fetchone()[0])
    relation = {"schema": "public", "name": table.rsplit(".", 1)[1], "oid": relation_oid}
    for name, (_, kind) in zip(names, object_specs, strict=True):
        connection.execute(f"CREATE STATISTICS {name} ({kind}) ON a, b FROM {table}")
        connection.execute(f"ALTER STATISTICS {name} SET STATISTICS {STATISTICS_TARGET}")
    connection.execute("SELECT setseed(1.0 / 123)")
    connection.execute(f"ANALYZE {table}")
    ordinary = ordinary_stats_fingerprint(connection, relation_oid)
    objects: list[dict[str, Any]] = []
    for (candidate_id, kind), name in zip(object_specs, names, strict=True):
        payload = _physical_payload(connection, relation_oid, name, kind)
        payload.pop("payload", None)
        payload["candidate_id"] = candidate_id
        objects.append(payload)
    catalog_order = [
        str(row[0])
        for row in connection.execute(
            "SELECT stxname FROM pg_catalog.pg_statistic_ext WHERE stxrelid = %s ORDER BY oid",
            (relation_oid,),
        ).fetchall()
    ]
    workload = [{"query_id": query_id, "sql": query} for query_id, query in _fixture_queries(table)]
    overlay_inactive = None
    if role == "patched":
        # Make the physical arm fail closed if a previous session left an
        # overlay active; this reset is intentionally before EXPLAIN.
        connection.execute("SELECT pg_catalog.pg_hypothetical_extstats_reset()")
        overlay_inactive = _active_oids(connection) is None
        if not overlay_inactive:
            raise RuntimeError("patched physical arm has active hypothetical OIDs")
    settings = _settings(connection)
    explain: list[dict[str, Any]] = []
    for query in workload:
        rows, document = _plan_rows_and_document(
            connection, query["sql"], "public", relation["name"]
        )
        explain.append(
            {
                **query,
                "plan_rows": rows,
                "explain": document,
                "explain_sha256": semantic_digest(document),
            }
        )
    data_digest = semantic_digest({"generator": "i % 7, i % 7, i % 3", "rows": 1000})
    schema_digest = semantic_digest(
        {"schema": "public", "table": "(a integer, b integer, c integer)"}
    )
    workload_digest = semantic_digest(workload)
    stats_count = _physical_count(connection, relation_oid)
    return {
        "role": role,
        "relation": relation,
        "data_digest": data_digest,
        "schema_digest": schema_digest,
        "workload_digest": workload_digest,
        "statistics_target": STATISTICS_TARGET,
        "statistics_definitions": objects,
        "catalog_order": catalog_order,
        "ordinary_stats_fingerprint": ordinary,
        "payload_digests": {item["candidate_id"]: item["payload_sha256"] for item in objects},
        "settings": settings,
        "explain": explain,
        "physical_catalog_objects_present": stats_count == len(object_specs),
        "overlay_inactive": overlay_inactive
        if role == "patched"
        else "not-applicable-stock-binary",
        "table_sql": "CREATE TABLE public.<table> (a integer, b integer, c integer)",
    }


def _cleanup_arm(
    connection: Any, arm: dict[str, Any], object_specs: tuple[tuple[str, str], ...]
) -> dict[str, Any]:
    relation_oid = arm["relation"]["oid"]
    names = [item["name"] for item in arm["statistics_definitions"]]
    table = f'public."{arm["relation"]["name"]}"'
    connection.execute(f"DROP STATISTICS IF EXISTS {', '.join(names)}")
    remaining = _physical_count(connection, relation_oid)
    if arm["role"] == "patched":
        connection.execute("SELECT pg_catalog.pg_hypothetical_extstats_reset()")
        active = _active_oids(connection)
    else:
        active = "not-applicable-stock-binary"
    connection.execute(f"DROP TABLE IF EXISTS {table}")
    return {
        "verified": remaining == 0 and (active is None or active == "not-applicable-stock-binary"),
        "physical_extstats_count_after": remaining,
        "overlay_active_after": active,
    }


def run_build_sanity(
    *,
    stock_dsn: str,
    patched_dsn: str,
    output: Path,
    advisor_root: Path = Path("/home/wqts/projects/extstats-advisor"),
    patched_postgres_root: Path = Path("/home/wqts/projects/postgresql-src-pgextadv"),
    research_root: Path | None = None,
) -> dict[str, Any]:
    """Run all three small physical-statistics build-sanity configurations."""

    research_root = research_root or Path(__file__).resolve().parents[2]
    research_identity = verify_research_repository(research_root)
    verify_frozen_systems(advisor_root, patched_postgres_root)
    stock_spec = role_spec("stock")
    patched_spec = role_spec("patched")
    stock_source = source_identity(stock_spec)
    validate_source_identity(stock_source, stock_spec)
    if stock_source["source_commit_sha"] != STOCK_SOURCE_SHA:
        raise ValueError("stock PostgreSQL source is not pinned to the requested sanity SHA")
    stock_identity = _read_identity(stock_spec)
    patched_identity = _read_identity(patched_spec)
    import psycopg

    connections: dict[str, Any] = {}
    started = time.monotonic()
    try:
        for role, dsn in (("stock", stock_dsn), ("patched", patched_dsn)):
            connections[role] = psycopg.connect(
                dsn, application_name="extstats-research-rq3-build-sanity", autocommit=True
            )
            _session_setup(connections[role])
        configurations: list[dict[str, Any]] = []
        for configuration_id, object_specs in SYNTHETIC_CONFIGURATIONS:
            stock_arm = _arm(
                connections["stock"],
                role="stock",
                configuration_id=configuration_id,
                object_specs=object_specs,
            )
            patched_arm = _arm(
                connections["patched"],
                role="patched",
                configuration_id=configuration_id,
                object_specs=object_specs,
            )
            stock_by_id = {item["query_id"]: item for item in stock_arm["explain"]}
            patched_by_id = {item["query_id"]: item for item in patched_arm["explain"]}

            def design_signature(arm: dict[str, Any]) -> list[dict[str, Any]]:
                return [
                    {
                        key: item[key]
                        for key in ("candidate_id", "kind", "keys", "catalog_kinds", "definition")
                    }
                    for item in arm["statistics_definitions"]
                ]

            controls = {
                "identical_relation_contents": stock_arm["data_digest"]
                == patched_arm["data_digest"],
                "identical_schema": stock_arm["schema_digest"] == patched_arm["schema_digest"],
                "identical_workload": stock_arm["workload_digest"]
                == patched_arm["workload_digest"],
                "ordinary_stats_equal": stock_arm["ordinary_stats_fingerprint"]
                == patched_arm["ordinary_stats_fingerprint"],
                "identical_statistics_target": stock_arm["statistics_target"]
                == patched_arm["statistics_target"],
                "equivalent_design": design_signature(stock_arm) == design_signature(patched_arm),
                "payload_correspondence_verified": stock_arm["payload_digests"]
                == patched_arm["payload_digests"],
                "identical_planner_settings": stock_arm["settings"] == patched_arm["settings"],
                "patched_overlay_inactive": patched_arm["overlay_inactive"] is True,
                "physical_catalog_objects_present": stock_arm["physical_catalog_objects_present"]
                and patched_arm["physical_catalog_objects_present"],
            }
            pairs = [
                {
                    "query_id": query_id,
                    "sql": stock_record["sql"],
                    "stock_plan_rows": stock_record["plan_rows"],
                    "patched_plan_rows": patched_by_id[query_id]["plan_rows"],
                    "stock_explain": stock_record["explain"],
                    "patched_explain": patched_by_id[query_id]["explain"],
                }
                for query_id, stock_record in stock_by_id.items()
            ]
            configuration = {
                "configuration_id": configuration_id,
                "statistics_definitions": [
                    {"candidate_id": candidate_id, "kind": kind}
                    for candidate_id, kind in object_specs
                ],
                "controls": controls,
                "stock": stock_arm,
                "patched": patched_arm,
                "paired_queries": pairs,
                "cleanup": {
                    "stock": _cleanup_arm(connections["stock"], stock_arm, object_specs),
                    "patched": _cleanup_arm(connections["patched"], patched_arm, object_specs),
                },
            }
            if (
                not configuration["cleanup"]["stock"]["verified"]
                or not configuration["cleanup"]["patched"]["verified"]
            ):
                raise RuntimeError(f"cleanup failed for {configuration_id}")
            configurations.append(configuration)
        system = {
            **research_identity,
            "advisor_repository": "1951123/extstats-advisor",
            "advisor_commit_sha": FROZEN_ADVISOR_SHA,
            "stock_postgres_repository": "1951123/postgresql-src",
            "stock_postgres_commit_sha": STOCK_SOURCE_SHA,
            "patched_postgres_repository": "1951123/postgresql-pgextadv",
            "patched_postgres_commit_sha": FROZEN_PATCHED_POSTGRES_SHA,
            "stock_build_identity": stock_identity,
            "patched_build_identity": patched_identity,
            "configure_identity": {
                "stock": stock_identity.get("configure_command"),
                "patched": patched_identity.get("configure_command"),
            },
            "binary_difference_is_intended_secondary_factor": True,
        }
        fixture = {
            "fixture_id": FIXTURE_ID,
            "relation_schema": "public",
            "row_count": 1000,
            "data_generator": "generate_series(1,1000) -> (i % 7, i % 7, i % 3)",
            "setseed": {
                "experiment_seed_identifier": SEED_IDENTIFIER,
                "postgresql_sql": "SELECT setseed(1.0 / 123)",
            },
            "statistics_target": STATISTICS_TARGET,
            "locale": "C.utf8",
            "encoding": "UTF8",
        }
        artifact = build_build_sanity_artifact(
            system=system, fixture=fixture, configurations=configurations
        )
        write_json(output, artifact)
        return {
            "status": "artifact-created",
            "artifact": str(output.resolve()),
            "summary": artifact["summary"],
            "semantic_digest": artifact["semantic_digest"],
            "elapsed_seconds": round(time.monotonic() - started, 6),
        }
    finally:
        for connection in connections.values():
            connection.close()


def validate_build_sanity_file(path: Path) -> dict[str, Any]:
    artifact = read_json(path)
    summary = validate_build_sanity_artifact(artifact)
    return {
        "status": "valid",
        "artifact": str(path.resolve()),
        "summary": summary,
        "semantic_digest": artifact["semantic_digest"],
    }
