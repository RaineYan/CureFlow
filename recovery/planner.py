"""
SelectiveRecoveryPlanner — Day 14 v2 thin adapter.

The v1 module (Day 8) implemented a hard-coded SELECTIVE-only
planner with the FULL/COARSE branches living elsewhere.  In v2
the planning logic lives behind a `Strategy` Protocol and a
process-local `StrategyRegistry`.  This module is now just the
backward-compat shim that:

  1. Imports the built-in strategies (populating the registry).
  2. Exposes `SelectiveRecoveryPlanner(...)` as a function that
     builds a `RecoveryContext` and calls
     `REGISTRY.get("SELECTIVE_RECOVERY").plan(cr, ctx)`.
  3. Exposes `build_recovery_plan(strategy_name, ...)` as the
     new public entry point for picking a strategy explicitly.

The shape and behaviour of the returned `RecoveryPlan` is
unchanged from v1, so every test that built a plan with
`SelectiveRecoveryPlanner(...)` keeps working.
"""
from __future__ import annotations

from contamination import ContaminationResult
from llm import LLMClient
from provenance.decorator import RunContext
from provenance.graph import ArtifactGraph

from artifacts import ArtifactRef, ArtifactStore

from .models import RecoveryPlan
from .strategy import REGISTRY, RecoveryContext
# Importing the strategies package is the side-effect that fills
# the global registry with the 3 built-in strategies.  Without
# this, the very first call to REGISTRY.get("SELECTIVE_RECOVERY")
# would raise KeyError.  We swallow the circular-import risk by
# doing the import at the bottom of the file.
from . import strategies as _strategies  # noqa: F401


def _build_context(
    graph: ArtifactGraph,
    store: ArtifactStore,
    run_id: str,
    target_ref: ArtifactRef,
    llm_client: LLMClient = None,
) -> RecoveryContext:
    """Construct a RecoveryContext.

    The `llm_client` argument is optional because some
    strategy.planning paths (COARSE, FULL) do not need it; only
    SELECTIVE-reprompt actually invokes the LLM at execution
    time, and the executor will fill `ctx.llm_client` from its
    own `__init__` before dispatching.
    """
    return RecoveryContext(
        run_id=run_id,
        target_ref=target_ref,
        graph=graph,
        store=store,
        llm_client=llm_client,
        run_ctx=RunContext(run_id=run_id, store=store),
        # `plan` and `replacement` are populated by the executor;
        # here we just allocate the structure so the strategy's
        # `plan` method has somewhere to put its result.
        plan=None,  # type: ignore[arg-type]
        replacement={},
        executed=[],
    )


def build_recovery_plan(
    strategy_name: str,
    graph: ArtifactGraph,
    store: ArtifactStore,
    run_id: str,
    target_ref: ArtifactRef,
    contamination: ContaminationResult,
    llm_client: LLMClient = None,
) -> RecoveryPlan:
    """Public v2 entry point: build a plan via the named strategy.

    Example:
        from recovery.planner import build_recovery_plan
        plan = build_recovery_plan(
            "SELECTIVE_RECOVERY", g, store, run_id, target, cr,
        )

    This is the function a 3rd-party strategy author would call
    after registering their own strategy.  The legacy
    `SelectiveRecoveryPlanner(...)` is a thin wrapper around it.
    """
    strategy = REGISTRY.get(strategy_name)
    ctx = _build_context(graph, store, run_id, target_ref, llm_client)
    plan = strategy.plan(contamination, ctx)
    return plan


def SelectiveRecoveryPlanner(
    graph: ArtifactGraph,
    store: ArtifactStore,
    run_id: str,
    target_ref: ArtifactRef,
    contamination: ContaminationResult,
) -> RecoveryPlan:
    """Backward-compat thin adapter to the SELECTIVE_RECOVERY strategy.

    This is the v1 public function.  Existing call sites (the
    Day-8 / Day-10 / Day-12 tests, plus `_exp02_runner`) keep
    working without modification.  Internally it just routes
    to the registered SELECTIVE_RECOVERY strategy.
    """
    return build_recovery_plan(
        "SELECTIVE_RECOVERY",
        graph, store, run_id, target_ref, contamination,
    )


__all__ = [
    "build_recovery_plan",
    "SelectiveRecoveryPlanner",
]
