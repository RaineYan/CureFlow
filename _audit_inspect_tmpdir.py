"""Audit: inspect the live_test_c tmpdir."""
import os
from pathlib import Path

p = Path(r"C:\Users\Lenovo\AppData\Local\Temp\poc_day10_faultA_live_test_c_pjl8w1f8")
if p.exists():
    print(f"Path exists: {p}")
    for sub in p.iterdir():
        print(f"  subdir: {sub}")
        for f in sub.rglob("*"):
            print(f"    {f}")
else:
    print("Path does not exist")

# Also check for any later ones
import glob
for d in glob.glob(r"C:\Users\Lenovo\AppData\Local\Temp\poc_day10_faultA_live_test_c*"):
    print(f"\n=== {d} ===")
    for sub in Path(d).iterdir():
        for f in sub.rglob("*"):
            print(f"  {f}")
