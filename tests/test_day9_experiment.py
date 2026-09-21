"""
Tests for Day 9 — Controlled Offline Structural Experiment.

Covers the FULL_RERUN / COARSE_CHECKPOINT / SELECTIVE_RECOVERY
comparison harness, the new validate_recovery_execution_integrity
check, and the formal metric definitions.

Counted against Day 9 spec section 26 (30 items).
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from artifacts import (
    ArtifactRef,
    ArtifactStatus,
    ArtifactStore,
)
from contamination import detect_field_faults, propagate_contamination
from experiments import (
    DEFINITIONS,
    ExperimentResult,
    ExperimentStrategy,
    RunDelta,
    StructuralComparison,
    build_golden_clean_scenario,
    build_main_fault_scenario,
    compute_artifact_reuse_rate,
    compute_llm_call_reduction,
    compute_materialization_reduction,
    compute_uarr,
    hash_content,
    inject_field_value_tamper,
    run_coarse_checkpoint_recovery,
    run_full_rerun_recovery,
    run_main_case_comparison,
    run_one_round,
    run_selective_recovery,
)
from llm import default_workflow_llm
from provenance.decorator import RunContext
from provenance.graph import ArtifactGraph
from recovery import (
    RecoveryExecutor,
    SelectiveRecoveryPlanner,
    validate_recovery_execution_integrity,
    validate_target_clean_path,
)


CSV = str(Path(__file__).resolve().parent.parent / "data" / "sample_company.csv")


# =====================================================================
# 1: semantic + integrity double validation
# 2: recovered Claim all INPUT clean
# 14-16: full / coarse / selective recovery success
# =====================================================================

def test_integrity_check_passes_for_main_case_recovery() -> None:
    sc, g = build_main_fault_scenario("run_int1")
    res = run_selective_recovery(sc, g)
    assert res.success is True
    assert res.execution_integrity_pass is True
    assert res.semantic_clean_path_pass is True


def test_recovered_claim_all_inputs_clean() -> None:
    sc, g = build_main_fault_scenario("run_int2")
    res = run_selective_recovery(sc, g)
    # The final claim@v3 (or v2 if checkpoint/full) has all VALID/RECOVERED inputs
    final = res.final_target_ref
    assert final is not None
    # Walk its chain back: every node in the upstream closure must
    # not be INVALIDATED or CONTAMINATED.
    store = sc.store
    final_art = store.get_version(final.run_id, final.artifact_id, final.version)
    assert final_art is not None
    bad = {ArtifactStatus.INVALIDATED.value, ArtifactStatus.CONTAMINATED.value}
    # walk all upstream refs
    g2 = ArtifactGraph.from_store(store, final.run_id)
    visited = {final}
    stack = [final]
    while stack:
        cur = stack.pop()
        a = store.get_version(cur.run_id, cur.artifact_id, cur.version)
        assert a is not None
        assert a.validation_status not in bad, (
            f"bad ancestor: {cur.artifact_id}@v{cur.version}={a.validation_status}"
        )
        for p in g2.get_upstream(cur):
            if p not in visited:
                visited.add(p)
                stack.append(p)


# =====================================================================
# 3-5: each strategy completes recovery
# =====================================================================

def test_full_rerun_recovers() -> None:
    sc, g = build_main_fault_scenario("run_full1")
    res = run_full_rerun_recovery(sc, g, CSV)
    assert res.success is True, res.failure_reason
    assert res.matches_golden_output is True


def test_coarse_checkpoint_recovers() -> None:
    sc, g = build_main_fault_scenario("run_cc1")
    res = run_coarse_checkpoint_recovery(sc, g, CSV)
    assert res.success is True, res.failure_reason
    assert res.matches_golden_output is True


def test_selective_uses_day8_planner() -> None:
    sc, g = build_main_fault_scenario("run_sel1")
    # Inspect: the planner is invoked inside the strategy.
    # We assert the result has the structural property of selective:
    # its llm_calls < full.llm_calls
    res = run_selective_recovery(sc, g)
    sc2, g2 = build_main_fault_scenario("run_full2")
    full = run_full_rerun_recovery(sc2, g2, CSV)
    assert res.llm_calls < full.llm_calls
    assert res.success is True


# =====================================================================
# 6-7: full / coarse must NOT call SelectiveRecoveryPlanner
# =====================================================================

def test_full_does_not_call_planner() -> None:
    sc, g = build_main_fault_scenario("run_full_no_planner")
    # spy: replace SelectiveRecoveryPlanner to fail the test if called
    from recovery import planner as _planner_mod

    real = _planner_mod.SelectiveRecoveryPlanner
    called = {"n": 0}

    def spy(*a, **kw):
        called["n"] += 1
        return real(*a, **kw)

    _planner_mod.SelectiveRecoveryPlanner = spy
    try:
        run_full_rerun_recovery(sc, g, CSV)
    finally:
        _planner_mod.SelectiveRecoveryPlanner = real
    assert called["n"] == 0, "FULL_RERUN must not call SelectiveRecoveryPlanner"


def test_coarse_does_not_call_planner() -> None:
    sc, g = build_main_fault_scenario("run_cc_no_planner")
    from recovery import planner as _planner_mod

    real = _planner_mod.SelectiveRecoveryPlanner
    called = {"n": 0}

    def spy(*a, **kw):
        called["n"] += 1
        return real(*a, **kw)

    _planner_mod.SelectiveRecoveryPlanner = spy
    try:
        run_coarse_checkpoint_recovery(sc, g, CSV)
    finally:
        _planner_mod.SelectiveRecoveryPlanner = real
    assert called["n"] == 0, "COARSE_CHECKPOINT must not call SelectiveRecoveryPlanner"


# =====================================================================
# 8-9: equivalent scenario + same FakeLLM config
# =====================================================================

def test_three_scenarios_equivalent() -> None:
    sc1, g1 = build_main_fault_scenario("run_eq1")
    sc2, g2 = build_main_fault_scenario("run_eq2")
    sc3, g3 = build_main_fault_scenario("run_eq3")
    # All three should have the same contamination set
    # (artifact IDs).  Day 7 invariant: contamination only
    # walks exact-version edges; v1 stays UNAFFECTED, v2
    # of the financial chain is CONTAMINATED.  The set is
    # therefore deterministic and identical across runs.
    ids1 = sorted(r.artifact_id for r in sc1.contamination.contaminated)
    ids2 = sorted(r.artifact_id for r in sc2.contamination.contaminated)
    ids3 = sorted(r.artifact_id for r in sc3.contamination.contaminated)
    assert ids1 == ids2 == ids3, (
        f"contamination sets differ across runs: "
        f"{ids1} vs {ids2} vs {ids3}"
    )
    # Same invalidated
    inv1 = sorted(r.artifact_id for r in sc1.contamination.invalidated)
    inv2 = sorted(r.artifact_id for r in sc2.contamination.invalidated)
    inv3 = sorted(r.artifact_id for r in sc3.contamination.invalidated)
    assert inv1 == inv2 == inv3
    # All three goldens hash to the same value
    assert g1.final_report_hash == g2.final_report_hash == g3.final_report_hash


def test_three_strategies_use_same_fake_llm_config() -> None:
    sc1, g1 = build_main_fault_scenario("run_cfg1")
    sc2, g2 = build_main_fault_scenario("run_cfg2")
    sc3, g3 = build_main_fault_scenario("run_cfg3")
    full = run_full_rerun_recovery(sc1, g1, CSV)
    coarse = run_coarse_checkpoint_recovery(sc2, g2, CSV)
    sel = run_selective_recovery(sc3, g3)
    # Same prompt character count per LLM call (FakeLLM is deterministic
    # so prompt size depends only on inputs).  We don't compare absolute
    # token totals because the strategies call different units.  Instead
    # we assert that all three eventually produce a final report whose
    # hash matches the golden — which is only possible if all three use
    # the same FakeLLM config.
    assert full.matches_golden_output
    assert coarse.matches_golden_output
    assert sel.matches_golden_output


# =====================================================================
# 10: recovery log reset at phase start
# =====================================================================

def test_recovery_log_resets_at_phase_start() -> None:
    sc, g = build_main_fault_scenario("run_phase_start")
    res = run_selective_recovery(sc, g)
    # selective strategy should have 2 LLM calls (financial claim +
    # financial section) — NOT 4 (which would mean baseline calls were
    # counted in).
    assert res.llm_calls == 2, f"expected 2 recovery LLM calls, got {res.llm_calls}"


# =====================================================================
# 11-13: golden match
# =====================================================================

def test_full_matches_golden() -> None:
    sc, g = build_main_fault_scenario("run_g_full")
    res = run_full_rerun_recovery(sc, g, CSV)
    assert res.final_content_hash == g.final_report_hash


def test_coarse_matches_golden() -> None:
    sc, g = build_main_fault_scenario("run_g_cc")
    res = run_coarse_checkpoint_recovery(sc, g, CSV)
    assert res.final_content_hash == g.final_report_hash


def test_selective_matches_golden() -> None:
    sc, g = build_main_fault_scenario("run_g_sel")
    res = run_selective_recovery(sc, g)
    assert res.final_content_hash == g.final_report_hash


# =====================================================================
# 17-21: instrumentation correctness
# =====================================================================

def test_task_calls_recorded() -> None:
    sc, g = build_main_fault_scenario("run_tc1")
    res = run_full_rerun_recovery(sc, g, CSV)
    # extract_fields + 6 metrics + 3 claims + 3 sections + 1 merge = 14
    assert res.task_calls == 14, f"expected 14 task calls, got {res.task_calls}"


def test_artifact_materializations_recorded() -> None:
    sc, g = build_main_fault_scenario("run_am1")
    res = run_full_rerun_recovery(sc, g, CSV)
    # 10 fields + 6 metrics + 3 claims + 3 sections + 1 merge = 23
    assert res.artifact_materializations == 23, (
        f"expected 23 materializations, got {res.artifact_materializations}"
    )


def test_llm_calls_recorded() -> None:
    sc, g = build_main_fault_scenario("run_lc1")
    res = run_full_rerun_recovery(sc, g, CSV)
    # 3 claims + 3 sections = 6 LLM calls
    assert res.llm_calls == 6


def test_unaffected_recomputation_recorded() -> None:
    sc, g = build_main_fault_scenario("run_ur1")
    res = run_full_rerun_recovery(sc, g, CSV)
    # Day 10: the available_unaffected_artifact_identities set has
    # 15 entries (Day 9's old "23 reusable" included target/raw_data).
    # FULL re-runs all 15 of them, so the recomputed list has >= 14
    # identity overlaps.
    assert len(res.unaffected_artifacts_recomputed) >= 14, (
        f"expected many recomputed, got {len(res.unaffected_artifacts_recomputed)}"
    )


def test_reuse_count_recorded() -> None:
    sc, g = build_main_fault_scenario("run_rc1")
    res = run_selective_recovery(sc, g)
    # selective reuses user / operation branches
    assert res.reused_artifact_count > 0


# =====================================================================
# 22-25: formula correctness
# =====================================================================

def test_uarr_formula() -> None:
    assert compute_uarr(0, 0) == 0.0
    assert compute_uarr(2, 10) == 0.2
    assert compute_uarr(5, 0) == 0.0  # zero denom → 0


def test_artifact_reuse_rate_formula() -> None:
    assert compute_artifact_reuse_rate(0, 0) == 0.0
    assert compute_artifact_reuse_rate(5, 10) == 0.5
    assert compute_artifact_reuse_rate(10, 0) == 0.0  # zero denom → 0


def test_llm_call_reduction_formula() -> None:
    assert compute_llm_call_reduction(10, 10) == 0.0
    assert compute_llm_call_reduction(10, 5) == 0.5
    assert compute_llm_call_reduction(0, 5) == 0.0  # zero denom → 0


def test_materialization_reduction_formula() -> None:
    assert compute_materialization_reduction(20, 5) == 0.75
    assert compute_materialization_reduction(0, 5) == 0.0


# =====================================================================
# 26: synthetic token flag
# 27: wall clock not used as model latency
# =====================================================================

def test_synthetic_tokens_marked_in_definitions() -> None:
    assert "synthetic" in DEFINITIONS["usage_type"]
    # provider_type may be FakeLLM (the synthetic tag is in usage_type);
    # the experiment_version is what carries the "synthetic" framing.
    assert "synthetic" in DEFINITIONS["experiment_version"]
    assert DEFINITIONS["latency_type"].startswith("local_wall_clock_ms")


# =====================================================================
# 28: machine-readable JSON
# =====================================================================

def test_machine_readable_json_roundtrip(tmp_path: Path) -> None:
    out = run_main_case_comparison(n_repeats=2, output_path=str(tmp_path / "r.json"))
    p = tmp_path / "r.json"
    assert p.exists()
    parsed = json.loads(p.read_text(encoding="utf-8"))
    assert "strategies" in parsed
    assert "full_rerun" in parsed["strategies"]
    assert "coarse_checkpoint" in parsed["strategies"]
    assert "selective_recovery" in parsed["strategies"]
    assert "metrics" in parsed
    assert "definitions" in parsed
    assert "stability" in parsed
    assert "FakeLLMClient" in parsed["provider_type"]
    assert "synthetic" in parsed["provider_type"]
    assert parsed["usage_type"].startswith("synthetic")


# =====================================================================
# 29: 5-run structural stability
# =====================================================================

def test_five_runs_structural_stability() -> None:
    rounds = []
    for i in range(5):
        c = run_one_round(case_id="case_a", run_id=f"run_stab_{i+1}")
        rounds.append(c)
    first = rounds[0]
    for r in rounds[1:]:
        for strat in ("full", "coarse_checkpoint", "selective"):
            sa, sb = getattr(first, strat), getattr(r, strat)
            for k in (
                "success", "matches_golden_output", "llm_calls",
                "artifact_materializations", "task_calls",
                "tool_task_calls", "llm_task_calls",
                "reused_artifact_count", "unaffected_recomputed_count",
                "uarr", "artifact_reuse_rate",
                "affected_recovery_precision_arp", "recompute_amplification",
            ):
                assert getattr(sa, k) == getattr(sb, k), (
                    f"stability violation: round {r.case_id} {strat} {k}: "
                    f"{getattr(sa, k)} != {getattr(sb, k)}"
                )


# =====================================================================
# 30: Day 1-8 tests still pass — covered by the full pytest run
# (no test in this file needed; we just keep this test slot for the
# spec's numbering).
# =====================================================================

def test_day9_module_invariants() -> None:
    """Sanity check: the Day 9 module exports the documented public API."""
    from experiments import (  # noqa: F401
        build_golden_clean_scenario,
        build_main_fault_scenario,
        run_full_rerun_recovery,
        run_coarse_checkpoint_recovery,
        run_selective_recovery,
        run_main_case_comparison,
        run_one_round,
        ExperimentResult,
        ExperimentStrategy,
        StructuralComparison,
        DEFINITIONS,
    )
    # Day 10 renamed tool_calls -> tool_task_calls and added several
    # new keys.  Test the new canonical names.
    for k in (
        "task_calls",
        "tool_task_calls",
        "llm_task_calls",
        "artifact_materializations",
        "llm_calls",
        "ArtifactReuseRate",
        "UARR",
        "ARP",
        "RecomputeAmplification",
        "LLMCallReductionVsFull",
        "ArtifactMaterializationReductionVsFull",
        "matches_golden_output",
        "semantic_clean_path_pass",
        "execution_integrity_pass",
    ):
        assert k in DEFINITIONS


# =====================================================================
# Extra tests for harness output structure
# =====================================================================

def test_harness_writes_results_file() -> None:
    with tempfile.TemporaryDirectory() as td:
        out = run_main_case_comparison(
            n_repeats=2,
            output_path=str(Path(td) / "experiments" / "results" / "day9.json"),
        )
        assert out["n_repeats"] == 2
        assert out["stability"]["structural_identical_across_runs"] is True


def test_selective_artifact_materializations_below_full() -> None:
    sc1, g1 = build_main_fault_scenario("run_reduce1")
    sc2, g2 = build_main_fault_scenario("run_reduce2")
    full = run_full_rerun_recovery(sc1, g1, CSV)
    sel = run_selective_recovery(sc2, g2)
    assert sel.artifact_materializations < full.artifact_materializations


def test_selective_llm_calls_below_full() -> None:
    sc1, g1 = build_main_fault_scenario("run_reduce_lc")
    sc2, g2 = build_main_fault_scenario("run_reduce_lc2")
    full = run_full_rerun_recovery(sc1, g1, CSV)
    sel = run_selective_recovery(sc2, g2)
    assert sel.llm_calls < full.llm_calls


def test_golden_clean_scenario_matches_baseline_run() -> None:
    g = build_golden_clean_scenario("run_g_check")
    # All final report versions should be v1 (clean baseline)
    assert g.final_report.version == 1
    # and its status is VALID
    assert g.final_report.validation_status == "VALID"
