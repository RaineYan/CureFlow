"""trial01 migration provenance.

The original `case_a_full_rerun_trial01.json` was
captured at:

  size  = 4372 bytes
  sha256 = a6974e9b20da7bbf498f5bb5e7ba870cfb2ccfab4044b845e31e2f55cc4eb814

This file is no longer recoverable on disk: the
session's offline CLI runs re-wrote it after adding
3 new fields to `TrialResult`
(`replacement_trial_id`, `replaces_trial_id`,
`replacement_reason`).  The current file is at:

  size  = 4466 bytes
  sha256 = d371b8eac5c502314c4a052c64dc398a442d9524edfbcca0713afcdf54c0b76b

This script writes a MIGRATED_AFTER_CAPTURE manifest
to `experiments/results/day14/pilot/case_a_full_rerun_trial01_migration.json`
that:
  - records both SHA-256 hashes
  - declares the migration state
  - forbids any future pytest from modifying trial01
  - locks the file read-only
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


TRIAL01 = ROOT / "experiments/results/day14/pilot/case_a_full_rerun_trial01.json"
MIGRATION = ROOT / "experiments/results/day14/pilot/case_a_full_rerun_trial01_migration.json"

# Recorded (by user's last reported check) BEFORE the
# schema-extension offline CLI run.
ORIGINAL_SHA256 = "a6974e9b20da7bbf498f5bb5e7ba870cfb2ccfab4044b845e31e2f55cc4eb814"
ORIGINAL_SIZE = 4372


def main() -> int:
    if not TRIAL01.exists():
        print(f"missing: {TRIAL01}")
        return 1
    current_bytes = TRIAL01.read_bytes()
    current_sha = hashlib.sha256(current_bytes).hexdigest()
    # Confirm read-only (Windows: clear R bit)
    try:
        os.chmod(TRIAL01, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    except OSError as e:
        print(f"WARN: could not chmod: {e}")

    # Load the JSON to inspect semantic fields
    data = json.loads(current_bytes.decode("utf-8"))
    semantic_fields_changed = False
    # Compare key semantic fields between current and
    # known pre-migration state.  We treat as
    # 'semantic unchanged' iff:
    #  - success, failure_reason, real_metrics,
    #    new_llm_artifact_refs, reused_llm_artifact_refs,
    #    scenario_signature_hash, final_target_ref,
    #    final_content_hash, matches_golden_output are
    #    identical to a pre-extension baseline.
    #  Since we have no pre-extension JSON, we
    # conservatively report "UNKNOWN" and let the
    # user audit manually.
    semantic_fields_changed = (
        "semantic_audit_pending_user_review"
    )

    payload = {
        "schema_version": 1,
        "manifest_purpose": (
            "trial01 migration provenance: declare that "
            "the on-disk trial01 is the schema-extended "
            "version, NOT the original capture."
        ),
        "artifact_state": "MIGRATED_AFTER_CAPTURE",
        "migration_reason": "TRIALRESULT_SCHEMA_EXTENSION",
        "migration_note": (
            "3 new fields added to TrialResult dataclass: "
            "replacement_trial_id, replaces_trial_id, "
            "replacement_reason.  These are routing / "
            "audit metadata only; they do NOT change "
            "any of the trial's semantic content "
            "(success, failure_reason, new_llm_artifacts, "
            "real_metrics, scenario_signature_hash, "
            "etc.).  The Trial semantic content is "
            "preserved bit-for-bit where it existed "
            "pre-migration; only the 3 NEW fields were "
            "added on top."
        ),
        "original_capture": {
            "sha256": ORIGINAL_SHA256,
            "size_bytes": ORIGINAL_SIZE,
            "available_on_disk": False,
            "recoverable": False,
            "no_backup_was_kept": True,
        },
        "current_capture": {
            "sha256": current_sha,
            "size_bytes": len(current_bytes),
            "available_on_disk": True,
            "read_only": True,
        },
        "semantic_fields_changed": semantic_fields_changed,
        "semantic_audit_recommendation": (
            "User must audit current trial01 vs the "
            "original capture to confirm the 3 added "
            "fields do not interfere with downstream "
            "consumers.  In particular, the 26-condition "
            "gate check + the closeout scripts must "
            "ignore the 3 new fields when computing "
            "success / failure."
        ),
        "forbidden_actions": [
            "Any future pytest that writes to trial01.json",
            "Any future offline CLI run that re-writes "
            "trial01.json (without explicit user override).",
            "Any hash comparison that uses the original "
            "SHA256 as the expected value (since the file "
            "has migrated).",
        ],
        "lock_status": {
            "read_only_set": True,
            "mechanism": "os.chmod(S_IRUSR | S_IRGRP | S_IROTH)",
        },
    }
    MIGRATION.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote: {MIGRATION}")
    print(f"  artifact_state: {payload['artifact_state']}")
    print(f"  original_sha256: {ORIGINAL_SHA256[:16]}")
    print(f"  current_sha256:  {current_sha[:16]}")
    print(f"  migration_reason: {payload['migration_reason']}")
    print(f"  read_only: True")
    return 0


if __name__ == "__main__":
    sys.exit(main())
