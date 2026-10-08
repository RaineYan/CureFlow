"""Day 13.6 CLOSEOUT — step 2: clean_path_audit on archived store."""
import json
import sys
from pathlib import Path

sys.path.insert(0, '.')

from artifacts import ArtifactRef, ArtifactStore
from provenance.graph import ArtifactGraph
from recovery.executor import clean_path_audit

ARCHIVED = Path("experiments/results/day13_6_live_test_c_store")
store = ArtifactStore(base_dir=str(ARCHIVED))
store.reload()

# The clean_path_audit also needs the v3 artifacts present.
# Verify pre-conditions.
target = ArtifactRef(run_id="live_test_c", artifact_id="report_merged", version=3)
art = store.get_version(target.run_id, target.artifact_id, target.version)
print(f"target @v3 exists: {art is not None}")
print(f"target status    : {art.validation_status if art else None}")

# Build a fresh graph
graph = ArtifactGraph.from_store(store, "live_test_c")
print(f"graph nodes     : {len(graph._graph.nodes)}")
print(f"target in graph : {target in graph._graph.nodes}")

# Run clean_path_audit
audit = clean_path_audit(
    target, graph, store, graph_rebuilt_after_recovery=True,
)
print(f"\naudit.passed            = {audit.passed}")
print(f"audit.reason            = {audit.reason!r}")
print(f"audit.visited_refs      = {len(audit.visited_refs)}")
print(f"audit.clean_refs        = {len(audit.clean_refs)}")
print(f"audit.non_clean_refs    = {len(audit.non_clean_refs)}")
print(f"audit.missing_refs      = {len(audit.missing_refs)}")
print(f"audit.graph_rebuilt     = {audit.graph_rebuilt_after_recovery}")

# Per-ref status
per_ref = {}
for r in audit.visited_refs:
    a = store.get_version(r.run_id, r.artifact_id, r.version)
    per_ref[r.to_ref_string()] = (
        a.validation_status if a else "MISSING"
    )
print(f"\n=== per_ref_status (visited) ===")
for k, v in per_ref.items():
    print(f"  {k}: {v}")

# Historical v2 status (preserved)
print(f"\n=== historical v2 fault trail (preserved) ===")
for aid in (
    "field_revenue_2025", "metric_revenue_growth", "metric_profit_margin",
    "metric_cost_ratio", "claim_financial_growth",
    "claim_report_financial_section", "report_merged",
):
    a = store.get_version("live_test_c", aid, 2)
    if a:
        print(f"  {aid}@v2: {a.validation_status}")

# final_report input refs
final = store.get_version("live_test_c", "report_merged", 3)
print(f"\n=== final_report@v3 input_artifact_ids ===")
for r in final.input_artifact_ids:
    print(f"  {r.to_ref_string()}")

# recovered_claim_evidence_refs
c = store.get_version("live_test_c", "claim_financial_growth", 3)
print(f"\n=== claim@v3 evidence_ids ===")
for r in c.evidence_ids:
    print(f"  {r.to_ref_string()}")

# recovered_section_evidence_refs
s = store.get_version("live_test_c", "claim_report_financial_section", 3)
print(f"\n=== section@v3 evidence_ids ===")
for r in s.evidence_ids:
    print(f"  {r.to_ref_string()}")
