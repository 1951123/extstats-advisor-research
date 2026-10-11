"""Offline contract for the v4 in-place Physical RQ5 campaign.

v4 keeps every scientific input from v3 and changes one lifecycle variable:
Physical configurations are evaluated sequentially in one fresh, experiment-
owned full-population database per repetition.  This module does not open a
database and does not alter the v3 manifests.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .provenance import read_json, semantic_digest, sha256_file
from .rq5_whatif_cost import (
    ADVISOR_SHA_V1,
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
from .rq5_whatif_cost_v3 import (
    V3_MANIFEST_FORMAT,
    V3_POPULATION_ROWS,
    V3_PROTOCOL_FORMAT,
    V3_PROTOCOL_PATH,
    V3_SAMPLE_ROWS,
    V3_SAMPLE_SEED,
    V3_SAMPLE_SELECTION,
    validate_v3_manifest,
)

V4_PROTOCOL_FORMAT = "rq5-whatif-evaluation-cost-v4"
V4_PROTOCOL_PATH = "docs/protocols/rq5-whatif-evaluation-cost-v4.md"
V4_MANIFEST_FORMAT = "rq5-whatif-evaluation-cost-manifest-v4"
V4_PREFLIGHT_MANIFEST_FORMAT = "rq5-whatif-evaluation-cost-v4-preflight-manifest-v1"
V4_EXPERIMENT_ID = "rq5-whatif-evaluation-cost-v4-forest10"
V4_PHYSICAL_RESET_STRATEGY = "single-inplace-database-per-repetition"


def _v4_require(condition: bool, message: str) -> None:
    if not condition:
        raise WhatIfCostError(message)


def _v4_binding_payload(manifest: Mapping[str, Any]) -> dict[str, Any]:
    configs = manifest["analysis_a"]["configurations"]
    return {
        "dataset": {
            "full_population_rows": manifest["dataset"]["full_population_rows"],
            "catalogless_sample_rows": manifest["dataset"]["catalogless_sample_rows"],
            "sample_seed": manifest["dataset"]["sample_seed"],
            "full_source": manifest["dataset"]["full_source"],
        },
        "candidate_definitions": manifest["candidate_universe"]["definitions"],
        "candidate_universe_semantic_digest": manifest["candidate_universe"]["semantic_digest"],
        "workload": manifest["workload"],
        "query_subset": manifest["query_subset"],
        "configurations": configs,
        "physical_reset_strategy": V4_PHYSICAL_RESET_STRATEGY,
    }


def build_v4_manifest_from_v3(
    v3_manifest: Mapping[str, Any],
    *,
    producer_sha: str,
    protocol_sha256: str,
) -> dict[str, Any]:
    """Derive a new v4 manifest without mutating the v3 source artifact."""

    validate_v3_manifest(v3_manifest)
    _commit(producer_sha, "v4 producer commit")
    _sha(protocol_sha256, "v4 protocol sha256")
    body = copy.deepcopy(dict(v3_manifest))
    body.update(
        {
            "format_version": V4_MANIFEST_FORMAT,
            "experiment_id": V4_EXPERIMENT_ID,
            "status": "ready-for-offline-validation",
            "producer_commit_sha": producer_sha,
            "protocol": {
                "format": V4_PROTOCOL_FORMAT,
                "path": V4_PROTOCOL_PATH,
                "sha256": protocol_sha256,
            },
            "physical_reset_strategy": {
                "id": V4_PHYSICAL_RESET_STRATEGY,
                "database_per_repetition": True,
                "clone_per_configuration": False,
                "common_full_population_provisioning_excluded": True,
                "ordinary_statistics_restored_between_configurations": False,
            },
            "formal_timing_authorized": False,
            "claim_boundary": "in-place mechanism-level lifecycle cost and observed amortization only",
        }
    )
    body["timing_contract"] = dict(body["timing_contract"])
    body["timing_contract"].update(
        {
            "physical_in_place": True,
            "physical_clone_per_configuration": False,
            "physical_analyze_per_configuration": True,
            "physical_post_drop_verification_per_configuration": True,
            "physical_provisioning_outside_primary_boundary": True,
        }
    )
    body["binding_digest"] = semantic_digest(_v4_binding_payload(body))
    body["semantic_digest"] = semantic_digest(_body(body))
    return body


def build_v4_limited_preflight_manifest(
    formal_manifest: Mapping[str, Any],
    *,
    formal_manifest_path: str,
    formal_manifest_sha256: str,
    configuration_count: int = 2,
    query_count: int = 5,
) -> dict[str, Any]:
    """Derive a bounded correctness manifest from formal v4 Analysis A."""

    validate_v4_manifest(formal_manifest)
    if formal_manifest.get("analysis") != "A":
        raise WhatIfCostError("v4 preflight requires Analysis A")
    if not 1 <= configuration_count <= 2 or not 1 <= query_count <= 5:
        raise WhatIfCostError("v4 preflight scope is limited to two configs and five queries")
    body = copy.deepcopy(dict(formal_manifest))
    selected_configs = copy.deepcopy(body["analysis_a"]["configurations"][:configuration_count])
    selected_queries = copy.deepcopy(body["query_subset"][:query_count])
    body.update(
        {
            "format_version": V4_PREFLIGHT_MANIFEST_FORMAT,
            "status": "ready-for-limited-live-preflight",
            "preflight_scope": "v4-inplace-physical-and-catalogless",
            "source_formal_manifest": {
                "path": formal_manifest_path,
                "semantic_digest": formal_manifest["semantic_digest"],
                "byte_sha256": formal_manifest_sha256,
            },
            "query_subset": selected_queries,
            "query_subset_size": len(selected_queries),
            "analysis_a": {
                "evaluation_count": len(selected_configs),
                "configuration_size": 1,
                "checkpoints": [len(selected_configs)],
                "configurations": selected_configs,
            },
            "analysis_b": {"configuration_sizes": [], "configurations": []},
            "limited_scope": {
                "configuration_count": len(selected_configs),
                "query_count": len(selected_queries),
                "classification": "integration-readiness-only",
                "does_not_authorize_formal_timing": True,
            },
        }
    )
    body["binding_digest"] = semantic_digest(_v4_binding_payload(body))
    body["semantic_digest"] = semantic_digest(_body(body))
    return body


def _legacy_v3_view(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Use v3's structural validator while keeping the v4 binding separate."""

    legacy = copy.deepcopy(dict(manifest))
    legacy["format_version"] = V3_MANIFEST_FORMAT
    legacy["experiment_id"] = "rq5-whatif-evaluation-cost-v3-forest10"
    legacy["protocol"] = {
        "format": V3_PROTOCOL_FORMAT,
        "path": V3_PROTOCOL_PATH,
        "sha256": "0" * 64,
    }
    legacy["semantic_digest"] = semantic_digest(_body(legacy))
    return legacy


def validate_v4_manifest(
    manifest: Mapping[str, Any],
    *,
    research_root: Path | None = None,
    verify_sources: bool = False,
) -> dict[str, Any]:
    """Validate v4 bindings, dimensions, and the in-place lifecycle contract."""

    format_version = manifest.get("format_version")
    _v4_require(
        format_version in {V4_MANIFEST_FORMAT, V4_PREFLIGHT_MANIFEST_FORMAT},
        "unsupported v4 manifest",
    )
    _v4_require(
        manifest.get("semantic_digest") == semantic_digest(_body(manifest)),
        "v4 manifest semantic digest mismatch",
    )
    _commit(manifest.get("producer_commit_sha"), "v4 producer commit")
    _v4_require(manifest.get("experiment_id") == V4_EXPERIMENT_ID, "v4 experiment identity drifted")
    _v4_require(manifest.get("analysis") in {"A", "B"}, "v4 analysis must be A or B")
    _v4_require(
        manifest.get("protocol", {}).get("format") == V4_PROTOCOL_FORMAT,
        "v4 protocol format drifted",
    )
    _sha(manifest.get("protocol", {}).get("sha256"), "v4 protocol sha256")
    _v4_require(
        manifest.get("physical_reset_strategy", {}).get("id") == V4_PHYSICAL_RESET_STRATEGY,
        "v4 physical reset strategy drifted",
    )
    _v4_require(
        manifest.get("physical_reset_strategy", {}).get("clone_per_configuration") is False,
        "v4 must disable per-configuration cloning",
    )
    _v4_require(
        manifest.get("binding_digest") == semantic_digest(_v4_binding_payload(manifest)),
        "v4 binding digest mismatch",
    )
    if format_version == V4_MANIFEST_FORMAT:
        # This validates the full frozen candidate/query/configuration
        # dimensions.  Its protocol fields are replaced only in a temporary
        # in-memory view; no historical artifact is changed.
        validate_v3_manifest(_legacy_v3_view(manifest))
    else:
        definitions = manifest.get("candidate_universe", {}).get("definitions", [])
        ids = [item.get("candidate_id") for item in definitions]
        _v4_require(
            len(ids) == 57 and len(set(ids)) == 57,
            "v4 preflight candidate universe must contain 57 unique candidates",
        )
        queries = manifest.get("query_subset", [])
        _v4_require(1 <= len(queries) <= 5, "v4 preflight query scope is invalid")
        _v4_require(
            len({item.get("query_id") for item in queries}) == len(queries),
            "v4 preflight query IDs are duplicated",
        )
        configs = manifest.get("analysis_a", {}).get("configurations", [])
        _v4_require(1 <= len(configs) <= 2, "v4 preflight configuration scope is invalid")
        _v4_require(
            [item.get("ordinal") for item in configs] == list(range(1, len(configs) + 1)),
            "v4 preflight configuration ordinals drifted",
        )
        for query in queries:
            _sha(query.get("sql_sha256"), "v4 preflight SQL digest")
        for config in configs:
            _v4_require(
                config.get("configuration_size") == 1
                and config.get("candidate_ids") == config.get("declared_order")
                and len(config.get("candidate_ids", [])) == 1
                and config["candidate_ids"][0] in ids,
                "v4 preflight configuration is not a frozen singleton",
            )
    dataset = manifest.get("dataset", {})
    _v4_require(
        dataset.get("full_population_rows") == V3_POPULATION_ROWS, "population rows drifted"
    )
    _v4_require(dataset.get("catalogless_sample_rows") == V3_SAMPLE_ROWS, "sample rows drifted")
    _v4_require(dataset.get("sample_seed") == V3_SAMPLE_SEED, "sample seed drifted")
    sample = manifest.get("sample_contract", {})
    _v4_require(
        sample.get("selection", {}).get("algorithm") == V3_SAMPLE_SELECTION,
        "sample selection algorithm drifted",
    )
    _v4_require(
        sample.get("selection", {}).get("seed") == V3_SAMPLE_SEED,
        "sample selection seed drifted",
    )
    _v4_require(
        sample.get("selection", {}).get("sample_rows") == V3_SAMPLE_ROWS,
        "sample selection size drifted",
    )
    _v4_require(
        manifest.get("source_bindings", {}).get("advisor_sha") == ADVISOR_SHA_V1,
        "Advisor source pin drifted",
    )
    _v4_require(
        manifest.get("source_bindings", {}).get("stock_postgres_sha") == STOCK_POSTGRES_SHA,
        "stock source pin drifted",
    )
    _v4_require(
        manifest.get("source_bindings", {}).get("patched_postgres_sha") == PATCHED_POSTGRES_SHA,
        "patched source pin drifted",
    )
    if verify_sources:
        _v4_require(research_root is not None, "research root required for source verification")
        root = research_root.resolve()
        protocol = root / str(manifest["protocol"]["path"])
        _v4_require(
            protocol.is_file() and sha256_file(protocol) == manifest["protocol"]["sha256"],
            "v4 protocol binding drifted",
        )
        for path, digest in (
            (RQ4_ABLATION_PATH, RQ4_ABLATION_DIGEST),
            (RQ4_DESIGN_PATH, RQ4_DESIGN_DIGEST),
        ):
            _v4_require(
                read_json(root / path).get("semantic_digest") == digest,
                f"frozen artifact binding drifted: {path}",
            )
    return {
        "status": "valid",
        "format_version": str(format_version),
        "analysis": manifest["analysis"],
        "semantic_digest": manifest["semantic_digest"],
        "binding_digest": manifest["binding_digest"],
        "configuration_count": len(manifest["analysis_a"]["configurations"]),
        "query_count": manifest["query_subset_size"],
        "physical_reset_strategy": V4_PHYSICAL_RESET_STRATEGY,
    }


__all__ = [
    "V4_EXPERIMENT_ID",
    "V4_MANIFEST_FORMAT",
    "V4_PHYSICAL_RESET_STRATEGY",
    "V4_PREFLIGHT_MANIFEST_FORMAT",
    "V4_PROTOCOL_FORMAT",
    "V4_PROTOCOL_PATH",
    "build_v4_limited_preflight_manifest",
    "build_v4_manifest_from_v3",
    "validate_v4_manifest",
]
