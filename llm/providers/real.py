"""
RealLLMClient — Day 13 + Day 13.5.

Day 13: an HTTP chat-completions adapter.  Uses the standard library
(`urllib`) so the POC has no third-party HTTP dependency.

Day 13.5: the usage flag is now a 3-state enum `UsageType`:

    provider returned `usage`     -> UsageType.PROVIDER_REPORTED
    provider did NOT return usage  -> UsageType.UNAVAILABLE  (NOT synthetic!)
    (synthetic only applies to FakeLLMClient)

UNAUTHENTICATED / NETWORK errors do not produce an LLMResponse at all;
they raise a `RealLLMError` so the workflow can fail cleanly.  HTTP
non-2xx (401, 429, 5xx) is caught and converted into a structured
error response so a single failed call does not crash the workflow.
The error response carries `usage_type=UNAVAILABLE` (it is a real
provider call that did not return a usable payload).

Provider compatibility:

This module does NOT claim that the OpenAI-compatible chat-completions
schema works for every provider that markets itself as "OpenAI-compatible".
The fields used here (messages, temperature, usage.prompt_tokens,
usage.completion_tokens, usage.total_tokens, choices[0].message.content,
id) are the OpenAI reference schema.  Other providers may differ in:

  - auth header scheme (Bearer vs. custom)
  - system-message field name
  - usage field names
  - SSE vs. non-streaming defaults
  - thinking / reasoning token accounting

If you target a provider other than the one this adapter was tested
against, verify the schema first.  Do not assume the same payload
just because `base_url` is set.

Hard rules (enforced in the code):
  - api_key is read from os.environ[api_key_env] ONLY.  Never
    serialized, never logged in full (only the last 4 chars appear
    in error messages, via `_redact_key`).
  - usage_type reflects the provider's response:
      * has usage block    -> PROVIDER_REPORTED
      * no usage block     -> UNAVAILABLE
  - input_tokens / output_tokens come from the provider's usage
    block, never from character counts.
  - Errors are classified into RealLLMErrorKind so downstream code
    can decide whether to retry.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from enum import Enum
from typing import Optional

from pydantic import BaseModel

from ..base import LLMClient, LLMResponse, RealLLMProviderConfig, UsageType


class RealLLMErrorKind(str, Enum):
    AUTH_ERROR = "AUTH_ERROR"
    TIMEOUT = "TIMEOUT"
    RATE_LIMIT = "RATE_LIMIT"
    NETWORK_ERROR = "NETWORK_ERROR"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    SCHEMA_ERROR = "SCHEMA_ERROR"


class RealLLMError(Exception):
    def __init__(self, kind: RealLLMErrorKind, message: str, status_code: int = 0):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.status_code = status_code


def _redact_key(key: str) -> str:
    """Return a redacted form of the api key, safe for logging."""
    if not key:
        return "<empty>"
    if len(key) <= 4:
        return "****"
    return f"***{key[-4:]}"


def _classify_http_error(status: int, body: str) -> RealLLMErrorKind:
    if status in (401, 403):
        return RealLLMErrorKind.AUTH_ERROR
    if status == 408:
        return RealLLMErrorKind.TIMEOUT
    if status == 429:
        return RealLLMErrorKind.RATE_LIMIT
    if 500 <= status < 600:
        return RealLLMErrorKind.NETWORK_ERROR
    return RealLLMErrorKind.INVALID_RESPONSE


def _auth_header(key: str) -> str:
    """Build the Authorization header value.  Currently a Bearer
    token because that is the OpenAI reference contract; do not
    assume the same scheme for other providers."""
    return f"Bearer {key}"


class RealLLMClient(LLMClient):
    """HTTP chat-completions adapter.

    Scope: this class is a single, named, tested integration.  It is
    NOT a generic OpenAI-compatible wrapper for every LLM provider.

    Example:
        cfg = RealLLMProviderConfig(
            provider="openai",
            model="gpt-4o-mini",
            api_key_env_var="OPENAI_API_KEY",
        )
        client = RealLLMClient(cfg)
        resp = client.generate("Hello")
    """

    def __init__(self, config: RealLLMProviderConfig):
        self.config = config
        if not config.has_api_key():
            raise RealLLMError(
                RealLLMErrorKind.AUTH_ERROR,
                f"api key env var {config.api_key_env_var!r} is not set",
            )
        self._api_key = os.environ[config.api_key_env_var]

    @property
    def provider_name(self) -> str:
        return self.config.provider

    @property
    def model_name(self) -> str:
        return self.config.model

    def _endpoint(self) -> str:
        """Resolve the final HTTP endpoint URL.

        Accepts three forms of `base_url` (so the same client works
        for SDK-style and HTTP-style configuration):

        1. Full HTTP endpoint already includes `/chat/completions`
             e.g. `https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions`
             -> used as-is.
        2. SDK-style base_url ends with `/v1` (or `/v1/`)
             e.g. `https://dashscope.aliyuncs.com/compatible-mode/v1`
             -> `/chat/completions` is appended.
        3. Bare host (no `/v1` at all)
             e.g. `https://api.openai.com`
             -> `/v1/chat/completions` is appended.

        This prevents the previous bug where Qwen's SDK-style
        base_url (with `/v1`) caused `/v1` to be appended twice.
        """
        base = (self.config.base_url or "https://api.openai.com").rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        if base.endswith("/v1"):
            return f"{base}/chat/completions"
        return f"{base}/v1/chat/completions"

    def _post(self, body: dict) -> dict:
        url = self._endpoint()
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": _auth_header(self._api_key),
                "User-Agent": "poc-day13/1.0",
            },
        )
        try:
            with urllib.request.urlopen(
                req, timeout=self.config.timeout_seconds,
            ) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            kind = _classify_http_error(e.code, body)
            raise RealLLMError(
                kind, f"HTTP {e.code}: {body[:200]}", status_code=e.code,
            ) from e
        except urllib.error.URLError as e:
            reason = str(e.reason) if hasattr(e, "reason") else str(e)
            if "timed out" in reason.lower():
                raise RealLLMError(
                    RealLLMErrorKind.TIMEOUT, f"request timed out: {reason}",
                ) from e
            raise RealLLMError(
                RealLLMErrorKind.NETWORK_ERROR, f"network error: {reason}",
            ) from e
        except (TimeoutError, ConnectionError) as e:
            raise RealLLMError(
                RealLLMErrorKind.TIMEOUT, f"connection error: {e}",
            ) from e

    def _post_with_retry(self, body: dict) -> dict:
        """At most max_retries additional attempts on transient errors.
        Day 13 / 13.5: max_retries <= 1 to keep live smoke-test cost bounded."""
        attempts = 0
        last_err: Optional[RealLLMError] = None
        while attempts <= self.config.max_retries:
            try:
                return self._post(body)
            except RealLLMError as e:
                last_err = e
                if e.kind in (
                    RealLLMErrorKind.NETWORK_ERROR,
                    RealLLMErrorKind.TIMEOUT,
                    RealLLMErrorKind.RATE_LIMIT,
                ):
                    attempts += 1
                    if attempts > self.config.max_retries:
                        break
                    # brief backoff
                    time.sleep(0.5 * attempts)
                    continue
                raise
        assert last_err is not None
        raise last_err

    def _error_response(
        self, error_kind: str, message: str, elapsed: float,
        request_id: Optional[str] = None,
    ) -> LLMResponse:
        """Build a structured error response for an HTTP/parse failure
        that did not yield a usable payload.  usage_type is UNAVAILABLE
        because no usage block was returned; usage_is_synthetic stays
        False because this is a real provider call that failed."""
        return LLMResponse(
            text=json.dumps({
                "error": error_kind,
                "message": message,
            }),
            input_tokens=0,
            output_tokens=0,
            monetary_usd=0.0,
            latency_ms=elapsed,
            provider=self.config.provider,
            model=self.config.model,
            usage_type=UsageType.UNAVAILABLE,
            request_id=request_id,
        )

    def generate(self, prompt: str, *, task_name: str = "") -> LLMResponse:  # type: ignore[override]
        """Synchronous LLM call.  Returns an LLMResponse whose
        usage_type is:
            PROVIDER_REPORTED — provider returned a `usage` block
            UNAVAILABLE        — provider did not return usage,
                                 or the call errored out
        Note: a real provider call that errored out is still UNAVAILABLE,
        NOT SYNTHETIC.  Only FakeLLMClient is SYNTHETIC.
        """
        body = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": (
                    "You are a deterministic JSON-only assistant.  "
                    "Respond with a single JSON object, no markdown, "
                    "no commentary.  The object must conform to the "
                    "claim_text / evidence_refs / confidence schema."
                )},
                {"role": "user", "content": prompt},
            ],
            "temperature": self.config.temperature,
        }
        if self.config.seed is not None:
            body["seed"] = self.config.seed
        if self.config.response_format is not None:
            body["response_format"] = self.config.response_format
        # Provider-specific body fields go at the TOP LEVEL of the
        # request body (direct HTTP, NOT OpenAI Python SDK extra_body).
        # Documented for Qwen as:
        #   enable_thinking, thinking_budget, reasoning_effort, etc.
        for k, v in self.config.provider_options.items():
            if k in body:
                # Don't silently overwrite; the explicit field wins.
                continue
            body[k] = v
        start = time.perf_counter()
        try:
            payload = self._post_with_retry(body)
        except RealLLMError as e:
            elapsed = (time.perf_counter() - start) * 1000.0
            return self._error_response(
                e.kind.value, e.message, elapsed, request_id=None,
            )
        elapsed = (time.perf_counter() - start) * 1000.0

        # Parse the response.  Any structural problem becomes an
        # INVALID_RESPONSE error in the returned LLMResponse
        # (workflow can decide to retry / fail / log).
        try:
            choice = payload["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            return self._error_response(
                RealLLMErrorKind.INVALID_RESPONSE.value,
                f"response missing 'choices[0].message.content': {e}",
                elapsed,
                request_id=payload.get("id"),
            )

        usage = payload.get("usage") or {}
        in_tok = int(usage.get("prompt_tokens", 0) or 0)
        out_tok = int(usage.get("completion_tokens", 0) or 0)
        total_tok = int(usage.get("total_tokens", in_tok + out_tok) or 0)
        request_id = (
            payload.get("id")
            or (payload.get("headers") or {}).get("x-request-id")
        )

        # Day 13.5: read provider-reported reasoning_tokens if the
        # provider included it.  We do NOT infer / estimate.  None
        # when absent.
        completion_details = usage.get("completion_tokens_details") or {}
        raw_reasoning = completion_details.get("reasoning_tokens")
        reasoning_tokens = (
            int(raw_reasoning) if raw_reasoning is not None else None
        )

        # Day 13.5: 3-state usage_type.  Missing usage -> UNAVAILABLE,
        # present usage -> PROVIDER_REPORTED.  This is the SINGLE
        # source of truth for whether tokens are real.
        usage_type = (
            UsageType.PROVIDER_REPORTED if usage else UsageType.UNAVAILABLE
        )

        return LLMResponse(
            text=content,
            input_tokens=in_tok,
            output_tokens=out_tok,
            reasoning_tokens=reasoning_tokens,
            monetary_usd=0.0,             # not billed at this layer
            latency_ms=elapsed,          # client-side request wall clock
            provider=self.config.provider,
            model=self.config.model,
            usage_type=usage_type,
            request_id=request_id,
        )
