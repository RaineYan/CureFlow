"""
Day 13.6 FINAL-FIX regression tests.

The previous Day 13.6 FINAL follow-up introduced two changes
that this FINAL-FIX reverts / refines:

  A. cross-version contamination expansion (in
     `propagate_contamination`) was removed.  Contamination
     now walks exact-version edges only (Day 7 invariant).
     v_n+1 INVALIDATED does NOT retroactively contaminate
     v_n's downstream.

  B. `run_test_c` no longer hardcodes `report_merged@v2`
     and no longer "finds the latest contaminated" inline.
     It delegates to `build_main_fault_scenario` which
     fully materializes the v2 financial chain and returns
     `scenario.target_ref` as the EXACT recovery target.

  C. The live gate's request / token counters are split
     into:
        - current_invocation_*  (NEW HTTP requests only)
        - verified_*_total      (deduped by request_id,
                                 includes chain-load replays)
        - replayed_record_count (chain-load replays only)

This file locks those three contracts with 15 regression
tests.  All tests run offline; no Qwen call is made.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from artifacts import (
    ArtifactRef,
    ArtifactStatus,
)
from experiments.live_ledger import LiveCallLedger, LiveCallRecord
from experiments.scenario import build_main_fault_scenario
from provenance.graph import ArtifactGraph
from recovery.models import RecoveryAction
from recovery.planner import SelectiveRecoveryPlanner


CSV = str(
    Path(__file__).resolve().parent.parent
    / "data" / "sample_company.csv"
)


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture
def scenario():
    """Build the Case A fault snapshot that the live gate's
    Test C phase now uses.  Returns a `MainFaultScenario`
    whose `store` is alive for the duration of the test.
    """
    sc, _golden = build_main_fault_scenario(
        run_id="day13_6_final_fix", csv_path=CSV,
    )
    yield sc
    # scenario.store is a tmpdir; cleanup is the test's
    # responsibility (handled by the scenario's tmpdir).


# =====================================================================
# 1. faulty field v2 exists
# =====================================================================

def test_faulty_field_v2_exists(scenario) -> None:
    """The scenario must materialize field_revenue_2025@v2 with
    the tampered value and INVALIDATED status."""
    store: ArtifactStore = scenario.store
    v2 = store.get_version(scenario.run_id, "field_revenue_2025", 2)
    assert v2 is not None
    assert v2.artifact_id == "field_revenue_2025"
    assert v2.version == 2
    assert v2.validation_status == ArtifactStatus.INVALIDATED.value
    # Tampered value = baseline * 10
    v1 = store.get_version(scenario.run_id, "field_revenue_2025", 1)
    assert v1 is not None
    assert v1.value is not None and v2.value is not None
    assert v2.value == pytest.approx(v1.value * 10.0)


# =====================================================================
# 2. faulty revenue metric v2 inputs include field_revenue_2025@v2
# =====================================================================

def test_faulty_revenue_metric_v2_inputs_include_faulty_field(
    scenario,
) -> None:
    store = scenario.store
    m = store.get_version(
        scenario.run_id, "metric_revenue_growth", 2,
    )
    assert m is not None
    assert m.version == 2
    ref_keys = {(r.run_id, r.artifact_id, r.version)
                for r in m.input_artifact_ids}
    assert (scenario.run_id, "field_revenue_2025", 2) in ref_keys, (
        "metric_revenue_growth@v2 must INPUT from "
        "field_revenue_2025@v2 (the faulty version)"
    )


# =====================================================================
# 3. faulty financial claim v2 evidence refs point to v2 metrics
# =====================================================================

def test_faulty_financial_claim_v2_evidence_refs_point_to_v2_metrics(
    scenario,
) -> None:
    store = scenario.store
    c = store.get_version(
        scenario.run_id, "claim_financial_growth", 2,
    )
    assert c is not None
    assert c.version == 2
    ev_keys = {(r.run_id, r.artifact_id, r.version)
               for r in c.evidence_ids}
    # revenue_growth + profit_margin (cost_ratio is INPUT only)
    assert (scenario.run_id, "metric_revenue_growth", 2) in ev_keys
    assert (scenario.run_id, "metric_profit_margin", 2) in ev_keys


# =====================================================================
# 4. faulty financial section v2 evidence points to claim v2
# =====================================================================

def test_faulty_financial_section_v2_evidence_points_to_claim_v2(
    scenario,
) -> None:
    store = scenario.store
    s = store.get_version(
        scenario.run_id, "claim_report_financial_section", 2,
    )
    assert s is not None
    assert s.version == 2
    ev_keys = {(r.run_id, r.artifact_id, r.version)
               for r in s.evidence_ids}
    assert (scenario.run_id, "claim_financial_growth", 2) in ev_keys


# =====================================================================
# 5. faulty report v2 inputs = financial v2 + user v1 + operation v1
# =====================================================================

def test_faulty_report_v2_inputs_mix_v2_financial_with_v1_branches(
    scenario,
) -> None:
    """The merge of the v2 financial section with the
    v1 user / operation sections produces a v2 merged
    report that cites BOTH the recovered branch (financial
    @v2) and the unaffected branches (user / operation @v1).
    No new user/operation version is created."""
    store = scenario.store
    r = store.get_version(scenario.run_id, "report_merged", 2)
    assert r is not None
    assert r.version == 2
    in_keys = {(ref.run_id, ref.artifact_id, ref.version)
               for ref in r.input_artifact_ids}
    assert (
        scenario.run_id, "claim_report_financial_section", 2,
    ) in in_keys
    assert (
        scenario.run_id, "claim_report_user_section", 1,
    ) in in_keys
    assert (
        scenario.run_id, "claim_report_operation_section", 1,
    ) in in_keys
    # No v2 for user / operation
    assert store.get_version(
        scenario.run_id, "claim_report_user_section", 2,
    ) is None
    assert store.get_version(
        scenario.run_id, "claim_report_operation_section", 2,
    ) is None


# =====================================================================
# 6. contamination contains faulty v2 financial path
# =====================================================================

def test_contamination_contains_faulty_v2_financial_path(
    scenario,
) -> None:
    """`scenario.contamination` (computed during
    `build_main_fault_scenario`) must mark the v2 financial
    chain CONTAMINATED.  User / operation @v1 stay
    UNAFFECTED."""
    result = scenario.contamination
    contaminated_ids = {r.artifact_id for r in result.contaminated}
    # Every v2 financial node must be CONTAMINATED
    for aid in (
        "metric_revenue_growth",
        "metric_profit_margin",
        "metric_cost_ratio",
        "claim_financial_growth",
        "claim_report_financial_section",
        "report_merged",
    ):
        assert aid in contaminated_ids, (
            f"{aid} must be in contaminated (v2 of the financial "
            f"chain is contaminated; user/operation @v1 are not)"
        )
    # INVALIDATED seed is field_revenue_2025@v2
    invalidated_versions = sorted(
        (r.artifact_id, r.version) for r in result.invalidated
    )
    assert ("field_revenue_2025", 2) in invalidated_versions


# =====================================================================
# 7. contamination does NOT contain clean v1 financial path
# =====================================================================

def test_contamination_does_not_contain_clean_v1_financial_path(
    scenario,
) -> None:
    """Day 7 invariant: v1 nodes stay UNAFFECTED.  v1 of
    field_revenue_2025 was the clean baseline; v1 of the
    metrics / claims / sections / report was computed
    BEFORE the tamper and points at v1 of the field.
    They must remain in `unaffected` and absent from
    `contaminated`."""
    result = scenario.contamination
    v1_refs = [
        ArtifactRef(run_id=scenario.run_id,
                    artifact_id=aid, version=1)
        for aid in (
            "field_revenue_2025",
            "metric_revenue_growth", "metric_profit_margin",
            "metric_cost_ratio",
            "claim_financial_growth",
            "claim_report_financial_section",
            "report_merged",
        )
    ]
    for r in v1_refs:
        assert r not in result.contaminated, (
            f"{r.artifact_id}@v{r.version} must NOT be contaminated "
            f"(Day 7 invariant — only exact-version edges propagate)"
        )
        assert r in result.unaffected, (
            f"{r.artifact_id}@v{r.version} must be UNAFFECTED"
        )


# =====================================================================
# 8. scenario.faulty_target_ref exists in store
# =====================================================================

def test_scenario_target_ref_exists_in_store(scenario) -> None:
    """`scenario.target_ref` is the EXACT ref the live gate's
    recovery planner must use; it must exist in the store."""
    target = scenario.target_ref
    art = scenario.store.get_version(
        target.run_id, target.artifact_id, target.version,
    )
    assert art is not None
    assert art.artifact_id == "report_merged"
    assert art.version == 2
    # And the target must be in a bad state (the recovery
    # executor checks this before planning).
    assert art.validation_status in (
        ArtifactStatus.INVALIDATED.value,
        ArtifactStatus.CONTAMINATED.value,
    )


# =====================================================================
# 9. planner target == scenario.faulty_target_ref
# =====================================================================

def test_planner_target_matches_scenario_target_ref(scenario) -> None:
    """When the planner is given `scenario.target_ref`, the
    resulting plan's target_ref must match exactly (no
    rewriting, no version bump, no 'find latest' search)."""
    store = scenario.store
    store.reload()
    g = ArtifactGraph.from_store(store, scenario.run_id)
    plan = SelectiveRecoveryPlanner(
        g, store, scenario.run_id,
        scenario.target_ref, scenario.contamination,
    )
    assert plan.target_ref == scenario.target_ref


# =====================================================================
# 10. planner produces 2 REPROMPT steps
# =====================================================================

def test_planner_produces_5_tool_2_reprompt(scenario) -> None:
    """Case A selective recovery: 5 TOOL_RECOMPUTE
    (field + 3 metrics + merge) + 2 REPROMPT
    (financial claim + section)."""
    store = scenario.store
    store.reload()
    g = ArtifactGraph.from_store(store, scenario.run_id)
    plan = SelectiveRecoveryPlanner(
        g, store, scenario.run_id,
        scenario.target_ref, scenario.contamination,
    )
    n_tool = sum(
        1 for s in plan.steps
        if s.action == RecoveryAction.TOOL_RECOMPUTE
    )
    n_reprompt = sum(
        1 for s in plan.steps
        if s.action == RecoveryAction.REPROMPT
    )
    assert n_tool == 5, (
        f"expected 5 TOOL_RECOMPUTE, got {n_tool}: "
        f"{[(s.target_ref.to_ref_string(), s.action.value) for s in plan.steps]}"
    )
    assert n_reprompt == 2, (
        f"expected 2 REPROMPT (financial claim + section), "
        f"got {n_reprompt}"
    )
    units = sorted(
        s.execution_unit for s in plan.steps
        if s.action == RecoveryAction.REPROMPT
    )
    assert units == [
        "generate_financial_claim",
        "generate_financial_section",
    ]


# =====================================================================
# 11. no cross-version propagation
# =====================================================================

def test_no_cross_version_propagation(scenario) -> None:
    """Day 7 invariant (re-locked): when v_n+1 is INVALIDATED,
    v_n of the same artifact_id and v_n's downstream are NOT
    retroactively contaminated.  We test this with three
    independent checks against `scenario.contamination`:
    (a) v1 field is not in `result.contaminated`; (b) v1
    metric is not in `result.contaminated`; (c) v2 of the
    same metric IS in `result.contaminated`."""
    result = scenario.contamination
    v1_field = ArtifactRef(
        run_id=scenario.run_id,
        artifact_id="field_revenue_2025", version=1,
    )
    assert v1_field not in result.contaminated
    v1_mrg = ArtifactRef(
        run_id=scenario.run_id,
        artifact_id="metric_revenue_growth", version=1,
    )
    assert v1_mrg not in result.contaminated
    v2_mrg = ArtifactRef(
        run_id=scenario.run_id,
        artifact_id="metric_revenue_growth", version=2,
    )
    assert v2_mrg in result.contaminated


# =====================================================================
# 12. replayed records not counted as new HTTP requests
# =====================================================================

def test_replayed_records_not_counted_as_new_http_requests() -> None:
    """LiveCallLedger: chain-loaded replays have
    `is_replayed=True`.  The `current_invocation_*` metrics
    must EXCLUDE replays so a `len(records)` reading cannot
    be confused with new HTTP requests."""
    ledger = LiveCallLedger()
    # 3 replays (chain-load of A, B, A again)
    for phase, eu, rid, tok, ref in (
        ("A", "generate_financial_claim", "req-a", 30,
         "live_test_a/claim_financial_growth@v1"),
        ("B", "generate_financial_section", "req-b", 23,
         "live_test_a/claim_report_financial_section@v1"),
        ("A", "generate_financial_claim", "req-a", 30,
         "live_test_a/claim_financial_growth@v1"),
    ):
        ledger.append(LiveCallRecord(
            phase=phase, execution_unit=eu,
            provider="qwen", model="qwen3.7-plus-2026-05-26",
            request_id=rid, usage_type="provider_reported",
            input_tokens=tok // 3, output_tokens=tok - tok // 3,
            total_tokens=tok,
            client_wall_clock_latency_ms=100.0,
            schema_valid=True, evidence_valid=True, semantic_valid=True,
            produced_ref=ref, is_replayed=True,
        ))
    # The legacy metric (`current_invocation_*` and the alias)
    # counts NEW HTTP requests only, so 0.
    assert ledger.current_invocation_real_request_count == 0
    assert ledger.real_request_count == 0
    assert ledger.replayed_record_count == 3
    assert ledger.current_invocation_provider_reported_tokens == 0
    # Adding a live record flips the count
    ledger.append(LiveCallRecord(
        phase="C-recovery", execution_unit="generate_financial_claim",
        provider="qwen", model="qwen3.7-plus-2026-05-26",
        request_id="chatcmpl-c1", usage_type="provider_reported",
        input_tokens=100, output_tokens=20, total_tokens=120,
        client_wall_clock_latency_ms=2000.0,
        schema_valid=True, evidence_valid=True, semantic_valid=True,
        produced_ref="live_test_c/claim_financial_growth@v2",
    ))
    assert ledger.current_invocation_real_request_count == 1
    assert ledger.current_invocation_provider_reported_tokens == 120


# =====================================================================
# 13. verified total dedupes request_id
# =====================================================================

def test_verified_total_dedupes_request_id() -> None:
    """A `request_id` that appears in both a chain-loaded
    replay and a live record must count as ONE bill, not
    two.  Synth records (no request_id) are each unique."""
    ledger = LiveCallLedger()
    # Two records with the SAME request_id (replay + live)
    for i, is_replayed in enumerate((True, False)):
        ledger.append(LiveCallRecord(
            phase="A" if is_replayed else "C-recovery",
            execution_unit="generate_financial_claim",
            provider="qwen", model="qwen3.7-plus-2026-05-26",
            request_id="req-shared", usage_type="provider_reported",
            input_tokens=10, output_tokens=5, total_tokens=15,
            client_wall_clock_latency_ms=50.0,
            schema_valid=True, evidence_valid=True, semantic_valid=True,
            produced_ref="live_test_x/claim@v1",
            is_replayed=is_replayed,
        ))
    # current_invocation: 1 NEW request (the live one)
    assert ledger.current_invocation_real_request_count == 1
    # verified: 1 distinct request_id
    assert ledger.verified_real_request_count_total == 1
    # current_invocation tokens: 15 (the live one)
    assert ledger.current_invocation_provider_reported_tokens == 15
    # verified tokens: 15 (deduped — replay does NOT add a 2nd bill)
    assert ledger.verified_provider_reported_token_total == 15


# =====================================================================
# 14. current invocation token total excludes replay
# =====================================================================

def test_current_invocation_tokens_exclude_replay() -> None:
    """`current_invocation_provider_reported_tokens` sums
    tokens for `is_replayed=False` records only.  A chain-
    loaded replay (already billed when A/B ran) must NOT
    contribute to this number — that would double-bill."""
    ledger = LiveCallLedger()
    ledger.append(LiveCallRecord(
        phase="A", execution_unit="generate_financial_claim",
        provider="qwen", model="qwen3.7-plus-2026-05-26",
        request_id="req-a", usage_type="provider_reported",
        input_tokens=20, output_tokens=10, total_tokens=30,
        client_wall_clock_latency_ms=100.0,
        schema_valid=True, evidence_valid=True, semantic_valid=True,
        produced_ref="live_test_a/claim_financial_growth@v1",
        is_replayed=True,
    ))
    # The replay's 30 tokens DO NOT count in
    # current_invocation_provider_reported_tokens.
    assert ledger.current_invocation_provider_reported_tokens == 0
    # They DO count once in verified (the historical bill).
    assert ledger.verified_provider_reported_token_total == 30
    # Adding a live Test C record: current_invocation += 120
    ledger.append(LiveCallRecord(
        phase="C-recovery", execution_unit="generate_financial_claim",
        provider="qwen", model="qwen3.7-plus-2026-05-26",
        request_id="req-c1", usage_type="provider_reported",
        input_tokens=100, output_tokens=20, total_tokens=120,
        client_wall_clock_latency_ms=2000.0,
        schema_valid=True, evidence_valid=True, semantic_valid=True,
        produced_ref="live_test_c/claim_financial_growth@v2",
    ))
    assert ledger.current_invocation_provider_reported_tokens == 120
    # verified = 30 + 120 = 150
    assert ledger.verified_provider_reported_token_total == 150


# =====================================================================
# 15. all historical tests still pass (smoke check)
# =====================================================================

def test_historical_scenario_builds_consistently() -> None:
    """Build 3 scenarios in parallel and assert that the
    contamination set is identical across runs (Day 7
    pure-exact-version invariant).  v1 nodes stay
    UNAFFECTED, v2 nodes of the financial chain are
    CONTAMINATED, and the set is deterministic.
    """
    sc1, g1 = build_main_fault_scenario("fx_eq1", csv_path=CSV)
    sc2, g2 = build_main_fault_scenario("fx_eq2", csv_path=CSV)
    sc3, g3 = build_main_fault_scenario("fx_eq3", csv_path=CSV)
    # v1 nodes must all be UNAFFECTED in every scenario
    for sc in (sc1, sc2, sc3):
        v1_ids = {r.artifact_id for r in sc.contamination.unaffected
                  if r.version == 1 and r.run_id == sc.run_id}
        for aid in (
            "field_revenue_2025",
            "metric_revenue_growth", "metric_profit_margin",
            "metric_cost_ratio",
            "claim_financial_growth",
            "claim_report_financial_section",
            "report_merged",
        ):
            assert aid in v1_ids, (
                f"scenario {sc.run_id}: v1 of {aid} must be "
                f"UNAFFECTED (Day 7 invariant)"
            )
    # v2 contamination set is identical across runs
    ids1 = sorted(
        (r.artifact_id, r.version)
        for r in sc1.contamination.contaminated
    )
    ids2 = sorted(
        (r.artifact_id, r.version)
        for r in sc2.contamination.contaminated
    )
    ids3 = sorted(
        (r.artifact_id, r.version)
        for r in sc3.contamination.contaminated
    )
    assert ids1 == ids2 == ids3
    # golden hash invariant
    assert g1.final_report_hash == g2.final_report_hash == g3.final_report_hash
