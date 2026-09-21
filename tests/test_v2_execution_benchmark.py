"""Contracts for the isolated, offline v2 execution benchmark."""
from experiments.v2_execution_benchmark import render_report, run_benchmark


def benchmark_data():
    if not hasattr(benchmark_data, "value"):
        benchmark_data.value = run_benchmark()
    return benchmark_data.value


def test_has_five_scenarios_and_twenty_runs() -> None:
    data = benchmark_data()
    assert data["scenario_count"] == 5
    assert data["run_count"] == 20


def test_every_run_labels_usage_as_synthetic() -> None:
    for case in benchmark_data()["cases"]:
        assert all(row["usage_type"] == "SYNTHETIC" for row in case["runs"])


def test_auto_strategy_matches_decision() -> None:
    for case in benchmark_data()["cases"]:
        auto = next(row for row in case["runs"] if row["requested_strategy"] == "AUTO")
        assert auto["chosen_strategy"] == auto["decision"]["strategy"]


def test_local_selective_plan_is_non_empty_and_executes() -> None:
    case = next(c for c in benchmark_data()["cases"] if c["scenario_id"] == "small_operation")
    selective = next(r for r in case["runs"] if r["requested_strategy"] == "SELECTIVE_RECOVERY")
    assert selective["plan_steps"] > 0
    assert selective["actual_task_calls"] > 0


def test_full_scope_does_not_claim_selective_advantage() -> None:
    case = next(c for c in benchmark_data()["cases"] if c["scenario_id"] == "full_scope")
    full = next(r for r in case["runs"] if r["requested_strategy"] == "FULL_RERUN")
    selective = next(r for r in case["runs"] if r["requested_strategy"] == "SELECTIVE_RECOVERY")
    assert selective["plan_steps"] >= full["plan_steps"]
    assert not selective["success"]


def test_at_least_one_equivalent_and_one_non_equivalent_result() -> None:
    rows = [row for case in benchmark_data()["cases"] for row in case["runs"]]
    assert any(row["output_equivalent"] for row in rows)
    assert any(not row["output_equivalent"] for row in rows)


def test_reductions_recompute_from_full_task_calls() -> None:
    for case in benchmark_data()["cases"]:
        full = next(r for r in case["runs"] if r["requested_strategy"] == "FULL_RERUN")
        for row in case["runs"]:
            expected = (
                (full["actual_task_calls"] - row["actual_task_calls"])
                / full["actual_task_calls"]
            ) if full["actual_task_calls"] else 0.0
            assert row["task_call_reduction_vs_full"] == round(expected, 6)


def test_report_marks_fixture_tokens_and_metric_boundaries() -> None:
    report = render_report(benchmark_data())
    assert "synthetic/fixture token" in report
    assert "plan step、task call、LLM call" in report


def test_report_contains_every_scenario() -> None:
    report = render_report(benchmark_data())
    for case in benchmark_data()["cases"]:
        assert f"## {case['scenario_id']}" in report
