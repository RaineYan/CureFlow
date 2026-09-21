"""Stage 2 — Trial-level data extraction (read-only).

Reads Stage 1.5 Run 2 evidence (12 trial JSONs + 12 ledgers + final report),
builds a unified trial matrix, and writes:
  - stage2_trial_matrix.csv
  - stage2_trial_matrix.json

Cross-references CANONICAL_CASES for fault_type and target_artifact_id
(trial JSONs only store case_id, not the (fault_type, target) pair).

NEVER modifies Stage 1.5 evidence. Writes only to stage2_evaluation/.
"""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

RUN2 = ROOT / "experiments/results/exp02/stage15_real_run2"
STAGE2 = ROOT / "experiments/results/exp02/stage2_evaluation"
STAGE2.mkdir(parents=True, exist_ok=True)

# CANONICAL_CASES is the single source of truth
from experiments.exp02_faults import CANONICAL_CASES  # type: ignore
CASE_META = {cid: {"scenario_id": sid, "fault_type": ft, "target_artifact_id": tgt}
             for cid, sid, ft, tgt in CANONICAL_CASES}

# Strategy order
STRAT_ORDER = ["FULL_RERUN", "COARSE_CHECKPOINT", "SELECTIVE_RECOVERY"]
CASE_ORDER = ["CASE_A", "CASE_B", "CASE_C", "CASE_D"]


def read_ledger_first_and_last(ledger_path: Path):
    """Return (first_request_id, last_request_id, first_usage_type, last_usage_type,
                first_response_keys, last_response_keys, call_count)."""
    if not ledger_path.exists():
        return None, None, None, None, None, None, 0
    first, last = None, None
    n = 0
    first_keys, last_keys = None, None
    first_usage, last_usage = None, None
    first_req, last_req = None, None
    with ledger_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            n += 1
            if first is None:
                first = rec
                first_keys = rec.get("raw_response_shape", {}).get("top_level_keys")
                first_usage = rec.get("usage_type")
                first_req = rec.get("request_id")
            last = rec
            last_keys = rec.get("raw_response_shape", {}).get("top_level_keys")
            last_usage = rec.get("usage_type")
            last_req = rec.get("request_id")
    return (first_req, last_req, first_usage, last_usage,
            first_keys, last_keys, n)


def main():
    rows = []
    for case_id in CASE_ORDER:
        for strat in STRAT_ORDER:
            trial_id = f"stage15_{case_id}_{strat.lower()}_01"
            trial_path = RUN2 / f"{trial_id}.json"
            ledger_path = RUN2 / f"{trial_id}_live_ledger.jsonl"
            if not trial_path.exists():
                continue
            t = json.loads(trial_path.read_text(encoding="utf-8"))
            meta = CASE_META.get(case_id, {})
            (first_req, last_req, first_usage, last_usage,
             first_keys, last_keys, ledger_n) = read_ledger_first_and_last(ledger_path)

            row = {
                # identity
                "trial_id": trial_id,
                "case_id": t.get("case_id"),
                "scenario_id": t.get("scenario_id") or meta.get("scenario_id", "N/A"),
                "fault_type": meta.get("fault_type", "N/A"),
                "target_artifact_id": meta.get("target_artifact_id", "N/A"),
                "strategy": t.get("strategy"),
                # 5-axis (5-axis contract)
                "provider_execution_success": t.get("provider_success"),
                "recovery_execution_success": t.get("recovery_execution_success"),
                "output_contract_valid": t.get("output_contract_valid"),
                "semantic_validation_success": t.get("semantic_validation_success"),
                "trial_valid": t.get("trial_valid"),
                "workflow_contract_success": t.get("workflow_contract_success"),
                # extra axes reported separately
                "recovery_success": t.get("recovery_success"),
                "golden_match": t.get("golden_match"),
                # cost & time
                "new_llm_calls": t.get("llm_calls"),
                "provider_reported_tokens": t.get("tokens_total"),
                "wall_time_ms": t.get("wall_time_ms"),
                # reused_artifacts — not in trial JSON; filled N/A
                "reused_artifacts": "N/A",
                # failure
                "failure_class": t.get("failure_type", "N/A"),
                "failure_subtype": t.get("infrastructure_failure_subtype", "N/A"),
                "failure_reason": t.get("error_message", "N/A"),
                "exception_type": t.get("error_type", "N/A"),
                # request / chatcmpl — from ledger
                "first_request_id": first_req or "N/A",
                "last_request_id": last_req or "N/A",
                "first_usage_type": first_usage or "N/A",
                "last_usage_type": last_usage or "N/A",
                "first_response_top_keys": first_keys or "N/A",
                "last_response_top_keys": last_keys or "N/A",
                "ledger_call_count": ledger_n,
                # retry status
                "retry_attempt": t.get("retry_attempt", 0),
                "retry_outcome": t.get("retry_outcome", "NOT_RETRIED"),
                "retry_exception": t.get("retry_exception", "N/A"),
            }
            rows.append(row)

    # write CSV
    csv_path = STAGE2 / "stage2_trial_matrix.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"CSV: {csv_path}  rows: {len(rows)}")

    # write JSON
    json_path = STAGE2 / "stage2_trial_matrix.json"
    json_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"JSON: {json_path}  rows: {len(rows)}")

    # also print a compact view to stdout
    print()
    cols = ["trial_id", "provider_execution_success", "trial_valid",
            "semantic_validation_success", "new_llm_calls",
            "provider_reported_tokens", "wall_time_ms",
            "failure_class", "failure_subtype", "retry_outcome"]
    for r in rows:
        print(" | ".join(f"{c}={r[c]}" for c in cols))


if __name__ == "__main__":
    main()
