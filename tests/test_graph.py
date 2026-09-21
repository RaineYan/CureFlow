"""
Tests for ArtifactGraph — POC Day 4.

Coverage (22 tests, matches the Day 4 spec):
   1.  ArtifactGraph builds from store
   2.  nodes use exact ArtifactRef
   3.  INPUT edges from input_artifact_ids
   4.  EVIDENCE edges from evidence_ids
   5.  get_upstream correct
   6.  get_downstream correct
   7.  get_ancestors correct
   8.  get_descendants correct
   9.  has_path correct
  10.  v1 and v2 both exist simultaneously
  11.  v1 -> metric_v1 path exists
  12.  v2 -> metric_v2 path exists
  13.  no false v1 -> metric_v2 path
  14.  Financial / User / Operation branch isolation
  15.  RawData -> Field -> Metric path
  16.  dangling ref rejected
  17.  cross-run ref rejected
  18.  rebuild after reload -> same graph
  19.  store.get() returns deep copy (mutation isolated)
  20.  store.get_version() returns deep copy
  21.  latency_ms is float
  22.  Day 1-3 tests still pass (covered by full pytest run)
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest

from artifacts import (
    Artifact,
    ArtifactRef,
    ArtifactStore,
    ArtifactType,
)
from provenance.decorator import RunContext, track_provenance
from provenance.graph import ArtifactGraph, EdgeType
from workflow.tasks import (
    compute_conversion_rate,
    compute_cost_ratio,
    compute_order_growth,
    compute_profit_margin,
    compute_revenue_growth,
    compute_user_growth,
    extract_fields,
)


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture
def store(tmp_path: Path) -> ArtifactStore:
    base = tmp_path / "artifacts"
    s = ArtifactStore(base_dir=str(base))
    yield s
    shutil.rmtree(base, ignore_errors=True)


@pytest.fixture
def csv_path() -> str:
    return str(Path(__file__).resolve().parent.parent / "data" / "sample_company.csv")


@pytest.fixture
def full_run(store: ArtifactStore, csv_path: str) -> str:
    """Build the 1 raw + 10 fields + 6 metrics DAG in run_001."""
    run_id = "run_001"
    extract_fields(csv_path, run_id, store)
    # Day 2 hand-written versions — they have the exact same refs decorator would build.
    compute_revenue_growth(run_id, store)
    compute_profit_margin(run_id, store)
    compute_cost_ratio(run_id, store)
    compute_user_growth(run_id, store)
    compute_conversion_rate(run_id, store)
    compute_order_growth(run_id, store)
    return run_id


# =====================================================================
# 1-2: build from store + nodes are exact ArtifactRef
# =====================================================================

def test_graph_builds_from_store(full_run: str, store: ArtifactStore) -> None:
    g = ArtifactGraph.from_store(store, full_run)
    # 1 raw + 10 fields + 6 metrics = 17 nodes
    assert g.node_count() == 17


def test_nodes_use_exact_artifact_ref(full_run: str, store: ArtifactStore) -> None:
    g = ArtifactGraph.from_store(store, full_run)
    raw = ArtifactRef(run_id=full_run, artifact_id="raw_data", version=1)
    rev25 = ArtifactRef(run_id=full_run, artifact_id="field_revenue_2025", version=1)
    metric = ArtifactRef(run_id=full_run, artifact_id="metric_revenue_growth", version=1)

    assert g.has_node(raw)
    assert g.has_node(rev25)
    assert g.has_node(metric)


# =====================================================================
# 3-4: INPUT and EVIDENCE edge types
# =====================================================================

def test_input_edge_from_metric_to_fields(full_run: str, store: ArtifactStore) -> None:
    g = ArtifactGraph.from_store(store, full_run)
    src = ArtifactRef(run_id=full_run, artifact_id="field_revenue_2025", version=1)
    dst = ArtifactRef(run_id=full_run, artifact_id="metric_revenue_growth", version=1)
    et = g.edge_type(src, dst)
    assert et == EdgeType.INPUT


def test_evidence_edge_from_metric_to_claim(full_run: str, store: ArtifactStore) -> None:
    """Hand-build a Claim that cites a Metric, then verify the EVIDENCE edge."""
    metric = ArtifactRef(run_id=full_run, artifact_id="metric_revenue_growth", version=1)
    claim = Artifact(
        artifact_id="claim_financial",
        artifact_type=ArtifactType.CLAIM,
        producer_task="test_claim",
        run_id=full_run,
        version=1,
        content="revenue grew",
        evidence_ids=[metric],
    )
    store.save(claim)

    g = ArtifactGraph.from_store(store, full_run)
    et = g.edge_type(metric, ArtifactRef(run_id=full_run, artifact_id="claim_financial", version=1))
    assert et == EdgeType.EVIDENCE


# =====================================================================
# 5-9: traversal queries
# =====================================================================

def test_get_upstream_for_metric(full_run: str, store: ArtifactStore) -> None:
    g = ArtifactGraph.from_store(store, full_run)
    m = ArtifactRef(run_id=full_run, artifact_id="metric_revenue_growth", version=1)
    up = g.get_upstream(m)
    assert up == {
        ArtifactRef(run_id=full_run, artifact_id="field_revenue_2025", version=1),
        ArtifactRef(run_id=full_run, artifact_id="field_revenue_2024", version=1),
    }


def test_get_downstream_for_field(full_run: str, store: ArtifactStore) -> None:
    g = ArtifactGraph.from_store(store, full_run)
    f = ArtifactRef(run_id=full_run, artifact_id="field_revenue_2025", version=1)
    down = g.get_downstream(f)
    # consumed by revenue_growth, profit_margin, cost_ratio
    assert down == {
        ArtifactRef(run_id=full_run, artifact_id="metric_revenue_growth", version=1),
        ArtifactRef(run_id=full_run, artifact_id="metric_profit_margin", version=1),
        ArtifactRef(run_id=full_run, artifact_id="metric_cost_ratio", version=1),
    }


def test_get_ancestors_metric_reaches_raw_data(full_run: str, store: ArtifactStore) -> None:
    g = ArtifactGraph.from_store(store, full_run)
    m = ArtifactRef(run_id=full_run, artifact_id="metric_revenue_growth", version=1)
    anc = g.get_ancestors(m)
    # must include field_revenue_2025, field_revenue_2024, raw_data
    assert ArtifactRef(run_id=full_run, artifact_id="field_revenue_2025", version=1) in anc
    assert ArtifactRef(run_id=full_run, artifact_id="field_revenue_2024", version=1) in anc
    assert ArtifactRef(run_id=full_run, artifact_id="raw_data", version=1) in anc


def test_get_descendants_field_revenue_2025_includes_three_metrics(
    full_run: str, store: ArtifactStore
) -> None:
    g = ArtifactGraph.from_store(store, full_run)
    f = ArtifactRef(run_id=full_run, artifact_id="field_revenue_2025", version=1)
    desc = g.get_descendants(f)
    assert ArtifactRef(run_id=full_run, artifact_id="metric_revenue_growth", version=1) in desc
    assert ArtifactRef(run_id=full_run, artifact_id="metric_profit_margin", version=1) in desc
    assert ArtifactRef(run_id=full_run, artifact_id="metric_cost_ratio", version=1) in desc
    # user / operation branch must NOT appear
    assert ArtifactRef(run_id=full_run, artifact_id="metric_user_growth", version=1) not in desc
    assert ArtifactRef(run_id=full_run, artifact_id="metric_conversion_rate", version=1) not in desc
    assert ArtifactRef(run_id=full_run, artifact_id="metric_order_growth", version=1) not in desc


def test_has_path_raw_to_metric(full_run: str, store: ArtifactStore) -> None:
    g = ArtifactGraph.from_store(store, full_run)
    raw = ArtifactRef(run_id=full_run, artifact_id="raw_data", version=1)
    m = ArtifactRef(run_id=full_run, artifact_id="metric_revenue_growth", version=1)
    assert g.has_path(raw, m) is True


# =====================================================================
# 10-13: v1 / v2 dual paths
# =====================================================================

def test_v1_and_v2_coexist_in_graph(full_run: str, store: ArtifactStore) -> None:
    """Tamper field_revenue_2025 -> v2; recompute metric_revenue_growth -> v2.
    Both v1 and v2 of each artifact must appear as distinct nodes."""
    # tamper
    f25 = store.get(full_run, "field_revenue_2025")
    f24 = store.get(full_run, "field_revenue_2024")
    assert f25 is not None and f24 is not None
    store.save(f25.model_copy(update={"value": 0.0, "version": 2}))
    # recompute metric with new field
    ctx = RunContext(run_id=full_run, store=store)

    @track_provenance(
        output_artifact_id="metric_revenue_growth",
        artifact_type=ArtifactType.METRIC,
    )
    def mrg(a, b, *, ctx):
        return (a.value - b.value) / b.value

    mrg(store.get(full_run, "field_revenue_2025"), f24, ctx=ctx)

    g = ArtifactGraph.from_store(store, full_run)
    assert g.has_node(ArtifactRef(run_id=full_run, artifact_id="field_revenue_2025", version=1))
    assert g.has_node(ArtifactRef(run_id=full_run, artifact_id="field_revenue_2025", version=2))
    assert g.has_node(ArtifactRef(run_id=full_run, artifact_id="metric_revenue_growth", version=1))
    assert g.has_node(ArtifactRef(run_id=full_run, artifact_id="metric_revenue_growth", version=2))


def test_v1_field_to_v1_metric_path_exists(full_run: str, store: ArtifactStore) -> None:
    g = ArtifactGraph.from_store(store, full_run)
    v1 = ArtifactRef(run_id=full_run, artifact_id="field_revenue_2025", version=1)
    m1 = ArtifactRef(run_id=full_run, artifact_id="metric_revenue_growth", version=1)
    assert g.has_path(v1, m1) is True


def test_v2_field_to_v2_metric_path_exists_after_recovery(
    full_run: str, store: ArtifactStore
) -> None:
    f25 = store.get(full_run, "field_revenue_2025")
    f24 = store.get(full_run, "field_revenue_2024")
    assert f25 is not None and f24 is not None
    store.save(f25.model_copy(update={"value": 0.0, "version": 2}))
    ctx = RunContext(run_id=full_run, store=store)

    @track_provenance(
        output_artifact_id="metric_revenue_growth",
        artifact_type=ArtifactType.METRIC,
    )
    def mrg(a, b, *, ctx):
        return (a.value - b.value) / b.value

    mrg(store.get(full_run, "field_revenue_2025"), f24, ctx=ctx)

    g = ArtifactGraph.from_store(store, full_run)
    v2_field = ArtifactRef(run_id=full_run, artifact_id="field_revenue_2025", version=2)
    v2_metric = ArtifactRef(run_id=full_run, artifact_id="metric_revenue_growth", version=2)
    assert g.has_path(v2_field, v2_metric) is True


def test_no_false_v1_field_to_v2_metric_path(full_run: str, store: ArtifactStore) -> None:
    """v1 field must NOT be reachable to v2 metric (different content version)."""
    f25 = store.get(full_run, "field_revenue_2025")
    f24 = store.get(full_run, "field_revenue_2024")
    assert f25 is not None and f24 is not None
    store.save(f25.model_copy(update={"value": 0.0, "version": 2}))
    ctx = RunContext(run_id=full_run, store=store)

    @track_provenance(
        output_artifact_id="metric_revenue_growth",
        artifact_type=ArtifactType.METRIC,
    )
    def mrg(a, b, *, ctx):
        return (a.value - b.value) / b.value

    mrg(store.get(full_run, "field_revenue_2025"), f24, ctx=ctx)

    g = ArtifactGraph.from_store(store, full_run)
    v1_field = ArtifactRef(run_id=full_run, artifact_id="field_revenue_2025", version=1)
    v2_metric = ArtifactRef(run_id=full_run, artifact_id="metric_revenue_growth", version=2)
    # v1 field has no edge to v2 metric
    assert g.has_path(v1_field, v2_metric) is False


# =====================================================================
# 14: Financial / User / Operation branch isolation
# =====================================================================

def test_branch_isolation_revenue_does_not_reach_user_or_op(
    full_run: str, store: ArtifactStore
) -> None:
    g = ArtifactGraph.from_store(store, full_run)
    rev25 = ArtifactRef(run_id=full_run, artifact_id="field_revenue_2025", version=1)
    desc = g.get_descendants(rev25)
    user_metrics = {
        ArtifactRef(run_id=full_run, artifact_id="metric_user_growth", version=1),
        ArtifactRef(run_id=full_run, artifact_id="metric_conversion_rate", version=1),
        ArtifactRef(run_id=full_run, artifact_id="metric_order_growth", version=1),
    }
    assert user_metrics.isdisjoint(desc), (
        f"Revenue field should not reach user/op metrics, but got {user_metrics & desc}"
    )


def test_branch_isolation_user_does_not_reach_revenue_or_op(
    full_run: str, store: ArtifactStore
) -> None:
    g = ArtifactGraph.from_store(store, full_run)
    u25 = ArtifactRef(run_id=full_run, artifact_id="field_users_2025", version=1)
    desc = g.get_descendants(u25)
    other = {
        ArtifactRef(run_id=full_run, artifact_id="metric_revenue_growth", version=1),
        ArtifactRef(run_id=full_run, artifact_id="metric_profit_margin", version=1),
        ArtifactRef(run_id=full_run, artifact_id="metric_cost_ratio", version=1),
        ArtifactRef(run_id=full_run, artifact_id="metric_conversion_rate", version=1),
        ArtifactRef(run_id=full_run, artifact_id="metric_order_growth", version=1),
    }
    assert other.isdisjoint(desc)


# =====================================================================
# 15: RawData -> Field -> Metric path
# =====================================================================

def test_raw_data_is_ancestor_of_all_fields_and_metrics(
    full_run: str, store: ArtifactStore
) -> None:
    g = ArtifactGraph.from_store(store, full_run)
    raw = ArtifactRef(run_id=full_run, artifact_id="raw_data", version=1)
    desc = g.get_descendants(raw)
    # all 10 fields and 6 metrics
    expected_ids = (
        {f"field_{c}_{y}" for c in ("revenue", "cost", "users", "orders", "visits") for y in (2024, 2025)}
        | {"metric_revenue_growth", "metric_profit_margin", "metric_cost_ratio",
           "metric_user_growth", "metric_conversion_rate", "metric_order_growth"}
    )
    actual_ids = {ref.artifact_id for ref in desc}
    assert expected_ids.issubset(actual_ids)


# =====================================================================
# 16: dangling ref rejected
# =====================================================================

def test_dangling_ref_raises(store: ArtifactStore, csv_path: str) -> None:
    run_id = "run_dangling"
    extract_fields(csv_path, run_id, store)
    # Save an artifact whose input_ref points at a non-existent version
    bogus = Artifact(
        artifact_id="metric_bogus",
        artifact_type=ArtifactType.METRIC,
        producer_task="test",
        run_id=run_id,
        version=1,
        value=0.0,
        input_artifact_ids=[
            ArtifactRef(run_id=run_id, artifact_id="field_revenue_2025", version=99),  # not on disk
        ],
    )
    store.save(bogus)
    with pytest.raises(ValueError, match="Dangling ArtifactRef"):
        ArtifactGraph.from_store(store, run_id)


# =====================================================================
# 17: cross-run ref rejected
# =====================================================================

def test_cross_run_ref_raises(store: ArtifactStore, csv_path: str) -> None:
    run_a = "run_a"
    extract_fields(csv_path, run_a, store)
    cross = Artifact(
        artifact_id="metric_cross",
        artifact_type=ArtifactType.METRIC,
        producer_task="test",
        run_id=run_a,
        version=1,
        value=0.0,
        input_artifact_ids=[
            ArtifactRef(run_id="run_OTHER", artifact_id="field_x", version=1),
        ],
    )
    store.save(cross)
    with pytest.raises(ValueError, match="Cross-run ArtifactRef"):
        ArtifactGraph.from_store(store, run_a)


# =====================================================================
# 18: rebuild after reload is identical
# =====================================================================

def test_rebuild_after_reload_is_identical(tmp_path: Path, full_run: str) -> None:
    base = tmp_path / "artifacts"
    s1 = ArtifactStore(base_dir=str(base))
    # re-run the full fixture by hand to populate s1
    csv = str(Path(__file__).resolve().parent.parent / "data" / "sample_company.csv")
    extract_fields(csv, full_run, s1)
    compute_revenue_growth(full_run, s1)
    compute_profit_margin(full_run, s1)
    compute_cost_ratio(full_run, s1)
    compute_user_growth(full_run, s1)
    compute_conversion_rate(full_run, s1)
    compute_order_growth(full_run, s1)
    g1 = ArtifactGraph.from_store(s1, full_run)

    # fresh store, reload
    s2 = ArtifactStore(base_dir=str(base))
    s2.reload()
    g2 = ArtifactGraph.from_store(s2, full_run)

    assert g1.nodes() == g2.nodes()
    assert g1.edge_count() == g2.edge_count()


# =====================================================================
# 19-20: store returns deep copy (mutation isolation)
# =====================================================================

def test_store_get_returns_deep_copy(store: ArtifactStore, csv_path: str) -> None:
    run_id = "run_iso"
    extract_fields(csv_path, run_id, store)
    a = store.get(run_id, "field_revenue_2025")
    assert a is not None
    original_value = a.value

    # Mutate the returned artifact
    a.value = 999_999
    a.input_artifact_ids.append(
        ArtifactRef(run_id=run_id, artifact_id="raw_data", version=2)
    )
    a.execution_cost.tokens = 7777

    # Re-fetch from the store: original value, original refs, original cost
    again = store.get(run_id, "field_revenue_2025")
    assert again is not None
    assert again.value == original_value
    assert all(r.version == 1 for r in again.input_artifact_ids)
    assert again.execution_cost.tokens == 0


def test_store_get_version_returns_deep_copy(store: ArtifactStore, csv_path: str) -> None:
    run_id = "run_iso"
    extract_fields(csv_path, run_id, store)
    a = store.get_version(run_id, "field_revenue_2025", 1)
    assert a is not None

    # Mutate
    a.value = -1
    a.execution_cost.monetary_usd = 99.0

    # Re-fetch: untouched
    again = store.get_version(run_id, "field_revenue_2025", 1)
    assert again is not None
    assert again.value != -1
    assert again.execution_cost.monetary_usd == 0.0


# =====================================================================
# 21: latency_ms is float
# =====================================================================

def test_latency_ms_is_float(store: ArtifactStore, csv_path: str) -> None:
    run_id = "run_lat"
    extract_fields(csv_path, run_id, store)
    f25 = store.get(run_id, "field_revenue_2025")
    f24 = store.get(run_id, "field_revenue_2024")
    assert f25 is not None and f24 is not None
    ctx = RunContext(run_id=run_id, store=store)

    @track_provenance(
        output_artifact_id="metric_latency_test",
        artifact_type=ArtifactType.METRIC,
    )
    def slow(a, b, *, ctx):
        time.sleep(0.001)   # 1 ms — sub-int precision matters here
        return a.value - b.value

    m = slow(f25, f24, ctx=ctx)
    assert isinstance(m.execution_cost.latency_ms, float)
    # Not requiring > 0 strictly; under 0.001s wall time is possible on fast machines,
    # but with sleep(0.001) we should be safely above.
    assert m.execution_cost.latency_ms >= 0.0
