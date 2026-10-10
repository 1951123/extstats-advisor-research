from __future__ import annotations

import json
from pathlib import Path

from extstats_advisor.candidates import load_candidate_universe
from extstats_advisor.snapshot.bundle import load_snapshot

from extstats_advisor_research.rq5_tiny_fixture import (
    ADVISOR_SHA,
    FIXTURE_ID,
    generate_fixture,
    validate_fixture,
)
from extstats_advisor_research.rq5_whatif_cost_postgres import (
    _query_source,
    _validate_candidate_sources,
)


def test_tiny_fixture_generation_and_validation_are_offline_and_deterministic(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first_report = generate_fixture(
        first,
        producer_commit_sha="1f41f9f14d2a0121c389461942e3a293305513df",
    )
    second_report = generate_fixture(
        second,
        producer_commit_sha="1f41f9f14d2a0121c389461942e3a293305513df",
    )

    assert first_report == second_report
    assert validate_fixture(first) == first_report
    assert json.loads((first / "manifest-v1.json").read_text()) == json.loads(
        (second / "manifest-v1.json").read_text()
    )
    assert (first / "manifest-v1.json").read_text() == (second / "manifest-v1.json").read_text()
    assert (first / "candidate-universe.json").read_text() == (
        second / "candidate-universe.json"
    ).read_text()
    assert (first / "workload.json").read_text() == (second / "workload.json").read_text()
    assert first_report["fixture_id"] == FIXTURE_ID
    assert first_report["candidate_count"] == 6
    assert first_report["query_count"] == 5


def test_tiny_fixture_has_distinct_ordered_configurations(tmp_path: Path) -> None:
    root = tmp_path / "fixture"
    generate_fixture(
        root,
        producer_commit_sha="1f41f9f14d2a0121c389461942e3a293305513df",
    )
    manifest = json.loads((root / "manifest-v1.json").read_text())
    universe = json.loads((root / "candidate-universe.json").read_text())
    configs = manifest["analysis_a"]["configurations"]

    assert manifest["source_bindings"]["advisor_sha"] == ADVISOR_SHA
    assert len(universe["candidates"]) == 6
    assert {candidate["kind"] for candidate in universe["candidates"]} == {
        "postgresql.mcv",
        "postgresql.dependencies",
    }
    assert configs[0]["candidate_ids"] == configs[0]["declared_order"]
    assert configs[1]["candidate_ids"] == configs[1]["declared_order"]
    assert configs[0]["candidate_ids"] != configs[1]["candidate_ids"]
    assert [query["query_id"] for query in manifest["query_subset"]] == [
        "rq5_tiny_q01",
        "rq5_tiny_q02",
        "rq5_tiny_q03",
        "rq5_tiny_q04",
        "rq5_tiny_q05",
    ]


def test_database_initialization_scripts_are_identical_and_explicit(tmp_path: Path) -> None:
    root = tmp_path / "fixture"
    generate_fixture(
        root,
        producer_commit_sha="1f41f9f14d2a0121c389461942e3a293305513df",
    )
    stock = (root / "fixture-rows-stock.sql").read_text()
    patched = (root / "fixture-rows-patched.sql").read_text()
    assert stock == patched == (root / "fixture-rows.sql").read_text()
    assert "CREATE TABLE public.rq5_tiny_cost_fixture" in stock
    assert stock.count("INSERT INTO") == 1
    assert stock.count("ANALYZE public.rq5_tiny_cost_fixture") == 1
    assert "DROP DATABASE" not in stock
    assert "rq5wc_" not in stock


def test_committed_fixture_binds_explicit_binary_identities() -> None:
    root = Path("experiments/rq5-whatif-evaluation-cost-v2/tiny-fixture-v1")
    for role, sha in (
        ("stock-postgresql-16.14", "0d1c00c624fa7367d4a895f44381887757289682"),
        ("patched-postgresql-16.14", "6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6"),
    ):
        value = json.loads(
            (
                root
                / "identities"
                / f"{'stock' if role.startswith('stock') else 'patched'}-identity.json"
            ).read_text()
        )
        assert value["identity_type"] == "expected-source-identity"
        assert value["source_role"] == role
        assert value["source_commit_sha"] == sha
        assert value["source_dirty"] is False


def test_tiny_fixture_matches_live_adapter_source_contracts() -> None:
    root = Path("experiments/rq5-whatif-evaluation-cost-v2/tiny-fixture-v1")
    manifest = json.loads((root / "manifest-v1.json").read_text())
    snapshot = load_snapshot(root / "snapshot")
    universe = load_candidate_universe(root / "candidate-universe.json", snapshot)
    candidates, relation = _validate_candidate_sources(manifest, snapshot, universe)
    queries = _query_source(root / "workload.json", manifest, allow_fixture=True)

    assert relation.name == "rq5_tiny_cost_fixture"
    assert len(candidates) == 6
    assert tuple(queries) == tuple(item["query_id"] for item in manifest["query_subset"])
