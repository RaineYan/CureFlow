"""Day 14A NETWORK PRE-CHECK (read-only, no Qwen API call).

Goal: confirm the current Python environment can reach
the Qwen DashScope endpoint BEFORE running R4.

We do NOT make a real LLM API call.  We only verify:
  1. DNS resolution
  2. TCP connect
  3. TLS handshake
  4. HTTP OPTIONS / GET to a non-auth endpoint
     (e.g. /v1/models returns 401 without a key, but
      the 401 itself proves the endpoint is reachable)

API key is NEVER read or printed.
"""
from __future__ import annotations

import json
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _step_dns(host: str) -> dict:
    t0 = time.perf_counter()
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        addrs = sorted({i[4][0] for i in infos})
        return {
            "step": "dns",
            "host": host,
            "ok": True,
            "addresses": addrs,
            "elapsed_ms": (time.perf_counter() - t0) * 1000.0,
        }
    except Exception as e:
        return {
            "step": "dns",
            "host": host,
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "elapsed_ms": (time.perf_counter() - t0) * 1000.0,
        }


def _step_tcp(host: str, port: int = 443, timeout: float = 15.0) -> dict:
    t0 = time.perf_counter()
    try:
        with socket.create_connection(
            (host, port), timeout=timeout
        ) as s:
            s.settimeout(timeout)
        return {
            "step": "tcp",
            "host": host, "port": port,
            "ok": True,
            "elapsed_ms": (time.perf_counter() - t0) * 1000.0,
        }
    except Exception as e:
        return {
            "step": "tcp",
            "host": host, "port": port,
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "elapsed_ms": (time.perf_counter() - t0) * 1000.0,
        }


def _step_tls(
    host: str, port: int = 443, timeout: float = 15.0
) -> dict:
    t0 = time.perf_counter()
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection(
            (host, port), timeout=timeout
        ) as s:
            with ctx.wrap_socket(s, server_hostname=host) as ss:
                cert = ss.getpeercert()
                return {
                    "step": "tls",
                    "host": host, "port": port,
                    "ok": True,
                    "tls_version": ss.version(),
                    "subject": str(cert.get("subject", ""))[:200],
                    "issuer": str(cert.get("issuer", ""))[:200],
                    "elapsed_ms": (
                        time.perf_counter() - t0
                    ) * 1000.0,
                }
    except Exception as e:
        return {
            "step": "tls",
            "host": host, "port": port,
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "elapsed_ms": (time.perf_counter() - t0) * 1000.0,
        }


def _step_http(
    url: str, timeout: float = 15.0
) -> dict:
    """Send a no-auth GET.  We expect either:
      - 401 Unauthorized (key required, but endpoint is
        reachable), OR
      - 200 OK (no auth needed for this endpoint), OR
      - 405 Method Not Allowed (if GET not allowed)
    Any of these prove the endpoint is reachable.  A
    connection error means the endpoint is NOT reachable
    from this process.
    """
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", "Day14A-PreCheck/1.0")
        with urllib.request.urlopen(
            req, timeout=timeout
        ) as r:
            return {
                "step": "http_get",
                "url": url,
                "ok": True,
                "status": r.status,
                "elapsed_ms": (
                    time.perf_counter() - t0
                ) * 1000.0,
            }
    except urllib.error.HTTPError as e:
        # 401 / 403 / 405 are all "endpoint reachable"
        return {
            "step": "http_get",
            "url": url,
            "ok": True,
            "status": e.code,
            "note": (
                f"HTTP {e.code} received — endpoint IS "
                f"reachable.  (Auth required, but that's "
                f"expected without API key.)"
            ),
            "elapsed_ms": (
                time.perf_counter() - t0
            ) * 1000.0,
        }
    except Exception as e:
        return {
            "step": "http_get",
            "url": url,
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "elapsed_ms": (
                time.perf_counter() - t0
            ) * 1000.0,
        }


def main() -> int:
    print("=" * 64)
    print("Day 14A NETWORK PRE-CHECK (read-only, no Qwen API call)")
    print("=" * 64)
    print()

    # 1. Inspect config (NO key read/print)
    print("[1/3] Inspecting configuration (NO key read)...")
    from experiments.day14.provider_config import FROZEN_PROVIDER_CONFIG
    cfg = FROZEN_PROVIDER_CONFIG.to_dict()
    print(f"  provider          : {cfg.get('provider')}")
    print(f"  model             : {cfg.get('model')}")
    print(f"  enable_thinking   : {cfg.get('enable_thinking')}")
    print(f"  response_format   : {cfg.get('response_format')}")
    print(f"  temperature       : {cfg.get('temperature')}")
    print(f"  seed              : {cfg.get('seed')}")
    # Env vars
    base_url_env = os.environ.get("REAL_LLM_BASE_URL", "")
    api_key_present = bool(os.environ.get("REAL_LLM_API_KEY"))
    print(f"  REAL_LLM_BASE_URL : "
          f"{base_url_env if base_url_env else '(NOT SET)'}")
    print(f"  REAL_LLM_API_KEY  : "
          f"{'PRESENT (length=' + str(len(os.environ['REAL_LLM_API_KEY'])) + ')'
             if api_key_present else 'NOT SET'}")
    print()
    print("  (API key value NEVER read, NEVER printed.)")
    print()

    # 2. Resolve target host
    EXPECTED_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    if base_url_env.rstrip("/") == EXPECTED_BASE.rstrip("/"):
        print(f"  [OK] REAL_LLM_BASE_URL matches expected: "
              f"{EXPECTED_BASE}")
        target_host = "dashscope.aliyuncs.com"
    elif base_url_env:
        print(f"  [WARN] REAL_LLM_BASE_URL is set to {base_url_env!r}, "
              f"NOT {EXPECTED_BASE!r}")
        # Parse the host
        from urllib.parse import urlparse
        target_host = urlparse(base_url_env).hostname or "unknown"
    else:
        print(f"  [WARN] REAL_LLM_BASE_URL is NOT set.  "
              f"RealLLMClient will fall back to api.openai.com "
              f"which is unreachable from this network.  "
              f"Set REAL_LLM_BASE_URL={EXPECTED_BASE} to fix.")
        # We will still test the dashscope endpoint
        # because that's the intended target.
        target_host = "dashscope.aliyuncs.com"
    print()

    # 3. Run pre-check
    print(f"[2/3] Running pre-check against {target_host}:443")
    print()
    report = {
        "schema_version": 1,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "config": {
            "provider": cfg.get("provider"),
            "model": cfg.get("model"),
            "REAL_LLM_BASE_URL_env": (
                base_url_env if base_url_env else None
            ),
            "expected_base_url": EXPECTED_BASE,
            "REAL_LLM_API_KEY_present": api_key_present,
            # length only, not value
            "REAL_LLM_API_KEY_length": (
                len(os.environ["REAL_LLM_API_KEY"])
                if api_key_present else 0
            ),
        },
        "target_host": target_host,
        "steps": [],
    }

    # 3.1 DNS
    print(f"  step 1/4 DNS resolve {target_host} ...")
    dns = _step_dns(target_host)
    report["steps"].append(dns)
    print(f"    {'OK' if dns['ok'] else 'FAIL'}  "
          f"elapsed={dns['elapsed_ms']:.0f}ms")
    if dns["ok"]:
        for a in dns["addresses"][:4]:
            print(f"      address: {a}")
    else:
        print(f"      error: {dns['error']}")
    print()

    if not dns["ok"]:
        # Cannot proceed with TCP
        report["verdict"] = "BLOCKED_DNS"
        report["recommendation"] = (
            "DNS resolution failed.  Check internet, "
            "DNS server, or hosts file.  Do NOT run R4."
        )
        out = ROOT / "experiments/results/day14/network_precheck.json"
        out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"  Saved: {out}")
        print()
        print("VERDICT: BLOCKED_DNS — DO NOT RUN R4")
        return 2

    # 3.2 TCP
    print(f"  step 2/4 TCP connect {target_host}:443 ...")
    tcp = _step_tcp(target_host, 443, timeout=15.0)
    report["steps"].append(tcp)
    print(f"    {'OK' if tcp['ok'] else 'FAIL'}  "
          f"elapsed={tcp['elapsed_ms']:.0f}ms")
    if not tcp["ok"]:
        print(f"      error: {tcp['error']}")
    print()

    if not tcp["ok"]:
        report["verdict"] = "BLOCKED_TCP"
        report["recommendation"] = (
            "TCP connect to DashScope failed.  Likely "
            "firewall, proxy, or routing issue.  Check "
            "netsh winhttp show proxy, HTTP_PROXY / "
            "HTTPS_PROXY env vars, and corporate "
            "firewall rules.  Do NOT run R4."
        )
        out = ROOT / "experiments/results/day14/network_precheck.json"
        out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"  Saved: {out}")
        print()
        print("VERDICT: BLOCKED_TCP — DO NOT RUN R4")
        return 3

    # 3.3 TLS
    print(f"  step 3/4 TLS handshake {target_host}:443 ...")
    tls = _step_tls(target_host, 443, timeout=15.0)
    report["steps"].append(tls)
    print(f"    {'OK' if tls['ok'] else 'FAIL'}  "
          f"elapsed={tls['elapsed_ms']:.0f}ms")
    if tls["ok"]:
        print(f"      TLS version: {tls.get('tls_version')}")
        print(f"      Subject: {tls.get('subject')[:120]}")
    else:
        print(f"      error: {tls['error']}")
    print()

    if not tls["ok"]:
        report["verdict"] = "BLOCKED_TLS"
        report["recommendation"] = (
            "TLS handshake failed.  Possible: corporate "
            "MITM proxy, expired CA, or TLS version "
            "mismatch.  Check OpenSSL and CA certs."
        )
        out = ROOT / "experiments/results/day14/network_precheck.json"
        out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"  Saved: {out}")
        print()
        print("VERDICT: BLOCKED_TLS — DO NOT RUN R4")
        return 4

    # 3.4 HTTP GET (no key, expect 401)
    base_for_http = (
        base_url_env if base_url_env
        else EXPECTED_BASE
    )
    test_url = f"{base_for_http.rstrip('/')}/models"
    print(f"  step 4/4 HTTP GET {test_url} (no key, expect 401) ...")
    http = _step_http(test_url, timeout=15.0)
    report["steps"].append(http)
    print(f"    {'OK' if http['ok'] else 'FAIL'}  "
          f"status={http.get('status', '?')}  "
          f"elapsed={http['elapsed_ms']:.0f}ms")
    if not http["ok"]:
        print(f"      error: {http['error']}")
    print()

    # 4. Verdict
    if http["ok"] and http.get("status", 0) in (200, 401, 403, 405):
        report["verdict"] = "OK_ENDPOINT_REACHABLE"
        report["recommendation"] = (
            "Endpoint is reachable from this Python process.  "
            "If REAL_LLM_BASE_URL is correctly set to "
            f"{EXPECTED_BASE}, you may proceed with R4.  "
            "If it's not set, set it first."
        )
        verdict_code = 0
    else:
        report["verdict"] = "BLOCKED_HTTP"
        report["recommendation"] = (
            f"HTTP GET to {test_url} failed.  Endpoint "
            "may be down, blocking our user-agent, or "
            "firewalled at the application layer."
        )
        verdict_code = 5

    out = ROOT / "experiments/results/day14/network_precheck.json"
    out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[3/3] Saved: {out}")
    print()
    print("=" * 64)
    if report["verdict"] == "OK_ENDPOINT_REACHABLE":
        print(f"VERDICT: {report['verdict']}")
        print("Endpoint reachable.  Set REAL_LLM_BASE_URL="
              f"{EXPECTED_BASE}")
        print("and you may run R4 (after providing API key).")
    else:
        print(f"VERDICT: {report['verdict']}")
        print(f"  -> {report['recommendation']}")
        print("  DO NOT RUN R4.  Fix the network issue first.")
    print("=" * 64)
    return verdict_code


if __name__ == "__main__":
    sys.exit(main())
