"""
FullRerunStrategy — the brute-force baseline.

Planning rule:
  - Take the target's upstream closure.
  - Recompute EVERY non-field artifact regardless of bad/good
    status.  Fields are REUSED (raw data is the input contract).
  - report_merged is always at the end (deepest).

Rationale: this is the "blunt instrument" baseline.  It is the
upper bound on cost and the lower bound on the precision/recall
trade-off.  Used in the Day 9/10 experiments as the reference
point against which SELECTIVE_RECOVERY measures its savings.
"""
from __future__ import annotations

from typing import Dict, List, Set, Tuple

from artifacts import Artifact, ArtifactRef, ArtifactStore
from contamination import ContaminationResult
from provenance.graph import ArtifactGraph

from recovery.models import RecoveryAction, RecoveryPlan, RecoveryStep
from recovery.strategy import (
    RecoveryContext,
    _BaseStrategyMixin,
    register_strategy,
)


Key = Tuple[str, str, int]


def _key(ref: ArtifactRef) -> Key:
    return (ref.run_id, ref.artifact_id, ref.version)


@register_strategy("FULL_RERUN")
class FullRerunStrategy(_BaseStrategyMixin):
    name = "FULL_RERUN"

    def applicable(self, cr: ContaminationResult) -> bool:
        # Full rerun applies to any contamination result; its
        # plan length is `len(closure) - fields`, so even a clean
        # upstream closure (cost_ratio-only control case) gets a
        # non-empty plan that recomputes every metric.  This is
        # the cost we are paying for the blunt approach.
        return True

    def plan(
        self,
        cr: ContaminationResult,
        ctx: RecoveryContext,
    ) -> RecoveryPlan:
        graph = ctx.graph
        store = ctx.store
        target_ref = ctx.target_ref
        run_id = ctx.run_id

        closure: Set[Key] = set()
        for a in graph.get_ancestors(target_ref):
            closure.add(_key(a))
        closure.add(_key(target_ref))

        actions: Dict[Key, RecoveryAction] = {}
        for k in closure:
            a = store.get_version(k[0], k[1], k[2])
            if a is None:
                continue
            kind = a.meta.get("task_kind", "tool")
            if kind == "llm":
                actions[k] = RecoveryAction.REPROMPT
            elif a.artifact_type in ("Field", "RawData"):
                # Field & RawData are inputs / row data; never recomputed
                actions[k] = RecoveryAction.REUSE
            else:
                actions[k] = RecoveryAction.TOOL_RECOMPUTE

        def _depth(k: Key, cache: Dict[Key, int]) -> int:
            if k in cache:
                return cache[k]
            ref = ArtifactRef(run_id=k[0], artifact_id=k[1], version=k[2])
            preds = [p for p in graph.get_upstream(ref) if _key(p) in actions]
            if not preds:
                cache[k] = 0
                return 0
            cache[k] = max(_depth(_key(p), cache) for p in preds) + 1
            return cache[k]

        depth: Dict[Key, int] = {}
        for k in actions:
            _depth(k, depth)
        ordered = sorted(actions.keys(), key=lambda k: depth[k])

        steps: List[RecoveryStep] = []
        for i, k in enumerate(ordered, start=1):
            ref = ArtifactRef(run_id=k[0], artifact_id=k[1], version=k[2])
            a = store.get_version(k[0], k[1], k[2])
            if a is None:
                continue
            required = list(a.input_artifact_ids)
            steps.append(RecoveryStep(
                order=i,
                action=actions[k],
                target_ref=ref,
                execution_unit=a.producer_task,
                reason=self._reason_for(actions[k], a),
                required_input_refs=required,
            ))

        reused_refs: List[ArtifactRef] = []
        return RecoveryPlan(
            run_id=run_id,
            fault_sources=list(cr.invalidated),
            target_ref=target_ref,
            steps=steps,
            reused_refs=reused_refs,
            strategy_name=self.name,
        )

    @staticmethod
    def _reason_for(action: RecoveryAction, artifact: Artifact) -> str:
        if action == RecoveryAction.REUSE:
            return (
                f"{artifact.artifact_id} v{artifact.version}: full rerun "
                "REUSE — fields are inputs, not derived"
            )
        if action == RecoveryAction.TOOL_RECOMPUTE:
            return (
                f"{artifact.artifact_id} v{artifact.version}: full rerun "
                "recompute every derived metric/merge"
            )
        if action == RecoveryAction.REPROMPT:
            return (
                f"{artifact.artifact_id} v{artifact.version}: full rerun "
                "reprompt every LLM artifact"
            )
        return ""
