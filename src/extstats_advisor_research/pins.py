"""Frozen system-under-test identities."""

from __future__ import annotations

import subprocess
from pathlib import Path

from . import FROZEN_ADVISOR_SHA, FROZEN_PATCHED_POSTGRES_SHA


def verify_git_sha(repository: Path, expected: str) -> str:
    actual = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if actual != expected:
        raise ValueError(f"repository {repository} is {actual}, expected frozen SHA {expected}")
    return actual


def verify_frozen_systems(advisor_root: Path, patched_postgres_root: Path) -> dict[str, str]:
    return {
        "advisor_sha": verify_git_sha(advisor_root, FROZEN_ADVISOR_SHA),
        "patched_postgres_sha": verify_git_sha(patched_postgres_root, FROZEN_PATCHED_POSTGRES_SHA),
    }
