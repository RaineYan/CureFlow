from __future__ import annotations

from artifacts import (
    Artifact,
    ArtifactRef,
    ArtifactStatus,
    ArtifactStore,
    ArtifactType,
)
from validation.claim_grounding import (
    METHOD,
    GroundingVerdict,
    RiskCode,
    audit_claim_grounding,
    summarize_grounding,
)


RUN_ID = "grounding_test"


def _metric(store: ArtifactStore, artifact_id: str, value: float) -> Artifact:
    return store.save(Artifact(
        artifact_id=artifact_id,
        artifact_type=ArtifactType.METRIC,
        producer_task="test",
        run_id=RUN_ID,
        value=value,
    ))


def _ref(artifact: Artifact) -> ArtifactRef:
    return ArtifactRef(
        run_id=artifact.run_id,
        artifact_id=artifact.artifact_id,
        version=artifact.version,
    )


def _claim(
    store: ArtifactStore,
    text: str,
    refs: list[ArtifactRef],
    *,
    artifact_id: str = "claim_financial_growth",
) -> Artifact:
    return store.save(Artifact(
        artifact_id=artifact_id,
        artifact_type=ArtifactType.CLAIM,
        producer_task="test",
        run_id=RUN_ID,
        content=text,
        evidence_ids=refs,
    ))


def _financial_store(tmp_path):
    store = ArtifactStore(str(tmp_path))
    revenue = _metric(store, "metric_revenue_growth", 0.6078)
    margin = _metric(store, "metric_profit_margin", 0.4756)
    return store, revenue, margin


def test_grounded_claim_passes_with_verified_sources(tmp_path) -> None:
    store, revenue, margin = _financial_store(tmp_path)
    claim = _claim(
        store,
        "营收同比增长60.78%，利润率达到47.56%。",
        [_ref(revenue), _ref(margin)],
    )
    audit = audit_claim_grounding(claim=claim, domain="financial", store=store)
    assert audit.verdict == GroundingVerdict.GROUNDED
    assert audit.risk_level == "low"
    assert audit.risk_codes == []
    assert audit.evidence_coverage == 1.0
    assert {row.source_status for row in audit.evidence} == {"verified"}


def test_claim_without_evidence_is_rejected(tmp_path) -> None:
    store = ArtifactStore(str(tmp_path))
    claim = _claim(store, "营收表现较好。", [])
    audit = audit_claim_grounding(claim=claim, domain="financial", store=store)
    assert audit.verdict == GroundingVerdict.REJECTED
    assert RiskCode.H1_UNSUPPORTED_ASSERTION in audit.risk_codes
    assert audit.human_review_required is True


def test_dangling_reference_is_distinguished(tmp_path) -> None:
    store = ArtifactStore(str(tmp_path))
    missing = ArtifactRef(
        run_id=RUN_ID, artifact_id="metric_revenue_growth", version=99,
    )
    claim = _claim(store, "营收同比增长60.78%。", [missing])
    audit = audit_claim_grounding(claim=claim, domain="financial", store=store)
    assert RiskCode.H2_DANGLING_REFERENCE in audit.risk_codes
    assert audit.evidence[0].source_status == "missing"
    assert audit.evidence_coverage == 0.0


def test_wrong_number_is_numeric_drift(tmp_path) -> None:
    store, revenue, margin = _financial_store(tmp_path)
    claim = _claim(
        store,
        "营收同比增长99.00%，利润率达到47.56%。",
        [_ref(revenue), _ref(margin)],
    )
    audit = audit_claim_grounding(claim=claim, domain="financial", store=store)
    assert audit.verdict == GroundingVerdict.REJECTED
    assert RiskCode.H3_NUMERIC_DRIFT in audit.risk_codes


def test_extra_number_is_numeric_drift(tmp_path) -> None:
    store, revenue, margin = _financial_store(tmp_path)
    claim = _claim(
        store,
        "营收同比增长60.78%，利润率47.56%，市场份额90%。",
        [_ref(revenue), _ref(margin)],
    )
    audit = audit_claim_grounding(claim=claim, domain="financial", store=store)
    assert RiskCode.H3_NUMERIC_DRIFT in audit.risk_codes


def test_wrong_evidence_identity_is_misattribution(tmp_path) -> None:
    store = ArtifactStore(str(tmp_path))
    user = _metric(store, "metric_user_growth", 0.5)
    claim = _claim(store, "用户增长50%。", [_ref(user)])
    audit = audit_claim_grounding(claim=claim, domain="financial", store=store)
    assert RiskCode.H4_EVIDENCE_MISATTRIBUTION in audit.risk_codes


def test_causal_language_requires_review_not_silent_acceptance(tmp_path) -> None:
    store, revenue, margin = _financial_store(tmp_path)
    claim = _claim(
        store,
        "由于营销升级，营收同比增长60.78%，利润率达到47.56%。",
        [_ref(revenue), _ref(margin)],
    )
    audit = audit_claim_grounding(claim=claim, domain="financial", store=store)
    assert audit.verdict == GroundingVerdict.NEEDS_REVIEW
    assert RiskCode.H6_CAUSAL_EXTRAPOLATION in audit.risk_codes


def test_absolute_scope_requires_review(tmp_path) -> None:
    store, revenue, margin = _financial_store(tmp_path)
    claim = _claim(
        store,
        "所有业务都增长：营收增长60.78%，利润率47.56%。",
        [_ref(revenue), _ref(margin)],
    )
    audit = audit_claim_grounding(claim=claim, domain="financial", store=store)
    assert audit.verdict == GroundingVerdict.NEEDS_REVIEW
    assert RiskCode.H5_SCOPE_EXPANSION in audit.risk_codes


def test_suspect_source_blocks_grounded_label(tmp_path) -> None:
    store, revenue, margin = _financial_store(tmp_path)
    store.update_status(RUN_ID, revenue.artifact_id, ArtifactStatus.SUSPECT)
    claim = _claim(
        store,
        "营收同比增长60.78%，利润率达到47.56%。",
        [_ref(revenue), _ref(margin)],
    )
    audit = audit_claim_grounding(claim=claim, domain="financial", store=store)
    assert audit.verdict == GroundingVerdict.REJECTED
    assert RiskCode.SOURCE_NOT_VERIFIED in audit.risk_codes
    assert audit.evidence[0].source_status == "suspect"


def test_unknown_domain_is_not_treated_as_grounded(tmp_path) -> None:
    store, revenue, _ = _financial_store(tmp_path)
    claim = _claim(store, "营收增长60.78%。", [_ref(revenue)])
    audit = audit_claim_grounding(claim=claim, domain="unknown", store=store)
    assert audit.verdict == GroundingVerdict.REJECTED
    assert RiskCode.H1_UNSUPPORTED_ASSERTION in audit.risk_codes


def test_audit_is_read_only(tmp_path) -> None:
    store, revenue, margin = _financial_store(tmp_path)
    claim = _claim(
        store,
        "营收同比增长60.78%，利润率达到47.56%。",
        [_ref(revenue), _ref(margin)],
    )
    before = claim.model_dump(mode="json")
    audit_claim_grounding(claim=claim, domain="financial", store=store)
    assert store.get(RUN_ID, claim.artifact_id).model_dump(mode="json") == before


def test_summary_labels_observed_rate_not_population_rate(tmp_path) -> None:
    store, revenue, margin = _financial_store(tmp_path)
    grounded = audit_claim_grounding(
        claim=_claim(
            store,
            "营收同比增长60.78%，利润率达到47.56%。",
            [_ref(revenue), _ref(margin)],
            artifact_id="claim_grounded",
        ),
        domain="financial",
        store=store,
    )
    review = audit_claim_grounding(
        claim=_claim(
            store,
            "由于活动，营收增长60.78%，利润率47.56%。",
            [_ref(revenue), _ref(margin)],
            artifact_id="claim_review",
        ),
        domain="financial",
        store=store,
    )
    summary = summarize_grounding([grounded, review])
    assert summary.total_claims == 2
    assert summary.grounded_claims == 1
    assert summary.needs_review_claims == 1
    assert summary.observed_risk_rate_pct == 50.0
    assert summary.population_hallucination_rate_available is False
    assert "不代表模型总体幻觉率" in summary.interpretation


def test_empty_summary_is_defined() -> None:
    summary = summarize_grounding([])
    assert summary.observed_risk_rate_pct == 0.0
    assert summary.total_claims == 0


def test_method_identifier_is_stable(tmp_path) -> None:
    store, revenue, margin = _financial_store(tmp_path)
    claim = _claim(
        store,
        "营收增长60.78%，利润率47.56%。",
        [_ref(revenue), _ref(margin)],
    )
    audit = audit_claim_grounding(claim=claim, domain="financial", store=store)
    assert audit.method == METHOD

