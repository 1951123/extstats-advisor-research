"""Validation for the versioned frozen system-under-test contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .provenance import reject_credentials, semantic_digest

SYSTEM_FREEZE_FORMAT = "system-freeze-v1"
SYSTEM_FREEZE_STATUS = "frozen"
RESEARCH_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SYSTEM_FREEZE_PATH = RESEARCH_ROOT / "paper" / "system-freeze-v1.json"
ADVISOR_SHA = "0865c5a6afb8bc176bd7d3b10b13b3da83f1f641"
PATCHED_POSTGRES_SHA = "6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6"
STOCK_POSTGRES_SHA = "0d1c00c624fa7367d4a895f44381887757289682"
POSTGRES_VERSION = "16.14"
CONFIGURE_FEATURES = ["--with-icu", "--with-libxml", "--with-libxslt", "--with-ssl=openssl"]
COMPILER_POLICY = {"CC": "cc", "CFLAGS": ""}
BUILD_CONTRACT = {
    "configure_features": CONFIGURE_FEATURES,
    "compiler_policy": COMPILER_POLICY,
    "locale": "C.utf8",
    "encoding": "UTF8",
}


def load_system_freeze(path: Path = DEFAULT_SYSTEM_FREEZE_PATH) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    validate_system_freeze(value)
    return value


def validate_system_freeze(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError("system freeze must be an object")
    if value.get("format_version") != SYSTEM_FREEZE_FORMAT:
        raise ValueError("unsupported system freeze format")
    if value.get("status") != SYSTEM_FREEZE_STATUS:
        raise ValueError("system freeze is not marked frozen")
    expected = {
        "advisor": ("1951123/extstats-advisor", ADVISOR_SHA),
        "patched_postgresql": ("1951123/postgresql-pgextadv", PATCHED_POSTGRES_SHA),
        "stock_postgresql": ("1951123/postgresql-src", STOCK_POSTGRES_SHA),
    }
    for name, (repository, commit_sha) in expected.items():
        identity = value.get(name)
        if not isinstance(identity, dict):
            raise TypeError(f"system freeze is missing {name}")
        if (
            identity.get("repository") != repository
            or identity.get("source_commit_sha", identity.get("commit_sha")) != commit_sha
        ):
            raise ValueError(f"system freeze {name} SHA or repository mismatch")
        if name != "advisor" and identity.get("postgres_version") != POSTGRES_VERSION:
            raise ValueError(f"system freeze {name} PostgreSQL version mismatch")
        if name != "advisor":
            contract = identity.get("required_build_contract")
            if not isinstance(contract, dict):
                raise TypeError(f"system freeze {name} lacks required_build_contract")
            for field, expected_value in BUILD_CONTRACT.items():
                if contract.get(field) != expected_value:
                    raise ValueError(f"system freeze {name} {field} contract mismatch")
            if name == "patched_postgresql" and contract.get("backend_contract") != (
                "postgresql-pgextadv-16.14-v1"
            ):
                raise ValueError("system freeze patched PostgreSQL backend contract mismatch")
    lab = value.get("postgres_lab")
    if not isinstance(lab, dict):
        raise TypeError("system freeze is missing postgres_lab")
    if lab.get("identity_format") != "postgres-lab-instance-v1":
        raise ValueError("system freeze has the wrong postgres-lab identity format")
    if lab.get("locale") != "C.utf8" or lab.get("encoding") != "UTF8":
        raise ValueError("system freeze locale/encoding contract mismatch")
    if lab.get("configure_features") != CONFIGURE_FEATURES:
        raise ValueError("system freeze configure feature contract mismatch")
    if lab.get("compiler_policy") != COMPILER_POLICY:
        raise ValueError("system freeze compiler policy mismatch")
    reject_credentials(value)
    return {
        "status": "valid",
        "format_version": SYSTEM_FREEZE_FORMAT,
        "advisor_commit_sha": ADVISOR_SHA,
        "patched_postgres_commit_sha": PATCHED_POSTGRES_SHA,
        "stock_postgres_commit_sha": STOCK_POSTGRES_SHA,
        "semantic_digest": semantic_digest(value),
    }
