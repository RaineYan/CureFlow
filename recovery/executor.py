"""
RecoveryExecutor — Day 8 + Day 14 v2 strategy dispatch.

Executes a RecoveryPlan step by step, applying **version remapping**:
each step's required_input_refs are looked up in the running
replacement_map; if found, the new ref is used; otherwise the exact
original ref is used. This guarantees that the recovered artifact's
recorded input_artifact_ids point at the recovered upstream versions
and never at the bad ones.

After every step:
  - replacement_map is updated
  - the new artifact's status is set to RECOVERED (if the action
    was a recompute/reprompt that produced a new version)

At the end:
  - validate_target_clean_path() confirms that the FINAL target
    (recovered version) has no INVALIDATED / CONTAMINATED ancestor.
  - The old (bad) versions stay in the store with their original
    statuses (INVALIDATED / CONTAMINATED) — that is the fault
    history. Recovery does NOT rewrite history.

Day 14 v2 — strategy dispatch:
  Per-step execution is now a `Strategy.dispatch_step(step, ctx)`
  call instead of an in-class if/elif chain.  The strategy is
  looked up by `plan.strategy_name` in `recovery.strategy.REGISTRY`
  (default "SELECTIVE_RECOVERY").  The per-action execution logic
  for the three built-in actions (REUSE / TOOL_RECOMPUTE / REPROMPT)
  moved to `recovery.strategy._BaseStrategyMixin` so a custom
  strategy can inherit it and just override `plan`.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

from artifacts import (
    Artifact,
    ArtifactRef,
    ArtifactStatus,
    ArtifactStore,
)
from llm import LLMClient
from provenance.decorator import RunContext
from provenance.graph import ArtifactGraph

from .models import (
    CleanPathAuditResult,
    ExecutedRecoveryStep,
    RecoveryAction,
    RecoveryExecutionResult,
    RecoveryPlan,
)
from .strategy import REGISTRY, RecoveryContext
# Side-effect: populate REGISTRY with the 3 built-in strategies
from . import strategies as _strategies  # noqa: F401


# Map ref_string -> ref_string
RefMap = Dict[str, str]


def _ref_key(ref: ArtifactRef) -> str:
    return ref.to_ref_string()


def _remapped(
    store: ArtifactStore,
    refs: List[ArtifactRef],
    mapping: RefMap,
) -> List[Artifact]:
    """For each ref, look up the replacement or fetch the original from the store.

    IMPORTANT: never uses store.get(id) which returns latest. Always
    uses get_version with the exact ref to keep recovery provenance
    reproducible.
    """
    out: List[Artifact] = []
    for r in refs:
        target_ref = r
        if _ref_key(r) in mapping:
            target_ref = ArtifactRef.from_ref_string(mapping[_ref_key(r)])
        a = store.get_version(target_ref.run_id, target_ref.artifact_id, target_ref.version)
        if a is None:
            raise KeyError(
                f"recovery executor: cannot fetch {_ref_key(target_ref)}"
            )
        out.append(a)
    return out


def _fetch_exact(store: ArtifactStore, ref: ArtifactRef) -> Optional[Artifact]:
    return store.get_version(ref.run_id, ref.artifact_id, ref.version)


def _validate_clean_ref(store: ArtifactStore, ref: ArtifactRef) -> str:
    """Return an actionable reason when an exact input cannot be reused."""
    artifact = _fetch_exact(store, ref)
    if artifact is None:
        return f"reuse input {ref.to_ref_string()} is missing"
    if artifact.validation_status not in {
        ArtifactStatus.VALID.value, ArtifactStatus.RECOVERED.value,
    }:
        return (
            f"reuse input {ref.to_ref_string()} is "
            f"{artifact.validation_status}, not VALID/RECOVERED"
        )
    return ""


# =====================================================================
# Executor
# =====================================================================

class RecoveryExecutor:
    def __init__(
        self,
        store: ArtifactStore,
        graph: ArtifactGraph,
        llm_client: LLMClient,
    ):
        self.store = store
        self.graph = graph
        self.llm_client = llm_client

    def execute(self, plan: RecoveryPlan) -> RecoveryExecutionResult:
        replacement: RefMap = {}
        executed: List[ExecutedRecoveryStep] = []
        strategy_name = getattr(plan, "strategy_name", "SELECTIVE_RECOVERY")
        # These two frozen experimental baselines intentionally replay their
        # original field-as-source policy. Strict guards apply to selective
        # (and future) strategies; changing baseline behavior here would make
        # historical cost comparisons non-comparable.
        strict_reuse = strategy_name.upper() not in {"FULL_RERUN", "COARSE_CHECKPOINT"}

        def failed(reason: str) -> RecoveryExecutionResult:
            return RecoveryExecutionResult(
                run_id=plan.run_id, plan=plan, replacement_map=replacement,
                executed_steps=executed, reused_refs=[], success=False,
                failure_reason=reason,
            )

        # Preflight every exact ref that the plan intends to reuse. A bad
        # source must fail before the first tool/model call, not only in the
        # clean-path audit after execution.
        if strict_reuse:
            already_replaced: Set[str] = set()
            for ref in plan.reused_refs:
                reason = _validate_clean_ref(self.store, ref)
                if reason:
                    return failed(reason)
            for step in plan.steps:
                if step.action == RecoveryAction.REUSE:
                    reason = _validate_clean_ref(self.store, step.target_ref)
                    if reason:
                        return failed(reason)
                for ref in step.required_input_refs:
                    if _ref_key(ref) not in already_replaced:
                        reason = _validate_clean_ref(self.store, ref)
                        if reason:
                            return failed(reason)
                if step.action != RecoveryAction.REUSE:
                    already_replaced.add(_ref_key(step.target_ref))

        # Day 14 v2: resolve the strategy for this plan.  The
        # legacy v1 plans did not stamp a strategy_name, so the
        # RecoveryPlan default of "SELECTIVE_RECOVERY" kicks in
        # and we end up with the v1 behaviour byte-for-byte.
        strategy = REGISTRY.get(strategy_name)

        ctx = RecoveryContext(
            run_id=plan.run_id,
            target_ref=plan.target_ref,
            graph=self.graph,
            store=self.store,
            llm_client=self.llm_client,
            run_ctx=RunContext(run_id=plan.run_id, store=self.store),
            plan=plan,
            replacement=replacement,
            executed=executed,
        )

        for step in plan.steps:
            # Recheck at actual consumption; planning and execution may be
            # separated by a status update in the store.
            if strict_reuse:
                refs = [step.target_ref] if step.action == RecoveryAction.REUSE else []
                refs.extend(
                    ArtifactRef.from_ref_string(replacement.get(_ref_key(ref), _ref_key(ref)))
                    for ref in step.required_input_refs
                )
                for ref in refs:
                    reason = _validate_clean_ref(self.store, ref)
                    if reason:
                        return failed(reason)
            # The strategy decides how to run a step.  For the
            # three built-in actions the BaseStrategy mixin
            # returns an ExecutedRecoveryStep; for new actions
            # a 3rd-party strategy's overridden dispatch_step
            # runs here.
            log = strategy.dispatch_step(step, ctx)
            executed.append(log)
            # Update the running replacement_map if the step
            # produced a new artifact.  REUSE steps have
            # produced_ref=None and so do not add an entry.
            if log.produced_ref is not None:
                replacement[_ref_key(step.target_ref)] = _ref_key(log.produced_ref)

        # Determine the final target_ref — last step's produced ref, or
        # the original target if no steps ran (control case).
        final_target = self._resolve_final_target(plan, replacement)

        # Clean-path validation on the final target
        semantic_ok, semantic_reason = validate_target_clean_path(
            final_target, self.graph, self.store,
        )
        # Execution-integrity validation: every consumed input must be
        # clean.  Only required when recovery actually produced new
        # artifacts (Day 9 spec: applies to "本次 recovery 新生成的
        # 每一个 Artifact").  For a control case (target already VALID,
        # 0 steps ran) the integrity check would always fail because
        # the upstream closure contains the original bad node, but no
        # new execution actually consumed it.
        integrity_ok = True
        integrity_reason = ""
        if any(s.action != RecoveryAction.REUSE for s in plan.steps):
            integrity_ok, integrity_reason = validate_recovery_execution_integrity(
                final_target, self.graph, self.store,
            )
        success = semantic_ok and integrity_ok
        if not success:
            failure_reason = (
                f"semantic: {semantic_reason}; integrity: {integrity_reason}"
            )
        else:
            failure_reason = ""

        return RecoveryExecutionResult(
            run_id=plan.run_id,
            plan=plan,
            replacement_map=replacement,
            executed_steps=executed,
            reused_refs=plan.reused_refs,
            final_target_refs=[final_target] if final_target else [],
            success=success,
            failure_reason=failure_reason,
        )

    # ---------- helpers ----------

    def _resolve_final_target(
        self,
        plan: RecoveryPlan,
        replacement: RefMap,
    ) -> Optional[ArtifactRef]:
        if plan.target_ref.to_ref_string() in replacement:
            return ArtifactRef.from_ref_string(
                replacement[plan.target_ref.to_ref_string()]
            )
        # No step ran -> control case: target was never contaminated,
        # so the original target_ref is the final valid artifact.
        return plan.target_ref


# =====================================================================
# Clean-path validation
# =====================================================================

def validate_target_clean_path(
    target_ref: ArtifactRef,
    graph: ArtifactGraph,
    store: ArtifactStore,
) -> Tuple[bool, str]:
    """True iff target and ALL propagating ancestors are VALID or RECOVERED.

    Day-7 propagation policy applies here too: we only walk ancestors
    reachable via propagating edges (INPUT for tool, EVIDENCE for LLM).
    A bad node that is an ancestor in the raw graph but does NOT
    propagate (e.g. metric_cost_ratio -> claim_financial is INPUT to
    an LLM, so it's not a propagating edge) does NOT block clean path.
    """
    target = store.get_version(
        target_ref.run_id, target_ref.artifact_id, target_ref.version,
    )
    if target is None:
        return False, f"target {target_ref.to_ref_string()} not in store"

    bad_states = {
        ArtifactStatus.INVALIDATED.value,
        ArtifactStatus.CONTAMINATED.value,
    }

    if target.validation_status in bad_states:
        return False, (
            f"target {target.artifact_id}@v{target.version} is "
            f"{target.validation_status}"
        )

    # BFS upstream along propagating edges only
    visited: Set[ArtifactRef] = {target_ref}
    stack: List[ArtifactRef] = [target_ref]
    while stack:
        cur = stack.pop()
        cur_art = store.get_version(cur.run_id, cur.artifact_id, cur.version)
        if cur_art is None:
            continue
        # cur_art is VALID or RECOVERED (we already checked target, and
        # by induction any other visited node was added because the
        # edge into it was a propagating edge from a clean node).
        # We still need to check cur_art's own status.
        if cur_art.validation_status in bad_states:
            return False, (
                f"ancestor {cur.artifact_id}@v{cur.version} is "
                f"{cur_art.validation_status}, blocking clean path"
            )
        edge_kind = "INPUT" if cur_art.meta.get("task_kind", "tool") == "tool" else "EVIDENCE"
        for pred in graph.get_upstream(cur):
            if pred in visited:
                continue
            et = graph.edge_type(pred, cur)
            if et is None:
                continue
            types = et if isinstance(et, set) else {et}
            if not any(e.value == edge_kind for e in types):
                continue
            visited.add(pred)
            stack.append(pred)
    return True, ""


# =====================================================================
# Day 13.6 FINAL-FINAL: clean_path_audit
# =====================================================================
#
# Structured clean-path audit.  Returns a `CleanPathAuditResult`
# so a False result is always debuggable from the JSON alone —
# which exact ref, which status, which edge, which version.
#
# The audit uses the SAME BFS as `validate_target_clean_path`
# (Day-7 propagation policy: INPUT for tool, EVIDENCE for LLM)
# but it does NOT short-circuit on the first bad ref.  It walks
# the entire reachable upstream closure so the failure report
# enumerates every non-clean and missing ref.
#
# Clean states: VALID, RECOVERED.
# Bad states:   INVALIDATED, CONTAMINATED.
#
# Day 13.6 FINAL-FINAL invariants:
#   - Exact-ArtifactRef semantics: the audit does NOT cross
#     versions.  v_n+1's bad state does not taint v_n.
#   - The audit does NOT mutate the store or the graph.
#   - `graph_rebuilt_after_recovery` is the caller's
#     responsibility.  When False, the audit still runs but
#     the result is flagged so a stale pre-recovery graph
#     cannot silently produce a false negative.

def clean_path_audit(
    target_ref: ArtifactRef,
    graph: ArtifactGraph,
    store: ArtifactStore,
    *,
    graph_rebuilt_after_recovery: bool = True,
) -> CleanPathAuditResult:
    """Structured clean-path audit.  Returns a
    `CleanPathAuditResult` with the full visited set,
    non-clean refs, and missing refs.

    Caller MUST rebuild the ArtifactGraph from the
    post-recovery store before calling.  When the caller
    cannot prove the graph is post-recovery, set
    `graph_rebuilt_after_recovery=False` so the audit
    surface records the staleness risk.
    """
    bad_states = {
        ArtifactStatus.INVALIDATED.value,
        ArtifactStatus.CONTAMINATED.value,
    }
    clean_states = {
        ArtifactStatus.VALID.value,
        ArtifactStatus.RECOVERED.value,
    }
    visited_in_order: List[ArtifactRef] = []
    clean: List[ArtifactRef] = []
    non_clean: List[dict] = []
    missing: List[ArtifactRef] = []

    seen: Set[ArtifactRef] = set()
    stack: List[ArtifactRef] = [target_ref]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        cur_art = store.get_version(
            cur.run_id, cur.artifact_id, cur.version,
        )
        if cur_art is None:
            missing.append(cur)
            continue
        visited_in_order.append(cur)
        if cur_art.validation_status in bad_states:
            non_clean.append({
                "ref": cur.to_ref_string(),
                "status": cur_art.validation_status,
                "artifact_type": cur_art.artifact_type,
                "producer_task": cur_art.producer_task,
            })
        elif cur_art.validation_status in clean_states:
            clean.append(cur)
        # Walk upstream along PROPAGATING edges only
        # (Day-7 INPUT for tool / EVIDENCE for LLM).
        edge_kind = (
            "INPUT" if cur_art.meta.get("task_kind", "tool") == "tool"
            else "EVIDENCE"
        )
        for pred in graph.get_upstream(cur):
            if pred in seen:
                continue
            et = graph.edge_type(pred, cur)
            if et is None:
                continue
            types = et if isinstance(et, set) else {et}
            if not any(e.value == edge_kind for e in types):
                continue
            stack.append(pred)

    # Stable-dedup by ref string (BFS may revisit via different
    # paths; we keep the first occurrence).
    def _dedupe(refs: List[ArtifactRef]) -> List[ArtifactRef]:
        seen_s: set = set()
        out: List[ArtifactRef] = []
        for r in refs:
            if r.to_ref_string() in seen_s:
                continue
            seen_s.add(r.to_ref_string())
            out.append(r)
        return out
    visited_in_order = _dedupe(visited_in_order)
    clean = _dedupe(clean)

    passed = (len(non_clean) == 0 and len(missing) == 0)
    if passed:
        reason = ""
    elif non_clean:
        first = non_clean[0]
        reason = (
            f"first non-clean ref: {first['ref']} "
            f"status={first['status']}"
        )
    else:
        first = missing[0].to_ref_string() if missing else "(none)"
        reason = f"missing ref in store: {first}"

    return CleanPathAuditResult(
        passed=passed,
        target_ref=target_ref,
        visited_refs=visited_in_order,
        clean_refs=clean,
        non_clean_refs=non_clean,
        missing_refs=missing,
        graph_rebuilt_after_recovery=graph_rebuilt_after_recovery,
        reason=reason,
    )


# =====================================================================
# Execution integrity validation (Day 9 addition)
# =====================================================================

def validate_recovery_execution_integrity(
    target_ref: ArtifactRef,
    graph: ArtifactGraph,
    store: ArtifactStore,
) -> Tuple[bool, str]:
    """True iff EVERY input_artifact_id of `target_ref` (and recursively,
    of every ancestor in the same run) is in a non-bad status.

    This is the Day-9 addition that complements validate_target_clean_path:

      - validate_target_clean_path  -> semantic contamination policy
        (Day-7 INPUT-for-tool / EVIDENCE-for-llm edges only)

      - validate_recovery_execution_integrity -> execution-time
        dependency integrity.  EVERY input that the execution unit
        actually consumed must be VALID or RECOVERED.  An artifact
        can pass the semantic check (e.g. cost_ratio is INPUT-only to
        the financial claim and therefore not a semantic ancestor)
        but still be a real execution prerequisite whose bad state
        would corrupt the rerun.

    Recovery success = semantic_clean AND execution_integrity.
    """
    target = store.get_version(
        target_ref.run_id, target_ref.artifact_id, target_ref.version,
    )
    if target is None:
        return False, f"target {target_ref.to_ref_string()} not in store"

    bad_states = {
        ArtifactStatus.INVALIDATED.value,
        ArtifactStatus.CONTAMINATED.value,
    }

    # walk the FULL upstream closure (all edges, not just propagating)
    # because the execution unit consumed every recorded input.
    queue: List[ArtifactRef] = [target_ref]
    visited: Set[ArtifactRef] = {target_ref}
    while queue:
        cur = queue.pop(0)
        cur_art = store.get_version(cur.run_id, cur.artifact_id, cur.version)
        if cur_art is None:
            continue
        if cur_art.validation_status in bad_states:
            return False, (
                f"execution-integrity: {cur.artifact_id}@v{cur.version} is "
                f"{cur_art.validation_status}"
            )
        for pred in graph.get_upstream(cur):
            if pred in visited:
                continue
            visited.add(pred)
            queue.append(pred)
    return True, ""
