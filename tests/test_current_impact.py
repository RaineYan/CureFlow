from __future__ import annotations

from workflow.current_impact import build_current_impact_report


def _tools():
    return {
        "executedTools": [
            {"key": "quality_audit", "label": "数据质量审计", "evidenceRef": "r/quality@v1"},
            {"key": "distribution_profile", "label": "分布筛查", "evidenceRef": "r/distribution@v1"},
            {"key": "rank_consistency", "label": "排名核验", "evidenceRef": "r/rank@v1"},
            {"key": "relationship_screen", "label": "关系探索", "evidenceRef": "r/relation@v1"},
            {"key": "group_comparison", "label": "分组比较", "evidenceRef": "r/group@v1"},
        ]
    }


def _build(*, issues=None, claims=None, summary=None):
    issues = issues or []
    repair_summary = {"issueCount": len(issues), **(summary or {})}
    return build_current_impact_report(
        repair_plan={"issues": issues, "summary": repair_summary},
        analysis_tools=_tools(),
        claims=claims or [],
        recovery_plan={"status": "review_required" if issues else "no_recovery_needed"},
        filename="scores.xlsx",
        sheet="成绩",
        input_fingerprint="abc123",
    )


def test_data_issue_path_starts_at_exact_cell_and_reaches_report() -> None:
    report = _build(issues=[{
        "id": "rank-1", "type": "rank_out_of_range", "title": "排名越界",
        "location": {"sheet": "成绩", "cell": "D8", "column": "排名"},
        "autoApplicable": False,
    }])
    path = report["paths"][0]
    assert path["sourceLabel"].startswith("成绩!D8")
    assert path["steps"] == ["data:rank-1", "tool:rank_consistency", "report:final"]
    assert report["summary"]["affectedToolCount"] == 1


def test_unknown_data_issue_conservatively_affects_all_executed_tools() -> None:
    report = _build(issues=[{
        "id": "new-1", "type": "future_rule", "title": "未知问题",
        "location": {"cell": "A2"},
    }])
    assert report["summary"]["affectedToolCount"] == 5
    assert "未知问题类型" in report["paths"][0]["meaning"]


def test_rejected_claim_with_missing_evidence_has_explicit_path() -> None:
    report = _build(claims=[{
        "content": "所有学生一定进步",
        "grounding": {
            "verdict": "rejected",
            "risk_codes": ["H2_DANGLING_REFERENCE"],
            "evidence": [{"ref": "r/missing@v1", "source_status": "missing"}],
        },
    }])
    assert report["summary"]["claimIssueCount"] == 1
    assert report["summary"]["evidenceIssueCount"] == 1
    assert report["paths"][0]["steps"] == [
        "evidence:r/missing@v1", "claim:1", "report:final",
    ]


def test_semantic_claim_failure_without_bad_reference_uses_rule_node() -> None:
    report = _build(claims=[{
        "content": "相关性证明因果",
        "grounding": {
            "verdict": "needs_review",
            "risk_codes": ["H6_CAUSAL_EXTRAPOLATION"],
            "evidence": [{"ref": "r/verified@v1", "source_status": "verified"}],
        },
    }])
    assert report["paths"][0]["steps"] == ["claim-rule:1", "claim:1", "report:final"]


def test_issue_total_remains_full_when_response_only_shows_a_page() -> None:
    report = _build(
        issues=[{
            "id": "shown", "type": "missing_value", "title": "缺失",
            "location": {"cell": "B2"},
        }],
        summary={"issueCount": 250},
    )
    assert report["summary"]["dataClueCount"] == 250
    assert report["summary"]["shownDataClueCount"] == 1


def test_execution_boundary_does_not_overclaim_selective_recovery() -> None:
    boundary = _build()["executionBoundary"]
    assert boundary == {
        "dataCluesConfirmedAsErrors": False,
        "impactIsPlan": True,
        "selectiveRecoveryExecuted": False,
        "actualToolReuseRecorded": False,
        "actualSavingsClaimed": False,
    }


def test_actual_selective_execution_is_recorded_without_cost_claim() -> None:
    report = build_current_impact_report(
        repair_plan={"issues": [], "summary": {"issueCount": 0}},
        analysis_tools=_tools(),
        claims=[],
        recovery_plan={"status": "no_recovery_needed"},
        filename="scores_fixed.xlsx",
        sheet="成绩",
        input_fingerprint="fixed123",
        recovery_execution={
            "actualSelectiveExecution": True,
            "executedToolKeys": ["quality_audit"],
            "reusedToolKeys": ["rank_consistency"],
        },
    )
    assert report["executionBoundary"]["selectiveRecoveryExecuted"] is True
    assert report["executionBoundary"]["actualToolReuseRecorded"] is True
    assert report["executionBoundary"]["actualSavingsClaimed"] is False
    assert report["recoveryExecution"]["reusedToolKeys"] == ["rank_consistency"]
