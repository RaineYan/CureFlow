"""Verify Stage 1.5 final state after retry."""
import os
import json
import hashlib
from pathlib import Path

print("REAL_LLM_API_KEY present?", "REAL_LLM_API_KEY" in os.environ)
print("REAL_LLM_BASE_URL present?", "REAL_LLM_BASE_URL" in os.environ)
print()

from experiments.day14 import verify_frozen_evidence_unchanged
r = verify_frozen_evidence_unchanged()
print("Day 13 frozen evidence:", r["passed"])
print("  files checked:", len(r["checked_files"]))
print()

pilot = Path("experiments/results/day14/pilot")
critical = {
    "case_a_full_rerun_trial01R2.json":
        "c88785ab234c30067a8cd26ddf00416b7a08ec61d39788eaa112944dc5628818",
    "case_a_full_rerun_trial01R2_live_ledger.jsonl":
        "2cf0d95f3efd5210cf9153a1baef7b3bdf42baaf77297686676d064ce4775184",
}
print("Critical R2 SHA:")
for f, exp in critical.items():
    p = pilot / f
    cur = hashlib.sha256(p.read_bytes()).hexdigest()
    status = "OK" if cur == exp else "CHANGED"
    print(f"  {f:50s}  {status}")
print()

stage15 = Path("experiments/results/exp02/stage15_real")
trials = sorted(stage15.glob("stage15_*_01.json"))
print(f"Stage 1.5 trial count: {len(trials)}")
prov_ok = 0
trial_valid = 0
strategy_calls = {"FULL_RERUN": [], "COARSE_CHECKPOINT": [], "SELECTIVE_RECOVERY": []}
strategy_tokens = {"FULL_RERUN": [], "COARSE_CHECKPOINT": [], "SELECTIVE_RECOVERY": []}
for t in trials:
    d = json.loads(t.read_text(encoding="utf-8"))
    s = d["strategy"]
    if d.get("provider_success"):
        prov_ok += 1
        strategy_calls[s].append(d.get("llm_calls", 0))
        strategy_tokens[s].append(d.get("tokens_total", 0))
    if d.get("trial_valid"):
        trial_valid += 1
    retry = d.get("retry_attempt", 0)
    print(
        f"  {t.name:50s}  strategy={s:18s}  "
        f"prov={d.get('provider_success')!s:5s}  "
        f"valid={d.get('trial_valid')!s:5s}  "
        f"calls={d.get('llm_calls'):2d}  "
        f"tokens={d.get('tokens_total'):5d}  "
        f"retry={retry}"
    )
print()
print(f"Provider success: {prov_ok}/12")
print(f"Trial valid: {trial_valid}/12")
print()
for s in ["FULL_RERUN", "COARSE_CHECKPOINT", "SELECTIVE_RECOVERY"]:
    if strategy_calls[s]:
        avg_c = sum(strategy_calls[s]) / len(strategy_calls[s])
        avg_t = sum(strategy_tokens[s]) / len(strategy_tokens[s])
    else:
        avg_c = avg_t = 0
    print(
        f"  {s:20s}  n_ok={len(strategy_calls[s])}  "
        f"avg_calls={avg_c:.1f}  avg_tokens={avg_t:.0f}"
    )
