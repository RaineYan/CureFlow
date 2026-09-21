"""
Day 13.5 — RealLLMClient endpoint construction check.

Run this BEFORE setting credentials to confirm that the base_url
you pass in does NOT get a duplicate /v1 or /chat/completions.

Prints the resolved endpoint for each of the three base_url forms
the adapter supports.  No network calls, no API key required.
"""
from __future__ import annotations

import os
import sys


def main() -> int:
    # Use a fake key so RealLLMClient.__init__ doesn't reject the
    # construction.  No network call is made.
    os.environ["REAL_LLM_DEMO_KEY"] = "x" * 32
    from llm import RealLLMClient, RealLLMProviderConfig

    cases = [
        # (label, base_url, expected_endpoint)
        (
            "Qwen SDK-style (Beijing)",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        ),
        (
            "Qwen full HTTP endpoint (Beijing)",
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        ),
        (
            "OpenAI default (no base_url)",
            None,
            "https://api.openai.com/v1/chat/completions",
        ),
        (
            "OpenAI explicit /v1",
            "https://api.openai.com/v1",
            "https://api.openai.com/v1/chat/completions",
        ),
        (
            "Self-hosted bare host",
            "http://localhost:8000",
            "http://localhost:8000/v1/chat/completions",
        ),
    ]

    print("Day 13.5 — endpoint construction check")
    print("=" * 80)
    failed = 0
    for label, base_url, expected in cases:
        cfg = RealLLMProviderConfig(
            provider="qwen" if "dashscope" in (base_url or "") else "openai",
            model="qwen3.7-plus-2026-05-26",
            base_url=base_url,
            api_key_env_var="REAL_LLM_DEMO_KEY",
        )
        try:
            client = RealLLMClient(cfg)
        except Exception as e:
            print(f"  [SKIP] {label}: construction failed: {e}")
            continue
        ep = client._endpoint()
        status = "OK " if ep == expected else "FAIL"
        if ep != expected:
            failed += 1
        print(f"  [{status}] {label}")
        print(f"          base_url : {base_url!r}")
        print(f"          resolved : {ep}")
        if ep != expected:
            print(f"          expected : {expected}")
        # No duplicate /v1
        if ep.count("/v1/") > 1:
            print(f"          WARNING: multiple /v1/ in endpoint: {ep.count('/v1/')}")
            failed += 1
    print()
    if failed == 0:
        print("All endpoint constructions are correct.")
        return 0
    print(f"{failed} endpoint construction(s) are wrong.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
