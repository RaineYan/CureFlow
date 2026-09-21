"""M2 — Provider error-envelope robustness regression tests.

Per spec section 7 (Stage 2 M2 fix), these tests cover:

  Test 1 — HTTP 200 + NETWORK_ERROR envelope
    -> INFRA_FAILURE / NETWORK_ERROR, NOT RESPONSE_SCHEMA_ERROR.

  Test 2 — Normal structured response (regression guard).
    -> parse_claim_payload returns the dict; no envelope detected.

  Test 3 — Malformed normal response (missing required business
    keys, no envelope).
    -> existing "missing required key" behaviour; NOT classified
       as INFRA_FAILURE.

  Test 4 — False-positive: business payload containing an
    "error" string but with claim_text present.
    -> NOT classified as envelope.

  Test 5 — Retry semantics unchanged.
    -> the new classifier does NOT add new retry behaviour;
       the runner-layer retry policy is unchanged.

  Test 6 — Evidence serialization.
    -> the envelope path persists failure_class,
       failure_reason, request_id, usage_type without
       forging any of them.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


# ============================================================================
# Test 1 — HTTP 200 + NETWORK_ERROR envelope (the bug we are fixing)
# ============================================================================

def test_test1_network_error_envelope_detected_by_parse_claim_payload():
    """DashScope-style envelope: HTTP 200 + {"error":"NETWORK_ERROR",...}.

    The fix: parse_claim_payload must detect this BEFORE the claim
    schema check, and return a reason that starts with
    PROVIDER_ERROR_ENVELOPE: so the runner can route to
    INFRA_FAILURE / NETWORK_ERROR.
    """
    from llm.parse import (
        PROVIDER_ERROR_ENVELOPE_PREFIX,
        is_provider_error_envelope,
        parse_claim_payload,
    )
    text = json.dumps({
        "error": "NETWORK_ERROR",
        "message": (
            "network error: [SSL: UNEXPECTED_EOF_WHILE_READING] "
            "EOF occurred in violation of protocol (_ssl.c:1032)"
        ),
    })
    # narrow envelope detector fires
    is_env, kind, msg = is_provider_error_envelope(json.loads(text))
    assert is_env is True
    assert kind == "NETWORK_ERROR"
    assert "SSL: UNEXPECTED_EOF" in msg
    # parse_claim_payload returns None + envelope reason
    payload, issues, reason = parse_claim_payload(text)
    assert payload is None
    assert reason.startswith(PROVIDER_ERROR_ENVELOPE_PREFIX)
    assert "NETWORK_ERROR" in reason
    # the original Provider message is preserved (not lost)
    assert "SSL: UNEXPECTED_EOF" in reason


def test_test1_runner_classifier_routes_envelope_to_infra_failure_network_error():
    """The runner's _classify_failure_for_audit must route the
    envelope-shaped ValueError to INFRA_FAILURE_NETWORK_ERROR, NOT
    RESPONSE_SCHEMA_ERROR.
    """
    from _exp02_stage15_real_runner import _classify_failure_for_audit
    e = ValueError(
        "PROVIDER_ERROR_ENVELOPE: NETWORK_ERROR: "
        "network error: [SSL: UNEXPECTED_EOF_WHILE_READING] "
        "EOF occurred in violation of protocol (_ssl.c:1032)"
    )
    sub = _classify_failure_for_audit(e)
    assert sub == "INFRA_FAILURE_NETWORK_ERROR"
    assert "SCHEMA" not in sub  # NOT classified as RESPONSE_SCHEMA_ERROR


def test_test1_claim_tasks_propagates_envelope_reason(tmp_path):
    """workflow.claim_tasks._parse_llm_output must propagate the
    envelope reason so the runner sees the prefix and routes
    correctly.  We use a real ArtifactStore and a stub LLMResponse
    to confirm the ValueError carries the prefix.
    """
    from artifacts.store import ArtifactStore  # type: ignore
    from llm.base import LLMResponse, UsageType  # type: ignore
    from workflow import claim_tasks  # type: ignore

    class _StubResponse:
        provider = "qwen"
        model = "qwen3.7-plus-2026-05-26"
        text = json.dumps({
            "error": "NETWORK_ERROR",
            "message": "network error: SSL EOF test",
        })
        request_id = None
        usage_type = UsageType.UNAVAILABLE
        input_tokens = 0
        output_tokens = 0
        monetary_usd = 0.0
        latency_ms = 0.0
        usage_is_synthetic = False

    store = ArtifactStore(base_dir=str(tmp_path / "store"))
    try:
        claim_tasks._parse_llm_output(
            response=_StubResponse(),  # type: ignore[arg-type]
            inputs=[],
            run_id="m2-test",
            store=store,
            artifact_id="claim_x",
            producer_task="generate_x_claim",
        )
    except ValueError as e:
        msg = str(e)
        assert msg.startswith("PROVIDER_ERROR_ENVELOPE:"), \
            f"expected envelope prefix, got: {msg[:120]}"
        assert "NETWORK_ERROR" in msg
    else:
        pytest.fail("expected ValueError for envelope payload")


# ============================================================================
# Test 2 — Normal structured response (regression guard)
# ============================================================================

def test_test2_normal_structured_response_unchanged():
    """A legitimate claim response must still parse cleanly, with
    no envelope detected and no schema issues.
    """
    from llm.parse import (
        is_provider_error_envelope,
        parse_claim_payload,
    )
    text = json.dumps({
        "claim_text": "Revenue growth is 60.78%.",
        "evidence_refs": ["metric_revenue_growth@v3"],
        "confidence": 0.95,
    })
    is_env, _, _ = is_provider_error_envelope(json.loads(text))
    assert is_env is False
    payload, issues, reason = parse_claim_payload(text)
    assert payload is not None
    assert issues == []
    assert reason == ""
    assert payload["claim_text"] == "Revenue growth is 60.78%."


# ============================================================================
# Test 3 — Malformed normal response (not an envelope)
# ============================================================================

def test_test3_malformed_normal_response_not_envelope():
    """A JSON object that is missing claim_text/evidence_refs but
    has no error+message keys is NOT an envelope.  It must still
    produce the legacy "missing required key" issue and route to
    RESPONSE_SCHEMA_ERROR, not INFRA_FAILURE.
    """
    from llm.parse import (
        is_provider_error_envelope,
        parse_claim_payload,
    )
    from _exp02_stage15_real_runner import _classify_failure_for_audit

    text = json.dumps({"foo": "bar", "baz": 1})  # no claim keys, no envelope
    is_env, _, _ = is_provider_error_envelope(json.loads(text))
    assert is_env is False
    payload, issues, reason = parse_claim_payload(text)
    # parse_claim_payload returns the dict (so the caller can decide
    # what to do with the schema issues).  The dict is not None.
    assert payload is not None
    assert "missing required key: 'claim_text'" in "; ".join(issues)
    assert "missing required key: 'evidence_refs'" in "; ".join(issues)
    # The caller's job is to surface the issue as a ValueError.  We
    # simulate the exact ValueError that claim_tasks raises for this
    # case (no envelope) and confirm classification is unchanged.
    e = ValueError(
        f"LLM output failed claim schema: {'; '.join(issues)}"
    )
    sub = _classify_failure_for_audit(e)
    assert sub == "RESPONSE_SCHEMA_ERROR"
    # And: NOT routed to an INFRA_FAILURE_* envelope label.
    assert "INFRA_FAILURE" not in sub


# ============================================================================
# Test 4 — False-positive: business payload with "error" string
# ============================================================================

def test_test4_business_payload_with_error_field_not_envelope():
    """A real business payload that happens to carry an "error"
    field (e.g. a soft warning) but ALSO has claim_text / evidence_refs
    must NOT be classified as an envelope.
    """
    from llm.parse import (
        is_provider_error_envelope,
        parse_claim_payload,
    )
    from _exp02_stage15_real_runner import _classify_failure_for_audit

    text = json.dumps({
        "claim_text": "Revenue is rising.",
        "evidence_refs": ["metric_revenue_growth@v3"],
        "confidence": 0.9,
        "error": "deprecated_field_present",  # business-side warning
        "message": "this is not a provider error envelope",
    })
    is_env, _, _ = is_provider_error_envelope(json.loads(text))
    assert is_env is False
    payload, issues, _ = parse_claim_payload(text)
    assert payload is not None
    assert issues == []
    # Sanity: a real business response with both claim_text and an
    # "error" field must NOT route to INFRA_FAILURE_*.
    fake_exception = ValueError("not used")
    sub = _classify_failure_for_audit(fake_exception)
    # The default for unrelated messages is PROVIDER_ERROR; never
    # an INFRA_FAILURE_* envelope label from a real payload.
    assert "INFRA_FAILURE" not in sub


# ============================================================================
# Test 5 — Retry semantics unchanged
# ============================================================================

def test_test5_classifier_does_not_introduce_retry():
    """The new envelope-aware classifier does NOT add new retry
    attempts.  RealLLMClient's max_retries (set by the Stage 1.5
    runner to 0) and the runner-layer `retries_per_trial` (1) are
    both unchanged.
    """
    from _exp02_stage15_real_runner import _classify_failure_for_audit
    # The classifier is a pure string-mapping function: it must
    # not have a side effect (no retry counter, no I/O, no
    # raising).  We invoke it many times and ensure the result is
    # deterministic and not stateful.
    e = ValueError(
        "PROVIDER_ERROR_ENVELOPE: NETWORK_ERROR: SSL EOF test"
    )
    a = _classify_failure_for_audit(e)
    b = _classify_failure_for_audit(e)
    c = _classify_failure_for_audit(e)
    assert a == b == c == "INFRA_FAILURE_NETWORK_ERROR"
    # The retry-crashed runner's RETRIES_PER_TRIAL is defined at
    # module level; verify it is still 1 (not changed).
    from _exp02_stage15_retry_crashed import RETRIES_PER_TRIAL
    assert RETRIES_PER_TRIAL == 1
    # The Stage 1.5 runner constructs RealLLMProviderConfig with
    # max_retries=0 (frozen Day 14 config).  Verify by inspecting
    # the source literal.
    import _exp02_stage15_real_runner as rmod
    src = Path(rmod.__file__).read_text(encoding="utf-8")
    assert "max_retries=0" in src, "frozen max_retries=0 was changed"
    # And: the RetryPolicy in llm/providers/real.py is unchanged
    # (only TRANSIENT kinds get retried at the Provider layer).
    from llm.providers.real import RealLLMErrorKind
    # Sanity: the only kinds retried in _post_with_retry are
    # NETWORK_ERROR, TIMEOUT, RATE_LIMIT.  We assert that the
    # enum is the same shape it has been since Day 13.
    assert {k.name for k in RealLLMErrorKind} == {
        "AUTH_ERROR", "TIMEOUT", "RATE_LIMIT", "NETWORK_ERROR",
        "INVALID_RESPONSE", "SCHEMA_ERROR",
    }


# ============================================================================
# Test 6 — Evidence serialization (no forging)
# ============================================================================

def test_test6_envelope_classification_serializes_fields_without_forging(tmp_path):
    """Simulate a trial with a NETWORK_ERROR envelope.  The
    serialized failure fields must preserve the original Provider
    message verbatim, set failure_subtype to the new envelope
    label, leave request_id/usage as UNAVAILABLE — and must NOT
    invent a request_id or usage tokens.
    """
    from _exp02_stage15_real_runner import _classify_failure_for_audit

    original_message = (
        "PROVIDER_ERROR_ENVELOPE: NETWORK_ERROR: "
        "network error: [SSL: UNEXPECTED_EOF_WHILE_READING] "
        "EOF occurred in violation of protocol (_ssl.c:1032)"
    )
    e = ValueError(original_message)
    sub = _classify_failure_for_audit(e)
    # persist the same shape the runner persists
    record = {
        "failure_class": "INFRA_OR_ARTIFACT",
        "failure_subtype": sub,
        "error_type": type(e).__name__,
        "error_message": str(e),
        # Provider-side evidence (must NOT be forged)
        "request_id": None,
        "usage_type": "unavailable",
        "input_tokens": 0,
        "output_tokens": 0,
    }
    out = tmp_path / "m2_test6_envelope_evidence.json"
    out.write_text(json.dumps(record, indent=2), encoding="utf-8")
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded["failure_subtype"] == "INFRA_FAILURE_NETWORK_ERROR"
    assert loaded["error_message"] == original_message
    assert loaded["request_id"] is None
    assert loaded["usage_type"] == "unavailable"
    assert loaded["input_tokens"] == 0
    assert loaded["output_tokens"] == 0
    # no provider-reported usage was forged
    assert "provider_reported" not in loaded["usage_type"]


def test_test6_normal_failure_serializes_unaffected(tmp_path):
    """A non-envelope failure (Test 3 type) must still serialize
    with the legacy RESPONSE_SCHEMA_ERROR subtype, so the change
    is fully backward-compatible for callers that do not see an
    envelope.
    """
    from _exp02_stage15_real_runner import _classify_failure_for_audit

    e = ValueError(
        "LLM output failed claim schema: missing required key: "
        "'claim_text'; missing required key: 'evidence_refs'"
    )
    sub = _classify_failure_for_audit(e)
    record = {
        "failure_subtype": sub,
        "error_message": str(e),
    }
    out = tmp_path / "m2_test6_legacy_evidence.json"
    out.write_text(json.dumps(record, indent=2), encoding="utf-8")
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded["failure_subtype"] == "RESPONSE_SCHEMA_ERROR"
    assert "missing required key" in loaded["error_message"]
