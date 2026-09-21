"""
SelectiveRecoveryStrategy — the v1 "selective" recovery as a strategy.

Planning rule (preserved verbatim from `recovery.planner` v1):
  - Compute the upstream closure of `target_ref`.
  - For each bad node (INVALIDATED / CONTAMINATED) reachable from
    `target_ref` AND `target_ref` itself, decide REPROMPT for LLM
    and TOOL_RECOMPUTE for tool.
  - Topologically order the steps (deepest dep first).
  - `reused_refs` = closure minus bad-keys minus target.

Important precondition:
  - `ContaminationResult` describes the measured impact scope, but planning reads
    the authoritative `validation_status` values from `ArtifactStore`.
  - The requested `target_ref` must therefore be INVALIDATED or CONTAMINATED
    after propagation.  A VALID target intentionally produces an empty plan,
    even when an unrelated artifact elsewhere in the run is contaminated.

This is the smallest, cheapest, most precise strategy; v1 was
this and the v2 refactor keeps it byte-for-byte equivalent
(except it now lives behind the registry and a Protocol).
"""
from __future__ import annotations

from typing import Dict, List, Set, Tuple

from artifacts import Artifact, ArtifactRef, ArtifactStatus, ArtifactStore
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


def _task_kind(artifact: Artifact) -> str:
    return artifact.meta.get("task_kind", "tool")


@register_strategy("SELECTIVE_RECOVERY")
class SelectiveRecoveryStrategy(_BaseStrategyMixin):
    name = "SELECTIVE_RECOVERY"

    def applicable(self, cr: ContaminationResult) -> bool:
        # Selective is the safe default — it always applies as long
        # as there is a contamination result to consult.  The only
        # case we skip is when there is literally no contamination
        # at all (no invalidated, no contaminated, and unaffected
        # is empty); the executor can short-circuit anyway.
        return bool(
            cr.invalidated
            or cr.contaminated
            or cr.unaffected
        )

    def plan(
        self,
        cr: ContaminationResult,
        ctx: RecoveryContext,
    ) -> RecoveryPlan:
        graph = ctx.graph
        store = ctx.store
        target_ref = ctx.target_ref
        run_id = ctx.run_id

        # 1. compute upstream closure of target
        closure: Set[Key] = set()
        for a in graph.get_ancestors(target_ref):
            closure.add(_key(a))
        closure.add(_key(target_ref))

        # 2. classify each artifact in the closure
        bad_keys: Set[Key] = set()
        for k in closure:
            a = store.get_version(k[0], k[1], k[2])
            if a is None:
                continue
            if a.validation_status in (
                ArtifactStatus.INVALIDATED.value,
                ArtifactStatus.CONTAMINATED.value,
            ):
                bad_keys.add(k)

        # 3. decide actions
        raw_actions: Dict[Key, RecoveryAction] = {}
        for k in bad_keys:
            a = store.get_version(k[0], k[1], k[2])
            if a is None:
                continue
            if _task_kind(a) == "llm":
                raw_actions[k] = RecoveryAction.REPROMPT
            else:
                raw_actions[k] = RecoveryAction.TOOL_RECOMPUTE

        # 3b. Filter: this is the "selective" part.
        #   - If `target_ref` is itself VALID, the plan is empty.
        #   - If `target_ref` is bad, recover every bad node in its
        #     upstream closure.
        target_art = store.get_version(
            target_ref.run_id, target_ref.artifact_id, target_ref.version,
        )
        target_is_bad = (
            target_art is not None
            and target_art.validation_status in (
                ArtifactStatus.INVALIDATED.value,
                ArtifactStatus.CONTAMINATED.value,
            )
        )

        actions: Dict[Key, RecoveryAction] = {}
        if target_is_bad:
            ancestor_keys = {_key(r) for r in graph.get_ancestors(target_ref)}
            for k, act in raw_actions.items():
                if k in ancestor_keys or k == _key(target_ref):
                    actions[k] = act

        # 4. depth-of-recovery
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

        # 5. order by depth ASC
        ordered = sorted(actions.keys(), key=lambda k: depth[k])

        # 6. build steps
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

        # 7. reused_refs = closure \ bad_keys \ target
        reused_keys = (closure - bad_keys) - {_key(target_ref)}
        reused_refs = [
            ArtifactRef(run_id=k[0], artifact_id=k[1], version=k[2])
            for k in sorted(reused_keys, key=lambda k: (k[1], k[2]))
        ]

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
                f"{artifact.artifact_id} v{artifact.version} is "
                f"{artifact.validation_status}; deterministic tool, recompute from upstream"
            )
        if action == RecoveryAction.REPROMPT:
            return (
                f"{artifact.artifact_id} v{artifact.version} is "
                f"{artifact.validation_status}; LLM artifact, re-run with recovered inputs"
            )
        return ""
