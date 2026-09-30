"""HTTP handler coverage for the local CureFlow desktop API.

These tests instantiate the handler without binding a socket or starting a
server. Request and response streams are in-memory BytesIO objects.
"""
from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest

from cureflow_app.server import ApiInputError, MAX_REQUEST_BYTES, make_handler


@pytest.fixture
def handler_request(tmp_path: Path):
    handler_type = make_handler(tmp_path)

    def invoke(method: str, path: str, body: bytes = b"", content_length: int | None = None):
        handler = object.__new__(handler_type)
        handler.path = path
        handler.headers = {"Content-Length": str(len(body) if content_length is None else content_length)}
        handler.rfile = BytesIO(body)
        handler.wfile = BytesIO()
        response: dict[str, object] = {"status": None, "headers": {}}
        handler.send_response = lambda status: response.update(status=status)
        handler.send_header = lambda key, value: response["headers"].__setitem__(key, value)
        handler.end_headers = lambda: None
        getattr(handler, f"do_{method}")()
        response["json"] = json.loads(handler.wfile.getvalue().decode("utf-8"))
        return response

    return invoke


def _post_json(handler_request, payload: object):
    return handler_request("POST", "/api/analyze", json.dumps(payload).encode("utf-8"))


def _inspect_json(handler_request, payload: object):
    return handler_request("POST", "/api/inspect", json.dumps(payload).encode("utf-8"))


def _repair_preview_json(handler_request, payload: object):
    return handler_request("POST", "/api/repair/preview", json.dumps(payload).encode("utf-8"))


def _repair_export_json(handler_request, payload: object):
    return handler_request("POST", "/api/repair/export", json.dumps(payload).encode("utf-8"))


def _repair_issues_json(handler_request, payload: object):
    return handler_request("POST", "/api/repair/issues", json.dumps(payload).encode("utf-8"))


def test_repair_issue_pages_bind_to_input_and_include_later_cells(handler_request) -> None:
    source = "编号,备注\n" + "".join(f"R{index}, 值{index} \n" for index in range(270))
    inspection = _inspect_json(handler_request, {"filename": "source.csv", "csvText": source})["json"]
    body = {"filename": "source.csv", "csvText": source, "inputFingerprint": inspection["inputFingerprint"], "offset": 250, "limit": 40}
    response = _repair_issues_json(handler_request, body)
    assert response["status"] == 200
    assert response["json"]["providerUsed"] is False
    assert response["json"]["summary"]["issueCount"] == 270
    assert len(response["json"]["issues"]) == 20
    assert response["json"]["issues"][0]["location"]["cell"] == "B252"
    assert response["json"]["nextOffset"] is None
    bad = _repair_issues_json(handler_request, {**body, "inputFingerprint": "other"})
    assert bad["status"] == 400


def test_repair_issue_page_respects_selected_group_context(handler_request) -> None:
    source = "性别,测量值\n" + "\n".join(
        f"{group},{value}" for group, values in (("甲", range(1, 21)), ("乙", range(101, 121))) for value in values
    ) + "\n"
    inspection = _inspect_json(handler_request, {"filename": "grouped.csv", "csvText": source})["json"]
    response = _repair_issues_json(handler_request, {
        "filename": "grouped.csv", "csvText": source,
        "inputFingerprint": inspection["inputFingerprint"],
        "analysisConfig": {"groupColumn": "性别"},
    })
    assert response["status"] == 200
    assessment = next(item for item in response["json"]["summary"]["iqrScreening"] if item["column"] == "测量值")
    assert assessment["status"] == "skipped"
    assert "本次已指定分组字段" in assessment["reason"]


def test_repair_can_apply_selected_issue_beyond_first_page(handler_request) -> None:
    source = "编号,备注\n" + "".join(f"R{index}, 值{index} \n" for index in range(260))
    inspection = _inspect_json(handler_request, {"filename": "source.csv", "csvText": source})["json"]
    body = {"filename": "source.csv", "csvText": source, "inputFingerprint": inspection["inputFingerprint"]}
    page = _repair_issues_json(handler_request, {**body, "offset": 250, "limit": 10})["json"]
    selected = page["issues"][0]
    exported = handler_request("POST", "/api/repair/export", json.dumps({**body, "approvedFixIds": [selected["id"]]}).encode("utf-8"))
    assert exported["status"] == 200
    assert exported["json"]["audit"]["appliedFixIds"] == [selected["id"]]
    assert "R250,值250" in exported["json"]["csvText"]
    preview = _repair_preview_json(handler_request, {**body, "approvedFixIds": [selected["id"]]})
    assert preview["status"] == 200
    assert preview["json"]["summary"]["beforeIssueCount"] == 260
    assert preview["json"]["summary"]["afterIssueCount"] == 259
    assert preview["json"]["changedCells"][0]["location"]["cell"] == "B252"


def test_health(handler_request) -> None:
    response = handler_request("GET", "/api/health")
    assert response["status"] == 200
    assert response["json"] == {"ok": True, "service": "cureflow-local"}


def test_sample(handler_request) -> None:
    response = handler_request("GET", "/api/sample")
    assert response["status"] == 200
    assert response["json"]["filename"] == "sample_company.csv"
    assert "year,month,revenue,cost,users,orders,visits" in response["json"]["csvText"]


def test_inspect_returns_file_structure_without_provider_or_claims(handler_request) -> None:
    response = _inspect_json(handler_request, {
        "filename": "scores.csv",
        "csvText": "学号,姓名,综合成绩\n20250001,甲,88\n20250002,乙,76\n",
    })
    assert response["status"] == 200
    payload = response["json"]
    assert payload["providerUsed"] is False
    assert payload["rowCount"] == 2
    assert payload["columnCount"] == 3
    assert payload["previewRows"][0]["学号"] == "20250001"
    assert "claims" not in payload


def test_inspect_separates_header_semantics_from_analysis_eligibility(handler_request) -> None:
    response = _inspect_json(handler_request, {
        "filename": "one-row.csv",
        "csvText": "学号,综合成绩,专业年级总人数\n,,104\n",
    })
    assert response["status"] == 200
    columns = {item["name"]: item for item in response["json"]["columns"]}
    assert columns["综合成绩"]["kind"] == "成绩数值"
    assert columns["综合成绩"]["analysisEligible"] is False
    assert columns["专业年级总人数"]["analysisEligible"] is False


def test_recovery_evidence(handler_request) -> None:
    response = handler_request("GET", "/api/recovery-evidence")
    assert response["status"] == 200
    expected = {"experimentId", "mode", "stage", "trialCount", "strategies", "reductionsVsFull"}
    assert expected <= response["json"].keys()


def test_provider_status_defaults_offline_and_exposes_no_key(handler_request) -> None:
    response = handler_request("GET", "/api/provider-status")
    assert response["status"] == 200
    assert response["json"]["defaultMode"] == "OFFLINE_DETERMINISTIC_FIXTURE"
    assert response["json"]["openai"]["maxCallsPerRun"] == 6
    assert response["json"]["openai"]["apiKeyValueExposed"] is False
    assert response["json"]["qwen"]["mode"] == "qwen_chat"
    assert response["json"]["deepseek"]["mode"] == "deepseek_chat"
    assert response["json"]["qwen"]["apiKeyValueExposed"] is False
    assert response["json"]["deepseek"]["apiKeyValueExposed"] is False
    assert "sk-" not in json.dumps(response["json"])


def test_provider_kill_switch_blocks_future_runs(handler_request) -> None:
    response = handler_request("POST", "/api/provider/kill")
    assert response["status"] == 200
    assert response["json"]["openai"]["killSwitchActive"] is True
    after = handler_request("GET", "/api/provider-status")
    assert "KILL_SWITCH_ACTIVE" in after["json"]["openai"]["blockers"]


def test_openai_analyze_without_confirmation_never_reaches_network(
    handler_request,
) -> None:
    payload = {
        "filename": "sample.csv",
        "csvText": "year,month,revenue,cost,users,orders,visits\n2025,1,1,1,1,1,1",
        "providerMode": "openai_responses",
        "confirmLive": False,
        "confirmMaxCalls": 6,
    }
    with patch("llm.openai_responses.urllib.request.urlopen") as network:
        response = _post_json(handler_request, payload)
    network.assert_not_called()
    assert response["status"] == 400
    assert "明确确认" in response["json"]["error"]


def test_analyze_success_shape(handler_request) -> None:
    sample = handler_request("GET", "/api/sample")["json"]
    response = _post_json(handler_request, sample)
    assert response["status"] == 200
    assert response["json"]["mode"] == "OFFLINE_DETERMINISTIC_FIXTURE"
    expected = {
        "runId", "mode", "metrics", "claims", "stages", "report", "cost",
        "groundingSummary", "routing",
    }
    assert expected <= response["json"].keys()


def test_analyze_runs_local_tools_before_model_interpretation(handler_request) -> None:
    response = _post_json(handler_request, {
        "filename": "scores.csv",
        "csvText": "学号,组别,成绩,班级排名,班级总人数\nA,一班,90,1,2\nB,一班,80,2,2\n",
        "analysisGoal": "score_audit",
    })
    assert response["status"] == 200
    tools = response["json"]["analysisTools"]
    assert tools["goal"] == "score_audit"
    assert tools["summary"]["executedToolCount"] == 3
    assert all(item["status"] == "success" for item in tools["executedTools"])
    assert response["json"]["input"]["analysisGoalLabel"] == "成绩与排名核验"
    knowledge = response["json"]["knowledgeContext"]
    assert knowledge["version"] == "cureflow_method_knowledge_v1"
    assert "education" in knowledge["detectedDomains"]
    assert knowledge["selectedMethodCount"] >= 1
    assert all(method["blockedClaims"] for method in knowledge["methods"])
    report = response["json"]["report"]
    assert "# 成绩与排名是否一致？" in report
    assert "## 一句话看懂" in report
    assert "## 发现了什么" in report
    assert "## 是否需要修正" in report
    assert "## 建议下一步" in report
    assert "## 目前不能下的结论" in report
    assert "@v1" not in report
    assert "analysis_tool_" not in report
    assert "distribution_profile" not in report
    recovery = response["json"]["currentRecoveryPlan"]
    assert recovery["source"]["filename"] == "scores.csv"
    assert recovery["source"]["inputFingerprint"] == response["json"]["input"]["inputFingerprint"]
    assert recovery["executionBoundary"]["planOnly"] is True
    impact = response["json"]["currentImpactReport"]
    assert impact["source"]["inputFingerprint"] == response["json"]["input"]["inputFingerprint"]
    assert impact["summary"]["dataClueCount"] == recovery["summary"]["issueCount"]
    assert impact["executionBoundary"]["impactIsPlan"] is True
    assert impact["executionBoundary"]["selectiveRecoveryExecuted"] is False


def test_method_card_partial_use_is_backed_by_this_runs_tool_result(handler_request) -> None:
    response = _post_json(handler_request, {
        "filename": "quality.csv",
        "csvText": "数值,类别\n1,A\n2,A\n3,B\n4,B\n",
        "analysisGoal": "data_quality",
    })
    assert response["status"] == 200
    payload = response["json"]
    quality = next(
        card for card in payload["knowledgeContext"]["methods"]
        if card["id"] == "quality_dimensions"
    )
    tool_refs = {tool["evidenceRef"] for tool in payload["analysisTools"]["executedTools"]}
    assert quality["applicationStatus"] == "partial"
    assert set(quality["executionEvidenceRefs"]) <= tool_refs
    assert payload["knowledgeContext"]["partiallyAppliedMethodCount"] >= 1


def test_real_file_can_be_checked_without_provider_key_or_model_call(handler_request) -> None:
    source = "学号,成绩,班级排名,班级总人数\nA,90,1,2\nB,,3,2\n"
    inspection = _inspect_json(handler_request, {"filename": "scores.csv", "csvText": source})
    assert inspection["status"] == 200
    with patch("cureflow_app.provider_runtime.DesktopProviderRuntime.resolve", side_effect=AssertionError("模型不应被调用")):
        response = _post_json(handler_request, {
            "filename": "scores.csv", "csvText": source,
            "analysisGoal": "exploratory_analysis", "providerMode": "offline",
        })
    assert response["status"] == 200
    result = response["json"]
    assert result["cost"]["llmCalls"] == 0
    assert result["provider"]["live"] is False
    assert result["input"]["inputFingerprint"] == inspection["json"]["inputFingerprint"]
    assert result["repairPlan"]["summary"]["issueCount"] >= 2
    assert result["analysisTools"]["summary"]["executedToolCount"] >= 1


def test_corrected_copy_recomputes_only_impacted_tools_and_reuses_the_rest(handler_request) -> None:
    source = "编号,组别,数值,备注\n" + "\n".join(
        f"R{index},{'甲' if index < 5 else '乙'},{index}, {'待核对' if index == 1 else '正常'} "
        for index in range(1, 9)
    ) + "\n"
    first = _post_json(handler_request, {
        "filename": "source.csv", "csvText": source, "analysisGoal": "auto",
    })
    assert first["status"] == 200
    original = first["json"]
    issue = next(
        item for item in original["repairPlan"]["issues"]
        if item["type"] == "trim_whitespace"
    )
    exported = _repair_export_json(handler_request, {
        "filename": "source.csv",
        "csvText": source,
        "sourceRunId": original["runId"],
        "inputFingerprint": original["input"]["inputFingerprint"],
        "approvedFixIds": [issue["id"]],
        "manualCorrections": [],
    })
    assert exported["status"] == 200
    assert exported["json"]["audit"]["appliedIssueTypes"] == ["trim_whitespace"]

    recovered = _post_json(handler_request, {
        "filename": exported["json"]["filename"],
        "csvText": exported["json"]["csvText"],
        "analysisGoal": "auto",
        "recoveryContext": {
            "sourceRunId": original["runId"],
            "sourceInputFingerprint": original["input"]["inputFingerprint"],
            "appliedIssueIds": [issue["id"]],
        },
    })
    assert recovered["status"] == 200
    execution = recovered["json"]["recoveryExecution"]
    assert execution["actualSelectiveExecution"] is True
    assert execution["executedToolKeys"] == ["quality_audit", "group_comparison"]
    assert execution["reusedToolKeys"] == [
        "distribution_profile", "rank_consistency", "relationship_screen",
    ]
    tools = recovered["json"]["analysisTools"]
    assert tools["summary"]["actuallyExecutedToolCount"] == 2
    assert tools["summary"]["reusedToolCount"] == 3
    assert {
        item["key"]: item.get("executionStatus") for item in tools["executedTools"]
    } == {
        "quality_audit": "recomputed",
        "distribution_profile": "reused",
        "rank_consistency": "reused",
        "relationship_screen": "reused",
        "group_comparison": "recomputed",
    }


def test_corrected_rank_value_invalidates_rank_tool_even_for_generic_missing_issue(handler_request) -> None:
    source = "编号,排名,总人数,数值\n" + "\n".join(
        f"R{index},{'' if index == 3 else index},10,{index * 2}"
        for index in range(1, 9)
    ) + "\n"
    first = _post_json(handler_request, {
        "filename": "source.csv", "csvText": source, "analysisGoal": "auto",
    })
    assert first["status"] == 200
    original = first["json"]
    issue = next(
        item for item in original["repairPlan"]["issues"]
        if item["type"] == "missing_value" and item["location"]["column"] == "排名"
    )
    exported = _repair_export_json(handler_request, {
        "filename": "source.csv",
        "csvText": source,
        "sourceRunId": original["runId"],
        "inputFingerprint": original["input"]["inputFingerprint"],
        "approvedFixIds": [],
        "manualCorrections": [{"issueId": issue["id"], "newValue": "3"}],
        "confirmManualCorrections": True,
    })
    assert exported["status"] == 200
    recovered = _post_json(handler_request, {
        "filename": exported["json"]["filename"],
        "csvText": exported["json"]["csvText"],
        "analysisGoal": "auto",
        "recoveryContext": {
            "sourceRunId": original["runId"],
            "sourceInputFingerprint": original["input"]["inputFingerprint"],
            "appliedIssueIds": [issue["id"]],
        },
    })
    assert recovered["status"] == 200
    execution = recovered["json"]["recoveryExecution"]
    assert "rank_consistency" in execution["inputChangedToolKeys"]
    assert "rank_consistency" in execution["executedToolKeys"]
    assert "rank_consistency" not in execution["reusedToolKeys"]
    full = _post_json(handler_request, {
        "filename": exported["json"]["filename"],
        "csvText": exported["json"]["csvText"],
        "analysisGoal": "auto",
    })
    assert full["status"] == 200
    recovered_rank = next(tool for tool in recovered["json"]["analysisTools"]["executedTools"] if tool["key"] == "rank_consistency")
    full_rank = next(tool for tool in full["json"]["analysisTools"]["executedTools"] if tool["key"] == "rank_consistency")
    assert recovered_rank["payload"] == full_rank["payload"]


def test_selective_recovery_rejects_fix_not_bound_to_exported_copy(handler_request) -> None:
    source = "编号,备注\nA, ok \nB,clean\n"
    first = _post_json(handler_request, {"filename": "source.csv", "csvText": source})["json"]
    response = _post_json(handler_request, {
        "filename": "changed.csv",
        "csvText": "编号,备注\nA,ok\nB,clean\n",
        "recoveryContext": {
            "sourceRunId": first["runId"],
            "sourceInputFingerprint": first["input"]["inputFingerprint"],
            "appliedIssueIds": ["not-from-this-run"],
        },
    })
    assert response["status"] == 400
    assert "授权不存在" in response["json"]["error"]


def test_relationship_question_controls_tools_and_report(handler_request) -> None:
    rows = "\n".join(
        f"S{index},{index * 10},{index * 2}" for index in range(1, 13)
    )
    response = _post_json(handler_request, {
        "filename": "关联数据.csv",
        "csvText": "学号,主指标,对比指标\n" + rows + "\n",
        "analysisGoal": "relationship_review",
        "analysisConfig": {"measureColumn": "主指标"},
    })
    assert response["status"] == 200
    payload = response["json"]
    assert [tool["key"] for tool in payload["analysisTools"]["executedTools"]] == [
        "relationship_screen"
    ]
    assert payload["analysisTools"]["summary"]["executedToolCount"] == 1
    assert payload["report"].startswith("# 哪些因素有关联？")
    assert "字段关系探索" in payload["report"]
    assert "数据质量审计" not in payload["report"]


def test_analyze_rejects_unknown_analysis_goal(handler_request) -> None:
    response = _post_json(handler_request, {
        "filename": "scores.csv",
        "csvText": "字段,值\nA,1\n",
        "analysisGoal": "invent_everything",
    })
    assert response["status"] == 400
    assert "analysisGoal" in response["json"]["error"]


def test_analyze_reports_unusable_selected_measure_as_input_error(handler_request) -> None:
    response = _post_json(handler_request, {
        "filename": "one-row.csv",
        "csvText": "学号,综合成绩,专业年级总人数\n,,104\n",
        "analysisGoal": "relationship_review",
        "analysisConfig": {"measureColumn": "综合成绩"},
    })
    assert response["status"] == 400
    assert "当前读取范围无法执行所选分析" in response["json"]["error"]
    assert "至少需要 4 个有效数值" in response["json"]["error"]


def test_analyze_uses_explicit_field_roles_for_current_file(handler_request) -> None:
    rows = "\n".join(
        f"{index},{'甲' if index < 5 else '乙'},{index * 10},{index * 3}"
        for index in range(1, 9)
    )
    response = _post_json(handler_request, {
        "filename": "实际表.csv",
        "csvText": "编号,组别,目标值,干扰值\n" + rows + "\n",
        "analysisGoal": "business_review",
        "analysisConfig": {
            "measureColumn": "目标值",
            "groupColumn": "组别",
            "identifierColumn": "编号",
            "timeColumn": None,
        },
    })
    assert response["status"] == 200
    config = response["json"]["analysisTools"]["analysisConfig"]
    assert config["measureColumn"] == "目标值"
    distribution = next(
        tool for tool in response["json"]["analysisTools"]["executedTools"]
        if tool["key"] == "distribution_profile"
    )
    assert [item["column"] for item in distribution["payload"]["fields"]] == ["目标值"]


def test_analyze_rejects_field_role_outside_current_file(handler_request) -> None:
    response = _post_json(handler_request, {
        "filename": "actual.csv",
        "csvText": "字段,数值\nA,1\nB,2\nC,3\nD,4\n",
        "analysisConfig": {"measureColumn": "不存在"},
    })
    assert response["status"] == 400
    assert "不属于当前读取范围" in response["json"]["error"]


def test_repair_preview_endpoint_returns_before_after_delta(handler_request) -> None:
    source = "编号,成绩\nA,999\nB,80\nC,90\nD,85\n"
    from workflow.repair_analysis import build_repair_plan
    plan = build_repair_plan(source, filename="scores.csv")
    issue = next(item for item in plan["issues"] if item["type"] == "score_out_of_range")
    response = _repair_preview_json(handler_request, {
        "filename": "scores.csv",
        "csvText": source,
        "approvedFixIds": [],
        "manualCorrections": [{"issueId": issue["id"], "newValue": "99"}],
        "confirmManualCorrections": True,
    })
    assert response["status"] == 200
    assert response["json"]["summary"]["resolvedIssueCount"] == 1
    assert response["json"]["summary"]["afterIssueCount"] == 0
    assert response["json"]["providerUsed"] is False


def test_analyze_exposes_claim_grounding_without_calling_a_judge_model(
    handler_request,
) -> None:
    sample = handler_request("GET", "/api/sample")["json"]
    payload = _post_json(handler_request, sample)["json"]
    assert len(payload["claims"]) == 3
    assert {claim["grounding"]["verdict"] for claim in payload["claims"]} == {
        "grounded"
    }
    assert payload["groundingSummary"] == {
        "method": "deterministic_evidence_grounding_v1",
        "total_claims": 3,
        "grounded_claims": 3,
        "needs_review_claims": 0,
        "rejected_claims": 0,
        "observed_risk_rate_pct": 0.0,
        "population_hallucination_rate_available": False,
        "interpretation": "该比例仅表示本次运行中被规则标记的断言占比，不代表模型总体幻觉率。",
    }
    assert payload["routing"]["calls"] == 6
    assert payload["routing"]["automaticFallback"] is False


def test_analyze_empty_csv(handler_request) -> None:
    response = _post_json(handler_request, {"filename": "empty.csv", "csvText": ""})
    assert response["status"] == 400
    assert "不能为空" in response["json"]["error"]


def test_analyze_invalid_json(handler_request) -> None:
    response = handler_request("POST", "/api/analyze", b"{not-json")
    assert response["status"] == 400


def test_analyze_oversized_request(handler_request) -> None:
    response = handler_request("POST", "/api/analyze", b"", content_length=MAX_REQUEST_BYTES + 1)
    assert response["status"] == 400
    assert "5 MB" in response["json"]["error"]


def test_analyze_workflow_failure_is_client_error(handler_request) -> None:
    with patch("cureflow_app.server.analyze_csv", side_effect=ApiInputError("分析失败：坏数据")):
        response = _post_json(
            handler_request,
            {
                "filename": "bad.csv",
                "csvText": "year,revenue,cost,users,orders,visits\n2025,1,1,1,1,1",
            },
        )
    assert response["status"] == 400
    assert response["json"]["error"] == "分析失败：坏数据"


def test_unknown_get(handler_request) -> None:
    response = handler_request("GET", "/api/missing")
    assert response["status"] == 404


def test_unknown_post(handler_request) -> None:
    response = handler_request("POST", "/api/missing", b"{}")
    assert response["status"] == 404


def test_analyze_has_six_named_metrics(handler_request) -> None:
    sample = handler_request("GET", "/api/sample")["json"]
    metrics = _post_json(handler_request, sample)["json"]["metrics"]
    assert [item["key"] for item in metrics] == [
        "revenue_growth", "profit_margin", "cost_ratio", "user_growth",
        "conversion_rate", "order_growth",
    ]


def test_analyze_has_five_successful_stages(handler_request) -> None:
    sample = handler_request("GET", "/api/sample")["json"]
    stages = _post_json(handler_request, sample)["json"]["stages"]
    assert [item["key"] for item in stages] == [
        "extract", "metrics", "claims", "sections", "merge",
    ]
    assert {item["status"] for item in stages} == {"success"}


def test_cors_defaults_to_dev_wildcard(handler_request) -> None:
    response = handler_request("GET", "/api/health")
    assert response["headers"]["Access-Control-Allow-Origin"] == "*"


def test_cors_restricted_origin_is_echoed(tmp_path: Path) -> None:
    handler_type = make_handler(tmp_path, "http://127.0.0.1:5173")
    handler = object.__new__(handler_type)
    handler.path = "/api/health"
    handler.headers = {"Content-Length": "0", "Origin": "http://127.0.0.1:5173"}
    handler.rfile = BytesIO()
    handler.wfile = BytesIO()
    headers: dict[str, str] = {}
    handler.send_response = lambda _status: None
    handler.send_header = lambda key, value: headers.__setitem__(key, value)
    handler.end_headers = lambda: None
    handler.do_GET()
    assert headers["Access-Control-Allow-Origin"] == "http://127.0.0.1:5173"
    assert headers["Vary"] == "Origin"
