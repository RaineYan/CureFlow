"""Windows DPAPI persistence contract, using disposable fake credentials."""
import json
import os

import pytest

from cureflow_app.key_store import KeyStore, KeyStoreError


@pytest.fixture
def store(tmp_path):
    if os.name != "nt":
        pytest.skip("DPAPI is Windows-only; no plaintext fallback")
    return KeyStore(tmp_path / "providers.json")


def test_opt_in_and_opt_out(store):
    assert not store.is_opted_in()
    store.opt_in()
    assert store.is_opted_in()
    store.opt_out()
    assert not store.is_opted_in()


def test_set_get_roundtrip_uses_ciphertext(store):
    store.opt_in()
    fake = "unit-test-placeholder-not-a-credential"
    store.set("openai", fake)
    assert store.get("openai") == fake
    assert fake not in store.path.read_text(encoding="utf-8")


def test_delete_one_provider(store):
    store.opt_in()
    store.set("qwen", "unit-test-placeholder-one")
    store.set("deepseek", "unit-test-placeholder-two")
    store.delete("qwen")
    assert store.get("qwen") is None
    assert store.get("deepseek") == "unit-test-placeholder-two"


def test_list_returns_fingerprint_not_plaintext(store):
    store.opt_in()
    fake = "unit-test-placeholder-not-a-credential"
    fingerprint = store.set("doubao", fake, "user-test-endpoint")
    assert store.list_providers() == {"doubao": fingerprint}
    assert fake not in json.dumps(store.list_providers())
    assert store.get_model("doubao") == "user-test-endpoint"


def test_corrupt_file_fails_closed(store):
    store.path.write_text('{"version":1,"optedIn":true,"keys":{"openai":{"plaintext":"bad"}}}', encoding="utf-8")
    with pytest.raises(KeyStoreError):
        store.list_providers()


def test_store_does_not_modify_agent_environment(store, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    store.opt_in()
    store.set("openai", "unit-test-placeholder-not-a-credential")
    assert "OPENAI_API_KEY" not in os.environ
