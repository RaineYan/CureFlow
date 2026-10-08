"""One-shot: rewrite experiments/fault_injection.py cleanly (UTF-8)
so the PowerShell corruption (em-dash turned into ?) does not
break Python import.
"""
from pathlib import Path

PATH = Path("experiments/fault_injection.py")
SOURCE = '''"""
Fault injection - Day 7.

Only VALUE_TAMPER is implemented. The injector mutates the latest
content version of a Field by multiplying its value, creating a new
content version (never overwriting v1). The original Artifact's
provenance (input refs) is preserved - only the value and meta change.

The FaultRecord is ground truth ONLY. Validators must not read it
or any `fault_injected` metadata to detect faults (this is locked
by pytest in test_fault_contamination).
"""
from __future__ import annotations

from typing import Optional, Tuple

from pydantic import BaseModel, ConfigDict

from artifacts import (
    Artifact,
    ArtifactRef,
    ArtifactStore,
)


class FaultRecord(BaseModel):
    """Ground-truth record of an injected fault. Detectors MUST NOT read this."""
    model_config = ConfigDict(extra="forbid")

    fault_id: str
    run_id: str
    artifact_ref: ArtifactRef
    fault_type: str
    original_value: float
    injected_value: float


def inject_field_value_tamper(
    store: ArtifactStore,
    run_id: str,
    artifact_id: str = "field_revenue_2025",
    factor: float = 10.0,
) -> Tuple[Artifact, FaultRecord]:
    """Multiply the latest version's value by `factor`, saving as new version.

    Day 13.6 FINAL follow-up: the cross-version contamination
    propagation is handled inside `propagate_contamination`
    (not here).  The baseline v_n's value is NOT overwritten
    and the Day 9/10 invariant "v1 remains VALID after
    baseline" is intact.  inject_field_value_tamper only
    creates the new v_n+1 content version; it does not touch
    the v_n status.

    Returns (new_artifact, fault_record).
    Raises KeyError if (run_id, artifact_id) is not present.
    """
    original = store.get(run_id, artifact_id)
    if original is None:
        raise KeyError(f"Artifact not found: ({run_id}, {artifact_id})")

    original_value = float(original.value)
    injected_value = original_value * factor
    next_v = original.version + 1

    new_meta = dict(original.meta)
    new_meta.update({
        "fault_injected": True,
        "fault_type": "VALUE_TAMPER",
        "fault_factor": factor,
    })

    new_artifact = original.model_copy(update={
        "value": injected_value,
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
        fault_type="VALUE_TAMPER",
        original_value=original_value,
        injected_value=injected_value,
    )
    return saved, record
'''
PATH.write_text(SOURCE, encoding="utf-8")
print("rewrote fault_injection.py (UTF-8 clean)")
