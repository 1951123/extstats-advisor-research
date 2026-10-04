"""Deterministic run identity and non-overwriting artifact layout."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..provenance import read_json, semantic_digest, write_json


@dataclass(frozen=True)
class RunLayout:
    root: Path
    run_id: str

    @property
    def directory(self) -> Path:
        return self.root / self.run_id

    def path(self, name: str) -> Path:
        return self.directory / name

    @property
    def manifest(self) -> Path:
        return self.path("manifest.json")

    def artifacts(self) -> dict[str, Path]:
        return {
            "snapshot": self.path("advisor-snapshot"),
            "ground_truth": self.path("ground-truth-v1.json"),
            "candidate_universe": self.path("candidate-universe.json"),
            "native_repository": self.path("native-stats-repository"),
            "singleton_profile": self.path("singleton-profile.json"),
            "optimization_plan": self.path("optimization-plan.json"),
            "search_result": self.path("search-result.json"),
            "recommendation": self.path("recommendation.json"),
            "summary": self.path("metrics-summary.json"),
            "logs": self.path("logs.jsonl"),
        }


def run_id(inputs: dict[str, Any]) -> str:
    """Return a stable identity; timestamps and DSNs must not be supplied."""
    return semantic_digest(inputs)[:24]


def create_layout(output_root: Path, identity: dict[str, Any]) -> RunLayout:
    layout = RunLayout(Path(output_root).expanduser().resolve(), run_id(identity))
    if layout.directory.exists():
        if layout.manifest.exists():
            raise FileExistsError(f"completed or started run already exists: {layout.directory}")
        raise FileExistsError(f"run directory already exists: {layout.directory}")
    layout.directory.mkdir(parents=True)
    write_json(
        layout.manifest, {"format": "research-run-manifest-v1", "run_id": layout.run_id, **identity}
    )
    return layout


def update_manifest(layout: RunLayout, updates: dict[str, Any]) -> dict[str, Any]:
    value = read_json(layout.manifest)
    value.update(updates)
    write_json(layout.manifest, value)
    return value
