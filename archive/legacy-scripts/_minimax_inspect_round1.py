"""MiniMax 抽查 round1 关键数字 + 安全扫描。"""
import json
import re
from pathlib import Path

ROOT = Path(r"C:\Users\Lenovo\.minimax-agent-cn\projects\poc")
FILES = [
    ROOT / "artifacts/openai_ab_pilot/openai_ab_pilot.json",
    ROOT / "artifacts/openai_ab_pilot/openai_ab_pilot.md",
    ROOT / "artifacts/openai_ab_pilot_round2/openai_ab_pilot.json",
    ROOT / "artifacts/openai_ab_pilot_round2/openai_ab_pilot.md",
]

print("=" * 60)
print("[1] round1 关键摘要（确认未被覆盖）")
print("=" * 60)
d = json.loads((ROOT / "artifacts/openai_ab_pilot/openai_ab_pilot.json").read_text(encoding="utf-8"))
s = d["summary"]
o = d["openai"]
print(f"mode: {d['mode']}")
print(f"openai array count: {len(o)}")
print(f"openaiAccepted: {s['openaiAccepted']}")
print(f"openaiProviderReportedTokens: {s['openaiProviderReportedTokens']}")
print(f"openaiEstimatedListPriceUsd: {s['openaiEstimatedListPriceUsd']}")
print(f"financial accepted: {o[0]['accepted']}")
print(f"user accepted: {o[1]['accepted']}")
print(f"user claimText: {o[1]['claimText']}")
print(f"operation accepted: {o[2]['accepted']}")
print(f"operation parseIssues: {o[2]['parseIssues']}")

print()
print("=" * 60)
print("[2] 安全扫描：API Key 痕迹")
print("=" * 60)
PATTERNS = ["sk-", "OPENAI_API_KEY=", "Bearer ", "Authorization:", "sk_live", "sk_test", "sk-proj-", "sk-or-"]
for f in FILES:
    text = f.read_text(encoding="utf-8")
    hits = []
    for p in PATTERNS:
        if p in text:
            hits.append(p)
    status = f"FOUND {hits}" if hits else "CLEAN"
    print(f"  {f.name}: {status}")

print()
print("=" * 60)
print("[3] 两轮 requestId 完整度")
print("=" * 60)
for label, path in [("round1", FILES[0]), ("round2", FILES[2])]:
    d = json.loads(path.read_text(encoding="utf-8"))
    print(f"--- {label} ---")
    for row in d["openai"]:
        rid = row.get("requestId")
        print(f"  {row['scenarioId']:>10}: requestId={'null' if rid is None else rid}")

print()
print("=" * 60)
print("[4] 两轮 token / latency 详细对比")
print("=" * 60)
print(f"{'scenario':>10} {'r1_in':>6} {'r1_out':>6} {'r1_lat':>10} {'r2_in':>6} {'r2_out':>6} {'r2_lat':>10}")
print("-" * 60)
r1 = json.loads((ROOT / "artifacts/openai_ab_pilot/openai_ab_pilot.json").read_text(encoding="utf-8"))
r2 = json.loads((ROOT / "artifacts/openai_ab_pilot_round2/openai_ab_pilot.json").read_text(encoding="utf-8"))
r1_by = {row["scenarioId"]: row for row in r1["openai"]}
r2_by = {row["scenarioId"]: row for row in r2["openai"]}
for sid in ["financial", "user", "operation"]:
    a = r1_by[sid]
    b = r2_by[sid]
    print(f"{sid:>10} {a['inputTokens']:>6} {a['outputTokens']:>6} {a['latencyMs']:>10.2f} {b['inputTokens']:>6} {b['outputTokens']:>6} {b['latencyMs']:>10.2f}")

print()
print("=" * 60)
print("[5] 成功请求平均 latency（仅 200 OK）")
print("=" * 60)
def avg_lat(rows):
    ok = [r for r in rows if r.get("accepted") and r.get("usageType") == "provider_reported"]
    if not ok:
        return None
    return sum(r["latencyMs"] for r in ok) / len(ok)
r1_ok_avg = avg_lat(r1["openai"])
r2_ok_avg = avg_lat(r2["openai"])
print(f"round1 成功请求平均 latency: {r1_ok_avg:.2f} ms" if r1_ok_avg else "round1: no successful requests")
print(f"round2 成功请求平均 latency: {r2_ok_avg:.2f} ms" if r2_ok_avg else "round2: no successful requests")
if r1_ok_avg and r2_ok_avg:
    delta_pct = (r2_ok_avg - r1_ok_avg) / r1_ok_avg * 100
    print(f"delta: {delta_pct:+.2f}%")

print()
print("=" * 60)
print("[6] 业务语义：百分比表达是否一致")
print("=" * 60)
EXPECTED = {
    "financial": [("revenue_growth", "60.78"), ("profit_margin", "47.56")],
    "user": [("user_growth", "50.00")],
    "operation": [("order_growth", "77.42"), ("conversion_rate", "9.32")],
}
for sid, items in EXPECTED.items():
    row = r2_by[sid]
    text = row["claimText"]
    for name, pct in items:
        ok = pct in text
        print(f"  {sid:>10} {name:>15} ≈ {pct}%: {'PRESENT' if ok else 'MISSING'}")