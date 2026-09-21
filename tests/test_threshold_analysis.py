import pytest

from recovery.threshold_analysis import analyze_thresholds, render_threshold_report, synthetic_scope


@pytest.mark.parametrize("affected,expected", [
    (0, "SELECTIVE_RECOVERY"), (29, "SELECTIVE_RECOVERY"),
    (30, "COARSE_CHECKPOINT"), (31, "COARSE_CHECKPOINT"),
    (50, "COARSE_CHECKPOINT"), (69, "COARSE_CHECKPOINT"),
    (70, "FULL_RERUN"), (71, "FULL_RERUN"), (100, "FULL_RERUN"),
])
def test_frozen_boundary_points(affected, expected) -> None:
    row = analyze_thresholds([affected])["rows"][0]
    assert row["selectedStrategy"] == expected


def test_small_graph_has_discrete_boundary_jump() -> None:
    rows = analyze_thresholds([7, 8], total_count=24)["rows"]
    assert rows[0]["affectedRatio"] == 0.291667
    assert rows[0]["selectedStrategy"] == "SELECTIVE_RECOVERY"
    assert rows[1]["selectedStrategy"] == "COARSE_CHECKPOINT"


@pytest.mark.parametrize("affected,total", [(-1, 10), (11, 10), (1, -1)])
def test_invalid_scope_is_rejected(affected, total) -> None:
    with pytest.raises(ValueError):
        synthetic_scope(affected, total)


def test_report_disclaims_optimality() -> None:
    report = render_threshold_report(analyze_thresholds())
    assert "不声称阈值最优" in report
    assert "恰好 30%" in report and "恰好 70%" in report
