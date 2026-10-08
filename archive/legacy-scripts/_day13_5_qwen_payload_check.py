"""
Day 13.5 — Qwen request payload inspection.

Builds the EXACT request body that the Live Gate would send to
DashScope.  Uses the same `_qwen_config()` shape as `live_gate_run.py`,
but does NOT make a real network call.  Instead it intercepts the
urlopen and prints the captured body for inspection.

API key is replaced with `[REDACTED]` in the printed output.  The
Authorization header is also redacted to its last 4 chars only.
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Optional


def _hr(t: str) -> None:
    print()
    print("=" * 80)
    print(t)
    print("=" * 80)


def main() -> int:
    # Provide a fake env var so RealLLMClient.__init__ does not reject
    # the config.  This is NOT a real API key; it lives only in the
    # local process and is redacted before any print.
    fake_key_env = "REAL_LLM_DEMO_KEY"
    os.environ[fake_key_env] = "demo-key-not-real-do-not-use-anywhere"

    from llm import RealLLMClient, RealLLMProviderConfig

    cfg = RealLLMProviderConfig(
        provider="qwen",
        model="qwen3.7-plus-2026-05-26",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key_env_var=fake_key_env,
        timeout_seconds=30.0,
        max_retries=1,
        temperature=0.0,
        seed=1234,
        response_format={"type": "json_object"},
        provider_options={"enable_thinking": False},
    )
    client = RealLLMClient(cfg)

    _hr("RESOLVED ENDPOINT")
    print(f"  base_url      = {cfg.base_url}")
    print(f"  resolved URL  = {client._endpoint()}")

    _hr("RESOLVED REQUEST CONFIG")
    print(f"  provider          = {cfg.provider}")
    print(f"  model             = {cfg.model}")
    print(f"  temperature       = {cfg.temperature}")
    print(f"  seed              = {cfg.seed}")
    print(f"  response_format   = {cfg.response_format}")
    print(f"  provider_options  = {cfg.provider_options}")
    print(f"  timeout_seconds   = {cfg.timeout_seconds}")
    print(f"  max_retries       = {cfg.max_retries}")

    _hr("CAPTURED HTTP REQUEST (mock)")
    captured: dict = {}

    def _mock_urlopen(req, *args, **kwargs):
        # Capture the request body and headers, then return a fake
        # response so the call appears to succeed (but we never read
        # the response back).
        captured["body_bytes"] = req.data
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["headers"] = dict(req.headers)
        # Synthesize a successful Qwen response
        body = json.dumps({
            "id": "chatcmpl-DEMO",
            "choices": [{"message": {"role": "assistant",
                                       "content": "{}"}}],
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 4,
                "total_tokens": 16,
                "completion_tokens_details": {"reasoning_tokens": 0},
            },
        }).encode("utf-8")
        class _Resp:
            def read(inner_self) -> bytes:
                return body
            def __enter__(inner_self):
                return inner_self
            def __exit__(inner_self, *a):
                return False
        return _Resp()

    import urllib.request
    orig = urllib.request.urlopen
    urllib.request.urlopen = _mock_urlopen
    try:
        client.generate("hello", task_name="generate_financial_claim")
    finally:
        urllib.request.urlopen = orig

    # Pretty-print the captured body with the api key redacted
    body_text = captured["body_bytes"].decode("utf-8")
    body_obj = json.loads(body_text)
    auth = captured["headers"].get("Authorization", "")
    redacted_auth = re.sub(r"Bearer\s+\S+", "Bearer ***REDACTED***", auth)

    print(f"  URL            = {captured['url']}")
    print(f"  Method         = {captured['method']}")
    print(f"  Headers:")
    for k, v in captured["headers"].items():
        if k.lower() == "authorization":
            print(f"    {k}: {redacted_auth}")
        else:
            print(f"    {k}: {v}")
    print(f"  Body (JSON):")
    print(json.dumps(body_obj, ensure_ascii=False, indent=2))

    _hr("KEY OBSERVATIONS")
    checks = [
        ("enable_thinking at top level",
         body_obj.get("enable_thinking") is False),
        ("No extra_body wrapper",
         "extra_body" not in body_obj),
        ("response_format at top level",
         body_obj.get("response_format") == {"type": "json_object"}),
        ("model set",
         body_obj.get("model") == "qwen3.7-plus-2026-05-26"),
        ("temperature = 0.0",
         body_obj.get("temperature") == 0.0),
        ("seed = 1234",
         body_obj.get("seed") == 1234),
        ("messages list present",
         isinstance(body_obj.get("messages"), list)
         and len(body_obj["messages"]) >= 1),
        ("URL has /chat/completions",
         captured["url"].endswith("/chat/completions")),
        ("URL has no double /v1",
         captured["url"].count("/v1/") == 1),
    ]
    failed = 0
    for label, ok in checks:
        status = "OK " if ok else "FAIL"
        if not ok:
            failed += 1
        print(f"  [{status}] {label}")

    _hr("REASONING TOKENS INSTRUMENTATION")
    print("  LLMResponse.reasoning_tokens is read from")
    print("  usage.completion_tokens_details.reasoning_tokens in the")
    print("  provider response.  When the provider does NOT include")
    print("  this field, LLMResponse.reasoning_tokens is None.")
    print("  No estimation, no default.  Forwarded verbatim into the")
    print("  LiveCallLedger.")
    print()
    print("  The ledger record (per call) carries:")
    print("    request_enable_thinking     = what we sent")
    print("    request_response_format     = what we sent")
    print("    reasoning_tokens            = what the provider reported")
    print("    usage_type                  = PROVIDER_REPORTED | UNAVAILABLE")
    print("    input_tokens / output_tokens / total_tokens")

    if failed:
        print(f"\n  {failed} check(s) failed.")
        return 1
    print("\n  All checks pass.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
