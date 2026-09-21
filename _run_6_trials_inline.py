"""Day 14A R3 + 5-trial inline runner.

Sets REAL_LLM_API_KEY in this process, then runs 6 trials
in-process (NOT subprocess) so the env var is guaranteed
visible.  All trial JSONs go to experiments/results/day14/pilot/.

USAGE: paste your key when prompted, then trials run.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    # 1. Set key (value already in this call's parameter list;
    #    we never print it, only the length).
    key = "sk-ws-H.EPPMEHE.MqJl.MEUCIQCvUknV_zPiQDwy9cWiVchPrZcXxJ4y35hi8Op8V6rYvQIgFeBgEZxNi3aLef50n4LY6obAz2EopJ91UGO5k7qbr4U"
    os.environ["REAL_LLM_API_KEY"] = key
    print("=" * 64)
    print("INLINE R3 + 5-TRIAL RUNNER")
    print("=" * 64)
    print(f"REAL_LLM_API_KEY length: {len(os.environ['REAL_LLM_API_KEY'])}")
    print(f"  starts with sk-: {os.environ['REAL_LLM_API_KEY'].startswith('sk-')}")
    print("(value NEVER printed)")
    print()

    # 2. Pre-flight: Day 13 frozen evidence
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

    # 3. Build trial specs
    from experiments.day14 import (
        FROZEN_PROVIDER_CONFIG,
        build_trial_spec,
        run_trial,
    )
    from experiments.day14.recording import (
        Day14RecordingClient,
    )
    from experiments.day14.pilot_runner import (
        is_replacement_id_occupied,
        next_replacement_id,
        root_trial_id_of,
    )
    from experiments.live_ledger import LiveCallLedger
    from llm import RealLLMProviderConfig
    from llm.providers import RealLLMClient

    pilot = ROOT / "experiments/results/day14/pilot"

    # 4. RealLLMClient config
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
    real_client = RealLLMClient(cfg)
    print(f"RealLLMClient built: provider={cfg.provider}, model={cfg.model}")
    print()

    def _build_recording_client(trial_id, case_id, strategy):
        ledger = LiveCallLedger()
        phase_prefix = (
            "A" if strategy == "FULL_RERUN"
            else "B" if strategy == "COARSE_CHECKPOINT"
            else "C-recovery"
        )
        persistence_path = pilot / f"{trial_id}_live_ledger.jsonl"
        return Day14RecordingClient(
            real_client, ledger,
            phase_prefix=phase_prefix,
            trial_id=trial_id, case_id=case_id, strategy=strategy,
            persistence_path=persistence_path,
        )

    def _run_trial(case, strategy, trial_id, replacement_of=""):
        spec = build_trial_spec(
            case_id=case, strategy=strategy,
            trial_id=trial_id, out_dir=pilot,
            replaces_trial_id=replacement_of or None,
        )
        rec = _build_recording_client(
            trial_id=spec.trial_id,
            case_id=spec.case_id,
            strategy=spec.strategy,
        )
        spec.real_client = rec
        spec.execution_mode = "REAL_PROVIDER"
        # chain metadata (R3 only)
        if replacement_of:
            spec.root_trial_id = root_trial_id_of(replacement_of)
            # Read previous audit for replacement_reason
            prev_audit_path = pilot / f"{replacement_of}_failure_audit.json"
            if prev_audit_path.exists():
                prev_audit = json.loads(prev_audit_path.read_text(
                    encoding="utf-8"
                ))
                spec.previous_failure_class = prev_audit.get(
                    "failure_class", "UNCONFIRMED_PROVIDER_EXECUTION_FAILURE"
                )
                spec.previous_failure_subtype = prev_audit.get(
                    "failure_subtype", "CLAIM_SCHEMA_FAILURE_OBSERVED"
                )
            else:
                spec.previous_failure_class = (
                    "UNCONFIRMED_PROVIDER_EXECUTION_FAILURE"
                )
                spec.previous_failure_subtype = (
                    "CLAIM_SCHEMA_FAILURE_OBSERVED"
                )
            from experiments.day14.recording import (
                derive_replacement_reason,
            )
            spec.replacement_reason = derive_replacement_reason(
                previous_failure_class=spec.previous_failure_class,
                previous_failure_subtype=spec.previous_failure_subtype,
            )

        t0 = time.perf_counter()
        try:
            res = run_trial(spec)
        except Exception as e:
            print(f"  EXCEPTION: {type(e).__name__}: {e}")
            return None
        dt = time.perf_counter() - t0

        # Write trial JSON
        out_path = pilot / f"{spec.trial_id}.json"
        d = res.to_dict()
        if replacement_of:
            d["replaces_trial_id"] = replacement_of
            d["replacement_trial_id"] = spec.trial_id
            d["root_trial_id"] = spec.root_trial_id
            d["previous_failure_class"] = spec.previous_failure_class
            d["previous_failure_subtype"] = spec.previous_failure_subtype
            d["replacement_reason"] = spec.replacement_reason
        out_path.write_text(
            json.dumps(d, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        # 5-layer state
        if hasattr(rec, "real_http_roundtrip_verified"):
            d["provider_verified"] = rec.provider_verified
            d["real_http_roundtrip_verified"] = (
                rec.real_http_roundtrip_verified
            )
            d["provider_authenticated"] = rec.provider_authenticated
            d["execution_unit_states"] = {
                cap.get("execution_unit", "unknown"): {
                    "request_attempted": True,
                    "response_received": cap.get(
                        "response_received", False
                    ),
                    "provider_verified": bool(
                        cap.get("request_id")
                    ) and cap.get("usage_type") == "provider_reported",
                    "schema_valid": cap.get("parsed_top_level_keys")
                    is not None,
                    "evidence_valid": None,
                    "semantic_valid": None,
                    "artifact_materialized": False,
                }
                for cap in rec.response_captures
            }
            out_path.write_text(
                json.dumps(d, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        # Failure audit on failure
        if not res.success:
            audit_path = pilot / f"{spec.trial_id}_failure_audit.json"
            from experiments.day14.recording import (
                derive_5_layer_provider_state,
            )
            last_cap = (
                rec.response_captures[-1]
                if rec.response_captures else None
            )
            state_5 = derive_5_layer_provider_state(
                response_received=bool(
                    last_cap and last_cap.get("response_received")
                ),
                provider=(
                    last_cap.get("provider") if last_cap else None
                ),
                model=(
                    last_cap.get("model") if last_cap else None
                ),
                request_id=(
                    last_cap.get("request_id") if last_cap else None
                ),
                usage_type=(
                    last_cap.get("usage_type") if last_cap else None
                ),
            )
            audit = {
                "schema_version": 1,
                "trial_id": spec.trial_id,
                "case_id": spec.case_id,
                "strategy": spec.strategy,
                "replaces_trial_id": (
                    replacement_of if replacement_of else None
                ),
                "replacement_trial_id": (
                    spec.trial_id if replacement_of else None
                ),
                "replacement_reason": (
                    spec.replacement_reason if replacement_of else None
                ),
                "previous_failure_class": (
                    spec.previous_failure_class if replacement_of else None
                ),
                "previous_failure_subtype": (
                    spec.previous_failure_subtype if replacement_of else None
                ),
                "valid_trial": False,
                "failure_reason": res.failure_reason,
                "real_http_roundtrip_verified":
                    state_5["real_http_roundtrip_verified"],
                "provider_identity_verified":
                    state_5["provider_identity_verified"],
                "provider_authenticated":
                    state_5["provider_authenticated"],
                "provider_request_succeeded":
                    state_5["provider_request_succeeded"],
                "provider_verified":
                    state_5["provider_verified"],
                "actual_provider_http_requests": (
                    len(rec.response_captures)
                ),
                "successful_provider_requests": int(
                    state_5["provider_request_succeeded"]
                ),
                "successful_artifact_materializations": len(
                    res.new_llm_artifact_refs
                ),
                "last_response_capture": last_cap,
            }
            audit_path.write_text(
                json.dumps(audit, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return res

    # 5. Run 6 trials
    results = []
    # 5.1 R3 (case_a FULL, replacement of R2)
    r3_id = next_replacement_id(pilot, "case_a_full_rerun_trial01")
    print(f"=== [1/6] R3: {r3_id} (replacement of trial01R2) ===")
    t0 = time.perf_counter()
    res = _run_trial("case_a", "FULL_RERUN", r3_id,
                     replacement_of="case_a_full_rerun_trial01R2")
    dt = time.perf_counter() - t0
    if res:
        results.append({
            "trial_id": res.trial_id, "case": res.case_id,
            "strategy": res.strategy, "success": res.success,
            "failure_reason": res.failure_reason,
            "real_llm_call_count": res.real_metrics.real_llm_call_count,
            "provider_reported_token_total":
                res.real_metrics.provider_reported_token_total,
            "recovery_wall_clock_ms": res.recovery_wall_clock_ms,
            "elapsed_s": dt,
        })
        print(f"  success={res.success}  rc={dt:.1f}s  "
              f"calls={res.real_metrics.real_llm_call_count}  "
              f"tokens={res.real_metrics.provider_reported_token_total}")
        if not res.success:
            print(f"  failure_reason: {res.failure_reason[:200]}")
    else:
        results.append({
            "trial_id": r3_id, "case": "case_a", "strategy": "FULL_RERUN",
            "success": False, "elapsed_s": dt,
        })
    print()

    # 5.2 - 5.6  Remaining 5 trials
    remaining = [
        ("case_a", "COARSE_CHECKPOINT"),
        ("case_a", "SELECTIVE_RECOVERY"),
        ("case_b", "FULL_RERUN"),
        ("case_b", "COARSE_CHECKPOINT"),
        ("case_b", "SELECTIVE_RECOVERY"),
    ]
    for case, strategy in remaining:
        trial_id = f"{case}_{strategy.lower()}_trial01_real01"
        print(f"=== trial: {trial_id} ===")
        t0 = time.perf_counter()
        res = _run_trial(case, strategy, trial_id)
        dt = time.perf_counter() - t0
        if res:
            results.append({
                "trial_id": res.trial_id, "case": res.case_id,
                "strategy": res.strategy, "success": res.success,
                "failure_reason": res.failure_reason,
                "real_llm_call_count": res.real_metrics.real_llm_call_count,
                "provider_reported_token_total":
                    res.real_metrics.provider_reported_token_total,
                "recovery_wall_clock_ms": res.recovery_wall_clock_ms,
                "elapsed_s": dt,
            })
            print(f"  success={res.success}  rc={dt:.1f}s  "
                  f"calls={res.real_metrics.real_llm_call_count}  "
                  f"tokens={res.real_metrics.provider_reported_token_total}")
            if not res.success:
                print(f"  failure_reason: "
                      f"{(res.failure_reason or '')[:200]}")
        else:
            results.append({
                "trial_id": trial_id, "case": case, "strategy": strategy,
                "success": False, "elapsed_s": dt,
            })
        print()

    # 6. Save runner report
    report_path = ROOT / "experiments/results/day14/runner_report.json"
    report_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Runner report: {report_path}")
    print()

    # 7. Clean up: remove key from env
    os.environ.pop("REAL_LLM_API_KEY", None)
    print("REAL_LLM_API_KEY removed from process env.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
