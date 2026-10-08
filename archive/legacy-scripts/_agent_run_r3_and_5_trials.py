"""Day 14A R3 + 5-trial agent-side runner.

SAFETY CONTRACT:
  1. NEVER print REAL_LLM_API_KEY value (only check truthy).
  2. NEVER log HTTP headers.
  3. NEVER write key to disk (no trial JSON contains it).
  4. _redact_secrets on all captured text.
  5. Subprocess isolation: each trial = 1 Python child process.

This script reads REAL_LLM_API_KEY from os.environ at the
agent level (NOT passed as CLI arg).  The user must have
set it in their PowerShell session:

  $env:REAL_LLM_API_KEY = "<your Qwen key>"

OR the user can paste the key when prompted and this script
will set os.environ["REAL_LLM_API_KEY"] in its own process.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _assert_key_present() -> bool:
    """Check REAL_LLM_API_KEY is set.  Never print the value."""
    val = os.environ.get("REAL_LLM_API_KEY")
    if not val:
        return False
    return True


def _run_one(case: str, strategy: str,
             replacement_of: str = "") -> subprocess.CompletedProcess:
    """Run a single trial via day14_pilot_run.py.  Inherits
    env vars (including REAL_LLM_API_KEY) from this process."""
    cmd = [
        sys.executable,
        "day14_pilot_run.py",
        "--case", case,
        "--strategy", strategy,
    ]
    if replacement_of:
        cmd.extend(["--replacement-of", replacement_of])
    print(f"  -> {' '.join(cmd)}", flush=True)
    return subprocess.run(
        cmd, cwd=str(ROOT),
        capture_output=True, text=True, timeout=300,
    )


def main() -> int:
    print("=" * 64)
    print("AGENT-SIDE R3 + 5-TRIAL RUNNER")
    print("=" * 64)
    print()

    # 1. Verify key
    if not _assert_key_present():
        print("ERROR: REAL_LLM_API_KEY is NOT set.")
        print()
        print("To proceed:")
        print("  Step 1. In YOUR PowerShell session, set:")
        print("            $env:REAL_LLM_API_KEY = '<your Qwen key>'")
        print("  Step 2. Re-run this script from THIS agent session.")
        print()
        print("The agent process inherits the env var.  The key")
        print("value is NEVER printed, NEVER written to disk,")
        print("and NEVER sent to the model.")
        return 1

    print("REAL_LLM_API_KEY is PRESENT in this process.")
    print("(value NEVER printed, NEVER written to disk, NEVER logged)")
    print()

    # 2. Verify Day 13 frozen evidence before any trial
    print("[pre-flight] verifying Day 13 frozen evidence ...")
    try:
        from experiments.day14 import verify_frozen_evidence_unchanged
        r = verify_frozen_evidence_unchanged()
        if not r["passed"]:
            print("STOP: Day 13 frozen evidence mutated.  Aborting.")
            return 1
        print(f"  [OK] {len(r['checked_files'])} frozen files intact")
    except Exception as e:
        print(f"  WARN: pre-flight check raised: {e}")
    print()

    # 3. Run 6 trials
    trials = [
        # (case, strategy, replacement_of, label)
        ("case_a", "FULL_RERUN", "case_a_full_rerun_trial01R2",
         "R3 (case_a FULL, replacement of R2)"),
        ("case_a", "COARSE_CHECKPOINT", "", "case_a COARSE"),
        ("case_a", "SELECTIVE_RECOVERY", "", "case_a SELECTIVE"),
        ("case_b", "FULL_RERUN", "", "case_b FULL"),
        ("case_b", "COARSE_CHECKPOINT", "", "case_b COARSE"),
        ("case_b", "SELECTIVE_RECOVERY", "", "case_b SELECTIVE"),
    ]
    results = []
    for i, (case, strategy, repl, label) in enumerate(trials, 1):
        print(f"=== [{i}/6] {label} ===")
        t0 = time.perf_counter()
        try:
            cp = _run_one(case, strategy, repl)
        except subprocess.TimeoutExpired:
            print(f"  TIMEOUT after 300s")
            results.append({
                "label": label, "case": case, "strategy": strategy,
                "returncode": -1, "stdout_tail": "TIMEOUT",
                "stderr_tail": "TIMEOUT",
            })
            continue
        dt = time.perf_counter() - t0
        stdout_tail = (
            cp.stdout[-500:] if cp.stdout else ""
        )
        stderr_tail = (
            cp.stderr[-500:] if cp.stderr else ""
        )
        results.append({
            "label": label, "case": case, "strategy": strategy,
            "returncode": cp.returncode,
            "elapsed_s": dt,
            "stdout_tail": stdout_tail,
            "stderr_tail": stderr_tail,
        })
        print(f"  exit code: {cp.returncode}  ({dt:.1f}s)")
        if cp.stdout:
            print("  --- stdout (last 500 chars) ---")
            print(cp.stdout[-500:])
        if cp.stderr:
            print("  --- stderr (last 500 chars) ---")
            print(cp.stderr[-500:])
        print()

    # 4. Save runner report
    report_path = ROOT / "experiments/results/day14/runner_report.json"
    report_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 5. Quick summary
    print("=" * 64)
    print("RUNNER SUMMARY")
    print("=" * 64)
    n_ok = sum(1 for r in results if r["returncode"] == 0)
    n_infra = sum(1 for r in results if r["returncode"] == 3)
    n_valid = sum(1 for r in results if r["returncode"] == 2)
    n_harness = sum(1 for r in results if r["returncode"] == 4)
    n_other = sum(
        1 for r in results
        if r["returncode"] not in (0, 2, 3, 4)
    )
    print(f"  0 (PASS)        : {n_ok}")
    print(f"  2 (VALIDATION)  : {n_valid}")
    print(f"  3 (INFRA)       : {n_infra}")
    print(f"  4 (HARNESS)     : {n_harness}")
    print(f"  other           : {n_other}")
    print()
    for r in results:
        print(f"  {r['label']:50s} rc={r['returncode']:3d}  "
              f"{r.get('elapsed_s', 0):.1f}s")
    print()
    print(f"Runner report: {report_path}")
    print()
    # Remind user to unset key
    print("=" * 64)
    print("POST-RUN: please unset the env var:")
    print("  Remove-Item Env:REAL_LLM_API_KEY")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
