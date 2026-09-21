"""Day 14A trial01R1 failure audit.

The user reported trial01R1 crashed with:
  ValueError: LLM output failed claim schema:
    missing required key: 'claim_text'
    missing required key: 'evidence_refs'

The original trial01R1.json was never written (the
harness had no exception containment, so the ValueError
propagated up before the JSON was saved).  This audit
captures everything we know about the failure.

NOTE: this is an OFFLINE forensic only.  No real Qwen
calls were made by this session.  The real Provider
records (if any) are in the user's local PowerShell
session's tmpdir; the real Provider HTTP request
status is REQUIRES MANUAL CONFIRMATION by the user
from their terminal output.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from experiments.day14 import (
    FROZEN_PROVIDER_CONFIG,
    assert_frozen_evidence_intact,
)


ORIGINAL_TRIAL = Path(
    "experiments/results/day14/pilot/case_a_full_rerun_trial01.json"
)
AUDIT_PATH = Path(
    "experiments/results/day14/pilot/case_a_full_rerun_trial01R1_failure_audit.json"
)


def main() -> int:
    # 0) Day 13 frozen evidence must be intact
    frozen = assert_frozen_evidence_intact()
    if not frozen["passed"]:
        print("STOP: Day 13 frozen evidence mutated.")
        return 1

    # 1) original trial01 still untouched
    if not ORIGINAL_TRIAL.exists():
        print(f"STOP: original trial01 missing: {ORIGINAL_TRIAL}")
        return 1
    original_bytes = ORIGINAL_TRIAL.read_bytes()
    original_sha = hashlib.sha256(original_bytes).hexdigest()
    print(
        f"original trial01 size={len(original_bytes)} "
        f"sha256={original_sha[:16]}"
    )

    # 2) Trial01R1 has NO on-disk file (the harness
    # crashed before write_trial_result was called).
    # Confirm the absence and the file path the CLI
    # WOULD have written.
    expected_replacement = Path(
        "experiments/results/day14/pilot/"
        "case_a_full_rerun_trial01R1.json"
    )
    if expected_replacement.exists():
        print(
            f"WARN: replacement file exists: "
            f"{expected_replacement} (unexpected)"
        )

    # 3) Build the audit
    audit = {
        "schema_version": 1,
        "audit_purpose": (
            "Day 14A trial01R1 forensic re-classification. "
            "trial01R1 crashed during the FIRST real LLM "
            "call (generate_financial_claim).  The harness "
            "had no exception containment, so the trial "
            "JSON was never written.  This audit captures "
            "the available evidence."
        ),
        "trial_id": "case_a_full_rerun_trial01R1",
        "case_id": "case_a",
        "strategy": "FULL_RERUN",
        "replaces_trial_id": "case_a_full_rerun_trial01",
        "replacement_trial_id": "case_a_full_rerun_trial01R1",
        "replacement_reason": "HARNESS_PROVIDER_ROUTING_ERROR",
        "valid_trial": False,
        "replacement_required": False,
        "retry_permitted": False,
        "retry_block_reason": (
            "Per spec section 9: VALIDATION_FAILURE is NOT "
            "auto-retryable.  Manual root-cause review and "
            "explicit re-authorization required before any "
            "new replacement is created."
        ),
        # PROVISIONAL classification.  The user's HARDENING
        # spec demands we MUST NOT classify as
        # REAL_MODEL_OUTPUT_VALIDATION_FAILURE without
        # confirmed evidence of a real Provider call.
        # The original trial01R1 trial JSON was never
        # written (harness had no exception containment at
        # the time), so the Day14RecordingClient ledger is
        # gone.  We do NOT know whether the schema FAIL
        # came from a real Qwen response or from a
        # pre-flight probe.
        "failure_class": "UNCONFIRMED_PROVIDER_EXECUTION_FAILURE",
        "failure_subtype": "CLAIM_SCHEMA_FAILURE_OBSERVED",
        "provider_execution_verified": False,
        "candidate_failure_classes": [
            "REAL_MODEL_OUTPUT_VALIDATION_FAILURE",
            "HARNESS_RESPONSE_ADAPTER_ERROR",
        ],
        "promotion_rule": (
            "May be promoted to REAL_MODEL_OUTPUT_VALIDATION_FAILURE "
            "ONLY after manual confirmation that: "
            "(a) Day14RecordingClient captured at least one record "
            "with usage_type=provider_reported, AND "
            "(b) request_id starts with 'chatcmpl-', AND "
            "(c) raw_response_shape is a non-empty JSON object. "
            "Until then it remains UNCONFIRMED."
        ),
        "failure_stage": "first_LLM_call_parse",
        "execution_unit": "generate_financial_claim",
        "exception_type": "ValueError",
        "exception_message_sanitized": (
            "LLM output failed claim schema: "
            "missing required key: 'claim_text'; "
            "missing required key: 'evidence_refs'"
        ),
        "exception_origin": (
            "workflow/claim_tasks.py:_parse_llm_output"
        ),
        "provider_request_occurred": "REQUIRES_MANUAL_CONFIRMATION",
        "artifact_materialized": False,
        "actual_real_provider_requests": "REQUIRES_MANUAL_CONFIRMATION",
        "successful_llm_artifact_materializations": 0,
        "raw_response_shape": {
            "kind": "REQUIRES_MANUAL_CONFIRMATION",
            "note": (
                "User must inspect the trial01R1 terminal "
                "output (or the tmpdir Day14RecordingClient "
                "ledger, if preserved) to determine the "
                "raw response shape.  This session has no "
                "access to that data."
            ),
        },
        "parsed_top_level_keys": [],
        "sanitized_response_excerpt": None,
        "schema_issues": [
            "missing required key: 'claim_text'",
            "missing required key: 'evidence_refs'",
        ],
        "no_api_key": True,
        "confirmed_facts": [
            "parser raised ValueError",
            "missing required key: 'claim_text'",
            "missing required key: 'evidence_refs'",
            "successful materialization count = 0",
        ],
        "unconfirmed_facts": [
            "real provider request occurred",
            "request_id (chatcmpl-? / sentinel-? / null?)",
            "provider-reported token usage",
            "client_wall_clock_latency_ms",
            "raw assistant response text",
            "HTTP status code",
            "Qwen model / provider",
        ],
        "prompt_contract_audit": {
            "day13_financial_prompt": (
                "experiments/live_gate_run.py:run_test_a "
                "calls generate_financial_claim with "
                "RealLLMClient.  Uses "
                "build_claim_prompt(task_name='financial "
                "analyst', output_role='claim' default).  "
                "Includes FINANCIAL CLAIM CONTRACT when "
                "'financial' in task_name."
            ),
            "day14_full_prompt": (
                "experiments/strategies.py:_rerun_3branch_pipeline "
                "calls generate_financial_claim via "
                "build_claim_prompt(task_name='financial "
                "analyst', output_role='claim' default).  "
                "Same builder, same FINANCIAL CLAIM CONTRACT. "
                "Equivalent contract."
            ),
            "verdict": "EQUIVALENT",
            "verdict_evidence": (
                "Both Day 13 (live_gate_run.py) and "
                "Day 14 (strategies.py) call "
                "workflow.claim_tasks.generate_financial_claim, "
                "which uses workflow.claim_tasks.build_claim_prompt.  "
                "build_claim_prompt injects FINANCIAL CLAIM "
                "CONTRACT when 'financial' in task_name.  No "
                "strategy-specific prompt exists in Day 14.  "
                "The prompt contract is structurally identical."
            ),
        },
        "response_format_audit": {
            "frozen_config": FROZEN_PROVIDER_CONFIG.to_dict(),
            "request_payload_contains_response_format": (
                "REQUIRES_MANUAL_CONFIRMATION"
            ),
            "note": (
                "The RealLLMClient in llm/providers/real.py "
                "merges response_format into the body top "
                "level (Day 13.5 Qwen patch).  This is the "
                "expected wiring, but the user must confirm "
                "by inspecting the actual outgoing HTTP "
                "request from their PowerShell session."
            ),
        },
        "freeze": {
            "day13_evidence_intact": frozen["passed"],
            "day13_checked_files": len(frozen["checked_files"]),
            "original_trial01_sha256": original_sha,
            "original_trial01_size": len(original_bytes),
            "trial01_status": (
                "read-only (chmod IsReadOnly=True).  "
                "Schema-extended: 3 new fields "
                "(replacement_trial_id, replaces_trial_id, "
                "replacement_reason) added by an offline "
                "CLI run.  Trial semantic content unchanged."
            ),
        },
        "fixes_applied": [
            "day14_pilot_run.py: added try/except around "
            "run_trial so ValueError / RealLLMError / etc. "
            "no longer crash the CLI; the failure JSON and "
            "failure audit are always written.",
            "day14_pilot_run.py: _write_failure_audit now "
            "writes a {trial_id}_failure_audit.json "
            "containing the preserved ledger metrics, "
            "exception details, and sanitized response "
            "excerpt.",
            "experiments/day14/strategies.py: TrialResult "
            "gains replacement_trial_id / replaces_trial_id / "
            "replacement_reason for the routing contract.",
        ],
        "outstanding_questions_for_user": [
            "Did the trial01R1 actually issue a real HTTP "
            "request?  Check the trial01R1 terminal output "
            "for 'REAL PROVIDER VERIFIED' or check the "
            "tmpdir Day14RecordingClient ledger.",
            "What did the Qwen response look like?  The "
            "next trial must have a way to capture the "
            "raw response text into the failure audit.  "
            "The Day14RecordingClient currently does NOT "
            "store the response text; we should add this.",
            "Is the REAL_LLM_API_KEY set, and is the "
            "provider URL correct?",
        ],
        "next_action": (
            "BLOCKED on user-side real Provider "
            "diagnostic.  Per spec section 9, this "
            "VALIDATION_FAILURE does NOT auto-retry.  "
            "User must inspect, fix, and explicitly "
            "re-authorize before any new replacement "
            "trial is created."
        ),
    }
    AUDIT_PATH.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote: {AUDIT_PATH}")
    print(
        f"  failure_class: {audit['failure_class']}"
    )
    print(
        f"  valid_trial: False   retry_permitted: False"
    )
    print(
        f"  original trial01 sha256: {original_sha[:16]}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
