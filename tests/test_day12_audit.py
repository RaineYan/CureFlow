"""
Tests for Day 12 — Final Metric Audit + Real-Provider Readiness Gate.

Covers:
  - ARR denominator rebuilt from PrerequisiteAuditRecord
  - CLEAN / SOURCE / RECOVERED categorization via replacement_map
  - missing_clean_prerequisite_refs empty when ARR == 1
  - TAF denominator renamed to reference_recovery_task_count
  - LLMResponse carries usage_is_synthetic=True from FakeLLM
  - RealProviderReadinessReport.ready is True
  - no hardcoded API keys
  - exact ArtifactRef audit in JSON
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from experiments import (
    CATEGORY_CLEAN,
    CATEGORY_RECOVERED,
    CATEGORY_SOURCE,
    PrerequisiteAuditRecord,
    RealProviderReadinessReport,
    SOURCE_OF_TRUTH_ARTIFACT,
    build_main_fault_scenario,
    build_prerequisite_audit,
    build_readiness_report,
    build_visits_fault_scenario,
    compute_arr_v2,
    derive_canonical_replacement_map,
    derive_clean_prerequisite_identities,
    derive_clean_prerequisite_refs,
    derive_missing_clean_prerequisite_refs,
    run_full_rerun_recovery,
    run_one_round,
    run_selective_recovery,
)
from experiments.scenario import DEFAULT_CSV
from recovery import SelectiveRecoveryPlanner
from provenance.graph import ArtifactGraph
from llm import LLMResponse, default_workflow_llm


CSV = DEFAULT_CSV


# =====================================================================
# 1-5: Case A prerequisite audit
# =====================================================================

def test_main_required_clean_prerequisite_refs_derived() -> None:
    sc, g = build_main_fault_scenario("d12_a_prereq")
    r = run_selective_recovery(sc, g)
    # main case: 4 unique CLEAN prerequisites
    assert sorted(set(ref.artifact_id for ref in r.required_clean_prerequisite_refs)) == \
        sorted(["field_revenue_2024", "field_cost_2025",
                "claim_report_user_section", "claim_report_operation_section"])


def test_main_clean_prerequisite_identity_count_matches_audit() -> None:
    sc, g = build_main_fault_scenario("d12_a_count")
    r = run_selective_recovery(sc, g)
    # Every row in prerequisite_audit that is CLEAN must show up
    # in required_clean_prerequisite_refs (possibly multiple times
    # across steps; the identity count is the unique number).
    audit = r.prerequisite_audit
    clean_rows = [row for row in audit if row["category"] == CATEGORY_CLEAN]
    unique_clean = {row["resolved_input_ref"]["artifact_id"] for row in clean_rows}
    assert len(unique_clean) == 4
    assert r.required_clean_prerequisite_count == 4


def test_main_active_reuse_refs_come_from_event_stream() -> None:
    sc, g = build_main_fault_scenario("d12_a_reuse")
    r = run_selective_recovery(sc, g)
    # Every ref in actively_reused_clean_prerequisite_refs must
    # appear in some event's input_refs or reused_refs.
    actively_used = {ref.to_ref_string() for ref in r.actively_reused_clean_prerequisite_refs}
    found: set = set()
    for e in r.events:
        for ref in e.input_refs:
            found.add(ref.to_ref_string())
        for ref in e.reused_refs:
            found.add(ref.to_ref_string())
    assert actively_used.issubset(found), (
        f"missing from events: {actively_used - found}"
    )


def test_main_missing_clean_prereq_empty() -> None:
    sc, g = build_main_fault_scenario("d12_a_miss")
    r = run_selective_recovery(sc, g)
    assert r.active_reuse_rate == 1.0
    assert len(r.missing_clean_prerequisite_refs) == 0


def test_main_arr_numerator_denominator_correct() -> None:
    sc, g = build_main_fault_scenario("d12_a_arr")
    r = run_selective_recovery(sc, g)
    # numerator == denominator (all 4 actively reused)
    assert r.active_reuse_rate == pytest.approx(1.0)
    assert len(r.actively_reused_clean_prerequisite_refs) == 5
    assert len(r.required_clean_prerequisite_refs) == 5
    # unique identities: 4 each
    num_ids = {r.artifact_id for r in r.actively_reused_clean_prerequisite_refs}
    den_ids = {r.artifact_id for r in r.required_clean_prerequisite_refs}
    assert len(num_ids) == 4
    assert len(den_ids) == 4
    assert num_ids == den_ids


# =====================================================================
# 6-9: Case B prerequisite audit
# =====================================================================

def test_case_b_required_clean_prerequisite_refs_derived() -> None:
    sc, g = build_visits_fault_scenario("d12_b_prereq")
    r = run_selective_recovery(sc, g)
    assert sorted(set(ref.artifact_id for ref in r.required_clean_prerequisite_refs)) == \
        sorted(["field_orders_2025", "metric_order_growth",
                "claim_report_financial_section", "claim_report_user_section"])


def test_case_b_active_reuse_refs_correct() -> None:
    sc, g = build_visits_fault_scenario("d12_b_reuse")
    r = run_selective_recovery(sc, g)
    actively_used = {ref.artifact_id for ref in r.actively_reused_clean_prerequisite_refs}
    assert actively_used == {
        "field_orders_2025", "metric_order_growth",
        "claim_report_financial_section", "claim_report_user_section",
    }


def test_case_b_missing_clean_prereq_empty() -> None:
    sc, g = build_visits_fault_scenario("d12_b_miss")
    r = run_selective_recovery(sc, g)
    assert r.active_reuse_rate == 1.0
    assert len(r.missing_clean_prerequisite_refs) == 0


def test_case_b_arr_correct() -> None:
    sc, g = build_visits_fault_scenario("d12_b_arr")
    r = run_selective_recovery(sc, g)
    assert r.active_reuse_rate == 1.0
    assert r.required_clean_prerequisite_count == 4


# =====================================================================
# 10-12: preserved vs active reuse, skipped, source
# =====================================================================

def test_preserved_not_equal_active_reuse() -> None:
    sc, g = build_main_fault_scenario("d12_pres")
    r = run_selective_recovery(sc, g)
    # SELECTIVE preserves 14 unaffected identities; only 4 are
    # actively consumed as clean prereqs.
    assert r.preserved_unaffected_count == 14
    assert r.actively_reused_clean_prerequisite_count == 5  # ref count (5 rows)
    assert r.skipped_irrelevant_count > 0


def test_skipped_artifact_not_in_arr_numerator() -> None:
    sc, g = build_main_fault_scenario("d12_skip")
    r = run_selective_recovery(sc, g)
    # A skipped identity must NOT appear in actively_reused_clean_prerequisite_refs
    skipped = set(r.skipped_irrelevant_artifact_identities)
    actively_used = {ref.artifact_id for ref in r.actively_reused_clean_prerequisite_refs}
    assert actively_used.isdisjoint(skipped)


def test_raw_data_only_source_prerequisite() -> None:
    sc, g = build_main_fault_scenario("d12_raw")
    r = run_selective_recovery(sc, g)
    for row in r.prerequisite_audit:
        if row["original_input_ref"]["artifact_id"] == SOURCE_OF_TRUTH_ARTIFACT:
            assert row["category"] == CATEGORY_SOURCE


# =====================================================================
# 13-14: replaced input, exact ArtifactRef
# =====================================================================

def test_replaced_input_belongs_to_recovered_prerequisite() -> None:
    sc, g = build_main_fault_scenario("d12_rep")
    r = run_selective_recovery(sc, g)
    # Every recovered row must have original.version != resolved.version
    for row in r.prerequisite_audit:
        if row["category"] == CATEGORY_RECOVERED:
            assert row["original_input_ref"]["version"] != \
                   row["resolved_input_ref"]["version"]


def test_exact_artifactref_version_preserved() -> None:
    sc, g = build_main_fault_scenario("d12_ver")
    r = run_selective_recovery(sc, g)
    # Every required_clean_prerequisite_refs entry should have a
    # concrete version (>= 1)
    for ref in r.required_clean_prerequisite_refs:
        assert ref.version >= 1


# =====================================================================
# 15: prerequisite audit JSON roundtrip
# =====================================================================

def test_prerequisite_audit_json_roundtrip(tmp_path: Path) -> None:
    sc, g = build_main_fault_scenario("d12_json")
    r = run_selective_recovery(sc, g)
    # audit is already a list[dict]; ensure it can serialize
    s = json.dumps(r.prerequisite_audit, ensure_ascii=False)
    parsed = json.loads(s)
    assert len(parsed) == len(r.prerequisite_audit)
    for row in parsed:
        assert "step_order" in row
        assert "execution_unit" in row
        assert "original_input_ref" in row
        assert "resolved_input_ref" in row
        assert "category" in row
        assert "actively_reused" in row
        assert "reason" in row


# =====================================================================
# 16-17: TAF renaming
# =====================================================================

def test_taf_uses_reference_recovery_task_count() -> None:
    sc, g = build_main_fault_scenario("d12_taf")
    r = run_selective_recovery(sc, g)
    # Day 12: reference_recovery_task_count == len(SELECTIVE plan)
    assert r.reference_recovery_task_count == 7
    # DEPRECATED alias still set
    assert r.minimum_required_execution_unit_count == 7
    # TAF denominator is the reference count
    assert r.task_amplification_factor == pytest.approx(7 / 7)


def test_no_global_minimum_claim() -> None:
    """Definitions must NOT use the words 'global minimum' or
    'theoretical minimum' to describe the TAF denominator."""
    from experiments import DEFINITIONS
    body = DEFINITIONS.get("TAF_renaming", "")
    assert "global minimum" not in body.lower()
    assert "theoretical minimum" not in body.lower()
    assert "reference_recovery_task_count" in body


# =====================================================================
# 18: UPR audit
# =====================================================================

def test_upr_numerator_denominator_audit() -> None:
    sc, g = build_main_fault_scenario("d12_upr")
    r = run_selective_recovery(sc, g)
    # UPR numerator: preserved_unaffected (set of identities)
    # UPR denominator: available_unaffected (set of identities)
    assert r.preserved_unaffected_count <= r.available_unaffected_count
    assert r.unaffected_preservation_rate == pytest.approx(
        r.preserved_unaffected_count / max(1, r.available_unaffected_count)
    )


# =====================================================================
# 19-20: usage_is_synthetic
# =====================================================================

def test_fake_usage_is_synthetic_true() -> None:
    llm = default_workflow_llm()
    for unit in ("generate_financial_claim", "generate_user_claim",
                 "generate_operation_claim", "generate_financial_section",
                 "generate_user_section", "generate_operation_section"):
        r = llm.generate("test", task_name=unit)
        assert r.usage_is_synthetic is True, f"{unit} returned usage_is_synthetic=False"
        assert r.provider == "fake"


def test_provider_readiness_gate_pass() -> None:
    rep = build_readiness_report()
    assert isinstance(rep, RealProviderReadinessReport)
    assert rep.ready is True
    # every individual check passes
    for c in rep.checks:
        assert c.passed, f"check failed: {c.name}: {c.detail}"


# =====================================================================
# 21: no hardcoded keys
# =====================================================================

def test_no_hardcoded_api_keys() -> None:
    rep = build_readiness_report()
    sec = next(c for c in rep.checks if c.name == "provider_secret_not_hardcoded")
    assert sec.passed, f"hardcoded key detected: {sec.detail}"


# =====================================================================
# 22: Day 1-11 tests still pass — covered by full pytest run
# Extra: the prerequisite audit on FULL/COARSE (not just SELECTIVE)
# =====================================================================

def test_full_has_audit_too() -> None:
    """FULL has no SELECTIVE plan per se, but the audit is still
    built from the SELECTIVE plan structure (with canonical
    replacement_map).  It must produce the same CLEAN set as
    SELECTIVE for the same scenario, with 0 actively_reused."""
    sc1, g1 = build_main_fault_scenario("d12_full")
    r1 = run_full_rerun_recovery(sc1, g1, CSV)
    sc2, g2 = build_main_fault_scenario("d12_full2")
    r2 = run_selective_recovery(sc2, g2)
    # Both have the same required clean prereqs (plan structure is shared)
    assert r1.required_clean_prerequisite_count == r2.required_clean_prerequisite_count
    # But FULL's numerator is 0 (re-materializes everything)
    assert r1.active_reuse_rate == 0.0
    assert r2.active_reuse_rate == 1.0
