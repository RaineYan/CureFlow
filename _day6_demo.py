"""Day 6 demo: baseline workflow + selective-version re-merge."""
import json
import shutil
import tempfile
from pathlib import Path

from artifacts import ArtifactStore, ArtifactRef
from llm import default_workflow_llm
from provenance.decorator import RunContext
from provenance.graph import ArtifactGraph
from workflow.claim_tasks import (
    generate_financial_claim,
    generate_financial_section,
    merge_report,
)
from workflow.runner import run_baseline_workflow

CSV = str(Path(__file__).resolve().parent / "data" / "sample_company.csv")
RUN_ID = "run_demo"

tmp = tempfile.mkdtemp(prefix="day6_demo_")
store = ArtifactStore(base_dir=tmp)
llm = default_workflow_llm()

# 1) Baseline workflow (v1 of everything)
print("=" * 60)
print("[1] Baseline workflow -> run v1 end-to-end")
print("=" * 60)
result_v1 = run_baseline_workflow(CSV, RUN_ID, store, llm)
print(f"  metrics computed: {list(result_v1.metrics.keys())}")
print(f"  claims produced:  {list(result_v1.claims.keys())}")
print(f"  sections produced:{list(result_v1.sections.keys())}")
print(f"  merged_report:    {result_v1.merged_report.artifact_id}@v{result_v1.merged_report.version}")
print(f"  total LLM calls:  {result_v1.total_llm_calls}")
print(f"  total tokens:     {result_v1.total_tokens}")
print()

# 2) Financial Claim v1 evidence vs input
print("=" * 60)
print("[2] Financial Claim v1: input=3 metrics, evidence=2 (cost_ratio excluded)")
print("=" * 60)
fc_v1 = result_v1.claims["financial"]
print(f"  content: {fc_v1.content}")
print(f"  input_artifact_ids ({len(fc_v1.input_artifact_ids)}):")
for r in fc_v1.input_artifact_ids:
    print(f"    - {r.artifact_id}@v{r.version}")
print(f"  evidence_ids ({len(fc_v1.evidence_ids)}):")
for r in fc_v1.evidence_ids:
    print(f"    - {r.artifact_id}@v{r.version}")
print()

# 3) Two merged reports
print("=" * 60)
print("[3] merged_report JSON dump")
print("=" * 60)
v1 = store.get_version(RUN_ID, "report_merged", 1)
print("--- report_merged@v1 ---")
print(json.dumps(v1.to_dict(), ensure_ascii=False, indent=2))
print()

# 4) Selective re-merge: tamper upstream, regen financial claim+section, re-merge
print("=" * 60)
print("[4] Selective re-merge: metric_revenue_growth -> v2, financial -> v2,")
print("    user/operation stay v1, merge_report -> v2")
print("=" * 60)
rg = store.get(RUN_ID, "metric_revenue_growth")
assert rg is not None
store.save(rg.model_copy(update={"value": 0.5, "version": 2}))
ctx = RunContext(run_id=RUN_ID, store=store)
fc_v2 = generate_financial_claim(
    store.get(RUN_ID, "metric_revenue_growth"),
    store.get(RUN_ID, "metric_profit_margin"),
    store.get(RUN_ID, "metric_cost_ratio"),
    ctx=ctx, llm_client=llm,
)
fs_v2 = generate_financial_section(fc_v2, ctx=ctx, llm_client=llm)
us_v1 = result_v1.sections["user"]
os_v1 = result_v1.sections["operation"]
merged_v2 = merge_report(fs_v2, us_v1, os_v1, ctx=ctx)
print(f"  financial_section: v{fs_v2.version}")
print(f"  user_section:      v{us_v1.version}  (unchanged)")
print(f"  operation_section: v{os_v1.version}  (unchanged)")
print(f"  merged_report:     v{merged_v2.version}")
print()
v2 = store.get_version(RUN_ID, "report_merged", 2)
print("--- report_merged@v2 (input_artifact_ids only) ---")
for r in v2.input_artifact_ids:
    print(f"  - {r.artifact_id}@v{r.version}")
print()

# 5) Graph
print("=" * 60)
print("[5] ArtifactGraph (filtered to claim->section->merged)")
print("=" * 60)
g = ArtifactGraph.from_store(store, RUN_ID)
for line in g.pretty_print().splitlines():
    if any(s in line for s in ("claim_", "report_merged", "metric_revenue_growth")):
        print("  " + line)

shutil.rmtree(tmp, ignore_errors=True)
