import sys
sys.path.insert(0, '.')
from experiments.scenario import build_main_fault_scenario
from artifacts import ArtifactRef, ArtifactStatus
sc, _ = build_main_fault_scenario('debug_run', csv_path='data/sample_company.csv')
# Inspect all field_revenue_2025 versions
print('=== All field_revenue_2025 versions ===')
for v in sc.store.list_versions(sc.run_id, 'field_revenue_2025'):
    a = sc.store.get_version(sc.run_id, 'field_revenue_2025', v)
    print(f'  v{v}: status={a.validation_status}, value={a.value}')
print('=== All report_merged versions ===')
for v in sc.store.list_versions(sc.run_id, 'report_merged'):
    a = sc.store.get_version(sc.run_id, 'report_merged', v)
    print(f'  v{v}: status={a.validation_status}')
print('=== contamination.invalidated ===')
for r in sc.contamination.invalidated:
    print(f'  {r.artifact_id}@v{r.version}: invalidated')
print('=== contamination.contaminated (count) ===')
print(f'  total: {len(sc.contamination.contaminated)}')
for r in sc.contamination.contaminated[:10]:
    print(f'  {r.artifact_id}@v{r.version}')
