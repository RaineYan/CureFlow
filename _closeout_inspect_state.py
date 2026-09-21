"""Inspect: current state for the offline closeout.

1. Check live_test_c recovered artifacts @v3 metadata
   (request_id, tokens, model, provider)
2. Check existing gate JSON records
3. Verify what the true real Qwen call data is
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, '.')

from artifacts import ArtifactStore

STORE_DIR = Path(r"C:\Users\Lenovo\AppData\Local\Temp"
                 r"\poc_day10_faultA_live_test_c_pjl8w1f8"
                 r"\artifacts")
store = ArtifactStore(base_dir=str(STORE_DIR))
store.reload()

# Inspect v3 financial artifacts' LLM metadata
print("=== v3 recovered artifacts — LLM metadata ===")
for aid in (
    "claim_financial_growth", "claim_report_financial_section",
    "metric_revenue_growth", "metric_profit_margin",
    "metric_cost_ratio", "field_revenue_2025",
    "report_merged",
):
    a = store.get_version("live_test_c", aid, 3)
    if a is None:
        print(f"  {aid}@v3: ABSENT")
        continue
    print(f"  {aid}@v3:")
    print(f"    status     = {a.validation_status}")
    print(f"    producer   = {a.producer_task}")
    print(f"    meta       = {a.meta}")

# Read existing gate JSON
gate_path = Path("experiments/results/day13_5_live_gate.json")
print(f"\n=== {gate_path} ===")
if gate_path.exists():
    with open(gate_path, encoding="utf-8") as f:
        gate = json.load(f)
    print(f"  real_request_count: {gate.get('real_request_count')}")
    print(f"  live_gate_pass: {gate.get('live_gate_pass')}")
    print(f"  records: {len(gate.get('records', []))}")
    for i, r in enumerate(gate.get("records", [])):
        print(f"    [{i}] phase={r.get('phase')} "
              f"unit={r.get('execution_unit')} "
              f"req={r.get('request_id')} "
              f"in={r.get('input_tokens')} "
              f"out={r.get('output_tokens')}")

# Inspect the FAILURE sample 002 to see what the documented
# real Qwen data is
fail_path = Path("experiments/results/day13_6_real_test_c_failure_002.json")
print(f"\n=== {fail_path} ===")
if fail_path.exists():
    with open(fail_path, encoding="utf-8") as f:
        fail = json.load(f)
    print(f"  real_qwen_calls_used_in_recovery: "
          f"{fail.get('real_qwen_calls_used_in_recovery')}")
    print(f"  actual_real_provider_requests_in_recovery: "
          f"{fail.get('actual_real_provider_requests_in_recovery')}")
    print(f"  successful_real_llm_artifact_materializations: "
          f"{fail.get('successful_real_llm_artifact_materializations')}")

# Read test_a / test_b state for real Qwen request_ids
print("\n=== test_a_state.json (req-a from Test A live call) ===")
ta_path = Path("experiments/results/day13_5_test_a_state.json")
if ta_path.exists():
    with open(ta_path, encoding="utf-8") as f:
        ta = json.load(f)
    lr = ta.get("ledger_record", {})
    print(f"  phase={lr.get('phase')} unit={lr.get('execution_unit')} "
          f"req={lr.get('request_id')} in={lr.get('input_tokens')} "
          f"out={lr.get('output_tokens')}")

print("\n=== test_b_state.json (req-b from Test B live call) ===")
tb_path = Path("experiments/results/day13_5_test_b_state.json")
if tb_path.exists():
    with open(tb_path, encoding="utf-8") as f:
        tb = json.load(f)
    lr = tb.get("ledger_record", {})
    print(f"  phase={lr.get('phase')} unit={lr.get('execution_unit')} "
          f"req={lr.get('request_id')} in={lr.get('input_tokens')} "
          f"out={lr.get('output_tokens')}")
