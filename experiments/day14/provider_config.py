"""Day 14 — frozen provider config.

Per user spec section 2: every strategy / case / trial
MUST use the same provider config.  If a Provider call
omits usage, the request is preserved but marked
usage_type=UNAVAILABLE and excluded from the token
metric.

Forbidden:
  - strategy-specific model
  - strategy-specific prompt optimization
  - strategy-specific temperature
  - strategy-specific retry policy

Required:
  provider = qwen
  model    = qwen3.7-plus-2026-05-26
  enable_thinking = False
  response_format = {"type": "json_object"}
  temperature = 0
  seed = 1234
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass(frozen=True)
class ProviderConfig:
    provider: str
    model: str
    enable_thinking: bool
    response_format: Dict[str, Any]
    temperature: float
    seed: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "enable_thinking": self.enable_thinking,
            "response_format": self.response_format,
            "temperature": self.temperature,
            "seed": self.seed,
        }


FROZEN_PROVIDER_CONFIG = ProviderConfig(
    provider="qwen",
    model="qwen3.7-plus-2026-05-26",
    enable_thinking=False,
    response_format={"type": "json_object"},
    temperature=0,
    seed=1234,
)


def validate_provider_config_match(
    actual: Dict[str, Any],
) -> Dict[str, Any]:
    """Return a fairness report.  Every frozen key MUST
    match; strategy-specific overrides MUST be rejected."""
    expected = FROZEN_PROVIDER_CONFIG.to_dict()
    mismatches: Dict[str, Any] = {}
    for key, want in expected.items():
        if key not in actual:
            mismatches[key] = {"expected": want, "got": "<MISSING>"}
            continue
        got = actual[key]
        if got != want:
            mismatches[key] = {"expected": want, "got": got}
    return {
        "passed": not mismatches,
        "frozen_config": expected,
        "actual_config": actual,
        "mismatches": mismatches,
    }
