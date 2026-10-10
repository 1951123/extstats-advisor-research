"""Research-owned PostgreSQL adapters for the RQ5 cost harness.

The module is deliberately inert at import time.  A live connection is only
opened after an adapter has been constructed with explicit DSNs, binary
identity records, source artifacts, and ``enable_live=True``.  The adapter
classes implement the narrow protocols in :mod:`rq5_whatif_cost`; they do
not add timing hooks to Advisor or PostgreSQL.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .pins import verify_git_sha
from .provenance import read_json, write_json
from .rq5_whatif_cost import (
    ADVISOR_SHA_V1,
    PATCHED_POSTGRES_SHA,
    STOCK_POSTGRES_SHA,
    WhatIfCostError,
    _digest,
    _load_workload,
)

_DATABASE_NAME = re.compile(r"^rq5wc_[a-z0-9][a-z0-9_]{0,54}$")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,80}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_EXPECTED_SERVER_VERSION_NUM = 160014
_EXPECTED_SERVER_VERSION_PREFIX = "16.14"


class LiveAdapterError(WhatIfCostError):
    """Raised when a live adapter cannot prove its execution boundary safe."""


@dataclass(frozen=True, slots=True)
class LiveAdapterConfig:
    """Explicit inputs shared by the stock and patched adapters.

    DSNs and passwords are runtime-only values and are never serialized by
    this module.  ``stock_dsn`` points at an experiment-owned template/source
    database; it is never dropped.  ``stock_admin_dsn`` is used only for
    creating and dropping names that this adapter has registered itself.
    """

    stock_dsn: str
    stock_admin_dsn: str
    patched_dsn: str
    advisor_root: Path
    snapshot_path: Path
    candidate_universe_path: Path
    workload_path: Path
    stock_identity_path: Path
    patched_identity_path: Path
    output_dir: Path
    run_id: str
    enable_live: bool = False
    allow_fixture: bool = False
    expected_advisor_sha: str = ADVISOR_SHA_V1
    expected_stock_sha: str = STOCK_POSTGRES_SHA
    expected_patched_sha: str = PATCHED_POSTGRES_SHA

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or _RUN_ID.fullmatch(self.run_id) is None:
            raise LiveAdapterError("run_id contains unsafe characters")
        if self.expected_advisor_sha != ADVISOR_SHA_V1:
            raise LiveAdapterError("RQ5 Forest10 adapter requires the historical Advisor pin")
        if self.expected_stock_sha != STOCK_POSTGRES_SHA:
            raise LiveAdapterError("RQ5 adapter stock PostgreSQL pin drifted")
        if self.expected_patched_sha != PATCHED_POSTGRES_SHA:
            raise LiveAdapterError("RQ5 adapter patched PostgreSQL pin drifted")

    def require_live(self) -> None:
        if not self.enable_live:
            raise LiveAdapterError(
                "live PostgreSQL adapters require explicit enable_live=True; "
                "offline commands never open a connection"
            )
        for label, dsn in (
            ("stock DSN", self.stock_dsn),
            ("stock admin DSN", self.stock_admin_dsn),
            ("patched DSN", self.patched_dsn),
        ):
            _validate_explicit_dsn(dsn, label)
        for label, path in (
            ("Advisor root", self.advisor_root),
            ("snapshot", self.snapshot_path),
            ("candidate universe", self.candidate_universe_path),
            ("workload", self.workload_path),
            ("stock binary identity", self.stock_identity_path),
            ("patched binary identity", self.patched_identity_path),
        ):
            if not Path(path).exists():
                raise LiveAdapterError(f"{label} does not exist: {path}")


def _validate_explicit_dsn(dsn: str, label: str) -> dict[str, str]:
    if not isinstance(dsn, str) or not dsn.strip():
        raise LiveAdapterError(f"{label} is required")
    try:
        from psycopg.conninfo import conninfo_to_dict

        fields = conninfo_to_dict(dsn)
    except Exception as exc:  # pragma: no cover - psycopg owns parser details
        raise LiveAdapterError(f"{label} is not a valid psycopg connection string") from exc
    if not fields.get("host") or not fields.get("port") or not fields.get("dbname"):
        raise LiveAdapterError(f"{label} must explicitly provide host, port, and dbname")
    try:
        port = int(fields["port"])
    except (TypeError, ValueError) as exc:
        raise LiveAdapterError(f"{label} has an invalid port") from exc
    if not 1 <= port <= 65535:
        raise LiveAdapterError(f"{label} has an invalid port")
    return {str(key): str(value) for key, value in fields.items()}


def _safe_database_name(name: str) -> str:
    if _DATABASE_NAME.fullmatch(name) is None:
        raise LiveAdapterError(f"unsafe experiment database name: {name!r}")
    return name


def validate_experiment_database_name(name: str) -> str:
    """Validate a disposable database name without contacting PostgreSQL."""

    return _safe_database_name(name)


def _connect(dsn: str, application_name: str) -> Any:
    import psycopg

    return psycopg.connect(dsn, autocommit=True, application_name=application_name)


def _make_conninfo(dsn: str, database: str) -> str:
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    fields = conninfo_to_dict(dsn)
    fields["dbname"] = database
    return make_conninfo(**fields)


def _quote_identifier(value: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise LiveAdapterError("invalid PostgreSQL identifier")
    return '"' + value.replace('"', '""') + '"'


def _advisor_modules(config: LiveAdapterConfig) -> None:
    advisor_root = config.advisor_root.resolve()
    verify_git_sha(advisor_root, config.expected_advisor_sha)
    advisor_src = str(advisor_root / "src")
    if advisor_src not in sys.path:
        sys.path.insert(0, advisor_src)


def _load_identity(path: Path, expected_sha: str, label: str) -> dict[str, Any]:
    value = read_json(path)
    if not isinstance(value, Mapping):
        raise LiveAdapterError(f"{label} identity is not an object")
    if value.get("source_commit_sha") != expected_sha:
        raise LiveAdapterError(
            f"{label} source identity is {value.get('source_commit_sha')!r}, expected {expected_sha}"
        )
    if value.get("source_dirty") is not False:
        raise LiveAdapterError(f"{label} source identity is not clean")
    return dict(value)


def _verify_server(
    connection: Any,
    dsn: str,
    identity: Mapping[str, Any],
    expected_version_num: int = _EXPECTED_SERVER_VERSION_NUM,
) -> dict[str, Any]:
    fields = _validate_explicit_dsn(dsn, "live DSN")
    version, version_num = connection.execute(
        "SELECT current_setting('server_version'), current_setting('server_version_num')"
    ).fetchone()
    version = str(version)
    version_num = int(version_num)
    if version_num != expected_version_num or not version.startswith(
        _EXPECTED_SERVER_VERSION_PREFIX
    ):
        raise LiveAdapterError(f"PostgreSQL 16.14 is required, observed {version!r}")
    database, role = connection.execute("SELECT current_database(), current_user").fetchone()
    expected_database = str(fields["dbname"])
    if str(database) != expected_database:
        raise LiveAdapterError(
            f"connection database mismatch: {database!r} != {expected_database!r}"
        )
    owner = connection.execute(
        "SELECT pg_catalog.pg_get_userbyid(datdba) "
        "FROM pg_catalog.pg_database WHERE datname = current_database()"
    ).fetchone()[0]
    if str(owner) != str(role):
        raise LiveAdapterError(
            f"database {database!r} is owned by {owner!r}, not current role {role!r}"
        )
    return {
        "server_version": version,
        "server_version_num": version_num,
        "database": str(database),
        "role": str(role),
        "source_commit_sha": identity["source_commit_sha"],
    }


def _relation_oid(connection: Any, schema: str, relation: str) -> int:
    row = connection.execute("SELECT to_regclass(%s)::oid", (f"{schema}.{relation}",)).fetchone()
    if row is None or row[0] is None:
        raise LiveAdapterError(f"fixture relation is missing: {schema}.{relation}")
    return int(row[0])


def _ordinary_fingerprint(connection: Any, relation_oid: int) -> str:
    from extstats_advisor.dbms.postgres.ordinary_stats import ordinary_stats_fingerprint

    return str(ordinary_stats_fingerprint(connection, relation_oid))


def _candidate_name(candidate_id: str, kind: str) -> str:
    from extstats_advisor.dbms.postgres.recommendation import postgres_statistics_object_name

    return str(postgres_statistics_object_name(candidate_id, kind))


def build_statistics_ddl(
    candidates: Mapping[str, Mapping[str, Any]],
    relation_schema: str,
    relation_name: str,
    candidate_ids: Sequence[str],
    statistics_target: int = 100,
) -> tuple[tuple[str, str, str], ...]:
    """Build deterministic CREATE/ALTER statements without opening PostgreSQL."""

    if not 1 <= statistics_target <= 10_000:
        raise LiveAdapterError("statistics target must be between 1 and 10000")
    relation = f"{_quote_identifier(relation_schema)}.{_quote_identifier(relation_name)}"
    statements: list[tuple[str, str, str]] = []
    for candidate_id in candidate_ids:
        candidate = candidates.get(candidate_id)
        if candidate is None:
            raise LiveAdapterError(f"unknown candidate in DDL request: {candidate_id}")
        kind = "mcv" if candidate["kind"] == "postgresql.mcv" else "dependencies"
        name = _candidate_name(candidate_id, str(candidate["kind"]))
        columns = ", ".join(_quote_identifier(str(item)) for item in candidate["column_names"])
        create = (
            f"CREATE STATISTICS {_quote_identifier(relation_schema)}.{_quote_identifier(name)} "
            f"({kind}) ON {columns} FROM {relation}"
        )
        alter = (
            f"ALTER STATISTICS {_quote_identifier(relation_schema)}.{_quote_identifier(name)} "
            f"SET STATISTICS {statistics_target}"
        )
        statements.append((candidate_id, create, alter))
    return tuple(statements)


def _candidate_mapping(manifest: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    definitions = manifest.get("candidate_universe", {}).get("definitions", [])
    if not isinstance(definitions, list) or not definitions:
        raise LiveAdapterError("manifest candidate universe is empty")
    result: dict[str, Mapping[str, Any]] = {}
    for candidate in definitions:
        candidate_id = candidate.get("candidate_id")
        if not isinstance(candidate_id, str) or candidate_id in result:
            raise LiveAdapterError("manifest candidate IDs are invalid or duplicated")
        if candidate.get("kind") not in {"postgresql.mcv", "postgresql.dependencies"}:
            raise LiveAdapterError(f"unsupported candidate kind: {candidate.get('kind')}")
        columns = candidate.get("column_names")
        ordinals = candidate.get("column_ordinals")
        if not isinstance(columns, list) or not isinstance(ordinals, list) or len(columns) != 2:
            raise LiveAdapterError(f"candidate definition is incomplete: {candidate_id}")
        if len(ordinals) != len(columns) or tuple(sorted(ordinals)) != tuple(ordinals):
            raise LiveAdapterError(f"candidate ordinals are invalid: {candidate_id}")
        result[candidate_id] = candidate
    return result


def _validate_candidate_sources(
    manifest: Mapping[str, Any], snapshot: Any, universe: Any
) -> tuple[dict[str, Mapping[str, Any]], Any]:
    expected = _candidate_mapping(manifest)
    if getattr(snapshot, "semantic_digest", None) is None:
        raise LiveAdapterError("snapshot must be sealed")
    actual = {candidate.candidate_id: candidate for candidate in universe.candidates}
    if set(actual) != set(expected):
        raise LiveAdapterError("candidate universe does not match the frozen manifest IDs")
    for candidate_id, frozen in expected.items():
        candidate = actual[candidate_id]
        for field in ("relation_id", "kind", "column_ordinals", "column_names"):
            observed = getattr(candidate, field)
            if field in {"column_ordinals", "column_names"}:
                observed = list(observed)
            if observed != frozen.get(field):
                raise LiveAdapterError(f"candidate mapping mismatch for {candidate_id}: {field}")
    if len(snapshot.schemas) != 1:
        raise LiveAdapterError("RQ5 live adapter requires exactly one snapshot relation")
    relation = snapshot.schemas[0].relation_name
    return expected, relation


def _query_source(
    path: Path, manifest: Mapping[str, Any], *, allow_fixture: bool = False
) -> dict[str, str]:
    if allow_fixture:
        value = read_json(path)
        queries = value.get("queries", [])
        if not isinstance(queries, list) or not queries:
            raise LiveAdapterError("fixture workload has no queries")
        source = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    else:
        source, _ = _load_workload(path.parent, path)
        value = read_json(path)
        queries = value["queries"]
    expected_source = manifest.get("workload", {})
    if source["sha256"] != expected_source.get("sha256"):
        raise LiveAdapterError("workload source digest does not match the manifest")
    by_id = {query["query_id"]: str(query["sql"]) for query in queries}
    selected: dict[str, str] = {}
    for query in manifest.get("query_subset", []):
        query_id = str(query["query_id"])
        sql = by_id.get(query_id)
        if sql is None:
            raise LiveAdapterError(f"manifest query is missing from workload source: {query_id}")
        digest = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        if digest != query.get("sql_sha256"):
            raise LiveAdapterError(f"SQL digest mismatch for query {query_id}")
        selected[query_id] = sql
    return selected


def _explain_rows(document: Any) -> int:
    if isinstance(document, str):
        document = json.loads(document)
    if not isinstance(document, list) or not document or not isinstance(document[0], Mapping):
        raise LiveAdapterError("EXPLAIN JSON has an invalid top-level shape")
    plan = document[0].get("Plan")
    if not isinstance(plan, Mapping) or "Plan Rows" not in plan:
        raise LiveAdapterError("EXPLAIN JSON has no root Plan Rows")
    rows = int(plan["Plan Rows"])
    if rows < 0:
        raise LiveAdapterError("EXPLAIN Plan Rows is negative")
    return rows


def _exclusive_json(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists():
        raise LiveAdapterError(f"adapter audit already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, dict(value))


class _BaseAdapter:
    def __init__(self, manifest: Mapping[str, Any], config: LiveAdapterConfig, arm: str) -> None:
        self.manifest = manifest
        self.config = config
        self.arm = arm
        self._snapshot: Any = None
        self._universe: Any = None
        self._candidates: dict[str, Mapping[str, Any]] = {}
        self._relation: Any = None
        self._queries: dict[str, str] = {}
        self._statistics_target = int(manifest.get("dataset", {}).get("statistics_target", 100))
        self._audit_root = config.output_dir / "adapter-audit" / arm / config.run_id

    def _require_live(self) -> None:
        self.config.require_live()

    def _load_sources(self) -> None:
        self._require_live()
        _advisor_modules(self.config)
        from extstats_advisor.candidates import load_candidate_universe
        from extstats_advisor.snapshot.bundle import load_snapshot

        self._snapshot = load_snapshot(self.config.snapshot_path)
        self._universe = load_candidate_universe(
            self.config.candidate_universe_path, self._snapshot
        )
        self._candidates, self._relation = _validate_candidate_sources(
            self.manifest, self._snapshot, self._universe
        )
        self._queries = _query_source(
            self.config.workload_path, self.manifest, allow_fixture=self.config.allow_fixture
        )

    def _configuration(self, configuration: Mapping[str, Any]) -> tuple[str, ...]:
        ids = tuple(str(item) for item in configuration.get("candidate_ids", []))
        if list(ids) != list(configuration.get("declared_order", [])):
            raise LiveAdapterError("configuration order differs from declared order")
        unknown = set(ids) - set(self._candidates)
        if unknown:
            raise LiveAdapterError(f"configuration contains unknown candidates: {sorted(unknown)}")
        if len(ids) != int(configuration.get("configuration_size", -1)):
            raise LiveAdapterError("configuration size is inconsistent")
        return ids

    def _audit_path(self, kind: str, configuration: Mapping[str, Any]) -> Path:
        ordinal = int(configuration["ordinal"])
        return self._audit_root / f"config-{ordinal:03d}-{kind}.json"

    def _query_ids(self, query_ids: Sequence[str]) -> tuple[str, ...]:
        expected = tuple(str(item["query_id"]) for item in self.manifest["query_subset"])
        actual = tuple(str(item) for item in query_ids)
        if actual != expected:
            raise LiveAdapterError("adapter query sequence does not match manifest order")
        return actual


class StockPhysicalCostAdapter(_BaseAdapter):
    """Repeated physical evaluation against disposable stock clones."""

    def __init__(self, manifest: Mapping[str, Any], config: LiveAdapterConfig) -> None:
        super().__init__(manifest, config, "physical")
        self._source_database: str | None = None
        self._admin_database: str | None = None
        self._clone_database: str | None = None
        self._clone_connection: Any = None
        self._owned_clones: set[str] = set()
        self._created_names: list[str] = []
        self._identity: dict[str, Any] | None = None

    def prepare_run(self) -> int:
        self._load_sources()
        self._identity = _load_identity(
            self.config.stock_identity_path, self.config.expected_stock_sha, "stock PostgreSQL"
        )
        stock_fields = _validate_explicit_dsn(self.config.stock_dsn, "stock DSN")
        admin_fields = _validate_explicit_dsn(self.config.stock_admin_dsn, "stock admin DSN")
        self._source_database = str(stock_fields["dbname"])
        self._admin_database = str(admin_fields["dbname"])
        if self._source_database.startswith("rq5wc_"):
            raise LiveAdapterError("stock source database cannot be experiment-owned")
        if self._admin_database.startswith("rq5wc_"):
            raise LiveAdapterError("stock admin database cannot be experiment-owned")
        connection = _connect(self.config.stock_dsn, "extstats-research-rq5-cost-physical")
        try:
            server = _verify_server(connection, self.config.stock_dsn, self._identity)
            relation_oid = _relation_oid(connection, self._relation.schema, self._relation.name)
            existing = connection.execute(
                "SELECT n.nspname, e.stxname FROM pg_catalog.pg_statistic_ext e "
                "JOIN pg_catalog.pg_namespace n ON n.oid=e.stxnamespace "
                "WHERE e.stxrelid=%s AND e.stxname LIKE 'extstats_adv_%' ORDER BY e.stxname",
                (relation_oid,),
            ).fetchall()
            if existing:
                raise LiveAdapterError(
                    "stock source contains experiment-named statistics; refusing to clone"
                )
            self._exclusive_preflight_audit(
                {
                    "server": server,
                    "relation_oid": relation_oid,
                    "source_database": self._source_database,
                }
            )
        finally:
            connection.close()
        return 1

    def _exclusive_preflight_audit(self, value: Mapping[str, Any]) -> None:
        _exclusive_json(self._audit_root / "preflight.json", value)

    def _clone_name(self, configuration: Mapping[str, Any]) -> str:
        digest = hashlib.sha256(self.config.run_id.encode("utf-8")).hexdigest()[:12]
        return _safe_database_name(f"rq5wc_{digest}_c{int(configuration['ordinal']):02d}")

    def create_clone(self, configuration: Mapping[str, Any]) -> int:
        self._require_live()
        if self._source_database is None or self._admin_database is None:
            raise LiveAdapterError("prepare_run must precede clone creation")
        if self._clone_database is not None:
            raise LiveAdapterError("a physical clone is already open")
        clone = self._clone_name(configuration)
        admin_dsn = self.config.stock_admin_dsn
        admin = _connect(admin_dsn, "extstats-research-rq5-cost-admin")
        try:
            _verify_server(
                admin,
                admin_dsn,
                self._identity or {},
            )
            exists = admin.execute(
                "SELECT 1 FROM pg_catalog.pg_database WHERE datname=%s", (clone,)
            ).fetchone()
            if exists:
                raise LiveAdapterError(f"experiment clone already exists: {clone}")
            admin.execute(
                f"CREATE DATABASE {_quote_identifier(clone)} TEMPLATE "
                f"{_quote_identifier(self._source_database)}"
            )
        finally:
            admin.close()
        self._owned_clones.add(clone)
        self._clone_database = clone
        self._clone_connection = _connect(
            _make_conninfo(self.config.stock_dsn, clone),
            "extstats-research-rq5-cost-clone",
        )
        _verify_server(
            self._clone_connection,
            _make_conninfo(self.config.stock_dsn, clone),
            self._identity or {},
        )
        _relation_oid(self._clone_connection, self._relation.schema, self._relation.name)
        return 1

    def create_statistics(self, configuration: Mapping[str, Any]) -> int:
        ids = self._configuration(configuration)
        if self._clone_connection is None:
            raise LiveAdapterError("create_clone must precede statistics creation")
        self._created_names = []
        for candidate_id, create, alter in build_statistics_ddl(
            self._candidates,
            self._relation.schema,
            self._relation.name,
            ids,
            self._statistics_target,
        ):
            name = _candidate_name(candidate_id, str(self._candidates[candidate_id]["kind"]))
            self._clone_connection.execute(create)
            self._clone_connection.execute(alter)
            self._created_names.append(name)
        return len(ids)

    def _capture_state(self, configuration: Mapping[str, Any]) -> dict[str, Any]:
        if self._clone_connection is None:
            raise LiveAdapterError("physical clone is not connected")
        ids = self._configuration(configuration)
        relation_oid = _relation_oid(
            self._clone_connection, self._relation.schema, self._relation.name
        )
        records = []
        for candidate_id in ids:
            candidate = self._candidates[candidate_id]
            kind = "m" if candidate["kind"] == "postgresql.mcv" else "f"
            name = _candidate_name(candidate_id, str(candidate["kind"]))
            expression = (
                "pg_catalog.pg_mcv_list_send(d.stxdmcv)"
                if kind == "m"
                else "pg_catalog.pg_dependencies_send(d.stxddependencies)"
            )
            row = self._clone_connection.execute(
                "SELECT e.oid::bigint, e.stxkind::text, e.stxkeys::text, "
                "e.stxstattarget, " + expression + " FROM pg_catalog.pg_statistic_ext e "
                "LEFT JOIN pg_catalog.pg_statistic_ext_data d ON d.stxoid=e.oid "
                "JOIN pg_catalog.pg_namespace n ON n.oid=e.stxnamespace "
                "WHERE e.stxrelid=%s AND n.nspname=%s AND e.stxname=%s",
                (relation_oid, self._relation.schema, name),
            ).fetchone()
            if row is None:
                raise LiveAdapterError(
                    f"statistics object is missing after ANALYZE: {candidate_id}"
                )
            expected_keys = " ".join(str(item) for item in candidate["column_ordinals"])
            if str(row[2]).strip() != expected_keys or kind not in str(row[1]):
                raise LiveAdapterError(f"statistics definition mismatch: {candidate_id}")
            payload = None if row[4] is None else bytes(row[4])
            records.append(
                {
                    "candidate_id": candidate_id,
                    "oid": int(row[0]),
                    "kind": str(candidate["kind"]),
                    "statistics_kind": str(row[1]),
                    "column_ordinals": list(candidate["column_ordinals"]),
                    "statistics_target": int(row[3]),
                    "payload_state": "native-null" if payload is None else "present",
                    "payload_size": None if payload is None else len(payload),
                    "payload_sha256": None
                    if payload is None
                    else hashlib.sha256(payload).hexdigest(),
                }
            )
        return {
            "relation_oid": relation_oid,
            "declared_order": list(ids),
            "observed_oid_order": [item["oid"] for item in records],
            "oid_order_non_decreasing": all(
                left < right
                for left, right in zip(
                    (item["oid"] for item in records),
                    (item["oid"] for item in records[1:]),
                    strict=False,
                )
            ),
            "statistics": records,
            "ordinary_statistics_fingerprint": _ordinary_fingerprint(
                self._clone_connection, relation_oid
            ),
        }

    def analyze(self, configuration: Mapping[str, Any]) -> int:
        if self._clone_connection is None:
            raise LiveAdapterError("physical clone is not connected")
        relation = (
            f"{_quote_identifier(self._relation.schema)}.{_quote_identifier(self._relation.name)}"
        )
        self._clone_connection.execute(f"ANALYZE {relation}")
        state = self._capture_state(configuration)
        _exclusive_json(self._audit_path("physical-state", configuration), state)
        return 1

    def explain(self, configuration: Mapping[str, Any], query_ids: Sequence[str]) -> int:
        if self._clone_connection is None:
            raise LiveAdapterError("physical clone is not connected")
        ids = self._query_ids(query_ids)
        rows: list[dict[str, Any]] = []
        attempted = successful = 0
        for query_id in ids:
            attempted += 1
            sql = self._queries[query_id].rstrip().rstrip(";").rstrip()
            try:
                document = self._clone_connection.execute(
                    f"EXPLAIN (FORMAT JSON) {sql}"
                ).fetchone()[0]
                plan_rows = _explain_rows(document[0] if isinstance(document, list) else document)
                rows.append({"query_id": query_id, "plan_rows": plan_rows})
                successful += 1
            except Exception as exc:
                _exclusive_json(
                    self._audit_path("physical-explain-failure", configuration),
                    {
                        "attempted": attempted,
                        "successful": successful,
                        "failed": 1,
                        "query_id": query_id,
                        "error": {"class": type(exc).__name__, "message": str(exc)},
                    },
                )
                raise
        _exclusive_json(
            self._audit_path("physical-explain", configuration),
            {"attempted": attempted, "successful": successful, "failed": 0, "rows": rows},
        )
        return successful

    def drop_statistics(self, configuration: Mapping[str, Any]) -> int:
        ids = self._configuration(configuration)
        if self._clone_connection is None:
            raise LiveAdapterError("physical clone is not connected")
        if len(self._created_names) != len(ids):
            raise LiveAdapterError("created statistics inventory does not match configuration")
        for name in self._created_names:
            self._clone_connection.execute(
                f"DROP STATISTICS {_quote_identifier(self._relation.schema)}.{_quote_identifier(name)}"
            )
        return len(ids)

    def destroy_clone(self, configuration: Mapping[str, Any]) -> int:
        if self._clone_database is None:
            raise LiveAdapterError("no physical clone is open")
        clone = self._clone_database
        if self._clone_connection is not None:
            self._clone_connection.close()
            self._clone_connection = None
        admin = _connect(self.config.stock_admin_dsn, "extstats-research-rq5-cost-admin")
        try:
            owner = admin.execute(
                "SELECT pg_catalog.pg_get_userbyid(datdba) FROM pg_catalog.pg_database "
                "WHERE datname=%s",
                (clone,),
            ).fetchone()
            if owner is None:
                raise LiveAdapterError(f"owned clone disappeared before cleanup: {clone}")
            current_user = admin.execute("SELECT current_user").fetchone()[0]
            if str(owner[0]) != str(current_user):
                raise LiveAdapterError(f"clone is not owned by current role: {clone}")
            admin.execute(f"DROP DATABASE {_quote_identifier(clone)} WITH (FORCE)")
        finally:
            admin.close()
        self._owned_clones.discard(clone)
        self._clone_database = None
        self._created_names = []
        return 1

    def cleanup(self) -> int:
        errors: list[BaseException] = []
        if self._clone_connection is not None:
            try:
                self._clone_connection.close()
            except BaseException as exc:  # noqa: BLE001 - preserve cleanup failures
                errors.append(exc)
            self._clone_connection = None
        for clone in sorted(self._owned_clones):
            try:
                admin = _connect(self.config.stock_admin_dsn, "extstats-research-rq5-cost-admin")
                try:
                    owner = admin.execute(
                        "SELECT pg_catalog.pg_get_userbyid(datdba) FROM pg_catalog.pg_database "
                        "WHERE datname=%s",
                        (clone,),
                    ).fetchone()
                    current_user = admin.execute("SELECT current_user").fetchone()[0]
                    if owner is None or str(owner[0]) != str(current_user):
                        raise LiveAdapterError(f"refusing cleanup of unowned clone: {clone}")
                    admin.execute(f"DROP DATABASE {_quote_identifier(clone)} WITH (FORCE)")
                finally:
                    admin.close()
                self._owned_clones.discard(clone)
            except BaseException as exc:  # noqa: BLE001 - preserve independent cleanup failures
                errors.append(exc)
        if errors:
            raise LiveAdapterError("one or more physical clone cleanups failed") from errors[0]
        return 1


class CataloglessWhatIfCostAdapter(_BaseAdapter):
    """Catalogless adapter using Advisor's native repository and planner session."""

    def __init__(self, manifest: Mapping[str, Any], config: LiveAdapterConfig) -> None:
        super().__init__(manifest, config, "catalogless")
        self._repository: Any = None
        self._materialization: Any = None
        self._session: Any = None
        self._registered_oids: dict[str, int] = {}

    def prepare_sample(self) -> int:
        self._load_sources()
        _load_identity(
            self.config.patched_identity_path,
            self.config.expected_patched_sha,
            "patched PostgreSQL",
        )
        _validate_explicit_dsn(self.config.patched_dsn, "patched DSN")
        return 1

    def materialize_payloads(self) -> int:
        self._require_live()
        if self._snapshot is None or self._universe is None:
            raise LiveAdapterError("prepare_sample must precede materialization")
        _advisor_modules(self.config)
        from extstats_advisor.dbms.postgres import materialize_native_stats

        self._materialization = materialize_native_stats(
            self.config.patched_dsn,
            self._snapshot,
            self._universe,
            statistics_target=self._statistics_target,
        )
        if (
            self._materialization.reference_source_commit != self.config.expected_patched_sha
            or self._materialization.server_version_num != _EXPECTED_SERVER_VERSION_NUM
        ):
            raise LiveAdapterError("native materialization backend identity does not match the pin")
        expected = tuple(self._universe.candidates)
        observed = tuple(self._materialization.candidates)
        if [item.candidate_id for item in observed] != [item.candidate_id for item in expected]:
            raise LiveAdapterError("native materialization candidate order differs from universe")
        repository_path = self.config.output_dir / "native-stats-repository"
        if repository_path.exists():
            raise LiveAdapterError(f"native repository output already exists: {repository_path}")
        from extstats_advisor.native_stats import write_native_stats_repository

        write_native_stats_repository(self._materialization, repository_path)
        self._repository_path = repository_path
        _exclusive_json(
            self._audit_root / "materialization.json",
            {
                "snapshot_semantic_digest": self._snapshot.semantic_digest,
                "candidate_universe_semantic_digest": self._universe.semantic_digest,
                "native_repository_semantic_digest": read_json(repository_path / "manifest.json")[
                    "semantic_digest"
                ],
                "analyze_count": self._materialization.analyze_count,
                "ordinary_statistics_fingerprint": self._materialization.ordinary_stats_fingerprint,
                "payload_states": {
                    item.candidate_id: item.state for item in self._materialization.candidates
                },
            },
        )
        return len(expected)

    def register_repository(self) -> int:
        self._require_live()
        if not hasattr(self, "_repository_path"):
            raise LiveAdapterError("materialize_payloads must precede repository registration")
        _advisor_modules(self.config)
        from extstats_advisor.dbms.postgres.planner import PostgresPlannerSession
        from extstats_advisor.native_stats import load_native_stats_repository
        from extstats_advisor.native_stats.repository import (
            validate_native_stats_repository_compatibility,
        )

        self._repository = load_native_stats_repository(self._repository_path)
        validate_native_stats_repository_compatibility(
            self._repository, self._snapshot, self._universe
        )
        self._session = PostgresPlannerSession(
            self.config.patched_dsn, self._snapshot, self._universe, self._repository
        )
        self._session.open()
        self._registered_oids = self._session.registered_oids
        if set(self._registered_oids) != set(self._candidates):
            raise LiveAdapterError("registered hypothetical candidate IDs do not match manifest")
        return len(self._registered_oids)

    def activate(self, configuration: Mapping[str, Any]) -> int:
        ids = self._configuration(configuration)
        if self._session is None:
            raise LiveAdapterError("repository is not registered")
        from extstats_advisor.dbms.postgres.planner import configuration_from_ids

        self._session.activate(configuration_from_ids(ids))
        observed = tuple(self._session.active_backend_oids())
        expected = tuple(self._registered_oids[candidate_id] for candidate_id in ids)
        if observed != expected:
            raise LiveAdapterError(f"hypothetical activation order mismatch: {observed!r}")
        _exclusive_json(
            self._audit_path("activation", configuration),
            {"candidate_ids": list(ids), "virtual_oids": list(observed)},
        )
        return len(ids)

    def explain(self, configuration: Mapping[str, Any], query_ids: Sequence[str]) -> int:
        ids = self._query_ids(query_ids)
        if self._session is None:
            raise LiveAdapterError("repository is not registered")
        rows: list[dict[str, Any]] = []
        attempted = successful = 0
        for query_id in ids:
            attempted += 1
            try:
                estimate = self._session.estimate_query(query_id)
                rows.append({"query_id": query_id, "plan_rows": int(estimate.estimated_rows)})
                successful += 1
            except Exception as exc:
                _exclusive_json(
                    self._audit_path("catalogless-explain-failure", configuration),
                    {
                        "attempted": attempted,
                        "successful": successful,
                        "failed": 1,
                        "query_id": query_id,
                        "error": {"class": type(exc).__name__, "message": str(exc)},
                    },
                )
                raise
        _exclusive_json(
            self._audit_path("catalogless-explain", configuration),
            {"attempted": attempted, "successful": successful, "failed": 0, "rows": rows},
        )
        return successful

    def deactivate(self, configuration: Mapping[str, Any]) -> int:
        ids = self._configuration(configuration)
        if self._session is None:
            raise LiveAdapterError("repository is not registered")
        from extstats_advisor.dbms.postgres.planner import configuration_from_ids

        self._session.activate(configuration_from_ids(()))
        observed = tuple(self._session.active_backend_oids())
        if observed:
            raise LiveAdapterError("hypothetical repository remained active after deactivation")
        return len(ids)

    def cleanup(self) -> int:
        if self._session is not None:
            self._session.close()
            self._session = None
        return 1


def validate_live_config(config: LiveAdapterConfig) -> dict[str, Any]:
    """Validate paths, pins, and explicit opt-in without opening a database."""

    stock_fields = _validate_explicit_dsn(config.stock_dsn, "stock DSN")
    admin_fields = _validate_explicit_dsn(config.stock_admin_dsn, "stock admin DSN")
    _validate_explicit_dsn(config.patched_dsn, "patched DSN")
    if str(stock_fields["dbname"]).startswith("rq5wc_"):
        raise LiveAdapterError("stock DSN points at an experiment-owned database")
    if str(admin_fields["dbname"]).startswith("rq5wc_"):
        raise LiveAdapterError("stock admin DSN points at an experiment-owned database")
    _load_identity(config.stock_identity_path, config.expected_stock_sha, "stock PostgreSQL")
    _load_identity(config.patched_identity_path, config.expected_patched_sha, "patched PostgreSQL")
    if not _COMMIT.fullmatch(config.expected_advisor_sha):
        raise LiveAdapterError("Advisor source pin is not a full SHA")
    return {
        "status": "valid-config-only",
        "live_opt_in": config.enable_live,
        "database_names_are_explicit": True,
        "source_pins": {
            "advisor_sha": config.expected_advisor_sha,
            "stock_postgres_sha": config.expected_stock_sha,
            "patched_postgres_sha": config.expected_patched_sha,
        },
    }


def run_integration_preflight(
    manifest: Mapping[str, Any], config: LiveAdapterConfig, output_path: Path
) -> dict[str, Any]:
    """Run a separately supplied tiny fixture, never a formal A/B manifest.

    The caller must provide a fixture snapshot, candidate universe, and
    workload whose manifest contains at most eight candidates, two
    configurations, and eight queries.  This keeps the command a correctness
    smoke rather than a timing run.  The fixture database itself must already
    contain the same relation on the two explicitly supplied DSNs; this
    function never creates or mutates a source database.
    """

    if not config.enable_live:
        raise LiveAdapterError("integration-preflight requires --enable-live-preflight")
    definitions = manifest.get("candidate_universe", {}).get("definitions", [])
    queries = manifest.get("query_subset", [])
    analysis = manifest.get("analysis_a", {})
    configurations = analysis.get("configurations", [])
    if not 1 <= len(definitions) <= 8:
        raise LiveAdapterError("integration preflight requires 1..8 fixture candidates")
    if not 1 <= len(configurations) <= 2:
        raise LiveAdapterError("integration preflight requires 1..2 configurations")
    if not 1 <= len(queries) <= 8:
        raise LiveAdapterError("integration preflight requires 1..8 fixture queries")
    validate_live_config(config)
    config.require_live()
    physical_config = replace(
        config,
        run_id=f"{config.run_id}-physical",
        output_dir=config.output_dir / "physical",
        allow_fixture=True,
    )
    catalogless_config = replace(
        config,
        run_id=f"{config.run_id}-catalogless",
        output_dir=config.output_dir / "catalogless",
        allow_fixture=True,
    )
    physical = StockPhysicalCostAdapter(manifest, physical_config)
    catalogless = CataloglessWhatIfCostAdapter(manifest, catalogless_config)
    query_ids = [str(item["query_id"]) for item in queries]
    primary: BaseException | None = None
    cleanup_errors: list[BaseException] = []
    result: dict[str, Any] = {
        "format_version": "rq5-whatif-evaluation-cost-integration-preflight-v1",
        "status": "failed",
        "classification": "integration-readiness-only",
        "live_opt_in": True,
        "candidate_count": len(definitions),
        "configuration_count": len(configurations),
        "query_count": len(queries),
        "physical": {"analyze_calls": 0, "explain_calls": 0},
        "catalogless": {"materialization_analyze_calls": 0, "explain_calls": 0},
    }
    try:
        physical.prepare_run()
        for configuration in configurations:
            physical.create_clone(configuration)
            physical.create_statistics(configuration)
            physical.analyze(configuration)
            physical.explain(configuration, query_ids)
            physical.drop_statistics(configuration)
            physical.destroy_clone(configuration)
            result["physical"]["analyze_calls"] += 1
            result["physical"]["explain_calls"] += len(query_ids)
        catalogless.prepare_sample()
        catalogless.materialize_payloads()
        catalogless.register_repository()
        result["catalogless"]["materialization_analyze_calls"] = 1
        for configuration in configurations:
            catalogless.activate(configuration)
            catalogless.explain(configuration, query_ids)
            catalogless.deactivate(configuration)
            result["catalogless"]["explain_calls"] += len(query_ids)
        result["status"] = "complete"
    except BaseException as exc:  # noqa: BLE001 - preserve smoke failure
        primary = exc
        result["error"] = {"class": type(exc).__name__, "message": str(exc)}
    finally:
        for adapter in (physical, catalogless):
            try:
                adapter.cleanup()
            except BaseException as exc:  # noqa: BLE001 - preserve cleanup outcome
                cleanup_errors.append(exc)
        result["cleanup"] = {
            "status": "complete" if not cleanup_errors else "failed",
            "error_count": len(cleanup_errors),
        }
        if cleanup_errors:
            result["status"] = "failed"
            result["cleanup"]["errors"] = [
                {"class": type(exc).__name__, "message": str(exc)} for exc in cleanup_errors
            ]
        result = _digest(result)
        _exclusive_json(output_path, result)
    if primary is not None:
        raise LiveAdapterError("integration preflight failed") from primary
    if cleanup_errors:
        raise LiveAdapterError("integration preflight cleanup failed") from cleanup_errors[0]
    return result


__all__ = [
    "CataloglessWhatIfCostAdapter",
    "LiveAdapterConfig",
    "LiveAdapterError",
    "StockPhysicalCostAdapter",
    "build_statistics_ddl",
    "run_integration_preflight",
    "validate_experiment_database_name",
    "validate_live_config",
]
