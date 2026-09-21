"""
Day 11 demo — Metric Semantics Finalization.

  - Every Recovery-Phase materialization is classified into exactly one
    of REQUIRED_RECOVERY or UNNECESSARY_RECOMPUTATION.
  - The partition invariant (req + unn == total) and the complement
    invariant (RMP + UARR == 1) are demonstrated live.
  - The 2 "missing" artifacts from Day 10's metric are identified
    explicitly in the FULL classification table.
  - Three sets (ACTIVELY_REUSED, PRESERVED_UNAFFECTED, SKIPPED_IRRELEVANT)
    are shown side by side for SELECTIVE.
  - Both cases, both runs, 5x stability.
"""
from __future__ import annotations

import json
from pathlib import Path

from experiments import (
    build_main_fault_scenario,
    build_visits_fault_scenario,
    run_full_rerun_recovery,
    run_main_case_comparison,
    run_multi_case_comparison,
    run_one_round,
    run_selective_recovery,
)
from experiments.harness import _run_case_n


CSV = str(Path(__file__).resolve().parent / "data" / "sample_company.csv")


def _hr(title: str) -> None:
    print()
    print("=" * 80)
    print(title)
    print("=" * 80)


def _print_classifications(label: str, classifications) -> None:
    print(f"  {label}: {len(classifications)} materializations")
    for i, c in enumerate(classifications, 1):
        ref = c["produced_ref"]
        marker = "REQ" if c["classification"] == "REQUIRED_RECOVERY" else "UNN"
        print(f"    {i:>2}. [{marker}] {ref['artifact_id']:<35} v{ref['version']}")


def main() -> None:
    p_a = Path("experiments/results/day11_main_case_metrics_final.json")
    p_b = Path("experiments/results/day11_case_b_metrics_final.json")
    p_sum = Path("experiments/results/day11_summary_metrics_final.json")

    # Run the harnesses
    out_a = run_main_case_comparison(n_repeats=5, output_path=str(p_a))
    out_b = _run_case_n(case_id="case_b", n_repeats=5, output_path=str(p_b))
    out_sum = run_multi_case_comparison(
        case_specs=["case_a", "case_b"], n_repeats=5, output_path=str(p_sum),
    )

    # ---- Case A FULL classifications ----
    _hr("CASE A (Revenue Fault) — FULL_RERUN classifications")
    sc, g = build_main_fault_scenario("demo_a_full")
    r = run_full_rerun_recovery(sc, g, CSV)
    _print_classifications("FULL_RERUN", r.classifications)
    print()
    print(f"  Partition: required={r.required_materializations}  "
          f"unnecessary={r.unnecessary_materializations}  "
          f"total={r.artifact_materializations}")
    print(f"  RMP={r.recovery_materialization_precision:.3f}  "
          f"UARR={r.uarr:.3f}  "
          f"RMP+UARR={r.recovery_materialization_precision + r.uarr:.3f}")
    print(f"  The 2 Day-10 'missing' artifacts are: " +
          ", ".join(sorted(
              {c["identity"] for c in r.classifications
               if c["classification"] == "UNNECESSARY_RECOMPUTATION"
               and c["identity"] not in r.affected_artifact_identities
               and c["identity"] not in r.available_unaffected_artifact_identities}
          )))

    # ---- Case A SELECTIVE classification summary ----
    _hr("CASE A — SELECTIVE_RECOVERY — three sets")
    sc, g = build_main_fault_scenario("demo_a_sel")
    s = run_selective_recovery(sc, g)
    print(f"  materializations:   {s.artifact_materializations}")
    print(f"  required:           {s.required_materializations}  "
          f"unnecessary: {s.unnecessary_materializations}  "
          f"RMP+UARR = {s.recovery_materialization_precision + s.uarr:.3f}")
    print()
    print(f"  ACTIVELY_REUSED ({s.actively_reused_count}): "
          f"{s.actively_reused_artifact_identities}")
    print(f"  PRESERVED_UNAFFECTED ({s.preserved_unaffected_count}): "
          f"{s.preserved_unaffected_artifact_identities}")
    print(f"  SKIPPED_IRRELEVANT  ({s.skipped_irrelevant_count}): "
          f"{s.skipped_irrelevant_artifact_identities}")
    print()
    print(f"  UPR = {s.unaffected_preservation_rate:.3f}  "
          f"ARR = {s.active_reuse_rate:.3f}  "
          f"RAF = {s.recompute_amplification_factor:.2f}  "
          f"TAF = {s.task_amplification_factor:.2f}")

    # ---- Case B FULL classifications ----
    _hr("CASE B (Visits Fault) — FULL_RERUN classifications")
    sc, g = build_visits_fault_scenario("demo_b_full")
    r = run_full_rerun_recovery(sc, g, CSV)
    _print_classifications("FULL_RERUN", r.classifications)
    print()
    print(f"  Partition: required={r.required_materializations}  "
          f"unnecessary={r.unnecessary_materializations}  "
          f"total={r.artifact_materializations}")
    print(f"  RMP={r.recovery_materialization_precision:.3f}  "
          f"UARR={r.uarr:.3f}  "
          f"RMP+UARR={r.recovery_materialization_precision + r.uarr:.3f}")

    # ---- Case B SELECTIVE summary ----
    _hr("CASE B — SELECTIVE_RECOVERY — three sets")
    sc, g = build_visits_fault_scenario("demo_b_sel")
    s = run_selective_recovery(sc, g)
    print(f"  materializations:   {s.artifact_materializations}")
    print(f"  required:           {s.required_materializations}  "
          f"unnecessary: {s.unnecessary_materializations}  "
          f"RMP+UARR = {s.recovery_materialization_precision + s.uarr:.3f}")
    print()
    print(f"  ACTIVELY_REUSED ({s.actively_reused_count}): "
          f"{s.actively_reused_artifact_identities}")
    print(f"  PRESERVED_UNAFFECTED ({s.preserved_unaffected_count}): "
          f"{s.preserved_unaffected_artifact_identities}")
    print(f"  SKIPPED_IRRELEVANT  ({s.skipped_irrelevant_count}): "
          f"{s.skipped_irrelevant_artifact_identities}")
    print()
    print(f"  UPR = {s.unaffected_preservation_rate:.3f}  "
          f"ARR = {s.active_reuse_rate:.3f}  "
          f"RAF = {s.recompute_amplification_factor:.2f}  "
          f"TAF = {s.task_amplification_factor:.2f}")

    # ---- Side-by-side comparison ----
    _hr("CASE A + CASE B — side-by-side")
    for case_id, out in (("Case A", out_a), ("Case B", out_b)):
        s = out["strategies"]
        m = out["metrics"]
        f, c, sel = s["full_rerun"], s["coarse_checkpoint"], s["selective_recovery"]
        print(f"\n  {case_id}:")
        print(f"    metric                                     FULL     COARSE  SELECT")
        print(f"    materializations                          "
              f"{f['artifact_materializations']:>4}    "
              f"{c['artifact_materializations']:>4}    "
              f"{sel['artifact_materializations']:>4}")
        print(f"    required / unnecessary                    "
              f"{f['required_materializations']:>3} / {f['unnecessary_materializations']:<3}"
              f"  {c['required_materializations']:>3} / {c['unnecessary_materializations']:<3}"
              f"  {sel['required_materializations']:>3} / {sel['unnecessary_materializations']:<3}")
        print(f"    RMP / UARR                                "
              f"{f['recovery_materialization_precision']*100:>4.1f}% / {f['uarr']*100:>4.1f}%"
              f"  {c['recovery_materialization_precision']*100:>4.1f}% / {c['uarr']*100:>4.1f}%"
              f"  {sel['recovery_materialization_precision']*100:>4.1f}% / {sel['uarr']*100:>4.1f}%")
        print(f"    UPR / ARR                                 "
              f"{f['unaffected_preservation_rate']*100:>4.1f}% / {f['active_reuse_rate']*100:>4.1f}%"
              f"  {c['unaffected_preservation_rate']*100:>4.1f}% / {c['active_reuse_rate']*100:>4.1f}%"
              f"  {sel['unaffected_preservation_rate']*100:>4.1f}% / {sel['active_reuse_rate']*100:>4.1f}%")
        print(f"    RAF / TAF                                 "
              f"{f['recompute_amplification_factor']:>4.2f} / {f['task_amplification_factor']:>4.2f}"
              f"  {c['recompute_amplification_factor']:>4.2f} / {c['task_amplification_factor']:>4.2f}"
              f"  {sel['recompute_amplification_factor']:>4.2f} / {sel['task_amplification_factor']:>4.2f}")

    # ---- 5-run stability ----
    _hr("5-RUN STABILITY (RMP, UARR, UPR, ARR, RAF, TAF)")
    for case_id, label in (("case_a", "Case A"), ("case_b", "Case B")):
        rounds = [run_one_round(case_id=case_id, run_id=f"demo_{case_id}_{i+1}")
                  for i in range(5)]
        first = rounds[0]
        ok = True
        for r in rounds[1:]:
            for strat in ("full", "coarse_checkpoint", "selective"):
                sa, sb = getattr(first, strat), getattr(r, strat)
                for k in ("required_materializations", "unnecessary_materializations",
                          "recovery_materialization_precision", "uarr",
                          "unaffected_preservation_rate", "active_reuse_rate",
                          "recompute_amplification_factor", "task_amplification_factor"):
                    if getattr(sa, k) != getattr(sb, k):
                        ok = False
        print(f"  {label}: structural_identical_across_runs = {ok}")

    # ---- Multi-case summary ----
    _hr("MULTI-CASE MACRO AVERAGE (Day 11 metrics)")
    macro = out_sum["macro_average"]
    for s in ("full_rerun", "coarse_checkpoint", "selective_recovery"):
        m = macro[s]
        print(f"  {s:<22} req={m['required_materializations']:.1f}  "
              f"unn={m['unnecessary_materializations']:.1f}  "
              f"rmp={m['recovery_materialization_precision']*100:.1f}%  "
              f"uarr={m['uarr']*100:.1f}%  "
              f"upr={m['unaffected_preservation_rate']*100:.1f}%  "
              f"arr={m['active_reuse_rate']*100:.1f}%  "
              f"raf={m['recompute_amplification_factor']:.2f}  "
              f"taf={m['task_amplification_factor']:.2f}")

    _hr("MACHINE-READABLE JSON FILES")
    for p in (p_a, p_b, p_sum):
        print(f"  {p}  ({p.stat().st_size} bytes)")

    _hr("DAYS 1-11 PYTEST SUMMARY")
    print("  Day 1  = 21 tests  (artifacts + store)")
    print("  Day 2  = 18 tests  (workflow tasks)")
    print("  Day 3  = 16 tests  (decorator)")
    print("  Day 4  = 22 tests  (graph)")
    print("  Day 5  = 22 tests  (fake LLM + claim tasks)")
    print("  Day 6  = 24 tests  (3-branch workflow)")
    print("  Day 7  = 23 tests  (fault injection + contamination)")
    print("  Day 8  = 33 tests  (selective recovery)")
    print("  Day 9  = 30 tests  (3-strategy harness v1)")
    print("  Day 10 = 32 tests  (event-based metrics + Case B)")
    print("  Day 11 = 24 tests  (classification + UPR/ARR/RAF/TAF)")
    print("  ----")
    print("  TOTAL = 265 tests")


if __name__ == "__main__":
    main()
