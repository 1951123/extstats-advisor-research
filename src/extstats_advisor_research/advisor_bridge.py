"""Bridge only to public APIs of the frozen production advisor."""

from __future__ import annotations

import sys
from pathlib import Path

from . import FROZEN_ADVISOR_SHA
from .pins import verify_git_sha


def materialize_native_repository(
    advisor_root: Path,
    patched_dsn: str,
    snapshot_path: Path,
    candidate_path: Path,
    output_path: Path,
    statistics_target: int,
    expected_advisor_sha: str = FROZEN_ADVISOR_SHA,
) -> str:
    """Materialize the fixed sample through production's public API."""
    verify_git_sha(advisor_root, expected_advisor_sha)
    sys.path.insert(0, str(advisor_root / "src"))
    from extstats_advisor.candidates import load_candidate_universe
    from extstats_advisor.dbms.postgres import materialize_native_stats
    from extstats_advisor.native_stats import write_native_stats_repository
    from extstats_advisor.snapshot.bundle import load_snapshot

    snapshot = load_snapshot(snapshot_path)
    universe = load_candidate_universe(candidate_path, snapshot)
    materialization = materialize_native_stats(
        patched_dsn, snapshot, universe, statistics_target=statistics_target
    )
    return write_native_stats_repository(materialization, output_path)
