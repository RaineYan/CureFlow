"""
Day 10 demo — Experiment Hardening.

  - Case A (Revenue field fault)
  - Case B (Visits field fault)
  - 5 independent runs each
  - Corrected UARR / ArtifactReuseRate / ARP / RecomputeAmplification
  - Multi-case summary
"""
from __future__ import annotations

import json
from pathlib import Path

from experiments import (
    DEFINITIONS,
    run_main_case_comparison,
    run_multi_case_comparison,
)


def _hr(title: str) -> None:
    print()
    print("=" * 80)
    print(title)
    print("=" * 80)


def _row(label: str, full, coarse, sel) -> str:
    return f"  {label:<42} {str(full):>10} {str(coarse):>10} {str(sel):>10}"


def main() -> None:
    p_a = Path("experiments/results/day10_main_case_v2.json")
    p_b = Path("experiments/results/day10_case_b.json")
    p_sum = Path("experiments/results/day10_summary.json")

    out_a = run_main_case_comparison(n_repeats=5, output_path=str(p_a))
    from experiments.harness import _run_case_n
    out_b = _run_case_n(case_id="case_b", n_repeats=5, output_path=str(p_b))
    out_sum = run_multi_case_comparison(
        case_specs=["case_a", "case_b"], n_repeats=5, output_path=str(p_sum),
    )

    for case_label, out in (("CASE A — Revenue Field Fault", out_a),
                            ("CASE B — Visits Field Fault", out_b)):
        _hr(case_label)
        s = out["strategies"]
        m = out["metrics"]
        f, c, sel = s["full_rerun"], s["coarse_checkpoint"], s["selective_recovery"]
        print(_row("metric", "FULL", "COARSE", "SELECTIVE"))
        print("  " + "-" * 70)
        print(_row("success", f["success"], c["success"], sel["success"]))
        print(_row("matches_golden", f["matches_golden_output"],
                   c["matches_golden_output"], sel["matches_golden_output"]))
        print(_row("task_calls", f["task_calls"], c["task_calls"], sel["task_calls"]))
        print(_row("tool_task_calls", f["tool_task_calls"],
                   c["tool_task_calls"], sel["tool_task_calls"]))
        print(_row("llm_task_calls", f["llm_task_calls"],
                   c["llm_task_calls"], sel["llm_task_calls"]))
        print(_row("artifact_materializations", f["artifact_materializations"],
                   c["artifact_materializations"], sel["artifact_materializations"]))
        print(_row("tool_artifact_materializations",
                   f["tool_artifact_materializations"],
                   c["tool_artifact_materializations"],
                   sel["tool_artifact_materializations"]))
        print(_row("llm_artifact_materializations",
                   f["llm_artifact_materializations"],
                   c["llm_artifact_materializations"],
                   sel["llm_artifact_materializations"]))
        print(_row("llm_calls", f["llm_calls"], c["llm_calls"], sel["llm_calls"]))
        print(_row("reused_unaffected (n)", f["reused_artifact_count"],
                   c["reused_artifact_count"], sel["reused_artifact_count"]))
        print(_row("available_unaffected (n)", f["available_unaffected_count"],
                   c["available_unaffected_count"], sel["available_unaffected_count"]))
        print(_row("unaffected_recomputed (n)", f["unaffected_recomputed_count"],
                   c["unaffected_recomputed_count"], sel["unaffected_recomputed_count"]))
        print(_row("affected_id_count", f["affected_artifact_identity_count"],
                   c["affected_artifact_identity_count"],
                   sel["affected_artifact_identity_count"]))
        print(_row("ArtifactReuseRate", f"{f['artifact_reuse_rate']*100:.1f}%",
                   f"{c['artifact_reuse_rate']*100:.1f}%",
                   f"{sel['artifact_reuse_rate']*100:.1f}%"))
        print(_row("UARR", f"{f['uarr']*100:.1f}%",
                   f"{c['uarr']*100:.1f}%", f"{sel['uarr']*100:.1f}%"))
        print(_row("ARP", f"{f['affected_recovery_precision_arp']*100:.1f}%",
                   f"{c['affected_recovery_precision_arp']*100:.1f}%",
                   f"{sel['affected_recovery_precision_arp']*100:.1f}%"))
        print(_row("RecomputeAmplification",
                   f"{f['recompute_amplification']:.2f}",
                   f"{c['recompute_amplification']:.2f}",
                   f"{sel['recompute_amplification']:.2f}"))
        print(_row("synthetic_total_tokens",
                   f["synthetic_total_tokens"],
                   c["synthetic_total_tokens"],
                   sel["synthetic_total_tokens"]))
        print(_row("local_wall_clock_ms",
                   f"{f['local_wall_clock_ms']:.2f}",
                   f"{c['local_wall_clock_ms']:.2f}",
                   f"{sel['local_wall_clock_ms']:.2f}"))

        _hr(f"{case_label} — STRUCTURAL DELTAS")
        print(_row("llm_call_reduction_vs_full",
                   f"{m['full_rerun']['llm_call_reduction_vs_full']*100:.1f}%",
                   f"{m['coarse_checkpoint']['llm_call_reduction_vs_full']*100:.1f}%",
                   f"{m['selective_recovery']['llm_call_reduction_vs_full']*100:.1f}%"))
        print(_row("materialization_reduction_vs_full",
                   f"{m['full_rerun']['materialization_reduction_vs_full']*100:.1f}%",
                   f"{m['coarse_checkpoint']['materialization_reduction_vs_full']*100:.1f}%",
                   f"{m['selective_recovery']['materialization_reduction_vs_full']*100:.1f}%"))

        _hr(f"{case_label} — 5-RUN STABILITY")
        for r in out["stability"]["per_round"]:
            print(f"  {r['case_id']}: full={r['full']['llm_calls']}L/{r['full']['artifact_materializations']}M, "
                  f"coarse={r['coarse_checkpoint']['llm_calls']}L/{r['coarse_checkpoint']['artifact_materializations']}M, "
                  f"sel={r['selective']['llm_calls']}L/{r['selective']['artifact_materializations']}M  "
                  f"golden={r['selective']['golden_match']}")
        print(f"  structural_identical_across_runs = "
              f"{out['stability']['structural_identical_across_runs']}")

    _hr("MULTI-CASE MACRO AVERAGE (unweighted across cases)")
    macro = out_sum["macro_average"]
    for s in ("full_rerun", "coarse_checkpoint", "selective_recovery"):
        m = macro[s]
        print(f"  {s:<22} task_calls={m['task_calls']:.1f}  mat={m['artifact_materializations']:.1f}  "
              f"llm={m['llm_calls']:.1f}  reuse={m['artifact_reuse_rate']*100:.1f}%  "
              f"uarr={m['uarr']*100:.1f}%  arp={m['affected_recovery_precision_arp']*100:.1f}%  "
              f"amp={m['recompute_amplification']:.2f}")

    _hr("MACHINE-READABLE JSON FILES WRITTEN")
    for p in (p_a, p_b, p_sum):
        print(f"  {p}  ({p.stat().st_size} bytes)")

    _hr("DAYS 1-10 PYTEST SUMMARY")
    print("  Day 1 = 21 tests  (artifacts + store)")
    print("  Day 2 = 18 tests  (workflow tasks)")
    print("  Day 3 = 16 tests  (decorator)")
    print("  Day 4 = 22 tests  (graph)")
    print("  Day 5 = 22 tests  (fake LLM + claim tasks)")
    print("  Day 6 = 24 tests  (3-branch workflow)")
    print("  Day 7 = 23 tests  (fault injection + contamination)")
    print("  Day 8 = 33 tests  (selective recovery)")
    print("  Day 9 = 30 tests  (3-strategy harness v1)")
    print("  Day 10 = 32 tests (event-based metrics + Case B)")
    print("  ----")
    print("  TOTAL = 241 tests")


if __name__ == "__main__":
    main()
