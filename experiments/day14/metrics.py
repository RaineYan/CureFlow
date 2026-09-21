"""Day 14 — real-LLM comparison metrics.

Per user spec section 16:
  RealLLMCallReductionVsFull =
    (Full real LLM calls - Strategy real LLM calls)
    / Full real LLM calls

  ProviderTokenReductionVsFull =
    (Full provider-reported total tokens
     - Strategy provider-reported total tokens)
    / Full provider-reported total tokens

  RecoveryWallClockReductionVsFull =
    (Full recovery wall-clock
     - Strategy recovery wall-clock)
    / Full recovery wall-clock

NO result may be pre-computed.  Every numerator /
denominator comes from the trial's own ledger and
wall-clock.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from experiments.live_ledger import LiveCallLedger


@dataclass
class RealMetricsReport:
    real_llm_call_count: int
    provider_reported_token_total: int
    recovery_wall_clock_ms: float
    llm_request_latency_sum_ms: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "real_llm_call_count": self.real_llm_call_count,
            "provider_reported_token_total":
                self.provider_reported_token_total,
            "recovery_wall_clock_ms":
                round(self.recovery_wall_clock_ms, 4),
            "llm_request_latency_sum_ms":
                round(self.llm_request_latency_sum_ms, 4),
        }


def collect_real_metrics(
    ledger: LiveCallLedger,
    recovery_wall_clock_ms: float,
) -> RealMetricsReport:
    """Aggregate from the ledger.  Only provider_reported
    tokens count toward `provider_reported_token_total`,
    and only NON-replayed records count toward
    `real_llm_call_count` (this is the canonical
    `current_invocation_real_request_count` for the
    pilot)."""
    real_calls = ledger.current_invocation_real_request_count
    provider_tokens = (
        ledger.current_invocation_provider_reported_tokens
    )
    llm_latency_sum = sum(
        float(r.client_wall_clock_latency_ms or 0.0)
        for r in ledger.records
        if not r.is_replayed
    )
    return RealMetricsReport(
        real_llm_call_count=real_calls,
        provider_reported_token_total=provider_tokens,
        recovery_wall_clock_ms=float(recovery_wall_clock_ms),
        llm_request_latency_sum_ms=float(llm_latency_sum),
    )


def _safe_div(num: float, den: float) -> float:
    if den == 0:
        return 0.0
    return float(num) / float(den)


def compute_real_llm_call_reduction(
    full: RealMetricsReport,
    strategy: RealMetricsReport,
) -> Dict[str, Any]:
    num = full.real_llm_call_count - strategy.real_llm_call_count
    den = full.real_llm_call_count
    return {
        "numerator": num,
        "denominator": den,
        "reduction": _safe_div(num, den),
    }


def compute_provider_token_reduction(
    full: RealMetricsReport,
    strategy: RealMetricsReport,
) -> Dict[str, Any]:
    num = (
        full.provider_reported_token_total
        - strategy.provider_reported_token_total
    )
    den = full.provider_reported_token_total
    return {
        "numerator": num,
        "denominator": den,
        "reduction": _safe_div(num, den),
    }


def compute_recovery_wallclock_reduction(
    full: RealMetricsReport,
    strategy: RealMetricsReport,
) -> Dict[str, Any]:
    num = (
        full.recovery_wall_clock_ms
        - strategy.recovery_wall_clock_ms
    )
    den = full.recovery_wall_clock_ms
    return {
        "numerator_ms": num,
        "denominator_ms": den,
        "reduction": _safe_div(num, den),
    }
