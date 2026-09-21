"""
Tests for workflow.tasks — POC Day 2.

Coverage (12 tests):
    - CSV reads with the expected shape
    - RawDataArtifact is created with correct shape
    - Field values come from CSV (not hard-coded)
    - revenue_growth / profit_margin / cost_ratio / user_growth /
      conversion_rate / order_growth each compute correctly
    - Metric input_artifact_ids are exact ArtifactRefs (run+id+version)
    - Different runs don't bleed into each other
    - recompute_field_from_raw() restores ground truth for field_revenue_2025
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pandas as pd
import pytest

from artifacts import ArtifactStore, ArtifactType
from workflow.tasks import (
    YEAR_COLUMNS,
    compute_conversion_rate,
    compute_cost_ratio,
    compute_order_growth,
    compute_profit_margin,
    compute_revenue_growth,
    compute_user_growth,
    extract_fields,
    recompute_field_from_raw,
)


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture
def csv_path() -> str:
    return str(Path(__file__).resolve().parent.parent / "data" / "sample_company.csv")


@pytest.fixture
def store(tmp_path: Path) -> ArtifactStore:
    base = tmp_path / "artifacts"
    s = ArtifactStore(base_dir=str(base))
    yield s
    shutil.rmtree(base, ignore_errors=True)


@pytest.fixture
def fields(store: ArtifactStore, csv_path: str):
    """Run extract_fields once and share the result across tests."""
    return extract_fields(csv_path, run_id="run_001", store=store)


# =====================================================================
# CSV + RawDataArtifact
# =====================================================================

def test_csv_reads_with_expected_shape(csv_path: str) -> None:
    df = pd.read_csv(csv_path)
    assert len(df) == 24                                  # 12 months x 2 years
    assert set(df.columns) >= {"year", "month", "revenue", "cost", "users", "orders", "visits"}
    assert set(df["year"].unique()) == {2024, 2025}


def test_raw_data_artifact_created(store: ArtifactStore, csv_path: str) -> None:
    extract_fields(csv_path, "run_001", store)
    raw = store.get("run_001", "raw_data")
    assert raw is not None
    assert raw.artifact_type == "Field" or raw.artifact_type == "RawData"
    # the value MUST be a list of records
    assert isinstance(raw.value, list)
    assert len(raw.value) == 24
    assert "csv_path" in raw.meta
    assert raw.meta["row_count"] == 24
    # assertion: artifact_type is exactly RawData (string after use_enum_values)
    assert raw.artifact_type == "RawData"


# =====================================================================
# Field values come from CSV
# =====================================================================

def test_field_values_come_from_csv(fields, csv_path: str) -> None:
    df = pd.read_csv(csv_path)
    # revenue / cost / orders / visits are flow quantities -> sum
    expected_rev_2024 = float(df.loc[df["year"] == 2024, "revenue"].sum())
    expected_cost_2025 = float(df.loc[df["year"] == 2025, "cost"].sum())
    expected_visits_2025 = float(df.loc[df["year"] == 2025, "visits"].sum())
    # users is MAU -> mean (semantic: monthly_active_users)
    expected_users_2024 = float(df.loc[df["year"] == 2024, "users"].mean())
    expected_users_2025 = float(df.loc[df["year"] == 2025, "users"].mean())

    assert fields["field_revenue_2024"].value == pytest.approx(expected_rev_2024)
    assert fields["field_cost_2025"].value == pytest.approx(expected_cost_2025)
    assert fields["field_visits_2025"].value == pytest.approx(expected_visits_2025)
    assert fields["field_users_2024"].value == pytest.approx(expected_users_2024)
    assert fields["field_users_2025"].value == pytest.approx(expected_users_2025)


def test_users_field_uses_mean_aggregation(fields) -> None:
    """Day 3 spec: users is MAU -> mean. Must be recorded in meta."""
    f = fields["field_users_2024"]
    assert f.meta["aggregation"] == "mean"
    assert f.meta["semantic"] == "monthly_active_users"


def test_non_users_fields_use_sum_aggregation(fields) -> None:
    """revenue/cost/orders/visits are flow quantities -> sum."""
    for fid in ("field_revenue_2024", "field_cost_2025", "field_orders_2025", "field_visits_2025"):
        assert fields[fid].meta["aggregation"] == "sum"


def test_field_artifacts_link_to_raw_data(fields) -> None:
    for fid, a in fields.items():
        assert a.artifact_type == "Field"
        assert a.version == 1
        assert a.producer_task == "extract_fields"
        assert len(a.input_artifact_ids) == 1
        ref = a.input_artifact_ids[0]
        assert ref.run_id == "run_001"
        assert ref.artifact_id == "raw_data"
        assert ref.version == 1


# =====================================================================
# Metric computations (deterministic, exact math)
# =====================================================================
# Annual sums derived from the CSV (also asserted in test_field_values_come_from_csv):
#   2024 revenue = 15,300,000   2025 revenue = 24,600,000
#   2024 cost    =  9,180,000   2025 cost    = 12,900,000
#   2024 users   =     73,200   2025 users   =    109,800
#   2024 orders  =      3,720   2025 orders  =      6,600
#   2024 visits  =     43,200   2025 visits  =     70,800

def test_revenue_growth_correct(fields, store: ArtifactStore) -> None:
    m = compute_revenue_growth("run_001", store)
    # (24.6M - 15.3M) / 15.3M = 0.607843...
    assert m.value == pytest.approx((24_600_000 - 15_300_000) / 15_300_000)


def test_profit_margin_correct(fields, store: ArtifactStore) -> None:
    m = compute_profit_margin("run_001", store)
    # (24.6M - 12.9M) / 24.6M = 0.475609...
    assert m.value == pytest.approx((24_600_000 - 12_900_000) / 24_600_000)


def test_cost_ratio_correct(fields, store: ArtifactStore) -> None:
    m = compute_cost_ratio("run_001", store)
    # 12.9M / 24.6M = 0.524390...
    assert m.value == pytest.approx(12_900_000 / 24_600_000)


def test_user_growth_correct(fields, store: ArtifactStore) -> None:
    m = compute_user_growth("run_001", store)
    # (MAU_2025 - MAU_2024) / MAU_2024 = (9150 - 6100) / 6100 = 0.5
    assert m.value == pytest.approx(0.5)


def test_conversion_rate_correct(fields, store: ArtifactStore) -> None:
    m = compute_conversion_rate("run_001", store)
    # 6600 / 70800 = 0.093220...
    assert m.value == pytest.approx(6_600 / 70_800)


def test_order_growth_correct(fields, store: ArtifactStore) -> None:
    m = compute_order_growth("run_001", store)
    # (6600 - 3720) / 3720 = 0.774193...
    assert m.value == pytest.approx((6_600 - 3_720) / 3_720)


# =====================================================================
# Metric input refs are exact ArtifactRefs (run_id + artifact_id + version)
# =====================================================================

def test_metric_input_refs_are_exact(fields, store: ArtifactStore) -> None:
    m = compute_revenue_growth("run_001", store)
    assert m.artifact_type == "Metric"
    assert m.version == 1
    refs = m.input_artifact_ids
    assert len(refs) == 2
    # exact (run_id, artifact_id, version) pins
    assert {(r.run_id, r.artifact_id, r.version) for r in refs} == {
        ("run_001", "field_revenue_2025", 1),
        ("run_001", "field_revenue_2024", 1),
    }


def test_profit_margin_refs_match_spec(fields, store: ArtifactStore) -> None:
    m = compute_profit_margin("run_001", store)
    assert {(r.artifact_id, r.version) for r in m.input_artifact_ids} == {
        ("field_revenue_2025", 1),
        ("field_cost_2025", 1),
    }


def test_cost_ratio_refs_match_spec(fields, store: ArtifactStore) -> None:
    m = compute_cost_ratio("run_001", store)
    assert {(r.artifact_id, r.version) for r in m.input_artifact_ids} == {
        ("field_cost_2025", 1),
        ("field_revenue_2025", 1),
    }


# =====================================================================
# Cross-run isolation
# =====================================================================

def test_different_runs_do_not_bleed(
    store: ArtifactStore, csv_path: str
) -> None:
    extract_fields(csv_path, "run_full", store)
    extract_fields(csv_path, "run_selective", store)

    m_full = compute_revenue_growth("run_full", store)
    m_sel = compute_revenue_growth("run_selective", store)

    # Same formula, same CSV -> same value, but they live in different runs.
    assert m_full.value == pytest.approx(m_sel.value)

    # Their refs are pinned to their own run.
    assert all(r.run_id == "run_full" for r in m_full.input_artifact_ids)
    assert all(r.run_id == "run_selective" for r in m_sel.input_artifact_ids)

    # list_by_run does not cross-contaminate
    full_ids = {a.artifact_id for a in store.list_by_run("run_full")}
    sel_ids = {a.artifact_id for a in store.list_by_run("run_selective")}
    assert "field_revenue_2025" in full_ids
    assert "field_revenue_2025" in sel_ids
    # 1 raw + 10 fields + 1 metric = 12 per run
    assert len(store.list_by_run("run_full")) == len(YEAR_COLUMNS) + 2
    assert len(store.list_by_run("run_selective")) == len(YEAR_COLUMNS) + 2


# =====================================================================
# recompute_field_from_raw (ground truth for the future detector)
# =====================================================================

def test_recompute_field_from_raw_restores_ground_truth(
    store: ArtifactStore, csv_path: str
) -> None:
    extract_fields(csv_path, "run_001", store)
    raw = store.get("run_001", "raw_data")
    assert raw is not None

    # 1) sanity: matches CSV
    expected = float(pd.read_csv(csv_path).query("year == 2025")["revenue"].sum())
    assert recompute_field_from_raw(raw, "field_revenue_2025") == pytest.approx(expected)

    # 2) tamper field, then recompute, values must differ
    f = store.get("run_001", "field_revenue_2025")
    assert f is not None
    tampered = f.model_copy(update={"value": 999_999_999.0, "version": 2})
    store.save(tampered)

    # The store now has the tampered value
    assert store.get("run_001", "field_revenue_2025").value == pytest.approx(999_999_999.0)

    # recompute_field_from_raw() ignores the tampered Field and reads raw
    ground_truth = recompute_field_from_raw(raw, "field_revenue_2025")
    assert ground_truth == pytest.approx(expected)
    assert ground_truth != pytest.approx(999_999_999.0)


def test_recompute_field_from_raw_rejects_bad_name(store: ArtifactStore) -> None:
    extract_fields(str(Path(__file__).resolve().parent.parent / "data" / "sample_company.csv"),
                   "run_001", store)
    raw = store.get("run_001", "raw_data")
    assert raw is not None
    with pytest.raises(ValueError):
        recompute_field_from_raw(raw, "metric_revenue_growth")  # not a field name
    with pytest.raises(ValueError):
        recompute_field_from_raw(raw, "field_revenue")          # no year suffix
