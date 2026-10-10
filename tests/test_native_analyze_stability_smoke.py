"""Offline contracts for the bounded Native ANALYZE Stability smoke."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from extstats_advisor_research.native_analyze_stability_harness import StabilityError
from extstats_advisor_research.native_analyze_stability_smoke import (
    METHODS,
    PROTOCOL_DIGEST,
    SMOKE_FORMAT,
    validate_integration_smoke,
)
from extstats_advisor_research.provenance import semantic_digest, sha256_file


def _artifact(tmp_path: Path) -> Path:
    raw_dir = tmp_path / "smoke-raw"
    raw_dir.mkdir()
    arms: dict[str, object] = {}
    for method in METHODS:
        raw = raw_dir / f"realization-01-{method}.jsonl.gz"
        record = {"query_id": "q-1", "plan_rows": 10.0, "truth": 10, "qerror": 1.0}
        with gzip.open(raw, "wt", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(record) + "\n")
        arms[method] = {
            "method_id": method,
            "analyze_count": 0,
            "controls": {
                "retained_payloads_equal": True,
                "ordinary_statistics_equal": True,
                "no_method_analyze": True,
            },
            "raw_observations": {
                "path": str(raw.relative_to(tmp_path)),
                "sha256": sha256_file(raw),
                "count": 1,
            },
        }
    realizations = []
    for number in (1, 2):
        realizations.append(
            {"realization_id": f"realization-{number:02d}", "analyze_count": 1, "method_arms": arms}
        )
    failure = tmp_path / "smoke-failure.json"
    failure_value = {
        "format_version": "native-analyze-stability-integration-smoke-failure-v1",
        "status": "failed",
        "semantic_digest": "placeholder",
    }
    failure_value["semantic_digest"] = semantic_digest(
        {key: value for key, value in failure_value.items() if key != "semantic_digest"}
    )
    failure.write_text(json.dumps(failure_value), encoding="utf-8")
    artifact = {
        "format_version": SMOKE_FORMAT,
        "scientific_eligibility": "integration-readiness-only",
        "formal_execution_authorized": False,
        "scientific_stability_results_available": False,
        "protocol_semantic_digest": PROTOCOL_DIGEST,
        "counters": {
            "parent_analyze_count": 2,
            "clone_analyze_count": 0,
            "physical_explain_count": 8,
        },
        "realizations": realizations,
        "failure_invocation": {
            "path": failure.name,
            "semantic_digest": failure_value["semantic_digest"],
        },
        "oid_mapping_regression": {"pass": True},
        "cleanup": {"status": "complete"},
    }
    artifact["semantic_digest"] = semantic_digest(artifact)
    output = tmp_path / "smoke.json"
    output.write_text(json.dumps(artifact), encoding="utf-8")
    return output


def test_smoke_validator_recomputes_raw_qerror_and_controls(tmp_path: Path) -> None:
    assert validate_integration_smoke(_artifact(tmp_path))["status"] == "valid"


def test_smoke_validator_rejects_tampered_raw_bytes(tmp_path: Path) -> None:
    path = _artifact(tmp_path)
    artifact = json.loads(path.read_text())
    raw = tmp_path / artifact["realizations"][0]["method_arms"]["all"]["raw_observations"]["path"]
    with gzip.open(raw, "wt", encoding="utf-8") as stream:
        stream.write('{"query_id":"q-1","plan_rows":20,"truth":10,"qerror":2}\n')
    with pytest.raises(StabilityError):
        validate_integration_smoke(path)


def test_smoke_validator_rejects_incomplete_arm_even_with_recomputed_digest(tmp_path: Path) -> None:
    path = _artifact(tmp_path)
    artifact = json.loads(path.read_text())
    del artifact["realizations"][1]["method_arms"]["none"]
    artifact["semantic_digest"] = semantic_digest(artifact)
    path.write_text(json.dumps(artifact), encoding="utf-8")
    with pytest.raises(StabilityError):
        validate_integration_smoke(path)


def test_smoke_contract_has_no_formal_authorization() -> None:
    assert set(METHODS) == {"all", "ab-only", "bc-only", "none"}
