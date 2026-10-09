"""Frozen system-under-test identities."""

from __future__ import annotations

import subprocess
from collections.abc import Iterable
from pathlib import Path

from . import (
    FROZEN_ADVISOR_REPOSITORY,
    FROZEN_ADVISOR_SHA,
    FROZEN_PATCHED_POSTGRES_REPOSITORY,
    FROZEN_PATCHED_POSTGRES_SHA,
    FROZEN_RESEARCH_REPOSITORY,
)


def _git(repository: Path, *arguments: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError(
            f"could not resolve Git identity for research repository {repository}"
        ) from exc


def verify_research_repository(
    repository: Path, *, allowed_untracked_paths: Iterable[Path] = ()
) -> dict[str, str]:
    """Require a clean revision, optionally with one declared untracked input.

    Formal commands may receive an append-only preflight that is intentionally
    created immediately before execution.  Only explicitly named untracked
    files are allowed; tracked modifications and every other status entry
    remain fatal.
    """
    allowed = {Path(path).resolve() for path in allowed_untracked_paths}
    status = _git(repository, "status", "--porcelain", "--untracked-files=all")
    unexpected: list[str] = []
    for line in status.splitlines():
        if line.startswith("?? "):
            candidate = (repository / line[3:]).resolve()
            if candidate in allowed:
                continue
        unexpected.append(line)
    if unexpected:
        raise ValueError(
            "canonical research runs require a clean research working tree; "
            f"uncommitted changes found in {repository}: {'; '.join(unexpected)}"
        )
    commit_sha = _git(repository, "rev-parse", "HEAD")
    if not commit_sha:
        raise ValueError(f"canonical research run has no resolvable HEAD: {repository}")
    return {
        "research_repository": FROZEN_RESEARCH_REPOSITORY,
        "research_commit_sha": commit_sha,
    }


def verify_git_sha(repository: Path, expected: str) -> str:
    actual = _git(repository, "rev-parse", "HEAD")
    if actual != expected:
        raise ValueError(f"repository {repository} is {actual}, expected frozen SHA {expected}")
    return actual


def verify_frozen_systems(advisor_root: Path, patched_postgres_root: Path) -> dict[str, str]:
    return {
        "advisor_repository": FROZEN_ADVISOR_REPOSITORY,
        "advisor_commit_sha": verify_git_sha(advisor_root, FROZEN_ADVISOR_SHA),
        "patched_postgres_repository": FROZEN_PATCHED_POSTGRES_REPOSITORY,
        "patched_postgres_commit_sha": verify_git_sha(
            patched_postgres_root, FROZEN_PATCHED_POSTGRES_SHA
        ),
    }
