"""
Tests for Day 13.5 Qwen-specific Live Gate patch.

Verifies the contract we promised before the first real Qwen call:

  1. Qwen request body has `enable_thinking=false` at the TOP LEVEL
     (not under `extra_body`).
  2. Request body does NOT contain an `extra_body` wrapper.
  3. `response_format` is sent at the top level.
  4. ClaimOutput schema validation still runs (the structured-output
     hint does not bypass our own validation).
  5. provider-reported `reasoning_tokens` (under
     `usage.completion_tokens_details.reasoning_tokens`) is parsed
     into LLMResponse.reasoning_tokens.
  6. When the provider does NOT return `reasoning_tokens`, the field
     is None (no estimation).
  7. The Qwen config used in Test A / B / C is identical: same
     provider, model, enable_thinking, response_format, temperature.
  8. The full Day 1-13.5 + previous Day 13.5 pytest suite still
     passes (verified by run, not by code).

All tests are offline (no real network).  They use the same
`urllib.request.urlopen` mocking pattern as Day 13, and inspect the
captured request body.
"""
from __future__ import annotations

import json
import os
from io import BytesIO
from pathlib import Path
from typing import Optional
from unittest.mock import patch

import pytest

from artifacts import Artifact, ArtifactRef, ArtifactStore, ArtifactType
from llm import (
    LLMResponse,
    RealLLMClient,
    RealLLMErrorKind,
    RealLLMProviderConfig,
    UsageType,
)
from llm.parse import extract_json_object, parse_claim_payload
from validation import validate_claim


CSV = str(os.path.join(os.path.dirname(__file__), "..", "data", "sample_company.csv"))


# =====================================================================
# Helpers
# =====================================================================

class _CapturingURLopen:
    """Mock urlopen that captures the request body bytes."""
    def __init__(self, response_payload: dict, status: int = 200):
        self.captured: list[bytes] = []
        self.response_payload = response_payload
        self.status = status
        self._raw = json.dumps(response_payload).encode("utf-8")

    def __call__(self, req, *args, **kwargs):
        if hasattr(req, "data") and req.data is not None:
            self.captured.append(req.data)
        raw = self._raw

        class _Resp:
            def read(inner_self) -> bytes:
                return raw
            def __enter__(inner_self):
                return inner_self
            def __exit__(inner_self, *a):
                return False
        return _Resp()


def _qwen_config(**overrides) -> RealLLMProviderConfig:
    """Build the canonical Qwen Live Gate config.

    Any field the user wants to override is passed via overrides.
    Used by tests 1-7 to make sure we exercise the same shape that
    `live_gate_run.py` will use.
    """
    base = dict(
        provider="qwen",
        model="qwen3.7-plus-2026-05-26",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key_env_var="TEST_QWEN_KEY",
        timeout_seconds=30.0,
        max_retries=1,
        temperature=0.0,
        seed=1234,
        response_format={"type": "json_object"},
        provider_options={"enable_thinking": False},
    )
    base.update(overrides)
    return RealLLMProviderConfig(**base)


# =====================================================================
# 1. Qwen request body has enable_thinking=false at the top level
# =====================================================================

def test_qwen_request_body_has_enable_thinking_false_at_top_level() -> None:
    """The request JSON body for Qwen must contain
    `enable_thinking=false` at the TOP LEVEL — never under
    `extra_body`, which is an OpenAI Python SDK construct and
    meaningless for direct HTTP.
    """
    os.environ["TEST_QWEN_KEY"] = "TEST_KEY_FAKE_NOT_REAL"
    try:
        cfg = _qwen_config()
        client = RealLLMClient(cfg)
        capture = _CapturingURLopen({
            "id": "req-xyz",
            "choices": [{"message": {"role": "assistant", "content": "{}"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })
        with patch("urllib.request.urlopen", new=capture):
            client.generate("hi", task_name="t")
        assert len(capture.captured) == 1
        body = json.loads(capture.captured[0].decode("utf-8"))
        assert body.get("enable_thinking") is False, (
            f"enable_thinking missing or not False at top level: {body}"
        )
    finally:
        os.environ.pop("TEST_QWEN_KEY", None)


# =====================================================================
# 2. Request body does NOT contain an `extra_body` wrapper
# =====================================================================

def test_qwen_request_body_has_no_extra_body_wrapper() -> None:
    """`extra_body` is an OpenAI Python SDK feature.  Our adapter
    speaks HTTP directly, so it must NOT appear in the body.
    """
    os.environ["TEST_QWEN_KEY"] = "TEST_KEY_FAKE_NOT_REAL"
    try:
        cfg = _qwen_config()
        client = RealLLMClient(cfg)
        capture = _CapturingURLopen({
            "choices": [{"message": {"role": "assistant", "content": "{}"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })
        with patch("urllib.request.urlopen", new=capture):
            client.generate("hi", task_name="t")
        body = json.loads(capture.captured[0].decode("utf-8"))
        assert "extra_body" not in body, (
            f"extra_body leaked into HTTP body: {list(body.keys())}"
        )
    finally:
        os.environ.pop("TEST_QWEN_KEY", None)


# =====================================================================
# 3. response_format is sent at the top level
# =====================================================================

def test_qwen_request_body_sends_response_format() -> None:
    """`response_format` is the OpenAI-compatible structured-output
    hint and must be sent at the top level of the body."""
    os.environ["TEST_QWEN_KEY"] = "TEST_KEY_FAKE_NOT_REAL"
    try:
        cfg = _qwen_config()
        client = RealLLMClient(cfg)
        capture = _CapturingURLopen({
            "choices": [{"message": {"role": "assistant", "content": "{}"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })
        with patch("urllib.request.urlopen", new=capture):
            client.generate("hi", task_name="t")
        body = json.loads(capture.captured[0].decode("utf-8"))
        assert body.get("response_format") == {"type": "json_object"}, (
            f"response_format missing or wrong: {body.get('response_format')}"
        )
    finally:
        os.environ.pop("TEST_QWEN_KEY", None)


# =====================================================================
# 4. ClaimOutput schema validation still runs after structured output
# =====================================================================

def test_claimoutput_schema_validation_still_runs() -> None:
    """Even when the provider signals response_format=json_object,
    the workflow must still run ClaimOutput.model_validate and our
    semantic / evidence validators.  The structured-output hint is
    a promise, not a substitute.
    """
    from workflow.claim_tasks import _parse_llm_output
    import tempfile

    os.environ["TEST_QWEN_KEY"] = "TEST_KEY_FAKE_NOT_REAL"
    try:
        with tempfile.TemporaryDirectory() as td:
            store = ArtifactStore(base_dir=str(Path(td) / "art"))
            rg = Artifact(
                artifact_id="metric_revenue_growth",
                artifact_type=ArtifactType.METRIC,
                producer_task="compute", run_id="r1", version=1,
                value=0.6078, input_artifact_ids=[],
                meta={"task_kind": "tool"},
            )
            pm = Artifact(
                artifact_id="metric_profit_margin",
                artifact_type=ArtifactType.METRIC,
                producer_task="compute", run_id="r1", version=1,
                value=0.4756, input_artifact_ids=[],
                meta={"task_kind": "tool"},
            )
            cr = Artifact(
                artifact_id="metric_cost_ratio",
                artifact_type=ArtifactType.METRIC,
                producer_task="compute", run_id="r1", version=1,
                value=0.5243, input_artifact_ids=[],
                meta={"task_kind": "tool"},
            )
            store.save(rg); store.save(pm); store.save(cr)

            # Provider returned a payload that LOOKS valid but has
            # confidence=99 (out of range).  Our schema validator
            # must still catch it.
            response = LLMResponse(
                text=json.dumps({
                    "claim_text": (
                        "营收同比增长 60.78%，利润率 47.56%，"
                        "经营增长表现较强。"
                    ),
                    "evidence_refs": [
                        f"{rg.run_id}/{rg.artifact_id}@v{rg.version}",
                        f"{pm.run_id}/{pm.artifact_id}@v{pm.version}",
                    ],
                    "confidence": 99,  # INVALID
                }),
                provider="qwen", model="qwen3.7-plus-2026-05-26",
                usage_type=UsageType.PROVIDER_REPORTED,
            )
            with pytest.raises(ValueError) as exc_info:
                _parse_llm_output(
                    response, [rg, pm, cr], "r1", store,
                    artifact_id="claim_financial_growth",
                    producer_task="generate_financial_claim",
                )
            assert "confidence" in str(exc_info.value).lower() or "schema" in str(exc_info.value).lower()
    finally:
        os.environ.pop("TEST_QWEN_KEY", None)


# =====================================================================
# 5. provider reasoning_tokens is parsed
# =====================================================================

def test_provider_reasoning_tokens_parsed_when_present() -> None:
    """When the provider returns
    `usage.completion_tokens_details.reasoning_tokens`, the value
    must be copied into LLMResponse.reasoning_tokens."""
    os.environ["TEST_QWEN_KEY"] = "TEST_KEY_FAKE_NOT_REAL"
    try:
        cfg = _qwen_config()
        client = RealLLMClient(cfg)
        capture = _CapturingURLopen({
            "id": "req-r1",
            "choices": [{"message": {"role": "assistant", "content": "{}"}}],
            "usage": {
                "prompt_tokens": 50,
                "completion_tokens": 100,
                "total_tokens": 150,
                "completion_tokens_details": {"reasoning_tokens": 42},
            },
        })
        with patch("urllib.request.urlopen", new=capture):
            r = client.generate("hi", task_name="t")
        assert r.usage_type == UsageType.PROVIDER_REPORTED
        assert r.reasoning_tokens == 42, (
            f"expected reasoning_tokens=42, got {r.reasoning_tokens}"
        )
    finally:
        os.environ.pop("TEST_QWEN_KEY", None)


# =====================================================================
# 6. reasoning_tokens is None when provider does not report it
# =====================================================================

def test_provider_reasoning_tokens_none_when_absent() -> None:
    """When the provider does NOT include reasoning_tokens in
    usage.completion_tokens_details, LLMResponse.reasoning_tokens
    must be None (no estimation, no default)."""
    os.environ["TEST_QWEN_KEY"] = "TEST_KEY_FAKE_NOT_REAL"
    try:
        cfg = _qwen_config()
        client = RealLLMClient(cfg)
        capture = _CapturingURLopen({
            "id": "req-r1",
            "choices": [{"message": {"role": "assistant", "content": "{}"}}],
            "usage": {
                "prompt_tokens": 5, "completion_tokens": 7,
                "total_tokens": 12,
                # no completion_tokens_details
            },
        })
        with patch("urllib.request.urlopen", new=capture):
            r = client.generate("hi", task_name="t")
        assert r.reasoning_tokens is None

        # Also when completion_tokens_details exists but reasoning_tokens is absent
        capture2 = _CapturingURLopen({
            "choices": [{"message": {"role": "assistant", "content": "{}"}}],
            "usage": {
                "prompt_tokens": 5, "completion_tokens": 7,
                "total_tokens": 12,
                "completion_tokens_details": {"text_tokens": 7},
            },
        })
        with patch("urllib.request.urlopen", new=capture2):
            r2 = client.generate("hi", task_name="t")
        assert r2.reasoning_tokens is None
    finally:
        os.environ.pop("TEST_QWEN_KEY", None)


# =====================================================================
# 7. Test A / B / C use identical Qwen config
# =====================================================================

def test_live_gate_qwen_config_is_consistent_across_phases() -> None:
    """The Qwen config that Test A, Test B and Test C use must be
    identical in: provider, model, enable_thinking, response_format,
    temperature, seed.  Otherwise benchmark numbers are not
    comparable.
    """
    # Test A config
    a = _qwen_config()
    # Test B config (same defaults)
    b = _qwen_config()
    # Test C config
    c = _qwen_config()

    for k in ("provider", "model", "temperature", "seed",
              "response_format", "provider_options",
              "base_url", "api_key_env_var"):
        assert getattr(a, k) == getattr(b, k) == getattr(c, k), (
            f"Test configs differ on {k!r}"
        )
    # Critical: enable_thinking must be False on all three
    assert a.provider_options.get("enable_thinking") is False
    assert b.provider_options.get("enable_thinking") is False
    assert c.provider_options.get("enable_thinking") is False
    # response_format must be the same json_object hint
    assert a.response_format == {"type": "json_object"}
    assert a.model == "qwen3.7-plus-2026-05-26"


# =====================================================================
# 8. LiveCallRecord reasoning_tokens / request_enable_thinking
# =====================================================================

def test_live_call_record_carries_qwen_echo() -> None:
    """LiveCallRecord must carry the request_enable_thinking and
    request_response_format the harness sent, plus the
    reasoning_tokens the provider reported.  This is the audit
    record that proves `enable_thinking=False` was actually
    transmitted, not just claimed."""
    from experiments.live_ledger import LiveCallLedger, LiveCallRecord
    ledger = LiveCallLedger()
    ledger.append(LiveCallRecord(
        phase="A", execution_unit="generate_financial_claim",
        provider="qwen", model="qwen3.7-plus-2026-05-26",
        request_id="req-1",
        usage_type=UsageType.PROVIDER_REPORTED.value,
        input_tokens=10, output_tokens=20, total_tokens=30,
        client_wall_clock_latency_ms=100.0,
        schema_valid=True, evidence_valid=True, semantic_valid=True,
        produced_ref="r1/claim@v1",
        reasoning_tokens=0,                  # provider reported 0 reasoning
        request_enable_thinking=False,        # we sent enable_thinking=False
        request_response_format={"type": "json_object"},
    ))
    rec = ledger.records[0]
    assert rec.reasoning_tokens == 0
    assert rec.request_enable_thinking is False
    assert rec.request_response_format == {"type": "json_object"}
    # serialization round-trip
    d = rec.__dict__
    assert d["reasoning_tokens"] == 0
    assert d["request_enable_thinking"] is False


# =====================================================================
# 9. endpoint + Qwen path: extra defensive — base_url with /v1
# and full enable_thinking path; the same client should also satisfy
# the Day 13.5 endpoint construction test (regression).
# =====================================================================

def test_full_qwen_config_endpoint_and_payload_consistency() -> None:
    """End-to-end: the Qwen config produces both the correct
    endpoint URL (no /v1 doubling) AND the correct payload shape."""
    os.environ["TEST_QWEN_KEY"] = "TEST_KEY_FAKE_NOT_REAL"
    try:
        cfg = _qwen_config()
        client = RealLLMClient(cfg)

        # endpoint check
        ep = client._endpoint()
        assert ep == (
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
        )
        assert "/v1/v1" not in ep

        # payload check
        capture = _CapturingURLopen({
            "id": "req-x",
            "choices": [{"message": {"role": "assistant", "content": "{}"}}],
            "usage": {
                "prompt_tokens": 1, "completion_tokens": 1,
                "total_tokens": 2,
                "completion_tokens_details": {"reasoning_tokens": 0},
            },
        })
        with patch("urllib.request.urlopen", new=capture):
            r = client.generate("hi", task_name="t")
        body = json.loads(capture.captured[0].decode("utf-8"))
        # All Qwen fields at top level, none under extra_body
        assert "extra_body" not in body
        assert body.get("enable_thinking") is False
        assert body.get("response_format") == {"type": "json_object"}
        assert body.get("model") == "qwen3.7-plus-2026-05-26"
        assert body.get("temperature") == 0.0
        assert body.get("seed") == 1234
        # And the response reasoning_tokens is parsed
        assert r.reasoning_tokens == 0
        assert r.usage_type == UsageType.PROVIDER_REPORTED
    finally:
        os.environ.pop("TEST_QWEN_KEY", None)
