"""Build a recovery plan bound to the current uploaded table.

This is deliberately separate from the frozen EXP-02 mock experiment.  It
does not claim that a repair has run; it explains which current-run analysis
artifacts become stale after a reviewed source correction and which can be
reused.
"""
from __future__ import annotations

from typing import Any


ALL_TOOL_KEYS = (
    "quality_audit",
    "distribution_profile",
    "rank_consistency",
    "relationship_screen",
    "group_comparison",
)

ISSUE_IMPACT = {
    "trim_whitespace": {"quality_audit", "group_comparison"},
    "missing_value": {"quality_audit", "distribution_profile", "relationship_screen", "group_comparison"},
    "formula_error": set(ALL_TOOL_KEYS),
    "score_out_of_range": {"distribution_profile", "relationship_screen", "group_comparison"},
    "non_positive_count": {"quality_audit", "rank_consistency"},
    "duplicate_row": {"quality_audit", "distribution_profile", "relationship_screen", "group_comparison"},
    "rank_out_of_range": {"rank_consistency"},
    "statistical_outlier": {"distribution_profile", "relationship_screen", "group_comparison"},
}


def build_current_recovery_plan(
    repair_plan: dict[str, Any],
    analysis_tools: dict[str, Any],
    *,
    filename: str,
    sheet: str | None,
    input_fingerprint: str,
) -> dict[str, Any]:
    """Return a conservative, current-input-bound invalidation plan."""
    issues = list(repair_plan.get("issues") or [])
    tool_entries = {
        item.get("key"): item
        for item in analysis_tools.get("executedTools", [])
        if item.get("key")
    }
    available_keys = [key for key in ALL_TOOL_KEYS if key in tool_entries]
    impacted: set[str] = set()
    unknown_issue = False
    for issue in issues:
        issue_type = issue.get("type")
        mapped = ISSUE_IMPACT.get(issue_type)
        if mapped is None:
            unknown_issue = True
        else:
            impacted.update(mapped)
    if unknown_issue:
        impacted.update(available_keys)
    impacted_keys = [key for key in available_keys if key in impacted]
    reusable_keys = [key for key in available_keys if key not in impacted]

    # Provider interpretation and report depend on every displayed local tool
    # result.  They are therefore regenerated whenever a source correction
    # invalidates at least one local analysis result.
    downstream_steps = 2 if impacted_keys else 0
    full_steps = len(available_keys) + 2
    selective_steps = len(impacted_keys) + downstream_steps
    reusable_steps = max(full_steps - selective_steps, 0)
    reduction = round((1 - selective_steps / full_steps) * 100, 2) if full_steps and selective_steps else 0.0
    safe_count = int((repair_plan.get("summary") or {}).get("safeFixCount", 0))
    review_count = int((repair_plan.get("summary") or {}).get("reviewCount", 0))
    if not issues:
        status = "no_recovery_needed"
        headline = "当前规则没有发现需要恢复的数据问题"
        next_action = "如需更深入分析，请指定目标字段、时间字段或分组字段。"
    elif review_count:
        status = "review_required"
        headline = f"本次文件有 {review_count} 项需要先人工核对"
        next_action = "先回到诊断工作台核对具体单元格；确认原值需要修改后，再生成修正版并重新分析。"
    else:
        status = "safe_fixes_available"
        headline = f"本次文件有 {safe_count} 项安全修正可以生成副本"
        next_action = "回到诊断工作台勾选安全修正，导出新副本后重新分析；原文件不会被覆盖。"

    def tool_view(key: str) -> dict[str, Any]:
        item = tool_entries[key]
        return {
            "key": key,
            "label": item.get("label", key),
            "evidenceRef": item.get("evidenceRef"),
        }

    return {
        "version": "current_input_recovery_v1",
        "source": {
            "filename": filename,
            "sheet": sheet,
            "inputFingerprint": input_fingerprint,
        },
        "status": status,
        "headline": headline,
        "nextAction": next_action,
        "summary": {
            "issueCount": len(issues),
            "safeFixCount": safe_count,
            "reviewCount": review_count,
            "impactedToolCount": len(impacted_keys),
            "reusableToolCount": len(reusable_keys),
            "fullRecomputeSteps": full_steps,
            "selectiveRecomputeSteps": selective_steps,
            "reusedSteps": reusable_steps,
            "estimatedStepReductionPct": reduction,
        },
        "issues": [
            {
                "id": item.get("id"),
                "type": item.get("type"),
                "title": item.get("title"),
                "severity": item.get("severity"),
                "location": item.get("location"),
                "autoApplicable": item.get("autoApplicable") is True,
                "reason": item.get("reason"),
            }
            for item in issues[:20]
        ],
        "impactedTools": [tool_view(key) for key in impacted_keys],
        "reusableTools": [tool_view(key) for key in reusable_keys],
        "downstream": [
            {"key": "provider_interpretation", "label": "AI 证据解读"},
            {"key": "report", "label": "报告生成"},
        ] if impacted_keys else [],
        "executionBoundary": {
            "planOnly": True,
            "sourceModified": False,
            "providerCalledByPlan": False,
            "actualRecoveryCompleted": False,
            "comparisonUnit": "pipeline_steps",
            "notLatencyOrCostClaim": True,
        },
    }
