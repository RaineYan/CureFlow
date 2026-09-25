"""Session setup security and behavior, with no sockets or real providers."""
import json
import os
from io import BytesIO
from unittest.mock import patch

import pytest

from cureflow_app.provider_runtime import DOUBAO_MODEL, DesktopProviderRuntime
from cureflow_app.server import make_handler


@pytest.fixture
def setup_request(tmp_path, monkeypatch):
    for provider in ("OPENAI", "QWEN", "DEEPSEEK", "DOUBAO"):
        monkeypatch.delenv(f"{provider}_API_KEY", raising=False)
        monkeypatch.delenv(f"CUREFLOW_ENABLE_{provider}_LIVE", raising=False)
    monkeypatch.delenv("DOUBAO_MODEL", raising=False)
    runtime = DesktopProviderRuntime()

    def request(payload=None, *, enabled=True, peer="127.0.0.1", headers=None, raw=None, method="POST"):
        handler = object.__new__(make_handler(tmp_path, provider_runtime=runtime, allow_local_provider_setup=enabled))
        body = raw if raw is not None else json.dumps(payload).encode()
        handler.path = "/api/provider-status" if method == "GET" else "/api/provider/configure"
        handler.client_address = (peer, 12345)
        handler.headers = {"Host": "127.0.0.1:5173", "Origin": "http://127.0.0.1:5173", "Content-Type": "application/json", "Content-Length": str(len(body)), **(headers or {})}
        handler.rfile, handler.wfile = BytesIO(body), BytesIO()
        result = {}
        handler.send_response = lambda status: result.update(status=status)
        handler.send_header = lambda *args: None
        handler.end_headers = lambda: None
        with patch("urllib.request.urlopen", side_effect=AssertionError("No provider call allowed")):
            getattr(handler, f"do_{method}")()
        result["body"] = json.loads(handler.wfile.getvalue())
        return result

    request.runtime = runtime
    return request


@pytest.mark.parametrize("provider", ["openai", "qwen", "deepseek"])
def test_configure_session_without_persistence_or_network(setup_request, tmp_path, provider):
    value = "unit-test-placeholder-not-a-credential"
    result = setup_request({"provider": provider, "apiKey": value, "confirmSession": True})
    assert result["status"] == 200
    assert result["body"][provider]["ready"] is False
    assert result["body"][provider]["connectionVerified"] is False
    assert "PROVIDER_CONNECTION_UNVERIFIED" in result["body"][provider]["blockers"]
    assert value not in json.dumps(result)
    assert not list(tmp_path.iterdir())
    assert os.environ[f"{provider.upper()}_API_KEY"] == value


@pytest.mark.parametrize("options", [
    {"enabled": False}, {"peer": "192.168.1.2"},
    {"headers": {"Origin": None}}, {"headers": {"Origin": "null"}},
    {"headers": {"Origin": "https://evil.example"}},
    {"headers": {"Host": "evil.example", "Origin": "http://evil.example"}},
    {"headers": {"Origin": "http://localhost:5173"}},
])
def test_remote_cross_origin_and_default_disabled_are_rejected(setup_request, options):
    result = setup_request({"provider": "openai", "apiKey": "unit-test-placeholder", "confirmSession": True}, **options)
    assert result["status"] == 403
    assert "OPENAI_API_KEY" not in os.environ


@pytest.mark.parametrize("payload", [
    [], {"provider": []}, {"provider": "unknown", "confirmSession": True},
    {"provider": "openai", "apiKey": "unit-test-placeholder"},
    {"provider": "openai", "apiKey": "short", "confirmSession": True},
    {"provider": "openai", "apiKey": "with white space", "confirmSession": True},
    {"provider": "openai", "apiKey": "unit-test-placeholder", "confirmSession": True, "extra": 1},
    {"provider": "openai", "useExistingEnvironmentKey": True, "apiKey": "unit-test-placeholder", "confirmSession": True},
])
def test_invalid_config_never_authorizes_or_echoes_key(setup_request, payload):
    result = setup_request(payload)
    assert result["status"] == 400
    assert "CUREFLOW_ENABLE_OPENAI_LIVE" not in os.environ
    assert "unit-test-placeholder" not in json.dumps(result)


@pytest.mark.parametrize("raw,headers", [(b"{bad-json", {}), (b"x" * 8193, {}), (b"{}", {"Content-Type": "text/plain"}), (b"{}", {"Content-Length": "20"})])
def test_malformed_requests_are_safe(setup_request, raw, headers, capsys):
    assert setup_request(raw=raw, headers=headers)["status"] == 400
    assert not capsys.readouterr().err


def test_existing_key_needs_explicit_authorization(setup_request, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "unit-test-placeholder")
    assert not setup_request(method="GET")["body"]["deepseek"]["ready"]
    result = setup_request({"provider": "deepseek", "useExistingEnvironmentKey": True, "confirmSession": True})
    assert not result["body"]["deepseek"]["ready"]
    assert "PROVIDER_CONNECTION_UNVERIFIED" in result["body"]["deepseek"]["blockers"]


def test_confirmed_doubao_model_does_not_need_manual_model_id(setup_request):
    before = setup_request(method="GET")["body"]["doubao"]
    assert before["model"] == DOUBAO_MODEL
    assert before["ready"] is False
    assert "API_KEY_ENV_MISSING" in before["blockers"]

    result = setup_request({
        "provider": "doubao", "apiKey": "unit-test-placeholder-not-a-credential",
        "confirmSession": True,
    })
    assert result["status"] == 200
    assert result["body"]["doubao"]["model"] == DOUBAO_MODEL
    assert result["body"]["doubao"]["ready"] is False
    assert "PROVIDER_CONNECTION_UNVERIFIED" in result["body"]["doubao"]["blockers"]


def test_kill_switch_cannot_be_overridden_by_setup(setup_request):
    setup_request.runtime.kill()
    assert setup_request({"provider": "qwen", "apiKey": "unit-test-placeholder", "confirmSession": True})["status"] == 400
    assert "QWEN_API_KEY" not in os.environ


def test_setup_discovery_does_not_require_origin_on_same_origin_get(setup_request):
    assert setup_request(method="GET", headers={"Origin": None})["body"]["localSetupEnabled"] is True
    assert setup_request(method="GET", enabled=False)["body"]["localSetupEnabled"] is False
