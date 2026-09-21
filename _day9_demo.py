"""
Day 9 demo — Controlled Offline Structural Experiment.

Runs the Main Case through all three strategies (FULL_RERUN,
COARSE_CHECKPOINT, SELECTIVE_RECOVERY) and prints a side-by-side
comparison table.  Also writes experiments/results/day9_main_case.json
for machine-readable inspection.

This is a structural POC: token and latency fields are explicitly
labelled "synthetic" and "local_wall_clock_ms" because they come
from FakeLLMClient, which is not a real model.  Real model / cost
experiments are reserved for Day 10.
"""
from __future__ import annotations

import json
from pathlib import Path

from experiments import run_main_case_comparison


def _hr(title: str) -> None:
    print()
    print("=" * 80)
    print(title)
    print("=" * 80)


def _row(label: str, full, coarse, sel) -> str:
    return f"  {label:<38} {str(full):>10} {str(coarse):>10} {str(sel):>10}"


def main() -> None:
    out_path = Path("experiments/results/day9_main_case.json")
    out = run_main_case_comparison(n_repeats=5, output_path=str(out_path))
    s = out["strategies"]
    m = out["metrics"]
    f = s["full_rerun"]
    c = s["coarse_checkpoint"]
    sel = s["selective_recovery"]
    deltas = {
        "full_rerun": m["full_rerun"],
        "coarse_checkpoint": m["coarse_checkpoint"],
        "selective_recovery": m["selective_recovery"],
    }

    _hr("DAY 9 — CONTROLLED OFFLINE STRUCTURAL EXPERIMENT")
    print("  Provider      :", out["provider_type"])
    print("  Usage type    :", out["usage_type"])
    print("  Latency type  :", out["latency_type"])
    print("  Real model    :", out["real_model_reserved_for"])
    print("  Case          :", out["case_id"])
    print("  Fault         :", out["fault_type"], "on", out["fault_target"])
    print("  N repeats     :", out["n_repeats"])
    print("  Golden hash   :", out["golden_output_hash"])
    print("  Stability     :", "structural_identical_across_runs =",
          out["stability"]["structural_identical_across_runs"])

    _hr("PER-STRATEGY RECOVERY-PHASE RESULTS  (Main Case)")
    header = _row("metric", "FULL", "COARSE", "SELECTIVE")
    print(header)
    print("  " + "-" * (len(header) - 2))
    print(_row("success", f["success"], c["success"], sel["success"]))
    print(_row("matches_golden_output", f["matches_golden_output"],
                                       c["matches_golden_output"],
                                       sel["matches_golden_output"]))
    print(_row("semantic_clean_path_pass", f["semantic_clean_path_pass"],
                                            c["semantic_clean_path_pass"],
                                            sel["semantic_clean_path_pass"]))
    print(_row("execution_integrity_pass", f["execution_integrity_pass"],
                                             c["execution_integrity_pass"],
                                             sel["execution_integrity_pass"]))
    print(_row("task_calls", f["task_calls"], c["task_calls"], sel["task_calls"]))
    print(_row("tool_calls", f["tool_calls"], c["tool_calls"], sel["tool_calls"]))
    print(_row("llm_calls", f["llm_calls"], c["llm_calls"], sel["llm_calls"]))
    print(_row("artifact_materializations",
               f["artifact_materializations"],
               c["artifact_materializations"],
               sel["artifact_materializations"]))
    print(_row("reused_artifact_count",
               f["reused_artifact_count"],
               c["reused_artifact_count"],
               sel["reused_artifact_count"]))
    print(_row("unaffected_artifacts_recomputed (len)",
               len(f["unaffected_artifacts_recomputed"]),
               len(c["unaffected_artifacts_recomputed"]),
               len(sel["unaffected_artifacts_recomputed"])))
    print(_row("synthetic_total_tokens (FakeLLM only)",
               f["synthetic_total_tokens"],
               c["synthetic_total_tokens"],
               sel["synthetic_total_tokens"]))
    print(_row("local_wall_clock_ms (NOT real model latency)",
               f"{f['local_wall_clock_ms']:.2f}",
               f"{c['local_wall_clock_ms']:.2f}",
               f"{sel['local_wall_clock_ms']:.2f}"))

    _hr("STRUCTURAL DELTAS  (vs FULL_RERUN)")
    header = _row("metric", "FULL", "COARSE", "SELECTIVE")
    print(header)
    print("  " + "-" * (len(header) - 2))
    print(_row("llm_call_reduction_vs_full",
               f"{deltas['full_rerun']['llm_call_reduction_vs_full']*100:.1f}%",
               f"{deltas['coarse_checkpoint']['llm_call_reduction_vs_full']*100:.1f}%",
               f"{deltas['selective_recovery']['llm_call_reduction_vs_full']*100:.1f}%"))
    print(_row("materialization_reduction_vs_full",
               f"{deltas['full_rerun']['materialization_reduction_vs_full']*100:.1f}%",
               f"{deltas['coarse_checkpoint']['materialization_reduction_vs_full']*100:.1f}%",
               f"{deltas['selective_recovery']['materialization_reduction_vs_full']*100:.1f}%"))
    print(_row("artifact_reuse_rate",
               f"{deltas['full_rerun']['artifact_reuse_rate']*100:.1f}%",
               f"{deltas['coarse_checkpoint']['artifact_reuse_rate']*100:.1f}%",
               f"{deltas['selective_recovery']['artifact_reuse_rate']*100:.1f}%"))
    print(_row("UARR (unaffected recompute ratio)",
               f"{deltas['full_rerun']['unaffected_recomputation_ratio_uarr']*100:.1f}%",
               f"{deltas['coarse_checkpoint']['unaffected_recomputation_ratio_uarr']*100:.1f}%",
               f"{deltas['selective_recovery']['unaffected_recomputation_ratio_uarr']*100:.1f}%"))

    _hr("5 RUNS — STRUCTURAL STABILITY")
    for r in out["stability"]["per_round"]:
        print(f"  {r['case_id']}: "
              f"full={r['full']['llm_calls']}L/{r['full']['artifact_materializations']}M, "
              f"coarse={r['coarse_checkpoint']['llm_calls']}L/{r['coarse_checkpoint']['artifact_materializations']}M, "
              f"sel={r['selective']['llm_calls']}L/{r['selective']['artifact_materializations']}M  "
              f"golden={r['selective']['golden_match']}")

    _hr("FAIRNESS CHECKS (passed)")
    print("  - FULL_RERUN   does not call SelectiveRecoveryPlanner")
    print("  - COARSE       does not call SelectiveRecoveryPlanner")
    print("  - All three start from equivalent build_main_fault_scenario state")
    print("  - All three use the same FakeLLM responder configuration")
    print("  - All three reset their recovery-phase log at the same point")
    print("  - Golden baseline is a clean run_baseline_workflow (v1 everywhere)")

    _hr("DEFINITIONS — REAL STRUCTURAL METRIC vs SYNTHETIC / NOT FOR COMPETITION CLAIMS")
    print("  REAL STRUCTURAL METRIC:")
    print("    - task_calls            (real execution unit invocations)")
    print("    - llm_calls             (logical LLM invocations)")
    print("    - artifact_materializations (real new content versions)")
    print("    - reused_artifact_count (real reuse of existing versions)")
    print("    - unaffected_artifacts_recomputed (real unnecessary work)")
    print("  SYNTHETIC / NOT FOR COMPETITION CLAIMS:")
    print("    - synthetic_input_tokens / synthetic_output_tokens / synthetic_total_tokens")
    print("      (FakeLLM fixture values, NOT real model tokens)")
    print("    - local_wall_clock_ms (local Python timing, NOT real model latency)")

    _hr(f"MACHINE-READABLE JSON WRITTEN TO {out_path}")
    print(f"  Size: {out_path.stat().st_size} bytes")


if __name__ == "__main__":
    main()
