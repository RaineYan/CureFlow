"""
Workflow scheduler — data-driven 5-stage orchestration.

Replaces the hard-coded `if stage_index == 0: ... elif stage_index == 1: ...`
control flow in the Day-6 `run_baseline_workflow` with a declarative table
of `Stage` entries. Adding a new stage is a 1-line change to the table
plus writing the stage function — no edits to runner.py required.

Design:

    Stage               — Pydantic v2 model describing a unit of work.
    WorkflowContext     — dataclass passed between stages; each stage reads
                          its declared inputs and writes its declared
                          outputs onto the same context.
    Scheduler           — validates the table, then walks it in order
                          handling retries and on_failure policies.

This module does not change any artifact, validation, or LLM behaviour —
it is a pure scheduling layer on top of the existing task primitives
(`extract_fields`, `compute_*`, `generate_*_claim`, `generate_*_section`,
`merge_report`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Literal, Optional, Set

from pydantic import BaseModel, ConfigDict, Field, field_validator


# =====================================================================
# Stage status (per-stage execution record)
# =====================================================================

class StageStatus(str, Enum):
    """Per-stage execution status. The string value is what the scheduler
    records into `WorkflowContext.stage_status[name]`, so it survives a
    debug print of the context."""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    SKIPPED = "skipped"
    FAILED = "failed"


# =====================================================================
# WorkflowContext — mutable state passed through the stage pipeline
# =====================================================================
#
# Carries the same per-run state that `run_baseline_workflow` used to
# hold in local variables: csv_path / run_id / store / llm_client, plus
# the artifact dicts for fields / metrics / claims / sections and the
# terminal merged_report.  Each stage mutates the context in place
# (or returns a new one — the scheduler does not care which).
#
# Intrinsic context attributes that are NOT produced by any stage:
#     csv_path, run_id, store, llm_client
# These are available to every stage from the moment the context is
# constructed; the scheduler treats them as always-satisfied inputs.

@dataclass
class WorkflowContext:
    # ---- intrinsic inputs (constructor-only) ----
    csv_path: str
    run_id: str
    store: Any               # ArtifactStore; Any to avoid circular import
    llm_client: Any          # LLMClient; Any to avoid circular import

    # ---- per-stage outputs (populated as stages run) ----
    raw_data: Any = None     # Artifact | None
    fields: Dict[str, Any] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)
    claims: Dict[str, Any] = field(default_factory=dict)
    sections: Dict[str, Any] = field(default_factory=dict)
    merged_report: Any = None  # Artifact | None

    # ---- execution bookkeeping ----
    stage_status: Dict[str, str] = field(default_factory=dict)
    stage_attempts: Dict[str, int] = field(default_factory=dict)
    stage_history: List[str] = field(default_factory=list)
    error: Optional[BaseException] = None

    # ---- LLM-cost accumulator (mirrors WorkflowResult totals) ----
    total_llm_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    total_latency_ms: float = 0.0


# Stage output attr names -> context attribute they should land on.
# Stages themselves own the mutation; the scheduler only uses these
# names to verify the table is well-formed.
OUTPUT_TO_ATTR: Dict[str, str] = {
    "raw_data":     "raw_data",
    "fields":       "fields",
    "metrics":      "metrics",
    "claims":       "claims",
    "sections":     "sections",
    "merged_report": "merged_report",
}

# Inputs that are satisfied by intrinsic context attributes (no
# preceding stage required).  `csv` is treated as a logical input name
# to keep the table declarative; the stage reads ctx.csv_path directly.
INTRINSIC_INPUTS: frozenset[str] = frozenset({"csv"})


# =====================================================================
# Stage — Pydantic v2 model
# =====================================================================

# Type alias for the stage function signature.  The signature is
# deliberately permissive: (Context) -> Context.  A stage may either
# mutate-and-return-self or return a new Context — both are accepted.
StageFn = Callable[[WorkflowContext], WorkflowContext]
ValidatorFn = Callable[[WorkflowContext], None]


class Stage(BaseModel):
    """Declarative description of one workflow stage.

    Attributes:
        name:       Unique stage name.  Used as the key in
                    `WorkflowContext.stage_status` and stage_history.
        inputs:     Logical names of upstream artifacts this stage
                    reads from the context.  Used by the scheduler to
                    validate the table — actual artifact lookup happens
                    inside the stage function.
        outputs:    Logical names this stage produces.  Used by the
                    scheduler to validate the table.
        execute:    The function that does the work.  Must accept a
                    `WorkflowContext` and return one (the same instance
                    is fine — mutation is allowed).
        validators: Optional list of post-execute validation callables.
                    Each receives the context; if any raises, the stage
                    is treated as failed.
        retries:    Number of retry attempts after the first failure
                    (0 = no retry, 1 = one retry, ...).  A stage whose
                    first attempt succeeds does not consume any retries.
        on_failure: Policy applied when retries are exhausted (or when
                    retries == 0 and the first attempt fails):
                      "abort"    — re-raise the exception, stop pipeline.
                      "skip"     — mark stage SKIPPED, continue.
                      "fallback" — call `fallback(ctx)` and treat its
                                   return as the stage's normal output.
        fallback:   Callable used only when on_failure == "fallback".
                    Receives the failing context; returns the context
                    to be used for downstream stages.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    inputs: List[str] = Field(default_factory=list)
    outputs: List[str] = Field(default_factory=list)
    execute: StageFn
    validators: List[ValidatorFn] = Field(default_factory=list)
    retries: int = 0
    on_failure: Literal["abort", "skip", "fallback"] = "abort"
    fallback: Optional[StageFn] = None

    @field_validator("name")
    @classmethod
    def _name_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Stage.name must be a non-empty string")
        return v

    @field_validator("retries")
    @classmethod
    def _retries_nonneg(cls, v: int) -> int:
        if v < 0:
            raise ValueError("Stage.retries must be >= 0")
        return v

    def model_post_init(self, __context: Any) -> None:
        # Pydantic v2 hook: enforce on_failure/fallback invariant.
        if self.on_failure == "fallback" and self.fallback is None:
            raise ValueError(
                f"Stage {self.name!r}: on_failure='fallback' requires "
                "a non-None `fallback` callable"
            )


# =====================================================================
# Scheduler
# =====================================================================

class TableValidationError(ValueError):
    """Raised when a stage table is malformed (missing inputs, cycles,
    duplicate stage names, or unrecognised input/output names)."""


class StageExecutionError(RuntimeError):
    """Wraps the original exception with the failing stage name."""
    def __init__(self, stage_name: str, attempts: int, original: BaseException):
        super().__init__(
            f"Stage {stage_name!r} failed after {attempts} attempt(s): "
            f"{type(original).__name__}: {original}"
        )
        self.stage_name = stage_name
        self.attempts = attempts
        self.original = original


class Scheduler:
    """Executes a list of `Stage` entries in declared order.

    Typical usage:
        table = [stage_extract, stage_metrics, stage_claims,
                 stage_sections, stage_merge]
        ctx = WorkflowContext(csv_path=..., run_id=..., store=...,
                              llm_client=...)
        Scheduler().run(table, ctx)
        result = ctx_to_workflow_result(ctx)   # in runner.py
    """

    # ---- public ----

    def validate(self, table: List[Stage]) -> None:
        """Static check on the table before any stage runs.

        Catches:
            * duplicate stage names
            * unknown input names (not intrinsic, not produced by any
              earlier stage)
            * unknown output names (not in OUTPUT_TO_ATTR)
            * undeclared inputs (a stage's `inputs` references a name
              that no earlier stage produces)
        """
        if not table:
            raise TableValidationError("Stage table is empty")

        seen_names: Set[str] = set()
        produced: Set[str] = set(INTRINSIC_INPUTS)

        for idx, stage in enumerate(table):
            if stage.name in seen_names:
                raise TableValidationError(
                    f"Duplicate stage name {stage.name!r} at index {idx}"
                )
            seen_names.add(stage.name)

            for inp in stage.inputs:
                if inp in INTRINSIC_INPUTS:
                    continue
                if inp not in produced:
                    raise TableValidationError(
                        f"Stage {stage.name!r} (index {idx}) declares "
                        f"input {inp!r} but no earlier stage produces it. "
                        f"Produced so far: {sorted(produced)}"
                    )

            for out in stage.outputs:
                if out not in OUTPUT_TO_ATTR:
                    raise TableValidationError(
                        f"Stage {stage.name!r} (index {idx}) declares "
                        f"output {out!r} which is not a known context "
                        f"attribute. Known: {sorted(OUTPUT_TO_ATTR)}"
                    )
                produced.add(out)

    def run(self, table: List[Stage], ctx: WorkflowContext) -> WorkflowContext:
        """Execute every stage in order.  Returns the same context (mutated).

        Behaviour:
            * Initial stage_status[name] = PENDING for all stages.
            * Each stage is run with up to (1 + retries) attempts.
            * After every successful execute, each validator is run in
              declared order.  Validator failure is treated like an
              execute failure.
            * On terminal failure, on_failure policy is applied:
                "abort"    — re-raise as StageExecutionError
                "skip"     — mark SKIPPED, continue
                "fallback" — call stage.fallback(ctx) and continue
                             (fallback exception itself aborts)
        """
        self.validate(table)

        # Initialise per-stage bookkeeping.
        for stage in table:
            ctx.stage_status[stage.name] = StageStatus.PENDING.value
            ctx.stage_attempts[stage.name] = 0

        for stage in table:
            ctx.stage_status[stage.name] = StageStatus.RUNNING.value
            ctx.stage_history.append(stage.name)

            attempts_allowed = 1 + max(0, stage.retries)
            last_exc: Optional[Exception] = None
            success = False

            for attempt_index in range(attempts_allowed):
                ctx.stage_attempts[stage.name] = attempt_index + 1
                try:
                    candidate_ctx = stage.execute(ctx)
                    if not isinstance(candidate_ctx, WorkflowContext):
                        raise TypeError(
                            f"Stage {stage.name!r} returned "
                            f"{type(candidate_ctx).__name__}, expected WorkflowContext"
                        )
                    ctx = candidate_ctx
                    for v in stage.validators:
                        v(ctx)
                    success = True
                    last_exc = None
                    break
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    # retry on the next loop iteration if budget remains

            if success:
                ctx.error = None
                ctx.stage_status[stage.name] = StageStatus.SUCCESS.value
                continue

            # Terminal failure — apply on_failure policy.
            assert last_exc is not None
            ctx.error = last_exc
            if stage.on_failure == "abort":
                ctx.stage_status[stage.name] = StageStatus.FAILED.value
                raise StageExecutionError(
                    stage.name, ctx.stage_attempts[stage.name], last_exc,
                ) from last_exc
            if stage.on_failure == "skip":
                ctx.stage_status[stage.name] = StageStatus.SKIPPED.value
                continue
            if stage.on_failure == "fallback":
                # stage.fallback is guaranteed non-None by model_post_init
                try:
                    candidate_ctx = stage.fallback(ctx)  # type: ignore[misc]
                    if not isinstance(candidate_ctx, WorkflowContext):
                        raise TypeError(
                            f"Fallback for stage {stage.name!r} returned "
                            f"{type(candidate_ctx).__name__}, expected WorkflowContext"
                        )
                    ctx = candidate_ctx
                    for v in stage.validators:
                        v(ctx)
                except Exception as fallback_exc:  # noqa: BLE001
                    ctx.error = fallback_exc
                    ctx.stage_status[stage.name] = StageStatus.FAILED.value
                    raise StageExecutionError(
                        stage.name,
                        ctx.stage_attempts[stage.name],
                        fallback_exc,
                    ) from fallback_exc
                ctx.error = None
                ctx.stage_status[stage.name] = StageStatus.SUCCESS.value
                continue
            # Defensive: literal type should prevent this.
            raise RuntimeError(
                f"Unknown on_failure policy: {stage.on_failure!r}"
            )

        return ctx


# =====================================================================
# Cost accumulator helper (used by stages that produce LLM artifacts)
# =====================================================================

def accumulate_cost(ctx: WorkflowContext, artifact: Any) -> None:
    """Add one artifact's ExecutionCost onto the context totals.

    Stages that produce LLM-backed artifacts (claims, sections, merged
    report) call this so the final `WorkflowResult` has the same
    total_* fields the legacy runner used to compute.
    """
    ec = getattr(artifact, "execution_cost", None)
    if ec is None:
        return
    ctx.total_llm_calls += getattr(ec, "llm_calls", 0) or 0
    ctx.total_input_tokens += getattr(ec, "input_tokens", 0) or 0
    ctx.total_output_tokens += getattr(ec, "output_tokens", 0) or 0
    ctx.total_tokens += getattr(ec, "tokens", 0) or 0
    ctx.total_latency_ms += float(getattr(ec, "latency_ms", 0.0) or 0.0)
