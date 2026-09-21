"""Day 14B/EXP-02 Stage 1.5 DRY-RUN runner.

Same outer-loop and 4x3 trial grid as the real runner
(_exp02_stage15_real_runner.py), but uses
MockHttpRealLLMClient instead of RealLLMClient.  ZERO
real Provider calls.

Purpose: validate the runner's pipeline end-to-end
(loop, unpack, trial JSON schema, 5-axis contract,
file persistence, crash-resilience) before the real
Stage 1.5 run with the user's Qwen key.

This is NOT a real benchmark run.  Numbers are
deterministic mock outputs.  The user's task is to
verify the runner's structure, then provide the API
key to enable _exp02_stage15_real_runner.py.

NO real Provider.  NO API key read.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


OUT = ROOT / "experiments/results/exp02/stage15_dry"
OUT.mkdir(parents=True, exist_ok=True)


def _build_mock_recording_client(
    trial_id: str, case_id: str, strategy: str,
):
    """Mock client with provider-shaped responses.  Same
    shape as the real client's recorded data, so the
    5-axis contract computation exercises the same path."""
    from experiments.day14.recording import (
        Day14RecordingClient,
        MockHttpRealLLMClient,
    )
    from experiments.live_ledger import LiveCallLedger
    import json as _json
    import re as _re

    def factory(prompt: str, unit: str) -> str:
        evidence_refs = []
        for line in prompt.splitlines():
            m = _re.match(
                r"\s*([A-Za-z0-9_]+)\s*/\s*"
                r"([A-Za-z0-9_]+)\s*@\s*v(\d+)\s",
                line,
            )
            if m:
                evidence_refs.append(
                    f"{m.group(1)}/{m.group(2)}@v{m.group(3)}"
                )
        nums: dict = {}
        for line in prompt.splitlines():
            m = _re.match(
                r"(\S+/\S+@v\d+)\s+value\s*=\s*(-?\d+\.?\d*)",
                line,
            )
            if m:
                nums[m.group(1)] = float(m.group(2))
        claim_parts = []
        for ref, val in nums.items():
            pct = val * 100.0
            direction = (
                "增长" if pct > 0
                else "下降" if pct < 0
                else "持平"
            )
            artifact_id = (
                ref.split("/", 1)[-1].split("@", 1)[0]
            )
            claim_parts.append(
                f"{artifact_id} {direction} {abs(pct):.2f}%"
            )
        claim_text = (
            "Pre-Stage15 mock observation: "
            + "；".join(claim_parts)
        ) if claim_parts else "Pre-Stage15 mock claim"
        return _json.dumps({
            "claim_text": claim_text,
            "evidence_refs": evidence_refs,
            "confidence": 0.92,
        }, ensure_ascii=False)

    mock = MockHttpRealLLMClient(
        assistant_content_factory=factory,
        input_tokens=433,
        output_tokens=82,
        latency_ms=2200.0,
    )
    ledger = LiveCallLedger()
    persistence_path = OUT / f"{trial_id}_live_ledger.jsonl"
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


def _trial_already_done(trial_id: str) -> bool:
    p = OUT / f"{trial_id}.json"
    if not p.exists():
        return False
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return "provider_success" in d
    except Exception:
        return False


def _run_dry_trial(
    case_label: str, scenario_id: str, strategy: str,
    target_artifact_id: str, trial_id: str,
) -> tuple:
    """Run ONE dry trial.  Returns (path, status).
    status in {'OK', 'SKIPPED', 'CRASHED'}.
    """
    if _trial_already_done(trial_id):
        return OUT / f"{trial_id}.json", "SKIPPED"
    from experiments.day14 import (
        FROZEN_PROVIDER_CONFIG,
        build_trial_spec,
    )
    from experiments.day14.strategies import _build_scenario
    from experiments.strategies import (
        run_full_rerun_recovery,
        run_coarse_checkpoint_recovery,
        run_selective_recovery,
    )
    from _exp02_runner import (
        _inject_fault,
        _identify_root_cause_and_affected,
        _output_contract_valid_from,
        _semantic_validation_success_from,
    )
    spec = build_trial_spec(
        case_id=scenario_id,
        strategy=strategy,
        trial_id=trial_id,
        out_dir=OUT,
    )
    rec = _build_mock_recording_client(
        trial_id, scenario_id, strategy,
    )
    spec.real_client = rec
    spec.execution_mode = "DRY_RUN_MOCK"
    scenario, golden = _build_scenario(
        scenario_id, spec.trial_id, spec.csv_path,
    )
    try:
        _inject_fault(
            case_label, scenario.store, scenario.run_id,
            target_artifact_id,
        )
    except Exception:
        pass
    t0 = time.perf_counter()
    try:
        if strategy == "FULL_RERUN":
            res = run_full_rerun_recovery(
                scenario, golden,
                csv_path=spec.csv_path,
                llm_client=spec.real_client,
            )
        elif strategy == "COARSE_CHECKPOINT":
            res = run_coarse_checkpoint_recovery(
                scenario, golden,
                csv_path=spec.csv_path,
                llm_client=spec.real_client,
            )
        else:
            res = run_selective_recovery(
                scenario, golden,
                llm_client=spec.real_client,
            )
    except Exception as e:
        wall_ms = (time.perf_counter() - t0) * 1000.0
        trial = {
            "experiment_id": "EXP-02",
            "trial_id": trial_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "provider": FROZEN_PROVIDER_CONFIG.provider,
            "model": FROZEN_PROVIDER_CONFIG.model,
            "case_id": case_label,
            "scenario_id": scenario_id,
            "failure_type": "INFRASTRUCTURE",
            "strategy": strategy,
            "success": False,
            "recovery_success": False,
            "provider_success": False,
            "recovery_execution_success": False,
            "output_contract_valid": False,
            "semantic_validation_success": False,
            "trial_valid": False,
            "workflow_contract_success": False,
            "golden_match": False,
            "llm_calls": len(rec.records),
            "tokens_total": 0,
            "latency_ms": sum(
                r.client_wall_clock_latency_ms or 0
                for r in rec.records
            ),
            "wall_time_ms": wall_ms,
            "error_type": type(e).__name__,
            "error_message": str(e),
        }
        out_path = OUT / f"{trial_id}.json"
        out_path.write_text(
            json.dumps(trial, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return out_path, "CRASHED"
    wall_ms = (time.perf_counter() - t0) * 1000.0
    n_calls = len(rec.records)
    total_in = sum(r.input_tokens or 0 for r in rec.records)
    total_out = sum(r.output_tokens or 0 for r in rec.records)
    provider_exec = (
        n_calls > 0
        and all(
            (r.request_id or "").startswith("chatcmpl-")
            and r.usage_type == "provider_reported"
            for r in rec.records
        )
    )
    output_contract = _output_contract_valid_from(res)
    semantic = _semantic_validation_success_from(res)
    golden = bool(res.matches_golden_output)
    has_exception = bool(
        res.failure_reason
        and "sem=True int=True golden=False" not in
        (res.failure_reason or "")
    )
    recovery_exec = bool(provider_exec and not has_exception)
    trial_valid = bool(
        provider_exec and recovery_exec
        and output_contract and semantic
    )
    structural = _identify_root_cause_and_affected(
        case_label, strategy,
    )
    est_cost = (
        total_in * 0.004 / 1000.0
        + total_out * 0.012 / 1000.0
    )
    trial = {
        "experiment_id": "EXP-02",
        "trial_id": trial_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "provider": FROZEN_PROVIDER_CONFIG.provider,
        "model": FROZEN_PROVIDER_CONFIG.model,
        "case_id": case_label,
        "scenario_id": scenario_id,
        "failure_type": "DRY_RUN_MOCK",
        "fault_id": None,
        "strategy": strategy,
        "success": bool(res.success),
        "recovery_success": recovery_exec,
        "provider_success": provider_exec,
        "recovery_execution_success": recovery_exec,
        "output_contract_valid": output_contract,
        "semantic_validation_success": semantic,
        "trial_valid": trial_valid,
        "workflow_contract_success": bool(res.success),
        "golden_match": golden,
        "root_cause": structural["root_cause"],
        "affected_nodes": structural["affected_nodes"],
        "unaffected_nodes": structural["unaffected_nodes"],
        "rerun_nodes": structural["rerun_nodes"],
        "reused_nodes": structural["reused_nodes"],
        "llm_calls": n_calls,
        "tokens_prompt": total_in,
        "tokens_completion": total_out,
        "tokens_total": total_in + total_out,
        "latency_ms": sum(
            r.client_wall_clock_latency_ms or 0
            for r in rec.records
        ),
        "wall_time_ms": wall_ms,
        "estimated_cost_cny": round(est_cost, 6),
        "error_type": (
            "none" if res.success else "GOLDEN_MISS"
        ),
        "error_message": res.failure_reason or "",
        "matches_golden_output": golden,
        "final_content_hash": res.final_content_hash,
        "execution_mode": "DRY_RUN_MOCK",
    }
    out_path = OUT / f"{trial_id}.json"
    out_path.write_text(
        json.dumps(trial, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return out_path, "OK"


def main() -> int:
    from experiments.exp02_faults import CANONICAL_CASES
    from _exp02_stage15_real_runner import STRATEGIES
    print("=" * 64)
    print("Stage 1.5 DRY-RUN (no real Provider)")
    print("=" * 64)
    print()
    # Preflight sanity: assert no real key
    import os
    if os.environ.get("REAL_LLM_API_KEY"):
        print("WARNING: REAL_LLM_API_KEY IS SET in env.")
        print("DRY-RUN should NOT use it.  Unset first.")
        return 1
    print("  REAL_LLM_API_KEY: NOT SET (correct for dry-run)")
    print()
    # Outer loop
    print("[1/2] Running 4 cases x 3 strategies = 12 dry trials")
    print()
    results = []
    for case_label, scenario_id, fault_type, target_id in (
        CANONICAL_CASES
    ):
        for strategy in STRATEGIES:
            trial_id = (
                f"stage15dry_{case_label}_{strategy.lower()}_01"
            )
            t0 = time.perf_counter()
            try:
                p, status = _run_dry_trial(
                    case_label, scenario_id, strategy,
                    target_id, trial_id,
                )
                dt = time.perf_counter() - t0
                d = json.loads(p.read_text(encoding="utf-8"))
                results.append({
                    "trial_id": trial_id,
                    "case_id": case_label,
                    "strategy": strategy,
                    "status": status,
                    "elapsed_s": dt,
                    "provider_success": d.get("provider_success"),
                    "trial_valid": d.get("trial_valid"),
                    "llm_calls": d.get("llm_calls"),
                    "tokens_total": d.get("tokens_total"),
                })
                print(
                    f"  [{len(results):2d}/12] "
                    f"{trial_id:50s} status={status:8s} "
                    f"prov={d.get('provider_success')!s:5s} "
                    f"valid={d.get('trial_valid')!s:5s} "
                    f"calls={d.get('llm_calls')} "
                    f"tokens={d.get('tokens_total')}"
                )
            except Exception as e:
                dt = time.perf_counter() - t0
                results.append({
                    "trial_id": trial_id,
                    "case_id": case_label,
                    "strategy": strategy,
                    "status": "HARD_CRASH",
                    "elapsed_s": dt,
                    "error": f"{type(e).__name__}: {e}",
                })
                print(
                    f"  [HARD_CRASH] {trial_id}: "
                    f"{type(e).__name__}: {e}"
                )
                # Per spec: hard crash stops the run
                break
    # Summary
    print()
    print("[2/2] Dry-run summary")
    n_ok = sum(1 for r in results if r["status"] == "OK")
    n_skip = sum(1 for r in results if r["status"] == "SKIPPED")
    n_crash = sum(1 for r in results if r["status"] == "CRASHED")
    n_hard = sum(1 for r in results if r["status"] == "HARD_CRASH")
    print(f"  OK         : {n_ok}/12")
    print(f"  SKIPPED    : {n_skip}/12")
    print(f"  CRASHED    : {n_crash}/12")
    print(f"  HARD_CRASH : {n_hard}/12")
    # Verify trial count
    n_unique = len({(r["case_id"], r["strategy"]) for r in results})
    print()
    print(f"  unique (case, strategy) pairs: {n_unique}/12")
    assert n_unique == 12, "outer loop must produce 12 unique pairs"
    # Save summary
    summary = {
        "schema_version": 1,
        "experiment_id": "EXP-02",
        "mode": "DRY_RUN_MOCK",
        "stage": "Stage 1.5 dry-run (no real Provider)",
        "trial_count": len(results),
        "results": results,
        "disclaimer": (
            "MOCK / PIPELINE VALIDATION ONLY.  No real Qwen.  "
            "Validates the runner's 4x3 outer loop, 5-axis "
            "contract, and trial JSON schema.  Re-run with "
            "_exp02_stage15_real_runner.py for real Provider."
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    (OUT / "stage15_dry_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print()
    print(f"Saved: {OUT / 'stage15_dry_summary.json'}")
    print()
    print("=" * 64)
    print("Stage 1.5 DRY-RUN COMPLETE")
    print("=" * 64)
    print(
        "Runner outer-loop, 4x3 grid, and 5-axis contract "
        "verified.  Ready for real Provider run when key is "
        "available."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
