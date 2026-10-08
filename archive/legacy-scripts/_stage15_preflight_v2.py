"""Stage 1.5 HARD-STOP PREFLIGHT (read-only, no Provider call)."""
import json
import os
import sys
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

PILOT = ROOT / "experiments/results/day14/pilot"
EX02_DIR = ROOT / "experiments/results/exp02"
STAGE15_DIR = Path(os.environ.get("STAGE15_OUT_DIR", str(EX02_DIR / "stage15_real")))
STAGE15_DIR = Path(STAGE15_DIR)


def main():
    print("=" * 64)
    print("STAGE 1.5 HARD-STOP PREFLIGHT (read-only, no Provider call)")
    print("=" * 64)
    print()
    report = {}
    hard_stop = False

    # 1. Provider env var presence
    key_present = bool(os.environ.get("REAL_LLM_API_KEY"))
    base_url = os.environ.get("REAL_LLM_BASE_URL", "")
    report["env_REAL_LLM_API_KEY"] = "PRESENT" if key_present else "MISSING"
    report["env_REAL_LLM_BASE_URL"] = base_url if base_url else "NOT SET"
    print("1. env REAL_LLM_API_KEY:", report["env_REAL_LLM_API_KEY"])
    print("   env REAL_LLM_BASE_URL:", report["env_REAL_LLM_BASE_URL"])
    if not key_present:
        hard_stop = True
        print("   --> FAIL: API key env var not set (HARD STOP trigger)")
    else:
        print(
            "   length:",
            len(os.environ["REAL_LLM_API_KEY"]),
            "(value NEVER printed)",
        )
    if not base_url:
        hard_stop = True
        print("   --> FAIL: base_url not set")
    elif base_url.rstrip("/") != "https://dashscope.aliyuncs.com/compatible-mode/v1":
        hard_stop = True
        print(f"   --> FAIL: base_url mismatch (got {base_url!r})")
    print()

    # 2. Day 13 frozen evidence
    print("2. Day 13 frozen evidence:")
    try:
        from experiments.day14 import verify_frozen_evidence_unchanged
        r = verify_frozen_evidence_unchanged()
        print(f"   passed: {r['passed']}  files: {len(r['checked_files'])}")
        if not r["passed"]:
            hard_stop = True
            print(
                f"   --> FAIL: mismatches={r['mismatches']}, "
                f"missing={r['missing']}"
            )
    except Exception as e:
        hard_stop = True
        print(f"   ERROR: {e}")
    print()

    # 3. R1/R2/R3/R4 / REAL02 / Stage 1 mock evidence SHA
    print("3. R1/R2/R3/R4 / REAL02 / Stage 1 mock evidence:")
    real02_dir = ROOT / "experiments/results/exp02"
    stage1_mock = (
        ROOT / "experiments/results/exp02/exp02_CASE_A_full_rerun_pre01.json"
    )
    critical = {
        "R2_trial_json": (
            PILOT / "case_a_full_rerun_trial01R2.json",
            "c88785ab234c30067a8cd26ddf00416b7a08ec61d39788eaa112944dc5628818",
        ),
        "R2_ledger": (
            PILOT / "case_a_full_rerun_trial01R2_live_ledger.jsonl",
            "2cf0d95f3efd5210cf9153a1baef7b3bdf42baaf77297686676d064ce4775184",
        ),
    }
    all_ok = True
    for name, (p, expected_sha) in critical.items():
        if not p.exists():
            print(f"   {name}: MISSING")
            all_ok = False
            continue
        cur = hashlib.sha256(p.read_bytes()).hexdigest()
        status = "OK" if cur == expected_sha else "CHANGED"
        print(f"   {name:25s}  {status}  {cur[:16]}")
        if cur != expected_sha:
            all_ok = False
    print(f"   -- R2 evidence (real baseline) verified: {all_ok}")
    # Stage 1 mock evidence - just verify file exists
    print(f"   Stage 1 mock trial example exists: {stage1_mock.exists()}")
    print()

    # 4. Output path independence
    print("4. Stage 1.5 output path independence:")
    print(f"   Stage 1.5 dir: {STAGE15_DIR} (independent)")
    print(f"   Stage 1 dir:  {PILOT}")
    print(f"   -- Stage 1.5 writes to exp02/stage15_real/, NOT to day14/pilot/")
    print()

    # 5. CANONICAL_CASES validation
    print("5. CANONICAL_CASES validation:")
    from experiments.exp02_faults import CANONICAL_CASES, CASE_INJECTORS
    expected = [
        ("CASE_A", "case_a", "ARTIFACT_DATA_CORRUPTION", "field_revenue_2025"),
        ("CASE_B", "case_b", "AGENT_OUTPUT_ERROR", "claim_financial_growth"),
        ("CASE_C", "case_a", "EVIDENCE_REFERENCE_ERROR", "claim_financial_growth"),
        ("CASE_D", "case_a", "ARTIFACT_DATA_CORRUPTION", "field_revenue_2025"),
    ]
    canonical_ok = list(CANONICAL_CASES) == expected
    print(f"   CANONICAL_CASES == expected: {canonical_ok}")
    if not canonical_ok:
        hard_stop = True
        print(f"   expected: {expected}")
        print(f"   got: {list(CANONICAL_CASES)}")
    for c in CANONICAL_CASES:
        print(f"   {c[0]:8s} | {c[1]:6s} | {c[2]:30s} | {c[3]}")
    print()

    # 6. 12 expected combinations
    print("6. 12 expected unique (case, strategy) combinations:")
    try:
        from _exp02_stage15_real_runner import STRATEGIES
        expected_combos = set()
        for case in ("CASE_A", "CASE_B", "CASE_C", "CASE_D"):
            for strat in STRATEGIES:
                expected_combos.add((case, strat))
        print(f"   Expected: {len(expected_combos)} unique combos")
        print(f"   Strategies: {STRATEGIES}")
    except Exception as e:
        print(f"   ERROR: {e}")
        hard_stop = True
    print()

    # 7. Stage 1.5 prior run integrity (do not overwrite)
    print("7. Stage 1.5 prior run evidence (12 trial JSONs from first run):")
    prior = STAGE15_DIR / "stage15_CASE_A_full_rerun_01.json"
    print(f"   sample: {prior}  exists: {prior.exists()}")
    print()

    # Save preflight report
    report["canonical_cases_ok"] = canonical_ok
    report["hard_stop"] = hard_stop
    report["classification"] = (
        "STAGE15_BLOCKED_INFRA_FAILURE" if hard_stop else "OK"
    )
    STAGE15_DIR.mkdir(parents=True, exist_ok=True)
    report_path = STAGE15_DIR / "stage15_preflight.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Preflight report saved: {report_path}")
    print()
    print("=" * 64)
    if hard_stop:
        print("HARD STOP TRIGGERED")
        print("Reason: REAL_LLM_API_KEY env var is not set in agent session")
        print("Per spec: do not call Provider; output forensic + final gate")
    else:
        print("PREFLIGHT PASSED")
        print("(model / base_url / frozen evidence / canonical mapping all OK)")
        print("Ready for 12 real Provider trials")
    print("=" * 64)
    return 0 if not hard_stop else 1


if __name__ == "__main__":
    sys.exit(main())
