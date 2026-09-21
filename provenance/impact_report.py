"""Human-readable Markdown reporting for a unified impact analysis."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from artifacts import ArtifactRef
from contamination import ContaminationResult

from .graph import ArtifactGraph


def _ref(ref: ArtifactRef) -> str:
    return f"{ref.artifact_id}@v{ref.version}"


def propagation_paths(result: ContaminationResult) -> list[list[ArtifactRef]]:
    """Expand recorded propagation edges into deterministic root-to-leaf paths."""
    outgoing: dict[ArtifactRef, list[ArtifactRef]] = defaultdict(list)
    targets: set[ArtifactRef] = set()
    for edge in result.propagation_edges:
        outgoing[edge.source].append(edge.target)
        targets.add(edge.target)
    for values in outgoing.values():
        values.sort(key=lambda item: (item.artifact_id, item.version))

    roots = sorted(result.invalidated, key=lambda item: (item.artifact_id, item.version))
    paths: list[list[ArtifactRef]] = []

    def walk(node: ArtifactRef, path: list[ArtifactRef]) -> None:
        children = [child for child in outgoing.get(node, []) if child not in path]
        if not children:
            paths.append(path)
            return
        for child in children:
            walk(child, path + [child])

    for root in roots:
        walk(root, [root])
    return paths


def build_impact_report(
    graph: ArtifactGraph,
    result: ContaminationResult,
    *,
    title: str = "CureFlow 可解释影响报告",
) -> str:
    """Render nodes, edges, propagation paths, and reuse advice as Markdown."""
    invalidated = set(result.invalidated)
    contaminated = set(result.contaminated)
    unaffected = set(result.unaffected)
    nodes = sorted(graph.nodes(), key=lambda item: (item.artifact_id, item.version))
    edges = sorted(graph.edges(), key=lambda item: (_ref(item[0]), _ref(item[1]), item[2].value))
    affected_count = len(invalidated | contaminated)
    ratio = affected_count / len(nodes) if nodes else 0.0

    lines = [
        f"# {title}", "",
        f"- 运行编号：`{result.run_id}`",
        f"- 节点总数：{len(nodes)}",
        f"- 受影响节点：{affected_count}（{ratio:.2%}）",
        f"- 可复用节点：{len(unaffected)}", "",
        "## 节点列表", "", "| 节点 | 状态 |", "|---|---|",
    ]
    for node in nodes:
        status = "INVALIDATED" if node in invalidated else "CONTAMINATED" if node in contaminated else "UNAFFECTED"
        lines.append(f"| `{_ref(node)}` | {status} |")

    lines.extend(["", "## 边列表", "", "| 上游 | 下游 | 类型 |", "|---|---|---|"])
    if edges:
        lines.extend(f"| `{_ref(src)}` | `{_ref(dst)}` | {edge_type.value} |" for src, dst, edge_type in edges)
    else:
        lines.append("| — | — | — |")

    lines.extend(["", "## 传播路径", ""])
    paths = propagation_paths(result)
    if paths:
        lines.extend(f"- {' → '.join(f'`{_ref(node)}`' for node in path)}" for path in paths)
    else:
        lines.append("- 无传播路径")

    lines.extend(["", "## 路径图", "", "```mermaid", "flowchart LR"])
    if result.propagation_edges:
        path_nodes = sorted(
            {ref for edge in result.propagation_edges for ref in (edge.source, edge.target)},
            key=lambda item: (item.artifact_id, item.version),
        )
        node_ids = {ref: f"N{index}" for index, ref in enumerate(path_nodes)}
        for ref in path_nodes:
            lines.append(f'  {node_ids[ref]}["{_ref(ref)}"]')
        for edge in result.propagation_edges:
            lines.append(f"  {node_ids[edge.source]} -->|{edge.via.value}| {node_ids[edge.target]}")
    else:
        lines.append('  NONE["无传播"]')
    lines.extend(["```", "", "## 复用建议", ""])
    if unaffected:
        lines.append("以下节点未进入污染传播链，可在校验其内容版本后直接复用：")
        lines.extend(f"- `{_ref(node)}`" for node in sorted(unaffected, key=lambda item: (item.artifact_id, item.version)))
    else:
        lines.append("当前没有可直接复用的节点，建议扩大恢复范围。")
    lines.append("")
    return "\n".join(lines)


def build_impact_view(graph: ArtifactGraph, result: ContaminationResult) -> dict[str, Any]:
    """Return a deterministic JSON-ready impact model for UI consumers."""
    invalidated = set(result.invalidated)
    contaminated = set(result.contaminated)
    propagated = {(e.source, e.target, e.via.value) for e in result.propagation_edges}
    nodes = []
    for ref in sorted(graph.nodes(), key=lambda item: (item.artifact_id, item.version)):
        artifact = graph.artifact(ref)
        status = "INVALIDATED" if ref in invalidated else "CONTAMINATED" if ref in contaminated else "UNAFFECTED"
        nodes.append({
            "ref": ref.to_ref_string(), "artifactId": ref.artifact_id,
            "version": ref.version,
            "artifactType": (
                getattr(artifact.artifact_type, "value", artifact.artifact_type)
                if artifact is not None else None
            ),
            "producerTask": artifact.producer_task if artifact is not None else None,
            "status": status, "directlyInvalidated": ref in invalidated,
        })
    edges = [{
        "source": source.to_ref_string(), "target": target.to_ref_string(),
        "type": edge_type.value,
        "carriedContamination": (source, target, edge_type.value) in propagated,
    } for source, target, edge_type in sorted(
        graph.edges(), key=lambda item: (_ref(item[0]), _ref(item[1]), item[2].value),
    )]
    affected = len(invalidated | contaminated)
    total = len(nodes)
    return {
        "schemaVersion": "1.0", "runId": result.run_id,
        "summary": {
            "totalNodes": total, "invalidatedNodes": len(invalidated),
            "contaminatedNodes": len(contaminated), "affectedNodes": affected,
            "unaffectedNodes": total - affected,
            "affectedRatio": round(affected / total, 6) if total else 0.0,
        },
        "nodes": nodes, "edges": edges,
        "propagationPaths": [[ref.to_ref_string() for ref in path] for path in propagation_paths(result)],
    }


def build_trace_diff(full: dict[str, Any], selective: dict[str, Any]) -> dict[str, Any]:
    """Compare two structured execution rows without parsing display text."""
    def tokens(row: dict[str, Any]) -> int:
        return int(row.get("fixture_input_tokens", 0)) + int(row.get("fixture_output_tokens", 0))
    def reduction(field: str) -> float:
        base, value = float(full.get(field, 0)), float(selective.get(field, 0))
        return round((base - value) / base, 6) if base else 0.0
    full_tokens, selective_tokens = tokens(full), tokens(selective)
    def side(row: dict[str, Any], default: str) -> dict[str, Any]:
        return {
            "strategy": row.get("chosen_strategy", default),
            "planSteps": int(row.get("plan_steps", 0)),
            "taskCalls": int(row.get("actual_task_calls", 0)),
            "llmCalls": int(row.get("actual_llm_calls", 0)),
            "tokens": tokens(row),
            "recomputedArtifacts": int(row.get("recomputed_artifacts", 0)),
            "reusedArtifacts": int(row.get("reused_artifacts", 0)),
            "residualContamination": int(row.get("residual_contamination", 0)),
            "outputEquivalent": bool(row.get("output_equivalent", False)),
        }
    return {
        "schemaVersion": "1.0", "usageType": selective.get("usage_type", "UNAVAILABLE"),
        "full": side(full, "FULL_RERUN"), "selective": side(selective, "SELECTIVE_RECOVERY"),
        "reductions": {
            "planSteps": reduction("plan_steps"), "taskCalls": reduction("actual_task_calls"),
            "llmCalls": reduction("actual_llm_calls"),
            "tokens": round((full_tokens - selective_tokens) / full_tokens, 6) if full_tokens else 0.0,
        },
        "sameRecoveredOutput": bool(full.get("recovered_output_fingerprint"))
        and full.get("recovered_output_fingerprint") == selective.get("recovered_output_fingerprint"),
    }


def render_trace_diff_markdown(diff: dict[str, Any]) -> str:
    """Render a compact human-readable trace comparison."""
    full, selective = diff["full"], diff["selective"]
    lines = ["# FULL 与 SELECTIVE 执行差异", "", f"> usage type: `{diff['usageType']}`；token 指标不得跨 usage type 比较。", "",
             "| 指标 | FULL | SELECTIVE | 降低 |", "|---|---:|---:|---:|"]
    for label, key in (("计划步骤", "planSteps"), ("实际 task calls", "taskCalls"), ("实际 LLM calls", "llmCalls"), ("fixture tokens", "tokens")):
        lines.append(f"| {label} | {full[key]} | {selective[key]} | {diff['reductions'][key]:.2%} |")
    lines.extend(["", f"- SELECTIVE 复用节点：{selective['reusedArtifacts']}",
                  f"- SELECTIVE 残余污染：{selective['residualContamination']}",
                  f"- 两策略恢复输出一致：{str(diff['sameRecoveredOutput']).lower()}", ""])
    return "\n".join(lines)


__all__ = ["build_impact_report", "build_impact_view", "build_trace_diff", "propagation_paths", "render_trace_diff_markdown"]
