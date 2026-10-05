from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
from extstats_advisor.ground_truth import (
    AUTHORITATIVE_CARDINALITY_OBSERVATIONS_FORMAT_VERSION,
    AUTHORITATIVE_EXTERNAL_COLLECTION_CONTRACT,
    AUTHORITATIVE_EXTERNAL_SOURCE,
    ArtifactGroundTruthProvider,
    CardinalityTruth,
    GroundTruthSet,
    GroundTruthSource,
)
from extstats_advisor.snapshot.bundle import load_snapshot, write_snapshot
from extstats_advisor.snapshot.model import (
    AdvisorSnapshot,
    ColumnSchema,
    DBMSIdentity,
    PopulationMetadata,
    RelationName,
    RelationSchema,
    Workload,
    WorkloadQuery,
)
from extstats_advisor.utility import QErrorLoss, WeightedWorkloadUtility

from extstats_advisor_research.external_truth import (
    import_audited_authoritative_truth,
    write_authoritative_observations,
)


def _sealed_snapshot(tmp_path: Path) -> tuple[Path, AdvisorSnapshot]:
    table = pa.table(
        {"id": pa.array([1, 2], type=pa.int32())},
        schema=pa.schema([pa.field("id", pa.int32(), nullable=False)]),
    )
    snapshot = AdvisorSnapshot(
        schemas=(
            RelationSchema(
                "rel_1",
                RelationName("items", schema="public"),
                (ColumnSchema("id", 1, "int32", False, "integer"),),
            ),
        ),
        populations=(PopulationMetadata("rel_1", 2, "estimate", "fixture"),),
        workload=Workload(
            "synthetic-workload",
            (
                WorkloadQuery("q1", "SELECT 1"),
                WorkloadQuery("q_zero", "SELECT 2", 0),
            ),
        ),
        samples={"rel_1": table},
        dbms=DBMSIdentity("example-db", "1"),
        semantic_provenance={"source_view_token": "synthetic-source-view"},
    )
    path = tmp_path / "snapshot"
    write_snapshot(snapshot, path)
    return path, load_snapshot(path)


def test_research_bridge_writes_only_production_observation_contract(tmp_path: Path) -> None:
    output = write_authoritative_observations(
        "synthetic-workload",
        {"q2": 0, "q1": 12},
        tmp_path / "observations.json",
    )
    value = json.loads(output.read_text(encoding="utf-8"))
    assert set(value) == {"format_version", "workload_id", "truths"}
    assert value["format_version"] == AUTHORITATIVE_CARDINALITY_OBSERVATIONS_FORMAT_VERSION
    assert value["truths"] == [
        {"query_id": "q1", "cardinality": 12},
        {"query_id": "q2", "cardinality": 0},
    ]
    assert "sql" not in output.read_text(encoding="utf-8").lower()


def test_external_import_delegates_binding_and_preserves_utility_invariant(tmp_path: Path) -> None:
    snapshot_path, snapshot = _sealed_snapshot(tmp_path)
    observations = write_authoritative_observations(
        snapshot.workload.workload_id,
        {"q1": 12},
        tmp_path / "observations.json",
    )
    external = import_audited_authoritative_truth(
        snapshot_path,
        observations,
        authority="audited-fixture-authority",
        dataset_identity="synthetic-dataset",
        source_revision="audit-revision-1",
    )
    assert external.source.kind == AUTHORITATIVE_EXTERNAL_SOURCE
    assert external.collection_contract == AUTHORITATIVE_EXTERNAL_COLLECTION_CONTRACT
    assert external.source.dbms is None
    assert external.source.server_version is None
    assert external.source.server_version_num is None
    assert external.source.source_view_token is None
    assert external.source.source_artifact_sha256

    production = GroundTruthSet(
        snapshot.semantic_digest or "",
        snapshot.workload.workload_id,
        GroundTruthSource(
            "production-exact-execution",
            snapshot.dbms.name,
            snapshot.dbms.version or "unknown",
            1,
            snapshot.semantic_provenance["source_view_token"],
        ),
        (CardinalityTruth("q1", 12, "production-exact-execution"),),
    )
    estimates = {"q1": 6}
    production_utility = WeightedWorkloadUtility(
        snapshot.workload, ArtifactGroundTruthProvider(production), QErrorLoss()
    ).evaluate(estimates)
    external_utility = WeightedWorkloadUtility(
        snapshot.workload, ArtifactGroundTruthProvider(external), QErrorLoss()
    ).evaluate(estimates)
    assert production_utility.objective == external_utility.objective == 2
    assert production.computed_semantic_digest != external.computed_semantic_digest
