"""Deterministic claim-grounding audit for user-facing CureFlow results.

This module is an additive observation layer.  It reuses the frozen semantic
validator without changing its acceptance rules, and adds explicit risk
labels for unsupported causal or absolute language.  The aggregate
``observed_risk_rate_pct`` describes only the claims in one run; it is not a
population-level model hallucination rate.
"""
from __future__ import annotations

from enum import Enum
from typing import Iterable

from pydantic import BaseModel, ConfigDict, Field

from artifacts import Artifact, ArtifactRef, ArtifactStatus, ArtifactStore
from validation.semantic import SemanticValidationResult, validate_claim


METHOD = "deterministic_evidence_grounding_v1"


class GroundingVerdict(str, Enum):
    GROUNDED = "grounded"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"


class RiskCode(str, Enum):
    H1_UNSUPPORTED_ASSERTION = "H1_UNSUPPORTED_ASSERTION"
    H2_DANGLING_REFERENCE = "H2_DANGLING_REFERENCE"
    H3_NUMERIC_DRIFT = "H3_NUMERIC_DRIFT"
    H4_EVIDENCE_MISATTRIBUTION = "H4_EVIDENCE_MISATTRIBUTION"
    H5_SCOPE_EXPANSION = "H5_SCOPE_EXPANSION"
    H6_CAUSAL_EXTRAPOLATION = "H6_CAUSAL_EXTRAPOLATION"
    SOURCE_NOT_VERIFIED = "SOURCE_NOT_VERIFIED"


class EvidenceAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str
    source_status: str
    resolved: bool
    artifact_type: str | None = None


class ClaimGroundingAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: str = METHOD
    verdict: GroundingVerdict
    risk_level: str
    risk_codes: list[RiskCode] = Field(default_factory=list)
    evidence_coverage: float
    evidence: list[EvidenceAudit] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    human_review_required: bool


class GroundingSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: str = METHOD
    total_claims: int
    grounded_claims: int
    needs_review_claims: int
    rejected_claims: int
    observed_risk_rate_pct: float
    population_hallucination_rate_available: bool = False
    interpretation: str = (
        "该比例仅表示本次运行中被规则标记的断言占比，不代表模型总体幻觉率。"
    )


_POLICY_BY_DOMAIN = {
    "financial": "financial_claim",
    "user": "user_claim",
    "operation": "operation_claim",
}
_EXTRA_INPUTS_BY_DOMAIN = {"financial": {"metric_cost_ratio"}}

_CAUSAL_TOKENS = (
    "因为", "由于", "源于", "得益于", "导致", "驱动了", "证明了",
    "because", "due to", "caused by", "driven by", "proves that",
)
_ABSOLUTE_TOKENS = (
    "所有", "始终", "必然", "完全", "绝对", "一定会", "任何情况下",
    "always", "all users", "guaranteed", "never", "in every case",
)


def _ref_text(ref: ArtifactRef) -> str:
    return ref.to_ref_string()


def _source_status(artifact: Artifact | None) -> str:
    if artifact is None:
        return "missing"
    raw_status = artifact.validation_status
    status = raw_status.value if isinstance(raw_status, ArtifactStatus) else str(raw_status)
    if status in {ArtifactStatus.VALID.value, ArtifactStatus.RECOVERED.value}:
        return "verified"
    if status == ArtifactStatus.SUSPECT.value:
        return "suspect"
    return "blocked"


def _codes_from_semantic(result: SemanticValidationResult) -> set[RiskCode]:
    codes: set[RiskCode] = set()
    for issue in result.issues:
        if "evidence ref not found" in issue:
            codes.add(RiskCode.H2_DANGLING_REFERENCE)
        elif "evidence policy" in issue:
            codes.add(RiskCode.H4_EVIDENCE_MISATTRIBUTION)
        elif any(token in issue for token in (
            "WRONG_NUMERIC_VALUE", "UNSUPPORTED_EXTRA_NUMBER",
            "direction contradiction",
        )):
            codes.add(RiskCode.H3_NUMERIC_DRIFT)
        elif "MISSING_NUMERIC_MENTION" in issue:
            codes.add(RiskCode.H1_UNSUPPORTED_ASSERTION)
        elif "recommendation" in issue.lower():
            codes.add(RiskCode.H5_SCOPE_EXPANSION)
    return codes


def audit_claim_grounding(
    *,
    claim: Artifact,
    domain: str,
    store: ArtifactStore,
) -> ClaimGroundingAudit:
    """Audit one persisted claim without invoking another model or network."""
    refs = list(claim.evidence_ids)
    evidence: list[EvidenceAudit] = []
    resolved_count = 0
    source_issue = False
    for ref in refs:
        artifact = store.get_version(ref.run_id, ref.artifact_id, ref.version)
        status = _source_status(artifact)
        resolved = artifact is not None
        resolved_count += int(resolved)
        source_issue = source_issue or status != "verified"
        evidence.append(EvidenceAudit(
            ref=_ref_text(ref),
            source_status=status,
            resolved=resolved,
            artifact_type=(str(artifact.artifact_type) if artifact else None),
        ))

    policy = _POLICY_BY_DOMAIN.get(domain)
    if policy is None:
        semantic = SemanticValidationResult(
            issues=[f"unsupported claim domain: {domain}"],
        )
    else:
        semantic = validate_claim(
            claim_text=claim.content or "",
            evidence_refs_in_text=refs,
            store=store,
            evidence_policy=policy,
            extra_input_ids=_EXTRA_INPUTS_BY_DOMAIN.get(domain),
        )

    codes = _codes_from_semantic(semantic)
    issues = list(semantic.issues)
    if policy is None:
        codes.add(RiskCode.H1_UNSUPPORTED_ASSERTION)
    text = (claim.content or "").lower()
    if not refs:
        codes.add(RiskCode.H1_UNSUPPORTED_ASSERTION)
        issues.append("claim has no explicit evidence references")
    causal_hits = [token for token in _CAUSAL_TOKENS if token in text]
    if causal_hits:
        codes.add(RiskCode.H6_CAUSAL_EXTRAPOLATION)
        issues.append(
            "causal language is not established by metric evidence: "
            + ", ".join(causal_hits)
        )
    absolute_hits = [token for token in _ABSOLUTE_TOKENS if token in text]
    if absolute_hits:
        codes.add(RiskCode.H5_SCOPE_EXPANSION)
        issues.append(
            "absolute scope exceeds the cited evidence: "
            + ", ".join(absolute_hits)
        )
    if source_issue:
        codes.add(RiskCode.SOURCE_NOT_VERIFIED)
        issues.append("one or more cited sources are not in verified lifecycle state")

    hard_codes = {
        RiskCode.H1_UNSUPPORTED_ASSERTION,
        RiskCode.H2_DANGLING_REFERENCE,
        RiskCode.H3_NUMERIC_DRIFT,
        RiskCode.H4_EVIDENCE_MISATTRIBUTION,
        RiskCode.SOURCE_NOT_VERIFIED,
    }
    if codes & hard_codes:
        verdict = GroundingVerdict.REJECTED
        risk_level = "high"
    elif codes:
        verdict = GroundingVerdict.NEEDS_REVIEW
        risk_level = "medium"
    else:
        verdict = GroundingVerdict.GROUNDED
        risk_level = "low"

    return ClaimGroundingAudit(
        verdict=verdict,
        risk_level=risk_level,
        risk_codes=sorted(codes, key=lambda item: item.value),
        evidence_coverage=(resolved_count / len(refs) if refs else 0.0),
        evidence=evidence,
        issues=issues,
        human_review_required=verdict != GroundingVerdict.GROUNDED,
    )


def summarize_grounding(
    audits: Iterable[ClaimGroundingAudit],
) -> GroundingSummary:
    rows = list(audits)
    grounded = sum(a.verdict == GroundingVerdict.GROUNDED for a in rows)
    review = sum(a.verdict == GroundingVerdict.NEEDS_REVIEW for a in rows)
    rejected = sum(a.verdict == GroundingVerdict.REJECTED for a in rows)
    total = len(rows)
    return GroundingSummary(
        total_claims=total,
        grounded_claims=grounded,
        needs_review_claims=review,
        rejected_claims=rejected,
        observed_risk_rate_pct=round(((review + rejected) / total * 100), 2)
        if total else 0.0,
    )
