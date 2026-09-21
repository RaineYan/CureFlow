"""Automatic recovery-strategy selection from the measured impact scope."""
from __future__ import annotations

from dataclasses import dataclass

from artifacts import ArtifactRef, ArtifactStore
from contamination import ContaminationResult
from llm import LLMClient
from provenance.graph import ArtifactGraph

from .models import RecoveryPlan
from .planner import build_recovery_plan


@dataclass(frozen=True)
class Decision:
    strategy_name: str
    affected_count: int
    total_count: int
    affected_ratio: float
    reason: str


class DecisionEngine:
    """Choose SELECTIVE/COARSE/FULL using fixed 30% and 70% boundaries."""

    selective_threshold = 0.30
    full_threshold = 0.70

    def decide(self, contamination: ContaminationResult, graph: ArtifactGraph) -> Decision:
        total = graph.node_count()
        affected = set(contamination.invalidated) | set(contamination.contaminated)
        affected_in_graph = affected & graph.nodes()
        ratio = len(affected_in_graph) / total if total else 0.0

        if ratio < self.selective_threshold:
            strategy = "SELECTIVE_RECOVERY"
            reason = "受影响比例低于 30%，仅恢复污染子图"
        elif ratio < self.full_threshold:
            strategy = "COARSE_CHECKPOINT"
            reason = "受影响比例介于 30%（含）与 70% 之间，从检查点恢复"
        else:
            strategy = "FULL_RERUN"
            reason = "受影响比例达到或超过 70%，执行全量重跑"
        return Decision(strategy, len(affected_in_graph), total, ratio, reason)

    def select_strategy(self, contamination: ContaminationResult, graph: ArtifactGraph) -> str:
        return self.decide(contamination, graph).strategy_name


def build_auto_plan(
    graph: ArtifactGraph,
    store: ArtifactStore,
    run_id: str,
    target_ref: ArtifactRef,
    contamination: ContaminationResult,
    llm_client: LLMClient = None,
    *,
    engine: DecisionEngine | None = None,
) -> RecoveryPlan:
    """Choose a strategy and delegate to the unchanged registry planner."""
    decision_engine = engine or DecisionEngine()
    strategy_name = decision_engine.select_strategy(contamination, graph)
    return build_recovery_plan(
        strategy_name, graph, store, run_id, target_ref, contamination, llm_client,
    )


__all__ = ["Decision", "DecisionEngine", "build_auto_plan"]
