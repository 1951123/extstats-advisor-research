"""Stable hashing and credential-safe manifest helpers."""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def semantic_digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, sort_keys=True, indent=2) + "\n"
    if path.suffix == ".gz":
        path.write_bytes(gzip.compress(payload.encode("utf-8"), mtime=0))
    else:
        path.write_text(payload, encoding="utf-8")


def read_json(path: Path) -> Any:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            return json.load(stream)
    return json.loads(path.read_text(encoding="utf-8"))


def reject_credentials(value: Any) -> None:
    """Reject obvious DSN/password fields before a value reaches a manifest."""
    encoded = json.dumps(value, sort_keys=True).lower()
    forbidden = ("password=", "passwd=", "postgresql://", "postgres://", '"dsn"')
    if any(token in encoded for token in forbidden):
        raise ValueError("credentials or DSNs must not be serialized into research artifacts")
