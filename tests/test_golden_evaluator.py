"""Run all 15 golden fixtures through the deterministic evaluator."""
from pathlib import Path

import pytest

from _golden_evaluator import evaluate_all, evaluate_fixture, render_report


GOLDEN_DIR = Path(__file__).with_name("golden_cases")
FIXTURES = sorted(GOLDEN_DIR.glob("*.json"))


@pytest.mark.parametrize("fixture_path", FIXTURES, ids=lambda path: path.stem)
def test_golden_fixture(fixture_path: Path) -> None:
    score = evaluate_fixture(fixture_path)
    assert score.strategy_match
    assert score.recall == 1.0
    assert score.reduction >= score.minimum_reduction
    assert score.passed


def test_golden_matrix_is_five_cases_by_three_strategies() -> None:
    scores = evaluate_all(GOLDEN_DIR)
    assert len(scores) == 15
    assert {score.case_id for score in scores} == {"CASE_A", "CASE_B", "CASE_C", "CASE_D", "CASE_E"}
    assert {score.expected_strategy for score in scores} == {
        "SELECTIVE_RECOVERY", "COARSE_CHECKPOINT", "FULL_RERUN",
    }


def test_report_contains_required_aggregate_metrics() -> None:
    report = render_report(evaluate_all(GOLDEN_DIR))
    assert "Precision（策略匹配率）：100.00%" in report
    assert "Recall（受影响节点识别率均值）：100.00%" in report
    assert "Reduction mean" in report


def test_case_e_is_combined_without_changing_canonical_cases() -> None:
    case_e = [path for path in FIXTURES if path.name.startswith("case_e_")]
    assert len(case_e) == 3
    for path in case_e:
        payload = __import__("json").loads(path.read_text(encoding="utf-8"))
        assert payload["contamination_type"] == "combined"
        assert len(payload["input"]["invalidated_nodes"]) == 2
