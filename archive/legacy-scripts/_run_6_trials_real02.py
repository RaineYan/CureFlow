"""Day 14A REAL02 runner — re-run 6 trials with
CORRECTED endpoint (REAL_LLM_BASE_URL=dashscope).

Trial ID strategy (DO NOT overwrite existing forensic):
  - R3 (case_a FULL) -> R4 (replacement of R3) — R3
    failure audit is preserved as forensic evidence.
  - 5 remaining trials -> *_real02 (sibling of
    *_real01; real01 failure audit is preserved).

Set REAL_LLM_BASE_URL before running.  Paste Qwen key
when prompted (NOT printed, NOT written to disk).
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
    print("=" * 64)
    print("Day 14A REAL02 runner (corrected endpoint)")
    print("=" * 64)
    print()

    # 1. Set endpoint FIRST
    expected_base = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    actual_base = os.environ.get("REAL_LLM_BASE_URL", "")
    if actual_base.rstrip("/") != expected_base.rstrip("/"):
        print("REAL_LLM_BASE_URL is NOT set to the Qwen endpoint.")
        print()
        print("Run this FIRST in your PowerShell session:")
        print(f'  $env:REAL_LLM_BASE_URL = "{expected_base}"')
        print()
        print("OR (single-line run):")
        print(f'  $env:REAL_LLM_BASE_URL = "{expected_base}"; python _run_6_trials_real02.py')
        print()
        return 1
    print(f"REAL_LLM_BASE_URL = {actual_base}")
    print("(target: Qwen DashScope compatible-mode/v1)")
    print()

    # 2. Set key
    if not os.environ.get("REAL_LLM_API_KEY"):
        print("REAL_LLM_API_KEY is NOT set.")
        print()
        print("Either set it in PowerShell or paste it now.")
        try:
            key = input("        Paste Qwen key: ").strip()
        except EOFError:
            print("ABORT: no stdin available.")
            return 1
        if not key:
            print("ABORT: empty key.")
            return 1
        os.environ["REAL_LLM_API_KEY"] = key
        print(f"  [OK] key set (length {len(key)} chars, NEVER printed)")
    else:
        print(f"REAL_LLM_API_KEY is PRESENT (length "
              f"{len(os.environ['REAL_LLM_API_KEY'])} chars, "
              f"value NEVER printed)")
    print()

    # 3. Pre-flight
    from experiments.day14 import (
        FROZEN_PROVIDER_CONFIG,
        build_trial_spec,
        run_trial,
        verify_frozen_evidence_unchanged,
    )
    from experiments.day14.recording import (
        Day14RecordingClient,
        derive_5_layer_provider_state,
        derive_replacement_reason,
    )
    from experiments.day14.pilot_runner import (
        next_replacement_id,
        root_trial_id_of,
    )
    from experiments.live_ledger import LiveCallLedger
    from llm import RealLLMProviderConfig
    from llm.providers import RealLLMClient

    fr = verify_frozen_evidence_unchanged()
    if not fr["passed"]:
        print("STOP: Day 13 frozen evidence mutated.")
        return 1
    print(f"[pre-flight] OK: {len(fr['checked_files'])} frozen files intact")
    print()

    # 4. RealLLMClient
    cfg = RealLLMProviderConfig(
        provider=FROZEN_PROVIDER_CONFIG.provider,
        model=FROZEN_PROVIDER_CONFIG.model,
        base_url=expected_base,  # CRITICAL: pin to Qwen
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
    print(f"RealLLMClient built: provider={cfg.provider} model={cfg.model}")
    print(f"  base_url = {cfg.base_url}")
    print()

    pilot = ROOT / "experiments/results/day14/pilot"

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

    def _write_trial_json_with_5layer(
        spec, res, rec, replacement_of=""
    ):
        d = res.to_dict()
        if replacement_of:
            d["replaces_trial_id"] = replacement_of
            d["replacement_trial_id"] = spec.trial_id
            d["root_trial_id"] = root_trial_id_of(replacement_of)
            prev = pilot / f"{replacement_of}_failure_audit.json"
            if prev.exists():
                pa = json.loads(prev.read_text(encoding="utf-8"))
                d["previous_failure_class"] = pa.get(
                    "failure_class", "INFRA_FAILURE"
                )
                d["previous_failure_subtype"] = pa.get(
                    "failure_subtype", "NETWORK_ERROR_TIMEOUT"
                )
            else:
                d["previous_failure_class"] = "INFRA_FAILURE"
                d["previous_failure_subtype"] = "NETWORK_ERROR_TIMEOUT"
            d["replacement_reason"] = derive_replacement_reason(
                previous_failure_class=d["previous_failure_class"],
                previous_failure_subtype=d["previous_failure_subtype"],
            )
        if hasattr(rec, "real_http_roundtrip_verified"):
            d["provider_verified"] = rec.provider_verified
            d["real_http_roundtrip_verified"] = (
                rec.real_http_roundtrip_verified
            )
            d["provider_authenticated"] = rec.provider_authenticated
            eus = {}
            for cap in rec.response_captures:
                eus[cap.get("execution_unit", "unknown")] = {
                    "request_attempted": True,
                    "response_received": cap.get(
                        "response_received", False
                    ),
                    "provider_verified": bool(
                        cap.get("request_id")
                    ) and cap.get("usage_type") == "provider_reported",
                    "schema_valid": bool(
                        cap.get("parsed_top_level_keys")
                    ),
                    "evidence_valid": None,
                    "semantic_valid": None,
                    "artifact_materialized": False,
                }
            d["execution_unit_states"] = eus
        out = pilot / f"{spec.trial_id}.json"
        out.write_text(
            json.dumps(d, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return out

    def _write_failure_audit(spec, res, rec, replacement_of=""):
        if res.success:
            return None
        last_cap = (
            rec.response_captures[-1]
            if rec.response_captures else None
        )
        if last_cap is None:
            return None
        state = derive_5_layer_provider_state(
            response_received=bool(last_cap.get("response_received")),
            provider=last_cap.get("provider"),
            model=last_cap.get("model"),
            request_id=last_cap.get("request_id"),
            usage_type=last_cap.get("usage_type"),
        )
        msg = last_cap.get("sanitized_assistant_content", "") or ""
        if "HTTP 401" in msg or "Incorrect API key" in msg:
            sub = "HTTP_401_AUTH_ERROR"
        elif "WinError 10060" in msg:
            sub = "NETWORK_ERROR_TIMEOUT"
        elif "WinError 10061" in msg:
            sub = "NETWORK_ERROR_CONNECTION_REFUSED"
        else:
            sub = "UNKNOWN_REAL_FAILURE"
        audit = {
            "schema_version": 1,
            "trial_id": spec.trial_id,
            "case_id": spec.case_id,
            "strategy": spec.strategy,
            "replaces_trial_id": replacement_of or None,
            "replacement_trial_id": spec.trial_id if replacement_of else None,
            "replacement_reason": (
                derive_replacement_reason(
                    previous_failure_class="INFRA_FAILURE",
                    previous_failure_subtype=sub,
                )
                if replacement_of else None
            ),
            "valid_trial": False,
            "retry_permitted": False,
            "replacement_required": True,
            "evidence_source": str(
                (pilot / f"{spec.trial_id}_live_ledger.jsonl")
                .relative_to(ROOT)
            ).replace("\\", "/"),
            "execution_unit": last_cap.get("execution_unit"),
            "request_attempted": True,
            "response_received": bool(last_cap.get("response_received")),
            "real_http_roundtrip_verified":
                state["real_http_roundtrip_verified"],
            "provider_identity_verified":
                state["provider_identity_verified"],
            "provider_authenticated":
                state["provider_authenticated"],
            "provider_request_succeeded":
                state["provider_request_succeeded"],
            "provider_verified":
                state["provider_verified"],
            "actual_provider_http_requests": len(rec.response_captures),
            "successful_provider_requests": int(
                state["provider_request_succeeded"]
            ),
            "successful_artifact_materializations": len(
                res.new_llm_artifact_refs
            ),
            "last_response_capture": last_cap,
            "failure_class": "INFRA_FAILURE",
            "failure_subtype": sub,
        }
        if replacement_of:
            prev = pilot / f"{replacement_of}_failure_audit.json"
            if prev.exists():
                pa = json.loads(prev.read_text(encoding="utf-8"))
                audit["previous_failure_class"] = pa.get(
                    "failure_class", "INFRA_FAILURE"
                )
                audit["previous_failure_subtype"] = pa.get(
                    "failure_subtype", "NETWORK_ERROR_TIMEOUT"
                )
        out = pilot / f"{spec.trial_id}_failure_audit.json"
        out.write_text(
            json.dumps(audit, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return out

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
        t0 = time.perf_counter()
        try:
            res = run_trial(spec)
        except Exception as e:
            print(f"  EXCEPTION: {type(e).__name__}: {e}")
            return None, None, None
        dt = time.perf_counter() - t0
        out = _write_trial_json_with_5layer(
            spec, res, rec, replacement_of
        )
        if not res.success:
            _write_failure_audit(
                spec, res, rec, replacement_of
            )
        return res, out, dt

    # 5. Run R4 (replacement of R3, which failed NETWORK_ERROR)
    r4_id = next_replacement_id(
        pilot, "case_a_full_rerun_trial01"
    )
    print(f"=== [1/6] R4: {r4_id} (replacement of case_a_full_rerun_trial01R3) ===")
    t0 = time.perf_counter()
    res, out, dt = _run_trial(
        "case_a", "FULL_RERUN", r4_id,
        replacement_of="case_a_full_rerun_trial01R3",
    )
    print(f"  dt={dt:.1f}s  success={res.success if res else 'EXC'}")
    if res:
        print(f"  calls={res.real_metrics.real_llm_call_count}  "
              f"tokens={res.real_metrics.provider_reported_token_total}")
        if not res.success:
            print(f"  failure_reason: {(res.failure_reason or '')[:200]}")
    print()

    # 6. Run 5 remaining (sibling _real02)
    remaining = [
        ("case_a", "COARSE_CHECKPOINT"),
        ("case_a", "SELECTIVE_RECOVERY"),
        ("case_b", "FULL_RERUN"),
        ("case_b", "COARSE_CHECKPOINT"),
        ("case_b", "SELECTIVE_RECOVERY"),
    ]
    for case, strategy in remaining:
        trial_id = f"{case}_{strategy.lower()}_trial01_real02"
        print(f"=== trial: {trial_id} ===")
        t0 = time.perf_counter()
        res, out, dt = _run_trial(case, strategy, trial_id)
        print(f"  dt={dt:.1f}s  success={res.success if res else 'EXC'}")
        if res:
            print(f"  calls={res.real_metrics.real_llm_call_count}  "
                  f"tokens={res.real_metrics.provider_reported_token_total}")
            if not res.success:
                print(f"  failure_reason: "
                      f"{(res.failure_reason or '')[:200]}")
        print()

    # 7. Save runner report
    report = {
        "schema_version": 1,
        "experiment": "DAY14_REAL02",
        "endpoint_used": expected_base,
        "frozen_evidence_intact": True,
        "note": (
            "6 trials run with corrected endpoint "
            "(REAL_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1).  "
            "R3 (failed NETWORK_ERROR) is preserved as forensic; R4 is its "
            "replacement.  5 trial _real01 (failed NETWORK_ERROR) are "
            "preserved; _real02 are their siblings."
        ),
    }
    (ROOT / "experiments/results/day14/real02_runner_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Runner report: experiments/results/day14/real02_runner_report.json")
    print()

    # 8. Cleanup
    os.environ.pop("REAL_LLM_API_KEY", None)
    print("REAL_LLM_API_KEY removed from process env.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
