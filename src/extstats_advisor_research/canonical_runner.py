"""Shared canonical advisor experiment engine for audited AreCEL datasets."""

from __future__ import annotations

import json
import math
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any

from . import FROZEN_PATCHED_POSTGRES_SHA
from .advisor_bridge import materialize_native_repository
from .analysis.audit import contribution_summary, run_audit
from .analysis.summary import extract_summary
from .arecel_truth import authoritative_truth_spec, validate_observation_wire
from .external_truth import import_audited_authoritative_truth, write_authoritative_observations
from .forest_baseline import _run_full_data_target100, representative_indices
from .forest_canonical import (
    compact_summary,
    compare_authoritative_truth_to_labels,
    compare_production_truth_to_labels,
    sampling_provenance,
)
from .pins import verify_frozen_systems, verify_research_repository
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


def _qerror_contribution(records: list[dict[str, Any]], field: str) -> dict[str, Any]:
    return contribution_summary(
        [
            {"weight": float(item["weight"]), "baseline_qerror": float(item[field])}
            for item in records
        ]
    )


def _paper_tail_contribution(directory: Path, artifact: dict[str, Any]) -> dict[str, Any]:
    records = []
    path = directory / artifact["per_query_path"]
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            value = json.loads(line)
            records.append({"weight": value["weight"], "qerror": value["qerror"]})
    result = _qerror_contribution(records, "qerror")
    result["max_query_id"] = artifact["summary"]["max_query_id"]
    return result


def _baseline_correspondence(
    full_records: list[dict[str, Any]], audit_records: list[dict[str, Any]]
) -> dict[str, Any]:
    full = {item["query_id"]: item for item in full_records}
    sandbox = {item["query_id"]: item for item in audit_records}
    if set(full) != set(sandbox):
        raise ValueError("full-data and sandbox baseline query IDs differ")
    directions = Counter()
    joined = []
    for query_id in sorted(full):
        ordinary = full[query_id]
        sampled = sandbox[query_id]
        ordinary_qerror = float(ordinary["qerror"])
        sandbox_qerror = float(sampled["baseline_qerror"])
        directions[
            "lower"
            if sandbox_qerror < ordinary_qerror
            else "higher"
            if sandbox_qerror > ordinary_qerror
            else "equal"
        ] += 1
        joined.append(
            {
                "query_id": query_id,
                "truth": ordinary["true_rows"],
                "full_data_target100": {
                    "estimate": ordinary["estimated_rows"],
                    "qerror": ordinary_qerror,
                },
                "sandbox_baseline": {
                    "estimate": sampled["baseline_estimated_rows"],
                    "qerror": sandbox_qerror,
                },
                "sandbox_final": {
                    "estimate": sampled["final_estimated_rows"],
                    "qerror": float(sampled["final_qerror"]),
                },
            }
        )

    def distribution(field: str) -> dict[str, float]:
        values = [float(item[field]) for item in joined]
        ordered = sorted(values)

        def percentile(fraction: float) -> float:
            position = (len(ordered) - 1) * fraction
            lower = math.floor(position)
            upper = math.ceil(position)
            if lower == upper:
                return ordered[lower]
            return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)

        return {
            "mean": sum(values) / len(values),
            "p50": percentile(0.50),
            "p95": percentile(0.95),
            "p99": percentile(0.99),
            "max": max(values),
        }

    top_full = sorted(joined, key=lambda item: item["full_data_target100"]["qerror"], reverse=True)[
        :10
    ]
    return {
        "full_data_target100_vs_sandbox_baseline": {
            "full_data_target100": distribution("full_data_target100_qerror"),
            "sandbox_baseline": distribution("sandbox_baseline_qerror"),
        },
        "sandbox_baseline_direction": dict(sorted(directions.items())),
        "top_10_full_data_target100_queries": top_full,
    }


def _correspondence_records(
    full_records: list[dict[str, Any]], audit_records: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    full = {item["query_id"]: item for item in full_records}
    sandbox = {item["query_id"]: item for item in audit_records}
    if set(full) != set(sandbox):
        raise ValueError("full-data and sandbox baseline query IDs differ")
    joined = []
    directions = Counter()
    for query_id in sorted(full):
        ordinary = full[query_id]
        sampled = sandbox[query_id]
        ordinary_qerror = float(ordinary["qerror"])
        sandbox_qerror = float(sampled["baseline_qerror"])
        direction = (
            "lower"
            if sandbox_qerror < ordinary_qerror
            else "higher"
            if sandbox_qerror > ordinary_qerror
            else "equal"
        )
        directions[direction] += 1
        joined.append(
            {
                "query_id": query_id,
                "truth": ordinary["true_rows"],
                "full_data_target100": {
                    "estimate": ordinary["estimated_rows"],
                    "qerror": ordinary_qerror,
                },
                "sandbox_baseline": {
                    "estimate": sampled["baseline_estimated_rows"],
                    "qerror": sandbox_qerror,
                },
                "sandbox_final": {
                    "estimate": sampled["final_estimated_rows"],
                    "qerror": float(sampled["final_qerror"]),
                },
            }
        )

    def distribution(key: str) -> dict[str, float]:
        values = sorted(item[key]["qerror"] for item in joined)

        def percentile(fraction: float) -> float:
            position = (len(values) - 1) * fraction
            lower = math.floor(position)
            upper = math.ceil(position)
            if lower == upper:
                return values[lower]
            return values[lower] + (values[upper] - values[lower]) * (position - lower)

        return {
            "mean": sum(values) / len(values),
            "p50": percentile(0.50),
            "p95": percentile(0.95),
            "p99": percentile(0.99),
            "max": max(values),
        }

    return joined, {
        "full_data_target100_vs_sandbox_baseline": {
            "full_data_target100": distribution("full_data_target100"),
            "sandbox_baseline": distribution("sandbox_baseline"),
        },
        "sandbox_baseline_direction": dict(sorted(directions.items())),
        "top_10_full_data_target100_queries": sorted(
            joined, key=lambda item: item["full_data_target100"]["qerror"], reverse=True
        )[:10],
    }


def _verify_sandbox(
    verify_result: dict[str, Any], *, rows: int, population: float, candidates: int
) -> None:
    metadata = verify_result.get("metadata", {})
    # The frozen advisor CLI publishes the sandbox check fields at the top
    # level; keep accepting a nested mapping for test doubles and older runs.
    checks = verify_result.get("checks", verify_result)
    if metadata.get("sample_row_count") != rows:
        raise ValueError(f"sandbox sample row count mismatch: {metadata.get('sample_row_count')}")
    if not math.isclose(
        float(metadata.get("population_row_count", 0)), population, rel_tol=1e-6, abs_tol=1.0
    ):
        raise ValueError(
            f"sandbox population row count mismatch: {metadata.get('population_row_count')}"
        )
    repository = verify_result.get("repository", {})
    repository_candidate_count = metadata.get(
        "repository_candidate_count", repository.get("candidate_count")
    )
    if repository_candidate_count != candidates:
        raise ValueError("sandbox repository candidate count mismatch")
    if (
        checks.get("target_sample_row_count") != rows
        or checks.get("frozen_sample_row_count") != rows
    ):
        raise ValueError("sandbox physical sample row count mismatch")
    if checks.get("physical_extstats_count") != 0:
        raise ValueError("sandbox contains unrelated physical extended statistics")


def _run_external_truth_sanity_check(
    dsn: str, records: list[dict[str, Any]], *, expected_count: int
) -> dict[str, Any]:
    """Recheck a deterministic small subset without making it the truth source."""
    import psycopg

    indices = representative_indices(records)
    if len(indices) != expected_count:
        raise ValueError(
            f"authoritative truth sanity subset expected {expected_count} checks, got {len(indices)}"
        )
    by_index = {record["source_index"]: record for record in records}
    checks = []
    with psycopg.connect(dsn, autocommit=True) as connection, connection.cursor() as cursor:
        for index in indices:
            record = by_index[index]
            cursor.execute(
                "SELECT count(*) FROM ("
                + record["sql"].rstrip("; ")
                + ") AS authoritative_truth_sanity"
            )
            observed = int(cursor.fetchone()[0])
            if observed != record["truth"]:
                raise ValueError(
                    f"authoritative truth sanity mismatch for {record['query_id']}: "
                    f"observed={observed}, audited={record['truth']}"
                )
            checks.append(
                {
                    "source_index": index,
                    "query_id": record["query_id"],
                    "audited_truth": record["truth"],
                    "observed": observed,
                    "predicate_arity": len(record["source_query"]["predicates"]),
                    "operators": sorted(
                        {item["operator"] for item in record["source_query"]["predicates"]}
                    ),
                }
            )
    return {
        "method": "fresh-stock-live-exact-subset-v1",
        "source_for_ground_truth_set": False,
        "count": len(checks),
        "all_match": True,
        "checks": checks,
    }


def _snapshot_capture_command(
    advisor_command: str,
    *,
    dsn: str,
    relation: str,
    sample_rows: int,
    sample_seed: int,
    workload: Path,
    output: Path,
    ground_truth_output: Path | None = None,
) -> list[str]:
    command = [
        advisor_command,
        "snapshot",
        "capture",
        "postgres",
        "--dsn",
        dsn,
        "--relation",
        relation,
        "--sample-rows",
        str(sample_rows),
        "--sample-seed",
        str(sample_seed),
        "--workload",
        str(workload),
        "--output",
        str(output),
    ]
    if ground_truth_output is not None:
        command.extend(["--ground-truth-output", str(ground_truth_output)])
    return command


def _validate_candidate_universe_contract(
    candidate_universe: dict[str, Any], *, expected_count: int | None = None
) -> int:
    candidates = candidate_universe.get("candidates", [])
    count = len(candidates)
    if expected_count is not None and count != expected_count:
        raise ValueError(f"expected {expected_count} candidates, got {count}")
    if expected_count == 110:
        pair_kinds: dict[tuple[str, ...], set[str]] = {}
        for candidate in candidates:
            pair = tuple(candidate.get("column_names", []))
            pair_kinds.setdefault(pair, set()).add(candidate.get("kind"))
        if len(pair_kinds) != 55 or any(
            kinds != {"postgresql.mcv", "postgresql.dependencies"} for kinds in pair_kinds.values()
        ):
            raise ValueError("DMV11 candidates are not exactly 55 pairs times two statistic kinds")
    return count


def _run_canonical(
    *,
    dataset: Any,
    loader: Any,
    full_format_version: str,
    compact_format_version: str,
    production_dsn: str,
    planner_dsn: str,
    advisor_root: Path,
    patched_postgres_root: Path,
    output_root: Path,
    sample_rows: int,
    sample_seed: int,
    statistics_target: int,
    candidate_limit: int,
    search_wall_clock_seconds: float,
    data_root: Path | None,
    reset_disposable: bool,
    advisor_command: str,
    expected_candidate_count: int | None = None,
    authoritative_truth: dict[str, Any] | None = None,
    seed_identifier: int | None = None,
    compact_evidence_directory: Path | None = None,
) -> dict[str, Any]:
    if not production_dsn or not planner_dsn:
        raise ValueError("both PostgreSQL DSNs are required for a canonical run")
    if sample_rows != 10_000 or sample_seed != 42 or statistics_target != 100:
        raise ValueError("canonical settings are sample_rows=10000, seed=42, target=100")
    if candidate_limit != 8 or search_wall_clock_seconds != 300:
        raise ValueError("canonical settings are candidate_limit=8 and wall_clock=300")

    research_root = Path(__file__).resolve().parents[2]
    research_identity = verify_research_repository(research_root)
    pins = verify_frozen_systems(advisor_root, patched_postgres_root)
    dataset_metadata = dataset.inspect(data_root)
    slug = dataset.BENCHMARK_ID.removeprefix("arecel-")
    workload_path = Path(output_root) / f"_{slug}_workload.json"
    dataset.extract_workload(workload_path, data_root, "test")
    workload = read_json(workload_path)
    for query in workload["queries"]:
        if set(query) != {"query_id", "sql", "weight"}:
            raise ValueError("production workload projection contains research-only query fields")
    identity = {
        **research_identity,
        **pins,
        "benchmark_id": dataset.BENCHMARK_ID,
        "dataset_content_identity": dataset_metadata["dataset_content_identity"],
        "schema_contract_id": dataset.SCHEMA_CONTRACT_ID,
        "row_count": dataset.EXPECTED_ROWS,
        "source_hashes": dataset_metadata["source_file_sha256"],
        "archive_sha256": dataset_metadata["archive_sha256"],
        "upstream_commit": dataset_metadata["upstream_commit"],
        "workload_id": workload["workload_id"],
        "workload_sha256": sha256_file(workload_path),
        "label_sha256": dataset_metadata["source_file_sha256"]["label_pickle"],
        "sample_rows": sample_rows,
        "sample_seed": sample_seed,
        "statistics_target": statistics_target,
        "candidate_limit": candidate_limit,
        "search_wall_clock_seconds": search_wall_clock_seconds,
    }
    if authoritative_truth is not None:
        identity["truth_source"] = {
            "kind": "authoritative-external-exact",
            "collection_contract": "authoritative-external-exact-cardinality-v1",
            "authority": authoritative_truth["authority"],
            "dataset_identity": authoritative_truth["dataset_identity"],
            "source_revision": authoritative_truth["source_revision"],
        }
    reject_credentials(identity)
    layout = create_layout(output_root, identity)
    paths = layout.artifacts()
    paths["workload"] = layout.path("workload.json")
    paths["dataset_manifest"] = layout.path("dataset-manifest.json")
    paths["full_data_target100"] = layout.path("full-data-target100-v1.json")
    paths["workload"].write_text(workload_path.read_text(encoding="utf-8"), encoding="utf-8")
    dataset.write_dataset_manifest(paths["dataset_manifest"], data_root)

    timings: dict[str, float] = {}
    load_result = loader(
        production_dsn,
        data_root=data_root,
        reset_disposable=reset_disposable,
        statistics_target=statistics_target,
        seed_identifier=seed_identifier,
    )
    if (
        load_result["rows"] != dataset.EXPECTED_ROWS
        or load_result["statistics_target"] != statistics_target
        or load_result.get("analyze_count") != 1
        or load_result.get("physical_extended_statistics_count") != 0
    ):
        raise ValueError("fresh stock load does not satisfy the canonical physical contract")
    timings["stock_load_initial_analyze"] = load_result["elapsed_seconds"]
    full_data = _run_full_data_target100(
        production_dsn,
        data_root=data_root,
        output_path=paths["full_data_target100"],
        repository=research_root,
        statistics_target=statistics_target,
        dataset=dataset,
        format_version=full_format_version,
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
    snapshot_command = _snapshot_capture_command(
        advisor_command,
        dsn=production_dsn,
        relation=dataset.RELATION,
        sample_rows=sample_rows,
        sample_seed=sample_seed,
        workload=paths["workload"],
        output=paths["snapshot"],
        ground_truth_output=paths["ground_truth"] if authoritative_truth is None else None,
    )
    _run(snapshot_command, paths["logs"])
    records = dataset.load_test_records(data_root)
    if authoritative_truth is None:
        truth_validation = compare_production_truth_to_labels(
            paths["snapshot"],
            paths["ground_truth"],
            records,
            advisor_root=advisor_root,
        )
        timings["snapshot_capture_exact_truth"] = round(time.monotonic() - started, 6)
        truth_capture = {
            "mode": "production-exact-execution",
            "elapsed_seconds": timings["snapshot_capture_exact_truth"],
            "rows_query_count_product": dataset.EXPECTED_ROWS * len(records),
            "queries_per_second": len(records) / timings["snapshot_capture_exact_truth"],
        }
    else:
        timings["snapshot_capture"] = round(time.monotonic() - started, 6)
        sanity_started = time.monotonic()
        sanity = _run_external_truth_sanity_check(
            production_dsn,
            records,
            expected_count=int(authoritative_truth["sanity_check_count"]),
        )
        timings["truth_sanity_check"] = round(time.monotonic() - sanity_started, 6)
        observations_started = time.monotonic()
        observations_path = layout.path("authoritative-observations-v1.json")
        source_observations = authoritative_truth.get("observations_path")
        if source_observations is not None:
            source_observations = Path(source_observations)
            validate_observation_wire(
                read_json(source_observations), workload_id=workload["workload_id"]
            )
            observations_path.write_bytes(source_observations.read_bytes())
        else:
            write_authoritative_observations(
                workload["workload_id"],
                {record["query_id"]: int(record["truth"]) for record in records},
                observations_path,
            )
        external = import_audited_authoritative_truth(
            paths["snapshot"],
            observations_path,
            authority=str(authoritative_truth["authority"]),
            dataset_identity=str(authoritative_truth["dataset_identity"]),
            source_revision=str(authoritative_truth["source_revision"]),
        )
        import sys

        sys.path.insert(0, str(advisor_root / "src"))
        from extstats_advisor.ground_truth.artifact import write_ground_truth_set

        write_ground_truth_set(external, paths["ground_truth"])
        timings["authoritative_observations_and_import"] = round(
            time.monotonic() - observations_started, 6
        )
        truth_validation = compare_authoritative_truth_to_labels(
            paths["snapshot"],
            paths["ground_truth"],
            observations_path,
            records,
            advisor_root=advisor_root,
            authority=str(authoritative_truth["authority"]),
            dataset_identity=str(authoritative_truth["dataset_identity"]),
            source_revision=str(authoritative_truth["source_revision"]),
        )
        truth_validation["sanity_check"] = sanity
        truth_capture = {
            "mode": "authoritative-external-exact",
            "observations_format_version": "authoritative-cardinality-observations-v1",
            "observations_path": str(observations_path.relative_to(layout.directory)),
            "observations_sha256": truth_validation["source_artifact_sha256"],
            "observations_query_count": len(records),
            "snapshot_capture_elapsed_seconds": timings["snapshot_capture"],
            "sanity_check_elapsed_seconds": timings["truth_sanity_check"],
            "observations_and_import_elapsed_seconds": timings[
                "authoritative_observations_and_import"
            ],
            "sanity_check": sanity,
        }
    sampling = sampling_provenance(
        paths["snapshot"],
        advisor_root=advisor_root,
        expected_rows=sample_rows,
        expected_seed=sample_seed,
    )
    import sys

    sys.path.insert(0, str(advisor_root / "src"))
    from extstats_advisor.snapshot.bundle import load_snapshot

    sealed_snapshot = load_snapshot(paths["snapshot"])
    if len(sealed_snapshot.populations) != 1:
        raise ValueError("canonical snapshot must contain exactly one population")
    expected_population = float(sealed_snapshot.populations[0].row_count)

    started = time.monotonic()
    candidate_result = _run(
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
    candidate_universe = read_json(paths["candidate_universe"])
    candidate_count = _validate_candidate_universe_contract(
        candidate_universe, expected_count=expected_candidate_count
    )

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
    native_manifest = read_json(paths["native_repository"] / "manifest.json")
    if native_manifest["materialization"]["sample_row_count"] != sample_rows:
        raise ValueError("native repository sample row count mismatch")
    if not math.isclose(
        native_manifest["materialization"]["population_row_count"],
        expected_population,
        rel_tol=1e-6,
        abs_tol=1.0,
    ):
        raise ValueError("native repository population row count mismatch")

    sandbox_active = False
    try:
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
        sandbox_active = True
        verify_result = _run(
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
        _verify_sandbox(
            verify_result,
            rows=sample_rows,
            population=expected_population,
            candidates=candidate_count,
        )
        timings["sandbox_prepare_verify"] = round(time.monotonic() - started, 6)

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
            _run(advisor + ["sandbox", "destroy", "postgres", "--dsn", planner_dsn], paths["logs"])
            sandbox_active = False
            manifest = update_manifest(
                layout,
                {
                    "candidate_summary": {
                        "observed_count": candidate_result.get("candidate_count"),
                        "incidence_count": candidate_result.get("incidence_count"),
                        "relevant_group_count": candidate_result.get("relevant_group_count"),
                    },
                    "native_repository_semantic_digest": native_digest,
                    "stage_timings": timings,
                    "snapshot_sampling": sampling,
                    "truth_validation": truth_validation,
                    "truth_capture": truth_capture,
                    "status": "budget-incomplete",
                    "termination_reason": search.get("termination_reason"),
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
    except BaseException:
        if sandbox_active:
            try:
                _run(
                    advisor + ["sandbox", "destroy", "postgres", "--dsn", planner_dsn],
                    paths["logs"],
                )
            except RuntimeError as cleanup_error:
                with paths["logs"].open("a", encoding="utf-8") as stream:
                    stream.write(
                        json.dumps({"cleanup_error": str(cleanup_error)}, sort_keys=True) + "\n"
                    )
        raise

    summary = extract_summary(layout, benchmark_id=dataset.BENCHMARK_ID, workload=workload)
    manifest = update_manifest(
        layout,
        {
            "artifacts": _artifact_records(layout),
            "candidate_summary": {
                "observed_count": candidate_result.get("candidate_count"),
                "incidence_count": candidate_result.get("incidence_count"),
                "relevant_group_count": candidate_result.get("relevant_group_count"),
            },
            "full_data_target100": {
                "path": str(paths["full_data_target100"].relative_to(layout.directory)),
                "semantic_digest": full_data["semantic_digest"],
            },
            "native_repository_semantic_digest": native_digest,
            "snapshot_sampling": sampling,
            "truth_validation": truth_validation,
            "truth_capture": truth_capture,
            "stage_timings": timings,
            "simulated_production": {
                "server_version": load_result["server_version"],
                "server_version_num": load_result["server_version_num"],
                "relation": dataset.RELATION,
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
    _run(advisor + ["sandbox", "destroy", "postgres", "--dsn", planner_dsn], paths["logs"])
    sandbox_active = False
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

    paper_directory = research_root / f"paper-baselines/{dataset.BENCHMARK_ID}"
    paper_baseline = read_json(paper_directory / "postgres-v1.json")
    paper_tail = _paper_tail_contribution(paper_directory, paper_baseline)
    audit_records = [
        json.loads(line) for line in Path(audit_result["per_query_path"]).read_text().splitlines()
    ]
    correspondence_records, correspondence = _correspondence_records(
        full_data["measured"], audit_records
    )
    focus_id = paper_baseline["summary"]["max_query_id"]
    tail_focus_query = next(item for item in correspondence_records if item["query_id"] == focus_id)
    paper_focus = None
    with (paper_directory / paper_baseline["per_query_path"]).open(encoding="utf-8") as stream:
        for line in stream:
            value = json.loads(line)
            if value["query_id"] == focus_id:
                paper_focus = value
                break
    if paper_focus is None:
        raise ValueError(f"paper baseline is missing focus query: {focus_id}")
    tail_focus_query["paper_target10000"] = {
        "estimate": paper_focus["estimated_rows"],
        "qerror": paper_focus["qerror"],
        "truth": paper_focus["true_rows"],
    }
    evidence_directory = compact_evidence_directory or (
        research_root / f"experiments/{dataset.BENCHMARK_ID}/canonical-k8"
    )
    if evidence_directory.exists() and any(evidence_directory.iterdir()):
        raise FileExistsError(f"compact evidence already exists: {evidence_directory}")
    evidence_directory.mkdir(parents=True, exist_ok=True)
    write_json(
        evidence_directory / "full-data-target100-v1.json", read_json(paths["full_data_target100"])
    )
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
        benchmark_id=dataset.BENCHMARK_ID,
        schema_contract_id=dataset.SCHEMA_CONTRACT_ID,
        row_count=dataset.EXPECTED_ROWS,
        format_version=compact_format_version,
        paper_tail_contribution=paper_tail,
        baseline_correspondence=correspondence,
        expected_candidate_count=expected_candidate_count
        if expected_candidate_count is not None
        else 42
        if dataset.BENCHMARK_ID == "arecel-power7"
        else None,
        tail_focus_query=tail_focus_query,
        truth_capture=truth_capture,
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


def run_forest10(**kwargs: Any) -> dict[str, Any]:
    from .datasets import forest10
    from .postgres.loader import load_forest10

    return _run_canonical(
        dataset=forest10,
        loader=load_forest10,
        full_format_version="arecel-forest-full-data-target100-v1",
        compact_format_version="arecel-forest-canonical-k8-summary-v1",
        authoritative_truth=authoritative_truth_spec("arecel-forest10"),
        seed_identifier=kwargs.pop("seed_identifier", None),
        compact_evidence_directory=kwargs.pop("compact_evidence_directory", None),
        **kwargs,
    )


def run_power7(**kwargs: Any) -> dict[str, Any]:
    from .datasets import power7
    from .postgres.loader import load_power7

    return _run_canonical(
        dataset=power7,
        loader=load_power7,
        full_format_version="arecel-power7-full-data-target100-v1",
        compact_format_version="arecel-power7-canonical-k8-summary-v1",
        authoritative_truth=authoritative_truth_spec("arecel-power7"),
        seed_identifier=kwargs.pop("seed_identifier", None),
        compact_evidence_directory=kwargs.pop("compact_evidence_directory", None),
        **kwargs,
    )


def run_dmv11(**kwargs: Any) -> dict[str, Any]:
    from .datasets import dmv11
    from .postgres.loader import load_dmv11

    return _run_canonical(
        dataset=dmv11,
        loader=load_dmv11,
        full_format_version="arecel-dmv11-full-data-target100-v1",
        compact_format_version="arecel-dmv11-canonical-k8-summary-v1",
        expected_candidate_count=110,
        authoritative_truth=authoritative_truth_spec("arecel-dmv11"),
        seed_identifier=kwargs.pop("seed_identifier", None),
        compact_evidence_directory=kwargs.pop("compact_evidence_directory", None),
        **kwargs,
    )
