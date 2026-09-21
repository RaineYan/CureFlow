"""
Regression tests for the Day 13.5 Test B failure: Section prompt
schema mismatch.

Live gate evidence (paste from user): Qwen 3.7-plus returned an
object missing `claim_text`; the section writer role had drifted
to `section_text` / `content` / `report_text` field names.  Parser
remained strict; the fix lives in the prompt.

These tests lock the fix end-to-end:
  1. Financial Section prompt contains the literal string
     "claim_text" in the rules block.
  2. User Section prompt (via _generate_section) also contains
     "claim_text" in the rules block.
  3. Operation Section prompt (via _generate_section) also
     contains "claim_text" in the rules block.
  4. The prompt explicitly forbids "section_text", "content",
     "report_text" as field-name aliases.
  5. A "real-style" valid section JSON containing claim_text,
     evidence_refs, confidence passes ClaimOutput.
  6. A JSON with `section_text` instead of `claim_text` is
     REJECTED by ClaimOutput (no silent repair).
  7. A Financial Section call cites the upstream Financial Claim
     ref (not a metric ref) as the only evidence.
  8. Test B mock: a real-provider-shaped response that respects
     the section prompt contract passes end-to-end.
  9. The full Day 1-13.5 + previous pytest still passes (verified
     by run, not by code).
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import List
from unittest.mock import patch

import pytest

from artifacts import Artifact, ArtifactRef, ArtifactStore, ArtifactType
from llm import LLMResponse, UsageType
from validation import validate_section
from workflow.claim_tasks import (
    ClaimOutput,
    _generate_section,
    build_claim_prompt,
)


CSV = str(Path(__file__).resolve().parent.parent / "data" / "sample_company.csv")


# =====================================================================
# 1-4: Section prompt schema enforcement
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


def _make_claim(store, run_id, aid, content, evidence_artifacts) -> Artifact:
    refs = [_ref(a) for a in evidence_artifacts]
    a = Artifact(
        artifact_id=aid, artifact_type=ArtifactType.CLAIM,
        producer_task="generate_financial_claim",
        run_id=run_id, version=1, value=None,
        content=content, input_artifact_ids=refs,
        evidence_ids=refs,
    )
    return store.save(a)


def test_financial_section_prompt_contains_claim_text() -> None:
    """build_claim_prompt with output_role="section" must include
    the literal string `claim_text` AND explicitly forbid
    section_text / content / report_text aliases."""
    with tempfile.TemporaryDirectory() as td:
        store = ArtifactStore(base_dir=str(Path(td) / "art"))
        rg = _make_metric(store, "r1", "metric_revenue_growth", 0.6)
        claim = _make_claim(store, "r1", "claim_financial_growth",
                            "营收增长 60%。", [rg])
        prompt = build_claim_prompt(
            "financial section writer", [claim],
            "Write the financial section of the report.",
            output_role="section",
        )
        assert "claim_text" in prompt, "claim_text literal must appear in section prompt"
        assert "evidence_refs" in prompt
        assert "confidence" in prompt
        # Day 13.5 fix: forbid aliases
        for forbidden in ("section_text", "report_text", "content"):
            assert forbidden in prompt, (
                f"section prompt must explicitly forbid alias {forbidden!r}"
            )


def test_user_section_prompt_contains_claim_text() -> None:
    with tempfile.TemporaryDirectory() as td:
        store = ArtifactStore(base_dir=str(Path(td) / "art"))
        ug = _make_metric(store, "r1", "metric_user_growth", 0.5)
        claim = _make_claim(store, "r1", "claim_user_growth",
                            "用户增长 50%。", [ug])
        prompt = build_claim_prompt(
            "user section writer", [claim],
            "Write the user section.",
            output_role="section",
        )
        assert "claim_text" in prompt


def test_operation_section_prompt_contains_claim_text() -> None:
    with tempfile.TemporaryDirectory() as td:
        store = ArtifactStore(base_dir=str(Path(td) / "art"))
        cr = _make_metric(store, "r1", "metric_conversion_rate", 0.09)
        og = _make_metric(store, "r1", "metric_order_growth", 0.77)
        claim = _make_claim(store, "r1", "claim_operation",
                            "订单增长 77%，转化率 9%。", [cr, og])
        prompt = build_claim_prompt(
            "operation section writer", [claim],
            "Write the operation section.",
            output_role="section",
        )
        assert "claim_text" in prompt


def test_section_prompt_explicitly_forbids_aliases() -> None:
    """Day 13.5: the section prompt must include explicit
    'do not use' rules so the model does not drift to
    section_text / content / report_text field names."""
    with tempfile.TemporaryDirectory() as td:
        store = ArtifactStore(base_dir=str(Path(td) / "art"))
        rg = _make_metric(store, "r1", "metric_revenue_growth", 0.6)
        claim = _make_claim(store, "r1", "claim_financial_growth",
                            "营收增长 60%。", [rg])
        prompt = build_claim_prompt(
            "financial section writer", [claim],
            "Write the section.",
            output_role="section",
        )
        # Each forbidden alias must be named in the prompt
        for alias in ("section_text", "content", "report_text", "summary"):
            assert alias in prompt, (
                f"forbidden alias {alias!r} must be named in the prompt"
            )
        # And a positive affirmation that claim_text is the right name
        assert "claim_text" in prompt


# =====================================================================
# 5-6: ClaimOutput validation behavior
# =====================================================================

def test_real_style_valid_section_json_passes_claimoutput() -> None:
    """A real-provider-shaped section JSON that uses
    claim_text/evidence_refs/confidence passes ClaimOutput.
    """
    payload = {
        "claim_text": "**财务摘要**：2025 年营收同比增长 60.78%，利润率为 47.56%。",
        "evidence_refs": ["r1/claim_financial_growth@v1"],
        "confidence": 0.9,
    }
    parsed = ClaimOutput.model_validate(payload)
    assert parsed.claim_text == payload["claim_text"]
    assert parsed.evidence_refs == payload["evidence_refs"]
    assert parsed.confidence == 0.9


def test_section_text_alias_rejected_no_silent_repair() -> None:
    """If the model returns `section_text` instead of `claim_text`,
    the parser MUST reject it.  Day 13.5 spec: no silent repair."""
    payload = {
        "section_text": "财务摘要：增长 60%。",   # wrong field name
        "evidence_refs": ["r1/claim_financial_growth@v1"],
        "confidence": 0.9,
    }
    with pytest.raises(Exception) as exc_info:
        ClaimOutput.model_validate(payload)
    # The error must mention the missing required key
    msg = str(exc_info.value).lower()
    assert "claim_text" in msg


def test_content_alias_rejected_no_silent_repair() -> None:
    payload = {
        "content": "财务摘要：增长 60%。",       # wrong field name
        "evidence_refs": ["r1/claim_financial_growth@v1"],
        "confidence": 0.9,
    }
    with pytest.raises(Exception) as exc_info:
        ClaimOutput.model_validate(payload)
    assert "claim_text" in str(exc_info.value).lower()


# =====================================================================
# 7: Financial Section evidence contract
# =====================================================================

def test_financial_section_evidence_is_exact_financial_claim_ref() -> None:
    """The Financial Section's evidence_refs must contain the
    upstream Financial Claim ref, NOT a Metric ref.  This is
    enforced by the input set passed to _generate_section and the
    parse-time `evidence must be subset of inputs` check.
    """
    with tempfile.TemporaryDirectory() as td:
        store = ArtifactStore(base_dir=str(Path(td) / "art"))
        rg = _make_metric(store, "r1", "metric_revenue_growth", 0.6078)
        pm = _make_metric(store, "r1", "metric_profit_margin", 0.4756)
        cr = _make_metric(store, "r1", "metric_cost_ratio", 0.5243)
        # Financial Claim cites revenue_growth + profit_margin
        fc = _make_claim(
            store, "r1", "claim_financial_growth",
            "营收增长 60.78%，利润率 47.56%。",
            [rg, pm],
        )

        # Valid section evidence = the financial claim
        valid_payload = {
            "claim_text": "**财务**：2025 营收 60.78%，利润 47.56%。",
            "evidence_refs": [f"{fc.run_id}/{fc.artifact_id}@v{fc.version}"],
            "confidence": 0.9,
        }
        parsed = ClaimOutput.model_validate(valid_payload)
        assert parsed.evidence_refs == [
            f"{fc.run_id}/{fc.artifact_id}@v{fc.version}"
        ]

        # Invalid: section citing a metric directly (not the claim)
        invalid_payload = {
            "claim_text": "**财务**：增长 60%。",
            "evidence_refs": [f"{rg.run_id}/{rg.artifact_id}@v{rg.version}"],
            "confidence": 0.9,
        }
        # Schema-level it parses; the policy check (semantic) is what
        # would reject metric-as-evidence for a SECTION.  We verify
        # that semantic validator does reject this.
        semantic = validate_section(
            section_text="**财务**：增长 60%。",
            section_evidence_refs=[ArtifactRef(
                run_id=rg.run_id, artifact_id=rg.artifact_id, version=rg.version,
            )],
            claim_evidence_refs=[rg, pm, cr],
            store=store,
            evidence_policy="financial_section",
        )
        # The expected claim is claim_financial_growth, not metric_revenue_growth
        assert not semantic.evidence_valid, (
            "section citing a metric as evidence must fail evidence_valid"
        )


# =====================================================================
# 8: Test B mock real-provider end-to-end
# =====================================================================

def test_financial_section_with_mock_real_provider_passes() -> None:
    """End-to-end: a FakeLLMClient returning a section JSON that
    follows the contract must produce a passable section artifact
    AND pass semantic validation.
    """
    with tempfile.TemporaryDirectory() as td:
        store = ArtifactStore(base_dir=str(Path(td) / "art"))
        rg = _make_metric(store, "r1", "metric_revenue_growth", 0.6078)
        pm = _make_metric(store, "r1", "metric_profit_margin", 0.4756)
        cr = _make_metric(store, "r1", "metric_cost_ratio", 0.5243)
        fc = _make_claim(
            store, "r1", "claim_financial_growth",
            "营收同比增长 60.78%，利润率 47.56%。",
            [rg, pm],
        )

        from llm.fake import FakeLLMClient
        valid_section_text = (
            "**财务摘要**：2025 年营收同比增长 60.78%，"
            "利润率为 47.56%，整体财务表现良好。"
        )
        body = json.dumps({
            "claim_text": valid_section_text,
            "evidence_refs": [f"{fc.run_id}/{fc.artifact_id}@v{fc.version}"],
            "confidence": 0.88,
        }, ensure_ascii=False)
        fake = FakeLLMClient(task_responders={
            "generate_financial_section": lambda prompt: LLMResponse(
                text=body, input_tokens=50, output_tokens=30,
                monetary_usd=0.0, latency_ms=100.0,
                provider="qwen", model="qwen3.7-plus-2026-05-26",
                usage_type=UsageType.PROVIDER_REPORTED,
            ),
        })

        from provenance.decorator import RunContext
        ctx = RunContext(run_id="r1", store=store)
        section = _generate_section(
            "financial", fc,
            ctx=ctx, llm_client=fake,
            artifact_id="claim_report_financial_section",
            task_name="generate_financial_section",
        )
        assert section.artifact_id == "claim_report_financial_section"
        assert section.content == valid_section_text
        assert section.meta["llm_provider"] == "qwen"
        assert section.meta["llm_usage_type"] == "provider_reported"

        # And the section must pass semantic validation
        semantic = validate_section(
            section_text=section.content,
            section_evidence_refs=list(section.evidence_ids),
            claim_evidence_refs=list(fc.evidence_ids),
            store=store,
            evidence_policy="financial_section",
        )
        assert semantic.passed, (
            f"section should pass semantic validation: {semantic.issues}"
        )


# =====================================================================
# 9: regression for the OLD failure mode (Qwen drift to section_text)
# =====================================================================

def test_financial_section_with_section_text_alias_still_fails() -> None:
    """Reproduce the live-gate failure: Qwen returns section_text
    instead of claim_text.  This MUST still fail (no silent repair).
    """
    with tempfile.TemporaryDirectory() as td:
        store = ArtifactStore(base_dir=str(Path(td) / "art"))
        rg = _make_metric(store, "r1", "metric_revenue_growth", 0.6078)
        pm = _make_metric(store, "r1", "metric_profit_margin", 0.4756)
        fc = _make_claim(
            store, "r1", "claim_financial_growth",
            "营收增长 60.78%，利润率 47.56%。", [rg, pm],
        )

        # The exact shape that the live Qwen returned:
        # { "section_text": "...", "evidence_refs": [...], "confidence": ... }
        body = json.dumps({
            "section_text": "**财务**：增长 60%。",
            "evidence_refs": [f"{fc.run_id}/{fc.artifact_id}@v{fc.version}"],
            "confidence": 0.9,
        }, ensure_ascii=False)
        from llm.fake import FakeLLMClient
        from provenance.decorator import RunContext
        fake = FakeLLMClient(task_responders={
            "generate_financial_section": lambda prompt: LLMResponse(
                text=body, input_tokens=50, output_tokens=30,
                monetary_usd=0.0, latency_ms=100.0,
                provider="qwen", model="qwen3.7-plus-2026-05-26",
                usage_type=UsageType.PROVIDER_REPORTED,
            ),
        })
        ctx = RunContext(run_id="r1", store=store)
        with pytest.raises(ValueError) as exc_info:
            _generate_section(
                "financial", fc,
                ctx=ctx, llm_client=fake,
                artifact_id="claim_report_financial_section",
                task_name="generate_financial_section",
            )
        # The error MUST mention the missing claim_text key
        assert "claim_text" in str(exc_info.value).lower()
