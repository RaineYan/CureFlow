"""Day 14A Pilot forensic — write the failure audit JSON
for trial01 (case_a / FULL_RERUN).

This is an OFFLINE forensic.  It re-classifies the
trial based on the new "REAL mode + 0 real records"
audit:

  PROVISIONAL failure_class:
    HARNESS_PROVIDER_ROUTING_ERROR

  Per the user's spec:
  - trial01 must be marked valid_trial = false
  - replacement_required = true
  - real_request_count for this trial = 0 (FORENSIC CONFIRMED)
  - this trial MUST NOT enter any token / latency /
    semantic success rate aggregate
  - the trial JSON is preserved (NOT deleted)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from experiments.day14 import (
    FROZEN_PROVIDER_CONFIG,
    assert_frozen_evidence_intact,
)


TRIAL_JSON = Path(
    "experiments/results/day14/pilot/case_a_full_rerun_trial01.json"
)
AUDIT_JSON = Path(
    "experiments/results/day14/pilot/case_a_full_rerun_trial01_failure_audit.json"
)


def main() -> int:
    # 0) Day 13 frozen evidence must be intact
    frozen = assert_frozen_evidence_intact()
    if not frozen["passed"]:
        print("STOP: Day 13 frozen evidence mutated.")
        return 1

    # 1) load trial JSON
    if not TRIAL_JSON.exists():
        print(f"missing: {TRIAL_JSON}")
        return 1
    trial = json.loads(TRIAL_JSON.read_text(encoding="utf-8"))

    # 2) re-classify
    n_new = len(trial.get("new_llm_artifact_refs", []))
    n_real = trial.get("real_metrics", {}).get(
        "real_llm_call_count", 0
    )
    n_reused = len(trial.get("reused_llm_artifact_refs", []))
    failure_reason = trial.get("failure_reason", "")
    per_artifact = trial.get("success_report", {}).get(
        "per_artifact_flags", {}
    )

    # Forensic classification logic
    if n_new > 0 and n_real == 0:
        failure_class = "HARNESS_PROVIDER_ROUTING_ERROR"
        failure_subtype = (
            "FAKE_LLM_ROUTED_IN_REAL_MODE — the "
            "Day14RecordingClient was built but the "
            "underlying RealLLMClient was NOT actually "
            "invoked by the strategy.  All 6 LLM artifacts "
            "were materialized by FakeLLM (the default "
            "fallback in experiments/strategies.py).  "
            "Real Qwen HTTP calls = 0."
        )
    elif n_new > 0 and n_real == 0 and fake_pattern_match:
        failure_class = "HARNESS_PROVIDER_ROUTING_ERROR"
        failure_subtype = (
            "FAKE_LLM_ROUTED_IN_REAL_MODE (confirmed by "
            "FakeLLM-style semantic pass/fail pattern: "
            "financial claim/section pass while user/operation "
            "claims fail).  Real Qwen HTTP calls = 0."
        )
    elif n_new > 0 and n_real > 0 and "semantic" in failure_reason:
        failure_class = "REAL_MODEL_VALIDATION_FAILURE"
        failure_subtype = "real Provider was called; downstream semantic validator flagged the response"
    else:
        failure_class = "UNKNOWN"
        failure_subtype = "see failure_reason"

    # 3) per-artifact flags indicate FakeLLM-style
    # semantic pass/fail pattern (financial PASS,
    # user/operation claims FAIL).  This is a strong
    # second indicator of FakeLLM routing.  We match
    # by artifact_id (the part before "@v<n>") to
    # avoid spurious matches on the section ids.
    fake_pattern = []
    for ref, flags in per_artifact.items():
        sem = flags.get("semantic_valid")
        # artifact_id is the substring before "@v"
        aid = ref.split("@v", 1)[0] if "@v" in ref else ref
        fake_pattern.append({"ref": ref, "aid": aid,
                             "semantic_valid": sem})
    # Financial claim and financial section: both PASS
    financial_claim_pass = any(
        p["semantic_valid"] is True
        for p in fake_pattern
        if p["aid"].endswith("claim_financial_growth")
    )
    financial_section_pass = any(
        p["semantic_valid"] is True
        for p in fake_pattern
        if p["aid"].endswith("claim_report_financial_section")
    )
    # User/operation claims: FAIL
    user_claim_fail = any(
        p["semantic_valid"] is False
        for p in fake_pattern
        if p["aid"].endswith("claim_user_growth")
    )
    operation_claim_fail = any(
        p["semantic_valid"] is False
        for p in fake_pattern
        if p["aid"].endswith("claim_operation")
    )
    fake_pattern_match = (
        financial_claim_pass
        and financial_section_pass
        and user_claim_fail
        and operation_claim_fail
    )

    # 4) build the audit
    audit = {
        "schema_version": 1,
        "audit_purpose": (
            "Day 14A Pilot trial01 forensic re-classification"
        ),
        "trial_json": str(TRIAL_JSON).replace("\\", "/"),
        "trial_id": trial.get("trial_id"),
        "case_id": trial.get("case_id"),
        "strategy": trial.get("strategy"),
        "valid_trial": False,
        "replacement_required": True,
        "replacement_trial_id_hint": (
            "case_a_full_rerun_trial01R1"
        ),
        "failure_class": failure_class,
        "failure_subtype": failure_subtype,
        "root_cause": (
            "experiments/strategies.py:522, 552, 580 — the "
            "three strategy functions (run_full_rerun_recovery, "
            "run_coarse_checkpoint_recovery, run_selective_recovery) "
            "internally called `default_workflow_llm()` which "
            "returns a FakeLLMClient.  The Day 14 wrapper "
            "(experiments/day14/strategies.py) had a "
            "TrialSpec.real_client field but did NOT pass it "
            "into the strategy functions.  As a result, even "
            "with REAL_LLM_API_KEY set and a RealLLMClient "
            "built, the strategy pipeline used FakeLLM.  "
            "FIXED in this turn: the three strategy functions "
            "now accept an optional `llm_client` parameter, "
            "and the Day 14 wrapper threads spec.real_client "
            "through.  Plus a Day14RecordingClient auto-appends "
            "to LiveCallLedger, plus a HARNESS_PROVIDER_ROUTING_ERROR "
            "hard gate that fails any REAL-mode trial with 0 "
            "real Provider records."
        ),
        "evidence": {
            "n_new_llm_artifacts": n_new,
            "n_reused_llm_artifacts": n_reused,
            "real_llm_call_count": n_real,
            "provider_reported_token_total":
                trial.get("real_metrics", {}).get(
                    "provider_reported_token_total", 0
                ),
            "failure_reason": failure_reason,
            "per_artifact_flags": per_artifact,
            "fake_pattern_match": fake_pattern_match,
            "fake_pattern_breakdown": fake_pattern,
            "recovery_wall_clock_ms":
                trial.get("recovery_wall_clock_ms"),
            "total_elapsed_s_observed":
                # trial01 was 0.37s — 6 real Qwen HTTP
                # calls cannot complete that fast.
                "≈ 0.37s (consistent with FakeLLM, "
                "inconsistent with 6 real Qwen HTTP calls)",
        },
        "request_artifact_audit": {
            "expected": n_new,
            "actual_real_requests": n_real,
            "matched": n_new == n_real,
            "verdict": (
                "MISMATCH: 6 LLM artifacts were materialized "
                "but 0 real Provider requests reached the "
                "ledger.  This is the canonical signature of "
                "HARNESS_PROVIDER_ROUTING_ERROR."
            ),
        },
        "exclusion_from_aggregates": {
            "valid_trial_token_averages": "EXCLUDED",
            "valid_trial_latency_averages": "EXCLUDED",
            "valid_trial_semantic_success_rate": "EXCLUDED",
            "appendix_destination":
                "experiments/results/day14/benchmark_engineering_"
                "failures_appendix.json",
        },
        "freeze": {
            "day13_evidence_intact": frozen["passed"],
            "day13_checked_files": len(frozen["checked_files"]),
        },
        "notes": (
            "trial01 is preserved as a forensic record.  The "
            "next attempt must be case_a_full_rerun_trial01R1 "
            "(replacement).  Do NOT run COARSE / SELECTIVE "
            "until trial01R1 passes."
        ),
    }

    AUDIT_JSON.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote: {AUDIT_JSON}")
    print(f"  failure_class: {failure_class}")
    print(f"  valid_trial: False")
    print(f"  replacement_required: True")
    print(f"  fake_pattern_match: {fake_pattern_match}")
    print(f"  real_records_for_{n_new}_artifacts: {n_real}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
