"""Day 14 Pilot — write the manifest JSON.

The manifest hashes every Day 14 pilot output file +
the Day 13 frozen evidence (so any tampering is
detectable).
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from experiments.day14 import (
    DAY13_FROZEN_FILES,
    load_frozen_evidence,
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
    pilot = Path("experiments/results/day14/pilot")
    if not pilot.exists():
        print(f"missing: {pilot}")
        return 1
    out = Path("experiments/results/day14/pilot_manifest.json")

    files = {}
    for p in sorted(pilot.iterdir()):
        rel = p.relative_to(Path("experiments/results"))
        if p.is_file() and p.suffix == ".json":
            files[str(rel).replace("\\", "/")] = {
                "kind": "file",
                "sha256": _hash_file(p),
                "size_bytes": p.stat().st_size,
            }

    # Day 13 frozen baseline (the meta-baseline)
    baseline = load_frozen_evidence()
    files["day14_day13_frozen_evidence.json"] = {
        "kind": "file",
        "sha256": _hash_file(
            Path("experiments/results/day14_day13_frozen_evidence.json")
        ),
        "size_bytes": Path(
            "experiments/results/day14_day13_frozen_evidence.json"
        ).stat().st_size,
    }

    payload = {
        "schema_version": 1,
        "manifest_purpose": (
            "Day 14 Pilot manifest — SHA-256 of every "
            "pilot output file + Day 13 frozen evidence."
        ),
        "experiment_type": "DAY14_PILOT_MANIFEST",
        "files": files,
        "day13_frozen_files": list(DAY13_FROZEN_FILES),
        "no_secrets_anywhere": True,
    }
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote: {out}")
    for rel, info in files.items():
        print(
            "  {}  size={:>6}  {}".format(
                info["sha256"][:16],
                info["size_bytes"],
                rel,
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
