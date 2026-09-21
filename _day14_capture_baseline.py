"""Day 14 Pilot — capture Day 13 evidence SHA-256 baseline.

The 10 files + 1 directory listed in the user's spec
section 1 are READ-ONLY for Day 14.  This script snapshots
their current SHA-256 digests so the pilot can verify
they are unchanged after pytest + after the pilot itself.
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path("experiments/results")
OUT = ROOT / "day14_day13_frozen_evidence.json"

DAY13_FILES = (
    "day13_5_live_gate.json",
    "day13_5_test_a_state.json",
    "day13_5_test_a_state_PASS_backup.json",
    "day13_5_test_b_state.json",
    "day13_6_real_test_c_failure_001.json",
    "day13_6_real_test_c_failure_002.json",
    "day13_6_live_test_c_offline_reaudit.json",
    "day13_6_live_test_c_store",
    "day13_6_final_real_live_gate.json",
    "day13_6_final_manifest.json",
)


def _hash_file(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


def _hash_dir(p: Path) -> str:
    digests = []
    for f in sorted(p.rglob("*.json")):
        hf = hashlib.sha256()
        hf.update(f.read_bytes())
        digests.append(
            f"{hf.hexdigest()}  {f.relative_to(p).as_posix()}"
        )
    return hashlib.sha256(
        "\n".join(digests).encode("utf-8")
    ).hexdigest()


def main() -> int:
    if OUT.exists():
        print(f"WARN: {OUT} already exists.  Refusing to overwrite.")
        existing = json.loads(OUT.read_text(encoding="utf-8"))
        print("Existing baseline saved_at_unix_ms:",
              existing.get("saved_at_unix_ms"))
        return 1
    files = {}
    missing = []
    for name in DAY13_FILES:
        p = ROOT / name
        if not p.exists():
            missing.append(name)
            files[name] = {"exists": False}
            continue
        if p.is_file():
            files[name] = {
                "exists": True,
                "kind": "file",
                "sha256": _hash_file(p),
                "size_bytes": p.stat().st_size,
            }
        elif p.is_dir():
            json_files = sorted(p.rglob("*.json"))
            files[name] = {
                "exists": True,
                "kind": "directory",
                "sha256": _hash_dir(p),
                "size_bytes": sum(f.stat().st_size for f in p.rglob("*")),
                "artifact_json_count": len(json_files),
            }
    payload = {
        "schema_version": 1,
        "purpose": "Day 14 Pilot — frozen SHA-256 baseline of Day 13 evidence",
        "saved_at_unix_ms": 1787200000000,
        "files": files,
        "missing": missing,
        "all_present": len(missing) == 0,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Saved baseline: {OUT}")
    print(f"all_present: {payload['all_present']}")
    for name, info in files.items():
        if not info.get("exists"):
            continue
        sha = info.get("sha256", "?")
        print(f"  {sha[:16]}  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
