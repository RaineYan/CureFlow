from __future__ import annotations

from workflow.domain_knowledge import (
    DOMAIN_LABELS,
    METHOD_CARDS,
    SOURCES,
    provider_safe_method_pack,
    select_method_pack,
)
from workflow.analysis_tools import run_analysis_tools


def _pack(columns, goal="auto", row_count=100, config=None, max_cards=9):
    return select_method_pack(
        columns, goal=goal, row_count=row_count,
        analysis_config=config, max_cards=max_cards,
    )


def test_registry_covers_all_requested_domains() -> None:
    assert {card["domain"] for card in METHOD_CARDS} == set(DOMAIN_LABELS)
    assert len(METHOD_CARDS) >= 55
    assert len(SOURCES) >= 39


def test_every_method_has_resolvable_authoritative_sources() -> None:
    for card in METHOD_CARDS:
        assert card["sourceIds"]
        assert all(source_id in SOURCES for source_id in card["sourceIds"])
        assert card["blockedClaims"]
        assert card["requiredChecks"]


def test_education_columns_route_to_education() -> None:
    pack = _pack(["学号", "班级", "综合成绩", "专业排名"])
    assert pack["detectedDomains"][:2] == ["general", "education"]
    assert any(method["domain"] == "education" for method in pack["methods"])


def test_industrial_columns_route_to_industrial() -> None:
    pack = _pack(["时间", "设备编号", "振动", "轴承温度", "故障标签"])
    assert "industrial" in pack["detectedDomains"]
    assert any(method["id"] == "sensor_integrity" for method in pack["methods"])


def test_business_columns_route_to_business() -> None:
    pack = _pack(["月份", "渠道", "收入", "成本", "订单", "转化率"])
    assert "business" in pack["detectedDomains"]
    assert any(method["id"] == "business_kpi" for method in pack["methods"])


def test_economic_columns_route_to_economics() -> None:
    pack = _pack(["年份", "地区", "GDP", "CPI", "失业率", "政策实施"])
    assert "economics" in pack["detectedDomains"]
    assert any(method["id"] == "difference_in_differences" for method in pack["methods"])


def test_explicit_goal_overrides_weak_header_signal() -> None:
    pack = _pack(["时间", "值"], goal="industrial_review")
    assert pack["detectedDomains"][1] == "industrial"
    assert "用户分析目标明确选择" in "".join(pack["routingReasons"])


def test_unknown_table_does_not_invent_a_domain() -> None:
    pack = _pack(["字段甲", "字段乙"])
    assert pack["detectedDomains"] == ["general"]
    assert "不会猜测业务背景" in pack["routingReasons"][-1]


def test_small_sample_blocks_methods_with_row_requirements() -> None:
    pack = _pack(["学号", "成绩"], row_count=1)
    blocked = [method for method in pack["methods"] if method["readiness"] == "insufficient_rows"]
    assert blocked
    assert all("当前 1 行" in method["readinessNote"] for method in blocked)


def test_time_method_requires_explicit_time_configuration() -> None:
    pack = _pack(["年份", "GDP"], goal="economic_review", row_count=100)
    trend = next(method for method in pack["methods"] if method["id"] == "economic_trend")
    assert trend["readiness"] == "needs_configuration"
    configured = _pack(
        ["年份", "GDP"], goal="economic_review", row_count=100,
        config={"timeColumn": "年份", "measureColumn": "GDP"},
    )
    trend = next(method for method in configured["methods"] if method["id"] == "economic_trend")
    assert trend["readiness"] == "method_reference"


def test_pack_separates_method_guidance_from_file_evidence() -> None:
    pack = _pack(["收入", "成本"], goal="business_review")
    assert "不作为当前文件事实证据" in pack["purpose"]
    assert any(
        "不能证明" in claim or "自动归因" in claim
        for method in pack["methods"] for claim in method["blockedClaims"]
    )


def test_provider_projection_omits_urls_and_bibliographic_payload() -> None:
    safe = provider_safe_method_pack(_pack(["设备", "温度"], goal="industrial_review"))
    rendered = str(safe)
    assert "https://" not in rendered
    assert "sources" not in safe
    assert "blockedClaims" in rendered


def test_selected_pack_is_bounded_but_full_library_is_counted() -> None:
    pack = _pack(["学号", "成绩", "收入", "订单"], max_cards=7)
    assert pack["selectedMethodCount"] <= 7
    assert pack["libraryMethodCount"] == len(METHOD_CARDS)
    assert pack["librarySourceCount"] == len(SOURCES)


def test_real_problem_type_prioritizes_relevant_methods() -> None:
    trend = _pack(
        ["时间", "设备编号", "振动", "故障"], goal="trend_forecast",
        config={"timeColumn": "时间", "measureColumn": "振动"},
    )
    assert trend["methods"][0]["id"] == "industrial_change_point"
    causal = _pack(["地区", "年份", "政策", "GDP"], goal="causal_evaluation")
    ids = [method["id"] for method in causal["methods"]]
    assert "causal_gate" in ids
    assert any(item in ids for item in ("economic_impact", "difference_in_differences"))


def test_method_annotation_explains_use_requirements_checks_and_limits() -> None:
    method = _pack(["学号", "班级", "成绩"], goal="group_comparison")["methods"][0]
    assert all(label in method["method"] for label in ("适用：", "做法：", "最低条件：", "必须核对："))
    assert method["blockedClaims"]
    assert method["readinessNote"]


def test_every_general_card_can_enter_a_relevant_task_pack() -> None:
    goals = (
        "data_quality", "descriptive_profile", "group_comparison",
        "relationship_review", "trend_forecast", "causal_evaluation",
    )
    selected = {
        method["id"]
        for goal in goals
        for method in _pack(["字段甲", "字段乙"], goal=goal, row_count=100)["methods"]
    }
    assert {card["id"] for card in METHOD_CARDS if card["domain"] == "general"} <= selected


def test_card_application_requires_a_real_executed_tool_reference() -> None:
    csv_text = "数值,类别\n1,A\n2,A\n3,B\n4,B\n"
    tools = run_analysis_tools(csv_text, run_id="method_link", goal="data_quality")
    pack = select_method_pack(
        ["数值", "类别"], goal="data_quality", row_count=4,
        analysis_tools=tools,
    )
    quality = next(card for card in pack["methods"] if card["id"] == "quality_dimensions")
    assert quality["applicationStatus"] == "partial"
    assert quality["executionEvidenceRefs"] == [
        next(tool["evidenceRef"] for tool in tools["executedTools"] if tool["key"] == "quality_audit")
    ]
    assert pack["partiallyAppliedMethodCount"] == sum(
        card["applicationStatus"] == "partial" for card in pack["methods"]
    )
    safe = provider_safe_method_pack(pack)
    assert safe["methods"][0]["applicationStatus"] == "partial"
    assert "https://" not in str(safe)

    unverified = select_method_pack(
        ["数值", "类别"], goal="data_quality", row_count=4,
        analysis_tools={"analysisProcess": {"methodDecisions": [
            {"methodCardIds": ["quality_dimensions"], "status": "executed", "evidenceRef": "made-up"},
        ]}},
    )
    quality = next(card for card in unverified["methods"] if card["id"] == "quality_dimensions")
    assert quality["applicationStatus"] == "reference_only"
    assert quality["executionEvidenceRefs"] == []
