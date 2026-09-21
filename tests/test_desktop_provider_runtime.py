"""Offline coverage for the Desktop OpenAI opt-in gate and local audit."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm import LLMResponse, UsageType
from llm.fake import default_workflow_llm
from cureflow_app.server import _analyze_csv_with_provider
from cureflow_app.provider_runtime import (
    DEEPSEEK_BASE_URL,
    DEEPSEEK_KEY_ENV,
    DEEPSEEK_LIVE_CONFIRM_ENV,
    DEEPSEEK_MODE,
    DEEPSEEK_MODEL,
    LIVE_CONFIRM_ENV,
    MAX_DESKTOP_LIVE_CALLS,
    OPENAI_KEY_ENV,
    OPENAI_MODE,
    QWEN_BASE_URL,
    QWEN_KEY_ENV,
    QWEN_LIVE_CONFIRM_ENV,
    QWEN_MODE,
    QWEN_MODEL,
    CallBudgetLLMClient,
    DesktopProviderError,
    DesktopProviderRuntime,
    ProviderRun,
    estimated_list_price_usd,
    write_private_live_audit,
)


class StubOpenAIClient:
    def generate(self, prompt: str, *, task_name: str = "") -> LLMResponse:
        del prompt, task_name
        return LLMResponse(
            text=json.dumps({
                "claim_text": "用户增长约为50%。",
                "evidence_refs": ["run/metric_user_growth@v1"],
                "confidence": 0.9,
            }, ensure_ascii=False),
            input_tokens=20,
            output_tokens=10,
            latency_ms=12.5,
            provider="openai",
            model="gpt-5.6-luna",
            usage_type=UsageType.PROVIDER_REPORTED,
            request_id="resp_desktop_test",
        )


class OfflineOpenAIShapeWorkflowClient:
    """Six valid Responses-shaped records with no network implementation."""

    def __init__(self) -> None:
        self.delegate = default_workflow_llm()
        self.calls = 0

    def generate(self, prompt: str, *, task_name: str = "") -> LLMResponse:
        self.calls += 1
        response = self.delegate.generate(prompt, task_name=task_name)
        return response.model_copy(update={
            "provider": "openai",
            "model": "gpt-5.6-luna",
            "usage_type": UsageType.PROVIDER_REPORTED,
            "request_id": f"resp_desktop_{self.calls}",
        })


class OfflineProviderErrorClient:
    def generate(self, prompt: str, *, task_name: str = "") -> LLMResponse:
        del prompt, task_name
        return LLMResponse(
            text=json.dumps({"error": "NETWORK_ERROR", "message": "offline test"}),
            latency_ms=5.0,
            provider="openai",
            model="gpt-5.6-luna",
            usage_type=UsageType.UNAVAILABLE,
        )


@pytest.fixture(autouse=True)
def clean_live_environment(monkeypatch):
    monkeypatch.delenv(OPENAI_KEY_ENV, raising=False)
    monkeypatch.delenv(LIVE_CONFIRM_ENV, raising=False)
    monkeypatch.delenv(QWEN_KEY_ENV, raising=False)
    monkeypatch.delenv(QWEN_LIVE_CONFIRM_ENV, raising=False)
    monkeypatch.delenv(DEEPSEEK_KEY_ENV, raising=False)
    monkeypatch.delenv(DEEPSEEK_LIVE_CONFIRM_ENV, raising=False)


def test_default_status_is_offline_and_secret_free() -> None:
    status = DesktopProviderRuntime().status()
    encoded = json.dumps(status)
    assert status["defaultMode"] == "OFFLINE_DETERMINISTIC_FIXTURE"
    assert status["openai"]["ready"] is False
    assert status["openai"]["apiKeyValueExposed"] is False
    assert "sk-" not in encoded


def test_status_exposes_three_selectable_cloud_providers_without_secrets() -> None:
    status = DesktopProviderRuntime().status()
    assert status["openai"]["mode"] == OPENAI_MODE
    assert status["qwen"]["mode"] == QWEN_MODE
    assert status["deepseek"]["mode"] == DEEPSEEK_MODE
    assert all(
        status[name]["apiKeyValueExposed"] is False
        for name in ("openai", "qwen", "deepseek")
    )


@pytest.mark.parametrize(
    ("mode", "key_env", "confirm_env", "provider", "model", "base_url"),
    [
        (
            QWEN_MODE, QWEN_KEY_ENV, QWEN_LIVE_CONFIRM_ENV,
            "qwen", QWEN_MODEL, QWEN_BASE_URL,
        ),
        (
            DEEPSEEK_MODE, DEEPSEEK_KEY_ENV, DEEPSEEK_LIVE_CONFIRM_ENV,
            "deepseek", DEEPSEEK_MODEL, DEEPSEEK_BASE_URL,
        ),
    ],
)
def test_qwen_and_deepseek_build_existing_zero_retry_adapter_without_network(
    monkeypatch, mode, key_env, confirm_env, provider, model, base_url,
) -> None:
    monkeypatch.setenv(key_env, "local-test-secret")
    monkeypatch.setenv(confirm_env, "1")
    resolved = DesktopProviderRuntime().resolve(
        mode=mode,
        confirm_live=True,
        confirm_max_calls=MAX_DESKTOP_LIVE_CALLS,
    )
    config = resolved.client._delegate.config
    assert resolved.provider == provider
    assert resolved.model == model
    assert resolved.call_budget == 6
    assert config.base_url == base_url
    assert config.max_retries == 0
    assert config.api_key_env_var == key_env


def test_qwen_keeps_tested_structured_output_contract(monkeypatch) -> None:
    monkeypatch.setenv(QWEN_KEY_ENV, "local-test-secret")
    monkeypatch.setenv(QWEN_LIVE_CONFIRM_ENV, "1")
    resolved = DesktopProviderRuntime().resolve(
        mode=QWEN_MODE,
        confirm_live=True,
        confirm_max_calls=MAX_DESKTOP_LIVE_CALLS,
    )
    config = resolved.client._delegate.config
    assert config.response_format == {"type": "json_object"}
    assert config.provider_options == {"enable_thinking": False}


def test_deepseek_enables_official_json_object_mode(monkeypatch) -> None:
    monkeypatch.setenv(DEEPSEEK_KEY_ENV, "local-test-secret")
    monkeypatch.setenv(DEEPSEEK_LIVE_CONFIRM_ENV, "1")
    resolved = DesktopProviderRuntime().resolve(
        mode=DEEPSEEK_MODE,
        confirm_live=True,
        confirm_max_calls=MAX_DESKTOP_LIVE_CALLS,
    )
    config = resolved.client._delegate.config
    assert config.response_format == {"type": "json_object"}
    assert config.max_retries == 0


@pytest.mark.parametrize(
    ("mode", "key_env", "confirm_env", "provider"),
    [
        (QWEN_MODE, QWEN_KEY_ENV, QWEN_LIVE_CONFIRM_ENV, "qwen"),
        (
            DEEPSEEK_MODE, DEEPSEEK_KEY_ENV,
            DEEPSEEK_LIVE_CONFIRM_ENV, "deepseek",
        ),
    ],
)
def test_each_cloud_provider_requires_its_own_local_authorization(
    monkeypatch, mode, key_env, confirm_env, provider,
) -> None:
    monkeypatch.setenv(key_env, "local-test-secret")
    with pytest.raises(DesktopProviderError, match=f"未启用 {provider}"):
        DesktopProviderRuntime().resolve(
            mode=mode,
            confirm_live=True,
            confirm_max_calls=MAX_DESKTOP_LIVE_CALLS,
        )
    monkeypatch.setenv(confirm_env, "1")
    assert DesktopProviderRuntime().status()[provider]["ready"] is True


def test_desktop_budget_is_full_workflow_not_three_scenario_pilot() -> None:
    # 3 claims + 3 report sections.  OPENAI_AB_PILOT_V1 remains separately
    # capped at three isolated scenarios and must not drive this constant.
    assert MAX_DESKTOP_LIVE_CALLS == 3 + 3 == 6


def test_environment_alone_never_changes_default(monkeypatch) -> None:
    monkeypatch.setenv(OPENAI_KEY_ENV, "local-test-secret")
    monkeypatch.setenv(LIVE_CONFIRM_ENV, "1")
    resolved = DesktopProviderRuntime().resolve()
    assert resolved.live is False
    assert resolved.mode == "OFFLINE_DETERMINISTIC_FIXTURE"


@pytest.mark.parametrize(
    ("confirm_live", "confirm_calls", "message"),
    [
        (False, MAX_DESKTOP_LIVE_CALLS, "明确确认"),
        (True, MAX_DESKTOP_LIVE_CALLS - 1, "最多 6 次"),
    ],
)
def test_live_selection_needs_request_confirmations(
    monkeypatch, confirm_live, confirm_calls, message
) -> None:
    monkeypatch.setenv(OPENAI_KEY_ENV, "local-test-secret")
    monkeypatch.setenv(LIVE_CONFIRM_ENV, "1")
    with pytest.raises(DesktopProviderError, match=message):
        DesktopProviderRuntime().resolve(
            mode=OPENAI_MODE,
            confirm_live=confirm_live,
            confirm_max_calls=confirm_calls,
        )


def test_live_selection_needs_local_enable_and_key(monkeypatch) -> None:
    runtime = DesktopProviderRuntime()
    with pytest.raises(DesktopProviderError, match="本地环境未启用"):
        runtime.resolve(
            mode=OPENAI_MODE, confirm_live=True,
            confirm_max_calls=MAX_DESKTOP_LIVE_CALLS,
        )
    monkeypatch.setenv(LIVE_CONFIRM_ENV, "1")
    with pytest.raises(DesktopProviderError, match="未配置"):
        runtime.resolve(
            mode=OPENAI_MODE, confirm_live=True,
            confirm_max_calls=MAX_DESKTOP_LIVE_CALLS,
        )


def test_live_selection_builds_zero_retry_bounded_client(monkeypatch) -> None:
    monkeypatch.setenv(OPENAI_KEY_ENV, "local-test-secret")
    monkeypatch.setenv(LIVE_CONFIRM_ENV, "1")
    resolved = DesktopProviderRuntime().resolve(
        mode=OPENAI_MODE,
        confirm_live=True,
        confirm_max_calls=MAX_DESKTOP_LIVE_CALLS,
    )
    assert resolved.live is True
    assert isinstance(resolved.client, CallBudgetLLMClient)
    assert resolved.call_budget == 6
    assert resolved.client._delegate.config.max_retries == 0


def test_live_selection_attaches_observable_task_router(monkeypatch) -> None:
    monkeypatch.setenv(OPENAI_KEY_ENV, "local-test-secret")
    monkeypatch.setenv(LIVE_CONFIRM_ENV, "1")
    resolved = DesktopProviderRuntime().resolve(
        mode=OPENAI_MODE,
        confirm_live=True,
        confirm_max_calls=MAX_DESKTOP_LIVE_CALLS,
    )
    assert resolved.router_client is not None
    snapshot = resolved.router_client.snapshot()
    assert snapshot["policy"] == "task_aware_authorized_provider_v1"
    assert snapshot["automaticFallback"] is False
    assert snapshot["silentProviderSwitch"] is False


def test_call_budget_blocks_before_extra_provider_call() -> None:
    bounded = CallBudgetLLMClient(StubOpenAIClient(), 1)
    bounded.generate("first")
    with pytest.raises(DesktopProviderError, match="达到 1 次上限"):
        bounded.generate("second")
    assert bounded.calls_started == 1
    assert len(bounded.responses) == 1


def test_kill_switch_blocks_future_live_runs(monkeypatch) -> None:
    monkeypatch.setenv(OPENAI_KEY_ENV, "local-test-secret")
    monkeypatch.setenv(LIVE_CONFIRM_ENV, "1")
    runtime = DesktopProviderRuntime()
    status = runtime.kill()
    assert status["openai"]["killSwitchActive"] is True
    assert status["openai"]["killSwitchScope"] == "future_runs_only"
    with pytest.raises(DesktopProviderError, match="停止开关"):
        runtime.resolve(
            mode=OPENAI_MODE, confirm_live=True,
            confirm_max_calls=MAX_DESKTOP_LIVE_CALLS,
        )


def test_private_audit_uses_provider_usage_and_never_serializes_key(
    tmp_path, monkeypatch
) -> None:
    secret = "local-test-secret"
    monkeypatch.setenv(OPENAI_KEY_ENV, secret)
    bounded = CallBudgetLLMClient(StubOpenAIClient(), 6)
    bounded.generate("test")
    provider_run = ProviderRun(
        mode="OPENAI_RESPONSES_LIVE", provider="openai",
        model="gpt-5.6-luna", live=True, client=bounded,
        call_budget=6, budget_client=bounded,
    )
    target = write_private_live_audit(
        tmp_path, run_id="desktop_test", provider_run=provider_run,
        totals={"inputTokens": 20, "outputTokens": 10},
    )
    assert target is not None
    text = target.read_text(encoding="utf-8")
    payload = json.loads(text)
    assert secret not in text
    assert payload["usageType"] == "provider_reported"
    assert payload["calls"][0]["protocolAudit"]["accepted"] is True
    assert payload["automaticRetries"] == 0
    assert payload["store"] is False


def test_estimated_price_is_labelled_snapshot_math() -> None:
    assert estimated_list_price_usd(1_000_000, 1_000_000) == 1.4


def test_live_desktop_path_closes_response_and_private_audit_loop(
    tmp_path,
) -> None:
    shaped = OfflineOpenAIShapeWorkflowClient()
    bounded = CallBudgetLLMClient(shaped, MAX_DESKTOP_LIVE_CALLS)
    provider_run = ProviderRun(
        mode="OPENAI_RESPONSES_LIVE", provider="openai",
        model="gpt-5.6-luna", live=True, client=bounded,
        call_budget=MAX_DESKTOP_LIVE_CALLS, budget_client=bounded,
    )
    sample = Path(__file__).resolve().parents[1] / "data" / "sample_company.csv"
    result = _analyze_csv_with_provider(
        sample.read_text(encoding="utf-8"), sample.name, tmp_path, provider_run
    )
    assert result["mode"] == "OPENAI_RESPONSES_LIVE"
    assert result["provider"] == {
        "name": "openai",
        "model": "gpt-5.6-luna",
        "live": True,
        "usageType": "provider_reported",
        "callBudget": 6,
        "store": False,
        "auditStoredLocally": True,
    }
    assert result["cost"]["llmCalls"] == 6
    assert result["cost"]["estimatedListPriceUsd"] > 0
    assert bounded.calls_started == 6
    audits = list((tmp_path / "openai_live_audit").glob("*.json"))
    assert len(audits) == 1
    audit = json.loads(audits[0].read_text(encoding="utf-8"))
    assert audit["callsStarted"] == 6
    assert audit["workflowSucceeded"] is True
    assert all(row["protocolAudit"]["accepted"] for row in audit["calls"])


def test_failed_live_workflow_still_writes_private_call_audit(tmp_path) -> None:
    bounded = CallBudgetLLMClient(OfflineProviderErrorClient(), 6)
    provider_run = ProviderRun(
        mode="OPENAI_RESPONSES_LIVE", provider="openai",
        model="gpt-5.6-luna", live=True, client=bounded,
        call_budget=6, budget_client=bounded,
    )
    sample = Path(__file__).resolve().parents[1] / "data" / "sample_company.csv"
    with pytest.raises(ValueError, match="分析失败"):
        _analyze_csv_with_provider(
            sample.read_text(encoding="utf-8"), sample.name, tmp_path,
            provider_run,
        )
    audits = list((tmp_path / "openai_live_audit").glob("*.json"))
    assert len(audits) == 1
    audit = json.loads(audits[0].read_text(encoding="utf-8"))
    assert audit["workflowSucceeded"] is False
    assert audit["callsStarted"] == 2
    assert audit["totals"]["usageType"] == "unavailable"
    assert audit["totals"]["estimatedListPriceUsd"] is None
