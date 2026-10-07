from __future__ import annotations

import json

from extstats_advisor_research.incremental_search_hardening import (
    FORMAT_VERSION,
    validate_artifact,
)
from extstats_advisor_research.provenance import semantic_digest


def test_hardening_validator_requires_budgets_empty_incidence_and_deadline(tmp_path) -> None:
    value = {
        "format_version": FORMAT_VERSION,
        "status": "complete",
        "formal_experiment": False,
        "plan_policy": {"tested_budgets": [3, 2, 1]},
        "cases": {
            "3": {"semantic_equivalence": {"trace": True}},
            "2": {"semantic_equivalence": {"trace": True}},
            "1": {"semantic_equivalence": {"trace": True}},
        },
        "empty_incidence_add": {"passed": True},
        "deadline_incomplete_round": {"termination_reason": "budget-expired-incomplete-round"},
    }
    value["artifact_digest"] = semantic_digest(value)
    path = tmp_path / "hardening.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    assert validate_artifact(path)["status"] == "valid"
