import os
import hashlib
import json
from pathlib import Path
import sys

print("REAL_LLM_API_KEY present?", "REAL_LLM_API_KEY" in os.environ)
print("REAL_LLM_BASE_URL present?", "REAL_LLM_BASE_URL" in os.environ)
print()

from experiments.day14 import verify_frozen_evidence_unchanged
r = verify_frozen_evidence_unchanged()
print("Day 13 frozen evidence:", r["passed"])
print("  files checked:", len(r["checked_files"]))
print("  mismatches:", r["mismatches"])
print()

ROOT = Path(__file__).resolve().parent
pilot = ROOT / "experiments/results/day14/pilot"
critical = {
    "case_a_full_rerun_trial01R2.json":
        "c88785ab234c30067a8cd26ddf00416b7a08ec61d39788eaa112944dc5628818",
    "case_a_full_rerun_trial01R2_live_ledger.jsonl":
        "2cf0d95f3efd5210cf9153a1baef7b3bdf42baaf77297686676d064ce4775184",
}
print("Critical R2 SHA:")
for f, expected in critical.items():
    p = pilot / f
    cur = hashlib.sha256(p.read_bytes()).hexdigest()
    ok = "OK" if cur == expected else "CHANGED"
    print(f"  {f:50s}  {ok}")
print()

# Count stage15_real files
stage15 = ROOT / "experiments/results/exp02/stage15_real"
trial_jsons = sorted(stage15.glob("*_01.json"))
print(f"stage15_real trial JSONs: {len(trial_jsons)}")
status_count = {"OK": 0, "CRASHED": 0}
for t in trial_jsons:
    d = json.loads(t.read_text(encoding="utf-8"))
    p = d.get("provider_success")
    s = d.get("success")
    t_v = d.get("trial_valid")
    print(
        f"  {t.name:50s}  success={s!s:5s}  "
        f"provider={p!s:5s}  trial_valid={t_v!s:5s}  "
        f"calls={d.get('llm_calls')}"
    )
