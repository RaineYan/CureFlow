"""Read-only verifier for the historical EXP-02 evidence SHA manifest."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "experiments/results/exp02/m3_cross_provider/preflight/m3_historical_evidence_sha256_baseline.json"


def verify(manifest: Path = MANIFEST, root: Path = ROOT) -> tuple[int, list[str]]:
    data = json.loads(manifest.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("Invalid evidence manifest")
    root = root.resolve()
    count = 0
    errors: list[str] = []
    for group, record in data.items():
        if not isinstance(record, dict) or not isinstance(record.get("shas"), dict):
            raise ValueError(f"Invalid group: {group}")
        if record.get("file_count") != len(record["shas"]):
            errors.append(f"{group}: file count differs from manifest")
        for raw_path, expected in record["shas"].items():
            count += 1
            path = (root / raw_path.replace("\\", "/")).resolve()
            if not path.is_relative_to(root) or not re.fullmatch(r"[0-9a-f]{64}", str(expected)):
                errors.append(f"{group}: invalid path or digest")
                continue
            if not path.is_file():
                errors.append(f"{group}: missing {raw_path}")
                continue
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != expected:
                errors.append(f"{group}: SHA mismatch {raw_path}")
    return count, errors


if __name__ == "__main__":
    total, mismatches = verify()
    for problem in mismatches:
        print(problem)
    print(f"Verified {total} files, {len(mismatches)} mismatch")
    if mismatches:
        raise SystemExit(1)
