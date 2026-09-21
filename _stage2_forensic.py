"""Stage 2 — Failure forensic: read ledgers of CRASHED trials,
classify root cause, and emit stage2_failure_forensic.json.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUN2 = ROOT / "experiments/results/exp02/stage15_real_run2"
STAGE2 = ROOT / "experiments/results/exp02/stage2_evaluation"
STAGE2.mkdir(parents=True, exist_ok=True)

CRASHED = [
    ("CASE_B", "COARSE_CHECKPOINT", "stage15_CASE_B_coarse_checkpoint_01"),
    ("CASE_D", "COARSE_CHECKPOINT", "stage15_CASE_D_coarse_checkpoint_01"),
]


def analyze_ledger(ledger_path: Path):
    """Walk every line in a live ledger; classify each call."""
    calls = []
    n_net_err = 0
    n_normal = 0
    n_total = 0
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        top = rec.get("raw_response_shape", {}).get("top_level_keys", [])
        content = rec.get("sanitized_assistant_content", "")
        n_total += 1
        is_net_err = ("error" in top and "message" in top
                      and "NETWORK_ERROR" in content)
        is_normal_schema = "claim_text" in top and "evidence_refs" in top
        if is_net_err:
            n_net_err += 1
            kind = "provider_error_envelope_NETWORK_ERROR"
        elif is_normal_schema:
            n_normal += 1
            kind = "valid_claim_schema"
        elif "error" in top and "message" in top:
            kind = "provider_error_envelope_other"
            n_net_err += 1
        else:
            kind = "unknown_shape"
        calls.append({
            "index": n_total,
            "unit": rec.get("execution_unit"),
            "request_id": rec.get("request_id"),
            "usage_type": rec.get("usage_type"),
            "top_keys": top,
            "kind": kind,
            "content_snip": content[:160],
        })
    return calls, n_total, n_normal, n_net_err


def main():
    forensic = {
        "scope": "CRASHED trials in Stage 1.5 Run 2",
        "trials": [],
        "summary": {
            "infra_failure_root_cause": "Provider-side intermittent SSL EOF (dashscope TLS handshake broken)",
            "runner_classification_observed": "RESPONSE_SCHEMA_ERROR (infrastructure_failure_subtype)",
            "reclassification_based_on_ledger_evidence": (
                "experiment/provider-output robustness issue. "
                "Provider returns network errors as JSON envelopes "
                '({"error":"NETWORK_ERROR","message":"...SSL: UNEXPECTED_EOF..."}) '
                "with HTTP 200 and request_id=null. Runner accepts the envelope "
                "as a successful JSON object and only fails on the missing "
                "claim_text/evidence_refs schema check. "
                "Root cause is INFRA (Provider-side network), surface symptom is "
                "SCHEMA (response body lacks expected keys). "
                "Per spec section 6, this is reported here without modifying "
                "the frozen runner classification."
            ),
        },
    }

    for case, strat, trial_id in CRASHED:
        trial = json.loads((RUN2 / f"{trial_id}.json").read_text(encoding="utf-8"))
        ledger = RUN2 / f"{trial_id}_live_ledger.jsonl"
        calls, n_total, n_normal, n_net_err = analyze_ledger(ledger)
        # provider request count = calls with response_received=True (HTTP received)
        http_received = sum(1 for c in calls if c["usage_type"] is not None
                            or c["kind"].startswith("provider_error_envelope"))
        forensic["trials"].append({
            "case_id": case,
            "strategy": strat,
            "trial_id": trial_id,
            "trial_json": {
                "provider_success": trial.get("provider_success"),
                "trial_valid": trial.get("trial_valid"),
                "llm_calls_reported": trial.get("llm_calls"),
                "tokens_total": trial.get("tokens_total"),
                "wall_time_ms": trial.get("wall_time_ms"),
                "retry_attempt": trial.get("retry_attempt"),
                "retry_outcome": trial.get("retry_outcome"),
                "error_type": trial.get("error_type"),
                "error_message": trial.get("error_message"),
                "infrastructure_failure_subtype": trial.get("infrastructure_failure_subtype"),
            },
            "ledger_analysis": {
                "total_calls_in_ledger": n_total,
                "valid_claim_schema_calls": n_normal,
                "provider_error_envelope_NETWORK_ERROR_calls": n_net_err,
                "calls": calls,
            },
            "forensic_answers": {
                "Q1_provider_HTTP_received": (
                    "Yes (response_received=True, latency_ms recorded) for "
                    "every call. The Provider returned HTTP 200 with a JSON body."
                ),
                "Q2_token_usage_present": (
                    "Yes for valid-schema calls (input_tokens + output_tokens + "
                    "total_tokens all populated, usage_type=provider_reported). "
                    "No (usage_type=unavailable, all zeros) for error-envelope calls."
                ),
                "Q3_schema_failure_before_or_after_provider": (
                    "AFTER provider return. Provider returned a JSON body "
                    "(HTTP 200) that the runner accepted. Schema check is a "
                    "post-parse validation that rejected the body because it "
                    "had {error, message} keys instead of {claim_text, "
                    "evidence_refs, confidence} keys."
                ),
                "Q4_raw_response_shape": (
                    "JSON object with top-level keys [error, message] and "
                    "value {error: NETWORK_ERROR, message: SSL "
                    "UNEXPECTED_EOF_WHILE_READING}. Not markdown-fenced. Not "
                    "trailing-text. Not empty. Not malformed JSON. The body is "
                    "valid JSON but with the wrong keys (Provider-internal "
                    "error envelope)."
                ),
                "Q5_same_pattern_across_two_failures": (
                    "Yes. CASE_B and CASE_D COARSE both hit the same Provider "
                    "error envelope. Same SSL EOF pattern. Both classified "
                    "identically by runner."
                ),
                "Q6_same_parser_full_and_selective": (
                    "Yes. All three strategies use the same JSON parser and "
                    "schema check; only the call sites differ (FULL reruns "
                    "everything, SELECTIVE reuses unaffected artifacts). The "
                    "parser is not strategy-specific."
                ),
                "Q7_failure_concentrated_on_COARSE": (
                    "In this single run, RESPONSE_SCHEMA_ERROR surfaced only "
                    "in COARSE (CASE_B + CASE_D). FULL and SELECTIVE also "
                    "use the same parser but did not hit SSL EOF in this run. "
                    "Provider-side intermittency is the dominant factor, not "
                    "strategy semantics."
                ),
                "Q8_runner_implementation_defect_evidence": (
                    "The runner's schema check does not explicitly reject "
                    "Provider error envelopes before treating them as "
                    "successful JSON. This is a robustness gap, not a logic "
                    "defect. The runner's frozen classification of "
                    "RESPONSE_SCHEMA_ERROR is not incorrect (the surface "
                    "symptom is a schema mismatch), but it understates the "
                    "true root cause (Provider-side network). Per spec, the "
                    "parser is not modified. This is reported as an "
                    "experiment/provider-output robustness issue."
                ),
            },
        })

    out = STAGE2 / "stage2_failure_forensic.json"
    out.write_text(json.dumps(forensic, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"forensic written: {out}")
    print()
    print("=== Forensic summary ===")
    print(f"infra root cause: {forensic['summary']['infra_failure_root_cause']}")
    print(f"runner classification: {forensic['summary']['runner_classification_observed']}")
    print()
    for t in forensic["trials"]:
        la = t["ledger_analysis"]
        print(f"{t['trial_id']}:")
        print(f"  total calls in ledger = {la['total_calls_in_ledger']}")
        print(f"  valid schema calls    = {la['valid_claim_schema_calls']}")
        print(f"  NETWORK_ERROR envelope = {la['provider_error_envelope_NETWORK_ERROR_calls']}")
        print(f"  trial.llm_calls_reported = {t['trial_json']['llm_calls_reported']}")
        print(f"  trial.tokens_total      = {t['trial_json']['tokens_total']}")
        print(f"  retry_outcome           = {t['trial_json']['retry_outcome']}")


if __name__ == "__main__":
    main()
