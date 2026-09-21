"""临时脚本：跑一次 extract_fields + 6 个 compute_*，输出真实 Artifact 列表和 metric 数值。"""
import json
import shutil
import tempfile
from pathlib import Path

from artifacts import ArtifactStore
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

tmp = tempfile.mkdtemp(prefix="day2_demo_")
store = ArtifactStore(base_dir=tmp)

print(f"=== Day 2 demo | csv={CSV} | run_id={RUN_ID} ===\n")

# 1) Field extraction
fields = extract_fields(CSV, RUN_ID, store)
print(f"[1] extract_fields produced {len(fields)} FieldArtifacts + 1 RawDataArtifact")
print(f"    RawDataArtifact: id=raw_data v1, meta={store.get(RUN_ID, 'raw_data').meta}\n")

print("[2] FieldArtifact values (from CSV):")
for fid in sorted(fields):
    a = fields[fid]
    print(f"    {fid:30s} v{a.version}  value={a.value!r:>15}  meta={a.meta}")
print()

# 2) Metric computations
print("[3] Metric computations (deterministic, exact refs):")
metrics = [
    compute_revenue_growth(RUN_ID, store),
    compute_profit_margin(RUN_ID, store),
    compute_cost_ratio(RUN_ID, store),
    compute_user_growth(RUN_ID, store),
    compute_conversion_rate(RUN_ID, store),
    compute_order_growth(RUN_ID, store),
]
for m in metrics:
    refs = ", ".join(
        f"{r.artifact_id}@v{r.version}" for r in m.input_artifact_ids
    )
    print(f"    {m.artifact_id:30s} = {m.value:.6f}    [{refs}]")
print()

# 3) Dump one example Artifact in full JSON
print("[4] Example Artifact (full JSON): metric_revenue_growth")
example = store.get(RUN_ID, "metric_revenue_growth")
example_json = json.dumps(example.to_dict(), ensure_ascii=False, indent=2)
print(example_json)
print()

# 4) Dump one FieldArtifact in full JSON
print("[5] Example FieldArtifact (full JSON): field_revenue_2025")
field_example = store.get(RUN_ID, "field_revenue_2025")
field_json = json.dumps(field_example.to_dict(), ensure_ascii=False, indent=2)
print(field_json)

shutil.rmtree(tmp, ignore_errors=True)
