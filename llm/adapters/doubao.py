"""Volcengine Ark chat adapter; no live request occurs at import time."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid

from llm.base import RealLLMProviderConfig
from llm.providers.real import RealLLMClient, RealLLMError, RealLLMErrorKind

ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"


class DoubaoClient(RealLLMClient):
    """Keep the Ark error envelope distinct from an ordinary model reply."""

    def __init__(self, model: str):
        if not model or any(char.isspace() for char in model):
            raise ValueError("必须填写已开通的火山方舟模型或接入点 ID")
        super().__init__(RealLLMProviderConfig(
            provider="doubao", model=model, base_url=ARK_BASE_URL,
            api_key_env_var="DOUBAO_API_KEY", max_retries=0,
            seed=None, response_format={"type": "json_object"},
        ))

    def _endpoint(self) -> str:
        return f"{ARK_BASE_URL}/chat/completions"

    def _post(self, body: dict) -> dict:
        client_request_id = str(uuid.uuid4())
        request = urllib.request.Request(
            self._endpoint(), data=json.dumps(body).encode("utf-8"), method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
                "X-Client-Request-Id": client_request_id,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # Never expose Ark's response body: it may contain account data.
            code = exc.code
            kind = (RealLLMErrorKind.AUTH_ERROR if code in (401, 403) else
                    RealLLMErrorKind.RATE_LIMIT if code == 429 else
                    RealLLMErrorKind.TIMEOUT if code == 408 else
                    RealLLMErrorKind.NETWORK_ERROR if 500 <= code < 600 else
                    RealLLMErrorKind.INVALID_RESPONSE)
            raise RealLLMError(kind, f"HTTP {code}", status_code=code) from exc
        except urllib.error.URLError as exc:
            kind = RealLLMErrorKind.TIMEOUT if "timed out" in str(exc.reason).lower() else RealLLMErrorKind.NETWORK_ERROR
            raise RealLLMError(kind, "火山方舟连接失败") from exc
        except (TimeoutError, ConnectionError) as exc:
            raise RealLLMError(RealLLMErrorKind.TIMEOUT, "火山方舟连接超时") from exc
        except (ValueError, UnicodeError) as exc:
            raise RealLLMError(RealLLMErrorKind.INVALID_RESPONSE, "火山方舟响应不是有效 JSON") from exc
        if isinstance(payload, dict) and payload.get("error"):
            error = payload["error"]
            code = str(error.get("code", "") if isinstance(error, dict) else error).lower()
            if any(part in code for part in ("auth", "permission", "invalidapikey")):
                kind = RealLLMErrorKind.AUTH_ERROR
            elif any(part in code for part in ("limit", "quota")):
                kind = RealLLMErrorKind.RATE_LIMIT
            else:
                kind = RealLLMErrorKind.INVALID_RESPONSE
            raise RealLLMError(kind, "火山方舟返回错误响应")
        return payload
