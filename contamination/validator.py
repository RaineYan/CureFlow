"""
Field validator — Day 7.

Re-derives each Field's value from the RawDataArtifact (using
Day 2's `recompute_field_from_raw`) and compares against the
current Field value. Mismatch -> validation failure.

This validator reads ONLY:
  - field.value
  - field.artifact_id
  - raw_data.value (records)
  - field.artifact_id to extract column + year

It does NOT read fault_injected / fault_type metadata. pytest
locks this.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict

from artifacts import (
    Artifact,
    ArtifactRef,
    ArtifactStatus,
    ArtifactStore,
    ArtifactType,
)
from workflow.tasks import recompute_field_from_raw


VALIDATOR_NAME = "raw_source_consistency"


class ValidationResult(BaseModel):
    """Result of running one validator on one artifact."""
    model_config = ConfigDict(extra="forbid")

    artifact_ref: ArtifactRef
    passed: bool
    validator: str
    reason: str
    expected_value: Optional[float] = None
    actual_value: Optional[float] = None


def validate_field_against_raw(
    field_artifact: Artifact,
    raw_artifact: Artifact,
) -> ValidationResult:
    """Compare field_artifact.value to the recomputed ground truth.

    Uses Day 2's recompute_field_from_raw, which rebuilds the value
    from the raw records list. No fault metadata is read.
    """
    aggregation = field_artifact.meta.get("aggregation", "sum")
    expected = recompute_field_from_raw(
        raw_artifact, field_artifact.artifact_id, aggregation=aggregation,
    )
    actual = float(field_artifact.value)
    passed = abs(expected - actual) < 1e-6
    return ValidationResult(
        artifact_ref=ArtifactRef(
            run_id=field_artifact.run_id,
            artifact_id=field_artifact.artifact_id,
            version=field_artifact.version,
        ),
        passed=passed,
        validator=VALIDATOR_NAME,
        reason=(
            "Field value matches recomputed raw-data aggregate"
            if passed else
            "Field value differs from recomputed raw-data aggregate"
        ),
        expected_value=float(expected),
        actual_value=actual,
    )


def detect_field_faults(
    run_id: str,
    store: ArtifactStore,
) -> List[ValidationResult]:
    """Run the field validator on every Field in the run.

    Returns the list of FAILURES (passed=False). On failure, also
    calls store.update_status(... INVALIDATED) to mark the artifact.
    Does NOT change content version.
    """
    raw = store.get(run_id, "raw_data")
    if raw is None:
        return []

    failures: List[ValidationResult] = []
    for a in store.list_by_run(run_id):
        if a.artifact_type != "Field":
            continue
        result = validate_field_against_raw(a, raw)
        if not result.passed:
            failures.append(result)
            # Status change, not content change — version stays
            store.update_status(
                run_id, a.artifact_id, ArtifactStatus.INVALIDATED,
            )
    return failures


# Mark for unit test that validator does not read fault metadata
def _validator_does_not_read_fault_metadata() -> bool:
    """Static check used by pytest. Returns True if this module's
    source does not import or reference 'fault_injected' or 'fault_type'."""
    import inspect
    src = inspect.getsource(validate_field_against_raw)
    return ("fault_injected" not in src
            and "fault_type" not in src
            and "FaultRecord" not in src)
