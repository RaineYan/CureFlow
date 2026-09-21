"""Day 14A PRE-PILOT — simulate the 6 trial with a
real-Provider-shaped mock client (no Qwen HTTP, no
forensic pollution).

Goal: produce reduction data (provider tokens, wall-clock)
to back the Day 14A 计划书 / 答辩 PPT.

Outputs:
  - experiments/results/day14/pre_pilot/<trial_id>.json
  - experiments/results/day14/pre_pilot/pre_pilot_report.json

The trial_id uses `pre01` prefix to avoid colliding with
trial01 / R1 / R2 in the main pilot directory.  These
files are NOT day-13 frozen evidence and do NOT
interfere with Day 14A live pilot.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from experiments.day14 import (
    FROZEN_PROVIDER_CONFIG,
    build_trial_spec,
    run_trial,
    TrialResult,
)
from experiments.day14.recording import (
    Day14RecordingClient,
    MockHttpRealLLMClient,
    _parse_evidence_refs_from_prompt,
)
from experiments.day14.pilot_runner import (
    is_replacement_id_occupied,
    PILOT_DIR,
)
from experiments.live_ledger import LiveCallLedger
from llm import LLMResponse, UsageType


PRE_PILOT_DIR = ROOT / "experiments/results/day14/pre_pilot"


def _make_mock_factory():
    """Return a factory that produces claim-shaped
    JSON with evidence_refs parsed from the prompt
    AND a claim_text that quotes the actual evidence
    numbers (so semantic validation passes)."""

    def factory(prompt: str, task_name: str) -> str:
        evidence_refs = _parse_evidence_refs_from_prompt(
            prompt, expected_run_id=None,
        )
        # Parse the evidence block values from the prompt
        # so the claim_text can quote the actual numbers
        # (semantic validation requires 1-to-1 numeric
        # consistency with cited evidence).
        import re as _re
        # Look for lines like "  run/artifact@v1  value=0.6078"
        # OR "  run/artifact@v1  content=...some text..."
        value_pattern = _re.compile(
            r"value\s*=\s*(-?\d+\.?\d*)",
        )
        ref_to_value: Dict[str, float] = {}
        for line in prompt.splitlines():
            m_ref = _re.search(
                r"(\S+/\S+@v\d+)\s+value\s*=\s*(-?\d+\.?\d*)",
                line,
            )
            if m_ref:
                ref_to_value[m_ref.group(1)] = float(
                    m_ref.group(2)
                )
        # Build claim_text by quoting each evidence value
        # with a sensible direction word.
        claim_parts: List[str] = []
        for ref, val in ref_to_value.items():
            pct = val * 100.0
            direction = (
                "增长" if pct > 0
                else "下降" if pct < 0
                else "持平"
            )
            # e.g. "revenue_growth 增长 60.78%"
            artifact_id = ref.split("/", 1)[-1].split("@", 1)[0]
            claim_parts.append(
                f"{artifact_id} {direction} {abs(pct):.2f}%"
            )
        if claim_parts:
            claim_text = (
                "Pre-pilot mock observation: " + "；".join(claim_parts)
            )
        else:
            claim_text = "Pre-pilot mock claim (no numeric evidence)"
        payload = {
            "claim_text": claim_text,
            "evidence_refs": evidence_refs,
            "confidence": 0.92,
        }
        return json.dumps(payload, ensure_ascii=False)
    return factory


def _build_recording_client(
    trial_id: str, case_id: str, strategy: str,
) -> Day14RecordingClient:
    """Build a Day14RecordingClient wrapping
    MockHttpRealLLMClient.  The mock simulates a real
    Provider HTTP 200 with chatcmpl-mock-* request_id
    and provider_reported usage.
    """
    mock = MockHttpRealLLMClient(
        assistant_content_factory=_make_mock_factory(),
        input_tokens=560,    # calibrated to Day 13.6 average
        output_tokens=82,    # calibrated to Day 13.6 average
        latency_ms=2200.0,   # calibrated to Day 13.6 average
    )
    ledger = LiveCallLedger()
    persistence_path = (
        PRE_PILOT_DIR / f"{trial_id}_live_ledger.jsonl"
    )
    phase_prefix = (
        "A" if strategy == "FULL_RERUN"
        else "B" if strategy == "COARSE_CHECKPOINT"
        else "C-recovery"
    )
    return Day14RecordingClient(
        mock, ledger,
        phase_prefix=phase_prefix,
        trial_id=trial_id, case_id=case_id, strategy=strategy,
        persistence_path=persistence_path,
    )


def _run_one(
    case_id: str, strategy: str, trial_id: str,
) -> TrialResult:
    spec = build_trial_spec(
        case_id=case_id,
        strategy=strategy,
        trial_id=trial_id,
        out_dir=PRE_PILOT_DIR,
    )
    rec = _build_recording_client(
        trial_id=spec.trial_id,
        case_id=spec.case_id,
        strategy=spec.strategy,
    )
    spec.real_client = rec
    spec.execution_mode = "PRE_PILOT_MOCK"
    PRE_PILOT_DIR.mkdir(parents=True, exist_ok=True)
    return run_trial(spec)


def _write_trial(result: TrialResult) -> str:
    p = PRE_PILOT_DIR / f"{result.trial_id}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(p)


def main() -> int:
    print("=" * 64)
    print("Day 14A PRE-PILOT — 6 trials, MockHttpRealLLMClient")
    print("(0 real Qwen calls, simulates real Provider shape)")
    print("=" * 64)
    print()
    PRE_PILOT_DIR.mkdir(parents=True, exist_ok=True)

    results: Dict[str, Dict[str, TrialResult]] = {
        "case_a": {},
        "case_b": {},
    }
    for case_id in ("case_a", "case_b"):
        for strategy in (
            "FULL_RERUN", "COARSE_CHECKPOINT", "SELECTIVE_RECOVERY",
        ):
            trial_id = (
                f"{case_id}_{strategy.lower()}_pre01"
            )
            print(f"  Running: {trial_id}  ...", end=" ", flush=True)
            t0 = time.perf_counter()
            try:
                res = _run_one(case_id, strategy, trial_id)
                out = _write_trial(res)
                dt = (time.perf_counter() - t0) * 1000.0
                results[case_id][strategy] = res
                print(
                    f"OK ({dt:.0f} ms) → {Path(out).name}  "
                    f"success={res.success}  "
                    f"calls={res.real_metrics.real_llm_call_count}  "
                    f"tokens={res.real_metrics.provider_reported_token_total}"
                )
            except Exception as e:
                dt = (time.perf_counter() - t0) * 1000.0
                print(
                    f"FAIL ({dt:.0f} ms): {type(e).__name__}: {e}"
                )
                raise

    # Build reduction report
    print()
    print("=" * 64)
    print("REDUCTION REPORT")
    print("=" * 64)
    report: Dict[str, Any] = {
        "schema_version": 1,
        "experiment_type": "DAY14_PRE_PILOT",
        "provider_config": FROZEN_PROVIDER_CONFIG.to_dict(),
        "is_offline_dry_run": True,
        "reason": (
            "Pre-pilot using MockHttpRealLLMClient — no real "
            "Qwen HTTP, but request_id=chatcmpl-mock-* and "
            "usage_type=provider_reported so the 5-layer state "
            "is fully True and the reduction formulas see real "
            "tokens."
        ),
        "per_case": {},
    }
    for case_id, by_strategy in results.items():
        full = by_strategy.get("FULL_RERUN")
        coarse = by_strategy.get("COARSE_CHECKPOINT")
        selective = by_strategy.get("SELECTIVE_RECOVERY")
        if not (full and coarse and selective):
            continue
        # Compute reduction for COARSE and SELECTIVE vs FULL
        def _reduce(smaller: TrialResult, larger: TrialResult) -> Dict[str, Any]:
            f_calls = larger.real_metrics.real_llm_call_count
            s_calls = smaller.real_metrics.real_llm_call_count
            f_tok = larger.real_metrics.provider_reported_token_total
            s_tok = smaller.real_metrics.provider_reported_token_total
            f_ms = larger.recovery_wall_clock_ms
            s_ms = smaller.recovery_wall_clock_ms
            return {
                "real_llm_call_count_larger": f_calls,
                "real_llm_call_count_smaller": s_calls,
                "real_llm_call_reduction_count": f_calls - s_calls,
                "real_llm_call_reduction_pct": (
                    100.0 * (f_calls - s_calls) / f_calls
                    if f_calls > 0 else 0.0
                ),
                "provider_token_total_larger": f_tok,
                "provider_token_total_smaller": s_tok,
                "provider_token_reduction_count": f_tok - s_tok,
                "provider_token_reduction_pct": (
                    100.0 * (f_tok - s_tok) / f_tok
                    if f_tok > 0 else 0.0
                ),
                "recovery_wallclock_ms_larger": f_ms,
                "recovery_wallclock_ms_smaller": s_ms,
                "recovery_wallclock_reduction_ms": f_ms - s_ms,
                "recovery_wallclock_reduction_pct": (
                    100.0 * (f_ms - s_ms) / f_ms
                    if f_ms > 0 else 0.0
                ),
            }
        report["per_case"][case_id] = {
            "FULL_RERUN": {
                "trial_id": full.trial_id,
                "success": full.success,
                "real_llm_call_count": full.real_metrics.real_llm_call_count,
                "provider_reported_token_total": full.real_metrics.provider_reported_token_total,
                "recovery_wall_clock_ms": full.recovery_wall_clock_ms,
                "llm_request_latency_sum_ms": full.real_metrics.llm_request_latency_sum_ms,
                "new_llm_artifact_count": len(full.new_llm_artifact_refs),
                "reused_llm_artifact_count": len(full.reused_llm_artifact_refs),
            },
            "COARSE_CHECKPOINT": {
                "trial_id": coarse.trial_id,
                "success": coarse.success,
                "real_llm_call_count": coarse.real_metrics.real_llm_call_count,
                "provider_reported_token_total": coarse.real_metrics.provider_reported_token_total,
                "recovery_wall_clock_ms": coarse.recovery_wall_clock_ms,
                "llm_request_latency_sum_ms": coarse.real_metrics.llm_request_latency_sum_ms,
                "new_llm_artifact_count": len(coarse.new_llm_artifact_refs),
                "reused_llm_artifact_count": len(coarse.reused_llm_artifact_refs),
            },
            "SELECTIVE_RECOVERY": {
                "trial_id": selective.trial_id,
                "success": selective.success,
                "real_llm_call_count": selective.real_metrics.real_llm_call_count,
                "provider_reported_token_total": selective.real_metrics.provider_reported_token_total,
                "recovery_wall_clock_ms": selective.recovery_wall_clock_ms,
                "llm_request_latency_sum_ms": selective.real_metrics.llm_request_latency_sum_ms,
                "new_llm_artifact_count": len(selective.new_llm_artifact_refs),
                "reused_llm_artifact_count": len(selective.reused_llm_artifact_refs),
            },
            "reductions": {
                "COARSE_vs_FULL": _reduce(coarse, full),
                "SELECTIVE_vs_FULL": _reduce(selective, full),
                "SELECTIVE_vs_COARSE": _reduce(selective, coarse),
            },
        }
        pc = report["per_case"][case_id]
        # Print summary
        print()
        print(f"--- {case_id} ---")
        for strat in ("FULL_RERUN", "COARSE_CHECKPOINT", "SELECTIVE_RECOVERY"):
            d = pc[strat]
            print(
                f"  {strat:20s} | calls={d['real_llm_call_count']}  "
                f"tokens={d['provider_reported_token_total']}  "
                f"wall={d['recovery_wall_clock_ms']:.0f}ms  "
                f"new_llm={d['new_llm_artifact_count']}  "
                f"reused={d['reused_llm_artifact_count']}"
            )
        for k, v in pc["reductions"].items():
            print(
                f"  {k}: calls -{v['real_llm_call_reduction_count']} "
                f"({v['real_llm_call_reduction_pct']:.1f}%), "
                f"tokens -{v['provider_token_reduction_count']} "
                f"({v['provider_token_reduction_pct']:.1f}%), "
                f"wall -{v['recovery_wallclock_reduction_ms']:.0f}ms "
                f"({v['recovery_wallclock_reduction_pct']:.1f}%)"
            )

    # Save report
    report_path = PRE_PILOT_DIR / "pre_pilot_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print()
    print(f"Report: {report_path}")
    print()
    print("=" * 64)
    print("PRE-PILOT COMPLETE — 6 trials, 0 Qwen calls.")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
