"""Offline contract tests for the opt-in OpenAI Responses adapter."""
from __future__ import annotations

import json
import os
import urllib.error
from unittest.mock import patch

import pytest

from llm import UsageType
from llm.openai_responses import (
    CLAIM_OUTPUT_SCHEMA,
    OPENAI_RESPONSES_ENDPOINT,
    OpenAIResponsesClient,
    OpenAIResponsesConfig,
    is_openai_response_id,
)
from llm.providers.real import RealLLMError, RealLLMErrorKind


ENV_NAME = "CUREFLOW_TEST_OPENAI_KEY"
FAKE_SECRET = "unit-test-credential-not-real"


@pytest.fixture
def client():
    os.environ[ENV_NAME] = FAKE_SECRET
    try:
        yield OpenAIResponsesClient(
            OpenAIResponsesConfig(api_key_env_var=ENV_NAME)
        )
    finally:
        os.environ.pop(ENV_NAME, None)


def _payload(*, usage=True, status="completed") -> dict:
    body = {
        "id": "resp_cureflow_mock_001",
        "status": status,
        "output": [
            {"type": "reasoning", "id": "rs_1"},
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(
                            {
                                "claim_text": "指标结果稳定。",
                                "evidence_refs": ["run/metric@v1"],
                                "confidence": 0.9,
                            },
                            ensure_ascii=False,
                        ),
                    }
                ],
            },
        ],
    }
    if usage:
        body["usage"] = {
            "input_tokens": 101,
            "output_tokens": 29,
            "total_tokens": 130,
            "output_tokens_details": {"reasoning_tokens": 7},
        }
    return body


def test_default_is_luna_and_endpoint_is_pinned() -> None:
    cfg = OpenAIResponsesConfig()
    assert cfg.model == "gpt-5.6-luna"
    assert OpenAIResponsesClient.endpoint == OPENAI_RESPONSES_ENDPOINT
    assert OPENAI_RESPONSES_ENDPOINT == "https://api.openai.com/v1/responses"


def test_missing_key_fails_before_network() -> None:
    os.environ.pop(ENV_NAME, None)
    with pytest.raises(RealLLMError) as caught:
        OpenAIResponsesClient(OpenAIResponsesConfig(api_key_env_var=ENV_NAME))
    assert caught.value.kind == RealLLMErrorKind.AUTH_ERROR


def test_config_never_serializes_a_secret() -> None:
    os.environ[ENV_NAME] = FAKE_SECRET
    try:
        cfg = OpenAIResponsesConfig(api_key_env_var=ENV_NAME)
        encoded = json.dumps(cfg.model_dump())
        assert FAKE_SECRET not in encoded
        assert "api_key" not in OpenAIResponsesConfig.model_fields
    finally:
        os.environ.pop(ENV_NAME, None)


def test_request_enforces_no_storage_and_strict_schema(client) -> None:
    body = client._request_body("prompt")
    assert body["store"] is False
    assert body["model"] == "gpt-5.6-luna"
    assert body["reasoning"] == {"effort": "none"}
    fmt = body["text"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["strict"] is True
    assert fmt["schema"] == CLAIM_OUTPUT_SCHEMA
    assert fmt["schema"]["additionalProperties"] is False


def test_success_preserves_native_id_and_provider_usage(client) -> None:
    with patch.object(client, "_post_once", return_value=_payload()):
        result = client.generate("prompt", task_name="claim")
    assert result.provider == "openai"
    assert result.model == "gpt-5.6-luna"
    assert result.request_id == "resp_cureflow_mock_001"
    assert is_openai_response_id(result.request_id)
    assert result.usage_type == UsageType.PROVIDER_REPORTED
    assert result.input_tokens == 101
    assert result.output_tokens == 29
    assert result.reasoning_tokens == 7
    assert json.loads(result.text)["evidence_refs"] == ["run/metric@v1"]


def test_missing_usage_is_unavailable_not_synthetic(client) -> None:
    with patch.object(client, "_post_once", return_value=_payload(usage=False)):
        result = client.generate("prompt")
    assert result.usage_type == UsageType.UNAVAILABLE
    assert result.usage_is_synthetic is False
    assert result.input_tokens == 0
    assert result.output_tokens == 0


def test_output_extractor_skips_non_message_items(client) -> None:
    payload = _payload()
    payload["output"].insert(0, {"type": "function_call", "arguments": "{}"})
    with patch.object(client, "_post_once", return_value=payload):
        result = client.generate("prompt")
    assert json.loads(result.text)["claim_text"] == "指标结果稳定。"


@pytest.mark.parametrize("status", ["failed", "incomplete", "cancelled"])
def test_non_completed_status_is_structured_error(client, status: str) -> None:
    with patch.object(client, "_post_once", return_value=_payload(status=status)):
        result = client.generate("prompt")
    error = json.loads(result.text)
    assert error["error"] == "INVALID_RESPONSE"
    assert result.usage_type == UsageType.UNAVAILABLE
    assert result.request_id == "resp_cureflow_mock_001"


def test_missing_output_text_is_structured_error(client) -> None:
    payload = _payload()
    payload["output"] = [{"type": "reasoning"}]
    with patch.object(client, "_post_once", return_value=payload):
        result = client.generate("prompt")
    assert json.loads(result.text)["error"] == "INVALID_RESPONSE"
    assert result.request_id == "resp_cureflow_mock_001"


def test_non_object_response_is_structured_error(client) -> None:
    with patch.object(client, "_post_once", return_value=["unexpected"]):
        result = client.generate("prompt")
    assert json.loads(result.text)["error"] == "INVALID_RESPONSE"
    assert result.usage_type == UsageType.UNAVAILABLE


def test_invalid_usage_is_not_counted(client) -> None:
    payload = _payload()
    payload["usage"]["input_tokens"] = -1
    with patch.object(client, "_post_once", return_value=payload):
        result = client.generate("prompt")
    assert json.loads(result.text)["error"] == "INVALID_RESPONSE"
    assert result.usage_type == UsageType.UNAVAILABLE
    assert result.input_tokens == 0


def test_auth_error_never_leaks_key(client) -> None:
    def fail(_body):
        raise RealLLMError(
            RealLLMErrorKind.AUTH_ERROR,
            f"provider echoed {FAKE_SECRET}",
            status_code=401,
        )

    with patch.object(client, "_post_once", side_effect=fail):
        result = client.generate("prompt")
    assert FAKE_SECRET not in result.text
    assert "<redacted>" in result.text


def test_rate_limit_retries_once(client) -> None:
    rate_limit = RealLLMError(
        RealLLMErrorKind.RATE_LIMIT, "HTTP 429", status_code=429
    )
    with patch.object(
        client, "_post_once", side_effect=[rate_limit, _payload()]
    ) as post, patch("llm.openai_responses.time.sleep") as sleep:
        result = client.generate("prompt")
    assert post.call_count == 2
    sleep.assert_called_once()
    assert result.usage_type == UsageType.PROVIDER_REPORTED


def test_legacy_chat_completion_id_is_not_relabelled_as_responses() -> None:
    assert is_openai_response_id("resp_native") is True
    assert is_openai_response_id("chatcmpl-legacy") is False
    assert is_openai_response_id(None) is False


def test_http_401_classification() -> None:
    assert _http_result(401) == RealLLMErrorKind.AUTH_ERROR


def _http_result(status: int) -> RealLLMErrorKind:
    from llm.openai_responses import _error_kind_for_status

    return _error_kind_for_status(status)
