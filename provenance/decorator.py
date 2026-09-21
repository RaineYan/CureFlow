"""
@track_provenance decorator — Day 3.

The Day 3 review's hard requirement:

    Refs must come from the ACTUAL input Artifact object, not from a
    hard-coded version, not from a string id, not from "v1" assumption.

If a caller passes an Artifact whose version is 2, the produced metric
records input_artifact_ids = [Ref(..., version=2)]. If a caller passes
v1, it records v1. The decorator does NOT know or care which version
"should" be used — it only records what was actually consumed.

Usage:
    @track_provenance(
        output_artifact_id="metric_revenue_growth",
        artifact_type=ArtifactType.METRIC,
    )
    def compute_revenue_growth(
        revenue_2025: Artifact,
        revenue_2024: Artifact,
        *,
        ctx: RunContext,
    ):
        return (revenue_2025.value - revenue_2024.value) / revenue_2024.value

Call site:
    rev25 = store.get(run_id, "field_revenue_2025")
    rev24 = store.get(run_id, "field_revenue_2024")
    ctx = RunContext(run_id=run_id, store=store)
    metric = compute_revenue_growth(rev25, rev24, ctx=ctx)

What the decorator does, in order:
  1. Collect Artifact instances from positional + keyword args.
  2. Reject if any input's run_id != ctx.run_id (no cross-run tools).
  3. Build ArtifactRef(run_id, artifact_id, version) for each input.
  4. Time the wrapped function.
  5. If the function raises, NO Artifact is saved. The exception propagates.
  6. next_version = (max existing version of output_artifact_id in this
     run) + 1, or 1 if the artifact has never been saved in this run.
     v1 is never overwritten.
  7. Build an Artifact with:
        value             = function return value
        input_artifact_ids = exact refs from step 3
        execution_cost    = {tokens:0, monetary_usd:0, latency_ms:measured,
                              llm_calls:0}
        meta              = {execution_unit, task_kind: "tool",
                              producer_task: ...}
  8. store.save(...) and return the new Artifact.

Out of POC scope (do NOT add):
  - LLM tasks / async / streaming
  - nested Artifact or List[Artifact] inputs
  - cross-run tools
  - automatic natural-language lineage inference
"""
from __future__ import annotations

import functools
import inspect
import time
from dataclasses import dataclass
from typing import Any, Callable, List, Optional

from artifacts import (
    Artifact,
    ArtifactRef,
    ArtifactStore,
    ArtifactType,
    ExecutionCost,
)


# =====================================================================
# RunContext
# =====================================================================

@dataclass
class RunContext:
    """The two things every decorated tool needs to know: which run, which store."""
    run_id: str
    store: ArtifactStore


# =====================================================================
# Decorator
# =====================================================================

def track_provenance(
    output_artifact_id: str,
    artifact_type: ArtifactType,
    producer_task: Optional[str] = None,
) -> Callable:
    """Decorator factory. See module docstring for the full contract.

    The wrapped function MUST accept a keyword-only `ctx: RunContext` arg.
    All other Artifact-typed args (positional or keyword) are auto-collected
    as inputs.
    """

    def decorator(func: Callable) -> Callable:
        sig = inspect.signature(func)
        if "ctx" not in sig.parameters:
            raise TypeError(
                f"@track_provenance requires the wrapped function to declare "
                f"a `ctx: RunContext` parameter. Function {func.__name__!r} "
                f"has params: {list(sig.parameters)}"
            )

        effective_producer_task = producer_task or func.__name__

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Artifact:
            # 1. resolve ctx (must be keyword per the decorator's contract)
            if "ctx" not in kwargs:
                raise TypeError(
                    f"{func.__name__}() missing required keyword argument: 'ctx'"
                )
            ctx: RunContext = kwargs["ctx"]
            run_id: str = ctx.run_id
            store: ArtifactStore = ctx.store

            # 2. collect Artifact inputs from args + kwargs (skip ctx itself)
            input_artifacts: List[Artifact] = []
            for value in args:
                if isinstance(value, Artifact):
                    input_artifacts.append(value)
            for key, value in kwargs.items():
                if key == "ctx":
                    continue
                if isinstance(value, Artifact):
                    input_artifacts.append(value)

            # 3. cross-run rejection
            for a in input_artifacts:
                if a.run_id != run_id:
                    raise ValueError(
                        f"Cross-run input not allowed: got run_id={a.run_id!r} "
                        f"from {a.artifact_id}@v{a.version}, expected "
                        f"run_id={run_id!r}"
                    )

            # 4. build exact refs (the Day 3 invariant)
            input_refs: List[ArtifactRef] = [
                ArtifactRef(
                    run_id=a.run_id,
                    artifact_id=a.artifact_id,
                    version=a.version,
                )
                for a in input_artifacts
            ]

            # 5. time + execute (NO save on exception)
            start = time.perf_counter()
            value = func(*args, **kwargs)        # may raise; nothing saved
            # Day 4 fix: latency_ms is float, no int() truncation for sub-ms work.
            elapsed_ms = (time.perf_counter() - start) * 1000.0

            # 6. determine next version — never overwrite, never collide
            existing_versions = store.list_versions(run_id, output_artifact_id)
            next_v = (max(existing_versions) + 1) if existing_versions else 1

            # 7. build the output artifact
            artifact = Artifact(
                artifact_id=output_artifact_id,
                artifact_type=artifact_type,
                producer_task=effective_producer_task,
                run_id=run_id,
                version=next_v,
                value=value,
                input_artifact_ids=input_refs,
                execution_cost=ExecutionCost(
                    tokens=0,
                    monetary_usd=0.0,
                    latency_ms=elapsed_ms,
                    llm_calls=0,
                ),
                meta={
                    "execution_unit": effective_producer_task,
                    "task_kind": "tool",
                },
            )
            return store.save(artifact)

        wrapper.__track_provenance__ = {  # type: ignore[attr-defined]
            "output_artifact_id": output_artifact_id,
            "artifact_type": artifact_type,
            "producer_task": effective_producer_task,
        }
        return wrapper

    return decorator


def tracked(func: Callable) -> bool:
    """Return True if `func` was wrapped with @track_provenance."""
    return hasattr(func, "__track_provenance__")
