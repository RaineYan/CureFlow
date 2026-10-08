"""Inspect the pre-pilot trial JSONs — what made them
success=False (8-condition TrialSuccessReport check)?
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PRE = Path("experiments/results/day14/pre_pilot")

for p in sorted(PRE.glob("*_pre01.json")):
    d = json.loads(p.read_text(encoding="utf-8"))
    print("=" * 64)
    print(p.name)
    print("=" * 64)
    print(f"  success          : {d['success']}")
    print(f"  failure_reason   : {d.get('failure_reason')!r}")
    print(f"  real_metrics     : {d.get('real_metrics')}")
    print()
    sr = d.get("success_report", {})
    if sr:
        for k, v in sr.items():
            if k == "conditions":
                print(f"  conditions:")
                for c, cv in v.items():
                    print(f"    {c}: {cv}")
            else:
                print(f"  {k}: {v}")
    print()
