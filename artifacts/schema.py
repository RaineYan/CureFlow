"""
Artifact data contract — POC Day 1 (revised after first review).

Revisions vs first draft:
  1. Added ArtifactRef: {run_id, artifact_id, version}. Used in
     input_artifact_ids / evidence_ids so provenance records an exact
     pointer, not a bare id. This is what lets us answer:
       "this Claim cites Metric @ run_001 / metric_revenue_growth / v1"
     even after the Metric gets recovered to v2.
  2. ArtifactStore API requires (run_id, artifact_id); bare-id lookups
     are removed. Prevents collisions when FULL_RERUN, CHECKPOINT and
     SELECTIVE_RECOVERY each produce their own run.
  3. update_status(run_id, artifact_id, status) no longer bumps version.
     version is now the content version. Status changes overwrite the
     same JSON file in place; the on-disk file's status field reflects
     the latest state, while version stays put.

Status policy:
  - VALID          default at save time
  - SUSPECT        detector flagged, not yet confirmed
  - CONTAMINATED   set by propagation (Day 7)
  - INVALIDATED    set by validator (Day 7) or fault injection
  - RECOVERED      set after a recovery action produces a new content
                   version (Day 9)
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ArtifactStatus(str, Enum):
    VALID = "VALID"
    SUSPECT = "SUSPECT"
    CONTAMINATED = "CONTAMINATED"
    INVALIDATED = "INVALIDATED"
    RECOVERED = "RECOVERED"


class ArtifactType(str, Enum):
    RAW_DATA = "RawData"
    FIELD = "Field"
    METRIC = "Metric"
    CHART = "Chart"
    CLAIM = "Claim"
    RECOMMENDATION = "Recommendation"


class ExecutionCost(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Day 5: split token accounting for LLM tasks.
    # `tokens` is the total; `input_tokens + output_tokens` should equal it
    # for LLM calls. Tool tasks leave all three at 0.
    tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    monetary_usd: float = 0.0
    # Day 4 fix: latency_ms is float. Sub-millisecond work was being
    # silently truncated to 0 by int() in the decorator. Keep float so
    # we can record e.g. 0.42 ms and not lose precision.
    latency_ms: float = 0.0
    llm_calls: int = 0


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ArtifactRef(BaseModel):
    """Pointer to a specific (run, artifact, version).

    Immutable so it can be hashed, compared, and safely embedded inside
    other Artifacts as a value object.

    On-disk / wire format:    "<run_id>/<artifact_id>@v<version>"
    Example:                  "run_001/metric_revenue_growth@v1"
    """
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    artifact_id: str
    version: int

    def to_ref_string(self) -> str:
        return f"{self.run_id}/{self.artifact_id}@v{self.version}"

    @classmethod
    def from_ref_string(cls, s: str) -> "ArtifactRef":
        run_part, rest = s.split("/", 1)
        artifact_id, version_part = rest.split("@v")
        return cls(run_id=run_part, artifact_id=artifact_id, version=int(version_part))


class Artifact(BaseModel):
    """Single data contract every module reads and writes.

    Two version spaces:
      - version: content version. Increments ONLY when value, content,
        or refs change. Recompute / recovery bumps this.
      - validation_status: current state. Decoupled from version.
        Validator, propagation, recovery all write here without
        creating a new version.

    Day 4 (graph), Day 7 (detector / propagation), Day 9 (recovery),
    Day 11 (experiments) all read this shape.
    """
    model_config = ConfigDict(
        use_enum_values=True,
        validate_assignment=True,
        extra="forbid",
    )

    # --- identity ---
    artifact_id: str
    artifact_type: ArtifactType
    producer_task: str
    run_id: str
    version: int = 1

    # --- REQUIRED dependencies (versioned pointers) ---
    # input_artifact_ids : data inputs (data-flow lineage, used by contamination)
    # evidence_ids       : semantic citations (used by Claim / Recommendation)
    # Both are List[ArtifactRef] so we never lose track of which version
    # was actually consumed, even after the upstream is recovered.
    input_artifact_ids: List[ArtifactRef] = Field(default_factory=list)
    evidence_ids: List[ArtifactRef] = Field(default_factory=list)

    # --- payload ---
    value: Optional[Any] = None       # numeric / structured (Field, Metric)
    content: Optional[str] = None     # natural language (Claim, Recommendation)

    # --- validation (decoupled from version) ---
    validation_status: ArtifactStatus = ArtifactStatus.VALID
    confidence: Optional[float] = None  # 0.0 - 1.0

    # --- metadata ---
    timestamp: str = Field(default_factory=_utc_now_iso)
    execution_cost: ExecutionCost = Field(default_factory=ExecutionCost)
    meta: dict[str, Any] = Field(default_factory=dict)

    # --- cache bookkeeping (Day 14+: semantic-aware LRU cache) ---
    # access_count   : how many times this artifact was read via the store
    # last_access_at : ISO-8601 timestamp of the most recent read (None if never read)
    # Both default to 0 / None so existing on-disk JSON files (without these
    # keys) load cleanly via from_dict() without breaking backward compat.
    access_count: int = 0
    last_access_at: Optional[str] = None

    def to_dict(self) -> dict:
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, data: dict) -> "Artifact":
        return cls.model_validate(data)
