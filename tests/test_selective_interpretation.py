"""Offline regression tests: real evidence artifacts, no provider networking."""
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from artifacts import Artifact, ArtifactType, ArtifactStore, ArtifactStatus
from cureflow_app.provider_runtime import ProviderRun
from cureflow_app.server import _interpret_generic_with_provider, ApiInputError, make_handler
from llm.base import LLMResponse, UsageType

FIRST = "desktop_profile_20261019T100000Z_aaaaaaaa"
SECOND = "desktop_profile_20261019T100001Z_bbbbbbbb"
THIRD = "desktop_profile_20261019T100002Z_cccccccc"


class EvidenceClient:
    def __init__(self, *, invalid=False, unavailable_usage=False, revoke=None):
        self.prompts = []
        self.invalid = invalid
        self.unavailable_usage = unavailable_usage
        self.revoke = revoke

    def generate(self, prompt, *, task_name=""):
        self.prompts.append(prompt)
        packet = json.loads(prompt.rsplit("<UNTRUSTED_EVIDENCE>", 1)[1].split("</UNTRUSTED_EVIDENCE>", 1)[0])
        tools = packet["analysisTools"]["executedTools"]
        selected = [t for key in ("distribution_profile", "quality_audit")
                    for t in tools if t["key"] == key]
        if not selected:
            selected = tools[:1]
        rows = [{"title": t["key"], "explanation": f"当前 {t['key']} 的统计需要复核。",
                 "confidence": "medium", "evidenceRefs": [t["evidenceRef"]]} for t in selected]
        if self.invalid:
            rows[0]["evidenceRefs"] = ["fabricated/ref@v1"]
        if self.revoke:
            self.revoke()
        return LLMResponse(
            text=json.dumps({"summary": "OLD_GLOBAL_SUMMARY", "findings": rows,
                             "actions": [{"action": "核对 " + r["title"], "reason": "需要人工核实。",
                                          "evidenceRefs": r["evidenceRefs"]} for r in rows],
                             "limitations": ["OLD_UNSCOPED_LIMITATION"]}),
            input_tokens=700 if len(tools) > 1 else 250, output_tokens=80,
            usage_type=UsageType.UNAVAILABLE if self.unavailable_usage else UsageType.PROVIDER_REPORTED,
            provider="doubao", model="configured-model", latency_ms=12,
        )


def provider(client, model="configured-model"):
    return ProviderRun(mode="DOUBAO_CHAT_LIVE", provider="doubao", model=model,
                       live=True, client=client, call_budget=6)


def packet_for(data_dir, run=FIRST, previous=None, changed=True):
    store = ArtifactStore(base_dir=str(data_dir / "artifacts"))
    tools = []
    for key in ("distribution_profile", "quality_audit"):
        if previous and (key == "distribution_profile" or not changed):
            tools.append(deepcopy(next(t for t in previous["analysisTools"]["executedTools"] if t["key"] == key)))
            continue
        artifact_id = "analysis_tool_" + key
        payload = {"count": 20, "missingCount": 1 if run == FIRST else 0}
        artifact = store.save(Artifact(artifact_id=artifact_id, artifact_type=ArtifactType.FIELD,
                                       producer_task=key, run_id=run, value=payload))
        tools.append({"key": key, "status": "success", "label": key,
                      "payload": payload, "evidenceRef": f"{run}/{artifact_id}@v{artifact.version}"})
    return {"metrics": [], "dataSufficiency": "adequate", "methodPack": {},
            "analysisTools": {"goal": "data_quality", "goalLabel": "数据检查", "analysisConfig": {},
                              "summary": {"rowCount": 20, "columnCount": 4}, "executedTools": tools}}


def result_for(run, source=None):
    result = {"runId": run, "metrics": [], "report": "本地报告", "groundingSummary": {},
              "analysisTools": {"executedTools": []}}
    if source:
        result["recoveryExecution"] = {"sourceRunId": source, "sourceInputFingerprint": "a" * 64}
    return result


def interpret(data_dir, result, packet, client, *, model="configured-model", brief=""):
    result["analysisTools"] = deepcopy(packet["analysisTools"])
    with patch("cureflow_app.server._provider_evidence_packet", return_value=packet):
        return _interpret_generic_with_provider(result, provider(client, model), data_dir,
                                                 brief, input_fingerprint="a" * 64)


def initial(data_dir):
    packet = packet_for(data_dir)
    client = EvidenceClient()
    result = interpret(data_dir, result_for(FIRST), packet, client)
    assert len(client.prompts) == 1
    return result, packet


def test_partial_sends_only_changed_evidence_and_revalidates_merged_items(tmp_path):
    original, first_packet = initial(tmp_path)
    packet = packet_for(tmp_path, SECOND, first_packet)
    client = EvidenceClient()
    recovered = interpret(tmp_path, result_for(SECOND, FIRST), packet, client)
    assert len(client.prompts) == 1
    assert "analysis_tool_distribution_profile" not in client.prompts[0]
    assert "OLD_GLOBAL_SUMMARY" not in client.prompts[0]
    ai = recovered["aiInterpretation"]
    assert len(ai["findings"]) == 2
    assert ai["findings"][0]["explanation"] == original["aiInterpretation"]["findings"][0]["explanation"]
    assert ai["findings"][0]["executionStatus"] == "reused"
    assert ai["findings"][1]["evidenceRefs"][0].startswith(SECOND)
    assert "OLD_GLOBAL_SUMMARY" not in ai["summary"]
    assert "OLD_UNSCOPED_LIMITATION" not in ai["limitations"]
    assert recovered["cost"]["inputTokens"] == 250
    assert recovered["aiRecovery"]["reusedFindingCount"] == 1
    assert recovered["aiRecovery"]["reusedActionCount"] == 1
    assert recovered["aiRecovery"]["sentEvidenceBytes"] < recovered["aiRecovery"]["fullEvidenceBytes"]
    assert recovered["aiRecovery"]["tokenSavingsPct"] is None


def test_all_unchanged_uses_zero_calls_and_does_not_rebill_historical_tokens(tmp_path):
    _, first_packet = initial(tmp_path)
    packet = packet_for(tmp_path, SECOND, first_packet, changed=False)
    client = EvidenceClient()
    recovered = interpret(tmp_path, result_for(SECOND, FIRST), packet, client)
    assert client.prompts == []
    assert recovered["aiRecovery"]["mode"] == "reuse_all"
    assert recovered["cost"]["llmCalls"] == recovered["cost"]["totalTokens"] == 0
    assert recovered["provider"]["calledThisRun"] is False
    audit = json.loads((tmp_path / "doubao_live_audit" / f"{SECOND}.json").read_text(encoding="utf-8"))
    assert audit["callsStarted"] == 0
    assert audit["requestSucceeded"] is None
    assert audit["aiRecovery"]["reusedFindingCount"] == 2


@pytest.mark.parametrize("scenario", ["missing", "corrupt", "checksum", "fingerprint", "model", "brief", "reviewed"])
def test_untrusted_or_incompatible_source_falls_back_to_full(tmp_path, scenario):
    _, first_packet = initial(tmp_path)
    path = tmp_path / "ai_dependency_manifests" / f"{FIRST}.json"
    result = result_for(SECOND, FIRST)
    options = {}
    if scenario == "missing":
        path.unlink()
    elif scenario == "corrupt":
        path.write_text("broken", encoding="utf-8")
    elif scenario == "checksum":
        value = json.loads(path.read_text(encoding="utf-8"))
        value["interpretation"]["findings"][0]["explanation"] = "tampered"
        path.write_text(json.dumps(value), encoding="utf-8")
    elif scenario == "fingerprint":
        result["recoveryExecution"]["sourceInputFingerprint"] = "b" * 64
    elif scenario in {"model", "brief"}:
        options[scenario] = "different"
    else:
        review = tmp_path / "interpretation_reviews" / f"{FIRST}.json"
        review.parent.mkdir(exist_ok=True)
        review.write_text(json.dumps({"revision": 1}), encoding="utf-8")
    packet = packet_for(tmp_path, SECOND, first_packet)
    client = EvidenceClient()
    recovered = interpret(tmp_path, result, packet, client, **options)
    assert len(client.prompts) == 1
    assert "analysis_tool_distribution_profile" in client.prompts[0]
    assert recovered["aiRecovery"]["mode"] == "full"
    assert recovered["aiRecovery"]["reusedFindingCount"] == 0


def test_changed_artifact_contents_or_status_cannot_reuse_old_text(tmp_path):
    _, first_packet = initial(tmp_path)
    store = ArtifactStore(base_dir=str(tmp_path / "artifacts"))
    store.reload()
    store.update_status(FIRST, "analysis_tool_distribution_profile", ArtifactStatus.INVALIDATED)
    packet = packet_for(tmp_path, SECOND, first_packet)
    client = EvidenceClient()
    with pytest.raises(ApiInputError, match="证据依赖核验失败"):
        interpret(tmp_path, result_for(SECOND, FIRST), packet, client)
    assert not client.prompts


def test_fabricated_delta_reference_is_rejected_without_retry(tmp_path):
    _, first_packet = initial(tmp_path)
    packet = packet_for(tmp_path, SECOND, first_packet)
    client = EvidenceClient(invalid=True)
    with pytest.raises(ApiInputError, match="不存在的证据"):
        interpret(tmp_path, result_for(SECOND, FIRST), packet, client)
    assert len(client.prompts) == 1
    assert not (tmp_path / "ai_dependency_manifests" / f"{SECOND}.json").exists()


def test_delta_cannot_cite_unchanged_evidence_hidden_from_request(tmp_path):
    _, first_packet = initial(tmp_path)
    packet = packet_for(tmp_path, SECOND, first_packet)
    unchanged = first_packet["analysisTools"]["executedTools"][0]["evidenceRef"]
    class CrossScopeClient(EvidenceClient):
        def generate(self, prompt, *, task_name=""):
            response = super().generate(prompt, task_name=task_name)
            value = json.loads(response.text)
            value["findings"][0]["evidenceRefs"] = [unchanged]
            return response.model_copy(update={"text": json.dumps(value)})
    client = CrossScopeClient()
    with pytest.raises(ApiInputError, match="不存在的证据"):
        interpret(tmp_path, result_for(SECOND, FIRST), packet, client)
    assert len(client.prompts) == 1


def test_method_assumptions_change_forces_full_interpretation(tmp_path):
    _, first_packet = initial(tmp_path)
    packet = packet_for(tmp_path, SECOND, first_packet)
    packet["methodPack"] = {"methods": [{"id": "different", "readiness": "blocked"}]}
    client = EvidenceClient()
    result = interpret(tmp_path, result_for(SECOND, FIRST), packet, client)
    assert result["aiRecovery"]["mode"] == "full"
    assert "analysis_tool_distribution_profile" in client.prompts[0]


def test_evidence_revoked_during_generation_blocks_merge(tmp_path):
    _, first_packet = initial(tmp_path)
    packet = packet_for(tmp_path, SECOND, first_packet)
    def revoke():
        store = ArtifactStore(base_dir=str(tmp_path / "artifacts"))
        store.reload()
        store.update_status(FIRST, "analysis_tool_distribution_profile", ArtifactStatus.INVALIDATED)
    client = EvidenceClient(revoke=revoke)
    with pytest.raises(ApiInputError, match="增量合并未通过"):
        interpret(tmp_path, result_for(SECOND, FIRST), packet, client)
    assert len(client.prompts) == 1


def test_second_correction_uses_latest_merged_manifest(tmp_path):
    _, first_packet = initial(tmp_path)
    second_packet = packet_for(tmp_path, SECOND, first_packet)
    interpret(tmp_path, result_for(SECOND, FIRST), second_packet, EvidenceClient())
    third_packet = packet_for(tmp_path, THIRD, second_packet)
    client = EvidenceClient()
    result = interpret(tmp_path, result_for(THIRD, SECOND), third_packet, client)
    assert result["aiRecovery"]["sourceRunId"] == SECOND
    assert result["aiInterpretation"]["findings"][0]["reusedFromRunId"] == SECOND
    assert result["aiInterpretation"]["findings"][1]["evidenceRefs"][0].startswith(THIRD)
    assert "analysis_tool_distribution_profile" not in client.prompts[0]


def test_missing_usage_is_not_reported_as_measured_token_savings(tmp_path):
    _, first_packet = initial(tmp_path)
    packet = packet_for(tmp_path, SECOND, first_packet)
    result = interpret(tmp_path, result_for(SECOND, FIRST), packet, EvidenceClient(unavailable_usage=True))
    assert result["cost"]["usageType"] == "unavailable"
    assert result["aiRecovery"]["tokenSavingsPct"] is None
    assert result["aiRecovery"]["fullRerunComparisonAvailable"] is False


def test_cache_stores_no_raw_rows_and_binds_provider_model(tmp_path):
    initial(tmp_path)
    text = (tmp_path / "ai_dependency_manifests" / f"{FIRST}.json").read_text(encoding="utf-8")
    saved = json.loads(text)
    assert saved["inputFingerprint"] == "a" * 64
    assert "snapshots" in saved
    assert "rawRows" not in saved
    assert "apiKey" not in text
    assert "payload" not in saved


@pytest.fixture
def api_request(tmp_path):
    handler_type = make_handler(tmp_path)
    def invoke(path, payload):
        body = json.dumps(payload).encode()
        handler = object.__new__(handler_type)
        handler.path = path
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile, handler.wfile = BytesIO(body), BytesIO()
        status = []
        handler.send_response = status.append
        handler.send_header = lambda *args: None
        handler.end_headers = lambda: None
        handler.do_POST()
        return status[0], json.loads(handler.wfile.getvalue())
    return invoke


def test_http_corrected_file_reuses_unaffected_ai_findings(api_request):
    source = "编号,组别,数值,备注\n" + "\n".join(
        f"R{i},{'甲' if i < 5 else '乙'},{i}, {'待核对' if i == 1 else '正常'} " for i in range(1, 9)) + "\n"
    client = EvidenceClient()
    with patch("cureflow_app.server.DesktopProviderRuntime.resolve", return_value=provider(client)):
        status, first = api_request("/api/analyze", {"filename": "source.csv", "csvText": source,
            "analysisGoal": "exploratory_analysis", "providerMode": "doubao_chat", "confirmLive": True, "confirmMaxCalls": 6})
        assert status == 200, first
        issue = next(i for i in first["repairPlan"]["issues"] if i["type"] == "trim_whitespace")
        status, corrected = api_request("/api/repair/export", {"filename": "source.csv", "csvText": source,
            "sourceRunId": first["runId"], "inputFingerprint": first["input"]["inputFingerprint"],
            "approvedFixIds": [issue["id"]], "manualCorrections": []})
        assert status == 200, corrected
        status, second = api_request("/api/analyze", {"filename": corrected["filename"], "csvText": corrected["csvText"],
            "analysisGoal": "exploratory_analysis", "providerMode": "doubao_chat", "confirmLive": True, "confirmMaxCalls": 6,
            "recoveryContext": {"sourceRunId": first["runId"], "sourceInputFingerprint": first["input"]["inputFingerprint"],
                                "appliedIssueIds": [issue["id"]]}})
    assert status == 200, second
    assert len(client.prompts) == 2
    assert second["aiRecovery"]["mode"] == "partial", second["aiRecovery"]
    assert second["aiRecovery"]["reusedFindingCount"] == 1
    assert "analysis_tool_distribution_profile" not in client.prompts[-1]
    assert second["recoveryExecution"]["providerRegenerated"] is True
