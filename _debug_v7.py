"""Day 7 debug: print graph structure and propagation result."""
import shutil
import tempfile
from pathlib import Path

from artifacts import ArtifactStore
from contamination import detect_field_faults, propagate_contamination
from experiments import inject_field_value_tamper
from llm import default_workflow_llm
from provenance.decorator import RunContext, track_provenance
from provenance.graph import ArtifactGraph, EdgeType
from workflow.claim_tasks import (
    generate_financial_claim,
    generate_financial_section,
    merge_report,
)
from workflow.runner import run_baseline_workflow


@track_provenance(output_artifact_id="metric_revenue_growth", artifact_type="Metric")
def mrg(r25, r24, *, ctx):
    return (r25.value - r24.value) / r24.value


@track_provenance(output_artifact_id="metric_profit_margin", artifact_type="Metric")
def mpm(r25, c25, *, ctx):
    return (r25.value - c25.value) / r25.value


@track_provenance(output_artifact_id="metric_cost_ratio", artifact_type="Metric")
def mcr(c25, r25, *, ctx):
    return c25.value / r25.value


tmp = tempfile.mkdtemp()
store = ArtifactStore(base_dir=tmp)
result = run_baseline_workflow("data/sample_company.csv", "run_x", store, default_workflow_llm())

inject_field_value_tamper(store, "run_x", "field_revenue_2025", factor=10.0)
ctx = RunContext(run_id="run_x", store=store)
mrg(store.get("run_x", "field_revenue_2025"), store.get("run_x", "field_revenue_2024"), ctx=ctx)
mpm(store.get("run_x", "field_revenue_2025"), store.get("run_x", "field_cost_2025"), ctx=ctx)
mcr(store.get("run_x", "field_cost_2025"), store.get("run_x", "field_revenue_2025"), ctx=ctx)
fc = generate_financial_claim(
    store.get("run_x", "metric_revenue_growth"),
    store.get("run_x", "metric_profit_margin"),
    store.get("run_x", "metric_cost_ratio"),
    ctx=ctx, llm_client=default_workflow_llm(),
)
fs = generate_financial_section(fc, ctx=ctx, llm_client=default_workflow_llm())
merge_report(fs, result.sections["user"], result.sections["operation"], ctx=ctx)

detect_field_faults("run_x", store)
g = ArtifactGraph.from_store(store, "run_x")

print("=== EDGES ===")
for src in sorted(g._graph.nodes(), key=lambda r: (r.artifact_id, r.version)):
    for dst in g._graph.successors(src):
        et = g.edge_type(src, dst)
        if et is None:
            continue
        if isinstance(et, set):
            et_str = "/".join(sorted(e.value for e in et))
        else:
            et_str = et.value
        print(f"  {src.artifact_id}@v{src.version} -> {dst.artifact_id}@v{dst.version} [{et_str}]")

print()
print("=== PROPAGATION ===")
r = propagate_contamination(g, store, "run_x")
print(f"invalidated:  {[str(x) for x in r.invalidated]}")
print(f"contaminated: {[str(x) for x in r.contaminated]}")

shutil.rmtree(tmp)
