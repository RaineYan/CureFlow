"""Workflow tasks: Day 2 Field extraction + 6 deterministic Metric tools,
Day 5 Financial Claim, Day 6 User/Operation Claims + 3 Sections + merge_report,
Day 6 Workflow Runner."""
from .tasks import (
    extract_fields,
    compute_revenue_growth,
    compute_profit_margin,
    compute_cost_ratio,
    compute_user_growth,
    compute_conversion_rate,
    compute_order_growth,
    recompute_field_from_raw,
)
from .claim_tasks import (
    ClaimOutput,
    build_claim_prompt,
    build_financial_claim_prompt,
    generate_financial_claim,
    generate_user_claim,
    generate_operation_claim,
    generate_financial_section,
    generate_user_section,
    generate_operation_section,
    merge_report,
)
from .runner import WorkflowResult, run_baseline_workflow

__all__ = [
    "extract_fields",
    "compute_revenue_growth",
    "compute_profit_margin",
    "compute_cost_ratio",
    "compute_user_growth",
    "compute_conversion_rate",
    "compute_order_growth",
    "recompute_field_from_raw",
    "ClaimOutput",
    "build_claim_prompt",
    "build_financial_claim_prompt",
    "generate_financial_claim",
    "generate_user_claim",
    "generate_operation_claim",
    "generate_financial_section",
    "generate_user_section",
    "generate_operation_section",
    "merge_report",
    "WorkflowResult",
    "run_baseline_workflow",
]
