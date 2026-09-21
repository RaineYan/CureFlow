"""Experiments layer.

Day 7  : fault-injection helpers + FaultRecord.
Day 9  : FULL_RERUN / COARSE_CHECKPOINT / SELECTIVE_RECOVERY harness.
Day 10 : event-based instrumentation, identity-level UARR / reuse
         rate / ARP / RecomputeAmplification, plus a second
         deterministic fault case (visits_2025).
Day 11 : metric semantics finalization.  Every materialization is
         classified as REQUIRED_RECOVERY or UNNECESSARY_RECOMPUTATION.
         New metrics: RMP / UARR (Day-11) / UPR / ARR / RAF / TAF.
         Active Reuse is now derived from ExecutionEvent.input_refs
         (not from "available - recomputed").  raw_data is excluded
         from derived metrics as source of truth.
Day 12 : final metric audit.  PrerequisiteAuditRecord traces every
         plan input through the replacement_map.  ARR numerator and
         denominator are now exact ArtifactRef lists.  TAF denominator
         is renamed `reference_recovery_task_count`.  RealProvider
         ReadinessReport gates the connection to a real LLM provider.
"""
from .classification import (
    CATEGORY_CLEAN,
    CATEGORY_RECOVERED,
    CATEGORY_SOURCE,
    SOURCE_OF_TRUTH_ARTIFACT,
    MaterializationClassification,
    PrerequisiteAuditRecord,
    build_prerequisite_audit,
    classify_materializations,
    compute_arr_v2,
    compute_rmp,
    compute_taf,
    compute_uarr_v2,
    compute_upr,
    count_required,
    count_unnecessary,
    derive_actively_reused_clean_prereq_identities,
    derive_actively_reused_clean_prereq_refs,
    derive_active_reuse_and_preserved,
    derive_canonical_replacement_map,
    derive_clean_prerequisite_identities,
    derive_clean_prerequisite_refs,
    derive_missing_clean_prerequisite_refs,
    derive_required_recovery_identities,
    derive_target_recovery_closure,
)
from .fault_injection import (
    FaultRecord,
    inject_field_value_tamper,
)
from .models import (
    DEFINITIONS,
    ExecutionEvent,
    ExperimentResult,
    ExperimentStrategy,
    RunDelta,
    StructuralComparison,
    aggregate_events,
    compute_arp,
    compute_artifact_reuse_rate,
    compute_llm_call_reduction,
    compute_materialization_reduction,
    compute_recompute_amplification,
    compute_reuse_and_uarr,
    compute_uarr,
    hash_content,
)
from .scenario import (
    GoldenClean,
    MainFaultScenario,
    build_golden_clean_scenario,
    build_main_fault_scenario,
    build_visits_fault_scenario,
    rerun_all_metrics,
    rerun_field_extraction,
)
from .strategies import (
    run_coarse_checkpoint_recovery,
    run_full_rerun_recovery,
    run_selective_recovery,
)
from .harness import (
    run_main_case_comparison,
    run_multi_case_comparison,
    run_one_round,
)
from .readiness import (
    RealProviderReadinessReport,
    ReadinessCheck,
    build_readiness_report,
)

__all__ = [
    "SOURCE_OF_TRUTH_ARTIFACT",
    "MaterializationClassification",
    "CATEGORY_CLEAN",
    "CATEGORY_RECOVERED",
    "CATEGORY_SOURCE",
    "SOURCE_OF_TRUTH_ARTIFACT",
    "MaterializationClassification",
    "PrerequisiteAuditRecord",
    "build_prerequisite_audit",
    "classify_materializations",
    "compute_arr_v2",
    "compute_rmp",
    "compute_taf",
    "compute_uarr_v2",
    "compute_upr",
    "count_required",
    "count_unnecessary",
    "derive_actively_reused_clean_prereq_identities",
    "derive_actively_reused_clean_prereq_refs",
    "derive_active_reuse_and_preserved",
    "derive_canonical_replacement_map",
    "derive_clean_prerequisite_identities",
    "derive_clean_prerequisite_refs",
    "derive_missing_clean_prerequisite_refs",
    "derive_required_recovery_identities",
    "derive_target_recovery_closure",
    "FaultRecord",
    "inject_field_value_tamper",
    "DEFINITIONS",
    "ExecutionEvent",
    "ExperimentResult",
    "ExperimentStrategy",
    "RunDelta",
    "StructuralComparison",
    "aggregate_events",
    "compute_arp",
    "compute_artifact_reuse_rate",
    "compute_llm_call_reduction",
    "compute_materialization_reduction",
    "compute_recompute_amplification",
    "compute_reuse_and_uarr",
    "compute_uarr",
    "hash_content",
    "GoldenClean",
    "MainFaultScenario",
    "build_golden_clean_scenario",
    "build_main_fault_scenario",
    "build_visits_fault_scenario",
    "rerun_all_metrics",
    "rerun_field_extraction",
    "run_coarse_checkpoint_recovery",
    "run_full_rerun_recovery",
    "run_selective_recovery",
    "run_main_case_comparison",
    "run_multi_case_comparison",
    "run_one_round",
    "RealProviderReadinessReport",
    "ReadinessCheck",
    "build_readiness_report",
]
