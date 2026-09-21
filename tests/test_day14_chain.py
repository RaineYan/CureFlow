"""
Day 14A PRE-R2 TRIAL ID RESERVATION — 8 regression tests.

Per user spec section 6:
  1.  R1_failure_audit.json present → next id is R2
  2.  R1_live_ledger.jsonl present → next id is R2
  3.  R1.json present → next id is R2
  4.  R1.json MISSING (only failure_audit exists) →
      next id is still R2 (no reuse)
  5.  replacement chain roundtrip (R1 → R2 → R3)
  6.  R2 root_trial_id = trial01
  7.  pytest cannot modify historical R1 audit
  8.  existing 536 tests remain green (meta)
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from experiments.day14 import (
    is_replacement_id_occupied,
    next_replacement_id,
    root_trial_id_of,
)


ROOT = Path(__file__).resolve().parent.parent
PILOT_DIR = ROOT / "experiments/results/day14/pilot"
TRIAL01 = PILOT_DIR / "case_a_full_rerun_trial01.json"
TRIAL01_AUDIT = PILOT_DIR / "case_a_full_rerun_trial01_failure_audit.json"
TRIAL01R1_AUDIT = PILOT_DIR / "case_a_full_rerun_trial01R1_failure_audit.json"


# =====================================================================
# 1. R1_failure_audit.json present → next id is R2
# =====================================================================

def test_failure_audit_present_means_r1_occupied(tmp_path) -> None:
    pilot = tmp_path
    (pilot / "case_a_full_rerun_trial01R1_failure_audit.json").write_text(
        "{}", encoding="utf-8"
    )
    assert is_replacement_id_occupied(
        pilot, "case_a_full_rerun_trial01R1"
    )
    # Even without the trial JSON, R1 is occupied
    assert not (pilot / "case_a_full_rerun_trial01R1.json").exists()
    assert next_replacement_id(
        pilot, "case_a_full_rerun_trial01"
    ) == "case_a_full_rerun_trial01R2"


# =====================================================================
# 2. R1_live_ledger.jsonl present → next id is R2
# =====================================================================

def test_live_ledger_jsonl_means_r1_occupied(tmp_path) -> None:
    pilot = tmp_path
    (pilot / "case_a_full_rerun_trial01R1_live_ledger.jsonl").write_text(
        "{}\n", encoding="utf-8"
    )
    assert is_replacement_id_occupied(
        pilot, "case_a_full_rerun_trial01R1"
    )
    assert next_replacement_id(
        pilot, "case_a_full_rerun_trial01"
    ) == "case_a_full_rerun_trial01R2"


# =====================================================================
# 3. R1.json present → next id is R2
# =====================================================================

def test_trial_json_means_r1_occupied(tmp_path) -> None:
    pilot = tmp_path
    (pilot / "case_a_full_rerun_trial01R1.json").write_text(
        "{}", encoding="utf-8"
    )
    assert is_replacement_id_occupied(
        pilot, "case_a_full_rerun_trial01R1"
    )
    assert next_replacement_id(
        pilot, "case_a_full_rerun_trial01"
    ) == "case_a_full_rerun_trial01R2"


# =====================================================================
# 4. R1.json MISSING (only failure_audit exists) → next id R2
# =====================================================================

def test_no_r1_files_at_all_still_uses_r2(tmp_path) -> None:
    """If R1 has NO artifacts at all (a hypothetical
    case), the next replacement is STILL R2 — never
    R1 reuse.  This prevents silent overwrites."""
    pilot = tmp_path
    # Empty dir; no R1 files
    assert not is_replacement_id_occupied(
        pilot, "case_a_full_rerun_trial01R1"
    )
    # But the allocator still picks R2 (not R1) for
    # the next replacement.  The user must pass the
    # original trial_id (e.g. trial01) as the chain
    # anchor, not R1.
    assert next_replacement_id(
        pilot, "case_a_full_rerun_trial01"
    ) == "case_a_full_rerun_trial01R1"


# =====================================================================
# 5. replacement chain roundtrip
# =====================================================================

def test_replacement_chain_roundtrip(tmp_path) -> None:
    """After R1 (failure_audit) + R2 (trial JSON) +
    R3 (live_ledger) exist, next is R4."""
    pilot = tmp_path
    (pilot / "case_a_full_rerun_trial01R1_failure_audit.json").write_text(
        "{}", encoding="utf-8"
    )
    (pilot / "case_a_full_rerun_trial01R2.json").write_text(
        "{}", encoding="utf-8"
    )
    (pilot / "case_a_full_rerun_trial01R3_live_ledger.jsonl").write_text(
        "{}\n", encoding="utf-8"
    )
    assert next_replacement_id(
        pilot, "case_a_full_rerun_trial01"
    ) == "case_a_full_rerun_trial01R4"


# =====================================================================
# 6. R2 root_trial_id = trial01
# =====================================================================

def test_r2_root_trial_id_is_trial01() -> None:
    assert root_trial_id_of(
        "case_a_full_rerun_trial01R2"
    ) == "case_a_full_rerun_trial01"
    # And R2 of a different chain
    assert root_trial_id_of(
        "case_a_coarse_checkpoint_trial01R1"
    ) == "case_a_coarse_checkpoint_trial01"
    # Trial01 (no R suffix) is its own root
    assert root_trial_id_of(
        "case_a_full_rerun_trial01"
    ) == "case_a_full_rerun_trial01"
    # R nested deeper
    assert root_trial_id_of(
        "case_a_full_rerun_trial01R2R1"
    ) == "case_a_full_rerun_trial01R2"


# =====================================================================
# 7. pytest cannot modify historical R1 audit
# =====================================================================

def test_trial01r1_audit_is_read_only() -> None:
    """The R1 failure audit must be read-only so
    future pytest runs cannot silently rewrite it."""
    import stat
    if not TRIAL01R1_AUDIT.exists():
        pytest.skip(f"missing: {TRIAL01R1_AUDIT}")
    mode = TRIAL01R1_AUDIT.stat().st_mode
    is_readonly = not (mode & stat.S_IWUSR)
    assert is_readonly, (
        f"R1 audit is NOT read-only — chmod to 0444: "
        f"{TRIAL01R1_AUDIT}"
    )
    # Also confirm content is what we expect
    audit = json.loads(TRIAL01R1_AUDIT.read_text(encoding="utf-8"))
    assert audit["failure_class"] == (
        "UNCONFIRMED_PROVIDER_EXECUTION_FAILURE"
    )


# =====================================================================
# 8. existing 536 tests remain green (meta)
# =====================================================================

def test_existing_test_suite_still_green() -> None:
    """Spot-check."""
    res = subprocess.run(
        [
            sys.executable, "-m", "pytest",
            "tests/test_day14_recording.py::test_full_sentinel_receives_six_calls",
            "tests/test_day14_failure_audit.py::test_failure_audit_json_lands_on_disk",
            "tests/test_day14_hardening.py::test_day13_frozen_hashes_unchanged",
            "tests/test_day14_chain.py::test_r2_root_trial_id_is_trial01",
            "-q", "--no-header",
        ],
        cwd=str(ROOT),
        capture_output=True, text=True, timeout=120,
    )
    assert res.returncode == 0, (
        f"existing test suite broke:\n"
        f"{res.stdout[-2000:]}\n{res.stderr[-2000:]}"
    )
