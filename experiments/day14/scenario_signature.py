"""Day 14 — ScenarioSignature fairness.

Per user spec section 6: every (case, strategy, trial)
combination starts from a Recovery Phase Start snapshot
that is structurally equivalent.  The signature must
be identical across strategies BEFORE the recovery
phase starts; otherwise FAIRNESS FAIL.

The signature canonicalizes:
  - artifact_id, version, content/value
  - validation_status, producer (producer_task)
  - input_artifact_ids, evidence_ids

It IGNORES:
  - run_id
  - filesystem path
  - timestamps
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from artifacts import Artifact, ArtifactStore


def _canonical_value(a: Artifact) -> Any:
    """Return a JSON-stable value for an artifact's
    payload (content or value, whichever is set)."""
    if a.content is not None:
        return {"kind": "content", "value": a.content}
    if a.value is not None:
        return {"kind": "value", "value": a.value}
    return {"kind": "none"}


def _canonical_refs(refs) -> List[Dict[str, Any]]:
    out = []
    for r in refs:
        out.append({
            "artifact_id": r.artifact_id,
            "version": r.version,
        })
    return sorted(out, key=lambda x: (x["artifact_id"], x["version"]))


def compute_scenario_signature(
    store: ArtifactStore,
    run_id: str,
) -> Dict[str, Any]:
    """Build a canonical, run-id-agnostic description of
    every artifact in `run_id`.  Returned dict is JSON
    stable."""
    store.reload()
    sig: Dict[str, Any] = {}
    # include EVERY version (v1 + v2 + v3) so the faulty
    # snapshot is part of the signature.
    for a in store.list_all_versions_by_run(run_id):
        key = f"{a.artifact_id}@v{a.version}"
        sig[key] = {
            "artifact_id": a.artifact_id,
            "artifact_type": a.artifact_type.value
                if hasattr(a.artifact_type, "value")
                else str(a.artifact_type),
            "version": a.version,
            "validation_status": a.validation_status,
            "producer_task": a.producer_task,
            "value_or_content": _canonical_value(a),
            "input_artifact_ids": _canonical_refs(
                a.input_artifact_ids
            ),
            "evidence_ids": _canonical_refs(a.evidence_ids),
        }
    # Sort by key for determinism.
    return dict(sorted(sig.items()))


def compute_scenario_signature_hash(
    store: ArtifactStore,
    run_id: str,
) -> str:
    sig = compute_scenario_signature(store, run_id)
    blob = json.dumps(
        sig, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def verify_signature_fairness(
    signatures: Dict[str, str],
) -> Dict[str, Any]:
    """Given a dict of (case, strategy, trial) -> hash,
    assert all hashes are equal.  Returns
        passed: bool
        hashes: {key: hash}
        baseline_hash: str
        mismatches: {key: hash} (only when not all equal)
    """
    if not signatures:
        return {
            "passed": False,
            "reason": "no signatures provided",
            "hashes": {},
        }
    first_hash = next(iter(signatures.values()))
    mismatches = {
        k: v for k, v in signatures.items() if v != first_hash
    }
    return {
        "passed": not mismatches,
        "hashes": dict(signatures),
        "baseline_hash": first_hash,
        "mismatches": mismatches,
    }
