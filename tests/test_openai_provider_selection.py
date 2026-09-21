"""Offline tests for explicit provider routing and Responses auditing."""
from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

import openai_pilot
from llm import FakeLLMClient, LLMResponse, UsageType
from llm.openai_audit import audit_openai_response
from llm.openai_responses import OpenAIResponsesClient, OpenAIResponsesConfig
from llm.providers.real import RealLLMError, RealLLMErrorKind
from llm.provider_selection import (
    ProviderMode,
    ProviderSelection,
    build_llm_client,
)


KEY_ENV = "CUREFLOW_SELECTION_TEST_KEY"


def _valid_response(**updates) -> LLMResponse:
    values = {
        "text": json.dumps(
            {
                "claim_text": "收入增长。",
                "evidence_refs": ["run/metric@v1"],
                "confidence": 0.9,
            },
            ensure_ascii=False,
        ),
        "input_tokens": 20,
        "output_tokens": 10,
        "provider": "openai",
        "model": "gpt-5.6-luna",
        "usage_type": UsageType.PROVIDER_REPORTED,
        "request_id": "resp_test_001",
    }
    values.update(updates)
    return LLMResponse(**values)


def test_default_selection_is_offline() -> None:
    assert isinstance(build_llm_client(), FakeLLMClient)


def test_openai_mode_requires_explicit_live_permission() -> None:
    with pytest.raises(PermissionError, match="allow_live"):
        build_llm_client(
            ProviderSelection(mode=ProviderMode.OPENAI_RESPONSES)
        )


def test_openai_mode_then_requires_local_key() -> None:
    os.environ.pop(KEY_ENV, None)
    with pytest.raises(RealLLMError, match="is not set") as caught:
        build_llm_client(
            ProviderSelection(
                mode=ProviderMode.OPENAI_RESPONSES,
                allow_live=True,
                openai=OpenAIResponsesConfig(api_key_env_var=KEY_ENV),
            )
        )
    assert caught.value.kind == RealLLMErrorKind.AUTH_ERROR


def test_explicit_openai_selection_builds_new_adapter() -> None:
    os.environ[KEY_ENV] = "not-a-real-key"
    try:
        client = build_llm_client(
            ProviderSelection(
                mode=ProviderMode.OPENAI_RESPONSES,
                allow_live=True,
                openai=OpenAIResponsesConfig(api_key_env_var=KEY_ENV),
            )
        )
        assert isinstance(client, OpenAIResponsesClient)
    finally:
        os.environ.pop(KEY_ENV, None)


def test_responses_audit_accepts_native_verified_record() -> None:
    audit = audit_openai_response(
        _valid_response(), expected_model="gpt-5.6-luna"
    )
    assert audit.request_succeeded is True
    assert audit.schema_valid is True
    assert audit.accepted is True
    assert audit.reason_codes == ()


@pytest.mark.parametrize(
    ("updates", "reason"),
    [
        ({"request_id": "chatcmpl_old"}, "INVALID_RESPONSES_REQUEST_ID"),
        ({"usage_type": UsageType.UNAVAILABLE}, "USAGE_NOT_PROVIDER_REPORTED"),
        ({"model": "different"}, "PROVIDER_IDENTITY_MISMATCH"),
        ({"text": "not json"}, "CLAIM_SCHEMA_INVALID"),
    ],
)
def test_responses_audit_rejects_each_missing_proof(updates, reason) -> None:
    audit = audit_openai_response(
        _valid_response(**updates), expected_model="gpt-5.6-luna"
    )
    assert audit.accepted is False
    assert reason in audit.reason_codes


def test_provider_error_envelope_is_not_schema_success() -> None:
    response = _valid_response(
        text='{"error":"RATE_LIMIT","message":"slow down"}',
        usage_type=UsageType.UNAVAILABLE,
    )
    audit = audit_openai_response(response, expected_model="gpt-5.6-luna")
    assert audit.schema_valid is False
    assert "PROVIDER_ERROR_ENVELOPE" in audit.reason_codes


def test_pilot_default_preflight_is_zero_cost(capsys) -> None:
    os.environ.pop("OPENAI_API_KEY", None)
    os.environ.pop(openai_pilot.LIVE_CONFIRM_ENV, None)
    with patch("llm.openai_responses.urllib.request.urlopen") as network:
        rc = openai_pilot.main([])
    output = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert output["mode"] == "offline"
    assert output["eligibleForLiveCall"] is False
    assert output["maxCalls"] == 1
    assert output["store"] is False
    network.assert_not_called()


def test_execute_flag_alone_cannot_call_network(capsys) -> None:
    os.environ.pop("OPENAI_API_KEY", None)
    os.environ.pop(openai_pilot.LIVE_CONFIRM_ENV, None)
    with patch("llm.openai_responses.urllib.request.urlopen") as network:
        rc = openai_pilot.main(
            ["--mode", "openai_responses", "--execute-live"]
        )
    output = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert "LOCAL_LIVE_CONFIRMATION_MISSING" in output["blockers"]
    assert "API_KEY_ENV_MISSING" in output["blockers"]
    network.assert_not_called()


def test_pilot_preflight_never_exposes_secret() -> None:
    secret = "local-secret-value"
    os.environ["OPENAI_API_KEY"] = secret
    os.environ[openai_pilot.LIVE_CONFIRM_ENV] = "1"
    try:
        result = openai_pilot.preflight(
            mode=ProviderMode.OPENAI_RESPONSES,
            execute_live=True,
            config=OpenAIResponsesConfig(),
        )
        assert result["eligibleForLiveCall"] is True
        assert secret not in json.dumps(result)
        assert result["apiKeyValueExposed"] is False
    finally:
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop(openai_pilot.LIVE_CONFIRM_ENV, None)
