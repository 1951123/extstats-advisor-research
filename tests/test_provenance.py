from __future__ import annotations

import pytest

from extstats_advisor_research import (
    FROZEN_ADVISOR_REPOSITORY,
    FROZEN_ADVISOR_SHA,
    FROZEN_PATCHED_POSTGRES_REPOSITORY,
    FROZEN_PATCHED_POSTGRES_SHA,
    FROZEN_RESEARCH_REPOSITORY,
)
from extstats_advisor_research.pins import verify_research_repository
from extstats_advisor_research.provenance import reject_credentials, semantic_digest
from extstats_advisor_research.runs.layout import create_layout


def test_semantic_identity_is_deterministic() -> None:
    assert semantic_digest({"b": 2, "a": 1}) == semantic_digest({"a": 1, "b": 2})


def test_credentials_are_rejected() -> None:
    with pytest.raises(ValueError, match="credentials"):
        reject_credentials({"dsn": "postgresql://user:password@host/db"})


def test_run_layout_does_not_overwrite(tmp_path) -> None:
    first = create_layout(tmp_path, {"benchmark_id": "fixture", "sample_rows": 1})
    assert first.directory.is_dir()
    with pytest.raises(FileExistsError):
        create_layout(tmp_path, {"benchmark_id": "fixture", "sample_rows": 1})


def test_run_id_changes_when_research_revision_changes() -> None:
    from extstats_advisor_research.runs.layout import run_id

    base = {
        "dataset_content_identity": "dataset",
        "workload_id": "workload",
        "research_repository": FROZEN_RESEARCH_REPOSITORY,
        "advisor_repository": FROZEN_ADVISOR_REPOSITORY,
        "advisor_commit_sha": FROZEN_ADVISOR_SHA,
        "patched_postgres_repository": FROZEN_PATCHED_POSTGRES_REPOSITORY,
        "patched_postgres_commit_sha": FROZEN_PATCHED_POSTGRES_SHA,
        "sample_rows": 10,
        "statistics_target": 100,
        "candidate_limit": 8,
        "search_wall_clock_seconds": 30.0,
    }
    first = {**base, "research_commit_sha": "a" * 40}
    second = {**base, "research_commit_sha": "b" * 40}
    assert run_id(first) == run_id(first)
    assert run_id(first) != run_id(second)


def test_manifest_records_all_version_layers_without_credentials(tmp_path) -> None:
    identity = {
        "research_repository": FROZEN_RESEARCH_REPOSITORY,
        "research_commit_sha": "a" * 40,
        "advisor_repository": FROZEN_ADVISOR_REPOSITORY,
        "advisor_commit_sha": FROZEN_ADVISOR_SHA,
        "patched_postgres_repository": FROZEN_PATCHED_POSTGRES_REPOSITORY,
        "patched_postgres_commit_sha": FROZEN_PATCHED_POSTGRES_SHA,
    }
    layout = create_layout(tmp_path, identity)
    manifest = layout.manifest.read_text(encoding="utf-8")
    assert all(field in manifest for field in identity)
    assert "password" not in manifest.lower()
    assert "postgresql://" not in manifest.lower()


def test_dirty_research_repository_is_rejected(tmp_path) -> None:
    import subprocess

    repository = tmp_path / "research"
    repository.mkdir()
    subprocess.run(["git", "init", str(repository)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "test@example.invalid"], check=True
    )
    subprocess.run(["git", "-C", str(repository), "config", "user.name", "Test"], check=True)
    tracked = repository / "tracked.txt"
    tracked.write_text("clean\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "tracked.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "commit", "-m", "fixture"], check=True, capture_output=True
    )
    tracked.write_text("dirty\n", encoding="utf-8")
    with pytest.raises(ValueError, match="clean research working tree"):
        verify_research_repository(repository)
