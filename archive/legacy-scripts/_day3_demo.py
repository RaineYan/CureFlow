"""Day 3 demo: compute_revenue_growth twice (with upstream v2 between),
dump v1 and v2 JSON, showing refs auto-update and never drift."""
import json
import shutil
import tempfile
from pathlib import Path

from artifacts import ArtifactStore, ArtifactType
from provenance.decorator import RunContext, track_provenance
from workflow.tasks import extract_fields

CSV = str(Path(__file__).resolve().parent / "data" / "sample_company.csv")
RUN_ID = "run_demo"

@track_provenance(
    output_artifact_id="metric_revenue_growth",
    artifact_type=ArtifactType.METRIC,
)
def compute_revenue_growth(
    revenue_2025,
    revenue_2024,
    *,
    ctx: RunContext,
) -> float:
    return (revenue_2025.value - revenue_2024.value) / revenue_2024.value


tmp = tempfile.mkdtemp(prefix="day3_demo_")
store = ArtifactStore(base_dir=tmp)

print(f"=== Day 3 demo | csv={CSV} | run_id={RUN_ID} ===\n")

# Setup fields
extract_fields(CSV, RUN_ID, store)
ctx = RunContext(run_id=RUN_ID, store=store)
rev25_v1 = store.get(RUN_ID, "field_revenue_2025")
rev24 = store.get(RUN_ID, "field_revenue_2024")

# 1) First compute -> metric v1
m1 = compute_revenue_growth(rev25_v1, rev24, ctx=ctx)
print(f"[1] First compute -> {m1.artifact_id}@v{m1.version}")
print(f"    value = {m1.value}")
print(f"    latency_ms = {m1.execution_cost.latency_ms}")
print(f"    refs = {[(r.artifact_id, r.version) for r in m1.input_artifact_ids]}")
print()

# 2) Tamper upstream: field_revenue_2025 v2
rev25_v2 = rev25_v1.model_copy(update={"value": 0.0, "version": 2})
store.save(rev25_v2)
rev25_latest = store.get(RUN_ID, "field_revenue_2025")
print(f"[2] Upstream tampered: field_revenue_2025@v{rev25_latest.version}, value={rev25_latest.value}\n")

# 3) Second compute -> metric v2, refs auto-update
m2 = compute_revenue_growth(rev25_latest, rev24, ctx=ctx)
print(f"[3] Second compute -> {m2.artifact_id}@v{m2.version}")
print(f"    value = {m2.value}")
print(f"    latency_ms = {m2.execution_cost.latency_ms}")
print(f"    refs = {[(r.artifact_id, r.version) for r in m2.input_artifact_ids]}")
print()

# 4) Dump v1 JSON
print("[4] === metric_revenue_growth@v1 (full JSON) ===")
m1_dump = store.get_version(RUN_ID, m1.artifact_id, 1)
print(json.dumps(m1_dump.to_dict(), ensure_ascii=False, indent=2))
print()

# 5) Dump v2 JSON
print("[5] === metric_revenue_growth@v2 (full JSON) ===")
m2_dump = store.get_version(RUN_ID, m2.artifact_id, 2)
print(json.dumps(m2_dump.to_dict(), ensure_ascii=False, indent=2))

shutil.rmtree(tmp, ignore_errors=True)
