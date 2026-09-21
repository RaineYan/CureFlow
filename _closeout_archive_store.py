"""DAY 13.6 CLOSEOUT: byte-preserving archive of the live_test_c
post-recovery store to a stable on-disk path.  This is the
single source of truth for all future audits.

NO LLM CALL.  Pure file copy.  No content rewriting.
"""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, '.')

SRC = Path(r"C:\Users\Lenovo\AppData\Local\Temp"
           r"\poc_day10_faultA_live_test_c_pjl8w1f8"
           r"\artifacts")
DST = Path("experiments/results/day13_6_live_test_c_store")

if DST.exists():
    shutil.rmtree(DST)
DST.parent.mkdir(parents=True, exist_ok=True)
shutil.copytree(SRC, DST)
print(f"Archived {SRC} -> {DST}")

# Verify by reloading
from artifacts import ArtifactStore
store = ArtifactStore(base_dir=str(DST))
store.reload()
# List all artifacts
all_v = []
for aid_v in store._by_ref:
    all_v.append(aid_v)
print(f"Total (run_id, artifact_id, version) entries: {len(all_v)}")

# Specifically verify the v3 recovered artifacts are present
v3_refs = [
    ("live_test_c", "field_revenue_2025", 3),
    ("live_test_c", "metric_revenue_growth", 3),
    ("live_test_c", "metric_profit_margin", 3),
    ("live_test_c", "metric_cost_ratio", 3),
    ("live_test_c", "claim_financial_growth", 3),
    ("live_test_c", "claim_report_financial_section", 3),
    ("live_test_c", "report_merged", 3),
]
print("\n=== v3 recovered refs readable from archive ===")
for (rid, aid, v) in v3_refs:
    a = store.get_version(rid, aid, v)
    if a is None:
        print(f"  {aid}@v{v}: MISSING")
    else:
        print(f"  {aid}@v{v}: status={a.validation_status}, "
              f"value={'set' if a.value is not None else 'null'}")
