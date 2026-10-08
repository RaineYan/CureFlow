"""Day 4 demo: build the full DAG, then tamper upstream to v2, rebuild graph,
print it so the v1/v2 split is visible."""
import shutil
import tempfile
from pathlib import Path

from artifacts import ArtifactStore, ArtifactType
from provenance.decorator import RunContext, track_provenance
from provenance.graph import ArtifactGraph
from workflow.tasks import (
    compute_conversion_rate,
    compute_cost_ratio,
    compute_order_growth,
    compute_profit_margin,
    compute_revenue_growth,
    compute_user_growth,
    extract_fields,
)

CSV = str(Path(__file__).resolve().parent / "data" / "sample_company.csv")
RUN_ID = "run_demo"


@track_provenance(
    output_artifact_id="metric_revenue_growth",
    artifact_type=ArtifactType.METRIC,
)
def mrg_decorated(a, b, *, ctx):
    return (a.value - b.value) / b.value


def build_full_dag(store: ArtifactStore) -> None:
    extract_fields(CSV, RUN_ID, store)
    compute_revenue_growth(RUN_ID, store)
    compute_profit_margin(RUN_ID, store)
    compute_cost_ratio(RUN_ID, store)
    compute_user_growth(RUN_ID, store)
    compute_conversion_rate(RUN_ID, store)
    compute_order_growth(RUN_ID, store)


tmp = tempfile.mkdtemp(prefix="day4_demo_")
store = ArtifactStore(base_dir=tmp)
build_full_dag(store)

print(f"=== Day 4 demo | run_id={RUN_ID} ===\n")
print("[1] Graph at v1 only (initial DAG)\n")
g1 = ArtifactGraph.from_store(store, RUN_ID)
print(f"  nodes: {g1.node_count()}  edges: {g1.edge_count()}\n")
print(g1.pretty_print())

# Tamper upstream
f25 = store.get(RUN_ID, "field_revenue_2025")
f24 = store.get(RUN_ID, "field_revenue_2024")
store.save(f25.model_copy(update={"value": 0.0, "version": 2}))
ctx = RunContext(run_id=RUN_ID, store=store)
mrg_decorated(store.get(RUN_ID, "field_revenue_2025"), f24, ctx=ctx)

print("\n\n[2] Graph after upstream v2 + recomputed metric v2\n")
g2 = ArtifactGraph.from_store(store, RUN_ID)
print(f"  nodes: {g2.node_count()}  edges: {g2.edge_count()}\n")
print(g2.pretty_print())

print("\n\n[3] v1 vs v2 dual-path check")
v1_field = "field_revenue_2025@v1"
v2_field = "field_revenue_2025@v2"
v1_metric = "metric_revenue_growth@v1"
v2_metric = "metric_revenue_growth@v2"
print(f"  has_path({v1_field} -> {v1_metric}) = {g2.has_path(__import__('artifacts').ArtifactRef(run_id=RUN_ID, artifact_id='field_revenue_2025', version=1), __import__('artifacts').ArtifactRef(run_id=RUN_ID, artifact_id='metric_revenue_growth', version=1))}")
print(f"  has_path({v2_field} -> {v2_metric}) = {g2.has_path(__import__('artifacts').ArtifactRef(run_id=RUN_ID, artifact_id='field_revenue_2025', version=2), __import__('artifacts').ArtifactRef(run_id=RUN_ID, artifact_id='metric_revenue_growth', version=2))}")
print(f"  has_path({v1_field} -> {v2_metric}) = {g2.has_path(__import__('artifacts').ArtifactRef(run_id=RUN_ID, artifact_id='field_revenue_2025', version=1), __import__('artifacts').ArtifactRef(run_id=RUN_ID, artifact_id='metric_revenue_growth', version=2))}  (should be False)")

shutil.rmtree(tmp, ignore_errors=True)
