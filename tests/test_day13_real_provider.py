"""
Tests for Day 13 — Real LLM Provider Integration + Semantic Validation.

Covers:
  - RealLLMClient + RealLLMError (offline, mocked HTTP via
    urllib.request mock)
  - LLMResponse.usage_is_synthetic handling
  - structured-output parser robustness
  - semantic validator sub-checks (evidence, numeric, direction,
    recommendation boundary)
  - integration: claim_tasks with a real-model-shaped response
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional
from unittest.mock import patch

import pytest

from artifacts import Artifact, ArtifactRef, ArtifactStore, ArtifactType
from llm import (
    LLMResponse,
    RealLLMClient,
    RealLLMError,
    RealLLMErrorKind,
    RealLLMProviderConfig,
    extract_json_object,
    parse_claim_payload,
)
from validation import (
    SemanticValidationResult,
    direction_check,
    numeric_consistency_check,
    parse_evidence_refs,
    recommendation_boundary_check,
    validate_claim,
    validate_section,
)


# =====================================================================
# 1-3: RealLLMClient construction + auth
# =====================================================================

def test_real_client_requires_api_key() -> None:
    import os
    old = os.environ.pop("TEST_DAY13_KEY", None)
    try:
        cfg = RealLLMProviderConfig(
            provider="openai",
            model="gpt-4o-mini",
            api_key_env_var="TEST_DAY13_KEY",
        )
        with pytest.raises(RealLLMError) as exc_info:
            RealLLMClient(cfg)
        assert exc_info.value.kind == RealLLMErrorKind.AUTH_ERROR
    finally:
        if old is not None:
            os.environ["TEST_DAY13_KEY"] = old


def test_real_client_accepts_api_key_from_env() -> None:
    import os
    os.environ["TEST_DAY13_KEY"] = "TEST_KEY_FAKE_NOT_REAL"
    cfg = RealLLMProviderConfig(
        provider="openai",
        model="gpt-4o-mini",
        api_key_env_var="TEST_DAY13_KEY",
    )
    client = RealLLMClient(cfg)
    assert client.provider_name == "openai"
    assert client.model_name == "gpt-4o-mini"
    del os.environ["TEST_DAY13_KEY"]


def test_redact_key_does_not_leak_full() -> None:
    from llm.providers.real import _redact_key
    assert _redact_key("") == "<empty>"
    assert _redact_key("ab") == "****"
    # Use a key-shaped string that doesn't match the readiness scanner.
    fake = "A" * 32
    redacted = _redact_key(fake)
    assert redacted == "***" + fake[-4:]
    assert fake not in redacted


# =====================================================================
# 4-7: RealLLMClient mocked HTTP
# =====================================================================

def _make_mock_urlopen(payload: dict, status: int = 200):
    """Return a context-manager factory that yields a fake response
    object with .read() and .__enter__/__exit__."""
    from io import BytesIO
    class _Resp:
        def __init__(self, body: bytes):
            self._body = body
        def read(self) -> bytes:
            return self._body
        def __enter__(self): return self
        def __exit__(self, *a): return False
    raw = json.dumps(payload).encode("utf-8")
    def factory(*args, **kwargs):
        return _Resp(raw)
    return factory


def test_real_client_returns_synthetic_false_with_usage() -> None:
    import os
    os.environ["TEST_DAY13_KEY"] = "TEST_KEY_FAKE_NOT_REAL"
    cfg = RealLLMProviderConfig(
        provider="openai", model="gpt-4o-mini",
        api_key_env_var="TEST_DAY13_KEY", timeout_seconds=2.0,
    )
    client = RealLLMClient(cfg)
    payload = {
        "id": "req-abc",
        "choices": [{"message": {"role": "assistant", "content": "hello"}}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 11, "total_tokens": 18},
    }
    with patch("urllib.request.urlopen", new=_make_mock_urlopen(payload)):
        r = client.generate("hi", task_name="smoke")
    assert r.usage_is_synthetic is False
    assert r.provider == "openai"
    assert r.model == "gpt-4o-mini"
    assert r.input_tokens == 7
    assert r.output_tokens == 11
    assert r.request_id == "req-abc"
    assert r.text == "hello"
    del os.environ["TEST_DAY13_KEY"]


def test_real_client_marks_unavailable_when_no_usage() -> None:
    """Day 13.5 contract: a real provider call WITHOUT a usage
    block is tagged UsageType.UNAVAILABLE, NOT synthetic.  Only
    FakeLLMClient is SYNTHETIC."""
    import os
    from llm import UsageType
    os.environ["TEST_DAY13_KEY"] = "TEST_KEY_FAKE_NOT_REAL"
    cfg = RealLLMProviderConfig(
        provider="openai", model="gpt-4o-mini",
        api_key_env_var="TEST_DAY13_KEY",
    )
    client = RealLLMClient(cfg)
    payload = {
        "choices": [{"message": {"role": "assistant", "content": "no-usage"}}],
        # no usage block
    }
    with patch("urllib.request.urlopen", new=_make_mock_urlopen(payload)):
        r = client.generate("hi", task_name="smoke")
    # Day 13.5: missing-usage is UNAVAILABLE (not synthetic).
    # The provider/model are still real; the tokens just aren't
    # trustworthy, so they must not enter real-token totals.
    assert r.usage_type == UsageType.UNAVAILABLE
    assert r.usage_is_synthetic is False
    assert r.provider == "openai"
    assert r.input_tokens == 0
    del os.environ["TEST_DAY13_KEY"]


def test_real_client_classifies_http_error() -> None:
    import os, urllib.error
    os.environ["TEST_DAY13_KEY"] = "TEST_KEY_FAKE_NOT_REAL"
    cfg = RealLLMProviderConfig(
        provider="openai", model="gpt-4o-mini",
        api_key_env_var="TEST_DAY13_KEY",
    )
    client = RealLLMClient(cfg)
    def raise_401(*a, **kw):
        raise urllib.error.HTTPError(
            "http://x", 401, "Unauthorized", {}, None,
        )
    with patch("urllib.request.urlopen", new=raise_401):
        r = client.generate("hi", task_name="smoke")
    # 401 -> AUTH_ERROR -> response text is the structured error JSON
    payload = json.loads(r.text)
    assert payload["error"] == "AUTH_ERROR"
    assert r.usage_is_synthetic is False  # it's a real call that failed
    del os.environ["TEST_DAY13_KEY"]


def test_real_client_invalid_response() -> None:
    import os
    os.environ["TEST_DAY13_KEY"] = "TEST_KEY_FAKE_NOT_REAL"
    cfg = RealLLMProviderConfig(
        provider="openai", model="gpt-4o-mini",
        api_key_env_var="TEST_DAY13_KEY",
    )
    client = RealLLMClient(cfg)
    # No choices at all
    with patch("urllib.request.urlopen", new=_make_mock_urlopen({"foo": "bar"})):
        r = client.generate("hi", task_name="smoke")
    payload = json.loads(r.text)
    assert payload["error"] == "INVALID_RESPONSE"
    del os.environ["TEST_DAY13_KEY"]


# =====================================================================
# 8-9: structured-output parser
# =====================================================================

def test_extract_json_object_plain() -> None:
    obj, err = extract_json_object('{"a": 1, "b": "x"}')
    assert obj == {"a": 1, "b": "x"}
    assert err == ""


def test_extract_json_object_in_fence() -> None:
    obj, err = extract_json_object('```json\n{"a": 1}\n```')
    assert obj == {"a": 1}


def test_extract_json_object_in_prose_rejected_day13_5() -> None:
    """Day 13.5 contract: NO free-text scan.  A JSON object
    embedded in free prose is INVALID_RESPONSE, not silently
    extracted.  This is the OPPOSITE of Day 13's behavior, and
    the change is intentional — we want to OBSERVE format drift
    in real-model experiments, not paper over it."""
    obj, err = extract_json_object('Sure! Here is the JSON: {"a": 1, "b": 2}. Hope it helps.')
    assert obj is None
    assert "INVALID_RESPONSE" in err


def test_extract_json_object_empty() -> None:
    obj, err = extract_json_object("")
    assert obj is None
    assert "empty" in err


def test_extract_json_object_garbage() -> None:
    obj, err = extract_json_object("not json at all")
    assert obj is None


def test_parse_claim_payload_valid() -> None:
    obj, issues, _ = parse_claim_payload(
        json.dumps({
            "claim_text": "revenue grew by 60.78%",
            "evidence_refs": ["run_x/metric_revenue_growth@v1"],
            "confidence": 0.9,
        })
    )
    assert obj is not None
    assert not issues
    assert obj["claim_text"] == "revenue grew by 60.78%"


def test_parse_claim_payload_missing_key() -> None:
    obj, issues, _ = parse_claim_payload(
        json.dumps({"evidence_refs": ["x"]})  # no claim_text
    )
    assert obj is not None  # extraction succeeded
    assert any("claim_text" in i for i in issues)


# =====================================================================
# 10-12: semantic validator sub-checks
# =====================================================================

def test_recommendation_boundary_pass_clean() -> None:
    ok, issues = recommendation_boundary_check(
        "2025 revenue grew 60.78 percent, business clearly expanded."
    )
    assert ok
    assert issues == []


def test_recommendation_boundary_fail_chinese() -> None:
    ok, issues = recommendation_boundary_check(
        "revenue is good. 建议 increase marketing investment."
    )
    assert not ok
    assert any("建议" in i for i in issues)


def test_recommendation_boundary_fail_english() -> None:
    ok, issues = recommendation_boundary_check(
        "Revenue grew by 60%. We recommend investing more in marketing."
    )
    assert not ok
    assert any("recommend" in i for i in issues)


def test_direction_check_positive_evidence() -> None:
    class A:
        artifact_id = "metric_growth"
        value = 0.6
        version = 1
    ok, issues = direction_check("metric_growth: revenue grew by 60%", [A()])
    assert ok


def test_direction_check_contradicts() -> None:
    """Day 13.5: per-metric.  A growth metric whose value is
    positive must not be described with a negative-direction word
    near the percentage."""
    class A:
        artifact_id = "metric_growth"
        value = 0.6
        version = 1
    # Place the negative token near 60% so the +/- 30 char window
    # is negative-dominant.
    ok, issues = direction_check(
        "metric_growth: revenue at 60% this quarter — declined "
        "sharply from the prior period.",
        [A()],
    )
    assert not ok
    assert any("direction" in i.lower() for i in issues)


def test_numeric_consistency_pass() -> None:
    # Day 13.6: evidence_ref is now the full ArtifactRef string,
    # so the test stub must have `run_id` as well.
    class A:
        run_id = "r1"
        artifact_id = "metric_x"
        version = 1
        value = 0.6078
    ok, results, issues, extras = numeric_consistency_check("grew 60.78%", [A()])
    assert ok
    assert not issues
    assert results[0].within_tolerance is True
    # And the evidence_ref is now the full <run_id>/<artifact_id>@v<n> string.
    assert results[0].evidence_ref == "r1/metric_x@v1"


def test_numeric_consistency_fail() -> None:
    class A:
        run_id = "r1"
        artifact_id = "metric_x"
        version = 1
        value = 0.6078
    ok, results, issues, extras = numeric_consistency_check("grew 20%", [A()])
    assert not ok
    assert any("mismatch" in i for i in issues)


def test_numeric_consistency_no_pct_in_text_day13_5() -> None:
    """Day 13.5 contract: claims MUST mention each required
    evidence metric with a number.  allow_no_numbers defaults
    to False; passing it for Sections is the only opt-out.
    Day 13.6: also verify the audit issues use the full ref."""
    class A:
        run_id = "r1"
        artifact_id = "metric_x"
        version = 1
        value = 0.5
    ok, results, issues, extras = numeric_consistency_check(
        "business is good (no concrete number)", [A()],
    )
    # Day 13.5: missing number is FAIL
    assert ok is False
    assert any("not numerically represented" in i for i in issues)
    # Day 13.6: issue / result both reference the full ArtifactRef.
    assert any("r1/metric_x@v1" in i for i in issues)
    assert results[0].evidence_ref == "r1/metric_x@v1"
    # Day 13.5: missing number is FAIL
    assert ok is False
    assert any("not numerically represented" in i for i in issues)


def test_parse_evidence_refs_extracts() -> None:
    text = "see run_x/metric_a@v3 and run_x/metric_b@v5;"
    refs = parse_evidence_refs(text)
    assert len(refs) == 2
    assert refs[0] == ArtifactRef(run_id="run_x", artifact_id="metric_a", version=3)
    assert refs[1] == ArtifactRef(run_id="run_x", artifact_id="metric_b", version=5)


# =====================================================================
# 13-14: validate_claim / validate_section integration
# =====================================================================

def _make_metric(store, run_id, aid, value) -> Artifact:
    a = Artifact(
        artifact_id=aid, artifact_type=ArtifactType.METRIC,
        producer_task="compute", run_id=run_id, version=1,
        value=value, input_artifact_ids=[],
        meta={"task_kind": "tool"},
    )
    return store.save(a)


def test_validate_claim_financial_happy() -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        store = ArtifactStore(base_dir=str(Path(td) / "art"))
        rg = _make_metric(store, "r1", "metric_revenue_growth", 0.6078)
        pm = _make_metric(store, "r1", "metric_profit_margin", 0.4756)
        cr = _make_metric(store, "r1", "metric_cost_ratio", 0.5243)
        text = (
            f"2025 revenue grew 60.78%, margin 47.56%. "
            f"Evidence: {rg.run_id}/{rg.artifact_id}@v{rg.version},"
            f"{pm.run_id}/{pm.artifact_id}@v{pm.version}."
        )
        result = validate_claim(
            claim_text=text,
            evidence_refs_in_text=[
                ArtifactRef(run_id=rg.run_id, artifact_id=rg.artifact_id, version=rg.version),
                ArtifactRef(run_id=pm.run_id, artifact_id=pm.artifact_id, version=pm.version),
            ],
            store=store,
            evidence_policy="financial_claim",
        )
        assert result.passed
        assert result.evidence_valid
        assert result.numeric_consistency_pass


def test_validate_claim_financial_missing_evidence() -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        store = ArtifactStore(base_dir=str(Path(td) / "art"))
        rg = _make_metric(store, "r1", "metric_revenue_growth", 0.6078)
        pm = _make_metric(store, "r1", "metric_profit_margin", 0.4756)
        cr = _make_metric(store, "r1", "metric_cost_ratio", 0.5243)
        # Only cite cost_ratio — missing revenue_growth & profit_margin
        text = f"cost ratio 52%. evidence: {cr.run_id}/{cr.artifact_id}@v{cr.version}."
        result = validate_claim(
            claim_text=text,
            evidence_refs_in_text=[ArtifactRef(
                run_id=cr.run_id, artifact_id=cr.artifact_id, version=cr.version,
            )],
            store=store,
            evidence_policy="financial_claim",
        )
        assert not result.passed
        assert not result.evidence_valid
        assert any("metric_revenue_growth" in i for i in result.issues)


def test_validate_claim_numeric_mismatch() -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        store = ArtifactStore(base_dir=str(Path(td) / "art"))
        rg = _make_metric(store, "r1", "metric_revenue_growth", 0.6078)
        pm = _make_metric(store, "r1", "metric_profit_margin", 0.4756)
        text = (
            f"revenue grew 20%. evidence: {rg.run_id}/{rg.artifact_id}@v{rg.version},"
            f"{pm.run_id}/{pm.artifact_id}@v{pm.version}."
        )
        result = validate_claim(
            claim_text=text,
            evidence_refs_in_text=[
                ArtifactRef(run_id=rg.run_id, artifact_id=rg.artifact_id, version=rg.version),
                ArtifactRef(run_id=pm.run_id, artifact_id=pm.artifact_id, version=pm.version),
            ],
            store=store,
            evidence_policy="financial_claim",
        )
        assert not result.passed
        assert not result.numeric_consistency_pass


def test_validate_section_clean() -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        store = ArtifactStore(base_dir=str(Path(td) / "art"))
        rg = _make_metric(store, "r1", "metric_revenue_growth", 0.6078)
        pm = _make_metric(store, "r1", "metric_profit_margin", 0.4756)
        # Build a fake claim artifact to act as section evidence
        claim = Artifact(
            artifact_id="claim_financial_growth",
            artifact_type=ArtifactType.CLAIM,
            producer_task="generate_financial_claim",
            run_id="r1", version=1, value=None,
            content="revenue grew 60.78%.",
            input_artifact_ids=[
                ArtifactRef(run_id=rg.run_id, artifact_id=rg.artifact_id, version=rg.version),
                ArtifactRef(run_id=pm.run_id, artifact_id=pm.artifact_id, version=pm.version),
            ],
            evidence_ids=[
                ArtifactRef(run_id=rg.run_id, artifact_id=rg.artifact_id, version=rg.version),
                ArtifactRef(run_id=pm.run_id, artifact_id=pm.artifact_id, version=pm.version),
            ],
        )
        store.save(claim)
        text = (
            "2025 financial: revenue grew 60.78%, margin 47.56%. "
            f"evidence: {claim.run_id}/{claim.artifact_id}@v{claim.version}."
        )
        result = validate_section(
            section_text=text,
            section_evidence_refs=[ArtifactRef(
                run_id=claim.run_id, artifact_id=claim.artifact_id, version=claim.version,
            )],
            claim_evidence_refs=[
                ArtifactRef(run_id=rg.run_id, artifact_id=rg.artifact_id, version=rg.version),
                ArtifactRef(run_id=pm.run_id, artifact_id=pm.artifact_id, version=pm.version),
            ],
            store=store,
            evidence_policy="financial_section",
        )
        assert result.passed


def test_validate_section_with_recommendation_fails() -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        store = ArtifactStore(base_dir=str(Path(td) / "art"))
        rg = _make_metric(store, "r1", "metric_revenue_growth", 0.6078)
        pm = _make_metric(store, "r1", "metric_profit_margin", 0.4756)
        claim = Artifact(
            artifact_id="claim_financial_growth",
            artifact_type=ArtifactType.CLAIM,
            producer_task="generate_financial_claim",
            run_id="r1", version=1, value=None,
            content="revenue grew 60.78%.",
            input_artifact_ids=[
                ArtifactRef(run_id=rg.run_id, artifact_id=rg.artifact_id, version=rg.version),
                ArtifactRef(run_id=pm.run_id, artifact_id=pm.artifact_id, version=pm.version),
            ],
            evidence_ids=[
                ArtifactRef(run_id=rg.run_id, artifact_id=rg.artifact_id, version=rg.version),
                ArtifactRef(run_id=pm.run_id, artifact_id=pm.artifact_id, version=pm.version),
            ],
        )
        store.save(claim)
        text = (
            "2025 financial: revenue grew 60.78%. 建议 invest more. "
            f"evidence: {claim.run_id}/{claim.artifact_id}@v{claim.version}."
        )
        result = validate_section(
            section_text=text,
            section_evidence_refs=[ArtifactRef(
                run_id=claim.run_id, artifact_id=claim.artifact_id, version=claim.version,
            )],
            claim_evidence_refs=[
                ArtifactRef(run_id=rg.run_id, artifact_id=rg.artifact_id, version=rg.version),
                ArtifactRef(run_id=pm.run_id, artifact_id=pm.artifact_id, version=pm.version),
            ],
            store=store,
            evidence_policy="financial_section",
        )
        assert not result.passed
        assert not result.recommendation_boundary_pass


# =====================================================================
# 15: provider config in claim meta
# =====================================================================

def test_real_provider_metadata_propagated_to_claim() -> None:
    """A claim produced by a real LLM must carry provider / model /
    usage_type in its meta.  Day 13.5 contract: the 3-state
    `usage_type` is the authoritative field; `usage_is_synthetic`
    is the derived boolean kept for back-compat."""
    from workflow.claim_tasks import _parse_llm_output
    from llm import UsageType
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        store = ArtifactStore(base_dir=str(Path(td) / "art"))
        rg = _make_metric(store, "r1", "metric_revenue_growth", 0.6)
        pm = _make_metric(store, "r1", "metric_profit_margin", 0.5)
        cr = _make_metric(store, "r1", "metric_cost_ratio", 0.4)
        response = LLMResponse(
            text=json.dumps({
                "claim_text": "revenue grew 60%",
                "evidence_refs": [rg.run_id + "/" + rg.artifact_id + "@v" + str(rg.version),
                                   pm.run_id + "/" + pm.artifact_id + "@v" + str(pm.version)],
                "confidence": 0.9,
            }),
            input_tokens=10, output_tokens=20, monetary_usd=0.0, latency_ms=100.0,
            provider="openai", model="gpt-4o-mini",
            usage_type=UsageType.PROVIDER_REPORTED, request_id="req-123",
        )
        claim = _parse_llm_output(
            response, [rg, pm, cr], "r1", store,
            artifact_id="claim_test", producer_task="test",
        )
        assert claim.meta["llm_provider"] == "openai"
        assert claim.meta["llm_model"] == "gpt-4o-mini"
        # Day 13.5: usage_type is the new field
        assert claim.meta["llm_usage_type"] == "provider_reported"
        assert claim.meta["llm_usage_is_synthetic"] is False
        assert claim.meta["llm_request_id"] == "req-123"
        assert claim.execution_cost.latency_ms == 100.0
        assert claim.execution_cost.input_tokens == 10
        assert claim.execution_cost.output_tokens == 20
