"""Counterfactual replay for v2 combined contamination experiments.

The replay is graph-structural and deterministic.  A repaired node blocks
contamination at that node; this can make a target clean while unrelated or
upstream contamination remains, which is reported separately.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

from artifacts import ArtifactRef
from contamination import ContaminationResult


@dataclass(frozen=True)
class CounterfactualOutcome:
    repaired_refs: tuple[ArtifactRef, ...]
    residual_affected_refs: tuple[ArtifactRef, ...]
    target_clean: bool
    run_clean: bool


def replay_repairs(
    contamination: ContaminationResult,
    target_ref: ArtifactRef,
    repaired_refs,
) -> CounterfactualOutcome:
    repaired = set(repaired_refs)
    outgoing: dict[ArtifactRef, set[ArtifactRef]] = {}
    for edge in contamination.propagation_edges:
        outgoing.setdefault(edge.source, set()).add(edge.target)
    active = set(contamination.invalidated) - repaired
    queue = sorted(active, key=lambda r: (r.artifact_id, r.version))
    while queue:
        source = queue.pop(0)
        for target in sorted(outgoing.get(source, set()), key=lambda r: (r.artifact_id, r.version)):
            if target in repaired or target in active:
                continue
            active.add(target)
            queue.append(target)
    ordered_repaired = tuple(sorted(repaired, key=lambda r: (r.artifact_id, r.version)))
    residual = tuple(sorted(active, key=lambda r: (r.artifact_id, r.version)))
    return CounterfactualOutcome(
        repaired_refs=ordered_repaired,
        residual_affected_refs=residual,
        target_clean=target_ref not in active,
        run_clean=not residual,
    )


def minimal_repair_families(
    contamination: ContaminationResult,
    target_ref: ArtifactRef,
    candidate_refs=None,
) -> list[tuple[ArtifactRef, ...]]:
    """Enumerate inclusion-minimal repair sets sufficient for this target."""
    candidates = sorted(
        set(candidate_refs if candidate_refs is not None else contamination.invalidated + contamination.contaminated),
        key=lambda r: (r.artifact_id, r.version),
    )
    minimal: list[tuple[ArtifactRef, ...]] = []
    for size in range(len(candidates) + 1):
        for family in combinations(candidates, size):
            family_set = set(family)
            if any(set(existing).issubset(family_set) for existing in minimal):
                continue
            if replay_repairs(contamination, target_ref, family).target_clean:
                minimal.append(family)
    return minimal


def render_counterfactual_report(
    contamination: ContaminationResult,
    target_ref: ArtifactRef,
    families: list[tuple[ArtifactRef, ...]],
) -> str:
    lines = ["# CureFlow v2 组合污染反事实恢复", "",
             f"- 目标：`{target_ref.to_ref_string()}`",
             f"- 污染源：{len(contamination.invalidated)}", f"- 最小充分修复集合：{len(families)}", "",
             "| # | 修复节点 | target clean | run clean | residual |", "|---:|---|---:|---:|---:|"]
    for index, family in enumerate(families, start=1):
        outcome = replay_repairs(contamination, target_ref, family)
        labels = ", ".join(ref.to_ref_string() for ref in family) or "（无需修复）"
        lines.append(f"| {index} | `{labels}` | {str(outcome.target_clean).lower()} | {str(outcome.run_clean).lower()} | {len(outcome.residual_affected_refs)} |")
    lines.extend(["", "> “最小”指对当前 target 的集合包含关系最小，不等于货币成本最低，也不保证整个 run 无残余污染。", ""])
    return "\n".join(lines)


__all__ = ["CounterfactualOutcome", "minimal_repair_families", "render_counterfactual_report", "replay_repairs"]
