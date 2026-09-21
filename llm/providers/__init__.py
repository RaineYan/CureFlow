"""LLM provider adapters.

Day 13: RealLLMClient (HTTP-based, OpenAI-compatible chat completions
schema) lives here.  The provider config (`RealLLMProviderConfig`) and
the contract (`LLMClient` Protocol) are in llm/base.py.
"""
from .real import RealLLMClient, RealLLMError, RealLLMErrorKind

__all__ = ["RealLLMClient", "RealLLMError", "RealLLMErrorKind"]
