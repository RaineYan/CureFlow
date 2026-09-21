"""
Tests for Day 8 — Dependency-aware Selective Recovery.

Coverage (30 tests, matches the Day 8 spec):
   1.  Planner produces a plan from a ContaminationResult
   2.  Plan steps are topologically ordered (deepest dependency first)
   3.  INVALIDATED Field -> TOOL_RECOMPUTE
   4.  CONTAMINATED Tool -> TOOL_RECOMPUTE
   5.  CONTAMINATED LLM -> REPROMPT
   6.  UNAFFECTED -> REUSE
   7.  replacement_map maps old ref to new ref
   8.  Field v2 -> recovered Field v3
   9.  recovered Field passes raw validation
  10.  recovered Field is marked RECOVERED
  11.  metric_revenue_growth v3 references field_revenue_2025 v3
  12.  profit_margin v3 references field_revenue_2025 v3
  13.  cost_ratio v3 references field_revenue_2025 v3
  14.  Financial Claim rerun uses three valid/recovered Metric inputs
  15.  Financial Claim v3 evidence updates precisely
  16.  Financial Section v3 references Claim v3
  17.  User Claim recovery phase: 0 LLM calls
  18.  User Section recovery phase: 0 LLM calls
  19.  Operation Claim recovery phase: 0 LLM calls
  20.  Operation Section recovery phase: 0 LLM calls
  21.  report_merged v3 = fin v3 + user v1 + op v1
  22.  old faulty v2 statuses are NOT modified
  23.  old baseline v1 not drifted
  24.  final target clean-path validation PASS
  25.  cost_ratio-only case: only cost_ratio recovered
  26.  cost_ratio-only case: report v1 REUSED (no v2)
  27.  cost_ratio-only case: no new Financial Claim
  28.  ExecutedStep log correctness
  29.  Graph reload + recovery stays consistent
  30.  Day 1-7 tests still pass (covered by full pytest run)
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from artifacts import (
    Artifact,
    ArtifactRef,
    ArtifactStatus,
    ArtifactStore,
    ArtifactType,
)
from contamination import (
    detect_field_faults,
    propagate_contamination,
)
from experiments import inject_field_value_tamper
from llm import FakeLLMClient, LLMResponse, default_workflow_llm
from provenance.decorator import RunContext, track_provenance
from provenance.graph import ArtifactGraph
from recovery import (
    RecoveryAction,
    RecoveryExecutor,
    SelectiveRecoveryPlanner,
    validate_target_clean_path,
)
from recovery.models import RecoveryPlan, RecoveryStep
from workflow.claim_tasks import (
    generate_financial_claim,
    generate_financial_section,
    generate_operation_claim,
    generate_operation_section,
    generate_user_claim,
    generate_user_section,
    merge_report,
)
from workflow.recompute import (
    mcr_recompute,
    mpm_recompute,
    mrg_recompute,
    recompute_field_artifact_from_raw,
)
from workflow.runner import run_baseline_workflow
from workflow.tasks import (
    compute_conversion_rate,
    compute_cost_ratio,
    compute_order_growth,
    compute_profit_margin,
    compute_revenue_growth,
    compute_user_growth,
    extract_fields,
)


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture
def store(tmp_path: Path) -> ArtifactStore:
    base = tmp_path / "artifacts"
    s = ArtifactStore(base_dir=str(base))
    yield s
    shutil.rmtree(base, ignore_errors=True)


@pytest.fixture
def csv_path() -> str:
    return str(Path(__file__).resolve().parent.parent / "data" / "sample_company.csv")


# Decorated versions for in-place recompute (v3) inside the test
@track_provenance(output_artifact_id="metric_revenue_growth", artifact_type=ArtifactType.METRIC)
def mrg_decorated(revenue_2025, revenue_2024, *, ctx):
    return (revenue_2025.value - revenue_2024.value) / revenue_2024.value


@track_provenance(output_artifact_id="metric_profit_margin", artifact_type=ArtifactType.METRIC)
def mpm_decorated(revenue_2025, cost_2025, *, ctx):
    return (revenue_2025.value - cost_2025.value) / revenue_2025.value


@track_provenance(output_artifact_id="metric_cost_ratio", artifact_type=ArtifactType.METRIC)
def mcr_decorated(cost_2025, revenue_2025, *, ctx):
    return cost_2025.value / revenue_2025.value


def _build_faulty_snapshot(store: ArtifactStore, run_id: str, llm: FakeLLMClient):
    """Run baseline + tamper + re-merge the 3 financial metrics (v2).
    Returns the baseline result."""
    result = run_baseline_workflow(csv_path_fixture(), run_id, store, llm)
    inject_field_value_tamper(store, run_id, "field_revenue_2025", factor=10.0)
    ctx = RunContext(run_id=run_id, store=store)
    mrg_decorated(
        store.get(run_id, "field_revenue_2025"),
        store.get(run_id, "field_revenue_2024"),
        ctx=ctx,
    )
    mpm_decorated(
        store.get(run_id, "field_revenue_2025"),
        store.get(run_id, "field_cost_2025"),
        ctx=ctx,
    )
    mcr_decorated(
        store.get(run_id, "field_cost_2025"),
        store.get(run_id, "field_revenue_2025"),
        ctx=ctx,
    )
    fc = generate_financial_claim(
        store.get(run_id, "metric_revenue_growth"),
        store.get(run_id, "metric_profit_margin"),
        store.get(run_id, "metric_cost_ratio"),
        ctx=ctx, llm_client=llm,
    )
    fs = generate_financial_section(fc, ctx=ctx, llm_client=llm)
    merge_report(fs, result.sections["user"], result.sections["operation"], ctx=ctx)
    return result


def csv_path_fixture() -> str:
    return str(Path(__file__).resolve().parent.parent / "data" / "sample_company.csv")


# =====================================================================
# 1-2: planner basics + topological order
# =====================================================================

def test_planner_produces_plan_from_contamination(store: ArtifactStore) -> None:
    llm = default_workflow_llm()
    _build_faulty_snapshot(store, "run_rec", llm)
    detect_field_faults("run_rec", store)
    g = ArtifactGraph.from_store(store, "run_rec")
    result = propagate_contamination(g, store, "run_rec")

    target = ArtifactRef(run_id="run_rec", artifact_id="report_merged", version=2)
    plan = SelectiveRecoveryPlanner(g, store, "run_rec", target, result)
    assert isinstance(plan, RecoveryPlan)
    assert plan.run_id == "run_rec"
    assert plan.target_ref == target
    # main case expects 7 steps (4 metrics + 1 field + 1 claim + 1 section + 1 merge)
    assert len(plan.steps) >= 5
    # all steps have increasing order
    orders = [s.order for s in plan.steps]
    assert orders == sorted(orders)
    assert list(range(1, len(orders) + 1)) == orders


def test_planner_topological_order(store: ArtifactStore) -> None:
    """field recovery (deepest dep) must come before metric recovery, etc."""
    llm = default_workflow_llm()
    _build_faulty_snapshot(store, "run_rec2", llm)
    detect_field_faults("run_rec2", store)
    g = ArtifactGraph.from_store(store, "run_rec2")
    result = propagate_contamination(g, store, "run_rec2")
    target = ArtifactRef(run_id="run_rec2", artifact_id="report_merged", version=2)
    plan = SelectiveRecoveryPlanner(g, store, "run_rec2", target, result)
    # Find indices of each step type by target
    idx_by_aid = {s.target_ref.artifact_id: i for i, s in enumerate(plan.steps)}
    field_idx = idx_by_aid["field_revenue_2025"]
    metric_idx = max(idx_by_aid[a] for a in ("metric_revenue_growth",
                                              "metric_profit_margin",
                                              "metric_cost_ratio"))
    claim_idx = idx_by_aid["claim_financial_growth"]
    section_idx = idx_by_aid["claim_report_financial_section"]
    merged_idx = idx_by_aid["report_merged"]
    assert field_idx < metric_idx
    assert claim_idx > field_idx
    assert section_idx > claim_idx
    assert merged_idx > section_idx


# =====================================================================
# 3-6: action selection rules
# =====================================================================

def test_invalidated_field_becomes_tool_recompute(store: ArtifactStore) -> None:
    llm = default_workflow_llm()
    _build_faulty_snapshot(store, "run_r1", llm)
    detect_field_faults("run_r1", store)
    g = ArtifactGraph.from_store(store, "run_r1")
    result = propagate_contamination(g, store, "run_r1")
    target = ArtifactRef(run_id="run_r1", artifact_id="report_merged", version=2)
    plan = SelectiveRecoveryPlanner(g, store, "run_r1", target, result)
    field_step = next(s for s in plan.steps if s.target_ref.artifact_id == "field_revenue_2025")
    assert field_step.action == RecoveryAction.TOOL_RECOMPUTE


def test_contaminated_tool_becomes_tool_recompute(store: ArtifactStore) -> None:
    llm = default_workflow_llm()
    _build_faulty_snapshot(store, "run_r2", llm)
    detect_field_faults("run_r2", store)
    g = ArtifactGraph.from_store(store, "run_r2")
    result = propagate_contamination(g, store, "run_r2")
    target = ArtifactRef(run_id="run_r2", artifact_id="report_merged", version=2)
    plan = SelectiveRecoveryPlanner(g, store, "run_r2", target, result)
    metric_steps = [s for s in plan.steps
                    if s.target_ref.artifact_id.startswith("metric_")]
    assert all(s.action == RecoveryAction.TOOL_RECOMPUTE for s in metric_steps)


def test_contaminated_llm_becomes_reprompt(store: ArtifactStore) -> None:
    llm = default_workflow_llm()
    _build_faulty_snapshot(store, "run_r3", llm)
    detect_field_faults("run_r3", store)
    g = ArtifactGraph.from_store(store, "run_r3")
    result = propagate_contamination(g, store, "run_r3")
    target = ArtifactRef(run_id="run_r3", artifact_id="report_merged", version=2)
    plan = SelectiveRecoveryPlanner(g, store, "run_r3", target, result)
    claim_step = next(s for s in plan.steps
                       if s.target_ref.artifact_id == "claim_financial_growth")
    section_step = next(s for s in plan.steps
                         if s.target_ref.artifact_id == "claim_report_financial_section")
    assert claim_step.action == RecoveryAction.REPROMPT
    assert section_step.action == RecoveryAction.REPROMPT


def test_unaffected_artifact_listed_as_reuse(store: ArtifactStore) -> None:
    llm = default_workflow_llm()
    _build_faulty_snapshot(store, "run_r4", llm)
    detect_field_faults("run_r4", store)
    g = ArtifactGraph.from_store(store, "run_r4")
    result = propagate_contamination(g, store, "run_r4")
    target = ArtifactRef(run_id="run_r4", artifact_id="report_merged", version=2)
    plan = SelectiveRecoveryPlanner(g, store, "run_r4", target, result)
    # user / op v1 sections should be reused
    reused_aids = {r.artifact_id for r in plan.reused_refs}
    assert "claim_report_user_section" in reused_aids
    assert "claim_report_operation_section" in reused_aids


# =====================================================================
# 7-13: executor + version remapping
# =====================================================================

def test_executor_replacement_map(store: ArtifactStore) -> None:
    llm = default_workflow_llm()
    _build_faulty_snapshot(store, "run_e1", llm)
    detect_field_faults("run_e1", store)
    g = ArtifactGraph.from_store(store, "run_e1")
    result = propagate_contamination(g, store, "run_e1")
    target = ArtifactRef(run_id="run_e1", artifact_id="report_merged", version=2)
    plan = SelectiveRecoveryPlanner(g, store, "run_e1", target, result)

    executor = RecoveryExecutor(store, g, llm)
    out = executor.execute(plan)

    # every TOOL_RECOMPUTE / REPROMPT must produce a mapping entry
    for step in plan.steps:
        if step.action != RecoveryAction.REUSE:
            assert step.target_ref.to_ref_string() in out.replacement_map
    # REUSE steps must not appear in the map
    for step in plan.steps:
        if step.action == RecoveryAction.REUSE:
            assert step.target_ref.to_ref_string() not in out.replacement_map


def test_field_v2_to_recovered_v3(store: ArtifactStore) -> None:
    llm = default_workflow_llm()
    _build_faulty_snapshot(store, "run_e2", llm)
    detect_field_faults("run_e2", store)
    g = ArtifactGraph.from_store(store, "run_e2")
    result = propagate_contamination(g, store, "run_e2")
    target = ArtifactRef(run_id="run_e2", artifact_id="report_merged", version=2)
    plan = SelectiveRecoveryPlanner(g, store, "run_e2", target, result)
    executor = RecoveryExecutor(store, g, llm)
    out = executor.execute(plan)

    # field_revenue_2025 v2 -> v3
    new_field_ref = ArtifactRef.from_ref_string(
        out.replacement_map[ArtifactRef(run_id="run_e2", artifact_id="field_revenue_2025", version=2).to_ref_string()]
    )
    assert new_field_ref.version == 3
    recovered = store.get_version("run_e2", "field_revenue_2025", 3)
    assert recovered is not None
    assert recovered.value == pytest.approx(24_600_000.0)  # back to ground truth
    assert recovered.validation_status == "RECOVERED"


def test_recovered_field_passes_raw_validation(store: ArtifactStore) -> None:
    llm = default_workflow_llm()
    _build_faulty_snapshot(store, "run_e3", llm)
    detect_field_faults("run_e3", store)
    g = ArtifactGraph.from_store(store, "run_e3")
    result = propagate_contamination(g, store, "run_e3")
    target = ArtifactRef(run_id="run_e3", artifact_id="report_merged", version=2)
    plan = SelectiveRecoveryPlanner(g, store, "run_e3", target, result)
    executor = RecoveryExecutor(store, g, llm)
    executor.execute(plan)

    from contamination import validate_field_against_raw
    raw = store.get("run_e3", "raw_data")
    recovered = store.get_version("run_e3", "field_revenue_2025", 3)
    assert recovered is not None
    res = validate_field_against_raw(recovered, raw)
    assert res.passed is True


def test_recovered_field_marked_recovered(store: ArtifactStore) -> None:
    llm = default_workflow_llm()
    _build_faulty_snapshot(store, "run_e4", llm)
    detect_field_faults("run_e4", store)
    g = ArtifactGraph.from_store(store, "run_e4")
    result = propagate_contamination(g, store, "run_e4")
    target = ArtifactRef(run_id="run_e4", artifact_id="report_merged", version=2)
    plan = SelectiveRecoveryPlanner(g, store, "run_e4", target, result)
    executor = RecoveryExecutor(store, g, llm)
    executor.execute(plan)
    latest = store.get("run_e4", "field_revenue_2025")
    assert latest is not None
    assert latest.validation_status == "RECOVERED"


def test_metric_revenue_v3_references_field_revenue_v3(store: ArtifactStore) -> None:
    llm = default_workflow_llm()
    _build_faulty_snapshot(store, "run_e5", llm)
    detect_field_faults("run_e5", store)
    g = ArtifactGraph.from_store(store, "run_e5")
    result = propagate_contamination(g, store, "run_e5")
    target = ArtifactRef(run_id="run_e5", artifact_id="report_merged", version=2)
    plan = SelectiveRecoveryPlanner(g, store, "run_e5", target, result)
    executor = RecoveryExecutor(store, g, llm)
    executor.execute(plan)
    m = store.get_version("run_e5", "metric_revenue_growth", 3)
    assert m is not None
    input_keys = {(r.artifact_id, r.version) for r in m.input_artifact_ids}
    assert ("field_revenue_2025", 3) in input_keys
    assert ("field_revenue_2024", 1) in input_keys


def test_profit_margin_v3_references_field_revenue_v3(store: ArtifactStore) -> None:
    llm = default_workflow_llm()
    _build_faulty_snapshot(store, "run_e6", llm)
    detect_field_faults("run_e6", store)
    g = ArtifactGraph.from_store(store, "run_e6")
    result = propagate_contamination(g, store, "run_e6")
    target = ArtifactRef(run_id="run_e6", artifact_id="report_merged", version=2)
    plan = SelectiveRecoveryPlanner(g, store, "run_e6", target, result)
    executor = RecoveryExecutor(store, g, llm)
    executor.execute(plan)
    m = store.get_version("run_e6", "metric_profit_margin", 3)
    assert m is not None
    input_keys = {(r.artifact_id, r.version) for r in m.input_artifact_ids}
    assert ("field_revenue_2025", 3) in input_keys
    assert ("field_cost_2025", 1) in input_keys


def test_cost_ratio_v3_references_field_revenue_v3(store: ArtifactStore) -> None:
    llm = default_workflow_llm()
    _build_faulty_snapshot(store, "run_e7", llm)
    detect_field_faults("run_e7", store)
    g = ArtifactGraph.from_store(store, "run_e7")
    result = propagate_contamination(g, store, "run_e7")
    target = ArtifactRef(run_id="run_e7", artifact_id="report_merged", version=2)
    plan = SelectiveRecoveryPlanner(g, store, "run_e7", target, result)
    executor = RecoveryExecutor(store, g, llm)
    executor.execute(plan)
    m = store.get_version("run_e7", "metric_cost_ratio", 3)
    assert m is not None
    input_keys = {(r.artifact_id, r.version) for r in m.input_artifact_ids}
    assert ("field_revenue_2025", 3) in input_keys
    assert ("field_cost_2025", 1) in input_keys


def test_financial_claim_uses_recovered_metric_inputs(store: ArtifactStore) -> None:
    llm = default_workflow_llm()
    _build_faulty_snapshot(store, "run_e8", llm)
    detect_field_faults("run_e8", store)
    g = ArtifactGraph.from_store(store, "run_e8")
    result = propagate_contamination(g, store, "run_e8")
    target = ArtifactRef(run_id="run_e8", artifact_id="report_merged", version=2)
    plan = SelectiveRecoveryPlanner(g, store, "run_e8", target, result)
    executor = RecoveryExecutor(store, g, llm)
    executor.execute(plan)
    fc = store.get_version("run_e8", "claim_financial_growth", 3)
    assert fc is not None
    input_keys = {(r.artifact_id, r.version) for r in fc.input_artifact_ids}
    assert ("metric_revenue_growth", 3) in input_keys
    assert ("metric_profit_margin", 3) in input_keys
    assert ("metric_cost_ratio", 3) in input_keys
    # evidence should be the FakeLLM default for financial: revenue + profit
    evidence_keys = {(r.artifact_id, r.version) for r in fc.evidence_ids}
    assert ("metric_revenue_growth", 3) in evidence_keys
    assert ("metric_profit_margin", 3) in evidence_keys


def test_financial_section_v3_references_claim_v3(store: ArtifactStore) -> None:
    llm = default_workflow_llm()
    _build_faulty_snapshot(store, "run_e9", llm)
    detect_field_faults("run_e9", store)
    g = ArtifactGraph.from_store(store, "run_e9")
    result = propagate_contamination(g, store, "run_e9")
    target = ArtifactRef(run_id="run_e9", artifact_id="report_merged", version=2)
    plan = SelectiveRecoveryPlanner(g, store, "run_e9", target, result)
    executor = RecoveryExecutor(store, g, llm)
    executor.execute(plan)
    fs = store.get_version("run_e9", "claim_report_financial_section", 3)
    assert fs is not None
    input_keys = {(r.artifact_id, r.version) for r in fs.input_artifact_ids}
    assert ("claim_financial_growth", 3) in input_keys
    evidence_keys = {(r.artifact_id, r.version) for r in fs.evidence_ids}
    assert ("claim_financial_growth", 3) in evidence_keys


def test_financial_claim_v3_evidence_drops_bad_versions(store: ArtifactStore) -> None:
    """The v3 financial claim's evidence_ids must point only at v3
    metrics — no v2 (bad) versions survive in the new evidence chain."""
    llm = default_workflow_llm()
    _build_faulty_snapshot(store, "run_fc3", llm)
    detect_field_faults("run_fc3", store)
    g = ArtifactGraph.from_store(store, "run_fc3")
    result = propagate_contamination(g, store, "run_fc3")
    target = ArtifactRef(run_id="run_fc3", artifact_id="report_merged", version=2)
    plan = SelectiveRecoveryPlanner(g, store, "run_fc3", target, result)
    RecoveryExecutor(store, g, llm).execute(plan)
    fc = store.get_version("run_fc3", "claim_financial_growth", 3)
    assert fc is not None
    evidence_versions = {(r.artifact_id, r.version) for r in fc.evidence_ids}
    # nothing in evidence should be a v2
    for aid, ver in evidence_versions:
        assert ver == 3, f"unexpected v{ver} in evidence for {aid}"


def test_field_v1_unchanged_after_recovery(store: ArtifactStore) -> None:
    """v1 field_revenue_2025 must keep its original ground-truth value
    even after recovery produces v3."""
    llm = default_workflow_llm()
    _build_faulty_snapshot(store, "run_fv1", llm)
    detect_field_faults("run_fv1", store)
    g = ArtifactGraph.from_store(store, "run_fv1")
    result = propagate_contamination(g, store, "run_fv1")
    target = ArtifactRef(run_id="run_fv1", artifact_id="report_merged", version=2)
    plan = SelectiveRecoveryPlanner(g, store, "run_fv1", target, result)
    RecoveryExecutor(store, g, llm).execute(plan)
    v1 = store.get_version("run_fv1", "field_revenue_2025", 1)
    assert v1 is not None
    # v1 was the original (ground-truth) value, never tampered
    assert v1.value == pytest.approx(24_600_000.0)
    # v1 was NEVER marked INVALIDATED
    assert v1.validation_status == "VALID"


def test_recovered_field_value_matches_ground_truth(store: ArtifactStore) -> None:
    """The recovered v3 field must equal the recomputed value from raw."""
    llm = default_workflow_llm()
    _build_faulty_snapshot(store, "run_gt", llm)
    detect_field_faults("run_gt", store)
    g = ArtifactGraph.from_store(store, "run_gt")
    result = propagate_contamination(g, store, "run_gt")
    target = ArtifactRef(run_id="run_gt", artifact_id="report_merged", version=2)
    plan = SelectiveRecoveryPlanner(g, store, "run_gt", target, result)
    RecoveryExecutor(store, g, llm).execute(plan)
    v3 = store.get_version("run_gt", "field_revenue_2025", 3)
    raw = store.get_version("run_gt", "raw_data", 1)
    assert v3 is not None and raw is not None
    from workflow.tasks import recompute_field_from_raw
    expected = recompute_field_from_raw(
        raw, "field_revenue_2025", aggregation="sum",
    )
    assert v3.value == pytest.approx(expected)


def test_recovery_step_count_in_main_case(store: ArtifactStore) -> None:
    """Main case plan should have exactly 7 steps:
    1 field + 3 metrics + 1 claim + 1 section + 1 merged = 7."""
    llm = default_workflow_llm()
    _build_faulty_snapshot(store, "run_sc", llm)
    detect_field_faults("run_sc", store)
    g = ArtifactGraph.from_store(store, "run_sc")
    result = propagate_contamination(g, store, "run_sc")
    target = ArtifactRef(run_id="run_sc", artifact_id="report_merged", version=2)
    plan = SelectiveRecoveryPlanner(g, store, "run_sc", target, result)
    assert len(plan.steps) == 7, f"got {len(plan.steps)} steps"


# =====================================================================
# 17-20: user/op branches are not touched
# =====================================================================

def test_user_op_branches_not_regenerated(store: ArtifactStore) -> None:
    """The FakeLLM call log must show 0 calls into user/op tasks during recovery."""
    llm = default_workflow_llm()
    # Use a fresh FakeLLM so we count only recovery-phase calls
    recovery_llm = FakeLLMClient(task_responders={
        "generate_financial_claim":   llm._task_responders["generate_financial_claim"],
        "generate_financial_section": llm._task_responders["generate_financial_section"],
    })

    _build_faulty_snapshot(store, "run_e10", recovery_llm)
    # Clear the call log so we only inspect RECOVERY-PHASE calls.
    recovery_llm.calls.clear()
    detect_field_faults("run_e10", store)
    g = ArtifactGraph.from_store(store, "run_e10")
    result = propagate_contamination(g, store, "run_e10")
    target = ArtifactRef(run_id="run_e10", artifact_id="report_merged", version=2)
    plan = SelectiveRecoveryPlanner(g, store, "run_e10", target, result)
    executor = RecoveryExecutor(store, g, recovery_llm)
    executor.execute(plan)

    task_names_called = [name for name, _ in recovery_llm.calls if name]
    # exactly 2 LLM calls in recovery: financial claim + financial section
    assert "generate_financial_claim" in task_names_called
    assert "generate_financial_section" in task_names_called
    # none of the user / operation tasks were called
    for forbidden in (
        "generate_user_claim",
        "generate_user_section",
        "generate_operation_claim",
        "generate_operation_section",
    ):
        assert forbidden not in task_names_called, (
            f"{forbidden} should not be called during recovery"
        )


# =====================================================================
# 17-20: per-task isolation (split out from the combined test above)
# =====================================================================

def test_user_claim_not_called_in_recovery(store: ArtifactStore) -> None:
    llm = default_workflow_llm()
    recovery_llm = FakeLLMClient(task_responders={
        "generate_financial_claim":   llm._task_responders["generate_financial_claim"],
        "generate_financial_section": llm._task_responders["generate_financial_section"],
    })
    _build_faulty_snapshot(store, "run_uc", recovery_llm)
    recovery_llm.calls.clear()
    detect_field_faults("run_uc", store)
    g = ArtifactGraph.from_store(store, "run_uc")
    result = propagate_contamination(g, store, "run_uc")
    target = ArtifactRef(run_id="run_uc", artifact_id="report_merged", version=2)
    plan = SelectiveRecoveryPlanner(g, store, "run_uc", target, result)
    RecoveryExecutor(store, g, recovery_llm).execute(plan)
    called = [n for n, _ in recovery_llm.calls if n]
    assert "generate_user_claim" not in called


def test_user_section_not_called_in_recovery(store: ArtifactStore) -> None:
    llm = default_workflow_llm()
    recovery_llm = FakeLLMClient(task_responders={
        "generate_financial_claim":   llm._task_responders["generate_financial_claim"],
        "generate_financial_section": llm._task_responders["generate_financial_section"],
    })
    _build_faulty_snapshot(store, "run_us", recovery_llm)
    recovery_llm.calls.clear()
    detect_field_faults("run_us", store)
    g = ArtifactGraph.from_store(store, "run_us")
    result = propagate_contamination(g, store, "run_us")
    target = ArtifactRef(run_id="run_us", artifact_id="report_merged", version=2)
    plan = SelectiveRecoveryPlanner(g, store, "run_us", target, result)
    RecoveryExecutor(store, g, recovery_llm).execute(plan)
    called = [n for n, _ in recovery_llm.calls if n]
    assert "generate_user_section" not in called


def test_operation_claim_not_called_in_recovery(store: ArtifactStore) -> None:
    llm = default_workflow_llm()
    recovery_llm = FakeLLMClient(task_responders={
        "generate_financial_claim":   llm._task_responders["generate_financial_claim"],
        "generate_financial_section": llm._task_responders["generate_financial_section"],
    })
    _build_faulty_snapshot(store, "run_oc", recovery_llm)
    recovery_llm.calls.clear()
    detect_field_faults("run_oc", store)
    g = ArtifactGraph.from_store(store, "run_oc")
    result = propagate_contamination(g, store, "run_oc")
    target = ArtifactRef(run_id="run_oc", artifact_id="report_merged", version=2)
    plan = SelectiveRecoveryPlanner(g, store, "run_oc", target, result)
    RecoveryExecutor(store, g, recovery_llm).execute(plan)
    called = [n for n, _ in recovery_llm.calls if n]
    assert "generate_operation_claim" not in called


def test_operation_section_not_called_in_recovery(store: ArtifactStore) -> None:
    llm = default_workflow_llm()
    recovery_llm = FakeLLMClient(task_responders={
        "generate_financial_claim":   llm._task_responders["generate_financial_claim"],
        "generate_financial_section": llm._task_responders["generate_financial_section"],
    })
    _build_faulty_snapshot(store, "run_os", recovery_llm)
    recovery_llm.calls.clear()
    detect_field_faults("run_os", store)
    g = ArtifactGraph.from_store(store, "run_os")
    result = propagate_contamination(g, store, "run_os")
    target = ArtifactRef(run_id="run_os", artifact_id="report_merged", version=2)
    plan = SelectiveRecoveryPlanner(g, store, "run_os", target, result)
    RecoveryExecutor(store, g, recovery_llm).execute(plan)
    called = [n for n, _ in recovery_llm.calls if n]
    assert "generate_operation_section" not in called


# =====================================================================
# 21: report_merged v3 = fin v3 + user v1 + op v1
# =====================================================================

def test_merged_report_v3_pins_mixed_versions(store: ArtifactStore) -> None:
    llm = default_workflow_llm()
    _build_faulty_snapshot(store, "run_e11", llm)
    detect_field_faults("run_e11", store)
    g = ArtifactGraph.from_store(store, "run_e11")
    result = propagate_contamination(g, store, "run_e11")
    target = ArtifactRef(run_id="run_e11", artifact_id="report_merged", version=2)
    plan = SelectiveRecoveryPlanner(g, store, "run_e11", target, result)
    executor = RecoveryExecutor(store, g, llm)
    executor.execute(plan)
    merged = store.get_version("run_e11", "report_merged", 3)
    assert merged is not None
    input_keys = {(r.artifact_id, r.version) for r in merged.input_artifact_ids}
    assert input_keys == {
        ("claim_report_financial_section", 3),
        ("claim_report_user_section", 1),
        ("claim_report_operation_section", 1),
    }


# =====================================================================
# 22-23: old statuses not modified, old v1 not drifted
# =====================================================================

def test_old_faulty_v2_statuses_not_modified(store: ArtifactStore) -> None:
    llm = default_workflow_llm()
    _build_faulty_snapshot(store, "run_e12", llm)
    detect_field_faults("run_e12", store)
    g = ArtifactGraph.from_store(store, "run_e12")
    result = propagate_contamination(g, store, "run_e12")
    target = ArtifactRef(run_id="run_e12", artifact_id="report_merged", version=2)
    plan = SelectiveRecoveryPlanner(g, store, "run_e12", target, result)
    executor = RecoveryExecutor(store, g, llm)
    executor.execute(plan)
    # old v2 field is still INVALIDATED
    field_v2 = store.get_version("run_e12", "field_revenue_2025", 2)
    assert field_v2 is not None
    assert field_v2.validation_status == "INVALIDATED"
    # old v2 financial claim is still CONTAMINATED
    claim_v2 = store.get_version("run_e12", "claim_financial_growth", 2)
    assert claim_v2 is not None
    assert claim_v2.validation_status == "CONTAMINATED"


def test_baseline_v1_not_drifted(store: ArtifactStore) -> None:
    llm = default_workflow_llm()
    _build_faulty_snapshot(store, "run_e13", llm)
    detect_field_faults("run_e13", store)
    g = ArtifactGraph.from_store(store, "run_e13")
    result = propagate_contamination(g, store, "run_e13")
    target = ArtifactRef(run_id="run_e13", artifact_id="report_merged", version=2)
    plan = SelectiveRecoveryPlanner(g, store, "run_e13", target, result)
    executor = RecoveryExecutor(store, g, llm)
    executor.execute(plan)
    # v1 baseline still exists and is VALID
    claim_v1 = store.get_version("run_e13", "claim_financial_growth", 1)
    assert claim_v1 is not None
    assert claim_v1.validation_status == "VALID"
    assert claim_v1.content  # content unchanged
    # field v1 revenue_2024 still has its old value
    field_rev_2024_v1 = store.get_version("run_e13", "field_revenue_2024", 1)
    assert field_rev_2024_v1 is not None
    assert field_rev_2024_v1.value == pytest.approx(15_300_000.0)


# =====================================================================
# 24: final target clean-path validation PASS
# =====================================================================

def test_final_target_clean_path_passes(store: ArtifactStore) -> None:
    llm = default_workflow_llm()
    _build_faulty_snapshot(store, "run_e14", llm)
    detect_field_faults("run_e14", store)
    g = ArtifactGraph.from_store(store, "run_e14")
    result = propagate_contamination(g, store, "run_e14")
    target = ArtifactRef(run_id="run_e14", artifact_id="report_merged", version=2)
    plan = SelectiveRecoveryPlanner(g, store, "run_e14", target, result)
    executor = RecoveryExecutor(store, g, llm)
    out = executor.execute(plan)

    assert out.success is True, out.failure_reason
    # final target is report_merged@v3
    final = out.final_target_refs[0]
    assert final.artifact_id == "report_merged"
    assert final.version == 3


# =====================================================================
# 25-27: Control case (cost_ratio-only)
# =====================================================================

def test_control_case_cost_ratio_only_recovered(store: ArtifactStore) -> None:
    llm = default_workflow_llm()
    # baseline only
    run_baseline_workflow(csv_path_fixture(), "run_ctrl", store, llm)
    # INVALIDATE cost_ratio ONLY (INPUT-only, not in financial_claim EVIDENCE)
    store.update_status("run_ctrl", "metric_cost_ratio", ArtifactStatus.INVALIDATED)
    g = ArtifactGraph.from_store(store, "run_ctrl")
    result = propagate_contamination(g, store, "run_ctrl")
    # Target the report v1 — it's VALID. Planner should not produce steps.
    target = ArtifactRef(run_id="run_ctrl", artifact_id="report_merged", version=1)
    plan = SelectiveRecoveryPlanner(g, store, "run_ctrl", target, result)

    # Planner: target is already VALID — its closure contains no bad nodes
    # that need recovery for THIS target. Steps should be empty.
    assert plan.steps == [], f"expected empty plan, got {[s.target_ref.artifact_id for s in plan.steps]}"

    # But metric_cost_ratio is the seed (invalidated) — it needs recovery
    # for the broader run. Let's say we WANT to recover cost_ratio only.
    cr_ref = ArtifactRef(run_id="run_ctrl", artifact_id="metric_cost_ratio", version=1)
    cr_plan = SelectiveRecoveryPlanner(g, store, "run_ctrl", cr_ref, result)
    cr_executor = RecoveryExecutor(store, g, llm)
    cr_out = cr_executor.execute(cr_plan)
    assert cr_out.success is True
    # cost_ratio v2 should exist
    cr_v2 = store.get_version("run_ctrl", "metric_cost_ratio", 2)
    assert cr_v2 is not None


def test_control_case_report_v1_reused_not_bumped(store: ArtifactStore) -> None:
    """When target report_merged@v1 was never contaminated, recovery
    must NOT create a report_merged@v2 just for version symmetry."""
    llm = default_workflow_llm()
    run_baseline_workflow(csv_path_fixture(), "run_ctrl2", store, llm)
    store.update_status("run_ctrl2", "metric_cost_ratio", ArtifactStatus.INVALIDATED)
    g = ArtifactGraph.from_store(store, "run_ctrl2")
    result = propagate_contamination(g, store, "run_ctrl2")
    target = ArtifactRef(run_id="run_ctrl2", artifact_id="report_merged", version=1)
    plan = SelectiveRecoveryPlanner(g, store, "run_ctrl2", target, result)

    executor = RecoveryExecutor(store, g, llm)
    out = executor.execute(plan)

    assert out.success is True
    # final target is the original v1 (not bumped to v2)
    final = out.final_target_refs[0]
    assert final.version == 1
    # No new report_merged created
    assert store.list_versions("run_ctrl2", "report_merged") == [1]


def test_control_case_no_new_financial_claim(store: ArtifactStore) -> None:
    """Cost_ratio-only control case must NOT trigger a new financial_claim."""
    llm = default_workflow_llm()
    # fresh LLM so we can count calls
    recovery_llm = FakeLLMClient(task_responders={
        "generate_financial_claim":   llm._task_responders["generate_financial_claim"],
        "generate_financial_section": llm._task_responders["generate_financial_section"],
    })
    run_baseline_workflow(csv_path_fixture(), "run_ctrl3", store, recovery_llm)
    # Clear the call log so we only inspect RECOVERY-PHASE calls.
    recovery_llm.calls.clear()
    store.update_status("run_ctrl3", "metric_cost_ratio", ArtifactStatus.INVALIDATED)
    g = ArtifactGraph.from_store(store, "run_ctrl3")
    result = propagate_contamination(g, store, "run_ctrl3")
    target = ArtifactRef(run_id="run_ctrl3", artifact_id="report_merged", version=1)
    plan = SelectiveRecoveryPlanner(g, store, "run_ctrl3", target, result)
    executor = RecoveryExecutor(store, g, recovery_llm)
    executor.execute(plan)
    # LLM call log: financial claim / section NEVER called
    task_names = [name for name, _ in recovery_llm.calls if name]
    for forbidden in ("generate_financial_claim", "generate_financial_section",
                      "generate_user_claim", "generate_user_section",
                      "generate_operation_claim", "generate_operation_section"):
        assert forbidden not in task_names


# =====================================================================
# 28: ExecutedStep log correctness
# =====================================================================

def test_executed_step_log_correctness(store: ArtifactStore) -> None:
    llm = default_workflow_llm()
    _build_faulty_snapshot(store, "run_log", llm)
    detect_field_faults("run_log", store)
    g = ArtifactGraph.from_store(store, "run_log")
    result = propagate_contamination(g, store, "run_log")
    target = ArtifactRef(run_id="run_log", artifact_id="report_merged", version=2)
    plan = SelectiveRecoveryPlanner(g, store, "run_log", target, result)
    executor = RecoveryExecutor(store, g, llm)
    out = executor.execute(plan)

    # Each step in the plan should have a matching log
    for step in plan.steps:
        if step.action == RecoveryAction.REUSE:
            log = next(l for l in out.executed_steps
                       if l.replaced_ref == step.target_ref)
            assert log.produced_ref is None
            assert log.llm_calls == 0
            assert log.tokens == 0
        else:
            log = next(l for l in out.executed_steps
                       if l.replaced_ref == step.target_ref)
            assert log.produced_ref is not None
            assert log.produced_ref.version > step.target_ref.version


# =====================================================================
# 29: Graph reload + recovery consistent
# =====================================================================

def test_reload_recovery_consistent(tmp_path: Path) -> None:
    base = tmp_path / "artifacts"
    s1 = ArtifactStore(base_dir=str(base))
    llm = default_workflow_llm()
    _build_faulty_snapshot(s1, "run_reload", llm)
    detect_field_faults("run_reload", s1)
    g1 = ArtifactGraph.from_store(s1, "run_reload")
    res1 = propagate_contamination(g1, s1, "run_reload")
    target = ArtifactRef(run_id="run_reload", artifact_id="report_merged", version=2)
    plan = SelectiveRecoveryPlanner(g1, s1, "run_reload", target, res1)
    executor = RecoveryExecutor(s1, g1, llm)
    out = executor.execute(plan)
    assert out.success is True

    # fresh store, reload, verify
    s2 = ArtifactStore(base_dir=str(base))
    s2.reload()
    g2 = ArtifactGraph.from_store(s2, "run_reload")
    final = store_check_v3(s2)
    assert final is not None
    ok, _ = validate_target_clean_path(
        ArtifactRef(run_id="run_reload", artifact_id="report_merged", version=3),
        g2, s2,
    )
    assert ok is True


def store_check_v3(store: ArtifactStore) -> Artifact | None:
    return store.get_version("run_reload", "report_merged", 3)
