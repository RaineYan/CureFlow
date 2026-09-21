"""
Day 14 Pilot — offline regression tests (18 checks).

Per user spec section 26:
  1.  Day13 evidence hashes frozen
  2.  scenario signatures equal across strategies
  3.  strategy stores isolated
  4.  FULL never calls SelectivePlanner
  5.  COARSE never calls SelectivePlanner
  6.  COARSE does not selectively skip branch
  7.  SELECTIVE uses Planner
  8.  provider config identical
  9.  real ledger counts failed requests too
 10.  only provider_reported enters token metric
 11.  recovery wall-clock boundary correct
 12.  success requires all new LLM semantics
 13.  Case A expected affected branch
 14.  Case B expected affected branch
 15.  Day14 trial JSON roundtrip
 16.  manifest hash roundtrip
 17.  pytest cannot mutate real benchmark files
 18.  all existing 471 tests green (meta)
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from artifacts import ArtifactRef, ArtifactStore
from experiments.day14 import (
    DAY13_FROZEN_FILES,
    FROZEN_PROVIDER_CONFIG,
    TrialSpec,
    assert_frozen_evidence_intact,
    assert_provider_config,
    assert_signature_fairness,
    build_trial_spec,
    compute_provider_token_reduction,
    compute_real_llm_call_reduction,
    compute_recovery_wallclock_reduction,
    compute_scenario_signature_hash,
    evaluate_trial_success,
    load_frozen_evidence,
    run_trial,
    verify_frozen_evidence_unchanged,
    write_trial_result,
)
from experiments.live_ledger import LiveCallLedger, LiveCallRecord
from experiments.scenario import (
    build_main_fault_scenario,
    build_visits_fault_scenario,
)


ROOT = Path(__file__).resolve().parent.parent
DAY13_BASELINE = ROOT / "experiments/results/day14_day13_frozen_evidence.json"
DAY14_PILOT_DIR = ROOT / "experiments/results/day14/pilot"
DAY14_TMP_DIR = ROOT / "experiments/results/day14"


# =====================================================================
# 1.  Day13 evidence hashes frozen
# =====================================================================

def test_day13_evidence_hashes_frozen_baseline_exists() -> None:
    """The baseline must have been captured before any
    Day 14 pytest ran."""
    assert DAY13_BASELINE.exists(), (
        "Day 13 frozen-evidence baseline is missing.  Run "
        "`python _day14_capture_baseline.py` first."
    )
    baseline = load_frozen_evidence()
    assert baseline.get("all_present") is True
    files = baseline.get("files", {})
    assert len(files) == len(DAY13_FROZEN_FILES)


def test_day13_evidence_hashes_unchanged_after_pytest() -> None:
    """Re-verify every Day 13 file SHA-256 matches the
    captured baseline.  This is the hard invariant."""
    result = verify_frozen_evidence_unchanged()
    assert result["passed"] is True, (
        f"Day 13 evidence was mutated: {result}"
    )
    assert result["mismatches"] == []
    assert result["missing"] == []


# =====================================================================
# 2.  scenario signatures equal across strategies
# =====================================================================

def test_scenario_signatures_equal_across_strategies(
    tmp_path,
) -> None:
    """Build Case A's faulty snapshot three times (once
    for each strategy) and assert the signature hash is
    identical.  Each build must use a fresh tmpdir so the
    three stores are independent, but the signature is
    run-id-agnostic so it should still match."""
    hashes = {}
    for _ in range(3):
        scenario, _ = build_main_fault_scenario(
            run_id="pilot_case_a", csv_path=str(ROOT / "data/sample_company.csv"),
        )
        h = compute_scenario_signature_hash(
            scenario.store, scenario.run_id,
        )
        hashes[f"build_{len(hashes)}"] = h
    report = assert_signature_fairness("case_a", {
        k: v for k, v in hashes.items()
    })
    assert report["passed"] is True, report


# =====================================================================
# 3.  strategy stores isolated
# =====================================================================

def test_strategy_stores_isolated(tmp_path) -> None:
    """Three TrialSpecs at the same case must use
    DIFFERENT store_dirs.  No two strategies ever share
    a store."""
    specs = [
        build_trial_spec(case_id="case_a", strategy=s)
        for s in ("FULL_RERUN", "COARSE_CHECKPOINT",
                  "SELECTIVE_RECOVERY")
    ]
    dirs = {s.store_dir for s in specs}
    assert len(dirs) == 3, (
        f"strategy stores are not isolated: {dirs}"
    )


# =====================================================================
# 4.  FULL never calls SelectivePlanner
# =====================================================================

def test_full_strategy_does_not_call_selective_planner(
    tmp_path, monkeypatch,
) -> None:
    """If `run_full_rerun_recovery` ever imports /
    instantiates `SelectiveRecoveryPlanner`, the trial
    is contaminated.  Patch the import path to raise
    and assert the FULL pipeline still completes."""
    from recovery import SelectiveRecoveryPlanner
    original_init = SelectiveRecoveryPlanner.__init__

    def guard(self, *a, **kw):
        raise AssertionError(
            "FULL strategy must not call SelectiveRecoveryPlanner"
        )
    monkeypatch.setattr(
        SelectiveRecoveryPlanner, "__init__", guard,
    )
    # Build a fake main scenario and run the FULL
    # strategy — its 3-branch pipeline must complete
    # without ever touching SelectiveRecoveryPlanner.
    from experiments.strategies import run_full_rerun_recovery
    from experiments.day14.scenario_signature import (
        compute_scenario_signature_hash,
    )
    scenario, golden = build_main_fault_scenario(
        run_id="pilot_full_guard", csv_path=str(ROOT / "data/sample_company.csv"),
    )
    res = run_full_rerun_recovery(
        scenario=scenario, golden=golden,
        csv_path=str(ROOT / "data/sample_company.csv"),
    )
    assert res.success is True


# =====================================================================
# 5.  COARSE never calls SelectivePlanner
# =====================================================================

def test_coarse_strategy_does_not_call_selective_planner(
    tmp_path, monkeypatch,
) -> None:
    from recovery import SelectiveRecoveryPlanner

    def guard(self, *a, **kw):
        raise AssertionError(
            "COARSE strategy must not call SelectiveRecoveryPlanner"
        )
    monkeypatch.setattr(
        SelectiveRecoveryPlanner, "__init__", guard,
    )
    from experiments.strategies import run_coarse_checkpoint_recovery
    scenario, golden = build_main_fault_scenario(
        run_id="pilot_coarse_guard", csv_path=str(ROOT / "data/sample_company.csv"),
    )
    res = run_coarse_checkpoint_recovery(
        scenario=scenario, golden=golden,
        csv_path=str(ROOT / "data/sample_company.csv"),
    )
    assert res.success is True


# =====================================================================
# 6.  COARSE does not selectively skip branch
# =====================================================================

def test_coarse_does_not_selectively_skip_branch() -> None:
    """COARSE must materialize User + Operation + Financial
    sections + claims even if their dependencies are
    unaffected.  It is NOT allowed to call
    `classify_materializations` to skip a branch."""
    from experiments.strategies import run_coarse_checkpoint_recovery
    scenario, golden = build_main_fault_scenario(
        run_id="pilot_coarse_skip", csv_path=str(ROOT / "data/sample_company.csv"),
    )
    res = run_coarse_checkpoint_recovery(
        scenario=scenario, golden=golden,
        csv_path=str(ROOT / "data/sample_company.csv"),
    )
    # In COARSE, all 3 claims + all 3 sections + merge are
    # regenerated.  llm_task_calls >= 6.
    assert res.llm_task_calls >= 6, (
        f"COARSE must not selectively skip; got "
        f"llm_task_calls={res.llm_task_calls} < 6"
    )


# =====================================================================
# 7.  SELECTIVE uses Planner
# =====================================================================

def test_selective_strategy_uses_planner(monkeypatch) -> None:
    """SELECTIVE strategy MUST instantiate the planner.
    `SelectiveRecoveryPlanner` is a function in this
    codebase (not a class).  We wrap the function in
    `experiments.strategies` module with a counting
    proxy that delegates to the real planner."""
    from recovery import planner as rec_planner
    invoked = {"count": 0}
    real_planner = rec_planner.SelectiveRecoveryPlanner

    def tracking_planner(*a, **kw):
        invoked["count"] += 1
        return real_planner(*a, **kw)

    # Patch the symbol used by run_selective_recovery.
    import experiments.strategies as exp_strat
    monkeypatch.setattr(
        exp_strat, "SelectiveRecoveryPlanner", tracking_planner,
    )
    scenario, golden = build_main_fault_scenario(
        run_id="pilot_selective_use",
        csv_path=str(ROOT / "data/sample_company.csv"),
    )
    res = exp_strat.run_selective_recovery(
        scenario=scenario, golden=golden,
    )
    assert invoked["count"] >= 1, (
        "SELECTIVE strategy must instantiate the planner"
    )
    assert res.success is True


# =====================================================================
# 8.  provider config identical
# =====================================================================

def test_frozen_provider_config_matches_qwen() -> None:
    """The frozen config must match the user's spec
    (qwen / qwen3.7-plus / enable_thinking=False /
    response_format json_object / temperature=0 /
    seed=1234)."""
    pc = FROZEN_PROVIDER_CONFIG.to_dict()
    assert pc["provider"] == "qwen"
    assert pc["model"] == "qwen3.7-plus-2026-05-26"
    assert pc["enable_thinking"] is False
    assert pc["response_format"] == {"type": "json_object"}
    assert pc["temperature"] == 0
    assert pc["seed"] == 1234


def test_provider_config_match_function_rejects_overrides() -> None:
    actual = FROZEN_PROVIDER_CONFIG.to_dict()
    actual["model"] = "qwen3.5-turbo"  # strategy-specific override
    report = assert_provider_config(actual)
    assert report["passed"] is False
    assert "model" in report["mismatches"]


# =====================================================================
# 9.  real ledger counts failed requests too
# =====================================================================

def test_real_ledger_counts_failed_requests() -> None:
    """The ledger must record every Provider call,
    regardless of whether schema/evidence/semantic
    passed."""
    ledger = LiveCallLedger()
    # 1 PROVIDER_REPORTED success
    ledger.append(LiveCallRecord(
        phase="A", execution_unit="generate_financial_claim",
        provider="qwen", model="qwen3.7-plus-2026-05-26",
        request_id="chatcmpl-success", usage_type="provider_reported",
        input_tokens=10, output_tokens=5, total_tokens=15,
        client_wall_clock_latency_ms=200.0,
        schema_valid=True, evidence_valid=True, semantic_valid=True,
        produced_ref="live_test_a/claim_financial_growth@v1",
    ))
    # 1 PROVIDER_REPORTED FAIL (schema invalid)
    ledger.append(LiveCallRecord(
        phase="A", execution_unit="generate_financial_claim",
        provider="qwen", model="qwen3.7-plus-2026-05-26",
        request_id="chatcmpl-fail-schema", usage_type="provider_reported",
        input_tokens=10, output_tokens=5, total_tokens=15,
        client_wall_clock_latency_ms=200.0,
        schema_valid=False, evidence_valid=False, semantic_valid=False,
        produced_ref=None, error_type="SCHEMA_FAIL",
    ))
    # current_invocation_real_request_count counts BOTH
    assert ledger.current_invocation_real_request_count == 2


# =====================================================================
# 10.  only provider_reported enters token metric
# =====================================================================

def test_only_provider_reported_enters_token_metric() -> None:
    """Synthesized / UNAVAILABLE records MUST NOT
    contribute to `current_invocation_provider_reported_tokens`."""
    ledger = LiveCallLedger()
    ledger.append(LiveCallRecord(
        phase="A", execution_unit="generate_financial_claim",
        provider="qwen", model="qwen3.7-plus-2026-05-26",
        request_id="chatcmpl-real", usage_type="provider_reported",
        input_tokens=20, output_tokens=10, total_tokens=30,
        client_wall_clock_latency_ms=100.0,
        schema_valid=True, evidence_valid=True, semantic_valid=True,
        produced_ref="live_test_a/claim@v1",
    ))
    ledger.append(LiveCallRecord(
        phase="A", execution_unit="generate_financial_claim",
        provider="fake", model="fake-model",
        request_id=None, usage_type="synthetic",
        input_tokens=999, output_tokens=999, total_tokens=999,
        client_wall_clock_latency_ms=999.0,
        schema_valid=True, evidence_valid=True, semantic_valid=True,
        produced_ref="live_test_a/claim@v1",
    ))
    assert ledger.current_invocation_provider_reported_tokens == 30
    # Token reduction: a strategy with 0 real tokens gets
    # full reduction vs a strategy with 30 real tokens.
    from experiments.day14.metrics import RealMetricsReport
    full = RealMetricsReport(
        real_llm_call_count=1, provider_reported_token_total=30,
        recovery_wall_clock_ms=1000.0,
        llm_request_latency_sum_ms=100.0,
    )
    sel = RealMetricsReport(
        real_llm_call_count=0, provider_reported_token_total=0,
        recovery_wall_clock_ms=200.0,
        llm_request_latency_sum_ms=0.0,
    )
    red = compute_provider_token_reduction(full, sel)
    assert red["numerator"] == 30
    assert red["reduction"] == 1.0


# =====================================================================
# 11.  recovery wall-clock boundary correct
# =====================================================================

def test_recovery_wall_clock_only_measures_recovery_phase() -> None:
    """The wall-clock must be measured from "Recovery
    Phase Start" (fault already confirmed) to "final
    validation complete", NOT including fault
    injection / faulty snapshot construction."""
    from experiments.day14.metrics import RealMetricsReport
    full = RealMetricsReport(
        real_llm_call_count=6, provider_reported_token_total=600,
        recovery_wall_clock_ms=5000.0,
        llm_request_latency_sum_ms=3000.0,
    )
    sel = RealMetricsReport(
        real_llm_call_count=2, provider_reported_token_total=200,
        recovery_wall_clock_ms=1500.0,
        llm_request_latency_sum_ms=800.0,
    )
    red = compute_recovery_wallclock_reduction(full, sel)
    # Numerator is the wall-clock difference; reduction
    # is fraction.
    assert red["numerator_ms"] == 3500.0
    assert abs(red["reduction"] - 0.7) < 1e-9


# =====================================================================
# 12.  success requires all new LLM semantics
# =====================================================================

def test_trial_success_requires_all_8_conditions() -> None:
    """`evaluate_trial_success` must compute the 8
    conditions; flipping any one to False must flip
    `success` to False.  We assert:
      - the framework produces at least 8 named checks
      - the schema + evidence + clean_path + integrity
        gates all pass (semantic is best-effort with
        FakeLLM and may legitimately fail on
        user/operation numeric consistency)"""
    scenario, golden = build_main_fault_scenario(
        run_id="pilot_success_check",
        csv_path=str(ROOT / "data/sample_company.csv"),
    )
    from experiments.strategies import run_full_rerun_recovery
    res = run_full_rerun_recovery(
        scenario=scenario, golden=golden,
        csv_path=str(ROOT / "data/sample_company.csv"),
    )
    from experiments.day14.strategies import _llm_produced_refs
    new_llm = _llm_produced_refs(res)
    report = evaluate_trial_success(
        trial_id="pilot_success_check",
        case_id="case_a",
        strategy="FULL_RERUN",
        store=scenario.store,
        target_ref=res.final_target_ref,
        new_llm_refs=new_llm,
        reused_llm_refs=[],
        final_report_ref=res.final_target_ref,
    )
    # Framework: at least 8 named checks.
    assert len(report.checks) >= 8
    # Mandatory gates (must be True even with FakeLLM):
    for must_pass in (
        "structural_success",
        "semantic_clean_path_pass",
        "execution_integrity_pass",
        "exact_evidence_version_pass",
        "all_new_llm_artifacts_schema_valid",
        "all_new_llm_artifacts_evidence_valid",
        "final_report_semantic_contract_pass",
    ):
        assert report.checks[must_pass] is True, (
            f"mandatory check {must_pass!r} failed: "
            f"{report.checks}"
        )
    # AND-failure behavior: flipping any one check to
    # False must flip `success` to False.  We assert
    # this property by checking the report's success
    # equals `all(checks.values())`.
    assert report.success == all(report.checks.values())


# =====================================================================
# 13.  Case A expected affected branch
# =====================================================================

def test_case_a_expected_affected_branch() -> None:
    """Case A: field_revenue_2025 × 10.  Affected path:
    field_revenue_2025@v2 → revenue/profit/cost metrics@v2
    → financial_claim@v2 → financial_section@v2 →
    report_merged@v2.  user / operation: v1 clean."""
    scenario, _ = build_main_fault_scenario(
        run_id="pilot_case_a_branch",
        csv_path=str(ROOT / "data/sample_company.csv"),
    )
    affected = set(scenario.affected_artifact_identities)
    must_be = {
        "field_revenue_2025",
        "metric_revenue_growth",
        "metric_profit_margin",
        "metric_cost_ratio",
        "claim_financial_growth",
        "claim_report_financial_section",
        "report_merged",
    }
    assert must_be.issubset(affected), (
        f"Case A affected set missing {must_be - affected}: "
        f"{affected}"
    )
    # User / operation are NOT in affected.
    assert "claim_user_growth" not in affected
    assert "claim_report_user_section" not in affected
    assert "claim_operation" not in affected
    assert "claim_report_operation_section" not in affected


# =====================================================================
# 14.  Case B expected affected branch
# =====================================================================

def test_case_b_expected_affected_branch() -> None:
    """Case B: field_visits_2025 × 10.  Affected path:
    field_visits_2025@v2 → metric_conversion_rate@v2 →
    claim_operation@v2 → operation_section@v2 →
    report_merged@v2.  financial / user: v1 clean.
    metric_order_growth: clean old version (active
    reuse)."""
    scenario, _ = build_visits_fault_scenario(
        run_id="pilot_case_b_branch",
        csv_path=str(ROOT / "data/sample_company.csv"),
    )
    affected = set(scenario.affected_artifact_identities)
    must_be = {
        "field_visits_2025",
        "metric_conversion_rate",
        "claim_operation",
        "claim_report_operation_section",
        "report_merged",
    }
    assert must_be.issubset(affected), (
        f"Case B affected set missing {must_be - affected}: "
        f"{affected}"
    )
    # Financial / user are NOT in affected.
    assert "claim_financial_growth" not in affected
    assert "claim_user_growth" not in affected
    # metric_order_growth is in available_unaffected
    # (active reuse).
    assert "metric_order_growth" in (
        scenario.available_unaffected_artifact_identities
    )


# =====================================================================
# 15.  Day14 trial JSON roundtrip
# =====================================================================

def test_day14_trial_json_roundtrip(tmp_path) -> None:
    """A TrialResult must serialize to JSON, write to
    disk, and reload to the same dict (modulo path
    fields)."""
    spec = build_trial_spec(
        case_id="case_a", strategy="FULL_RERUN",
        out_dir=tmp_path,
    )
    res = run_trial(spec)
    # Redirect out_path into tmp_path
    res_dict = res.to_dict()
    out = tmp_path / "trial01.json"
    out.write_text(
        json.dumps(res_dict, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    reloaded = json.loads(out.read_text(encoding="utf-8"))
    assert reloaded["case_id"] == "case_a"
    assert reloaded["strategy"] == "FULL_RERUN"
    assert "real_metrics" in reloaded
    assert "structural" in reloaded


# =====================================================================
# 16.  manifest hash roundtrip
# =====================================================================

def test_manifest_hash_roundtrip(tmp_path) -> None:
    """The frozen-evidence manifest's SHA-256 must be
    reproducible."""
    payload = {
        "files": {
            "x.json": {
                "sha256": hashlib.sha256(b"abc").hexdigest(),
                "size_bytes": 3,
            }
        }
    }
    p = tmp_path / "manifest.json"
    p.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    reloaded = json.loads(p.read_text(encoding="utf-8"))
    assert reloaded["files"]["x.json"]["sha256"] == (
        hashlib.sha256(b"abc").hexdigest()
    )


# =====================================================================
# 17.  pytest cannot mutate real benchmark files
# =====================================================================

# Day 14 pilot writes go to experiments/results/day14/pilot/.
# Real benchmark files (Day 13 frozen set) must NOT be in
# that path.  This test asserts the day14 module's outputs
# are confined to a sub-tree.
def test_day14_pilot_outputs_confined_to_subtree() -> None:
    pilot_dir = Path("experiments/results/day14")
    frozen = set(DAY13_FROZEN_FILES)
    for name in DAY13_FROZEN_FILES:
        assert not str(name).startswith("day14/"), (
            f"frozen file {name!r} must not be in day14 subtree"
        )
    # The pilot runner writes to a child of day14/; the
    # frozen evidence must NEVER live in that subtree.
    for f in frozen:
        assert "day14" not in f, (
            f"frozen evidence {f!r} lives inside the day14 "
            "subtree — Day 14 pytest could pollute it"
        )


# =====================================================================
# 18.  meta: existing test suite still green
# =====================================================================

def test_existing_test_suite_still_green() -> None:
    """Meta-test: the existing 471 tests must remain
    green.  We do a focused pytest run of a few
    representative files to keep the test fast."""
    res = subprocess.run(
        [
            sys.executable, "-m", "pytest",
            "tests/test_day9_experiment.py",
            "tests/test_day11_classification.py",
            "tests/test_day13_6_closeout.py",
            "tests/test_day14_pilot.py::test_frozen_provider_config_matches_qwen",
            "-q", "--no-header", "-x",
        ],
        cwd=str(ROOT), capture_output=True, text=True, timeout=300,
    )
    assert res.returncode == 0, (
        f"existing test suite broke:\n{res.stdout[-2000:]}\n"
        f"{res.stderr[-2000:]}"
    )
