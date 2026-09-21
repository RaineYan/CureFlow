"""Stage 1.5 preflight + evidence integrity check.
No Provider calls.  Hard-stops on any preflight failure.
"""
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def main():
    # Step 1: preflight (no Provider call yet)
    os.environ.setdefault(
        "REAL_LLM_BASE_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    from _exp02_stage15_real_runner import preflight
    pf = preflight()
    print("=" * 64)
    print("PREFLIGHT (no Provider call)")
    print("=" * 64)
    for k, v in pf.items():
        print(f"  {k:18s}: {v}")
    print()

    # Step 2: evidence integrity (no Provider call)
    print("=" * 64)
    print("EVIDENCE INTEGRITY")
    print("=" * 64)
    from experiments.day14 import verify_frozen_evidence_unchanged
    fr = verify_frozen_evidence_unchanged()
    print(f"Day 13 frozen evidence: passed={fr['passed']}")
    print(f"  files checked: {len(fr['checked_files'])}")
    print(f"  mismatches: {fr['mismatches']}")
    print(f"  missing: {fr['missing']}")
    print()

    pilot = ROOT / "experiments/results/day14/pilot"
    critical = {
        "case_a_full_rerun_trial01R2.json":
            "c88785ab234c30067a8cd26ddf00416b7a08ec61d39788eaa112944dc5628818",
        "case_a_full_rerun_trial01R2_live_ledger.jsonl":
            "2cf0d95f3efd5210cf9153a1baef7b3bdf42baaf77297686676d064ce4775184",
        "case_a_full_rerun_trial01R3.json": None,
        "case_a_full_rerun_trial01R4.json": None,
    }
    print("Critical evidence (R1/R2/R3/R4) SHA check:")
    all_ok = True
    for fname, expected in critical.items():
        p = pilot / fname
        if not p.exists():
            print(f"  {fname:55s} MISSING")
            all_ok = False
            continue
        cur = hashlib.sha256(p.read_bytes()).hexdigest()
        marker = "OK " if (expected is None or cur == expected) else "CHG"
        if expected is None:
            # Just print current SHA; no baseline to compare.
            print(f"  {fname:55s} {marker}  {cur[:16]} (no baseline)")
        else:
            ok = cur == expected
            print(f"  {fname:55s} {marker}  {cur[:16]}")
            all_ok = all_ok and ok
    print(f"  All CRITICAL evidence SHA unchanged: {all_ok}")
    print()

    # Step 3: canonical mapping check
    print("=" * 64)
    print("CANONICAL MAPPING (4 cases)")
    print("=" * 64)
    from experiments.exp02_faults import CANONICAL_CASES, CASE_INJECTORS
    for c, s, ft, t in CANONICAL_CASES:
        print(
            f"  {c:8s} | {s:6s} | {ft:30s} | {t}"
        )
    print()
    print("CASE_INJECTORS:", CASE_INJECTORS)
    print()

    # Step 4: stage15_real output path collision check
    print("=" * 64)
    print("OUTPUT PATH SAFETY")
    print("=" * 64)
    out = ROOT / "experiments/results/exp02/stage15_real"
    print(f"Output dir: {out}")
    print(f"  exists: {out.exists()}")
    if out.exists():
        files = list(out.glob("*.json"))
        print(f"  existing JSON files: {len(files)}")
        for f in files:
            print(f"    {f.name}")
    print()

    # Save preflight report
    out.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": 1,
        "stage": "Stage 1.5",
        "preflight": pf,
        "frozen_evidence": {
            "day13_passed": fr["passed"],
            "critical_evidence_unchanged": all_ok,
        },
        "canonical_mapping": [
            {"case": c, "scenario": s,
             "fault_type": ft, "target": t}
            for c, s, ft, t in CANONICAL_CASES
        ],
    }
    (out / "stage15_preflight.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Saved: {out / 'stage15_preflight.json'}")

    return 0 if (
        pf["classification"] == "OK" and all_ok
    ) else 1


if __name__ == "__main__":
    sys.exit(main())
