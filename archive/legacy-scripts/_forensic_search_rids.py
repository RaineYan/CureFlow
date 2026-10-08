"""Search for the real Qwen request_ids in all artifacts."""
import json
from pathlib import Path

ROOT = Path("experiments/results")
RIDS = [
    "chatcmpl-09fdfcb4",  # Test A real
    "chatcmpl-faca3f9f",  # Test B real
    "chatcmpl-740bbd14",  # Test C claim real
    "chatcmpl-f7e7bab5",  # Test C section real
]
MOCK_PATTERNS = ["req-a", "req-b", "req-1", "req-2"]

print("=== Search for real Qwen request_ids ===")
for p in sorted(ROOT.rglob("*.json")):
    rel = p.relative_to(ROOT)
    try:
        text = p.read_text(encoding="utf-8")
    except Exception:
        continue
    found_real = [r for r in RIDS if r in text]
    found_mock = [m for m in MOCK_PATTERNS
                  if f'"{m}"' in text or f"'{m}'" in text]
    if found_real or found_mock:
        print(f"  {rel}:")
        if found_real:
            print(f"    REAL: {found_real}")
        if found_mock:
            print(f"    MOCK: {found_mock}")

# Also check for the ledger_record.request_id field
print()
print("=== Search for ledger_record fields ===")
for p in sorted(ROOT.rglob("*.json")):
    rel = p.relative_to(ROOT)
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        continue
    if "ledger_record" in data and isinstance(data["ledger_record"], dict):
        rid = data["ledger_record"].get("request_id", "?")
        in_t = data["ledger_record"].get("input_tokens", "?")
        out_t = data["ledger_record"].get("output_tokens", "?")
        phase = data["ledger_record"].get("phase", "?")
        unit = data["ledger_record"].get("execution_unit", "?")
        print(f"  {rel}: phase={phase} unit={unit} req={rid!r} "
              f"in={in_t} out={out_t}")
