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

from . import FROZEN_ADVISOR_SHA, FROZEN_PATCHED_POSTGRES_SHA
from .pins import verify_frozen_systems, verify_research_repository
from .provenance import read_json, reject_credentials, semantic_digest, write_json

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
    "physical-object-selection-difference",
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
        ("statistics_order_equal", "statistics-order-mismatch", True),
        ("planner_settings_equal", "planner-setting-mismatch", True),
        ("physical_object_selection_equal", "physical-object-selection-difference", True),
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
    settings: dict[str, Any],
    physical: dict[str, Any],
    hypothetical: dict[str, Any],
    paired_queries: list[dict[str, Any]],
    controls: dict[str, Any],
    cleanup: dict[str, Any],
) -> dict[str, Any]:
    """Build and validate one immutable RQ3 evidence object."""

    if not isinstance(experiment_id, str) or not experiment_id:
        raise ValueError("experiment_id must be a non-empty string")
    records: list[dict[str, Any]] = []
    for source in paired_queries:
        record = dict(source)
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
    summary = paired_plan_rows_metrics(records)
    artifact: dict[str, Any] = {
        "format": FIDELITY_FORMAT,
        "experiment_id": experiment_id,
        "paper_specification": {
            "identity": PAPER_SPECIFICATION,
            "experiment_id": PRIMARY_EXPERIMENT_ID,
        },
        "system": system,
        "fixture": fixture,
        "settings": settings,
        "controls": controls,
        "physical": physical,
        "hypothetical": hypothetical,
        "paired_queries": records,
        "summary": summary,
        "cleanup": cleanup,
    }
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
        "settings",
        "controls",
        "physical",
        "hypothetical",
        "paired_queries",
        "summary",
        "cleanup",
        "semantic_digest",
    }
    missing = sorted(required - set(artifact))
    if missing:
        raise ValueError(f"RQ3 fidelity artifact is missing fields: {missing}")
    if artifact["format"] != FIDELITY_FORMAT:
        raise ValueError("unsupported RQ3 fidelity artifact format")
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
    _require_sha(system["research_commit_sha"], "research_commit_sha")
    _require_sha(system["advisor_commit_sha"], "advisor_commit_sha", length=40)
    _require_sha(system["patched_postgres_commit_sha"], "patched_postgres_commit_sha", length=40)
    controls = artifact["controls"]
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
        "physical_object_selection_equal",
        "overlay_resolution_verified",
    )
    for field in required_controls:
        if controls.get(field) is not True:
            raise ValueError(f"RQ3 primary control is not verified: {field}")
    physical = artifact["physical"]
    hypothetical = artifact["hypothetical"]
    for side, value in (("physical", physical), ("hypothetical", hypothetical)):
        if not isinstance(value, dict):
            raise TypeError(f"{side} realization must be an object")
        if not value.get("ordinary_stats_fingerprint"):
            raise ValueError(f"{side} ordinary statistics fingerprint is missing")
        _require_sha(value["ordinary_stats_fingerprint"], f"{side} ordinary_stats_fingerprint")
    if physical["ordinary_stats_fingerprint"] != hypothetical["ordinary_stats_fingerprint"]:
        raise ValueError("physical and hypothetical ordinary statistics fingerprints differ")
    if physical.get("payload_correspondence") != "exact-bytes-from-physical-source":
        raise ValueError("physical payload correspondence is not verified by exact bytes")
    if hypothetical.get("payload_source") != "physical-extracted-payloads":
        raise ValueError("hypothetical payload source is not the physical extraction")
    if hypothetical.get("active_candidate_ids") != physical.get("candidate_ids"):
        raise ValueError("physical and hypothetical candidate ordering differs")
    if artifact["cleanup"].get("verified") is not True:
        raise ValueError("RQ3 cleanup was not verified")
    if artifact["cleanup"].get("physical_extstats_count_after") != 0:
        raise ValueError("physical extended-statistics state leaked after cleanup")
    if artifact["cleanup"].get("overlay_active_after") is not None:
        raise ValueError("hypothetical overlay state leaked after cleanup")
    records = artifact["paired_queries"]
    if not isinstance(records, list) or not records:
        raise ValueError("RQ3 artifact needs paired query records")
    summary = paired_plan_rows_metrics(records)
    if artifact["summary"] != summary:
        raise ValueError("RQ3 summary does not match paired query records")
    reject_credentials(artifact)
    return summary


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
        "summary": summary,
        "cleanup_verified": artifact["cleanup"]["verified"],
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
    return str(row[0]), str(row[1])


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


def run_synthetic_fidelity(
    *,
    dsn: str,
    output: Path,
    advisor_root: Path = Path("/home/wqts/projects/extstats-advisor"),
    patched_postgres_root: Path = Path("/home/wqts/projects/postgresql-src-pgextadv"),
    research_root: Path | None = None,
) -> dict[str, Any]:
    """Run only the small MCV+FD same-patched-binary primary comparison."""

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
    verify_frozen_systems(advisor_root, patched_postgres_root)
    import psycopg
    from extstats_advisor.dbms.postgres.ordinary_stats import ordinary_stats_fingerprint
    from extstats_advisor.dbms.postgres.patch import probe_patched_postgres

    connection = None
    relation_oid: int | None = None
    mcv_name = "rq3_fidelity_mcv"
    fd_name = "rq3_fidelity_fd"
    physical_payloads: list[dict[str, Any]] = []
    physical_payload_bytes: dict[str, bytes] = {}
    paired: list[dict[str, Any]] = []
    cleanup: dict[str, Any] = {
        "physical_objects_dropped": False,
        "overlay_reset_before": False,
        "overlay_reset_after": False,
        "physical_extstats_count_after": None,
        "overlay_active_after": None,
        "verified": False,
    }
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
        cleanup["overlay_reset_before"] = True
        connection.execute("SELECT pg_catalog.pg_hypothetical_extstats_reset()")
        if _active_oids(connection) is not None:
            raise RuntimeError("hypothetical overlay reset did not clear active state")
        connection.execute("SET timezone = 'UTC'")
        connection.execute("SET DateStyle = 'ISO, YMD'")
        connection.execute("SET default_statistics_target = 100")
        connection.execute(
            "CREATE TEMP TABLE rq3_fidelity_fixture (a integer, b integer, c integer)"
        )
        connection.execute(
            """
            INSERT INTO rq3_fidelity_fixture
            SELECT i % 7, i % 7, i % 3
              FROM generate_series(1, 1000) AS s(i)
            """
        )
        relation_oid = int(
            connection.execute("SELECT 'rq3_fidelity_fixture'::regclass::oid").fetchone()[0]
        )
        schema, relation = _relation_identity(connection, relation_oid)
        connection.execute(
            "CREATE STATISTICS rq3_fidelity_mcv (mcv) ON a, b FROM rq3_fidelity_fixture"
        )
        connection.execute(
            "CREATE STATISTICS rq3_fidelity_fd (dependencies) ON a, b FROM rq3_fidelity_fixture"
        )
        connection.execute("ALTER STATISTICS rq3_fidelity_mcv SET STATISTICS 100")
        connection.execute("ALTER STATISTICS rq3_fidelity_fd SET STATISTICS 100")
        connection.execute("ANALYZE rq3_fidelity_fixture")
        ordinary_before = ordinary_stats_fingerprint(connection, relation_oid)
        extracted_payloads = [
            _physical_payload(connection, relation_oid, mcv_name, "mcv"),
            _physical_payload(connection, relation_oid, fd_name, "dependencies"),
        ]
        physical_candidate_ids = ("synthetic-mcv", "synthetic-fd")
        for candidate_id, payload in zip(physical_candidate_ids, extracted_payloads, strict=True):
            payload["candidate_id"] = candidate_id
            physical_payload_bytes[candidate_id] = payload.pop("payload")
        physical_payloads = extracted_payloads
        catalog_order = tuple(
            str(row[0])
            for row in connection.execute(
                """
                SELECT e.stxname
                  FROM pg_catalog.pg_statistic_ext AS e
                 WHERE e.stxrelid = %s
                 ORDER BY e.oid
                """,
                (relation_oid,),
            ).fetchall()
        )
        catalog_candidate_order = tuple(
            payload["candidate_id"]
            for name in catalog_order
            for payload in physical_payloads
            if payload["name"] == name
        )
        physical_queries: list[dict[str, Any]] = []
        connection.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        physical_transaction_settings = _settings(connection)
        for query_id, query in _fixture_queries("rq3_fidelity_fixture"):
            rows, document = _plan_rows_and_document(connection, query, schema, relation)
            physical_queries.append(
                {
                    "query_id": query_id,
                    "sql": query,
                    "plan_rows": rows,
                    "explain": document,
                    "explain_sha256": semantic_digest(document),
                }
            )
        connection.execute("ROLLBACK")
        connection.execute("DROP STATISTICS rq3_fidelity_mcv, rq3_fidelity_fd")
        cleanup["physical_objects_dropped"] = True
        if _physical_count(connection, relation_oid) != 0:
            raise RuntimeError("physical extended statistics remained after DROP STATISTICS")
        ordinary_after_drop = ordinary_stats_fingerprint(connection, relation_oid)
        if ordinary_after_drop != ordinary_before:
            raise RuntimeError("dropping physical statistics changed ordinary statistics")
        if catalog_candidate_order != physical_candidate_ids:
            raise RuntimeError(
                "physical statistics catalog order differs from fixture design order"
            )

        connection.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        connection.execute("SELECT pg_catalog.pg_hypothetical_extstats_reset()")
        hypothetical_oids: list[int] = []
        for candidate_id, kind in zip(physical_candidate_ids, ("m", "f"), strict=True):
            row = connection.execute(
                """
                SELECT pg_catalog.pg_hypothetical_extstats_register_definition(
                    %s::text, %s::oid, %s::"char", %s::smallint[], %s::bytea
                )
                """,
                (candidate_id, relation_oid, kind, [1, 2], physical_payload_bytes[candidate_id]),
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
            raise RuntimeError("patched PostgreSQL did not preserve synthetic activation order")
        if _physical_count(connection, relation_oid) != 0:
            raise RuntimeError("hypothetical realization has physical catalog state")
        ordinary_hypothetical = ordinary_stats_fingerprint(connection, relation_oid)
        if ordinary_hypothetical != ordinary_after_drop:
            raise RuntimeError("hypothetical realization changed ordinary statistics")
        hypothetical_transaction_settings = _settings(connection)
        hypothetical_queries: list[dict[str, Any]] = []
        for query_id, query in _fixture_queries("rq3_fidelity_fixture"):
            rows, document = _plan_rows_and_document(connection, query, schema, relation)
            hypothetical_queries.append(
                {
                    "query_id": query_id,
                    "sql": query,
                    "plan_rows": rows,
                    "explain": document,
                    "explain_sha256": semantic_digest(document),
                }
            )
        connection.execute("ROLLBACK")
        connection.execute("SELECT pg_catalog.pg_hypothetical_extstats_reset()")
        cleanup["overlay_reset_after"] = True
        cleanup["overlay_active_after"] = _active_oids(connection)
        cleanup["physical_extstats_count_after"] = _physical_count(connection, relation_oid)
        connection.execute("DROP TABLE rq3_fidelity_fixture")
        if cleanup["overlay_active_after"] is not None:
            raise RuntimeError("hypothetical overlay remained active after cleanup")
        if cleanup["physical_extstats_count_after"] != 0:
            raise RuntimeError("physical statistics leaked after cleanup")
        cleanup["verified"] = True
        by_id = {record["query_id"]: record for record in hypothetical_queries}
        controls = {
            "identical_binary": True,
            "identical_relation_contents": True,
            "identical_schema": True,
            "identical_workload": True,
            "ordinary_stats_equal": ordinary_before == ordinary_after_drop == ordinary_hypothetical,
            "identical_statistics_target": True,
            "equivalent_design": True,
            "payload_correspondence_verified": True,
            "identical_planner_settings": physical_transaction_settings
            == hypothetical_transaction_settings,
            "physical_object_selection_equal": catalog_candidate_order == physical_candidate_ids,
            "overlay_resolution_verified": observed_oids == tuple(hypothetical_oids),
        }
        for physical_record in physical_queries:
            hypothetical_record = by_id[physical_record["query_id"]]
            paired_queries = {
                "query_id": physical_record["query_id"],
                "sql": physical_record["sql"],
                "physical_plan_rows": physical_record["plan_rows"],
                "hypothetical_plan_rows": hypothetical_record["plan_rows"],
                "physical_explain": physical_record["explain"],
                "hypothetical_explain": hypothetical_record["explain"],
                "physical_explain_sha256": physical_record["explain_sha256"],
                "hypothetical_explain_sha256": hypothetical_record["explain_sha256"],
                "mismatch_evidence": {
                    "payload_correspondence": controls["payload_correspondence_verified"],
                    "ordinary_stats_equal": controls["ordinary_stats_equal"],
                    "statistics_order_equal": controls["physical_object_selection_equal"],
                    "planner_settings_equal": controls["identical_planner_settings"],
                    "physical_object_selection_equal": controls["physical_object_selection_equal"],
                    "overlay_resolution_equal": controls["overlay_resolution_verified"],
                },
            }
            paired.append(paired_queries)
        artifact = build_fidelity_artifact(
            experiment_id=SYNTHETIC_FIXTURE_ID,
            system={
                **system,
                "patched_backend_contract": capabilities.backend_contract,
                "patched_reference_source_commit": capabilities.reference_source_commit,
                "patched_server_version": capabilities.server_version,
                "patched_server_version_num": capabilities.server_version_num,
            },
            fixture={
                "fixture_id": SYNTHETIC_FIXTURE_ID,
                "relation": {"schema": schema, "name": relation, "oid": relation_oid},
                "row_count": 1000,
                "data_generator": "generate_series(1,1000) -> (i % 7, i % 7, i % 3)",
                "data_digest": semantic_digest({"generator": "i % 7, i % 7, i % 3", "rows": 1000}),
                "workload": [
                    {"query_id": query_id, "sql": query}
                    for query_id, query in _fixture_queries("rq3_fidelity_fixture")
                ],
                "workload_digest": semantic_digest(
                    [
                        {"query_id": query_id, "sql": query}
                        for query_id, query in _fixture_queries("rq3_fidelity_fixture")
                    ]
                ),
            },
            settings={
                "physical": physical_transaction_settings,
                "hypothetical": hypothetical_transaction_settings,
                "statistics_target": 100,
            },
            physical={
                "candidate_ids": list(physical_candidate_ids),
                "catalog_order_candidate_ids": list(catalog_candidate_order),
                "ordinary_stats_fingerprint": ordinary_after_drop,
                "payload_correspondence": "exact-bytes-from-physical-source",
                "objects": physical_payloads,
                "explain": physical_queries,
            },
            hypothetical={
                "active_candidate_ids": list(physical_candidate_ids),
                "virtual_oids": hypothetical_oids,
                "ordinary_stats_fingerprint": ordinary_after_drop,
                "payload_source": "physical-extracted-payloads",
                "payload_sha256": {
                    candidate_id: physical_payloads[index]["payload_sha256"]
                    for index, candidate_id in enumerate(physical_candidate_ids)
                },
                "explain": hypothetical_queries,
            },
            paired_queries=paired,
            controls=controls,
            cleanup=cleanup,
        )
        write_fidelity_artifact(output, artifact)
        return {
            "status": "ready-to-run",
            "artifact": str(Path(output).resolve()),
            "summary": artifact["summary"],
            "semantic_digest": artifact["semantic_digest"],
            "elapsed_seconds": round(time.monotonic() - started, 6),
        }
    except Exception:
        if connection is not None:
            try:
                connection.execute("SELECT pg_catalog.pg_hypothetical_extstats_reset()")
            except psycopg.Error as cleanup_error:
                cleanup_errors.append(f"overlay reset: {cleanup_error}")
            try:
                connection.execute("DROP TABLE IF EXISTS rq3_fidelity_fixture")
            except psycopg.Error as cleanup_error:
                cleanup_errors.append(f"fixture drop: {cleanup_error}")
        raise
    finally:
        if connection is not None:
            connection.close()
