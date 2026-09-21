"""Evaluate deterministic golden cases and write a reproducible Markdown report."""
from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from pathlib import Path

from artifacts import ArtifactRef
from contamination import ContaminationResult
from provenance.graph import ArtifactGraph
from recovery.decision import DecisionEngine


ROOT = Path(__file__).resolve().parent
GOLDEN_DIR = ROOT / "tests" / "golden_cases"
REPORT_PATH = ROOT / "_golden_evaluator_report.md"


@dataclass(frozen=True)
class GoldenScore:
    fixture: str
    case_id: str
    expected_strategy: str
    actual_strategy: str
    strategy_match: bool
    recall: float
    reduction: float
    minimum_reduction: float

    @property
    def passed(self) -> bool:
        return self.strategy_match and self.recall == 1.0 and self.reduction >= self.minimum_reduction


def load_fixture(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def evaluate_fixture(path: Path) -> GoldenScore:
    fixture = load_fixture(path)
    run_id = fixture["case_id"].lower()
    total = fixture["input"]["total_nodes"]
    affected_names = fixture["input"]["invalidated_nodes"] + fixture["input"]["contaminated_nodes"]
    all_names = list(dict.fromkeys(affected_names))
    all_names.extend(f"unaffected_{index}" for index in range(total - len(all_names)))
    refs = {name: ArtifactRef(run_id=run_id, artifact_id=name, version=1) for name in all_names}

    graph = ArtifactGraph()
    graph._graph.add_nodes_from(refs.values())
    invalidated = [refs[name] for name in fixture["input"]["invalidated_nodes"]]
    contaminated = [refs[name] for name in fixture["input"]["contaminated_nodes"]]
    affected = set(invalidated + contaminated)
    result = ContaminationResult(
        run_id=run_id,
        invalidated=invalidated,
        contaminated=contaminated,
        unaffected=sorted(graph.nodes() - affected, key=lambda ref: ref.artifact_id),
        propagation_edges=[],
    )
    decision = DecisionEngine().decide(result, graph)
    expected_names = set(fixture["expected"]["affected_nodes"])
    actual_names = {ref.artifact_id for ref in affected}
    recall = len(expected_names & actual_names) / len(expected_names) if expected_names else 1.0
    reduction = 0.0 if decision.strategy_name == "FULL_RERUN" else 1.0 - decision.affected_ratio
    return GoldenScore(
        fixture=path.name,
        case_id=fixture["case_id"],
        expected_strategy=fixture["expected"]["strategy"],
        actual_strategy=decision.strategy_name,
        strategy_match=decision.strategy_name == fixture["expected"]["strategy"],
        recall=recall,
        reduction=reduction,
        minimum_reduction=fixture["expected"]["minimum_reduction"],
    )


def evaluate_all(directory: Path = GOLDEN_DIR) -> list[GoldenScore]:
    return [evaluate_fixture(path) for path in sorted(directory.glob("*.json"))]


def render_report(scores: list[GoldenScore]) -> str:
    precision = statistics.mean(score.strategy_match for score in scores) if scores else 0.0
    recall = statistics.mean(score.recall for score in scores) if scores else 0.0
    reduction = statistics.mean(score.reduction for score in scores) if scores else 0.0
    lines = [
        "# CureFlow 金标准自动评分报告", "",
        "> 本报告来自确定性 golden fixtures；reduction 表示图节点重跑范围缩减，不替代 M1/M3 的真实 Provider 实验结果。", "",
        f"- Fixtures：{len(scores)}",
        f"- Precision（策略匹配率）：{precision:.2%}",
        f"- Recall（受影响节点识别率均值）：{recall:.2%}",
        f"- Reduction mean（图节点重跑缩减均值）：{reduction:.2%}",
        f"- Passed：{sum(score.passed for score in scores)}/{len(scores)}", "",
        "| Fixture | Case | 期望策略 | 实际策略 | Recall | Reduction | PASS |",
        "|---|---|---|---|---:|---:|---|",
    ]
    for score in scores:
        lines.append(
            f"| {score.fixture} | {score.case_id} | {score.expected_strategy} | "
            f"{score.actual_strategy} | {score.recall:.2%} | {score.reduction:.2%} | "
            f"{'PASS' if score.passed else 'FAIL'} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    scores = evaluate_all()
    report = render_report(scores)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"golden fixtures: {len(scores)}; passed: {sum(score.passed for score in scores)}")
    print(report.splitlines()[5])
    print(report.splitlines()[6])
    print(report.splitlines()[7])
    if not scores or not all(score.passed for score in scores):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
