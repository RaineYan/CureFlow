import pytest

from artifacts import Artifact, ArtifactRef, ArtifactType
from contamination import ContaminationResult
from provenance.graph import ArtifactGraph
from recovery.cost_risk_decision import CostRiskConfig, CostRiskDecisionEngine
from recovery.decision import DecisionEngine


def scope(total=10, affected=2, *, llm=False):
    graph, refs = ArtifactGraph(), []
    for index in range(total):
        ref = ArtifactRef(run_id="cost", artifact_id=f"n{index}", version=1)
        artifact = Artifact(artifact_id=f"n{index}", artifact_type=ArtifactType.CLAIM if llm else ArtifactType.METRIC, producer_task="x", run_id="cost", meta={"task_kind": "llm" if llm else "tool"})
        graph._graph.add_node(ref, artifact=artifact)
        refs.append(ref)
    result = ContaminationResult(run_id="cost", invalidated=refs[:affected], contaminated=[], unaffected=refs[affected:], propagation_edges=[])
    return result, graph


def test_default_engine_contract_is_unchanged() -> None:
    assert DecisionEngine().select_strategy(*scope(10, 3)) == "COARSE_CHECKPOINT"


def test_opt_in_engine_uses_abstract_units() -> None:
    decision = CostRiskDecisionEngine().decide(*scope(10, 2))
    assert decision.strategy_name == "SELECTIVE_RECOVERY"
    assert "ABSTRACT_COST_UNITS" in decision.reason_codes


def test_checkpoint_is_not_selected_when_unavailable() -> None:
    decision = CostRiskDecisionEngine().decide(*scope(10, 6))
    assert decision.scores["COARSE_CHECKPOINT"] == float("inf")
    assert "CHECKPOINT_UNAVAILABLE" in decision.reason_codes


def test_checkpoint_can_win_when_explicitly_available() -> None:
    cfg = CostRiskConfig(checkpoint_available=True, critical_claim_risk=100, critical_artifact_ids=frozenset({"n0"}))
    assert CostRiskDecisionEngine(cfg).select_strategy(*scope(10, 6)) == "COARSE_CHECKPOINT"


def test_critical_artifact_adds_risk_reason() -> None:
    cfg = CostRiskConfig(critical_artifact_ids=frozenset({"n0"}))
    assert "CRITICAL_ARTIFACT_AFFECTED" in CostRiskDecisionEngine(cfg).decide(*scope(10, 1)).reason_codes


def test_llm_nodes_add_explicit_cost() -> None:
    tool = CostRiskDecisionEngine().decide(*scope(10, 2, llm=False))
    llm = CostRiskDecisionEngine().decide(*scope(10, 2, llm=True))
    assert llm.scores["SELECTIVE_RECOVERY"] > tool.scores["SELECTIVE_RECOVERY"]


def test_empty_graph_is_deterministic() -> None:
    decision = CostRiskDecisionEngine().decide(*scope(0, 0))
    assert decision.strategy_name == "SELECTIVE_RECOVERY"
    assert "EMPTY_GRAPH" in decision.reason_codes


def test_tie_break_is_deterministic() -> None:
    cfg = CostRiskConfig(recompute_unit_cost=0, verification_unit_cost=0, llm_unit_cost=0, critical_claim_risk=0, residual_risk_per_skipped_node=0, checkpoint_available=True)
    assert CostRiskDecisionEngine(cfg).select_strategy(*scope(10, 2)) == "SELECTIVE_RECOVERY"


def test_negative_cost_is_rejected() -> None:
    with pytest.raises(ValueError):
        CostRiskDecisionEngine(CostRiskConfig(recompute_unit_cost=-1)).decide(*scope())
