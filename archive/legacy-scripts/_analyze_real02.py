"""Analyze 6 real02 trials — get reduction data, verify
5-layer state, check evidence integrity."""
import json
import hashlib
from pathlib import Path
from collections import defaultdict

PILOT = Path("experiments/results/day14/pilot")

trials_meta = [
    ("case_a", "FULL_RERUN", "case_a_full_rerun_trial01R4"),
    ("case_a", "COARSE_CHECKPOINT", "case_a_coarse_checkpoint_trial01_real02"),
    ("case_a", "SELECTIVE_RECOVERY", "case_a_selective_recovery_trial01_real02"),
    ("case_b", "FULL_RERUN", "case_b_full_rerun_trial01_real02"),
    ("case_b", "COARSE_CHECKPOINT", "case_b_coarse_checkpoint_trial01_real02"),
    ("case_b", "SELECTIVE_RECOVERY", "case_b_selective_recovery_trial01_real02"),
]

print("=" * 64)
print("REAL02 — 6 trials analysis")
print("=" * 64)
print()

by_case_strategy = defaultdict(dict)
for case, strategy, tid in trials_meta:
    p = PILOT / f"{tid}.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    audit_p = PILOT / f"{tid}_failure_audit.json"
    audit = json.loads(audit_p.read_text(encoding="utf-8")) if audit_p.exists() else {}
    ledger_p = PILOT / f"{tid}_live_ledger.jsonl"
    ledger_lines = (
        ledger_p.read_text(encoding="utf-8").splitlines()
        if ledger_p.exists() else []
    )
    rm = d.get("real_metrics", {})
    print(f"--- {tid} ---")
    print(f"  success       : {d.get('success')}")
    print(f"  failure_reason: {(d.get('failure_reason') or '')[:120]}")
    print(f"  calls         : {rm.get('real_llm_call_count')}")
    print(f"  tokens        : {rm.get('provider_reported_token_total')}")
    print(f"  wall (ms)     : {rm.get('recovery_wall_clock_ms'):.0f}")
    print(f"  new_llm       : {len(d.get('new_llm_artifact_refs', []))}")
    print(f"  reused_llm    : {len(d.get('reused_llm_artifact_refs', []))}")
    if ledger_lines:
        # 5-layer state from ledger
        cap = json.loads(ledger_lines[0])
        prov = cap.get("provider")
        model = cap.get("model")
        ut = cap.get("usage_type")
        rid = cap.get("request_id")
        # count distinct request_ids
        all_rids = set()
        for ln in ledger_lines:
            r = json.loads(ln)
            if r.get("request_id"):
                all_rids.add(r["request_id"])
        print(f"  ledger: {len(ledger_lines)} records, "
              f"{len(all_rids)} unique request_ids")
        print(f"  sample provider={prov} model={model} usage={ut}")
        if rid:
            print(f"  sample request_id: {rid[:32]}...")
        else:
            print(f"  request_id: (none on first cap)")
    print()
    by_case_strategy[case][strategy] = {
        "trial_id": tid,
        "calls": rm.get("real_llm_call_count", 0),
        "tokens": rm.get("provider_reported_token_total", 0),
        "wall_ms": rm.get("recovery_wall_clock_ms", 0),
        "new_llm": len(d.get("new_llm_artifact_refs", [])),
        "reused_llm": len(d.get("reused_llm_artifact_refs", [])),
    }

# Reduction
print("=" * 64)
print("REDUCTION (REAL02 — actual Qwen)")
print("=" * 64)
for case in ("case_a", "case_b"):
    d = by_case_strategy[case]
    full = d.get("FULL_RERUN")
    coarse = d.get("COARSE_CHECKPOINT")
    selective = d.get("SELECTIVE_RECOVERY")
    if not (full and coarse and selective):
        continue
    print(f"\n--- {case} ---")
    for strat in ("FULL_RERUN", "COARSE_CHECKPOINT", "SELECTIVE_RECOVERY"):
        v = d[strat]
        print(f"  {strat:20s}  calls={v['calls']:2d}  "
              f"tokens={v['tokens']:5d}  "
              f"new_llm={v['new_llm']:2d}  "
              f"reused={v['reused_llm']:2d}")
    # SELECTIVE vs FULL
    sf = full["calls"] - selective["calls"]
    tf = full["tokens"] - selective["tokens"]
    nf = full["new_llm"] - selective["new_llm"]
    rf_full = full["reused_llm"]
    rs_full = selective["reused_llm"]
    print(f"  SELECTIVE_vs_FULL:")
    print(f"    calls reduction   : {sf} ({sf/full['calls']*100:.1f}%)")
    print(f"    tokens reduction  : {tf} ({tf/full['tokens']*100:.1f}%)")
    print(f"    new_llm reduction: {nf} ({nf/full['new_llm']*100:.1f}%)")
    # SELECTIVE vs COARSE
    sc = coarse["calls"] - selective["calls"]
    tc = coarse["tokens"] - selective["tokens"]
    print(f"  SELECTIVE_vs_COARSE:")
    print(f"    calls reduction   : {sc} ({sc/coarse['calls']*100:.1f}%)")
    print(f"    tokens reduction  : {tc} ({tc/coarse['tokens']*100:.1f}%)")
