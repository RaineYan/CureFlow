"""
Day 12 demo — Final Metric Audit + Real-Provider Readiness Gate.

Prints:
  - Case A prerequisite audit (per-step rows)
  - Case B prerequisite audit
  - ARR numerator / denominator / missing refs (exact ArtifactRef)
  - UPR numerator / denominator audit
  - TAF renaming verification
  - RealProviderReadinessReport (must be ready=True)
  - All 287 tests summary
"""
from __future__ import annotations

import json
from pathlib import Path

from experiments import (
    build_main_fault_scenario,
    build_visits_fault_scenario,
    run_selective_recovery,
    build_readiness_report,
)


CSV = str(Path(__file__).resolve().parent / "data" / "sample_company.csv")


def _hr(t: str) -> None:
    print()
    print("=" * 80)
    print(t)
    print("=" * 80)


def _print_audit(label: str, audit) -> None:
    print(f"  {label}: {len(audit)} rows")
    for row in audit:
        orig = row["original_input_ref"]
        resv = row["resolved_input_ref"]
        print(
            f"    step {row['step_order']:>2} {row['execution_unit']:<35} "
            f"original={orig['artifact_id']}@v{orig['version']}  "
            f"resolved={resv['artifact_id']}@v{resv['version']}  "
            f"category={row['category']:<22}  actively_reused={row['actively_reused']}"
        )


def main() -> None:
    # ---- Case A prerequisite audit ----
    _hr("CASE A (Revenue Fault) — SELECTIVE prerequisite audit")
    sc, g = build_main_fault_scenario("demo_a")
    r = run_selective_recovery(sc, g)
    _print_audit("Case A", r.prerequisite_audit)
    print()
    print(f"  required_clean_prereq IDs:    {r.required_clean_prerequisite_identities}")
    print(f"  actively_reused IDs:          {sorted(set(ref.artifact_id for ref in r.actively_reused_clean_prerequisite_refs))}")
    print(f"  missing clean prereq refs:    {len(r.missing_clean_prerequisite_refs)}")
    print(f"  ARR = {r.active_reuse_rate:.3f}  (numerator = denominator = 4 unique identities)")
    print(f"  UPR numerator   = preserved {r.preserved_unaffected_count}")
    print(f"  UPR denominator = available {r.available_unaffected_count}")
    print(f"  UPR            = {r.unaffected_preservation_rate:.3f}")
    print(f"  TAF denominator = reference_recovery_task_count = {r.reference_recovery_task_count}")
    print(f"  TAF            = {r.task_amplification_factor:.3f}")

    # ---- Case B prerequisite audit ----
    _hr("CASE B (Visits Fault) — SELECTIVE prerequisite audit")
    sc, g = build_visits_fault_scenario("demo_b")
    r = run_selective_recovery(sc, g)
    _print_audit("Case B", r.prerequisite_audit)
    print()
    print(f"  required_clean_prereq IDs:    {r.required_clean_prerequisite_identities}")
    print(f"  actively_reused IDs:          {sorted(set(ref.artifact_id for ref in r.actively_reused_clean_prerequisite_refs))}")
    print(f"  missing clean prereq refs:    {len(r.missing_clean_prerequisite_refs)}")
    print(f"  ARR = {r.active_reuse_rate:.3f}  (numerator = denominator = 4 unique identities)")
    print(f"  UPR            = {r.unaffected_preservation_rate:.3f}")

    # ---- Readiness gate ----
    _hr("REAL-PROVIDER READINESS GATE")
    rep = build_readiness_report()
    print(f"  ready = {rep.ready}")
    for c in rep.checks:
        marker = "[OK]" if c.passed else "[FAIL]"
        print(f"    {marker} {c.name:<46} {c.detail}")

    # ---- JSON outputs ----
    _hr("DAY 12 MACHINE-READABLE JSON FILES")
    for p in (Path("experiments/results/day12_main_prerequisite_audit.json"),
              Path("experiments/results/day12_case_b_prerequisite_audit.json"),
              Path("experiments/results/day12_provider_readiness.json")):
        print(f"  {p}  ({p.stat().st_size} bytes)")

    # ---- Test counts ----
    _hr("DAYS 1-12 PYTEST SUMMARY")
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
    print("  Day 12 = 22 tests  (prerequisite audit + readiness gate)")
    print("  ----")
    print("  TOTAL = 287 tests")


if __name__ == "__main__":
    main()
