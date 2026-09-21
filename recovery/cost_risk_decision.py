"""Opt-in cost/risk strategy selection using abstract, documented units."""
from __future__ import annotations

from dataclasses import dataclass, field

from contamination import ContaminationResult
from provenance.graph import ArtifactGraph


@dataclass(frozen=True)
class CostRiskConfig:
    recompute_unit_cost: float = 1.0
    verification_unit_cost: float = 0.25
    llm_unit_cost: float = 2.0
    critical_claim_risk: float = 8.0
    residual_risk_per_skipped_node: float = 4.0
    checkpoint_available: bool = False
    critical_artifact_ids: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class CostRiskDecision:
    strategy_name: str
    affected_count: int
    total_count: int
    affected_ratio: float
    reason: str
    reason_codes: tuple[str, ...]
    scores: dict[str, float]


class CostRiskDecisionEngine:
    """Choose the minimum abstract expected cost; never implies currency."""
    _tie_order = ("SELECTIVE_RECOVERY", "COARSE_CHECKPOINT", "FULL_RERUN")

    def __init__(self, config: CostRiskConfig | None = None) -> None:
        self.config = config or CostRiskConfig()

    def decide(self, contamination: ContaminationResult, graph: ArtifactGraph) -> CostRiskDecision:
        cfg = self.config
        units = (cfg.recompute_unit_cost, cfg.verification_unit_cost, cfg.llm_unit_cost,
                 cfg.critical_claim_risk, cfg.residual_risk_per_skipped_node)
        if min(units) < 0:
            raise ValueError("cost/risk units must be non-negative")
        total = graph.node_count()
        affected_refs = (set(contamination.invalidated) | set(contamination.contaminated)) & graph.nodes()
        affected = len(affected_refs)
        ratio = affected / total if total else 0.0
        critical = any(ref.artifact_id in cfg.critical_artifact_ids for ref in affected_refs)
        affected_llm = sum(1 for ref in affected_refs if graph.artifact(ref) is not None and (graph.artifact(ref).meta or {}).get("task_kind") == "llm")
        full = total * (cfg.recompute_unit_cost + cfg.verification_unit_cost)
        selective = affected * (cfg.recompute_unit_cost + cfg.verification_unit_cost) + affected_llm * cfg.llm_unit_cost + (cfg.critical_claim_risk if critical else 0.0)
        if cfg.checkpoint_available:
            coarse_nodes = min(total, max(affected, (total + 1) // 2))
            coarse = coarse_nodes * cfg.recompute_unit_cost + affected * cfg.verification_unit_cost
        else:
            coarse = float("inf")
        scores = {"SELECTIVE_RECOVERY": round(selective, 6), "COARSE_CHECKPOINT": round(coarse, 6), "FULL_RERUN": round(full, 6)}
        chosen = min(self._tie_order, key=lambda name: (scores[name], self._tie_order.index(name)))
        codes = ["ABSTRACT_COST_UNITS", "MIN_EXPECTED_COST"]
        if not cfg.checkpoint_available:
            codes.append("CHECKPOINT_UNAVAILABLE")
        if critical:
            codes.append("CRITICAL_ARTIFACT_AFFECTED")
        if total == 0:
            codes.append("EMPTY_GRAPH")
        reason = f"{chosen} 的抽象成本—风险评分最低（{scores[chosen]} cost units）"
        return CostRiskDecision(chosen, affected, total, ratio, reason, tuple(codes), scores)

    def select_strategy(self, contamination: ContaminationResult, graph: ArtifactGraph) -> str:
        return self.decide(contamination, graph).strategy_name


__all__ = ["CostRiskConfig", "CostRiskDecision", "CostRiskDecisionEngine"]
