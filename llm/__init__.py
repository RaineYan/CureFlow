"""LLM provider abstraction (Day 5 + Day 6 + Day 13 + Day 13.5).

Day 5:  small LLMClient Protocol so unit tests can run without network.
Day 6:  per-task responder routing + default_workflow_llm() factory
        that returns realistic Chinese business text for the 3-branch
        baseline workflow.
Day 13: RealLLMClient (OpenAI-compatible HTTP) and structured-output
        parser.  Real LLM responses carry usage_is_synthetic=False;
        FakeLLMClient responses carry usage_is_synthetic=True.
Day 13.5: tightened usage flag into a 3-state UsageType enum
        (SYNTHETIC / PROVIDER_REPORTED / UNAVAILABLE) and tightened
        the JSON parser to plain-or-single-fence.
"""
from .base import (
    LLMClient,
    LLMResponse,
    RealLLMProviderConfig,
    UsageType,
)
from .fake import (
    FakeLLMClient,
    default_workflow_llm,
    make_financial_responder,
    make_operation_responder,
    make_section_responder,
    make_user_responder,
)
from .parse import extract_json_object, parse_claim_payload
from .providers import RealLLMClient, RealLLMError, RealLLMErrorKind

__all__ = [
    "LLMClient",
    "LLMResponse",
    "RealLLMProviderConfig",
    "UsageType",
    "FakeLLMClient",
    "default_workflow_llm",
    "make_financial_responder",
    "make_user_responder",
    "make_operation_responder",
    "make_section_responder",
    "extract_json_object",
    "parse_claim_payload",
    "RealLLMClient",
    "RealLLMError",
    "RealLLMErrorKind",
]
