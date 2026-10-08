"""Generate UI-ready impact and execution-diff demo artifacts."""
import json
from pathlib import Path

from experiments.scenario import build_main_fault_scenario
from provenance.graph import ArtifactGraph
from provenance.impact_report import build_impact_view, build_trace_diff, render_trace_diff_markdown


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    scenario, _ = build_main_fault_scenario(run_id="impact_trace_demo")
    impact = build_impact_view(
        ArtifactGraph.from_store(scenario.store, scenario.run_id), scenario.contamination,
    )
    benchmark = json.loads((root / "experiments" / "results" / "v2_execution_benchmark" / "benchmark_data.json").read_text(encoding="utf-8"))
    case = next(item for item in benchmark["cases"] if item["scenario_id"] == "medium_financial")
    full = next(row for row in case["runs"] if row["requested_strategy"] == "FULL_RERUN")
    selective = next(row for row in case["runs"] if row["requested_strategy"] == "SELECTIVE_RECOVERY")
    diff = build_trace_diff(full, selective)
    output = root / "artifacts"
    output.mkdir(exist_ok=True)
    json_path = output / "impact_trace_diff_sample.json"
    md_path = output / "impact_trace_diff_sample.md"
    json_path.write_text(json.dumps({"impact": impact, "traceDiff": diff}, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_trace_diff_markdown(diff), encoding="utf-8")
    print(f"JSON={json_path}")
    print(f"MARKDOWN={md_path}")
