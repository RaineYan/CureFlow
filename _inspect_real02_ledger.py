"""Inspect real02 ledger"""
import json
from pathlib import Path

pilot = Path("experiments/results/day14/pilot")
ledger_path = pilot / "case_a_full_rerun_trial01R4_live_ledger.jsonl"
ledger = [
    json.loads(l)
    for l in ledger_path.read_text(encoding="utf-8").splitlines()
]
print(f"Ledger records: {len(ledger)}")
print()
for i, r in enumerate(ledger):
    rid_short = (r["request_id"][:36] if r.get("request_id") else "None")
    unit = r.get("execution_unit", "?")
    ut = r.get("usage_type", "?")
    tot = r.get("total_tokens", 0)
    print(f"  [{i}] unit={unit:30s} request_id={rid_short:36s}  usage={ut:18s}  tokens={tot}")
