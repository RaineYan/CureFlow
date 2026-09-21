"""Day 7 demo: main case (revenue tamper) + cost_ratio-only case."""
import shutil
import tempfile
from pathlib import Path

from artifacts import Artifact, ArtifactRef, ArtifactStatus, ArtifactStore, ArtifactType
from contamination import (
    detect_field_faults,
    propagate_contamination,
)
from experiments import inject_field_value_tamper
from llm import default_workflow_llm
from provenance.decorator import RunContext, track_provenance
from provenance.graph import ArtifactGraph
from workflow.claim_tasks import (
    generate_financial_claim,
    generate_financial_section,
    merge_report,
)
from workflow.runner import run_baseline_workflow


@track_provenance(output_artifact_id="metric_revenue_growth", artifact_type=ArtifactType.METRIC)
def mrg(r25, r24, *, ctx):
    return (r25.value - r24.value) / r24.value


@track_provenance(output_artifact_id="metric_profit_margin", artifact_type=ArtifactType.METRIC)
def mpm(r25, c25, *, ctx):
    return (r25.value - c25.value) / r25.value


@track_provenance(output_artifact_id="metric_cost_ratio", artifact_type=ArtifactType.METRIC)
def mcr(c25, r25, *, ctx):
    return c25.value / r25.value


def fmt(result):
    print("    FAULT SOURCE / INVALIDATED:")
    for r in result.invalidated:
        print(f"      {r.artifact_id}@v{r.version}")
    print("    CONTAMINATED:")
    for r in result.contaminated:
        print(f"      {r.artifact_id}@v{r.version}")
    print("    PROPAGATION EDGES:")
    for e in result.propagation_edges:
        print(f"      {e.source.artifact_id}@v{e.source.version} --[{e.via.value}]--> {e.target.artifact_id}@v{e.target.version}")
    print("    UNAFFECTED (key nodes):")
    unaffected_ids = {r.artifact_id for r in result.unaffected}
    for aid in sorted(unaffected_ids):
        versions = sorted(r.version for r in result.unaffected if r.artifact_id == aid)
        vs = ",".join(f"v{v}" for v in versions)
        print(f"      {aid} ({vs})")


# ============================================================
# Main Case: revenue_2025 tampered (×10)
# ============================================================
print("=" * 70)
print("MAIN CASE: revenue_2025 value tamper (×10) in run_fault")
print("=" * 70)

tmp = tempfile.mkdtemp(prefix="day7_main_")
store = ArtifactStore(base_dir=tmp)
result = run_baseline_workflow(
    "data/sample_company.csv", "run_fault", store, default_workflow_llm(),
)

# Tamper
inject_field_value_tamper(store, "run_fault", "field_revenue_2025", factor=10.0)
ctx = RunContext(run_id="run_fault", store=store)

# Re-run the 3 financial metrics using the v2 field
mrg(store.get("run_fault", "field_revenue_2025"),
    store.get("run_fault", "field_revenue_2024"), ctx=ctx)
mpm(store.get("run_fault", "field_revenue_2025"),
    store.get("run_fault", "field_cost_2025"), ctx=ctx)
mcr(store.get("run_fault", "field_cost_2025"),
    store.get("run_fault", "field_revenue_2025"), ctx=ctx)

# Re-generate financial claim + section (v2), re-merge
fc = generate_financial_claim(
    store.get("run_fault", "metric_revenue_growth"),
    store.get("run_fault", "metric_profit_margin"),
    store.get("run_fault", "metric_cost_ratio"),
    ctx=ctx, llm_client=default_workflow_llm(),
)
fs = generate_financial_section(fc, ctx=ctx, llm_client=default_workflow_llm())
merge_report(fs, result.sections["user"], result.sections["operation"], ctx=ctx)

# Validate + propagate
detect_field_faults("run_fault", store)
g = ArtifactGraph.from_store(store, "run_fault")
r = propagate_contamination(g, store, "run_fault")
fmt(r)
shutil.rmtree(tmp)

# ============================================================
# Control Case: only cost_ratio is INVALIDATED (input-only, not evidence)
# ============================================================
print()
print("=" * 70)
print("CONTROL CASE: only cost_ratio INVALIDATED, revenue/profit stay clean")
print("=" * 70)

tmp = tempfile.mkdtemp(prefix="day7_ctrl_")
store = ArtifactStore(base_dir=tmp)
result2 = run_baseline_workflow(
    "data/sample_company.csv", "run_ctrl", store, default_workflow_llm(),
)

# Manually mark cost_ratio INVALIDATED (no value change)
store.update_status(
    "run_ctrl", "metric_cost_ratio", ArtifactStatus.INVALIDATED,
)

# Detect + propagate
g2 = ArtifactGraph.from_store(store, "run_ctrl")
r2 = propagate_contamination(g2, store, "run_ctrl")
fmt(r2)
shutil.rmtree(tmp)
