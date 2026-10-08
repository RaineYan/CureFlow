"""Day 14A NETWORK PRE-CHECK (extended) — compare the
intended Qwen endpoint against the OpenAI fallback that
the unconfigured RealLLMClient uses.

Confirms:
  - dashscope.aliyuncs.com:443 is fully reachable
  - api.openai.com:443 is NOT reachable from this network
    (the reason R3 + 5 trial failed with WinError 10060)

NO API key read, NO Qwen chat API call, NO file overwrite.
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
            "host": host, "step": "dns", "ok": True,
            "addresses": addrs,
            "elapsed_ms": (time.perf_counter() - t0) * 1000.0,
        }
    except Exception as e:
        return {
            "host": host, "step": "dns", "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "elapsed_ms": (time.perf_counter() - t0) * 1000.0,
        }


def _step_tcp(host: str, timeout: float = 8.0) -> dict:
    t0 = time.perf_counter()
    try:
        with socket.create_connection(
            (host, 443), timeout=timeout
        ) as s:
            s.settimeout(timeout)
        return {
            "host": host, "step": "tcp", "ok": True,
            "elapsed_ms": (time.perf_counter() - t0) * 1000.0,
        }
    except Exception as e:
        return {
            "host": host, "step": "tcp", "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "elapsed_ms": (time.perf_counter() - t0) * 1000.0,
        }


def _step_tls(host: str, timeout: float = 8.0) -> dict:
    t0 = time.perf_counter()
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection(
            (host, 443), timeout=timeout
        ) as s:
            with ctx.wrap_socket(s, server_hostname=host) as ss:
                cert = ss.getpeercert()
                return {
                    "host": host, "step": "tls", "ok": True,
                    "tls_version": ss.version(),
                    "subject": str(cert.get("subject", ""))[:200],
                    "elapsed_ms": (
                        time.perf_counter() - t0
                    ) * 1000.0,
                }
    except Exception as e:
        return {
            "host": host, "step": "tls", "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "elapsed_ms": (time.perf_counter() - t0) * 1000.0,
        }


def _step_http(url: str, timeout: float = 8.0) -> dict:
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", "Day14A-PreCheck/1.0")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {
                "url": url, "step": "http_get", "ok": True,
                "status": r.status,
                "elapsed_ms": (
                    time.perf_counter() - t0
                ) * 1000.0,
            }
    except urllib.error.HTTPError as e:
        return {
            "url": url, "step": "http_get", "ok": True,
            "status": e.code,
            "elapsed_ms": (
                time.perf_counter() - t0
            ) * 1000.0,
        }
    except Exception as e:
        return {
            "url": url, "step": "http_get", "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "elapsed_ms": (
                time.perf_counter() - t0
            ) * 1000.0,
        }


def _test_host(host: str) -> dict:
    """Run all 4 steps against a host.  Stop early on first FAIL."""
    out = {"host": host, "steps": []}
    dns = _step_dns(host)
    out["steps"].append(dns)
    if not dns["ok"]:
        out["verdict"] = "BLOCKED_DNS"
        return out
    tcp = _step_tcp(host)
    out["steps"].append(tcp)
    if not tcp["ok"]:
        out["verdict"] = "BLOCKED_TCP"
        return out
    tls = _step_tls(host)
    out["steps"].append(tls)
    if not tls["ok"]:
        out["verdict"] = "BLOCKED_TLS"
        return out
    # HTTP path
    http = _step_http(
        f"https://{host}/compatible-mode/v1/models"
        if host == "dashscope.aliyuncs.com"
        else f"https://{host}/v1/models",
        timeout=8.0,
    )
    out["steps"].append(http)
    if not http["ok"]:
        out["verdict"] = "BLOCKED_HTTP"
    else:
        out["verdict"] = "OK"
    return out


def main() -> int:
    print("=" * 64)
    print("Day 14A NETWORK PRE-CHECK (extended comparison)")
    print("=" * 64)
    print()

    # 1. Configuration
    print("[1/3] Configuration snapshot (NO key read) ...")
    from experiments.day14.provider_config import FROZEN_PROVIDER_CONFIG
    cfg = FROZEN_PROVIDER_CONFIG.to_dict()
    print(f"  provider        : {cfg.get('provider')}")
    print(f"  model           : {cfg.get('model')}")
    print(f"  enable_thinking : {cfg.get('enable_thinking')}")
    print(f"  response_format : {cfg.get('response_format')}")
    print(f"  temperature     : {cfg.get('temperature')}")
    print(f"  seed            : {cfg.get('seed')}")
    base_url = os.environ.get("REAL_LLM_BASE_URL", "")
    api_key_present = bool(os.environ.get("REAL_LLM_API_KEY"))
    print(f"  REAL_LLM_BASE_URL : "
          f"{base_url if base_url else '(NOT SET)'}")
    print(f"  REAL_LLM_API_KEY  : "
          f"{'PRESENT (length=' + str(len(os.environ['REAL_LLM_API_KEY'])) + ')'
             if api_key_present else 'NOT SET'}")
    print("  (API key value NEVER read, NEVER printed.)")
    print()

    # 2. Compare two hosts
    targets = [
        ("dashscope.aliyuncs.com", "intended Qwen DashScope endpoint"),
        ("api.openai.com", "OpenAI fallback (current default)"),
    ]
    report = {
        "schema_version": 1,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "config": {
            "provider": cfg.get("provider"),
            "model": cfg.get("model"),
            "REAL_LLM_BASE_URL_env": (
                base_url if base_url else None
            ),
            "REAL_LLM_API_KEY_present": api_key_present,
            "REAL_LLM_API_KEY_length": (
                len(os.environ["REAL_LLM_API_KEY"])
                if api_key_present else 0
            ),
        },
        "targets": [],
    }
    for host, desc in targets:
        print(f"[2/3] Testing {host} ({desc})")
        r = _test_host(host)
        report["targets"].append(r)
        for s in r["steps"]:
            status = (
                f"OK   status={s.get('status', '-')}  "
                f"elapsed={s['elapsed_ms']:.0f}ms"
                if s["ok"]
                else f"FAIL {s.get('error', '?')}  "
                     f"elapsed={s['elapsed_ms']:.0f}ms"
            )
            print(f"  {s['step']:8s}  {status}")
        print(f"  VERDICT: {r['verdict']}")
        print()

    # 3. Overall
    dashscope_ok = report["targets"][0]["verdict"] == "OK"
    openai_ok = report["targets"][1]["verdict"] == "OK"
    if dashscope_ok and not openai_ok:
        report["overall_verdict"] = (
            "DASHSCOPE_REACHABLE_OPENAI_BLOCKED"
        )
        report["recommendation"] = (
            "The intended Qwen DashScope endpoint is fully "
            "reachable (DNS+TCP+TLS+HTTP all OK).  The "
            "OpenAI fallback used by the unconfigured "
            "RealLLMClient is BLOCKED.  This confirms the "
            "root cause of the R3 + 5-trial NETWORK_ERROR: "
            "REAL_LLM_BASE_URL was NOT set, so requests went "
            "to api.openai.com which times out from this "
            "network.  Set REAL_LLM_BASE_URL=https://"
            "dashscope.aliyuncs.com/compatible-mode/v1 "
            "and you may run R4 (with valid API key)."
        )
    elif dashscope_ok and openai_ok:
        report["overall_verdict"] = "BOTH_REACHABLE"
        report["recommendation"] = (
            "Both endpoints reachable.  Set "
            "REAL_LLM_BASE_URL=dashscope to use Qwen."
        )
    elif not dashscope_ok:
        report["overall_verdict"] = "DASHSCOPE_BLOCKED"
        report["recommendation"] = (
            "Qwen DashScope NOT reachable.  Investigate DNS, "
            "firewall, or proxy.  Do NOT run R4."
        )
    else:
        report["overall_verdict"] = "UNKNOWN"
        report["recommendation"] = "See per-host details."

    out = ROOT / "experiments/results/day14/network_precheck_compare.json"
    out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[3/3] Saved: {out}")
    print()
    print("=" * 64)
    print(f"OVERALL VERDICT: {report['overall_verdict']}")
    print("=" * 64)
    print(report["recommendation"])
    print("=" * 64)
    return 0 if report["overall_verdict"] in (
        "DASHSCOPE_REACHABLE_OPENAI_BLOCKED",
        "BOTH_REACHABLE",
    ) else 1


if __name__ == "__main__":
    sys.exit(main())
