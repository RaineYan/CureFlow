"""One-shot offline supplement for the metadata fields the upstream
fix did not restore.  This script does NOT call any LLM.  It:
  1) reads the v3 state
  2) re-attaches the full semantic dict (from the real Test A
     audit JSON user pasted in the request) and the real ledger
     record
  3) re-saves the state

Run once after the upstream fix.
"""
from __future__ import annotations

import json

from artifacts import ArtifactStore
from live_gate_run import _save_test_a_state
from experiments.live_ledger import LiveCallLedger, LiveCallRecord


def main() -> int:
    state_path = "experiments/results/day13_5_test_a_state.json"
    with open(state_path, "r", encoding="utf-8") as f:
        state = json.load(f)
    ta = state["test_a"]

    # Real semantic dict from user's Test A audit paste
    real_semantic = {
        "passed": True,
        "evidence_valid": True,
        "numeric_consistency_pass": True,
        "direction_consistency_pass": True,
        "unsupported_claims_pass": True,
        "recommendation_boundary_pass": True,
        "checked_refs": [
            {"run_id": "live_test_a", "artifact_id": "metric_revenue_growth",
             "version": 1},
            {"run_id": "live_test_a", "artifact_id": "metric_profit_margin",
             "version": 1},
        ],
        "issues": [],
        "details": {
            "numeric": [
                {
                    "evidence_ref": "live_test_a/metric_revenue_growth@v1",
                    "expected_pct": 60.78431372549019,
                    "text_pct": 60.78,
                    "diff_pp": -0.004313725490192155,
                    "within_tolerance": True,
                    "match_status": "MATCHED",
                },
                {
                    "evidence_ref": "live_test_a/metric_profit_margin@v1",
                    "expected_pct": 47.5609756097561,
                    "text_pct": 47.56,
                    "diff_pp": -0.0009756097560966737,
                    "within_tolerance": True,
                    "match_status": "MATCHED",
                },
            ],
            "numeric_extras": [],
        },
    }

    # Real ledger record (from user's Test A audit paste)
    real_ledger = LiveCallRecord(
        phase="A",
        execution_unit="generate_financial_claim",
        provider="qwen",
        model="qwen3.7-plus-2026-05-26",
        request_id=ta["request_id"],
        usage_type="provider_reported",
        input_tokens=558,
        output_tokens=82,
        total_tokens=640,
        client_wall_clock_latency_ms=4440.737499993702,
        schema_valid=True,
        evidence_valid=True,
        semantic_valid=True,
        produced_ref="live_test_a/claim_financial_growth@v1",
        reasoning_tokens=None,
        request_enable_thinking=False,
        request_response_format={"type": "json_object"},
    )

    # Re-build test_a dict with the supplemented metadata
    from artifacts import Artifact
    claim = Artifact.model_validate(ta["claim_artifact"])
    test_a = {
        "passed": True,
        "schema_valid": True,
        "evidence_valid": True,
        "numeric_consistency_pass": True,
        "direction_consistency_pass": True,
        "recommendation_boundary_pass": True,
        "semantic_valid": True,
        "claim_ref": ta["claim_ref"],
        "claim_artifact": claim,
        "usage_type": "provider_reported",
        "request_id": ta["request_id"],
        "reasoning_tokens": None,
        "request_enable_thinking": False,
        "request_response_format": {"type": "json_object"},
        "real_request_count": 1,
        "semantic": real_semantic,
    }
    ledger = LiveCallLedger()
    ledger.append(real_ledger)

    store = ArtifactStore(base_dir="experiments/results/day13_5_test_a_store")
    store.reload()
    _save_test_a_state(test_a, ledger, store)

    with open(state_path, "r", encoding="utf-8") as f:
        new_state = json.load(f)
    nta = new_state["test_a"]
    print("AFTER SUPPLEMENT:")
    print("  semantic present:",
          nta.get("semantic") is not None,
          "with keys:" if nta.get("semantic") else "",
          list(nta["semantic"].keys()) if nta.get("semantic") else "")
    print("  upstream_artifacts count:",
          len(nta.get("upstream_artifacts", [])))
    print("  raw_data present:",
          nta.get("raw_data") is not None)
    rd = new_state.get("ledger_record")
    print("  ledger_record present:", rd is not None)
    if rd:
        print(f"    request_id      = {rd['request_id']}")
        print(f"    total_tokens    = {rd['total_tokens']}")
        print(f"    input_tokens    = {rd['input_tokens']}")
        print(f"    output_tokens   = {rd['output_tokens']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
