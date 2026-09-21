"""Structured impact view and FULL/SELECTIVE trace-diff contracts."""
from artifacts import ArtifactRef
from contamination import ContaminationResult, PropagationEdge
from provenance.graph import ArtifactGraph, EdgeType
from provenance.impact_report import build_impact_view, build_trace_diff, render_trace_diff_markdown


def _impact_scenario():
    refs = [ArtifactRef(run_id="trace", artifact_id=name, version=1) for name in ("raw", "metric", "claim", "report", "reusable")]
    raw, metric, claim, report, reusable = refs
    graph = ArtifactGraph()
    graph._graph.add_nodes_from(refs)
    graph._graph.add_edge(raw, metric, type=EdgeType.INPUT)
    graph._graph.add_edge(metric, claim, type=EdgeType.EVIDENCE)
    graph._graph.add_edge(claim, report, type=EdgeType.INPUT)
    result = ContaminationResult(
        run_id="trace", invalidated=[raw], contaminated=[metric, claim, report], unaffected=[reusable],
        propagation_edges=[PropagationEdge(source=raw, target=metric, via=EdgeType.INPUT), PropagationEdge(source=metric, target=claim, via=EdgeType.EVIDENCE), PropagationEdge(source=claim, target=report, via=EdgeType.INPUT)],
    )
    return graph, result


def _rows():
    full = {"chosen_strategy": "FULL_RERUN", "plan_steps": 22, "actual_task_calls": 14, "actual_llm_calls": 6, "fixture_input_tokens": 675, "fixture_output_tokens": 224, "recomputed_artifacts": 22, "reused_artifacts": 0, "residual_contamination": 0, "output_equivalent": True, "recovered_output_fingerprint": "same", "usage_type": "SYNTHETIC"}
    selective = {"chosen_strategy": "SELECTIVE_RECOVERY", "plan_steps": 7, "actual_task_calls": 7, "actual_llm_calls": 2, "fixture_input_tokens": 230, "fixture_output_tokens": 88, "recomputed_artifacts": 7, "reused_artifacts": 15, "residual_contamination": 0, "output_equivalent": True, "recovered_output_fingerprint": "same", "usage_type": "SYNTHETIC"}
    return full, selective


def test_impact_view_summary_and_schema() -> None:
    graph, result = _impact_scenario()
    view = build_impact_view(graph, result)
    assert view["schemaVersion"] == "1.0"
    assert view["summary"]["affectedNodes"] == 4
    assert view["summary"]["affectedRatio"] == 0.8


def test_impact_view_distinguishes_edge_types() -> None:
    graph, result = _impact_scenario()
    edges = build_impact_view(graph, result)["edges"]
    assert {edge["type"] for edge in edges} == {"INPUT", "EVIDENCE"}
    assert all(edge["carriedContamination"] for edge in edges)


def test_impact_view_marks_only_direct_seed() -> None:
    graph, result = _impact_scenario()
    nodes = build_impact_view(graph, result)["nodes"]
    assert [node["artifactId"] for node in nodes if node["directlyInvalidated"]] == ["raw"]


def test_impact_view_is_deterministic() -> None:
    graph, result = _impact_scenario()
    assert build_impact_view(graph, result) == build_impact_view(graph, result)


def test_trace_diff_computes_independent_reductions() -> None:
    diff = build_trace_diff(*_rows())
    assert diff["reductions"] == {"planSteps": 0.681818, "taskCalls": 0.5, "llmCalls": 0.666667, "tokens": 0.646274}


def test_trace_diff_keeps_usage_and_equivalence() -> None:
    diff = build_trace_diff(*_rows())
    assert diff["usageType"] == "SYNTHETIC"
    assert diff["sameRecoveredOutput"] is True


def test_markdown_uses_structured_values() -> None:
    report = render_trace_diff_markdown(build_trace_diff(*_rows()))
    assert "| 实际 task calls | 14 | 7 | 50.00% |" in report
    assert "fixture tokens" in report
