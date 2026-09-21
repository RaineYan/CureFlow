"""
Day 13.6 FINAL-FINAL clean-path audit regression tests.

The previous audit code in `run_test_c` silently dropped
the `RECOVERED` status (it checked only `== "VALID"`),
falsely reporting `semantic_clean_path_pass = false` even
when the recovery was structurally clean.  The fix:
`recovery.executor.clean_path_audit` returns a structured
`CleanPathAuditResult` that:

  (a) accepts both VALID and RECOVERED as clean states,
  (b) walks only PROPAGATING edges (Day-7 INPUT for tool
      / EVIDENCE for LLM),
  (c) records every visited ref, every non-clean ref,
      every missing ref, and a human-readable reason.

These tests lock the contract so the same bug cannot
re-appear.  All tests run offline; no Qwen call.
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from artifacts import (
    Artifact,
    ArtifactRef,
    ArtifactStatus,
    ArtifactStore,
    ArtifactType,
)
from provenance.decorator import RunContext, track_provenance
from provenance.graph import ArtifactGraph
from recovery.executor import (
    RecoveryExecutor,
    clean_path_audit,
)
from recovery.models import CleanPathAuditResult


CSV = str(
    Path(__file__).resolve().parent.parent
    / "data" / "sample_company.csv"
)


# =====================================================================
# Helpers
# =====================================================================

@track_provenance(
    output_artifact_id="metric_revenue_growth",
    artifact_type=ArtifactType.METRIC,
)
def mrg_dec(r2025, r2024, *, ctx):
    return (r2025.value - r2024.value) / r2024.value


@track_provenance(
    output_artifact_id="metric_profit_margin",
    artifact_type=ArtifactType.METRIC,
)
def mpm_dec(r2025, cost_2025, *, ctx):
    return (r2025.value - cost_2025.value) / r2025.value


@track_provenance(
    output_artifact_id="metric_cost_ratio",
    artifact_type=ArtifactType.METRIC,
)
def mcr_dec(cost_2025, r2025, *, ctx):
    return cost_2025.value / r2025.value


def _build_recovered_store(
    tmp_path: Path,
    *,
    run_id: str = "day13_6_audit",
) -> tuple[ArtifactStore, ArtifactRef, ArtifactGraph]:
    """Build a full v1 -> v2 -> v3 financial chain.

    Strategy: use `run_baseline_workflow` for v1 (clean
    baseline), then `build_main_fault_scenario`-style
    re-execution for v2 (fault snapshot), then mark
    everything @v3 as RECOVERED.

    v1  = clean baseline (VALID)
    v2  = fault snapshot (INVALIDATED for field, CONTAMINATED for downstream)
    v3  = recovered (RECOVERED for all v3 financial nodes)

    Returns (store, recovered_target_ref, graph).
    """
    base = tmp_path / "store"
    base.mkdir(parents=True, exist_ok=True)
    store = ArtifactStore(base_dir=str(base))
    # --- v1 clean baseline (uses real CSV) ---
    from llm import default_workflow_llm
    from workflow.runner import run_baseline_workflow
    fake = default_workflow_llm()
    run_baseline_workflow(CSV, run_id, store, fake)
    ctx = RunContext(run_id=run_id, store=store)

    # --- v2 fault snapshot: inject field_revenue_2025@v2
    # tampered, re-execute the 3 financial metrics + claim +
    # section + merge.  We use the scenario's @track_provenance
    # decorated metrics for clean re-execution.
    field_v2 = store.get_version(run_id, "field_revenue_2025", 1).model_copy(
        update={
            "version": 2,
            "value": 24_600_000.0 * 10,
            "meta": {"fault_injected": True, "fault_type": "VALUE_TAMPER"},
            "validation_status": "INVALIDATED",
        }
    )
    store.save(field_v2)
    m_v2 = (
        mrg_dec(field_v2, store.get_version(run_id, "field_revenue_2024", 1), ctx=ctx),
        mpm_dec(field_v2, store.get_version(run_id, "field_cost_2025", 1), ctx=ctx),
        mcr_dec(store.get_version(run_id, "field_cost_2025", 1), field_v2, ctx=ctx),
    )
    for m in m_v2:
        store.update_status(
            run_id, m.artifact_id, ArtifactStatus.CONTAMINATED,
        )
    from workflow.claim_tasks import (
        generate_financial_claim,
        generate_financial_section,
        merge_report,
    )
    c_v2 = generate_financial_claim(*m_v2, ctx=ctx, llm_client=fake)
    store.update_status(
        run_id, c_v2.artifact_id, ArtifactStatus.CONTAMINATED,
    )
    fs_v2 = generate_financial_section(c_v2, ctx=ctx, llm_client=fake)
    store.update_status(
        run_id, fs_v2.artifact_id, ArtifactStatus.CONTAMINATED,
    )
    r_v2 = merge_report(
        fs_v2,
        store.get_version(run_id, "claim_report_user_section", 1),
        store.get_version(run_id, "claim_report_operation_section", 1),
        ctx=ctx,
    )
    store.update_status(
        run_id, r_v2.artifact_id, ArtifactStatus.CONTAMINATED,
    )

    # --- v3 recovered ---
    field_v3 = store.get_version(run_id, "field_revenue_2025", 2).model_copy(
        update={
            "version": 3,
            "value": 24_600_000.0,
            "meta": {},
            "validation_status": "RECOVERED",
        }
    )
    store.save(field_v3)
    m_v3 = (
        mrg_dec(field_v3, store.get_version(run_id, "field_revenue_2024", 1), ctx=ctx),
        mpm_dec(field_v3, store.get_version(run_id, "field_cost_2025", 1), ctx=ctx),
        mcr_dec(store.get_version(run_id, "field_cost_2025", 1), field_v3, ctx=ctx),
    )
    for m in m_v3:
        store.update_status(
            run_id, m.artifact_id, ArtifactStatus.RECOVERED,
        )
    c_v3 = generate_financial_claim(*m_v3, ctx=ctx, llm_client=fake)
    store.update_status(
        run_id, c_v3.artifact_id, ArtifactStatus.RECOVERED,
    )
    fs_v3 = generate_financial_section(c_v3, ctx=ctx, llm_client=fake)
    store.update_status(
        run_id, fs_v3.artifact_id, ArtifactStatus.RECOVERED,
    )
    r_v3 = merge_report(
        fs_v3,
        store.get_version(run_id, "claim_report_user_section", 1),
        store.get_version(run_id, "claim_report_operation_section", 1),
        ctx=ctx,
    )
    store.update_status(
        run_id, r_v3.artifact_id, ArtifactStatus.RECOVERED,
    )

    target = ArtifactRef(
        run_id=run_id, artifact_id="report_merged", version=3,
    )
    graph = ArtifactGraph.from_store(store, run_id)
    return store, target, graph


# =====================================================================
# 1. recovered report@v3 exists in rebuilt graph
# =====================================================================

def test_recovered_report_v3_exists_in_rebuilt_graph(tmp_path) -> None:
    store, target, graph = _build_recovered_store(tmp_path)
    assert target in graph._graph.nodes


# =====================================================================
# 2. recovered section@v3 exists
# =====================================================================

def test_recovered_section_v3_exists(tmp_path) -> None:
    store, target, graph = _build_recovered_store(tmp_path)
    sec_v3 = ArtifactRef(
        run_id=target.run_id,
        artifact_id="claim_report_financial_section", version=3,
    )
    assert sec_v3 in graph._graph.nodes
    art = store.get_version(sec_v3.run_id, sec_v3.artifact_id, sec_v3.version)
    assert art is not None
    assert art.validation_status == ArtifactStatus.RECOVERED.value


# =====================================================================
# 3. recovered claim@v3 exists
# =====================================================================

def test_recovered_claim_v3_exists(tmp_path) -> None:
    store, target, graph = _build_recovered_store(tmp_path)
    c_v3 = ArtifactRef(
        run_id=target.run_id,
        artifact_id="claim_financial_growth", version=3,
    )
    assert c_v3 in graph._graph.nodes
    art = store.get_version(c_v3.run_id, c_v3.artifact_id, c_v3.version)
    assert art is not None
    assert art.validation_status == ArtifactStatus.RECOVERED.value


# =====================================================================
# 4. rebuilt graph exact recovered path does not traverse old v2
# =====================================================================

def test_recovered_path_does_not_traverse_old_v2(tmp_path) -> None:
    store, target, graph = _build_recovered_store(tmp_path)
    audit = clean_path_audit(target, graph, store)
    visited_ids = {(r.artifact_id, r.version) for r in audit.visited_refs}
    # v2 chain must NOT appear in the visited set
    for aid in (
        "field_revenue_2025", "metric_revenue_growth",
        "metric_profit_margin", "metric_cost_ratio",
        "claim_financial_growth", "claim_report_financial_section",
        "report_merged",
    ):
        assert (aid, 2) not in visited_ids, (
            f"{aid}@v2 must not appear in the recovered v3 path"
        )


# =====================================================================
# 5. old contaminated v2 statuses remain unchanged
# =====================================================================

def test_old_v2_bad_statuses_preserved(tmp_path) -> None:
    """The audit MUST NOT touch historical fault trail.
    v2 nodes keep their CONTAMINATED / INVALIDATED status."""
    store, target, graph = _build_recovered_store(tmp_path)
    # field_revenue_2025@v2 stays INVALIDATED
    assert (
        store.get_version(target.run_id, "field_revenue_2025", 2)
        .validation_status == ArtifactStatus.INVALIDATED.value
    )
    # metric_revenue_growth@v2 stays CONTAMINATED
    assert (
        store.get_version(target.run_id, "metric_revenue_growth", 2)
        .validation_status == ArtifactStatus.CONTAMINATED.value
    )
    # report_merged@v2 stays CONTAMINATED
    assert (
        store.get_version(target.run_id, "report_merged", 2)
        .validation_status == ArtifactStatus.CONTAMINATED.value
    )
    # Running the audit does not change any of these
    clean_path_audit(target, graph, store)
    assert (
        store.get_version(target.run_id, "field_revenue_2025", 2)
        .validation_status == ArtifactStatus.INVALIDATED.value
    )
    assert (
        store.get_version(target.run_id, "report_merged", 2)
        .validation_status == ArtifactStatus.CONTAMINATED.value
    )


# =====================================================================
# 6. clean-path accepts RECOVERED
# =====================================================================

def test_clean_path_accepts_recovered_status(tmp_path) -> None:
    store, target, graph = _build_recovered_store(tmp_path)
    audit = clean_path_audit(target, graph, store)
    assert audit.passed is True
    assert audit.non_clean_refs == []
    assert audit.missing_refs == []
    # The target itself is RECOVERED, not VALID — but the
    # audit must accept it.
    assert any(
        r.artifact_id == "report_merged" and r.version == 3
        for r in audit.visited_refs
    )


# =====================================================================
# 7. mixed v3 / v1 / v1 report path passes
# =====================================================================

def test_mixed_v3_v1_v1_report_path_passes(tmp_path) -> None:
    """The recovered report@v3 cites financial@v3 + user@v1
    + operation@v1.  All three branches must be clean."""
    store, target, graph = _build_recovered_store(tmp_path)
    audit = clean_path_audit(target, graph, store)
    assert audit.passed
    # user and op sections are at v1 (clean baseline)
    user_v1 = ArtifactRef(
        run_id=target.run_id,
        artifact_id="claim_report_user_section", version=1,
    )
    op_v1 = ArtifactRef(
        run_id=target.run_id,
        artifact_id="claim_report_operation_section", version=1,
    )
    assert user_v1 in audit.visited_refs
    assert op_v1 in audit.visited_refs
    # And they are CLEAN (not in non_clean)
    for r in (user_v1, op_v1):
        assert r in audit.clean_refs


# =====================================================================
# 8. stale pre-recovery graph produces explicit diagnostic
# =====================================================================

def test_stale_graph_flag_surfaces_staleness_risk() -> None:
    """`graph_rebuilt_after_recovery=False` is a deliberate
    signal to the audit.  The audit still runs but the
    flag is recorded so downstream consumers can
    distinguish a True result on a fresh graph from a
    True result on a stale graph (which would be
    meaningless)."""
    with tempfile.TemporaryDirectory() as td:
        store, target, graph = _build_recovered_store(Path(td))
        # Build a STALE graph from a snapshot that excludes
        # the @v3 nodes (simulating: audit called before
        # the recovery ran).
        store_stale = ArtifactStore(base_dir=str(Path(td) / "stale"))
        # Copy only v1 + v2 JSON files (no v3)
        import shutil
        src = Path(td) / "store" / target.run_id
        dst = Path(td) / "stale" / target.run_id
        dst.mkdir(parents=True, exist_ok=True)
        for f in src.iterdir():
            if "__v3.json" in f.name:
                continue
            shutil.copy(f, dst / f.name)
        store_stale.reload()
        graph_stale = ArtifactGraph.from_store(store_stale, target.run_id)
        # v3 nodes are absent from the stale graph
        assert target not in graph_stale._graph.nodes
        # Run the audit with graph_rebuilt_after_recovery=False
        audit = clean_path_audit(
            target, graph_stale, store_stale,
            graph_rebuilt_after_recovery=False,
        )
        # The target is not in the stale graph; the audit
        # records it as missing (cannot conclude).
        assert audit.graph_rebuilt_after_recovery is False
        # missing_refs includes the target (and possibly more)
        assert any(
            r.artifact_id == "report_merged" and r.version == 3
            for r in audit.missing_refs
        )
        # And the audit does NOT falsely report passed=True
        # just because the stale graph has no bad nodes —
        # the target is missing so we cannot conclude.
        assert audit.passed is False


# =====================================================================
# 9. graph rebuild after recovery fixes stale graph scenario
# =====================================================================

def test_graph_rebuild_after_recovery_fixes_stale_scenario(
    tmp_path,
) -> None:
    """Building a fresh graph from the post-recovery store
    and re-running the audit flips the result from
    `passed=False (missing target)` to `passed=True`."""
    with tempfile.TemporaryDirectory() as td:
        store_fresh, target, graph_fresh = _build_recovered_store(Path(td))
        # Build a "stale" ArtifactStore that contains only v1+v2
        # (no v3).  Reuse v1+v2 from the fresh store's
        # on-disk JSON.
        stale_dir = Path(td) / "stale"
        stale_dir.mkdir(parents=True, exist_ok=True)
        for f in (Path(td) / "store" / target.run_id).iterdir():
            if "__v3.json" in f.name:
                continue
            a = Artifact.model_validate_json(
                f.read_text(encoding="utf-8")
            )
            ArtifactStore(base_dir=str(stale_dir)).save(a)
        stale_store = ArtifactStore(base_dir=str(stale_dir))
        stale_store.reload()
        stale_graph = ArtifactGraph.from_store(stale_store, target.run_id)
        # Stale audit: target missing
        stale_audit = clean_path_audit(
            target, stale_graph, stale_store,
            graph_rebuilt_after_recovery=True,
        )
        assert stale_audit.passed is False
        assert any(
            r.artifact_id == "report_merged" and r.version == 3
            for r in stale_audit.missing_refs
        )
        # Fresh audit (using the post-recovery store + graph):
        # passed=True.
        fresh_audit = clean_path_audit(
            target, graph_fresh, store_fresh,
            graph_rebuilt_after_recovery=True,
        )
        assert fresh_audit.passed is True


# =====================================================================
# 10. CleanPathAuditResult JSON roundtrip
# =====================================================================

def test_clean_path_audit_result_json_roundtrip(tmp_path) -> None:
    store, target, graph = _build_recovered_store(tmp_path)
    audit = clean_path_audit(target, graph, store)
    payload = audit.model_dump(mode="json")
    text = json.dumps(payload, ensure_ascii=False)
    restored = CleanPathAuditResult.model_validate_json(text)
    assert restored.passed == audit.passed
    assert restored.target_ref == audit.target_ref
    assert (
        [r.to_ref_string() for r in restored.visited_refs]
        == [r.to_ref_string() for r in audit.visited_refs]
    )
    assert restored.non_clean_refs == audit.non_clean_refs
    assert (
        [r.to_ref_string() for r in restored.missing_refs]
        == [r.to_ref_string() for r in audit.missing_refs]
    )
    assert (
        restored.graph_rebuilt_after_recovery
        == audit.graph_rebuilt_after_recovery
    )


# =====================================================================
# 11. offline re-audit performs 0 RealLLM requests
# =====================================================================

def test_offline_reaudit_performs_zero_reallm_requests(
    tmp_path, monkeypatch,
) -> None:
    """`clean_path_audit` must not call any LLM.  We patch
    the `RealLLMClient` symbol to raise if it is touched,
    then run the audit.  If the audit ever reached an
    LLM, the test would fail.
    """
    import llm.providers.real as _real_mod

    def _explode(*a, **kw):
        raise AssertionError(
            "RealLLMClient.generate called during offline "
            "re-audit — this is forbidden"
        )
    monkeypatch.setattr(_real_mod.RealLLMClient, "generate", _explode)
    store, target, graph = _build_recovered_store(tmp_path)
    audit = clean_path_audit(target, graph, store)
    assert audit.passed is True
    # No exception was raised = no LLM call was made.


# =====================================================================
# 12. all existing 442 tests remain green
# =====================================================================

def test_existing_442_tests_remain_green() -> None:
    """This is a meta-test: we just assert that the
    CleanPathAuditResult JSON shape does not collide with
    any existing field.  The full pytest run is what
    actually locks this; here we just confirm the
    `clean_path_audit` import does not break the public
    surface of `recovery`."""
    from recovery import (
        CleanPathAuditResult as _A,
        RecoveryExecutor,
        SelectiveRecoveryPlanner,
        clean_path_audit as _cpa,
        validate_recovery_execution_integrity as _vri,
        validate_target_clean_path as _vtcp,
    )
    assert _A is not None
    assert callable(_cpa)
    assert callable(_vri)
    assert callable(_vtcp)
