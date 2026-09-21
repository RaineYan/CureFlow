from __future__ import annotations

import pytest

from workflow.analysis_tools import (
    GOAL_TOOLS,
    provider_safe_tool_results,
    run_analysis_tools,
    validate_analysis_config,
    validate_analysis_goal,
)
from workflow.repair_analysis import build_repair_plan


def _run(csv_text: str, **kwargs):
    return run_analysis_tools(csv_text, run_id="tool_test", **kwargs)


def test_analysis_goal_is_strict() -> None:
    assert validate_analysis_goal("score_audit") == "score_audit"
    for goal in (
        "descriptive_profile", "group_comparison", "relationship_review",
        "trend_forecast", "causal_evaluation",
    ):
        assert validate_analysis_goal(goal) == goal
    with pytest.raises(ValueError, match="analysisGoal"):
        validate_analysis_goal("make_up_answers")


@pytest.mark.parametrize("goal", [
    "descriptive_profile", "group_comparison", "relationship_review",
    "trend_forecast", "causal_evaluation", "industrial_review", "economic_review",
])
def test_extended_goals_execute_only_the_tools_selected_by_the_question(goal: str) -> None:
    result = _run(
        "时间,组别,指标\n2024-01,A,1\n2024-02,A,2\n2024-03,A,3\n2024-04,A,4\n"
        "2024-05,B,5\n2024-06,B,6\n2024-07,B,7\n2024-08,B,8\n",
        goal=goal,
        analysis_config={"measureColumn": "指标", "groupColumn": "组别", "timeColumn": "时间"},
    )
    assert result["goal"] == goal
    assert {item["key"] for item in result["executedTools"]} == GOAL_TOOLS[goal]


def test_relationship_goal_reports_when_it_cannot_answer() -> None:
    result = _run(
        "学号,综合成绩,班级\nA,80,一班\nB,82,一班\nC,84,一班\nD,86,一班\n"
        "E,88,二班\nF,90,二班\nG,92,二班\nH,94,二班\n",
        goal="relationship_review",
        analysis_config={"measureColumn": "综合成绩"},
    )
    assert [item["key"] for item in result["executedTools"]] == ["relationship_screen"]
    payload = result["executedTools"][0]["payload"]
    assert payload["eligibleFieldCount"] == 1
    assert "至少需要 2 个" in payload["blockedReason"]
    assert result["recommendations"][0]["action"] == "补齐关联分析条件"


def test_analysis_config_rejects_unknown_or_reused_columns() -> None:
    assert validate_analysis_config(
        {"measureColumn": "成绩", "groupColumn": "班级"}, ["班级", "成绩"],
    )["measureColumn"] == "成绩"
    with pytest.raises(ValueError, match="不属于当前读取范围"):
        validate_analysis_config({"measureColumn": "不存在"}, ["成绩"])
    with pytest.raises(ValueError, match="同时承担"):
        validate_analysis_config(
            {"measureColumn": "成绩", "groupColumn": "成绩"}, ["成绩"],
        )


def test_user_selected_measure_limits_distribution_and_relationships() -> None:
    csv_text = "组别,主指标,辅助值,其他值\n" + "\n".join(
        f"{('A' if index < 6 else 'B')},{index},{index * 2},{100-index}"
        for index in range(1, 13)
    )
    result = _run(csv_text, analysis_config={"measureColumn": "主指标"})
    distribution = next(tool for tool in result["executedTools"] if tool["key"] == "distribution_profile")
    relationships = next(tool for tool in result["executedTools"] if tool["key"] == "relationship_screen")
    assert [field["column"] for field in distribution["payload"]["fields"]] == ["主指标"]
    assert all("主指标" in {item["left"], item["right"]} for item in relationships["payload"]["candidates"])


def test_user_selected_group_and_measure_drive_group_comparison() -> None:
    csv_text = "指定分组,干扰分组,指定指标,其他指标\n" + "\n".join(
        [
            "甲,X,90,1", "甲,Y,88,2", "甲,X,92,3", "甲,Y,91,4",
            "乙,X,60,5", "乙,Y,62,6", "乙,X,58,7", "乙,Y,61,8",
        ]
    )
    result = _run(csv_text, analysis_config={
        "measureColumn": "指定指标", "groupColumn": "指定分组",
    })
    group = next(tool for tool in result["executedTools"] if tool["key"] == "group_comparison")
    assert group["payload"]["comparisons"]
    assert {item["categoryColumn"] for item in group["payload"]["comparisons"]} == {"指定分组"}
    assert {item["measureColumn"] for item in group["payload"]["comparisons"]} == {"指定指标"}


def test_user_selected_time_field_produces_real_trend_summary() -> None:
    csv_text = "月份,指标\n" + "\n".join(
        f"2026-{month:02d},{month * 10}" for month in range(1, 9)
    ) + "\n"
    result = _run(csv_text, analysis_config={
        "measureColumn": "指标", "timeColumn": "月份",
    })
    relationship = next(
        tool for tool in result["executedTools"] if tool["key"] == "relationship_screen"
    )
    trend = relationship["payload"]["timeTrend"]
    assert trend["comparableRows"] == 8
    assert trend["firstValue"] == 10.0
    assert trend["lastValue"] == 80.0
    assert trend["changePct"] == 700.0
    assert any(item["type"] == "time_trend" for item in result["findings"])


def test_quality_tool_counts_missing_and_duplicate_rows() -> None:
    result = _run("编号,值\nA,1\nA,1\nB,\n", goal="data_quality")
    quality = result["executedTools"][0]
    assert quality["key"] == "quality_audit"
    assert quality["payload"]["missingCellCount"] == 1
    assert quality["payload"]["duplicateRowCount"] == 1
    assert {item["type"] for item in result["findings"]} >= {"missingness", "duplicate_rows"}


def test_distribution_tool_reports_locations_without_calling_them_errors() -> None:
    csv_text = "样本,测量值\n" + "\n".join(
        f"S{index},{value}" for index, value in enumerate([10, 10, 11, 11, 12, 12, 13, 100])
    )
    result = _run(csv_text, sheet="数据", source_row_numbers=range(5, 13))
    finding = next(item for item in result["findings"] if item["type"] == "distribution_outlier")
    assert finding["severity"] == "review"
    assert "不等于数据错误" in finding["explanation"]
    assert finding["details"]["outlierLocations"] == [{"sheet": "数据", "cell": "B12", "row": 12}]


def test_rank_tool_reports_exact_invalid_cell() -> None:
    result = _run("学号,班级排名,班级总人数\nA,12,10\nB,2,10\n", sheet="0")
    finding = next(item for item in result["findings"] if item["type"] == "rank_consistency")
    assert finding["details"]["invalidRows"] == 1
    assert finding["details"]["invalidLocations"] == ["0!B2"]


def test_relationship_tool_computes_current_file_correlation_only() -> None:
    csv_text = "序号,投入,产出\n" + "\n".join(
        f"{index},{index},{index * 2}" for index in range(1, 13)
    )
    result = _run(csv_text, goal="business_review")
    finding = next(item for item in result["findings"] if item["type"] == "correlation_candidate")
    assert finding["details"]["coefficient"] == 1.0
    assert "不能证明因果关系" in finding["explanation"]


def test_group_tool_produces_descriptive_comparison() -> None:
    csv_text = "组别,成绩\n" + "\n".join(
        ["A,90", "A,88", "A,92", "A,91", "B,60", "B,62", "B,58", "B,61"]
    )
    result = _run(csv_text, goal="score_audit")
    finding = next(item for item in result["findings"] if item["type"] == "group_difference")
    assert finding["details"]["highestGroup"] == "A"
    assert finding["details"]["lowestGroup"] == "B"
    assert "未经显著性" in finding["explanation"]


def test_repair_plan_drives_non_automatic_recommendation() -> None:
    csv_text = "编号,备注\nA, ok \nB,clean\n"
    plan = build_repair_plan(csv_text, filename="input.csv")
    result = _run(csv_text, repair_plan=plan)
    recommendation = next(item for item in result["recommendations"] if "安全修正版" in item["action"])
    assert recommendation["automatic"] is False
    assert recommendation["evidenceRefs"] == ["tool_test/analysis_tool_quality_audit@v1"]


def test_provider_projection_removes_locations_and_category_labels() -> None:
    csv_text = "组别,成绩\n" + "\n".join(
        ["甲组,90", "甲组,88", "甲组,92", "甲组,91", "乙组,60", "乙组,62", "乙组,58", "乙组,61"]
    )
    result = _run(csv_text, goal="score_audit", sheet="学生表")
    safe = provider_safe_tool_results(result)
    rendered = str(safe)
    assert "outlierLocations" not in rendered
    assert "invalidLocations" not in rendered
    assert "highestGroup" not in rendered
    assert "甲组" not in rendered
    assert safe["privacy"]["rawRowsSentToProvider"] is False


def test_all_tool_outputs_have_stable_evidence_refs() -> None:
    result = _run("字段A,字段B\n1,2\n2,4\n3,6\n4,8\n5,10\n6,12\n7,14\n8,16\n")
    assert len(result["executedTools"]) == 5
    assert all(item["status"] == "success" for item in result["executedTools"])
    assert all(item["evidenceRef"].startswith("tool_test/analysis_tool_") for item in result["executedTools"])
