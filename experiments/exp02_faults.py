"""Day 14B/EXP-02 fault types - extends Day 7
`experiments/fault_injection.py` with 3 new fault types
required by the EXP-02 benchmark:

  - AGENT_OUTPUT_ERROR   (CASE_A: upstream, CASE_B: midstream)
  - EVIDENCE_REFERENCE_ERROR (CASE_C: wrong evidence ref)
  - ARTIFACT_DATA_CORRUPTION (CASE_D: tampered artifact value)

The original `inject_field_value_tamper` (Day 7) is
preserved unchanged.  This module adds the 3 new
injectors and a unified `inject_fault(case_id, store, run_id)`
entry point.

All injectors return `(new_artifact, fault_record)` and
create new content versions (never overwrite v1).
"""
from __future__ import annotations

from typing import Tuple

from pydantic import BaseModel, ConfigDict

from artifacts import (
    Artifact,
    ArtifactRef,
    ArtifactStore,
)

from experiments.fault_injection import (
    FaultRecord,
    inject_field_value_tamper,
)


# Canonical CASE -> fault_type + scenario + target mapping.
# This is the SINGLE SOURCE OF TRUTH for the EXP-02 case grid.
# Both _exp02_runner.py and _exp02_stage15_real_runner.py
# derive their CASES list from this tuple.
#
# Schema: (case_id, scenario_id, fault_type, target_artifact_id)
#
# Per spec section 1 / B1:
#   CASE_A  = upstream data corruption (field value 10x)
#   CASE_B  = midstream agent output error (claim text)
#   CASE_C  = evidence reference error
#   CASE_D  = downstream data corruption (sign-flip)
#
# The fault_type and target_artifact_id MUST match the
# `inject_fault(case_id, ...)` dispatch (below) exactly.
# The scenario_id is the case label used by
# `_build_scenario` (must be "case_a" or "case_b").
CANONICAL_CASES: tuple = (
    ("CASE_A", "case_a", "ARTIFACT_DATA_CORRUPTION",
     "field_revenue_2025"),
    ("CASE_B", "case_b", "AGENT_OUTPUT_ERROR",
     "claim_financial_growth"),
    ("CASE_C", "case_a", "EVIDENCE_REFERENCE_ERROR",
     "claim_financial_growth"),
    ("CASE_D", "case_a", "ARTIFACT_DATA_CORRUPTION",
     "field_revenue_2025"),
)


# Back-compat alias: (case_id) -> fault_type string.
# Generated from CANONICAL_CASES so the two cannot drift.
CASE_INJECTORS: dict = {
    case: fault_type
    for case, _scenario, fault_type, _target in CANONICAL_CASES
}


# =====================================================================
# Injector 2: AGENT_OUTPUT_ERROR (Claim content swap)
# =====================================================================

def inject_agent_output_error(
    store: ArtifactStore,
    run_id: str,
    artifact_id: str,
    *,
    bad_claim_text: str = "WRONG_AGENT_OUTPUT (injected for benchmark)",
) -> Tuple[Artifact, FaultRecord]:
    """Replace a Claim's content with a clearly-wrong text
    to simulate a downstream Agent returning an incorrect
    answer.  Used by CASE_A (upstream) and CASE_B
    (midstream)."""
    original = store.get(run_id, artifact_id)
    if original is None:
        raise KeyError(
            f"Artifact not found: ({run_id}, {artifact_id})"
        )
    next_v = original.version + 1
    new_meta = dict(original.meta)
    new_meta.update({
        "fault_injected": True,
        "fault_type": "AGENT_OUTPUT_ERROR",
        "fault_artifact_id": artifact_id,
    })
    new_artifact = original.model_copy(update={
        "content": bad_claim_text,
        "version": next_v,
        "meta": new_meta,
    })
    saved = store.save(new_artifact)
    record = FaultRecord(
        fault_id=f"flt_{run_id}_{artifact_id}_v{next_v}",
        run_id=run_id,
        artifact_ref=ArtifactRef(
            run_id=run_id, artifact_id=artifact_id, version=next_v,
        ),
        fault_type="AGENT_OUTPUT_ERROR",
        original_value=0.0,
        injected_value=0.0,
    )
    return saved, record


# =====================================================================
# Injector 3: EVIDENCE_REFERENCE_ERROR
# =====================================================================

def inject_evidence_reference_error(
    store: ArtifactStore,
    run_id: str,
    artifact_id: str,
    *,
    bad_ref_index: int = 0,
) -> Tuple[Artifact, FaultRecord]:
    """Swap an evidence ref to a non-existent artifact
    (simulates the Agent citing a wrong evidence id)."""
    original = store.get(run_id, artifact_id)
    if original is None:
        raise KeyError(
            f"Artifact not found: ({run_id}, {artifact_id})"
        )
    next_v = original.version + 1
    new_evidence = list(original.evidence_ids)
    if not new_evidence:
        raise ValueError(
            f"Artifact {artifact_id} has no evidence_ids to corrupt"
        )
    # Replace the chosen evidence ref with a clearly-bad one
    bad_ref = ArtifactRef(
        run_id=run_id,
        artifact_id=f"corrupted_evidence_{bad_ref_index}",
        version=999,
    )
    idx = bad_ref_index % len(new_evidence)
    new_evidence[idx] = bad_ref
    new_meta = dict(original.meta)
    new_meta.update({
        "fault_injected": True,
        "fault_type": "EVIDENCE_REFERENCE_ERROR",
        "fault_artifact_id": artifact_id,
        "fault_ref_index": bad_ref_index,
    })
    new_artifact = original.model_copy(update={
        "evidence_ids": new_evidence,
        "version": next_v,
        "meta": new_meta,
    })
    saved = store.save(new_artifact)
    record = FaultRecord(
        fault_id=f"flt_{run_id}_{artifact_id}_v{next_v}",
        run_id=run_id,
        artifact_ref=ArtifactRef(
            run_id=run_id, artifact_id=artifact_id, version=next_v,
        ),
        fault_type="EVIDENCE_REFERENCE_ERROR",
        original_value=0.0,
        injected_value=0.0,
    )
    return saved, record


# =====================================================================
# Injector 4: ARTIFACT_DATA_CORRUPTION
# (wraps Day 7 VALUE_TAMPER with case-specific naming)
# =====================================================================

def inject_artifact_data_corruption(
    store: ArtifactStore,
    run_id: str,
    artifact_id: str = "field_revenue_2025",
    *,
    factor: float = -1.0,
) -> Tuple[Artifact, FaultRecord]:
    """Flip a metric's sign (or multiply by a factor) to
    simulate corrupted data.  Wraps the Day 7
    `inject_field_value_tamper` with CASE_D naming
    convention."""
    saved, _ = inject_field_value_tamper(
        store, run_id, artifact_id=artifact_id, factor=factor,
    )
    next_v = saved.version
    new_meta = dict(saved.meta)
    new_meta["fault_type"] = "ARTIFACT_DATA_CORRUPTION"
    saved = saved.model_copy(update={"meta": new_meta})
    # Construct a NEW FaultRecord with the correct type
    # (Day 7's helper returns "VALUE_TAMPER"; we override).
    record = FaultRecord(
        fault_id=f"flt_{run_id}_{artifact_id}_v{next_v}",
        run_id=run_id,
        artifact_ref=ArtifactRef(
            run_id=run_id, artifact_id=artifact_id, version=next_v,
        ),
        fault_type="ARTIFACT_DATA_CORRUPTION",
        original_value=0.0,
        injected_value=0.0,
    )
    return saved, record


# =====================================================================
# Unified entry point
# =====================================================================

def inject_fault(
    case_id: str,
    store: ArtifactStore,
    run_id: str,
    *,
    target_artifact_id: str = "field_revenue_2025",
    factor: float = 10.0,
    bad_claim_text: str = "WRONG_AGENT_OUTPUT (injected)",
    bad_ref_index: int = 0,
) -> Tuple[Artifact, FaultRecord]:
    """Unified entry point.  Dispatches on case_id
    (CASE_A / CASE_B / CASE_C / CASE_D).

    Mapping:
      CASE_A: data corruption (upstream field value 10x)
      CASE_B: agent output error (midstream claim text)
      CASE_C: evidence reference error
      CASE_D: data corruption (sign-flip)
    """
    if case_id == "CASE_A":
        return inject_artifact_data_corruption(
            store, run_id, "field_revenue_2025",
            factor=10.0,
        )
    if case_id == "CASE_B":
        # CASE_B is midstream agent output error; targets
        # a downstream claim artifact.
        return inject_agent_output_error(
            store, run_id, "claim_financial_growth",
            bad_claim_text=bad_claim_text,
        )
    if case_id == "CASE_C":
        return inject_evidence_reference_error(
            store, run_id, "claim_financial_growth",
            bad_ref_index=bad_ref_index,
        )
    if case_id == "CASE_D":
        return inject_artifact_data_corruption(
            store, run_id, target_artifact_id,
            factor=factor,
        )
    raise ValueError(f"unknown case_id: {case_id!r}")
