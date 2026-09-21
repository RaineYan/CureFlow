"""
LLM provider interface — Day 5 + Day 12 readiness extensions + Day 13.5
usage-type refinement.

Day 5: minimal contract: take a prompt, return an LLMResponse.  Claim
tasks in workflow/claim_tasks.py depend only on this Protocol so
swapping fake vs real providers is a one-line change at the call site.

Day 12: extended LLMResponse schema with `provider`, `model`, and
`usage_is_synthetic`.  Real providers set usage_is_synthetic=False;
FakeLLMClient sets usage_is_synthetic=True.  No more "did the harness
guess right" — the response itself carries the flag.

Day 13.5: tightened the usage flag into a 3-state enum `UsageType`:

    SYNTHETIC          -> tokens were fabricated by the harness
    PROVIDER_REPORTED  -> provider returned a usage block we trust
    UNAVAILABLE        -> provider did not return usage; tokens=0 is
                          not real data and must NOT enter token stats

`usage_is_synthetic` is preserved as a derived boolean for backwards
compatibility:
    SYNTHETIC         -> True
    PROVIDER_REPORTED -> False
    UNAVAILABLE       -> False

Downstream token aggregators MUST gate on `usage_type == PROVIDER_REPORTED`
before adding to totals; otherwise the numbers mix fake and real.

Also adds a `RealLLMProviderConfig` for the future real-provider
adapter.  The config is just a typed dataclass; the actual HTTP
adapter is implemented in `llm/providers/real.py`.

This module does NOT claim any provider is compatible with any other.
Each provider's API contract must be verified per-config in the
adapter code.  See llm/providers/real.py.
"""
from __future__ import annotations

import os
from enum import Enum
from typing import Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


# =====================================================================
# Day 13.5: UsageType enum
# =====================================================================

class UsageType(str, Enum):
    """Sentinel for where the LLMResponse.token numbers came from.

    SYNTHETIC         : the harness / FakeLLMClient made them up.
    PROVIDER_REPORTED : the real provider returned a usage block.
    UNAVAILABLE       : a real provider was called but did not return
                        a usage block.  Tokens are 0 and MUST NOT be
                        treated as real measurements.
    """
    SYNTHETIC = "synthetic"
    PROVIDER_REPORTED = "provider_reported"
    UNAVAILABLE = "unavailable"


class LLMResponse(BaseModel):
    """What an LLM call returns. The text is what the model produced;
    token / monetary / latency fields let downstream code record cost
    on the produced ClaimArtifact (or any other artifact).

    Day 12: `usage_is_synthetic` MUST be True for FakeLLMClient and
    False for any real provider response.

    Day 13.5: `usage_type` is the authoritative source.  It distinguishes
    "real provider without usage" (UNAVAILABLE) from "synthetic"
    (SYNTHETIC).  The two are NOT the same thing:

      - SYNTHETIC: tokens are fake; using them in benchmark totals is
        allowed for offline experiments but must be flagged.
      - UNAVAILABLE: tokens are 0 because the provider did not report
        them.  The numbers MUST NOT be added to any real-token total.
      - PROVIDER_REPORTED: numbers are the provider's measurement.

    `usage_is_synthetic` is kept for back-compat:
      SYNTHETIC         -> True
      PROVIDER_REPORTED -> False
      UNAVAILABLE       -> False
    """
    model_config = ConfigDict(extra="forbid")

    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    monetary_usd: float = 0.0
    latency_ms: float = 0.0

    # Day 13.5: provider-reported reasoning tokens.  Only set when
    # the provider returned `usage.completion_tokens_details.reasoning_tokens`;
    # otherwise None.  We never infer / estimate.
    reasoning_tokens: Optional[int] = None

    # Day 12: explicit provider / model / synthetic flag
    provider: str = "unknown"          # e.g. "fake" | "openai" | "minimax" | "qwen"
    model: str = "unknown"             # e.g. "gpt-4o-mini" | "fake-deterministic"

    # Day 13.5: the authoritative 3-state usage classification.
    usage_type: UsageType = Field(
        default=UsageType.SYNTHETIC,
        description="Where the input_tokens/output_tokens numbers came from.",
    )
    # Day 12 field kept for back-compat.  Derived from usage_type:
    #   SYNTHETIC -> True, otherwise False.
    usage_is_synthetic: bool = True
    request_id: Optional[str] = None   # provider's request id, if any

    def model_post_init(self, __context) -> None:  # type: ignore[no-untyped-def]
        # Keep usage_is_synthetic in sync with usage_type.  We do not
        # fail on mismatch (some legacy code may still set the bool
        # directly); we just correct it.
        expected_synth = (self.usage_type == UsageType.SYNTHETIC)
        if self.usage_is_synthetic != expected_synth:
            object.__setattr__(self, "usage_is_synthetic", expected_synth)


@runtime_checkable
class LLMClient(Protocol):
    """Anything that can turn a prompt into an LLMResponse."""

    def generate(self, prompt: str) -> LLMResponse: ...


class RealLLMProviderConfig(BaseModel):
    """Configuration for a real LLM provider adapter.

    Notes:
      - `api_key` is intentionally NOT a field.  It is read from the
        environment variable named by `api_key_env_var`.  The actual
        key is never stored on this model and never logged.
      - All other fields are safe to serialize.
      - Compatibility with any particular provider is NOT implied
        by the field set.  The adapter in `llm/providers/real.py`
        currently verifies the contract for a single, named
        provider + endpoint pair; do not assume "works with X by
        changing base_url".

    Day 13.5 fields:
      - `response_format`: standard OpenAI-compatible structured-output
        hint.  Example: `{"type": "json_object"}` or
        `{"type": "json_schema", "json_schema": {...}}`.  When set,
        the adapter writes it into the top level of the request body.
      - `provider_options`: a dict of provider-specific request body
        fields merged at the TOP LEVEL of the JSON body (NOT under
        `extra_body`).  This is the documented mechanism for
        non-OpenAI fields like Qwen's `enable_thinking`,
        `thinking_budget`, `response_format` (if not using the
        standard field), etc.

        We do NOT wrap into `extra_body` because the current adapter
        speaks HTTP directly (not the OpenAI Python SDK).  Provider
        docs that show `extra_body={...}` are SDK-only; the
        direct-HTTP equivalent is to put the same keys at the top
        level of the body.
    """
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(..., description="e.g. 'openai' | 'minimax' | 'qwen'")
    model: str = Field(..., description="e.g. 'gpt-4o-mini'")
    base_url: Optional[str] = Field(
        default=None, description="override base URL if needed"
    )
    timeout_seconds: float = 60.0
    max_retries: int = 1
    temperature: float = 0.0
    seed: Optional[int] = 1234
    response_format: Optional[dict] = Field(
        default=None,
        description=(
            "OpenAI-compatible response_format object; written to top "
            "level of request body. e.g. {'type': 'json_object'}"
        ),
    )
    provider_options: dict = Field(
        default_factory=dict,
        description=(
            "Provider-specific body fields merged at the TOP LEVEL of "
            "the JSON request body. Example for Qwen: "
            "{'enable_thinking': False}. NOT wrapped in 'extra_body'."
        ),
    )
    # api_key is intentionally NOT serialized.  Use env_var to read.
    api_key_env_var: str = "REAL_LLM_API_KEY"

    def has_api_key(self) -> bool:
        """True iff the env var named by api_key_env_var is set and non-empty."""
        return bool(os.environ.get(self.api_key_env_var))
