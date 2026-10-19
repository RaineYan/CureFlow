"""Explicit session configuration for the opt-in loopback web server.

The HTTP handler must enforce its loopback/origin boundary before calling this
module. Keys only enter this process's environment, which existing adapters
already consume. This operation does not contact a provider or persist data.
"""
from __future__ import annotations

import os
from threading import Lock

from cureflow_app.provider_runtime import DOUBAO_MODEL, DESKTOP_MODEL_ENVS, DesktopProviderError, DesktopProviderRuntime
from cureflow_app.key_store import KeyStore

_LOCK = Lock()
_PROVIDERS = {
    "openai": ("OPENAI_API_KEY", "CUREFLOW_ENABLE_OPENAI_LIVE"),
    "qwen": ("QWEN_API_KEY", "CUREFLOW_ENABLE_QWEN_LIVE"),
    "deepseek": ("DEEPSEEK_API_KEY", "CUREFLOW_ENABLE_DEEPSEEK_LIVE"),
    "doubao": ("DOUBAO_API_KEY", "CUREFLOW_ENABLE_DOUBAO_LIVE"),
}


def restore_persisted_keys(runtime: DesktopProviderRuntime, store: KeyStore) -> None:
    """Load opted-in DPAPI keys into this process, without cloud authorization."""
    if not store.is_opted_in():
        runtime.set_persistence(False, set())
        return
    providers = store.list_providers()
    for provider in providers:
        # An explicitly supplied session key always wins over an older saved
        # credential. Never attach the saved model to a different active key.
        if os.environ.get(_PROVIDERS[provider][0]):
            continue
        key = store.get(provider)
        if key:
            os.environ[_PROVIDERS[provider][0]] = key
            model = store.get_model(provider)
            if model and not os.environ.get(DESKTOP_MODEL_ENVS[provider]):
                os.environ[DESKTOP_MODEL_ENVS[provider]] = model
    runtime.set_persistence(True, set(providers))


def configure_session(runtime: DesktopProviderRuntime, payload: object) -> dict:
    if not isinstance(payload, dict) or set(payload) - {
        "provider", "apiKey", "modelId", "useExistingEnvironmentKey", "confirmSession",
    }:
        raise DesktopProviderError("连接配置格式不正确")
    provider = payload.get("provider")
    if not isinstance(provider, str) or provider not in _PROVIDERS:
        raise DesktopProviderError("不支持的模型服务")
    if payload.get("confirmSession") is not True:
        raise DesktopProviderError("请明确允许本次启动使用所选模型")
    existing = payload.get("useExistingEnvironmentKey", False)
    if not isinstance(existing, bool) or (existing and "apiKey" in payload):
        raise DesktopProviderError("请选择输入密钥或使用已有密钥其中一种方式")
    key_env, confirm_env = _PROVIDERS[provider]
    model_id = payload.get("modelId")
    if provider == "doubao" and model_id is None:
        model_id = model_id or os.environ.get("DOUBAO_MODEL") or DOUBAO_MODEL
    if model_id is not None and (not isinstance(model_id, str) or not model_id or len(model_id) > 128
                                 or any(c.isspace() or ord(c) < 32 for c in model_id)):
        raise DesktopProviderError("请填写该服务实际开通的模型 ID；豆包也可填写接入点 ID")
    with _LOCK:
        if runtime.killed:
            raise DesktopProviderError("云调用已停用，请重启服务后重新配置")
        key = os.environ.get(key_env, "") if existing else payload.get("apiKey", "")
        if not isinstance(key, str) or not 8 <= len(key) <= 4096 or any(c.isspace() for c in key) or "\0" in key:
            raise DesktopProviderError("密钥为空或格式不正确，请重新输入")
        if model_id == key:
            raise DesktopProviderError("模型 ID 与 API Key 不能相同，请将密钥只填入密钥框")
        if not existing:
            os.environ[key_env] = key
        if model_id is not None:
            os.environ[DESKTOP_MODEL_ENVS[provider]] = model_id
        os.environ[confirm_env] = "1"
        runtime.clear_verification(provider)
    return runtime.status()
