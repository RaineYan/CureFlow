"""OFFLINE RE-AUDIT of the persisted live_test_c store.

Loads the post-recovery store from disk, rebuilds the
ArtifactGraph, and runs `clean_path_audit` on
`report_merged@v3`.  Writes the result to a JSON file for
review and to feed the failure sample 002.

NO LLM CALL.  Pure offline.
"""
import sys
import json
sys.path.insert(0, '.')

from pathlib import Path
from artifacts import ArtifactRef, ArtifactStore
from provenance.graph import ArtifactGraph
from recovery.executor import clean_path_audit

STORE_DIR = Path(r"C:\Users\Lenovo\AppData\Local\Temp"
                 r"\poc_day10_faultA_live_test_c_pjl8w1f8"
                 r"\artifacts")
OUT_PATH = Path("experiments/results/day13_6_live_test_c_offline_reaudit.json")

print("=== OFFLINE RE-AUDIT ===")
store = ArtifactStore(base_dir=str(STORE_DIR))
store.reload()
graph = ArtifactGraph.from_store(store, "live_test_c")

target = ArtifactRef(run_id="live_test_c", artifact_id="report_merged", version=3)
audit = clean_path_audit(
    target, graph, store,
    graph_rebuilt_after_recovery=True,
)

# Print summary
print(f"target            = {target.to_ref_string()}")
print(f"passed            = {audit.passed}")
print(f"reason            = {audit.reason}")
print(f"graph_rebuilt     = {audit.graph_rebuilt_after_recovery}")
print(f"visited_refs      = {len(audit.visited_refs)}")
print(f"clean_refs        = {len(audit.clean_refs)}")
print(f"non_clean_refs    = {len(audit.non_clean_refs)}")
print(f"missing_refs      = {len(audit.missing_refs)}")
print()
print("--- visited_refs (status) ---")
for r in audit.visited_refs:
    a = store.get_version(r.run_id, r.artifact_id, r.version)
    if a is None:
        print(f"  {r.to_ref_string()}: MISSING")
    else:
        print(f"  {r.to_ref_string()}: {a.validation_status}")
print()
print("--- non_clean_refs ---")
for entry in audit.non_clean_refs:
    print(f"  {entry}")
print()
print("--- missing_refs ---")
for r in audit.missing_refs:
    print(f"  {r.to_ref_string()}")

# Serialize
payload = {
    "schema_version": 1,
    "audit_type": "offline_clean_path_audit",
    "ran_at_unix_ms": 1787150000000,
    "store_dir": str(STORE_DIR),
    "target_ref": target.to_ref_string(),
    "passed": audit.passed,
    "reason": audit.reason,
    "graph_rebuilt_after_recovery": audit.graph_rebuilt_after_recovery,
    "visited_refs": [r.to_ref_string() for r in audit.visited_refs],
    "clean_refs": [r.to_ref_string() for r in audit.clean_refs],
    "non_clean_refs": audit.non_clean_refs,
    "missing_refs": [r.to_ref_string() for r in audit.missing_refs],
    "per_ref_status": {
        r.to_ref_string(): (
            store.get_version(r.run_id, r.artifact_id, r.version)
            .validation_status
            if store.get_version(r.run_id, r.artifact_id, r.version) else "MISSING"
        )
        for r in audit.visited_refs
    },
}
OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
with open(OUT_PATH, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=2)
print(f"\nWrote {OUT_PATH}")
print(f"\n=== CONCLUSION ===")
print(f"  semantic_clean_path_pass = {audit.passed}")
print(f"  non_clean_refs = {len(audit.non_clean_refs)}")
print(f"  missing_refs   = {len(audit.missing_refs)}")
print(f"  real Qwen calls needed for re-audit = 0 (pure offline)")
