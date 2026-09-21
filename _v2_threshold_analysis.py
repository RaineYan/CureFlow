import json
from pathlib import Path

from recovery.threshold_analysis import analyze_thresholds, render_threshold_report


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    data = analyze_thresholds()
    json_path = root / "artifacts" / "threshold_sensitivity.json"
    md_path = root / "artifacts" / "threshold_sensitivity.md"
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_threshold_report(data), encoding="utf-8")
    print(f"JSON={json_path}")
    print(f"MARKDOWN={md_path}")
