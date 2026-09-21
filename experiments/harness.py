"""
ExperimentHarness — Day 10.

  run_one_round(case_id, run_id)  -> StructuralComparison
  run_main_case_comparison(n_repeats, output_path)
  run_multi_case_comparison(case_specs, n_repeats, output_path)
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .models import (
    DEFINITIONS,
    ExperimentStrategy,
    RunDelta,
    StructuralComparison,
    compute_artifact_reuse_rate,
    compute_llm_call_reduction,
    compute_materialization_reduction,
    compute_uarr,
    compute_arp,
    compute_recompute_amplification,
)
from .scenario import (
    DEFAULT_CSV,
    GoldenClean,
    MainFaultScenario,
    build_golden_clean_scenario,
    build_main_fault_scenario,
    build_visits_fault_scenario,
)
from .strategies import (
    run_coarse_checkpoint_recovery,
    run_full_rerun_recovery,
    run_selective_recovery,
)


# =====================================================================
# Single case × single run
# =====================================================================

def run_one_round(
    *,
    case_id: str,
    run_id: str,
    csv_path: str = DEFAULT_CSV,
) -> StructuralComparison:
    if case_id == "case_a":
        scenario_factory: Callable[..., tuple[MainFaultScenario, GoldenClean]] = (
            build_main_fault_scenario
        )
    elif case_id == "case_b":
        scenario_factory = build_visits_fault_scenario
    else:
        raise ValueError(f"unknown case_id: {case_id!r}")

    sc1, g1 = scenario_factory(run_id=f"{run_id}_full", csv_path=csv_path)
    full = run_full_rerun_recovery(sc1, g1, csv_path)
    sc2, _ = scenario_factory(run_id=f"{run_id}_cc", csv_path=csv_path)
    coarse = run_coarse_checkpoint_recovery(sc2, g1, csv_path)
    sc3, _ = scenario_factory(run_id=f"{run_id}_sel", csv_path=csv_path)
    selective = run_selective_recovery(sc3, g1)

    deltas = _compute_deltas(full, coarse, selective)
    return StructuralComparison(
        case_id=case_id,
        full=full,
        coarse_checkpoint=coarse,
        selective=selective,
        golden_output_hash=g1.final_report_hash,
        deltas=deltas,
    )


def _compute_deltas(full, coarse, sel) -> Dict[str, RunDelta]:
    out: Dict[str, RunDelta] = {}
    for name, r in (("full", full), ("coarse_checkpoint", coarse), ("selective", sel)):
        out[name] = RunDelta(
            strategy=r.strategy,
            task_calls_delta_vs_full=r.task_calls - full.task_calls,
            tool_task_calls_delta_vs_full=r.tool_task_calls - full.tool_task_calls,
            llm_task_calls_delta_vs_full=r.llm_task_calls - full.llm_task_calls,
            artifact_materializations_delta_vs_full=(
                r.artifact_materializations - full.artifact_materializations
            ),
            tool_artifact_materializations_delta_vs_full=(
                r.tool_artifact_materializations - full.tool_artifact_materializations
            ),
            llm_artifact_materializations_delta_vs_full=(
                r.llm_artifact_materializations - full.llm_artifact_materializations
            ),
            llm_calls_delta_vs_full=r.llm_calls - full.llm_calls,
            llm_call_reduction_vs_full=compute_llm_call_reduction(
                full.llm_calls, r.llm_calls),
            materialization_reduction_vs_full=compute_materialization_reduction(
                full.artifact_materializations, r.artifact_materializations),
            tool_materialization_reduction_vs_full=compute_materialization_reduction(
                full.tool_artifact_materializations, r.tool_artifact_materializations),
            artifact_reuse_rate=r.artifact_reuse_rate,
            uarr=r.uarr,
            recovery_materialization_precision=r.recovery_materialization_precision,
            affected_recovery_precision_arp=r.affected_recovery_precision_arp,
            recompute_amplification_factor=r.recompute_amplification_factor,
            recompute_amplification=r.recompute_amplification_factor,
            task_amplification_factor=r.task_amplification_factor,
            unaffected_preservation_rate=r.unaffected_preservation_rate,
            active_reuse_rate=r.active_reuse_rate,
        )
    return out


# =====================================================================
# One case, N repeats
# =====================================================================

def run_main_case_comparison(
    n_repeats: int = 5,
    output_path: Optional[str] = None,
) -> dict:
    return _run_case_n(
        case_id="case_a",
        n_repeats=n_repeats,
        output_path=output_path,
    )


def _run_case_n(
    *,
    case_id: str,
    n_repeats: int,
    output_path: Optional[str] = None,
) -> dict:
    if n_repeats < 1:
        raise ValueError("n_repeats must be >= 1")

    rounds: List[StructuralComparison] = []
    for i in range(n_repeats):
        c = run_one_round(
            case_id=case_id,
            run_id=f"day10_{case_id}_{i+1}",
        )
        rounds.append(c)

    stability_ok = True
    if n_repeats >= 2:
        first = rounds[0]
        for r in rounds[1:]:
            if not _stable_match(first, r):
                stability_ok = False
                break

    summary = rounds[0]
    out: dict = {
        "experiment_version": DEFINITIONS["experiment_version"],
        "case_id": case_id,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "dataset": "data/sample_company.csv",
        "n_repeats": n_repeats,
        "provider_type": DEFINITIONS["provider_type"],
        "usage_type": DEFINITIONS["usage_type"],
        "latency_type": DEFINITIONS["latency_type"],
        "real_model_reserved_for": DEFINITIONS["real_model_reserved_for"],
        "strategies": {
            "full_rerun":         _result_to_dict(summary.full),
            "coarse_checkpoint":  _result_to_dict(summary.coarse_checkpoint),
            "selective_recovery": _result_to_dict(summary.selective),
        },
        "metrics": {
            "full_rerun":         _delta_to_dict(summary.deltas["full"]),
            "coarse_checkpoint":  _delta_to_dict(summary.deltas["coarse_checkpoint"]),
            "selective_recovery": _delta_to_dict(summary.deltas["selective"]),
        },
        "definitions": DEFINITIONS,
        "stability": {
            "structural_identical_across_runs": stability_ok,
            "per_round": [_round_summary(r) for r in rounds],
        },
        "golden_output_hash": summary.golden_output_hash,
        "scenario_affected_artifact_identities": (
            summary.full.affected_artifact_identities
        ),
        "scenario_available_unaffected_identities": (
            summary.full.available_unaffected_artifact_identities
        ),
    }

    if output_path is not None:
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    return out


# =====================================================================
# Multi-case summary
# =====================================================================

def run_multi_case_comparison(
    *,
    case_specs: List[str],
    n_repeats: int = 5,
    output_path: Optional[str] = None,
) -> dict:
    cases_out: dict = {}
    for case_id in case_specs:
        cases_out[case_id] = _run_case_n(case_id=case_id, n_repeats=n_repeats)

    macro = _macro_average(cases_out)

    out: dict = {
        "experiment_version": DEFINITIONS["experiment_version"],
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "n_repeats": n_repeats,
        "case_ids": case_specs,
        "cases": cases_out,
        "macro_average": macro,
        "definitions": DEFINITIONS,
        "stability_all_cases": all(
            cases_out[c]["stability"]["structural_identical_across_runs"]
            for c in case_specs
        ),
    }

    if output_path is not None:
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    return out


def _macro_average(cases_out: dict) -> dict:
    """Unweighted average of the three strategies across all cases."""
    strategies = ("full_rerun", "coarse_checkpoint", "selective_recovery")
    keys = [
        "task_calls", "tool_task_calls", "llm_task_calls",
        "artifact_materializations",
        "tool_artifact_materializations", "llm_artifact_materializations",
        "llm_calls", "reused_artifact_count", "unaffected_recomputed_count",
        "artifact_reuse_rate", "uarr",
        "affected_recovery_precision_arp", "recompute_amplification",
        # Day 11
        "required_materializations", "unnecessary_materializations",
        "recovery_materialization_precision",
        "actively_reused_count", "preserved_unaffected_count",
        "skipped_irrelevant_count",
        "unaffected_preservation_rate", "active_reuse_rate",
        "recompute_amplification_factor", "task_amplification_factor",
    ]
    macro: dict = {}
    n = max(1, len(cases_out))
    for s in strategies:
        macro[s] = {}
        for k in keys:
            total = 0.0
            for case_id in cases_out:
                total += cases_out[case_id]["strategies"][s].get(k, 0)
            macro[s][k] = total / n
    return macro


# =====================================================================
# Helpers
# =====================================================================

def _stable_match(a: StructuralComparison, b: StructuralComparison) -> bool:
    for strat in ("full", "coarse_checkpoint", "selective"):
        sa, sb = getattr(a, strat), getattr(b, strat)
        for k in (
            "success", "matches_golden_output",
            "task_calls", "tool_task_calls", "llm_task_calls",
            "artifact_materializations",
            "tool_artifact_materializations", "llm_artifact_materializations",
            "llm_calls", "reused_artifact_count",
            "unaffected_recomputed_count",
            "artifact_reuse_rate", "uarr",
            "affected_recovery_precision_arp", "recompute_amplification",
            # Day 11
            "required_materializations", "unnecessary_materializations",
            "recovery_materialization_precision",
            "actively_reused_count", "preserved_unaffected_count",
            "skipped_irrelevant_count",
            "unaffected_preservation_rate", "active_reuse_rate",
            "recompute_amplification_factor", "task_amplification_factor",
        ):
            if getattr(sa, k) != getattr(sb, k):
                return False
    return True


def _round_summary(c: StructuralComparison) -> dict:
    return {
        "case_id": c.case_id,
        "full": {
            "success": c.full.success,
            "golden_match": c.full.matches_golden_output,
            "llm_calls": c.full.llm_calls,
            "artifact_materializations": c.full.artifact_materializations,
        },
        "coarse_checkpoint": {
            "success": c.coarse_checkpoint.success,
            "golden_match": c.coarse_checkpoint.matches_golden_output,
            "llm_calls": c.coarse_checkpoint.llm_calls,
            "artifact_materializations": c.coarse_checkpoint.artifact_materializations,
        },
        "selective": {
            "success": c.selective.success,
            "golden_match": c.selective.matches_golden_output,
            "llm_calls": c.selective.llm_calls,
            "artifact_materializations": c.selective.artifact_materializations,
        },
    }


def _result_to_dict(r) -> dict:
    return {
        "case_id": r.case_id,
        "strategy": r.strategy.value,
        "success": r.success,
        "final_target_ref": (
            r.final_target_ref.to_ref_string() if r.final_target_ref else None
        ),
        "final_content_hash": r.final_content_hash,
        "matches_golden_output": r.matches_golden_output,
        "task_calls": r.task_calls,
        "tool_task_calls": r.tool_task_calls,
        "llm_task_calls": r.llm_task_calls,
        "tool_artifact_materializations": r.tool_artifact_materializations,
        "llm_artifact_materializations": r.llm_artifact_materializations,
        "artifact_materializations": r.artifact_materializations,
        "llm_calls": r.llm_calls,
        "reused_artifact_count": r.reused_artifact_count,
        "reused_artifact_ids": r.reused_artifact_ids,
        "reused_unaffected_artifact_identities":
            r.reused_unaffected_artifact_identities,
        "recomputed_artifact_ids": r.recomputed_artifact_ids,
        "unaffected_artifacts_recomputed": r.unaffected_artifacts_recomputed,
        "affected_artifact_identities": r.affected_artifact_identities,
        "available_unaffected_artifact_identities":
            r.available_unaffected_artifact_identities,
        "affected_artifact_identity_count":
            r.affected_artifact_identity_count,
        "available_unaffected_count": r.available_unaffected_count,
        "unaffected_recomputed_count": r.unaffected_recomputed_count,
        "artifact_reuse_rate": r.artifact_reuse_rate,
        "uarr": r.uarr,
        "affected_recovery_precision_arp":
            r.affected_recovery_precision_arp,
        "recompute_amplification": r.recompute_amplification,
        # Day 11 metrics
        "required_recovery_artifact_identities":
            r.required_recovery_artifact_identities,
        "required_recovery_identity_count":
            r.required_recovery_identity_count,
        "required_clean_prerequisite_identities":
            r.required_clean_prerequisite_identities,
        "required_clean_prerequisite_count":
            r.required_clean_prerequisite_count,
        "required_materializations": r.required_materializations,
        "unnecessary_materializations": r.unnecessary_materializations,
        "recovery_materialization_precision":
            r.recovery_materialization_precision,
        "actively_reused_artifact_identities":
            r.actively_reused_artifact_identities,
        "actively_reused_count": r.actively_reused_count,
        "actively_reused_clean_prerequisite_count":
            r.actively_reused_clean_prerequisite_count,
        "preserved_unaffected_artifact_identities":
            r.preserved_unaffected_artifact_identities,
        "preserved_unaffected_count": r.preserved_unaffected_count,
        "skipped_irrelevant_artifact_identities":
            r.skipped_irrelevant_artifact_identities,
        "skipped_irrelevant_count": r.skipped_irrelevant_count,
        "unaffected_preservation_rate": r.unaffected_preservation_rate,
        "active_reuse_rate": r.active_reuse_rate,
        "recompute_amplification_factor": r.recompute_amplification_factor,
        "task_amplification_factor": r.task_amplification_factor,
        "minimum_required_execution_unit_count":
            r.minimum_required_execution_unit_count,
        "classifications": r.classifications,
        "synthetic_input_tokens": r.synthetic_input_tokens,
        "synthetic_output_tokens": r.synthetic_output_tokens,
        "synthetic_total_tokens": r.synthetic_total_tokens,
        "local_wall_clock_ms": r.local_wall_clock_ms,
        "semantic_clean_path_pass": r.semantic_clean_path_pass,
        "execution_integrity_pass": r.execution_integrity_pass,
        "failure_reason": r.failure_reason,
        "executed_units": r.executed_units,
        "event_count": len(r.events),
    }


def _delta_to_dict(d: RunDelta) -> dict:
    return {
        "strategy": d.strategy.value,
        "task_calls_delta_vs_full": d.task_calls_delta_vs_full,
        "tool_task_calls_delta_vs_full": d.tool_task_calls_delta_vs_full,
        "llm_task_calls_delta_vs_full": d.llm_task_calls_delta_vs_full,
        "artifact_materializations_delta_vs_full":
            d.artifact_materializations_delta_vs_full,
        "tool_artifact_materializations_delta_vs_full":
            d.tool_artifact_materializations_delta_vs_full,
        "llm_artifact_materializations_delta_vs_full":
            d.llm_artifact_materializations_delta_vs_full,
        "llm_calls_delta_vs_full": d.llm_calls_delta_vs_full,
        "llm_call_reduction_vs_full": d.llm_call_reduction_vs_full,
        "materialization_reduction_vs_full": d.materialization_reduction_vs_full,
        "tool_materialization_reduction_vs_full":
            d.tool_materialization_reduction_vs_full,
        "artifact_reuse_rate": d.artifact_reuse_rate,
        "uarr": d.uarr,
        "recovery_materialization_precision": d.recovery_materialization_precision,
        "affected_recovery_precision_arp": d.affected_recovery_precision_arp,
        "recompute_amplification_factor": d.recompute_amplification_factor,
        "recompute_amplification": d.recompute_amplification,
        "task_amplification_factor": d.task_amplification_factor,
        "unaffected_preservation_rate": d.unaffected_preservation_rate,
        "active_reuse_rate": d.active_reuse_rate,
    }
