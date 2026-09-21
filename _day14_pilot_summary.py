"""Day 14 Pilot — offline dry run of all 6 trials.

For each (case, strategy) combination, run the pilot
with FakeLLM (since the API key is not in this
session).  This validates the harness end-to-end.

NOTE: this is an OFFLINE DRY RUN.  Numbers are
FakeLLM-derived (synthetic).  The real pilot must
be executed user-side via `day14_pilot_run.py` with
QWEN_API_KEY set.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from experiments.day14 import (
    FROZEN_PROVIDER_CONFIG,
    build_trial_spec,
    run_trial,
    write_trial_result,
    assert_frozen_evidence_intact,
    assert_signature_fairness,
    compute_scenario_signature_hash,
    assert_provider_config,
    compute_reductions,
    TrialSpec,
    TrialResult,
)
from experiments.day14.frozen_evidence import (
    verify_frozen_evidence_unchanged,
)

PILOT_DIR = Path("experiments/results/day14/pilot")
CASES = ("case_a", "case_b")
STRATEGIES = (
    "FULL_RERUN",
    "COARSE_CHECKPOINT",
    "SELECTIVE_RECOVERY",
)


def main() -> int:
    # 0) hard gate: Day 13 frozen
    frozen = assert_frozen_evidence_intact()
    if not frozen["passed"]:
        print("STOP: Day 13 frozen evidence mutated.")
        return 1

    # 1) provider config sanity
    pc = assert_provider_config(FROZEN_PROVIDER_CONFIG.to_dict())
    if not pc["passed"]:
        print(f"STOP: provider config mismatch: {pc}")
        return 1

    # 2) Run all 6 trials.  For each case, capture
    #    scenario signature per strategy and assert
    #    fairness BEFORE running the trial.
    results: dict = {}
    signatures: dict = {}
    for case in CASES:
        for strat in STRATEGIES:
            spec = build_trial_spec(
                case_id=case, strategy=strat,
                out_dir=PILOT_DIR,
            )
            res = run_trial(spec)
            results[(case, strat)] = res
            signatures.setdefault(case, {})[strat] = (
                res.scenario_signature_hash
            )
            write_trial_result(res)
            print(
                f"  {case:6s}  {strat:20s}  "
                f"success={res.success}  "
                f"new_llm={len(res.new_llm_artifact_refs)}  "
                f"reused_llm={len(res.reused_llm_artifact_refs)}  "
                f"wall_ms={res.recovery_wall_clock_ms:.2f}"
            )

    # 3) Scenario signature fairness check.
    fairness = {}
    for case, sigs in signatures.items():
        fairness[case] = assert_signature_fairness(case, sigs)
        if not fairness[case]["passed"]:
            print(f"STOP: signature mismatch for {case}")
            return 1

    # 4) Compute reductions per case.
    summary = {
        "schema_version": 1,
        "experiment_type": "DAY14_PILOT_SUMMARY",
        "is_offline_dry_run": True,
        "reason": (
            "Offline dry run.  No real Qwen calls.  "
            "Real numbers require user-side execution "
            "with QWEN_API_KEY set."
        ),
        "provider_config": FROZEN_PROVIDER_CONFIG.to_dict(),
        "scenario_signature_fairness": {
            c: f["passed"] for c, f in fairness.items()
        },
        "per_case": {},
    }
    for case in CASES:
        full = results[(case, "FULL_RERUN")]
        coarse = results[(case, "COARSE_CHECKPOINT")]
        sel = results[(case, "SELECTIVE_RECOVERY")]
        summary["per_case"][case] = {
            "FULL_RERUN": _summary_row(full),
            "COARSE_CHECKPOINT": _summary_row(coarse),
            "SELECTIVE_RECOVERY": _summary_row(sel),
            "reductions": compute_reductions(full, coarse, sel),
        }

    out = PILOT_DIR / "day14_pilot_summary.json"
    out.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nWrote: {out}")

    # 5) Verify Day 13 evidence STILL unchanged after
    #    dry run.
    post = verify_frozen_evidence_unchanged()
    if not post["passed"]:
        print("STOP: Day 13 evidence mutated by dry run.")
        return 1
    print(
        f"OK: Day 13 evidence still intact "
        f"({len(post['checked_files'])} files checked)."
    )

    return 0


def _summary_row(res: TrialResult) -> dict:
    s = res.structural
    return {
        "success": res.success,
        "failure_reason": res.failure_reason,
        "real_llm_call_count":
            res.real_metrics.real_llm_call_count,
        "provider_reported_token_total":
            res.real_metrics.provider_reported_token_total,
        "recovery_wall_clock_ms":
            round(res.recovery_wall_clock_ms, 2),
        "llm_request_latency_sum_ms":
            round(res.real_metrics.llm_request_latency_sum_ms, 2),
        "task_calls": s.get("task_calls", 0),
        "tool_task_calls": s.get("tool_task_calls", 0),
        "llm_task_calls": s.get("llm_task_calls", 0),
        "artifact_materializations":
            s.get("artifact_materializations", 0),
        "required_materializations":
            s.get("required_materializations", 0),
        "unnecessary_materializations":
            s.get("unnecessary_materializations", 0),
        "rmp": s.get("rmp"),
        "uarr": s.get("uarr"),
        "upr": s.get("upr"),
        "arr": s.get("arr"),
        "raf": s.get("raf"),
        "taf": s.get("taf"),
        "semantic_clean_path_pass":
            res.semantic.get("semantic_clean_path_pass"),
        "execution_integrity_pass":
            res.semantic.get("execution_integrity_pass"),
    }


if __name__ == "__main__":
    sys.exit(main())
