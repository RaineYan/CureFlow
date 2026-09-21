"""Boundary and delegation tests for automatic strategy selection."""
from unittest.mock import patch

from artifacts import ArtifactRef
from contamination import ContaminationResult
from provenance.graph import ArtifactGraph
from recovery.decision import DecisionEngine, build_auto_plan


def make_case(affected_count: int, total_count: int = 100):
    refs = [ArtifactRef(run_id="decision", artifact_id=f"n{i:03}", version=1) for i in range(total_count)]
    graph = ArtifactGraph()
    graph._graph.add_nodes_from(refs)
    result = ContaminationResult(
        run_id="decision",
        invalidated=refs[:1] if affected_count else [],
        contaminated=refs[1:affected_count],
        unaffected=refs[affected_count:],
        propagation_edges=[],
    )
    return graph, result, refs


def test_zero_percent_selects_selective() -> None:
    graph, result, _ = make_case(0)
    assert DecisionEngine().select_strategy(result, graph) == "SELECTIVE_RECOVERY"


def test_below_thirty_percent_selects_selective() -> None:
    graph, result, _ = make_case(29)
    assert DecisionEngine().select_strategy(result, graph) == "SELECTIVE_RECOVERY"


def test_exactly_thirty_percent_selects_coarse() -> None:
    graph, result, _ = make_case(30)
    assert DecisionEngine().select_strategy(result, graph) == "COARSE_CHECKPOINT"


def test_below_seventy_percent_selects_coarse() -> None:
    graph, result, _ = make_case(69)
    assert DecisionEngine().select_strategy(result, graph) == "COARSE_CHECKPOINT"


def test_exactly_seventy_percent_selects_full() -> None:
    graph, result, _ = make_case(70)
    assert DecisionEngine().select_strategy(result, graph) == "FULL_RERUN"


def test_decision_deduplicates_affected_refs() -> None:
    graph, result, refs = make_case(1, 10)
    result.contaminated = [refs[0]]
    decision = DecisionEngine().decide(result, graph)
    assert decision.affected_count == 1 and decision.affected_ratio == 0.1


def test_empty_graph_is_safe_and_selective() -> None:
    graph = ArtifactGraph()
    result = ContaminationResult(run_id="decision", invalidated=[], contaminated=[], unaffected=[], propagation_edges=[])
    decision = DecisionEngine().decide(result, graph)
    assert decision.total_count == 0 and decision.affected_ratio == 0
    assert decision.strategy_name == "SELECTIVE_RECOVERY"


def test_build_auto_plan_delegates_selected_name_without_changing_old_builder() -> None:
    graph, result, refs = make_case(30)
    sentinel = object()
    with patch("recovery.decision.build_recovery_plan", return_value=sentinel) as builder:
        actual = build_auto_plan(graph, object(), "decision", refs[-1], result)
    assert actual is sentinel
    assert builder.call_args.args[0] == "COARSE_CHECKPOINT"
