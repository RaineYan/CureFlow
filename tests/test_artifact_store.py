"""
Tests for Artifact + ArtifactStore — POC Day 1 (revised).

Coverage:
    - basic save / get / get_version
    - duplicate (run_id, artifact_id, version) is rejected
    - get(run, id) returns latest content version by default
    - get(run, id, version=N) returns exact version
    - status update does NOT increment version
    - content change DOES increment version
    - full recovery flow: v1 VALID -> INVALIDATED -> (v2 with new value) -> RECOVERED
    - ArtifactRef round-trip via to_ref_string / from_ref_string
    - cross-run isolation: same artifact_id in two runs never collide
    - list_by_run is run-scoped
    - evidence refs do NOT drift when upstream gets recovered
    - extra fields rejected (Pydantic extra="forbid")
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
    ExecutionCost,
)


# ---------- fixtures ----------

@pytest.fixture
def store(tmp_path: Path) -> ArtifactStore:
    base = tmp_path / "artifacts"
    s = ArtifactStore(base_dir=str(base))
    yield s
    shutil.rmtree(base, ignore_errors=True)


def _mk(
    artifact_id: str,
    run_id: str = "run_001",
    artifact_type: ArtifactType = ArtifactType.FIELD,
    version: int = 1,
    **overrides,
) -> Artifact:
    defaults: dict = {
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "producer_task": "extract_fields",
        "run_id": run_id,
        "version": version,
        "value": 100,
    }
    defaults.update(overrides)
    return Artifact(**defaults)


# =====================================================================
# Section 1: basic save / get round trip
# =====================================================================

def test_save_and_get_basic(store: ArtifactStore) -> None:
    a = _mk("field_revenue_2025", value=1_000_000)
    store.save(a)

    got = store.get("run_001", "field_revenue_2025")
    assert got is not None
    assert got.value == 1_000_000
    # use_enum_values=True: enum fields are stored as plain strings
    assert got.artifact_type == "Field"
    assert got.validation_status == "VALID"
    assert got.version == 1


def test_save_writes_file_to_disk(store: ArtifactStore, tmp_path: Path) -> None:
    a = _mk("field_revenue_2025")
    store.save(a)
    expected = tmp_path / "artifacts" / "run_001" / "field_revenue_2025__v1.json"
    assert expected.exists()


def test_save_duplicate_version_raises(store: ArtifactStore) -> None:
    """(run_id, artifact_id, version) must be unique; save() rejects duplicates."""
    a = _mk("field_x", version=1)
    store.save(a)
    duplicate = _mk("field_x", version=1, value=999)  # same key
    with pytest.raises(ValueError, match="Duplicate"):
        store.save(duplicate)


# =====================================================================
# Section 2: versioned lookup
# =====================================================================

def test_get_returns_latest_version_by_default(store: ArtifactStore) -> None:
    a_v1 = _mk("field_x", version=1, value=100)
    a_v2 = _mk("field_x", version=2, value=200)
    a_v3 = _mk("field_x", version=3, value=300)
    store.save(a_v1)
    store.save(a_v2)
    store.save(a_v3)

    latest = store.get("run_001", "field_x")
    assert latest is not None
    assert latest.version == 3
    assert latest.value == 300


def test_get_specific_version(store: ArtifactStore) -> None:
    a_v1 = _mk("field_x", version=1, value=100)
    a_v2 = _mk("field_x", version=2, value=200)
    store.save(a_v1)
    store.save(a_v2)

    assert store.get("run_001", "field_x", version=1).value == 100
    assert store.get("run_001", "field_x", version=2).value == 200


def test_list_versions(store: ArtifactStore) -> None:
    for v in (1, 3, 2):  # out of order on purpose
        store.save(_mk("field_x", version=v, value=v * 100))
    assert store.list_versions("run_001", "field_x") == [1, 2, 3]


# =====================================================================
# Section 3: status update does NOT bump version
# =====================================================================

def test_update_status_does_not_increment_version(store: ArtifactStore) -> None:
    a = _mk("field_x", version=1, value=1234)
    store.save(a)

    updated = store.update_status("run_001", "field_x", ArtifactStatus.INVALIDATED)
    assert updated.validation_status == "INVALIDATED"
    assert updated.version == 1          # unchanged
    assert updated.value == 1234         # content preserved

    latest = store.get("run_001", "field_x")
    assert latest.validation_status == "INVALIDATED"
    assert latest.version == 1           # still 1

    # only one file on disk
    files = sorted(p.name for p in (store.base_dir / "run_001").iterdir())
    assert files == ["field_x__v1.json"]


def test_content_change_increments_version(store: ArtifactStore) -> None:
    a_v1 = _mk("field_x", version=1, value=100)
    store.save(a_v1)

    # recovery produces a new content version explicitly
    a_v2 = a_v1.model_copy(update={"value": 150, "version": 2})
    store.save(a_v2)

    assert store.get("run_001", "field_x").version == 2
    assert store.get("run_001", "field_x").value == 150
    assert store.get_version("run_001", "field_x", 1).value == 100
    assert store.list_versions("run_001", "field_x") == [1, 2]


def test_update_status_missing_raises(store: ArtifactStore) -> None:
    with pytest.raises(KeyError):
        store.update_status("run_001", "nope", ArtifactStatus.INVALIDATED)


# =====================================================================
# Section 4: full recovery workflow (the contradiction case from review)
# =====================================================================

def test_full_recovery_workflow(store: ArtifactStore) -> None:
    """The exact scenario the review asked us to lock down:

      1. create v1, status VALID
      2. mark INVALIDATED
      3. current state MUST be INVALIDATED
      4. version MUST NOT have incremented from step 2
      5. recovery produces v2 with new value, marked RECOVERED
    """
    # step 1
    a_v1 = _mk("field_revenue_2025", value=1_000_000)
    store.save(a_v1)
    assert store.get("run_001", "field_revenue_2025").validation_status == "VALID"
    assert store.get("run_001", "field_revenue_2025").version == 1

    # step 2
    store.update_status("run_001", "field_revenue_2025", ArtifactStatus.INVALIDATED)

    # step 3
    cur = store.get("run_001", "field_revenue_2025")
    assert cur is not None
    assert cur.validation_status == "INVALIDATED"

    # step 4
    assert cur.version == 1, "INVALIDATED must not bump content version"

    # step 5: recovery generates new content
    a_v2 = a_v1.model_copy(update={"value": 2_000_000, "version": 2})
    store.save(a_v2)
    store.update_status("run_001", "field_revenue_2025", ArtifactStatus.RECOVERED)

    final = store.get("run_001", "field_revenue_2025")
    assert final is not None
    assert final.version == 2
    assert final.value == 2_000_000
    assert final.validation_status == "RECOVERED"

    # v1 is still around for provenance
    v1 = store.get_version("run_001", "field_revenue_2025", 1)
    assert v1 is not None
    assert v1.value == 1_000_000
    assert v1.validation_status == "INVALIDATED"  # latest status of v1


# =====================================================================
# Section 5: ArtifactRef round trip + use in input / evidence
# =====================================================================

def test_artifact_ref_round_trip() -> None:
    ref = ArtifactRef(run_id="run_001", artifact_id="field_x", version=3)
    s = ref.to_ref_string()
    assert s == "run_001/field_x@v3"
    parsed = ArtifactRef.from_ref_string(s)
    assert parsed == ref


def test_artifact_ref_in_inputs_and_evidence(store: ArtifactStore) -> None:
    ref_in = ArtifactRef(run_id="run_001", artifact_id="field_revenue_2025", version=1)
    ref_ev = ArtifactRef(run_id="run_001", artifact_id="metric_revenue_growth", version=1)
    a = _mk(
        "claim_financial_growth",
        artifact_type=ArtifactType.CLAIM,
        content="营收同比增长 12.4%",
        input_artifact_ids=[ref_in],
        evidence_ids=[ref_ev],
        confidence=0.9,
    )
    store.save(a)

    got = store.get("run_001", "claim_financial_growth")
    assert got is not None
    assert len(got.input_artifact_ids) == 1
    assert got.input_artifact_ids[0].run_id == "run_001"
    assert got.input_artifact_ids[0].artifact_id == "field_revenue_2025"
    assert got.input_artifact_ids[0].version == 1
    assert got.evidence_ids[0] == ref_ev
    assert got.confidence == 0.9


# =====================================================================
# Section 6: cross-run isolation (the FULL_RERUN vs SELECTIVE case)
# =====================================================================

def test_cross_run_isolation(store: ArtifactStore) -> None:
    """Same artifact_id in two different runs must never collide."""
    a_full = _mk("field_revenue_2025", run_id="run_full", value=1_000_000)
    a_sel = _mk("field_revenue_2025", run_id="run_selective", value=2_000_000)
    store.save(a_full)
    store.save(a_sel)

    assert store.get("run_full", "field_revenue_2025").value == 1_000_000
    assert store.get("run_selective", "field_revenue_2025").value == 2_000_000


def test_list_by_run_isolates_runs(store: ArtifactStore) -> None:
    store.save(_mk("field_a", run_id="run_full", value=1))
    store.save(_mk("field_b", run_id="run_full", value=2))
    store.save(_mk("field_a", run_id="run_selective", value=3))

    full = store.list_by_run("run_full")
    selective = store.list_by_run("run_selective")

    assert {(a.artifact_id, a.value) for a in full} == {
        ("field_a", 1),
        ("field_b", 2),
    }
    assert {(a.artifact_id, a.value) for a in selective} == {("field_a", 3)}


def test_update_status_only_affects_target_run(store: ArtifactStore) -> None:
    store.save(_mk("field_revenue_2025", run_id="run_full", value=1))
    store.save(_mk("field_revenue_2025", run_id="run_selective", value=2))

    store.update_status("run_full", "field_revenue_2025", ArtifactStatus.INVALIDATED)

    assert (
        store.get("run_full", "field_revenue_2025").validation_status
        == "INVALIDATED"
    )
    assert (
        store.get("run_selective", "field_revenue_2025").validation_status
        == "VALID"
    )


def test_reload_preserves_cross_run_isolation(tmp_path: Path) -> None:
    base = tmp_path / "artifacts"
    s1 = ArtifactStore(base_dir=str(base))
    s1.save(_mk("field_revenue_2025", run_id="run_full", value=1000))
    s1.save(_mk("field_revenue_2025", run_id="run_selective", value=2000))

    s2 = ArtifactStore(base_dir=str(base))
    s2.reload()
    assert s2.get("run_full", "field_revenue_2025").value == 1000
    assert s2.get("run_selective", "field_revenue_2025").value == 2000
    # sanity: two separate files on disk
    files = sorted(p.name for p in base.iterdir())
    assert files == ["run_full", "run_selective"]


# =====================================================================
# Section 7: evidence refs do NOT drift when upstream is recovered
# =====================================================================

def test_evidence_refs_do_not_drift_when_input_recovered(store: ArtifactStore) -> None:
    """The invariant Day 4 graph and Day 7 propagation rely on:

      Claim cites Metric @ v1.
      Metric gets recovered to v2.
      Claim's evidence_ids MUST still point to v1, never silently to v2.
    """
    # create Metric v1
    metric_v1 = _mk(
        "metric_revenue_growth",
        artifact_type=ArtifactType.METRIC,
        version=1,
        value=0.124,
    )
    store.save(metric_v1)

    # create Claim that explicitly cites Metric v1
    claim = _mk(
        "claim_financial_growth",
        artifact_type=ArtifactType.CLAIM,
        version=1,
        content="营收同比增长 12.4%",
        evidence_ids=[
            ArtifactRef(
                run_id="run_001",
                artifact_id="metric_revenue_growth",
                version=1,  # explicit version pin
            )
        ],
    )
    store.save(claim)

    # Metric is recovered: new value, new version
    metric_v2 = metric_v1.model_copy(update={"value": 0.15, "version": 2})
    store.save(metric_v2)

    # Latest Metric is now v2
    latest_metric = store.get("run_001", "metric_revenue_growth")
    assert latest_metric is not None
    assert latest_metric.version == 2
    assert latest_metric.value == 0.15

    # But the Claim's evidence_ids still point at v1 — no drift
    claim_after = store.get("run_001", "claim_financial_growth")
    assert claim_after is not None
    assert len(claim_after.evidence_ids) == 1
    cited = claim_after.evidence_ids[0]
    assert cited.run_id == "run_001"
    assert cited.artifact_id == "metric_revenue_growth"
    assert cited.version == 1, "Evidence refs must not auto-update to v2"

    # v1 of Metric is still on disk and can be reconstructed
    metric_v1_again = store.get_version("run_001", "metric_revenue_growth", 1)
    assert metric_v1_again is not None
    assert metric_v1_again.value == 0.124


# =====================================================================
# Section 8: misc
# =====================================================================

def test_execution_cost_attached(store: ArtifactStore) -> None:
    a = _mk(
        "claim_x",
        artifact_type=ArtifactType.CLAIM,
        execution_cost=ExecutionCost(
            tokens=320, monetary_usd=0.0008, latency_ms=2100.0, llm_calls=1
        ),
    )
    store.save(a)
    got = store.get("run_001", "claim_x")
    assert got is not None
    assert got.execution_cost.tokens == 320
    assert got.execution_cost.monetary_usd == 0.0008
    assert got.execution_cost.llm_calls == 1


def test_extra_field_rejected() -> None:
    with pytest.raises(Exception):
        _mk("field_x", not_a_field="boom")  # type: ignore[call-arg]


def test_artifact_ref_extra_field_rejected() -> None:
    with pytest.raises(Exception):
        ArtifactRef(run_id="r", artifact_id="a", version=1, extra="x")  # type: ignore[call-arg]


def test_artifact_ref_is_frozen() -> None:
    ref = ArtifactRef(run_id="r", artifact_id="a", version=1)
    with pytest.raises(Exception):
        ref.version = 2  # type: ignore[misc]
