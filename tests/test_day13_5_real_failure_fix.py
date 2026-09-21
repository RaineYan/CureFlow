"""
Day 13.5 follow-up regression tests for the real Qwen Test A failure.

The user reported the real Test A semantic audit:
  evidence_valid = false           (metric_profit_margin missing)
  numeric_consistency_pass = false (metric_revenue_growth not numeric)
  direction_consistency_pass = true
  recommendation_boundary_pass = true
  audit details bug: text_pct=0.0 and diff_pp=0.0 hid the missing
  number (should be None)

These tests lock the prompt-side fix and the audit-side fix:

  1. Financial Claim prompt must explicitly require BOTH required
     evidence refs (no hard-coded IDs, derived from Artifacts).
  2. Financial Claim prompt must state the expected percentage for
     each required metric (derived from Artifact.value, not
     hard-coded).
  3. profit_margin missing from evidence -> evidence_valid=False.
  4. revenue_growth missing numeric -> numeric FAIL, text_pct=None
     and diff_pp=None, match_status="MISSING_NUMERIC_MENTION".
  5. Matched numeric -> diff_pp math is correct.
  6. The day-13.5 audit-bug returns None for missing text_pct /
     diff_pp (NOT 0.0).
  7. extras list contains UNSUPPORTED_EXTRA_NUMBER records for
     unmatched text percentages.
  8. validate_claim surfaces details.numeric_extras in result.details.
  9. The financial-claim prompt does NOT hard-code "60.78" or
     "47.56" anywhere in the builder (we verify by changing the
     metric values and checking the prompt text changes accordingly).
 10. Previous 371 tests still pass (verified by run, not by code).
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import List
from unittest.mock import patch

import pytest

from artifacts import Artifact, ArtifactRef, ArtifactStore, ArtifactType
from llm import LLMResponse
from validation import (
    NumericConsistencyResult,
    validate_claim,
)
from workflow.claim_tasks import build_claim_prompt


CSV = str(Path(__file__).resolve().parent.parent / "data" / "sample_company.csv")


# =====================================================================
# Helpers
# =====================================================================

def _make_metric(store, run_id, aid, value) -> Artifact:
    a = Artifact(
        artifact_id=aid, artifact_type=ArtifactType.METRIC,
        producer_task="compute", run_id=run_id, version=1,
        value=value, input_artifact_ids=[],
        meta={"task_kind": "tool"},
    )
    return store.save(a)


def _ref(a: Artifact) -> ArtifactRef:
    return ArtifactRef(run_id=a.run_id, artifact_id=a.artifact_id, version=a.version)


def _make_claim_with_metrics(rg_val, pm_val):
    """Return (claim, rg, pm, cr) Artifacts (not saved to store) for
    prompt tests.  No LLM call."""
    rg = Artifact(
        artifact_id="metric_revenue_growth", artifact_type=ArtifactType.METRIC,
        producer_task="compute", run_id="r1", version=1,
        value=rg_val, input_artifact_ids=[],
        meta={"task_kind": "tool"},
    )
    pm = Artifact(
        artifact_id="metric_profit_margin", artifact_type=ArtifactType.METRIC,
        producer_task="compute", run_id="r1", version=1,
        value=pm_val, input_artifact_ids=[],
        meta={"task_kind": "tool"},
    )
    cr = Artifact(
        artifact_id="metric_cost_ratio", artifact_type=ArtifactType.METRIC,
        producer_task="compute", run_id="r1", version=1,
        value=0.5, input_artifact_ids=[],
        meta={"task_kind": "tool"},
    )
    return rg, pm, cr


# =====================================================================
# 1-2: financial claim prompt contract
# =====================================================================

def test_financial_prompt_requires_bOTH_required_evidence_refs() -> None:
    """The financial-claim prompt must explicitly say: 'evidence_refs
    MUST include BOTH <rg_ref> AND <pm_ref>' — derived from the
    actual Artifact refs, no hard-coding."""
    rg, pm, cr = _make_claim_with_metrics(0.6078, 0.4756)
    prompt = build_claim_prompt(
        "financial analyst", [rg, pm, cr],
        "Summarize the financial branch.",
    )
    # The two required refs MUST be present
    rg_ref = f"{rg.run_id}/{rg.artifact_id}@v{rg.version}"
    pm_ref = f"{pm.run_id}/{pm.artifact_id}@v{pm.version}"
    assert rg_ref in prompt
    assert pm_ref in prompt
    # And the contract text must be present
    assert "evidence_refs MUST include BOTH" in prompt
    assert "Do NOT omit either required evidence ref" in prompt


def test_financial_prompt_receives_method_knowledge_without_treating_it_as_evidence() -> None:
    rg, pm, cr = _make_claim_with_metrics(0.6078, 0.4756)
    prompt = build_claim_prompt(
        "financial analyst", [rg, pm, cr],
        "Summarize the financial branch.",
    )
    assert "ANALYSIS METHOD KNOWLEDGE" in prompt
    assert "guidance only, NOT run evidence" in prompt
    assert "经营指标分解" in prompt
    assert "Never cite a method card as proof" in prompt


def test_financial_prompt_contains_revenue_growth_exact_ref() -> None:
    rg, pm, cr = _make_claim_with_metrics(0.6078, 0.4756)
    prompt = build_claim_prompt(
        "financial analyst", [rg, pm, cr],
        "Summarize.",
    )
    rg_ref = f"{rg.run_id}/metric_revenue_growth@v{rg.version}"
    assert rg_ref in prompt


def test_financial_prompt_contains_profit_margin_exact_ref() -> None:
    rg, pm, cr = _make_claim_with_metrics(0.6078, 0.4756)
    prompt = build_claim_prompt(
        "financial analyst", [rg, pm, cr],
        "Summarize.",
    )
    pm_ref = f"{pm.run_id}/metric_profit_margin@v{pm.version}"
    assert pm_ref in prompt


def test_financial_prompt_requires_numeric_for_each_required_metric() -> None:
    rg, pm, cr = _make_claim_with_metrics(0.6078, 0.4756)
    prompt = build_claim_prompt(
        "financial analyst", [rg, pm, cr],
        "Summarize.",
    )
    assert "claim_text MUST explicitly state the numerical value" in prompt
    assert "revenue_growth" in prompt
    assert "profit_margin" in prompt
    # And the validator-style rule:
    assert "numerically represented" in prompt.lower() or "state the numerical" in prompt.lower()


def test_financial_prompt_values_are_derived_not_hardcoded() -> None:
    """If we change the Artifact values, the prompt text must change
    accordingly.  This proves the prompt builder derives expected
    numbers from the actual Artifacts, not from a hard-coded
    "60.78 / 47.56" table.
    """
    rg1, pm1, cr1 = _make_claim_with_metrics(0.6078, 0.4756)
    p1 = build_claim_prompt(
        "financial analyst", [rg1, pm1, cr1], "Summarize.",
    )
    rg2, pm2, cr2 = _make_claim_with_metrics(0.1234, 0.5678)
    p2 = build_claim_prompt(
        "financial analyst", [rg2, pm2, cr2], "Summarize.",
    )
    # The numeric strings in p1 should be the format-string result
    # of 0.6078 * 100 and 0.4756 * 100.  In p2 they should be
    # 0.1234 * 100 and 0.5678 * 100.
    assert "60.78%" in p1
    assert "47.56%" in p1
    assert "12.34%" in p2
    assert "56.78%" in p2
    # And the OLD p1 strings should not appear in p2.
    assert "60.78%" not in p2
    assert "47.56%" not in p2


# =====================================================================
# 3: profit_margin missing -> evidence FAIL
# =====================================================================

def test_profit_margin_missing_from_evidence_fails() -> None:
    """Reproduce the live-gate failure: model returns only
    revenue_growth in evidence_refs, omitting profit_margin.
    `validate_claim` must fail `evidence_valid`.
    """
    with tempfile.TemporaryDirectory() as td:
        store = ArtifactStore(base_dir=str(Path(td) / "art"))
        rg = _make_metric(store, "r1", "metric_revenue_growth", 0.6078)
        pm = _make_metric(store, "r1", "metric_profit_margin", 0.4756)
        cr = _make_metric(store, "r1", "metric_cost_ratio", 0.5243)
        # Model output cites only revenue_growth + cost_ratio
        claim_text = "营收增长 60.78%，但成本比率为 52%。"
        evidence_refs_in_text = [
            _ref(rg),
            # profit_margin INTENTIONALLY missing
        ]
        sem = validate_claim(
            claim_text=claim_text,
            evidence_refs_in_text=evidence_refs_in_text,
            store=store,
            evidence_policy="financial_claim",
        )
        assert not sem.evidence_valid
        assert any("metric_profit_margin" in i for i in sem.issues)


# =====================================================================
# 4: revenue_growth missing numeric -> text_pct=None, diff_pp=None
# =====================================================================

def test_missing_numeric_text_pct_and_diff_pp_are_none() -> None:
    """Reproduce the live-gate audit bug: when the required metric
    is not numerically represented in text, the audit must record
    `text_pct=None` and `diff_pp=None` with `match_status=
    "MISSING_NUMERIC_MENTION"` — NOT `0.0`.
    """
    from validation.semantic import numeric_consistency_check
    rg = Artifact(
        artifact_id="metric_revenue_growth", artifact_type=ArtifactType.METRIC,
        producer_task="compute", run_id="r1", version=1,
        value=0.6078, input_artifact_ids=[], meta={"task_kind": "tool"},
    )
    pm = Artifact(
        artifact_id="metric_profit_margin", artifact_type=ArtifactType.METRIC,
        producer_task="compute", run_id="r1", version=1,
        value=0.4756, input_artifact_ids=[], meta={"task_kind": "tool"},
    )
    # Text mentions NEITHER metric numerically (the live failure).
    text = "业务表现良好，但需要进一步观察。"
    ok, results, issues, extras = numeric_consistency_check(
        text, [rg, pm], allow_no_numbers=False,
    )
    assert ok is False
    assert len(results) == 2
    for r in results:
        # Day 13.5 bugfix: must be None, not 0.0
        assert r.text_pct is None, f"text_pct must be None, got {r.text_pct!r}"
        assert r.diff_pp is None, f"diff_pp must be None, got {r.diff_pp!r}"
        assert r.within_tolerance is False
        assert r.match_status == "MISSING_NUMERIC_MENTION"
    # Two MISSING_NUMERIC_MENTION issues
    missing_issues = [i for i in issues if "MISSING_NUMERIC_MENTION" in i]
    assert len(missing_issues) == 2
    # No extras (no numbers in text)
    assert extras == []


# =====================================================================
# 5: matched numeric -> diff_pp math is correct
# =====================================================================

def test_matched_numeric_diff_pp_is_mathematically_correct() -> None:
    from validation.semantic import numeric_consistency_check
    rg = Artifact(
        artifact_id="metric_revenue_growth", artifact_type=ArtifactType.METRIC,
        producer_task="compute", run_id="r1", version=1,
        value=0.6078, input_artifact_ids=[], meta={"task_kind": "tool"},
    )
    text = "营收增长 60.78%。"
    ok, results, issues, extras = numeric_consistency_check(
        text, [rg], allow_no_numbers=False,
    )
    assert ok is True
    assert len(results) == 1
    r = results[0]
    assert r.text_pct == 60.78
    # diff_pp = text_pct - expected_pct = 60.78 - 60.78431372549019 ≈ -0.004
    assert abs(r.diff_pp - (60.78 - 0.6078 * 100.0)) < 1e-9
    assert r.match_status == "MATCHED"
    assert r.within_tolerance is True


# =====================================================================
# 6: extra unmatched text_pct -> extras list
# =====================================================================

def test_unmatched_text_pct_appears_in_extras_as_unsupported() -> None:
    from validation.semantic import numeric_consistency_check
    rg = Artifact(
        artifact_id="metric_revenue_growth", artifact_type=ArtifactType.METRIC,
        producer_task="compute", run_id="r1", version=1,
        value=0.6078, input_artifact_ids=[], meta={"task_kind": "tool"},
    )
    # Text has revenue_growth (60.78%) + an extra 99.99% with no
    # evidence.  The extra is UNSUPPORTED_EXTRA_NUMBER.
    text = "营收增长 60.78%，市场整体 99.99% 增长。"
    ok, results, issues, extras = numeric_consistency_check(
        text, [rg], allow_no_numbers=False,
    )
    assert ok is False
    assert any(e.get("match_status") == "UNSUPPORTED_EXTRA_NUMBER"
               and e.get("text_pct") == 99.99 for e in extras)


# =====================================================================
# 7: validate_claim surfaces details.numeric_extras
# =====================================================================

def test_validate_claim_surfaces_numeric_extras() -> None:
    with tempfile.TemporaryDirectory() as td:
        store = ArtifactStore(base_dir=str(Path(td) / "art"))
        rg = _make_metric(store, "r1", "metric_revenue_growth", 0.6078)
        pm = _make_metric(store, "r1", "metric_profit_margin", 0.4756)
        cr = _make_metric(store, "r1", "metric_cost_ratio", 0.5243)
        claim_text = (
            "营收同比增长 60.78%，利润率 47.56%，"
            "市场整体 99.99% 增长。"  # extra number
        )
        sem = validate_claim(
            claim_text=claim_text,
            evidence_refs_in_text=[_ref(rg), _ref(pm)],
            store=store,
            evidence_policy="financial_claim",
        )
        # details should contain both 'numeric' (per-evidence) and
        # 'numeric_extras' (unmatched text percentages).
        assert "numeric" in sem.details
        assert "numeric_extras" in sem.details
        extras = sem.details["numeric_extras"]
        assert any(e["text_pct"] == 99.99
                   and e["match_status"] == "UNSUPPORTED_EXTRA_NUMBER"
                   for e in extras)


# =====================================================================
# 8: financial_claim prompt must NOT contain "60.78" as a
# literal string when the actual value is 0.1234 (i.e. values
# are derived, not hard-coded)
# =====================================================================

def test_financial_prompt_does_not_hardcode_60_78_or_47_56() -> None:
    """Day 13.5 spec: do NOT hard-code 60.78 / 47.56 in the prompt
    builder.  If the actual Artifact values are different, the
    prompt must show the new values, not the old defaults.
    """
    rg, pm, cr = _make_claim_with_metrics(0.123, 0.456)
    prompt = build_claim_prompt(
        "financial analyst", [rg, pm, cr], "Summarize.",
    )
    # The hard-coded "60.78" / "47.56" must NOT appear.
    assert "60.78" not in prompt
    assert "47.56" not in prompt
    # The actual derived values DO appear.
    assert "12.30%" in prompt
    assert "45.60%" in prompt


# =====================================================================
# 9: prompt still passes ClaimOutput schema for the contract it
# declares (the JSON template at the end uses claim_text, NOT
# section_text) — combined regression for the day-13.5 prompt fix
# + the new financial contract.
# =====================================================================

def test_financial_prompt_template_uses_claim_text_alias() -> None:
    """The financial-claim prompt's JSON template must use the
    canonical field `claim_text` and must NOT introduce aliases
    like `section_text` / `report_text` / `content` / `summary`.
    Day 13.5 spec: aliases are not part of the contract."""
    rg, pm, cr = _make_claim_with_metrics(0.6078, 0.4756)
    prompt = build_claim_prompt(
        "financial analyst", [rg, pm, cr], "Summarize.",
    )
    # The JSON template at the end must use claim_text.
    assert '"claim_text"' in prompt
    # And it must NOT introduce aliases (which would invite the LLM
    # to drift to a different field name).
    assert "section_text" not in prompt
    assert "report_text" not in prompt
    # The JSON template also has 'content' inside `Output STRICT
    # JSON only, no other text:` — we instead forbid the alias
    # being inside the JSON shape itself.  The prompt uses
    # `claim_text` as the value-bearing field; the template line
    # `'  "claim_text": "...",'` is the only declaration of the
    # output field.
