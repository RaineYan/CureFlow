"""OFFLINE clean-path audit on the persisted live_test_c store.

The real recovery produced @v3 of the financial chain + merged
report.  We rebuild the store + graph from disk and re-run
`validate_target_clean_path` to find the offending ref.
"""
import sys
sys.path.insert(0, '.')

from pathlib import Path
from artifacts import (
    ArtifactRef, ArtifactStatus, ArtifactStore, ArtifactType,
)
from provenance.graph import ArtifactGraph
from recovery.executor import (
    validate_target_clean_path,
    validate_recovery_execution_integrity,
)

STORE_DIR = Path(r"C:\Users\Lenovo\AppData\Local\Temp"
                 r"\poc_day10_faultA_live_test_c_pjl8w1f8"
                 r"\artifacts")
print("=== Loading store from disk ===")
store = ArtifactStore(base_dir=str(STORE_DIR))
store.reload()

# What we expect (from user report):
#   - report_merged@v3 is the final target
#   - section@v3, claim@v3, 3 metrics@v3, field@v3 are recovered
#   - user_section@v1, op_section@v1 stayed @v1
#   - the @v2 chain is still CONTAMINATED (historical)
print("\n=== Inventory by artifact_id+version ===")
for aid in ("field_revenue_2025", "field_revenue_2024",
            "metric_revenue_growth", "metric_profit_margin",
            "metric_cost_ratio", "claim_financial_growth",
            "claim_report_financial_section", "report_merged",
            "claim_report_user_section",
            "claim_report_operation_section"):
    versions = store.list_versions("live_test_c", aid)
    if not versions:
        print(f"  {aid}: ABSENT")
        continue
    for v in versions:
        a = store.get_version("live_test_c", aid, v)
        print(f"  {aid}@v{v}: status={a.validation_status}, "
              f"type={a.artifact_type}, "
              f"producer={a.producer_task}, "
              f"value={'set' if a.value is not None else 'null'}")

# Build a fresh graph from the post-recovery store
print("\n=== Building fresh graph from post-recovery store ===")
graph = ArtifactGraph.from_store(store, "live_test_c")
n_nodes = len(graph._graph.nodes)
print(f"  graph has {n_nodes} nodes")
# Check the v3 recovered refs
for aid, v in (
    ("report_merged", 3),
    ("claim_report_financial_section", 3),
    ("claim_financial_growth", 3),
    ("metric_revenue_growth", 3),
    ("metric_profit_margin", 3),
    ("metric_cost_ratio", 3),
    ("field_revenue_2025", 3),
):
    ref = ArtifactRef(run_id="live_test_c", artifact_id=aid, version=v)
    print(f"  graph has {ref.to_ref_string()}: {ref in graph._graph.nodes}")

# Run clean-path on report_merged@v3
target = ArtifactRef(run_id="live_test_c", artifact_id="report_merged", version=3)
print(f"\n=== Clean-path audit on {target.to_ref_string()} ===")
ok, reason = validate_target_clean_path(target, graph, store)
print(f"  passed = {ok}")
print(f"  reason = {reason}")
