"""Unified read model for provenance topology and contamination propagation.

The legacy modules remain the source of v1 behaviour.  This module couples their
outputs behind one read-only object so reporting and decision layers no longer
need to reach into either implementation's private state.
"""
from __future__ import annotations

from dataclasses import dataclass

from artifacts import ArtifactStore
from contamination.propagation import ContaminationResult, propagate_contamination

from .graph import ArtifactGraph, EdgeType


@dataclass(frozen=True)
class ImpactAnalysis:
    graph: ArtifactGraph
    contamination: ContaminationResult

    def __post_init__(self) -> None:
        run_ids = {ref.run_id for ref in self.graph.nodes()}
        if run_ids and run_ids != {self.contamination.run_id}:
            raise ValueError("graph and contamination must describe the same run")

    @classmethod
    def from_store(
        cls,
        store: ArtifactStore,
        run_id: str,
        invalidated_refs=(),
    ) -> "ImpactAnalysis":
        graph = ArtifactGraph.from_store(store, run_id)
        result = propagate_contamination(graph, store, run_id, invalidated_refs)
        return cls(graph=graph, contamination=result)

    @property
    def affected(self):
        return tuple(self.contamination.invalidated + self.contamination.contaminated)

    @property
    def affected_ratio(self) -> float:
        total = self.graph.node_count()
        return len(set(self.affected)) / total if total else 0.0


__all__ = [
    "ArtifactGraph", "ContaminationResult", "EdgeType", "ImpactAnalysis",
    "propagate_contamination",
]
