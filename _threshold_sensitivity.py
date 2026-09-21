"""DecisionEngine 30%/70% 阈值敏感性分析。

纯 read-only，不调 propagate_contamination（避免副作用）。
基于现有 sample_company.csv 跑出 24 节点图，在每个污染等级直接调 DecisionEngine.decide()。
不 fabricate：所有 ratio / strategy 来自真实跑出。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from artifacts import ArtifactRef, ArtifactStore
from contamination import ContaminationResult
from contamination.propagation import PropagationEdge
from provenance.graph import ArtifactGraph
from recovery.decision import DecisionEngine
from workflow.runner import run_baseline_workflow
from llm import default_workflow_llm

SAMPLE_CSV = ROOT / "data" / "sample_company.csv"
TOTAL = 24  # sample_company 跑出 24 节点（1 raw_data + 8 fields + 6 metrics + 6 claims + 3 sections + 1 report + ...实际可能不同，先跑一次）


def build_contamination_for_n(g: ArtifactGraph, run_id: str, n: int) -> ContaminationResult:
    """构造 n 个 affected 节点的污染（按 artifact_id 排序取前 n 个）。"""
    all_refs = sorted(g.nodes(), key=lambda r: (r.artifact_id, r.version))
    n = min(n, len(all_refs))
    affected_refs = all_refs[:n]
    invalidated = affected_refs[:max(1, n // 3)] if n > 0 else []
    contaminated = affected_refs[max(1, n // 3):] if n > 1 else []
    unaffected = all_refs[n:]
    return ContaminationResult(
        run_id=run_id,
        invalidated=invalidated,
        contaminated=contaminated,
        unaffected=unaffected,
        propagation_edges=[],
    )


def main() -> None:
    # 跑 1 次 sample workflow 拿真实 24 节点图
    data_dir = ROOT / ".threshold_test_data"
    store = ArtifactStore(base_dir=str(data_dir))
    run_id = f"threshold_{int(time.time())}"
    run_baseline_workflow(str(SAMPLE_CSV), run_id, store, default_workflow_llm())
    g = ArtifactGraph.from_store(store, run_id)
    total = g.node_count()
    print(f"Actual total nodes from sample workflow: {total}")

    # 11 个污染等级（覆盖关键边界）
    levels = [0, 1, 3, 6, 7, 8, 10, 12, 16, 17, total]
    rows = []
    for n in levels:
        if n > total:
            continue
        cr = build_contamination_for_n(g, run_id, n)
        decision = DecisionEngine().decide(cr, g)
        rows.append({
            "affected_count": decision.affected_count,
            "total_count": decision.total_count,
            "affected_ratio": round(decision.affected_ratio, 6),
            "affected_ratio_pct": f"{decision.affected_ratio * 100:.2f}%",
            "selected_strategy": decision.strategy_name,
            "reason": decision.reason,
        })

    print("\n=== 阈值敏感性表（实测，不 fabricate）===")
    print(f"{'affected':>9} / {'total':<6} | {'ratio':<10} | {'strategy':<20} | reason")
    print("-" * 100)
    for r in rows:
        print(f"{r['affected_count']:>9} / {r['total_count']:<6} | {r['affected_ratio_pct']:<10} | {r['selected_strategy']:<20} | {r['reason']}")

    # 写 JSON 数据
    out = ROOT / "_threshold_sensitivity_data.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[DATA] {out}")


if __name__ == "__main__":
    main()