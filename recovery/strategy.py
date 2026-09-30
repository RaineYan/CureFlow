"""
Strategy market — Day 14 v2 sub-direction 2.

A recovery *strategy* is a named, pluggable alternative to the v1
hard-coded SELECTIVE/COARSE/FULL switch.  The original switch lived
inside `SelectiveRecoveryPlanner` and `RecoveryExecutor.execute` as
two coupled if/elif chains; the v2 market splits them into:

  - a `Strategy` protocol that owns the planning logic for ONE
    family of recovery decisions (`plan(cr, ctx)`), and
  - a `StrategyRegistry` global catalog mapping strategy name ->
    strategy instance, mutated by `@register_strategy(name)`.

Backward-compat: the old function `SelectiveRecoveryPlanner(...)`
and `RecoveryExecutor.execute(plan)` keep working unchanged.  The
executor internally consults the strategy registered under the name
held in `plan.strategy_name` (default: "SELECTIVE_RECOVERY"); the
planner adapter just calls the registered strategy's `plan` and
stamps the plan with the strategy name.

Rules of the v2 market:

  1. Strategy names are UPPER_SNAKE_CASE strings.  Three built-in
     names are reserved: "FULL_RERUN", "COARSE_CHECKPOINT",
     "SELECTIVE_RECOVERY".
  2. Every strategy exposes exactly one planning method
     `plan(contamination, ctx) -> RecoveryPlan`.  The plan is
     stamped with `strategy_name=self.name` by the registry.
  3. Executor dispatch goes through the registry:
     `strategy = REGISTRY.get(plan.strategy_name)`.  Strategies
     are responsible for per-step execution semantics: they
     dispatch each `RecoveryStep.action` through
     `strategy.dispatch_step(step, mapping, ctx)` and return an
     `ExecutedRecoveryStep`.  The base implementation in
     `BaseStrategy` covers the existing 3 RecoveryAction
     (REUSE / TOOL_RECOMPUTE / REPROMPT).
  4. The Protocol is duck-typed; a class only needs `name` and
     the two callables to be a strategy.  This is the smallest
     surface that still allows per-strategy execution overrides.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol, runtime_checkable

from contamination import ContaminationResult
from llm import LLMClient
from provenance.decorator import RunContext
from provenance.graph import ArtifactGraph

from artifacts import Artifact, ArtifactRef, ArtifactStore

from .models import (
    ExecutedRecoveryStep,
    RecoveryExecutionResult,
    RecoveryPlan,
    RecoveryStep,
)


# =====================================================================
# Context object passed into Strategy.plan / Strategy.dispatch_step.
# =====================================================================

@dataclass
class RecoveryContext:
    """All the state a strategy needs to plan and execute recovery.

    Built once per (run_id, target_ref) at the top of
    `RecoveryExecutor.execute` and threaded through strategy calls.
    Strategies must NOT reach into other modules directly when the
    same value is on this context — this is the boundary that
    keeps the strategy self-contained.
    """
    run_id: str
    target_ref: ArtifactRef
    graph: ArtifactGraph
    store: ArtifactStore
    llm_client: LLMClient
    run_ctx: RunContext
    plan: RecoveryPlan
    replacement: Dict[str, str] = field(default_factory=dict)
    executed: List[ExecutedRecoveryStep] = field(default_factory=list)
    # Optional pluggable hooks a strategy may want to call.
    extras: Dict[str, Any] = field(default_factory=dict)


# =====================================================================
# Strategy Protocol
# =====================================================================

@runtime_checkable
class Strategy(Protocol):
    """Minimal duck-typed contract for a recovery strategy.

    Required surface:
      - `name: str`                                  strategy identifier
      - `applicable(cr) -> bool`                     pre-filter for the planner
      - `plan(cr, ctx) -> RecoveryPlan`              build the plan
      - `dispatch_step(step, ctx) -> ExecutedRecoveryStep`
                                                     per-step execution

    The Protocol is structural; concrete strategies are free to
    override `dispatch_step` for custom actions.  Most strategies
    will inherit from `BaseStrategy` which implements the common
    REUSE / TOOL_RECOMPUTE / REPROMPT execution.
    """
    name: str

    def applicable(self, cr: ContaminationResult) -> bool: ...

    def plan(
        self,
        cr: ContaminationResult,
        ctx: RecoveryContext,
    ) -> RecoveryPlan: ...

    def dispatch_step(
        self,
        step: RecoveryStep,
        ctx: RecoveryContext,
    ) -> ExecutedRecoveryStep: ...


# =====================================================================
# Registry
# =====================================================================

class StrategyRegistry:
    """Process-local catalog of strategies, keyed by name.

    Lookup is case-insensitive on read but stored in the casing
    used at registration.  This keeps registered keys readable
    in error messages while still tolerating "selective_recovery"
    vs "SELECTIVE_RECOVERY" in caller code.
    """
    def __init__(self) -> None:
        self._strategies: Dict[str, Strategy] = {}

    def register(self, strategy: Strategy, *, name: Optional[str] = None) -> Strategy:
        if not isinstance(strategy, Strategy):
            raise TypeError(
                f"object {strategy!r} does not satisfy Strategy protocol "
                f"(missing one of: name / applicable / plan / dispatch_step)"
            )
        key = (name or strategy.name).upper()
        if not key:
            raise ValueError("strategy name must be a non-empty string")
        if key in self._strategies:
            raise ValueError(
                f"strategy {key!r} already registered; use replace=True to override"
            )
        self._strategies[key] = strategy
        return strategy

    def replace(self, strategy: Strategy, *, name: Optional[str] = None) -> Strategy:
        """Like register but allows overriding an existing entry.

        Used in tests where a strategy gets monkey-patched.  The
        executor and planner do NOT call this in production code.
        """
        if not isinstance(strategy, Strategy):
            raise TypeError(
                f"object {strategy!r} does not satisfy Strategy protocol"
            )
        key = (name or strategy.name).upper()
        self._strategies[key] = strategy
        return strategy

    def get(self, name: str) -> Strategy:
        key = name.upper()
        if key not in self._strategies:
            raise KeyError(
                f"strategy {name!r} not registered; "
                f"available: {sorted(self._strategies.keys())}"
            )
        return self._strategies[key]

    def has(self, name: str) -> bool:
        return name.upper() in self._strategies

    def names(self) -> List[str]:
        return sorted(self._strategies.keys())

    def clear(self) -> None:
        """Drop every registration.  Test-only helper."""
        self._strategies.clear()


# Single global registry shared by executor + planner.  Tests can
# swap entries via `REGISTRY.replace(...)` without losing the
# built-in three.
REGISTRY: StrategyRegistry = StrategyRegistry()


# =====================================================================
# Decorator
# =====================================================================

def register_strategy(
    name: str,
    *,
    registry: Optional[StrategyRegistry] = None,
    replace: bool = False,
) -> Callable[[type], type]:
    """Class decorator that registers a strategy under `name`.

    Usage:
        @register_strategy("SELECTIVE_RECOVERY")
        class SelectiveRecoveryStrategy(BaseStrategy):
            ...

    The decorator validates duck-typing at import time so a
    mis-registered strategy fails fast.
    """
    target = registry or REGISTRY

    def _decorator(cls: type) -> type:
        instance = cls()
        # If the class forgot to set `.name`, fall back to the decorator arg.
        if not getattr(instance, "name", None):
            instance.name = name
        if replace:
            target.replace(instance, name=name)
        else:
            target.register(instance, name=name)
        # Stash a back-reference so tests can introspect which
        # class produced a given registration.
        instance.__class__._registered_name = name  # type: ignore[attr-defined]
        return cls

    return _decorator


# =====================================================================
# BaseStrategy — common execution semantics for the 3 RecoveryAction
# =====================================================================
#
# Strategies that only differ in *which* steps to plan (e.g.
# FULL_RERUN plans all upstream; SELECTIVE plans only the bad
# ones) can inherit from BaseStrategy and just override `plan`.
# Strategies that need a custom action (e.g. MODEL_FALLBACK
# would add a new RecoveryAction value) override
# `dispatch_step` instead.
#
# The base dispatcher is intentionally a faithful re-implementation
# of the v1 RecoveryExecutor logic (REUSE / TOOL_RECOMPUTE /
# REPROMPT) so the per-strategy `execute` body can be a single
# loop that calls `self.dispatch_step(step, ctx)`.

class _BaseStrategyMixin:
    """Mixin that owns the v1 three-action execution semantics.

    Lives outside the registry so strategies can pick the parts
    they want.  Inheriting from this mixin plus the Protocol duck
    type is enough to satisfy the registry check.
    """
    name: str = ""

    # ----- default planning stub: must be overridden -----
    def applicable(self, cr: ContaminationResult) -> bool:
        # Default: every strategy is applicable to every contamination
        # result.  Strategies that should be filtered out (e.g. an
        # experimental one) override this.
        return True

    def plan(
        self,
        cr: ContaminationResult,
        ctx: RecoveryContext,
    ) -> RecoveryPlan:
        raise NotImplementedError(
            f"strategy {self.name!r} must override plan()"
        )

    # ----- execution dispatch (REUSE / TOOL_RECOMPUTE / REPROMPT) -----
    def dispatch_step(
        self,
        step: RecoveryStep,
        ctx: RecoveryContext,
    ) -> ExecutedRecoveryStep:
        from .models import RecoveryAction  # local import to avoid cycle
        if step.action == RecoveryAction.REUSE:
            return self._exec_reuse(step)
        if step.action == RecoveryAction.TOOL_RECOMPUTE:
            return self._exec_tool_recompute(step, ctx)
        if step.action == RecoveryAction.REPROMPT:
            return self._exec_reprompt(step, ctx)
        raise NotImplementedError(
            f"strategy {self.name!r}: action {step.action!r} not handled"
        )

    # ----- per-action runners (factored from v1 executor) -----
    @staticmethod
    def _exec_reuse(step: RecoveryStep) -> ExecutedRecoveryStep:
        from .models import RecoveryAction
        return ExecutedRecoveryStep(
            action=RecoveryAction.REUSE,
            replaced_ref=step.target_ref,
            produced_ref=None,
            execution_unit=step.execution_unit,
            input_refs=list(step.required_input_refs),
            tokens=0, input_tokens=0, output_tokens=0,
            llm_calls=0, latency_ms=0.0,
        )

    def _exec_tool_recompute(
        self,
        step: RecoveryStep,
        ctx: RecoveryContext,
    ) -> ExecutedRecoveryStep:
        import time
        from .models import RecoveryAction
        from artifacts import ArtifactType
        from workflow.claim_tasks import merge_report
        from workflow.recompute import (
            conv_recompute,
            mark_recovered,
            mcr_recompute,
            mog_recompute,
            mpm_recompute,
            mrg_recompute,
            mug_recompute,
            recompute_field_artifact_from_raw,
        )
        store = ctx.store
        a = store.get_version(
            step.target_ref.run_id,
            step.target_ref.artifact_id,
            step.target_ref.version,
        )
        assert a is not None, f"missing target {step.target_ref}"
        start = time.perf_counter()
        if a.artifact_type == ArtifactType.FIELD:
            raw = store.get(ctx.run_id, "raw_data")
            assert raw is not None
            from artifacts import ArtifactStatus
            if ctx.plan.strategy_name.upper() not in {"FULL_RERUN", "COARSE_CHECKPOINT"} and raw.validation_status not in {
                ArtifactStatus.VALID.value, ArtifactStatus.RECOVERED.value,
            }:
                raise ValueError(f"strategy: raw_data is {raw.validation_status}")
            new_art = recompute_field_artifact_from_raw(
                bad_field=a, raw_artifact=raw, ctx=ctx.run_ctx,
            )
        elif a.artifact_type == ArtifactType.METRIC:
            fn_map = {
                "metric_revenue_growth":   mrg_recompute,
                "metric_profit_margin":    mpm_recompute,
                "metric_cost_ratio":       mcr_recompute,
                "metric_conversion_rate":  conv_recompute,
                "metric_user_growth":      mug_recompute,
                "metric_order_growth":     mog_recompute,
            }
            fn = fn_map.get(a.artifact_id)
            assert fn is not None, f"no recompute fn for {a.artifact_id}"
            remapped = self._remapped(ctx, list(step.required_input_refs))
            kwargs = {self._arg_name(ref): art for ref, art in zip(step.required_input_refs, remapped)}
            kwargs["ctx"] = ctx.run_ctx
            new_art = fn(**kwargs)
        elif a.artifact_id == "report_merged":
            remapped = self._remapped(ctx, list(step.required_input_refs))
            new_art = merge_report(*remapped, ctx=ctx.run_ctx)
        else:
            raise NotImplementedError(
                f"strategy {self.name!r}: TOOL_RECOMPUTE not wired for {a.artifact_id}"
            )
        mark_recovered(store, new_art.run_id, new_art.artifact_id)
        elapsed = (time.perf_counter() - start) * 1000.0
        return ExecutedRecoveryStep(
            action=RecoveryAction.TOOL_RECOMPUTE,
            replaced_ref=step.target_ref,
            produced_ref=ArtifactRef(
                run_id=new_art.run_id,
                artifact_id=new_art.artifact_id,
                version=new_art.version,
            ),
            execution_unit=step.execution_unit,
            input_refs=list(step.required_input_refs),
            tokens=new_art.execution_cost.tokens,
            input_tokens=new_art.execution_cost.input_tokens,
            output_tokens=new_art.execution_cost.output_tokens,
            llm_calls=new_art.execution_cost.llm_calls,
            latency_ms=elapsed,
        )

    def _exec_reprompt(
        self,
        step: RecoveryStep,
        ctx: RecoveryContext,
    ) -> ExecutedRecoveryStep:
        import time
        from .models import RecoveryAction
        from workflow.claim_tasks import (
            generate_financial_claim,
            generate_financial_section,
        )
        from workflow.recompute import mark_recovered
        from workflow import claim_tasks  # for the user/op variants
        store = ctx.store
        a = store.get_version(
            step.target_ref.run_id,
            step.target_ref.artifact_id,
            step.target_ref.version,
        )
        assert a is not None
        remapped = self._remapped(ctx, list(step.required_input_refs))
        start = time.perf_counter()
        unit = step.execution_unit
        if unit == "generate_financial_claim":
            new_art = generate_financial_claim(
                *remapped, ctx=ctx.run_ctx, llm_client=ctx.llm_client,
            )
        elif unit == "generate_financial_section":
            new_art = generate_financial_section(
                *remapped, ctx=ctx.run_ctx, llm_client=ctx.llm_client,
            )
        elif unit == "generate_user_claim":
            new_art = claim_tasks.generate_user_claim(
                *remapped, ctx=ctx.run_ctx, llm_client=ctx.llm_client,
            )
        elif unit == "generate_operation_claim":
            new_art = claim_tasks.generate_operation_claim(
                *remapped, ctx=ctx.run_ctx, llm_client=ctx.llm_client,
            )
        elif unit == "generate_user_section":
            new_art = claim_tasks.generate_user_section(
                *remapped, ctx=ctx.run_ctx, llm_client=ctx.llm_client,
            )
        elif unit == "generate_operation_section":
            new_art = claim_tasks.generate_operation_section(
                *remapped, ctx=ctx.run_ctx, llm_client=ctx.llm_client,
            )
        else:
            raise NotImplementedError(
                f"strategy {self.name!r}: REPROMPT not wired for {unit!r}"
            )
        elapsed = (time.perf_counter() - start) * 1000.0
        mark_recovered(store, new_art.run_id, new_art.artifact_id)
        return ExecutedRecoveryStep(
            action=RecoveryAction.REPROMPT,
            replaced_ref=step.target_ref,
            produced_ref=ArtifactRef(
                run_id=new_art.run_id,
                artifact_id=new_art.artifact_id,
                version=new_art.version,
            ),
            execution_unit=unit,
            input_refs=list(step.required_input_refs),
            tokens=new_art.execution_cost.tokens,
            input_tokens=new_art.execution_cost.input_tokens,
            output_tokens=new_art.execution_cost.output_tokens,
            llm_calls=new_art.execution_cost.llm_calls,
            latency_ms=elapsed,
        )

    # ----- shared helpers -----
    @staticmethod
    def _remapped(
        ctx: RecoveryContext,
        refs: List[ArtifactRef],
    ) -> List[Artifact]:
        out: List[Artifact] = []
        for r in refs:
            target_ref = r
            if r.to_ref_string() in ctx.replacement:
                target_ref = ArtifactRef.from_ref_string(ctx.replacement[r.to_ref_string()])
            a = ctx.store.get_version(target_ref.run_id, target_ref.artifact_id, target_ref.version)
            if a is None:
                raise KeyError(
                    f"strategy: cannot fetch {target_ref.to_ref_string()}"
                )
            from artifacts import ArtifactStatus
            if ctx.plan.strategy_name.upper() not in {"FULL_RERUN", "COARSE_CHECKPOINT"} and a.validation_status not in {
                ArtifactStatus.VALID.value, ArtifactStatus.RECOVERED.value,
            }:
                raise ValueError(
                    f"strategy: input {target_ref.to_ref_string()} is {a.validation_status}"
                )
            out.append(a)
        return out

    @staticmethod
    def _arg_name(ref: ArtifactRef) -> str:
        s = ref.artifact_id
        assert s.startswith("field_"), f"unexpected metric input ref id {s!r}"
        return s[len("field_"):]
