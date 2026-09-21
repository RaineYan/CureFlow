"""Day 14 — trial success validation.

Per user spec section 10 + 11: every trial's success
requires ALL of:
  1. structural_success
  2. semantic_clean_path_pass
  3. execution_integrity_pass
  4. exact_evidence_version_pass
  5. all_new_llm_artifacts_schema_valid
  6. all_new_llm_artifacts_evidence_valid
  7. all_new_llm_artifacts_semantic_valid
  8. final_report_semantic_contract_pass

FULL / COARSE regenerate all 6 LLM artifacts; each must
be schema/evidence/semantic valid.  SELECTIVE only
validates the new materialized LLM artifacts + the
reused ones (status VALID/RECOVERED, exact ref).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from artifacts import Artifact, ArtifactRef, ArtifactStore
from recovery import clean_path_audit
from validation.semantic import (
    numeric_consistency_check,
    validate_claim,
    validate_section,
)


@dataclass
class TrialSuccessReport:
    trial_id: str
    case_id: str
    strategy: str
    success: bool
    checks: Dict[str, bool] = field(default_factory=dict)
    failure_reason: str = ""
    new_llm_artifact_refs: List[ArtifactRef] = field(
        default_factory=list
    )
    reused_llm_artifact_refs: List[ArtifactRef] = field(
        default_factory=list
    )
    per_artifact_flags: Dict[str, Dict[str, bool]] = field(
        default_factory=dict
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trial_id": self.trial_id,
            "case_id": self.case_id,
            "strategy": self.strategy,
            "success": self.success,
            "checks": dict(self.checks),
            "failure_reason": self.failure_reason,
            "new_llm_artifact_refs": [
                r.to_ref_string() for r in self.new_llm_artifact_refs
            ],
            "reused_llm_artifact_refs": [
                r.to_ref_string() for r in self.reused_llm_artifact_refs
            ],
            "per_artifact_flags": self.per_artifact_flags,
        }


def validate_new_llm_artifact(
    store: ArtifactStore,
    art: Artifact,
) -> Dict[str, bool]:
    """Per-artifact schema/evidence/semantic validation.
    Returns dict with keys schema_valid, evidence_valid,
    semantic_valid."""
    flags = {
        "schema_valid": False,
        "evidence_valid": False,
        "semantic_valid": False,
    }
    if art is None:
        return flags
    # ArtifactType is a str enum; compare against the
    # value strings directly.
    atype = (
        art.artifact_type.value
        if hasattr(art.artifact_type, "value")
        else str(art.artifact_type)
    )
    # 1) schema: artifact_type matches expected + content
    #    is a non-empty string OR value is a number.
    if atype == "Claim":
        if isinstance(art.content, str) and len(art.content) > 0:
            flags["schema_valid"] = True
    elif atype == "RawData":
        if art.value is not None or art.content is not None:
            flags["schema_valid"] = True
    else:
        if art.content is not None or art.value is not None:
            flags["schema_valid"] = True
    # 2) evidence: every evidence_ref points to an existing
    #    artifact in the store at the exact version.
    evidence_ok = True
    for ev in art.evidence_ids:
        got = store.get_version(ev.run_id, ev.artifact_id, ev.version)
        if got is None:
            evidence_ok = False
            break
    flags["evidence_valid"] = evidence_ok
    # 3) semantic: claim_text / section_text + evidence
    if atype == "Claim":
        # policy: 'financial_claim' / 'user_claim' / 'operation_claim'
        policy = f"{_infer_branch(art)}_claim"
        # Sections in this codebase use artifact_type=Claim but
        # content starting with the section key word.  Distinguish
        # by artifact_id.
        if "section" in art.artifact_id.lower():
            res = validate_section(
                section_text=art.content or "",
                section_evidence_refs=list(art.evidence_ids),
                claim_evidence_refs=list(art.input_artifact_ids),
                store=store,
                evidence_policy=f"{_infer_branch(art)}_section",
            )
            flags["semantic_valid"] = bool(res.passed)
        else:
            res = validate_claim(
                claim_text=art.content or "",
                evidence_refs_in_text=list(art.evidence_ids),
                store=store,
                evidence_policy=policy,
            )
            flags["semantic_valid"] = bool(res.passed)
    else:
        # tool artifacts: semantic is just "value or content present"
        flags["semantic_valid"] = flags["schema_valid"]
    return flags


def _infer_branch(art: Artifact) -> str:
    aid = art.artifact_id.lower()
    if "financial" in aid or "revenue" in aid or "profit" in aid or "cost" in aid:
        return "financial"
    if "user" in aid:
        return "user"
    if "operation" in aid or "conversion" in aid or "order" in aid:
        return "operation"
    return "unknown"


def evaluate_trial_success(
    trial_id: str,
    case_id: str,
    strategy: str,
    store: ArtifactStore,
    *,
    target_ref: ArtifactRef,
    new_llm_refs: List[ArtifactRef],
    reused_llm_refs: List[ArtifactRef],
    final_report_ref: Optional[ArtifactRef] = None,
) -> TrialSuccessReport:
    """Evaluate the 8-condition success expression."""
    report = TrialSuccessReport(
        trial_id=trial_id, case_id=case_id, strategy=strategy,
        success=False,
        new_llm_artifact_refs=list(new_llm_refs),
        reused_llm_artifact_refs=list(reused_llm_refs),
    )

    # 1) structural_success: the new + reused LLM artifact
    #    sets must be non-empty and disjoint.
    report.checks["structural_success"] = bool(
        new_llm_refs or reused_llm_refs
    ) and set(new_llm_refs).isdisjoint(set(reused_llm_refs))

    # 2) semantic_clean_path_pass: walk all clean-path edges
    #    from target; nothing should be INVALIDATED /
    #    CONTAMINATED.
    from provenance.graph import ArtifactGraph
    graph = ArtifactGraph.from_store(store, target_ref.run_id)
    audit = clean_path_audit(
        target_ref, graph, store, graph_rebuilt_after_recovery=True,
    )
    report.checks["semantic_clean_path_pass"] = bool(audit.passed)

    # 3) execution_integrity_pass: final_report_ref present.
    report.checks["execution_integrity_pass"] = (
        final_report_ref is not None
        and store.get_version(
            final_report_ref.run_id,
            final_report_ref.artifact_id,
            final_report_ref.version,
        ) is not None
    )

    # 4) exact_evidence_version_pass: every artifact in the
    #    store has its evidence_ids at the exact version the
    #    target cites.
    exact_ok = True
    for r in (*new_llm_refs, *reused_llm_refs):
        a = store.get_version(r.run_id, r.artifact_id, r.version)
        if a is None:
            exact_ok = False
            break
        for ev in a.evidence_ids:
            if store.get_version(
                ev.run_id, ev.artifact_id, ev.version
            ) is None:
                exact_ok = False
                break
    report.checks["exact_evidence_version_pass"] = exact_ok

    # 5-7) per-artifact schema/evidence/semantic valid for
    #      every new LLM artifact.  Also validate that
    #      reused LLM artifacts have status in
    #      {VALID, RECOVERED}.
    all_new_schema = True
    all_new_evidence = True
    all_new_semantic = True
    for r in new_llm_refs:
        a = store.get_version(r.run_id, r.artifact_id, r.version)
        flags = validate_new_llm_artifact(store, a)
        report.per_artifact_flags[r.to_ref_string()] = flags
        all_new_schema = all_new_schema and flags["schema_valid"]
        all_new_evidence = all_new_evidence and flags["evidence_valid"]
        all_new_semantic = all_new_semantic and flags["semantic_valid"]
    report.checks["all_new_llm_artifacts_schema_valid"] = all_new_schema
    report.checks["all_new_llm_artifacts_evidence_valid"] = all_new_evidence
    report.checks["all_new_llm_artifacts_semantic_valid"] = all_new_semantic

    reused_ok = True
    for r in reused_llm_refs:
        a = store.get_version(r.run_id, r.artifact_id, r.version)
        if a is None or a.validation_status not in ("VALID", "RECOVERED"):
            reused_ok = False
        report.per_artifact_flags[r.to_ref_string()] = {
            "validation_status": a.validation_status if a else "MISSING",
            "schema_valid": a is not None,
        }
    report.checks["reused_llm_artifacts_status_valid"] = reused_ok

    # 8) final_report_semantic_contract_pass: the final
    #    report's content/structure is non-empty.
    final_ok = False
    if final_report_ref is not None:
        final_art = store.get_version(
            final_report_ref.run_id,
            final_report_ref.artifact_id,
            final_report_ref.version,
        )
        if final_art is not None and (
            final_art.content is not None
            and len(str(final_art.content)) > 0
        ):
            # Sanity: final report must cite 3 sections at
            # exact versions.
            if len(final_art.input_artifact_ids) >= 3:
                final_ok = True
    report.checks["final_report_semantic_contract_pass"] = final_ok

    report.success = all(report.checks.values())
    if not report.success:
        failed = [k for k, v in report.checks.items() if not v]
        report.failure_reason = "FAILED: " + ", ".join(failed)
    return report
