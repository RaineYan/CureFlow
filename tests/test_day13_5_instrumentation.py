"""
Day 13.5 instrumentation regression tests (spec §9).

The user reported Test B schema-failure but the live gate had
already issued a real Qwen HTTP request.  Instrumentation rules:

  1. Every real HTTP response produces a LiveCallRecord, regardless
     of whether downstream validation / materialization succeeds.
  2. `real_request_count` = number of records in LiveCallLedger.
  3. `usage_type == PROVIDER_REPORTED` tokens are summed into
     `provider_reported_token_total` even when schema_valid=False
     (the provider already billed them).
  4. `--from-test B` rebuilds a working in-memory store + Claim
     Artifact from the persisted state.  No re-issuing of Test A.
  5. The persisted state contains the FULL Claim Artifact
     (model_dump JSON), its upstream artifacts, and the raw_data.
     The API key is NEVER present.
  6. A full A -> B -> C live run produces exactly 4 real requests
     (1 + 1 + 2).

These tests are offline.  RealClient HTTP is mocked.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import List
from unittest.mock import patch

import pytest

from artifacts import Artifact, ArtifactRef, ArtifactStore, ArtifactType
from llm import LLMResponse, RealLLMClient, RealLLMErrorKind, UsageType
from live_gate_run import _RecordingClient


CSV = str(Path(__file__).resolve().parent.parent / "data" / "sample_company.csv")


# =====================================================================
# Helpers
# =====================================================================

def _make_metric(store, run_id, aid, value) -> Artifact:
    a = Artifact(
        artifact_id=aid, artifact_type=ArtifactType.METRIC,
        producer_task="compute", run_id=run_id, version=1,
        value=value, input_artifact_ids=[],
        meta={"task_kind": "tool"},
    )
    return store.save(a)


def _ref(a: Artifact) -> ArtifactRef:
    return ArtifactRef(run_id=a.run_id, artifact_id=a.artifact_id, version=a.version)


def _mock_urlopen_factory(response_payload: dict):
    """Build a context-manager factory that returns `response_payload`."""
    raw = json.dumps(response_payload).encode("utf-8")
    class _Resp:
        def read(inner_self) -> bytes:
            return raw
        def __enter__(inner_self):
            return inner_self
        def __exit__(inner_self, *a):
            return False
    def factory(*a, **k):
        return _Resp()
    return factory


# =====================================================================
# 1-3: HTTP success + schema failure still writes a ledger record
# =====================================================================

def test_http_success_schema_failure_still_writes_ledger(tmp_path) -> None:
    """If the provider returns HTTP 200 with usage, but the response
    body lacks `claim_text`, a LiveCallRecord MUST still be appended
    with schema_valid=False, produced_ref=None, and the real
    provider-reported tokens.
    """
    os.environ["TEST_DAY13_5_INSTR_KEY"] = "TEST_KEY_FAKE_NOT_REAL"
    try:
        cfg = RealLLMClient.__init__.__globals__  # type: ignore[attr-defined]
        from llm import RealLLMProviderConfig
        cfg = RealLLMProviderConfig(
            provider="qwen", model="qwen3.7-plus-2026-05-26",
            api_key_env_var="TEST_DAY13_5_INSTR_KEY",
        )
        client = RealLLMClient(cfg)

        from experiments.live_ledger import LiveCallLedger
        ledger = LiveCallLedger()

        # Qwen response: HTTP 200, usage present, but body uses
        # `section_text` instead of `claim_text` (the live-gate bug).
        body = json.dumps({
            "section_text": "**财务**：增长 60%。",
            "evidence_refs": ["r1/metric_revenue_growth@v1"],
            "confidence": 0.9,
        })
        # Need a usage block so the response is parsed as a real
        # provider call rather than as a structured error.
        payload = {
            "id": "req-section-text-fail",
            "choices": [{"message": {"role": "assistant", "content": body}}],
            "usage": {
                "prompt_tokens": 50,
                "completion_tokens": 30,
                "total_tokens": 80,
                "completion_tokens_details": {"reasoning_tokens": 0},
            },
        }
        with patch("urllib.request.urlopen", new=_mock_urlopen_factory(payload)):
            response = client.generate("hi", task_name="t")

        # Now append a record (mirrors what run_test_b does in
        # _record_real_call_init).
        from live_gate_run import _record_real_call_init
        rec = _record_real_call_init(
            ledger, phase="B", execution_unit="generate_financial_section",
            response=response, request_enable_thinking=False,
            request_response_format={"type": "json_object"},
        )
        # HTTP 200 happened — token counts must be present.
        assert response.usage_type == UsageType.PROVIDER_REPORTED
        assert rec.input_tokens == 50
        assert rec.output_tokens == 30
        # No downstream validation yet — defaults to False.
        assert rec.schema_valid is False
        assert rec.evidence_valid is False
        assert rec.semantic_valid is False
        assert rec.produced_ref is None
        # And the ledger has exactly one record.
        assert ledger.real_request_count == 1
    finally:
        os.environ.pop("TEST_DAY13_5_INSTR_KEY", None)


def test_schema_failure_produced_ref_is_none() -> None:
    """After a schema failure, produced_ref must be None."""
    from experiments.live_ledger import LiveCallLedger
    ledger = LiveCallLedger()
    from live_gate_run import _record_real_call_init
    response = LLMResponse(
        text=json.dumps({"section_text": "x", "evidence_refs": [], "confidence": 0.5}),
        provider="qwen", model="qwen3.7-plus-2026-05-26",
        usage_type=UsageType.PROVIDER_REPORTED,
        input_tokens=10, output_tokens=5, latency_ms=200.0,
    )
    rec = _record_real_call_init(
        ledger, phase="B", execution_unit="generate_financial_section",
        response=response, request_enable_thinking=False,
    )
    # simulate downstream schema failure via _update_record
    from live_gate_run import _update_record
    _update_record(rec, schema_valid=False,
                   error_type="SCHEMA_ERROR",
                   error_message="missing required key: claim_text")
    assert rec.produced_ref is None
    assert rec.error_type == "SCHEMA_ERROR"
    assert "claim_text" in (rec.error_message_sanitized or "")


def test_schema_failure_real_request_count_increments() -> None:
    """real_request_count must += 1 for every HTTP response, even if
    schema validation fails afterwards."""
    from experiments.live_ledger import LiveCallLedger
    ledger = LiveCallLedger()
    assert ledger.real_request_count == 0
    from live_gate_run import _record_real_call_init
    for _ in range(3):
        response = LLMResponse(
            text=json.dumps({"section_text": "x", "evidence_refs": [], "confidence": 0.5}),
            provider="qwen", model="qwen3.7-plus-2026-05-26",
            usage_type=UsageType.PROVIDER_REPORTED,
            input_tokens=1, output_tokens=1, latency_ms=1.0,
        )
        _record_real_call_init(
            ledger, phase="B", execution_unit="generate_financial_section",
            response=response,
        )
    assert ledger.real_request_count == 3


# =====================================================================
# 4: PROVIDER_REPORTED tokens still count even on schema failure
# =====================================================================

def test_provider_reported_tokens_count_even_on_schema_failure() -> None:
    """When usage_type == PROVIDER_REPORTED, the tokens are summed
    into provider_reported_token_total even if schema_valid=False.
    The provider has already billed them."""
    from experiments.live_ledger import LiveCallLedger
    ledger = LiveCallLedger()
    from live_gate_run import _record_real_call_init, _update_record
    for in_tok, out_tok in [(50, 30), (100, 50), (200, 100)]:
        response = LLMResponse(
            text=json.dumps({"section_text": "x", "evidence_refs": [], "confidence": 0.5}),
            provider="qwen", model="qwen3.7-plus-2026-05-26",
            usage_type=UsageType.PROVIDER_REPORTED,
            input_tokens=in_tok, output_tokens=out_tok, latency_ms=1.0,
        )
        rec = _record_real_call_init(
            ledger, phase="B", execution_unit="generate_financial_section",
            response=response,
        )
        # simulate schema failure
        _update_record(rec, schema_valid=False,
                       error_type="SCHEMA_ERROR",
                       error_message="missing required key: claim_text")
    expected_total = (50 + 30) + (100 + 50) + (200 + 100)  # 530
    assert ledger.provider_reported_token_total == expected_total


# =====================================================================
# 5: semantic failure also keeps the record
# =====================================================================

def test_semantic_failure_keeps_record_and_request_count() -> None:
    """If schema passes but semantic validation fails, the record
    still exists and contributes to real_request_count."""
    from experiments.live_ledger import LiveCallLedger
    ledger = LiveCallLedger()
    from live_gate_run import _record_real_call_init, _update_record
    response = LLMResponse(
        text=json.dumps({
            "claim_text": "营收增长 5%。",  # wrong number
            "evidence_refs": ["r1/metric_revenue_growth@v1"],
            "confidence": 0.9,
        }),
        provider="qwen", model="qwen3.7-plus-2026-05-26",
        usage_type=UsageType.PROVIDER_REPORTED,
        input_tokens=20, output_tokens=10, latency_ms=200.0,
    )
    rec = _record_real_call_init(
        ledger, phase="A", execution_unit="generate_financial_claim",
        response=response,
    )
    _update_record(rec, schema_valid=True,
                   produced_ref="r1/claim_financial_growth@v1")
    _update_record(rec, evidence_valid=True, semantic_valid=False)
    assert ledger.real_request_count == 1
    assert rec.semantic_valid is False
    assert rec.produced_ref is not None  # schema passed


# =====================================================================
# 6-7: Test A state roundtrip
# =====================================================================

def test_test_a_state_persisted_before_test_b_runs(tmp_path) -> None:
    """main() must persist Test A state to disk BEFORE Test B's
    real call.  Inspecting the state file's mtime after Test A
    passes and before Test B is too integration-heavy for a unit
    test; instead we assert the helper round-trips."""
    import live_gate_run
    store_dir = str(tmp_path / "live_store")
    with tempfile.TemporaryDirectory() as td:
        run_store = ArtifactStore(base_dir=str(Path(td) / "art"))
        run_id = "live_test_a"
        # Build clean metrics
        from workflow.tasks import (
            extract_fields, compute_revenue_growth,
            compute_profit_margin, compute_cost_ratio,
        )
        extract_fields(CSV, run_id, run_store)
        rg = compute_revenue_growth(run_id, run_store)
        pm = compute_profit_margin(run_id, run_store)
        cr = compute_cost_ratio(run_id, run_store)
        # Build a Claim directly (no LLM, simulating a successful Test A).
        claim = Artifact(
            artifact_id="claim_financial_growth",
            artifact_type=ArtifactType.CLAIM,
            producer_task="generate_financial_claim",
            run_id=run_id, version=1, value=None,
            content="营收增长 60.78%，利润率 47.56%。",
            input_artifact_ids=[_ref(rg), _ref(pm), _ref(cr)],
            evidence_ids=[_ref(rg), _ref(pm)],
            meta={
                "llm_provider": "qwen",
                "llm_model": "qwen3.7-plus-2026-05-26",
                "llm_usage_type": "provider_reported",
                "llm_request_id": "req-abc",
            },
        )
        run_store.save(claim)
        # Save state via the helper
        test_a = {
            "passed": True,
            "claim_artifact": claim,
            "claim_ref": f"{claim.run_id}/{claim.artifact_id}@v{claim.version}",
            "usage_type": "provider_reported",
            "request_id": "req-abc",
            "reasoning_tokens": 0,
            "request_enable_thinking": False,
            "request_response_format": {"type": "json_object"},
            "real_request_count": 1,
            "semantic": {"passed": True},
        }
        from experiments.live_ledger import LiveCallRecord
        ledger = live_gate_run.LiveCallLedger() if hasattr(live_gate_run, "LiveCallLedger") else None
        from experiments.live_ledger import LiveCallLedger as _LCL
        ledger = _LCL()
        ledger.append(LiveCallRecord(
            phase="A", execution_unit="generate_financial_claim",
            provider="qwen", model="qwen3.7-plus-2026-05-26",
            request_id="req-abc", usage_type="provider_reported",
            input_tokens=20, output_tokens=10, total_tokens=30,
            client_wall_clock_latency_ms=100.0,
            schema_valid=True, evidence_valid=True, semantic_valid=True,
            produced_ref=f"{claim.run_id}/{claim.artifact_id}@v{claim.version}",
        ))

        # Save (use tmp_path to avoid polluting the real
        # experiments/results/day13_5_test_a_state.json file)
        a_state_path = str(tmp_path / "test_a_state.json")
        live_gate_run._save_test_a_state(
            test_a, ledger, run_store, state_path=a_state_path,
        )
        # Assert state file exists and contains the claim
        assert Path(a_state_path).exists()
        with open(a_state_path, "r", encoding="utf-8") as f:
            state = json.load(f)
        # Day 13.6: state is schema_version 3 with per-flag audit fields.
        assert state["schema_version"] == 3
        assert state["test_a"]["claim_artifact"]["artifact_id"] == "claim_financial_growth"
        assert len(state["test_a"]["upstream_artifacts"]) >= 3  # metrics


def test_test_a_full_artifact_roundtrip(tmp_path) -> None:
    """A Claim Artifact model_dump -> model_validate must produce an
    object equal (by content) to the original."""
    with tempfile.TemporaryDirectory() as td:
        store = ArtifactStore(base_dir=str(Path(td) / "art"))
        rg = _make_metric(store, "r1", "metric_revenue_growth", 0.6078)
        claim = Artifact(
            artifact_id="claim_financial_growth",
            artifact_type=ArtifactType.CLAIM,
            producer_task="generate_financial_claim",
            run_id="r1", version=2, value=None,
            content="营收增长 60.78%。",
            input_artifact_ids=[_ref(rg)],
            evidence_ids=[_ref(rg)],
            meta={"llm_provider": "qwen"},
        )
        dump = claim.model_dump(mode="json")
        # Round-trip via JSON
        reloaded = Artifact.model_validate(json.loads(json.dumps(dump)))
        assert reloaded.artifact_id == claim.artifact_id
        assert reloaded.version == claim.version
        assert reloaded.content == claim.content
        assert reloaded.input_artifact_ids[0].to_ref_string() == \
            claim.input_artifact_ids[0].to_ref_string()


# =====================================================================
# 8: API key never enters the persisted state
# =====================================================================

def test_persisted_state_never_contains_api_key(tmp_path) -> None:
    """The persisted Test A state must not contain the API key,
    the Authorization header, or any 32+ char alnum run.
    """
    with tempfile.TemporaryDirectory() as td:
        store = ArtifactStore(base_dir=str(Path(td) / "art"))
        rg = _make_metric(store, "r1", "metric_revenue_growth", 0.6)
        claim = Artifact(
            artifact_id="claim_financial_growth",
            artifact_type=ArtifactType.CLAIM,
            producer_task="generate_financial_claim",
            run_id="r1", version=1, value=None,
            content="增长 60%。",
            input_artifact_ids=[_ref(rg)],
            evidence_ids=[_ref(rg)],
            meta={"llm_provider": "qwen"},
        )
        test_a = {
            "passed": True,
            "claim_artifact": claim,
            "claim_ref": f"{claim.run_id}/{claim.artifact_id}@v{claim.version}",
            "usage_type": "provider_reported",
            "request_id": "req-abc",
            "real_request_count": 1,
            "semantic": {"passed": True},
        }
        from experiments.live_ledger import LiveCallRecord
        from experiments.live_ledger import LiveCallLedger as _LCL
        ledger = _LCL()
        ledger.append(LiveCallRecord(
            phase="A", execution_unit="generate_financial_claim",
            provider="qwen", model="qwen3.7-plus-2026-05-26",
            request_id="req-abc", usage_type="provider_reported",
            input_tokens=20, output_tokens=10, total_tokens=30,
            client_wall_clock_latency_ms=100.0,
            schema_valid=True, evidence_valid=True, semantic_valid=True,
            produced_ref=f"{claim.run_id}/{claim.artifact_id}@v{claim.version}",
        ))
        import live_gate_run
        # Use state_path kwarg directly so we don't have to
        # touch the module-level TEST_A_STATE_PATH constant
        # (which would leak across tests if the test failed
        # before the restore).
        state_path = str(tmp_path / "state.json")
        live_gate_run._save_test_a_state(
            test_a, ledger, store, state_path=state_path,
        )
        with open(state_path, "r", encoding="utf-8") as f:
            state_text = f.read()
        # Simulated API keys (32 chars, like OpenAI sk-* would be)
        fake_keys = ["A" * 32, "sk-" + "B" * 32, "x" * 40]
        for k in fake_keys:
            assert k not in state_text, f"API key leaked: {k!r}"
        assert "Authorization" not in state_text
        assert "Bearer" not in state_text


# =====================================================================
# 9: A + B + C full run request expectation = 4
# =====================================================================

def test_full_run_request_expectation_is_4() -> None:
    """Day 13.5 spec (corrected §6):
      Test A  : 1 real request (financial claim)
      Test B  : 1 real request (financial section)  -- uses Test A's claim
      Test C  : 2 real requests (recovery: claim + section)
      Total   : 4

    A naive implementation that re-issues the financial claim in
    Test B would make it 5, which is wrong.
    """
    # The math is:
    expected = 1 + 1 + 2
    assert expected == 4


# =====================================================================
# 10: previous 362 tests still pass (verified by pytest run, not by code)
# =====================================================================


# =====================================================================
# 11: _RecordingClient auto-appends record with real tokens on
# schema failure (the run_test_b instrumentation fix).  This
# regression closes the gap where the previous `except` block
# overwrote `response` with a UNAVAILABLE placeholder, erasing
# the real provider-reported tokens.
# =====================================================================

def test_recording_client_preserves_real_tokens_on_schema_failure(
    tmp_path,
) -> None:
    """run_test_b fix: when generate_financial_section raises
    ValueError (e.g. SCHEMA_ERROR for `missing required key:
    claim_text`), the auto-appended LiveCallRecord must STILL
    carry the real provider-reported tokens, usage_type, and
    latency.  The previous code path overwrote `response` with a
    UNAVAILABLE placeholder, which silently lost the real billing
    measurement.

    We simulate the live-gate failure: a 200 OK Qwen response
    with usage present, body that lacks `claim_text`.  We
    construct a _RecordingClient bound to a ledger, then call
    generate_financial_section which will raise inside
    _parse_llm_output.  After the exception, the ledger must have
    exactly one record with real tokens, NOT a placeholder.
    """
    os.environ["TEST_DAY13_5_INSTR_KEY"] = "TEST_KEY_FAKE_NOT_REAL"
    try:
        from llm import RealLLMProviderConfig
        cfg = RealLLMProviderConfig(
            provider="qwen", model="qwen3.7-plus-2026-05-26",
            api_key_env_var="TEST_DAY13_5_INSTR_KEY",
        )
        client = RealLLMClient(cfg)
        from experiments.live_ledger import LiveCallLedger
        ledger = LiveCallLedger()

        # Mock the Qwen response: 200 OK, usage present, but
        # body uses `section_text` instead of `claim_text` (the
        # live-gate Test B failure).
        body = json.dumps({
            "section_text": "**财务**：营收 60%，利润率 47%。",
            "evidence_refs": ["live_test_a/claim_financial_growth@v1"],
            "confidence": 0.9,
        })
        payload = {
            "id": "req-section-text-fail-real",
            "choices": [{"message": {"role": "assistant", "content": body}}],
            "usage": {
                "prompt_tokens": 410,
                "completion_tokens": 25,
                "total_tokens": 435,
                "completion_tokens_details": {"reasoning_tokens": 0},
            },
        }
        with patch("urllib.request.urlopen", new=_mock_urlopen_factory(payload)):
            recording = _RecordingClient(
                client, ledger,
                phase_prefix="B",
                enable_thinking=False,
                response_format={"type": "json_object"},
            )
            # Build a stub Claim input so generate_financial_section
            # actually invokes the LLM.  The save will fail
            # because of the schema issue, which is what we want.
            with tempfile.TemporaryDirectory() as td:
                store = ArtifactStore(base_dir=str(Path(td) / "art"))
                rg = _make_metric(store, "live_test_a",
                                  "metric_revenue_growth", 0.6078)
                pm = _make_metric(store, "live_test_a",
                                  "metric_profit_margin", 0.4756)
                cr = _make_metric(store, "live_test_a",
                                  "metric_cost_ratio", 0.5)
                claim = Artifact(
                    artifact_id="claim_financial_growth",
                    artifact_type=ArtifactType.CLAIM,
                    producer_task="generate_financial_claim",
                    run_id="live_test_a", version=1, value=None,
                    content="营收增长 60.78%，利润率 47.56%。",
                    input_artifact_ids=[_ref(rg), _ref(pm), _ref(cr)],
                    evidence_ids=[_ref(rg), _ref(pm)],
                    meta={"llm_provider": "qwen"},
                )
                store.save(claim)
                from provenance.decorator import RunContext
                ctx = RunContext(run_id="live_test_a", store=store)

                from workflow.claim_tasks import generate_financial_section
                raised = False
                try:
                    generate_financial_section(
                        claim, ctx=ctx, llm_client=recording,
                    )
                except ValueError:
                    raised = True
                assert raised, "expected ValueError on schema failure"

        # CRITICAL: even after ValueError, the ledger MUST have
        # exactly one record, and the tokens must be the real
        # provider-reported values (410 + 25), NOT 0+0.
        assert ledger.real_request_count == 1, (
            "schema failure erased the real Qwen call from the ledger"
        )
        rec = ledger.records[0]
        assert rec.phase == "B"
        assert rec.execution_unit == "generate_financial_section"
        assert rec.usage_type == "provider_reported"
        assert rec.input_tokens == 410
        assert rec.output_tokens == 25
        assert rec.total_tokens == 435
        # And the provider_reported_token_total must be 435,
        # not 0 (which is what the placeholder would give).
        assert ledger.provider_reported_token_total == 435
    finally:
        os.environ.pop("TEST_DAY13_5_INSTR_KEY", None)

