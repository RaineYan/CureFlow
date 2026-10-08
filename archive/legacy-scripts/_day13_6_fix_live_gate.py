"""One-shot offline fix for the live gate JSON.

The previous `--stop-after B` run wrote `live_gate_pass: true`
even though Test C was NOT_EXECUTED.  This script recomputes the
gate from the real Test A + Test B ledger records (no LLM call)
and rewrites the gate JSON with the corrected decision.

Run once: `python _day13_6_fix_live_gate.py`
"""
from __future__ import annotations

import json

from experiments.live_ledger import (
    LiveCallLedger,
    LiveCallRecord,
    build_day13_5_live_gate,
)


def main() -> int:
    gate_path = "experiments/results/day13_5_live_gate.json"
    with open(gate_path, "r", encoding="utf-8") as f:
        old = json.load(f)

    # Replay the real Test A + Test B ledger records into a fresh
    # ledger so the gate audit has the right provider-reported
    # token total and real_request_count.
    ledger = LiveCallLedger()
    # Test A real record
    ledger.append(LiveCallRecord(
        phase="A",
        execution_unit="generate_financial_claim",
        provider="qwen",
        model="qwen3.7-plus-2026-05-26",
        request_id="chatcmpl-09fdfcb4-ad8c-9fc8-9016-79326cf3b530",
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
    ))
    # Test B real record (from the user's actual `--stop-after B`
    # run output)
    for r in old.get("records", []):
        if r.get("phase") == "B":
            ledger.append(LiveCallRecord(
                phase=r["phase"],
                execution_unit=r["execution_unit"],
                provider=r["provider"],
                model=r["model"],
                request_id=r["request_id"],
                usage_type=r["usage_type"],
                input_tokens=r["input_tokens"],
                output_tokens=r["output_tokens"],
                total_tokens=r["total_tokens"],
                client_wall_clock_latency_ms=r[
                    "client_wall_clock_latency_ms"
                ],
                schema_valid=r.get("schema_valid", False),
                evidence_valid=r.get("evidence_valid", False),
                semantic_valid=r.get("semantic_valid", False),
                produced_ref=r.get("produced_ref"),
                error_type=r.get("error_type"),
                error_message_sanitized=r.get(
                    "error_message_sanitized"
                ),
                reasoning_tokens=r.get("reasoning_tokens"),
                request_enable_thinking=r.get(
                    "request_enable_thinking"
                ),
                request_response_format=r.get(
                    "request_response_format"
                ),
            ))

    # Build the corrected live_test_results
    live_results = {
        "test_a": {
            "passed": True,
            "status": "PASS_RESUMED",
            "schema_valid": True,
            "evidence_valid": True,
            "semantic_valid": True,
            "numeric_consistency_pass": True,
            "direction_consistency_pass": True,
            "recommendation_boundary_pass": True,
        },
        "test_b": {
            "passed": True,
            "status": "PASS",
            "schema_valid": True,
            "evidence_valid": True,
            "semantic_valid": True,
        },
        "test_c": {
            "passed": None,
            "status": "NOT_EXECUTED",
            "real_llm_calls_in_recovery": 0,
            "financial_real_llm_calls": 0,
            "user_real_llm_calls": 0,
            "operation_real_llm_calls": 0,
            "semantic_clean_path_pass": False,
            "execution_integrity_pass": False,
            "exact_evidence_version_pass": False,
        },
    }

    corrected = build_day13_5_live_gate(
        adapter_offline_pass=True,
        credential_available=True,
        real_request_count=ledger.real_request_count,
        reason=("Test A and B passed; Test C selective "
                "recovery not yet verified."),
        ledger=ledger,
        live_test_results=live_results,
    )

    with open(gate_path, "w", encoding="utf-8") as f:
        json.dump(corrected, f, ensure_ascii=False, indent=2)

    print("CORRECTED live_gate.json:")
    print(f"  live_gate_pass         = {corrected['live_gate_pass']}")
    print(f"  full_benchmark_allowed = "
          f"{corrected['full_benchmark_allowed']}")
    print(f"  real_request_count     = "
          f"{corrected['real_request_count']}")
    print(f"  provider_reported_token_total = "
          f"{corrected.get('provider_reported_token_total')}")
    print(f"  reason                 = {corrected['reason']}")
    print(f"  gate_checks: ({len(corrected['gate_checks'])} checks)")
    for k, v in corrected["gate_checks"].items():
        marker = "X" if v else "."
        print(f"    [{marker}] {k} = {v}")
    print("  live_test_results:")
    for test, res in corrected["live_test_results"].items():
        print(f"    {test}: {res}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
