"""Day 14 Pilot — main runner.

Drives ONE trial at a time.  Trial results are
written to JSON so the offline reducer can build
the Pilot summary table.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from experiments.live_ledger import LiveCallLedger
from .frozen_evidence import verify_frozen_evidence_unchanged
from .metrics import (
    RealMetricsReport,
    collect_real_metrics,
    compute_provider_token_reduction,
    compute_real_llm_call_reduction,
    compute_recovery_wallclock_reduction,
)
from .provider_config import (
    FROZEN_PROVIDER_CONFIG,
    validate_provider_config_match,
)
from .scenario_signature import (
    compute_scenario_signature_hash,
    verify_signature_fairness,
)
from .strategies import (
    TrialResult,
    TrialSpec,
    run_coarse_trial,
    run_full_trial,
    run_selective_trial,
)


PILOT_DIR = Path("experiments/results/day14/pilot")


def _default_csv() -> str:
    return str(Path("data/sample_company.csv"))


def _new_trial_dir(trial_id: str) -> str:
    p = Path(tempfile.mkdtemp(prefix=f"poc_day14_{trial_id}_"))
    return str(p)


def build_trial_spec(
    *,
    case_id: str,
    strategy: str,
    trial_id: Optional[str] = None,
    csv_path: Optional[str] = None,
    out_dir: Optional[Path] = None,
    replaces_trial_id: Optional[str] = None,
) -> TrialSpec:
    """Build a TrialSpec for a single trial.  Each trial
    gets a fresh tmpdir store.

    If `replaces_trial_id` is given, the on-disk file is
    named `<case_id>_<strategy>_<replaces_id>R1.json`
    so the original trial01.json is NEVER overwritten.
    """
    if trial_id is None:
        trial_id = (
            f"day14_{case_id}_{strategy.lower()}_"
            f"{int(time.time() * 1000)}"
        )
    out_dir = out_dir or PILOT_DIR
    if replaces_trial_id:
        # The replacement trial_id already encodes the
        # base + suffix (e.g. "case_a_full_rerun_trial01R1").
        # The on-disk filename is just <trial_id>.json.
        out_path = str(out_dir / f"{trial_id}.json")
    else:
        out_path = str(
            out_dir / f"{case_id}_{strategy.lower()}_trial01.json"
        )
    return TrialSpec(
        trial_id=trial_id,
        case_id=case_id,
        strategy=strategy,
        store_dir=_new_trial_dir(trial_id),
        csv_path=csv_path or _default_csv(),
        provider_config=FROZEN_PROVIDER_CONFIG.to_dict(),
        out_path=out_path,
        real_client=None,  # set by user-side runner
        replaces_trial_id=replaces_trial_id,
    )


# =====================================================================
# next_replacement_id — scan for any of the four evidence
# file patterns and pick the next free R<n> suffix.
# =====================================================================

EVIDENCE_FILE_SUFFIXES = (
    ".json",
    "_failure_audit.json",
    "_live_ledger.jsonl",
    "_live_ledger.json",
)


def is_replacement_id_occupied(
    pilot_dir: Path, trial_id: str,
) -> bool:
    """True iff any of the four evidence artifacts
    for `trial_id` already exists in `pilot_dir`."""
    for suffix in EVIDENCE_FILE_SUFFIXES:
        if (pilot_dir / f"{trial_id}{suffix}").exists():
            return True
    return False


def next_replacement_id(
    pilot_dir: Path, root_trial_id: str,
) -> str:
    """Find the smallest n>=1 such that
    `{root_trial_id}R{n}` is NOT occupied.  Returns
    the chosen trial_id string."""
    n = 1
    while is_replacement_id_occupied(
        pilot_dir, f"{root_trial_id}R{n}"
    ):
        n += 1
    return f"{root_trial_id}R{n}"


def root_trial_id_of(replacement_id: str) -> str:
    """Strip the trailing R<n> suffix from a trial_id
    to recover the chain's root trial_id.

    Example: root_trial_id_of("case_a_full_rerun_trial01R2")
    = "case_a_full_rerun_trial01"."""
    rid = replacement_id
    while "R" in rid:
        idx = rid.rfind("R")
        tail = rid[idx + 1:]
        if tail.isdigit():
            return rid[:idx]
        break
    return rid


def run_trial(spec: TrialSpec) -> TrialResult:
    """Dispatch a TrialSpec to the matching strategy
    function.  Returns the TrialResult; the caller is
    responsible for writing the JSON to disk."""
    if spec.strategy == "FULL_RERUN":
        res = run_full_trial(spec)
    elif spec.strategy == "COARSE_CHECKPOINT":
        res = run_coarse_trial(spec)
    elif spec.strategy == "SELECTIVE_RECOVERY":
        res = run_selective_trial(spec)
    else:
        raise ValueError(f"unknown strategy: {spec.strategy!r}")
    # Apply replacement metadata if this trial replaces
    # an earlier invalid one.
    if spec.replaces_trial_id:
        res.replaces_trial_id = spec.replaces_trial_id
        # trial_id is the new ID; write_trial_result
        # uses it to pick a non-conflicting filename.
        res.replacement_trial_id = res.trial_id
        if not res.replacement_reason:
            res.replacement_reason = (
                "HARNESS_PROVIDER_ROUTING_ERROR"
            )
    return res


def write_trial_result(result: TrialResult) -> str:
    """Write the trial JSON.  Filename policy:

      - If `result.replaces_trial_id` is set, derive the
        filename from the new trial_id (which encodes
        the replacement suffix, e.g. "trial01R1").
      - Otherwise default to "trial01.json" (which is
        what `build_trial_spec` would have set as
        `out_path`).

    The original `trial01.json` is NEVER overwritten
    by a replacement.
    """
    p = Path(PILOT_DIR)
    if result.replaces_trial_id and result.trial_id:
        # The trial_id is the authoritative name.
        # e.g. "case_a_full_rerun_trial01R1" → "case_a_full_rerun_trial01R1.json"
        p = p / f"{result.trial_id}.json"
    else:
        p = p / (
            f"{result.case_id}_{result.strategy.lower()}_trial01.json"
        )
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(p)


# =====================================================================
# Higher-level helpers
# =====================================================================

def assert_frozen_evidence_intact() -> Dict[str, Any]:
    """Hard pre-condition: Day 13 evidence must be
    unchanged BEFORE every pilot trial."""
    return verify_frozen_evidence_unchanged()


def assert_provider_config(actual: Dict[str, Any]) -> Dict[str, Any]:
    return validate_provider_config_match(actual)


def assert_signature_fairness(
    case_id: str,
    signatures_by_strategy: Dict[str, str],
) -> Dict[str, Any]:
    """The 3 strategies of a single case must share the
    same scenario signature hash at Recovery Phase
    Start."""
    return verify_signature_fairness(
        {f"{case_id}:{k}": v for k, v in signatures_by_strategy.items()}
    )


def compute_reductions(
    full: TrialResult,
    coarse: TrialResult,
    selective: TrialResult,
) -> Dict[str, Any]:
    return {
        "COARSE": {
            "real_llm_call_reduction_vs_full":
                compute_real_llm_call_reduction(
                    full.real_metrics, coarse.real_metrics
                ),
            "provider_token_reduction_vs_full":
                compute_provider_token_reduction(
                    full.real_metrics, coarse.real_metrics
                ),
            "recovery_wallclock_reduction_vs_full":
                compute_recovery_wallclock_reduction(
                    full.real_metrics, coarse.real_metrics
                ),
        },
        "SELECTIVE": {
            "real_llm_call_reduction_vs_full":
                compute_real_llm_call_reduction(
                    full.real_metrics, selective.real_metrics
                ),
            "provider_token_reduction_vs_full":
                compute_provider_token_reduction(
                    full.real_metrics, selective.real_metrics
                ),
            "recovery_wallclock_reduction_vs_full":
                compute_recovery_wallclock_reduction(
                    full.real_metrics, selective.real_metrics
                ),
        },
    }
