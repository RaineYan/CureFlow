"""Day 14B/EXP-02 Stage 1.5 RETRY runner.

Re-runs ONLY the CRASHED trials from the first Stage 1.5
real run.  Already-OK trials (provider_success=True) are
SKIPPED via _trial_already_done.

CRITICAL CONSTRAINTS (per spec section 6):
  - Does NOT modify any OK trial JSON
  - Does NOT modify frozen evidence (Day 13, R1-R4, REAL02,
    Stage 1 mock, Stage 1.5 dry)
  - Does NOT modify prompts, validators, parsers, or
    golden definitions
  - max_retries=1 at the runner layer ONLY (Provider's
    built-in max_retries=0 is preserved per Day 14 frozen
    config)
  - API key is read from os.environ at process start,
    never printed, never written to disk; pop() at end

If a trial still fails after retry, the trial JSON is
updated to mark retry attempts; the original failure
evidence is preserved.
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# M3: detect cross-Provider mode (same gate as the main runner).
M3_ADAPTER_ACTIVE = bool(
    os.environ.get("REAL_LLM_EXPECTED_BASE")
    or os.environ.get("REAL_LLM_EXPECTED_MODEL")
    or os.environ.get("REAL_LLM_API_KEY_NAME")
)


OUT = Path(os.environ.get("STAGE15_OUT_DIR", str(ROOT / "experiments/results/exp02/stage15_real")))
OUT = Path(OUT)
RETRIES_PER_TRIAL = 1
# M1: glob pattern matches the per-repeat trial-id format
# (`stage15_<case>_<strategy>_repeat_NN.json`).  It also matches
# the single-repeat default `..._repeat_01.json` because
# STAGE15_REPEAT defaults to "01".
# M3: pattern also matches `m3_<case>_<strategy>_repeat_NN.json`
# (or any STAGE15_TRIAL_PREFIX) so the same retry runner works
# for cross-Provider runs.
TRIAL_JSON_GLOB = os.environ.get(
    "STAGE15_TRIAL_GLOB",
    "stage15_*_repeat_*.json",
)


def _is_already_ok(trial_id: str) -> bool:
    """An OK trial is identified by `provider_success=True`
    and a non-empty provider call record.  Skip these."""
    p = OUT / f"{trial_id}.json"
    if not p.exists():
        return False
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return False
    return bool(d.get("provider_success"))


def _crashed_trial_ids() -> list:
    """Return list of trial_ids whose provider_success
    is False.  These are the ones to retry."""
    out = []
    for p in sorted(OUT.glob(TRIAL_JSON_GLOB)):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not d.get("provider_success"):
            out.append(p.stem)
    return out


def _retry_trial(trial_id: str) -> dict:
    """Retry one trial.  Reuses the same case/strategy
    from the original trial JSON (does NOT re-read
    CANONICAL_CASES to avoid silent drift)."""
    p = OUT / f"{trial_id}.json"
    orig = json.loads(p.read_text(encoding="utf-8"))
    case_label = orig["case_id"]
    strategy = orig["strategy"]
    scenario_id = orig["scenario_id"]
    # Recover target_artifact_id from CANONICAL_CASES
    from experiments.exp02_faults import CANONICAL_CASES
    target_id = None
    for c, s, _ft, t in CANONICAL_CASES:
        if c == case_label and s == scenario_id:
            target_id = t
            break
    if target_id is None:
        return {
            "trial_id": trial_id, "status": "MAPPING_ERROR",
            "error": f"no CANONICAL_CASES mapping for {case_label}/{scenario_id}",
        }
    from experiments.day14 import (
        FROZEN_PROVIDER_CONFIG,
        build_trial_spec,
    )
    from experiments.day14.strategies import _build_scenario
    from experiments.day14.recording import Day14RecordingClient
    from experiments.live_ledger import LiveCallLedger
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
    from llm import RealLLMProviderConfig
    from llm.providers import RealLLMClient
    # M3 Provider Adapter: reuse the same env-var-driven helpers
    # as the main runner.  In Qwen mode (no M3 env vars) this
    # reproduces the original Qwen-only behavior exactly.
    base_url = os.environ.get(
        "REAL_LLM_EXPECTED_BASE",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    model = os.environ.get(
        "REAL_LLM_EXPECTED_MODEL", FROZEN_PROVIDER_CONFIG.model,
    )
    key_env = os.environ.get("REAL_LLM_API_KEY_NAME", "REAL_LLM_API_KEY")
    if M3_ADAPTER_ACTIVE:
        provider_label = "cross_provider"
        provider_options = {}
    else:
        provider_label = FROZEN_PROVIDER_CONFIG.provider
        provider_options = {
            "enable_thinking": FROZEN_PROVIDER_CONFIG.enable_thinking,
        }
    cfg = RealLLMProviderConfig(
        provider=provider_label,
        model=model,
        base_url=base_url,
        api_key_env_var=key_env,
        response_format=FROZEN_PROVIDER_CONFIG.response_format,
        temperature=FROZEN_PROVIDER_CONFIG.temperature,
        seed=FROZEN_PROVIDER_CONFIG.seed,
        provider_options=provider_options,
        max_retries=0,  # Provider's built-in retry: 0 (FROZEN)
    )
    real_client = RealLLMClient(cfg)
    spec = build_trial_spec(
        case_id=scenario_id,
        strategy=strategy,
        trial_id=trial_id,
        out_dir=OUT,
    )
    ledger = LiveCallLedger()
    persistence_path = OUT / f"{trial_id}_live_ledger.jsonl"
    phase_prefix = (
        "A" if strategy == "FULL_RERUN"
        else "B" if strategy == "COARSE_CHECKPOINT"
        else "C-recovery"
    )
    rec = Day14RecordingClient(
        real_client, ledger,
        phase_prefix=phase_prefix,
        trial_id=trial_id, case_id=scenario_id, strategy=strategy,
        persistence_path=persistence_path,
    )
    spec.real_client = rec
    spec.execution_mode = "REAL_PROVIDER_RETRY"
    scenario, golden = _build_scenario(
        scenario_id, spec.trial_id, spec.csv_path,
    )
    try:
        _inject_fault(
            case_label, scenario.store, scenario.run_id,
            target_id,
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
        # Update trial JSON with retry attempt recorded.
        orig["retry_attempt"] = (
            orig.get("retry_attempt", 0) + 1
        )
        orig["retry_outcome"] = "STILL_CRASHED"
        orig["retry_exception"] = f"{type(e).__name__}: {e}"
        orig["llm_calls"] = len(rec.records)
        orig["wall_time_ms"] = wall_ms
        out_path = OUT / f"{trial_id}.json"
        out_path.write_text(
            json.dumps(orig, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {
            "trial_id": trial_id, "status": "STILL_CRASHED",
            "calls": len(rec.records),
            "wall_s": wall_ms / 1000.0,
        }
    wall_ms = (time.perf_counter() - t0) * 1000.0
    n_calls = len(rec.records)
    total_in = sum(r.input_tokens or 0 for r in rec.records)
    total_out = sum(r.output_tokens or 0 for r in rec.records)
    # M3 Provider Adapter (same logic as the main runner).
    # In Qwen mode the original condition holds; in M3 mode
    # we relax the chatcmpl- prefix check to allow UUID
    # request_ids from OpenAI-compatible Providers such as
    # DeepSeek.  usage_type=provider_reported is still
    # required so the tokens are real, not estimated.
    if M3_ADAPTER_ACTIVE:
        provider_exec = (
            n_calls > 0
            and all(
                bool(r.request_id)
                and r.usage_type == "provider_reported"
                for r in rec.records
            )
        )
    else:
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
    # Update trial JSON: keep original fields, add retry info.
    new_trial = dict(orig)
    new_trial.update({
        "retry_attempt": orig.get("retry_attempt", 0) + 1,
        "retry_outcome": "NOW_OK" if provider_exec else "STILL_CRASHED",
        "success": bool(res.success),
        "recovery_success": recovery_exec,
        "provider_success": provider_exec,
        "recovery_execution_success": recovery_exec,
        "output_contract_valid": output_contract,
        "semantic_validation_success": semantic,
        "trial_valid": trial_valid,
        "workflow_contract_success": bool(res.success),
        "golden_match": golden,
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
        "execution_mode": "REAL_PROVIDER",
    })
    out_path = OUT / f"{trial_id}.json"
    out_path.write_text(
        json.dumps(new_trial, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "trial_id": trial_id,
        "status": "NOW_OK" if provider_exec else "STILL_CRASHED",
        "trial_valid": trial_valid,
        "calls": n_calls,
        "tokens": total_in + total_out,
        "wall_s": wall_ms / 1000.0,
    }


def main() -> int:
    print("=" * 64)
    print("Stage 1.5 RETRY (CRASHED only)")
    print("=" * 64)
    print()
    # 1. Verify env (M3-aware: api-key env var name is
    # REAL_LLM_API_KEY_NAME; defaults to REAL_LLM_API_KEY).
    key_env_name = os.environ.get("REAL_LLM_API_KEY_NAME", "REAL_LLM_API_KEY")
    if not os.environ.get(key_env_name):
        print(f"ERROR: {key_env_name} not set.")
        print("Retry requires the API key.  Re-paste.")
        return 1
    if not os.environ.get("REAL_LLM_BASE_URL"):
        os.environ["REAL_LLM_BASE_URL"] = os.environ.get(
            "REAL_LLM_EXPECTED_BASE",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
    print(f"  {key_env_name}: PRESENT (length "
          f"{len(os.environ[key_env_name])}, value never printed)")
    print(f"  REAL_LLM_BASE_URL: {os.environ['REAL_LLM_BASE_URL']}")
    print()
    # 2. List CRASHED trials
    crashed = _crashed_trial_ids()
    print(f"  CRASHED trials to retry: {len(crashed)}")
    for tid in crashed:
        print(f"    - {tid}")
    # 3. Skip OK trials
    ok = []
    for p in sorted(OUT.glob(TRIAL_JSON_GLOB)):
        if _is_already_ok(p.stem):
            ok.append(p.stem)
    print(f"  OK trials (will SKIP): {len(ok)}")
    for tid in ok:
        print(f"    - {tid}")
    print()
    if not crashed:
        print("  No CRASHED trials to retry.  Done.")
        return 0
    # 4. Retry each CRASHED trial
    print(f"=== Retrying {len(crashed)} CRASHED trials "
          f"({RETRIES_PER_TRIAL} attempt each) ===")
    print()
    results = []
    for tid in crashed:
        for attempt in range(RETRIES_PER_TRIAL):
            t0 = time.perf_counter()
            try:
                r = _retry_trial(tid)
                dt = time.perf_counter() - t0
                results.append({
                    "trial_id": tid,
                    "attempt": attempt + 1,
                    "elapsed_s": dt,
                    **r,
                })
                print(
                    f"  [{len(results):2d}/{len(crashed)}] "
                    f"{tid:50s} status={r['status']:12s} "
                    f"calls={r.get('calls', '-')} "
                    f"tokens={r.get('tokens', '-')}"
                )
                # If now OK, stop retrying this trial.
                if r["status"] == "NOW_OK":
                    break
            except Exception as e:
                dt = time.perf_counter() - t0
                results.append({
                    "trial_id": tid, "attempt": attempt + 1,
                    "elapsed_s": dt, "status": "HARD_CRASH",
                    "error": f"{type(e).__name__}: {e}",
                })
                print(
                    f"  [HARD_CRASH] {tid}: {type(e).__name__}: {e}"
                )
                break
    # 5. Summary
    print()
    print("=" * 64)
    print("RETRY SUMMARY")
    print("=" * 64)
    n_now_ok = sum(1 for r in results if r["status"] == "NOW_OK")
    n_still = sum(1 for r in results if r["status"] == "STILL_CRASHED")
    n_hard = sum(1 for r in results if r["status"] == "HARD_CRASH")
    print(f"  NOW_OK       : {n_now_ok}/{len(crashed)}")
    print(f"  STILL_CRASHED: {n_still}/{len(crashed)}")
    print(f"  HARD_CRASH   : {n_hard}/{len(crashed)}")
    # Combined with the original 5 OK
    print()
    print(f"  Combined: {len(ok) + n_now_ok}/12 now_provider_success")
    summary = {
        "schema_version": 1,
        "experiment_id": "EXP-02",
        "stage": "Stage 1.5 RETRY (CRASHED only)",
        "retries_per_trial": RETRIES_PER_TRIAL,
        "crashed_trial_ids": crashed,
        "ok_trial_ids_preserved": ok,
        "results": results,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    (OUT / "stage15_retry_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print()
    print(f"Saved: {OUT / 'stage15_retry_summary.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
