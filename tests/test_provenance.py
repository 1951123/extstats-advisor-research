from __future__ import annotations

import pytest

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
