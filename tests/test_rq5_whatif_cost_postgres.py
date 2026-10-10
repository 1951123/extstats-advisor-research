from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

import extstats_advisor_research.rq5_whatif_cost_postgres as postgres


def _identity(path: Path, sha: str) -> Path:
    path.write_text(json.dumps({"source_commit_sha": sha, "source_dirty": False}), encoding="utf-8")
    return path


def _config(tmp_path: Path, *, enable_live: bool = False) -> postgres.LiveAdapterConfig:
    stock_sha = "0d1c00c624fa7367d4a895f44381887757289682"
    patched_sha = "6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6"
    return postgres.LiveAdapterConfig(
        stock_dsn="host=127.0.0.1 port=55432 dbname=fixture_template",
        stock_admin_dsn="host=127.0.0.1 port=55432 dbname=postgres",
        patched_dsn="host=127.0.0.1 port=55433 dbname=fixture_template",
        advisor_root=tmp_path / "advisor",
        snapshot_path=tmp_path / "snapshot",
        candidate_universe_path=tmp_path / "candidates.json",
        workload_path=tmp_path / "workload.json",
        stock_identity_path=_identity(tmp_path / "stock.json", stock_sha),
        patched_identity_path=_identity(tmp_path / "patched.json", patched_sha),
        output_dir=tmp_path / "output",
        run_id="offline-adapter-test",
        enable_live=enable_live,
    )


def test_live_adapters_require_explicit_opt_in(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with pytest.raises(postgres.LiveAdapterError, match="enable_live"):
        config.require_live()


def test_live_config_rejects_missing_dsn(tmp_path: Path) -> None:
    config = _config(tmp_path)
    invalid = replace(config, stock_dsn="")
    with pytest.raises(postgres.LiveAdapterError, match="stock DSN"):
        postgres.validate_live_config(invalid)


def test_database_name_and_identity_safety(tmp_path: Path) -> None:
    with pytest.raises(postgres.LiveAdapterError):
        postgres.validate_experiment_database_name("postgres")
    with pytest.raises(postgres.LiveAdapterError):
        postgres.validate_experiment_database_name("rq5wc_bad-name")

    config = _config(tmp_path)
    config.patched_identity_path.write_text(
        json.dumps(
            {
                "source_commit_sha": "0" * 40,
                "source_dirty": False,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(postgres.LiveAdapterError, match="patched PostgreSQL source identity"):
        postgres.validate_live_config(config)


def test_ddl_preserves_declared_candidate_order() -> None:
    candidates = {
        "cand_a": {
            "kind": "postgresql.mcv",
            "column_names": ["a", "b"],
        },
        "cand_b": {
            "kind": "postgresql.dependencies",
            "column_names": ["b", "c"],
        },
    }
    statements = postgres.build_statistics_ddl(
        candidates, "public", "fixture", ("cand_b", "cand_a"), statistics_target=250
    )
    assert [item[0] for item in statements] == ["cand_b", "cand_a"]
    assert "(dependencies)" in statements[0][1]
    assert "(mcv)" in statements[1][1]
    assert all("SET STATISTICS 250" in item[2] for item in statements)


def test_explain_shape_is_fail_closed() -> None:
    assert postgres._explain_rows([{"Plan": {"Plan Rows": 17}}]) == 17
    with pytest.raises(postgres.LiveAdapterError, match="Plan Rows"):
        postgres._explain_rows([{"Plan": {}}])
    with pytest.raises(postgres.LiveAdapterError, match="top-level"):
        postgres._explain_rows({"Plan": {"Plan Rows": 1}})


def test_import_and_configuration_validation_do_not_connect(tmp_path: Path) -> None:
    config = _config(tmp_path)
    result = postgres.validate_live_config(config)
    assert result["status"] == "valid-config-only"
    assert result["live_opt_in"] is False


def test_preflight_is_disabled_without_explicit_opt_in(tmp_path: Path) -> None:
    config = _config(tmp_path)
    manifest = {
        "candidate_universe": {"definitions": [{"candidate_id": "cand_a"}]},
        "query_subset": [{"query_id": "q1", "sql_sha256": "0" * 64}],
        "analysis_a": {
            "configurations": [
                {
                    "ordinal": 1,
                    "configuration_id": "fixture-1",
                    "candidate_ids": ["cand_a"],
                    "declared_order": ["cand_a"],
                    "configuration_size": 1,
                }
            ]
        },
    }
    with pytest.raises(postgres.LiveAdapterError, match="enable-live-preflight"):
        postgres.run_integration_preflight(manifest, config, tmp_path / "result.json")
