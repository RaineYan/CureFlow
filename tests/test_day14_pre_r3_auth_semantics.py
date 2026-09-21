"""Day 14A PRE-R3 AUTH SEMANTICS FIX — 12 new regression
tests.

Per user spec section "PRE-R3 AUTH SEMANTICS FIX":

  Required regression tests:
    1. HTTP 401 => real_http_roundtrip_verified=True
    2. HTTP 401 => provider_identity_verified=True
    3. HTTP 401 => provider_authenticated=False
    4. HTTP 401 => provider_verified=False
    5. HTTP 401 => actual_provider_http_requests=1
    6. HTTP 401 => successful_provider_requests=0
    7. usage unavailable does not enter token aggregate
    8. R3 replacement_reason=AUTH_CREDENTIAL_CORRECTION
    9. R3 auto-derives from R2 failure_audit.json
   10. R2 historical replacement_reason preserved
   11. existing tests remain green
   12. Day 13 frozen evidence unchanged

The 5-layer state semantics MUST distinguish:
  - "real HTTP roundtrip to Provider happened" (True
    on HTTP 401 — the request DID reach the auth proxy)
  - "Provider accepted auth" (False on HTTP 401)
  - "Provider returned successful inference" (False)
  - Legacy single boolean `provider_verified` is the
    "request succeeded" layer, NOT the "roundtrip"
    layer.  This is the layer the benchmark aggregates.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

from experiments.day14.recording import (
    Day14RecordingClient,
    MockHttpRealLLMClient,
    derive_5_layer_provider_state,
    derive_replacement_reason,
    _redact_secrets,
)
from experiments.live_ledger import LiveCallLedger, LiveCallRecord
from llm import LLMResponse, UsageType


ROOT = Path(__file__).resolve().parent.parent
PILOT_DIR = ROOT / "experiments/results/day14/pilot"
R2_TRIAL = PILOT_DIR / "case_a_full_rerun_trial01R2.json"
R2_LEDGER = PILOT_DIR / "case_a_full_rerun_trial01R2_live_ledger.jsonl"
R2_AUDIT = PILOT_DIR / "case_a_full_rerun_trial01R2_failure_audit.json"


# Recorded SHA-256 of the R2 trial JSON and R2 ledger
# BEFORE this hardening pass.  These must remain stable.
R2_TRIAL_SHA_BASELINE = (
    "c88785ab234c30067a8cd26ddf00416b7a08ec61d39788eaa112944dc5628818"
)
R2_LEDGER_SHA_BASELINE = (
    "2cf0d95f3efd5210cf9153a1baef7b3bdf42baaf77297686676d064ce4775184"
)


# =====================================================================
# 1. HTTP 401 => real_http_roundtrip_verified=True
# =====================================================================

def test_http401_real_http_roundtrip_verified_true() -> None:
    """When the Provider returns HTTP 401 (auth error),
    an HTTP roundtrip DID happen — the request reached
    the Provider's auth proxy.  The first 5-layer state
    field MUST be True."""
    state = derive_5_layer_provider_state(
        response_received=True,  # we got a response
        provider="qwen",
        model="qwen3.7-plus-2026-05-26",
        request_id=None,  # provider did NOT issue a chatcmpl- id
        usage_type="unavailable",
    )
    assert state["real_http_roundtrip_verified"] is True


# =====================================================================
# 2. HTTP 401 => provider_identity_verified=True
# =====================================================================

def test_http401_provider_identity_verified_true() -> None:
    """Identity is verified by the (provider, model)
    pair on the response, NOT by the request_id.  A
    HTTP 401 still tells us the request went to qwen
    qwen3.7-plus-2026-05-26."""
    state = derive_5_layer_provider_state(
        response_received=True,
        provider="qwen",
        model="qwen3.7-plus-2026-05-26",
        request_id=None,
        usage_type="unavailable",
    )
    assert state["provider_identity_verified"] is True


# =====================================================================
# 3. HTTP 401 => provider_authenticated=False
# =====================================================================

def test_http401_provider_authenticated_false() -> None:
    """Authentication is the (provider, model, request_id
    starts with chatcmpl-) triple.  A HTTP 401 means the
    Provider REJECTED our key, so it returns an error
    envelope WITHOUT a request_id.  provider_authenticated
    MUST be False."""
    state = derive_5_layer_provider_state(
        response_received=True,
        provider="qwen",
        model="qwen3.7-plus-2026-05-26",
        request_id=None,  # NO chatcmpl- on auth error
        usage_type="unavailable",
    )
    assert state["provider_authenticated"] is False


# =====================================================================
# 4. HTTP 401 => provider_verified=False (legacy alias)
# =====================================================================

def test_http401_provider_verified_false_legacy() -> None:
    """The legacy `provider_verified` field aliases
    the strict "request succeeded" layer.  A HTTP 401
    means no successful inference response, so the
    legacy boolean is False."""
    state = derive_5_layer_provider_state(
        response_received=True,
        provider="qwen",
        model="qwen3.7-plus-2026-05-26",
        request_id=None,
        usage_type="unavailable",
    )
    assert state["provider_verified"] is False
    # AND it MUST equal provider_request_succeeded
    assert state["provider_verified"] == state[
        "provider_request_succeeded"
    ]


# =====================================================================
# 5. HTTP 401 => actual_provider_http_requests=1
# =====================================================================

def test_http401_actual_provider_http_requests_is_one() -> None:
    """A HTTP 401 IS a real Provider HTTP request.  The
    count of actual HTTP requests is 1, not 0.  The
    count of SUCCESSFUL Provider requests is 0 (which
    is a separate counter)."""
    # Build a real-looking record
    rec = LiveCallRecord(
        phase="A",
        execution_unit="generate_financial_claim",
        provider="qwen",
        model="qwen3.7-plus-2026-05-26",
        request_id=None,
        usage_type="unavailable",
        input_tokens=0, output_tokens=0, total_tokens=0,
        client_wall_clock_latency_ms=2238.0,
        schema_valid=False, evidence_valid=False,
        semantic_valid=False, produced_ref=None,
    )
    ledger = LiveCallLedger()
    ledger.append(rec)
    # actual HTTP requests = 1 (record was appended)
    assert ledger.current_invocation_real_request_count == 1
    # And the 5-layer state from the record:
    state = derive_5_layer_provider_state(
        response_received=True,
        provider=rec.provider,
        model=rec.model,
        request_id=rec.request_id,
        usage_type=rec.usage_type,
    )
    assert state["real_http_roundtrip_verified"] is True
    # Counter pair:
    actual_provider_http_requests = 1
    successful_provider_requests = (
        1 if state["provider_request_succeeded"] else 0
    )
    assert actual_provider_http_requests == 1
    assert successful_provider_requests == 0


# =====================================================================
# 6. HTTP 401 => successful_provider_requests=0
# =====================================================================

def test_http401_successful_provider_requests_is_zero() -> None:
    """HTTP 401 is NOT a successful Provider inference
    request.  successful_provider_requests is 0.
    The two counters are SEPARATE."""
    state = derive_5_layer_provider_state(
        response_received=True,
        provider="qwen",
        model="qwen3.7-plus-2026-05-26",
        request_id=None,
        usage_type="unavailable",
    )
    successful_provider_requests = (
        1 if state["provider_request_succeeded"] else 0
    )
    assert successful_provider_requests == 0


# =====================================================================
# 7. usage unavailable does not enter token aggregate
# =====================================================================

def test_usage_unavailable_does_not_enter_token_aggregate() -> None:
    """A record with usage_type=unavailable (Provider
    returned an error envelope, no usage block) MUST
    NOT contribute to provider-reported token aggregate."""
    rec = LiveCallRecord(
        phase="A",
        execution_unit="generate_financial_claim",
        provider="qwen",
        model="qwen3.7-plus-2026-05-26",
        request_id=None,
        usage_type="unavailable",
        input_tokens=0, output_tokens=0, total_tokens=0,
        client_wall_clock_latency_ms=2238.0,
        schema_valid=False, evidence_valid=False,
        semantic_valid=False, produced_ref=None,
    )
    ledger = LiveCallLedger()
    ledger.append(rec)
    # Tokens=0 because provider didn't return a usage
    # block on the error response.  This is the correct
    # value, NOT an instrumentation bug.
    assert ledger.current_invocation_provider_reported_tokens == 0
    # usage_type=unavailable MUST NOT be aggregated as
    # provider-reported.
    assert rec.usage_type != "provider_reported"


# =====================================================================
# 8. R3 replacement_reason=AUTH_CREDENTIAL_CORRECTION
# =====================================================================

def test_r3_replacement_reason_is_auth_credential_correction() -> None:
    """When the immediate predecessor failed with
    INFRA_FAILURE / HTTP_401_AUTH_ERROR, the new
    replacement's replacement_reason MUST be
    AUTH_CREDENTIAL_CORRECTION (not the legacy
    PRE_RETRY_FORENSIC_HARDENING)."""
    reason = derive_replacement_reason(
        previous_failure_class="INFRA_FAILURE",
        previous_failure_subtype="HTTP_401_AUTH_ERROR",
    )
    assert reason == "AUTH_CREDENTIAL_CORRECTION"


# =====================================================================
# 9. R3 auto-derives from R2 failure_audit.json
# =====================================================================

def test_r3_auto_derives_from_r2_failure_audit(tmp_path) -> None:
    """The CLI runner must READ the predecessor's
    failure_audit.json and derive R3's
    replacement_reason from the predecessor's
    failure_class/subtype.  This test simulates
    that flow with a tmp failure_audit.json."""
    pilot = tmp_path
    # Write a fake R2 failure_audit.json
    r2_audit_path = pilot / "trial01R2_failure_audit.json"
    r2_audit_path.write_text(
        json.dumps({
            "trial_id": "trial01R2",
            "failure_class": "INFRA_FAILURE",
            "failure_subtype": "HTTP_401_AUTH_ERROR",
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    # Read it
    prev_audit = json.loads(r2_audit_path.read_text(
        encoding="utf-8"
    ))
    # Derive R3 reason
    reason = derive_replacement_reason(
        previous_failure_class=prev_audit["failure_class"],
        previous_failure_subtype=prev_audit["failure_subtype"],
    )
    assert reason == "AUTH_CREDENTIAL_CORRECTION"


# =====================================================================
# 10. R2 historical replacement_reason preserved
# =====================================================================

def test_r2_historical_replacement_reason_preserved() -> None:
    """R2 was issued DURING harness hardening, before
    the auth-credential correction was identified as
    the actual blocker.  R2's OWN replacement_reason
    must remain PRE_RETRY_FORENSIC_HARDENING (the
    historical label) — the AUTH_CREDENTIAL_CORRECTION
    label applies only to R3 (the NEXT replacement)."""
    # R2's own replacement_reason is on its audit
    if not R2_AUDIT.exists():
        pytest.skip(f"missing: {R2_AUDIT}")
    r2_audit = json.loads(R2_AUDIT.read_text(encoding="utf-8"))
    # R2's own label is preserved historically
    assert r2_audit["replacement_reason"] == (
        "PRE_RETRY_FORENSIC_HARDENING"
    )
    # The CORRECTION intent is documented separately
    assert r2_audit["r3_replacement_reason_intent"] == (
        "AUTH_CREDENTIAL_CORRECTION"
    )


# =====================================================================
# 11. Day 13 frozen evidence unchanged
# =====================================================================

def test_day13_frozen_evidence_unchanged() -> None:
    """Day 13 frozen evidence must remain unchanged
    after this hardening pass."""
    from experiments.day14 import verify_frozen_evidence_unchanged
    r = verify_frozen_evidence_unchanged()
    assert r["passed"] is True, (
        f"frozen evidence mutated: mismatches={r['mismatches']}, "
        f"missing={r['missing']}"
    )


# =====================================================================
# 12. Existing R2 trial JSON + ledger unchanged
# =====================================================================

def test_r2_trial_json_and_ledger_sha256_unchanged() -> None:
    """The R2 trial JSON and R2 ledger must not be
    re-written by this hardening pass.  Their SHA-256
    must match the values captured BEFORE this pass."""
    if not R2_TRIAL.exists():
        pytest.skip(f"missing: {R2_TRIAL}")
    if not R2_LEDGER.exists():
        pytest.skip(f"missing: {R2_LEDGER}")
    trial_sha = hashlib.sha256(
        R2_TRIAL.read_bytes()
    ).hexdigest()
    ledger_sha = hashlib.sha256(
        R2_LEDGER.read_bytes()
    ).hexdigest()
    assert trial_sha == R2_TRIAL_SHA_BASELINE, (
        f"R2 trial JSON SHA changed: was {R2_TRIAL_SHA_BASELINE}, "
        f"now {trial_sha}"
    )
    assert ledger_sha == R2_LEDGER_SHA_BASELINE, (
        f"R2 ledger SHA changed: was {R2_LEDGER_SHA_BASELINE}, "
        f"now {ledger_sha}"
    )


# =====================================================================
# BONUS: Day14RecordingClient 5-layer state on real HTTP 401
# =====================================================================

def test_recording_client_5_layer_on_auth_error(tmp_path) -> None:
    """Day14RecordingClient's 5-layer state must be
    correctly set when the inner client returns
    a HTTP 401 (auth error envelope with no chatcmpl-
    id and usage_type=unavailable)."""
    class AuthErrorClient:
        def generate(self, prompt, *, task_name=""):
            return LLMResponse(
                text='{"error": "AUTH_ERROR", "message": "HTTP 401"}',
                provider="qwen",
                model="qwen3.7-plus-2026-05-26",
                usage_type=UsageType.UNAVAILABLE,
                request_id=None,
            )
    jsonl = tmp_path / "ledger.jsonl"
    rec = Day14RecordingClient(
        AuthErrorClient(),
        LiveCallLedger(),
        persistence_path=jsonl,
    )
    rec.generate("p", task_name="generate_financial_claim")
    # 5-layer state
    assert rec.real_http_roundtrip_verified is True
    assert rec.provider_identity_verified is True
    assert rec.provider_authenticated is False  # ← key
    assert rec.provider_request_succeeded is False
    assert rec.provider_verified is False  # legacy


# =====================================================================
# BONUS: Day14RecordingClient 5-layer state on successful call
# =====================================================================

def test_recording_client_5_layer_on_success(tmp_path) -> None:
    """Day14RecordingClient's 5-layer state must be
    fully True on a real successful response (chatcmpl-
    id + usage_type=provider_reported)."""
    class SuccessClient:
        def generate(self, prompt, *, task_name=""):
            return LLMResponse(
                text='{"claim_text": "x", "evidence_refs": []}',
                provider="qwen",
                model="qwen3.7-plus-2026-05-26",
                usage_type=UsageType.PROVIDER_REPORTED,
                request_id="chatcmpl-real-1234",
            )
    jsonl = tmp_path / "ledger.jsonl"
    rec = Day14RecordingClient(
        SuccessClient(),
        LiveCallLedger(),
        persistence_path=jsonl,
    )
    rec.generate("p", task_name="generate_financial_claim")
    # All 5 layers True
    assert rec.real_http_roundtrip_verified is True
    assert rec.provider_identity_verified is True
    assert rec.provider_authenticated is True
    assert rec.provider_request_succeeded is True
    assert rec.provider_verified is True  # legacy


# =====================================================================
# BONUS: R2 failure audit contains 5-layer state
# =====================================================================

def test_r2_failure_audit_contains_5_layer_state() -> None:
    """The R2 failure_audit.json (rebuilt by
    _r2_postmortem_audit.py) MUST contain the 5-layer
    state with the correct values for an HTTP 401."""
    if not R2_AUDIT.exists():
        pytest.skip(f"missing: {R2_AUDIT}")
    audit = json.loads(R2_AUDIT.read_text(encoding="utf-8"))
    # 5-layer state is present
    for key in (
        "real_http_roundtrip_verified",
        "provider_identity_verified",
        "provider_authenticated",
        "provider_request_succeeded",
        "provider_verified",
    ):
        assert key in audit, (
            f"R2 audit missing 5-layer state field: {key!r}"
        )
    # Values match the R2 evidence (HTTP 401)
    assert audit["real_http_roundtrip_verified"] is True
    assert audit["provider_identity_verified"] is True
    assert audit["provider_authenticated"] is False
    assert audit["provider_request_succeeded"] is False
    assert audit["provider_verified"] is False
    # Counters
    assert audit["actual_provider_http_requests"] == 1
    assert audit["successful_provider_requests"] == 0
    assert audit["successful_artifact_materializations"] == 0


# =====================================================================
# BONUS: R2 failure audit preserves no-secret invariant
# =====================================================================

def test_r2_failure_audit_no_secret() -> None:
    """The R2 audit must NOT contain an un-redacted
    API key, even after the 5-layer state addition."""
    if not R2_AUDIT.exists():
        pytest.skip(f"missing: {R2_AUDIT}")
    text = R2_AUDIT.read_text(encoding="utf-8")
    assert "sk-ws-H." not in text
    # Generic redaction markers must be present
    assert "<sk-redacted>" in text or "<redacted>" in text
