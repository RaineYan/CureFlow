"""Offline Ark envelope tests; never contact the real provider."""
import io
import json
import urllib.error
import uuid
from unittest.mock import patch

import pytest

from llm.adapters.doubao import DoubaoClient


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("DOUBAO_API_KEY", "unit-test-placeholder-not-a-key")
    return DoubaoClient("user-supplied-test-endpoint")


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


def _outcome(response):
    body = json.loads(response.text)
    return "error" not in body, body.get("error")


def test_normal_envelope_has_provider_success_and_uuid_request_id(client):
    request_id = "a10b47d0-9ef1-4a13-9ac7-55ed62013b71"
    payload = {"id": request_id, "choices": [{"message": {"content": '{"claim_text":"ok"}'}}], "usage": {"prompt_tokens": 2, "completion_tokens": 3}}
    with patch("urllib.request.urlopen", return_value=_Response(payload)) as call:
        response = client.generate("test")
    assert _outcome(response) == (True, None)
    assert response.request_id == request_id
    request = call.call_args.args[0]
    assert request.full_url == "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
    assert request.get_header("Authorization").startswith("Bearer ")
    assert uuid.UUID(request.get_header("X-client-request-id")).version == 4


def test_error_envelope_is_not_provider_success(client):
    with patch("urllib.request.urlopen", return_value=_Response({"error": {"code": "InvalidApiKey", "message": "secret"}})):
        response = client.generate("test")
    assert _outcome(response) == (False, "AUTH_ERROR")
    assert "secret" not in response.text


def test_http_500_is_failed_network_class(client):
    error = urllib.error.HTTPError("https://example.invalid", 500, "server", {}, io.BytesIO(b'{}'))
    with patch("urllib.request.urlopen", side_effect=error):
        response = client.generate("test")
    assert _outcome(response) == (False, "NETWORK_ERROR")


def test_network_failure_is_not_provider_success(client):
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("disconnected")):
        response = client.generate("test")
    assert _outcome(response) == (False, "NETWORK_ERROR")


def test_http_401_is_auth_failure(client):
    error = urllib.error.HTTPError("https://example.invalid", 401, "unauthorized", {}, io.BytesIO(b'{}'))
    with patch("urllib.request.urlopen", side_effect=error):
        response = client.generate("test")
    assert _outcome(response) == (False, "AUTH_ERROR")
