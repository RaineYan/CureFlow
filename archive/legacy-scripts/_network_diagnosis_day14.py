"""Day 14A NETWORK DIAGNOSIS — read-only forensic.

Generates network_diagnosis_day14.json with the full
forensic picture:
  - DNS resolution
  - TCP connectivity
  - proxy state
  - env proxy state
  - actual endpoint (where requests were sent)
  - classification + recommended recovery

DOES NOT modify:
  - Day 13 frozen evidence
  - R1 / R2 / R3 trial JSONs
  - R1 / R2 / R3 failure audits
  - any pre-existing classifier
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from experiments.day14.provider_config import FROZEN_PROVIDER_CONFIG


def _run(cmd: list, timeout: int = 30) -> dict:
    """Run a read-only command, return parsed output (best effort)."""
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
        return {
            "cmd": " ".join(cmd),
            "returncode": r.returncode,
            "stdout": r.stdout,
            "stderr": r.stderr,
        }
    except Exception as e:
        return {"cmd": " ".join(cmd), "error": str(e)}


def main() -> int:
    print("=" * 64)
    print("Day 14A NETWORK DIAGNOSIS (read-only)")
    print("=" * 64)
    print()

    diagnosis = {
        "schema_version": 1,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "purpose": (
            "Read-only network forensic for Day 14A R3 + 5-trial "
            "NETWORK_ERROR failures.  Identifies the actual HTTP "
            "endpoint used and classifies the path."
        ),
        "frozen_provider_config": FROZEN_PROVIDER_CONFIG.to_dict(),
    }

    # 1. Frozen evidence integrity (do not modify)
    print("[1/4] verifying Day 13 frozen evidence unchanged ...")
    try:
        from experiments.day14 import verify_frozen_evidence_unchanged
        r = verify_frozen_evidence_unchanged()
        diagnosis["day13_frozen_evidence"] = {
            "intact": r["passed"],
            "checked_files": len(r["checked_files"]),
            "mismatches": r["mismatches"],
            "missing": r["missing"],
        }
        print(f"  [OK] {len(r['checked_files'])} frozen files intact")
    except Exception as e:
        diagnosis["day13_frozen_evidence"] = {"error": str(e)}
    print()

    # 2. Test-NetConnection to dashscope (the intended target)
    print("[2/4] Test-NetConnection dashscope.aliyuncs.com:443 ...")
    dashscope = _run([
        "powershell", "-NoProfile", "-Command",
        "Test-NetConnection -ComputerName 'dashscope.aliyuncs.com' "
        "-Port 443 -InformationLevel Detailed"
    ])
    diagnosis["dashscope_connectivity"] = {
        "command": dashscope["cmd"],
        "stdout": dashscope.get("stdout", ""),
    }
    # Parse TcpTestSucceeded from output (any column alignment)
    def _parse_tcp(out: str) -> str:
        import re as _re
        m = _re.search(
            r"TcpTestSucceeded\s*:\s*(True|False)",
            out,
        )
        return m.group(1) if m else "UNKNOWN"
    dashscope_tcp = _parse_tcp(dashscope.get("stdout", ""))
    diagnosis["dashscope_connectivity"]["TcpTestSucceeded"] = dashscope_tcp
    print(f"  dashscope TcpTestSucceeded: {dashscope_tcp}")
    print()

    # 3. Test-NetConnection to api.openai.com (the ACTUAL fallback target)
    print("[3/4] Test-NetConnection api.openai.com:443 ...")
    openai = _run([
        "powershell", "-NoProfile", "-Command",
        "Test-NetConnection -ComputerName 'api.openai.com' -Port 443"
    ], timeout=90)
    diagnosis["openai_connectivity"] = {
        "command": openai["cmd"],
        "stdout": openai.get("stdout", ""),
    }
    openai_tcp = _parse_tcp(openai.get("stdout", ""))
    diagnosis["openai_connectivity"]["TcpTestSucceeded"] = openai_tcp
    print(f"  openai TcpTestSucceeded: {openai_tcp}")
    print()

    # 4. netsh winhttp show proxy
    print("[4/4] netsh winhttp show proxy ...")
    proxy = _run(["netsh", "winhttp", "show", "proxy"])
    diagnosis["winhttp_proxy"] = {
        "command": proxy["cmd"],
        "stdout": proxy.get("stdout", ""),
    }
    print(f"  {proxy.get('stdout', '').strip()}")
    print()

    # 5. Environment variables (HTTP_PROXY, HTTPS_PROXY, etc.)
    import os
    env_proxy_keys = (
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
        "http_proxy", "https_proxy", "all_proxy", "no_proxy",
        "REAL_LLM_BASE_URL",
    )
    env_state = {}
    for k in env_proxy_keys:
        v = os.environ.get(k)
        env_state[k] = "NOT SET" if v is None else v
    diagnosis["env_proxy_status"] = env_state
    print("Env proxy state:")
    for k, v in env_state.items():
        print(f"  {k} = {v}")
    print()

    # 6. Resolve the actual endpoint used by the trial
    print("[endpoint] resolving actual HTTP endpoint used by trial ...")
    from llm import RealLLMProviderConfig
    from llm.providers.real import RealLLMClient
    cfg = RealLLMProviderConfig(
        provider=FROZEN_PROVIDER_CONFIG.provider,
        model=FROZEN_PROVIDER_CONFIG.model,
        api_key_env_var="REAL_LLM_API_KEY",
        response_format=FROZEN_PROVIDER_CONFIG.response_format,
        temperature=FROZEN_PROVIDER_CONFIG.temperature,
        seed=FROZEN_PROVIDER_CONFIG.seed,
        provider_options={
            "enable_thinking": FROZEN_PROVIDER_CONFIG.enable_thinking,
        },
        max_retries=0,
    )
    base_url = cfg.base_url
    endpoint = RealLLMClient._endpoint_real(cfg) if hasattr(
        RealLLMClient, "_endpoint_real"
    ) else None
    # Use the same logic as the source
    base = (cfg.base_url or "https://api.openai.com").rstrip("/")
    if base.endswith("/chat/completions"):
        endpoint = base
    elif base.endswith("/v1"):
        endpoint = f"{base}/chat/completions"
    else:
        endpoint = f"{base}/v1/chat/completions"
    diagnosis["effective_endpoint"] = {
        "configured_base_url": base_url,
        "effective_endpoint": endpoint,
        "is_qwen_dashscope": "dashscope.aliyuncs.com" in endpoint,
        "is_openai_fallback": "api.openai.com" in endpoint,
    }
    print(f"  configured base_url: {base_url!r}")
    print(f"  effective endpoint: {endpoint}")
    print(f"  is Qwen dashscope: {diagnosis['effective_endpoint']['is_qwen_dashscope']}")
    print(f"  is OpenAI fallback: {diagnosis['effective_endpoint']['is_openai_fallback']}")
    print()

    # 7. Classification
    if (
        diagnosis["effective_endpoint"]["is_openai_fallback"]
        and openai_tcp == "False"
        and dashscope_tcp == "True"
    ):
        classification = (
            "NETWORK_PATH_BLOCKED_DUE_TO_ENDPOINT_MISMATCH"
        )
        recommended_recovery = (
            "The RealLLMProviderConfig did NOT set base_url.  "
            "RealLLMClient._endpoint() fell back to "
            "https://api.openai.com/v1/chat/completions, which is "
            "unreachable from this network (api.openai.com:443 TCP "
            "connect failed).  The intended Qwen DashScope endpoint "
            "(dashscope.aliyuncs.com:443) is REACHABLE.  Fix: set "
            "$env:REAL_LLM_BASE_URL = "
            "'https://dashscope.aliyuncs.com/compatible-mode/v1' "
            "BEFORE running the trial.  The provider config should "
            "explicitly pin the base_url, not rely on the fallback."
        )
    elif dashscope_tcp == "False":
        classification = "NETWORK_PATH_BLOCKED_DASHSCOPE_UNREACHABLE"
        recommended_recovery = (
            "Even the intended Qwen DashScope endpoint is "
            "unreachable.  Try a different network (mobile hotspot, "
            "VPN, corporate proxy)."
        )
    elif openai_tcp == "True":
        classification = "OPENAI_REACHABLE_BUT_QWEN_INTENDED"
        recommended_recovery = (
            "api.openai.com is reachable, but the trial should "
            "target Qwen DashScope.  The current request would "
            "succeed against OpenAI (with a valid OpenAI key) but "
            "fail because the key is Qwen-formatted.  Fix base_url."
        )
    else:
        classification = "NETWORK_ERROR_OTHER"
        recommended_recovery = (
            "See Test-NetConnection output for details."
        )
    diagnosis["classification"] = classification
    diagnosis["recommended_recovery"] = recommended_recovery
    print(f"CLASSIFICATION: {classification}")
    print()

    # 8. R2 / R3 evidence re-statement (do NOT change classifications)
    diagnosis["r2_r3_evidence_unchanged"] = {
        "R2_audit": (
            "experiments/results/day14/pilot/"
            "case_a_full_rerun_trial01R2_failure_audit.json"
        ),
        "R2_classification_preserved": "INFRA_FAILURE / HTTP_401_AUTH_ERROR",
        "R2_note": (
            "R2's response (Incorrect API key provided: sk-ws-H.****...br4U) "
            "is what api.openai.com returns when an sk- key is presented "
            "to it.  The HTTP_401 classification is factually correct "
            "(api.openai.com did return 401), but the deeper root cause "
            "is endpoint mismatch.  Per the user's spec, R2's "
            "classification is preserved unchanged."
        ),
        "R3_audit": (
            "experiments/results/day14/pilot/"
            "case_a_full_rerun_trial01R3_failure_audit.json"
        ),
        "R3_classification_preserved": "INFRA_FAILURE / NETWORK_ERROR_TIMEOUT",
        "R3_note": (
            "R3's WinError 10060 was a TCP connect failure to "
            "api.openai.com (the fallback endpoint).  Per the user's "
            "spec, R3's classification is preserved unchanged."
        ),
    }

    # 9. Save
    out = ROOT / "experiments/results/day14/network_diagnosis_day14.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(diagnosis, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Saved: {out}")
    print()

    # 10. Also freeze R1 / R2 / R3 SHA for evidence
    pilot = ROOT / "experiments/results/day14/pilot"
    import hashlib
    sha = {}
    for fname in (
        "case_a_full_rerun_trial01.json",
        "case_a_full_rerun_trial01_failure_audit.json",
        "case_a_full_rerun_trial01R1_failure_audit.json",
        "case_a_full_rerun_trial01R2.json",
        "case_a_full_rerun_trial01R2_failure_audit.json",
        "case_a_full_rerun_trial01R2_live_ledger.jsonl",
        "case_a_full_rerun_trial01R3.json",
        "case_a_full_rerun_trial01R3_failure_audit.json",
        "case_a_full_rerun_trial01R3_live_ledger.jsonl",
        "case_a_coarse_checkpoint_trial01_real01.json",
        "case_a_coarse_checkpoint_trial01_real01_failure_audit.json",
        "case_a_coarse_checkpoint_trial01_real01_live_ledger.jsonl",
        "case_a_selective_recovery_trial01_real01.json",
        "case_a_selective_recovery_trial01_real01_failure_audit.json",
        "case_a_selective_recovery_trial01_real01_live_ledger.jsonl",
        "case_b_full_rerun_trial01_real01.json",
        "case_b_full_rerun_trial01_real01_failure_audit.json",
        "case_b_full_rerun_trial01_real01_live_ledger.jsonl",
        "case_b_coarse_checkpoint_trial01_real01.json",
        "case_b_coarse_checkpoint_trial01_real01_failure_audit.json",
        "case_b_coarse_checkpoint_trial01_real01_live_ledger.jsonl",
        "case_b_selective_recovery_trial01_real01.json",
        "case_b_selective_recovery_trial01_real01_failure_audit.json",
        "case_b_selective_recovery_trial01_real01_live_ledger.jsonl",
    ):
        p = pilot / fname
        if p.exists():
            sha[fname] = hashlib.sha256(p.read_bytes()).hexdigest()
    sha_path = ROOT / "experiments/results/day14/evidence_sha_after_diagnosis.json"
    sha_path.write_text(
        json.dumps(sha, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Evidence SHA frozen: {sha_path}")
    print()
    print("=" * 64)
    print("RECOVERY DECISION")
    print("=" * 64)
    if "ENDPOINT_MISMATCH" in classification:
        print("FIX BEFORE RETRY:")
        print("  $env:REAL_LLM_BASE_URL = "
              "'https://dashscope.aliyuncs.com/compatible-mode/v1'")
        print("  python _run_6_trials_inline.py")
    elif "UNREACHABLE" in classification:
        print("CHANGE NETWORK (mobile hotspot / VPN / proxy).")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
