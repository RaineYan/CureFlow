"""Day 14 — strategy wrappers.

Three Day 14 trial entry points.  Each is a thin
adapter that:
  1. builds the matching MainFaultScenario (or
     visits-fault variant for Case B);
  2. wires the real Qwen client + LiveCallLedger;
  3. runs the existing strategy (FULL / COARSE /
     SELECTIVE);
  4. returns a `TrialResult` that the runner writes
     to disk.

Every trial uses an INDEPENDENT store / graph / ledger
/ RealLLMClient, per user spec section 6.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from artifacts import ArtifactRef, ArtifactStore

from experiments.live_ledger import LiveCallLedger
from experiments.scenario import (
    build_main_fault_scenario,
    build_visits_fault_scenario,
)
from experiments.strategies import (
    run_coarse_checkpoint_recovery,
    run_full_rerun_recovery,
    run_selective_recovery,
)

from .metrics import (
    RealMetricsReport,
    collect_real_metrics,
)


# =====================================================================
# Trial spec / result
# =====================================================================

@dataclass
class TrialSpec:
    trial_id: str
    case_id: str          # "case_a" | "case_b"
    strategy: str         # "FULL_RERUN" | "COARSE_CHECKPOINT" | "SELECTIVE_RECOVERY"
    store_dir: str        # tmpdir used for this trial only
    csv_path: str
    provider_config: Dict[str, Any]
    out_path: str         # path to write the trial JSON
    real_client: Any = None  # RecordingClient-wrapped RealLLMClient (or None for offline)
    execution_mode: str = "OFFLINE_FAKE"  # "REAL_PROVIDER" | "OFFLINE_FAKE"
    replaces_trial_id: Optional[str] = None  # e.g. "case_a_full_rerun_trial01"


@dataclass
class TrialResult:
    trial_id: str
    case_id: str
    strategy: str
    store_dir: str
    success: bool
    failure_reason: str
    recovery_wall_clock_ms: float
    real_metrics: RealMetricsReport
    final_target_ref: Optional[str]
    final_content_hash: Optional[str]
    matches_golden_output: bool
    new_llm_artifact_refs: List[str] = field(default_factory=list)
    reused_llm_artifact_refs: List[str] = field(default_factory=list)
    structural: Dict[str, Any] = field(default_factory=dict)
    semantic: Dict[str, Any] = field(default_factory=dict)
    success_report: Dict[str, Any] = field(default_factory=dict)
    infra_failure: bool = False
    infra_failure_reason: str = ""
    scenario_signature_hash: Optional[str] = None
    replacement_trial_id: Optional[str] = None
    replaces_trial_id: Optional[str] = None
    replacement_reason: Optional[str] = None
    # 7-layer per-execution-unit state (Day 14A forensic
    # hardening).  Maps execution_unit -> 7 booleans.
    execution_unit_states: Dict[str, Dict[str, bool]] = field(
        default_factory=dict
    )
    # provider_verified: True iff at least one real
    # Provider response was captured with
    # usage_type=provider_reported and a non-empty
    # request_id.  Set by TrialRunner after the run.
    provider_verified: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trial_id": self.trial_id,
            "case_id": self.case_id,
            "strategy": self.strategy,
            "store_dir": self.store_dir,
            "success": self.success,
            "failure_reason": self.failure_reason,
            "recovery_wall_clock_ms":
                round(self.recovery_wall_clock_ms, 4),
            "real_metrics": self.real_metrics.to_dict(),
            "final_target_ref": self.final_target_ref,
            "final_content_hash": self.final_content_hash,
            "matches_golden_output": self.matches_golden_output,
            "new_llm_artifact_refs": self.new_llm_artifact_refs,
            "reused_llm_artifact_refs": self.reused_llm_artifact_refs,
            "structural": self.structural,
            "semantic": self.semantic,
            "success_report": self.success_report,
            "infra_failure": self.infra_failure,
            "infra_failure_reason": self.infra_failure_reason,
            "scenario_signature_hash": self.scenario_signature_hash,
            "replacement_trial_id": self.replacement_trial_id,
            "replaces_trial_id": self.replaces_trial_id,
            "replacement_reason": self.replacement_reason,
            "execution_unit_states": self.execution_unit_states,
            "provider_verified": self.provider_verified,
        }


# =====================================================================
# Helpers
# =====================================================================

def _build_scenario(case_id: str, run_id: str, csv_path: str):
    if case_id == "case_a":
        return build_main_fault_scenario(
            run_id=run_id, csv_path=csv_path,
        )
    if case_id == "case_b":
        return build_visits_fault_scenario(
            run_id=run_id, csv_path=csv_path,
        )
    raise ValueError(f"unknown case_id: {case_id!r}")


def _structural_block(result) -> Dict[str, Any]:
    """Pull the structural metrics from an ExperimentResult.
    Day 11-12 spec: structural metrics MUST be derived
    from events, never hand-incremented."""
    return {
        "task_calls": result.task_calls,
        "tool_task_calls": result.tool_task_calls,
        "llm_task_calls": result.llm_task_calls,
        "artifact_materializations": result.artifact_materializations,
        "required_materializations":
            result.required_materializations,
        "unnecessary_materializations":
            result.unnecessary_materializations,
        "rmp": result.recovery_materialization_precision,
        "uarr": result.uarr,
        "upr": result.unaffected_preservation_rate,
        "arr": result.active_reuse_rate,
        "raf": result.recompute_amplification_factor,
        "taf": result.task_amplification_factor,
        "preserved_unaffected_count":
            result.preserved_unaffected_count,
        "actively_reused_count":
            result.actively_reused_count,
        "required_clean_prerequisite_count":
            result.required_clean_prerequisite_count,
        "required_recovery_identity_count":
            result.required_recovery_identity_count,
        "reference_recovery_task_count":
            result.reference_recovery_task_count,
    }


# =====================================================================
# Trial entry points
# =====================================================================

def run_full_trial(spec: TrialSpec) -> TrialResult:
    from experiments.day14.scenario_signature import (
        compute_scenario_signature_hash,
    )
    from experiments.day14.validators import evaluate_trial_success

    scenario, golden = _build_scenario(
        spec.case_id, spec.trial_id, spec.csv_path,
    )
    sig_hash = compute_scenario_signature_hash(
        scenario.store, scenario.run_id,
    )
    # FULL_RERUN — re-run the entire 3-branch pipeline.
    # If a real_client (RecordingClient-wrapped RealLLMClient)
    # is present in the spec, thread it through.  This is the
    # ONLY path that issues real Provider HTTP; otherwise we
    # fall back to FakeLLM.
    start = time.perf_counter()
    res = run_full_rerun_recovery(
        scenario=scenario, golden=golden, csv_path=spec.csv_path,
        llm_client=spec.real_client,
    )
    wall_ms = (time.perf_counter() - start) * 1000.0
    # If a real client is wired in, harvest the LiveCallLedger
    # it accumulated.  Otherwise the offline ledger is empty.
    real_metrics = _harvest_real_metrics(
        spec, wall_ms,
    )
    new_llm = [r.to_ref_string() for r in _llm_produced_refs(res)]
    reused_llm: List[str] = []
    # No reused LLM artifacts in FULL (everything is fresh).
    success_report = evaluate_trial_success(
        trial_id=spec.trial_id,
        case_id=spec.case_id,
        strategy=spec.strategy,
        store=scenario.store,
        target_ref=res.final_target_ref,
        new_llm_refs=_llm_produced_refs(res),
        reused_llm_refs=[],
        final_report_ref=res.final_target_ref,
    )
    return TrialResult(
        trial_id=spec.trial_id,
        case_id=spec.case_id,
        strategy=spec.strategy,
        store_dir=spec.store_dir,
        success=bool(res.success and success_report.success),
        failure_reason=res.failure_reason or
            success_report.failure_reason,
        recovery_wall_clock_ms=wall_ms,
        real_metrics=real_metrics,
        final_target_ref=res.final_target_ref.to_ref_string()
            if res.final_target_ref else None,
        final_content_hash=res.final_content_hash,
        matches_golden_output=res.matches_golden_output,
        new_llm_artifact_refs=new_llm,
        reused_llm_artifact_refs=reused_llm,
        structural=_structural_block(res),
        semantic={
            "semantic_clean_path_pass":
                res.semantic_clean_path_pass,
            "execution_integrity_pass":
                res.execution_integrity_pass,
        },
        success_report=success_report.to_dict(),
        scenario_signature_hash=sig_hash,
    )


def run_coarse_trial(spec: TrialSpec) -> TrialResult:
    from experiments.day14.scenario_signature import (
        compute_scenario_signature_hash,
    )
    from experiments.day14.validators import evaluate_trial_success

    scenario, golden = _build_scenario(
        spec.case_id, spec.trial_id, spec.csv_path,
    )
    sig_hash = compute_scenario_signature_hash(
        scenario.store, scenario.run_id,
    )
    start = time.perf_counter()
    res = run_coarse_checkpoint_recovery(
        scenario=scenario, golden=golden, csv_path=spec.csv_path,
        llm_client=spec.real_client,
    )
    wall_ms = (time.perf_counter() - start) * 1000.0
    real_metrics = _harvest_real_metrics(spec, wall_ms)
    new_llm = [r.to_ref_string() for r in _llm_produced_refs(res)]
    success_report = evaluate_trial_success(
        trial_id=spec.trial_id,
        case_id=spec.case_id,
        strategy=spec.strategy,
        store=scenario.store,
        target_ref=res.final_target_ref,
        new_llm_refs=_llm_produced_refs(res),
        reused_llm_refs=[],
        final_report_ref=res.final_target_ref,
    )
    return TrialResult(
        trial_id=spec.trial_id,
        case_id=spec.case_id,
        strategy=spec.strategy,
        store_dir=spec.store_dir,
        success=bool(res.success and success_report.success),
        failure_reason=res.failure_reason or
            success_report.failure_reason,
        recovery_wall_clock_ms=wall_ms,
        real_metrics=real_metrics,
        final_target_ref=res.final_target_ref.to_ref_string()
            if res.final_target_ref else None,
        final_content_hash=res.final_content_hash,
        matches_golden_output=res.matches_golden_output,
        new_llm_artifact_refs=new_llm,
        reused_llm_artifact_refs=[],
        structural=_structural_block(res),
        semantic={
            "semantic_clean_path_pass":
                res.semantic_clean_path_pass,
            "execution_integrity_pass":
                res.execution_integrity_pass,
        },
        success_report=success_report.to_dict(),
        scenario_signature_hash=sig_hash,
    )


def run_selective_trial(spec: TrialSpec) -> TrialResult:
    from experiments.day14.scenario_signature import (
        compute_scenario_signature_hash,
    )
    from experiments.day14.validators import evaluate_trial_success

    scenario, golden = _build_scenario(
        spec.case_id, spec.trial_id, spec.csv_path,
    )
    sig_hash = compute_scenario_signature_hash(
        scenario.store, scenario.run_id,
    )
    start = time.perf_counter()
    res = run_selective_recovery(
        scenario=scenario, golden=golden,
        llm_client=spec.real_client,
    )
    wall_ms = (time.perf_counter() - start) * 1000.0
    real_metrics = _harvest_real_metrics(spec, wall_ms)
    # SELECTIVE: LLM artifacts that were freshly materialized
    # are the financial claim + financial section (Case A)
    # or operation claim + operation section (Case B).
    new_llm = [r.to_ref_string() for r in _llm_produced_refs(res)]
    # reused LLM artifacts: the user / operation (Case A) or
    # user / financial (Case B) sections + claims at v1
    # were never materialized in this trial.
    reused_llm = _llm_reused_refs(res, scenario)
    success_report = evaluate_trial_success(
        trial_id=spec.trial_id,
        case_id=spec.case_id,
        strategy=spec.strategy,
        store=scenario.store,
        target_ref=res.final_target_ref,
        new_llm_refs=_llm_produced_refs(res),
        reused_llm_refs=[
            ArtifactRef.from_ref_string(s) for s in reused_llm
        ],
        final_report_ref=res.final_target_ref,
    )
    return TrialResult(
        trial_id=spec.trial_id,
        case_id=spec.case_id,
        strategy=spec.strategy,
        store_dir=spec.store_dir,
        success=bool(res.success and success_report.success),
        failure_reason=res.failure_reason or
            success_report.failure_reason,
        recovery_wall_clock_ms=wall_ms,
        real_metrics=real_metrics,
        final_target_ref=res.final_target_ref.to_ref_string()
            if res.final_target_ref else None,
        final_content_hash=res.final_content_hash,
        matches_golden_output=res.matches_golden_output,
        new_llm_artifact_refs=new_llm,
        reused_llm_artifact_refs=reused_llm,
        structural=_structural_block(res),
        semantic={
            "semantic_clean_path_pass":
                res.semantic_clean_path_pass,
            "execution_integrity_pass":
                res.execution_integrity_pass,
        },
        success_report=success_report.to_dict(),
        scenario_signature_hash=sig_hash,
    )


# =====================================================================
# Harvest the real-call metrics from the spec's recording
# client (if a real client was wired in).  Otherwise the
# offline ledger is empty.
# =====================================================================

def _harvest_real_metrics(
    spec: TrialSpec,
    wall_ms: float,
) -> RealMetricsReport:
    """Return the RealMetricsReport for this trial.

    Priority:
      1. If `spec.real_client` is a RecordingClient-style
         wrapper exposing `.ledger`, use it.
      2. Otherwise return an empty offline ledger.
    """
    real = spec.real_client
    if real is not None and hasattr(real, "ledger"):
        ledger = real.ledger
    else:
        ledger = LiveCallLedger()
    return collect_real_metrics(ledger, wall_ms)


# =====================================================================
# Internal: which refs are LLM-produced vs reused
# =====================================================================

def _llm_produced_refs(res) -> List[ArtifactRef]:
    """LLM-produced artifacts in this trial (REPROMPT
    events).  Tool artifacts are excluded."""
    out: List[ArtifactRef] = []
    for e in res.events:
        if e.action == "REPROMPT":
            out.extend(e.produced_refs)
    # Dedup while preserving order.
    seen = set()
    dedup: List[ArtifactRef] = []
    for r in out:
        if r.to_ref_string() in seen:
            continue
        seen.add(r.to_ref_string())
        dedup.append(r)
    return dedup


def _llm_reused_refs(res, scenario) -> List[str]:
    """Reused LLM artifacts in SELECTIVE — branches that
    were NOT affected by the fault.  We derive this from
    the scenario's `available_unaffected` set intersected
    with what the executor reused (REUSE events)."""
    reused_ids = set()
    for e in res.events:
        if e.action == "REUSE":
            for r in e.reused_refs:
                reused_ids.add(r.artifact_id)
    out: List[str] = []
    for aid in sorted(
        set(scenario.available_unaffected_artifact_identities)
        & reused_ids
    ):
        # The reused ref's exact version is the latest
        # in the store (v1 for unaffected).
        a = scenario.store.get(scenario.run_id, aid)
        if a is not None:
            out.append(
                f"{scenario.run_id}/{aid}@v{a.version}"
            )
    return out
