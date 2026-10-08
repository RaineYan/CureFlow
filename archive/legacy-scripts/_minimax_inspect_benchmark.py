"""MiniMax 抽查脚本：验 benchmark_data.json 的 event stream 不是静态填写。"""
import json
from pathlib import Path

ROOT = Path(r"C:\Users\Lenovo\.minimax-agent-cn\projects\poc")
data = json.loads((ROOT / "experiments/results/v2_execution_benchmark/benchmark_data.json").read_text(encoding="utf-8"))

print(f"Top-level keys: {list(data.keys())[:10]}")
# 找 scenarios 容器
def find_scenarios(d, depth=0):
    if isinstance(d, dict):
        for k, v in d.items():
            if k == "scenarios" and isinstance(v, dict):
                return v
            r = find_scenarios(v, depth + 1)
            if r is not None:
                return r
    return None

scenarios = find_scenarios(data)
if scenarios is None:
    print("Cannot find scenarios dict; trying cases list...")
    cases_list = data.get("cases", [])
    print(f"cases is list of {len(cases_list)}")
    if cases_list:
        c0 = cases_list[0]
        print(f"first case keys: {list(c0.keys())}")
        print(f"first case: {json.dumps(c0, ensure_ascii=False, indent=2)[:2000]}")
    raise SystemExit(0)

print(f"\nScenarios: {list(scenarios.keys())}")
for sid, sbody in scenarios.items():
    print(f"\n=== {sid} ===")
    cases = sbody.get("cases", sbody)
    if not isinstance(cases, dict):
        print(f"  cases type: {type(cases).__name__}")
        continue
    for strategy, c in cases.items():
        if not isinstance(c, dict):
            continue
        plan = c.get("plan", {})
        execution = c.get("execution", {})
        events = execution.get("events", [])
        llm = execution.get("llm_calls")
        print(f"  {strategy}: plan_steps={len(plan.get('steps', []))}, "
              f"events={len(events) if isinstance(events, list) else 'n/a'}, "
              f"llm_calls={llm}")
        if isinstance(events, list) and events:
            print(f"    first event keys: {list(events[0].keys()) if isinstance(events[0], dict) else type(events[0]).__name__}")
            print(f"    first event sample: {events[0]}")