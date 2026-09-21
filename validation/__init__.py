"""Semantic validation — Day 13.

Deterministic validators for Claim and Section content produced by
either a fake or a real LLM.  For real-LLM responses we cannot
require byte-equal content; instead we check:
  - evidence_ids parse to existing ArtifactRefs of the expected
    version (no invented refs, no latest-version drift)
  - numeric values reported in the text are consistent with the
    evidence Artifact.value (with rounding tolerance)
  - direction (positive/negative growth) is consistent with metric
    semantics
  - no recommendation / decision language ("建议", "应该", "采取"…)

No LLM-as-judge.  No external dependencies.  Works offline and
online.
"""
from .semantic import (
    NumericConsistencyResult,
    SemanticValidationResult,
    direction_check,
    numeric_consistency_check,
    parse_evidence_refs,
    recommendation_boundary_check,
    validate_claim,
    validate_section,
)

__all__ = [
    "NumericConsistencyResult",
    "SemanticValidationResult",
    "direction_check",
    "numeric_consistency_check",
    "parse_evidence_refs",
    "recommendation_boundary_check",
    "validate_claim",
    "validate_section",
]
