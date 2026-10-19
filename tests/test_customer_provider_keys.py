"""User-owned keys/models: no sockets, no real credentials, no live calls."""
import json
import os
from io import BytesIO
from unittest.mock import patch

import pytest

from cureflow_app.key_store import KeyStore, KeyStoreError
from cureflow_app.local_provider_setup import configure_session, restore_persisted_keys, _PROVIDERS
from cureflow_app.provider_runtime import DesktopProviderRuntime, DesktopProviderError, DESKTOP_MODEL_ENVS, estimated_list_price_usd
from cureflow_app.server import make_handler


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    for key_env, confirm_env in _PROVIDERS.values():
        monkeypatch.setenv(key_env, "")
        monkeypatch.setenv(confirm_env, "")
    for model_env in DESKTOP_MODEL_ENVS.values():
        monkeypatch.setenv(model_env, "")


@pytest.mark.parametrize("provider", sorted(_PROVIDERS))
def test_user_key_and_model_reach_runtime_without_echo_or_calls(provider):
    runtime = DesktopProviderRuntime()
    placeholder = "unit-test-customer-owned-key-not-a-credential"
    with patch("urllib.request.urlopen", side_effect=AssertionError("No live call allowed")):
        status = configure_session(runtime, {"provider": provider, "apiKey": placeholder,
                                           "modelId": "customer-enabled-model", "confirmSession": True})
        state = status[provider]
        assert state["model"] == "customer-enabled-model"
        assert state["ready"] is False
        assert state["connectionVerified"] is False
        assert placeholder not in json.dumps(status)
        run = runtime.resolve(mode=state["mode"], confirm_live=True, confirm_max_calls=6)
        assert run.model == "customer-enabled-model" and run.provider == provider
        assert run.budget_client.calls_started == 0


@pytest.mark.parametrize("model", ["", "with space", "bad\x00model", "x" * 129, 123, []])
def test_bad_model_never_changes_key_or_authorizes(model):
    runtime = DesktopProviderRuntime()
    with pytest.raises(DesktopProviderError):
        configure_session(runtime, {"provider": "qwen", "apiKey": "unit-test-unused-key",
                                    "modelId": model, "confirmSession": True})
    assert os.environ["QWEN_API_KEY"] == ""
    assert os.environ["CUREFLOW_ENABLE_QWEN_LIVE"] == ""


def test_reconfiguring_key_invalidates_only_that_provider():
    runtime = DesktopProviderRuntime()
    runtime.mark_verified("openai")
    runtime.mark_verified("deepseek")
    configure_session(runtime, {"provider": "deepseek", "apiKey": "unit-test-new-session-key",
                               "modelId": "new-enabled-model", "confirmSession": True})
    assert runtime.is_verified("openai")
    assert not runtime.is_verified("deepseek")
    runtime.kill()
    with pytest.raises(DesktopProviderError):
        configure_session(runtime, {"provider": "deepseek", "apiKey": "unit-test-another-key", "confirmSession": True})
    assert os.environ["DEEPSEEK_API_KEY"] == "unit-test-new-session-key"


class SavedKeys:
    def __init__(self):
        self.reads = []

    def is_opted_in(self):
        return True

    def list_providers(self):
        return {"doubao": "0123456789ab"}

    def get(self, provider):
        self.reads.append(provider)
        return "unit-test-older-saved-key"

    def get_model(self, provider):
        return "older-saved-model"


def test_session_key_and_model_cannot_be_overwritten_by_saved_credentials(monkeypatch):
    monkeypatch.setenv("DOUBAO_API_KEY", "unit-test-fresh-session-key")
    monkeypatch.setenv("DOUBAO_MODEL", "fresh-session-model")
    saved = SavedKeys()
    restore_persisted_keys(DesktopProviderRuntime(), saved)
    assert saved.reads == []
    assert os.environ["DOUBAO_API_KEY"] == "unit-test-fresh-session-key"
    assert os.environ["DOUBAO_MODEL"] == "fresh-session-model"


def test_saved_key_restores_with_matching_model_but_not_authorization():
    saved = SavedKeys()
    restore_persisted_keys(DesktopProviderRuntime(), saved)
    assert os.environ["DOUBAO_API_KEY"] == "unit-test-older-saved-key"
    assert os.environ["DOUBAO_MODEL"] == "older-saved-model"
    assert os.environ["CUREFLOW_ENABLE_DOUBAO_LIVE"] != "1"


@pytest.mark.parametrize("provider", sorted(_PROVIDERS))
def test_model_metadata_round_trips_without_plaintext_key(tmp_path, provider):
    # Exercise real JSON persistence, isolating just the OS crypto boundary.
    store = KeyStore(tmp_path / "providers.json")
    store._write({"version": 1, "optedIn": True, "keys": {}})
    with patch("cureflow_app.key_store._protect", side_effect=lambda data: b"test-cipher:" + data[::-1]), \
         patch("cureflow_app.key_store._unprotect", side_effect=lambda data: data[len(b"test-cipher:"):][::-1]):
        store.set(provider, "unit-test-not-a-real-credential", "user-enabled-model")
        assert store.get(provider) == "unit-test-not-a-real-credential"
        assert store.get_model(provider) == "user-enabled-model"
        assert "unit-test-not-a-real-credential" not in store.path.read_text(encoding="utf-8")


def test_custom_model_does_not_inherit_unrelated_price_snapshot():
    assert estimated_list_price_usd(1000, 1000, model="customer-enabled-model") is None
    assert DesktopProviderRuntime().status()["openai"]["priceSnapshot"]["inputUsdPerMillionTokens"] > 0
    configure_session(DesktopProviderRuntime(), {"provider": "openai", "apiKey": "unit-test-key-placeholder",
                                               "modelId": "customer-enabled-model", "confirmSession": True})
    assert DesktopProviderRuntime().status()["openai"]["priceSnapshot"]["available"] is False


@pytest.mark.parametrize("provider", sorted(_PROVIDERS))
def test_key_cannot_be_saved_or_echoed_as_model_metadata(tmp_path, provider):
    value = "unit-test-model-field-key-placeholder"
    with pytest.raises(DesktopProviderError, match="不能相同"):
        configure_session(DesktopProviderRuntime(), {"provider": provider, "apiKey": value,
                                                   "modelId": value, "confirmSession": True})
    assert os.environ[_PROVIDERS[provider][0]] == ""
    store = KeyStore(tmp_path / "providers.json")
    with pytest.raises(KeyStoreError, match="不能相同"):
        store.set(provider, value, value)
    assert not store.path.exists()


def test_invalid_model_and_control_character_key_are_not_persisted(tmp_path):
    store = KeyStore(tmp_path / "providers.json")
    store._write({"version": 1, "optedIn": True, "keys": {}})
    with pytest.raises(KeyStoreError):
        store.set("openai", "unit-test-key-placeholder", "bad\x00model")
    with pytest.raises(KeyStoreError):
        store.set("openai", "unit-test\x00key-placeholder", "valid-model")
    assert store.list_providers() == {}


@pytest.mark.parametrize("provider", sorted(_PROVIDERS))
def test_local_key_endpoint_stores_user_model_and_returns_only_fingerprint(tmp_path, provider):
    store = KeyStore(tmp_path / "providers.json")
    store._write({"version": 1, "optedIn": True, "keys": {}})
    runtime = DesktopProviderRuntime()
    handler_type = make_handler(tmp_path, provider_runtime=runtime, allow_local_provider_setup=True, key_store=store)
    value = "unit-test-user-key-not-a-credential"
    body = json.dumps({"apiKey": value, "modelId": "customer-enabled-model"}).encode()
    handler = object.__new__(handler_type)
    handler.path = f"/api/keys/{provider}"
    handler.client_address = ("127.0.0.1", 12345)
    handler.headers = {"Host": "127.0.0.1:5173", "Origin": "http://127.0.0.1:5173", "Content-Type": "application/json", "Content-Length": str(len(body))}
    handler.rfile, handler.wfile = BytesIO(body), BytesIO()
    status = []
    handler.send_response = status.append
    handler.send_header = lambda *args: None
    handler.end_headers = lambda: None
    with patch("urllib.request.urlopen", side_effect=AssertionError("No network allowed")), \
         patch("cureflow_app.key_store._protect", side_effect=lambda data: b"test-cipher:" + data[::-1]):
        handler.do_POST()
    assert status == [200]
    response = json.loads(handler.wfile.getvalue())
    assert len(response["providers"][provider]) == 12
    assert value not in handler.wfile.getvalue().decode()
    assert "ciphertext" not in handler.wfile.getvalue().decode()
    assert store.get_model(provider) == "customer-enabled-model"
