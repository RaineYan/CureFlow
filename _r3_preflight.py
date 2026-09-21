"""R3 + 5-trial pre-flight check.  No Qwen calls.  Just
verify R3 metadata auto-derivation, trial_id allocation,
and estimate API cost.
"""
from __future__ import annotations

import os
import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from experiments.day14.recording import (
    derive_replacement_reason,
)
from experiments.day14.pilot_runner import (
    is_replacement_id_occupied,
    next_replacement_id,
    root_trial_id_of,
)


def main() -> int:
    print("=" * 64)
    print("R3 + 5-TRIAL PRE-FLIGHT CHECK")
    print("=" * 64)
    print()

    # 1. API key status
    key_set = bool(os.environ.get("REAL_LLM_API_KEY"))
    print(f"1. REAL_LLM_API_KEY env var: "
          f"{'PRESENT' if key_set else 'MISSING'}")
    if not key_set:
        print("   -> R3 CANNOT run user-side OR agent-side without a key.")
        print("   -> Cost-estimate below; you decide whether to provide key.")
    print()

    # 2. R2 audit state
    pilot = ROOT / "experiments/results/day14/pilot"
    r2_audit = pilot / "case_a_full_rerun_trial01R2_failure_audit.json"
    r2_audit_data = json.loads(r2_audit.read_text(encoding="utf-8"))
    print(f"2. R2 audit: failure_class={r2_audit_data['failure_class']}, "
          f"failure_subtype={r2_audit_data['failure_subtype']}")
    print(f"   R2 historical replacement_reason: "
          f"{r2_audit_data['replacement_reason']}")
    print()

    # 3. R3 metadata auto-derivation
    r3_reason = derive_replacement_reason(
        previous_failure_class=r2_audit_data["failure_class"],
        previous_failure_subtype=r2_audit_data["failure_subtype"],
    )
    print(f"3. R3 replacement_reason (auto-derived): {r3_reason}")
    assert r3_reason == "AUTH_CREDENTIAL_CORRECTION", \
        f"expected AUTH_CREDENTIAL_CORRECTION, got {r3_reason}"
    print("   [OK] R3 reason is AUTH_CREDENTIAL_CORRECTION")
    print()

    # 4. R3 trial_id allocation
    # next_replacement_id takes the CHAIN ROOT (trial01), then
    # finds smallest free R<n> in the chain.  R1 + R2 are
    # occupied, so R3 is the next.
    r3_id = next_replacement_id(
        pilot, "case_a_full_rerun_trial01"
    )
    print(f"4. R3 trial_id: {r3_id}")
    print(f"   root_trial_id: {root_trial_id_of(r3_id)}")
    print(f"   is_occupied: {is_replacement_id_occupied(pilot, r3_id)}")
    print()

    # 5. Remaining 5 trial status
    print("5. Remaining 5 trials (case_a/b × COARSE/SELECTIVE + case_b FULL):")
    remaining = [
        ("case_a_coarse_checkpoint_trial01", "COARSE_CHECKPOINT"),
        ("case_a_selective_recovery_trial01", "SELECTIVE_RECOVERY"),
        ("case_b_full_rerun_trial01", "FULL_RERUN"),
        ("case_b_coarse_checkpoint_trial01", "COARSE_CHECKPOINT"),
        ("case_b_selective_recovery_trial01", "SELECTIVE_RECOVERY"),
    ]
    for tid, strat in remaining:
        p = pilot / f"{tid}.json"
        print(f"   - {tid:50s} ({strat:20s}) "
              f"{'EXISTS (offline)' if p.exists() else 'NOT YET'}")
    print()

    # 6. API cost estimate
    print("=" * 64)
    print("API COST ESTIMATE (Qwen qwen3.7-plus-2026-05-26)")
    print("=" * 64)
    print("Per Day 13.6 real data:")
    print("  - 4 real calls, total 2120 tokens")
    print("  - Per call avg: 530 input + 75 output = 605 tokens")
    print()
    print("Qwen qwen3.7-plus dashscope pricing (typical):")
    print("  - input:  ~0.004 CNY / 1k tokens")
    print("  - output: ~0.012 CNY / 1k tokens")
    print()
    print("Per call cost: 530*0.004/1000 + 75*0.012/1000")
    print("              = 0.00212 + 0.00090 = 0.00302 CNY/call")
    print()

    # Cost per trial
    n_calls_per_trial = {
        "FULL_RERUN": 6,
        "COARSE_CHECKPOINT": 6,
        "SELECTIVE_RECOVERY": 2,
    }
    total_calls = (
        n_calls_per_trial["FULL_RERUN"]  # R3 (case_a FULL)
        + n_calls_per_trial["COARSE_CHECKPOINT"] * 2  # case_a/b
        + n_calls_per_trial["SELECTIVE_RECOVERY"] * 2  # case_a/b
        + n_calls_per_trial["FULL_RERUN"]  # case_b FULL
    )
    print(f"R3 (case_a FULL)                : 6 calls")
    print(f"5 remaining trial (full set)     : 6+2+6+6+2 = 22 calls")
    print(f"Total (R3 + 5 remaining)         : 6 + 22 = {total_calls} calls")
    print()
    total_cost = total_calls * 0.00302
    print(f"Total cost estimate: {total_calls} * 0.00302 = "
          f"{total_cost:.3f} CNY")
    print()
    print("R3 alone:        6 calls  * 0.00302 = 0.018 CNY")
    print("5 trial alone:  22 calls * 0.00302 = 0.066 CNY")
    print()
    print("=" * 64)
    print("DECISION REQUIRED")
    print("=" * 64)
    print("API cost is TRIVIAL (<0.10 CNY per batch).")
    print("But the key requirement is: you MUST set $env:REAL_LLM_API_KEY")
    print("in your PowerShell session (R2's key was rejected with HTTP 401).")
    print()
    print("I CANNOT execute R3 / 5-trial from agent side because the API")
    print("key is never in agent state.  These trials MUST run user-side")
    print("(same pattern as R2).")
    print()
    print("Options:")
    print("  (a) You set $env:REAL_LLM_API_KEY, then I issue the R3 command")
    print("      and watch the result.")
    print("  (b) You set key + run R3 yourself, paste the result back, I")
    print("      analyze and continue with remaining 5 trials.")
    print("  (c) We pause experiments here, finalize plan/PPT/Demo with")
    print("      pre-pilot reduction data (66.7% reduction) until you have")
    print("      a fresh API key.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
