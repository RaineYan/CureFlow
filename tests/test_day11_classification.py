"""
Tests for Day 11 — Metric Semantics Finalization.

Every Recovery-Phase materialization is classified into exactly one of
two buckets (REQUIRED_RECOVERY or UNNECESSARY_RECOMPUTATION).  Active
reuse is derived from the event stream, not from "available -
recomputed".  The partition invariant `required + unnecessary == total`
and the precision-complement invariant `RMP + UARR == 1` are asserted
across all strategies and both cases.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from experiments import (
    ExperimentStrategy,
    MaterializationClassification,
    SOURCE_OF_TRUTH_ARTIFACT,
    build_main_fault_scenario,
    build_visits_fault_scenario,
    classify_materializations,
    compute_arr_v2,
    compute_recompute_amplification as compute_raf,
    compute_rmp,
    compute_taf,
    compute_uarr_v2,
    compute_upr,
    count_required,
    count_unnecessary,
    derive_active_reuse_and_preserved,
    derive_required_recovery_identities,
    derive_target_recovery_closure,
    run_full_rerun_recovery,
    run_one_round,
    run_selective_recovery,
)
from artifacts import ArtifactRef, ArtifactStore
from provenance.graph import ArtifactGraph


CSV = str(Path(__file__).resolve().parent.parent / "data" / "sample_company.csv")


# =====================================================================
# 1-2: partition invariant
# =====================================================================

def test_every_materialization_classified_exactly_once() -> None:
    sc, g = build_main_fault_scenario("run_p1")
    r = run_full_rerun_recovery(sc, g, CSV)
    # every produced_ref appears exactly once in r.classifications
    produced_keys = [
        (c["produced_ref"]["run_id"], c["produced_ref"]["artifact_id"],
         c["produced_ref"]["version"])
        for c in r.classifications
    ]
    assert len(produced_keys) == r.artifact_materializations
    # unique
    assert len(set(produced_keys)) == len(produced_keys)
    # every classification is one of the two valid labels
    for c in r.classifications:
        assert c["classification"] in ("REQUIRED_RECOVERY", "UNNECESSARY_RECOMPUTATION")


def test_partition_invariant_required_plus_unnecessary_equals_total() -> None:
    def _full(sc, g, csv):
        return run_full_rerun_recovery(sc, g, csv)
    def _sel(sc, g, csv=None):
        return run_selective_recovery(sc, g)
    cases = [
        ("case_a", build_main_fault_scenario, _full, "FULL"),
        ("case_a", build_main_fault_scenario, _sel, "SELECT"),
        ("case_b", build_visits_fault_scenario, _full, "FULL"),
        ("case_b", build_visits_fault_scenario, _sel, "SELECT"),
    ]
    for case_id, builder, factory, label in cases:
        sc, g = builder(f"run_p_{case_id}_{label}")
        r = factory(sc, g, CSV) if label == "FULL" else factory(sc, g)
        assert r.required_materializations + r.unnecessary_materializations == \
            r.artifact_materializations, (
                f"{case_id} {label}: req+unn != total"
            )


# =====================================================================
# 3: RMP + UARR == 1
# =====================================================================

def test_rmp_plus_uarr_equals_one() -> None:
    def _full(sc, g, csv):
        return run_full_rerun_recovery(sc, g, csv)
    def _sel(sc, g, csv=None):
        return run_selective_recovery(sc, g)
    cases = [
        ("case_a", build_main_fault_scenario, _full, "FULL"),
        ("case_a", build_main_fault_scenario, _sel, "SELECT"),
        ("case_b", build_visits_fault_scenario, _full, "FULL"),
        ("case_b", build_visits_fault_scenario, _sel, "SELECT"),
    ]
    for case_id, builder, factory, label in cases:
        sc, g = builder(f"run_rmp_{case_id}_{label}")
        r = factory(sc, g, CSV) if label == "FULL" else factory(sc, g)
        assert r.recovery_materialization_precision + r.uarr == pytest.approx(1.0, abs=1e-9), (
            f"{case_id} {label}: RMP+UARR={r.recovery_materialization_precision + r.uarr}"
        )


# =====================================================================
# 4-7: unnecessary counts
# =====================================================================

def test_main_full_identifies_unnecessary_materializations() -> None:
    sc, g = build_main_fault_scenario("run_u_main_full")
    r = run_full_rerun_recovery(sc, g, CSV)
    # required = 7; unnecessary = 16
    assert r.required_materializations == 7
    assert r.unnecessary_materializations == 16
    # inspect the classification records
    unnec = [c for c in r.classifications if c["classification"] == "UNNECESSARY_RECOMPUTATION"]
    assert len(unnec) == 16


def test_main_selective_unnecessary_zero() -> None:
    sc, g = build_main_fault_scenario("run_u_main_sel")
    r = run_selective_recovery(sc, g)
    assert r.unnecessary_materializations == 0
    assert r.required_materializations == r.artifact_materializations


def test_case_b_full_identifies_unnecessary_materializations() -> None:
    sc, g = build_visits_fault_scenario("run_u_b_full")
    r = run_full_rerun_recovery(sc, g, CSV)
    # required = 5; unnecessary = 18
    assert r.required_materializations == 5
    assert r.unnecessary_materializations == 18


def test_case_b_selective_unnecessary_zero() -> None:
    sc, g = build_visits_fault_scenario("run_u_b_sel")
    r = run_selective_recovery(sc, g)
    assert r.unnecessary_materializations == 0


# =====================================================================
# 8-9: active reuse from event stream
# =====================================================================

def test_active_reuse_derived_from_event_stream() -> None:
    sc, g = build_main_fault_scenario("run_arr1")
    r = run_selective_recovery(sc, g)
    # every identity in actively_reused_artifact_identities must
    # appear in at least one event's input_refs or reused_refs.
    actively_reused = set(r.actively_reused_artifact_identities)
    found_in_events: set = set()
    for e in r.events:
        for ref in e.input_refs:
            if ref.artifact_id not in (
                set(r.affected_artifact_identities) | {SOURCE_OF_TRUTH_ARTIFACT}
            ):
                found_in_events.add(ref.artifact_id)
        for ref in e.reused_refs:
            if ref.artifact_id not in (
                set(r.affected_artifact_identities) | {SOURCE_OF_TRUTH_ARTIFACT}
            ):
                found_in_events.add(ref.artifact_id)
    assert actively_reused.issubset(found_in_events)


def test_not_recomputed_is_not_actively_reused() -> None:
    """Preserved identities that the strategy never consumed (input_ref
    or reused_ref) must NOT be in actively_reused.  Example: a
    metric that's available but never read by any recovery step."""
    sc, g = build_main_fault_scenario("run_arr2")
    r = run_full_rerun_recovery(sc, g, CSV)
    # FULL re-runs everything; nothing is preserved → nothing is
    # actively reused
    assert r.actively_reused_count == 0
    # SELECTIVE reuses user/op sections
    sc2, g2 = build_main_fault_scenario("run_arr2s")
    r2 = run_selective_recovery(sc2, g2)
    assert r2.actively_reused_count > 0
    # and the actively_reused identities should NOT include identities
    # the SELECTIVE plan never touches (like field_orders_2024 for
    # which no SELECTIVE step has it in input_refs).
    for aid in r2.actively_reused_artifact_identities:
        # every active identity must appear in some event's input_refs
        # or reused_refs
        present = any(
            (ref.artifact_id == aid)
            for e in r2.events
            for ref in (e.input_refs + e.reused_refs)
        )
        assert present, f"{aid} not found in any event's input/reused refs"


# =====================================================================
# 10-11: preserved vs active reuse
# =====================================================================

def test_actively_reused_is_subset_of_preserved() -> None:
    sc, g = build_main_fault_scenario("run_ar1")
    r = run_selective_recovery(sc, g)
    assert set(r.actively_reused_artifact_identities).issubset(
        set(r.preserved_unaffected_artifact_identities)
    )


def test_skipped_irrelevant_correct() -> None:
    sc, g = build_main_fault_scenario("run_ar2")
    r = run_selective_recovery(sc, g)
    # skipped = available - recomputed - actively_reused
    avail = set(r.available_unaffected_artifact_identities)
    recomputed = set(c["identity"] for c in r.classifications)
    actively = set(r.actively_reused_artifact_identities)
    expected_skipped = avail - recomputed - actively
    actual_skipped = set(r.skipped_irrelevant_artifact_identities)
    assert actual_skipped == expected_skipped


# =====================================================================
# 12: raw_data not in derived metrics
# =====================================================================

def test_raw_data_excluded_from_derived_metrics() -> None:
    sc, g = build_main_fault_scenario("run_raw1")
    r = run_selective_recovery(sc, g)
    assert SOURCE_OF_TRUTH_ARTIFACT not in r.actively_reused_artifact_identities
    assert SOURCE_OF_TRUTH_ARTIFACT not in r.preserved_unaffected_artifact_identities
    assert SOURCE_OF_TRUTH_ARTIFACT not in r.available_unaffected_artifact_identities
    # denominator for UPR excludes raw_data
    assert r.available_unaffected_count + 1 >= r.preserved_unaffected_count


# =====================================================================
# 13-16: UPR / ARR
# =====================================================================

def test_main_upr_correct() -> None:
    sc, g = build_main_fault_scenario("run_upr1")
    r = run_full_rerun_recovery(sc, g, CSV)
    # FULL re-runs all 14 available → preserved=0, UPR=0
    assert r.unaffected_preservation_rate == 0.0
    sc2, g2 = build_main_fault_scenario("run_upr1s")
    r2 = run_selective_recovery(sc2, g2)
    # SELECTIVE preserves all 14 → UPR=1
    assert r2.unaffected_preservation_rate == 1.0


def test_main_arr_correct() -> None:
    sc, g = build_main_fault_scenario("run_arr_a1")
    r = run_full_rerun_recovery(sc, g, CSV)
    # FULL never actively reuses a clean prereq (it re-creates them)
    assert r.active_reuse_rate == 0.0
    sc2, g2 = build_main_fault_scenario("run_arr_a1s")
    r2 = run_selective_recovery(sc2, g2)
    # SELECTIVE actively reuses all 3 financial metric v1s (its
    # execution prereqs are exactly those)
    assert r2.active_reuse_rate == 1.0


def test_case_b_upr_correct() -> None:
    sc, g = build_visits_fault_scenario("run_upr_b1")
    r = run_full_rerun_recovery(sc, g, CSV)
    assert r.unaffected_preservation_rate == 0.0
    sc2, g2 = build_visits_fault_scenario("run_upr_b1s")
    r2 = run_selective_recovery(sc2, g2)
    assert r2.unaffected_preservation_rate == 1.0


def test_case_b_arr_correct() -> None:
    sc, g = build_visits_fault_scenario("run_arr_b1")
    r = run_full_rerun_recovery(sc, g, CSV)
    assert r.active_reuse_rate == 0.0
    sc2, g2 = build_visits_fault_scenario("run_arr_b1s")
    r2 = run_selective_recovery(sc2, g2)
    assert r2.active_reuse_rate == 1.0


# =====================================================================
# 17-18: RAF / TAF
# =====================================================================

def test_raf_correct() -> None:
    sc, g = build_main_fault_scenario("run_raf1")
    r = run_full_rerun_recovery(sc, g, CSV)
    # 23 materializations / 7 required identities = ~3.29
    assert r.recompute_amplification_factor == pytest.approx(23 / 7)
    sc2, g2 = build_main_fault_scenario("run_raf1s")
    r2 = run_selective_recovery(sc2, g2)
    assert r2.recompute_amplification_factor == 1.0


def test_taf_correct() -> None:
    sc, g = build_main_fault_scenario("run_taf1")
    r = run_full_rerun_recovery(sc, g, CSV)
    # FULL = 14 task calls / 7 minimum = 2.0
    assert r.task_amplification_factor == pytest.approx(2.0)
    sc2, g2 = build_main_fault_scenario("run_taf1s")
    r2 = run_selective_recovery(sc2, g2)
    assert r2.task_amplification_factor == 1.0


# =====================================================================
# 19: every strategy produces an auditable classification list
# =====================================================================

def test_all_strategies_emit_classifications() -> None:
    c = run_one_round(case_id="case_a", run_id="run_aud_a")
    for strat in ("full", "coarse_checkpoint", "selective"):
        r = getattr(c, strat)
        assert isinstance(r.classifications, list)
        assert len(r.classifications) == r.artifact_materializations


# =====================================================================
# 20-21: 5-run stability for both cases
# =====================================================================

def test_main_5_runs_stable() -> None:
    rounds = [run_one_round(case_id="case_a", run_id=f"run_a11_{i+1}")
              for i in range(5)]
    first = rounds[0]
    for r in rounds[1:]:
        for strat in ("full", "coarse_checkpoint", "selective"):
            sa, sb = getattr(first, strat), getattr(r, strat)
            for k in (
                "required_materializations", "unnecessary_materializations",
                "recovery_materialization_precision", "uarr",
                "actively_reused_count", "preserved_unaffected_count",
                "skipped_irrelevant_count", "unaffected_preservation_rate",
                "active_reuse_rate",
                "recompute_amplification_factor", "task_amplification_factor",
            ):
                assert getattr(sa, k) == getattr(sb, k), (
                    f"main stability: {strat} {k}: {getattr(sa, k)} != {getattr(sb, k)}"
                )


def test_case_b_5_runs_stable() -> None:
    rounds = [run_one_round(case_id="case_b", run_id=f"run_b11_{i+1}")
              for i in range(5)]
    first = rounds[0]
    for r in rounds[1:]:
        for strat in ("full", "coarse_checkpoint", "selective"):
            sa, sb = getattr(first, strat), getattr(r, strat)
            for k in (
                "required_materializations", "unnecessary_materializations",
                "recovery_materialization_precision", "uarr",
                "actively_reused_count", "preserved_unaffected_count",
                "skipped_irrelevant_count", "unaffected_preservation_rate",
                "active_reuse_rate",
                "recompute_amplification_factor", "task_amplification_factor",
            ):
                assert getattr(sa, k) == getattr(sb, k), (
                    f"case_b stability: {strat} {k}: {getattr(sa, k)} != {getattr(sb, k)}"
                )


# =====================================================================
# 22: JSON roundtrip
# =====================================================================

def test_day11_json_roundtrip(tmp_path: Path) -> None:
    from experiments import run_main_case_comparison, run_multi_case_comparison
    out = run_main_case_comparison(
        n_repeats=2, output_path=str(tmp_path / "main.json"),
    )
    import json as _json
    parsed = _json.loads((tmp_path / "main.json").read_text(encoding="utf-8"))
    for s in ("full_rerun", "coarse_checkpoint", "selective_recovery"):
        d = parsed["strategies"][s]
        for k in (
            "required_materializations", "unnecessary_materializations",
            "recovery_materialization_precision", "uarr",
            "actively_reused_count", "preserved_unaffected_count",
            "skipped_irrelevant_count", "unaffected_preservation_rate",
            "active_reuse_rate",
            "recompute_amplification_factor", "task_amplification_factor",
            "classifications",
        ):
            assert k in d, f"{s}.{k} missing"


# =====================================================================
# 23: Day 1-10 tests still pass — covered by full pytest run.
# Extra: directly test derive_required_recovery_identities
# =====================================================================

def test_derive_required_recovery_identities_main() -> None:
    sc, g = build_main_fault_scenario("run_dri1")
    g2 = ArtifactGraph.from_store(sc.store, sc.run_id)
    closure = derive_target_recovery_closure(g2, sc.target_ref)
    required = derive_required_recovery_identities(
        contamination_invalidated=list(sc.contamination.invalidated),
        contamination_contaminated=list(sc.contamination.contaminated),
        target_ref=sc.target_ref,
        target_recovery_closure=closure,
    )
    assert "field_revenue_2025" in required
    assert "metric_revenue_growth" in required
    assert "metric_profit_margin" in required
    assert "metric_cost_ratio" in required
    assert "claim_financial_growth" in required
    assert "claim_report_financial_section" in required
    assert "report_merged" in required
    # raw_data must NOT be in required
    assert "raw_data" not in required


def test_derive_required_recovery_identities_case_b() -> None:
    sc, g = build_visits_fault_scenario("run_dri1b")
    g2 = ArtifactGraph.from_store(sc.store, sc.run_id)
    closure = derive_target_recovery_closure(g2, sc.target_ref)
    required = derive_required_recovery_identities(
        contamination_invalidated=list(sc.contamination.invalidated),
        contamination_contaminated=list(sc.contamination.contaminated),
        target_ref=sc.target_ref,
        target_recovery_closure=closure,
    )
    assert "field_visits_2025" in required
    assert "metric_conversion_rate" in required
    assert "claim_operation" in required
    assert "claim_report_operation_section" in required
    assert "report_merged" in required
    # NOT required (clean):
    assert "metric_order_growth" not in required
    assert "metric_revenue_growth" not in required
    assert "claim_financial_growth" not in required
