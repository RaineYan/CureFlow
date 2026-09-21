"""Inspect CRASHED COARSE ledgers (CASE_B, CASE_D)."""
import json
from pathlib import Path

RUN2 = Path("experiments/results/exp02/stage15_real_run2")

for case in ("B", "D"):
    p = RUN2 / f"stage15_CASE_{case}_coarse_checkpoint_01_live_ledger.jsonl"
    print(f"=== CASE_{case} COARSE ledger ===")
    with p.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            rec = json.loads(line)
            top = rec.get("raw_response_shape", {}).get("top_level_keys", [])
            content = rec.get("sanitized_assistant_content", "")
            print(f"[{i}] unit={rec['execution_unit']}")
            print(f"    request_id    = {rec.get('request_id')}")
            print(f"    usage_type    = {rec.get('usage_type')}")
            print(f"    response_recv = {rec.get('response_received')}")
            print(f"    error_type    = {rec.get('error_type')}")
            print(f"    top_keys      = {top}")
            print(f"    content_snip  = {content[:240]}")
            print()
