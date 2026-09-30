from __future__ import annotations

import pytest

from workflow.analysis_tools import (
    GOAL_TOOLS,
    analysis_tool_input_signatures,
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
        "exploratory_analysis", "descriptive_profile", "group_comparison", "relationship_review",
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


def test_product_distribution_uses_same_iqr_gate_as_repair_review() -> None:
    csv_text = "组别,测量值\n" + "\n".join(
        f"{group},{value}" for group, values in (("A", range(1, 21)), ("B", range(101, 121))) for value in values
    ) + "\n"
    repair_plan = build_repair_plan(csv_text, filename="grouped.csv")
    result = _run(csv_text, repair_plan=repair_plan)
    distribution = next(tool for tool in result["executedTools"] if tool["key"] == "distribution_profile")
    field = next(item for item in distribution["payload"]["fields"] if item["column"] == "测量值")
    assert field["iqrScreening"]["status"] == "skipped"
    assert field["outlierCount"] == 0
    assert not any(item["type"] == "distribution_outlier" for item in result["findings"])
    assert any(item["key"] == "group_comparison" for item in result["executedTools"])


def test_rate_distribution_keeps_descriptives_without_iqr_error_candidates() -> None:
    values = [round((index % 40 - 20) / 100, 3) for index in range(100)] + [-0.71]
    csv_text = "学业进步率\n" + "\n".join(str(value) for value in values) + "\n"
    repair_plan = build_repair_plan(csv_text, filename="rates.csv")
    result = _run(csv_text, repair_plan=repair_plan)
    distribution = next(tool for tool in result["executedTools"] if tool["key"] == "distribution_profile")
    field = distribution["payload"]["fields"][0]
    assert field["column"] == "学业进步率"
    assert field["count"] == len(values)
    assert field["iqrScreening"]["status"] == "skipped"
    assert field["outlierCount"] == 0
    assert not any(item["type"] == "distribution_outlier" for item in result["findings"])


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


def test_provider_projection_excludes_chart_points_and_process_payload() -> None:
    csv_text = "月份,指标,辅助\n" + "\n".join(
        f"2026-{month:02d},{month},{month * 3}" for month in range(1, 9)
    ) + "\n"
    result = _run(
        csv_text, goal="exploratory_analysis",
        analysis_config={"measureColumn": "指标", "timeColumn": "月份"},
    )
    safe = provider_safe_tool_results(result)
    relationship = next(item for item in safe["executedTools"] if item["key"] == "relationship_screen")
    assert "points" not in relationship["payload"]["timeTrend"]
    assert "analysisProcess" not in safe


def test_all_tool_outputs_have_stable_evidence_refs() -> None:
    result = _run("字段A,字段B\n1,2\n2,4\n3,6\n4,8\n5,10\n6,12\n7,14\n8,16\n")
    assert len(result["executedTools"]) == 5
    assert all(item["status"] == "success" for item in result["executedTools"])
    assert all(item["evidenceRef"].startswith("tool_test/analysis_tool_") for item in result["executedTools"])


def test_exploratory_process_contains_preprocessing_visuals_and_method_boundaries() -> None:
    csv_text = "时间,组别,指标,辅助值\n" + "\n".join(
        f"2026-{month:02d},{'甲' if month <= 6 else '乙'},{month * 10},{month * 20}"
        for month in range(1, 13)
    ) + "\n"
    result = _run(
        csv_text,
        goal="exploratory_analysis",
        analysis_config={"measureColumn": "指标", "groupColumn": "组别", "timeColumn": "时间"},
    )
    process = result["analysisProcess"]
    assert process["version"] == "analysis_process_v1"
    assert [stage["key"] for stage in process["stages"]] == [
        "understand", "preprocess", "visualize", "analyze", "validate",
    ]
    chart_types = {chart["type"] for chart in process["visualizations"]}
    assert chart_types >= {"histogram", "correlation", "line", "comparison"}
    histogram = next(chart for chart in process["visualizations"] if chart["type"] == "histogram")
    assert sum(item["value"] for item in histogram["series"]) == 12
    assert len(next(chart for chart in process["visualizations"] if chart["type"] == "line")["series"]) == 12
    assert process["preprocessing"]["automaticMutation"] is False
    assert "不代表已执行所有" in process["boundary"]


def test_process_reports_preprocessing_review_without_guessing_values() -> None:
    result = _run(
        "编号,值\nA,1\nA,1\nB,\nC,3\n",
        goal="exploratory_analysis",
        repair_plan={"summary": {"safeFixCount": 0, "reviewCount": 2}},
    )
    items = {item["key"]: item for item in result["analysisProcess"]["preprocessing"]["items"]}
    assert items["missing"]["count"] == 1
    assert items["duplicates"]["count"] == 1
    assert "不会" not in items["missing"]["action"]
    assert result["analysisProcess"]["stages"][1]["status"] == "review_required"


def test_selective_execution_physically_skips_unselected_numeric_tools(monkeypatch) -> None:
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("unaffected numeric tool preparation must not run")

    monkeypatch.setattr("workflow.analysis_tools._numeric_columns", fail_if_called)
    monkeypatch.setattr("workflow.analysis_tools._relationship_numeric_columns", fail_if_called)
    result = _run(
        "编号,备注\nA,ok\nB,clean\n",
        execute_tool_keys=["quality_audit"],
    )
    assert [item["key"] for item in result["executedTools"]] == ["quality_audit"]
    assert result["execution"] == {
        "mode": "selected",
        "executedToolKeys": ["quality_audit"],
        "reusedToolKeys": [],
    }


def test_distribution_reuse_guard_tracks_selected_measure_not_unselected_numeric_column() -> None:
    source = "编号,数值,辅助值\n" + "\n".join(
        f"R{index},{index},{index * 2}" for index in range(1, 9)
    ) + "\n"
    other_changed = source.replace("R3,3,6", "R3,3,60")
    measure_changed = source.replace("R3,3,6", "R3,30,6")
    config = {"measureColumn": "数值"}
    before = analysis_tool_input_signatures(source, analysis_config=config)
    after_other = analysis_tool_input_signatures(other_changed, analysis_config=config)
    after_measure = analysis_tool_input_signatures(measure_changed, analysis_config=config)
    assert before["distribution_profile"] == after_other["distribution_profile"]
    assert before["distribution_profile"] != after_measure["distribution_profile"]
    assert before["relationship_screen"] != after_other["relationship_screen"]


def test_selective_execution_rejects_tool_outside_goal() -> None:
    with pytest.raises(ValueError, match="本次分析目标未使用"):
        _run(
            "编号,值\nA,1\nB,2\n",
            goal="data_quality",
            execute_tool_keys=["relationship_screen"],
        )


def test_categorical_frequency_is_calculated_locally_with_explicit_denominator() -> None:
    result = _run(
        "学号,班级,成绩\nA,甲,80\nB,甲,81\nC,甲,82\nD,甲,83\n"
        "E,乙,84\nF,乙,85\nG,乙,86\nH,乙,87\n",
        goal="data_quality",
    )
    quality = next(tool for tool in result["executedTools"] if tool["key"] == "quality_audit")
    profile = next(item for item in quality["payload"]["categoricalProfiles"] if item["column"] == "班级")
    assert profile["validCount"] == 8
    assert profile["categoryCount"] == 2
    assert {item["sharePct"] for item in profile["frequencies"]} == {50.0}
    assert all(item["column"] != "学号" for item in quality["payload"]["categoricalProfiles"])
    assert "甲" not in str(provider_safe_tool_results(result))


def test_regular_datetime_series_reports_gap_candidate_without_claiming_missing_data() -> None:
    dates = ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04",
             "2026-01-05", "2026-01-08", "2026-01-09", "2026-01-10",
             "2026-01-11", "2026-01-12"]
    csv_text = "日期,指标\n" + "\n".join(f"{day},{index}" for index, day in enumerate(dates, 1))
    result = _run(csv_text, goal="trend_forecast", analysis_config={
        "measureColumn": "指标", "timeColumn": "日期",
    })
    relationship = next(tool for tool in result["executedTools"] if tool["key"] == "relationship_screen")
    cadence = relationship["payload"]["timeTrend"]["cadence"]
    assert cadence["regularCadence"] is True
    assert cadence["gapCandidateCount"] == 1
    finding = next(item for item in result["findings"] if item["type"] == "time_gap_candidate")
    assert "不等于记录缺失" in finding["explanation"]


def test_group_comparison_includes_median_and_spread_not_only_mean() -> None:
    result = _run(
        "组别,数值\n甲,10\n甲,11\n甲,12\n甲,100\n乙,20\n乙,21\n乙,22\n乙,23\n",
        goal="group_comparison", analysis_config={"groupColumn": "组别", "measureColumn": "数值"},
    )
    comparison = next(tool for tool in result["executedTools"] if tool["key"] == "group_comparison")["payload"]["comparisons"][0]
    assert comparison["highestMean"] > comparison["lowestMean"]
    assert comparison["highestMedian"] < comparison["lowestMedian"]
    assert comparison["highestStd"] > comparison["lowestStd"]


def test_method_decisions_distinguish_executed_from_skipped_and_link_basis() -> None:
    result = _run("编号,类别\nA,甲\nB,甲\nC,乙\nD,乙\n", goal="data_quality")
    decisions = {item["method"]: item for item in result["analysisProcess"]["methodDecisions"]}
    assert decisions["缺失、重复与分类频数"]["status"] == "executed"
    assert decisions["连续数值分布"]["status"] == "skipped"
    assert decisions["时间顺序与采样间隔"]["status"] == "skipped"
    assert decisions["缺失、重复与分类频数"]["sourceUrl"].startswith("https://www.itl.nist.gov/")
    assert decisions["连续数值分布"]["evidenceRef"] is None


def test_two_categorical_fields_use_cross_tab_without_inventing_significance() -> None:
    result = _run(
        "渠道,状态\n线上,完成\n线上,完成\n线上,完成\n线上,取消\n"
        "线下,完成\n线下,取消\n线下,取消\n线下,取消\n",
        goal="group_comparison", analysis_config={"groupColumn": "渠道"},
    )
    group = next(tool for tool in result["executedTools"] if tool["key"] == "group_comparison")
    table = group["payload"]["categoryCrossTabs"][0]
    assert table["pairedRows"] == 8
    assert sum(cell["count"] for cell in table["cells"]) == 8
    assert next(cell["rowSharePct"] for cell in table["cells"] if cell["row"] == "线上" and cell["column"] == "完成") == 75.0
    assert result["analysisProcess"]["methodDecisions"][5]["status"] == "executed"
    assert any(chart["title"].endswith("分类交叉") for chart in result["analysisProcess"]["visualizations"])
    assert "线上" not in str(provider_safe_tool_results(result))


def test_extension_cards_only_offer_real_executors_and_explain_missing_fields() -> None:
    result = _run("类别,指标\n甲,1\n甲,2\n乙,3\n乙,4\n", goal="data_quality")
    process = result["analysisProcess"]
    extensions = {item["method"]: item for item in process["extensions"]}
    assert extensions["分组数值描述"]["goal"] == "group_comparison"
    assert extensions["分组数值描述"]["missingFields"] == ["主指标", "分组字段"]
    assert extensions["双分类交叉计数"]["goal"] == "exploratory_analysis"
    assert extensions["时间顺序与采样检查"]["status"] == "configurable"
    assert all(item["goal"] in GOAL_TOOLS for item in process["extensions"])
    assert {item["method"] for item in process["futureMethods"]} >= {"假设检验", "回归建模", "聚类分群", "预测与预警", "因果评估"}
    assert all("尚无执行器" in item["requires"] for item in process["futureMethods"])
