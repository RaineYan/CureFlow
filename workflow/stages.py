"""
Workflow stages — one function per pipeline stage.

Each stage function:
    1. reads the artifacts it declared as `inputs` from the
       `WorkflowContext`,
    2. calls the existing Day-2/Day-5/Day-6 task primitives
       (`extract_fields`, `compute_*`, `generate_*_claim`,
       `generate_*_section`, `merge_report`),
    3. writes the produced artifacts onto the context under the
       attribute named in its `outputs` declaration,
    4. accumulates any LLM/tool cost onto the context totals.

The `BASELINE_TABLE` declares the 5 stages that mirror what
`run_baseline_workflow` did on Day 6.  Adding a 6th stage is a
one-line change to this table plus a stage function — no edits to
`runner.py` are required.
"""
from __future__ import annotations

from typing import Any, Dict, List

from provenance.decorator import RunContext

from .claim_tasks import (
    generate_financial_claim,
    generate_financial_section,
    generate_operation_claim,
    generate_operation_section,
    generate_user_claim,
    generate_user_section,
    merge_report,
)
from .scheduler import (
    Stage,
    WorkflowContext,
    accumulate_cost,
)
from .tasks import (
    compute_conversion_rate,
    compute_cost_ratio,
    compute_order_growth,
    compute_profit_margin,
    compute_revenue_growth,
    compute_user_growth,
    extract_fields,
)


# =====================================================================
# Stage 1 — extract_fields
# =====================================================================

def stage_extract(ctx: WorkflowContext) -> WorkflowContext:
    """Read CSV, persist raw_data + 10 FieldArtifacts.  Populates
    `ctx.raw_data` and `ctx.fields`."""
    ctx.fields = extract_fields(ctx.csv_path, ctx.run_id, ctx.store)
    raw = ctx.store.get(ctx.run_id, "raw_data")
    if raw is None:
        raise RuntimeError(
            f"extract_fields did not persist raw_data for run_id={ctx.run_id!r}"
        )
    ctx.raw_data = raw
    return ctx


def _validate_extract(ctx: WorkflowContext) -> None:
    """Defensive post-execute check: 10 FieldArtifacts + raw_data present."""
    if ctx.raw_data is None:
        raise AssertionError("extract stage produced no raw_data")
    expected_field_ids = {
        f"field_{col}_{yr}" for col in ("revenue", "cost", "users", "orders", "visits")
        for yr in (2024, 2025)
    }
    missing = expected_field_ids - set(ctx.fields.keys())
    if missing:
        raise AssertionError(
            f"extract stage missing FieldArtifacts: {sorted(missing)}"
        )


stage_extract_spec = Stage(
    name="extract",
    inputs=["csv"],
    outputs=["raw_data", "fields"],
    execute=stage_extract,
    validators=[_validate_extract],
    retries=0,
    on_failure="abort",
)


# =====================================================================
# Stage 2 — compute_metrics (6 deterministic metrics)
# =====================================================================

def stage_metrics(ctx: WorkflowContext) -> WorkflowContext:
    """Compute the 6 Day-2 metric artifacts and store them on the
    context under `ctx.metrics`."""
    store = ctx.store
    rid = ctx.run_id
    metrics: Dict[str, Any] = {
        "revenue_growth":  compute_revenue_growth(rid, store),
        "profit_margin":   compute_profit_margin(rid, store),
        "cost_ratio":      compute_cost_ratio(rid, store),
        "user_growth":     compute_user_growth(rid, store),
        "conversion_rate": compute_conversion_rate(rid, store),
        "order_growth":    compute_order_growth(rid, store),
    }
    ctx.metrics = metrics
    return ctx


def _validate_metrics(ctx: WorkflowContext) -> None:
    expected = {
        "revenue_growth", "profit_margin", "cost_ratio",
        "user_growth", "conversion_rate", "order_growth",
    }
    missing = expected - set(ctx.metrics.keys())
    if missing:
        raise AssertionError(
            f"metrics stage missing artifacts: {sorted(missing)}"
        )


stage_metrics_spec = Stage(
    name="metrics",
    inputs=["fields"],
    outputs=["metrics"],
    execute=stage_metrics,
    validators=[_validate_metrics],
    retries=0,
    on_failure="abort",
)


# =====================================================================
# Stage 3 — generate_claims (3 LLM-backed ClaimArtifacts)
# =====================================================================

def stage_claims(ctx: WorkflowContext) -> WorkflowContext:
    """Invoke the 3 LLM claim tasks.  Populates `ctx.claims`.

    The FakeLLMClient (used by tests) is offline; the real provider
    client (used by live-gate / day-14 pilot) is selected by the
    caller at context construction time.
    """
    run_ctx = RunContext(run_id=ctx.run_id, store=ctx.store)
    llm = ctx.llm_client
    m = ctx.metrics

    fc = generate_financial_claim(
        m["revenue_growth"], m["profit_margin"], m["cost_ratio"],
        ctx=run_ctx, llm_client=llm,
    )
    uc = generate_user_claim(
        m["user_growth"], ctx=run_ctx, llm_client=llm,
    )
    oc = generate_operation_claim(
        m["conversion_rate"], m["order_growth"],
        ctx=run_ctx, llm_client=llm,
    )
    for a in (fc, uc, oc):
        accumulate_cost(ctx, a)

    ctx.claims = {"financial": fc, "user": uc, "operation": oc}
    return ctx


def _validate_claims(ctx: WorkflowContext) -> None:
    for k in ("financial", "user", "operation"):
        if k not in ctx.claims:
            raise AssertionError(f"claims stage missing {k!r} claim")


stage_claims_spec = Stage(
    name="claims",
    inputs=["metrics"],
    outputs=["claims"],
    execute=stage_claims,
    validators=[_validate_claims],
    # Claims hit the LLM — the live Qwen test-A path can fail on
    # transient infra (rate-limit / 504).  Allow one retry so a single
    # flake doesn't abort the whole pipeline; the cost of retrying a
    # 3-call stage is bounded.
    retries=1,
    on_failure="abort",
)


# =====================================================================
# Stage 4 — generate_sections (3 LLM-backed Report-Section ClaimArtifacts)
# =====================================================================

def stage_sections(ctx: WorkflowContext) -> WorkflowContext:
    """Invoke the 3 LLM report-section tasks.  Populates
    `ctx.sections`."""
    run_ctx = RunContext(run_id=ctx.run_id, store=ctx.store)
    llm = ctx.llm_client
    c = ctx.claims

    fs = generate_financial_section(c["financial"], ctx=run_ctx, llm_client=llm)
    us = generate_user_section(c["user"], ctx=run_ctx, llm_client=llm)
    os_ = generate_operation_section(c["operation"], ctx=run_ctx, llm_client=llm)
    for a in (fs, us, os_):
        accumulate_cost(ctx, a)

    ctx.sections = {"financial": fs, "user": us, "operation": os_}
    return ctx


def _validate_sections(ctx: WorkflowContext) -> None:
    for k in ("financial", "user", "operation"):
        if k not in ctx.sections:
            raise AssertionError(f"sections stage missing {k!r} section")


stage_sections_spec = Stage(
    name="sections",
    inputs=["claims"],
    outputs=["sections"],
    execute=stage_sections,
    validators=[_validate_sections],
    retries=1,
    on_failure="abort",
)


# =====================================================================
# Stage 5 — merge_report (deterministic Tool, no LLM)
# =====================================================================

def stage_merge(ctx: WorkflowContext) -> WorkflowContext:
    """Concatenate the 3 section artifacts.  Populates
    `ctx.merged_report` and accrues the tool's measured latency."""
    run_ctx = RunContext(run_id=ctx.run_id, store=ctx.store)
    s = ctx.sections
    merged = merge_report(
        s["financial"], s["user"], s["operation"],
        ctx=run_ctx,
    )
    accumulate_cost(ctx, merged)
    ctx.merged_report = merged
    return ctx


def _validate_merge(ctx: WorkflowContext) -> None:
    if ctx.merged_report is None:
        raise AssertionError("merge stage produced no merged_report")
    if ctx.merged_report.meta.get("task_kind") != "tool":
        raise AssertionError(
            "merged_report must be a deterministic tool (task_kind='tool')"
        )


stage_merge_spec = Stage(
    name="merge",
    inputs=["sections"],
    outputs=["merged_report"],
    execute=stage_merge,
    validators=[_validate_merge],
    retries=0,
    on_failure="abort",
)


# =====================================================================
# Declarative baseline table — mirrors Day-6 run_baseline_workflow
# =====================================================================
#
# Order matters.  Each stage's `inputs` must be produced by some
# earlier stage (or be in INTRINSIC_INPUTS = {"csv"}).  Each stage's
# `outputs` must be a key in OUTPUT_TO_ATTR.  Scheduler.validate()
# enforces both.
#
# To add a 6th stage:
#   1. write `def stage_my_new_thing(ctx): ...`
#   2. append a Stage(...) to BASELINE_TABLE
# That's it — no edits to runner.py.

BASELINE_TABLE: List[Stage] = [
    stage_extract_spec,
    stage_metrics_spec,
    stage_claims_spec,
    stage_sections_spec,
    stage_merge_spec,
]
