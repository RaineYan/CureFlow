"""
Tests for @track_provenance — POC Day 3.

Coverage (14 tests, matches the Day 3 spec):
    1.  decorator auto-produces a MetricArtifact
    2.  decorator auto-captures two input ArtifactRefs
    3.  refs contain exact run_id
    4.  refs contain exact version
    5.  first output is auto-versioned v1
    6.  second recompute auto-produces v2 (never overwrites v1)
    7.  after upstream v2, the new metric refs v2
    8.  the old v1 metric's refs do NOT drift to v2
    9.  tool: tokens = 0
    10. tool: llm_calls = 0
    11. latency_ms is recorded and >= 0
    12. cross-run inputs are rejected
    13. exception during compute -> no half-baked artifact saved
    14. (covered: Day 1/2 tests in other files stay green)

Also demonstrates: a decorated `compute_revenue_growth` that produces the
same result as the Day 2 hand-written version in workflow.tasks.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from artifacts import (
    Artifact,
    ArtifactRef,
    ArtifactStatus,
    ArtifactStore,
    ArtifactType,
)
from provenance.decorator import RunContext, track_provenance, tracked
from workflow.tasks import (
    compute_revenue_growth as compute_revenue_growth_manual,
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
def fields(store: ArtifactStore):
    csv = str(Path(__file__).resolve().parent.parent / "data" / "sample_company.csv")
    return extract_fields(csv, "run_001", store)


# =====================================================================
# A decorated compute_revenue_growth (Day 3 style)
# =====================================================================

@track_provenance(
    output_artifact_id="metric_revenue_growth_decorated",
    artifact_type=ArtifactType.METRIC,
)
def compute_revenue_growth_decorated(
    revenue_2025: Artifact,
    revenue_2024: Artifact,
    *,
    ctx: RunContext,
) -> float:
    """Identical math to workflow.tasks.compute_revenue_growth, but provenance
    is captured automatically by the decorator."""
    return (revenue_2025.value - revenue_2024.value) / revenue_2024.value


# =====================================================================
# 1-2: decorator auto-produces MetricArtifact + captures two input refs
# =====================================================================

def test_decorator_produces_metric_artifact(fields, store: ArtifactStore) -> None:
    rev25 = store.get("run_001", "field_revenue_2025")
    rev24 = store.get("run_001", "field_revenue_2024")
    assert rev25 is not None and rev24 is not None
    ctx = RunContext(run_id="run_001", store=store)

    m = compute_revenue_growth_decorated(rev25, rev24, ctx=ctx)

    assert isinstance(m, Artifact)
    assert m.artifact_type == "Metric"
    assert m.artifact_id == "metric_revenue_growth_decorated"
    assert m.run_id == "run_001"
    assert m.producer_task == "compute_revenue_growth_decorated"


def test_decorator_captures_two_input_refs(fields, store: ArtifactStore) -> None:
    rev25 = store.get("run_001", "field_revenue_2025")
    rev24 = store.get("run_001", "field_revenue_2024")
    ctx = RunContext(run_id="run_001", store=store)
    m = compute_revenue_growth_decorated(rev25, rev24, ctx=ctx)

    assert len(m.input_artifact_ids) == 2
    assert all(isinstance(r, ArtifactRef) for r in m.input_artifact_ids)


# =====================================================================
# 3-4: refs contain exact run_id + exact version
# =====================================================================

def test_refs_contain_exact_run_id(fields, store: ArtifactStore) -> None:
    rev25 = store.get("run_001", "field_revenue_2025")
    rev24 = store.get("run_001", "field_revenue_2024")
    ctx = RunContext(run_id="run_001", store=store)
    m = compute_revenue_growth_decorated(rev25, rev24, ctx=ctx)

    for r in m.input_artifact_ids:
        assert r.run_id == "run_001"


def test_refs_contain_exact_version(fields, store: ArtifactStore) -> None:
    rev25 = store.get("run_001", "field_revenue_2025")
    rev24 = store.get("run_001", "field_revenue_2024")
    assert rev25 is not None and rev24 is not None
    ctx = RunContext(run_id="run_001", store=store)
    m = compute_revenue_growth_decorated(rev25, rev24, ctx=ctx)

    # Both inputs are v1, so both refs are v1
    assert {r.artifact_id: r.version for r in m.input_artifact_ids} == {
        "field_revenue_2025": 1,
        "field_revenue_2024": 1,
    }


# =====================================================================
# 5-6: first output is v1, second is v2 (never overwrite)
# =====================================================================

def test_first_output_auto_v1(fields, store: ArtifactStore) -> None:
    rev25 = store.get("run_001", "field_revenue_2025")
    rev24 = store.get("run_001", "field_revenue_2024")
    ctx = RunContext(run_id="run_001", store=store)
    m = compute_revenue_growth_decorated(rev25, rev24, ctx=ctx)
    assert m.version == 1


def test_second_recompute_auto_v2(fields, store: ArtifactStore) -> None:
    rev25 = store.get("run_001", "field_revenue_2025")
    rev24 = store.get("run_001", "field_revenue_2024")
    ctx = RunContext(run_id="run_001", store=store)

    m1 = compute_revenue_growth_decorated(rev25, rev24, ctx=ctx)
    m2 = compute_revenue_growth_decorated(rev25, rev24, ctx=ctx)

    assert m1.version == 1
    assert m2.version == 2
    # Both still on disk
    assert store.get_version("run_001", m1.artifact_id, 1) is not None
    assert store.get_version("run_001", m1.artifact_id, 2) is not None


# =====================================================================
# 7-8: upstream v2 -> new metric refs v2; old v1 refs do NOT drift
# =====================================================================

def test_upstream_v2_makes_new_metric_ref_v2(fields, store: ArtifactStore) -> None:
    """Case 2 of the Day 3 spec:
       - field_revenue_2025 v1 exists, v2 is then created
       - call decorated compute again
       - new metric@v2 must ref field_revenue_2025@v2
    """
    rev25_v1 = store.get("run_001", "field_revenue_2025")
    rev24 = store.get("run_001", "field_revenue_2024")
    ctx = RunContext(run_id="run_001", store=store)

    # first compute: refs v1
    m1 = compute_revenue_growth_decorated(rev25_v1, rev24, ctx=ctx)
    assert {r.artifact_id: r.version for r in m1.input_artifact_ids} == {
        "field_revenue_2025": 1,
        "field_revenue_2024": 1,
    }

    # upstream gets recovered to v2 (different value)
    rev25_v2 = rev25_v1.model_copy(update={"value": 0.0, "version": 2})  # tamper
    store.save(rev25_v2)

    # caller re-fetches the (new) latest from the store and re-runs
    rev25_latest = store.get("run_001", "field_revenue_2025")
    assert rev25_latest is not None
    assert rev25_latest.version == 2

    m2 = compute_revenue_growth_decorated(rev25_latest, rev24, ctx=ctx)
    assert m2.version == 2

    # Critical: the new metric's refs reflect v2
    assert {r.artifact_id: r.version for r in m2.input_artifact_ids} == {
        "field_revenue_2025": 2,
        "field_revenue_2024": 1,
    }


def test_old_metric_v1_refs_do_not_drift(fields, store: ArtifactStore) -> None:
    """Case 3 of the Day 3 spec:
       - after upstream v2 exists, m1 (which pointed at v1) must STILL point at v1
    """
    rev25_v1 = store.get("run_001", "field_revenue_2025")
    rev24 = store.get("run_001", "field_revenue_2024")
    ctx = RunContext(run_id="run_001", store=store)

    m1 = compute_revenue_growth_decorated(rev25_v1, rev24, ctx=ctx)
    # tamper upstream
    rev25_v2 = rev25_v1.model_copy(update={"value": 0.0, "version": 2})
    store.save(rev25_v2)
    rev25_latest = store.get("run_001", "field_revenue_2025")
    m2 = compute_revenue_growth_decorated(rev25_latest, rev24, ctx=ctx)

    # m1 still points at v1, m2 points at v2 — both queryable
    m1_after = store.get_version("run_001", m1.artifact_id, 1)
    m2_after = store.get_version("run_001", m2.artifact_id, 2)
    assert m1_after is not None and m2_after is not None

    refs1 = {r.artifact_id: r.version for r in m1_after.input_artifact_ids}
    refs2 = {r.artifact_id: r.version for r in m2_after.input_artifact_ids}
    assert refs1 == {"field_revenue_2025": 1, "field_revenue_2024": 1}
    assert refs2 == {"field_revenue_2025": 2, "field_revenue_2024": 1}


# =====================================================================
# 9-10-11: cost fields (tokens=0, llm_calls=0, latency_ms>=0)
# =====================================================================

def test_tool_cost_tokens_zero(fields, store: ArtifactStore) -> None:
    rev25 = store.get("run_001", "field_revenue_2025")
    rev24 = store.get("run_001", "field_revenue_2024")
    ctx = RunContext(run_id="run_001", store=store)
    m = compute_revenue_growth_decorated(rev25, rev24, ctx=ctx)
    assert m.execution_cost.tokens == 0


def test_tool_cost_llm_calls_zero(fields, store: ArtifactStore) -> None:
    rev25 = store.get("run_001", "field_revenue_2025")
    rev24 = store.get("run_001", "field_revenue_2024")
    ctx = RunContext(run_id="run_001", store=store)
    m = compute_revenue_growth_decorated(rev25, rev24, ctx=ctx)
    assert m.execution_cost.llm_calls == 0


def test_tool_cost_latency_recorded_and_nonneg(fields, store: ArtifactStore) -> None:
    rev25 = store.get("run_001", "field_revenue_2025")
    rev24 = store.get("run_001", "field_revenue_2024")
    ctx = RunContext(run_id="run_001", store=store)
    m = compute_revenue_growth_decorated(rev25, rev24, ctx=ctx)
    # Day 4 fix: latency_ms is float, not int
    assert isinstance(m.execution_cost.latency_ms, float)
    assert m.execution_cost.latency_ms >= 0.0


# =====================================================================
# 12: cross-run inputs are rejected
# =====================================================================

def test_cross_run_inputs_rejected(fields, store: ArtifactStore) -> None:
    """Inject an Artifact from a different run; decorator must refuse."""
    # fields fixture is for run_001
    rev25 = store.get("run_001", "field_revenue_2025")
    rev24 = store.get("run_001", "field_revenue_2024")
    assert rev25 is not None and rev24 is not None

    # Synthesize a same-id Artifact but with run_002
    foreign = rev25.model_copy(update={"run_id": "run_002", "version": 1})
    ctx = RunContext(run_id="run_001", store=store)

    with pytest.raises(ValueError, match="Cross-run input not allowed"):
        compute_revenue_growth_decorated(foreign, rev24, ctx=ctx)


# =====================================================================
# 13: exception during compute -> NO half-baked artifact saved
# =====================================================================

@track_provenance(
    output_artifact_id="metric_broken",
    artifact_type=ArtifactType.METRIC,
)
def broken_compute(value: Artifact, *, ctx: RunContext) -> float:
    raise RuntimeError("intentional failure for Day 3 test")


def test_exception_during_compute_does_not_save(store: ArtifactStore) -> None:
    a = Artifact(
        artifact_id="input_x",
        artifact_type=ArtifactType.FIELD,
        producer_task="seed",
        run_id="run_001",
        version=1,
        value=42,
    )
    store.save(a)
    ctx = RunContext(run_id="run_001", store=store)

    with pytest.raises(RuntimeError, match="intentional failure"):
        broken_compute(a, ctx=ctx)

    # No artifact under the output id should exist
    assert store.get("run_001", "metric_broken") is None
    assert store.list_versions("run_001", "metric_broken") == []


# =====================================================================
# Bonus: parity with the Day 2 hand-written compute_revenue_growth
# =====================================================================

def test_decorated_value_matches_manual_version(fields, store: ArtifactStore) -> None:
    rev25 = store.get("run_001", "field_revenue_2025")
    rev24 = store.get("run_001", "field_revenue_2024")
    ctx = RunContext(run_id="run_001", store=store)

    m_manual = compute_revenue_growth_manual("run_001", store)
    m_decorated = compute_revenue_growth_decorated(rev25, rev24, ctx=ctx)

    assert m_manual.value == pytest.approx(m_decorated.value)


# =====================================================================
# Bonus: tracked() helper
# =====================================================================

def test_tracked_returns_true_for_decorated() -> None:
    assert tracked(compute_revenue_growth_decorated) is True


def test_tracked_returns_false_for_plain_function() -> None:
    def plain(x): return x
    assert tracked(plain) is False
