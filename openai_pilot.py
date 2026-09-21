"""CureFlow OpenAI single-call pilot gate.

Running this file with no flags is a zero-cost preflight.  A real call needs
all three independent signals: ``--mode openai_responses``,
``--execute-live``, and local ``CUREFLOW_ENABLE_OPENAI_LIVE=1``.  The phase-2
runner permits exactly one call.
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Sequence

from llm.openai_audit import audit_openai_response
from llm.openai_responses import OpenAIResponsesConfig
from llm.provider_selection import (
    ProviderMode,
    ProviderSelection,
    build_llm_client,
)


LIVE_CONFIRM_ENV = "CUREFLOW_ENABLE_OPENAI_LIVE"


def preflight(
    *, mode: ProviderMode, execute_live: bool, config: OpenAIResponsesConfig
) -> dict:
    local_confirmation = os.environ.get(LIVE_CONFIRM_ENV) == "1"
    key_available = config.has_api_key()
    eligible = (
        mode == ProviderMode.OPENAI_RESPONSES
        and execute_live
        and local_confirmation
        and key_available
    )
    blockers: list[str] = []
    if mode != ProviderMode.OPENAI_RESPONSES:
        blockers.append("MODE_IS_OFFLINE")
    if not execute_live:
        blockers.append("EXECUTE_LIVE_NOT_REQUESTED")
    if not local_confirmation:
        blockers.append("LOCAL_LIVE_CONFIRMATION_MISSING")
    if not key_available:
        blockers.append("API_KEY_ENV_MISSING")
    return {
        "phase": "OPENAI_SINGLE_CALL_PILOT",
        "mode": mode.value,
        "model": config.model,
        "endpoint": "https://api.openai.com/v1/responses",
        "maxCalls": 1,
        "store": False,
        "eligibleForLiveCall": eligible,
        "blockers": blockers,
        "apiKeyValueExposed": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CureFlow OpenAI pilot gate")
    parser.add_argument(
        "--mode",
        choices=[item.value for item in ProviderMode],
        default=ProviderMode.OFFLINE.value,
    )
    parser.add_argument("--execute-live", action="store_true")
    parser.add_argument("--model", default="gpt-5.6-luna")
    args = parser.parse_args(argv)

    mode = ProviderMode(args.mode)
    config = OpenAIResponsesConfig(model=args.model)
    gate = preflight(mode=mode, execute_live=args.execute_live, config=config)
    print(json.dumps(gate, ensure_ascii=False, indent=2))
    if not gate["eligibleForLiveCall"]:
        return 0 if not args.execute_live else 2

    client = build_llm_client(
        ProviderSelection(mode=mode, allow_live=True, openai=config)
    )
    response = client.generate(
        "Return a short Chinese claim. Use evidence_refs "
        "[\"pilot/sample_metric@v1\"] and confidence 0.9.",
        task_name="openai_single_call_pilot",
    )
    audit = audit_openai_response(response, expected_model=config.model)
    result = {
        "audit": audit.to_dict(),
        "requestId": response.request_id,
        "usageType": response.usage_type.value,
        "inputTokens": response.input_tokens,
        "outputTokens": response.output_tokens,
        "latencyMs": response.latency_ms,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if audit.accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())

