"""Record SHA256 of all real experiment files for the
post-pytest stability test."""
import hashlib
import json
from pathlib import Path

REAL_FILES = [
    "experiments/results/day13_5_live_gate.json",
    "experiments/results/day13_5_test_a_state.json",
    "experiments/results/day13_5_test_b_state.json",
    "experiments/results/day13_5_real_failure_a_001.json",
    "experiments/results/day13_5_test_a_state_PASS_backup.json",
    "experiments/results/day13_6_real_test_c_failure_001.json",
    "experiments/results/day13_6_real_test_c_failure_002.json",
    "experiments/results/day13_6_live_test_c_offline_reaudit.json",
    "experiments/results/day13_6_final_real_live_gate.json",
    "experiments/results/day13_6_final_manifest.json",
]

# Add the archived store directory
ARCHIVED = "experiments/results/day13_6_live_test_c_store"


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _dir_hash(p: Path) -> str:
    files = sorted(p.rglob("*.json"))
    digests = []
    for f in files:
        hf = hashlib.sha256()
        with open(f, "rb") as fh:
            for chunk in iter(lambda: fh.read(8192), b""):
                hf.update(chunk)
        digests.append(f"{hf.hexdigest()}  {f.relative_to(p).as_posix()}")
    combined = "\n".join(digests).encode("utf-8")
    return hashlib.sha256(combined).hexdigest()


def snapshot(out_path: str = "_forensic_baseline.json") -> dict:
    snap = {"files": {}, "directories": {}}
    for f in REAL_FILES:
        p = Path(f)
        if p.exists():
            snap["files"][f] = {
                "sha256": _sha256(p),
                "size_bytes": p.stat().st_size,
            }
    p = Path(ARCHIVED)
    if p.exists():
        snap["directories"][ARCHIVED] = {
            "sha256": _dir_hash(p),
            "size_bytes": sum(f.stat().st_size for f in p.rglob("*")),
            "json_count": len(list(p.rglob("*.json"))),
        }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)
    return snap


if __name__ == "__main__":
    snap = snapshot()
    print(f"=== Baseline snapshot (file count: "
          f"{len(snap['files'])}, dir count: "
          f"{len(snap['directories'])}) ===")
    for k, v in snap["files"].items():
        print(f"  {k}: sha256={v['sha256'][:16]}... size={v['size_bytes']}")
    for k, v in snap["directories"].items():
        print(f"  {k}/: sha256={v['sha256'][:16]}... "
              f"json_count={v['json_count']}")
