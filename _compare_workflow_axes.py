import json
for tid in ('case_a_selective_recovery_trial01_real02',
            'case_a_full_rerun_trial01R4'):
    d = json.loads(open(f'experiments/results/day14/pilot/{tid}.json', encoding='utf-8').read())
    sr = d.get('success_report', {})
    print(f'--- {tid} ---')
    print(f'  legacy success: {d["success"]}')
    print(f'  success_report.success: {sr.get("success")}')
    print(f'  failure_reason: {d["failure_reason"]}')
    print(f'  success_report.failure_reason: {sr.get("failure_reason")}')
    for k, v in sr.get('checks', {}).items():
        print(f'    {k}: {v}')
    print()
