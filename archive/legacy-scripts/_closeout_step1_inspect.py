"""Day 13.6 CLOSEOUT — step 1: inspect real data.

Read the existing gate JSON (5 records) and the archived
store.  Print the real Qwen request_ids so we know what
the verified totals are.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, '.')

GATE_PATH = Path("experiments/results/day13_5_live_gate.json")
ARCHIVED = Path("experiments/results/day13_6_live_test_c_store")

with open(GATE_PATH, encoding="utf-8") as f:
    gate = json.load(f)
records = gate.get("records", [])
print(f"=== {GATE_PATH} ===")
print(f"  real_request_count: {gate.get('real_request_count')}")
print(f"  live_gate_pass:     {gate.get('live_gate_pass')}")
print(f"  total records:      {len(records)}")
print()
print("=== records ===")
for i, r in enumerate(records):
    print(
        f"  [{i}] phase={r.get('phase'):<12} "
        f"unit={r.get('execution_unit'):<32} "
        f"req={r.get('request_id')!s:<46} "
        f"in={r.get('input_tokens', 0):<5} "
        f"out={r.get('output_tokens', 0):<5} "
        f"latency={r.get('client_wall_clock_latency_ms', 0):.1f}"
    )

# Compute distinct request_ids by phase
from collections import defaultdict
by_phase = defaultdict(set)
for r in records:
    rid = r.get("request_id")
    if rid:
        by_phase[r.get("phase")].add(rid)
print()
print("=== distinct request_ids per phase ===")
for phase, rids in sorted(by_phase.items()):
    print(f"  {phase}: {len(rids)} -> {sorted(rids)}")

# All distinct request_ids total
all_rids = set()
for rids in by_phase.values():
    all_rids |= rids
print(f"\nTotal distinct real request_ids: {len(all_rids)}")

# Token totals (sum over records with usage_type=provider_reported,
# deduped by request_id)
seen = set()
total_tokens = 0
for r in records:
    rid = r.get("request_id")
    if rid in seen:
        continue
    seen.add(rid)
    if r.get("usage_type") == "provider_reported":
        total_tokens += r.get("total_tokens", 0)
print(f"Verified provider_reported_token_total (deduped): {total_tokens}")

# Verify archived store has v3 recovered artifacts
from artifacts import ArtifactStore
store = ArtifactStore(base_dir=str(ARCHIVED))
store.reload()
print()
print("=== archived store v3 recovered artifacts ===")
for aid in (
    "field_revenue_2025", "metric_revenue_growth",
    "metric_profit_margin", "metric_cost_ratio",
    "claim_financial_growth", "claim_report_financial_section",
    "report_merged",
):
    a = store.get_version("live_test_c", aid, 3)
    if a:
        # Pull LLM metadata for the 2 LLM-produced artifacts
        llm = ""
        if a.meta.get("task_kind") == "llm":
            llm = (
                f" llm_request_id={a.meta.get('llm_request_id')!s:<46} "
                f"in={a.meta.get('llm_input_tokens', 0):<5} "
                f"out={a.meta.get('llm_output_tokens', 0):<5}"
            )
        print(f"  {aid}@v3: status={a.validation_status}{llm}")
    else:
        print(f"  {aid}@v3: MISSING")
