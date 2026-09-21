"""
Tests for Day 10 — Experiment Hardening.

Covers:
  - Corrected UARR / ArtifactReuseRate / ARP / RecomputeAmplification
  - Identity-level (not version-level) classification
  - tool_task_calls + llm_task_calls decomposition
  - tool_artifact_materializations + llm_artifact_materializations
  - Event-based instrumentation (no hand-increment in strategy code)
  - Case B (visits_2025 fault) and its SELECTIVE plan only recovers
    the Operation path
  - 5-run stability for both cases
  - Multi-case summary
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from artifacts import ArtifactRef, ArtifactStore
from experiments import (
    DEFINITIONS,
    ExecutionEvent,
    ExperimentResult,
    ExperimentStrategy,
    RunDelta,
    StructuralComparison,
    aggregate_events,
    build_main_fault_scenario,
    build_visits_fault_scenario,
    compute_arp,
    compute_artifact_reuse_rate,
    compute_recompute_amplification,
    compute_uarr,
    hash_content,
    run_coarse_checkpoint_recovery,
    run_full_rerun_recovery,
    run_main_case_comparison,
    run_multi_case_comparison,
    run_one_round,
    run_selective_recovery,
)
from provenance.decorator import RunContext
from recovery import (
    RecoveryExecutor,
    SelectiveRecoveryPlanner,
    validate_recovery_execution_integrity,
    validate_target_clean_path,
)


CSV = str(Path(__file__).resolve().parent.parent / "data" / "sample_company.csv")


# =====================================================================
# 1: UARR denominator is artifact_materializations, not "available"
# 2: Main selective UARR == 0
# 3: Full UARR > selective UARR
# 4: reuse denominator = available_unaffected_artifact_identities
# 5: selective reused numerator == available numerator
# 6: full reuse rate = 0
# =====================================================================

def test_uarr_denominator_is_materializations() -> None:
    # Synthetic check: the formula divides by materializations
    assert compute_uarr(0, 0) == 0.0
    assert compute_uarr(2, 10) == 0.2
    assert compute_uarr(5, 0) == 0.0


def test_main_selective_uarr_zero() -> None:
    sc, g = build_main_fault_scenario("run_a1")
    res = run_selective_recovery(sc, g)
    assert res.uarr == 0.0, f"selective UARR should be 0, got {res.uarr}"


def test_full_uarr_above_selective() -> None:
    sc1, g1 = build_main_fault_scenario("run_a_full")
    sc2, g2 = build_main_fault_scenario("run_a_sel")
    full = run_full_rerun_recovery(sc1, g1, CSV)
    sel = run_selective_recovery(sc2, g2)
    assert full.uarr > sel.uarr, (
        f"FULL UARR ({full.uarr}) should exceed SELECTIVE UARR ({sel.uarr})"
    )


def test_reuse_denominator_correct() -> None:
    sc, g = build_main_fault_scenario("run_a_rd")
    res = run_selective_recovery(sc, g)
    # available unaffected identities = the scenario's set
    available = res.available_unaffected_artifact_identities
    # reused_unaffected_artifact_identities must be a subset of
    # available (and vice versa, of those NOT in recomputed).
    reused = res.reused_unaffected_artifact_identities
    assert set(reused).issubset(set(available))
    # numerator + (unaffected recomputed) <= denominator
    assert len(reused) + res.unaffected_recomputed_count <= len(available)


def test_selective_reused_equals_available() -> None:
    sc, g = build_main_fault_scenario("run_a_eq")
    res = run_selective_recovery(sc, g)
    available = len(res.available_unaffected_artifact_identities)
    reused = len(res.reused_unaffected_artifact_identities)
    assert reused == available, (
        f"selective should reuse all available: {reused}/{available}"
    )


def test_full_reuse_rate_zero() -> None:
    sc, g = build_main_fault_scenario("run_a_full_r")
    res = run_full_rerun_recovery(sc, g, CSV)
    assert res.artifact_reuse_rate == 0.0


# =====================================================================
# 7-9: count decomposition
# =====================================================================

def test_task_calls_decomposes() -> None:
    sc, g = build_main_fault_scenario("run_a_decomp")
    res = run_full_rerun_recovery(sc, g, CSV)
    assert res.task_calls == res.tool_task_calls + res.llm_task_calls
    # tool: rerun_field_extraction=1, 6 metrics=6, merge_report=1 = 8
    # llm:  3 claims + 3 sections = 6
    assert res.tool_task_calls == 8
    assert res.llm_task_calls == 6


def test_artifact_materializations_decomposes() -> None:
    sc, g = build_main_fault_scenario("run_a_decomp2")
    res = run_full_rerun_recovery(sc, g, CSV)
    assert res.artifact_materializations == (
        res.tool_artifact_materializations + res.llm_artifact_materializations
    )
    # tool: 10 fields + 6 metrics + 1 merge = 17
    assert res.tool_artifact_materializations == 17
    # llm: 3 claims + 3 sections = 6
    assert res.llm_artifact_materializations == 6
    assert res.artifact_materializations == 23


def test_one_field_extraction_makes_many_artifacts() -> None:
    sc, g = build_main_fault_scenario("run_a_field")
    res = run_full_rerun_recovery(sc, g, CSV)
    # one rerun_field_extraction event must have produced 10 fields
    field_events = [e for e in res.events
                    if e.execution_unit == "rerun_field_extraction"]
    assert len(field_events) == 1
    assert len(field_events[0].produced_refs) == 10
    # and tool_task_calls counts it as 1
    assert res.tool_task_calls == 8


# =====================================================================
# 10-11: events can rebuild counts
# =====================================================================

def test_events_rebuild_all_counts() -> None:
    sc, g = build_main_fault_scenario("run_a_events")
    res = run_full_rerun_recovery(sc, g, CSV)
    agg = aggregate_events(res.events, case_id=res.case_id, strategy=res.strategy)
    assert agg["task_calls"] == res.task_calls
    assert agg["tool_task_calls"] == res.tool_task_calls
    assert agg["llm_task_calls"] == res.llm_task_calls
    assert agg["artifact_materializations"] == res.artifact_materializations
    assert agg["tool_artifact_materializations"] == res.tool_artifact_materializations
    assert agg["llm_artifact_materializations"] == res.llm_artifact_materializations
    assert agg["llm_calls"] == res.llm_calls


def test_strategy_does_not_hand_increment() -> None:
    """The strategy files should not have lines like 'task_calls += 1'
    or 'artifact_materializations += 1' — every count must come from
    the event stream.  This is enforced by reading the source."""
    import re
    src = Path("experiments/strategies.py").read_text(encoding="utf-8")
    for bad in (
        r"task_calls\s*\+=\s*1",
        r"llm_calls\s*\+=\s*1",
        r"artifact_materializations\s*\+=",
    ):
        assert not re.search(bad, src), (
            f"strategies.py still hand-increments via '{bad}'"
        )


# =====================================================================
# 12-13: Main Case 5-run stability + golden match
# =====================================================================

def test_main_case_5_runs_stable() -> None:
    rounds = [run_one_round(case_id="case_a", run_id=f"run_a_{i+1}")
              for i in range(5)]
    first = rounds[0]
    for r in rounds[1:]:
        for strat in ("full", "coarse_checkpoint", "selective"):
            sa, sb = getattr(first, strat), getattr(r, strat)
            for k in (
                "success", "matches_golden_output", "llm_calls",
                "task_calls", "tool_task_calls", "llm_task_calls",
                "artifact_materializations",
                "tool_artifact_materializations", "llm_artifact_materializations",
                "reused_artifact_count", "unaffected_recomputed_count",
                "uarr", "artifact_reuse_rate",
                "affected_recovery_precision_arp", "recompute_amplification",
            ):
                assert getattr(sa, k) == getattr(sb, k), (
                    f"stability: round {r.case_id} {strat} {k}: "
                    f"{getattr(sa, k)} != {getattr(sb, k)}"
                )


def test_main_case_golden_match_all_strategies() -> None:
    c = run_one_round(case_id="case_a", run_id="run_a_g")
    for strat in ("full", "coarse_checkpoint", "selective"):
        r = getattr(c, strat)
        assert r.matches_golden_output is True
        assert r.final_content_hash == c.golden_output_hash


# =====================================================================
# 14-19: Case B construction
# =====================================================================

def test_case_b_fault_injection() -> None:
    sc, g = build_visits_fault_scenario("run_b_inj")
    bad = sc.store.get_version(sc.run_id, "field_visits_2025", 2)
    assert bad is not None
    assert bad.meta.get("fault_injected") is True
    assert bad.validation_status == "INVALIDATED"


def test_case_b_validator() -> None:
    sc, g = build_visits_fault_scenario("run_b_val")
    bad = sc.store.get_version(sc.run_id, "field_visits_2025", 2)
    raw = sc.store.get_version(sc.run_id, "raw_data", 1)
    from contamination import validate_field_against_raw
    res = validate_field_against_raw(bad, raw)
    assert res.passed is False
    assert "differ" in res.reason.lower() or "recompute" in res.reason.lower()


def test_case_b_contamination_range() -> None:
    sc, g = build_visits_fault_scenario("run_b_contam")
    inv = {r.artifact_id for r in sc.contamination.invalidated}
    con = {r.artifact_id for r in sc.contamination.contaminated}
    assert inv == {"field_visits_2025"}
    assert "metric_conversion_rate" in con
    assert "claim_operation" in con
    assert "claim_report_operation_section" in con
    assert "report_merged" in con


def test_case_b_order_growth_valid() -> None:
    sc, g = build_visits_fault_scenario("run_b_og")
    art = sc.store.get_version(sc.run_id, "metric_order_growth", 1)
    assert art is not None
    assert art.validation_status == "VALID"


def test_case_b_financial_branch_valid() -> None:
    sc, g = build_visits_fault_scenario("run_b_fin")
    for aid in (
        "metric_revenue_growth", "metric_profit_margin", "metric_cost_ratio",
        "claim_financial_growth", "claim_report_financial_section",
    ):
        a = sc.store.get_version(sc.run_id, aid, 1)
        assert a is not None, f"missing {aid}@v1"
        assert a.validation_status == "VALID", (
            f"{aid} should be VALID but is {a.validation_status}"
        )


def test_case_b_user_branch_valid() -> None:
    sc, g = build_visits_fault_scenario("run_b_usr")
    for aid in ("metric_user_growth", "claim_user_growth", "claim_report_user_section"):
        a = sc.store.get_version(sc.run_id, aid, 1)
        assert a is not None
        assert a.validation_status == "VALID"


# =====================================================================
# 20-22: Case B selective recovery
# =====================================================================

def test_case_b_selective_plan_only_operation() -> None:
    sc, g = build_visits_fault_scenario("run_b_plan")
    g2 = __import__("provenance.graph", fromlist=["ArtifactGraph"]).ArtifactGraph.from_store(
        sc.store, sc.run_id,
    )
    plan = SelectiveRecoveryPlanner(
        g2, sc.store, sc.run_id, sc.target_ref, sc.contamination,
    )
    affected = {s.target_ref.artifact_id for s in plan.steps}
    # operation path: field_visits_2025, metric_conversion_rate,
    #   claim_operation, claim_report_operation_section, report_merged
    assert "field_visits_2025" in affected
    assert "metric_conversion_rate" in affected
    assert "claim_operation" in affected
    assert "claim_report_operation_section" in affected
    assert "report_merged" in affected
    # financial / user are NOT in the plan
    assert "claim_financial_growth" not in affected
    assert "claim_user_growth" not in affected
    # metric_order_growth not in the plan
    assert "metric_order_growth" not in affected


def test_case_b_operation_reprompt_uses_old_order_growth() -> None:
    """generate_operation_claim rerun must consume metric_order_growth@v1
    (still VALID) as execution prerequisite, even though it is not
    in the EVIDENCE set of the bad operation_claim."""
    sc, g = build_visits_fault_scenario("run_b_repr")
    res = run_selective_recovery(sc, g)
    # find the recovered operation_claim v3
    op_claim = sc.store.get_version(sc.run_id, "claim_operation", 3)
    assert op_claim is not None
    keys = {(r.artifact_id, r.version) for r in op_claim.input_artifact_ids}
    assert ("metric_conversion_rate", 3) in keys
    assert ("metric_order_growth", 1) in keys   # ← REUSED v1


def test_case_b_final_report_mixed_versions() -> None:
    sc, g = build_visits_fault_scenario("run_b_mix")
    res = run_selective_recovery(sc, g)
    final = sc.store.get_version(sc.run_id, "report_merged", 3)
    assert final is not None
    keys = {(r.artifact_id, r.version) for r in final.input_artifact_ids}
    assert keys == {
        ("claim_report_financial_section", 1),
        ("claim_report_user_section", 1),
        ("claim_report_operation_section", 3),
    }


# =====================================================================
# 23-26: Case B three-strategy success
# =====================================================================

def test_case_b_matches_golden() -> None:
    c = run_one_round(case_id="case_b", run_id="run_b_g")
    for strat in ("full", "coarse_checkpoint", "selective"):
        r = getattr(c, strat)
        assert r.matches_golden_output is True, (
            f"{strat} did not match Case B golden"
        )
        assert r.final_content_hash == c.golden_output_hash


def test_case_b_full_success() -> None:
    c = run_one_round(case_id="case_b", run_id="run_b_full")
    assert c.full.success is True


def test_case_b_coarse_success() -> None:
    c = run_one_round(case_id="case_b", run_id="run_b_cc")
    assert c.coarse_checkpoint.success is True


def test_case_b_selective_success() -> None:
    c = run_one_round(case_id="case_b", run_id="run_b_sel")
    assert c.selective.success is True


# =====================================================================
# 27: Case B 5-run stability
# =====================================================================

def test_case_b_5_runs_stable() -> None:
    rounds = [run_one_round(case_id="case_b", run_id=f"run_b_stab_{i+1}")
              for i in range(5)]
    first = rounds[0]
    for r in rounds[1:]:
        for strat in ("full", "coarse_checkpoint", "selective"):
            sa, sb = getattr(first, strat), getattr(r, strat)
            for k in (
                "success", "matches_golden_output", "llm_calls",
                "task_calls", "artifact_materializations",
                "reused_artifact_count", "uarr", "artifact_reuse_rate",
                "affected_recovery_precision_arp", "recompute_amplification",
            ):
                assert getattr(sa, k) == getattr(sb, k), (
                    f"case_b stability: {strat} {k}"
                )


# =====================================================================
# 28-29: ARP + RecomputeAmplification formulas
# =====================================================================

def test_arp_formula() -> None:
    assert compute_arp(0, 0) == 0.0
    assert compute_arp(7, 7) == 1.0
    assert compute_arp(0, 10) == 0.0
    assert compute_arp(5, 10) == 0.5


def test_recompute_amplification_formula() -> None:
    assert compute_recompute_amplification(0, 0) == 0.0
    assert compute_recompute_amplification(7, 7) == 1.0
    assert compute_recompute_amplification(23, 7) == pytest.approx(23 / 7)


def test_arp_plus_uarr_equals_one_when_no_double_materialization() -> None:
    """For Case A SELECTIVE: every materialization IS an affected
    identity, so ARP = 1 and UARR = 0.  ARP + UARR == 1."""
    sc, g = build_main_fault_scenario("run_a_arp")
    res = run_selective_recovery(sc, g)
    assert res.affected_recovery_precision_arp + res.uarr == pytest.approx(1.0)


# =====================================================================
# 30: machine-readable JSON roundtrip
# =====================================================================

def test_day10_summary_json_roundtrip(tmp_path: Path) -> None:
    out = run_multi_case_comparison(
        case_specs=["case_a", "case_b"],
        n_repeats=2,
        output_path=str(tmp_path / "summary.json"),
    )
    p = tmp_path / "summary.json"
    assert p.exists()
    parsed = json.loads(p.read_text(encoding="utf-8"))
    assert "case_a" in parsed["cases"]
    assert "case_b" in parsed["cases"]
    assert "macro_average" in parsed
    assert "stability_all_cases" in parsed
    macro = parsed["macro_average"]
    for s in ("full_rerun", "coarse_checkpoint", "selective_recovery"):
        assert s in macro
        for k in ("task_calls", "artifact_materializations",
                  "uarr", "artifact_reuse_rate",
                  "affected_recovery_precision_arp", "recompute_amplification"):
            assert k in macro[s]


# =====================================================================
# 31: Day 1-9 tests covered by full pytest run (no body needed).
# Extra: ensure the corrected selectivity still beats full in both cases
# =====================================================================

def test_selective_strictly_better_than_full_in_both_cases() -> None:
    for case in ("case_a", "case_b"):
        c = run_one_round(case_id=case, run_id=f"run_both_{case}")
        assert c.selective.task_calls < c.full.task_calls, case
        assert c.selective.llm_task_calls <= c.full.llm_task_calls, case
        assert c.selective.artifact_materializations < c.full.artifact_materializations, case
        assert c.selective.uarr == 0.0, case
        assert c.full.uarr > 0.0, case
        assert c.selective.artifact_reuse_rate == 1.0, case
        assert c.full.artifact_reuse_rate == 0.0, case
        assert c.selective.affected_recovery_precision_arp == 1.0, case
        assert c.full.affected_recovery_precision_arp < 1.0, case
