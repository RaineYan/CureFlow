"""
LiveCallLedger — Day 13.5.

A structured record of every real LLM network call made during
a live smoke test.  Each entry captures:

  phase                which smoke phase (A / B / C)
  execution_unit       which claim / section task
  provider             "openai" / "minimax" / ...
  model                model name
  request_id           provider's id (or None)

  usage_type           SYNTHETIC / PROVIDER_REPORTED / UNAVAILABLE
  input_tokens         only counted if usage_type == PROVIDER_REPORTED
  output_tokens        only counted if usage_type == PROVIDER_REPORTED
  total_tokens         only counted if usage_type == PROVIDER_REPORTED
  client_wall_clock_latency_ms   always recorded

  schema_valid         whether parse_claim_payload succeeded
  evidence_valid       whether validate_claim.evidence_valid was True
  semantic_valid       overall SemanticValidationResult.passed
  produced_ref         ref string of the saved Claim artifact
  error_type           RealLLMErrorKind value or None
  error_message_sanitized   message with the api key redacted

The ledger is the SINGLE source of truth for "did a real
request happen".  Test harnesses must consult the ledger,
not the in-memory LLMResponse alone, because a LLMResponse
can be constructed by a mock and look real.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from llm.base import UsageType


def _sanitize(message: str) -> str:
    """Crude scrubber: drop any token that looks like an api key
    (>= 16 chars of [A-Za-z0-9_-]).  This is defense in depth;
    the rest of the pipeline must not put keys into messages."""
    import re
    if not message:
        return ""
    return re.sub(r"[A-Za-z0-9_\-]{16,}", "[REDACTED]", message)


@dataclass
class LiveCallRecord:
    phase: str
    execution_unit: str
    provider: str
    model: str
    request_id: Optional[str]
    usage_type: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    client_wall_clock_latency_ms: float
    schema_valid: bool
    evidence_valid: bool
    semantic_valid: bool
    produced_ref: Optional[str]
    error_type: Optional[str] = None
    error_message_sanitized: Optional[str] = None
    recorded_at_unix_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    # Day 13.6 FINAL follow-up: True iff this record was
    # replayed from a previously-persisted state file
    # (`_load_test_a_state` or `_load_test_b_state`) rather
    # than produced by a live `_RecordingClient.generate()`
    # call.  Used by `LiveCallLedger.real_request_count` to
    # distinguish a real HTTP request (not replayed) from a
    # chain-loaded replay (not a real request).
    is_replayed: bool = False

    # Day 13.5: provider-reported reasoning tokens.  None when the
    # provider did not include `usage.completion_tokens_details.
    # reasoning_tokens`.  We never infer.  These tokens are NOT
    # counted in `total_tokens` (that's a provider-defined total
    # that may or may not include reasoning; we trust whatever
    # `total_tokens` the provider returns and just record the
    # reasoning breakdown as a separate observation).
    reasoning_tokens: Optional[int] = None
    # Day 13.5: what we sent in the request body.  None when not
    # requested.  For Qwen with enable_thinking=False this is False.
    request_enable_thinking: Optional[bool] = None
    # Day 13.5: what structured-output hint we sent.  None for
    # free-form text requests.
    request_response_format: Optional[dict] = None

    def provider_reported_tokens(self) -> int:
        """Tokens are only counted as 'real' if the provider
        actually reported a usage block.  Otherwise they are 0
        by contract, regardless of the numbers in LLMResponse."""
        if self.usage_type == UsageType.PROVIDER_REPORTED.value:
            return self.total_tokens
        return 0

    def provider_reported_reasoning_tokens(self) -> Optional[int]:
        """Reasoning tokens are reported verbatim from the provider
        OR are None.  No estimation.  Used by Day 14+ to prove that
        `enable_thinking=False` actually disabled reasoning spend."""
        return self.reasoning_tokens


class LiveCallLedger:
    """Append-only ledger of live LLM calls.  Used by Test A/B/C
    and persisted into `day13_5_live_gate.json`.

    Day 13.6 FINAL-FIX: the request / token counters are split
    into TWO distinct, auditable metrics so a `len(records)`
    reading can never be confused with "new HTTP requests":

      1. `current_invocation_real_request_count`
         Number of NEW HTTP requests issued by THIS
         invocation of the live gate.  Equals the number of
         `LiveCallRecord` with `is_replayed=False`.  This is
         the metric a real Qwen billing audit cares about.

      2. `verified_real_request_count_total`
         Number of DISTINCT real HTTP requests across the
         full session (Test A + Test B + Test C, including
         resumed history).  Deduped by `request_id`.  When a
         record's `request_id is None` (synthetic), it is
         counted as a unique request (different `request_id`
         bucket).  The user-visible grand total.

      3. `replayed_record_count`
         Number of records that were chain-loaded from
         persisted state files (`_load_test_a_state` /
         `_load_test_b_state`) and therefore do NOT
         represent a new HTTP request.

      4. `current_invocation_provider_reported_tokens`
         Sum of provider-reported tokens for THIS
         invocation's new HTTP requests.  Excludes replays.

      5. `verified_provider_reported_token_total`
         Sum of provider-reported tokens across the full
         session, deduped by `request_id`.  A record with
         a duplicate `request_id` (a chain-loaded replay of
         an already-billed request) does NOT add a second
         bill.
    """

    def __init__(self) -> None:
        self.records: List[LiveCallRecord] = []

    def append(self, record: LiveCallRecord) -> None:
        self.records.append(record)

    # ---- new Day 13.6 FINAL-FIX counters ----

    @property
    def replayed_record_count(self) -> int:
        """Number of records that are chain-loaded replays
        (`is_replayed=True`).  These are NOT new HTTP requests
        and must not be billed twice."""
        return sum(1 for r in self.records if r.is_replayed)

    @property
    def current_invocation_real_request_count(self) -> int:
        """Number of NEW HTTP requests issued by THIS
        invocation.  Excludes `is_replayed=True` records."""
        return sum(1 for r in self.records if not r.is_replayed)

    @property
    def verified_real_request_count_total(self) -> int:
        """Number of DISTINCT real HTTP requests across the
        full session, deduped by `request_id`.  Includes
        chain-loaded replays (because their request_id was
        already billed).  Records without a `request_id`
        (synthetic) are each counted as unique (one per
        record)."""
        seen: set = set()
        for r in self.records:
            key = r.request_id if r.request_id else (
                f"synthetic::{r.phase}::{r.execution_unit}::"
                f"{r.recorded_at_unix_ms}"
            )
            seen.add(key)
        return len(seen)

    @property
    def current_invocation_provider_reported_tokens(self) -> int:
        """Sum of provider-reported tokens for THIS
        invocation's NEW HTTP requests.  Excludes replays
        (chain-loaded replays were already billed)."""
        return sum(
            r.provider_reported_tokens()
            for r in self.records if not r.is_replayed
        )

    @property
    def verified_provider_reported_token_total(self) -> int:
        """Sum of provider-reported tokens across the full
        session, deduped by `request_id`.  Includes
        chain-loaded replays (their tokens were already
        billed when the live A/B ran, so they count once).
        A second occurrence of the same `request_id` (e.g.
        a chain-load replay after the live run) does NOT
        add a second bill — it is deduped to 0.
        """
        seen: set = set()
        total = 0
        for r in self.records:
            tokens = r.provider_reported_tokens()
            if tokens <= 0:
                continue
            key = r.request_id if r.request_id else (
                f"synthetic::{r.phase}::{r.execution_unit}::"
                f"{r.recorded_at_unix_ms}"
            )
            if key in seen:
                continue
            seen.add(key)
            total += tokens
        return total

    # ---- legacy aliases (kept for backward compatibility) ----

    @property
    def real_request_count(self) -> int:
        """DEPRECATED alias for
        `current_invocation_real_request_count`.  Counts
        records with `is_replayed=False`.  New code should
        use the explicit `current_invocation_*` /
        `verified_*` names so a `len(records)` reading can
        never be confused with new HTTP requests."""
        return self.current_invocation_real_request_count

    @property
    def provider_reported_token_total(self) -> int:
        """DEPRECATED alias for
        `current_invocation_provider_reported_tokens`.
        Counts tokens for `is_replayed=False` records.  New
        code should use the explicit `current_invocation_*`
        / `verified_*` names."""
        return self.current_invocation_provider_reported_tokens

    def to_dict(self) -> dict:
        return {
            "real_request_count": self.current_invocation_real_request_count,
            "current_invocation_real_request_count":
                self.current_invocation_real_request_count,
            "verified_real_request_count_total":
                self.verified_real_request_count_total,
            "replayed_record_count": self.replayed_record_count,
            "provider_reported_token_total":
                self.current_invocation_provider_reported_tokens,
            "current_invocation_provider_reported_tokens":
                self.current_invocation_provider_reported_tokens,
            "verified_provider_reported_token_total":
                self.verified_provider_reported_token_total,
            "records": [asdict(r) for r in self.records],
        }

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)


def build_day13_5_live_gate(
    *,
    adapter_offline_pass: bool,
    credential_available: bool,
    real_request_count: int,
    reason: str,
    ledger: Optional[LiveCallLedger] = None,
    live_test_results: Optional[dict] = None,
) -> dict:
    """Assemble the Day 13.5/13.6 gate JSON.

    Schema (always present):
      adapter_offline_pass:    bool
      credential_available:    bool
      real_request_count:      int
      live_gate_pass:          bool  (only True if every gate_check is True)
      full_benchmark_allowed:  bool  (= live_gate_pass; explicit alias for
                                       downstream tooling that must not
                                       re-derive the rule)
      gate_checks:             dict  (every individual check; explicit
                                       audit so a single typo in the
                                       rule cannot accidentally flip the
                                       gate green)
      reason:                  str

    Schema (only when ledger has records):
      provider_reported_token_total
      records: list of record dicts
      live_test_results: A / B / C results

    Day 13.6 follow-up: the gate rule is now an EXPLICIT conjunction
    of every individual check.  The previous code only checked
    `test_a.passed` and `test_b.passed` and let any `real_request_count
    > 0` flip the gate green, which incorrectly validated a staged
    run that stopped at Test B without running Test C.

    Live gate passes only when ALL of the following are true:
      1.  credential_available         = True
      2.  test_a.passed                 = True
      3.  test_a.schema_valid           = True
      4.  test_a.evidence_valid         = True
      5.  test_a.semantic_valid         = True
      6.  test_b.passed                 = True
      7.  test_b.schema_valid           = True
      8.  test_b.evidence_valid         = True
      9.  test_b.semantic_valid         = True
     10.  test_c.passed                 = True  (NOT None, NOT False, NOT NOT_EXECUTED)
     11.  test_c.real_llm_calls_in_recovery == 2
     12.  test_c.financial_real_llm_calls == 2
     13.  test_c.user_real_llm_calls == 0
     14.  test_c.operation_real_llm_calls == 0
     15.  test_c.semantic_clean_path_pass == True
     16.  test_c.execution_integrity_pass == True
     17.  test_c.exact_evidence_version_pass == True
    """
    # ----- gate_checks: every individual predicate, explicitly named
    #       so any future drift is auditable from the gate JSON.
    a = (live_test_results or {}).get("test_a") or {}
    b = (live_test_results or {}).get("test_b") or {}
    c = (live_test_results or {}).get("test_c") or {}

    gate_checks: Dict[str, Any] = {
        "credential_available": bool(credential_available),
        "test_a_pass": a.get("passed") is True,
        "test_a_schema_valid": a.get("schema_valid") is True,
        "test_a_evidence_valid": a.get("evidence_valid") is True,
        "test_a_semantic_valid": a.get("semantic_valid") is True,
        "test_b_pass": b.get("passed") is True,
        "test_b_schema_valid": b.get("schema_valid") is True,
        "test_b_evidence_valid": b.get("evidence_valid") is True,
        "test_b_semantic_valid": b.get("semantic_valid") is True,
        "test_c_pass": c.get("passed") is True,
        "test_c_executed": c.get("status") != "NOT_EXECUTED"
                              and c.get("passed") is not None,
        # Every per-metric and per-flag check below REQUIRES
        # Test C to have actually executed.  When Test C is
        # NOT_EXECUTED, the numeric counters default to 0
        # (e.g. user_real_llm_calls=0) which would accidentally
        # satisfy the `== 0` predicate.  We guard each numeric
        # check with `c.get("passed") is True` so absent data
        # cannot satisfy the rule.
        "test_c_real_llm_calls_eq_2":
            c.get("passed") is True
            and c.get("real_llm_calls_in_recovery") == 2,
        "test_c_financial_real_llm_calls_eq_2":
            c.get("passed") is True
            and c.get("financial_real_llm_calls") == 2,
        "test_c_user_real_llm_calls_eq_0":
            c.get("passed") is True
            and c.get("user_real_llm_calls", -1) == 0,
        "test_c_operation_real_llm_calls_eq_0":
            c.get("passed") is True
            and c.get("operation_real_llm_calls", -1) == 0,
        "test_c_semantic_clean_path_pass":
            c.get("passed") is True
            and c.get("semantic_clean_path_pass") is True,
        "test_c_execution_integrity_pass":
            c.get("passed") is True
            and c.get("execution_integrity_pass") is True,
        "test_c_exact_evidence_version_pass":
            c.get("passed") is True
            and c.get("exact_evidence_version_pass") is True,
        "test_c_per_record_real_call_integrity":
            c.get("passed") is True
            and c.get("per_record_real_call_integrity") is True,
        # Day 13.6 FINAL: per-recovered-Artifact content
        # validation.  structural recovery success is not
        # enough; the recovered Claim and the recovered
        # Section must each pass the deterministic validator.
        "test_c_recovered_claim_schema_valid":
            c.get("passed") is True
            and c.get("recovered_financial_claim_schema_valid") is True,
        "test_c_recovered_claim_evidence_valid":
            c.get("passed") is True
            and c.get("recovered_financial_claim_evidence_valid") is True,
        "test_c_recovered_claim_semantic_valid":
            c.get("passed") is True
            and c.get("recovered_financial_claim_semantic_valid") is True,
        "test_c_recovered_section_schema_valid":
            c.get("passed") is True
            and c.get("recovered_financial_section_schema_valid") is True,
        "test_c_recovered_section_evidence_valid":
            c.get("passed") is True
            and c.get("recovered_financial_section_evidence_valid") is True,
        "test_c_recovered_section_semantic_valid":
            c.get("passed") is True
            and c.get("recovered_financial_section_semantic_valid") is True,
        "test_c_no_new_user_or_op_version":
            c.get("passed") is True
            and c.get("no_new_user_or_op_version") is True,
    }

    # The gate is the strict AND of every individual check.  An
    # empty / missing live_test_results key (e.g. test_c dict absent
    # in a --stop-after B run) defaults that check to False so the
    # gate cannot be flipped green by absence of data.
    live_gate_pass = all(gate_checks.values())

    gate = {
        "adapter_offline_pass": bool(adapter_offline_pass),
        "credential_available": bool(credential_available),
        "real_request_count": int(real_request_count),
        "live_gate_pass": live_gate_pass,
        "full_benchmark_allowed": live_gate_pass,
        "gate_checks": gate_checks,
        "reason": reason,
    }
    if ledger is not None and ledger.records:
        gate["provider_reported_token_total"] = (
            ledger.provider_reported_token_total
        )
        gate["records"] = [asdict(r) for r in ledger.records]
    if live_test_results is not None:
        gate["live_test_results"] = live_test_results

    return gate
