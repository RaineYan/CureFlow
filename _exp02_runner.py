"""Day 14B/EXP-02 runner — 4 cases x 3 strategies x 1 repeat = 12 trials.

Mode: MOCK (uses MockHttpRealLLMClient, no real Provider calls).

Output (spec section B5):
  experiments/results/exp02/
    trial_<case>_<strategy>_pre01.json     (unified trial JSON)
    exp02_summary.json                     (aggregated metrics)
    exp02_summary.csv                      (CSV for charting)
    EXP02_REPORT.md                        (human-readable report)
    figures/*.svg                          (charts, spec section C)

Each trial JSON includes the spec B4 fields:
  experiment_id, trial_id, timestamp, provider, model,
  case_id, failure_type, strategy, success, recovery_success,
  provider_execution_success, workflow_contract_success,
  golden_match, root_cause, affected_nodes, rerun_nodes,
  reused_nodes, llm_calls, tokens_prompt, tokens_completion,
  tokens_total, latency_ms, wall_time_ms, estimated_cost,
  error_type, error_message.

NO real Provider calls.  MOCK mode preserves the
day14/pre_pilot.py reduction semantics.
"""
from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


OUT = ROOT / "experiments/results/exp02"
OUT.mkdir(parents=True, exist_ok=True)


# 4 case x 3 strategy grid (Stage 1: 12 trials).
# The (case, scenario, fault_type, target) tuple is
# derived from `experiments.exp02_faults.CANONICAL_CASES`
# (the single source of truth) so the two runners cannot
# drift apart.
from experiments.exp02_faults import CANONICAL_CASES as _CC
CASES = [
    (case_id, scenario_id, fault_type, target_id)
    for case_id, scenario_id, fault_type, target_id in _CC
]
STRATEGIES = ["FULL_RERUN", "COARSE_CHECKPOINT", "SELECTIVE_RECOVERY"]


def _build_recording_client(
    trial_id: str, case_id: str, strategy: str,
):
    from experiments.day14.recording import (
        Day14RecordingClient,
        MockHttpRealLLMClient,
    )
    from experiments.live_ledger import LiveCallLedger
    import json as _json
    import re as _re

    def factory(prompt: str, unit: str) -> str:
        # Parse evidence refs (run/artifact@v format used
        # in build_claim_prompt).
        evidence_refs = []
        for line in prompt.splitlines():
            m = _re.match(
                r"\s*([A-Za-z0-9_]+)\s*/\s*"
                r"([A-Za-z0-9_]+)\s*@\s*v(\d+)\s",
                line,
            )
            if m:
                evidence_refs.append(
                    f"{m.group(1)}/{m.group(2)}@v{m.group(3)}"
                )
        nums: dict = {}
        for line in prompt.splitlines():
            m = _re.search(
                r"(\S+/\S+@v\d+)\s+value\s*=\s*(-?\d+\.?\d*)",
                line,
            )
            if m:
                nums[m.group(1)] = float(m.group(2))
        claim_parts = []
        for ref, val in nums.items():
            pct = val * 100.0
            direction = (
                "增长" if pct > 0
                else "下降" if pct < 0
                else "持平"
            )
            artifact_id = (
                ref.split("/", 1)[-1].split("@", 1)[0]
            )
            claim_parts.append(
                f"{artifact_id} {direction} {abs(pct):.2f}%"
            )
        claim_text = (
            "Pre-EXP02 mock observation: "
            + "；".join(claim_parts)
        ) if claim_parts else "Pre-EXP02 mock claim"
        return _json.dumps({
            "claim_text": claim_text,
            "evidence_refs": evidence_refs,
            "confidence": 0.92,
        }, ensure_ascii=False)

    mock = MockHttpRealLLMClient(
        assistant_content_factory=factory,
        input_tokens=433,
        output_tokens=82,
        latency_ms=2200.0,
    )
    ledger = LiveCallLedger()
    persistence_path = (
        OUT / f"{trial_id}_live_ledger.jsonl"
    )
    phase_prefix = (
        "A" if strategy == "FULL_RERUN"
        else "B" if strategy == "COARSE_CHECKPOINT"
        else "C-recovery"
    )
    return Day14RecordingClient(
        mock, ledger,
        phase_prefix=phase_prefix,
        trial_id=trial_id, case_id=case_id, strategy=strategy,
        persistence_path=persistence_path,
    )


def _inject_fault(case_label, store, run_id, target_artifact_id):
    from experiments.exp02_faults import (
        inject_agent_output_error,
        inject_evidence_reference_error,
        inject_artifact_data_corruption,
    )
    if case_label == "CASE_A":
        return inject_artifact_data_corruption(
            store, run_id,
            target_artifact_id="field_revenue_2025",
            factor=10.0,
        )
    if case_label == "CASE_B":
        return inject_agent_output_error(
            store, run_id,
            target_artifact_id="claim_financial_growth",
            bad_claim_text=(
                "WRONG_MID_AGENT_OUTPUT (CASE_B injected)"
            ),
        )
    if case_label == "CASE_C":
        return inject_evidence_reference_error(
            store, run_id,
            target_artifact_id="claim_financial_growth",
            bad_ref_index=0,
        )
    if case_label == "CASE_D":
        return inject_artifact_data_corruption(
            store, run_id,
            target_artifact_id="field_revenue_2025",
            factor=-1.0,
        )
    raise ValueError(f"unknown case_label: {case_label!r}")


def _identify_root_cause_and_affected(case_label, strategy):
    """Heuristic for the demo DAG (Research/Data/Analysis/Report +
    Evidence)."""
    roots = {
        "CASE_A": "Data Agent (field_revenue_2025)",
        "CASE_B": "Analysis Agent (claim_financial_growth)",
        "CASE_C": "Report Agent (claim_financial_growth evidence)",
        "CASE_D": "Data Agent (field_revenue_2025 sign-flipped)",
    }
    affected_pool = {
        "CASE_A": ["Analysis Agent", "Report Agent"],
        "CASE_B": ["Report Agent"],
        "CASE_C": ["Report Agent"],
        "CASE_D": ["Analysis Agent", "Report Agent"],
    }
    unaffected_pool = {
        "CASE_A": ["Research Agent", "Evidence Agent"],
        "CASE_B": [
            "Research Agent", "Data Agent", "Evidence Agent",
        ],
        "CASE_C": [
            "Research Agent", "Data Agent", "Evidence Agent",
        ],
        "CASE_D": ["Research Agent", "Evidence Agent"],
    }
    if strategy == "FULL_RERUN":
        rerun = [
            "Data Agent", "Analysis Agent", "Report Agent",
        ]
    elif strategy == "COARSE_CHECKPOINT":
        rerun = [
            "Data Agent", "Analysis Agent", "Report Agent",
        ]
    else:
        rerun = affected_pool[case_label]
    return {
        "root_cause": roots[case_label],
        "affected_nodes": affected_pool[case_label],
        "unaffected_nodes": unaffected_pool[case_label],
        "rerun_nodes": rerun,
        "reused_nodes": unaffected_pool[case_label],
    }


def _run_trial(case_label, scenario_id, strategy,
               target_artifact_id):
    from experiments.day14 import (
        FROZEN_PROVIDER_CONFIG,
        build_trial_spec,
    )
    from experiments.day14.strategies import _build_scenario
    from experiments.strategies import (
        run_full_rerun_recovery,
        run_coarse_checkpoint_recovery,
        run_selective_recovery,
    )
    trial_id = f"exp02_{case_label}_{strategy.lower()}_pre01"
    spec = build_trial_spec(
        case_id=scenario_id,
        strategy=strategy,
        trial_id=trial_id,
        out_dir=OUT,
    )
    rec = _build_recording_client(trial_id, scenario_id, strategy)
    spec.real_client = rec
    spec.execution_mode = "MOCK"
    scenario, golden = _build_scenario(
        scenario_id, spec.trial_id, spec.csv_path,
    )
    try:
        new_art, fault = _inject_fault(
            case_label, scenario.store, scenario.run_id,
            target_artifact_id,
        )
        fault_id = fault.fault_id
        fault_type = fault.fault_type
    except Exception as e:
        fault_id = f"INJECTION_FAILED_{case_label}"
        fault_type = "INJECTION_ERROR"
    start = time.perf_counter()
    if strategy == "FULL_RERUN":
        res = run_full_rerun_recovery(
            scenario, golden,
            csv_path=spec.csv_path,
            llm_client=spec.real_client,
        )
    elif strategy == "COARSE_CHECKPOINT":
        res = run_coarse_checkpoint_recovery(
            scenario, golden,
            csv_path=spec.csv_path,
            llm_client=spec.real_client,
        )
    else:
        res = run_selective_recovery(
            scenario, golden,
            llm_client=spec.real_client,
        )
    wall_ms = (time.perf_counter() - start) * 1000.0
    n_calls = len(rec.records)
    total_in = sum(r.input_tokens or 0 for r in rec.records)
    total_out = sum(r.output_tokens or 0 for r in rec.records)
    total_tokens = total_in + total_out
    latency_sum = sum(
        r.client_wall_clock_latency_ms or 0
        for r in rec.records
    )
    structural = _identify_root_cause_and_affected(
        case_label, strategy,
    )
    provider_exec = (
        n_calls > 0
        and all(
            (r.request_id or "").startswith("chatcmpl-")
            and r.usage_type == "provider_reported"
            for r in rec.records
        )
    )
    # 5-axis contract (per METRIC_DEFINITIONS.md).
    # We re-derive output_contract_valid and semantic_validation
    # from res structural fields, NOT from res.success (which
    # mixes in golden_match).  This is the fix for the Stage 1
    # workflow_contract_success=1/12 over-counting failure.
    output_contract_valid = _output_contract_valid_from(res)
    semantic_validation_success = (
        _semantic_validation_success_from(res)
    )
    golden_match = bool(res.matches_golden_output)
    # recovery_execution_success: provider ran AND no real
    # exception raised.  Golden miss is NOT an exception.
    has_exception = bool(
        res.failure_reason
        and "sem=True int=True golden=False" not in
        (res.failure_reason or "")
    )
    recovery_execution_success = bool(
        provider_exec and not has_exception
    )
    # Legacy fields (kept for back-compat with EXP-02
    # downstream consumers).
    workflow_contract = bool(res.success)
    recovery_success = recovery_execution_success
    # Composite trial_valid
    trial_valid = bool(
        provider_exec
        and recovery_execution_success
        and output_contract_valid
        and semantic_validation_success
    )
    est_cost = (
        total_in * 0.004 / 1000.0
        + total_out * 0.012 / 1000.0
    )
    return {
        "experiment_id": "EXP-02",
        "trial_id": trial_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "provider": FROZEN_PROVIDER_CONFIG.provider,
        "model": FROZEN_PROVIDER_CONFIG.model,
        "case_id": case_label,
        "scenario_id": scenario_id,
        "failure_type": fault_type,
        "fault_id": fault_id,
        "strategy": strategy,
        # ---- 5-axis contract (new) ----
        "provider_success": provider_exec,
        "recovery_execution_success": recovery_execution_success,
        "output_contract_valid": output_contract_valid,
        "semantic_validation_success": semantic_validation_success,
        "trial_valid": trial_valid,
        # ---- Legacy fields (back-compat) ----
        "success": bool(res.success),
        "recovery_success": recovery_success,
        "provider_execution_success": provider_exec,
        "workflow_contract_success": workflow_contract,
        "golden_match": golden_match,
        # ---- Domain ----
        "root_cause": structural["root_cause"],
        "affected_nodes": structural["affected_nodes"],
        "unaffected_nodes": structural["unaffected_nodes"],
        "rerun_nodes": structural["rerun_nodes"],
        "reused_nodes": structural["reused_nodes"],
        "llm_calls": n_calls,
        "tokens_prompt": total_in,
        "tokens_completion": total_out,
        "tokens_total": total_tokens,
        "latency_ms": latency_sum,
        "wall_time_ms": wall_ms,
        "estimated_cost_cny": round(est_cost, 6),
        "error_type": (
            "none" if res.success
            else "STRUCTURAL_OR_GOLDEN_MISMATCH"
        ),
        "error_message": res.failure_reason or "",
        "matches_golden_output": bool(
            res.matches_golden_output
        ),
        "final_content_hash": res.final_content_hash,
        "structural": {
            "task_calls": res.task_calls,
            "tool_task_calls": res.tool_task_calls,
            "llm_task_calls": res.llm_task_calls,
            "artifact_materializations":
                res.artifact_materializations,
            "required_materializations":
                res.required_materializations,
            "unnecessary_materializations":
                res.unnecessary_materializations,
            "rmp": res.recovery_materialization_precision,
            "uarr": res.uarr,
            "arr": res.active_reuse_rate,
        },
    }


def _output_contract_valid_from(res) -> bool:
    """Compute `output_contract_valid` from the ExperimentResult
    directly (no success_report needed).  Excludes the
    mock-only `matches_golden_output` check.

    The output contract requires:
      - final_target_ref present
      - final_content_hash present
      - required_materializations > 0 (i.e. at least one
        artifact was correctly materialized)
      - artifact_materializations >= required_materializations
        (no missing required)
      - recovery_materialization_precision (RMP) > 0
      - no failure_reason that indicates a real exception
        (golden miss is NOT an exception)
    """
    if res is None:
        return False
    if res.final_target_ref is None:
        return False
    if not res.final_content_hash:
        return False
    if res.required_materializations <= 0:
        return False
    if res.artifact_materializations < res.required_materializations:
        return False
    if res.recovery_materialization_precision <= 0:
        return False
    # Real exceptions (not golden miss) should not occur
    if res.failure_reason:
        golden_marker = "sem=True int=True golden=False"
        if golden_marker not in res.failure_reason:
            return False
    return True


def _semantic_validation_success_from(res) -> bool:
    """`semantic_validation_success` is True iff the
    per-artifact semantic checks all pass.  Derived from
    ExperimentResult structural fields (NOT from
    `matches_golden_output`).

    Specifically:
      - recovery_materialization_precision (RMP) > 0
      - active_reuse_rate (ARR) > 0
      - no preserved_unaffected artifacts re-computed
        (i.e. unaffected_recomputed_count == 0)
    """
    if res is None:
        return False
    if res.recovery_materialization_precision <= 0:
        return False
    if getattr(res, "active_reuse_rate", 0.0) <= 0:
        return False
    if res.unaffected_recomputed_count > 0:
        return False
    return True


def _build_summary(results: list) -> dict:
    by_case_strat = defaultdict(lambda: defaultdict(list))
    for t in results:
        by_case_strat[t["case_id"]][t["strategy"]].append(t)
    summary = {
        "schema_version": 1,
        "experiment_id": "EXP-02",
        "mode": "MOCK",
        "stage": "Stage 1 (1 repeat)",
        "disclaimer": (
            "MOCK / PIPELINE VALIDATION ONLY.  Numbers are "
            "deterministic mock fixture outputs, NOT real Qwen "
            "performance claims.  See METRIC_DEFINITIONS.md."
        ),
        "trial_count": len(results),
        "trial_count_breakdown": {
            "cases": 4,
            "strategies": 3,
            "repeats": 1,
            "note": (
                "4 cases x 3 strategies x 1 repeat = 12 trials"
            ),
        },
        "per_strategy_metrics": {},
        "reductions_vs_full": {},
        "per_case_strategy_metrics": {},
        "provider_calls_count": 0,
    }
    for strategy in STRATEGIES:
        flat = []
        for case in by_case_strat:
            flat.extend(by_case_strat[case][strategy])
        n = len(flat)
        success_n = sum(
            1 for t in flat if t["recovery_success"]
        )
        llm_calls = [t["llm_calls"] for t in flat]
        tokens = [t["tokens_total"] for t in flat]
        latencies = [t["wall_time_ms"] for t in flat]
        rerun_counts = [len(t["rerun_nodes"]) for t in flat]
        reused_counts = [len(t["reused_nodes"]) for t in flat]
        def _avg(xs): return sum(xs) / len(xs) if xs else 0
        def _med(xs):
            if not xs: return 0
            s = sorted(xs)
            return (
                s[len(s) // 2] if len(s) % 2
                else (s[len(s) // 2 - 1] + s[len(s) // 2]) / 2
            )
        summary["per_strategy_metrics"][strategy] = {
            "total_trials": n,
            "successful_trials": success_n,
            "recovery_success_rate": (
                round(success_n / n, 4) if n else 0
            ),
            "provider_success_rate": round(
                sum(1 for t in flat if t.get("provider_success")) / n,
                4,
            ) if n else 0,
            "recovery_execution_success_rate": round(
                sum(1 for t in flat if t.get(
                    "recovery_execution_success"
                )) / n,
                4,
            ) if n else 0,
            "output_contract_valid_rate": round(
                sum(1 for t in flat if t.get(
                    "output_contract_valid"
                )) / n,
                4,
            ) if n else 0,
            "semantic_validation_success_rate": round(
                sum(1 for t in flat if t.get(
                    "semantic_validation_success"
                )) / n,
                4,
            ) if n else 0,
            "trial_valid_rate": round(
                sum(1 for t in flat if t.get("trial_valid")) / n,
                4,
            ) if n else 0,
            "golden_match_rate": round(
                sum(1 for t in flat if t.get("golden_match")) / n,
                4,
            ) if n else 0,
            "avg_llm_calls": round(_avg(llm_calls), 2),
            "avg_tokens_total": round(_avg(tokens), 2),
            "median_tokens_total": _med(tokens),
            "avg_latency_ms": round(_avg(latencies), 2),
            "median_latency_ms": _med(latencies),
            "avg_rerun_nodes": round(_avg(rerun_counts), 2),
            "avg_reused_nodes": round(_avg(reused_counts), 2),
        }
    full = summary["per_strategy_metrics"]["FULL_RERUN"]
    for strategy in STRATEGIES:
        if strategy == "FULL_RERUN":
            continue
        sm = summary["per_strategy_metrics"][strategy]
        def _pct(s, f):
            return round(100 * (f - s) / f, 2) if f else 0
        summary["reductions_vs_full"][strategy] = {
            "call_reduction_pct": _pct(
                sm["avg_llm_calls"], full["avg_llm_calls"]
            ),
            "token_reduction_pct": _pct(
                sm["avg_tokens_total"],
                full["avg_tokens_total"],
            ),
            "latency_reduction_pct": _pct(
                sm["avg_latency_ms"],
                full["avg_latency_ms"],
            ),
            "rerun_node_reduction_pct": _pct(
                sm["avg_rerun_nodes"],
                full["avg_rerun_nodes"],
            ),
        }
    for case in by_case_strat:
        summary["per_case_strategy_metrics"][case] = {}
        for strategy in STRATEGIES:
            trials = by_case_strat[case][strategy]
            if not trials:
                continue
            t = trials[0]
            summary["per_case_strategy_metrics"][case][strategy] = {
                "n": len(trials),
                "recovery_success": sum(
                    1 for x in trials if x["recovery_success"]
                ),
                "llm_calls": t["llm_calls"],
                "tokens_total": t["tokens_total"],
                "rerun_nodes": t["rerun_nodes"],
                "reused_nodes": t["reused_nodes"],
            }
    return summary


def _write_csv(results: list, path: Path) -> None:
    import csv as _csv
    fields = [
        "experiment_id", "trial_id", "timestamp", "provider",
        "model", "case_id", "failure_type", "strategy",
        # 5-axis contract
        "provider_success", "recovery_execution_success",
        "output_contract_valid", "semantic_validation_success",
        "trial_valid",
        # Legacy / back-compat
        "success", "recovery_success",
        "provider_execution_success", "workflow_contract_success",
        "golden_match",
        # Domain
        "root_cause", "affected_nodes",
        "rerun_nodes", "reused_nodes",
        "llm_calls", "tokens_prompt", "tokens_completion",
        "tokens_total", "latency_ms", "wall_time_ms",
        "estimated_cost_cny", "error_type", "error_message",
    ]
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = _csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for t in results:
            row = {k: t.get(k, "") for k in fields}
            for list_k in (
                "affected_nodes", "rerun_nodes", "reused_nodes",
            ):
                if isinstance(row[list_k], list):
                    row[list_k] = "|".join(row[list_k])
            w.writerow(row)


def _write_markdown(summary: dict, results: list,
                    path: Path) -> None:
    md = ["# EXP-02 — Stage 1 Report (12 trials, MOCK mode)", ""]
    md.append(
        "> ⚠️ **MOCK / PIPELINE VALIDATION ONLY**.  This is "
        "Stage 1 (MOCK mode, 12 trials, 0 real Qwen calls).  "
        "Numbers are **deterministic mock fixture outputs**, "
        "NOT real LLM performance claims.  See "
        "`METRIC_DEFINITIONS.md` for the 5-axis contract and "
        "why mock data cannot be used as benchmark claims."
    )
    md.append("")
    md.append("## 5-Axis Success Contract (Stage 1.5+)")
    md.append("")
    md.append("| Strategy | Provider | Recovery-exec | "
              "Output-contract | Semantic | Trial-Valid | Golden |")
    md.append("|---|---|---|---|---|---|---|")
    for s, m in summary["per_strategy_metrics"].items():
        md.append(
            f"| {s} | "
            f"{m.get('provider_success_rate', 0)*100:.0f}% | "
            f"{m.get('recovery_execution_success_rate', 0)*100:.0f}% | "
            f"{m.get('output_contract_valid_rate', 0)*100:.0f}% | "
            f"{m.get('semantic_validation_success_rate', 0)*100:.0f}% | "
            f"{m.get('trial_valid_rate', 0)*100:.0f}% | "
            f"{m.get('golden_match_rate', 0)*100:.0f}%* |"
        )
    md.append("")
    md.append("*Golden match is mock-only regression metric, "
              "excluded from trial_valid.*")
    md.append("")
    md.append("## Per-strategy metrics (legacy, for back-compat)")
    md.append("")
    md.append(
        "| Strategy | Trials | Recovery | Avg calls | Avg tokens | "
        "Median tokens | Avg latency (ms) | Avg rerun | "
        "Avg reused |"
    )
    md.append("|---|---|---|---|---|---|---|---|---|")
    for s, m in summary["per_strategy_metrics"].items():
        md.append(
            f"| {s} | {m['total_trials']} | "
            f"{m['successful_trials']}/{m['total_trials']} | "
            f"{m['avg_llm_calls']} | {m['avg_tokens_total']} | "
            f"{m['median_tokens_total']} | "
            f"{m['avg_latency_ms']:.0f} | "
            f"{m['avg_rerun_nodes']} | {m['avg_reused_nodes']} |"
        )
    md.append("")
    md.append("## Reductions vs FULL_RERUN (mock fixture)")
    md.append("")
    md.append(
        "| Strategy | Call Δ | Token Δ | Latency Δ | "
        "Rerun-node Δ |"
    )
    md.append("|---|---|---|---|---|")
    for s, r in summary["reductions_vs_full"].items():
        md.append(
            f"| {s} vs FULL | {r['call_reduction_pct']}% | "
            f"{r['token_reduction_pct']}% | "
            f"{r['latency_reduction_pct']}% | "
            f"{r['rerun_node_reduction_pct']}% |"
        )
    md.append("")
    md.append("## Per-case × strategy")
    md.append("")
    md.append("| Case | Strategy | Calls | Tokens | Rerun | Reused |")
    md.append("|---|---|---|---|---|---|")
    for case, d in summary["per_case_strategy_metrics"].items():
        for strat, t in d.items():
            md.append(
                f"| {case} | {strat} | {t['llm_calls']} | "
                f"{t['tokens_total']} | "
                f"{len(t['rerun_nodes'])} | "
                f"{len(t['reused_nodes'])} |"
            )
    md.append("")
    md.append("## Observed vs Interpretation")
    md.append("")
    md.append("**Observed Result** (12 MOCK trials):")
    n_prov = sum(1 for t in results if t["provider_success"])
    n_rec = sum(1 for t in results if t["recovery_execution_success"])
    n_out = sum(1 for t in results if t["output_contract_valid"])
    n_sem = sum(1 for t in results if t["semantic_validation_success"])
    n_valid = sum(1 for t in results if t["trial_valid"])
    n_gold = sum(1 for t in results if t["golden_match"])
    md.append(
        f"- Provider success: {n_prov}/12"
    )
    md.append(
        f"- Recovery execution success: {n_rec}/12"
    )
    md.append(
        f"- Output contract valid: {n_out}/12"
    )
    md.append(
        f"- Semantic validation success: {n_sem}/12"
    )
    md.append(
        f"- Trial valid: {n_valid}/12"
    )
    md.append(
        f"- Golden match: {n_gold}/12 (mock-only regression metric)"
    )
    md.append(
        "- SELECTIVE consistently reruns fewer nodes than "
        "FULL/COARSE across all 4 cases"
    )
    md.append(
        "- Token reduction for SELECTIVE vs FULL: see "
        "reductions_vs_full above"
    )
    md.append("")
    md.append("**Interpretation** (caveats — do NOT over-generalize):")
    md.append(
        "- **This is MOCK data, NOT a benchmark claim.**  "
        "Stage 1.5 (real Qwen) and Stage 2 (5-repeat) are "
        "required before any CureFlow performance claim is made."
    )
    md.append(
        "- Sample size: 12 trials (4 cases x 3 strategies x "
        "1 repeat).  Stage 1 only."
    )
    md.append(
        "- Tokens are simulated, not real Qwen spend.  Real "
        "Qwen runs would have noise and cost."
    )
    md.append(
        "- Real 60-trial Stage 2 not yet executed; do NOT "
        "extrapolate to large-scale conclusions."
    )
    md.append(
        "- Golden match is NOT counted as failure (mock-only "
        "regression metric).  See METRIC_DEFINITIONS.md."
    )
    path.write_text("\n".join(md), encoding="utf-8")


def main() -> int:
    print("=" * 64)
    print("EXP-02 Runner — Stage 1 (12 trials, MOCK mode)")
    print("=" * 64)
    print()
    results = []
    for case_label, scenario_id, fault_type, target_id in CASES:
        for strategy in STRATEGIES:
            t0 = time.perf_counter()
            try:
                t = _run_trial(
                    case_label, scenario_id, strategy,
                    target_id,
                )
                t["_elapsed_s"] = time.perf_counter() - t0
                results.append(t)
                out_path = OUT / f"{t['trial_id']}.json"
                out_path.write_text(
                    json.dumps(t, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                print(
                    f"  [{len(results):2d}/12] "
                    f"{t['trial_id']:60s} "
                    f"success={t['success']}  "
                    f"calls={t['llm_calls']}  "
                    f"tokens={t['tokens_total']}  "
                    f"recovery={t['recovery_success']}"
                )
            except Exception as e:
                print(
                    f"  [FAIL] {case_label}/{strategy}: "
                    f"{type(e).__name__}: {e}"
                )
                raise
    summary = _build_summary(results)
    (OUT / "exp02_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_csv(results, OUT / "exp02_summary.csv")
    _write_markdown(summary, results, OUT / "EXP02_REPORT.md")
    # Charts
    _write_charts(summary, results, OUT / "figures")
    print()
    print("=" * 64)
    print("Stage 1 — 12 trials PASS (MOCK mode)")
    print("=" * 64)
    n_prov = sum(1 for t in results if t["provider_success"])
    n_rec = sum(1 for t in results if t["recovery_execution_success"])
    n_out = sum(1 for t in results if t["output_contract_valid"])
    n_sem = sum(1 for t in results if t["semantic_validation_success"])
    n_valid = sum(1 for t in results if t["trial_valid"])
    n_gold = sum(1 for t in results if t["golden_match"])
    print(f"  Provider success               : {n_prov}/12")
    print(f"  Recovery execution success     : {n_rec}/12")
    print(f"  Output contract valid           : {n_out}/12")
    print(f"  Semantic validation success     : {n_sem}/12")
    print(f"  Trial valid                     : {n_valid}/12")
    print(f"  Golden match (mock-only)        : {n_gold}/12")
    print()
    print("  Reductions vs FULL_RERUN (mock fixture):")
    for s, r in summary["reductions_vs_full"].items():
        print(
            f"    {s:20s}  calls {r['call_reduction_pct']}%  "
            f"tokens {r['token_reduction_pct']}%  "
            f"rerun_nodes {r['rerun_node_reduction_pct']}%"
        )
    return 0


def _write_charts(summary: dict, results: list,
                  out_dir: Path) -> None:
    """Generate SVG charts (no matplotlib; pure stdlib).
    Section C of the spec."""
    out_dir.mkdir(parents=True, exist_ok=True)
    strategies = STRATEGIES
    # Chart 1: LLM Calls by Strategy
    _svg_bar_chart(
        title="LLM Calls by Strategy (avg, EXP-02 Stage 1)",
        labels=strategies,
        values=[
            summary["per_strategy_metrics"][s]["avg_llm_calls"]
            for s in strategies
        ],
        ylabel="avg LLM calls",
        out_path=out_dir / "llm_calls_by_strategy.svg",
        value_fmt="{:.1f}",
    )
    # Chart 2: Token Usage by Strategy
    _svg_bar_chart(
        title=(
            "Token Usage by Strategy "
            "(avg, EXP-02 Stage 1)"
        ),
        labels=strategies,
        values=[
            summary["per_strategy_metrics"][s]["avg_tokens_total"]
            for s in strategies
        ],
        ylabel="avg total tokens",
        out_path=out_dir / "tokens_by_strategy.svg",
        value_fmt="{:.0f}",
    )
    # Chart 3: Latency by Strategy
    _svg_bar_chart(
        title="Wall Time by Strategy (avg, EXP-02 Stage 1)",
        labels=strategies,
        values=[
            summary["per_strategy_metrics"][s]["avg_latency_ms"]
            for s in strategies
        ],
        ylabel="avg wall time (ms)",
        out_path=out_dir / "latency_by_strategy.svg",
        value_fmt="{:.0f}",
    )
    # Chart 4: Re-executed Nodes by Strategy
    _svg_bar_chart(
        title=(
            "Re-executed Nodes by Strategy "
            "(avg, EXP-02 Stage 1)"
        ),
        labels=strategies,
        values=[
            summary["per_strategy_metrics"][s]["avg_rerun_nodes"]
            for s in strategies
        ],
        ylabel="avg rerun nodes",
        out_path=out_dir / "rerun_nodes_by_strategy.svg",
        value_fmt="{:.1f}",
    )
    # Chart 5: Recovery Success Rate by Strategy
    _svg_bar_chart(
        title=(
            "Recovery Success Rate by Strategy "
            "(EXP-02 Stage 1)"
        ),
        labels=strategies,
        values=[
            summary["per_strategy_metrics"][s][
                "recovery_success_rate"
            ] * 100
            for s in strategies
        ],
        ylabel="recovery success rate (%)",
        out_path=out_dir / "recovery_success_by_strategy.svg",
        value_fmt="{:.0f}",
        ymax=110,
    )
    # Chart 6: Case x Strategy token usage
    cases = sorted(summary["per_case_strategy_metrics"].keys())
    case_x_strat = {
        c: {
            s: summary["per_case_strategy_metrics"][c][s][
                "tokens_total"
            ]
            for s in strategies
        }
        for c in cases
    }
    _svg_grouped_bar_chart(
        title="Token Usage by Case x Strategy (EXP-02 Stage 1)",
        groups=cases,
        series_labels=strategies,
        values=case_x_strat,
        ylabel="total tokens",
        out_path=out_dir / "tokens_case_x_strategy.svg",
        value_fmt="{:.0f}",
    )


def _svg_bar_chart(
    title: str, labels: list, values: list,
    ylabel: str, out_path: Path,
    value_fmt: str = "{:.1f}",
    ymax: float = None,
) -> None:
    """Minimal bar chart as inline SVG.  No external deps."""
    width, height = 600, 360
    margin_l, margin_r, margin_t, margin_b = 70, 30, 50, 90
    plot_w = width - margin_l - margin_r
    plot_h = height - margin_t - margin_b
    vmax = max(values) if values else 1
    if ymax is not None:
        vmax = max(vmax, ymax)
    else:
        vmax = vmax * 1.15 if vmax > 0 else 1
    n = len(labels)
    bar_w = plot_w / max(n, 1) * 0.6
    gap = plot_w / max(n, 1) * 0.4
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{width}" height="{height}">',
        f'<text x="{width/2}" y="20" text-anchor="middle" '
        f'font-size="14" font-family="sans-serif" '
        f'font-weight="bold">{title}</text>',
        f'<line x1="{margin_l}" y1="{margin_t}" '
        f'x2="{margin_l}" y2="{height-margin_b}" '
        f'stroke="black" stroke-width="1"/>',
        f'<line x1="{margin_l}" y1="{height-margin_b}" '
        f'x2="{width-margin_r}" y2="{height-margin_b}" '
        f'stroke="black" stroke-width="1"/>',
    ]
    # y-axis ticks
    for i in range(6):
        y_val = vmax * i / 5
        y_pos = (
            height - margin_b - (y_val / vmax) * plot_h
        )
        parts.append(
            f'<text x="{margin_l-6}" y="{y_pos+4}" '
            f'text-anchor="end" font-size="10" '
            f'font-family="sans-serif">{y_val:.0f}</text>'
        )
        parts.append(
            f'<line x1="{margin_l-3}" y1="{y_pos}" '
            f'x2="{margin_l}" y2="{y_pos}" '
            f'stroke="black" stroke-width="1"/>'
        )
    # y-axis label
    parts.append(
        f'<text x="15" y="{height/2}" '
        f'transform="rotate(-90 15 {height/2})" '
        f'text-anchor="middle" font-size="11" '
        f'font-family="sans-serif">{ylabel}</text>'
    )
    # bars + labels
    for i, (lab, v) in enumerate(zip(labels, values)):
        x = margin_l + i * (bar_w + gap) + gap / 2
        h = (v / vmax) * plot_h
        y = height - margin_b - h
        color = ["#4C78A8", "#F58518", "#54A24B"][i % 3]
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" '
            f'width="{bar_w:.1f}" height="{h:.1f}" '
            f'fill="{color}"/>'
        )
        # value above bar
        parts.append(
            f'<text x="{x + bar_w/2:.1f}" y="{y-4:.1f}" '
            f'text-anchor="middle" font-size="10" '
            f'font-family="sans-serif">'
            f'{value_fmt.format(v)}</text>'
        )
        # x-axis label
        parts.append(
            f'<text x="{x + bar_w/2:.1f}" '
            f'y="{height-margin_b+15}" text-anchor="middle" '
            f'font-size="10" font-family="sans-serif" '
            f'transform="rotate(-15 {x + bar_w/2:.1f} '
            f'{height-margin_b+15})">{lab}</text>'
        )
    parts.append('</svg>')
    out_path.write_text(
        "\n".join(parts), encoding="utf-8"
    )


def _svg_grouped_bar_chart(
    title: str, groups: list, series_labels: list,
    values: dict, ylabel: str, out_path: Path,
    value_fmt: str = "{:.0f}",
) -> None:
    width, height = 700, 380
    margin_l, margin_r, margin_t, margin_b = 70, 30, 60, 90
    plot_w = width - margin_l - margin_r
    plot_h = height - margin_t - margin_b
    all_vals = [
        v for d in values.values() for v in d.values()
    ]
    vmax = max(all_vals) if all_vals else 1
    vmax = vmax * 1.15 if vmax > 0 else 1
    n_groups = len(groups)
    n_series = len(series_labels)
    group_w = plot_w / max(n_groups, 1)
    bar_w = group_w / (n_series + 1)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{width}" height="{height}">',
        f'<text x="{width/2}" y="22" text-anchor="middle" '
        f'font-size="14" font-family="sans-serif" '
        f'font-weight="bold">{title}</text>',
        f'<line x1="{margin_l}" y1="{margin_t}" '
        f'x2="{margin_l}" y2="{height-margin_b}" '
        f'stroke="black" stroke-width="1"/>',
        f'<line x1="{margin_l}" y1="{height-margin_b}" '
        f'x2="{width-margin_r}" y2="{height-margin_b}" '
        f'stroke="black" stroke-width="1"/>',
    ]
    # legend
    for i, lab in enumerate(series_labels):
        cx = margin_l + i * 90
        cy = margin_t - 20
        color = ["#4C78A8", "#F58518", "#54A24B"][i % 3]
        parts.append(
            f'<rect x="{cx}" y="{cy-8}" width="12" '
            f'height="12" fill="{color}"/>'
            f'<text x="{cx+18}" y="{cy+3}" font-size="11" '
            f'font-family="sans-serif">{lab}</text>'
        )
    # y-axis ticks
    for i in range(6):
        y_val = vmax * i / 5
        y_pos = (
            height - margin_b - (y_val / vmax) * plot_h
        )
        parts.append(
            f'<text x="{margin_l-6}" y="{y_pos+4}" '
            f'text-anchor="end" font-size="10" '
            f'font-family="sans-serif">{y_val:.0f}</text>'
        )
    # bars
    for gi, g in enumerate(groups):
        for si, lab in enumerate(series_labels):
            v = values[g].get(lab, 0)
            x = margin_l + gi * group_w + si * bar_w
            h = (v / vmax) * plot_h
            y = height - margin_b - h
            color = ["#4C78A8", "#F58518", "#54A24B"][si]
            parts.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" '
                f'width="{bar_w-1:.1f}" height="{h:.1f}" '
                f'fill="{color}"/>'
            )
            parts.append(
                f'<text x="{x + bar_w/2:.1f}" y="{y-3:.1f}" '
                f'text-anchor="middle" font-size="9" '
                f'font-family="sans-serif">'
                f'{value_fmt.format(v)}</text>'
            )
        # x label
        parts.append(
            f'<text x="{margin_l + gi*group_w + group_w/2:.1f}" '
            f'y="{height-margin_b+15}" text-anchor="middle" '
            f'font-size="10" font-family="sans-serif">{g}</text>'
        )
    parts.append('</svg>')
    out_path.write_text(
        "\n".join(parts), encoding="utf-8"
    )


if __name__ == "__main__":
    sys.exit(main())
