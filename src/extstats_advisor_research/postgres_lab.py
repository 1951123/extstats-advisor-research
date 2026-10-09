"""Manage disposable, user-owned PostgreSQL lab instances.

The lab is deliberately separate from the PostgreSQL source trees and from
the system PostgreSQL installation.  This module owns only the fixed
``.runtime/postgres-lab`` tree below the research repository; it never accepts
an arbitrary deletion path and never invokes sudo or systemd.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import socket
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import FROZEN_PATCHED_POSTGRES_SHA

LAB_FORMAT = "postgres-lab-instance-v1"
MARKER_NAME = ".postgres-lab-instance-v1"
IDENTITY_NAME = "identity.json"
LOCALE = "C.utf8"
ENCODING = "UTF8"
STOCK_PORT = 55432
PATCHED_PORT = 55433
LAB_DATABASE = "extstats_lab"
STOCK_DATABASE = LAB_DATABASE
PATCHED_DATABASE = LAB_DATABASE
RESEARCH_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = RESEARCH_ROOT / ".runtime" / "postgres-lab"
REQUIRED_TOOLS = ("cc", "make", "flex", "bison", "perl", "pkg-config")
CONFIGURE_FEATURES = ("--with-icu", "--with-libxml", "--with-libxslt", "--with-ssl=openssl")


@dataclass(frozen=True)
class LabRole:
    name: str
    source: Path
    port: int
    database: str
    expected_sha: str | None

    @property
    def root(self) -> Path:
        return RUNTIME_ROOT / self.name

    @property
    def build(self) -> Path:
        return self.root / "build"

    @property
    def install(self) -> Path:
        return self.root / "install"

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def socket(self) -> Path:
        return self.root / "socket"

    @property
    def logs(self) -> Path:
        return self.root / "logs"


def role_spec(role: str) -> LabRole:
    if role == "stock":
        return LabRole(
            role,
            Path("/home/wqts/projects/postgresql-src"),
            STOCK_PORT,
            STOCK_DATABASE,
            None,
        )
    if role == "patched":
        return LabRole(
            role,
            Path("/home/wqts/projects/postgresql-src-pgextadv"),
            PATCHED_PORT,
            PATCHED_DATABASE,
            FROZEN_PATCHED_POSTGRES_SHA,
        )
    raise ValueError(f"unsupported PostgreSQL lab role: {role}")


def roles_for(role: str) -> tuple[str, ...]:
    if role == "all":
        return ("stock", "patched")
    role_spec(role)
    return (role,)


def _git(source: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(source), *args],
        check=check,
        capture_output=True,
        text=True,
    )
    if result.returncode and not check:
        return ""
    return result.stdout.strip()


def source_identity(spec: LabRole) -> dict[str, Any]:
    if not spec.source.is_dir() or not (spec.source / "configure").is_file():
        raise ValueError(f"PostgreSQL source/configure is missing: {spec.source}")
    commit = _git(spec.source, "rev-parse", "HEAD")
    if not commit or len(commit) != 40:
        raise ValueError(f"could not resolve a full source SHA: {spec.source}")
    dirty = bool(_git(spec.source, "status", "--porcelain"))
    return {
        "source_repository": str(spec.source),
        "source_commit_sha": commit,
        "source_dirty": dirty,
        "source_branch": _git(spec.source, "branch", "--show-current", check=False),
        "source_description": _git(spec.source, "describe", "--always", check=False),
    }


def validate_source_identity(identity: dict[str, Any], spec: LabRole) -> None:
    if not isinstance(identity, dict):
        raise TypeError("source identity must be an object")
    if identity.get("source_commit_sha") is None or len(identity["source_commit_sha"]) != 40:
        raise ValueError("source identity has an invalid commit SHA")
    if identity.get("source_dirty") is not False:
        raise ValueError(f"{spec.name} PostgreSQL source must be clean before build")
    if spec.expected_sha is not None and identity["source_commit_sha"] != spec.expected_sha:
        raise ValueError(
            f"{spec.name} PostgreSQL source is {identity['source_commit_sha']}, "
            f"expected pinned SHA {spec.expected_sha}"
        )


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def safe_role_directory(role: str, runtime_root: Path | None = None) -> Path:
    """Resolve one role directory and reject symlink/root escapes."""

    if role not in {"stock", "patched"}:
        raise ValueError("destructive lab operations accept only stock or patched")
    raw_root = (runtime_root or RUNTIME_ROOT).absolute()
    if raw_root == Path("/") or raw_root.is_symlink():
        raise ValueError("runtime root is unsafe")
    root = raw_root.resolve(strict=False)
    if root == Path("/"):
        raise ValueError("runtime root is unsafe")
    candidate = root / role
    if candidate == root or candidate.parent != root:
        raise ValueError("lab role path is outside the approved runtime root")
    if candidate.is_symlink():
        raise ValueError("lab role directory must not be a symlink")
    resolved = candidate.resolve(strict=False)
    if not _within(resolved, root):
        raise ValueError("lab role path escapes the approved runtime root")
    return candidate


def _paths(spec: LabRole) -> dict[str, Path]:
    # LabRole paths are fixed constants; this check protects future changes.
    root = safe_role_directory(spec.name)
    return {
        "root": root,
        "build": root / "build",
        "install": root / "install",
        "data": root / "data",
        "socket": root / "socket",
        "logs": root / "logs",
    }


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _read_identity(spec: LabRole) -> dict[str, Any]:
    paths = _paths(spec)
    marker = paths["root"] / MARKER_NAME
    identity_path = paths["root"] / IDENTITY_NAME
    if not marker.is_file() or marker.read_text(encoding="utf-8").strip() != LAB_FORMAT:
        raise ValueError(f"{spec.name} lab marker is missing or malformed")
    try:
        identity = json.loads(identity_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{spec.name} lab identity is missing or malformed") from exc
    if identity.get("format_version") != LAB_FORMAT or identity.get("role") != spec.name:
        raise ValueError(f"{spec.name} lab identity marker/role mismatch")
    if identity.get("source_repository") != str(spec.source):
        raise ValueError(f"{spec.name} lab source identity mismatch")
    source_sha = identity.get("source_commit_sha")
    if not isinstance(source_sha, str) or len(source_sha) != 40:
        raise ValueError(f"{spec.name} lab identity has an invalid source SHA")
    if spec.expected_sha is not None and source_sha != spec.expected_sha:
        raise ValueError(f"{spec.name} lab identity is not pinned to the required source SHA")
    return identity


def validate_destroy_target(role: str, runtime_root: Path | None = None) -> Path:
    """Validate every condition before deleting one managed role directory."""

    target = safe_role_directory(role, runtime_root)
    if not target.exists():
        return target
    # Use the same marker and identity checks as a runtime operation.  This
    # intentionally refuses an unmarked directory, even if it looks empty.
    identity = _read_identity(role_spec(role))
    validate_source_identity(
        {
            "source_commit_sha": identity["source_commit_sha"],
            "source_dirty": identity.get("source_dirty"),
        },
        role_spec(role),
    )
    for key in ("build", "install", "data", "socket", "logs"):
        expected = target / key
        recorded = Path(identity.get(f"{key}_directory", expected))
        if recorded != expected and key in {"data", "socket", "install"}:
            raise ValueError(f"{role} lab identity path mismatch for {key}")
    return target


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    capture: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(cwd) if cwd is not None else None,
        check=True,
        text=True,
        capture_output=capture,
        env=env,
    )


def _binary(spec: LabRole, name: str) -> Path:
    path = spec.install / "bin" / name
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ValueError(f"{spec.name} lab binary is missing; run postgres-lab build first: {path}")
    return path


def configure_command(spec: LabRole, source_dir: Path | None = None) -> list[str]:
    source = source_dir or spec.source
    return [
        str(source / "configure"),
        f"--prefix={spec.install}",
        *CONFIGURE_FEATURES,
    ]


def start_command(spec: LabRole) -> list[str]:
    return [
        str(_binary(spec, "pg_ctl")),
        "-D",
        str(spec.data),
        "-l",
        str(spec.logs / "postgres.log"),
        "-o",
        f"-p {spec.port} -k {spec.socket} -c listen_addresses='' -c unix_socket_permissions=0700",
        "-w",
        "-t",
        "60",
        "start",
    ]


def _status_process(spec: LabRole) -> bool:
    pg_ctl = spec.install / "bin" / "pg_ctl"
    if not pg_ctl.is_file():
        return False
    result = subprocess.run(
        [str(pg_ctl), "-D", str(spec.data), "status"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _server_query(spec: LabRole, query: str, database: str | None = None) -> str:
    result = _run(
        [
            str(_binary(spec, "psql")),
            "-X",
            "-h",
            str(spec.socket),
            "-p",
            str(spec.port),
            "-d",
            database or spec.database,
            "-Atqc",
            query,
        ],
        capture=True,
    )
    return result.stdout.strip()


def _identity(spec: LabRole, source: dict[str, Any], *, initialized: bool) -> dict[str, Any]:
    pg_config = _binary(spec, "pg_config")
    postgres = _binary(spec, "postgres")
    compiler = shutil.which("cc") or "cc"
    compiler_version = subprocess.run(
        [compiler, "--version"], check=True, capture_output=True, text=True
    ).stdout.splitlines()[0]
    make_version = subprocess.run(
        ["make", "--version"], check=True, capture_output=True, text=True
    ).stdout.splitlines()[0]
    postgres_version = subprocess.run(
        [str(postgres), "--version"], check=True, capture_output=True, text=True
    ).stdout.strip()
    pg_configure = subprocess.run(
        [str(pg_config), "--configure"], check=True, capture_output=True, text=True
    ).stdout.strip()
    initdb_identity = None
    if initialized:
        initdb = _binary(spec, "initdb")
        initdb_identity = {
            "binary": str(initdb),
            "version": subprocess.run(
                [str(initdb), "--version"], check=True, capture_output=True, text=True
            ).stdout.strip(),
            "command": [
                str(initdb),
                f"--locale={LOCALE}",
                f"--encoding={ENCODING}",
                "--auth-local=trust",
                "--auth-host=reject",
            ],
        }
    return {
        "format_version": LAB_FORMAT,
        "role": spec.name,
        **source,
        "configure_command": configure_command(spec),
        "configure_features": list(CONFIGURE_FEATURES),
        "pg_config_configure": pg_configure,
        "compiler": compiler,
        "compiler_version": compiler_version,
        "make_version": make_version,
        "postgres_version": postgres_version,
        "install_prefix": str(spec.install),
        "build_directory": str(spec.build),
        "data_directory": str(spec.data),
        "socket_directory": str(spec.socket),
        "port": spec.port,
        "database": spec.database,
        "initdb_identity": initdb_identity,
        "locale": LOCALE,
        "encoding": ENCODING,
        "runtime_created_at": datetime.now(UTC).isoformat(),
        "build_environment": {"CC": "cc", "CFLAGS": ""},
        "source_materialization": "temporary clean git archive of source_commit_sha",
    }


def _build_environment() -> dict[str, str]:
    environment = os.environ.copy()
    # Keep stock and patched builds identical at the compiler/environment
    # boundary, independent of an ambient shell CFLAGS/CC setting.
    environment["CC"] = "cc"
    environment["CFLAGS"] = ""
    return environment


def _stage_clean_source(spec: LabRole, source: dict[str, Any], directory: Path) -> Path:
    staged_source = directory / "source"
    staged_source.mkdir()
    archive = directory / "source.tar"
    _run(
        [
            "git",
            "-C",
            str(spec.source),
            "archive",
            "--format=tar",
            f"--output={archive}",
            source["source_commit_sha"],
        ]
    )
    with tarfile.open(archive) as source_archive:
        source_archive.extractall(staged_source)
    if not (staged_source / "configure").is_file():
        raise ValueError(f"clean source archive is missing configure: {staged_source}")
    return staged_source


def build_role(role: str, jobs: int | None = None) -> dict[str, Any]:
    spec = role_spec(role)
    if _status_process(spec):
        raise ValueError(f"cannot build a running {role} lab; stop it first")
    source = source_identity(spec)
    validate_source_identity(source, spec)
    paths = _paths(spec)
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    build_environment = _build_environment()
    # The checked-out source directories may contain ignored products from an
    # earlier in-tree build. PostgreSQL's VPATH makefiles can otherwise reuse
    # those products even for an out-of-tree build. A clean git archive keeps
    # the source trees immutable while making the build inputs unambiguous.
    with tempfile.TemporaryDirectory(prefix=f"extstats-{role}-postgres-source-") as temporary:
        staged_source = _stage_clean_source(spec, source, Path(temporary))
        _run(
            configure_command(spec, staged_source),
            cwd=spec.build,
            env=build_environment,
        )
        # PostgreSQL's first out-of-tree build can race generated objfiles.txt
        # fragments under parallel make; serial is the reproducible safe default.
        job_count = jobs or 1
        _run(["make", f"-j{job_count}"], cwd=spec.build, env=build_environment)
        _run(["make", "install"], cwd=spec.build, env=build_environment)
    identity = _identity(spec, source, initialized=False)
    (paths["root"] / MARKER_NAME).write_text(LAB_FORMAT + "\n", encoding="utf-8")
    _write_json(paths["root"] / IDENTITY_NAME, identity)
    return {"role": role, "status": "built", "identity": identity}


def init_role(role: str) -> dict[str, Any]:
    spec = role_spec(role)
    paths = _paths(spec)
    if not (paths["root"] / MARKER_NAME).is_file():
        raise ValueError(f"{role} lab has not been built")
    if _status_process(spec):
        raise ValueError(f"cannot init an already-running {role} lab")
    if paths["data"].exists() and any(paths["data"].iterdir()):
        raise ValueError(f"{role} PGDATA is not empty; use destroy/recreate explicitly")
    paths["data"].mkdir(parents=True, exist_ok=True)
    _run(
        [
            str(_binary(spec, "initdb")),
            "-D",
            str(paths["data"]),
            f"--locale={LOCALE}",
            f"--encoding={ENCODING}",
            "--auth-local=trust",
            "--auth-host=reject",
            "--no-instructions",
        ]
    )
    source = source_identity(spec)
    validate_source_identity(source, spec)
    identity = _identity(spec, source, initialized=True)
    _write_json(paths["root"] / IDENTITY_NAME, identity)
    return {"role": role, "status": "initialized", "identity": identity}


def start_role(role: str) -> dict[str, Any]:
    spec = role_spec(role)
    identity = _read_identity(spec)
    if identity.get("initdb_identity") is None:
        raise ValueError(f"{role} lab is built but not initialized")
    if _status_process(spec):
        return status_role(role)
    if not _port_available(spec.port):
        raise ValueError(f"port {spec.port} is occupied")
    spec.socket.mkdir(parents=True, exist_ok=True)
    spec.logs.mkdir(parents=True, exist_ok=True)
    _run(start_command(spec))
    exists = _server_query(
        spec,
        "SELECT 1 FROM pg_catalog.pg_database WHERE datname = current_database()",
        database="postgres",
    )
    if exists != "1":
        raise RuntimeError(f"{role} lab started but database connection failed")
    database_exists = _server_query(
        spec,
        f"SELECT 1 FROM pg_catalog.pg_database WHERE datname = '{spec.database}'",
        database="postgres",
    )
    if database_exists != "1":
        _run(
            [
                str(_binary(spec, "createdb")),
                "-h",
                str(spec.socket),
                "-p",
                str(spec.port),
                spec.database,
            ]
        )
    return status_role(role)


def stop_role(role: str) -> dict[str, Any]:
    spec = role_spec(role)
    if not _status_process(spec):
        return status_role(role)
    _run(
        [str(_binary(spec, "pg_ctl")), "-D", str(spec.data), "-m", "fast", "-w", "-t", "60", "stop"]
    )
    if _status_process(spec):
        raise RuntimeError(f"{role} PostgreSQL server did not stop cleanly")
    return status_role(role)


def status_role(role: str) -> dict[str, Any]:
    spec = role_spec(role)
    identity = None
    identity_error = None
    try:
        identity = _read_identity(spec)
    except (OSError, ValueError) as exc:
        identity_error = str(exc)
    running = _status_process(spec)
    result: dict[str, Any] = {
        "role": role,
        "running": running,
        "socket": str(spec.socket),
        "port": spec.port,
        "database": spec.database,
        "socket_exists": spec.socket.exists(),
        "identity": identity,
    }
    if identity_error:
        result["identity_error"] = identity_error
    if running:
        try:
            result["server_version"] = _server_query(spec, "SELECT version()")
        except (OSError, ValueError, subprocess.CalledProcessError) as exc:
            result["server_error"] = str(exc)
    return result


def destroy_role(role: str) -> dict[str, Any]:
    spec = role_spec(role)
    target = validate_destroy_target(role)
    if not target.exists():
        return {"role": role, "status": "absent"}
    if _status_process(spec):
        stop_role(role)
        if _status_process(spec):
            raise RuntimeError(f"refusing to destroy running {role} lab")
    # target was validated immediately before deletion and is never supplied
    # by the caller.  shutil.rmtree is used only for this managed role path.
    shutil.rmtree(target)
    return {"role": role, "status": "destroyed"}


def recreate_role(role: str, jobs: int | None = None) -> dict[str, Any]:
    destroy_role(role)
    build_role(role, jobs=jobs)
    init_role(role)
    start_role(role)
    return {"role": role, "status": "recreated", "runtime": status_role(role)}


def _remove_runtime_component(target: Path, name: str) -> None:
    """Remove one validated runtime component, never a symlink or escape."""

    component = target / name
    if component.parent != target or component.is_symlink():
        raise ValueError(f"refusing to remove unsafe {name} path")
    resolved = component.resolve(strict=False)
    if not _within(resolved, target.resolve(strict=False)):
        raise ValueError(f"refusing to remove {name} outside the managed role")
    if component.is_dir():
        shutil.rmtree(component)
    elif component.exists():
        component.unlink()


def reinit_role(role: str) -> dict[str, Any]:
    """Reset one managed cluster while preserving its compiled install/build."""

    spec = role_spec(role)
    target = validate_destroy_target(role)
    if not target.exists():
        raise ValueError(f"{role} lab is absent; build it before reinit")
    if _status_process(spec):
        stop_role(role)
    if _status_process(spec):
        raise RuntimeError(f"refusing to reinit running {role} lab")
    # The target and identity were validated above.  Only disposable runtime
    # state is removed; build/ and install/ remain untouched.
    for name in ("data", "socket", "logs"):
        _remove_runtime_component(target, name)
    init_role(role)
    start_role(role)
    return {"role": role, "status": "reinitialized", "runtime": status_role(role)}


def env_exports(role: str = "all") -> str:
    selected = roles_for(role)
    lines: list[str] = []
    for selected_role in selected:
        spec = role_spec(selected_role)
        variable = (
            "SIMULATED_PRODUCTION_DSN"
            if selected_role == "stock"
            else "ADVISOR_PATCHED_POSTGRES_DSN"
        )
        dsn = f"host={spec.socket} port={spec.port} dbname={spec.database}"
        lines.append(f"export {variable}={shlex.quote(dsn)}")
        if selected_role == "patched":
            lines.append(f"export EXTSTATS_RQ3_PATCHED_DSN={shlex.quote(dsn)}")
    return "\n".join(lines) + "\n"


def _port_available(port: int) -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def _patched_probe(spec: LabRole) -> dict[str, Any]:
    try:
        import psycopg
        from extstats_advisor.dbms.postgres.patch import probe_patched_postgres

        connection = psycopg.connect(
            f"host={spec.socket} port={spec.port} dbname={spec.database}",
            application_name="extstats-research-postgres-lab-doctor",
            autocommit=True,
        )
        try:
            capabilities = probe_patched_postgres(connection)
            return {
                "ok": True,
                "backend_contract": capabilities.backend_contract,
                "reference_source_commit": capabilities.reference_source_commit,
                "server_version": capabilities.server_version,
                "server_version_num": capabilities.server_version_num,
            }
        finally:
            connection.close()
    except Exception as exc:  # noqa: BLE001  # doctor must report probe failures
        return {"ok": False, "error": str(exc)}


def doctor_role(role: str) -> dict[str, Any]:
    spec = role_spec(role)
    checks: dict[str, Any] = {}
    errors: list[str] = []
    checks["source_path"] = spec.source.is_dir()
    if not checks["source_path"]:
        errors.append(f"missing source path: {spec.source}")
    else:
        try:
            source = source_identity(spec)
            checks["source"] = source
            validate_source_identity(source, spec)
            checks["source_identity"] = True
        except (OSError, ValueError, subprocess.CalledProcessError) as exc:
            checks["source_identity"] = False
            errors.append(str(exc))
    missing_tools = [tool for tool in REQUIRED_TOOLS if shutil.which(tool) is None]
    checks["missing_build_tools"] = missing_tools
    if missing_tools:
        errors.append(f"missing build tools: {', '.join(missing_tools)}")
    checks["port"] = {"number": spec.port, "available": _port_available(spec.port)}
    if not checks["port"]["available"] and not _status_process(spec):
        errors.append(f"port {spec.port} is occupied")
    checks["build"] = {
        "config_status": (spec.build / "config.status").is_file(),
        "postgres": (spec.install / "bin" / "postgres").is_file(),
        "pg_ctl": (spec.install / "bin" / "pg_ctl").is_file(),
        "pg_config": (spec.install / "bin" / "pg_config").is_file(),
        "initdb": (spec.install / "bin" / "initdb").is_file(),
        "psql": (spec.install / "bin" / "psql").is_file(),
    }
    if not all(checks["build"].values()):
        errors.append("build/install is incomplete")
    try:
        checks["identity"] = _read_identity(spec)
    except (OSError, ValueError) as exc:
        checks["identity"] = None
        errors.append(str(exc))
    checks["runtime"] = status_role(role)
    if checks["runtime"]["running"]:
        checks["server_version"] = checks["runtime"].get("server_version")
    if role == "patched" and checks["runtime"]["running"]:
        checks["patched_backend"] = _patched_probe(spec)
        if not checks["patched_backend"]["ok"]:
            errors.append("patched backend capability probe failed")
        elif (
            checks["patched_backend"].get("reference_source_commit") != FROZEN_PATCHED_POSTGRES_SHA
        ):
            errors.append("patched backend reference source commit is not pinned")
    checks["errors"] = errors
    checks["ok"] = not errors
    return checks


def doctor(role: str = "all") -> dict[str, Any]:
    roles = {selected: doctor_role(selected) for selected in roles_for(role)}
    return {"roles": roles, "ok": all(item["ok"] for item in roles.values())}


def inspect(role: str = "all") -> dict[str, Any]:
    return {"roles": {selected: status_role(selected) for selected in roles_for(role)}}
