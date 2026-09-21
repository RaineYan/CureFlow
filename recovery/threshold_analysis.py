"""Read-only sensitivity analysis for the frozen 30%/70% decision rules."""
from __future__ import annotations

from artifacts import ArtifactRef
from contamination import ContaminationResult
from provenance.graph import ArtifactGraph

from .decision import DecisionEngine


DEFAULT_POINTS = (0, 29, 30, 31, 50, 69, 70, 71, 100)


def synthetic_scope(affected_count: int, total_count: int = 100) -> tuple[ContaminationResult, ArtifactGraph]:
    if total_count < 0 or affected_count < 0 or affected_count > total_count:
        raise ValueError("require 0 <= affected_count <= total_count")
    graph = ArtifactGraph()
    refs = [ArtifactRef(run_id="threshold", artifact_id=f"node_{index:03d}", version=1) for index in range(total_count)]
    graph._graph.add_nodes_from(refs)
    result = ContaminationResult(
        run_id="threshold", invalidated=refs[:affected_count], contaminated=[],
        unaffected=refs[affected_count:], propagation_edges=[],
    )
    return result, graph


def analyze_thresholds(points=DEFAULT_POINTS, total_count: int = 100) -> dict:
    engine = DecisionEngine()
    rows = []
    for affected in points:
        result, graph = synthetic_scope(affected, total_count)
        decision = engine.decide(result, graph)
        rows.append({
            "affectedCount": affected, "totalCount": total_count,
            "affectedRatio": round(decision.affected_ratio, 6),
            "selectedStrategy": decision.strategy_name, "reason": decision.reason,
        })
    return {
        "schemaVersion": "1.0", "selectiveThreshold": engine.selective_threshold,
        "fullThreshold": engine.full_threshold, "rows": rows,
        "claim": "Sensitivity analysis only; it does not establish threshold optimality.",
    }


def render_threshold_report(data: dict) -> str:
    lines = ["# CureFlow 决策阈值敏感性分析", "",
             "> 保持现有 30%/70% 规则不变；本报告不声称阈值最优。", "",
             "| affected | ratio | strategy |", "|---:|---:|---|"]
    for row in data["rows"]:
        lines.append(f"| {row['affectedCount']}/{row['totalCount']} | {row['affectedRatio']:.2%} | {row['selectedStrategy']} |")
    lines.extend(["", "## 观察", "",
                  "- 恰好 30% 进入 COARSE_CHECKPOINT。",
                  "- 恰好 70% 进入 FULL_RERUN。",
                  "- 小图中增加一个节点可能跨越边界，因此比例决策具有离散跳变。", ""])
    return "\n".join(lines)


__all__ = ["DEFAULT_POINTS", "analyze_thresholds", "render_threshold_report", "synthetic_scope"]
