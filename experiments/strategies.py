"""
Three recovery strategies — Day 10.

Each strategy now emits an `ExecutionEvent` stream.  All counters
(artifact_materializations, task_calls, llm_calls, etc.) are
*aggregated* from the event stream by `aggregate_events` in models.py.
Strategies never hand-increment counters.

The three strategies remain FULL_RERUN / COARSE_CHECKPOINT /
SELECTIVE_RECOVERY, with the same fairness invariants as Day 9.
"""
from __future__ import annotations

import time
from typing import List, Optional

from artifacts import (
    Artifact,
    ArtifactRef,
    ArtifactStore,
)
from llm import LLMClient, default_workflow_llm
from provenance.decorator import RunContext
from provenance.graph import ArtifactGraph

from recovery import (
    RecoveryExecutor,
    SelectiveRecoveryPlanner,
    validate_recovery_execution_integrity,
    validate_target_clean_path,
)
from workflow.claim_tasks import (
    generate_financial_claim,
    generate_financial_section,
    generate_operation_claim,
    generate_operation_section,
    generate_user_claim,
    generate_user_section,
    merge_report,
)

from .models import (
    ExecutionEvent,
    ExperimentResult,
    ExperimentStrategy,
    aggregate_events,
    compute_arp,
    compute_artifact_reuse_rate,
    compute_recompute_amplification,
    compute_uarr,
    compute_uarr_v2,
    hash_content,
)
from .scenario import (
    GoldenClean,
    MainFaultScenario,
    rerun_all_metrics,
    rerun_field_extraction,
)
from .classification import (
    SOURCE_OF_TRUTH_ARTIFACT,
    build_prerequisite_audit,
    classify_materializations,
    count_required,
    count_unnecessary,
    compute_rmp,
    compute_taf,
    compute_raf as compute_raf_metric,
    compute_upr,
    compute_arr_v2,
    derive_active_reuse_and_preserved,
    derive_actively_reused_clean_prereq_identities,
    derive_actively_reused_clean_prereq_refs,
    derive_clean_prerequisite_identities,
    derive_clean_prerequisite_refs,
    derive_missing_clean_prerequisite_refs,
    derive_required_recovery_identities,
    derive_target_recovery_closure,
)
from recovery import SelectiveRecoveryPlanner as _Planner  # for plan step lookup


# =====================================================================
# Internal LLM wrapper: clears the FakeLLM call log on construction.
# =====================================================================

class _CountingLLM:
    def __init__(self, inner: LLMClient):
        self._inner = inner
        if hasattr(inner, "calls"):
            inner.calls = []  # type: ignore[attr-defined]

    def __getattr__(self, item):
        return getattr(self._inner, item)

    def generate(self, prompt: str, *, task_name: str = ""):
        return self._inner.generate(prompt, task_name=task_name)


# =====================================================================
# Emit an ExecutionEvent
# =====================================================================

def _event(
    strategy: ExperimentStrategy,
    case_id: str,
    unit: str,
    task_kind: str,
    action: str,
    *,
    input_refs: List[ArtifactRef],
    produced_refs: List[ArtifactRef],
    reused_refs: List[ArtifactRef],
    llm_calls: int = 0,
    in_tok: int = 0,
    out_tok: int = 0,
    total_tok: int = 0,
    elapsed_ms: float = 0.0,
) -> ExecutionEvent:
    return ExecutionEvent(
        strategy=strategy,
        case_id=case_id,
        execution_unit=unit,
        task_kind=task_kind,
        action=action,
        input_refs=input_refs,
        produced_refs=produced_refs,
        reused_refs=reused_refs,
        llm_calls=llm_calls,
        synthetic_input_tokens=in_tok,
        synthetic_output_tokens=out_tok,
        synthetic_total_tokens=total_tok,
        local_elapsed_ms=elapsed_ms,
    )


# =====================================================================
# Common finalize
# =====================================================================

def _finalize(
    result: ExperimentResult,
    *,
    events: List[ExecutionEvent],
    final_artifact: Artifact,
    golden: GoldenClean,
    graph: ArtifactGraph,
    store: ArtifactStore,
    scenario: MainFaultScenario,
    replacement_map: Optional[Dict[str, str]] = None,
) -> ExperimentResult:
    replacement_map = replacement_map or {}
    # 1) aggregate counters from events
    agg = aggregate_events(events, case_id=result.case_id, strategy=result.strategy)

    # 2) final target ref + golden match
    final_ref = ArtifactRef(
        run_id=final_artifact.run_id,
        artifact_id=final_artifact.artifact_id,
        version=final_artifact.version,
    )
    final_hash = hash_content(final_artifact.content)
    matches_golden = final_hash == golden.final_report_hash

    # 3) clean-path + integrity
    sem_ok, _ = validate_target_clean_path(final_ref, graph, store)
    int_ok, _ = validate_recovery_execution_integrity(final_ref, graph, store)
    success = sem_ok and int_ok and matches_golden
    failure = "" if success else f"sem={sem_ok} int={int_ok} golden={matches_golden}"

    # 4) STRATEGY-INDEPENDENT ground truth: required_recovery_identities
    #    derived from contamination + target recovery closure.
    target_closure = derive_target_recovery_closure(graph, scenario.target_ref)
    required_set = derive_required_recovery_identities(
        contamination_invalidated=list(scenario.contamination.invalidated),
        contamination_contaminated=list(scenario.contamination.contaminated),
        target_ref=scenario.target_ref,
        target_recovery_closure=target_closure,
    )

    # 5) Classify every produced artifact.
    produced_refs: List[ArtifactRef] = []
    for e in events:
        for r in e.produced_refs:
            produced_refs.append(r)
    classifications = classify_materializations(produced_refs, required_set)
    required_n = count_required(classifications)
    unnecessary_n = count_unnecessary(classifications)
    total_mat = agg["artifact_materializations"]
    assert required_n + unnecessary_n == total_mat, (
        f"partition invariant broken: required {required_n} + "
        f"unnecessary {unnecessary_n} != total {total_mat}"
    )

    rmp = compute_rmp(required_n, total_mat)
    uarr_v2 = compute_uarr_v2(unnecessary_n, total_mat)
    # back-compat aliases
    uarr_day10 = uarr_v2
    arp_legacy = rmp

    # 6) Reuse / Preserved / Skipped — from the event stream.
    affected_set = set(scenario.affected_artifact_identities)
    available_set = set(scenario.available_unaffected_artifact_identities)
    reuse_cls = derive_active_reuse_and_preserved(
        events,
        required_identities=required_set,
        available_unaffected=available_set,
    )
    actively_reused_ids = reuse_cls["actively_reused"]
    preserved_ids = reuse_cls["preserved_unaffected"]
    skipped_ids = reuse_cls["skipped_irrelevant"]

    # 7) Prerequisite audit (Day 12 rebuild).  Uses the SELECTIVE
    #    plan + the CANONICAL replacement map (derived from the
    #    plan structure, not the strategy's actual execution).  Each
    #    (step, input) row is classified into SOURCE / CLEAN /
    #    RECOVERED.  actively_reused is set iff the resolved ref
    #    appears in the strategy's actual event stream AND the
    #    strategy did NOT re-materialize that same identity.
    planner = _Planner(
        graph, store, scenario.run_id, scenario.target_ref, scenario.contamination,
    )
    from .classification import derive_canonical_replacement_map
    canonical_replacement_map = derive_canonical_replacement_map(
        planner.steps, required_set,
    )
    # Recomputed identities in this run
    recomputed_ids = {r.artifact_id for e in events for r in e.produced_refs}
    audit = build_prerequisite_audit(
        plan_steps=planner.steps,
        replacement_map=canonical_replacement_map,
        events=events,
        recomputed_identities=recomputed_ids,
    )
    clean_prereq_refs = derive_clean_prerequisite_refs(audit)
    clean_prereq_ids = derive_clean_prerequisite_identities(audit)
    actively_reused_prereq_refs = derive_actively_reused_clean_prereq_refs(audit)
    actively_reused_prereq_ids = derive_actively_reused_clean_prereq_identities(audit)
    missing_prereq_refs = derive_missing_clean_prerequisite_refs(audit)
    arr = compute_arr_v2(
        actively_reused_identities=actively_reused_prereq_ids,
        required_clean_identities=clean_prereq_ids,
    )
    upr = compute_upr(
        preserved_count=len(preserved_ids),
        total_available=len(available_set),
    )

    # 8) Amplification factors
    raf = compute_raf_metric(
        materializations=total_mat,
        required_identities_count=len(required_set),
    )
    reference_recovery_task_count = len(planner.steps)
    taf = compute_taf(
        task_calls=agg["task_calls"],
        minimum_required_units=reference_recovery_task_count,
    )

    # 9) Back-compat fields
    recomputed_ids_set = {r.artifact_id for r in produced_refs}
    recomputed_unaffected = sorted(available_set & recomputed_ids_set)
    reused_unaffected = sorted(available_set - recomputed_ids_set)

    return ExperimentResult(
        case_id=result.case_id,
        strategy=result.strategy,
        success=success,
        final_target_ref=final_ref,
        final_content_hash=final_hash,
        matches_golden_output=matches_golden,
        failure_reason=failure,
        events=events,

        # Day 10 raw counters
        tool_task_calls=agg["tool_task_calls"],
        llm_task_calls=agg["llm_task_calls"],
        task_calls=agg["task_calls"],
        tool_artifact_materializations=agg["tool_artifact_materializations"],
        llm_artifact_materializations=agg["llm_artifact_materializations"],
        artifact_materializations=total_mat,
        llm_calls=agg["llm_calls"],
        executed_units=agg["executed_units"],

        recomputed_artifact_ids=agg["recomputed_artifact_ids"],

        # Day 10 identity sets (kept for back-compat)
        affected_artifact_identities=sorted(affected_set),
        available_unaffected_artifact_identities=sorted(available_set),
        unaffected_artifacts_recomputed=recomputed_unaffected,
        reused_artifact_ids=reused_unaffected,
        reused_artifact_count=len(reused_unaffected),
        reused_unaffected_artifact_identities=reused_unaffected,
        affected_artifact_identity_count=len(affected_set),
        available_unaffected_count=len(available_set),
        unaffected_recomputed_count=len(recomputed_unaffected),

        # Day 10 back-compat metrics
        artifact_reuse_rate=compute_artifact_reuse_rate(
            reused=len(reused_unaffected), available=len(available_set),
        ),
        uarr=uarr_day10,
        affected_recovery_precision_arp=arp_legacy,
        recompute_amplification=raf,

        # Day 11 classification
        required_recovery_artifact_identities=sorted(required_set),
        required_recovery_identity_count=len(required_set),
        required_clean_prerequisite_identities=clean_prereq_ids,
        required_clean_prerequisite_count=len(clean_prereq_ids),
        required_clean_prerequisite_refs=clean_prereq_refs,
        actively_reused_clean_prerequisite_refs=actively_reused_prereq_refs,
        missing_clean_prerequisite_refs=missing_prereq_refs,
        required_materializations=required_n,
        unnecessary_materializations=unnecessary_n,
        recovery_materialization_precision=rmp,
        # affected_recovery_precision_arp is already set above as a
        # back-compat alias for rmp.

        # Day 11 reuse / preserved / skipped
        actively_reused_artifact_identities=actively_reused_ids,
        actively_reused_count=len(actively_reused_ids),
        actively_reused_clean_prerequisite_count=len(actively_reused_prereq_refs),
        preserved_unaffected_artifact_identities=preserved_ids,
        preserved_unaffected_count=len(preserved_ids),
        skipped_irrelevant_artifact_identities=skipped_ids,
        skipped_irrelevant_count=len(skipped_ids),
        unaffected_preservation_rate=upr,
        active_reuse_rate=arr,

        # Day 11/12 amplification
        recompute_amplification_factor=raf,
        task_amplification_factor=taf,
        reference_recovery_task_count=reference_recovery_task_count,
        minimum_required_execution_unit_count=reference_recovery_task_count,  # alias

        # audit trail
        classifications=[c.model_dump(mode="json") for c in classifications],
        prerequisite_audit=[r.model_dump(mode="json") for r in audit],

        # synthetic usage
        synthetic_input_tokens=agg["synthetic_input_tokens"],
        synthetic_output_tokens=agg["synthetic_output_tokens"],
        synthetic_total_tokens=agg["synthetic_total_tokens"],
        local_wall_clock_ms=agg["local_wall_clock_ms"],

        semantic_clean_path_pass=sem_ok,
        execution_integrity_pass=int_ok,
    )


# =====================================================================
# Reusable: re-run the whole 3-branch pipeline.  Used by FULL_RERUN and
# COARSE_CHECKPOINT (which for the Main Case is structurally identical).
# Each step emits an ExecutionEvent.
# =====================================================================

def _rerun_3branch_pipeline(
    result: ExperimentResult,
    csv_path: str,
    run_id: str,
    store: ArtifactStore,
    llm: LLMClient,
) -> tuple[Artifact, List[ExecutionEvent]]:
    events: List[ExecutionEvent] = []

    # ---- 1) rerun_field_extraction (1 tool task call -> many fields) ----
    t0 = time.perf_counter()
    fields = rerun_field_extraction(csv_path, run_id, store)
    elapsed = (time.perf_counter() - t0) * 1000.0
    produced = [
        ArtifactRef(run_id=run_id, artifact_id=a.artifact_id, version=a.version)
        for a in fields.values()
    ]
    input_refs = [
        ArtifactRef(run_id=run_id, artifact_id="raw_data", version=1),
    ]
    # synthetic token usage is 0 for a tool step
    total_tokens = sum(a.execution_cost.tokens for a in fields.values())
    in_tok = sum(a.execution_cost.input_tokens for a in fields.values())
    out_tok = sum(a.execution_cost.output_tokens for a in fields.values())
    events.append(_event(
        result.strategy, run_id, "rerun_field_extraction", "tool", "TOOL_RECOMPUTE",
        input_refs=input_refs, produced_refs=produced, reused_refs=[],
        llm_calls=0, in_tok=in_tok, out_tok=out_tok, total_tok=total_tokens,
        elapsed_ms=elapsed,
    ))

    # ---- 2) 6 metrics ----
    metric_specs = [
        ("compute_revenue_growth",  "metric_revenue_growth"),
        ("compute_profit_margin",   "metric_profit_margin"),
        ("compute_cost_ratio",      "metric_cost_ratio"),
        ("compute_user_growth",     "metric_user_growth"),
        ("compute_conversion_rate", "metric_conversion_rate"),
        ("compute_order_growth",    "metric_order_growth"),
    ]
    metrics = rerun_all_metrics(run_id, store)
    for unit, mid in metric_specs:
        a = metrics[mid]
        produced = [ArtifactRef(run_id=run_id, artifact_id=mid, version=a.version)]
        ec = a.execution_cost
        events.append(_event(
            result.strategy, run_id, unit, "tool", "TOOL_RECOMPUTE",
            input_refs=list(a.input_artifact_ids),
            produced_refs=produced, reused_refs=[],
            llm_calls=0,
            in_tok=ec.input_tokens, out_tok=ec.output_tokens, total_tok=ec.tokens,
            elapsed_ms=ec.latency_ms,
        ))

    # ---- 3) 3 claims + 3 sections + 1 merge ----
    ctx = RunContext(run_id=run_id, store=store)
    claim_specs = [
        ("generate_financial_claim",
         ("metric_revenue_growth", "metric_profit_margin", "metric_cost_ratio"),
         "claim_financial_growth"),
        ("generate_user_claim",
         ("metric_user_growth",),
         "claim_user_growth"),
        ("generate_operation_claim",
         ("metric_conversion_rate", "metric_order_growth"),
         "claim_operation"),
    ]
    claims: dict[str, Artifact] = {}
    for unit, mid_keys, cid in claim_specs:
        kwargs = {k.split("_", 1)[1]: store.get(run_id, k) for k in mid_keys}
        # first arg is the metric, second is the metric, etc.  The
        # claim task signatures vary, so we look up the artifact by id.
        # Pass the metrics in the order the function expects.
        if unit == "generate_financial_claim":
            claim_art = generate_financial_claim(
                kwargs["revenue_growth"],
                kwargs["profit_margin"],
                kwargs["cost_ratio"],
                ctx=ctx, llm_client=llm,
            )
        elif unit == "generate_user_claim":
            claim_art = generate_user_claim(
                kwargs["user_growth"],
                ctx=ctx, llm_client=llm,
            )
        else:
            claim_art = generate_operation_claim(
                kwargs["conversion_rate"],
                kwargs["order_growth"],
                ctx=ctx, llm_client=llm,
            )
        claims[cid] = claim_art
        ec = claim_art.execution_cost
        events.append(_event(
            result.strategy, run_id, unit, "llm", "REPROMPT",
            input_refs=list(claim_art.input_artifact_ids),
            produced_refs=[ArtifactRef(
                run_id=run_id, artifact_id=cid, version=claim_art.version)],
            reused_refs=[],
            llm_calls=ec.llm_calls,
            in_tok=ec.input_tokens, out_tok=ec.output_tokens, total_tok=ec.tokens,
            elapsed_ms=ec.latency_ms,
        ))

    section_specs = [
        ("generate_financial_section", "claim_financial_growth",
         "claim_report_financial_section"),
        ("generate_user_section",      "claim_user_growth",
         "claim_report_user_section"),
        ("generate_operation_section", "claim_operation",
         "claim_report_operation_section"),
    ]
    sections: dict[str, Artifact] = {}
    for unit, claim_id, section_id in section_specs:
        if unit == "generate_financial_section":
            s = generate_financial_section(claims[claim_id], ctx=ctx, llm_client=llm)
        elif unit == "generate_user_section":
            s = generate_user_section(claims[claim_id], ctx=ctx, llm_client=llm)
        else:
            s = generate_operation_section(claims[claim_id], ctx=ctx, llm_client=llm)
        sections[section_id] = s
        ec = s.execution_cost
        events.append(_event(
            result.strategy, run_id, unit, "llm", "REPROMPT",
            input_refs=list(s.input_artifact_ids),
            produced_refs=[ArtifactRef(
                run_id=run_id, artifact_id=section_id, version=s.version)],
            reused_refs=[],
            llm_calls=ec.llm_calls,
            in_tok=ec.input_tokens, out_tok=ec.output_tokens, total_tok=ec.tokens,
            elapsed_ms=ec.latency_ms,
        ))

    # ---- 4) merge_report ----
    merged = merge_report(
        sections["claim_report_financial_section"],
        sections["claim_report_user_section"],
        sections["claim_report_operation_section"],
        ctx=ctx,
    )
    ec = merged.execution_cost
    events.append(_event(
        result.strategy, run_id, "merge_report", "tool", "TOOL_RECOMPUTE",
        input_refs=list(merged.input_artifact_ids),
        produced_refs=[ArtifactRef(
            run_id=run_id, artifact_id="report_merged", version=merged.version)],
        reused_refs=[],
        llm_calls=0,
        in_tok=ec.input_tokens, out_tok=ec.output_tokens, total_tok=ec.tokens,
        elapsed_ms=ec.latency_ms,
    ))

    return merged, events


# =====================================================================
# FULL_RERUN
# =====================================================================

def run_full_rerun_recovery(
    scenario: MainFaultScenario,
    golden: GoldenClean,
    csv_path: str,
    *,
    llm_client: Optional[LLMClient] = None,
) -> ExperimentResult:
    """Run FULL_RERUN recovery.  If `llm_client` is provided
    (e.g. a RealLLMClient wrapper for the Day 14 pilot),
    use it instead of `default_workflow_llm()`.  The
    injection is mandatory for real Qwen trials — the
    Day 14 pilot refuses to mark a trial PASS if 0 real
    provider records were captured."""
    result = ExperimentResult(case_id=scenario.run_id, strategy=ExperimentStrategy.FULL_RERUN)
    base_llm = llm_client if llm_client is not None else default_workflow_llm()
    llm = _CountingLLM(base_llm)
    start = time.perf_counter()
    merged, events = _rerun_3branch_pipeline(
        result, csv_path, scenario.run_id, scenario.store, llm,
    )
    wall = (time.perf_counter() - start) * 1000.0
    # adjust wall to include any python overhead (we keep the events'
    # own elapsed_ms; this is a separate debug-only signal)
    g = ArtifactGraph.from_store(scenario.store, scenario.run_id)
    finalized = _finalize(
        result, events=events, final_artifact=merged, golden=golden,
        graph=g, store=scenario.store, scenario=scenario,
    )
    finalized.local_wall_clock_ms = wall
    return finalized


# =====================================================================
# COARSE_CHECKPOINT
# =====================================================================

def run_coarse_checkpoint_recovery(
    scenario: MainFaultScenario,
    golden: GoldenClean,
    csv_path: str,
    *,
    llm_client: Optional[LLMClient] = None,
) -> ExperimentResult:
    """Same injection contract as `run_full_rerun_recovery`."""
    result = ExperimentResult(
        case_id=scenario.run_id, strategy=ExperimentStrategy.COARSE_CHECKPOINT,
    )
    base_llm = llm_client if llm_client is not None else default_workflow_llm()
    llm = _CountingLLM(base_llm)
    start = time.perf_counter()
    merged, events = _rerun_3branch_pipeline(
        result, csv_path, scenario.run_id, scenario.store, llm,
    )
    wall = (time.perf_counter() - start) * 1000.0
    g = ArtifactGraph.from_store(scenario.store, scenario.run_id)
    finalized = _finalize(
        result, events=events, final_artifact=merged, golden=golden,
        graph=g, store=scenario.store, scenario=scenario,
    )
    finalized.local_wall_clock_ms = wall
    return finalized


# =====================================================================
# SELECTIVE_RECOVERY
# =====================================================================

def run_selective_recovery(
    scenario: MainFaultScenario,
    golden: GoldenClean,
    *,
    llm_client: Optional[LLMClient] = None,
) -> ExperimentResult:
    """Same injection contract as the other two.  The
    `llm_client` flows through the RecoveryExecutor."""
    result = ExperimentResult(
        case_id=scenario.run_id, strategy=ExperimentStrategy.SELECTIVE_RECOVERY,
    )
    store = scenario.store
    run_id = scenario.run_id
    base_llm = llm_client if llm_client is not None else default_workflow_llm()
    llm = _CountingLLM(base_llm)

    g = ArtifactGraph.from_store(store, run_id)
    plan = SelectiveRecoveryPlanner(
        g, store, run_id, scenario.target_ref, scenario.contamination,
    )
    start = time.perf_counter()
    executor = RecoveryExecutor(store, g, llm)
    out = executor.execute(plan)
    wall = (time.perf_counter() - start) * 1000.0

    # Convert executor's ExecutedRecoveryStep stream into ExecutionEvent
    # stream.  Each step is one event.  For REUSE steps, the produced
    # list is empty and reused_refs contains the target_ref.
    # For TOOL_RECOMPUTE / REPROMPT, produced_refs contains the new ref
    # and input_refs are the (remapped) inputs.
    events: List[ExecutionEvent] = []
    for step in out.executed_steps:
        if step.action.value == "REUSE":
            events.append(_event(
                result.strategy, run_id, step.execution_unit,
                "tool" if step.llm_calls == 0 else "llm",
                "REUSE",
                input_refs=[step.replaced_ref],
                produced_refs=[],
                reused_refs=[step.replaced_ref],
                llm_calls=0, in_tok=0, out_tok=0, total_tok=0, elapsed_ms=0.0,
            ))
        else:
            task_kind = "llm" if step.llm_calls > 0 else "tool"
            action = "REPROMPT" if task_kind == "llm" else "TOOL_RECOMPUTE"
            produced = [step.produced_ref] if step.produced_ref else []
            events.append(_event(
                result.strategy, run_id, step.execution_unit,
                task_kind, action,
                input_refs=list(step.input_refs),
                produced_refs=produced,
                reused_refs=[],
                llm_calls=step.llm_calls,
                in_tok=step.input_tokens,
                out_tok=step.output_tokens,
                total_tok=step.tokens,
                elapsed_ms=step.latency_ms,
            ))

    if not out.final_target_refs:
        result.success = False
        result.failure_reason = "no final target"
        result.events = events
        return result
    final_ref = out.final_target_refs[0]
    final_art = store.get_version(
        final_ref.run_id, final_ref.artifact_id, final_ref.version,
    )
    assert final_art is not None

    g2 = ArtifactGraph.from_store(store, run_id)
    finalized = _finalize(
        result, events=events, final_artifact=final_art, golden=golden,
        graph=g2, store=store, scenario=scenario,
        replacement_map=dict(out.replacement_map),
    )
    finalized.local_wall_clock_ms = wall
    return finalized
