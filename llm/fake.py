"""
FakeLLMClient — Day 5 + Day 6 + Day 13.5.

Day 5: deterministic, no-network LLM stand-in. Parses the prompt's
AVAILABLE_EVIDENCE block and echoes every ref into evidence_refs.

Day 6: per-task response routing. Tests can pass `task_responders={...}`
to control what each task's LLM call returns, while still being
deterministic. The legacy `responder=...` kwarg is preserved so Day 5
tests keep working.

Day 13.5: every FakeLLMClient response is tagged
    usage_type = UsageType.SYNTHETIC
so the downstream token aggregator can filter on
    usage_type == UsageType.PROVIDER_REPORTED
to count only real provider measurements.

Call routing priority (highest first):
    1. task_responders[task_name](prompt)  if task_name in task_responders
    2. responder(prompt)                   if responder is not None
    3. self._default(prompt)               always works
"""
from __future__ import annotations

import json
import re
from typing import Callable, Dict, Optional, Tuple

from .base import LLMClient, LLMResponse, UsageType


# A prompt line in the build_*_claim_prompt() / build_*_section_prompt()
# functions has the form:
#   "  <run_id>/<artifact_id>@v<version>  value=<number|content>"
# We use the ref-string as the citation key.
_EVIDENCE_LINE_RE = re.compile(r"^\s+(\S+/[^\s]+@v\d+)\s+value=", re.MULTILINE)


def _parse_evidence_refs(prompt: str) -> list[str]:
    return _EVIDENCE_LINE_RE.findall(prompt)


class FakeLLMClient:
    """Deterministic LLM stand-in. No network code paths."""

    def __init__(
        self,
        responder: Optional[Callable[[str], LLMResponse]] = None,
        task_responders: Optional[Dict[str, Callable[[str], LLMResponse]]] = None,
    ) -> None:
        self._responder = responder                          # legacy
        self._task_responders = task_responders or {}        # Day 6
        self.calls: list[Tuple[str, str]] = []              # (task_name, prompt)

    def generate(self, prompt: str, *, task_name: str = "") -> LLMResponse:  # type: ignore[override]
        self.calls.append((task_name, prompt))
        if task_name and task_name in self._task_responders:
            return self._task_responders[task_name](prompt)
        if self._responder is not None:
            return self._responder(prompt)
        return self._default(prompt)

    @staticmethod
    def _default(prompt: str) -> LLMResponse:
        """Echo every AVAILABLE_EVIDENCE ref; templated claim_text."""
        refs = _parse_evidence_refs(prompt)
        body = json.dumps(
            {
                "claim_text": (
                    f"分析涉及 {len(refs)} 个指标"
                    + "".join(f"; {r}" for r in refs)
                ),
                "evidence_refs": refs,
                "confidence": 0.95,
            },
            ensure_ascii=False,
        )
        return LLMResponse(
            text=body,
            input_tokens=120,
            output_tokens=40,
            monetary_usd=0.0,
            latency_ms=1.0,
            provider="fake",
            model="fake-deterministic",
            usage_type=UsageType.SYNTHETIC,
        )


# =====================================================================
# Day 6 canned responders (real-looking Chinese business text)
# =====================================================================
# These produce stable JSON the tests can assert on, and exercise the
# "input != evidence" path (Financial Claim cites 2 of its 3 inputs).
# DO NOT USE THESE NUMBERS AS REAL MODEL DATA IN COMPETITION MATERIAL.

def _fake_resp(
    text: str,
    in_tok: int,
    out_tok: int,
    latency: float,
) -> LLMResponse:
    """Shared FakeLLMResponse builder so all canned responders mark
    usage_type=SYNTHETIC in exactly one place."""
    return LLMResponse(
        text=text, input_tokens=in_tok, output_tokens=out_tok,
        monetary_usd=0.0, latency_ms=latency,
        provider="fake", model="fake-deterministic",
        usage_type=UsageType.SYNTHETIC,
    )


def make_financial_responder() -> Callable[[str], LLMResponse]:
    def f(prompt: str) -> LLMResponse:
        refs = _parse_evidence_refs(prompt)
        # The first two refs are revenue_growth + profit_margin.
        evidence_refs = refs[:2]   # excludes cost_ratio on purpose
        body = json.dumps({
            "claim_text": "2025 年营收同比增长 60.78%，利润率达到 47.56%，经营增长表现较强。",
            "evidence_refs": evidence_refs,
            "confidence": 0.92,
        }, ensure_ascii=False)
        return _fake_resp(body, 140, 48, 1.2)
    return f


def make_user_responder() -> Callable[[str], LLMResponse]:
    def f(prompt: str) -> LLMResponse:
        refs = _parse_evidence_refs(prompt)
        body = json.dumps({
            "claim_text": "2025 年平均月活用户同比增长 50%，用户规模明显扩大。",
            "evidence_refs": refs,           # user branch only has 1 input -> 1 evidence
            "confidence": 0.90,
        }, ensure_ascii=False)
        return _fake_resp(body, 110, 42, 1.0)
    return f


def make_operation_responder() -> Callable[[str], LLMResponse]:
    def f(prompt: str) -> LLMResponse:
        refs = _parse_evidence_refs(prompt)
        body = json.dumps({
            "claim_text": "2025 年订单同比增长 77.42%，整体转化率约为 9.32%。",
            "evidence_refs": refs,
            "confidence": 0.88,
        }, ensure_ascii=False)
        return _fake_resp(body, 125, 44, 1.1)
    return f


def make_section_responder(
    section_name: str,
    claim_text: str,
    confidence: float = 0.85,
) -> Callable[[str], LLMResponse]:
    """A section cites exactly the upstream Claim (not the underlying metrics)."""
    def f(prompt: str) -> LLMResponse:
        refs = _parse_evidence_refs(prompt)
        body = json.dumps({
            "claim_text": claim_text,
            "evidence_refs": refs,           # section's evidence == upstream claim
            "confidence": confidence,
        }, ensure_ascii=False)
        return _fake_resp(body, 100, 30, 0.9)
    return f


def default_workflow_llm() -> FakeLLMClient:
    """The one-stop responder for the full Day 6 baseline workflow.

    Day 7 rule: section text is FACTUAL ONLY. No recommendations
    (建议/应当/应该/推荐/需要采取). Recommendation text belongs to a
    future Decision Agent, not to a report Section.
    """
    return FakeLLMClient(task_responders={
        "generate_financial_claim":   make_financial_responder(),
        "generate_user_claim":        make_user_responder(),
        "generate_operation_claim":   make_operation_responder(),
        "generate_financial_section": make_section_responder(
            "financial",
            "**财务摘要**：2025 年营收同比增长 60.78%，利润率为 47.56%，整体财务指标显示业务规模明显增长。",
        ),
        "generate_user_section": make_section_responder(
            "user",
            "**用户摘要**：2025 年平均月活用户同比增长 50%，用户规模较上一年度明显扩大。",
        ),
        "generate_operation_section": make_section_responder(
            "operation",
            "**运营摘要**：2025 年订单量同比增长 77.42%，整体转化率约为 9.32%。",
        ),
    })
