from artifacts import ArtifactRef
from contamination import ContaminationResult, PropagationEdge
from provenance.graph import EdgeType
from recovery.counterfactual import minimal_repair_families, render_counterfactual_report, replay_repairs


def ref(name): return ArtifactRef(run_id="cf", artifact_id=name, version=1)


def combined():
    a, b, metric, claim, target, unrelated = map(ref, ("a", "b", "metric", "claim", "target", "unrelated"))
    result = ContaminationResult(
        run_id="cf", invalidated=[a, b, unrelated], contaminated=[metric, claim, target], unaffected=[],
        propagation_edges=[
            PropagationEdge(source=a, target=metric, via=EdgeType.INPUT),
            PropagationEdge(source=metric, target=claim, via=EdgeType.EVIDENCE),
            PropagationEdge(source=b, target=claim, via=EdgeType.EVIDENCE),
            PropagationEdge(source=claim, target=target, via=EdgeType.INPUT),
        ],
    )
    return result, (a, b, metric, claim, target, unrelated)


def test_no_repairs_leave_target_contaminated() -> None:
    result, refs = combined()
    assert not replay_repairs(result, refs[4], []).target_clean


def test_repair_only_a_is_insufficient() -> None:
    result, refs = combined()
    assert not replay_repairs(result, refs[4], [refs[0]]).target_clean


def test_repair_only_b_is_insufficient() -> None:
    result, refs = combined()
    assert not replay_repairs(result, refs[4], [refs[1]]).target_clean


def test_repair_a_and_b_is_sufficient_for_target() -> None:
    result, refs = combined()
    outcome = replay_repairs(result, refs[4], refs[:2])
    assert outcome.target_clean
    assert not outcome.run_clean  # unrelated source deliberately remains


def test_unrelated_source_does_not_need_target_repair() -> None:
    result, refs = combined()
    families = minimal_repair_families(result, refs[4], [refs[0], refs[1], refs[5]])
    assert families == [(refs[0], refs[1])]


def test_bottleneck_repair_is_an_alternative_minimal_family() -> None:
    result, refs = combined()
    families = minimal_repair_families(result, refs[4], [refs[0], refs[1], refs[3]])
    assert (refs[3],) in families
    assert (refs[0], refs[1]) in families


def test_repairing_target_is_minimal_but_does_not_clean_run() -> None:
    result, refs = combined()
    outcome = replay_repairs(result, refs[4], [refs[4]])
    assert outcome.target_clean and not outcome.run_clean


def test_run_clean_requires_all_sources_or_downstream_state() -> None:
    result, refs = combined()
    assert replay_repairs(result, refs[4], [refs[0], refs[1], refs[5]]).run_clean


def test_report_defines_minimality_and_residual_scope() -> None:
    result, refs = combined()
    families = minimal_repair_families(result, refs[4], [refs[0], refs[1], refs[5]])
    report = render_counterfactual_report(result, refs[4], families)
    assert "集合包含关系最小" in report
    assert "不保证整个 run 无残余污染" in report
