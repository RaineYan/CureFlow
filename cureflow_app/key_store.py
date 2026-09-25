"""Explicit, Windows-user-bound API key storage for the desktop app.

Only DPAPI ciphertext and non-secret metadata are written to disk.  On
non-Windows platforms persistence is unavailable; there is no plaintext or
portable-encryption fallback.
"""
from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import re
import tempfile
from ctypes import wintypes
from pathlib import Path

PROVIDERS = frozenset({"openai", "qwen", "deepseek", "doubao"})


class KeyStoreError(ValueError):
    """Safe message for a local UI; never contains a credential."""


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _dpapi(data: bytes, *, decrypt: bool) -> bytes:
    if os.name != "nt" or not hasattr(ctypes, "windll"):
        raise KeyStoreError("仅 Windows 支持本机加密保存密钥")
    source_buffer = ctypes.create_string_buffer(data, len(data))
    source = _DataBlob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_byte)))
    output = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    crypt32.CryptProtectData.argtypes = [ctypes.POINTER(_DataBlob), wintypes.LPCWSTR, ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob)]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob)]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    ctypes.windll.kernel32.LocalFree.restype = ctypes.c_void_p
    action = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    if decrypt:
        ok = action(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output))
    else:
        ok = action(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output))
    if not ok:
        raise KeyStoreError("无法解密本机保存的密钥，请重新输入")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)


def _protect(data: bytes) -> bytes:
    return _dpapi(data, decrypt=False)


def _unprotect(data: bytes) -> bytes:
    return _dpapi(data, decrypt=True)


class KeyStore:
    def __init__(self, path: Path | None = None):
        appdata = os.environ.get("APPDATA")
        if path is None and not appdata:
            raise KeyStoreError("找不到 Windows 用户配置目录")
        self.path = Path(path) if path else Path(appdata) / "cureflow" / "providers.json"

    def _read(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "optedIn": False, "keys": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if (not isinstance(data, dict) or data.get("version") != 1
                    or data.get("optedIn") is not True or not isinstance(data.get("keys"), dict)):
                raise ValueError()
            for provider, record in data["keys"].items():
                if (provider not in PROVIDERS or not isinstance(record, dict)
                        or not isinstance(record.get("ciphertext"), str)
                        or not isinstance(record.get("fingerprint"), str)
                        or not re.fullmatch(r"[0-9a-f]{12}", record["fingerprint"])
                        or (provider == "doubao" and (not isinstance(record.get("modelId"), str)
                            or not record["modelId"] or len(record["modelId"]) > 128
                            or any(c.isspace() for c in record["modelId"])))):
                    raise ValueError()
            return data
        except (OSError, ValueError, UnicodeError) as exc:
            raise KeyStoreError("本机密钥文件损坏或无法读取，请先关闭保存功能再重新配置") from exc

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.path.parent, prefix=".providers-", delete=False) as handle:
                temp_path = Path(handle.name)
                handle.write(encoded)
            os.replace(temp_path, self.path)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def is_opted_in(self) -> bool:
        return self._read()["optedIn"]

    def opt_in(self) -> None:
        if os.name != "nt":
            raise KeyStoreError("仅 Windows 支持本机加密保存密钥")
        if not self.is_opted_in():
            self._write({"version": 1, "optedIn": True, "keys": {}})

    def opt_out(self) -> None:
        # Exact user-specific file only; a missing file is already opted out.
        self.path.unlink(missing_ok=True)

    def set(self, provider: str, key: str, model_id: str | None = None) -> str:
        if provider not in PROVIDERS or not isinstance(key, str) or not 8 <= len(key) <= 4096 or any(c.isspace() for c in key):
            raise KeyStoreError("模型或密钥格式无效")
        data = self._read()
        if not data["optedIn"]:
            raise KeyStoreError("请先明确开启本机加密保存")
        if provider == "doubao" and (not isinstance(model_id, str) or not model_id or len(model_id) > 128 or any(c.isspace() for c in model_id)):
            raise KeyStoreError("保存豆包密钥前需填写已开通的模型或接入点 ID")
        ciphertext = _protect(key.encode("utf-8"))
        fingerprint = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
        data["keys"][provider] = {
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
            "fingerprint": fingerprint,
        }
        if provider == "doubao":
            data["keys"][provider]["modelId"] = model_id
        self._write(data)
        return fingerprint

    def get(self, provider: str) -> str | None:
        if provider not in PROVIDERS:
            raise KeyStoreError("不支持的模型服务")
        data = self._read()
        if not data["optedIn"] or provider not in data["keys"]:
            return None
        try:
            ciphertext = base64.b64decode(data["keys"][provider]["ciphertext"], validate=True)
            key = _unprotect(ciphertext).decode("utf-8")
            if hashlib.sha256(key.encode("utf-8")).hexdigest()[:12] != data["keys"][provider]["fingerprint"]:
                raise ValueError("fingerprint mismatch")
            return key
        except (ValueError, UnicodeError) as exc:
            raise KeyStoreError("本机保存的密钥无法解密，请重新输入") from exc

    def delete(self, provider: str) -> None:
        if provider not in PROVIDERS:
            raise KeyStoreError("不支持的模型服务")
        data = self._read()
        if data["optedIn"] and provider in data["keys"]:
            del data["keys"][provider]
            self._write(data)

    def get_model(self, provider: str) -> str | None:
        if provider not in PROVIDERS:
            raise KeyStoreError("不支持的模型服务")
        return self._read()["keys"].get(provider, {}).get("modelId")

    def list_providers(self) -> dict[str, str]:
        data = self._read()
        return {provider: record["fingerprint"] for provider, record in data["keys"].items()}
