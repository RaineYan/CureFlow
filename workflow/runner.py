"""
Minimal workflow runner — Day 6 (refactored to data-driven scheduler).

The public API is unchanged:

    result = run_baseline_workflow(
        csv_path="data/sample_company.csv",
        run_id="run_full",
        store=store,
        llm_client=default_workflow_llm(),
    )
    result.run_id, result.fields, result.metrics, result.claims,
    result.sections, result.merged_report, result.total_* are all
    populated exactly as in the pre-refactor Day-6 runner.

Internally, the 5-stage pipeline is now a declarative table in
`workflow.stages.BASELINE_TABLE`, executed by `workflow.scheduler.Scheduler`.
This is what makes "add a new stage" a 1-line change to the table
instead of an edit to this file.

WorkflowResult hands back the produced artifacts and a tiny cost
summary aggregated from each artifact's ExecutionCost. Numbers are
FakeLLMClient fixture values, NOT real competition data.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from artifacts import Artifact, ArtifactStore, ExecutionCost
from llm import LLMClient

from .claim_tasks import (
    generate_financial_claim,
    generate_financial_section,
    generate_operation_claim,
    generate_operation_section,
    generate_user_claim,
    generate_user_section,
    merge_report,
)
from .scheduler import WorkflowContext
from .stages import BASELINE_TABLE
from .tasks import (
    compute_conversion_rate,
    compute_cost_ratio,
    compute_order_growth,
    compute_profit_margin,
    compute_revenue_growth,
    compute_user_growth,
    extract_fields,
)


__all__ = ["WorkflowResult", "run_baseline_workflow"]


@dataclass
class WorkflowResult:
    run_id: str
    raw_data: Artifact
    fields: Dict[str, Artifact]
    metrics: Dict[str, Artifact]
    claims: Dict[str, Artifact]
    sections: Dict[str, Artifact]
    merged_report: Artifact

    # Aggregated cost across all LLM + tool execution units.
    # FakeLLMClient fixture values, NOT real competition data.
    total_llm_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    total_latency_ms: float = 0.0


def _accumulate(into: WorkflowResult, artifact: Artifact) -> None:
    ec: ExecutionCost = artifact.execution_cost
    into.total_llm_calls += ec.llm_calls
    into.total_input_tokens += ec.input_tokens
    into.total_output_tokens += ec.output_tokens
    into.total_tokens += ec.tokens
    into.total_latency_ms += ec.latency_ms


def _context_to_result(ctx: WorkflowContext) -> WorkflowResult:
    """Build the legacy WorkflowResult from a populated WorkflowContext.

    This is the only place that knows the dual shape (context attrs vs
    WorkflowResult fields). Adding a new artifact to the workflow means
    either extending WorkflowContext + WorkflowResult, or accepting that
    the new artifact lives on the context only.
    """
    if ctx.raw_data is None or ctx.merged_report is None:
        raise RuntimeError(
            "WorkflowContext was not fully populated; merged_report or "
            "raw_data is None.  Did all stages run successfully?"
        )
    result = WorkflowResult(
        run_id=ctx.run_id,
        raw_data=ctx.raw_data,
        fields=dict(ctx.fields),
        metrics=dict(ctx.metrics),
        claims=dict(ctx.claims),
        sections=dict(ctx.sections),
        merged_report=ctx.merged_report,
    )
    # Cost totals were accumulated onto the context by each stage's
    # accumulate_cost() call; copy them onto the result so the
    # WorkflowResult API contract is preserved.
    result.total_llm_calls = ctx.total_llm_calls
    result.total_input_tokens = ctx.total_input_tokens
    result.total_output_tokens = ctx.total_output_tokens
    result.total_tokens = ctx.total_tokens
    result.total_latency_ms = ctx.total_latency_ms
    return result


def run_baseline_workflow(
    csv_path: str,
    run_id: str,
    store: ArtifactStore,
    llm_client: LLMClient,
) -> WorkflowResult:
    """Run the full baseline workflow sequentially and return all produced artifacts.

    Backward-compatible wrapper around the data-driven scheduler:

        1. Build a WorkflowContext from the (csv_path, run_id, store,
           llm_client) arguments.
        2. Run BASELINE_TABLE on it via Scheduler.run().
        3. Materialize the legacy WorkflowResult.

    No stage logic lives in this function — the 5 stages are
    declared in `workflow.stages.BASELINE_TABLE` and executed by
    `workflow.scheduler.Scheduler`.
    """
    ctx = WorkflowContext(
        csv_path=csv_path,
        run_id=run_id,
        store=store,
        llm_client=llm_client,
    )
    # Local import to keep `workflow.runner` importable in isolation
    # (e.g. from tests that mock the scheduler).
    from .scheduler import Scheduler
    Scheduler().run(BASELINE_TABLE, ctx)
    return _context_to_result(ctx)
