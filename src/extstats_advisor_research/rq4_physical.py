"""Controlled stock-PostgreSQL realization evidence for RQ4b.

The selection/search side remains in :mod:`rq4_ablation` and the estimator
remains PostgreSQL.  This module only orchestrates native ``CREATE
STATISTICS``/``ANALYZE`` and EXPLAIN calls, recording the distinction between
one shared native realization and the later no-ANALYZE method clones.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from . import FROZEN_ADVISOR_SHA
from .pins import verify_git_sha
from .provenance import read_json, semantic_digest, write_json
from .rq4_ablation import METHOD_IDS, RQ4ValidationError

PHYSICAL_FORMAT = "rq4-stock-physical-evaluation-v1"
SHARED_REALIZATION_FORMAT = "rq4-stock-shared-realization-v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _runtime_free(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _runtime_free(item)
            for key, item in value.items()
            if not (
                str(key).endswith("_seconds")
                or str(key) in {"elapsed_seconds", "wall_clock_seconds", "wall_time_seconds"}
            )
        }
    if isinstance(value, list):
        return [_runtime_free(item) for item in value]
    return value


def union_selected_memberships(selected_by_method: Mapping[str, Sequence[str]]) -> tuple[str, ...]:
    """Return a stable union without allowing method order to affect realization."""

    if not selected_by_method:
        raise RQ4ValidationError("shared stock realization needs at least one method")
    unknown = set(selected_by_method) - set(METHOD_IDS)
    if unknown:
        raise RQ4ValidationError(f"unknown RQ4 method in shared realization: {sorted(unknown)}")
    return tuple(
        sorted({candidate_id for ids in selected_by_method.values() for candidate_id in ids})
    )


def validate_method_union(
    selected_by_method: Mapping[str, Sequence[str]], union: Sequence[str]
) -> None:
    expected = set(union_selected_memberships(selected_by_method))
    if set(union) != expected or len(tuple(union)) != len(expected):
        raise RQ4ValidationError("shared stock realization union does not cover method memberships")
    for method, selected in selected_by_method.items():
        if not set(selected).issubset(expected):
            raise RQ4ValidationError(f"{method} selected a candidate outside the shared union")


def _quote_identifier(value: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("invalid PostgreSQL identifier")
    return '"' + value.replace('"', '""') + '"'


def _candidate_definition(candidate: Any) -> tuple[str, str, tuple[int, ...]]:
    kind = {"postgresql.mcv": "mcv", "postgresql.dependencies": "dependencies"}.get(candidate.kind)
    if kind is None:
        raise ValueError(f"unsupported physical candidate kind: {candidate.kind}")
    return kind, candidate.candidate_id, tuple(candidate.column_ordinals)


def _candidate_name(candidate_id: str, kind: str) -> str:
    from extstats_advisor.dbms.postgres.recommendation import postgres_statistics_object_name

    return postgres_statistics_object_name(candidate_id, kind)


def _connect(dsn: str) -> Any:
    import psycopg

    return psycopg.connect(dsn, autocommit=True, application_name="extstats-research-rq4b")


def _relation_oid(connection: Any, schema: str, relation: str) -> int:
    row = connection.execute("SELECT to_regclass(%s)::oid", (f"{schema}.{relation}",)).fetchone()
    if row is None or row[0] is None:
        raise RQ4ValidationError(f"physical fixture relation is missing: {schema}.{relation}")
    return int(row[0])


def _existing_statistics(connection: Any, relation_oid: int) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT e.oid::bigint, n.nspname, e.stxname, e.stxkind::text, e.stxkeys::text "
        "FROM pg_catalog.pg_statistic_ext AS e "
        "JOIN pg_catalog.pg_namespace AS n ON n.oid=e.stxnamespace "
        "WHERE e.stxrelid=%s ORDER BY e.oid",
        (relation_oid,),
    ).fetchall()
    return [
        {
            "oid": int(row[0]),
            "schema": str(row[1]),
            "name": str(row[2]),
            "kind": str(row[3]),
            "keys": str(row[4]),
        }
        for row in rows
    ]


def _ordinary_fingerprint(connection: Any, relation_oid: int, advisor_root: Path) -> str:
    advisor_src = str(advisor_root / "src")
    if advisor_src not in sys.path:
        sys.path.insert(0, advisor_src)
    from extstats_advisor.dbms.postgres.ordinary_stats import ordinary_stats_fingerprint

    return str(ordinary_stats_fingerprint(connection, relation_oid))


def _payload_record(
    connection: Any, relation_oid: int, schema: str, candidate: Any
) -> dict[str, Any]:
    kind, candidate_id, ordinals = _candidate_definition(candidate)
    name = _candidate_name(candidate_id, candidate.kind)
    expression = (
        "pg_catalog.pg_mcv_list_send(d.stxdmcv)"
        if kind == "mcv"
        else "pg_catalog.pg_dependencies_send(d.stxddependencies)"
    )
    row = connection.execute(
        "SELECT e.oid::bigint, e.stxname, e.stxkind::text, e.stxkeys::text, "
        "e.stxstattarget, " + expression + " "
        "FROM pg_catalog.pg_statistic_ext AS e "
        "JOIN pg_catalog.pg_namespace AS n ON n.oid=e.stxnamespace "
        "LEFT JOIN pg_catalog.pg_statistic_ext_data AS d ON d.stxoid=e.oid "
        "WHERE e.stxrelid=%s AND n.nspname=%s AND e.stxname=%s",
        (relation_oid, schema, name),
    ).fetchone()
    if row is None:
        raise RQ4ValidationError(f"physical statistics object is missing: {candidate_id}")
    payload = b"" if row[5] is None else bytes(row[5])
    expected_keys = " ".join(str(item) for item in ordinals)
    if str(row[3]).strip() != expected_keys:
        raise RQ4ValidationError(f"physical statistics keys mismatch: {candidate_id}")
    return {
        "candidate_id": candidate_id,
        "schema": schema,
        "name": str(row[1]),
        "oid": int(row[0]),
        "kind": candidate.kind,
        "column_ordinals": list(ordinals),
        "statistics_target": int(row[4]),
        "payload_bytes": len(payload),
        "payload_sha256": _sha256_bytes(payload),
        "payload_present": bool(payload),
        "payload_serialization": (
            "postgresql.pg_mcv_list_send-v1"
            if kind == "mcv"
            else "postgresql.pg_dependencies_send-v1"
        ),
        # The bytes are compared in-process between the parent and clones;
        # only their digest/length is serialized to keep artifacts compact.
        "payload_bytes_observed": payload,
    }


def _serialize_payload_record(record: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key != "payload_bytes_observed"}


def _capture_payloads(
    connection: Any,
    relation_oid: int,
    schema: str,
    candidates_by_id: Mapping[str, Any],
    candidate_ids: Sequence[str],
) -> tuple[dict[str, dict[str, Any]], dict[str, bytes]]:
    records: dict[str, dict[str, Any]] = {}
    raw: dict[str, bytes] = {}
    for candidate_id in candidate_ids:
        record = _payload_record(connection, relation_oid, schema, candidates_by_id[candidate_id])
        records[candidate_id] = _serialize_payload_record(record)
        raw[candidate_id] = bytes(record["payload_bytes_observed"])
    return records, raw


def _create_statistics(
    connection: Any,
    relation_schema: str,
    relation_name: str,
    candidates_by_id: Mapping[str, Any],
    candidate_ids: Sequence[str],
    statistics_target: int,
) -> list[str]:
    created: list[str] = []
    relation_sql = f"{_quote_identifier(relation_schema)}.{_quote_identifier(relation_name)}"
    for candidate_id in candidate_ids:
        candidate = candidates_by_id[candidate_id]
        kind, _, ordinals = _candidate_definition(candidate)
        name = _candidate_name(candidate_id, candidate.kind)
        columns = ", ".join(_quote_identifier(column) for column in candidate.column_names)
        connection.execute(
            f"CREATE STATISTICS {_quote_identifier(relation_schema)}.{_quote_identifier(name)} "
            f"({kind}) ON {columns} FROM {relation_sql}"
        )
        connection.execute(
            f"ALTER STATISTICS {_quote_identifier(relation_schema)}.{_quote_identifier(name)} "
            f"SET STATISTICS {int(statistics_target)}"
        )
        if len(ordinals) != len(candidate.column_names):
            raise RQ4ValidationError(f"candidate schema mismatch: {candidate_id}")
        created.append(name)
    return created


def _drop_statistics(connection: Any, schema: str, names: Sequence[str]) -> None:
    for name in names:
        connection.execute(
            f"DROP STATISTICS IF EXISTS {_quote_identifier(schema)}.{_quote_identifier(name)}"
        )


def _explain_rows(connection: Any, queries: Sequence[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for query in queries:
        sql = str(query.sql).rstrip().rstrip(";").rstrip()
        document = connection.execute(f"EXPLAIN (FORMAT JSON) {sql}").fetchone()[0]
        if isinstance(document, str):
            document = json.loads(document)
        estimate = int(document[0]["Plan"]["Plan Rows"])
        rows.append({"query_id": query.query_id, "sql": query.sql, "plan_rows": estimate})
    return rows


def _metrics(
    records: Sequence[Mapping[str, Any]], loss: Any, truth_by_id: Mapping[str, Any]
) -> dict[str, Any]:
    values: list[float] = []
    output: list[dict[str, Any]] = []
    for record in records:
        truth = int(truth_by_id[record["query_id"]])
        qerror = float(loss.loss(int(record["plan_rows"]), truth))
        values.append(qerror)
        output.append(
            {
                **dict(record),
                "truth": truth,
                "qerror": qerror,
                "weight": 1.0,
            }
        )
    if not values:
        raise RQ4ValidationError("physical evaluation requires at least one query")
    ordered = sorted(values)

    def percentile(fraction: float) -> float:
        index = (len(ordered) - 1) * fraction
        lower = int(index)
        upper = min(lower + 1, len(ordered) - 1)
        return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)

    return {
        "query_count": len(output),
        "mean": sum(values) / len(values),
        "weighted_objective": sum(values) / len(values),
        "p50": percentile(0.50),
        "p95": percentile(0.95),
        "p99": percentile(0.99),
        "max": max(values),
        "per_query": output,
        "qerror_contract": "qerror-cardinality-floor-1-v1",
        "lower_is_better": True,
    }


def _make_clone_dsn(dsn: str, database: str) -> tuple[str, str]:
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    fields = conninfo_to_dict(dsn)
    parent = str(fields.get("dbname") or "")
    if not parent:
        raise RQ4ValidationError("physical clone requires a named parent database")
    clone = f"rq4_clone_{uuid.uuid4().hex[:12]}"
    clone_fields = dict(fields)
    clone_fields["dbname"] = clone
    admin_fields = dict(fields)
    admin_fields["dbname"] = database
    return make_conninfo(**clone_fields), make_conninfo(**admin_fields)


def _create_clone(admin_dsn: str, clone: str, parent: str) -> None:
    conn = _connect(admin_dsn)
    try:
        conn.execute(
            f"CREATE DATABASE {_quote_identifier(clone)} TEMPLATE {_quote_identifier(parent)}"
        )
    finally:
        conn.close()


def _drop_database(admin_dsn: str, database: str) -> None:
    conn = _connect(admin_dsn)
    try:
        conn.execute(f"DROP DATABASE IF EXISTS {_quote_identifier(database)} WITH (FORCE)")
    finally:
        conn.close()


def build_shared_stock_realization(
    *,
    stock_dsn: str,
    advisor_root: Path,
    snapshot_path: Path,
    candidate_universe_path: Path,
    ground_truth_path: Path,
    selected_by_method: Mapping[str, Sequence[str]],
    system_freeze: Mapping[str, Any],
    research_commit_sha: str,
    statistics_target: int = 100,
    query_count: int = 3,
) -> dict[str, Any]:
    """Create one stock union realization and evaluate method clones.

    The caller must provide a fresh stock database/relation.  The function
    refuses pre-existing extended statistics and never executes ``ANALYZE``
    after a method clone drops unrelated statistics.
    """

    if query_count <= 0 or statistics_target <= 0:
        raise ValueError("query_count and statistics_target must be positive")
    verify_git_sha(advisor_root, FROZEN_ADVISOR_SHA)
    advisor_src = str(advisor_root / "src")
    if advisor_src not in sys.path:
        sys.path.insert(0, advisor_src)
    from extstats_advisor.candidates import load_candidate_universe
    from extstats_advisor.ground_truth import load_ground_truth_set
    from extstats_advisor.snapshot.bundle import load_snapshot
    from extstats_advisor.utility.loss import QErrorLoss

    snapshot = load_snapshot(snapshot_path)
    universe = load_candidate_universe(candidate_universe_path, snapshot)
    ground_truth = load_ground_truth_set(ground_truth_path, snapshot)
    schema = snapshot.schemas[0].relation_name
    candidates_by_id = {candidate.candidate_id: candidate for candidate in universe.candidates}
    union = union_selected_memberships(selected_by_method)
    validate_method_union(selected_by_method, union)
    unknown = set(union) - set(candidates_by_id)
    if unknown:
        raise RQ4ValidationError(f"selected physical candidates are absent: {sorted(unknown)}")
    queries = tuple(query for query in snapshot.workload.queries if query.weight > 0)[:query_count]
    truth_by_id = {truth.query_id: truth.cardinality for truth in ground_truth.truths}
    if any(query.query_id not in truth_by_id for query in queries):
        raise RQ4ValidationError("physical query subset is not covered by bound GroundTruthSet")
    loss = QErrorLoss()
    truth_source = getattr(ground_truth.source, "value", str(ground_truth.source))
    parent_conn = _connect(stock_dsn)
    relation_oid = _relation_oid(parent_conn, schema.schema or "public", schema.name)
    relation_schema = schema.schema or "public"
    created_names: list[str] = []
    clone_names: list[str] = []
    try:
        preexisting = _existing_statistics(parent_conn, relation_oid)
        if preexisting:
            raise RQ4ValidationError(
                "shared stock realization requires a fresh relation with no extended statistics"
            )
        realization_started = time.perf_counter()
        created_names = _create_statistics(
            parent_conn,
            relation_schema,
            schema.name,
            candidates_by_id,
            union,
            statistics_target,
        )
        analyze_started = time.perf_counter()
        parent_conn.execute(
            f"ANALYZE {_quote_identifier(relation_schema)}.{_quote_identifier(schema.name)}"
        )
        analyze_seconds = time.perf_counter() - analyze_started
        parent_ordinary = _ordinary_fingerprint(parent_conn, relation_oid, advisor_root)
        parent_payloads, parent_raw_payloads = _capture_payloads(
            parent_conn, relation_oid, relation_schema, candidates_by_id, union
        )
        database = str(parent_conn.info.dbname)
        stock_server = parent_conn.execute(
            "SELECT current_setting('server_version'), current_setting('server_version_num')"
        ).fetchone()
        parent_conn.close()
        parent_conn = None
        _, admin_dsn = _make_clone_dsn(stock_dsn, "postgres")
        realization_id = semantic_digest(
            {
                "format_version": SHARED_REALIZATION_FORMAT,
                "database": database,
                "relation_oid": relation_oid,
                "union_payloads": parent_payloads,
                "ordinary_stats_fingerprint": parent_ordinary,
                "statistics_target": statistics_target,
                "analyze_count": 1,
            }
        )
        methods: dict[str, Any] = {}
        for method, selected_values in selected_by_method.items():
            clone_dsn, _ = _make_clone_dsn(stock_dsn, "postgres")
            from psycopg.conninfo import conninfo_to_dict

            clone_db = str(conninfo_to_dict(clone_dsn)["dbname"])
            _create_clone(admin_dsn, clone_db, database)
            clone_names.append(clone_db)
            conn = _connect(clone_dsn)
            try:
                drop_names = [
                    _candidate_name(candidate_id, candidates_by_id[candidate_id].kind)
                    for candidate_id in union
                    if candidate_id not in set(selected_values)
                ]
                _drop_statistics(conn, relation_schema, drop_names)
                remaining, remaining_raw = _capture_payloads(
                    conn,
                    _relation_oid(conn, relation_schema, schema.name),
                    relation_schema,
                    candidates_by_id,
                    tuple(selected_values),
                )
                expected_payloads = {
                    candidate_id: parent_payloads[candidate_id] for candidate_id in selected_values
                }
                payloads_equal = remaining == expected_payloads
                bytes_equal = all(
                    remaining_raw[candidate_id] == parent_raw_payloads[candidate_id]
                    for candidate_id in selected_values
                )
                ordinary = _ordinary_fingerprint(
                    conn, _relation_oid(conn, relation_schema, schema.name), advisor_root
                )
                eval_started = time.perf_counter()
                explain = _explain_rows(conn, queries)
                eval_seconds = time.perf_counter() - eval_started
                metrics = _metrics(explain, loss, truth_by_id)
                methods[method] = {
                    "selected_membership": list(selected_values),
                    "deployment_order": list(selected_values),
                    "physical_evaluation_source": "stock-postgresql-explain",
                    "physical_statistics": list(remaining.values()),
                    "shared_analyze_realization_id": realization_id,
                    "parent_realization_digest": semantic_digest(parent_payloads),
                    "payloads_exactly_preserved_after_drop": payloads_equal and bytes_equal,
                    "payload_bytes_equal_after_drop": bytes_equal,
                    "ordinary_statistics_fingerprint": ordinary,
                    "ordinary_statistics_equal_to_parent": ordinary == parent_ordinary,
                    "analyze_after_drop": False,
                    "plan_rows": metrics["per_query"],
                    "metrics": {key: value for key, value in metrics.items() if key != "per_query"},
                    "costs": {
                        "design_search": "not measured by stock physical executor",
                        "native_realization": {
                            "shared_union_create_and_alter_seconds": time.perf_counter()
                            - realization_started,
                            "shared_analyze_seconds": analyze_seconds,
                            "analyze_count": 1,
                            "analyze_sampling_stochastic": True,
                            "setseed_controls_analyze": False,
                        },
                        "evaluation": {
                            "postgresql_planner_query_calls": len(explain),
                            "explain_wall_clock_seconds": eval_seconds,
                        },
                    },
                }
            finally:
                conn.close()
        artifact: dict[str, Any] = {
            "format_version": PHYSICAL_FORMAT,
            "experiment_id": "rq4-stock-physical-evaluation-smoke-v1",
            "formal_confirmatory_experiment": False,
            "status": "shared-realization-smoke",
            "research_commit_sha": research_commit_sha,
            "system_freeze": dict(system_freeze),
            "system_freeze_semantic_digest": semantic_digest(system_freeze),
            "snapshot_semantic_digest": snapshot.semantic_digest,
            "candidate_universe_semantic_digest": universe.semantic_digest,
            "ground_truth_semantic_digest": ground_truth.computed_semantic_digest,
            "dataset": {
                "dataset_id": "real-artifact-fixture",
                "relation": f"{relation_schema}.{schema.name}",
            },
            "workload": {
                "workload_id": snapshot.workload.workload_id,
                "query_ids": [query.query_id for query in queries],
                "query_count": len(queries),
            },
            "truth_binding": {
                "source_kind": truth_source,
                "cardinality_vector_digest": semantic_digest(
                    [(query.query_id, truth_by_id[query.query_id]) for query in queries]
                ),
                "qerror_contract": loss.contract_version,
            },
            "shared_realization": {
                "format_version": SHARED_REALIZATION_FORMAT,
                "realization_id": realization_id,
                "union_candidate_ids": list(union),
                "preexisting_extended_statistics": preexisting,
                "fresh_state_verified": True,
                "stock_server": {
                    "version": str(stock_server[0]),
                    "version_num": int(stock_server[1]),
                },
                "ordinary_statistics_fingerprint": parent_ordinary,
                "physical_statistics": list(parent_payloads.values()),
                "analyze_count": 1,
                "analyze_sampling_stochastic": True,
                "setseed_is_not_analyze_reproducibility_proof": True,
            },
            "methods": methods,
            "cost_accounting_rule": "design/search, native realization, and physical evaluation are separate",
        }
        artifact["semantic_digest"] = semantic_digest(_runtime_free(artifact))
        return artifact
    finally:
        if parent_conn is not None:
            _drop_statistics(parent_conn, relation_schema, created_names)
            parent_conn.close()
        elif created_names:
            cleanup_conn = _connect(stock_dsn)
            try:
                _drop_statistics(cleanup_conn, relation_schema, created_names)
            finally:
                cleanup_conn.close()
        if clone_names:
            _, admin_dsn = _make_clone_dsn(stock_dsn, "postgres")
            for clone_name in clone_names:
                _drop_database(admin_dsn, clone_name)


def validate_shared_stock_realization(path: Path) -> dict[str, Any]:
    artifact = read_json(path)
    if artifact.get("format_version") != PHYSICAL_FORMAT:
        raise RQ4ValidationError("unsupported stock physical artifact format")
    expected = semantic_digest(
        _runtime_free({k: v for k, v in artifact.items() if k != "semantic_digest"})
    )
    if artifact.get("semantic_digest") != expected:
        raise RQ4ValidationError("stock physical artifact semantic digest mismatch")
    shared = artifact.get("shared_realization", {})
    if shared.get("analyze_count") != 1 or not shared.get("fresh_state_verified"):
        raise RQ4ValidationError("shared stock realization freshness/analyze gate failed")
    if not shared.get("setseed_is_not_analyze_reproducibility_proof"):
        raise RQ4ValidationError("stock artifact does not state the ANALYZE randomness limitation")
    methods = artifact.get("methods", {})
    if not methods:
        raise RQ4ValidationError("stock physical artifact has no method results")
    realization_id = shared.get("realization_id")
    for method, result in methods.items():
        if result.get("shared_analyze_realization_id") != realization_id:
            raise RQ4ValidationError(f"{method} does not bind to the shared realization")
        if not result.get("payloads_exactly_preserved_after_drop"):
            raise RQ4ValidationError(f"{method} payload changed after no-ANALYZE drop")
        if result.get("analyze_after_drop") is not False:
            raise RQ4ValidationError(f"{method} ran ANALYZE after dropping unrelated statistics")
        if result.get("physical_evaluation_source") != "stock-postgresql-explain":
            raise RQ4ValidationError(f"{method} is missing stock physical evaluation evidence")
        if "sandbox_objective" in result.get("metrics", {}):
            raise RQ4ValidationError(f"{method} substituted a sandbox objective for stock metrics")
        if result.get("metrics", {}).get("query_count") != artifact["workload"]["query_count"]:
            raise RQ4ValidationError(f"{method} physical query count is incomplete")
        costs = result.get("costs", {})
        if (
            "design_search" not in costs
            or "native_realization" not in costs
            or "evaluation" not in costs
        ):
            raise RQ4ValidationError(f"{method} cost stages are not separated")
    return {
        "status": "valid",
        "format_version": PHYSICAL_FORMAT,
        "semantic_digest": expected,
        "method_count": len(methods),
        "formal_confirmatory_experiment": False,
    }


def write_shared_stock_realization(output: Path, **kwargs: Any) -> dict[str, Any]:
    artifact = build_shared_stock_realization(**kwargs)
    write_json(output, artifact)
    return artifact


__all__ = [
    "PHYSICAL_FORMAT",
    "SHARED_REALIZATION_FORMAT",
    "build_shared_stock_realization",
    "union_selected_memberships",
    "validate_method_union",
    "validate_shared_stock_realization",
    "write_shared_stock_realization",
]
