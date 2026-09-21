"""OpenAI Responses API adapter for the opt-in CureFlow cloud pilot.

This module is deliberately separate from ``llm.providers.real``.  The
existing Chat Completions adapter and its evidence hashes stay unchanged.
Offline ``FakeLLMClient`` also remains the product default.

Security and evidence rules:

* the API key is read only from an environment variable;
* the request always sets ``store`` to ``False``;
* provider token usage is copied, never estimated;
* the native ``resp_*`` response id is preserved without rewriting it to
  the legacy ``chatcmpl-*`` convention;
* network/provider failures become the existing structured error envelope.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from .base import LLMClient, LLMResponse, UsageType
from .providers.real import RealLLMError, RealLLMErrorKind


OPENAI_RESPONSES_ENDPOINT = "https://api.openai.com/v1/responses"

CLAIM_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "claim_text": {"type": "string", "minLength": 1},
        "evidence_refs": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["claim_text", "evidence_refs", "confidence"],
    "additionalProperties": False,
}


class OpenAIResponsesConfig(BaseModel):
    """Serializable, secret-free configuration for the OpenAI pilot."""

    model_config = ConfigDict(extra="forbid")

    model: str = "gpt-5.6-luna"
    api_key_env_var: str = "OPENAI_API_KEY"
    timeout_seconds: float = Field(default=60.0, gt=0, le=300)
    max_retries: int = Field(default=1, ge=0, le=1)
    max_output_tokens: int = Field(default=1200, ge=64, le=128_000)
    reasoning_effort: Literal["none", "low", "medium", "high"] = "none"

    def has_api_key(self) -> bool:
        return bool(os.environ.get(self.api_key_env_var))


def is_openai_response_id(value: Optional[str]) -> bool:
    """Identify the native Responses API id without changing old gates."""

    return bool(value and value.startswith("resp_"))


def _error_kind_for_status(status: int) -> RealLLMErrorKind:
    if status in (401, 403):
        return RealLLMErrorKind.AUTH_ERROR
    if status in (408, 504):
        return RealLLMErrorKind.TIMEOUT
    if status == 429:
        return RealLLMErrorKind.RATE_LIMIT
    if 500 <= status < 600:
        return RealLLMErrorKind.NETWORK_ERROR
    return RealLLMErrorKind.INVALID_RESPONSE


def _output_text(payload: dict) -> str:
    """Extract HTTP Responses output; do not assume output[0] is text."""

    for item in payload.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if not isinstance(content, dict):
                continue
            if content.get("type") == "output_text" and isinstance(
                content.get("text"), str
            ):
                return content["text"]
    raise ValueError("response has no message output_text item")


def _usage_int(usage: dict, key: str) -> int:
    raw = usage.get(key, 0)
    if isinstance(raw, bool):
        raise ValueError(f"usage.{key} is not an integer")
    value = int(raw or 0)
    if value < 0:
        raise ValueError(f"usage.{key} is negative")
    return value


class OpenAIResponsesClient(LLMClient):
    """Synchronous, stdlib-only client for ``POST /v1/responses``."""

    endpoint = OPENAI_RESPONSES_ENDPOINT

    def __init__(self, config: Optional[OpenAIResponsesConfig] = None):
        self.config = config or OpenAIResponsesConfig()
        if not self.config.has_api_key():
            raise RealLLMError(
                RealLLMErrorKind.AUTH_ERROR,
                f"api key env var {self.config.api_key_env_var!r} is not set",
            )
        self._api_key = os.environ[self.config.api_key_env_var]

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def model_name(self) -> str:
        return self.config.model

    def _request_body(self, prompt: str) -> dict:
        return {
            "model": self.config.model,
            "instructions": (
                "Return only data matching the supplied JSON schema. "
                "Use only evidence references present in the user input."
            ),
            "input": prompt,
            "store": False,
            "max_output_tokens": self.config.max_output_tokens,
            "reasoning": {"effort": self.config.reasoning_effort},
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "cureflow_claim",
                    "strict": True,
                    "schema": CLAIM_OUTPUT_SCHEMA,
                }
            },
        }

    def _post_once(self, body: dict) -> dict:
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "User-Agent": "cureflow-openai-pilot/1.0",
            },
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.config.timeout_seconds
            ) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = ""
            try:
                raw = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            # A provider must never be able to echo the credential into logs.
            safe = raw.replace(self._api_key, "<redacted>")[:200]
            raise RealLLMError(
                _error_kind_for_status(exc.code),
                f"HTTP {exc.code}: {safe}",
                status_code=exc.code,
            ) from exc
        except urllib.error.URLError as exc:
            reason = str(getattr(exc, "reason", exc)).replace(
                self._api_key, "<redacted>"
            )
            kind = (
                RealLLMErrorKind.TIMEOUT
                if "timed out" in reason.lower()
                else RealLLMErrorKind.NETWORK_ERROR
            )
            raise RealLLMError(kind, f"network error: {reason}") from exc
        except (TimeoutError, ConnectionError) as exc:
            raise RealLLMError(
                RealLLMErrorKind.TIMEOUT, "connection timed out"
            ) from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RealLLMError(
                RealLLMErrorKind.INVALID_RESPONSE,
                f"response body is not valid UTF-8 JSON: {type(exc).__name__}",
            ) from exc

    def _post_with_retry(self, body: dict) -> dict:
        last_error: Optional[RealLLMError] = None
        for attempt in range(self.config.max_retries + 1):
            try:
                return self._post_once(body)
            except RealLLMError as exc:
                last_error = exc
                transient = exc.kind in {
                    RealLLMErrorKind.NETWORK_ERROR,
                    RealLLMErrorKind.TIMEOUT,
                    RealLLMErrorKind.RATE_LIMIT,
                }
                if not transient or attempt >= self.config.max_retries:
                    raise
                time.sleep(0.5 * (attempt + 1))
        assert last_error is not None
        raise last_error

    def _error_response(
        self, error: RealLLMError, elapsed_ms: float
    ) -> LLMResponse:
        safe_message = error.message.replace(self._api_key, "<redacted>")
        return LLMResponse(
            text=json.dumps(
                {"error": error.kind.value, "message": safe_message},
                ensure_ascii=False,
            ),
            latency_ms=elapsed_ms,
            provider="openai",
            model=self.config.model,
            usage_type=UsageType.UNAVAILABLE,
        )

    def generate(self, prompt: str, *, task_name: str = "") -> LLMResponse:  # type: ignore[override]
        del task_name  # task identity is already included in CureFlow's prompt
        started = time.perf_counter()
        try:
            payload = self._post_with_retry(self._request_body(prompt))
        except RealLLMError as exc:
            return self._error_response(
                exc, (time.perf_counter() - started) * 1000.0
            )
        elapsed_ms = (time.perf_counter() - started) * 1000.0

        if not isinstance(payload, dict):
            return self._error_response(
                RealLLMError(
                    RealLLMErrorKind.INVALID_RESPONSE,
                    "response body is not a JSON object",
                ),
                elapsed_ms,
            )

        request_id = payload.get("id") if isinstance(payload.get("id"), str) else None
        try:
            if payload.get("status") not in (None, "completed"):
                raise ValueError(f"response status is {payload.get('status')!r}")
            text = _output_text(payload)
            usage = payload.get("usage") or {}
            if not isinstance(usage, dict):
                raise ValueError("usage is not an object")
            input_tokens = _usage_int(usage, "input_tokens")
            output_tokens = _usage_int(usage, "output_tokens")
            details = usage.get("output_tokens_details") or {}
            if not isinstance(details, dict):
                raise ValueError("usage.output_tokens_details is not an object")
            raw_reasoning = details.get("reasoning_tokens")
            reasoning_tokens = (
                _usage_int(details, "reasoning_tokens")
                if raw_reasoning is not None
                else None
            )
        except (OverflowError, TypeError, ValueError) as exc:
            return self._error_response(
                RealLLMError(
                    RealLLMErrorKind.INVALID_RESPONSE, str(exc)
                ),
                elapsed_ms,
            ).model_copy(update={"request_id": request_id})

        return LLMResponse(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            monetary_usd=0.0,
            latency_ms=elapsed_ms,
            provider="openai",
            model=self.config.model,
            usage_type=(
                UsageType.PROVIDER_REPORTED
                if payload.get("usage")
                else UsageType.UNAVAILABLE
            ),
            request_id=request_id,
        )
