"""
Materialization classification — Day 12 (refinement).

Each Recovery Phase materialization is classified into exactly one of
two buckets, with a third class for source-of-truth artifacts that
are never recomputed (raw_data):

  REQUIRED_RECOVERY          identity is in `required_recovery_identities`
  UNNECESSARY_RECOMPUTATION  identity is NOT in `required_recovery_identities`
                             (this includes: clean-but-unaffected,
                             clean-prerequisite, AND clean-but-
                             completely-irrelevant artifacts that the
                             strategy happened to re-materialize)
  SOURCE_OF_TRUTH            raw_data@v1 (never classified as either
                             of the above; excluded from both UARR
                             and reuse-rate denominators)

Day 12 additions:
  - PrerequisiteAuditRecord  : per (step, input_ref) row that records
                                original_input_ref, resolved_input_ref,
                                category (SOURCE / CLEAN / RECOVERED),
                                actively_reused flag, and reason.
  - build_prerequisite_audit : derives the audit from a SELECTIVE
                                plan + replacement_map + event stream.
  - ARR is now derived from
      required_clean_prerequisite_refs  (denominator)
      actively_reused_clean_prerequisite_refs  (numerator)
    both as unique identity sets, with the exact refs preserved in
    ExperimentResult for audit.
  - missing_clean_prerequisite_refs = required - actively_reused;
    when ARR == 1 this set MUST be empty.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

from pydantic import BaseModel

from artifacts import ArtifactRef
from provenance.graph import ArtifactGraph

from .models import ExecutionEvent


SOURCE_OF_TRUTH_ARTIFACT = "raw_data"


CATEGORY_SOURCE = "SOURCE_PREREQUISITE"
CATEGORY_CLEAN = "CLEAN_PREREQUISITE"
CATEGORY_RECOVERED = "RECOVERED_PREREQUISITE"


class MaterializationClassification(BaseModel):
    """Audit record for one produced artifact version."""
    produced_ref: ArtifactRef
    identity: str
    classification: str          # "REQUIRED_RECOVERY" | "UNNECESSARY_RECOMPUTATION"
    reason: str


class PrerequisiteAuditRecord(BaseModel):
    """Per (step, input_ref) audit row.  The full list, in plan order,
    is the ground truth for ARR and for the missing-prereq check."""
    step_order: int
    execution_unit: str

    original_input_ref: ArtifactRef     # exact ref recorded in plan.required_input_refs
    resolved_input_ref: ArtifactRef     # what the executor actually consumed
                                         # (after replacement_map, or original
                                         # if no replacement)

    category: str                      # SOURCE | CLEAN | RECOVERED
    actively_reused: bool              # True iff the exact resolved_ref
                                       # appeared in some event's
                                       # input_refs or reused_refs
    reason: str


# =====================================================================
# Required recovery identities — strategy-independent ground truth
# =====================================================================

def derive_required_recovery_identities(
    *,
    contamination_invalidated: List[ArtifactRef],
    contamination_contaminated: List[ArtifactRef],
    target_ref: ArtifactRef,
    target_recovery_closure: Set[str],
) -> Set[str]:
    """The set of artifact identities that MUST be re-materialized to
    produce a clean `target_ref` from the given contamination snapshot.

    Rule: any artifact_id that is either INVALIDATED, CONTAMINATED, or
    is the target itself, AND lies in the target's recovery closure,
    must be re-materialized.  raw_data is never required (it is the
    source of truth).

    This is the SAME definition for every strategy.  Strategies are
    not consulted.
    """
    required: Set[str] = set()
    for r in list(contamination_invalidated) + list(contamination_contaminated):
        if r.artifact_id in target_recovery_closure:
            required.add(r.artifact_id)
    required.add(target_ref.artifact_id)
    required.discard(SOURCE_OF_TRUTH_ARTIFACT)
    return required


def derive_target_recovery_closure(
    graph: ArtifactGraph,
    target_ref: ArtifactRef,
) -> Set[str]:
    """artifact_ids reachable from the target via reverse BFS, plus
    the target itself.  raw_data is excluded."""
    closure: Set[str] = {target_ref.artifact_id}
    for ref in graph.get_ancestors(target_ref) | {target_ref}:
        closure.add(ref.artifact_id)
    closure.discard(SOURCE_OF_TRUTH_ARTIFACT)
    return closure


# =====================================================================
# Required clean prerequisites
# =====================================================================

def derive_required_clean_prerequisite_identities(
    plan_steps: List[dict],
) -> Set[str]:
    """artifact_ids that are clean (NOT in required_recovery) but must
    be re-consumed as inputs by a recovery step's REPROMPT or
    TOOL_RECOMPUTE — they are the *execution prerequisites*.

    Derived from the plan steps' input refs minus the required
    recovery identities.

    Note: this is the *ground-truth* prerequisite set, not what the
    strategy actually reused at runtime.  Use it as the denominator
    for ARR (active reuse rate)."""
    prereq: Set[str] = set()
    for step in plan_steps:
        for r in step.get("required_input_refs", []):
            prereq.add(r.artifact_id)
    return prereq


# =====================================================================
# classify produced materializations
# =====================================================================

def classify_materializations(
    produced_refs: List[ArtifactRef],
    required_identities: Set[str],
) -> List[MaterializationClassification]:
    """For every produced artifact version, emit one classification
    record.  The partition invariant (required + unnecessary ==
    len(produced_refs)) holds by construction."""
    out: List[MaterializationClassification] = []
    seen_identities: Set[str] = set()
    for ref in produced_refs:
        cls = (
            "REQUIRED_RECOVERY"
            if ref.artifact_id in required_identities
            else "UNNECESSARY_RECOMPUTATION"
        )
        if cls == "REQUIRED_RECOVERY":
            reason = f"identity {ref.artifact_id!r} is in required_recovery_identities (was INVALIDATED or CONTAMINATED at Phase Start, or is the target itself)"
        else:
            reason = (
                f"identity {ref.artifact_id!r} is clean (not in "
                f"required_recovery_identities); this version was "
                f"re-materialized unnecessarily"
            )
        # Mark whether this identity was previously seen (for audit only;
        # we do not change classification on duplicates).
        out.append(MaterializationClassification(
            produced_ref=ref,
            identity=ref.artifact_id,
            classification=cls,
            reason=reason,
        ))
        seen_identities.add(ref.artifact_id)
    return out


# =====================================================================
# Active reuse / Preserved / Skipped — derived from the EVENT STREAM
# =====================================================================

def _strip_run_and_version(s: str) -> str:
    if "/" in s:
        s = s.split("/", 1)[1]
    if "@v" in s:
        s = s.split("@v", 1)[0]
    return s


def derive_active_reuse_and_preserved(
    events: List[ExecutionEvent],
    *,
    required_identities: Set[str],
    available_unaffected: Set[str],
    plan_step_input_refs: List[List[ArtifactRef]] | None = None,
) -> Dict[str, List[str]]:
    """Walk the event stream and produce three identity sets:

      - ACTIVELY_REUSED      : clean identity whose existing version
                                (one that the strategy did NOT
                                re-materialize in this run) was
                                an input_ref or reused_ref in some
                                event.  Excludes identities the
                                strategy itself just produced.

      - PRESERVED_UNAFFECTED : identity that was clean (in available
                                or not even in the target closure)
                                and was NOT re-materialized.  Includes
                                ACTIVELY_REUSED.

      - SKIPPED_IRRELEVANT   : identity in available_unaffected that
                                was neither re-materialized nor
                                actively reused.
    """
    # 1) identities the strategy RE-MATERIALIZED in this run.
    #    An identity in this set cannot be "actively reused" — the
    #    version the strategy consumed is a version IT produced.
    recomputed_ids: Set[str] = set()
    for e in events:
        for r in e.produced_refs:
            recomputed_ids.add(r.artifact_id)

    # 2) identities the strategy ACTUALLY consumed from an existing
    #    version (input_refs or reused_refs in some event).
    #    raw_data is excluded — it is the source of truth, never a
    #    "reuse" decision.  We also exclude any identity the
    #    strategy itself just re-materialized.
    actively_reused_ids: Set[str] = set()
    for e in events:
        for r in e.input_refs:
            if r.artifact_id == SOURCE_OF_TRUTH_ARTIFACT:
                continue
            if r.artifact_id in required_identities:
                continue
            if r.artifact_id in recomputed_ids:
                continue
            actively_reused_ids.add(r.artifact_id)
        for r in e.reused_refs:
            if r.artifact_id == SOURCE_OF_TRUTH_ARTIFACT:
                continue
            if r.artifact_id in required_identities:
                continue
            if r.artifact_id in recomputed_ids:
                continue
            actively_reused_ids.add(r.artifact_id)

    # 3) preserved = clean identities (not in required) that were
    #    not re-materialized.  Includes the actively-reused ones.
    preserved: Set[str] = actively_reused_ids | (
        available_unaffected - recomputed_ids
    )

    # 4) skipped irrelevant = in available_unaffected, not
    #    recomputed, not actively reused.
    skipped = (available_unaffected - recomputed_ids) - actively_reused_ids

    return {
        "actively_reused": sorted(actively_reused_ids),
        "preserved_unaffected": sorted(preserved),
        "skipped_irrelevant": sorted(skipped),
    }


# =====================================================================
# Counting helpers
# =====================================================================

def count_required(classifications: List[MaterializationClassification]) -> int:
    return sum(1 for c in classifications
               if c.classification == "REQUIRED_RECOVERY")


def count_unnecessary(classifications: List[MaterializationClassification]) -> int:
    return sum(1 for c in classifications
               if c.classification == "UNNECESSARY_RECOMPUTATION")


def compute_rmp(required: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return required / float(total)


def compute_uarr_v2(unnecessary: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return unnecessary / float(total)


def compute_upr(preserved_count: int, total_available: int) -> float:
    """Unaffected Preservation Rate = preserved / total_available."""
    if total_available <= 0:
        return 0.0
    return preserved_count / float(total_available)


def compute_arr(
    actively_reused_prereq_count: int,
    required_prereq_count: int,
) -> float:
    """Active Reuse Rate = actively_reused_clean_prerequisites /
    required_clean_prerequisites."""
    if required_prereq_count <= 0:
        return 0.0
    return actively_reused_prereq_count / float(required_prereq_count)


def compute_raf(materializations: int, required_identities_count: int) -> float:
    if required_identities_count <= 0:
        return 0.0
    return materializations / float(required_identities_count)


def compute_taf(task_calls: int, minimum_required_units: int) -> float:
    if minimum_required_units <= 0:
        return 0.0
    return task_calls / float(minimum_required_units)


# =====================================================================
# Day 12: Prerequisite audit
# =====================================================================

def _resolve_ref(
    ref: ArtifactRef, replacement_map: Dict[str, str],
) -> ArtifactRef:
    """Apply the replacement_map to an input ref.  If a replacement
    exists, the new ref is used; otherwise the original is returned."""
    key = ref.to_ref_string()
    if key in replacement_map:
        return ArtifactRef.from_ref_string(replacement_map[key])
    return ref


def _category_for(
    original_ref: ArtifactRef, resolved_ref: ArtifactRef,
) -> str:
    """Classify a (original, resolved) ref pair."""
    if original_ref.artifact_id == SOURCE_OF_TRUTH_ARTIFACT:
        return CATEGORY_SOURCE
    if original_ref.to_ref_string() != resolved_ref.to_ref_string():
        return CATEGORY_RECOVERED
    return CATEGORY_CLEAN


def _is_actively_reused(
    resolved_ref: ArtifactRef,
    events: List[ExecutionEvent],
    recomputed_identities: Set[str],
) -> bool:
    """True iff the exact resolved ref appears as input_ref or
    reused_ref in some event, AND the strategy did NOT re-materialize
    that same identity in this run.

    The second condition is critical: if the strategy produces a
    fresh v2 of the same identity, it consumed the v1 only to feed
    a downstream re-materialization, not to preserve the v1 across
    the recovery.  Counting that as "active reuse" would let FULL
    strategies claim clean-prerequisite reuse they did not actually
    preserve."""
    if resolved_ref.artifact_id in recomputed_identities:
        return False
    for e in events:
        for r in e.input_refs:
            if r.to_ref_string() == resolved_ref.to_ref_string():
                return True
        for r in e.reused_refs:
            if r.to_ref_string() == resolved_ref.to_ref_string():
                return True
    return False


def build_prerequisite_audit(
    *,
    plan_steps: List,
    replacement_map: Dict[str, str],
    events: List[ExecutionEvent],
    recomputed_identities: Optional[Set[str]] = None,
) -> List[PrerequisiteAuditRecord]:
    """Walk the SELECTIVE plan, apply replacement_map, classify every
    (step, input) pair, and check active reuse against the event stream.
    An input is `actively_reused` only if the strategy both (a)
    consumed its existing version and (b) did NOT itself re-materialize
    that same identity in the Recovery Phase.
    """
    if recomputed_identities is None:
        recomputed_identities = {
            r.artifact_id for e in events for r in e.produced_refs
        }
    audit: List[PrerequisiteAuditRecord] = []
    for step in plan_steps:
        for original in step.required_input_refs:
            resolved = _resolve_ref(original, replacement_map)
            category = _category_for(original, resolved)
            reused = _is_actively_reused(resolved, events, recomputed_identities)
            if category == CATEGORY_SOURCE:
                reason = (
                    f"{original.artifact_id}@{original.version} is the "
                    f"source of truth; consumed directly, never reused "
                    f"or replaced."
                )
            elif category == CATEGORY_RECOVERED:
                reason = (
                    f"{original.artifact_id}@{original.version} was "
                    f"replaced by {resolved.artifact_id}@{resolved.version} "
                    f"in the recovery plan (recovery step produced a "
                    f"new recovered version of this identity)."
                )
            else:  # CLEAN
                reason = (
                    f"{original.artifact_id}@{original.version} is clean "
                    f"and was NOT replaced; the executor consumed the "
                    f"existing version directly."
                )
            audit.append(PrerequisiteAuditRecord(
                step_order=step.order,
                execution_unit=step.execution_unit,
                original_input_ref=original,
                resolved_input_ref=resolved,
                category=category,
                actively_reused=reused,
                reason=reason,
            ))
    return audit


def derive_clean_prerequisite_refs(
    audit: List[PrerequisiteAuditRecord],
) -> List[ArtifactRef]:
    """CLEAN prerequisites, exact refs (in audit order)."""
    return [r.resolved_input_ref for r in audit if r.category == CATEGORY_CLEAN]


def derive_clean_prerequisite_identities(
    audit: List[PrerequisiteAuditRecord],
) -> List[str]:
    """Unique clean prerequisite identities (sorted)."""
    return sorted({r.resolved_input_ref.artifact_id for r in audit
                   if r.category == CATEGORY_CLEAN})


def derive_actively_reused_clean_prereq_refs(
    audit: List[PrerequisiteAuditRecord],
) -> List[ArtifactRef]:
    """CLEAN prerequisites that were actively consumed (exact refs)."""
    return [r.resolved_input_ref for r in audit
            if r.category == CATEGORY_CLEAN and r.actively_reused]


def derive_actively_reused_clean_prereq_identities(
    audit: List[PrerequisiteAuditRecord],
) -> List[str]:
    return sorted({r.resolved_input_ref.artifact_id for r in audit
                   if r.category == CATEGORY_CLEAN and r.actively_reused})


def derive_missing_clean_prerequisite_refs(
    audit: List[PrerequisiteAuditRecord],
) -> List[ArtifactRef]:
    """CLEAN prerequisites that were NOT actively consumed (i.e.
    the strategy did not consume an existing version, but should have).

    When ARR < 1, this set is non-empty and contains the missing refs."""
    return [r.resolved_input_ref for r in audit
            if r.category == CATEGORY_CLEAN and not r.actively_reused]


def compute_arr_v2(
    actively_reused_identities: List[str],
    required_clean_identities: List[str],
) -> float:
    if not required_clean_identities:
        return 0.0
    rs = set(actively_reused_identities)
    denom = len(set(required_clean_identities))
    num = len(rs & set(required_clean_identities))
    return num / float(denom)


def derive_canonical_replacement_map(
    plan_steps: List,
    required_recovery_identities: Set[str],
) -> Dict[str, str]:
    """Build a strategy-INDEPENDENT replacement map for the plan.

    For every plan step's input ref whose identity is in
    `required_recovery_identities`, the consumed version is the
    "bad" one and will be replaced by a fresh recovered version
    (current+1) of the same identity.  Otherwise no replacement.

    This lets the audit derive a scenario-wide canonical CLEAN-prereq
    set that is independent of which strategy ran the recovery.
    """
    canonical: Dict[str, str] = {}
    for step in plan_steps:
        for original in step.required_input_refs:
            if original.artifact_id in required_recovery_identities:
                replaced = ArtifactRef(
                    run_id=original.run_id,
                    artifact_id=original.artifact_id,
                    version=original.version + 1,
                )
                canonical[original.to_ref_string()] = replaced.to_ref_string()
    return canonical
