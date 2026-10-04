"""Canonical end-to-end run orchestration around the frozen advisor CLI."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from . import FROZEN_ADVISOR_SHA, FROZEN_PATCHED_POSTGRES_SHA
from .advisor_bridge import materialize_native_repository
from .analysis.summary import extract_summary
from .datasets import census13
from .pins import verify_frozen_systems
from .postgres.loader import load_census13
from .provenance import read_json, reject_credentials, sha256_file
from .runs.layout import RunLayout, create_layout, update_manifest


def _run(command: list[str], log_path: Path) -> dict[str, Any]:
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
    advisor_command: str = "extstats-advisor",
) -> dict[str, Any]:
    if not production_dsn or not planner_dsn:
        raise ValueError("both PostgreSQL DSNs are required for a canonical run")
    pins = verify_frozen_systems(advisor_root, patched_postgres_root)
    dataset = census13.inspect(data_root)
    workload_path = Path(output_root) / "_workload.json"
    census13.extract_workload(workload_path, data_root, "test")
    workload = read_json(workload_path)
    identity = {
        "benchmark_id": census13.BENCHMARK_ID,
        "dataset_content_identity": dataset.get(
            "dataset_content_identity", dataset.get("csv_sha256")
        ),
        "workload_id": workload["workload_id"],
        "workload_sha256": sha256_file(workload_path),
        "advisor_sha": FROZEN_ADVISOR_SHA,
        "patched_postgres_sha": FROZEN_PATCHED_POSTGRES_SHA,
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
    paths["workload"].write_text(workload_path.read_text(encoding="utf-8"), encoding="utf-8")
    census13.write_dataset_manifest(paths["dataset_manifest"], data_root)
    load_result = load_census13(
        production_dsn, data_root=data_root, reset_disposable=reset_disposable
    )
    update_manifest(
        layout,
        {
            "dataset_manifest": str(paths["dataset_manifest"].relative_to(layout.directory)),
            "workload": str(paths["workload"].relative_to(layout.directory)),
            "load": load_result,
            "pins": pins,
        },
    )
    advisor = [advisor_command]
    _run(
        advisor
        + [
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
            "--ground-truth-output",
            str(paths["ground_truth"]),
        ],
        paths["logs"],
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
                "patched_postgres_sha": FROZEN_PATCHED_POSTGRES_SHA,
            },
            "summary": "metrics-summary.json",
            "status": "complete",
        },
    )
    return {"run_id": layout.run_id, "run_directory": str(layout.directory), "summary": summary}
