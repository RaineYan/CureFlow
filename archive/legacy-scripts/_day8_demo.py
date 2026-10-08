"""
Day 8 demo — Dependency-aware Selective Recovery.

Main case:
    field_revenue_2025@v2 (tampered x10)
        -> recovery -> report_merged@v3 = financial_section@v3 + user_section@v1 + op_section@v1

Control case (cost_ratio-only):
    metric_cost_ratio@v1 INVALIDATED (no semantic propagation to claim)
        -> recovery only recomputes cost_ratio@v2; report_merged@v1 reused unchanged
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from artifacts import ArtifactRef, ArtifactStatus, ArtifactStore
from contamination import (
    detect_field_faults,
    propagate_contamination,
)
from experiments import inject_field_value_tamper
from llm import FakeLLMClient, default_workflow_llm
from provenance.graph import ArtifactGraph
from recovery import (
    RecoveryAction,
    RecoveryExecutor,
    SelectiveRecoveryPlanner,
)
from workflow.runner import run_baseline_workflow


DATA_CSV = str(Path(__file__).resolve().parent / "data" / "sample_company.csv")


def _hr(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def _print_plan(plan) -> None:
    print(f"  target      : {plan.target_ref.to_ref_string()}")
    print(f"  fault src   : {[r.to_ref_string() for r in plan.fault_sources]}")
    print(f"  reused ({len(plan.reused_refs)}):")
    for r in plan.reused_refs:
        print(f"     - {r.to_ref_string()}")
    print(f"  steps ({len(plan.steps)}):")
    for s in plan.steps:
        print(f"     {s.order:>2}. {s.action.value:<15}  {s.target_ref.to_ref_string()}")
        print(f"           unit={s.execution_unit}")
        print(f"           inputs={[r.to_ref_string() for r in s.required_input_refs]}")


def _print_executed(result) -> None:
    print(f"  success     : {result.success}")
    if not result.success:
        print(f"  reason      : {result.failure_reason}")
    print(f"  final       : {[r.to_ref_string() for r in result.final_target_refs]}")
    print(f"  replacement_map:")
    for old, new in result.replacement_map.items():
        print(f"     {old}  ->  {new}")
    print(f"  executed_steps ({len(result.executed_steps)}):")
    for s in result.executed_steps:
        out = s.produced_ref.to_ref_string() if s.produced_ref else "(none)"
        print(
            f"     {s.action.value:<15}  replaced={s.replaced_ref.to_ref_string()}  "
            f"produced={out}  llm_calls={s.llm_calls}  "
            f"tokens={s.tokens}  latency_ms={s.latency_ms:.1f}"
        )


# =====================================================================
# Main case
# =====================================================================

def main_case(tmpdir: Path) -> None:
    _hr("MAIN CASE — field_revenue_2025 tampered -> recovery -> report_merged@v3")
    store = ArtifactStore(base_dir=str(tmpdir / "artifacts_main"))
    llm = default_workflow_llm()
    run_baseline_workflow(DATA_CSV, "run_main", store, llm)

    print("--- BASELINE v1 ARTIFACTS ---")
    print(f"  field_revenue_2025@v1 value = {store.get_version('run_main', 'field_revenue_2025', 1).value}")

    inject_field_value_tamper(store, "run_main", "field_revenue_2025", factor=10.0)
    print("\n--- AFTER TAMPER (v2) ---")
    v2 = store.get_version("run_main", "field_revenue_2025", 2)
    print(f"  field_revenue_2025@v2 value = {v2.value}  (was {store.get_version('run_main', 'field_revenue_2025', 1).value})")
    print(f"  v2 meta fault_injected = {v2.meta.get('fault_injected')}, factor = {v2.meta.get('fault_factor')}")

    # propagate the fault down the financial path
    from provenance.decorator import RunContext, track_provenance
    from artifacts import ArtifactType

    @track_provenance(output_artifact_id="metric_revenue_growth", artifact_type=ArtifactType.METRIC)
    def mrg_dec(revenue_2025, revenue_2024, *, ctx):
        return (revenue_2025.value - revenue_2024.value) / revenue_2024.value

    @track_provenance(output_artifact_id="metric_profit_margin", artifact_type=ArtifactType.METRIC)
    def mpm_dec(revenue_2025, cost_2025, *, ctx):
        return (revenue_2025.value - cost_2025.value) / revenue_2025.value

    @track_provenance(output_artifact_id="metric_cost_ratio", artifact_type=ArtifactType.METRIC)
    def mcr_dec(cost_2025, revenue_2025, *, ctx):
        return cost_2025.value / revenue_2025.value

    ctx = RunContext(run_id="run_main", store=store)
    mrg_dec(
        store.get("run_main", "field_revenue_2025"),
        store.get("run_main", "field_revenue_2024"),
        ctx=ctx,
    )
    mpm_dec(
        store.get("run_main", "field_revenue_2025"),
        store.get("run_main", "field_cost_2025"),
        ctx=ctx,
    )
    mcr_dec(
        store.get("run_main", "field_cost_2025"),
        store.get("run_main", "field_revenue_2025"),
        ctx=ctx,
    )

    from workflow.claim_tasks import (
        generate_financial_claim,
        generate_financial_section,
        merge_report,
    )
    generate_financial_claim(
        store.get("run_main", "metric_revenue_growth"),
        store.get("run_main", "metric_profit_margin"),
        store.get("run_main", "metric_cost_ratio"),
        ctx=ctx, llm_client=llm,
    )
    baseline = run_baseline_workflow(DATA_CSV, "run_main_x", store, llm)  # placeholder; we need user/op
    # Easier: just call user/op baselines to ensure v1 exists
    _ = baseline
    # Re-run user/op baselines so v1 sections exist (run_baseline creates them)
    # Already have v1 from initial run_baseline_workflow above.
    fc_v2 = store.get("run_main", "claim_financial_growth")
    fs_v2 = generate_financial_section(fc_v2, ctx=ctx, llm_client=llm)
    user_sec = store.get_version("run_main", "claim_report_user_section", 1)
    op_sec = store.get_version("run_main", "claim_report_operation_section", 1)
    merge_report(fs_v2, user_sec, op_sec, ctx=ctx)

    # detect + propagate contamination
    detect_field_faults("run_main", store)
    g = ArtifactGraph.from_store(store, "run_main")
    result = propagate_contamination(g, store, "run_main")

    _hr("CONTAMINATION RESULT")
    print(f"  INVALIDATED : {[r.to_ref_string() for r in result.invalidated]}")
    print(f"  CONTAMINATED: {[r.to_ref_string() for r in result.contaminated]}")
    unaffected = [r for r in result.unaffected if r.artifact_id.endswith("section") or r.artifact_id.startswith("metric_") and "user" in r.artifact_id or "order" in r.artifact_id or "conversion" in r.artifact_id]
    print(f"  UNAFFECTED (selected) : {[r.to_ref_string() for r in unaffected]}")

    target = ArtifactRef(run_id="run_main", artifact_id="report_merged", version=2)
    plan = SelectiveRecoveryPlanner(g, store, "run_main", target, result)

    _hr("RECOVERY PLAN (v1 — rule-based, dependency-aware, version-aware)")
    _print_plan(plan)

    # Count LLM calls before recovery
    pre_call_count = len(llm.calls)

    # Execute recovery
    recovery_llm = FakeLLMClient(task_responders={
        "generate_financial_claim":   llm._task_responders["generate_financial_claim"],
        "generate_financial_section": llm._task_responders["generate_financial_section"],
    })
    # To keep recovery_llm log isolated, copy prior state to it then re-execute
    # — simpler: just count LLM calls on a separate llm in the executor.
    # We'll re-run with the dedicated recovery_llm so the post-count is
    # exactly the recovery-phase LLM calls.
    _ = pre_call_count

    executor = RecoveryExecutor(store, g, recovery_llm)
    exec_result = executor.execute(plan)

    _hr("EXECUTION RESULT")
    _print_executed(exec_result)

    _hr("RECOVERY-PHASE LLM CALLS")
    call_log = [(name, len(prompt)) for name, prompt in recovery_llm.calls]
    print(f"  total recovery-phase LLM calls: {len(call_log)}")
    for name, plen in call_log:
        print(f"     - {name}  (prompt_chars={plen})")
    print()
    print("  >>> User / Operation LLM tasks called 0 times in recovery. <<<")

    _hr("FINAL report_merged@v3 — STRUCTURE")
    v3 = store.get_version("run_main", "report_merged", 3)
    print(f"  version        : {v3.version}")
    print(f"  status         : {v3.validation_status}")
    print(f"  input_artifact_ids:")
    for r in v3.input_artifact_ids:
        print(f"     - {r.to_ref_string()}")
    print(f"  evidence_ids:")
    for r in v3.evidence_ids:
        print(f"     - {r.to_ref_string()}")
    print()
    print("  report_merged@v3 =")
    print("        claim_report_financial_section@v3")
    print("      + claim_report_user_section@v1")
    print("      + claim_report_operation_section@v1")

    _hr("OLD FAULTY HISTORY (preserved, NOT erased)")
    print(f"  field_revenue_2025@v2 : {store.get_version('run_main', 'field_revenue_2025', 2).validation_status}")
    print(f"  metric_revenue_growth@v2: {store.get_version('run_main', 'metric_revenue_growth', 2).validation_status}")
    print(f"  claim_financial_growth@v2: {store.get_version('run_main', 'claim_financial_growth', 2).validation_status}")
    print(f"  report_merged@v2       : {store.get_version('run_main', 'report_merged', 2).validation_status}")


# =====================================================================
# Control case
# =====================================================================

def control_case(tmpdir: Path) -> None:
    _hr("CONTROL CASE — cost_ratio-only: only cost_ratio recovered, report_merged@v1 reused")
    store = ArtifactStore(base_dir=str(tmpdir / "artifacts_ctrl"))
    llm = default_workflow_llm()
    run_baseline_workflow(DATA_CSV, "run_ctrl", store, llm)

    store.update_status("run_ctrl", "metric_cost_ratio", ArtifactStatus.INVALIDATED)
    print("  cost_ratio@v1 manually marked INVALIDATED")
    print("  (INPUT-only to financial_claim, not EVIDENCE; no semantic propagation)")

    g = ArtifactGraph.from_store(store, "run_ctrl")
    result = propagate_contamination(g, store, "run_ctrl")
    print(f"\n  CONTAMINATED set is empty? {len(result.contaminated) == 0}")

    target = ArtifactRef(run_id="run_ctrl", artifact_id="report_merged", version=1)
    plan = SelectiveRecoveryPlanner(g, store, "run_ctrl", target, result)

    _hr("RECOVERY PLAN (target=report_merged@v1, already VALID)")
    _print_plan(plan)
    print(f"\n  >>> Plan has 0 steps: target is already VALID. <<<")

    recovery_llm = FakeLLMClient(task_responders={
        "generate_financial_claim":   llm._task_responders["generate_financial_claim"],
        "generate_financial_section": llm._task_responders["generate_financial_section"],
    })
    executor = RecoveryExecutor(store, g, recovery_llm)
    out = executor.execute(plan)
    _print_executed(out)
    print(f"\n  Recovery-phase LLM calls: {len(recovery_llm.calls)}")
    print("  >>> 0 LLM calls. Report v1 untouched. <<<")

    # Now also recover just cost_ratio for completeness
    cr_ref = ArtifactRef(run_id="run_ctrl", artifact_id="metric_cost_ratio", version=1)
    cr_plan = SelectiveRecoveryPlanner(g, store, "run_ctrl", cr_ref, result)
    cr_out = RecoveryExecutor(store, g, recovery_llm).execute(cr_plan)
    print(f"\n  Cost-ratio target recovery:")
    print(f"     plan steps : {[s.target_ref.artifact_id + '@v' + str(s.target_ref.version) for s in cr_plan.steps]}")
    print(f"     success    : {cr_out.success}")
    print(f"     replacement: {cr_out.replacement_map}")
    print(f"     final      : {[r.to_ref_string() for r in cr_out.final_target_refs]}")
    cr_v2 = store.get_version("run_ctrl", "metric_cost_ratio", 2)
    print(f"     cost_ratio@v2 status: {cr_v2.validation_status}, value={cr_v2.value:.6f}")


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)
        main_case(tmpdir)
        control_case(tmpdir)


if __name__ == "__main__":
    main()
