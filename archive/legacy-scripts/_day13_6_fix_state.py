"""One-shot offline fix for `experiments/results/day13_5_test_a_state.json`.

This script does NOT call any LLM.  It:
  1) reads the existing v3 PASS state
  2) opens the persistent store, calls reload() so the in-memory
     cache contains every on-disk JSON file
  3) re-runs `_save_test_a_state` which now uses reload() and
     captures upstream_artifacts + raw_data
  4) reports the audit: required / persisted / missing dependency refs

Run once: `python _day13_6_fix_state.py`
"""
from __future__ import annotations

import json

from artifacts import Artifact, ArtifactStore
from live_gate_run import _save_test_a_state
from experiments.live_ledger import LiveCallLedger, LiveCallRecord


def main() -> int:
    state_path = "experiments/results/day13_5_test_a_state.json"
    with open(state_path, "r", encoding="utf-8") as f:
        state = json.load(f)
    ta = state["test_a"]
    print("BEFORE FIX:")
    print("  upstream_artifacts count:",
          len(ta.get("upstream_artifacts", [])))
    print("  raw_data present:", ta.get("raw_data") is not None)

    store_dir = "experiments/results/day13_5_test_a_store"
    store = ArtifactStore(base_dir=store_dir)
    store.reload()
    print("STORE after reload():")
    for aid in [
        "metric_revenue_growth",
        "metric_profit_margin",
        "metric_cost_ratio",
        "raw_data",
        "claim_financial_growth",
    ]:
        a = store.get_version("live_test_a", aid, 1)
        if a is not None:
            kind = "Metric" if a.artifact_type == "Metric" else \
                "Claim" if a.artifact_type == "Claim" else \
                a.artifact_type
            print(f"  {aid}@v1: type={kind}")
        else:
            print(f"  {aid}@v1: NOT FOUND")

    claim = Artifact.model_validate(ta["claim_artifact"])
    print()
    print("CLAIM:")
    print("  artifact_id:", claim.artifact_id)
    print("  input_artifact_ids:", [
        (r.run_id, r.artifact_id, r.version)
        for r in claim.input_artifact_ids
    ])
    print("  evidence_ids:", [
        (r.run_id, r.artifact_id, r.version)
        for r in claim.evidence_ids
    ])

    test_a = {
        "passed": ta["passed"],
        "schema_valid": ta["schema_valid"],
        "evidence_valid": ta["evidence_valid"],
        "numeric_consistency_pass": ta["numeric_consistency_pass"],
        "direction_consistency_pass": ta["direction_consistency_pass"],
        "recommendation_boundary_pass": ta["recommendation_boundary_pass"],
        "semantic_valid": ta["semantic_valid"],
        "claim_ref": ta["claim_ref"],
        "claim_artifact": claim,
        "usage_type": ta["usage_type"],
        "request_id": ta["request_id"],
        "reasoning_tokens": ta.get("reasoning_tokens"),
        "request_enable_thinking": ta.get("request_enable_thinking"),
        "request_response_format": ta.get("request_response_format"),
        "real_request_count": ta["real_request_count"],
        "semantic": ta.get("semantic"),
    }

    ledger = LiveCallLedger()
    rd = state.get("ledger_record")
    if rd:
        ledger.append(LiveCallRecord(**rd))

    _save_test_a_state(test_a, ledger, store)

    with open(state_path, "r", encoding="utf-8") as f:
        new_state = json.load(f)
    nta = new_state["test_a"]
    print()
    print("AFTER FIX:")
    print("  upstream_artifacts count:",
          len(nta.get("upstream_artifacts", [])))
    print("  raw_data present:", nta.get("raw_data") is not None)
    print("  saved refs:")
    for d in nta.get("upstream_artifacts", []):
        r = d["ref"]
        print(f"    {r['run_id']}/{r['artifact_id']}@v{r['version']}")
    print("  raw_data artifact_id:",
          nta["raw_data"]["artifact_id"]
          if nta.get("raw_data") else None)

    required: set[str] = set()
    for r in list(claim.input_artifact_ids) + list(claim.evidence_ids):
        required.add(f"{r.run_id}/{r.artifact_id}@v{r.version}")
    persisted: set[str] = set(
        f"{d['ref']['run_id']}/{d['ref']['artifact_id']}@v{d['ref']['version']}"
        for d in nta.get("upstream_artifacts", [])
    )
    missing = required - persisted
    print()
    print("AUDIT:")
    print(f"  required_dependency_refs ({len(required)}): {sorted(required)}")
    print(f"  persisted_dependency_refs ({len(persisted)}): {sorted(persisted)}")
    print(f"  missing_dependency_refs ({len(missing)}): {sorted(missing)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
