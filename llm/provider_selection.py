"""Explicit provider selection for CureFlow.

No environment variable silently changes the provider.  Callers must choose
OpenAI *and* pass ``allow_live=True``; otherwise the deterministic offline
fixture remains the default.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict

from .base import LLMClient
from .fake import default_workflow_llm
from .openai_responses import OpenAIResponsesClient, OpenAIResponsesConfig


class ProviderMode(str, Enum):
    """Low-level offline/OpenAI selector used by ``build_llm_client``.

    The desktop's full provider list lives in
    ``cureflow_app.provider_runtime.DesktopProviderRuntime``. Its
    ``resolve`` gate routes OpenAI through this selector, Qwen and
    DeepSeek through ``RealLLMClient``, and Doubao through
    ``DoubaoClient`` (a ``RealLLMClient`` subclass). Do not use this
    enum as the desktop provider catalog.
    """
    OFFLINE = "offline"
    OPENAI_RESPONSES = "openai_responses"


class ProviderSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: ProviderMode = ProviderMode.OFFLINE
    allow_live: bool = False
    openai: Optional[OpenAIResponsesConfig] = None


def build_llm_client(
    selection: Optional[ProviderSelection] = None,
) -> LLMClient:
    """Build a client without any implicit network-mode switch."""

    resolved = selection or ProviderSelection()
    if resolved.mode == ProviderMode.OFFLINE:
        return default_workflow_llm()
    if not resolved.allow_live:
        raise PermissionError(
            "OpenAI live mode requires explicit allow_live=True"
        )
    return OpenAIResponsesClient(resolved.openai or OpenAIResponsesConfig())
