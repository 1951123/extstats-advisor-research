"""Canonical end-to-end run orchestration around the frozen advisor CLI."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

from . import FROZEN_PATCHED_POSTGRES_SHA
from .advisor_bridge import materialize_native_repository
from .analysis.audit import run_audit
from .analysis.summary import extract_summary
from .arecel_truth import authoritative_truth_spec, validate_observation_wire
from .canonical_runner import run_dmv11 as _shared_run_dmv11
from .canonical_runner import run_forest10 as _shared_run_forest10
from .canonical_runner import run_power7 as _shared_run_power7
from .datasets import census13, forest10
from .forest_baseline import run_forest_full_data_target100
from .forest_canonical import (
    compact_summary,
    compare_production_truth_to_labels,
    sampling_provenance,
)
from .pins import verify_frozen_systems, verify_research_repository
from .postgres.loader import load_census13, load_forest10
from .provenance import read_json, reject_credentials, sha256_file, write_json
from .runs.layout import RunLayout, create_layout, update_manifest


def _run(command: list[str], log_path: Path) -> dict[str, Any]:
    started = time.monotonic()
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    safe_command = list(command)
    for index, value in enumerate(safe_command):
        if value in {"--dsn", "--production-dsn", "--planner-dsn"} and index + 1 < len(
            safe_command
        ):
            safe_command[index + 1] = "<redacted>"

    def redact(text: str) -> str:
        import re

        return re.sub(r"(?i)(password|passwd)=[^\s]+", r"\1=<redacted>", text)

    record = {
        "command": safe_command,
        "returncode": completed.returncode,
        "stdout": redact(completed.stdout),
        "stderr": redact(completed.stderr),
        "elapsed_seconds": round(time.monotonic() - started, 6),
    }
    with log_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True) + "\n")
    if completed.returncode:
        raise RuntimeError(f"advisor command failed ({completed.returncode}): {' '.join(command)}")
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    return json.loads(lines[-1]) if lines and lines[-1].startswith("{") else {"status": "ok"}


def _artifact_digests(layout: RunLayout) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for name, path in layout.artifacts().items():
        if name in {"logs", "summary"}:
            continue
        manifest = path / "manifest.json" if path.is_dir() else path
        if manifest.is_file():
            value = read_json(manifest)
            result[name] = value.get("semantic_digest") or value.get("sha256")
    return result


def _artifact_records(layout: RunLayout) -> dict[str, dict[str, str | None]]:
    digests = _artifact_digests(layout)
    return {
        name: {
            "path": str(path.relative_to(layout.directory)),
            "semantic_digest": digests.get(name),
        }
        for name, path in layout.artifacts().items()
        if name not in {"logs", "summary"}
    }


def run_census13(
    *,
    production_dsn: str,
    planner_dsn: str,
    advisor_root: Path,
    patched_postgres_root: Path,
    output_root: Path,
    sample_rows: int = 10_000,
    sample_seed: int = 42,
    statistics_target: int = 100,
    candidate_limit: int = 8,
    search_wall_clock_seconds: float = 30.0,
    data_root: Path | None = None,
    reset_disposable: bool = False,
    seed_identifier: int | None = None,
    advisor_command: str = "extstats-advisor",
    truth_source: str = "authoritative-arecel",
    authoritative_observations: Path | None = None,
) -> dict[str, Any]:
    if not production_dsn or not planner_dsn:
        raise ValueError("both PostgreSQL DSNs are required for a canonical run")
    research_root = Path(__file__).resolve().parents[2]
    if truth_source not in {"authoritative-arecel", "production-exact"}:
        raise ValueError("truth_source must be authoritative-arecel or production-exact")
    truth_spec = None
    if truth_source == "authoritative-arecel":
        truth_spec = authoritative_truth_spec(census13.BENCHMARK_ID, research_root)
        if authoritative_observations is not None:
            candidate = Path(authoritative_observations).expanduser().resolve()
            if not candidate.is_file():
                raise FileNotFoundError(candidate)
            validate_observation_wire(read_json(candidate), workload_id="arecel_census13_test_v1")
            truth_spec["observations_path"] = candidate
    research_identity = verify_research_repository(research_root)
    pins = verify_frozen_systems(advisor_root, patched_postgres_root)
    dataset = census13.inspect(data_root)
    workload_path = Path(output_root) / "_workload.json"
    census13.extract_workload(workload_path, data_root, "test")
    workload = read_json(workload_path)
    identity = {
        **research_identity,
        **pins,
        "benchmark_id": census13.BENCHMARK_ID,
        "dataset_content_identity": dataset.get(
            "dataset_content_identity", dataset.get("csv_sha256")
        ),
        "workload_id": workload["workload_id"],
        "workload_sha256": sha256_file(workload_path),
        "sample_rows": sample_rows,
        "sample_seed": sample_seed,
        "statistics_target": statistics_target,
        "candidate_limit": candidate_limit,
        "search_wall_clock_seconds": search_wall_clock_seconds,
        "experiment_seed_identifier": seed_identifier,
        "postgresql_setseed_sql": f"SELECT setseed(1.0 / {seed_identifier})"
        if seed_identifier is not None
        else None,
        "truth_source": truth_source,
    }
    reject_credentials(identity)
    layout = create_layout(output_root, identity)
    paths = layout.artifacts()
    paths["workload"] = layout.path("workload.json")
    paths["dataset_manifest"] = layout.path("dataset-manifest.json")
    if truth_spec is not None:
        paths["authoritative_observations"] = layout.path("authoritative-observations-v1.json")
        paths["authoritative_observations"].write_bytes(
            Path(truth_spec["observations_path"]).read_bytes()
        )
    paths["workload"].write_text(workload_path.read_text(encoding="utf-8"), encoding="utf-8")
    census13.write_dataset_manifest(paths["dataset_manifest"], data_root)
    load_result = load_census13(
        production_dsn,
        data_root=data_root,
        reset_disposable=reset_disposable,
        statistics_target=statistics_target,
        seed_identifier=seed_identifier,
    )
    update_manifest(
        layout,
        {
            "dataset_manifest": str(paths["dataset_manifest"].relative_to(layout.directory)),
            "workload": str(paths["workload"].relative_to(layout.directory)),
            "load": load_result,
        },
    )
    advisor = [advisor_command]
    capture_command = advisor + [
        "snapshot",
        "capture",
        "postgres",
        "--dsn",
        production_dsn,
        "--relation",
        census13.RELATION,
        "--sample-rows",
        str(sample_rows),
        "--sample-seed",
        str(sample_seed),
        "--workload",
        str(paths["workload"]),
        "--output",
        str(paths["snapshot"]),
    ]
    if truth_source == "production-exact":
        capture_command.extend(["--ground-truth-output", str(paths["ground_truth"])])
    _run(capture_command, paths["logs"])
    if truth_spec is not None:
        _run(
            advisor
            + [
                "ground-truth",
                "import",
                "authoritative",
                str(paths["snapshot"]),
                str(paths["authoritative_observations"]),
                "--authority",
                str(truth_spec["authority"]),
                "--dataset-identity",
                str(truth_spec["dataset_identity"]),
                "--source-revision",
                str(truth_spec["source_revision"]),
                "--output",
                str(paths["ground_truth"]),
            ],
            paths["logs"],
        )
        update_manifest(
            layout,
            {
                "authoritative_observations": {
                    "path": str(paths["authoritative_observations"].relative_to(layout.directory)),
                    "sha256": sha256_file(paths["authoritative_observations"]),
                    "authority": truth_spec["authority"],
                    "dataset_identity": truth_spec["dataset_identity"],
                    "source_revision": truth_spec["source_revision"],
                }
            },
        )
    _run(
        advisor
        + [
            "candidates",
            "derive",
            str(paths["snapshot"]),
            "--output",
            str(paths["candidate_universe"]),
        ],
        paths["logs"],
    )
    native_digest = materialize_native_repository(
        advisor_root,
        planner_dsn,
        paths["snapshot"],
        paths["candidate_universe"],
        paths["native_repository"],
        statistics_target,
    )
    _run(
        advisor
        + [
            "sandbox",
            "prepare",
            "postgres",
            str(paths["snapshot"]),
            str(paths["candidate_universe"]),
            str(paths["native_repository"]),
            "--dsn",
            planner_dsn,
        ],
        paths["logs"],
    )
    _run(
        advisor
        + [
            "sandbox",
            "verify",
            "postgres",
            str(paths["snapshot"]),
            str(paths["candidate_universe"]),
            str(paths["native_repository"]),
            "--dsn",
            planner_dsn,
        ],
        paths["logs"],
    )
    _run(
        advisor
        + [
            "profiling",
            "singleton",
            "postgres",
            str(paths["snapshot"]),
            str(paths["candidate_universe"]),
            str(paths["native_repository"]),
            str(paths["ground_truth"]),
            "--dsn",
            planner_dsn,
            "--output",
            str(paths["singleton_profile"]),
        ],
        paths["logs"],
    )
    _run(
        advisor
        + [
            "optimization",
            "plan",
            str(paths["snapshot"]),
            str(paths["candidate_universe"]),
            str(paths["native_repository"]),
            str(paths["ground_truth"]),
            str(paths["singleton_profile"]),
            "--candidate-limit",
            str(candidate_limit),
            "--wall-clock-seconds",
            str(search_wall_clock_seconds),
            "--output",
            str(paths["optimization_plan"]),
        ],
        paths["logs"],
    )
    _run(
        advisor
        + [
            "optimization",
            "search",
            "postgres",
            str(paths["snapshot"]),
            str(paths["candidate_universe"]),
            str(paths["native_repository"]),
            str(paths["ground_truth"]),
            str(paths["singleton_profile"]),
            str(paths["optimization_plan"]),
            "--dsn",
            planner_dsn,
            "--output",
            str(paths["search_result"]),
        ],
        paths["logs"],
    )
    _run(
        advisor
        + [
            "recommendation",
            "build",
            "postgres",
            str(paths["snapshot"]),
            str(paths["candidate_universe"]),
            str(paths["native_repository"]),
            str(paths["ground_truth"]),
            str(paths["singleton_profile"]),
            str(paths["optimization_plan"]),
            str(paths["search_result"]),
            "--output",
            str(paths["recommendation"]),
        ],
        paths["logs"],
    )
    _run(advisor + ["snapshot", "validate", str(paths["snapshot"])], paths["logs"])
    _run(
        advisor
        + [
            "candidates",
            "validate",
            str(paths["candidate_universe"]),
            "--snapshot",
            str(paths["snapshot"]),
        ],
        paths["logs"],
    )
    _run(
        advisor
        + [
            "profiling",
            "validate",
            str(paths["singleton_profile"]),
            "--snapshot",
            str(paths["snapshot"]),
            "--candidate-universe",
            str(paths["candidate_universe"]),
            "--native-repository",
            str(paths["native_repository"]),
            "--ground-truth",
            str(paths["ground_truth"]),
        ],
        paths["logs"],
    )
    _run(
        advisor
        + [
            "optimization",
            "validate",
            str(paths["optimization_plan"]),
            "--snapshot",
            str(paths["snapshot"]),
            "--candidate-universe",
            str(paths["candidate_universe"]),
            "--native-repository",
            str(paths["native_repository"]),
            "--ground-truth",
            str(paths["ground_truth"]),
            "--singleton-profile",
            str(paths["singleton_profile"]),
        ],
        paths["logs"],
    )
    _run(
        advisor
        + [
            "optimization",
            "search",
            "validate",
            str(paths["search_result"]),
            "--snapshot",
            str(paths["snapshot"]),
            "--candidate-universe",
            str(paths["candidate_universe"]),
            "--native-repository",
            str(paths["native_repository"]),
            "--ground-truth",
            str(paths["ground_truth"]),
            "--singleton-profile",
            str(paths["singleton_profile"]),
            "--optimization-plan",
            str(paths["optimization_plan"]),
        ],
        paths["logs"],
    )
    _run(
        advisor
        + [
            "recommendation",
            "validate",
            str(paths["recommendation"]),
            "--snapshot",
            str(paths["snapshot"]),
            "--candidate-universe",
            str(paths["candidate_universe"]),
            "--native-repository",
            str(paths["native_repository"]),
            "--ground-truth",
            str(paths["ground_truth"]),
            "--singleton-profile",
            str(paths["singleton_profile"]),
            "--optimization-plan",
            str(paths["optimization_plan"]),
            "--search-result",
            str(paths["search_result"]),
        ],
        paths["logs"],
    )
    summary = extract_summary(layout, benchmark_id=census13.BENCHMARK_ID, workload=workload)
    native_manifest = read_json(paths["native_repository"] / "manifest.json")
    update_manifest(
        layout,
        {
            "artifacts": _artifact_records(layout),
            "native_repository_semantic_digest": native_digest,
            "simulated_production": {
                "server_version": load_result["server_version"],
                "server_version_num": load_result["server_version_num"],
                "relation": census13.RELATION,
            },
            "planner": {
                "server_version": native_manifest["backend"]["server_version"],
                "server_version_num": native_manifest["backend"]["server_version_num"],
                "backend_contract": native_manifest["backend"]["contract"],
                "reference_source_commit": native_manifest["backend"]["reference_source_commit"],
                "patched_postgres_repository": "1951123/postgresql-pgextadv",
                "patched_postgres_commit_sha": FROZEN_PATCHED_POSTGRES_SHA,
            },
            "summary": "metrics-summary.json",
            "status": "complete",
        },
    )
    return {"run_id": layout.run_id, "run_directory": str(layout.directory), "summary": summary}


def _legacy_run_forest10(
    *,
    production_dsn: str,
    planner_dsn: str,
    advisor_root: Path,
    patched_postgres_root: Path,
    output_root: Path,
    sample_rows: int = 10_000,
    sample_seed: int = 42,
    statistics_target: int = 100,
    candidate_limit: int = 8,
    search_wall_clock_seconds: float = 300.0,
    data_root: Path | None = None,
    reset_disposable: bool = False,
    advisor_command: str = "extstats-advisor",
) -> dict[str, Any]:
    """Run exactly one audited Forest10 canonical advisor experiment."""
    if not production_dsn or not planner_dsn:
        raise ValueError("both PostgreSQL DSNs are required for a canonical run")
    if sample_rows != 10_000 or sample_seed != 42 or statistics_target != 100:
        raise ValueError("Forest10 canonical settings are sample_rows=10000, seed=42, target=100")
    if candidate_limit != 8 or search_wall_clock_seconds != 300:
        raise ValueError("Forest10 canonical settings are candidate_limit=8 and wall_clock=300")
    research_root = Path(__file__).resolve().parents[2]
    research_identity = verify_research_repository(research_root)
    pins = verify_frozen_systems(advisor_root, patched_postgres_root)
    dataset = forest10.inspect(data_root)
    workload_path = Path(output_root) / "_forest10_workload.json"
    forest10.extract_workload(workload_path, data_root, "test")
    workload = read_json(workload_path)
    identity = {
        **research_identity,
        **pins,
        "benchmark_id": forest10.BENCHMARK_ID,
        "dataset_content_identity": dataset["dataset_content_identity"],
        "workload_id": workload["workload_id"],
        "workload_sha256": sha256_file(workload_path),
        "label_sha256": dataset["source_file_sha256"]["label_pickle"],
        "sample_rows": sample_rows,
        "sample_seed": sample_seed,
        "statistics_target": statistics_target,
        "candidate_limit": candidate_limit,
        "search_wall_clock_seconds": search_wall_clock_seconds,
    }
    reject_credentials(identity)
    layout = create_layout(output_root, identity)
    paths = layout.artifacts()
    paths["workload"] = layout.path("workload.json")
    paths["dataset_manifest"] = layout.path("dataset-manifest.json")
    paths["full_data_target100"] = layout.path("full-data-target100-v1.json")
    paths["workload"].write_text(workload_path.read_text(encoding="utf-8"), encoding="utf-8")
    forest10.write_dataset_manifest(paths["dataset_manifest"], data_root)

    timings: dict[str, float] = {}
    load_result = load_forest10(
        production_dsn,
        data_root=data_root,
        reset_disposable=reset_disposable,
        statistics_target=statistics_target,
    )
    timings["stock_load_initial_analyze"] = load_result["elapsed_seconds"]
    full_data = run_forest_full_data_target100(
        production_dsn,
        data_root=data_root,
        output_path=paths["full_data_target100"],
        repository=research_root,
        statistics_target=statistics_target,
    )
    timings["full_data_target100_explain_baseline"] = full_data["elapsed_seconds"]
    update_manifest(
        layout,
        {
            "dataset_manifest": str(paths["dataset_manifest"].relative_to(layout.directory)),
            "workload": str(paths["workload"].relative_to(layout.directory)),
            "full_data_target100": {
                "path": str(paths["full_data_target100"].relative_to(layout.directory)),
                "semantic_digest": full_data["semantic_digest"],
            },
            "load": load_result,
            "stage_timings": timings,
        },
    )
    advisor = [advisor_command]

    started = time.monotonic()
    _run(
        advisor
        + [
            "snapshot",
            "capture",
            "postgres",
            "--dsn",
            production_dsn,
            "--relation",
            forest10.RELATION,
            "--sample-rows",
            str(sample_rows),
            "--sample-seed",
            str(sample_seed),
            "--workload",
            str(paths["workload"]),
            "--output",
            str(paths["snapshot"]),
            "--ground-truth-output",
            str(paths["ground_truth"]),
        ],
        paths["logs"],
    )
    truth_validation = compare_production_truth_to_labels(
        paths["snapshot"],
        paths["ground_truth"],
        forest10.load_test_records(data_root),
        advisor_root=advisor_root,
    )
    timings["snapshot_capture_exact_truth"] = round(time.monotonic() - started, 6)
    sampling = sampling_provenance(paths["snapshot"], advisor_root=advisor_root)

    started = time.monotonic()
    _run(
        advisor
        + [
            "candidates",
            "derive",
            str(paths["snapshot"]),
            "--output",
            str(paths["candidate_universe"]),
        ],
        paths["logs"],
    )
    timings["candidate_derivation"] = round(time.monotonic() - started, 6)

    started = time.monotonic()
    native_digest = materialize_native_repository(
        advisor_root,
        planner_dsn,
        paths["snapshot"],
        paths["candidate_universe"],
        paths["native_repository"],
        statistics_target,
    )
    timings["native_materialization"] = round(time.monotonic() - started, 6)

    started = time.monotonic()
    _run(
        advisor
        + [
            "sandbox",
            "prepare",
            "postgres",
            str(paths["snapshot"]),
            str(paths["candidate_universe"]),
            str(paths["native_repository"]),
            "--dsn",
            planner_dsn,
        ],
        paths["logs"],
    )
    timings["sandbox_prepare_verify"] = round(time.monotonic() - started, 6)
    _run(
        advisor
        + [
            "sandbox",
            "verify",
            "postgres",
            str(paths["snapshot"]),
            str(paths["candidate_universe"]),
            str(paths["native_repository"]),
            "--dsn",
            planner_dsn,
        ],
        paths["logs"],
    )

    started = time.monotonic()
    _run(
        advisor
        + [
            "profiling",
            "singleton",
            "postgres",
            str(paths["snapshot"]),
            str(paths["candidate_universe"]),
            str(paths["native_repository"]),
            str(paths["ground_truth"]),
            "--dsn",
            planner_dsn,
            "--output",
            str(paths["singleton_profile"]),
        ],
        paths["logs"],
    )
    timings["singleton_profiling"] = round(time.monotonic() - started, 6)

    started = time.monotonic()
    _run(
        advisor
        + [
            "optimization",
            "plan",
            str(paths["snapshot"]),
            str(paths["candidate_universe"]),
            str(paths["native_repository"]),
            str(paths["ground_truth"]),
            str(paths["singleton_profile"]),
            "--candidate-limit",
            str(candidate_limit),
            "--wall-clock-seconds",
            str(search_wall_clock_seconds),
            "--output",
            str(paths["optimization_plan"]),
        ],
        paths["logs"],
    )
    timings["optimization_plan"] = round(time.monotonic() - started, 6)

    started = time.monotonic()
    _run(
        advisor
        + [
            "optimization",
            "search",
            "postgres",
            str(paths["snapshot"]),
            str(paths["candidate_universe"]),
            str(paths["native_repository"]),
            str(paths["ground_truth"]),
            str(paths["singleton_profile"]),
            str(paths["optimization_plan"]),
            "--dsn",
            planner_dsn,
            "--output",
            str(paths["search_result"]),
        ],
        paths["logs"],
    )
    timings["optimization_search"] = round(time.monotonic() - started, 6)

    for command in (
        advisor + ["snapshot", "validate", str(paths["snapshot"])],
        advisor
        + [
            "candidates",
            "validate",
            str(paths["candidate_universe"]),
            "--snapshot",
            str(paths["snapshot"]),
        ],
        advisor
        + [
            "profiling",
            "validate",
            str(paths["singleton_profile"]),
            "--snapshot",
            str(paths["snapshot"]),
            "--candidate-universe",
            str(paths["candidate_universe"]),
            "--native-repository",
            str(paths["native_repository"]),
            "--ground-truth",
            str(paths["ground_truth"]),
        ],
        advisor
        + [
            "optimization",
            "validate",
            str(paths["optimization_plan"]),
            "--snapshot",
            str(paths["snapshot"]),
            "--candidate-universe",
            str(paths["candidate_universe"]),
            "--native-repository",
            str(paths["native_repository"]),
            "--ground-truth",
            str(paths["ground_truth"]),
            "--singleton-profile",
            str(paths["singleton_profile"]),
        ],
        advisor
        + [
            "optimization",
            "search",
            "validate",
            str(paths["search_result"]),
            "--snapshot",
            str(paths["snapshot"]),
            "--candidate-universe",
            str(paths["candidate_universe"]),
            "--native-repository",
            str(paths["native_repository"]),
            "--ground-truth",
            str(paths["ground_truth"]),
            "--singleton-profile",
            str(paths["singleton_profile"]),
            "--optimization-plan",
            str(paths["optimization_plan"]),
        ],
    ):
        _run(command, paths["logs"])

    search = read_json(paths["search_result"])
    completed_reasons = {"local-optimum", "all-screened-candidates-selected"}
    if search.get("termination_reason") not in completed_reasons:
        manifest = update_manifest(
            layout,
            {
                "native_repository_semantic_digest": native_digest,
                "stage_timings": timings,
                "snapshot_sampling": sampling,
                "truth_validation": truth_validation,
                "status": "budget-incomplete",
            },
        )
        return {
            "run_id": layout.run_id,
            "run_directory": str(layout.directory),
            "status": "budget-incomplete",
            "termination_reason": search.get("termination_reason"),
            "manifest": manifest,
        }

    started = time.monotonic()
    _run(
        advisor
        + [
            "recommendation",
            "build",
            "postgres",
            str(paths["snapshot"]),
            str(paths["candidate_universe"]),
            str(paths["native_repository"]),
            str(paths["ground_truth"]),
            str(paths["singleton_profile"]),
            str(paths["optimization_plan"]),
            str(paths["search_result"]),
            "--output",
            str(paths["recommendation"]),
        ],
        paths["logs"],
    )
    _run(
        advisor
        + [
            "recommendation",
            "validate",
            str(paths["recommendation"]),
            "--snapshot",
            str(paths["snapshot"]),
            "--candidate-universe",
            str(paths["candidate_universe"]),
            "--native-repository",
            str(paths["native_repository"]),
            "--ground-truth",
            str(paths["ground_truth"]),
            "--singleton-profile",
            str(paths["singleton_profile"]),
            "--optimization-plan",
            str(paths["optimization_plan"]),
            "--search-result",
            str(paths["search_result"]),
        ],
        paths["logs"],
    )
    timings["recommendation_build_validation"] = round(time.monotonic() - started, 6)

    summary = extract_summary(layout, benchmark_id=forest10.BENCHMARK_ID, workload=workload)
    native_manifest = read_json(paths["native_repository"] / "manifest.json")
    manifest = update_manifest(
        layout,
        {
            "artifacts": _artifact_records(layout),
            "full_data_target100": {
                "path": str(paths["full_data_target100"].relative_to(layout.directory)),
                "semantic_digest": full_data["semantic_digest"],
            },
            "native_repository_semantic_digest": native_digest,
            "snapshot_sampling": sampling,
            "truth_validation": truth_validation,
            "stage_timings": timings,
            "simulated_production": {
                "server_version": load_result["server_version"],
                "server_version_num": load_result["server_version_num"],
                "relation": forest10.RELATION,
            },
            "planner": {
                "server_version": native_manifest["backend"]["server_version"],
                "server_version_num": native_manifest["backend"]["server_version_num"],
                "backend_contract": native_manifest["backend"]["contract"],
                "reference_source_commit": native_manifest["backend"]["reference_source_commit"],
                "patched_postgres_repository": "1951123/postgresql-pgextadv",
                "patched_postgres_commit_sha": FROZEN_PATCHED_POSTGRES_SHA,
            },
            "summary": "metrics-summary.json",
            "status": "complete",
        },
    )

    started = time.monotonic()
    _run(
        advisor + ["sandbox", "destroy", "postgres", "--dsn", planner_dsn],
        paths["logs"],
    )
    timings["sandbox_destroy_before_audit"] = round(time.monotonic() - started, 6)

    started = time.monotonic()
    audit_result = run_audit(
        layout.directory,
        planner_dsn=planner_dsn,
        advisor_root=advisor_root,
        patched_postgres_root=patched_postgres_root,
    )
    timings["audit_replay"] = round(time.monotonic() - started, 6)
    audit_artifact = read_json(Path(audit_result["audit_path"]))
    manifest = update_manifest(layout, {"audit": audit_result, "stage_timings": timings})

    paper_baseline = read_json(research_root / "paper-baselines/arecel-forest10/postgres-v1.json")
    evidence_directory = research_root / "experiments/arecel-forest10/canonical-k8"
    if evidence_directory.exists() and any(evidence_directory.iterdir()):
        raise FileExistsError(f"Forest compact evidence already exists: {evidence_directory}")
    evidence_directory.mkdir(parents=True, exist_ok=True)
    full_data_evidence = evidence_directory / "full-data-target100-v1.json"
    write_json(full_data_evidence, read_json(paths["full_data_target100"]))
    compact = compact_summary(
        run_id=layout.run_id,
        run_directory=str(layout.directory.relative_to(research_root)),
        manifest=manifest,
        full_data=read_json(paths["full_data_target100"]),
        paper_baseline=paper_baseline,
        candidate_universe=read_json(paths["candidate_universe"]),
        native_repository=native_manifest,
        singleton_profile=read_json(paths["singleton_profile"]),
        optimization_plan=read_json(paths["optimization_plan"]),
        search_result=search,
        recommendation=read_json(paths["recommendation"]),
        truth_validation=truth_validation,
        sampling=sampling,
        stage_timings=timings,
        audit=audit_artifact,
    )
    write_json(evidence_directory / "canonical-k8-summary-v1.json", compact)
    update_manifest(
        layout,
        {
            "stage_timings": timings,
            "compact_evidence": {
                "directory": str(evidence_directory.relative_to(research_root)),
                "summary_semantic_digest": compact["semantic_digest"],
            },
        },
    )
    return {
        "run_id": layout.run_id,
        "run_directory": str(layout.directory),
        "status": "complete",
        "summary": summary,
        "audit": audit_result,
        "compact_evidence": {
            "directory": str(evidence_directory),
            "semantic_digest": compact["semantic_digest"],
        },
    }


def run_forest10(
    *,
    production_dsn: str,
    planner_dsn: str,
    advisor_root: Path,
    patched_postgres_root: Path,
    output_root: Path,
    sample_rows: int = 10_000,
    sample_seed: int = 42,
    statistics_target: int = 100,
    candidate_limit: int = 8,
    search_wall_clock_seconds: float = 300.0,
    data_root: Path | None = None,
    reset_disposable: bool = False,
    advisor_command: str = "extstats-advisor",
) -> dict[str, Any]:
    """Run the shared canonical engine with the historical Forest10 settings."""
    return _shared_run_forest10(
        production_dsn=production_dsn,
        planner_dsn=planner_dsn,
        advisor_root=advisor_root,
        patched_postgres_root=patched_postgres_root,
        output_root=output_root,
        sample_rows=sample_rows,
        sample_seed=sample_seed,
        statistics_target=statistics_target,
        candidate_limit=candidate_limit,
        search_wall_clock_seconds=search_wall_clock_seconds,
        data_root=data_root,
        reset_disposable=reset_disposable,
        advisor_command=advisor_command,
    )


def run_power7(
    *,
    production_dsn: str,
    planner_dsn: str,
    advisor_root: Path,
    patched_postgres_root: Path,
    output_root: Path,
    sample_rows: int = 10_000,
    sample_seed: int = 42,
    statistics_target: int = 100,
    candidate_limit: int = 8,
    search_wall_clock_seconds: float = 300.0,
    data_root: Path | None = None,
    reset_disposable: bool = False,
    advisor_command: str = "extstats-advisor",
) -> dict[str, Any]:
    """Run exactly one audited Power7 canonical advisor experiment."""
    return _shared_run_power7(
        production_dsn=production_dsn,
        planner_dsn=planner_dsn,
        advisor_root=advisor_root,
        patched_postgres_root=patched_postgres_root,
        output_root=output_root,
        sample_rows=sample_rows,
        sample_seed=sample_seed,
        statistics_target=statistics_target,
        candidate_limit=candidate_limit,
        search_wall_clock_seconds=search_wall_clock_seconds,
        data_root=data_root,
        reset_disposable=reset_disposable,
        advisor_command=advisor_command,
    )


def run_dmv11(
    *,
    production_dsn: str,
    planner_dsn: str,
    advisor_root: Path,
    patched_postgres_root: Path,
    output_root: Path,
    sample_rows: int = 10_000,
    sample_seed: int = 42,
    statistics_target: int = 100,
    candidate_limit: int = 8,
    search_wall_clock_seconds: float = 300.0,
    data_root: Path | None = None,
    reset_disposable: bool = False,
    advisor_command: str = "extstats-advisor",
) -> dict[str, Any]:
    """Run exactly one DMV11 K=8 canonical external-truth experiment."""
    return _shared_run_dmv11(
        production_dsn=production_dsn,
        planner_dsn=planner_dsn,
        advisor_root=advisor_root,
        patched_postgres_root=patched_postgres_root,
        output_root=output_root,
        sample_rows=sample_rows,
        sample_seed=sample_seed,
        statistics_target=statistics_target,
        candidate_limit=candidate_limit,
        search_wall_clock_seconds=search_wall_clock_seconds,
        data_root=data_root,
        reset_disposable=reset_disposable,
        advisor_command=advisor_command,
    )
