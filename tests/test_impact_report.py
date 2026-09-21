"""Unified impact graph and explainable Markdown report contracts."""
from artifacts import ArtifactRef
from contamination import ContaminationResult, PropagationEdge
from provenance.graph import ArtifactGraph, EdgeType
from provenance.impact import ImpactAnalysis
from provenance.impact_report import build_impact_report, propagation_paths


def ref(name: str, run_id: str = "impact_demo") -> ArtifactRef:
    return ArtifactRef(run_id=run_id, artifact_id=name, version=1)


def scenario():
    raw, metric, claim, report, reusable = map(ref, ["raw", "metric", "claim", "report", "reusable"])
    graph = ArtifactGraph()
    graph._graph.add_nodes_from([raw, metric, claim, report, reusable])
    graph._graph.add_edge(raw, metric, type=EdgeType.INPUT)
    graph._graph.add_edge(metric, claim, type=EdgeType.EVIDENCE)
    graph._graph.add_edge(claim, report, type=EdgeType.INPUT)
    result = ContaminationResult(
        run_id="impact_demo", invalidated=[raw], contaminated=[metric, claim, report],
        unaffected=[reusable],
        propagation_edges=[
            PropagationEdge(source=raw, target=metric, via=EdgeType.INPUT),
            PropagationEdge(source=metric, target=claim, via=EdgeType.EVIDENCE),
            PropagationEdge(source=claim, target=report, via=EdgeType.INPUT),
        ],
    )
    return graph, result, (raw, metric, claim, report, reusable)


def test_unified_analysis_keeps_graph_and_result() -> None:
    graph, result, _ = scenario()
    analysis = ImpactAnalysis(graph, result)
    assert analysis.graph is graph and analysis.contamination is result


def test_unified_analysis_affected_refs() -> None:
    graph, result, refs = scenario()
    assert set(ImpactAnalysis(graph, result).affected) == set(refs[:4])


def test_unified_analysis_affected_ratio() -> None:
    graph, result, _ = scenario()
    assert ImpactAnalysis(graph, result).affected_ratio == 0.8


def test_unified_analysis_rejects_cross_run() -> None:
    graph, result, _ = scenario()
    result.run_id = "other"
    try:
        ImpactAnalysis(graph, result)
    except ValueError as exc:
        assert "same run" in str(exc)
    else:
        raise AssertionError("cross-run analysis must fail")


def test_graph_public_edges_are_typed() -> None:
    graph, _, _ = scenario()
    assert [edge[2] for edge in graph.edges()] == [EdgeType.INPUT, EdgeType.EVIDENCE, EdgeType.INPUT]


def test_graph_artifact_missing_node_returns_none() -> None:
    graph, _, _ = scenario()
    assert graph.artifact(ref("missing")) is None


def test_paths_follow_recorded_propagation() -> None:
    _, result, refs = scenario()
    assert propagation_paths(result) == [list(refs[:4])]


def test_path_with_no_edges_keeps_invalidated_root() -> None:
    root = ref("root")
    result = ContaminationResult(run_id="impact_demo", invalidated=[root], contaminated=[], unaffected=[], propagation_edges=[])
    assert propagation_paths(result) == [[root]]


def test_report_has_node_list() -> None:
    graph, result, _ = scenario()
    assert "## 节点列表" in build_impact_report(graph, result)


def test_report_has_edge_list() -> None:
    graph, result, _ = scenario()
    assert "## 边列表" in build_impact_report(graph, result)


def test_report_has_propagation_paths() -> None:
    graph, result, _ = scenario()
    assert "`raw@v1` → `metric@v1` → `claim@v1` → `report@v1`" in build_impact_report(graph, result)


def test_report_has_mermaid_path_graph() -> None:
    graph, result, _ = scenario()
    report = build_impact_report(graph, result)
    assert "```mermaid" in report and "flowchart LR" in report
    assert 'N1["metric@v1"]' in report
    assert "N2 -->|INPUT| N1" in report
    assert "N1 -->|EVIDENCE| N0" in report


def test_report_has_reuse_advice() -> None:
    graph, result, _ = scenario()
    assert "`reusable@v1`" in build_impact_report(graph, result).split("## 复用建议", 1)[1]


def test_report_displays_affected_ratio() -> None:
    graph, result, _ = scenario()
    assert "受影响节点：4（80.00%）" in build_impact_report(graph, result)


def test_report_stays_below_two_hundred_lines() -> None:
    graph, result, _ = scenario()
    assert len(build_impact_report(graph, result).splitlines()) < 200
