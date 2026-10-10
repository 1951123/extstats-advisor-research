"""Bounded real-PostgreSQL integration smoke for Native ANALYZE Stability v1.

This module is intentionally separate from the formal campaign.  It creates a
small synthetic database in a caller-provided disposable cluster, requires an
explicit live-smoke authorization flag, and writes only readiness evidence.
It never resolves a default DSN and never accepts an AreCEL dataset.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import socket
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .native_analyze_stability_harness import StabilityError
from .paper_baseline import qerror
from .provenance import read_json, semantic_digest, sha256_file

SMOKE_FORMAT = "native-analyze-stability-integration-smoke-v1"
FAILURE_FORMAT = "native-analyze-stability-integration-smoke-failure-v1"
PROTOCOL_DIGEST = "65123eb04bd53ed6dc35a526ceff190072f305490c2f3f00b4c6454f505a9c47"
EXPECTED_STOCK_SHA = "0d1c00c624fa7367d4a895f44381887757289682"
FIXTURE_FORMAT = "native-analyze-stability-synthetic-fixture-v1"
MAX_EXPLAIN_CALLS = 32
METHODS = {
    "all": ("stat_ab", "stat_bc"),
    "ab-only": ("stat_ab",),
    "bc-only": ("stat_bc",),
    "none": (),
}
QUERIES = (
    ("q_ab", "SELECT * FROM public.oid_stability_fixture WHERE a = 1 AND b = 1"),
    ("q_bc", "SELECT * FROM public.oid_stability_fixture WHERE b = 2 AND c = 2"),
    ("q_abc", "SELECT * FROM public.oid_stability_fixture WHERE a = 3 AND b = 3 AND c = 3"),
)


@dataclass(frozen=True)
class SmokeConfig:
    stock_install: Path
    port: int
    invocation_id: str
    output: Path

    @property
    def postgres(self) -> Path:
        return self.stock_install / "bin" / "postgres"

    @property
    def initdb(self) -> Path:
        return self.stock_install / "bin" / "initdb"

    @property
    def pg_ctl(self) -> Path:
        return self.stock_install / "bin" / "pg_ctl"


def _run(command: list[str], *, capture: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, capture_output=capture, text=True)


def _ident(value: str) -> str:
    if not value or any(
        character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
        for character in value
    ):
        raise StabilityError(f"unsafe SQL identifier: {value!r}")
    return value


def _dsn(port: int, database: str) -> str:
    # Deliberately explicit: no socket, environment, or default port fallback.
    return f"host=127.0.0.1 port={port} dbname={_ident(database)} user={_ident('wqts')}"


def _connect(port: int, database: str) -> Any:
    import psycopg

    return psycopg.connect(
        _dsn(port, database), autocommit=True, application_name="native-analyze-stability-smoke"
    )


def _binary_identity(config: SmokeConfig) -> dict[str, Any]:
    required = (config.postgres, config.initdb, config.pg_ctl)
    if any(not path.is_file() or not path.stat().st_mode & 0o111 for path in required):
        raise StabilityError(f"stock PostgreSQL installation is incomplete: {config.stock_install}")
    source = Path("/home/wqts/projects/postgresql-src")
    source_sha = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "-C", str(source), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if source_sha != EXPECTED_STOCK_SHA or dirty:
        raise StabilityError("stock PostgreSQL source is not the frozen clean revision")
    version = _run([str(config.postgres), "--version"]).stdout.strip()
    if "16.14" not in version:
        raise StabilityError(f"unexpected stock PostgreSQL version: {version}")
    return {
        "source_commit_sha": source_sha,
        "source_dirty": False,
        "postgres_binary": str(config.postgres),
        "postgres_binary_sha256": sha256_file(config.postgres),
        "postgres_version": version,
        "initdb_binary": str(config.initdb),
        "pg_ctl_binary": str(config.pg_ctl),
    }


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex(("127.0.0.1", port)) != 0


def _start_cluster(config: SmokeConfig, root: Path) -> dict[str, Any]:
    if not _port_is_free(config.port):
        raise StabilityError(f"requested smoke port is occupied: {config.port}")
    data = root / "data"
    socket_dir = root / "socket"
    log = root / "postgres.log"
    _run(
        [
            str(config.initdb),
            "-D",
            str(data),
            "--no-instructions",
            "--locale=C.utf8",
            "--encoding=UTF8",
            "--auth-local=trust",
            "--auth-host=trust",
        ]
    )
    socket_dir.mkdir()
    _run(
        [
            str(config.pg_ctl),
            "-D",
            str(data),
            "-l",
            str(log),
            "-o",
            f"-p {config.port} -h 127.0.0.1 -k {socket_dir} -c listen_addresses=127.0.0.1",
            "-w",
            "-t",
            "60",
            "start",
        ]
    )
    return {"data_directory": str(data), "socket_directory": str(socket_dir), "log": str(log)}


def _stop_cluster(config: SmokeConfig, root: Path) -> dict[str, Any]:
    data = root / "data"
    if not data.exists():
        return {"status": "not-started"}
    result = subprocess.run(
        [str(config.pg_ctl), "-D", str(data), "-m", "fast", "-w", "-t", "60", "stop"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        return {"status": "failed", "exit_code": result.returncode, "stderr": result.stderr[-4000:]}
    return {"status": "complete"}


def _admin(config: SmokeConfig) -> Any:
    return _connect(config.port, "postgres")


def _create_database(config: SmokeConfig, database: str) -> None:
    with _admin(config) as connection:
        connection.execute(f"CREATE DATABASE {_ident(database)}")


def _drop_database(config: SmokeConfig, database: str) -> None:
    with _admin(config) as connection:
        connection.execute(f"DROP DATABASE IF EXISTS {_ident(database)} WITH (FORCE)")


def _setup_fixture(config: SmokeConfig, database: str) -> dict[str, Any]:
    with _connect(config.port, database) as connection:
        connection.execute("DROP TABLE IF EXISTS public.oid_stability_fixture CASCADE")
        connection.execute(
            """CREATE TABLE public.oid_stability_fixture (
                a integer NOT NULL, b integer NOT NULL, c integer NOT NULL, payload text NOT NULL
            )"""
        )
        connection.execute(
            """INSERT INTO public.oid_stability_fixture(a,b,c,payload)
               SELECT (i % 4) + 1, (i % 4) + 1, (i % 4) + 1, 'row-' || i::text
               FROM generate_series(1, 2000) AS g(i)"""
        )
        connection.execute(
            "CREATE STATISTICS public.stat_ab (mcv) ON a, b FROM public.oid_stability_fixture"
        )
        connection.execute(
            "CREATE STATISTICS public.stat_bc (mcv) ON b, c FROM public.oid_stability_fixture"
        )
        connection.execute("ALTER STATISTICS public.stat_ab SET STATISTICS 100")
        connection.execute("ALTER STATISTICS public.stat_bc SET STATISTICS 100")
        connection.execute("ANALYZE public.oid_stability_fixture")
        truth = {
            query_id: int(connection.execute(f"SELECT count(*) FROM ({sql}) AS q").fetchone()[0])
            for query_id, sql in QUERIES
        }
    return {
        "format_version": FIXTURE_FORMAT,
        "database": database,
        "schema": "public",
        "relation": "oid_stability_fixture",
        "row_count": 2000,
        "statistics_target": 100,
        "statistics_definitions": {
            "stat_ab": {"kind": "mcv", "columns": ["a", "b"]},
            "stat_bc": {"kind": "mcv", "columns": ["b", "c"]},
        },
        "canonical_creation_order": ["stat_ab", "stat_bc"],
        "method_memberships": {key: list(value) for key, value in METHODS.items()},
        "queries": [{"query_id": key, "sql": sql, "truth": truth[key]} for key, sql in QUERIES],
    }


def _ordinary_fingerprint(connection: Any, relation_oid: int) -> str:
    rows = connection.execute(
        """SELECT jsonb_agg(to_jsonb(s) ORDER BY s.attname)::text
           FROM pg_catalog.pg_stats AS s
           JOIN pg_catalog.pg_class AS c ON c.relname = s.tablename
           JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace AND n.nspname = s.schemaname
           WHERE c.oid = %s""",
        (relation_oid,),
    ).fetchone()[0]
    return hashlib.sha256((rows or "null").encode()).hexdigest()


def _catalog(connection: Any, *, relation_oid: int | None = None) -> dict[str, Any]:
    rel = connection.execute(
        "SELECT c.oid::int FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname='oid_stability_fixture'"
    ).fetchone()
    if rel is None:
        raise StabilityError("fixture relation is missing")
    relation = int(rel[0])
    target = relation if relation_oid is None else relation_oid
    rows = connection.execute(
        """SELECT e.oid::int, e.stxrelid::int, e.stxname::text, e.stxkeys::text,
                  e.stxkind::text, pg_catalog.pg_get_statisticsobjdef(e.oid),
                  (d.stxdmcv IS NOT NULL), pg_catalog.pg_column_size(d.stxdmcv),
                  pg_catalog.pg_mcv_list_send(d.stxdmcv)
           FROM pg_catalog.pg_statistic_ext e
           JOIN pg_catalog.pg_statistic_ext_data d ON d.stxoid=e.oid
           WHERE e.stxrelid=%s AND e.stxname IN ('stat_ab','stat_bc')
           ORDER BY e.oid""",
        (target,),
    ).fetchall()
    objects: list[dict[str, Any]] = []
    for oid, stxrelid, name, keys, kinds, definition, present, size, payload in rows:
        if not present or not payload:
            raise StabilityError(f"physical mcv payload is missing for {name}")
        payload_bytes = bytes(payload)
        objects.append(
            {
                "candidate_id": name,
                "oid": int(oid),
                "relation_oid": int(stxrelid),
                "keys": keys,
                "kinds": kinds,
                "definition": definition,
                "payload_present": bool(present),
                "payload_bytes": len(payload_bytes),
                "payload_sha256": hashlib.sha256(payload_bytes).hexdigest(),
            }
        )
    return {
        "relation_oid": relation,
        "statistics_objects": objects,
        "payloads": {item["candidate_id"]: item for item in objects},
        "ordinary_statistics_fingerprint": _ordinary_fingerprint(connection, relation),
    }


def _catalog_order(catalog: dict[str, Any], expected: tuple[str, ...]) -> dict[str, Any]:
    objects = catalog["statistics_objects"]
    actual = [item["candidate_id"] for item in objects]
    if actual != list(expected):
        raise StabilityError(f"statistics OID order mismatch: expected {expected}, got {actual}")
    if any(item["relation_oid"] != catalog["relation_oid"] for item in objects):
        raise StabilityError("statistics-object OID was confused with relation OID")
    return {
        "expected_creation_order": list(expected),
        "observed_oid_order": actual,
        "relative_order_status": "verified",
        "relation_oid": catalog["relation_oid"],
        "statistics_oids": {item["candidate_id"]: item["oid"] for item in objects},
    }


def _explain(connection: Any, sql: str, truth: int, query_id: str) -> dict[str, Any]:
    payload = connection.execute(f"EXPLAIN (FORMAT JSON) {sql}").fetchone()[0]
    plan = payload[0]["Plan"]
    estimate = float(plan["Plan Rows"])
    return {
        "query_id": query_id,
        "plan_rows": estimate,
        "truth": truth,
        "qerror": qerror(estimate, truth),
    }


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise StabilityError(f"append-only artifact already exists: {path}")
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _write_raw(path: Path, records: list[dict[str, Any]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise StabilityError(f"append-only raw evidence already exists: {path}")
    with path.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
        for record in records:
            compressed.write((json.dumps(record, sort_keys=True) + "\n").encode("utf-8"))
    return sha256_file(path)


def _metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    values = sorted(float(item["qerror"]) for item in records)
    if not values:
        raise StabilityError("empty smoke query evidence")

    def position(fraction: float) -> float:
        index = (len(values) - 1) * fraction
        lower, upper = int(index), int(index + 0.999999999)
        if lower == upper:
            return values[lower]
        return values[lower] + (values[upper] - values[lower]) * (index - lower)

    return {
        "query_count": len(values),
        "mean_qerror": sum(values) / len(values),
        "p50_qerror": position(0.5),
        "p95_qerror": position(0.95),
        "p99_qerror": position(0.99),
        "max_qerror": values[-1],
    }


def _failure(
    path: Path, invocation_id: str, phase: str, error: Exception, counters: dict[str, int]
) -> dict[str, Any]:
    value = {
        "format_version": FAILURE_FORMAT,
        "invocation_id": invocation_id,
        "phase": phase,
        "status": "failed",
        "exception": {"class": type(error).__name__, "message": str(error)},
        "counters": counters,
        "cleanup": {"status": "complete"},
    }
    value["semantic_digest"] = semantic_digest(value)
    _write_json(path, value)
    return value


def run_integration_smoke(config: SmokeConfig) -> dict[str, Any]:
    if not config.invocation_id or config.output.exists():
        raise StabilityError("smoke invocation identity/output is missing or already exists")
    if config.port in {5432, 55432, 55433}:
        raise StabilityError("smoke requires a non-default, non-managed-lab port")
    protocol = read_json(Path("paper/native-analyze-stability-protocol-v1.json"))
    if protocol.get("semantic_digest") != PROTOCOL_DIGEST:
        raise StabilityError("frozen Native ANALYZE Stability protocol digest mismatch")
    binary = _binary_identity(config)
    root = Path(tempfile.mkdtemp(prefix=f"native-analyze-smoke-{config.invocation_id}-"))
    raw_root = config.output.parent / f"{config.output.stem}-raw"
    dbs: list[str] = []
    counters = {
        "analyze_count": 0,
        "explain_count": 0,
        "completed_realizations": 0,
        "completed_clones": 0,
    }
    realizations: list[dict[str, Any]] = []
    failure_record: dict[str, Any] | None = None
    cluster_started = False
    try:
        _start_cluster(config, root)
        cluster_started = True
        for realization_number in (1, 2):
            parent_db = f"nas_smoke_r{realization_number}"
            dbs.append(parent_db)
            _create_database(config, parent_db)
            fixture = _setup_fixture(config, parent_db)
            with _connect(config.port, parent_db) as connection:
                parent_catalog = _catalog(connection)
            order = _catalog_order(parent_catalog, ("stat_ab", "stat_bc"))
            counters["analyze_count"] += 1
            method_results: dict[str, Any] = {}
            for method, selected in METHODS.items():
                clone_db = f"nas_smoke_r{realization_number}_{method.replace('-', '_')}"
                dbs.append(clone_db)
                with _admin(config) as connection:
                    connection.execute(
                        f"CREATE DATABASE {_ident(clone_db)} TEMPLATE {_ident(parent_db)}"
                    )
                with _connect(config.port, clone_db) as connection:
                    before = _catalog(connection)
                    for candidate in ("stat_ab", "stat_bc"):
                        if candidate not in selected:
                            connection.execute(f"DROP STATISTICS public.{_ident(candidate)}")
                    after = (
                        _catalog(connection)
                        if selected
                        else {
                            "relation_oid": before["relation_oid"],
                            "statistics_objects": [],
                            "payloads": {},
                            "ordinary_statistics_fingerprint": _ordinary_fingerprint(
                                connection, before["relation_oid"]
                            ),
                        }
                    )
                    if [item["candidate_id"] for item in after["statistics_objects"]] != list(
                        selected
                    ):
                        raise StabilityError(f"clone membership mismatch for {method}")
                    if (
                        after["ordinary_statistics_fingerprint"]
                        != parent_catalog["ordinary_statistics_fingerprint"]
                    ):
                        raise StabilityError("ordinary statistics changed in method clone")
                    for candidate in selected:
                        if (
                            after["payloads"][candidate]["payload_sha256"]
                            != parent_catalog["payloads"][candidate]["payload_sha256"]
                        ):
                            raise StabilityError(f"retained payload mismatch for {candidate}")
                    records = []
                    for query_id, sql in QUERIES:
                        query = next(
                            item for item in fixture["queries"] if item["query_id"] == query_id
                        )
                        record = _explain(connection, sql, int(query["truth"]), query_id)
                        record["sql_digest"] = hashlib.sha256(sql.encode()).hexdigest()
                        record["method_id"] = method
                        records.append(record)
                    counters["explain_count"] += len(records)
                raw_path = raw_root / f"realization-{realization_number:02d}-{method}.jsonl.gz"
                raw_sha = _write_raw(raw_path, records)
                method_results[method] = {
                    "method_id": method,
                    "selected_membership": list(selected),
                    "analyze_count": 0,
                    "controls": {
                        "retained_payloads_equal": True,
                        "ordinary_statistics_equal": True,
                        "no_method_analyze": True,
                    },
                    "catalog": after,
                    "metrics": _metrics(records),
                    "raw_observations": {
                        "path": str(raw_path.relative_to(config.output.parent)),
                        "sha256": raw_sha,
                        "count": len(records),
                    },
                }
                counters["completed_clones"] += 1
            counters["completed_realizations"] += 1
            realizations.append(
                {
                    "realization_id": f"realization-{realization_number:02d}",
                    "database": parent_db,
                    "fixture": fixture,
                    "shared_parent": parent_catalog,
                    "oid_controls": order,
                    "analyze_count": 1,
                    "method_arms": method_results,
                }
            )
        # Controlled reader regression: using a statistics-object OID as stxrelid must not find payloads.
        first = realizations[0]["shared_parent"]["statistics_objects"][0]
        with _connect(config.port, realizations[0]["database"]) as connection:
            wrong = _catalog(connection, relation_oid=int(first["oid"]))
        if wrong["statistics_objects"]:
            raise StabilityError("statistics-object OID incorrectly resolved as relation OID")
        oid_regression = {
            "pass": True,
            "relation_oid": realizations[0]["shared_parent"]["relation_oid"],
            "statistics_oid": first["oid"],
        }
        # A separate, non-destructive failure invocation uses the same database and preserves a new record.
        failure_path = config.output.parent / f"{config.output.stem}-failure.json"
        try:
            raise StabilityError("controlled wrong statistics-object/relation OID mapping")
        except StabilityError as error:
            failure_record = _failure(
                failure_path,
                f"{config.invocation_id}-failure",
                "catalog-payload-audit",
                error,
                counters,
            )
        cleanup = {"status": "complete", "database_count": len(dbs), "cluster": "pending"}
    except Exception as error:
        failure_path = config.output.parent / f"{config.output.stem}-failure.json"
        if not failure_path.exists():
            _failure(failure_path, config.invocation_id, "live-smoke", error, counters)
        raise
    finally:
        cleanup_errors: list[str] = []
        for database in reversed(dbs):
            try:
                _drop_database(config, database)
            except Exception as error:  # noqa: BLE001  # preserve cleanup diagnostics
                cleanup_errors.append(f"{database}: {error}")
        if cluster_started:
            stopped = _stop_cluster(config, root)
            if stopped.get("status") != "complete":
                cleanup_errors.append(json.dumps(stopped, sort_keys=True))
        shutil.rmtree(root, ignore_errors=False)
        cleanup = {"status": "failed" if cleanup_errors else "complete", "errors": cleanup_errors}
    artifact = {
        "format_version": SMOKE_FORMAT,
        "scientific_eligibility": "integration-readiness-only",
        "formal_execution_authorized": False,
        "scientific_stability_results_available": False,
        "invocation_id": config.invocation_id,
        "protocol_semantic_digest": PROTOCOL_DIGEST,
        "producer_sha": subprocess.run(
            ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
        ).stdout.strip(),
        "postgresql": binary,
        "fixture": {
            "identity": "synthetic-oid-stability-fixture-v1",
            "sql_contract": "deterministic 2000-row correlated a/b/c fixture",
        },
        "realizations": realizations,
        "counters": {
            **counters,
            "parent_analyze_count": counters["analyze_count"],
            "clone_analyze_count": 0,
            "physical_explain_count": counters["explain_count"],
        },
        "max_explain_calls": MAX_EXPLAIN_CALLS,
        "failure_invocation": {
            "path": str(failure_path.relative_to(config.output.parent)),
            "semantic_digest": failure_record["semantic_digest"],
        },
        "oid_mapping_regression": oid_regression,
        "cleanup": cleanup,
    }
    artifact["semantic_digest"] = semantic_digest(artifact)
    _write_json(config.output, artifact)
    return artifact


def validate_integration_smoke(path: Path) -> dict[str, Any]:
    artifact = read_json(path)
    body = {key: value for key, value in artifact.items() if key != "semantic_digest"}
    if semantic_digest(body) != artifact.get("semantic_digest"):
        raise StabilityError("integration smoke semantic digest mismatch")
    if (
        artifact.get("format_version") != SMOKE_FORMAT
        or artifact.get("scientific_eligibility") != "integration-readiness-only"
    ):
        raise StabilityError("unsupported integration smoke artifact")
    if artifact.get("protocol_semantic_digest") != PROTOCOL_DIGEST:
        raise StabilityError("integration smoke protocol binding mismatch")
    counters = artifact.get("counters", {})
    if counters.get("parent_analyze_count") != 2 or counters.get("clone_analyze_count") != 0:
        raise StabilityError("ANALYZE accounting is invalid")
    if counters.get("physical_explain_count", 0) > MAX_EXPLAIN_CALLS:
        raise StabilityError("smoke EXPLAIN bound exceeded")
    root = path.parent
    realizations = artifact.get("realizations")
    if not isinstance(realizations, list) or [
        item.get("realization_id") for item in realizations
    ] != [
        "realization-01",
        "realization-02",
    ]:
        raise StabilityError("smoke must contain exactly two ordered realizations")
    for realization in artifact.get("realizations", []):
        if realization.get("analyze_count") != 1:
            raise StabilityError("realization ANALYZE count is invalid")
        method_arms = realization.get("method_arms", {})
        if set(method_arms) != set(METHODS):
            raise StabilityError("smoke method-arm inventory is incomplete")
        for arm in method_arms.values():
            if arm.get("analyze_count") != 0 or not all(arm.get("controls", {}).values()):
                raise StabilityError("clone control failed")
            evidence = arm["raw_observations"]
            raw = root / evidence["path"]
            if sha256_file(raw) != evidence["sha256"]:
                raise StabilityError("raw smoke evidence digest mismatch")
            with gzip.open(raw, "rt", encoding="utf-8") as stream:
                records = [json.loads(line) for line in stream if line.strip()]
            if len(records) != evidence["count"]:
                raise StabilityError("raw smoke evidence count mismatch")
            for record in records:
                if qerror(record["plan_rows"], record["truth"]) != record["qerror"]:
                    raise StabilityError("raw smoke q-error mismatch")
    failure = root / artifact["failure_invocation"]["path"]
    failed = read_json(failure)
    if failed.get("semantic_digest") != artifact["failure_invocation"]["semantic_digest"]:
        raise StabilityError("failure provenance digest mismatch")
    if not artifact.get("oid_mapping_regression", {}).get("pass"):
        raise StabilityError("OID mapping regression did not pass")
    if artifact.get("cleanup", {}).get("status") != "complete":
        raise StabilityError("smoke cleanup did not complete")
    return {
        "status": "valid",
        "format_version": SMOKE_FORMAT,
        "semantic_digest": artifact["semantic_digest"],
        "explain_count": counters["physical_explain_count"],
    }


__all__ = ["SMOKE_FORMAT", "SmokeConfig", "run_integration_smoke", "validate_integration_smoke"]
