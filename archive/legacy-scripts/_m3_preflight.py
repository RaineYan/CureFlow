"""M3 preflight (read-mostly): 1 smoke + 1 workflow trial (CASE_A × SELECTIVE).

Uses the same path as M1: build_trial_spec -> run_trial -> write_trial_result,
but with a real DeepSeek-bound RealLLMClient injected via TrialSpec.real_client.

The preflight trial is written to m3_cross_provider/preflight/ and is
EXCLUDED from the Phase A statistics (which writes to m3_cross_provider/pilot/).

On any failure, writes m3_preflight.json with status=FAIL and HARD-STOPS.
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

M3 = ROOT / "experiments/results/exp02/m3_cross_provider"
PREFLIGHT = M3 / "preflight"
PREFLIGHT.mkdir(parents=True, exist_ok=True)

# ------------------------------------------------------------------
# Step 0: env validation
# ------------------------------------------------------------------
expected_base = os.environ.get("REAL_LLM_EXPECTED_BASE", "")
expected_model_env = os.environ.get("REAL_LLM_EXPECTED_MODEL", "")
key_env_name = os.environ.get("REAL_LLM_API_KEY_NAME", "REAL_LLM_API_KEY")
base_url = os.environ.get("REAL_LLM_BASE_URL", "")
model_env = os.environ.get("REAL_LLM_MODEL", "")

key_value = os.environ.get(key_env_name, "")
report = {
    "step_0_env": {
        "expected_base": expected_base,
        "expected_model": expected_model_env,
        "key_env_name": key_env_name,
        "actual_base": base_url,
        "actual_model": model_env,
        "key_present": bool(key_value),
        "key_length": len(key_value) if key_value else 0,
        "consistent": (
            bool(expected_base)
            and expected_base.rstrip("/") == base_url.rstrip("/")
            and expected_model_env == model_env
            and key_value != ""
        ),
    }
}
if not report["step_0_env"]["consistent"]:
    report["status"] = "M3_PREFLIGHT_FAILED"
    report["hard_stop_reason"] = "env inconsistent"
    PREFLIGHT.joinpath("m3_preflight.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(report["hard_stop_reason"])
    sys.exit(1)

# ------------------------------------------------------------------
# Step 1: smoke call (1 trivial HTTP round-trip)
# ------------------------------------------------------------------
from llm.providers import RealLLMClient
from llm import RealLLMProviderConfig
from llm.base import UsageType

provider_name = "deepseek" if "deepseek" in expected_base else (
    "openai" if "openai" in expected_base else "cross_provider"
)
smoke_cfg = RealLLMProviderConfig(
    provider=provider_name,
    model=expected_model_env,
    base_url=expected_base,
    api_key_env_var=key_env_name,
    response_format=None,
    temperature=0.0,
    seed=1234,
    provider_options={},
    max_retries=0,
    timeout_seconds=60.0,
)
smoke_client = RealLLMClient(smoke_cfg)
smoke_resp = smoke_client.generate(
    "Respond with exactly: PONG",
    task_name="m3_preflight_smoke",
)
report["step_1_smoke"] = {
    "text": smoke_resp.text,
    "request_id": smoke_resp.request_id,
    "usage_type": (
        smoke_resp.usage_type.value
        if hasattr(smoke_resp.usage_type, "value")
        else str(smoke_resp.usage_type)
    ),
    "input_tokens": smoke_resp.input_tokens,
    "output_tokens": smoke_resp.output_tokens,
    "latency_ms": smoke_resp.latency_ms,
    "provider": smoke_resp.provider,
    "model": smoke_resp.model,
}
smoke_ok = (
    "PONG" in (smoke_resp.text or "")
    and smoke_resp.request_id is not None
    and smoke_resp.usage_type == UsageType.PROVIDER_REPORTED
    and smoke_resp.input_tokens > 0
    and smoke_resp.output_tokens > 0
)
report["step_1_smoke"]["ok"] = smoke_ok
if not smoke_ok:
    report["status"] = "M3_PREFLIGHT_FAILED"
    report["hard_stop_reason"] = (
        f"smoke call failed: text={smoke_resp.text!r}, "
        f"request_id={smoke_resp.request_id}, "
        f"usage={smoke_resp.usage_type}, "
        f"in={smoke_resp.input_tokens}, "
        f"out={smoke_resp.output_tokens}"
    )
    PREFLIGHT.joinpath("m3_preflight.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(report["hard_stop_reason"])
    sys.exit(1)

# ------------------------------------------------------------------
# Step 2: workflow-compatibility trial CASE_A × SELECTIVE_RECOVERY
# using the same pilot_runner path as M1.
# ------------------------------------------------------------------
from experiments.day14.pilot_runner import (
    build_trial_spec,
    run_trial,
    write_trial_result,
)
from experiments.day14 import FROZEN_PROVIDER_CONFIG
from experiments.day14.recording import Day14RecordingClient
from experiments.live_ledger import LiveCallLedger

# Build the spec with the cross-Provider client.
spec = build_trial_spec(
    case_id="case_a",
    strategy="SELECTIVE_RECOVERY",
    trial_id="m3_preflight_case_a_selective_recovery",
    out_dir=PREFLIGHT,
)
# Inject the cross-Provider real client (overrides the default
# workflow fake-LLM used inside pilot_runner).
ledger = LiveCallLedger()
phase_prefix = "C-recovery"
spec.real_client = Day14RecordingClient(
    inner=smoke_client,
    phase_prefix=phase_prefix,
    ledger=ledger,
)

try:
    result = run_trial(spec)
    preflight_workflow_ok = result is not None
except Exception as e:
    preflight_workflow_ok = False
    report["step_2_workflow_exception"] = f"{type(e).__name__}: {e}"

# Persist the trial result so Phase A can confirm it is not
# counted in statistics.
trial_json_path = ""
if preflight_workflow_ok:
    try:
        trial_json_path = write_trial_result(result)
    except Exception as e:
        report["step_2_persist_exception"] = f"{type(e).__name__}: {e}"
        preflight_workflow_ok = False

report["step_2_workflow"] = {
    "trial_id": getattr(spec, "trial_id", "?"),
    "case_id": "case_a",
    "strategy": "SELECTIVE_RECOVERY",
    "artifact_returned": preflight_workflow_ok,
    "trial_json_path": trial_json_path,
}
if not preflight_workflow_ok:
    report["status"] = "M3_PREFLIGHT_FAILED"
    report["hard_stop_reason"] = "workflow-compatibility trial failed"
    PREFLIGHT.joinpath("m3_preflight.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(report["hard_stop_reason"])
    sys.exit(1)

# ------------------------------------------------------------------
# Step 3: API-key leakage check across all written files in M3 dir.
# ------------------------------------------------------------------
def scan_for_key(root_dir: Path, key: str) -> list:
    leaks = []
    for f in root_dir.rglob("*"):
        if not f.is_file():
            continue
        try:
            txt = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if key and key in txt:
            leaks.append(str(f.relative_to(root_dir)))
    return leaks

leaks = scan_for_key(M3, key_value)
report["step_3_key_leakage"] = {
    "files_with_key": leaks,
    "ok": (len(leaks) == 0),
}
if leaks:
    report["status"] = "M3_PREFLIGHT_FAILED"
    report["hard_stop_reason"] = f"key leaked into {len(leaks)} files"
    PREFLIGHT.joinpath("m3_preflight.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(report["hard_stop_reason"], leaks)
    sys.exit(1)

# ------------------------------------------------------------------
# Step 4: historical-evidence SHA integrity (compare to baseline).
# ------------------------------------------------------------------
import hashlib

baseline = json.loads(
    (PREFLIGHT / "m3_historical_evidence_sha256_baseline.json").read_text(
        encoding="utf-8-sig"
    )
)
mismatches = []
for name, info in baseline.items():
    for rel, expected_sha in info["shas"].items():
        p = ROOT / rel
        if not p.exists():
            mismatches.append((name, rel, "MISSING"))
            continue
        cur = hashlib.sha256(p.read_bytes()).hexdigest()
        if cur != expected_sha:
            mismatches.append((name, rel, f"got {cur[:16]}, expected {expected_sha[:16]}"))
report["step_4_historical_evidence"] = {
    "mismatches": mismatches,
    "ok": (len(mismatches) == 0),
    "checked_files": sum(len(info["shas"]) for info in baseline.values()),
}
if mismatches:
    report["status"] = "M3_PREFLIGHT_FAILED"
    report["hard_stop_reason"] = f"{len(mismatches)} historical evidence files changed"
    PREFLIGHT.joinpath("m3_preflight.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(report["hard_stop_reason"])
    for m in mismatches[:5]:
        print(f"  {m}")
    sys.exit(1)

# ------------------------------------------------------------------
# Step 5: pytest (sanity — should still be 609/609)
# ------------------------------------------------------------------
import subprocess
res = subprocess.run(
    [sys.executable, "-m", "pytest", "-q", "--tb=line"],
    cwd=str(ROOT), capture_output=True, text=True, timeout=900,
)
report["step_5_pytest"] = {
    "returncode": res.returncode,
    "stdout_tail": res.stdout[-400:],
    "ok": (res.returncode == 0),
}
if res.returncode != 0:
    report["status"] = "M3_PREFLIGHT_FAILED"
    report["hard_stop_reason"] = f"pytest failed (rc={res.returncode})"
    PREFLIGHT.joinpath("m3_preflight.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(res.stdout[-1500:])
    sys.exit(1)

# ------------------------------------------------------------------
# All steps PASS
# ------------------------------------------------------------------
report["status"] = "M3_PREFLIGHT_PASSED"
report["hard_stop_reason"] = None
PREFLIGHT.joinpath("m3_preflight.json").write_text(
    json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
)
print("M3 preflight PASSED")
print(f"  Provider:    {provider_name}  base={expected_base}")
print(f"  Model:       {expected_model_env}")
print(f"  Smoke:       text={smoke_resp.text!r}  in={smoke_resp.input_tokens} out={smoke_resp.output_tokens}  rid={smoke_resp.request_id}")
print(f"  Workflow:    CASE_A × SELECTIVE_RECOVERY returned artifact = {preflight_workflow_ok}")
print(f"  Key leak:    none (scanned {len(list(M3.rglob('*')))} files)")
print(f"  Hist SHA:    {report['step_4_historical_evidence']['checked_files']} files, 0 mismatches")
print(f"  pytest:      PASS")
