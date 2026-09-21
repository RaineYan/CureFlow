"""
CoarseCheckpointStrategy — recover at the artifact-type boundary.

Planning rule:
  - Find the target's upstream closure.
  - For every metric in the closure -> TOOL_RECOMPUTE
  - For every LLM artifact (claim / section) in the closure
    -> REPROMPT
  - For every field in the closure -> REUSE (the data row is
    still valid; only the *computed* downstream is suspect)
  - report_merged -> TOOL_RECOMPUTE (always re-merge at the end)

Rationale (Day 9/10 paper): "coarse checkpoint" is a common
real-world pattern where the database is trusted as ground
truth, and recovery starts at the metric level.  Compared to
SELECTIVE it over-recovers (it does not check whether a metric
is actually bad) but under-recovers in the sense that it never
touches a field.  It is the natural midpoint between
SELECTIVE_RECOVERY (precision) and FULL_RERUN (recall).
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


@register_strategy("COARSE_CHECKPOINT")
class CoarseCheckpointStrategy(_BaseStrategyMixin):
    name = "COARSE_CHECKPOINT"

    def applicable(self, cr: ContaminationResult) -> bool:
        # Coarse checkpoint only makes sense if there's something
        # downstream of fields to recompute.  We accept any
        # contamination result (a 0-step plan is valid and the
        # executor short-circuits).
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

        # classify: every metric/llm/merged -> action; every
        # field -> REUSE (coarse policy: trust the row)
        actions: Dict[Key, RecoveryAction] = {}
        for k in closure:
            a = store.get_version(k[0], k[1], k[2])
            if a is None:
                continue
            kind = a.meta.get("task_kind", "tool")
            if kind == "llm":
                actions[k] = RecoveryAction.REPROMPT
            elif a.artifact_id == "report_merged":
                actions[k] = RecoveryAction.TOOL_RECOMPUTE
            elif a.artifact_type == "Metric":
                actions[k] = RecoveryAction.TOOL_RECOMPUTE
            else:
                # field artifact or anything else: REUSE
                actions[k] = RecoveryAction.REUSE

        # topologically order (deepest first)
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
        if action == RecoveryAction.TOOL_RECOMPUTE:
            return (
                f"{artifact.artifact_id} v{artifact.version}: coarse policy "
                "recompute at metric/merge checkpoint"
            )
        if action == RecoveryAction.REPROMPT:
            return (
                f"{artifact.artifact_id} v{artifact.version}: coarse policy "
                "reprompt at LLM boundary"
            )
        return (
            f"{artifact.artifact_id} v{artifact.version}: coarse policy "
            "REUSE — fields trusted as ground truth"
        )
