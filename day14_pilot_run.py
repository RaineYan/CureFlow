"""Day 14 Pilot — user-side CLI runner.

This CLI runs ONE pilot trial at a time and writes
the trial JSON to experiments/results/day14/pilot/.

Usage (user-side, in PowerShell where the API key is
set as an environment variable):

  # Step 0: capture the Day 13 baseline (one-time, mandatory)
  python _day14_capture_baseline.py

  # Step 1: set the API key — the CANONICAL name is
  #         REAL_LLM_API_KEY (same as live_gate_run.py)
  $env:REAL_LLM_API_KEY = "<your real Qwen key>"

  # Step 2: run a single trial
  python day14_pilot_run.py --case case_a --strategy FULL_RERUN
  python day14_pilot_run.py --case case_a --strategy COARSE_CHECKPOINT
  python day14_pilot_run.py --case case_a --strategy SELECTIVE_RECOVERY
  python day14_pilot_run.py --case case_b --strategy FULL_RERUN
  python day14_pilot_run.py --case case_b --strategy COARSE_CHECKPOINT
  python day14_pilot_run.py --case case_b --strategy SELECTIVE_RECOVERY

  # Step 3: build the summary table
  python _day14_pilot_summary.py

  # Step 4: write the manifest
  python _day14_pilot_manifest.py

The CLI uses the frozen provider config and runs
the 6 pilot trials serially.  Each trial writes to
its own tmpdir store so the three strategies never
share a store.

NEVER pass --api-key on the command line.  The real
client reads it from REAL_LLM_API_KEY in your
PowerShell session.  The key is NEVER written to
disk, NEVER logged in full (only PRESENT/MISSING +
last-4-chars are surfaced in error messages).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from experiments.day14 import (
    FROZEN_PROVIDER_CONFIG,
    TrialSpec,
    TrialResult,
    assert_frozen_evidence_intact,
    assert_provider_config,
    build_trial_spec,
    run_trial,
    write_trial_result,
)
from experiments.live_ledger import LiveCallLedger, LiveCallRecord


# CANONICAL env var name.  The single source of truth —
# matches live_gate_run.py.  No other names are accepted.
CANONICAL_API_KEY_ENV_VAR = "REAL_LLM_API_KEY"


# =====================================================================
# Failure audit writer
# =====================================================================

def _write_failure_audit(
    *,
    audit_path: Path,
    res: "TrialResult",
    spec: "TrialSpec",
    exception_type: Optional[str],
    exception_message: Optional[str],
) -> None:
    """Persist a failure audit JSON next to the trial
    JSON.  Contains: trial_id, replaces_trial_id,
    failure_class/subtype/stage, exception details, real
    metrics (preserved even on exception), and the
    sanitized response of the LAST real request (if
    the recording client captured one).

    Sensitive fields (api_key, Authorization, Bearer)
    are NEVER written.
    """
    # Harvest the last record (if any) for forensic
    last_record = None
    if spec.real_client is not None and hasattr(
        spec.real_client, "records"
    ):
        records = spec.real_client.records
        if records:
            last_record = records[-1]
    # Classify
    if exception_type is not None:
        if "schema" in (exception_message or "").lower() or \
                "claim_text" in (exception_message or "") or \
                "evidence_refs" in (exception_message or ""):
            failure_class = (
                "REAL_MODEL_OUTPUT_VALIDATION_FAILURE"
            )
            failure_subtype = "CLAIM_SCHEMA_FAILURE"
        elif "RealLLM" in exception_type:
            failure_class = "INFRA_FAILURE"
            failure_subtype = exception_type
        else:
            failure_class = "UNEXPECTED_EXCEPTION"
            failure_subtype = exception_type
    else:
        failure_class = "VALIDATION_FAILURE"
        failure_subtype = (
            "see res.failure_reason"
        )
    # Build sanitized response excerpt
    sanitized_excerpt: Optional[str] = None
    parsed_keys: List[str] = []
    raw_shape: Dict[str, Any] = {}
    if last_record is not None:
        # We don't have the raw response text on the
        # LiveCallRecord, but we know the execution_unit,
        # provider, model, request_id, etc.  If the
        # response is captured, expose its top-level keys.
        if hasattr(last_record, "response_text"):
            text = getattr(last_record, "response_text") or ""
            sanitized_excerpt = text[:500]
            try:
                import json as _json
                parsed = _json.loads(text)
                if isinstance(parsed, dict):
                    parsed_keys = sorted(parsed.keys())
                    raw_shape = {
                        "kind": "json_object",
                        "top_level_keys": parsed_keys,
                    }
            except Exception:
                raw_shape = {
                    "kind": "non_json",
                    "len": len(text),
                }

    # ---- 5-LAYER PROVIDER STATE (PRE-R3 SEMANTICS FIX) ----
    # Derive the 5-layer state from the last_record (if
    # any).  A single `provider_verified` boolean
    # conflates "real HTTP roundtrip happened" with
    # "Provider returned a successful inference response".
    # The 5 layers separate these concerns.  Use the
    # canonical derivation from
    # experiments.day14.recording.
    state_5 = {
        "real_http_roundtrip_verified": False,
        "provider_identity_verified": False,
        "provider_authenticated": False,
        "provider_request_succeeded": False,
        "provider_verified": False,
    }
    if last_record is not None:
        from experiments.day14.recording import (
            derive_5_layer_provider_state,
        )
        state_5 = derive_5_layer_provider_state(
            response_received=bool(
                getattr(last_record, "response_received", False)
            ),
            provider=getattr(last_record, "provider", None),
            model=getattr(last_record, "model", None),
            request_id=getattr(last_record, "request_id", None),
            usage_type=getattr(last_record, "usage_type", None),
        )

    # Request count vs materialization count
    actual_provider_http_requests = int(
        res.real_metrics.real_llm_call_count
    ) if res.real_metrics is not None else 0
    successful_provider_requests = int(
        state_5["provider_request_succeeded"]
    )
    successful_artifact_materializations = len(
        res.new_llm_artifact_refs
    )

    audit = {
        "schema_version": 1,
        "trial_id": res.trial_id,
        "case_id": res.case_id,
        "strategy": res.strategy,
        "replaces_trial_id": res.replaces_trial_id,
        "replacement_trial_id": res.replacement_trial_id,
        "replacement_reason": res.replacement_reason,
        "valid_trial": False,
        "replacement_required": False,
        "retry_permitted": False,
        "failure_class": failure_class,
        "failure_subtype": failure_subtype,
        "failure_stage": (
            "exception_raised" if exception_type else "validation"
        ),
        "execution_unit": (
            last_record.execution_unit if last_record else None
        ),
        "provider_request_occurred": last_record is not None,
        "artifact_materialized": (
            len(res.new_llm_artifact_refs) > 0
        ),
        "actual_real_provider_requests":
            res.real_metrics.real_llm_call_count,
        "successful_llm_artifact_materializations":
            len(res.new_llm_artifact_refs),
        # ---- 5-LAYER PROVIDER STATE (PRE-R3 SEMANTICS FIX) ----
        # The single `provider_verified` boolean is too
        # coarse — it conflates "real HTTP roundtrip to
        # the Provider happened" with "the Provider
        # returned a successful inference response".
        # We decompose the state into 5 layers, plus
        # the legacy alias for back-compat.
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
        "actual_provider_http_requests":
            actual_provider_http_requests,
        "successful_provider_requests":
            successful_provider_requests,
        "successful_artifact_materializations":
            successful_artifact_materializations,
        "last_record_summary": (
            {
                "execution_unit":
                    last_record.execution_unit,
                "provider": last_record.provider,
                "model": last_record.model,
                "request_id": last_record.request_id,
                "usage_type": last_record.usage_type,
                "input_tokens": last_record.input_tokens,
                "output_tokens": last_record.output_tokens,
                "total_tokens": last_record.total_tokens,
                "reasoning_tokens": last_record.reasoning_tokens,
                "client_wall_clock_latency_ms":
                    last_record.client_wall_clock_latency_ms,
                "is_replayed": last_record.is_replayed,
                "schema_valid": last_record.schema_valid,
                "evidence_valid": last_record.evidence_valid,
                "semantic_valid": last_record.semantic_valid,
            }
            if last_record is not None else None
        ),
        "raw_response_shape": raw_shape,
        "parsed_top_level_keys": parsed_keys,
        "sanitized_response_excerpt": sanitized_excerpt,
        "schema_issues": (
            [exception_message] if exception_type
            and "schema" in (exception_message or "").lower()
            else []
        ),
        "exception_type": exception_type,
        "exception_message_sanitized": (
            # Strip any accidental api-key / Bearer token
            re.sub(
                r"(sk-[A-Za-z0-9]{8,})|"
                r"(Bearer [A-Za-z0-9._-]{8,})|"
                r"(Authorization: [^\n]+)",
                "<redacted>",
                exception_message or "",
            )
            if exception_message else None
        ),
        "real_metrics": res.real_metrics.to_dict(),
        "no_api_key": True,
        "audit_notes": (
            "Day 14A FORENSIC: real Provider request may "
            "have occurred (if request_id starts with "
            "'chatcmpl-').  The real_llm_call_count is "
            "preserved even when downstream parsing fails."
        ),
    }
    # NB: do NOT add `import re` here.  Python's
    # compile-time scope analysis would treat `re` as
    # a local variable for the entire function, causing
    # the earlier `re.sub(...)` (in the
    # exception_message_sanitized branch) to raise
    # UnboundLocalError.  The top-of-module `import re`
    # is already in place.
    audit_path.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# Expected logical LLM execution units for a single
# FULL_RERUN trial.  These are the units the workflow
# RUNS, regardless of whether each unit succeeds at
# the Provider level.  The real HTTP success count
# comes from the LiveCallLedger, NOT from this list.
EXPECTED_FULL_TRIAL_LLM_UNITS: tuple = (
    "generate_financial_claim",
    "generate_user_claim",
    "generate_operation_claim",
    "generate_financial_section",
    "generate_user_section",
    "generate_operation_section",
)
# Sanity expectation: 6 logical LLM units per FULL/COARSE
# trial, 2 per SELECTIVE trial.  These are the
# EXPECTED COUNTS — actual real HTTP counts must come
# from the LiveCallLedger.


def _maybe_build_real_recording_client(trial_id: str, case_id: str, strategy: str):
    """If the canonical env var is set, return a
    Day14RecordingClient wrapping a RealLLMClient.  The
    recording client auto-appends to a LiveCallLedger
    and threads the inner client through the strategy.

    Returns None otherwise (offline dry run)."""
    if not os.environ.get(CANONICAL_API_KEY_ENV_VAR):
        return None
    from llm.providers import RealLLMClient
    from llm import RealLLMProviderConfig
    from experiments.live_ledger import LiveCallLedger
    from experiments.day14.recording import (
        Day14RecordingClient,
        assert_not_fake_client,
    )
    # RealLLMClient reads the key from the env var
    # named by config.api_key_env_var.  We never pass
    # the key as a constructor argument.
    # enable_thinking goes into provider_options (Qwen
    # is provider-specific, NOT a top-level field).
    cfg = RealLLMProviderConfig(
        provider=FROZEN_PROVIDER_CONFIG.provider,
        model=FROZEN_PROVIDER_CONFIG.model,
        api_key_env_var=CANONICAL_API_KEY_ENV_VAR,
        response_format=FROZEN_PROVIDER_CONFIG.response_format,
        temperature=FROZEN_PROVIDER_CONFIG.temperature,
        seed=FROZEN_PROVIDER_CONFIG.seed,
        provider_options={
            "enable_thinking": FROZEN_PROVIDER_CONFIG.enable_thinking,
        },
        max_retries=0,  # spec section 19: no auto-retry
    )
    real_client = RealLLMClient(cfg)
    # Guard: the real client must NOT be a FakeLLMClient
    # in disguise.  This is the hard gate that catches
    # the "key present but FakeLLM routed" bug.
    assert_not_fake_client(real_client)
    ledger = LiveCallLedger()
    phase_prefix = (
        "A" if strategy == "FULL_RERUN"
        else "B" if strategy == "COARSE_CHECKPOINT"
        else "C-recovery"
    )
    # Append-safe persistence: every real response
    # gets a JSON line in <trial_id>_live_ledger.jsonl
    # under the pilot dir.  This survives process
    # crash / traceback.
    persistence_path = (
        Path("experiments/results/day14/pilot")
        / f"{trial_id}_live_ledger.jsonl"
    )
    rec = Day14RecordingClient(
        real_client, ledger,
        phase_prefix=phase_prefix,
        trial_id=trial_id, case_id=case_id, strategy=strategy,
        persistence_path=persistence_path,
    )
    return rec


def main() -> int:
    p = argparse.ArgumentParser(
        description=(
            "Day 14 Pilot — run one trial with a frozen "
            "provider config.  Run all 6 trials to "
            "complete the pilot."
        ),
    )
    p.add_argument(
        "--case", required=True,
        choices=("case_a", "case_b"),
    )
    p.add_argument(
        "--strategy", required=True,
        choices=(
            "FULL_RERUN",
            "COARSE_CHECKPOINT",
            "SELECTIVE_RECOVERY",
        ),
    )
    p.add_argument(
        "--out-dir",
        default="experiments/results/day14/pilot",
        help="Directory to write the trial JSON.",
    )
    p.add_argument(
        "--csv",
        default="data/sample_company.csv",
    )
    p.add_argument(
        "--replacement-of",
        default=None,
        help=(
            "If this trial replaces an earlier invalid "
            "trial, pass the original trial_id (e.g. "
            "'case_a_full_rerun_trial01').  The replacement "
            "JSON is written to a NEW file (e.g. "
            "'case_a_full_rerun_trial01R1.json') and "
            "carries replacement_trial_id / "
            "replaces_trial_id / replacement_reason fields.  "
            "The original trial01.json is NEVER overwritten."
        ),
    )
    args = p.parse_args()

    # ---- 0. hard gate: Day 13 frozen evidence must be intact ----
    frozen = assert_frozen_evidence_intact()
    if not frozen["passed"]:
        print(
            "STOP: Day 13 evidence was mutated.  "
            f"Mismatches: {frozen['mismatches']}.  "
            f"Missing: {frozen['missing']}."
        )
        return 1
    print(
        f"OK: Day 13 frozen evidence intact "
        f"({len(frozen['checked_files'])} files)."
    )

    # ---- 1. provider config sanity ----
    pc = assert_provider_config(FROZEN_PROVIDER_CONFIG.to_dict())
    if not pc["passed"]:
        print(f"STOP: provider config mismatch: {pc}")
        return 1
    print(
        "OK: provider config matches frozen spec "
        f"({pc['frozen_config']})."
    )

    # ---- 2. print provider config source (no key value) ----
    api_key_present = bool(
        os.environ.get(CANONICAL_API_KEY_ENV_VAR)
    )
    print("=== Real provider config source ===")
    print(f"  provider          : {FROZEN_PROVIDER_CONFIG.provider}")
    print(f"  model             : {FROZEN_PROVIDER_CONFIG.model}")
    print(f"  base_url          : (read from REAL_LLM_BASE_URL or "
          f"default 'https://dashscope.aliyuncs.com/compatible-mode/v1')")
    print(f"  api_key env var   : {CANONICAL_API_KEY_ENV_VAR}")
    print(f"  api_key status    : "
          f"{'PRESENT' if api_key_present else 'MISSING'}")
    print(f"  enable_thinking   : {FROZEN_PROVIDER_CONFIG.enable_thinking}")
    print(f"  response_format   : {FROZEN_PROVIDER_CONFIG.response_format}")
    print(f"  temperature       : {FROZEN_PROVIDER_CONFIG.temperature}")
    print(f"  seed              : {FROZEN_PROVIDER_CONFIG.seed}")
    print(f"  max_retries       : 0  (spec section 19)")

    # ---- 2.5  print expected LLM units for this trial ----
    if args.strategy in ("FULL_RERUN", "COARSE_CHECKPOINT"):
        units = EXPECTED_FULL_TRIAL_LLM_UNITS
        print()
        print(f"=== Expected LLM units for {args.strategy} ===")
        print(f"  logical count = {len(units)}")
        for u in units:
            print(f"    - {u}")
        print("  (Real HTTP success count comes from "
              "LiveCallLedger, NOT this list.)")
    elif args.strategy == "SELECTIVE_RECOVERY":
        # SELECTIVE: 2 units for Case A (financial claim +
        # financial section) or Case B (operation claim +
        # operation section).  Actual units come from the
        # plan.
        print()
        print("=== Expected LLM units for SELECTIVE_RECOVERY ===")
        print("  logical count = 2  (plan-derived; will be")
        print("  confirmed by the actual plan + ledger)")
        print("  (Real HTTP success count comes from "
              "LiveCallLedger, NOT this list.)")

    # ---- 3. build spec ----
    # If this is a replacement trial, derive the new
    # trial_id and pass `replaces_trial_id` so the file
    # is written to a new path.
    if args.replacement_of:
        # The replacement ID is "occupied" iff ANY of
        # the following evidence artifacts exists for
        # that ID:
        #   - <id>.json                  (trial JSON)
        #   - <id>_failure_audit.json    (failure audit)
        #   - <id>_live_ledger.jsonl     (append-safe ledger)
        #   - <id>_live_ledger.json      (atomic ledger)
        # This protects the chain from silently
        # overwriting R1 (whose main trial JSON is
        # missing but whose failure_audit.json IS on
        # disk, marking R1 as occupied).
        pilot_dir = Path(args.out_dir)
        def _is_occupied(trial_id: str) -> bool:
            return any((
                (pilot_dir / f"{trial_id}.json").exists(),
                (pilot_dir / f"{trial_id}_failure_audit.json").exists(),
                (pilot_dir / f"{trial_id}_live_ledger.jsonl").exists(),
                (pilot_dir / f"{trial_id}_live_ledger.json").exists(),
            ))
        n = 1
        while _is_occupied(f"{args.replacement_of}R{n}"):
            n += 1
        new_trial_id = f"{args.replacement_of}R{n}"
        # The root_trial_id is the deepest non-R ID.
        # For trial01R1, root_trial_id is trial01.
        # For trial01R2, root_trial_id is still trial01
        # (the chain's "trunk" ID, not the immediate
        # parent).
        root_trial_id = args.replacement_of
        while "R" in root_trial_id:
            # Strip the last "R<n>" suffix iteratively.
            idx = root_trial_id.rfind("R")
            # Verify the suffix is a number
            tail = root_trial_id[idx + 1:]
            if tail.isdigit():
                root_trial_id = root_trial_id[:idx]
            else:
                break
        spec = build_trial_spec(
            case_id=args.case,
            strategy=args.strategy,
            trial_id=new_trial_id,
            csv_path=args.csv,
            out_dir=pilot_dir,
            replaces_trial_id=args.replacement_of,
        )
        # Stash the root + chain on the spec so
        # write_trial_result can serialize them.
        spec.root_trial_id = root_trial_id
        # Auto-derive chain metadata from the
        # IMMEDIATE predecessor's failure_audit.json
        # (if available).  The previous failure
        # class/subtype drive the new trial's
        # replacement_reason.  This is the
        # PRE-R3 AUTH SEMANTICS FIX: if the
        # predecessor was INFRA/HTTP_401_AUTH, the
        # next replacement MUST be labeled
        # AUTH_CREDENTIAL_CORRECTION, NOT
        # PRE_RETRY_FORENSIC_HARDENING.
        prev_audit_path = (
            pilot_dir
            / f"{args.replacement_of}_failure_audit.json"
        )
        prev_failure_class = (
            "UNCONFIRMED_PROVIDER_EXECUTION_FAILURE"
        )
        prev_failure_subtype = (
            "CLAIM_SCHEMA_FAILURE_OBSERVED"
        )
        prev_replacement_reason = (
            "PRE_RETRY_FORENSIC_HARDENING"
        )
        if prev_audit_path.exists():
            try:
                prev_audit = json.loads(
                    prev_audit_path.read_text(
                        encoding="utf-8"
                    )
                )
                prev_failure_class = (
                    prev_audit.get(
                        "failure_class",
                        prev_failure_class,
                    )
                )
                prev_failure_subtype = (
                    prev_audit.get(
                        "failure_subtype",
                        prev_failure_subtype,
                    )
                )
            except (OSError, ValueError):
                # If the audit is unreadable, fall
                # back to defaults; the user will
                # see the WARN and inspect manually.
                pass
        spec.previous_failure_class = prev_failure_class
        spec.previous_failure_subtype = prev_failure_subtype
        # Derive replacement_reason from the
        # predecessor's failure class/subtype.  Use
        # the canonical derivation from
        # experiments.day14.recording.
        from experiments.day14.recording import (
            derive_replacement_reason,
        )
        spec.replacement_reason = derive_replacement_reason(
            previous_failure_class=prev_failure_class,
            previous_failure_subtype=prev_failure_subtype,
        )
        print(
            f"  REPLACEMENT of {args.replacement_of} → "
            f"new trial_id={new_trial_id}\n"
            f"  root_trial_id = {root_trial_id}\n"
            f"  previous_failure_class = "
            f"{spec.previous_failure_class}\n"
            f"  previous_failure_subtype = "
            f"{spec.previous_failure_subtype}\n"
            f"  replacement_reason = "
            f"{spec.replacement_reason}"
        )
    else:
        spec = build_trial_spec(
            case_id=args.case,
            strategy=args.strategy,
            csv_path=args.csv,
            out_dir=Path(args.out_dir),
        )
    print(
        f"\ntrial_id={spec.trial_id}  case={spec.case_id}  "
        f"strategy={spec.strategy}  store_dir={spec.store_dir}"
    )

    # ---- 4. decide: real Qwen or FakeLLM ----
    # Pre-run banner uses "REQUESTED" (not "VERIFIED") —
    # only after the first real Provider response lands
    # in the ledger do we know the client is actually
    # being used.
    real_client = _maybe_build_real_recording_client(
        trial_id=spec.trial_id,
        case_id=spec.case_id,
        strategy=spec.strategy,
    )
    if real_client is not None:
        spec.real_client = real_client
        spec.execution_mode = "REAL_PROVIDER"
        print(
            "REAL PROVIDER REQUESTED (env var present, "
            "RealLLMClient built, Day14RecordingClient "
            "wrapping it).  Awaiting first real Provider "
            "response..."
        )
    else:
        spec.execution_mode = "OFFLINE_FAKE"
        print(
            f"OFFLINE mode ({CANONICAL_API_KEY_ENV_VAR} NOT set; "
            f"FakeLLM used)."
        )

    # ---- 5. run with exception containment ----
    # The strategy pipeline may raise:
    #   - RealLLMError / RealLLMErrorKind.*  (provider transport)
    #   - ValueError from _parse_llm_output  (claim schema fail)
    #   - other unexpected errors
    # All of these MUST be caught so the trial JSON
    # is still written and the real request ledger is
    # preserved (the Day14RecordingClient has already
    # appended the record before the parser ran).
    t0 = time.perf_counter()
    res = None
    exception_type: Optional[str] = None
    exception_message: Optional[str] = None
    try:
        res = run_trial(spec)
    except Exception as e:
        elapsed_s = time.perf_counter() - t0
        exception_type = type(e).__name__
        exception_message = str(e)
        print(
            f"\nEXCEPTION during trial: {exception_type}: "
            f"{exception_message}"
        )
        # Build a failure TrialResult so the JSON is
        # still on disk.  The recording client's ledger
        # is harvested from the spec.
        from experiments.day14.strategies import (
            TrialResult,
        )
        from experiments.day14.metrics import (
            collect_real_metrics,
        )
        # Harvest the ledger — even on exception, the
        # Day14RecordingClient has already appended the
        # record.  We MUST NOT lose it.
        if spec.real_client is not None and hasattr(
            spec.real_client, "ledger"
        ):
            real_metrics = collect_real_metrics(
                spec.real_client.ledger, elapsed_s * 1000.0,
            )
        else:
            real_metrics = collect_real_metrics(
                LiveCallLedger(), elapsed_s * 1000.0,
            )
        res = TrialResult(
            trial_id=spec.trial_id,
            case_id=spec.case_id,
            strategy=spec.strategy,
            store_dir=spec.store_dir,
            success=False,
            failure_reason=(
                f"EXCEPTION: {exception_type}: {exception_message}"
            ),
            recovery_wall_clock_ms=elapsed_s * 1000.0,
            real_metrics=real_metrics,
            final_target_ref=None,
            final_content_hash=None,
            matches_golden_output=False,
            new_llm_artifact_refs=[],
            reused_llm_artifact_refs=[],
            structural={},
            semantic={},
            success_report={},
            scenario_signature_hash=None,
            replaces_trial_id=spec.replaces_trial_id,
        )
    else:
        elapsed_s = time.perf_counter() - t0

    # ---- 5.5  attach replacement-chain metadata ----
    # If this is a replacement, propagate the chain
    # info (root_trial_id, previous_failure_class, etc.)
    # to the TrialResult so the JSON captures the
    # chain.  The replacement_reason is taken from
    # spec.replacement_reason (set in step 3 from the
    # predecessor's failure_audit.json) — this is the
    # PRE-R3 AUTH SEMANTICS FIX.
    if args.replacement_of:
        if res.replaces_trial_id is None:
            res.replaces_trial_id = args.replacement_of
        res.replacement_trial_id = res.trial_id
        if not res.replacement_reason:
            res.replacement_reason = getattr(
                spec, "replacement_reason",
                "PRE_RETRY_FORENSIC_HARDENING",
            )
        # The chain root is the trunk ID.  Stash it
        # in a top-level field.
        res_dict = res.to_dict()
        res_dict["root_trial_id"] = getattr(
            spec, "root_trial_id", args.replacement_of
        )
        res_dict["previous_failure_class"] = (
            getattr(spec, "previous_failure_class", None)
        )
        res_dict["previous_failure_subtype"] = (
            getattr(spec, "previous_failure_subtype", None)
        )
        # Write a chain summary that the closeout can
        # consume.
        out_path = write_trial_result(res)
        # Now re-write with the chain metadata.
        Path(out_path).write_text(
            json.dumps(
                res_dict, ensure_ascii=False, indent=2
            ),
            encoding="utf-8",
        )

    # ---- 6. write trial JSON ----
    # Capture provider_verified + 7-layer state from
    # the recording client BEFORE writing the JSON.
    if spec.real_client is not None:
        res.provider_verified = bool(
            getattr(spec.real_client, "provider_verified", False)
        )
        # Build per-execution-unit 7-layer state from
        # the recording client's response_captures.
        if hasattr(spec.real_client, "response_captures"):
            states: Dict[str, Dict[str, bool]] = {}
            for cap in spec.real_client.response_captures:
                unit = cap.get("execution_unit", "unknown")
                states[unit] = {
                    "request_attempted": True,
                    "response_received": cap.get(
                        "response_received", False
                    ),
                    "provider_verified": bool(
                        cap.get("request_id")
                    ) and cap.get("usage_type") == "provider_reported",
                    "schema_valid": cap.get(
                        "parsed_top_level_keys"
                    ) is not None and any(
                        k in (cap.get("parsed_top_level_keys") or [])
                        for k in ("claim_text",)
                    ),
                    "evidence_valid": False,  # downstream
                    "semantic_valid": False,  # downstream
                    "artifact_materialized": False,  # downstream
                }
            res.execution_unit_states = states
    out_path = write_trial_result(res)
    print(f"\nWrote: {out_path}")
    print(
        f"  success={res.success}  "
        f"failure_reason={res.failure_reason!r}"
    )
    print(
        f"  recovery_wall_clock_ms={res.recovery_wall_clock_ms:.2f}  "
        f"real_metrics={res.real_metrics.to_dict()}"
    )
    print(
        f"  new_llm={res.new_llm_artifact_refs}\n"
        f"  reused_llm={res.reused_llm_artifact_refs}"
    )
    print(f"  total elapsed: {elapsed_s:.2f}s")
    if res.provider_verified:
        print("  provider_verified: True")
    if res.execution_unit_states:
        print(
            f"  execution_unit_states: "
            f"{len(res.execution_unit_states)} units"
        )

    # ---- 6.5  REAL mode hard gate: provider-routing audit ----
    # If spec.execution_mode == "REAL_PROVIDER" but
    # real_llm_call_count == 0, this is a
    # HARNESS_PROVIDER_ROUTING_ERROR (the real client
    # was built but never threaded into the strategy).
    if spec.execution_mode == "REAL_PROVIDER":
        n_new = len(res.new_llm_artifact_refs)
        n_real = res.real_metrics.real_llm_call_count
        if n_new > 0 and n_real == 0:
            print()
            print("=" * 72)
            print(
                "HARNESS_PROVIDER_ROUTING_ERROR (REAL mode)"
            )
            print("=" * 72)
            print(
                f"  {n_new} new LLM artifacts materialized,"
            )
            print(
                f"  but 0 real Provider records in the ledger."
            )
            print(
                "  The Day14RecordingClient was built but the"
            )
            print(
                "  underlying RealLLMClient was NOT actually"
            )
            print(
                "  invoked by the strategy.  This is the"
            )
            print(
                "  'key present but FakeLLM routed' bug."
            )
            print()
            print(
                "  Marking this trial as INVALID.  Do NOT"
            )
            print(
                "  retry until the harness is fixed."
            )
            # Persist the failure classification into the
            # trial JSON so the closeout script can
            # exclude it.
            res.failure_reason = (
                "HARNESS_PROVIDER_ROUTING_ERROR: "
                "REAL_PROVIDER mode but 0 real provider "
                "records in ledger despite "
                f"{n_new} new LLM artifacts.  "
                + (res.failure_reason or "")
            )
            # Re-write the trial JSON with the updated
            # failure_reason.
            out_path = write_trial_result(res)
            print(f"  Updated trial JSON: {out_path}")
            return 4

    # ---- 6.6  Write failure audit JSON if the trial failed ----
    if not res.success:
        audit_path = Path(
            args.out_dir
        ) / f"{spec.trial_id}_failure_audit.json"
        try:
            _write_failure_audit(
                audit_path=audit_path,
                res=res,
                spec=spec,
                exception_type=exception_type,
                exception_message=exception_message,
            )
            print(f"  Wrote failure audit: {audit_path}")
        except Exception as audit_err:
            print(
                f"  WARN: could not write failure audit: "
                f"{audit_err}"
            )

    # ---- 7. strict stop on schema/evidence/semantic FAIL
    #         (spec section 18: any of those FAIL → STOP
    #         PILOT, do not continue to next strategy
    #         trial).  The user must inspect the failure
    #         audit before continuing.  We surface a clear
    #         instruction in the failure output.
    if not res.success:
        print()
        print("=" * 72)
        print("PILOT TRIAL FAILED — STRICT STOP")
        print("=" * 72)
        print(
            "Per spec section 18, a single trial FAIL on"
        )
        print(
            "schema/evidence/semantic/exact_version/fairness"
        )
        print(
            "must STOP the pilot.  Do NOT run the next"
        )
        print(
            "strategy trial until the failure is audited."
        )
        print()
        print(
            f"  failure_reason: {res.failure_reason!r}"
        )
        print(
            f"  trial JSON   : {out_path}"
        )
        print()
        # Distinguish infra failure from validation
        # failure.  Infra failure (HTTP 5xx, timeout,
        # rate-limit) is acceptable to retry; validation
        # failure is not.
        if res.infra_failure:
            print(
                "  INFRA_FAILURE — provider returned an"
            )
            print(
                "  HTTP/network error.  This trial may be"
            )
            print(
                "  re-run as a replacement_trial_id."
            )
            return 3
        print(
            "  VALIDATION_FAILURE — re-running this trial"
        )
        print(
            "  is NOT permitted.  Investigate the failure"
        )
        print(
            "  audit JSON before continuing."
        )
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
