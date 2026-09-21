"""
Experiment models — Day 12 (final metric audit).

Day 12 changes vs Day 11:
  - ARR denominator rebuilt from PrerequisiteAuditRecord, NOT from
    "plan.required_input_refs unique identities".  Each (step,
    input) is classified into SOURCE / CLEAN / RECOVERED via
    replacement_map, and only CLEAN refs enter the denominator.
  - New fields: required_clean_prerequisite_refs (exact ArtifactRef
    list, not just identities), actively_reused_clean_prerequisite_refs,
    missing_clean_prerequisite_refs, prerequisite_audit (list of
    PrerequisiteAuditRecord dicts).
  - TAF denominator renamed: `reference_recovery_task_count` (the
    actual SELECTIVE plan's step count for the scenario).  The old
    `minimum_required_execution_unit_count` is kept as a deprecated
    alias.
  - DEFINITIONS["Day_11_ARR_bug_root_cause"] explains why the
    previous 3/3 result was wrong.
"""
from __future__ import annotations

import hashlib
from enum import Enum
from typing import Dict, List, Optional, Set

from pydantic import BaseModel, Field

from artifacts import ArtifactRef


# =====================================================================
# Strategy enum
# =====================================================================

class ExperimentStrategy(str, Enum):
    FULL_RERUN = "FULL_RERUN"
    COARSE_CHECKPOINT = "COARSE_CHECKPOINT"
    SELECTIVE_RECOVERY = "SELECTIVE_RECOVERY"


# =====================================================================
# ExecutionEvent — every observable step in the recovery phase
# =====================================================================

class ExecutionEvent(BaseModel):
    """A single observable step inside a strategy's recovery phase.

    `task_kind` is "tool" or "llm" — derived from the execution unit's
    intrinsic nature (not the recovery action).

    `action` is one of:
        - "REUSE"             : a step that referenced an existing
                                artifact version without re-executing
                                the producer.  llm_calls=0.
        - "TOOL_RECOMPUTE"    : deterministic tool step.
        - "REPROMPT"          : LLM step.

    `produced_refs` lists every new artifact version produced (often
    exactly 1, but `rerun_field_extraction` produces 10 fields).
    `reused_refs` lists existing artifact versions directly consumed
    (not re-materialized).

    All ExperimentResult counters are derived from the event list —
    no hand-increment anywhere else.
    """
    strategy: ExperimentStrategy
    case_id: str
    execution_unit: str
    task_kind: str                       # "tool" | "llm"
    action: str                          # "REUSE" | "TOOL_RECOMPUTE" | "REPROMPT"
    input_refs: List[ArtifactRef] = Field(default_factory=list)
    produced_refs: List[ArtifactRef] = Field(default_factory=list)
    reused_refs: List[ArtifactRef] = Field(default_factory=list)
    llm_calls: int = 0
    synthetic_input_tokens: int = 0
    synthetic_output_tokens: int = 0
    synthetic_total_tokens: int = 0
    local_elapsed_ms: float = 0.0


# =====================================================================
# ExperimentResult — per-strategy result (aggregated from events)
# =====================================================================

class ExperimentResult(BaseModel):
    case_id: str
    strategy: ExperimentStrategy

    # ---- outcome ----
    success: bool = False
    final_target_ref: Optional[ArtifactRef] = None
    final_content_hash: Optional[str] = None
    matches_golden_output: bool = False
    failure_reason: str = ""

    # ---- raw event stream (for audit) ----
    events: List[ExecutionEvent] = Field(default_factory=list)

    # ---- execution counts (all derived from events) ----
    tool_task_calls: int = 0
    llm_task_calls: int = 0
    task_calls: int = 0           # = tool_task_calls + llm_task_calls
    tool_artifact_materializations: int = 0
    llm_artifact_materializations: int = 0
    artifact_materializations: int = 0   # = tool + llm
    llm_calls: int = 0            # = sum of events' llm_calls (kept for back-compat)
    executed_units: List[str] = Field(default_factory=list)

    # ---- reuse / recompute classification ----
    recomputed_artifact_ids: List[str] = Field(default_factory=list)
    reused_artifact_ids: List[str] = Field(default_factory=list)
    reused_artifact_count: int = 0
    affected_artifact_identities: List[str] = Field(default_factory=list)
    available_unaffected_artifact_identities: List[str] = Field(default_factory=list)
    unaffected_artifacts_recomputed: List[str] = Field(default_factory=list)
    reused_unaffected_artifact_identities: List[str] = Field(default_factory=list)

    # ---- structural metrics ----
    affected_artifact_identity_count: int = 0
    available_unaffected_count: int = 0
    unaffected_recomputed_count: int = 0
    artifact_reuse_rate: float = 0.0          # = reused_unaffected / available_unaffected  (Day-10 def, kept for back-compat)
    uarr: float = 0.0                         # Day-10 def, kept for back-compat

    # ---- Day 11: classification-based structural metrics ----
    required_recovery_artifact_identities: List[str] = Field(default_factory=list)
    required_recovery_identity_count: int = 0
    required_clean_prerequisite_identities: List[str] = Field(default_factory=list)
    required_clean_prerequisite_count: int = 0
    required_clean_prerequisite_refs: List[ArtifactRef] = Field(default_factory=list)
    actively_reused_clean_prerequisite_refs: List[ArtifactRef] = Field(default_factory=list)
    missing_clean_prerequisite_refs: List[ArtifactRef] = Field(default_factory=list)
    required_materializations: int = 0
    unnecessary_materializations: int = 0
    recovery_materialization_precision: float = 0.0   # = required / materializations
    affected_recovery_precision_arp: float = 0.0      # DEPRECATED alias for RMP

    # ---- Day 11: three reuse / preservation sets ----
    actively_reused_artifact_identities: List[str] = Field(default_factory=list)
    actively_reused_count: int = 0
    actively_reused_clean_prerequisite_count: int = 0
    preserved_unaffected_artifact_identities: List[str] = Field(default_factory=list)
    preserved_unaffected_count: int = 0
    skipped_irrelevant_artifact_identities: List[str] = Field(default_factory=list)
    skipped_irrelevant_count: int = 0
    unaffected_preservation_rate: float = 0.0
    active_reuse_rate: float = 0.0

    # ---- Day 11: amplification factors ----
    recompute_amplification_factor: float = 0.0
    recompute_amplification: float = 0.0             # DEPRECATED alias for RAF
    task_amplification_factor: float = 0.0
    reference_recovery_task_count: int = 0          # Day 12 renaming
    minimum_required_execution_unit_count: int = 0  # DEPRECATED alias

    # ---- audit trail ----
    classifications: List[dict] = Field(default_factory=list)
    prerequisite_audit: List[dict] = Field(default_factory=list)

    # ---- synthetic usage ----
    synthetic_input_tokens: int = 0
    synthetic_output_tokens: int = 0
    synthetic_total_tokens: int = 0

    # ---- timing (NOT real model latency) ----
    local_wall_clock_ms: float = 0.0

    # ---- validations ----
    semantic_clean_path_pass: bool = False
    execution_integrity_pass: bool = False


# =====================================================================
# RunDelta
# =====================================================================

class RunDelta(BaseModel):
    strategy: ExperimentStrategy

    task_calls_delta_vs_full: int = 0
    tool_task_calls_delta_vs_full: int = 0
    llm_task_calls_delta_vs_full: int = 0
    artifact_materializations_delta_vs_full: int = 0
    tool_artifact_materializations_delta_vs_full: int = 0
    llm_artifact_materializations_delta_vs_full: int = 0
    llm_calls_delta_vs_full: int = 0

    llm_call_reduction_vs_full: float = 0.0
    materialization_reduction_vs_full: float = 0.0
    tool_materialization_reduction_vs_full: float = 0.0

    artifact_reuse_rate: float = 0.0
    uarr: float = 0.0
    recovery_materialization_precision: float = 0.0
    affected_recovery_precision_arp: float = 0.0
    recompute_amplification_factor: float = 0.0
    recompute_amplification: float = 0.0
    task_amplification_factor: float = 0.0
    unaffected_preservation_rate: float = 0.0
    active_reuse_rate: float = 0.0


# =====================================================================
# StructuralComparison
# =====================================================================

class StructuralComparison(BaseModel):
    case_id: str
    full: ExperimentResult
    coarse_checkpoint: ExperimentResult
    selective: ExperimentResult
    golden_output_hash: str
    deltas: Dict[str, RunDelta] = Field(default_factory=dict)


# =====================================================================
# Aggregation: events -> ExperimentResult
# =====================================================================

def aggregate_events(
    events: List[ExecutionEvent],
    *,
    case_id: str,
    strategy: ExperimentStrategy,
) -> dict:
    """Derive every counter in ExperimentResult from the event stream.
    Returns a dict that can be splat-merged into a partial ExperimentResult.

    Invariants enforced here (and tested):
        task_calls                == tool_task_calls + llm_task_calls
        artifact_materializations == tool_artifact_materializations + llm_artifact_materializations
        llm_calls                 == sum(events[*].llm_calls)
    """
    tool_task_calls = 0
    llm_task_calls = 0
    tool_art = 0
    llm_art = 0
    llm_calls = 0
    in_tok = 0
    out_tok = 0
    total_tok = 0
    wall = 0.0
    recomputed: List[str] = []
    executed_units: List[str] = []
    for e in events:
        executed_units.append(e.execution_unit)
        if e.task_kind == "llm":
            llm_task_calls += 1
            llm_calls += e.llm_calls
            llm_art += len(e.produced_refs)
        else:
            tool_task_calls += 1
            tool_art += len(e.produced_refs)
        for r in e.produced_refs:
            recomputed.append(f"{r.run_id}/{r.artifact_id}@v{r.version}")
        in_tok += e.synthetic_input_tokens
        out_tok += e.synthetic_output_tokens
        total_tok += e.synthetic_total_tokens
        wall += e.local_elapsed_ms

    return {
        "events": events,
        "tool_task_calls": tool_task_calls,
        "llm_task_calls": llm_task_calls,
        "task_calls": tool_task_calls + llm_task_calls,
        "tool_artifact_materializations": tool_art,
        "llm_artifact_materializations": llm_art,
        "artifact_materializations": tool_art + llm_art,
        "llm_calls": llm_calls,
        "synthetic_input_tokens": in_tok,
        "synthetic_output_tokens": out_tok,
        "synthetic_total_tokens": total_tok,
        "local_wall_clock_ms": wall,
        "recomputed_artifact_ids": recomputed,
        "executed_units": executed_units,
    }


def compute_uarr(unaffected_recomputed: int, artifact_materializations: int) -> float:
    """Day-10 definition: unaffected_recomputed / materializations.  Kept
    for back-compat.  Day 11 prefers UARR_v2 = unnecessary /
    materializations, but the two numerators are derived from the same
    event stream and should be equal in the absence of identity
    double-materialization."""
    if artifact_materializations <= 0:
        return 0.0
    return unaffected_recomputed / float(artifact_materializations)


def compute_uarr_v2(unnecessary_materializations: int, artifact_materializations: int) -> float:
    """Day-11 UARR — counts every non-required materialization."""
    if artifact_materializations <= 0:
        return 0.0
    return unnecessary_materializations / float(artifact_materializations)


def compute_artifact_reuse_rate(reused: int, available: int) -> float:
    if available <= 0:
        return 0.0
    return reused / float(available)


def compute_arp(recomputed_affected: int, total_materializations: int) -> float:
    """Affected Recovery Precision: of all materializations, what fraction
    are recomputing an actually-affected identity.  Complement of UARR
    only when no identity is materialized twice.  We compute it
    independently here so the relationship is empirical, not assumed."""
    if total_materializations <= 0:
        return 0.0
    return recomputed_affected / float(total_materializations)


def compute_recompute_amplification(
    total_materializations: int, affected_identity_count: int,
) -> float:
    if affected_identity_count <= 0:
        return 0.0
    return total_materializations / float(affected_identity_count)


def compute_llm_call_reduction(full: int, strategy: int) -> float:
    if full <= 0:
        return 0.0
    return (full - strategy) / float(full)


def compute_materialization_reduction(full: int, strategy: int) -> float:
    if full <= 0:
        return 0.0
    return (full - strategy) / float(full)


# =====================================================================
# Helpers
# =====================================================================

def hash_content(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def artifact_id_of(ref: ArtifactRef) -> str:
    return ref.artifact_id


def _strip_run_and_version(s: str) -> str:
    """'run_X/field_revenue_2025@v3' -> 'field_revenue_2025'"""
    if "/" in s:
        s = s.split("/", 1)[1]
    if "@v" in s:
        s = s.split("@v", 1)[0]
    return s


def compute_reuse_and_uarr(
    recomputed_artifact_ids: List[str],
    affected_identities: Set[str],
    available_unaffected_identities: Set[str],
) -> dict:
    """Given the recomputed-artifact-id list from an experiment, return:
        - recomputed_affected: identities in `affected_identities` that
          were re-materialized.
        - recomputed_unaffected: identities in
          `available_unaffected_identities` that were re-materialized.
        - reused_unaffected: identities in
          `available_unaffected_identities` that were NOT
          re-materialized (i.e. the strategy kept the existing version).
    All three are returned as sorted lists.
    """
    recomputed_ids = {_strip_run_and_version(s) for s in recomputed_artifact_ids}
    recomputed_affected = sorted(affected_identities & recomputed_ids)
    recomputed_unaffected = sorted(available_unaffected_identities & recomputed_ids)
    reused_unaffected = sorted(available_unaffected_identities - recomputed_ids)
    return {
        "recomputed_affected": recomputed_affected,
        "recomputed_unaffected": recomputed_unaffected,
        "reused_unaffected": reused_unaffected,
        "recomputed_unaffected_count": len(recomputed_unaffected),
        "reused_unaffected_count": len(reused_unaffected),
    }


# =====================================================================
# Definitions (embedded in machine-readable JSON)
# =====================================================================

DEFINITIONS: Dict[str, str] = {
    "experiment_version":
        "Day 11 / metric-semantics-finalization (synthetic FakeLLM, NOT real model)",
    "provider_type":
        "FakeLLMClient (deterministic, no network, synthetic)",
    "usage_type":
        "synthetic — token counts are fixture values, NOT real model tokens",
    "latency_type":
        "local_wall_clock_ms — local Python timing, NOT real model latency",
    "real_model_reserved_for": "Day 12+",

    "RECOVERY_PHASE_START":
        "Fault + ContaminationResult already prepared.  No baseline / "
        "fault / validator / contamination-analysis cost is counted.",

    "RECOVERY_PHASE_END":
        "Final target passed both semantic-clean-path and "
        "execution-integrity validation, and final content matches the "
        "case-specific golden clean output.",

    "tool_task_calls":
        "Number of times a tool execution unit was actually invoked "
        "(e.g. rerun_field_extraction = 1, compute_revenue_growth = 1, "
        "merge_report = 1).",

    "llm_task_calls":
        "Number of times an LLM execution unit was actually invoked "
        "(e.g. generate_financial_claim = 1, generate_user_section = 1).",

    "task_calls":
        "tool_task_calls + llm_task_calls.",

    "tool_artifact_materializations":
        "Number of new content versions produced by tool tasks.",

    "llm_artifact_materializations":
        "Number of new content versions produced by LLM tasks.",

    "artifact_materializations":
        "tool_artifact_materializations + llm_artifact_materializations.  "
        "Note: a single tool_task_call (e.g. rerun_field_extraction) may "
        "produce many artifact versions; this counter sums over versions, "
        "not over calls.",

    "llm_calls":
        "Sum of events[*].llm_calls.  For Day 9/10 FakeLLM, each LLM "
        "task call produces exactly 1 LLM call, so llm_calls == "
        "llm_task_calls.",

    "affected_artifact_identities":
        "Set of artifact_ids that, at Recovery Phase Start, are "
        "INVALIDATED or CONTAMINATED.  Identity is artifact_id (not the "
        "exact bad version ref).",

    "available_unaffected_artifact_identities":
        "Set of artifact_ids that are in the target's relevant workflow "
        "closure, are NOT in affected_artifact_identities, and are "
        "therefore candidates for reuse.",

    "reused_unaffected_artifact_identities":
        "Subset of available_unaffected_artifact_identities whose "
        "existing exact version the strategy directly referenced in "
        "the recovered final report (i.e. did NOT re-materialize).",

    "ArtifactReuseRate":
        "reused_unaffected_artifact_identities / "
        "available_unaffected_artifact_identities.",

    "unaffected_artifacts_recomputed":
        "Subset of available_unaffected_artifact_identities that the "
        "strategy chose to re-materialize (a new version was created) "
        "during the Recovery Phase.",

    "UARR":
        "Unnecessary Artifact Recomputation Ratio = "
        "unaffected_artifacts_recomputed / artifact_materializations.  "
        "Computed independently from ARP.",

    "ARP":
        "Affected Recovery Precision = "
        "(recomputed materializations whose identity IS in "
        "affected_artifact_identities) / artifact_materializations.  "
        "ARP + UARR == 1 iff no identity is materialized more than once.",

    "RecomputeAmplification":
        "artifact_materializations / "
        "affected_artifact_identity_count.  Measures how many extra "
        "versions a strategy materializes per affected identity.  "
        "Ideal (no waste) == 1.",

    "LLMCallReductionVsFull":
        "(Full.llm_calls - Strategy.llm_calls) / Full.llm_calls.  "
        "Counted as a structural POC indicator only — NOT a real-cost claim.",

    "ArtifactMaterializationReductionVsFull":
        "(Full.artifact_materializations - Strategy.artifact_materializations) "
        "/ Full.artifact_materializations.  Structural indicator only.",

    "matches_golden_output":
        "True iff the final merged-report content equals the case-"
        "specific golden clean output (string-equal, deterministic FakeLLM).",

    "semantic_clean_path_pass":
        "True iff validate_target_clean_path passes on the final target "
        "(Day-7 INPUT-for-tool / EVIDENCE-for-llm propagation).",

    "execution_integrity_pass":
        "True iff validate_recovery_execution_integrity passes on the "
        "final target (Day-9: every consumed input is VALID/RECOVERED).  "
        "recovery_success = semantic_clean AND execution_integrity AND "
        "matches_golden_output.",

    "fairness_constraints":
        "All three strategies start from an equivalent fault scenario "
        "built by build_*_fault_scenario().  All use the same FakeLLM "
        "responder configuration.  No cross-strategy caching.  The "
        "Recovery Phase log is reset at the same point for every "
        "strategy.",

    "Day_9_metric_correction":
        "Day 9 originally defined UARR with a wrong denominator and "
        "reuse-rate numerator and got 100% for all three strategies.  "
        "Day 10 fixes this by basing both on the per-case "
        "affected_artifact_identities and available_unaffected_artifact_"
        "identities sets, derived directly from ContaminationResult.",

    "Day_10_metric_bug_root_cause":
        "Day 10's `available_unaffected_artifact_identities` was defined "
        "as `target_closure − affected`.  But the target closure was the "
        "BFS ancestors of report_merged, which does NOT include the "
        "2 fields that the workflow reads in baseline but never "
        "propagates to the final report (field_cost_2024, "
        "field_visits_2024 — neither is consumed by any metric that "
        "feeds report_merged).  FULL_RERUN's rerun_field_extraction "
        "still re-materializes all 10 fields, so 2 of its 23 "
        "materializations fell into neither the `affected` nor the "
        "`available` bucket.  Day 11 fixes this by classifying EVERY "
        "materialization as either REQUIRED_RECOVERY or "
        "UNNECESSARY_RECOMPUTATION, regardless of which target closure "
        "the identity nominally belongs to.",

    "Day_11_ARR_bug_root_cause":
        "Day 11's ARR numerator/denominator were built from "
        "plan.required_input_refs (as a set of unique identities).  "
        "That set incorrectly counted the 3 financial metric v2 refs "
        "as 'required clean prerequisites' (the plan input refs are "
        "v2, but those v2 versions ARE themselves bad and the "
        "executor will REPLACE them with v3 before they reach the "
        "REPROMPT step).  Day 12 fixes this by classifying each "
        "(step, input) into SOURCE / CLEAN / RECOVERED via the "
        "replacement_map: CLEAN only counts inputs whose original "
        "ref is NOT replaced by the recovery plan, ACTIVE_REUSE is "
        "verified against the actual event stream.  The correct "
        "Main Case ARR denominator is 4 (field_revenue_2024, "
        "field_cost_2025, user_section, op_section), and the "
        "numerator is 4.  The previous 3/3 was wrong; the comment "
        "'3 financial metrics v1' was wrong because the plan never "
        "consumes the v1 metrics — it consumes v2 then replaces "
        "them.",

    "TAF_renaming":
        "Day 12 renames `minimum_required_execution_unit_count` to "
        "`reference_recovery_task_count` (kept as deprecated alias).  "
        "TAF is now defined as `strategy.task_calls / "
        "reference_recovery_task_count`, where the denominator is the "
        "actual SELECTIVE plan's step count for the scenario.  The "
        "denominator is a reference baseline derived from the current "
        "planner, not a claim of optimality.",

    "REQUIRED_RECOVERY":
        "A materialization whose identity is in "
        "`required_recovery_identities` (the strategy-independent set "
        "derived from the Phase-Start ContaminationResult + target "
        "closure).",

    "UNNECESSARY_RECOMPUTATION":
        "A materialization whose identity is NOT in "
        "`required_recovery_identities`.  This includes both the "
        "clean-but-not-required identities (re-materialized waste) AND "
        "the clean-but-completely-irrelevant identities (e.g. "
        "field_cost_2024 in the Main Case, which the pipeline does "
        "not even read).",

    "RMP":
        "Recovery Materialization Precision = required_materializations "
        "/ artifact_materializations.  Invariant: RMP + UARR == 1.  "
        "Day-10 'ARP' is preserved as a deprecated alias.",

    "UARR_Day11":
        "Unnecessary Artifact Recomputation Ratio = "
        "unnecessary_materializations / artifact_materializations.  "
        "Day-10's UARR (which was based on the smaller 'available' set) "
        "is preserved as `uarr` for back-compat, but `UARR` in the "
        "new metrics is the Day-11 definition.",

    "ACTIVELY_REUSED":
        "Clean identity whose existing version was an `input_ref` or "
        "`reused_ref` of at least one ExecutionEvent.  Derived from "
        "the event stream — NOT from `available − recomputed` (which "
        "would over-credit the strategy).",

    "PRESERVED_UNAFFECTED":
        "Identity that was clean (not in required_recovery) and was NOT "
        "re-materialized in the Recovery Phase.  Includes ACTIVELY_REUSED.",

    "SKIPPED_IRRELEVANT":
        "Identity in `available_unaffected_artifact_identities` that "
        "was neither re-materialized nor actively consumed.  The "
        "strategy's recovery plan simply did not need it.",

    "UPR":
        "Unaffected Preservation Rate = preserved_unaffected_count / "
        "available_unaffected_count.  Measures 'did the strategy leave "
        "the clean upstream alone?'",

    "ARR":
        "Active Reuse Rate = actively_reused_clean_prerequisites / "
        "required_clean_prerequisites.  Measures 'when the recovery "
        "needed a clean upstream, did the strategy consume the "
        "existing version rather than re-materialize a new one?'",

    "RAF":
        "Recompute Amplification Factor = artifact_materializations / "
        "required_recovery_identity_count.  Ideal == 1.",

    "TAF":
        "Task Amplification Factor = task_calls / "
        "minimum_required_execution_unit_count.  Ideal == 1.  The "
        "denominator is the recovery plan produced by "
        "SelectiveRecoveryPlanner for the scenario.",

    "source_of_truth_exclusion":
        "`raw_data` is the source of truth; it is never re-materialized "
        "and is excluded from UARR / RMP / UPR / ARR numerators and "
        "denominators.  It is reported separately as "
        "`source_artifacts_reused`.",
}
