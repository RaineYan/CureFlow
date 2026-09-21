"""Day 5 demo: build metric DAG, then claim, then tamper upstream to v2,
rebuild claim, dump v1/v2 JSONs and the EVIDENCE-bearing graph."""
import json
import shutil
import tempfile
from pathlib import Path

from artifacts import ArtifactStore
from llm import FakeLLMClient
from provenance.decorator import RunContext
from provenance.graph import ArtifactGraph
from workflow.claim_tasks import generate_financial_claim
from workflow.tasks import (
    compute_cost_ratio,
    compute_profit_margin,
    compute_revenue_growth,
    extract_fields,
)

CSV = str(Path(__file__).resolve().parent / "data" / "sample_company.csv")
RUN_ID = "run_demo"
llm = FakeLLMClient()

tmp = tempfile.mkdtemp(prefix="day5_demo_")
store = ArtifactStore(base_dir=tmp)

# Setup: fields + 3 metrics
extract_fields(CSV, RUN_ID, store)
compute_revenue_growth(RUN_ID, store)
compute_profit_margin(RUN_ID, store)
compute_cost_ratio(RUN_ID, store)
ctx = RunContext(run_id=RUN_ID, store=store)

# 1) Claim v1
claim_v1 = generate_financial_claim(
    store.get(RUN_ID, "metric_revenue_growth"),
    store.get(RUN_ID, "metric_profit_margin"),
    store.get(RUN_ID, "metric_cost_ratio"),
    ctx=ctx, llm_client=llm,
)
print(f"[1] First claim -> {claim_v1.artifact_id}@v{claim_v1.version}")
print(f"    content: {claim_v1.content}")
print(f"    evidence: {[(r.artifact_id, r.version) for r in claim_v1.evidence_ids]}")
print()

# 2) Tamper upstream: metric_revenue_growth v2
mrg_v1 = store.get(RUN_ID, "metric_revenue_growth")
store.save(mrg_v1.model_copy(update={"value": 0.5, "version": 2}))
mrg_v2 = store.get(RUN_ID, "metric_revenue_growth")
print(f"[2] Upstream tampered: metric_revenue_growth@v{mrg_v2.version}, value={mrg_v2.value}\n")

# 3) Claim v2
claim_v2 = generate_financial_claim(
    mrg_v2,
    store.get(RUN_ID, "metric_profit_margin"),
    store.get(RUN_ID, "metric_cost_ratio"),
    ctx=ctx, llm_client=llm,
)
print(f"[3] Second claim -> {claim_v2.artifact_id}@v{claim_v2.version}")
print(f"    content: {claim_v2.content}")
print(f"    evidence: {[(r.artifact_id, r.version) for r in claim_v2.evidence_ids]}")
print()

# 4) Dump v1 JSON
print("[4] === claim_financial_growth@v1 (full JSON) ===")
v1_dump = store.get_version(RUN_ID, "claim_financial_growth", 1)
print(json.dumps(v1_dump.to_dict(), ensure_ascii=False, indent=2))
print()

# 5) Dump v2 JSON
print("[5] === claim_financial_growth@v2 (full JSON) ===")
v2_dump = store.get_version(RUN_ID, "claim_financial_growth", 2)
print(json.dumps(v2_dump.to_dict(), ensure_ascii=False, indent=2))
print()

# 6) Graph
print("[6] === ArtifactGraph (filter to claim + 3 metrics) ===")
g = ArtifactGraph.from_store(store, RUN_ID)
for line in g.pretty_print().splitlines():
    if "claim_financial" in line or "metric_revenue" in line or "metric_profit" in line or "metric_cost" in line:
        print("  " + line)

shutil.rmtree(tmp, ignore_errors=True)
