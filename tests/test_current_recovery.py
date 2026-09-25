from __future__ import annotations

from workflow.current_recovery import build_current_recovery_plan


def _tools():
    labels = {
        "quality_audit": "数据质量审计",
        "distribution_profile": "数值分布与异常筛查",
        "rank_consistency": "排名约束核验",
        "relationship_screen": "字段关系探索",
        "group_comparison": "分组差异探索",
    }
    return {
        "executedTools": [
            {
                "key": key,
                "label": label,
                "evidenceRef": f"run/analysis_tool_{key}@v1",
            }
            for key, label in labels.items()
        ]
    }


def _plan(issues, safe=0, review=0):
    return {
        "issues": issues,
        "summary": {"safeFixCount": safe, "reviewCount": review},
    }


def _issue(issue_type, cell="B8", auto=False):
    return {
        "id": f"fix-{issue_type}",
        "type": issue_type,
        "title": issue_type,
        "severity": "medium",
        "location": {"sheet": "0", "cell": cell, "column": "综合成绩", "row": 8},
        "autoApplicable": auto,
        "reason": "test",
    }


def _build(repair):
    return build_current_recovery_plan(
        repair,
        _tools(),
        filename="scores.xlsx",
        sheet="0",
        input_fingerprint="abc123",
    )


def test_current_recovery_is_bound_to_current_input() -> None:
    result = _build(_plan([_issue("statistical_outlier")], review=1))
    assert result["source"] == {
        "filename": "scores.xlsx", "sheet": "0", "inputFingerprint": "abc123",
    }
    assert result["version"] == "current_input_recovery_v1"


def test_outlier_recomputes_distribution_relationship_and_groups_only() -> None:
    result = _build(_plan([_issue("statistical_outlier")], review=1))
    assert [item["key"] for item in result["impactedTools"]] == [
        "distribution_profile", "relationship_screen", "group_comparison",
    ]
    assert [item["key"] for item in result["reusableTools"]] == [
        "quality_audit", "rank_consistency",
    ]
    assert result["summary"]["selectiveRecomputeSteps"] == 5
    assert result["summary"]["fullRecomputeSteps"] == 7


def test_rank_issue_only_invalidates_rank_and_downstream() -> None:
    result = _build(_plan([_issue("rank_out_of_range")], review=1))
    assert [item["key"] for item in result["impactedTools"]] == ["rank_consistency"]
    assert result["summary"]["reusedSteps"] == 4
    assert result["summary"]["estimatedStepReductionPct"] == 57.14


def test_unknown_issue_fails_conservative_to_all_tools() -> None:
    result = _build(_plan([_issue("new_unknown_rule")], review=1))
    assert result["summary"]["impactedToolCount"] == 5
    assert result["summary"]["reusableToolCount"] == 0


def test_plan_never_claims_recovery_was_executed() -> None:
    result = _build(_plan([_issue("trim_whitespace", auto=True)], safe=1))
    assert result["status"] == "safe_fixes_available"
    assert result["executionBoundary"] == {
        "planOnly": True,
        "sourceModified": False,
        "providerCalledByPlan": False,
        "actualRecoveryCompleted": False,
        "comparisonUnit": "pipeline_steps",
        "notLatencyOrCostClaim": True,
    }


def test_no_issues_requires_no_recovery() -> None:
    result = _build(_plan([]))
    assert result["status"] == "no_recovery_needed"
    assert result["summary"]["selectiveRecomputeSteps"] == 0
    assert result["impactedTools"] == []


def test_recovery_uses_full_issue_count_and_hidden_issue_types() -> None:
    repair = _plan([_issue("trim_whitespace")], review=300)
    repair["summary"].update({"issueCount": 300, "byType": {"trim_whitespace": 1, "formula_error": 299}})
    result = _build(repair)
    assert result["summary"]["issueCount"] == 300
    assert result["summary"]["shownIssueCount"] == 1
    assert result["summary"]["impactedToolCount"] == 5
    assert result["status"] == "review_required"
