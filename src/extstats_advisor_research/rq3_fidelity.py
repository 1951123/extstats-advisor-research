"""RQ3 mechanism-fidelity artifacts and the small synthetic runner.

The runner deliberately owns orchestration and evidence capture only.  Native
statistics construction, payload validation, and catalogless overlay behavior
remain PostgreSQL/advisor contracts; this module calls those contracts rather
than implementing a second estimator or search algorithm.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from . import FROZEN_PATCHED_POSTGRES_SHA
from .pins import verify_research_repository
from .postgres_lab import _read_identity, role_spec
from .provenance import read_json, reject_credentials, semantic_digest, write_json
from .system_freeze_v2 import (
    FROZEN_ADVISOR_SHA,
    formal_system_freeze_v2_identity,
    verify_frozen_systems_v2,
)

FIDELITY_FORMAT = "rq3-fidelity-v1"
PAPER_SPECIFICATION = "paper-experiment-v1"
PRIMARY_EXPERIMENT_ID = "rq3-primary-mechanism-fidelity"
SYNTHETIC_FIXTURE_ID = "rq3-synthetic-mcv-fd-v1"

MISMATCH_CATEGORIES = (
    "exact-match",
    "payload-mismatch",
    "ordinary-stats-drift",
    "statistics-order-mismatch",
    "planner-setting-mismatch",
    "overlay-resolution-difference",
    "plan-rows-mismatch-unexplained",
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SHA1 = re.compile(r"^[0-9a-f]{40}$")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_sha(value: Any, label: str, *, length: int = 64) -> str:
    pattern = _SHA256 if length == 64 else _SHA1
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"{label} must be a SHA-{256 if length == 64 else 1} token")
    return value


def relative_plan_rows_delta(physical: int, hypothetical: int) -> float:
    """Use the physical estimate as the denominator with a floor of one."""

    if isinstance(physical, bool) or isinstance(hypothetical, bool):
        raise TypeError("Plan Rows values must be integers")
    if not isinstance(physical, int) or not isinstance(hypothetical, int):
        raise TypeError("Plan Rows values must be integers")
    if physical < 0 or hypothetical < 0:
        raise ValueError("Plan Rows values must be non-negative")
    return abs(hypothetical - physical) / max(abs(physical), 1)


def classify_plan_rows_mismatch(
    physical: int,
    hypothetical: int,
    evidence: dict[str, Any],
) -> str:
    """Classify only from recorded evidence; never infer a cause from magnitude."""

    if physical == hypothetical:
        return "exact-match"
    checks = (
        ("payload_correspondence", "payload-mismatch", True),
        ("ordinary_stats_equal", "ordinary-stats-drift", True),
        ("physical_catalog_order_equal", "statistics-order-mismatch", True),
        ("planner_settings_equal", "planner-setting-mismatch", True),
        ("overlay_resolution_equal", "overlay-resolution-difference", True),
    )
    for field, category, expected in checks:
        if field not in evidence or not isinstance(evidence[field], bool):
            raise ValueError(f"mismatch evidence is missing boolean field {field!r}")
        if evidence[field] is not expected:
            return category
    return "plan-rows-mismatch-unexplained"


def paired_plan_rows_metrics(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Compute direct Plan Rows fidelity metrics without a near-exact threshold."""

    rows = list(records)
    if not rows:
        raise ValueError("at least one paired query record is required")
    exact = 0
    absolute: list[int] = []
    relative: list[float] = []
    categories: Counter[str] = Counter()
    seen: set[str] = set()
    for record in rows:
        query_id = record.get("query_id")
        if not isinstance(query_id, str) or not query_id or query_id in seen:
            raise ValueError("paired query IDs must be unique, non-empty strings")
        seen.add(query_id)
        physical = record.get("physical_plan_rows")
        hypothetical = record.get("hypothetical_plan_rows")
        if not isinstance(physical, int) or isinstance(physical, bool) or physical < 0:
            raise ValueError(f"invalid physical Plan Rows for {query_id!r}")
        if not isinstance(hypothetical, int) or isinstance(hypothetical, bool) or hypothetical < 0:
            raise ValueError(f"invalid hypothetical Plan Rows for {query_id!r}")
        if record.get("exact_match") is not (physical == hypothetical):
            raise ValueError(f"exact_match is inconsistent for {query_id!r}")
        category = record.get("mismatch_category")
        if category not in MISMATCH_CATEGORIES:
            raise ValueError(f"unknown mismatch category for {query_id!r}")
        expected_category = "exact-match"
        if physical != hypothetical:
            expected_category = category
        if category != expected_category:
            raise ValueError(f"mismatch category is inconsistent for {query_id!r}")
        expected_abs = abs(hypothetical - physical)
        expected_rel = relative_plan_rows_delta(physical, hypothetical)
        if record.get("absolute_delta") != expected_abs:
            raise ValueError(f"absolute_delta is inconsistent for {query_id!r}")
        if float(record.get("relative_delta", -1.0)) != expected_rel:
            raise ValueError(f"relative_delta is inconsistent for {query_id!r}")
        exact += int(physical == hypothetical)
        absolute.append(expected_abs)
        relative.append(expected_rel)
        categories[category] += 1
    return {
        "query_count": len(rows),
        "exact_match_count": exact,
        "exact_match_fraction": exact / len(rows),
        "mismatch_count": len(rows) - exact,
        "max_absolute_delta": max(absolute),
        "max_relative_delta": max(relative),
        "mismatch_categories": dict(sorted(categories.items())),
    }


def _artifact_semantic_payload(artifact: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in artifact.items() if key != "semantic_digest"}


def _check_artifact_digest(artifact: dict[str, Any]) -> None:
    expected = semantic_digest(_artifact_semantic_payload(artifact))
    if artifact.get("semantic_digest") != expected:
        raise ValueError("RQ3 fidelity artifact semantic digest mismatch")


def build_fidelity_artifact(
    *,
    experiment_id: str,
    system: dict[str, Any],
    fixture: dict[str, Any],
    configurations: list[dict[str, Any]],
    formal_experiment: bool = False,
    system_freeze: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build and validate one immutable multi-configuration RQ3 artifact."""

    if not isinstance(experiment_id, str) or not experiment_id:
        raise ValueError("experiment_id must be a non-empty string")
    if not configurations:
        raise ValueError("at least one RQ3 configuration is required")
    normalized: list[dict[str, Any]] = []
    all_records: list[dict[str, Any]] = []
    for source in configurations:
        configuration = dict(source)
        configuration_id = configuration.get("configuration_id")
        if not isinstance(configuration_id, str) or not configuration_id:
            raise ValueError("each RQ3 configuration needs a non-empty configuration_id")
        source_records = configuration.pop("paired_queries", None)
        if not isinstance(source_records, list) or not source_records:
            raise ValueError(f"configuration {configuration_id!r} needs paired query records")
        records: list[dict[str, Any]] = []
        for source_record in source_records:
            record = dict(source_record)
            physical_rows = record.get("physical_plan_rows")
            hypothetical_rows = record.get("hypothetical_plan_rows")
            record["exact_match"] = physical_rows == hypothetical_rows
            record["absolute_delta"] = abs(hypothetical_rows - physical_rows)
            record["relative_delta"] = relative_plan_rows_delta(physical_rows, hypothetical_rows)
            evidence = record.pop("mismatch_evidence", None)
            if not isinstance(evidence, dict):
                raise TypeError("each paired query needs mismatch evidence")
            record["mismatch_category"] = classify_plan_rows_mismatch(
                physical_rows, hypothetical_rows, evidence
            )
            record["mismatch_evidence"] = evidence
            records.append(record)
            all_records.append(
                {
                    "query_id": f"{configuration_id}:{record['query_id']}",
                    "physical_plan_rows": physical_rows,
                    "hypothetical_plan_rows": hypothetical_rows,
                    "exact_match": record["exact_match"],
                    "absolute_delta": record["absolute_delta"],
                    "relative_delta": record["relative_delta"],
                    "mismatch_category": record["mismatch_category"],
                }
            )
        configuration["paired_queries"] = records
        configuration["summary"] = paired_plan_rows_metrics(records)
        normalized.append(configuration)
    summary = paired_plan_rows_metrics(all_records)
    artifact: dict[str, Any] = {
        "format": FIDELITY_FORMAT,
        "experiment_id": experiment_id,
        "formal_experiment": formal_experiment,
        "execution_status": "complete" if formal_experiment else "readiness-artifact",
        "paper_specification": {
            "identity": PAPER_SPECIFICATION,
            "experiment_id": PRIMARY_EXPERIMENT_ID,
        },
        "system": system,
        "fixture": fixture,
        "configurations": normalized,
        "summary": summary,
    }
    if system_freeze is not None:
        artifact["system_freeze"] = dict(system_freeze)
    artifact["semantic_digest"] = semantic_digest(artifact)
    validate_fidelity_artifact(artifact)
    return artifact


def validate_fidelity_artifact(artifact: Any) -> dict[str, Any]:
    """Validate an artifact and return its compact summary.

    Missing provenance, unverified payload correspondence, state drift, and
    cleanup failures are errors.  A mismatching query may be recorded, but its
    category must be supported by explicit evidence and the artifact's control
    checks must remain truthful.
    """

    if not isinstance(artifact, dict):
        raise TypeError("RQ3 fidelity artifact must be an object")
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
        raise ValueError(f"RQ3 fidelity artifact is missing fields: {missing}")
    if artifact["format"] != FIDELITY_FORMAT:
        raise ValueError("unsupported RQ3 fidelity artifact format")
    formal_experiment = artifact.get("formal_experiment")
    if not isinstance(formal_experiment, bool):
        raise TypeError("RQ3 formal_experiment must be boolean")
    if formal_experiment:
        if artifact["experiment_id"] != PRIMARY_EXPERIMENT_ID:
            raise ValueError("formal RQ3 artifact must use the primary experiment ID")
        if artifact.get("system_freeze") != formal_system_freeze_v2_identity():
            raise ValueError("formal RQ3 artifact is not bound to system-freeze-v2")
        if artifact.get("execution_status") != "complete":
            raise ValueError("formal RQ3 artifact execution_status is not complete")
    paper = artifact["paper_specification"]
    if paper != {"identity": PAPER_SPECIFICATION, "experiment_id": PRIMARY_EXPERIMENT_ID}:
        raise ValueError("RQ3 artifact is not bound to paper-experiment-v1 RQ3 primary")
    _check_artifact_digest(artifact)
    system = artifact["system"]
    if not isinstance(system, dict):
        raise TypeError("RQ3 system identity must be an object")
    for field in (
        "research_commit_sha",
        "advisor_commit_sha",
        "patched_postgres_commit_sha",
        "patched_backend_contract",
        "patched_server_version",
    ):
        if not system.get(field):
            raise ValueError(f"RQ3 system identity is missing {field}")
    _require_sha(system["research_commit_sha"], "research_commit_sha", length=40)
    _require_sha(system["advisor_commit_sha"], "advisor_commit_sha", length=40)
    _require_sha(system["patched_postgres_commit_sha"], "patched_postgres_commit_sha", length=40)
    if formal_experiment and system["advisor_commit_sha"] != FROZEN_ADVISOR_SHA:
        raise ValueError("formal RQ3 artifact is not bound to the frozen v2 Advisor")
    if formal_experiment and not isinstance(system.get("patched_build_identity"), dict):
        raise TypeError("formal RQ3 artifact is missing patched build identity")
    configurations = artifact["configurations"]
    if not isinstance(configurations, list) or not configurations:
        raise ValueError("RQ3 artifact needs configurations")
    configuration_ids: set[str] = set()
    all_records: list[dict[str, Any]] = []
    for configuration in configurations:
        _validate_configuration(configuration)
        configuration_id = configuration["configuration_id"]
        if configuration_id in configuration_ids:
            raise ValueError(f"duplicate RQ3 configuration ID: {configuration_id}")
        configuration_ids.add(configuration_id)
        records = configuration["paired_queries"]
        config_summary = paired_plan_rows_metrics(records)
        if configuration["summary"] != config_summary:
            raise ValueError(
                f"RQ3 summary does not match paired query records for {configuration_id}"
            )
        all_records.extend(
            {
                **record,
                "query_id": f"{configuration_id}:{record['query_id']}",
            }
            for record in records
        )
    summary = paired_plan_rows_metrics(all_records)
    if artifact["summary"] != summary:
        raise ValueError("RQ3 summary does not match paired query records")
    reject_credentials(artifact)
    return summary


def _validate_configuration(configuration: Any) -> None:
    if not isinstance(configuration, dict):
        raise TypeError("RQ3 configuration must be an object")
    required = {
        "configuration_id",
        "settings",
        "controls",
        "physical",
        "hypothetical",
        "paired_queries",
        "summary",
        "cleanup",
    }
    missing = sorted(required - set(configuration))
    if missing:
        raise ValueError(f"RQ3 configuration is missing fields: {missing}")
    configuration_id = configuration["configuration_id"]
    if not isinstance(configuration_id, str) or not configuration_id:
        raise ValueError("RQ3 configuration_id must be a non-empty string")
    settings = configuration["settings"]
    if not isinstance(settings, dict):
        raise TypeError("RQ3 configuration settings must be an object")
    if settings.get("physical") != settings.get("hypothetical"):
        raise ValueError(f"RQ3 settings differ for {configuration_id}")
    controls = configuration["controls"]
    if not isinstance(controls, dict):
        raise TypeError("RQ3 control evidence must be an object")
    required_controls = (
        "identical_binary",
        "identical_relation_contents",
        "identical_schema",
        "identical_workload",
        "ordinary_stats_equal",
        "identical_statistics_target",
        "equivalent_design",
        "payload_correspondence_verified",
        "identical_planner_settings",
        "physical_catalog_order_equal",
        "overlay_resolution_verified",
    )
    for field in required_controls:
        if controls.get(field) is not True:
            raise ValueError(f"RQ3 primary control is not verified: {field}")
    physical = configuration["physical"]
    hypothetical = configuration["hypothetical"]
    if not isinstance(physical, dict) or not isinstance(hypothetical, dict):
        raise TypeError("RQ3 physical and hypothetical realizations must be objects")
    relation = physical.get("relation")
    if (
        not isinstance(relation, dict)
        or not isinstance(relation.get("schema"), str)
        or not isinstance(relation.get("name"), str)
    ):
        raise TypeError("RQ3 physical relation identity is missing")
    if (
        not isinstance(relation.get("oid"), int)
        or isinstance(relation["oid"], bool)
        or relation["oid"] <= 0
    ):
        raise ValueError("RQ3 physical relation OID is invalid")
    candidate_ids = physical.get("candidate_ids")
    if (
        not isinstance(candidate_ids, list)
        or not candidate_ids
        or any(not isinstance(item, str) or not item for item in candidate_ids)
        or len(set(candidate_ids)) != len(candidate_ids)
    ):
        raise ValueError(
            f"RQ3 physical candidate IDs are not non-empty and unique: {configuration_id}"
        )
    if hypothetical.get("active_candidate_ids") != candidate_ids:
        raise ValueError("physical and hypothetical candidate ordering differs")
    catalog_order = physical.get("catalog_order_candidate_ids")
    if catalog_order != candidate_ids:
        raise ValueError("physical catalog order does not match the declared candidate order")
    for side, value in (("physical", physical), ("hypothetical", hypothetical)):
        fingerprint = value.get("ordinary_stats_fingerprint")
        _require_sha(fingerprint, f"{configuration_id} {side} ordinary_stats_fingerprint")
    if physical["ordinary_stats_fingerprint"] != hypothetical["ordinary_stats_fingerprint"]:
        raise ValueError("physical and hypothetical ordinary statistics fingerprints differ")
    if physical.get("payload_correspondence") != "exact-bytes-from-physical-source":
        raise ValueError("physical payload correspondence is not verified by exact bytes")
    if hypothetical.get("payload_source") != "physical-extracted-payloads":
        raise ValueError("hypothetical payload source is not the physical extraction")
    objects = physical.get("objects")
    if (
        not isinstance(objects, list)
        or [item.get("candidate_id") for item in objects] != candidate_ids
    ):
        raise ValueError("physical object records do not match the exact candidate set/order")
    payload_digests: dict[str, str] = {}
    object_oids: list[int] = []
    for item in objects:
        if not isinstance(item, dict):
            raise TypeError("physical statistics object record must be an object")
        candidate_id = item.get("candidate_id")
        if item.get("kind") not in {"mcv", "dependencies"}:
            raise ValueError(f"unsupported physical statistics kind for {candidate_id}")
        oid = item.get("oid")
        if not isinstance(oid, int) or isinstance(oid, bool) or oid <= 0:
            raise ValueError(f"invalid physical statistics OID for {candidate_id}")
        object_oids.append(oid)
        digest = _require_sha(item.get("payload_sha256"), f"{candidate_id} payload_sha256")
        payload_size = item.get("payload_size")
        if not isinstance(payload_size, int) or isinstance(payload_size, bool) or payload_size <= 0:
            raise ValueError(f"invalid physical payload size for {candidate_id}")
        payload_digests[candidate_id] = digest
    if len(set(object_oids)) != len(object_oids):
        raise ValueError("physical statistics OIDs are not unique")
    if set(hypothetical.get("payload_sha256", {})) != set(candidate_ids):
        raise ValueError("hypothetical payload digest keys do not match candidate IDs")
    if hypothetical["payload_sha256"] != payload_digests:
        raise ValueError("hypothetical payload digests differ from physical payload digests")
    virtual_oids = hypothetical.get("virtual_oids")
    if (
        not isinstance(virtual_oids, list)
        or len(virtual_oids) != len(candidate_ids)
        or any(
            not isinstance(oid, int) or isinstance(oid, bool) or oid <= 0 for oid in virtual_oids
        )
        or len(set(virtual_oids)) != len(virtual_oids)
    ):
        raise ValueError("hypothetical virtual OIDs are missing, invalid, or duplicated")
    physical_explain = physical.get("explain")
    hypothetical_explain = hypothetical.get("explain")
    if not isinstance(physical_explain, list) or not isinstance(hypothetical_explain, list):
        raise TypeError("physical and hypothetical EXPLAIN records must be lists")
    if len(physical_explain) != len(hypothetical_explain) or not physical_explain:
        raise ValueError("physical and hypothetical EXPLAIN record counts differ")
    for physical_record, hypothetical_record in zip(
        physical_explain, hypothetical_explain, strict=True
    ):
        for side, record in (("physical", physical_record), ("hypothetical", hypothetical_record)):
            if not isinstance(record, dict):
                raise TypeError(f"{side} EXPLAIN record must be an object")
            if not isinstance(record.get("query_id"), str) or not isinstance(
                record.get("sql"), str
            ):
                raise TypeError("EXPLAIN query identity is missing")
            expected_digest = semantic_digest(record.get("explain"))
            if record.get("explain_sha256") != expected_digest:
                raise ValueError(f"{side} EXPLAIN semantic digest mismatch")
    physical_query_identity = [(item["query_id"], item["sql"]) for item in physical_explain]
    hypothetical_query_identity = [(item["query_id"], item["sql"]) for item in hypothetical_explain]
    if physical_query_identity != hypothetical_query_identity:
        raise ValueError("physical and hypothetical workload query order differs")
    records = configuration["paired_queries"]
    if not isinstance(records, list) or not records:
        raise ValueError("RQ3 configuration needs paired query records")
    if [(item.get("query_id"), item.get("sql")) for item in records] != physical_query_identity:
        raise ValueError("paired query identity/order differs from EXPLAIN records")
    for record, physical_explain_record, hypothetical_explain_record in zip(
        records, physical_explain, hypothetical_explain, strict=True
    ):
        baseline_rows = record.get("no_extstats_baseline_plan_rows")
        changed_from_baseline = record.get("physical_estimate_changed_from_no_extstats_baseline")
        if (
            not isinstance(baseline_rows, int)
            or isinstance(baseline_rows, bool)
            or not isinstance(changed_from_baseline, bool)
            or changed_from_baseline != (record["physical_plan_rows"] != baseline_rows)
        ):
            raise ValueError("baseline-to-physical estimate evidence is missing or inconsistent")
        if record.get("physical_explain") != physical_explain_record.get("explain"):
            raise ValueError("paired physical EXPLAIN does not match stored physical EXPLAIN")
        if record.get("hypothetical_explain") != hypothetical_explain_record.get("explain"):
            raise ValueError(
                "paired hypothetical EXPLAIN does not match stored hypothetical EXPLAIN"
            )
        if record.get("physical_explain_sha256") != physical_explain_record.get("explain_sha256"):
            raise ValueError("paired physical EXPLAIN digest does not match stored digest")
        if record.get("hypothetical_explain_sha256") != hypothetical_explain_record.get(
            "explain_sha256"
        ):
            raise ValueError("paired hypothetical EXPLAIN digest does not match stored digest")
    evidence = physical.get("mechanism_evidence")
    if not isinstance(evidence, dict) or evidence.get("payloads_nonempty") is not True:
        raise ValueError("RQ3 payload evidence is missing or empty")
    if (
        not isinstance(evidence.get("supported_clause_form"), str)
        or not evidence["supported_clause_form"]
    ):
        raise ValueError("RQ3 supported clause-form evidence is missing")
    cleanup = configuration["cleanup"]
    if (
        not isinstance(cleanup, dict)
        or cleanup.get("verified") is not True
        or cleanup.get("physical_extstats_count_after") != 0
        or cleanup.get("overlay_active_after") is not None
    ):
        raise ValueError("RQ3 configuration cleanup was not verified")


def write_fidelity_artifact(path: Path, artifact: dict[str, Any]) -> None:
    validate_fidelity_artifact(artifact)
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"RQ3 fidelity artifact destination already exists: {path}")
    write_json(path, artifact)


def inspect_fidelity_artifact(path: Path) -> dict[str, Any]:
    artifact = read_json(path)
    summary = validate_fidelity_artifact(artifact)
    return {
        "format": artifact["format"],
        "experiment_id": artifact["experiment_id"],
        "semantic_digest": artifact["semantic_digest"],
        "fixture_id": artifact["fixture"]["fixture_id"],
        "configuration_ids": [
            configuration["configuration_id"] for configuration in artifact["configurations"]
        ],
        "summary": summary,
        "cleanup_verified": all(
            configuration["cleanup"]["verified"] for configuration in artifact["configurations"]
        ),
    }


def _fixture_queries(table: str) -> tuple[tuple[str, str], ...]:
    # The table identifier is generated by this module and never user SQL.
    return (
        ("mcv-fd-equal", f"SELECT * FROM {table} WHERE a = 1 AND b = 1"),
        ("mcv-fd-rare", f"SELECT * FROM {table} WHERE a = 2 AND b = 2"),
        ("mcv-fd-cross", f"SELECT * FROM {table} WHERE a = 1 AND b = 2"),
    )


def _relation_identity(connection: Any, relation_oid: int) -> tuple[str, str]:
    row = connection.execute(
        """
        SELECT n.nspname, c.relname
          FROM pg_catalog.pg_class AS c
          JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
         WHERE c.oid = %s
        """,
        (relation_oid,),
    ).fetchone()
    if row is None:
        raise RuntimeError("synthetic RQ3 relation disappeared")
    schema = str(row[0])
    # PostgreSQL catalogs expose a session-local temporary namespace as
    # pg_temp_N, while EXPLAIN VERBOSE renders the same relation as pg_temp.
    # Normalize the catalog identity to the planner's displayed identity.
    if schema.startswith("pg_temp_"):
        schema = "pg_temp"
    return schema, str(row[1])


def _plan_rows_and_document(
    connection: Any, query: str, schema: str, relation: str
) -> tuple[int, Any]:
    from extstats_advisor.dbms.postgres.native_stats import _psycopg

    sql = _psycopg().sql
    row = connection.execute(
        sql.SQL("EXPLAIN (VERBOSE, FORMAT JSON) {} ").format(sql.SQL(query))
    ).fetchone()
    if row is None or not row[0]:
        raise RuntimeError("PostgreSQL returned an empty synthetic EXPLAIN plan")
    document = row[0][0] if isinstance(row[0], list) else row[0]
    nodes: list[dict[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("Relation Name") == relation and value.get("Schema") == schema:
                nodes.append(value)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(document)
    if len(nodes) != 1 or "Plan Rows" not in nodes[0]:
        raise RuntimeError("synthetic EXPLAIN does not contain one managed base-relation node")
    rows = int(nodes[0]["Plan Rows"])
    if rows < 0:
        raise RuntimeError("synthetic EXPLAIN returned negative Plan Rows")
    return rows, document


def _settings(connection: Any) -> dict[str, str]:
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


def _physical_payload(connection: Any, relation_oid: int, name: str, kind: str) -> dict[str, Any]:
    expression = (
        "pg_catalog.pg_mcv_list_send(d.stxdmcv)"
        if kind == "mcv"
        else "pg_catalog.pg_dependencies_send(d.stxddependencies)"
    )
    row = connection.execute(
        f"""
        SELECT e.oid::bigint, e.stxkeys::text, e.stxkind::text,
               pg_catalog.pg_get_statisticsobjdef(e.oid), {expression}
          FROM pg_catalog.pg_statistic_ext AS e
          LEFT JOIN pg_catalog.pg_statistic_ext_data AS d ON d.stxoid = e.oid
         WHERE e.stxrelid = %s AND e.stxname = %s
        """,
        (relation_oid, name),
    ).fetchone()
    if row is None or row[4] is None:
        raise RuntimeError(f"physical {kind} payload is missing for {name}")
    payload = bytes(row[4])
    return {
        "kind": kind,
        "name": name,
        "oid": int(row[0]),
        "keys": str(row[1]),
        "catalog_kinds": str(row[2]),
        "definition": str(row[3]),
        "payload_sha256": _sha256_bytes(payload),
        "payload_size": len(payload),
        "payload": payload,
    }


def _physical_count(connection: Any, relation_oid: int) -> int:
    return int(
        connection.execute(
            "SELECT count(*) FROM pg_catalog.pg_statistic_ext WHERE stxrelid = %s",
            (relation_oid,),
        ).fetchone()[0]
    )


def _active_oids(connection: Any) -> tuple[int, ...] | None:
    value = connection.execute("SELECT pg_catalog.pg_hypothetical_extstats_active()").fetchone()[0]
    return None if value is None else tuple(int(item) for item in value)


SYNTHETIC_CONFIGURATIONS = (
    ("mcv-only", (("synthetic-mcv", "mcv"),)),
    ("fd-only", (("synthetic-fd", "dependencies"),)),
    ("mcv-plus-fd", (("synthetic-mcv", "mcv"), ("synthetic-fd", "dependencies"))),
)


def fidelity_gate(summary: dict[str, Any]) -> str:
    """Return the live artifact gate without promoting paper status."""

    if not isinstance(summary, dict) or not isinstance(summary.get("mismatch_count"), int):
        raise TypeError("RQ3 summary must contain an integer mismatch_count")
    return "pass" if summary["mismatch_count"] == 0 else "fail"


def fidelity_run_result(
    artifact: dict[str, Any], output: Path, elapsed_seconds: float = 0.0
) -> dict[str, Any]:
    """Return the non-promoting result contract for a written fidelity artifact."""

    summary = validate_fidelity_artifact(artifact)
    configurations = artifact["configurations"]
    return {
        "status": "artifact-created",
        "fidelity_gate": fidelity_gate(summary),
        "artifact": str(Path(output).resolve()),
        "summary": summary,
        "configuration_count": len(configurations),
        "configuration_ids": [item["configuration_id"] for item in configurations],
        "semantic_digest": artifact["semantic_digest"],
        "elapsed_seconds": round(elapsed_seconds, 6),
    }


def _capture_queries(
    connection: Any, table: str, schema: str, relation: str
) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []
    for query_id, query in _fixture_queries(table):
        rows, document = _plan_rows_and_document(connection, query, schema, relation)
        captured.append(
            {
                "query_id": query_id,
                "sql": query,
                "plan_rows": rows,
                "explain": document,
                "explain_sha256": semantic_digest(document),
            }
        )
    return captured


def _run_synthetic_configuration(
    connection: Any,
    *,
    configuration_id: str,
    object_specs: tuple[tuple[str, str], ...],
) -> dict[str, Any]:
    from extstats_advisor.dbms.postgres.ordinary_stats import (
        ordinary_stats_fingerprint as fingerprint,
    )

    suffix = configuration_id.replace("-", "_")
    table = f"rq3_fidelity_{suffix}"
    stats_names = tuple(
        f"rq3_{suffix}_{candidate_id.rsplit('-', 1)[-1]}" for candidate_id, _ in object_specs
    )
    candidate_ids = tuple(candidate_id for candidate_id, _ in object_specs)
    kind_chars = {"mcv": "m", "dependencies": "f"}
    cleanup: dict[str, Any] = {
        "physical_objects_dropped": False,
        "overlay_reset_before": False,
        "overlay_reset_after": False,
        "physical_extstats_count_after": None,
        "overlay_active_after": None,
        "verified": False,
    }
    connection.execute("SELECT pg_catalog.pg_hypothetical_extstats_reset()")
    cleanup["overlay_reset_before"] = True
    connection.execute(f"CREATE TEMP TABLE {table} (a integer, b integer, c integer)")
    connection.execute(
        f"INSERT INTO {table} SELECT i % 7, i % 7, i % 3 FROM generate_series(1, 1000) AS s(i)"
    )
    relation_oid = int(connection.execute(f"SELECT '{table}'::regclass::oid").fetchone()[0])
    schema, relation = _relation_identity(connection, relation_oid)
    connection.execute(f"ANALYZE {table}")
    connection.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
    baseline_settings = _settings(connection)
    baseline_queries = _capture_queries(connection, table, schema, relation)
    connection.execute("ROLLBACK")
    for stats_name, (_, kind) in zip(stats_names, object_specs, strict=True):
        connection.execute(f"CREATE STATISTICS {stats_name} ({kind}) ON a, b FROM {table}")
        connection.execute(f"ALTER STATISTICS {stats_name} SET STATISTICS 100")
    connection.execute(f"ANALYZE {table}")
    ordinary_physical = fingerprint(connection, relation_oid)
    physical_objects: list[dict[str, Any]] = []
    payload_bytes: dict[str, bytes] = {}
    for (candidate_id, kind), stats_name in zip(object_specs, stats_names, strict=True):
        payload = _physical_payload(connection, relation_oid, stats_name, kind)
        payload["candidate_id"] = candidate_id
        payload_bytes[candidate_id] = payload.pop("payload")
        physical_objects.append(payload)
    catalog_order = tuple(
        str(row[0])
        for row in connection.execute(
            "SELECT e.stxname FROM pg_catalog.pg_statistic_ext AS e "
            "WHERE e.stxrelid = %s ORDER BY e.oid",
            (relation_oid,),
        ).fetchall()
    )
    catalog_candidate_order = tuple(
        object_record["candidate_id"]
        for name in catalog_order
        for object_record in physical_objects
        if object_record["name"] == name
    )
    connection.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
    physical_settings = _settings(connection)
    physical_queries = _capture_queries(connection, table, schema, relation)
    connection.execute("ROLLBACK")
    connection.execute(f"DROP STATISTICS {', '.join(stats_names)}")
    cleanup["physical_objects_dropped"] = True
    if _physical_count(connection, relation_oid) != 0:
        raise RuntimeError(f"physical extended statistics leaked for {configuration_id}")
    ordinary_after_drop = fingerprint(connection, relation_oid)
    if ordinary_after_drop != ordinary_physical:
        raise RuntimeError(
            f"dropping physical statistics changed ordinary state for {configuration_id}"
        )
    if catalog_candidate_order != candidate_ids:
        raise RuntimeError(f"physical catalog order differs for {configuration_id}")
    connection.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
    connection.execute("SELECT pg_catalog.pg_hypothetical_extstats_reset()")
    hypothetical_oids: list[int] = []
    for candidate_id, kind in object_specs:
        row = connection.execute(
            """
            SELECT pg_catalog.pg_hypothetical_extstats_register_definition(
                %s::text, %s::oid, %s::"char", %s::smallint[], %s::bytea
            )
            """,
            (candidate_id, relation_oid, kind_chars[kind], [1, 2], payload_bytes[candidate_id]),
        ).fetchone()
        if row is None or row[0] is None:
            raise RuntimeError(f"hypothetical registration returned no OID for {candidate_id}")
        hypothetical_oids.append(int(row[0]))
    connection.execute(
        "SELECT pg_catalog.pg_hypothetical_extstats_activate(%s::oid[])",
        (hypothetical_oids,),
    )
    observed_oids = _active_oids(connection)
    if observed_oids != tuple(hypothetical_oids):
        raise RuntimeError(f"hypothetical activation order changed for {configuration_id}")
    if _physical_count(connection, relation_oid) != 0:
        raise RuntimeError(f"hypothetical realization has physical state for {configuration_id}")
    ordinary_hypothetical = fingerprint(connection, relation_oid)
    if ordinary_hypothetical != ordinary_after_drop:
        raise RuntimeError(
            f"hypothetical realization changed ordinary state for {configuration_id}"
        )
    hypothetical_settings = _settings(connection)
    hypothetical_queries = _capture_queries(connection, table, schema, relation)
    connection.execute("ROLLBACK")
    connection.execute("SELECT pg_catalog.pg_hypothetical_extstats_reset()")
    cleanup["overlay_reset_after"] = True
    cleanup["overlay_active_after"] = _active_oids(connection)
    cleanup["physical_extstats_count_after"] = _physical_count(connection, relation_oid)
    connection.execute(f"DROP TABLE {table}")
    if cleanup["overlay_active_after"] is not None or cleanup["physical_extstats_count_after"] != 0:
        raise RuntimeError(f"cleanup leaked state for {configuration_id}")
    cleanup["verified"] = True
    hypothetical_by_id = {record["query_id"]: record for record in hypothetical_queries}
    controls = {
        "identical_binary": True,
        "identical_relation_contents": True,
        "identical_schema": True,
        "identical_workload": True,
        "ordinary_stats_equal": ordinary_physical == ordinary_after_drop == ordinary_hypothetical,
        "identical_statistics_target": True,
        "equivalent_design": True,
        "payload_correspondence_verified": True,
        "identical_planner_settings": physical_settings == hypothetical_settings,
        "physical_catalog_order_equal": catalog_candidate_order == candidate_ids,
        "overlay_resolution_verified": observed_oids == tuple(hypothetical_oids),
    }
    paired: list[dict[str, Any]] = []
    for baseline_record, physical_record in zip(baseline_queries, physical_queries, strict=True):
        hypothetical_record = hypothetical_by_id[physical_record["query_id"]]
        paired.append(
            {
                "query_id": physical_record["query_id"],
                "sql": physical_record["sql"],
                "physical_plan_rows": physical_record["plan_rows"],
                "hypothetical_plan_rows": hypothetical_record["plan_rows"],
                "no_extstats_baseline_plan_rows": baseline_record["plan_rows"],
                "physical_estimate_changed_from_no_extstats_baseline": (
                    physical_record["plan_rows"] != baseline_record["plan_rows"]
                ),
                "physical_explain": physical_record["explain"],
                "hypothetical_explain": hypothetical_record["explain"],
                "physical_explain_sha256": physical_record["explain_sha256"],
                "hypothetical_explain_sha256": hypothetical_record["explain_sha256"],
                "mismatch_evidence": {
                    "payload_correspondence": True,
                    "ordinary_stats_equal": controls["ordinary_stats_equal"],
                    "physical_catalog_order_equal": controls["physical_catalog_order_equal"],
                    "planner_settings_equal": controls["identical_planner_settings"],
                    "overlay_resolution_equal": controls["overlay_resolution_verified"],
                },
            }
        )
    return {
        "configuration_id": configuration_id,
        "settings": {
            "physical": physical_settings,
            "hypothetical": hypothetical_settings,
            "statistics_target": 100,
        },
        "controls": controls,
        "physical": {
            "relation": {"schema": schema, "name": relation, "oid": relation_oid},
            "candidate_ids": list(candidate_ids),
            "catalog_order_candidate_ids": list(catalog_candidate_order),
            "ordinary_stats_fingerprint": ordinary_after_drop,
            "payload_correspondence": "exact-bytes-from-physical-source",
            "objects": physical_objects,
            "explain": physical_queries,
            "mechanism_evidence": {
                "payloads_nonempty": all(item["payload_size"] > 0 for item in physical_objects),
                "supported_clause_form": "simple equality conjunction over (a, b)",
                "baseline_settings": baseline_settings,
            },
        },
        "hypothetical": {
            "active_candidate_ids": list(candidate_ids),
            "virtual_oids": hypothetical_oids,
            "ordinary_stats_fingerprint": ordinary_after_drop,
            "payload_source": "physical-extracted-payloads",
            "payload_sha256": {
                item["candidate_id"]: item["payload_sha256"] for item in physical_objects
            },
            "explain": hypothetical_queries,
        },
        "paired_queries": paired,
        "cleanup": cleanup,
    }


def run_synthetic_fidelity(
    *,
    dsn: str,
    output: Path,
    advisor_root: Path = Path("/home/wqts/projects/extstats-advisor"),
    patched_postgres_root: Path = Path("/home/wqts/projects/postgresql-src-pgextadv"),
    research_root: Path | None = None,
    formal_experiment: bool = False,
) -> dict[str, Any]:
    """Run the three small same-patched-binary RQ3 mechanism comparisons."""

    if not dsn or not isinstance(dsn, str):
        raise ValueError("a patched PostgreSQL DSN is required")
    research_root = research_root or Path(__file__).resolve().parents[2]
    research_identity = verify_research_repository(research_root)
    system = {
        **research_identity,
        "advisor_repository": "1951123/extstats-advisor",
        "advisor_commit_sha": FROZEN_ADVISOR_SHA,
        "patched_postgres_repository": "1951123/postgresql-pgextadv",
        "patched_postgres_commit_sha": FROZEN_PATCHED_POSTGRES_SHA,
    }
    verify_frozen_systems_v2(advisor_root, patched_postgres_root)
    patched_build_identity = _read_identity(role_spec("patched"))
    import psycopg
    from extstats_advisor.dbms.postgres.patch import probe_patched_postgres

    connection = None
    created_tables: list[str] = []
    cleanup_errors: list[str] = []
    started = time.monotonic()
    try:
        connection = psycopg.connect(
            dsn, application_name="extstats-research-rq3-fidelity", autocommit=True
        )
        capabilities = probe_patched_postgres(connection)
        if capabilities.reference_source_commit != FROZEN_PATCHED_POSTGRES_SHA:
            raise RuntimeError(
                "connected patched PostgreSQL reference source does not match the pinned source"
            )
        connection.execute("SELECT pg_catalog.pg_hypothetical_extstats_reset()")
        if _active_oids(connection) is not None:
            raise RuntimeError("hypothetical overlay reset did not clear active state")
        connection.execute("SET timezone = 'UTC'")
        connection.execute("SET DateStyle = 'ISO, YMD'")
        connection.execute("SET default_statistics_target = 100")
        configurations: list[dict[str, Any]] = []
        for configuration_id, object_specs in SYNTHETIC_CONFIGURATIONS:
            created_tables.append(f"rq3_fidelity_{configuration_id.replace('-', '_')}")
            configurations.append(
                _run_synthetic_configuration(
                    connection,
                    configuration_id=configuration_id,
                    object_specs=object_specs,
                )
            )
        first_table = "rq3_fidelity_mcv_only"
        fixture_workload = [
            {"query_id": query_id, "sql": query}
            for query_id, query in _fixture_queries(first_table)
        ]
        artifact = build_fidelity_artifact(
            experiment_id=PRIMARY_EXPERIMENT_ID if formal_experiment else SYNTHETIC_FIXTURE_ID,
            system={
                **system,
                "patched_backend_contract": capabilities.backend_contract,
                "patched_reference_source_commit": capabilities.reference_source_commit,
                "patched_server_version": capabilities.server_version,
                "patched_server_version_num": capabilities.server_version_num,
                "patched_build_identity": patched_build_identity,
            },
            fixture={
                "fixture_id": SYNTHETIC_FIXTURE_ID,
                "relation_scope": "three independent temporary relations, one per configuration",
                "row_count": 1000,
                "data_generator": "generate_series(1,1000) -> (i % 7, i % 7, i % 3)",
                "data_digest": semantic_digest({"generator": "i % 7, i % 7, i % 3", "rows": 1000}),
                "workload": fixture_workload,
                "workload_digest": semantic_digest(fixture_workload),
            },
            configurations=configurations,
            formal_experiment=formal_experiment,
            system_freeze=formal_system_freeze_v2_identity() if formal_experiment else None,
        )
        write_fidelity_artifact(output, artifact)
        return fidelity_run_result(artifact, output, time.monotonic() - started)
    except Exception as experiment_error:
        if connection is not None:
            try:
                # An EXPLAIN capture intentionally runs in a read-only
                # transaction. End it before attempting fixture cleanup.
                connection.rollback()
            except psycopg.Error as cleanup_error:
                cleanup_errors.append(f"transaction rollback: {cleanup_error}")
            try:
                connection.execute("SELECT pg_catalog.pg_hypothetical_extstats_reset()")
            except psycopg.Error as cleanup_error:
                cleanup_errors.append(f"overlay reset: {cleanup_error}")
            for table in created_tables:
                try:
                    connection.execute(f"DROP TABLE IF EXISTS {table}")
                except psycopg.Error as cleanup_error:
                    cleanup_errors.append(f"fixture drop {table}: {cleanup_error}")
        if cleanup_errors:
            diagnostic = "; ".join(cleanup_errors)
            raise RuntimeError(
                f"{experiment_error}; cleanup also failed: {diagnostic}"
            ) from experiment_error
        raise
    finally:
        if connection is not None:
            connection.close()
