"""Offline contracts and artifact preparation for RQ5 what-if cost v3.

The v3 comparison deliberately separates the complete Forest10 population used
by the stock arm from the fixed 30,000-row Advisor snapshot used by the
catalogless arm.  This module performs no PostgreSQL I/O.  Snapshot and
candidate-universe creation use the pinned Advisor serialization contracts and
are intended to run before a separately authorized live benchmark.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .datasets import forest10
from .provenance import read_json, semantic_digest, sha256_file
from .rq5_whatif_cost import (
    ADVISOR_SHA_V1,
    ANALYSIS_A_CHECKPOINTS,
    ANALYSIS_B_SIZES,
    PATCHED_POSTGRES_SHA,
    RQ4_ABLATION_DIGEST,
    RQ4_ABLATION_PATH,
    RQ4_DESIGN_DIGEST,
    RQ4_DESIGN_PATH,
    STOCK_POSTGRES_SHA,
    WhatIfCostError,
    _body,
    _commit,
    _sha,
)

V3_PROTOCOL_FORMAT = "rq5-whatif-evaluation-cost-v3"
V3_PROTOCOL_PATH = "docs/protocols/rq5-whatif-evaluation-cost-v3.md"
V3_MANIFEST_FORMAT = "rq5-whatif-evaluation-cost-manifest-v3"
V3_EXPERIMENT_ID = "rq5-whatif-evaluation-cost-v3-forest10"
V3_SAMPLE_ROWS = 30_000
V3_SAMPLE_SEED = 42
V3_POPULATION_ROWS = forest10.EXPECTED_ROWS
V3_QUERY_SUBSET_SIZE = 128
V3_CANDIDATE_COUNT = 57
V3_SAMPLE_SELECTION = "sha256-row-rank-v1"
V3_RELATION_ID = "rel_96c2e4f82f8ed10b05330552"
V3_SAMPLE_RUNTIME_PATH = ".runtime/rq5-whatif-evaluation-cost-v3/forest10-30000"
# This digest is the immutable membership binding used by the v3 manifests.
V3_SAMPLE_INDEX_DIGEST = "d3ae114d5b18d71bd28d95199ff32989a3623d3a29ff7f8ef59ab82d79b99a0b"


@dataclass(frozen=True, slots=True)
class V3SampleAcquisition:
    """Runtime sample material produced inside the primary timing boundary."""

    source: dict[str, Any]
    indices: tuple[int, ...]
    rows: tuple[tuple[float | None, ...], ...]
    recipe: dict[str, Any]
    index_digest: str


def _v3_require(condition: bool, message: str) -> None:
    if not condition:
        raise WhatIfCostError(message)


def validate_full_forest10_source(data_root: Path | None = None) -> dict[str, Any]:
    """Validate the audited full Forest10 source without opening a database."""

    metadata = forest10.inspect(data_root)
    _v3_require(
        metadata["observed_rows"] == V3_POPULATION_ROWS, "Forest10 population row count drifted"
    )
    _v3_require(
        metadata["dataset_content_identity"],
        "Forest10 source content identity is missing",
    )
    return {
        "benchmark_id": metadata["benchmark_id"],
        "relation": metadata["relation"],
        "schema_contract_id": metadata["schema_contract"]["id"],
        "population_rows": metadata["observed_rows"],
        "dataset_content_identity": metadata["dataset_content_identity"],
        "source_files": metadata["source_file_sha256"],
        "archive_sha256": metadata["archive_sha256"],
        "upstream_commit": metadata["upstream_commit"],
    }


def _row_rank(seed: int, source_index: int) -> bytes:
    return hashlib.sha256(f"{seed}:{source_index}".encode("ascii")).digest()


def derive_sample_indices(
    population_rows: int = V3_POPULATION_ROWS,
    sample_rows: int = V3_SAMPLE_ROWS,
    seed: int = V3_SAMPLE_SEED,
) -> list[int]:
    """Select rows by seeded SHA-256 rank, then restore source-row order."""

    if population_rows < 1 or sample_rows < 1 or sample_rows > population_rows:
        raise WhatIfCostError("invalid Forest10 population/sample sizes")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise WhatIfCostError("sample seed must be an integer")
    ranked = sorted((_row_rank(seed, index), index) for index in range(population_rows))
    return sorted(index for _, index in ranked[:sample_rows])


def sample_recipe(
    *,
    population_rows: int = V3_POPULATION_ROWS,
    sample_rows: int = V3_SAMPLE_ROWS,
    seed: int = V3_SAMPLE_SEED,
) -> dict[str, Any]:
    recipe = {
        "algorithm": V3_SAMPLE_SELECTION,
        "seed": seed,
        "population_rows": population_rows,
        "sample_rows": sample_rows,
        "source_index_base": 0,
        "output_order": "ascending-source-row-index",
        "row_identity": "source-csv-data-row-index",
    }
    recipe["semantic_digest"] = semantic_digest(recipe)
    return recipe


def sample_index_digest(indices: Sequence[int]) -> str:
    """Return the canonical digest for frozen source-row membership."""

    return hashlib.sha256(
        json.dumps(list(indices), separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _load_v2_manifest(path: Path) -> dict[str, Any]:
    value = read_json(path)
    if value.get("analysis") not in {"A", "B"}:
        raise WhatIfCostError(f"unsupported source manifest analysis: {path}")
    if value.get("dataset", {}).get("candidate_count") != V3_CANDIDATE_COUNT:
        raise WhatIfCostError("source manifest does not contain 57 candidates")
    if value.get("query_subset_size") != V3_QUERY_SUBSET_SIZE:
        raise WhatIfCostError("source manifest does not contain 128 frozen queries")
    return value


def _advisor_snapshot_objects(
    source_root: Path,
    workload: Mapping[str, Any],
    sample_rows: list[list[float | None]],
    population: Mapping[str, Any],
    recipe: Mapping[str, Any],
) -> Any:
    try:
        import pyarrow as pa
        from extstats_advisor.snapshot.model import (
            AdvisorSnapshot,
            ColumnSchema,
            DBMSIdentity,
            PopulationMetadata,
            RelationName,
            RelationSchema,
            SnapshotConsistency,
            Workload,
            WorkloadQuery,
        )
    except ImportError as exc:  # pragma: no cover - optional runtime dependency
        raise WhatIfCostError("Advisor snapshot dependencies are unavailable") from exc

    columns = tuple(
        ColumnSchema(name, index, "double", True, "double precision")
        for index, (name, _) in enumerate(forest10.COLUMNS, start=1)
    )
    schema = RelationSchema(
        V3_RELATION_ID,
        RelationName("forest10", schema="public", catalog="extstats_stock"),
        columns,
    )
    arrays = {
        name: pa.array([row[index] for row in sample_rows], type=pa.float64())
        for index, (name, _) in enumerate(forest10.COLUMNS)
    }
    table = pa.table(arrays)
    queries = tuple(
        WorkloadQuery(str(item["query_id"]), str(item["sql"]), float(item.get("weight", 1.0)))
        for item in workload["queries"]
    )
    snapshot_workload = Workload(
        str(workload["workload_id"]), queries, dict(workload.get("provenance", {}))
    )
    provenance = {
        "benchmark_id": forest10.BENCHMARK_ID,
        "source_csv_sha256": population["source_files"]["csv"],
        "dataset_content_identity": population["dataset_content_identity"],
        "sample_recipe": dict(recipe),
        "generator": "extstats-advisor-research.rq5_whatif_cost_v3",
        "source_root_logical": "EXTSTATS_RESEARCH_DATA_ROOT/arecel/raw/data/forest10",
    }
    return AdvisorSnapshot(
        schemas=(schema,),
        populations=(
            PopulationMetadata(
                V3_RELATION_ID,
                float(V3_POPULATION_ROWS),
                "exact",
                "audited Forest10 full-source row count",
            ),
        ),
        workload=snapshot_workload,
        samples={V3_RELATION_ID: table},
        dbms=DBMSIdentity("postgresql", "16.14"),
        consistency=SnapshotConsistency(),
        semantic_provenance=provenance,
        runtime_metadata={"source_root": str(source_root.resolve())},
    )


def _read_selected_rows(
    data_root: Path | None,
    indices: list[int],
) -> list[list[float | None]]:
    path = forest10.csv_path(data_root)
    selected = set(indices)
    result: list[list[float | None]] = []
    with path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.reader(stream)
        header = next(reader)
        if header != list(forest10.SOURCE_COLUMNS):
            raise WhatIfCostError("Forest10 CSV header drifted")
        for source_index, row in enumerate(reader):
            if source_index in selected:
                result.append([None if value == "" else float(value) for value in row])
    if len(result) != len(indices):
        raise WhatIfCostError("Forest10 sample selection did not find all source rows")
    return result


def _load_v3_workload(workload_path: Path) -> dict[str, Any]:
    workload = read_json(workload_path)
    if workload.get("workload_id") != "arecel_forest10_test_v1":
        raise WhatIfCostError("Forest10 workload identity drifted")
    if len(workload.get("queries", [])) != 10_000:
        raise WhatIfCostError("Forest10 workload must contain 10,000 queries")
    return workload


def acquire_v3_sample(
    data_root: Path | None = None,
    *,
    sample_rows: int = V3_SAMPLE_ROWS,
    seed: int = V3_SAMPLE_SEED,
) -> V3SampleAcquisition:
    """Validate the population and materialize the frozen rows at runtime.

    This function intentionally performs source validation and selected-row
    reading together so a live adapter can place both operations inside one
    explicitly timed sample-acquisition stage.  Frozen membership is not
    regenerated from a prebuilt Snapshot.
    """

    population = validate_full_forest10_source(data_root)
    indices = derive_sample_indices(V3_POPULATION_ROWS, sample_rows, seed)
    rows = _read_selected_rows(data_root, indices)
    _v3_require(len(rows) == sample_rows, "runtime Forest10 sample row count drifted")
    return V3SampleAcquisition(
        source=population,
        indices=tuple(indices),
        rows=tuple(tuple(row) for row in rows),
        recipe=sample_recipe(
            population_rows=V3_POPULATION_ROWS,
            sample_rows=sample_rows,
            seed=seed,
        ),
        index_digest=sample_index_digest(indices),
    )


def construct_v3_snapshot(
    data_root: Path | None,
    workload_path: Path,
    acquisition: V3SampleAcquisition,
) -> Any:
    """Construct the AdvisorSnapshot object from runtime-acquired rows."""

    workload = _load_v3_workload(workload_path)
    return _advisor_snapshot_objects(
        data_root or forest10.data_root(),
        workload,
        [list(row) for row in acquisition.rows],
        acquisition.source,
        acquisition.recipe,
    )


def prepare_v3_snapshot(
    data_root: Path | None,
    workload_path: Path,
    destination: Path,
    *,
    sample_rows: int = V3_SAMPLE_ROWS,
    seed: int = V3_SAMPLE_SEED,
) -> dict[str, Any]:
    """Create and validate a sealed 30,000-row AdvisorSnapshot offline."""

    acquisition = acquire_v3_sample(data_root, sample_rows=sample_rows, seed=seed)
    snapshot = construct_v3_snapshot(data_root, workload_path, acquisition)
    from extstats_advisor.snapshot.bundle import load_snapshot, write_snapshot

    digest = write_snapshot(snapshot, destination)
    loaded = load_snapshot(destination)
    _v3_require(
        len(loaded.samples[V3_RELATION_ID]) == sample_rows, "sealed sample row count drifted"
    )
    return {
        "status": "sealed",
        "snapshot_path": str(destination),
        "snapshot_semantic_digest": digest,
        "sample_rows": sample_rows,
        "sample_seed": seed,
        "sample_indices_sha256": acquisition.index_digest,
        "recipe": acquisition.recipe,
        "source": acquisition.source,
    }


def prepare_v3_candidate_universe(
    snapshot_path: Path,
    destination: Path,
    eligible_manifest_path: Path,
) -> dict[str, Any]:
    """Derive the real Advisor universe and retain the frozen 57 eligible IDs."""

    from extstats_advisor.candidates import derive_candidate_universe, write_candidate_universe
    from extstats_advisor.candidates.universe import CandidateUniverse
    from extstats_advisor.snapshot.bundle import load_snapshot

    snapshot = load_snapshot(snapshot_path)
    derived = derive_candidate_universe(snapshot)
    eligible_manifest = read_json(eligible_manifest_path)
    frozen = eligible_manifest.get("candidate_universe", {}).get("definitions", [])
    eligible_ids = [str(item["candidate_id"]) for item in frozen]
    _v3_require(
        len(eligible_ids) == V3_CANDIDATE_COUNT, "frozen eligible candidate count is not 57"
    )
    by_id = {item.candidate_id: item for item in derived.candidates}
    _v3_require(
        set(eligible_ids).issubset(by_id), "frozen eligible candidate is absent from derivation"
    )
    candidates = tuple(
        replace(by_id[candidate_id], static_precedence_rank=index)
        for index, candidate_id in enumerate(eligible_ids, start=1)
    )
    candidate_ids = set(eligible_ids)
    incidence = tuple(item for item in derived.incidence if item.candidate_id in candidate_ids)
    universe = CandidateUniverse(
        derived.source_snapshot_semantic_digest,
        derived.workload_analysis,
        derived.capability,
        derived.static_precedence_policy,
        derived.query_profiles,
        derived.relevant_groups,
        candidates,
        incidence,
    )
    digest = write_candidate_universe(universe, destination)
    return {
        "status": "sealed",
        "candidate_universe_path": str(destination),
        "candidate_universe_semantic_digest": digest,
        "candidate_count": len(universe.candidates),
        "source_snapshot_semantic_digest": snapshot.semantic_digest,
    }


def _source_bindings(v2: Mapping[str, Any], universe_digest: str | None) -> dict[str, Any]:
    bindings = dict(v2["source_bindings"])
    bindings["advisor_sha"] = ADVISOR_SHA_V1
    bindings["stock_postgres_sha"] = STOCK_POSTGRES_SHA
    bindings["patched_postgres_sha"] = PATCHED_POSTGRES_SHA
    bindings["candidate_universe"] = {
        "path": f"{V3_SAMPLE_RUNTIME_PATH}/candidate-universe.json",
        "semantic_digest": universe_digest,
    }
    return bindings


def build_v3_manifest(
    v2_manifest: Mapping[str, Any],
    *,
    analysis: str,
    producer_sha: str,
    protocol_sha256: str,
    full_source: Mapping[str, Any],
    snapshot_metadata: Mapping[str, Any],
    candidate_universe_digest: str,
) -> dict[str, Any]:
    """Build a v3 manifest while preserving v2 candidates, queries, and order."""

    if analysis not in {"A", "B"}:
        raise WhatIfCostError("v3 analysis must be A or B")
    _commit(producer_sha, "producer commit")
    _sha(protocol_sha256, "v3 protocol sha256")
    definitions = [dict(item) for item in v2_manifest["candidate_universe"]["definitions"]]
    configs = [
        dict(item)
        for item in (
            v2_manifest["analysis_a"]["configurations"]
            if analysis == "A"
            else v2_manifest["analysis_b"]["configurations"]
        )
    ]
    sample_contract = {
        "sample_rows": V3_SAMPLE_ROWS,
        "sample_seed": V3_SAMPLE_SEED,
        "selection": sample_recipe(),
        "snapshot_semantic_digest": snapshot_metadata.get("snapshot_semantic_digest"),
        "snapshot_path": snapshot_metadata.get("snapshot_path"),
        "snapshot_status": snapshot_metadata.get("status", "required"),
        "candidate_universe_semantic_digest": candidate_universe_digest,
    }
    body = {
        "format_version": V3_MANIFEST_FORMAT,
        "experiment_id": V3_EXPERIMENT_ID,
        "analysis": analysis,
        "status": "ready-for-offline-validation",
        "producer_commit_sha": producer_sha,
        "protocol": {
            "format": V3_PROTOCOL_FORMAT,
            "path": V3_PROTOCOL_PATH,
            "sha256": protocol_sha256,
        },
        "dataset": {
            "dataset_id": "arecel-forest10",
            "relation": "public.forest10",
            "workload_id": "arecel_forest10_test_v1",
            "full_population_rows": V3_POPULATION_ROWS,
            "catalogless_sample_rows": V3_SAMPLE_ROWS,
            "sample_seed": V3_SAMPLE_SEED,
            "statistics_target": 100,
            "candidate_count": V3_CANDIDATE_COUNT,
            "full_source": dict(full_source),
        },
        "source_bindings": _source_bindings(v2_manifest, candidate_universe_digest),
        "candidate_universe": {
            "semantic_digest": candidate_universe_digest,
            "count": len(definitions),
            "definitions": definitions,
        },
        "workload": dict(v2_manifest["workload"]),
        "query_subset": [dict(item) for item in v2_manifest["query_subset"]],
        "query_subset_size": V3_QUERY_SUBSET_SIZE,
        "sample_contract": sample_contract,
        "analysis_a": {
            "evaluation_count": 20,
            "configuration_size": 1,
            "checkpoints": list(ANALYSIS_A_CHECKPOINTS),
            "configurations": configs if analysis == "A" else [],
        },
        "analysis_b": {
            "configuration_sizes": list(ANALYSIS_B_SIZES),
            "configurations": configs if analysis == "B" else [],
        },
        "binding_digest": semantic_digest(
            {
                "dataset": {
                    "full_population_rows": V3_POPULATION_ROWS,
                    "catalogless_sample_rows": V3_SAMPLE_ROWS,
                    "sample_seed": V3_SAMPLE_SEED,
                    "full_source": dict(full_source),
                },
                "candidate_definitions": definitions,
                "candidate_universe_semantic_digest": candidate_universe_digest,
                "workload": dict(v2_manifest["workload"]),
                "query_subset": [dict(item) for item in v2_manifest["query_subset"]],
                "configurations": configs,
            }
        ),
        "timing_contract": {
            "clock": "client-monotonic-perf-counter-ns",
            "primary_boundary": "evaluation-ready-population",
            "common_full_population_provisioning_excluded": True,
            "physical_full_population_required": True,
            "physical_analyze_per_configuration": True,
            "catalogless_sample_acquisition_in_primary": True,
            "catalogless_materialization_once_per_run": True,
            "catalogless_analyze_per_configuration": False,
            "equal_payloads_required": False,
            "equal_plan_rows_required": False,
            "secondary_boundary": "prepared-state",
            "warm_start_excludes": [
                "fixed-sample-preparation",
                "native-payload-materialization",
                "repository-registration",
            ],
        },
        "claim_boundary": "mechanism-level lifecycle cost and observed amortization only",
        "formal_timing_authorized": False,
    }
    body["semantic_digest"] = semantic_digest(body)
    return body


def validate_v3_manifest(
    manifest: Mapping[str, Any],
    *,
    research_root: Path | None = None,
    verify_sources: bool = False,
    require_sample: bool = False,
) -> dict[str, Any]:
    """Validate v3 structure; strict sample readiness is opt-in and fail-closed."""

    _v3_require(manifest.get("format_version") == V3_MANIFEST_FORMAT, "unsupported v3 manifest")
    _v3_require(
        manifest.get("semantic_digest") == semantic_digest(_body(manifest)),
        "v3 manifest semantic digest mismatch",
    )
    _commit(manifest.get("producer_commit_sha"), "producer commit")
    _v3_require(manifest.get("experiment_id") == V3_EXPERIMENT_ID, "v3 experiment identity drifted")
    _v3_require(manifest.get("analysis") in {"A", "B"}, "v3 analysis must be A or B")
    _v3_require(manifest.get("query_subset_size") == V3_QUERY_SUBSET_SIZE, "v3 query count drifted")
    dataset = manifest.get("dataset", {})
    _v3_require(
        dataset.get("full_population_rows") == V3_POPULATION_ROWS,
        "full population row count drifted",
    )
    _v3_require(
        dataset.get("catalogless_sample_rows") == V3_SAMPLE_ROWS, "catalogless sample size drifted"
    )
    _v3_require(dataset.get("sample_seed") == V3_SAMPLE_SEED, "sample seed drifted")
    _v3_require(dataset.get("statistics_target") == 100, "statistics target drifted")
    _v3_require(dataset.get("candidate_count") == V3_CANDIDATE_COUNT, "candidate count drifted")
    bindings = manifest.get("source_bindings", {})
    _v3_require(bindings.get("advisor_sha") == ADVISOR_SHA_V1, "Advisor source pin drifted")
    _v3_require(
        bindings.get("stock_postgres_sha") == STOCK_POSTGRES_SHA, "stock source pin drifted"
    )
    _v3_require(
        bindings.get("patched_postgres_sha") == PATCHED_POSTGRES_SHA, "patched source pin drifted"
    )
    _sha(manifest.get("protocol", {}).get("sha256"), "v3 protocol sha256")
    _sha(manifest.get("binding_digest"), "v3 binding digest")
    definitions = manifest.get("candidate_universe", {}).get("definitions", [])
    ids = [item.get("candidate_id") for item in definitions]
    _v3_require(
        len(ids) == V3_CANDIDATE_COUNT and len(set(ids)) == V3_CANDIDATE_COUNT,
        "v3 candidates incomplete",
    )
    queries = manifest.get("query_subset", [])
    _v3_require(len(queries) == V3_QUERY_SUBSET_SIZE, "v3 query subset incomplete")
    _v3_require(
        len({item.get("query_id") for item in queries}) == V3_QUERY_SUBSET_SIZE,
        "duplicate v3 query IDs",
    )
    for query in queries:
        _v3_require(isinstance(query.get("query_id"), str), "invalid v3 query ID")
        _sha(query.get("sql_sha256"), "v3 SQL digest")
    configs = (
        manifest["analysis_a"]["configurations"]
        if manifest["analysis"] == "A"
        else manifest["analysis_b"]["configurations"]
    )
    _v3_require(configs, "v3 manifest has no configurations")
    _v3_require(
        [item.get("ordinal") for item in configs] == list(range(1, len(configs) + 1)),
        "v3 configuration ordinals drifted",
    )
    for config in configs:
        candidate_ids = config.get("candidate_ids")
        _v3_require(candidate_ids == config.get("declared_order"), "v3 configuration order drifted")
        _v3_require(
            len(candidate_ids) == config.get("configuration_size"), "v3 configuration size drifted"
        )
        _v3_require(
            set(candidate_ids).issubset(set(ids)), "v3 configuration contains unknown candidate"
        )
    if manifest["analysis"] == "A":
        _v3_require(
            len(configs) == 20 and all(item["configuration_size"] == 1 for item in configs),
            "v3 Analysis A contract drifted",
        )
    else:
        _v3_require(
            [item["configuration_size"] for item in configs] == list(ANALYSIS_B_SIZES),
            "v3 Analysis B contract drifted",
        )
    sample_contract = manifest.get("sample_contract", {})
    _v3_require(
        sample_contract.get("sample_rows") == V3_SAMPLE_ROWS, "sample contract row count drifted"
    )
    _v3_require(
        sample_contract.get("selection", {}).get("algorithm") == V3_SAMPLE_SELECTION,
        "sample selection algorithm drifted",
    )
    if verify_sources:
        _v3_require(research_root is not None, "research root required for v3 source verification")
        root = research_root.resolve()
        protocol = root / manifest["protocol"]["path"]
        _v3_require(
            protocol.is_file() and sha256_file(protocol) == manifest["protocol"]["sha256"],
            "v3 protocol binding drifted",
        )
        _v3_require(
            read_json(root / RQ4_ABLATION_PATH).get("semantic_digest") == RQ4_ABLATION_DIGEST,
            "RQ4 ablation binding drifted",
        )
        _v3_require(
            read_json(root / RQ4_DESIGN_PATH).get("semantic_digest") == RQ4_DESIGN_DIGEST,
            "RQ4 design binding drifted",
        )
    if require_sample:
        path_value = sample_contract.get("snapshot_path")
        _v3_require(path_value, "v3 sealed snapshot is required")
        snapshot_path = Path(path_value)
        if research_root is not None and not snapshot_path.is_absolute():
            snapshot_path = research_root / snapshot_path
        _v3_require(snapshot_path.is_dir(), f"v3 sealed snapshot is missing: {snapshot_path}")
        from extstats_advisor.snapshot.bundle import load_snapshot

        snapshot = load_snapshot(snapshot_path)
        _v3_require(
            snapshot.semantic_digest == sample_contract.get("snapshot_semantic_digest"),
            "v3 snapshot digest mismatch",
        )
        _v3_require(
            len(snapshot.samples[V3_RELATION_ID]) == V3_SAMPLE_ROWS,
            "v3 sealed snapshot row count mismatch",
        )
        candidate_binding = manifest.get("source_bindings", {}).get("candidate_universe", {})
        candidate_path = Path(str(candidate_binding.get("path", "")))
        if research_root is not None and not candidate_path.is_absolute():
            candidate_path = research_root / candidate_path
        _v3_require(candidate_path.is_file(), f"v3 candidate universe is missing: {candidate_path}")
        candidate_value = read_json(candidate_path)
        _v3_require(
            candidate_value.get("semantic_digest") == candidate_binding.get("semantic_digest"),
            "v3 candidate-universe digest mismatch",
        )
        _v3_require(
            len(candidate_value.get("candidates", [])) == V3_CANDIDATE_COUNT,
            "v3 candidate-universe count mismatch",
        )
    return {
        "status": "valid",
        "format_version": V3_MANIFEST_FORMAT,
        "analysis": manifest["analysis"],
        "semantic_digest": manifest["semantic_digest"],
        "binding_digest": manifest["binding_digest"],
        "configuration_count": len(configs),
        "query_count": len(queries),
        "sample_ready": bool(sample_contract.get("snapshot_semantic_digest")),
    }


__all__ = [
    "V3_MANIFEST_FORMAT",
    "V3_PROTOCOL_FORMAT",
    "V3_SAMPLE_INDEX_DIGEST",
    "V3_SAMPLE_ROWS",
    "V3_SAMPLE_SEED",
    "V3SampleAcquisition",
    "acquire_v3_sample",
    "build_v3_manifest",
    "construct_v3_snapshot",
    "derive_sample_indices",
    "prepare_v3_candidate_universe",
    "prepare_v3_snapshot",
    "sample_index_digest",
    "sample_recipe",
    "validate_full_forest10_source",
    "validate_v3_manifest",
]
