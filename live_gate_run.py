"""
Live Gate Test A / B / C — Day 13.5.

This script proves that the RealLLMClient wiring works end-to-end on a
real provider.  It writes every successful real call into a
LiveCallLedger (experiments/live_ledger.py) and refuses to mark the
gate as passed unless:

  Test A — 1 real call:  generate_financial_claim
            schema_valid, evidence_valid, semantic (numeric + direction
            + recommendation) all pass.

  Test B — 1 real call:  generate_financial_section using Test A's
            claim output as input.
            schema_valid, evidence_valid, semantic (recommendation
            + numeric consistency) all pass.

  Test C — selective recovery on Case A.  Fault snapshot is
            constructed with deterministic / Fake tools, so it does
            NOT consume real LLM budget.  Only the RECOVERY PHASE
            uses RealLLMClient.  The expected real-LLM call count
            during the recovery phase is exactly 2
            (generate_financial_claim + generate_financial_section).
            User / Operation branches are NOT recovered because they
            are unaffected in Case A; the merge_report input
            provenance must show recovered financial + clean user
            + clean operation.

If REAL_LLM_API_KEY is not set, the script SKIPS the live tests and
prints the unchanged "credential unavailable" gate JSON.

It also refuses to log any part of the API key.  Only usage_type,
token counts, latency, and request_id are recorded.  If the provider
echoes the API key in any error body, the ledger redaction filter
masks it before write.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from artifacts import Artifact, ArtifactRef, ArtifactStore, ArtifactStatus
from contamination.propagation import propagate_contamination
from contamination.validator import detect_field_faults
from experiments.fault_injection import inject_field_value_tamper
from experiments.live_ledger import (
    LiveCallLedger,
    LiveCallRecord,
    build_day13_5_live_gate,
)
from llm import (
    LLMResponse,
    RealLLMClient,
    RealLLMError,
    RealLLMProviderConfig,
    UsageType,
)
from provenance.decorator import RunContext
from provenance.graph import ArtifactGraph
from recovery.executor import RecoveryExecutor
from recovery.planner import SelectiveRecoveryPlanner
from validation import validate_claim, validate_section
from workflow.claim_tasks import (
    generate_financial_claim,
    generate_financial_section,
)
from workflow.runner import run_baseline_workflow
from workflow.tasks import extract_fields


CSV = str(Path(__file__).resolve().parent / "data" / "sample_company.csv")


# =====================================================================
# Helpers
# =====================================================================

def _hr(t: str) -> None:
    print()
    print("=" * 80)
    print(t)
    print("=" * 80)


def _make_real_client_or_skip() -> RealLLMClient:
    """Return a configured RealLLMClient or print SKIP and sys.exit(0).

    Day 13.5 Qwen-specific contract:
      - provider     = "qwen"   (env-overridable)
      - model        = "qwen3.7-plus-2026-05-26"  (frozen snapshot, NOT a rolling alias)
      - base_url     = "https://dashscope.aliyuncs.com/compatible-mode/v1"
                       (SDK-style; the adapter will append /chat/completions
                        ONCE; no /v1 double-append)
      - response_format = {"type": "json_object"}  (Day 13.5: prefer structured output)
      - provider_options = {"enable_thinking": False}  (Qwen hybrid-thinking OFF)
      - temperature  = 0.0
      - seed         = 1234

    Test A, B, C MUST use the same config so benchmark numbers are
    comparable.
    """
    api_key = os.environ.get("REAL_LLM_API_KEY")
    if not api_key:
        print("[SKIP] REAL_LLM_API_KEY not set; live tests skipped.")
        sys.exit(0)
    cfg = RealLLMProviderConfig(
        provider=os.environ.get("REAL_LLM_PROVIDER", "qwen"),
        model=os.environ.get("REAL_LLM_MODEL", "qwen3.7-plus-2026-05-26"),
        base_url=os.environ.get("REAL_LLM_BASE_URL")
                 or "https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key_env_var="REAL_LLM_API_KEY",
        timeout_seconds=float(os.environ.get("REAL_LLM_TIMEOUT", "30")),
        max_retries=1,
        temperature=0.0,
        seed=1234,
        # Day 13.5: prefer structured output over prompt-only coercion.
        # `json_object` is the conservative Qwen-supported level; we
        # do NOT yet switch to a full json_schema because the Claim
        # contract still has to pass ClaimOutput.model_validate on
        # every response (provider compliance is a hint, not a
        # substitute for our schema validation).
        response_format={"type": "json_object"},
        # Day 13.5: Qwen hybrid-thinking is OFF for short structured
        # claim / section tasks.  If left default (True) the provider
        # would burn reasoning tokens we never asked for.  Placed at
        # the TOP LEVEL of the JSON body (direct HTTP), not under
        # extra_body.
        provider_options={"enable_thinking": False},
    )
    print(f"  provider                 = {cfg.provider}")
    print(f"  model                    = {cfg.model}")
    print(f"  base_url                 = {cfg.base_url}")
    print(f"  endpoint                 = {RealLLMClient(cfg)._endpoint()}")
    print(f"  response_format          = {cfg.response_format}")
    print(f"  provider_options         = {cfg.provider_options}")
    print(f"  temperature              = {cfg.temperature}")
    print(f"  seed                     = {cfg.seed}")
    return RealLLMClient(cfg)


def _ledger_record_from_response(
    *,
    phase: str,
    execution_unit: str,
    response: LLMResponse,
    schema_valid: bool,
    evidence_valid: bool,
    semantic_valid: bool,
    produced_ref: Optional[str],
    error_type: Optional[str] = None,
    error_message: Optional[str] = None,
    request_enable_thinking: Optional[bool] = None,
    request_response_format: Optional[dict] = None,
) -> LiveCallRecord:
    return LiveCallRecord(
        phase=phase,
        execution_unit=execution_unit,
        provider=response.provider,
        model=response.model,
        request_id=response.request_id,
        usage_type=response.usage_type.value,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        total_tokens=response.input_tokens + response.output_tokens,
        client_wall_clock_latency_ms=response.latency_ms,
        schema_valid=schema_valid,
        evidence_valid=evidence_valid,
        semantic_valid=semantic_valid,
        produced_ref=produced_ref,
        error_type=error_type,
        error_message_sanitized=(
            _sanitize(error_message) if error_message else None
        ),
        reasoning_tokens=response.reasoning_tokens,
        request_enable_thinking=request_enable_thinking,
        request_response_format=request_response_format,
    )


def _sanitize(msg: str) -> str:
    """Mask anything that looks like an API key (16+ alnum/-/_)."""
    import re
    return re.sub(r"[A-Za-z0-9_\-]{16,}", "[REDACTED]", msg or "")


def _record_real_call_init(
    ledger: LiveCallLedger,
    *,
    phase: str,
    execution_unit: str,
    response: LLMResponse,
    request_enable_thinking: Optional[bool] = None,
    request_response_format: Optional[dict] = None,
) -> LiveCallRecord:
    """Append a LiveCallRecord initialized from a real LLMResponse.

    Day 13.5 spec (instrumentation): every real HTTP request that
    returned a response MUST produce a ledger record, regardless of
    whether downstream validation (schema, evidence, semantic) and
    Artifact materialization later succeed.  This function is the
    "HTTP response returned" hook.  Subsequent steps mutate the
    returned record's fields via `_update_record`.

    Tokens are recorded verbatim from the response.  When
    `usage_type == PROVIDER_REPORTED` the tokens are real-provider
    measurements and are summed into `provider_reported_token_total`
    even if validation later fails.
    """
    rec = LiveCallRecord(
        phase=phase,
        execution_unit=execution_unit,
        provider=response.provider,
        model=response.model,
        request_id=response.request_id,
        usage_type=response.usage_type.value,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        total_tokens=response.input_tokens + response.output_tokens,
        # Client-side request wall clock.  Even if a downstream
        # exception is raised, this is the real latency of the
        # request and must not be lost.
        client_wall_clock_latency_ms=response.latency_ms,
        # All three validation flags start as False; they are flipped
        # to True by `_update_record` after each step completes.
        schema_valid=False,
        evidence_valid=False,
        semantic_valid=False,
        # produced_ref is filled in by `_update_record` only if
        # materialization actually succeeded.
        produced_ref=None,
        reasoning_tokens=response.reasoning_tokens,
        request_enable_thinking=request_enable_thinking,
        request_response_format=request_response_format,
    )
    ledger.append(rec)
    return rec


def _update_record(
    rec: LiveCallRecord,
    *,
    schema_valid: Optional[bool] = None,
    evidence_valid: Optional[bool] = None,
    semantic_valid: Optional[bool] = None,
    produced_ref: Optional[str] = None,
    error_type: Optional[str] = None,
    error_message: Optional[str] = None,
) -> None:
    """Mutate an existing LiveCallRecord with downstream validation
    outcomes.  Each kwarg is optional; only the ones passed are
    updated."""
    if schema_valid is not None:
        rec.schema_valid = schema_valid
    if evidence_valid is not None:
        rec.evidence_valid = evidence_valid
    if semantic_valid is not None:
        rec.semantic_valid = semantic_valid
    if produced_ref is not None:
        rec.produced_ref = produced_ref
    if error_type is not None:
        rec.error_type = error_type
    if error_message is not None:
        rec.error_message_sanitized = _sanitize(error_message)


def _try_parse_error(response: LLMResponse) -> Optional[dict]:
    """If the response text is a structured error JSON, return it."""
    try:
        return json.loads(response.text)
    except Exception:
        return None


# =====================================================================
# Test A — one real call to generate_financial_claim
# =====================================================================

class _RecordingClient:
    """Wraps a real client and remembers the LAST response.

    Day 13.5 instrumentation: when constructed with `ledger=`,
    `phase_prefix=`, `enable_thinking=`, `response_format=`, every
    `generate` call AUTOMATICALLY appends an initial LiveCallRecord
    to the ledger ("HTTP response returned" hook).  Downstream
    validation steps can then mutate the record via
    `_update_record`.

    The record's `phase` is `phase_prefix` and `execution_unit` is
    the `task_name` argument passed to `generate`.  This lets Test C
    (which calls LLM through `RecoveryExecutor` and doesn't directly
    see the responses) still produce one ledger record per real
    HTTP request, in order.
    """
    def __init__(
        self,
        inner: RealLLMClient,
        ledger: Optional[LiveCallLedger] = None,
        phase_prefix: str = "",
        enable_thinking: Optional[bool] = None,
        response_format: Optional[dict] = None,
    ):
        self.inner = inner
        self.last_response: Optional[LLMResponse] = None
        self.all_responses: list[LLMResponse] = []
        # Parallel list to all_responses: the ledger record that
        # corresponds to each `generate` call (in order).  When
        # `ledger is None` this stays empty and the wrapper degrades
        # to the Day-13 behavior (response-only recording).
        self.records: list[LiveCallRecord] = []
        self.ledger = ledger
        self.phase_prefix = phase_prefix
        self._enable_thinking = enable_thinking
        self._response_format = response_format

    def generate(self, prompt: str, *, task_name: str = "") -> LLMResponse:  # type: ignore[override]
        r = self.inner.generate(prompt, task_name=task_name)
        self.last_response = r
        self.all_responses.append(r)
        if self.ledger is not None:
            rec = _record_real_call_init(
                self.ledger,
                phase=self.phase_prefix,
                execution_unit=task_name,
                response=r,
                request_enable_thinking=self._enable_thinking,
                request_response_format=self._response_format,
            )
            self.records.append(rec)
        return r


def _request_echo(real_client: RealLLMClient) -> tuple[Optional[bool], Optional[dict]]:
    """Return what this client will send in the next request body:
        (enable_thinking, response_format).
    None means that key is not present in the body.
    """
    enable_thinking = real_client.config.provider_options.get("enable_thinking")
    response_format = real_client.config.response_format
    return enable_thinking, response_format


def run_test_a(
    real_client: RealLLMClient,
    ledger: LiveCallLedger,
    *,
    store_dir: str,
) -> dict:
    """Returns a result dict with `passed: bool` and details.

    Day 13.5 instrumentation:
      - Every real HTTP request that returned a response produces a
        LiveCallRecord.  This is true even when downstream
        validation / materialization fails.
      - The token counts are written verbatim from the provider
        response.  When `usage_type == PROVIDER_REPORTED`, the tokens
        are summed into `provider_reported_token_total` regardless of
        the validation outcome (the provider has already billed them).
      - `store_dir` is a PERSISTENT directory (NOT a tempdir) so the
        saved artifacts remain available for `--from-test B` to
        rebuild a working in-memory store and re-run the section
        call without re-issuing Test A.
    """
    _hr("TEST A — generate_financial_claim (real call)")

    enable_thinking, response_format = _request_echo(real_client)
    print(f"  request.enable_thinking = {enable_thinking}")
    print(f"  request.response_format = {response_format}")

    Path(store_dir).mkdir(parents=True, exist_ok=True)
    store = ArtifactStore(base_dir=store_dir)
    run_id = "live_test_a"
    # 1) deterministic tool path to produce clean metric v1s
    extract_fields(CSV, run_id, store)
    from workflow.tasks import (
        compute_revenue_growth,
        compute_profit_margin,
        compute_cost_ratio,
    )
    rg = compute_revenue_growth(run_id, store)
    pm = compute_profit_margin(run_id, store)
    cr = compute_cost_ratio(run_id, store)

    # 2) one real LLM call (wrapped so we can read the response).
    #    The wrapper AUTO-APPENDS a LiveCallRecord to the ledger on
    #    every `generate` call (the "HTTP response returned" hook),
    #    BEFORE any downstream parse / schema / evidence / semantic
    #    validation.  When _parse_llm_output raises ValueError
    #    (e.g. schema FAIL), the real response is preserved in
    #    `recording.last_response` and the record it created
    #    already has the real tokens.  We do NOT overwrite `response`
    #    in the except block — the placeholder would erase the
    #    provider-reported tokens that the user has already been
    #    billed for.
    ctx = RunContext(run_id=run_id, store=store)
    recording = _RecordingClient(
        real_client, ledger,
        phase_prefix="A",
        enable_thinking=enable_thinking,
        response_format=response_format,
    )
    claim = None
    schema_error: Optional[str] = None
    try:
        claim = generate_financial_claim(
            rg, pm, cr, ctx=ctx, llm_client=recording,
        )
    except Exception as e:
        # The real HTTP call already happened and the wrapper
        # already wrote a ledger record with real tokens.  Capture
        # the failure reason for the record's error_type / message,
        # but do NOT touch `recording.last_response`.
        schema_error = f"{type(e).__name__}: {e}"

    response = recording.last_response
    if response is None:
        # The wrapper did not record any call; that means the
        # transport itself never returned.  In that case the
        # ledger has no record.  Synthesize a UNAVAILABLE record
        # so `real_request_count` is still 1 for the user-visible
        # attempt, with usage_type=UNAVAILABLE.
        response = LLMResponse(
            text=json.dumps({"error": "TRANSPORT_ERROR",
                             "message": "no response recorded"}),
            provider=real_client.config.provider,
            model=real_client.config.model,
            usage_type=UsageType.UNAVAILABLE,
            latency_ms=0.0,
        )
        rec = _record_real_call_init(
            ledger,
            phase="A",
            execution_unit="generate_financial_claim",
            response=response,
            request_enable_thinking=enable_thinking,
            request_response_format=response_format,
        )
        return {
            "passed": False,
            "error": {"error": "TRANSPORT_ERROR",
                      "detail": "no response recorded"},
            "real_request_count": 1,
            "phase_record": rec,
            "store_dir": store_dir,
        }

    # 3) The wrapper already appended a record; reuse it (last
    # entry) instead of creating a duplicate.
    rec = recording.records[-1]

    if schema_error is not None:
        # The call happened, parse/schema/evidence/semantic failed
        # AFTER the HTTP response.  Mutate the auto-created record
        # with the failure reason.  Tokens stay verbatim.
        _update_record(
            rec,
            error_type=(
                "SCHEMA_ERROR" if "claim_text" in schema_error or
                "ValidationError" in schema_error or
                "missing" in schema_error else "EXCEPTION"
            ),
            error_message=schema_error,
        )
        return {
            "passed": False,
            "error": {"error": "SCHEMA_ERROR", "detail": schema_error},
            "real_request_count": 1,
            "phase_record": rec,
            "store_dir": store_dir,
        }

    # 4) structured parse — was the response itself a JSON object?
    parse_err = _try_parse_error(response)
    if parse_err is not None and "error" in parse_err:
        print(f"  [FAIL] Real call errored at parse: {parse_err}")
        _update_record(
            rec,
            error_type=parse_err.get("error"),
            error_message=parse_err.get("message"),
        )
        return {
            "passed": False, "error": parse_err,
            "real_request_count": 1,
            "phase_record": rec,
            "store_dir": store_dir,
        }

    # 5) schema validation already passed inside _parse_llm_output
    _update_record(
        rec,
        schema_valid=True,
        produced_ref=f"{claim.run_id}/{claim.artifact_id}@v{claim.version}",
    )

    # 6) evidence + semantic validation
    semantic = validate_claim(
        claim_text=claim.content,
        evidence_refs_in_text=list(claim.evidence_ids),
        store=store,
        evidence_policy="financial_claim",
    )
    _update_record(
        rec,
        evidence_valid=semantic.evidence_valid,
        semantic_valid=semantic.passed,
    )
    print(f"  semantic.passed        = {semantic.passed}")
    print(f"  provider               = {claim.meta.get('llm_provider')}")
    print(f"  model                  = {claim.meta.get('llm_model')}")
    print(f"  usage_type             = {claim.meta.get('llm_usage_type')}")
    print(f"  usage_synthetic        = {claim.meta.get('llm_usage_is_synthetic')}")
    print(f"  reasoning_tokens       = {response.reasoning_tokens}")
    print(f"  request_id             = {claim.meta.get('llm_request_id')}")
    print(f"  evidence_ids           = {[r.to_ref_string() for r in claim.evidence_ids]}")
    print(f"  content                = {claim.content}")
    print(f"  issues                 = {semantic.issues}")

    return {
        "passed": semantic.passed,
        # Day 13.6: explicit per-flag fields so the resume gate
        # (Test A state) can be audited without re-running the
        # nested semantic.model_dump() each time.  These are the
        # six flags the spec requires for a Test A state to be
        # acceptable for `--from-test B`.
        "schema_valid": True,
        "evidence_valid": semantic.evidence_valid,
        "numeric_consistency_pass": semantic.numeric_consistency_pass,
        "direction_consistency_pass": semantic.direction_consistency_pass,
        "recommendation_boundary_pass": semantic.recommendation_boundary_pass,
        "semantic_valid": semantic.passed,
        "semantic": semantic.model_dump(),
        "claim_ref": f"{claim.run_id}/{claim.artifact_id}@v{claim.version}",
        "usage_type": claim.meta.get("llm_usage_type"),
        "request_id": claim.meta.get("llm_request_id"),
        "reasoning_tokens": response.reasoning_tokens,
        "request_enable_thinking": enable_thinking,
        "request_response_format": response_format,
        "real_request_count": 1,
        "phase_record": rec,
        "claim_artifact": claim,
        "store_dir": store_dir,
    }


# =====================================================================
# Test B — one real call to generate_financial_section
# =====================================================================

def run_test_b(
    real_client: RealLLMClient,
    ledger: LiveCallLedger,
    *,
    test_a: dict,
) -> dict:
    """Returns a result dict with `passed: bool` and details.

    Day 13.5 contract (spec §6):
      - Test B uses Test A's REAL claim (NOT a re-issued one).  This
        keeps the A -> B -> C real-request count at exactly 4
        (1 A + 1 B + 2 C), not 5.
      - Test B issues exactly 1 real call (the section).
      - Every real HTTP response produces a LiveCallRecord, even if
        downstream validation fails.
    """
    _hr("TEST B — generate_financial_section (real call, 1 request)")

    enable_thinking, response_format = _request_echo(real_client)
    print(f"  request.enable_thinking = {enable_thinking}")
    print(f"  request.response_format = {response_format}")

    if not test_a.get("passed"):
        print("  Test A is not PASS; refusing Test B.")
        return {
            "passed": False, "error": "Test A not PASS",
            "real_request_count": 0, "status": "NOT_EXECUTED",
        }

    claim = test_a.get("claim_artifact")
    if claim is None:
        print("  Test A's claim_artifact is missing; cannot resume Test B.")
        return {
            "passed": False,
            "error": "Test A claim_artifact missing",
            "real_request_count": 0, "status": "NOT_EXECUTED",
        }

    # Re-attach claim to a fresh, throwaway store so the section
    # call can run.  The store is not persisted (we only need the
    # Claim + its recorded evidence for the section call).
    with tempfile.TemporaryDirectory() as td:
        store = ArtifactStore(base_dir=str(Path(td) / "art_b"))
        # Persist the claim so generate_financial_section's
        # evidence-subset / store-lookup checks pass.
        store.save(claim)
        ctx = RunContext(run_id=claim.run_id, store=store)

        # The wrapper AUTO-APPENDS a LiveCallRecord to the ledger
        # on every `generate` call (the "HTTP response returned"
        # hook), BEFORE any downstream parse / schema / evidence /
        # semantic validation.  When _parse_llm_output raises
        # ValueError (e.g. SCHEMA_ERROR), the real response is
        # preserved in `recording.last_response` and the record it
        # created already has the real tokens.  We do NOT overwrite
        # `response` in the except block — the placeholder would
        # erase the provider-reported tokens the user has already
        # been billed for.
        recording = _RecordingClient(
            real_client, ledger,
            phase_prefix="B",
            enable_thinking=enable_thinking,
            response_format=response_format,
        )
        section = None
        schema_error: Optional[str] = None
        try:
            section = generate_financial_section(
                claim, ctx=ctx, llm_client=recording,
            )
        except Exception as e:
            schema_error = f"{type(e).__name__}: {e}"

        response = recording.last_response
        if response is None:
            # The wrapper did not record any call; transport
            # itself never returned.  Synthesize a UNAVAILABLE
            # record so `real_request_count` is still 1 for the
            # user-visible attempt.
            response = LLMResponse(
                text=json.dumps({"error": "TRANSPORT_ERROR",
                                 "message": "no response recorded"}),
                provider=real_client.config.provider,
                model=real_client.config.model,
                usage_type=UsageType.UNAVAILABLE,
                latency_ms=0.0,
            )
            rec = _record_real_call_init(
                ledger,
                phase="B",
                execution_unit="generate_financial_section",
                response=response,
                request_enable_thinking=enable_thinking,
                request_response_format=response_format,
            )
            return {
                "passed": False,
                "error": {"error": "TRANSPORT_ERROR",
                          "detail": "no response recorded"},
                "real_request_count": 1, "phase_record": rec,
            }

        # Reuse the auto-appended record (the last one).
        rec = recording.records[-1]

        if schema_error is not None:
            _update_record(
                rec,
                error_type=(
                    "SCHEMA_ERROR" if "claim_text" in schema_error or
                    "ValidationError" in schema_error or
                    "missing" in schema_error else "EXCEPTION"
                ),
                error_message=schema_error,
            )
            return {
                "passed": False,
                "error": {"error": "SCHEMA_ERROR", "detail": schema_error},
                "real_request_count": 1, "phase_record": rec,
            }

        # structured parse
        parse_err = _try_parse_error(response)
        if parse_err is not None and "error" in parse_err:
            _update_record(rec, error_type=parse_err.get("error"),
                           error_message=parse_err.get("message"))
            return {
                "passed": False, "error": parse_err,
                "real_request_count": 1, "phase_record": rec,
            }

        if section is None:
            _update_record(rec, error_type="EXCEPTION",
                           error_message="section not produced")
            return {
                "passed": False, "error": "SECTION_NOT_PRODUCED",
                "real_request_count": 1, "phase_record": rec,
            }

        # 1) Check for the day-13.5 bug: model returned `section_text`
        # instead of `claim_text`.  _parse_llm_output raises ValueError
        # before this point, but if the response is plain JSON the
        # missing-key detection runs inside ClaimOutput.model_validate.
        # Detect that and record SCHEMA_ERROR instead of treating as
        # generic error.
        if section is not None and not getattr(section, "meta", {}).get("llm_provider"):
            # No metadata means parse failed before _parse_llm_output
            # could attach it.  This is a hard schema error.
            _update_record(
                rec, schema_valid=False,
                error_type="SCHEMA_ERROR",
                error_message="missing required key: claim_text",
            )
            return {
                "passed": False,
                "error": {"error": "SCHEMA_ERROR",
                          "detail": "missing required key: claim_text"},
                "real_request_count": 1, "phase_record": rec,
            }

        # 2) schema passed inside _parse_llm_output
        _update_record(
            rec, schema_valid=True,
            produced_ref=f"{section.run_id}/{section.artifact_id}@v{section.version}",
        )

        # 3) evidence + semantic validation
        semantic = validate_section(
            section_text=section.content,
            section_evidence_refs=list(section.evidence_ids),
            claim_evidence_refs=list(claim.evidence_ids),
            store=store,
            evidence_policy="financial_section",
        )
        _update_record(
            rec,
            evidence_valid=semantic.evidence_valid,
            semantic_valid=semantic.passed,
        )
        print(f"  semantic.passed        = {semantic.passed}")
        print(f"  section.content        = {section.content}")
        print(f"  section.evidence_ids   = {[r.to_ref_string() for r in section.evidence_ids]}")
        print(f"  reasoning_tokens       = {response.reasoning_tokens}")
        print(f"  issues                 = {semantic.issues}")

        return {
            "passed": semantic.passed,
            # Day 13.6 follow-up: explicit per-flag fields so the
            # live gate's gate_checks can audit Test B without
            # re-running the nested semantic.model_dump().
            "schema_valid": True,
            "evidence_valid": semantic.evidence_valid,
            "semantic_valid": semantic.passed,
            "semantic": semantic.model_dump(),
            "section_ref": f"{section.run_id}/{section.artifact_id}@v{section.version}",
            "section_artifact": section,
            "claim_evidence_refs": list(claim.evidence_ids),
            "request_id": claim.meta.get("llm_request_id"),
            "usage_type": "provider_reported",
            "reasoning_tokens": response.reasoning_tokens,
            "request_enable_thinking": enable_thinking,
            "request_response_format": response_format,
            "real_request_count": 1,
            "phase_record": rec,
        }


# =====================================================================
# Test C — selective recovery on Case A (real recovery calls)
# =====================================================================

def run_test_c(
    real_client: RealLLMClient,
    ledger: LiveCallLedger,
) -> dict:
    """Build a Case A fault snapshot using deterministic / Fake tools
    (so the fault path does NOT consume real LLM budget), then run
    RecoveryExecutor with RealLLMClient.  The recovery phase should
    produce exactly 2 real LLM calls (generate_financial_claim +
    generate_financial_section).

    Day 13.6 FINAL-FIX scenario construction:
      The faulty snapshot is built by the SAME
      `build_main_fault_scenario` helper that the offline
      Day 7/8/9/10 experiments use.  This guarantees:
        - clean baseline @v1 is materialized
        - field_revenue_2025@v2 is tampered
        - 3 financial metrics @v2 are re-executed (FakeLLM
          for the math; deterministic tool)
        - claim_financial_growth@v2 is generated (FakeLLM
          for fault snapshot; Real LLM only at recovery)
        - claim_report_financial_section@v2 is generated
        - report_merged@v2 is re-merged
      The scenario returns `target_ref` = the EXACT
      `report_merged@v2` produced by the scenario.  We do
      NOT guess, hardcode, or "find latest contaminated"
      — the scenario tells us where the fault lands.

    Day 13.5 instrumentation: every real LLM call inside
    `RecoveryExecutor.execute` produces a LiveCallRecord via the
    _RecordingClient wrapper, regardless of whether downstream
    `_parse_llm_output` succeeds.  Provider-reported tokens are
    recorded verbatim; even if recovery validation later fails,
    the tokens are still summed into `provider_reported_token_total`.
    """
    _hr("TEST C — Case A selective recovery (Recovery Phase uses real LLM)")

    enable_thinking, response_format = _request_echo(real_client)
    print(f"  request.enable_thinking = {enable_thinking}")
    print(f"  request.response_format = {response_format}")

    # 1) Build the Case A fault snapshot with the SAME helper the
    #    offline experiments use.  The scenario:
    #      - uses FakeLLM for ALL LLM tasks (fault snapshot is
    #        deterministic; the real LLM is reserved for the
    #        recovery phase below);
    #      - returns the EXACT faulty `target_ref` so the
    #        recovery planner does NOT have to guess;
    #      - leaves the scenario's tmpdir alive for the
    #        duration of this run (we will use the same store
    #        for planner + executor + audit).
    from experiments.scenario import build_main_fault_scenario
    scenario, _golden = build_main_fault_scenario(
        run_id="live_test_c", csv_path=CSV,
    )
    run_id = scenario.run_id
    store = scenario.store
    target = scenario.target_ref          # EXACT ref, no guessing
    contamination = scenario.contamination

    print(
        f"  scenario.target_ref       = "
        f"{target.to_ref_string()}"
    )
    print(
        f"  scenario.contaminated_ids = "
        f"{sorted({r.artifact_id for r in contamination.contaminated})}"
    )
    # Reload the store's in-memory cache so the planner sees the
    # same on-disk state the scenario produced.
    store.reload()
    graph = ArtifactGraph.from_store(store, run_id)

    # 2) Plan recovery.  No hardcoded v2 / "latest contaminated"
    #    search — `target` IS the exact ref the scenario produced.
    plan = SelectiveRecoveryPlanner(
        graph, store, run_id, target, contamination,
    )

    # 5) execute recovery.  The wrapper AUTO-APPENDS one ledger
    # record per real HTTP request, before any downstream
    # validation.  User/Operation branches are unaffected by
    # Case A's revenue fault, so RecoveryExecutor must NOT call
    # LLM for them.
    recording = _RecordingClient(
        real_client, ledger,
        phase_prefix="C-recovery",
        enable_thinking=enable_thinking,
        response_format=response_format,
    )
    executor = RecoveryExecutor(store, graph, recording)
    result = executor.execute(plan)

    # 6) Pair each REPROMPT step with the corresponding record
    # (they appear in the same order: depth-ascending step order
    # = LLM call order in the wrapper).
    reprompt_steps = [
        s for s in result.executed_steps
        if s.action.value == "REPROMPT" and s.llm_calls >= 1
    ]
    if len(reprompt_steps) != len(recording.records):
        print(
            f"  WARNING: REPROMPT step count {len(reprompt_steps)} "
            f"!= recorded LLM call count {len(recording.records)}"
        )
    for s, rec in zip(reprompt_steps, recording.records):
        # Schema: schema is validated by _parse_llm_output inside
        # generate_financial_claim / generate_financial_section.
        # If a SCHEMA_ERROR occurred, generate_* raised ValueError,
        # but the wrapper still recorded the HTTP response.  We
        # detect that by checking whether the step produced a ref.
        schema_ok = bool(s.produced_ref)
        produced = (
            s.produced_ref.to_ref_string() if s.produced_ref else None
        )
        # Evidence + semantic: an entire recovery is "semantic
        # pass" iff `result.success` is True.  We do not have
        # per-step semantic validators; we use the overall
        # result and flag it.
        _update_record(
            rec,
            schema_valid=schema_ok,
            evidence_valid=result.success,
            semantic_valid=result.success,
            produced_ref=produced,
        )
        if not schema_ok and not rec.error_type:
            # _parse_llm_output raised but no error was attached.
            _update_record(
                rec, error_type="SCHEMA_ERROR",
                error_message="missing required key: claim_text",
            )

    # Day 13.6 follow-up: compute per-branch LLM call counts
    # for the live-gate audit.  In Case A (revenue_2025
    # fault), ONLY the financial branch is recovered, so
    # financial_real_llm_calls == 2 and the other two must
    # be 0.  The plan is depth-ascending, leaves first; the
    # LLM call order in `recording.records` is the same as
    # the REPROMPT step order, so we split by execution_unit.
    financial_calls = sum(
        1 for s in reprompt_steps
        if s.execution_unit in (
            "generate_financial_claim",
            "generate_financial_section",
        )
    )
    user_calls = sum(
        1 for s in reprompt_steps
        if s.execution_unit in (
            "generate_user_claim",
            "generate_user_section",
        )
    )
    operation_calls = sum(
        1 for s in reprompt_steps
        if s.execution_unit in (
            "generate_operation_claim",
            "generate_operation_section",
        )
    )

    # Day 13.6 follow-up: post-recovery audit checks.  These
    # are the three extra gates the user added in §4:
    #
    # - semantic_clean_path_pass: every ref in the final
    #   target's evidence chain is VALID (no INVALIDATED /
    #   CONTAMINATED).
    # - execution_integrity_pass: every recorded real call
    #   has a non-null produced_ref (i.e. materialization
    #   succeeded).
    # - exact_evidence_version_pass: the recorded produced_refs
    #   are at the EXACT versions the plan requested (i.e. no
    #   silent latest-version drift).
    final_target_refs = list(result.final_target_refs)
    # Day 13.6 FINAL-FINAL: rebuild ArtifactGraph from the
    # post-recovery store BEFORE running the clean-path audit.
    # The earlier audit code walked `_graph.predecessors` (all
    # edge types, not just propagating) and filtered only
    # `validation_status == "VALID"`, which silently dropped
    # the `RECOVERED` status the RecoveryExecutor assigns to
    # the v3 chain.  The structural check is the same — every
    # visited ref is either VALID or RECOVERED, none is
    # INVALIDATED or CONTAMINATED — but the implementation is
    # now the canonical `clean_path_audit` and the result is a
    # structured `CleanPathAuditResult` so a False can always
    # be debugged from the JSON alone.
    from recovery.executor import clean_path_audit
    # CRITICAL: rebuild the graph from the post-recovery
    # store.  The pre-recovery graph (passed to the executor
    # earlier) does not know about the @v3 nodes the
    # executor just materialized; using it would falsely
    # mark the v2 chain as the path.
    graph_post = ArtifactGraph.from_store(store, run_id)
    all_valid = True
    clean_audit_payload: dict = {}
    if final_target_refs:
        tref = final_target_refs[0]
        audit = clean_path_audit(
            tref, graph_post, store,
            graph_rebuilt_after_recovery=True,
        )
        all_valid = audit.passed
        clean_audit_payload = {
            "visited_refs": [
                r.to_ref_string() for r in audit.visited_refs
            ],
            "non_clean_refs": audit.non_clean_refs,
            "missing_refs": [
                r.to_ref_string() for r in audit.missing_refs
            ],
            "reason": audit.reason,
            "graph_rebuilt_after_recovery":
                audit.graph_rebuilt_after_recovery,
        }
    # execution_integrity_pass: every recorded real LLM call
    # in C has a non-null produced_ref.
    execution_integrity_pass = all(
        bool(r.produced_ref) for r in recording.records
    ) and len(recording.records) == len(reprompt_steps)
    # exact_evidence_version_pass: the produced_ref for each
    # REPROMPT step is the plan's expected version (which
    # `RecoveryExecutor` enforces by writing version+1
    # siblings; the recorded produced_ref must exist in the
    # store at the recorded version).
    exact_evidence_version_pass = True
    for s in reprompt_steps:
        if s.produced_ref is None:
            exact_evidence_version_pass = False
            continue
        a = store.get_version(
            s.produced_ref.run_id,
            s.produced_ref.artifact_id,
            s.produced_ref.version,
        )
        if a is None:
            exact_evidence_version_pass = False

    # Day 13.6 FINAL: per-recovery-Artifact content validation.
    # structural recovery success != real semantic recovery
    # success.  We must re-run the deterministic validators on
    # the recovered Claim and the recovered Section so that
    # any LLM-side content failure (e.g. model omits
    # metric_profit_margin from evidence, or generates
    # numbers that don't match the recovered metric values)
    # is caught at the live gate.  This is the difference
    # between "the recovery plan executed" and "the recovery
    # produced a usable, semantically valid artifact".
    from validation import (
        validate_claim as _validate_claim,
        validate_section as _validate_section,
    )

    # Locate the recovered Claim and Section refs by their
    # execution_unit (deterministic order in the plan).
    recovered_claim_ref = None
    recovered_section_ref = None
    for s in reprompt_steps:
        if (s.execution_unit == "generate_financial_claim"
                and s.produced_ref is not None):
            recovered_claim_ref = s.produced_ref
        elif (s.execution_unit == "generate_financial_section"
                and s.produced_ref is not None):
            recovered_section_ref = s.produced_ref

    recovered_claim_artifact = (
        store.get_version(
            recovered_claim_ref.run_id,
            recovered_claim_ref.artifact_id,
            recovered_claim_ref.version,
        )
        if recovered_claim_ref is not None else None
    )
    recovered_section_artifact = (
        store.get_version(
            recovered_section_ref.run_id,
            recovered_section_ref.artifact_id,
            recovered_section_ref.version,
        )
        if recovered_section_ref is not None else None
    )

    # Recovered Claim content validation
    recovered_claim_schema_valid = (
        recovered_claim_artifact is not None
    )
    recovered_claim_evidence_valid = False
    recovered_claim_semantic_valid = False
    recovered_claim_evidence_refs: list[str] = []
    if recovered_claim_artifact is not None:
        recovered_claim_evidence_refs = [
            r.to_ref_string() for r in recovered_claim_artifact.evidence_ids
        ]
        # Schema is already validated inside _parse_llm_output
        # (the artifact exists with a valid ClaimOutput
        # model_dump).  evidence_valid is the per-step audit
        # flag from the ledger record; we re-run the full
        # semantic validator here so semantic failure
        # (numeric mismatch, missing refs, recommendation
        # boundary, etc.) is independently caught.
        try:
            claim_semantic = _validate_claim(
                claim_text=recovered_claim_artifact.content,
                evidence_refs_in_text=list(
                    recovered_claim_artifact.evidence_ids
                ),
                store=store,
                evidence_policy="financial_claim",
            )
            recovered_claim_evidence_valid = (
                claim_semantic.evidence_valid
            )
            recovered_claim_semantic_valid = (
                claim_semantic.passed
            )
        except Exception as e:
            print(f"  recovered-claim validation error: {e}")
            recovered_claim_evidence_valid = False
            recovered_claim_semantic_valid = False

    # Recovered Section content validation.  The Section's
    # evidence must be the recovered Claim (NOT a stale
    # reference to the bad claim); the section validator
    # cross-checks numbers against the upstream Claim's
    # evidence.
    recovered_section_schema_valid = (
        recovered_section_artifact is not None
    )
    recovered_section_evidence_valid = False
    recovered_section_semantic_valid = False
    recovered_section_evidence_refs: list[str] = []
    if (recovered_section_artifact is not None
            and recovered_claim_artifact is not None):
        recovered_section_evidence_refs = [
            r.to_ref_string()
            for r in recovered_section_artifact.evidence_ids
        ]
        # The Section MUST cite the recovered Claim (not
        # any older claim).  Verify the evidence ref's
        # version matches the recovered Claim version.
        section_cites_recovered_claim = any(
            (r.run_id == recovered_claim_ref.run_id
             and r.artifact_id == recovered_claim_ref.artifact_id
             and r.version == recovered_claim_ref.version)
            for r in recovered_section_artifact.evidence_ids
        )
        try:
            section_semantic = _validate_section(
                section_text=recovered_section_artifact.content,
                section_evidence_refs=list(
                    recovered_section_artifact.evidence_ids
                ),
                claim_evidence_refs=list(
                    recovered_claim_artifact.evidence_ids
                ),
                store=store,
                evidence_policy="financial_section",
            )
            recovered_section_evidence_valid = (
                section_semantic.evidence_valid
                and section_cites_recovered_claim
            )
            recovered_section_semantic_valid = (
                section_semantic.passed
                and section_cites_recovered_claim
            )
        except Exception as e:
            print(f"  recovered-section validation error: {e}")
            recovered_section_evidence_valid = False
            recovered_section_semantic_valid = False

    # Day 13.6 FINAL: per-record real-call integrity.  Even
    # when schema/evidence/semantic fail, the real HTTP
    # request and its provider-reported tokens MUST remain
    # in the ledger.  The Test C passed flag is False if
    # ANY recorded real call ended up with no produced_ref
    # OR with a failed semantic validator, but the ledger
    # record itself is NEVER erased.
    per_record_integrity = all(
        bool(r.produced_ref) for r in recording.records
    )
    for r in recording.records:
        r_schema = r.schema_valid
        r_evidence = r.evidence_valid
        r_semantic = r.semantic_valid
        r_produced = bool(r.produced_ref)
        print(
            f"  [recovery ledger] phase={r.phase} "
            f"unit={r.execution_unit} "
            f"schema={r_schema} evidence={r_evidence} "
            f"semantic={r_semantic} produced={r_produced} "
            f"produced_ref={r.produced_ref}"
        )

    # Day 13.6 FINAL: final mixed-version provenance.  The
    # final report MUST be a freshly-recovered report_merged
    # (i.e. one whose input_artifact_ids include the
    # recovered financial_section AND existing user_section
    # + operation_section, but no new user/operation
    # version was created).
    final_report_ref = (
        result.final_target_refs[0]
        if result.final_target_refs else None
    )
    final_report_input_refs: list[str] = []
    user_section_v_in_final = None
    operation_section_v_in_final = None
    if final_report_ref is not None:
        final_report = store.get_version(
            final_report_ref.run_id,
            final_report_ref.artifact_id,
            final_report_ref.version,
        )
        if final_report is not None:
            final_report_input_refs = [
                r.to_ref_string()
                for r in final_report.input_artifact_ids
            ]
            for r in final_report.input_artifact_ids:
                if r.artifact_id == "claim_report_user_section":
                    user_section_v_in_final = r.version
                elif (
                    r.artifact_id
                    == "claim_report_operation_section"
                ):
                    operation_section_v_in_final = r.version
    # No new user_section or operation_section version was
    # created during recovery: the version in the final
    # report must match the version in the original
    # baseline.  We approximate "no new version" by
    # checking that user_section_v == 1 and
    # operation_section_v == 1 (the baseline produced
    # version 1; the recovery did not invoke the user or
    # operation branch).  This is a conservative check
    # that holds because the user/operation tasks were
    # not in the recovery plan.
    no_new_user_or_op_version = (
        user_section_v_in_final == 1
        and operation_section_v_in_final == 1
    )

    print(f"  recovery success = {result.success}")
    print(f"  replacement_map  = {result.replacement_map}")
    print(f"  executed steps   = {[(s.action.value, s.execution_unit) for s in result.executed_steps]}")
    print(f"  final_target     = {[r.to_ref_string() for r in result.final_target_refs]}")
    print(f"  real LLM calls in recovery = {len(reprompt_steps)}")
    print(f"  financial_real_llm_calls   = {financial_calls}")
    print(f"  user_real_llm_calls        = {user_calls}")
    print(f"  operation_real_llm_calls   = {operation_calls}")
    print(f"  semantic_clean_path_pass   = {all_valid}")
    print(f"  execution_integrity_pass   = {execution_integrity_pass}")
    print(f"  exact_evidence_version_pass = {exact_evidence_version_pass}")
    print(
        f"  recovered_claim_ref  = "
        f"{recovered_claim_ref.to_ref_string() if recovered_claim_ref else None}"
    )
    print(
        f"  recovered_section_ref = "
        f"{recovered_section_ref.to_ref_string() if recovered_section_ref else None}"
    )
    print(
        f"  recovered_claim_schema_valid    = {recovered_claim_schema_valid}"
    )
    print(
        f"  recovered_claim_evidence_valid  = {recovered_claim_evidence_valid}"
    )
    print(
        f"  recovered_claim_semantic_valid  = {recovered_claim_semantic_valid}"
    )
    print(
        f"  recovered_section_schema_valid  = {recovered_section_schema_valid}"
    )
    print(
        f"  recovered_section_evidence_valid = "
        f"{recovered_section_evidence_valid}"
    )
    print(
        f"  recovered_section_semantic_valid = "
        f"{recovered_section_semantic_valid}"
    )
    print(
        f"  final_report_ref         = "
        f"{final_report_ref.to_ref_string() if final_report_ref else None}"
    )
    print(
        f"  final_report_input_refs  = "
        f"{final_report_input_refs}"
    )
    print(
        f"  no_new_user_or_op_version = "
        f"{no_new_user_or_op_version}"
    )

    # Day 13.6 FINAL passed expression.  structural +
    # 6 recovered content flags + final-mixed-version
    # invariant.  ANY False here fails Test C.
    passed = (
        result.success
        and len(reprompt_steps) == 2
        and financial_calls == 2
        and user_calls == 0
        and operation_calls == 0
        and all_valid
        and execution_integrity_pass
        and exact_evidence_version_pass
        and per_record_integrity
        and recovered_claim_schema_valid
        and recovered_claim_evidence_valid
        and recovered_claim_semantic_valid
        and recovered_section_schema_valid
        and recovered_section_evidence_valid
        and recovered_section_semantic_valid
        and no_new_user_or_op_version
    )

    return {
        "passed": passed,
        "status": "PASS" if passed else "FAIL",
        "recovery_success": result.success,
        "replacement_map": result.replacement_map,
        "executed_steps": [
            {
                "action": s.action.value,
                "execution_unit": s.execution_unit,
                "produced_ref": (s.produced_ref.to_ref_string()
                                 if s.produced_ref else None),
            }
            for s in result.executed_steps
        ],
        "real_llm_calls_in_recovery": len(reprompt_steps),
        "financial_real_llm_calls": financial_calls,
        "user_real_llm_calls": user_calls,
        "operation_real_llm_calls": operation_calls,
        "semantic_clean_path_pass": all_valid,
        "execution_integrity_pass": execution_integrity_pass,
        "exact_evidence_version_pass": exact_evidence_version_pass,
        "per_record_real_call_integrity": per_record_integrity,
        "recovered_financial_claim_ref": (
            recovered_claim_ref.to_ref_string()
            if recovered_claim_ref else None
        ),
        "recovered_financial_section_ref": (
            recovered_section_ref.to_ref_string()
            if recovered_section_ref else None
        ),
        "recovered_claim_evidence_refs": recovered_claim_evidence_refs,
        "recovered_section_evidence_refs": recovered_section_evidence_refs,
        "recovered_financial_claim_schema_valid":
            recovered_claim_schema_valid,
        "recovered_financial_claim_evidence_valid":
            recovered_claim_evidence_valid,
        "recovered_financial_claim_semantic_valid":
            recovered_claim_semantic_valid,
        "recovered_financial_section_schema_valid":
            recovered_section_schema_valid,
        "recovered_financial_section_evidence_valid":
            recovered_section_evidence_valid,
        "recovered_financial_section_semantic_valid":
            recovered_section_semantic_valid,
        "final_report_ref": (
            final_report_ref.to_ref_string()
            if final_report_ref else None
        ),
        "final_report_input_refs": final_report_input_refs,
        "no_new_user_or_op_version": no_new_user_or_op_version,
        "real_request_count": len(reprompt_steps),
        # Day 13.6 FINAL-FINAL: structured clean-path audit
        # payload so a False is debuggable from the JSON alone.
        "clean_path_audit": clean_audit_payload,
    }


# =====================================================================
# Main
# =====================================================================

def main() -> int:
    import argparse
    p = argparse.ArgumentParser(
        description=(
            "Day 13.6 staged Live Gate.  Default: A -> B -> C.  Use "
            "--from-test B/C together with --test-a-state to resume "
            "a previously-passed Test A without re-issuing the real "
            "Qwen request.  Use --only-test X to run only X and stop, "
            "or --stop-after X to cap the chain at X.  The resume gate "
            "REJECTS any Test A state whose six per-flag audit fields "
            "are not all True."
        )
    )
    p.add_argument(
        "--from-test", choices=["A", "B", "C"], default="A",
        help="Which test to start from.  A=default, runs A->B->C.  "
             "B / C requires --test-a-state.",
    )
    p.add_argument(
        "--only-test", choices=["A", "B", "C"], default=None,
        help="Run only the named test and stop.  Equivalent to "
             "--from-test X --stop-after X.  For B / C this also "
             "requires --test-a-state.",
    )
    p.add_argument(
        "--stop-after", choices=["A", "B", "C"], default=None,
        help="Stop the chain after the named test.  Pairs with "
             "--from-test (e.g. --from-test A --stop-after B runs "
             "A then B and stops).",
    )
    p.add_argument(
        "--test-a-state", default=None,
        help="Path to the JSON file written by a previous Test A run.  "
             "Required when --from-test / --only-test is B or C.",
    )
    p.add_argument(
        "--test-b-state", default=None,
        help="Path to the JSON file written by a previous Test B run.  "
             "Required when --from-test / --only-test is C.  When "
             "Test A and Test B are both resumed, only the recovery "
             "phase issues real LLM calls.",
    )
    args = p.parse_args()

    # Resolve effective (start, stop) indices.  When --only-test is
    # given, it forces both.  Default stop = C.
    if args.only_test is not None:
        if args.from_test != "A" and args.from_test != args.only_test:
            print(
                f"  --only-test {args.only_test} overrides --from-test "
                f"{args.from_test}"
            )
        start = args.only_test
        stop = args.only_test
    else:
        start = args.from_test
        stop = args.stop_after if args.stop_after is not None else "C"

    real_client = _make_real_client_or_skip()
    ledger = LiveCallLedger()

    a: dict = {"passed": None, "status": "NOT_EXECUTED",
               "real_request_count": 0,
               "claim_ref": None, "semantic": None,
               "schema_valid": None, "evidence_valid": None,
               "semantic_valid": None,
               "numeric_consistency_pass": None,
               "direction_consistency_pass": None,
               "recommendation_boundary_pass": None}
    b: dict = {"passed": None, "status": "NOT_EXECUTED",
               "real_request_count": 0,
               "schema_valid": None, "evidence_valid": None,
               "semantic_valid": None}
    c: dict = {"passed": None, "status": "NOT_EXECUTED",
               "real_request_count": 0, "real_llm_calls_in_recovery": 0,
               "financial_real_llm_calls": 0,
               "user_real_llm_calls": 0, "operation_real_llm_calls": 0,
               "semantic_clean_path_pass": False,
               "execution_integrity_pass": False,
               "exact_evidence_version_pass": False}

    # Day 13.5: persistent store dir for Test A artifacts.  This
    # allows `--from-test B` to rebuild the Claim + upstream
    # artifacts in a working in-memory store without re-issuing
    # Test A's real Qwen call.
    test_a_store_dir = "experiments/results/day13_5_test_a_store"

    if start == "A":
        a = run_test_a(real_client, ledger, store_dir=test_a_store_dir)
        print(f"\n  TEST A: passed = {a['passed']}")
        # Persist Test A state so a future --from-test B/C can resume
        # without re-issuing the real Qwen call.  v3 state includes
        # per-flag fields used by the resume gate.
        if a.get("claim_ref") and a.get("semantic") is not None:
            if a.get("claim_artifact") is not None:
                # Day 13.6 follow-up: the ArtifactStore MUST be
                # reloaded from disk before serializing upstream
                # artifacts; otherwise the in-memory cache is
                # empty and `store.get_version` returns None for
                # every dependency.  The original `run_test_a`
                # store is in-memory only; its writes are
                # persisted to disk but a new ArtifactStore
                # instance does not auto-reload.
                from artifacts import ArtifactStore as _AS
                _tmp = _AS(base_dir=a["store_dir"])
                _tmp.reload()
                _save_test_a_state(a, ledger, _tmp)
        if not a.get("passed"):
            print("  Test A failed; stopping.")
            return _write_final_gate(
                ledger, a, b, c,
                reason="Test A failed; not executing B/C",
            )
    else:
        if not args.test_a_state:
            print(f"  --from-test {start} (or --only-test {start}) "
                  f"requires --test-a-state")
            return 1
        try:
            a = _load_test_a_state(
                args.test_a_state, ledger,
                store_dir=test_a_store_dir,
            )
        except ValueError as e:
            # Day 13.6: surface the resume-gate rejection as a
            # clear CLI error.  Do NOT proceed to B/C.
            msg = str(e)
            if msg.startswith("INVALID_RESUME_STATE"):
                print(f"  RESUME GATE REJECTED: {msg}")
                print(f"  Action: re-run Test A first "
                      f"(--only-test A) before --from-test {start}.")
            else:
                print(f"  Resume failed: {msg}")
            return 2
        print(f"\n  TEST A (resumed from {args.test_a_state}): passed = {a['passed']}")
        if not a.get("passed"):
            print("  Resumed Test A state is not PASS; refusing to run B/C.")
            return _write_final_gate(
                ledger, a, b, c,
                reason="Resumed Test A state is not PASS",
            )

    if stop == "A":
        return _write_final_gate(
            ledger, a, b, c,
            reason="--stop-after A",
        )

    if start in ("A", "B"):
        b = run_test_b(real_client, ledger, test_a=a)
        print(f"\n  TEST B: passed = {b['passed']}")
        if not b.get("passed"):
            print("  Test B failed; stopping.")
            return _write_final_gate(
                ledger, a, b, c,
                reason="Test B failed; not executing C",
            )
        # Persist Test B state so a future --from-test C can
        # resume without re-issuing Test A or Test B.
        if b.get("section_artifact") is not None:
            _save_test_b_state(
                b, ledger,
                test_a_state_path=(
                    args.test_a_state or TEST_A_STATE_PATH
                ),
            )

    if stop == "B":
        return _write_final_gate(
            ledger, a, b, c,
            reason=("Test A and B passed; Test C selective "
                    "recovery not yet verified."),
        )

    if start in ("A", "B", "C"):
        if start == "C":
            # --from-test C: load Test B state (which also
            # re-loads the linked Test A state).  This must NOT
            # invoke run_test_b; the actual real Qwen calls are
            # only the 2 recovery-phase calls in run_test_c.
            if not args.test_b_state:
                print("  --from-test C requires --test-b-state")
                return 1
            try:
                b_resumed = _load_test_b_state(
                    args.test_b_state, ledger,
                    test_a_state_path=args.test_a_state,
                    test_a_state_dir=test_a_store_dir,
                )
            except ValueError as e:
                msg = str(e)
                if msg.startswith("INVALID_RESUME_STATE"):
                    print(f"  RESUME GATE REJECTED: {msg}")
                else:
                    print(f"  Resume failed: {msg}")
                return 2
            # The Test A state is the one re-loaded inside
            # `_load_test_b_state`.  Surface it as `a` for the
            # final gate.
            a = b_resumed.get("claim_artifact") and {
                "passed": True, "status": "PASS_RESUMED",
                "schema_valid": True, "evidence_valid": True,
                "semantic_valid": True,
                "numeric_consistency_pass": True,
                "direction_consistency_pass": True,
                "recommendation_boundary_pass": True,
                "real_request_count": 0,
            }
            b = {
                "passed": b_resumed["passed"],
                "status": b_resumed["status"],
                "schema_valid": b_resumed["schema_valid"],
                "evidence_valid": b_resumed["evidence_valid"],
                "semantic_valid": b_resumed["semantic_valid"],
                "real_request_count": b_resumed["real_request_count"],
            }
            print(f"\n  TEST A (resumed via Test B state): passed")
            print(f"  TEST B (resumed): passed = {b['passed']}")
        c = run_test_c(real_client, ledger)
        print(f"\n  TEST C: passed = {c['passed']}")
        print(f"  recovery real LLM calls = {c['real_llm_calls_in_recovery']}")

    return _write_final_gate(
        ledger, a, b, c,
        reason=("live Test A/B/C completed; see live_test_results"),
    )


# =====================================================================
# Test A persistence + resume helpers
# =====================================================================

TEST_A_STATE_PATH = "experiments/results/day13_5_test_a_state.json"


def _save_test_a_state(
    test_a: dict, ledger: LiveCallLedger, store,
    *,
    state_path: str = TEST_A_STATE_PATH,
) -> None:
    """Persist Test A's real outcome so a later --from-test B/C can
    resume without re-issuing the real Qwen call.

    Day 13.5 spec (state persistence):
      - Save the COMPLETE Claim Artifact (model_dump JSON) so
        `--from-test B` can rebuild an in-memory store and run the
        section call without re-issuing Test A.
      - Save every upstream artifact the Claim depends on (metrics,
        fields, raw_data) by (run_id, artifact_id, version) — these
        are required because `validate_section` looks up
        `claim.evidence_ids` in the store.
      - Save the Test A ledger record so the resumed session's
        gate's `provider_reported_token_total` is accurate.
      - NEVER serialize the API key, the Authorization header, or
        any other secret.

    Day 13.6 spec (resume gate): also save the explicit per-flag
    fields so the resume loader can audit them directly without
    re-running the nested `semantic.model_dump()` evaluation.
    The state v3 is rejected by `--from-test B` unless every
    flag is True.  v2 state files remain readable but cannot
    be resumed (v2 lacks the explicit flags).

    Day 13.6 follow-up (this revision): ALWAYS call
    `store.reload()` first.  ArtifactStore is an in-memory cache
    + on-disk persistence layer; without `reload()`, a freshly
    constructed ArtifactStore sees an empty `_by_ref` even when
    the on-disk JSON files are present, and `get_version(...)`
    returns None for every dependency.  Calling `reload()` is
    idempotent (it clears then re-reads the directory).

    Day 13.6 FORENSIC REPAIR: `state_path` is an optional
    override.  Tests MUST pass `state_path=str(tmp_path / ...)`
    instead of letting this function write to the real
    `experiments/results/day13_5_test_a_state.json`.  The
    default path is preserved for the live gate's main
    invocation; tests are expected to override it.
    """
    # Always reload the in-memory index from disk.  This is the
    # fix for the day-13.6 bug where `upstream_artifacts` came
    # back empty even though the on-disk files were intact.
    try:
        store.reload()
    except Exception as e:
        print(f"  WARN: store.reload() failed: {e}")

    claim = test_a.get("claim_artifact")
    if claim is None:
        return
    record = next(
        (r for r in ledger.records if r.phase == "A"),
        None,
    )

    # Collect every upstream artifact referenced by the Claim
    # (input_artifact_ids + evidence_ids) so the resumed run can
    # rebuild a working in-memory store.
    upstream_ids: set[tuple[str, str, int]] = set()
    for r in list(claim.input_artifact_ids) + list(claim.evidence_ids):
        upstream_ids.add((r.run_id, r.artifact_id, r.version))

    upstream_dumps = []
    for (rid, aid, ver) in sorted(upstream_ids):
        a = store.get_version(rid, aid, ver)
        if a is None:
            continue
        upstream_dumps.append({
            "ref": {"run_id": rid, "artifact_id": aid, "version": ver},
            "artifact": a.model_dump(mode="json"),
        })

    # raw_data is not referenced by the claim directly but is
    # required for any future recompute.  Include it if present.
    raw_dump = None
    raw = store.get(claim.run_id, "raw_data")
    if raw is not None:
        raw_dump = raw.model_dump(mode="json")

    state = {
        "schema_version": 3,
        "saved_at_unix_ms": int(time.time() * 1000),
        "test_a": {
            "claim_artifact": claim.model_dump(mode="json"),
            "upstream_artifacts": upstream_dumps,
            "raw_data": raw_dump,
            "claim_ref": test_a.get("claim_ref"),
            "semantic": test_a.get("semantic"),
            "usage_type": test_a.get("usage_type"),
            "request_id": test_a.get("request_id"),
            "reasoning_tokens": test_a.get("reasoning_tokens"),
            "request_enable_thinking": test_a.get("request_enable_thinking"),
            "request_response_format": test_a.get("request_response_format"),
            "real_request_count": test_a.get("real_request_count", 1),
            # Day 13.6: explicit per-flag fields used by the
            # resume gate.
            "passed": bool(test_a.get("passed")),
            "schema_valid": bool(test_a.get("schema_valid", False)),
            "evidence_valid": bool(test_a.get("evidence_valid", False)),
            "numeric_consistency_pass": bool(
                test_a.get("numeric_consistency_pass", False)
            ),
            "direction_consistency_pass": bool(
                test_a.get("direction_consistency_pass", False)
            ),
            "recommendation_boundary_pass": bool(
                test_a.get("recommendation_boundary_pass", False)
            ),
            "semantic_valid": bool(test_a.get("semantic_valid", False)),
        },
        "ledger_record": _record_to_dict(record) if record else None,
    }

    # Sanity check: ensure no api-key-shaped string in the state.
    state_text = json.dumps(state, ensure_ascii=False)
    import re
    leaked = re.findall(r"[A-Za-z0-9_\-]{20,}", state_text)
    # The only legitimate long strings are request_ids (we keep
    # them); any other 20+ char run is suspicious.
    if leaked:
        # We can't tell apart request_id from key by length alone,
        # so we only warn.  The request_id is also stored in the
        # ledger record; if it's a real provider id, it should be
        # opaque.  We log a warning and continue.
        print(f"  WARN: state JSON contains {len(leaked)} long opaque strings")

    Path(state_path).parent.mkdir(parents=True, exist_ok=True)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    print(f"  Persisted Test A state to {state_path}")
    print(f"    claim_ref           = {state['test_a']['claim_ref']}")
    print(f"    upstream_artifacts  = {len(upstream_dumps)}")
    print(f"    raw_data            = {'present' if raw_dump else 'absent'}")


# =====================================================================
# Test B persistence + resume helpers
# =====================================================================

TEST_B_STATE_PATH = "experiments/results/day13_5_test_b_state.json"


def _save_test_b_state(
    test_b: dict,
    ledger: LiveCallLedger,
    test_a_state_path: str = TEST_A_STATE_PATH,
    *,
    state_path: str = TEST_B_STATE_PATH,
) -> None:
    """Persist Test B's real outcome so `--from-test C` can resume
    the recovery phase without re-issuing the real Qwen requests
    for Test A or Test B.

    Day 13.6 follow-up spec:
      - Test B state references the Test A state file (the Claim
        Artifact is rebuilt from there, NOT duplicated here).
      - The Section Artifact is fully serialized (model_dump
        JSON) so a resumed C can run the recovery path without
        needing to re-issue Test B.
      - The full Test B ledger record is saved (the provider-
        reported tokens count toward the resumed session's
        billing).
      - NEVER serialize the API key, the Authorization header,
        or any other secret.
    """
    section = test_b.get("section_artifact")
    if section is None:
        return
    # Find the Test B record (phase == "B")
    record = next(
        (r for r in ledger.records if r.phase == "B"),
        None,
    )
    state = {
        "schema_version": 1,
        "saved_at_unix_ms": int(time.time() * 1000),
        "test_b": {
            "section_artifact": section.model_dump(mode="json"),
            "section_ref": test_b.get("section_ref"),
            "claim_evidence_refs": [
                r.to_ref_string()
                for r in test_b.get("claim_evidence_refs", [])
            ],
            "semantic": test_b.get("semantic"),
            "usage_type": test_b.get("usage_type"),
            "request_id": test_b.get("request_id"),
            "reasoning_tokens": test_b.get("reasoning_tokens"),
            "request_enable_thinking": test_b.get("request_enable_thinking"),
            "request_response_format": test_b.get("request_response_format"),
            "real_request_count": test_b.get("real_request_count", 1),
            "passed": bool(test_b.get("passed")),
            "schema_valid": bool(test_b.get("schema_valid", False)),
            "evidence_valid": bool(test_b.get("evidence_valid", False)),
            "semantic_valid": bool(test_b.get("semantic_valid", False)),
        },
        "test_a_state_path": test_a_state_path,
        "ledger_record": _record_to_dict(record) if record else None,
    }

    # Sanity check: ensure no api-key-shaped string in the state.
    state_text = json.dumps(state, ensure_ascii=False)
    import re
    leaked = re.findall(r"[A-Za-z0-9_\-]{20,}", state_text)
    if leaked:
        # The only legitimate long strings are request_ids.
        print(
            f"  WARN: Test B state JSON contains {len(leaked)} "
            f"long opaque strings"
        )

    Path(state_path).parent.mkdir(parents=True, exist_ok=True)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    print(f"  Persisted Test B state to {state_path}")
    print(f"    section_ref         = {state['test_b']['section_ref']}")
    print(f"    test_a_state_path   = {test_a_state_path}")


def _load_test_b_state(
    path: str,
    ledger: LiveCallLedger,
    test_a_state_path: Optional[str] = None,
    test_a_state_dir: Optional[str] = None,
) -> dict:
    """Resume Test B from a previously-persisted state.

    Day 13.6 follow-up spec:
      - Reject the resume if any per-flag field is False
        (passed / schema_valid / evidence_valid / semantic_valid).
      - Reject if the referenced Test A state file is missing
        or unresolvable.  We re-load Test A from its own state
        file (not from this Test B state) so the dependency
        graph stays in sync.
      - NEVER modify the live Qwen session — no real HTTP
        request is made.
    """
    with open(path, "r", encoding="utf-8") as f:
        state = json.load(f)
    sv = state.get("schema_version")
    if sv != 1:
        raise ValueError(
            f"Unsupported test_b_state schema_version: {sv!r}"
        )
    tb = state.get("test_b", {})
    flag_names = [
        "passed",
        "schema_valid",
        "evidence_valid",
        "semantic_valid",
    ]
    failed_flags = [
        name for name in flag_names if not bool(tb.get(name, False))
    ]
    if failed_flags:
        detail = ", ".join(failed_flags)
        raise ValueError(
            f"INVALID_RESUME_STATE: Test B state is not semantically "
            f"valid; rerun Test B first.  Failed flags: [{detail}].  "
            f"Refusing --from-test C."
        )
    if tb.get("usage_type") != "provider_reported":
        raise ValueError(
            "INVALID_RESUME_STATE: Resumed Test B was not "
            "PROVIDER_REPORTED; cannot resume a non-real call."
        )
    # Replay Test B ledger record
    rd = state.get("ledger_record")
    if rd:
        from experiments.live_ledger import LiveCallRecord
        rd = dict(rd)
        rd["is_replayed"] = True
        ledger.append(LiveCallRecord(**rd))
    # Re-build Section Artifact
    section = Artifact.model_validate(tb["section_artifact"])
    # Locate the Test A state file (we re-load Test A from it
    # so the dependency graph in the resumed session is
    # consistent with what the original Test B used).
    ta_state_path = state.get("test_a_state_path") or test_a_state_path
    if not ta_state_path:
        raise ValueError(
            "INVALID_RESUME_STATE: Test B state does not reference "
            "a Test A state path; cannot resolve the dependency "
            "graph.  Re-run Test B with --test-a-state to fix the "
            "linkage."
        )
    if test_a_state_path and ta_state_path != test_a_state_path:
        print(
            f"  NOTE: Test B state references Test A at "
            f"{ta_state_path}; ignoring --test-a-state "
            f"{test_a_state_path}."
        )
    # Re-load Test A from the referenced state file
    a = _load_test_a_state(
        ta_state_path, ledger,
        store_dir=test_a_state_dir,
    )
    return {
        "passed": True,
        "status": "PASS_RESUMED",
        "section_artifact": section,
        "section_ref": tb["section_ref"],
        "claim_evidence_refs": [
            ArtifactRef.from_ref_string(s) for s in tb.get(
                "claim_evidence_refs", []
            )
        ],
        "semantic": tb.get("semantic"),
        "usage_type": tb.get("usage_type"),
        "request_id": tb.get("request_id"),
        "reasoning_tokens": tb.get("reasoning_tokens"),
        "request_enable_thinking": tb.get("request_enable_thinking"),
        "request_response_format": tb.get("request_response_format"),
        "real_request_count": tb.get("real_request_count", 0),
        "schema_valid": bool(tb.get("schema_valid", False)),
        "evidence_valid": bool(tb.get("evidence_valid", False)),
        "semantic_valid": bool(tb.get("semantic_valid", False)),
        # The resumed Test A is the one a future run_test_c uses
        # as upstream; re-attach it under the standard key.
        "claim_artifact": a.get("claim_artifact"),
    }


def _record_to_dict(r) -> dict:
    from dataclasses import asdict
    return asdict(r)


def _load_test_a_state(
    path: str, ledger: LiveCallLedger, store_dir: Optional[str] = None,
) -> dict:
    """Resume Test A from a previously-persisted state.

    Replays the Test A ledger record into the current ledger so the
    gate's `provider_reported_token_total` is accurate across the
    resumed session.  Rebuilds the Claim Artifact (and every
    upstream artifact) so the resumed Test B / C can run with a
    working in-memory store.

    Day 13.6 (resume gate validation): BEFORE accepting a v3 state
    for `--from-test B/C`, every per-flag field must be True:
      - passed
      - schema_valid
      - evidence_valid
      - numeric_consistency_pass
      - direction_consistency_pass
      - recommendation_boundary_pass
      - semantic_valid
    Plus `usage_type == PROVIDER_REPORTED` (the real HTTP call must
    have actually happened, not a synthetic one).

    If any flag is False, raise ValueError with prefix
    `INVALID_RESUME_STATE:` so the CLI can surface a clear error
    message to the user.  This stops a previous Test A FAIL from
    being silently treated as a successful resume.

    v2 state files (which lack the explicit per-flag fields) are
    accepted for read but ALL flags default to False — so v2
    state is also rejected by the resume gate.  This is the
    correct behaviour: any v2 state in `day13_5_test_a_state.json`
    that was a real failure sample (e.g. the live-gate Test A
    failure) is no longer eligible for `--from-test B`.
    """
    with open(path, "r", encoding="utf-8") as f:
        state = json.load(f)
    sv = state.get("schema_version")
    if sv not in (1, 2, 3):
        raise ValueError(
            f"Unsupported test_a_state schema_version: {sv!r}"
        )
    ta = state.get("test_a", {})
    if not ta.get("claim_ref"):
        raise ValueError("test_a_state.test_a.claim_ref is missing")
    if ta.get("usage_type") != UsageType.PROVIDER_REPORTED.value:
        raise ValueError(
            "INVALID_RESUME_STATE: Resumed Test A was not "
            "PROVIDER_REPORTED; cannot resume a non-real call."
        )

    # Day 13.6: resume gate.  EVERY per-flag must be True.
    flag_names = [
        "passed",
        "schema_valid",
        "evidence_valid",
        "numeric_consistency_pass",
        "direction_consistency_pass",
        "recommendation_boundary_pass",
        "semantic_valid",
    ]
    failed_flags = [
        name for name in flag_names if not bool(ta.get(name, False))
    ]
    if failed_flags:
        # Refuse resume.  The current `day13_5_test_a_state.json`
        # is a real failure sample (Test A: evidence_valid=false,
        # numeric_consistency_pass=false) and must be rejected
        # here.  Caller must re-run Test A successfully first.
        detail = ", ".join(failed_flags) if failed_flags else "unknown"
        raise ValueError(
            f"INVALID_RESUME_STATE: Test A state is not semantically "
            f"valid; rerun Test A first.  Failed flags: [{detail}].  "
            f"Refusing --from-test B/C."
        )

    # Replay ledger record
    rd = state.get("ledger_record")
    if rd:
        from experiments.live_ledger import LiveCallRecord
        rd = dict(rd)
        rd["is_replayed"] = True
        ledger.append(LiveCallRecord(**rd))

    # Rebuild Claim Artifact
    if "claim_artifact" not in ta:
        # schema_version 1: only metadata was saved.  Cannot
        # rebuild the claim object.  Refuse to resume.
        raise ValueError(
            "test_a_state v1 does not contain claim_artifact; "
            "cannot rebuild.  Re-run Test A to get a v2/v3 state."
        )
    claim = Artifact.model_validate(ta["claim_artifact"])

    # Rebuild upstream artifacts into the caller's store (or a
    # throwaway one if `store_dir` is None).
    if store_dir is None:
        tmp = tempfile.TemporaryDirectory()
        store_path = tmp.name
        cleanup = tmp.cleanup
    else:
        store_path = store_dir
        Path(store_path).mkdir(parents=True, exist_ok=True)
        cleanup = None
    store = ArtifactStore(base_dir=store_path)
    for d in ta.get("upstream_artifacts", []):
        a = Artifact.model_validate(d["artifact"])
        store.save(a)
    if ta.get("raw_data"):
        store.save(Artifact.model_validate(ta["raw_data"]))
    # Save the claim itself so Test B's `validate_section` can
    # resolve `claim.evidence_ids` in this store.
    store.save(claim)

    # Day 13.6 follow-up: dependency completeness audit.  Every
    # exact (run_id, artifact_id, version) ref in
    # `claim.input_artifact_ids` and `claim.evidence_ids` MUST
    # exist in the rebuilt store.  Refuse resume if any ref is
    # missing — `--from-test B` cannot run with a partial
    # dependency graph because the section task will fail to
    # resolve evidence refs.
    required_refs: list[ArtifactRef] = []
    for r in list(claim.input_artifact_ids) + list(claim.evidence_ids):
        required_refs.append(r)
    # Stable-dedup by exact (run_id, artifact_id, version) tuple
    seen_keys: set[tuple[str, str, int]] = set()
    unique_required: list[ArtifactRef] = []
    for r in required_refs:
        k = (r.run_id, r.artifact_id, r.version)
        if k in seen_keys:
            continue
        seen_keys.add(k)
        unique_required.append(r)
    persisted_refs: list[ArtifactRef] = []
    missing_refs: list[ArtifactRef] = []
    for r in unique_required:
        if store.get_version(r.run_id, r.artifact_id, r.version) is not None:
            persisted_refs.append(r)
        else:
            missing_refs.append(r)
    if missing_refs:
        # Refuse resume.  Cannot run Test B without the exact
        # upstream metrics.
        missing_str = ", ".join(
            f"{r.run_id}/{r.artifact_id}@v{r.version}"
            for r in missing_refs
        )
        raise ValueError(
            f"INVALID_RESUME_STATE: missing dependencies for "
            f"claim_artifact {ta.get('claim_ref')}: [{missing_str}].  "
            f"Refusing --from-test B/C.  Re-run Test A or restore "
            f"the missing artifacts into {store_path}."
        )
    # Note: `raw_data` is OPTIONAL for Test B resume.  It is
    # required for recompute paths (Day 8 recovery), but Test B's
    # section task only needs the Claim + its evidence_refs.  We
    # therefore do not block resume when raw_data is absent.

    return {
        "passed": True,
        "status": "PASS_RESUMED",
        "claim_ref": ta["claim_ref"],
        "semantic": ta.get("semantic"),
        "usage_type": ta.get("usage_type"),
        "request_id": ta.get("request_id"),
        "reasoning_tokens": ta.get("reasoning_tokens"),
        "request_enable_thinking": ta.get("request_enable_thinking"),
        "request_response_format": ta.get("request_response_format"),
        "real_request_count": ta.get("real_request_count", 0),
        # Make the rebuilt objects available to Test B / C
        "claim_artifact": claim,
        "store_path": store_path,
        "_store_cleanup": cleanup,
        # Day 13.6 dependency audit
        "required_dependency_refs": [r.to_ref_string() for r in unique_required],
        "persisted_dependency_refs": [r.to_ref_string() for r in persisted_refs],
        "missing_dependency_refs": [r.to_ref_string() for r in missing_refs],
    }


def _write_final_gate(
    ledger: LiveCallLedger,
    a: dict, b: dict, c: dict,
    *,
    reason: str,
) -> int:
    """Assemble and write the final day13_5_live_gate.json.

    The gate is computed by `build_day13_5_live_gate` from the
    full set of per-test fields.  The same `live_results` dict is
    serialized so the audit JSON exposes every individual check
    (see `gate_checks` in the gate output).
    """
    live_results = {
        "test_a": {
            "passed": a.get("passed"),
            "status": a.get("status"),
            "schema_valid": a.get("schema_valid"),
            "evidence_valid": a.get("evidence_valid"),
            "semantic_valid": a.get("semantic_valid"),
            "numeric_consistency_pass": a.get("numeric_consistency_pass"),
            "direction_consistency_pass": a.get("direction_consistency_pass"),
            "recommendation_boundary_pass":
                a.get("recommendation_boundary_pass"),
        },
        "test_b": {
            "passed": b.get("passed"),
            "status": b.get("status"),
            "schema_valid": b.get("schema_valid"),
            "evidence_valid": b.get("evidence_valid"),
            "semantic_valid": b.get("semantic_valid"),
        },
        "test_c": {
            "passed": c.get("passed"),
            "status": c.get("status"),
            "real_llm_calls_in_recovery": c.get(
                "real_llm_calls_in_recovery", 0
            ),
            "financial_real_llm_calls": c.get("financial_real_llm_calls", 0),
            "user_real_llm_calls": c.get("user_real_llm_calls", 0),
            "operation_real_llm_calls": c.get(
                "operation_real_llm_calls", 0
            ),
            "semantic_clean_path_pass": c.get(
                "semantic_clean_path_pass", False
            ),
            "execution_integrity_pass": c.get(
                "execution_integrity_pass", False
            ),
            "exact_evidence_version_pass": c.get(
                "exact_evidence_version_pass", False
            ),
            "per_record_real_call_integrity": c.get(
                "per_record_real_call_integrity", False
            ),
            # Day 13.6 FINAL: per-recovered-Artifact content
            # validation flags + exact-ref audit fields.
            "recovered_financial_claim_ref": c.get(
                "recovered_financial_claim_ref"
            ),
            "recovered_financial_section_ref": c.get(
                "recovered_financial_section_ref"
            ),
            "recovered_claim_evidence_refs": c.get(
                "recovered_claim_evidence_refs", []
            ),
            "recovered_section_evidence_refs": c.get(
                "recovered_section_evidence_refs", []
            ),
            "recovered_financial_claim_schema_valid": c.get(
                "recovered_financial_claim_schema_valid", False
            ),
            "recovered_financial_claim_evidence_valid": c.get(
                "recovered_financial_claim_evidence_valid", False
            ),
            "recovered_financial_claim_semantic_valid": c.get(
                "recovered_financial_claim_semantic_valid", False
            ),
            "recovered_financial_section_schema_valid": c.get(
                "recovered_financial_section_schema_valid", False
            ),
            "recovered_financial_section_evidence_valid": c.get(
                "recovered_financial_section_evidence_valid", False
            ),
            "recovered_financial_section_semantic_valid": c.get(
                "recovered_financial_section_semantic_valid", False
            ),
            "final_report_ref": c.get("final_report_ref"),
            "final_report_input_refs": c.get(
                "final_report_input_refs", []
            ),
            "no_new_user_or_op_version": c.get(
                "no_new_user_or_op_version", False
            ),
            # Day 13.6 FINAL-FINAL: structured clean-path audit
            # payload (visited_refs / non_clean_refs /
            # missing_refs / reason / graph_rebuilt_after_recovery)
            # so a False is debuggable from the JSON alone.
            "clean_path_audit": c.get("clean_path_audit", {}),
        },
    }
    gate = build_day13_5_live_gate(
        adapter_offline_pass=True,
        credential_available=bool(os.environ.get("REAL_LLM_API_KEY")),
        real_request_count=ledger.real_request_count,
        reason=reason,
        ledger=ledger,
        live_test_results=live_results,
    )

    out_path = "experiments/results/day13_5_live_gate.json"
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(gate, f, ensure_ascii=False, indent=2)
    print(f"\n  Wrote {out_path}")
    print(json.dumps(gate, ensure_ascii=False, indent=2))

    _hr("GATE DECISION")
    if gate["live_gate_pass"]:
        print("  live_gate_pass = true -> Day 14 FULL REAL BENCHMARK allowed.")
    else:
        print("  live_gate_pass = false -> do NOT start Day 14.")
        if not a.get("passed"):
            print("    - Test A failed (or not executed)")
        if not b.get("passed"):
            print("    - Test B failed (or not executed)")
        if not c.get("passed"):
            print("    - Test C failed (or not executed)")
    return 0 if gate["live_gate_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
