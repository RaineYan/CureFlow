"""Trace the offending ref via a per-step audit of
`validate_target_clean_path` against the post-recovery store.
"""
import sys
sys.path.insert(0, '.')
from pathlib import Path
from artifacts import (
    ArtifactRef, ArtifactStatus, ArtifactStore,
)
from provenance.graph import ArtifactGraph

STORE_DIR = Path(r"C:\Users\Lenovo\AppData\Local\Temp"
                 r"\poc_day10_faultA_live_test_c_pjl8w1f8"
                 r"\artifacts")
store = ArtifactStore(base_dir=str(STORE_DIR))
store.reload()
graph = ArtifactGraph.from_store(store, "live_test_c")

# Replicate the BFS from validate_target_clean_path exactly,
# but RECORD every ref we visit and its status.
target = ArtifactRef(run_id="live_test_c", artifact_id="report_merged", version=3)
visited: set = set()
stack = [target]
visit_log = []
while stack:
    cur = stack.pop()
    if cur.to_ref_string() in visited:
        continue
    visited.add(cur.to_ref_string())
    cur_art = store.get_version(cur.run_id, cur.artifact_id, cur.version)
    if cur_art is None:
        visit_log.append((cur, None, "MISSING"))
        continue
    visit_log.append((cur, cur_art.validation_status, cur_art.artifact_type))
    # Determine edge kind for the next traversal step
    edge_kind = ("INPUT" if cur_art.meta.get("task_kind", "tool") == "tool"
                 else "EVIDENCE")
    preds = list(graph._graph.predecessors(cur))
    for p in preds:
        if p.to_ref_string() in visited:
            continue
        et = graph.edge_type(p, cur)
        if et is None:
            continue
        types = et if isinstance(et, set) else {et}
        if not any(e.value == edge_kind for e in types):
            continue
        stack.append(p)

print("=== Full BFS upstream from report_merged@v3 (post-recovery) ===")
for ref, status, atype in visit_log:
    print(f"  {ref.artifact_id}@v{ref.version}: status={status}, type={atype}")
print()
print(f"Total visited: {len(visit_log)}")
# Check if any are in bad states
bad = [(r, s) for r, s, _ in visit_log if s in ("INVALIDATED", "CONTAMINATED")]
print(f"Bad refs in path: {len(bad)}")
for r, s in bad:
    print(f"  {r.artifact_id}@v{r.version}: {s}")
