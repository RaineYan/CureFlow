"""Persist the Day 13.6 real Test C failure as a reproducible
sample (no LLM call).  This is the OFFICIAL failure record for
Test C; the API key is never present.

Root cause of the failure (post-mortem):
  - `run_test_c` used `target_ref = report_merged@v2`, but the
    baseline workflow only produced v1.  v2 did not exist.
  - `SelectiveRecoveryPlanner` therefore saw `target_is_bad=False`
    (None is not INVALIDATED/CONTAMINATED) and produced an empty
    `RecoveryPlan`.
  - With 0 REPROMPT steps, no real Qwen call was issued during
    the recovery phase, even though `gate.real_request_count` was
    reported as 3 (because chain-loaded replayed records inflated
    the count).
  - The recovery was therefore a no-op: the contaminated
    `report_merged@v1` was not recovered; the empty plan was
    trivially `success=True` only because `validate_target_clean_path`
    against a non-existent v2 returned `False`, but that flag was
    not surfaced to the final gate.
  - Net effect: live Gate's Test C passed = false, but the
    recorded `real_request_count` does NOT match the actual
    number of real provider HTTP requests.
"""
from __future__ import annotations

import json
import os
import re

from experiments.live_ledger import LiveCallLedger, LiveCallRecord


def main() -> int:
    out_path = "experiments/results/day13_6_real_test_c_failure_001.json"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    # The 3 records in the live gate (2 chain-loaded replays of
    # the historical Test A + Test B, plus 1 spurious
    # double-replay of A during `_load_test_b_state`'s
    # chain-load).  None of these 3 is a real Test C recovery
    # request — there were 0 real Qwen calls during the
    # recovery phase because the plan was empty.
    chain_loaded_records = [
        LiveCallRecord(
            phase="A",
            execution_unit="generate_financial_claim",
            provider="qwen",
            model="qwen3.7-plus-2026-05-26",
            request_id="req-a",  # chain-load replay marker
            usage_type="provider_reported",
            input_tokens=20,
            output_tokens=10,
            total_tokens=30,
            client_wall_clock_latency_ms=100.0,
            schema_valid=True,
            evidence_valid=True,
            semantic_valid=True,
            produced_ref="live_test_a/claim_financial_growth@v1",
            error_type=None,
            error_message_sanitized=None,
            reasoning_tokens=None,
            request_enable_thinking=None,
            request_response_format=None,
        ),
        LiveCallRecord(
            phase="B",
            execution_unit="generate_financial_section",
            provider="qwen",
            model="qwen3.7-plus-2026-05-26",
            request_id="req-b",  # chain-load replay marker
            usage_type="provider_reported",
            input_tokens=15,
            output_tokens=8,
            total_tokens=23,
            client_wall_clock_latency_ms=80.0,
            schema_valid=True,
            evidence_valid=True,
            semantic_valid=True,
            produced_ref="live_test_a/claim_report_financial_section@v1",
            error_type=None,
            error_message_sanitized=None,
            reasoning_tokens=None,
            request_enable_thinking=None,
            request_response_format=None,
        ),
        LiveCallRecord(
            phase="A",
            execution_unit="generate_financial_claim",
            provider="qwen",
            model="qwen3.7-plus-2026-05-26",
            request_id="req-a",  # chain-load replay marker (double)
            usage_type="provider_reported",
            input_tokens=20,
            output_tokens=10,
            total_tokens=30,
            client_wall_clock_latency_ms=100.0,
            schema_valid=True,
            evidence_valid=True,
            semantic_valid=True,
            produced_ref="live_test_a/claim_financial_growth@v1",
            error_type=None,
            error_message_sanitized=None,
            reasoning_tokens=None,
            request_enable_thinking=None,
            request_response_format=None,
        ),
    ]

    failure = {
        "schema_version": 1,
        "saved_at_unix_ms": 1787150000000,
        "test_c_run_id": "live_test_c",
        "failure_root_cause": (
            "run_test_c set target_ref = live_test_c/report_merged@v2, "
            "but the baseline workflow only produced v1.  v2 did not "
            "exist in the store.  SelectiveRecoveryPlanner therefore "
            "treated target_is_bad = False (None is not in "
            "{INVALIDATED, CONTAMINATED}) and produced an empty "
            "RecoveryPlan (0 steps).  RecoveryExecutor.execute() ran "
            "0 REPROMPT steps, so 0 real Qwen calls were issued "
            "during the recovery phase.  live_gate.real_request_count "
            "= 3 because three LiveCallRecords were appended to the "
            "ledger: 1 by main()'s _load_test_a_state() (else branch), "
            "1 by _load_test_b_state()'s own ledger_record replay, and "
            "1 by _load_test_b_state()'s internal chain-load call to "
            "_load_test_a_state() — NONE of these three is a Test C "
            "real recovery request.  Counting logic bug: "
            "LiveCallLedger.real_request_count = len(records) treats "
            "chain-loaded replayed records as 'real requests'."
        ),
        "stage_failed_at": "RecoveryPlan.steps is empty (pre-execution)",
        "real_request_count_in_gate": 3,
        "actual_real_qwen_requests_in_recovery_phase": 0,
        "successful_recovery_llm_artifact_materializations": 0,
        "reprompt_steps_in_plan": 0,
        "recovered_financial_claim_ref": None,
        "recovered_financial_section_ref": None,
        "final_report_ref_after_recovery": "live_test_c/report_merged@v2",
        "sanitized_error": (
            "plan.steps is empty because target_ref v2 does not exist "
            "in the store; target_is_bad evaluated to False.  The "
            "recovery phase did not issue any LLM call.  This is a "
            "planner / run_test_c configuration bug, not a model "
            "failure."
        ),
        "chain_loaded_records": [
            {
                "phase": r.phase,
                "execution_unit": r.execution_unit,
                "request_id": r.request_id,
                "is_real_recovery_request": False,
                "is_chain_loaded_replay": True,
                "usage_type": r.usage_type,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "total_tokens": r.total_tokens,
                "latency_ms": r.client_wall_clock_latency_ms,
                "produced_ref": r.produced_ref,
            }
            for r in chain_loaded_records
        ],
        "test_a_test_b_real_qwen_count": 2,
        "test_c_real_qwen_count": 0,
        "two_distinct_metrics_defined": {
            "actual_real_provider_requests_in_recovery":
                "Every HTTP request issued by the live recovery phase. "
                "Must increment even if downstream parse/schema/semantic "
                "fails.  Recovered by `_RecordingClient` auto-append at "
                "HTTP response time.",
            "successful_recovery_llm_artifact_materializations":
                "Number of REPROMPT steps whose produced_ref is non-null "
                "in the recovery plan execution.  This is the 'tangible' "
                "success metric; can be 0 when no LLM artifact was "
                "produced in the recovery phase."
        },
        "no_api_key": True,
        "notes": (
            "The api key is NEVER present in this file.  request_id "
            "fields are replay markers ('req-a' / 'req-b') and do not "
            "resemble real provider request IDs.  Failure preserved for "
            "post-mortem and regression-test seeding."
        ),
    }

    # Sanity check: ensure no api-key-shaped string in the failure
    # sample.
    state_text = json.dumps(failure, ensure_ascii=False)
    leaked = re.findall(r"[A-Za-z0-9_\-]{32,}", state_text)
    if leaked:
        print(f"  WARN: failure sample contains {len(leaked)} long "
              f"opaque strings")
        for s in leaked:
            print(f"    {s[:60]}...")

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(failure, f, ensure_ascii=False, indent=2)
    print(f"Saved failure sample to {out_path}")
    print(f"  stage_failed_at                 = {failure['stage_failed_at']}")
    print(f"  real_request_count_in_gate       = "
          f"{failure['real_request_count_in_gate']}")
    print(f"  actual_real_qwen_requests_in_recovery = "
          f"{failure['actual_real_qwen_requests_in_recovery_phase']}")
    print(f"  successful_materializations     = "
          f"{failure['successful_recovery_llm_artifact_materializations']}")
    print(f"  recovered_financial_claim_ref   = "
          f"{failure['recovered_financial_claim_ref']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
