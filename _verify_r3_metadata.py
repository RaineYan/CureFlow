"""Day 14A PRE-R3 — Verify R3 metadata auto-derivation
from R2's failure_audit.json.  Pure offline verification,
0 provider calls.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from experiments.day14.recording import (
    derive_5_layer_provider_state,
    derive_replacement_reason,
)


R2_AUDIT = ROOT / "experiments/results/day14/pilot/case_a_full_rerun_trial01R2_failure_audit.json"


def main() -> int:
    # 1. Read R2 failure audit
    r2_audit = json.loads(R2_AUDIT.read_text(encoding="utf-8"))
    print("=" * 64)
    print("R2 FAILURE AUDIT — 5-LAYER STATE VERIFICATION")
    print("=" * 64)
    print(f"  trial_id              : {r2_audit['trial_id']}")
    print(f"  failure_class         : {r2_audit['failure_class']}")
    print(f"  failure_subtype       : {r2_audit['failure_subtype']}")
    print()
    print("  5-LAYER PROVIDER STATE:")
    for k in (
        "real_http_roundtrip_verified",
        "provider_identity_verified",
        "provider_authenticated",
        "provider_request_succeeded",
        "provider_verified",
    ):
        print(f"    {k:32s} = {r2_audit[k]}")
    print()
    print("  COUNTERS:")
    for k in (
        "actual_provider_http_requests",
        "successful_provider_requests",
        "successful_artifact_materializations",
    ):
        print(f"    {k:32s} = {r2_audit[k]}")
    print()

    # 2. Verify 5-layer values are correct for HTTP 401
    expected = {
        "real_http_roundtrip_verified": True,  # HTTP did reach
        "provider_identity_verified": True,    # qwen+qwen3.7-plus
        "provider_authenticated": False,        # key rejected
        "provider_request_succeeded": False,    # no chatcmpl- id
        "provider_verified": False,             # legacy alias
    }
    print("=" * 64)
    print("5-LAYER STATE ASSERTIONS (must all match)")
    print("=" * 64)
    ok = True
    for k, v in expected.items():
        actual = r2_audit[k]
        status = "OK" if actual == v else "FAIL"
        if actual != v:
            ok = False
        print(f"  [{status}] {k:32s} = {actual}  (expected {v})")
    print()

    # 3. Verify R3 auto-derivation
    print("=" * 64)
    print("R3 METADATA AUTO-DERIVATION")
    print("=" * 64)
    r3_reason = derive_replacement_reason(
        previous_failure_class=r2_audit["failure_class"],
        previous_failure_subtype=r2_audit["failure_subtype"],
    )
    print(f"  R2 failure_class      = {r2_audit['failure_class']}")
    print(f"  R2 failure_subtype    = {r2_audit['failure_subtype']}")
    print(f"  R3 replacement_reason = {r3_reason}")
    if r3_reason != "AUTH_CREDENTIAL_CORRECTION":
        print(f"  [FAIL] expected AUTH_CREDENTIAL_CORRECTION")
        return 1
    print("  [OK] R3 reason is AUTH_CREDENTIAL_CORRECTION")
    print()

    # 4. Print R3 expected metadata
    print("=" * 64)
    print("R3 EXPECTED TRIAL JSON METADATA (after user runs R3)")
    print("=" * 64)
    print("  trial_id              : case_a_full_rerun_trial01R3")
    print("  replaces_trial_id     : case_a_full_rerun_trial01R2")
    print("  root_trial_id         : case_a_full_rerun_trial01")
    print("  previous_failure_class: INFRA_FAILURE")
    print("  previous_failure_subtype: HTTP_401_AUTH_ERROR")
    print("  replacement_reason    : AUTH_CREDENTIAL_CORRECTION")
    print()

    # 5. Print R3 PowerShell command
    print("=" * 64)
    print("R3 POWERSHELL COMMAND (user-side)")
    print("=" * 64)
    print('  $env:REAL_LLM_API_KEY = "<correct Qwen key>"')
    print('  cd C:\\Users\\Lenovo\\.minimax-agent-cn\\projects\\poc')
    print('  python day14_pilot_run.py '
          '--case case_a --strategy FULL_RERUN '
          '--replacement-of case_a_full_rerun_trial01R2')
    print()
    print("Expected outputs:")
    print("  - case_a_full_rerun_trial01R3.json")
    print("  - case_a_full_rerun_trial01R3_live_ledger.jsonl")
    print("  - case_a_full_rerun_trial01R3_failure_audit.json (only on failure)")
    print()

    if ok:
        print("=" * 64)
        print("ALL CHECKS PASS — R3 is ready to run user-side.")
        print("=" * 64)
        return 0
    else:
        print("=" * 64)
        print("5-LAYER STATE MISMATCH — DO NOT RUN R3 until fixed.")
        print("=" * 64)
        return 1


if __name__ == "__main__":
    sys.exit(main())
