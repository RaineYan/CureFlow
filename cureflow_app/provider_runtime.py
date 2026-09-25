"""Desktop-only provider gate for CureFlow.

The product default stays deterministic and offline.  Each cloud provider is
available only when three independent signals agree: the renderer explicitly
selects it for one run, its local live-confirmation environment flag is set,
and its API key exists in the backend process environment. Secrets are never
part of analysis/audit payloads or serialized configuration. The optional
loopback setup endpoint only populates the current process environment.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

from llm import RealLLMClient, RealLLMProviderConfig
from llm.base import LLMClient, LLMResponse, UsageType
from llm.openai_audit import audit_openai_response
from llm.openai_responses import OpenAIResponsesConfig
from llm.provider_selection import (
    ProviderMode,
    ProviderSelection,
    build_llm_client,
)
from llm.task_router import TaskAwareLLMClient
from llm.adapters.doubao import DoubaoClient


OFFLINE_MODE = "offline"
OPENAI_MODE = ProviderMode.OPENAI_RESPONSES.value
OPENAI_MODEL = "gpt-5.6-luna"
OPENAI_KEY_ENV = "OPENAI_API_KEY"
LIVE_CONFIRM_ENV = "CUREFLOW_ENABLE_OPENAI_LIVE"
QWEN_MODE = "qwen_chat"
QWEN_MODEL = "qwen3.7-plus-2026-05-26"
QWEN_KEY_ENV = "QWEN_API_KEY"
QWEN_LIVE_CONFIRM_ENV = "CUREFLOW_ENABLE_QWEN_LIVE"
QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEEPSEEK_MODE = "deepseek_chat"
DEEPSEEK_MODEL = "deepseek-v4-flash"
DEEPSEEK_KEY_ENV = "DEEPSEEK_API_KEY"
DEEPSEEK_LIVE_CONFIRM_ENV = "CUREFLOW_ENABLE_DEEPSEEK_LIVE"
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DOUBAO_MODE = "doubao_chat"
# User-confirmed Ark model ID from the console's actual REST example; this is
# model selection only, not evidence of a configured key or a verified call.
DOUBAO_MODEL = "doubao-seed-2-0-pro-260215"
DOUBAO_MODEL_ENV = "DOUBAO_MODEL"
DOUBAO_KEY_ENV = "DOUBAO_API_KEY"
DOUBAO_LIVE_CONFIRM_ENV = "CUREFLOW_ENABLE_DOUBAO_LIVE"
# A complete Desktop workflow has six LLM execution units: three branch
# claims plus three report sections.  This product-run ceiling is therefore
# 6.  It is intentionally separate from OPENAI_AB_PILOT_V1's 3-call ceiling,
# which covers three isolated evaluation scenarios rather than a full
# workflow.  Neither limit may be inferred from or silently change the other.
MAX_DESKTOP_LIVE_CALLS = 6

# Public list-price snapshot used only for a labelled estimate.  It is not a
# provider bill and must be refreshed deliberately when pricing changes.
PRICE_SNAPSHOT = {
    "asOf": "2026-09-15",
    "inputUsdPerMillionTokens": 0.20,
    "outputUsdPerMillionTokens": 1.20,
    "source": "https://developers.openai.com/api/docs/models/gpt-5.6-luna",
}


class DesktopProviderError(ValueError):
    """Safe provider-selection error suitable for the local UI."""


class CallBudgetLLMClient:
    """Enforce a hard per-run call ceiling and retain secret-free metadata."""

    def __init__(self, delegate: LLMClient, max_calls: int):
        self._delegate = delegate
        self.max_calls = max_calls
        self.calls_started = 0
        self.responses: list[LLMResponse] = []
        self._lock = Lock()

    def generate(self, prompt: str, *, task_name: str = "") -> LLMResponse:
        with self._lock:
            if self.calls_started >= self.max_calls:
                raise DesktopProviderError(
                    f"本次云模型调用已达到 {self.max_calls} 次上限"
                )
            self.calls_started += 1
        response = self._delegate.generate(prompt, task_name=task_name)
        with self._lock:
            self.responses.append(response)
        return response


@dataclass(frozen=True)
class ProviderRun:
    mode: str
    provider: str
    model: str
    live: bool
    client: LLMClient
    call_budget: int
    budget_client: CallBudgetLLMClient | None = None
    router_client: TaskAwareLLMClient | None = None


class DesktopProviderRuntime:
    """In-process live gate and kill switch for future desktop runs."""

    def __init__(self) -> None:
        self._killed = False
        self._verified_providers: set[str] = set()
        self._last_errors: dict[str, str] = {}
        self._persisted_providers: set[str] = set()
        self._persistence_opted_in = False
        self._persistence_error: str | None = None
        self._lock = Lock()

    @property
    def killed(self) -> bool:
        with self._lock:
            return self._killed

    def kill(self) -> dict[str, Any]:
        """Block future cloud runs until the backend process restarts."""
        with self._lock:
            self._killed = True
        return self.status()

    def clear_verification(self, provider: str) -> None:
        with self._lock:
            self._verified_providers.discard(provider)
            self._last_errors.pop(provider, None)

    def record_connection_error(self, provider: str, message: str) -> None:
        if provider in {"openai", "qwen", "deepseek", "doubao"}:
            with self._lock:
                self._verified_providers.discard(provider)
                self._last_errors[provider] = message

    def set_persistence(self, opted_in: bool, providers: set[str]) -> None:
        with self._lock:
            self._persistence_opted_in = opted_in
            self._persisted_providers = set(providers)
            self._persistence_error = None

    def set_persistence_error(self, message: str) -> None:
        with self._lock:
            self._persistence_error = message

    def mark_verified(self, provider: str) -> None:
        with self._lock:
            self._verified_providers.add(provider)
            self._last_errors.pop(provider, None)

    def is_verified(self, provider: str) -> bool:
        with self._lock:
            return provider in self._verified_providers

    def status(self) -> dict[str, Any]:
        def cloud_status(
            *, provider: str, mode: str, model: str, key_env: str, confirm_env: str,
        ) -> dict[str, Any]:
            local_confirmed = os.environ.get(confirm_env) == "1"
            key_present = bool(os.environ.get(key_env))
            blockers: list[str] = []
            if self.killed:
                blockers.append("KILL_SWITCH_ACTIVE")
            if not local_confirmed:
                blockers.append("LOCAL_LIVE_CONFIRMATION_MISSING")
            if not key_present:
                blockers.append("API_KEY_ENV_MISSING")
            connection_verified = self.is_verified(provider)
            with self._lock:
                last_error = self._last_errors.get(provider)
                persisted = provider in self._persisted_providers
                opted_in = self._persistence_opted_in
            if provider == "doubao" and not model:
                blockers.append("MODEL_ID_MISSING")
            if not blockers and not connection_verified:
                blockers.append("PROVIDER_CONNECTION_UNVERIFIED")
            return {
                "mode": mode,
                "model": model,
                "ready": not blockers,
                "maxCallsPerRun": MAX_DESKTOP_LIVE_CALLS,
                "automaticRetries": 0,
                "store": False,
                "killSwitchActive": self.killed,
                "killSwitchScope": "future_runs_only",
                "blockers": blockers,
                "apiKeyPresent": key_present,
                "connectionVerified": connection_verified,
                "lastError": last_error,
                "persistence": {"optedIn": opted_in, "stored": persisted},
                "apiKeyValueExposed": False,
            }

        openai = cloud_status(
            provider="openai",
            mode=OPENAI_MODE,
            model=OPENAI_MODEL,
            key_env=OPENAI_KEY_ENV,
            confirm_env=LIVE_CONFIRM_ENV,
        )
        openai["priceSnapshot"] = PRICE_SNAPSHOT
        qwen = cloud_status(
            provider="qwen",
            mode=QWEN_MODE,
            model=QWEN_MODEL,
            key_env=QWEN_KEY_ENV,
            confirm_env=QWEN_LIVE_CONFIRM_ENV,
        )
        deepseek = cloud_status(
            provider="deepseek",
            mode=DEEPSEEK_MODE,
            model=DEEPSEEK_MODEL,
            key_env=DEEPSEEK_KEY_ENV,
            confirm_env=DEEPSEEK_LIVE_CONFIRM_ENV,
        )
        doubao = cloud_status(
            provider="doubao", mode=DOUBAO_MODE,
            model=os.environ.get(DOUBAO_MODEL_ENV) or DOUBAO_MODEL,
            key_env=DOUBAO_KEY_ENV, confirm_env=DOUBAO_LIVE_CONFIRM_ENV,
        )
        unavailable_price = {"available": False, "reason": "未配置可信目录价；不估算费用"}
        for state in (qwen, deepseek, doubao):
            state["priceSnapshot"] = unavailable_price.copy()
        return {
            "defaultMode": "OFFLINE_DETERMINISTIC_FIXTURE",
            "persistence": {"optedIn": self._persistence_opted_in, "lastError": self._persistence_error},
            "offline": {
                "mode": OFFLINE_MODE,
                "model": "fake-deterministic",
                "ready": True,
                "live": False,
            },
            "openai": openai,
            "qwen": qwen,
            "deepseek": deepseek,
            "doubao": doubao,
        }

    def resolve(
        self,
        *,
        mode: str = OFFLINE_MODE,
        confirm_live: bool = False,
        confirm_max_calls: int | None = None,
    ) -> ProviderRun:
        if mode == OFFLINE_MODE:
            routed = TaskAwareLLMClient(
                build_llm_client(),
                provider="fake",
                model="fake-deterministic",
            )
            return ProviderRun(
                mode="OFFLINE_DETERMINISTIC_FIXTURE",
                provider="fake",
                model="fake-deterministic",
                live=False,
                client=routed,
                call_budget=0,
                router_client=routed,
            )
        provider_specs = {
            OPENAI_MODE: {
                "provider": "openai", "model": OPENAI_MODEL,
                "key_env": OPENAI_KEY_ENV, "confirm_env": LIVE_CONFIRM_ENV,
            },
            QWEN_MODE: {
                "provider": "qwen", "model": QWEN_MODEL,
                "key_env": QWEN_KEY_ENV, "confirm_env": QWEN_LIVE_CONFIRM_ENV,
                "base_url": QWEN_BASE_URL,
            },
            DEEPSEEK_MODE: {
                "provider": "deepseek", "model": DEEPSEEK_MODEL,
                "key_env": DEEPSEEK_KEY_ENV,
                "confirm_env": DEEPSEEK_LIVE_CONFIRM_ENV,
                "base_url": DEEPSEEK_BASE_URL,
            },
            DOUBAO_MODE: {
                "provider": "doubao", "model": os.environ.get(DOUBAO_MODEL_ENV) or DOUBAO_MODEL,
                "key_env": DOUBAO_KEY_ENV,
                "confirm_env": DOUBAO_LIVE_CONFIRM_ENV,
                "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            },
        }
        if mode not in provider_specs:
            raise DesktopProviderError(f"不支持的模型模式：{mode}")
        spec = provider_specs[mode]
        if self.killed:
            raise DesktopProviderError("云模型停止开关已启用；重启本地引擎后可恢复")
        if not confirm_live:
            raise DesktopProviderError("必须在界面中明确确认本次云模型运行")
        if confirm_max_calls != MAX_DESKTOP_LIVE_CALLS:
            raise DesktopProviderError(
                f"必须明确确认本次最多 {MAX_DESKTOP_LIVE_CALLS} 次调用"
            )
        if os.environ.get(spec["confirm_env"]) != "1":
            raise DesktopProviderError(
                f"本地环境未启用 {spec['provider']} live 模式"
            )
        if not os.environ.get(spec["key_env"]):
            raise DesktopProviderError(
                f"本地运行环境未配置 {spec['provider']} API key"
            )
        if mode == DOUBAO_MODE and not spec["model"]:
            raise DesktopProviderError("请先填写已开通的火山方舟模型或接入点 ID")

        if mode == OPENAI_MODE:
            client = build_llm_client(
                ProviderSelection(
                    mode=ProviderMode.OPENAI_RESPONSES,
                    allow_live=True,
                    openai=OpenAIResponsesConfig(
                        model=OPENAI_MODEL,
                        api_key_env_var=OPENAI_KEY_ENV,
                        max_retries=0,
                    ),
                )
            )
        elif mode == DOUBAO_MODE:
            client = DoubaoClient(model=spec["model"])
        else:
            client = RealLLMClient(RealLLMProviderConfig(
                provider=spec["provider"],
                model=spec["model"],
                base_url=spec["base_url"],
                api_key_env_var=spec["key_env"],
                timeout_seconds=60.0,
                max_retries=0,
                temperature=0.0,
                seed=1234,
                response_format=(
                    {"type": "json_object"}
                    if mode in {QWEN_MODE, DEEPSEEK_MODE} else None
                ),
                provider_options=(
                    {"enable_thinking": False} if mode == QWEN_MODE else {}
                ),
            ))
        routed = TaskAwareLLMClient(
            client,
            provider=spec["provider"],
            model=spec["model"],
        )
        bounded = CallBudgetLLMClient(routed, MAX_DESKTOP_LIVE_CALLS)
        return ProviderRun(
            mode=f"{mode.upper()}_LIVE",
            provider=spec["provider"],
            model=spec["model"],
            live=True,
            client=bounded,
            call_budget=MAX_DESKTOP_LIVE_CALLS,
            budget_client=bounded,
            router_client=routed,
        )


def estimated_list_price_usd(input_tokens: int, output_tokens: int) -> float:
    value = (
        input_tokens * PRICE_SNAPSHOT["inputUsdPerMillionTokens"]
        + output_tokens * PRICE_SNAPSHOT["outputUsdPerMillionTokens"]
    ) / 1_000_000
    return round(value, 10)


def write_private_live_audit(
    audit_dir: Path,
    *,
    run_id: str,
    provider_run: ProviderRun,
    totals: dict[str, Any],
) -> Path | None:
    """Persist provider metadata locally; never serialize credentials."""
    if not provider_run.live or provider_run.budget_client is None:
        return None
    rows = []
    for index, response in enumerate(provider_run.budget_client.responses, 1):
        if provider_run.provider == "openai":
            protocol = audit_openai_response(
                response, expected_model=provider_run.model
            ).to_dict()
        else:
            reason_codes: list[str] = []
            if response.provider != provider_run.provider:
                reason_codes.append("PROVIDER_IDENTITY_MISMATCH")
            if response.model != provider_run.model:
                reason_codes.append("MODEL_IDENTITY_MISMATCH")
            if not response.text:
                reason_codes.append("EMPTY_RESPONSE")
            if not response.request_id:
                reason_codes.append("REQUEST_ID_MISSING")
            if response.usage_type != UsageType.PROVIDER_REPORTED:
                reason_codes.append("PROVIDER_USAGE_UNAVAILABLE")
            protocol = {
                "protocol": "openai_compatible_chat_v1",
                "expectedModel": provider_run.model,
                "providerIdentityVerified": (
                    response.provider == provider_run.provider
                ),
                "modelIdentityVerified": response.model == provider_run.model,
                "responseReceived": bool(response.text),
                "requestIdVerified": bool(response.request_id),
                "usageReported": (
                    response.usage_type == UsageType.PROVIDER_REPORTED
                ),
                "accepted": not reason_codes,
                "reasonCodes": reason_codes,
            }
        rows.append({
            "callIndex": index,
            "provider": response.provider,
            "model": response.model,
            "requestId": response.request_id,
            "usageType": response.usage_type.value,
            "inputTokens": response.input_tokens,
            "outputTokens": response.output_tokens,
            "latencyMs": response.latency_ms,
            "protocolAudit": protocol,
        })
    payload = {
        "runId": run_id,
        "workflowSucceeded": totals.get("workflowSucceeded"),
        "mode": provider_run.mode,
        "provider": provider_run.provider,
        "model": provider_run.model,
        "callBudget": provider_run.call_budget,
        "callsStarted": provider_run.budget_client.calls_started,
        "automaticRetries": 0,
        "store": False,
        "usageType": (
            UsageType.PROVIDER_REPORTED.value
            if rows and all(r["usageType"] == UsageType.PROVIDER_REPORTED.value for r in rows)
            else UsageType.UNAVAILABLE.value
        ),
        "totals": totals,
        "calls": rows,
        "apiKeyValueExposed": False,
    }
    if provider_run.router_client is not None:
        payload["taskRouting"] = provider_run.router_client.snapshot()
    audit_dir.mkdir(parents=True, exist_ok=True)
    target = audit_dir / f"{run_id}.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target
