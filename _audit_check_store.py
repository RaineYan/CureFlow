"""Audit: check if live_test_c store is still persisted."""
from pathlib import Path
import glob
import tempfile
import os

# Check standard locations
candidates = [
    Path("experiments/results/live_test_c_store"),
    Path("experiments/results/day13_6_test_c_store"),
    Path("live_test_c"),
    Path("/tmp/live_test_c"),
    Path("experiments/results/day13_5_test_c_store"),
]
for p in candidates:
    if p.exists():
        files = list(p.rglob("*.json"))[:5]
        print(f"FOUND {p} ({len(files)} json files):")
        for f in files:
            print(f"  {f}")
    else:
        print(f"  {p}: absent")

# Search for tmpdirs
print("\n=== searching tmpdirs ===")
import platform
tmp = tempfile.gettempdir()
print(f"tmpdir: {tmp}")
for pat in ("*poc_day10*", "*live_test*", "*faultA*"):
    matches = glob.glob(os.path.join(tmp, pat))
    for m in matches[:5]:
        print(f"  {m}")
