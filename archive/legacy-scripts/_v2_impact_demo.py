"""Generate the checked-in explainable impact report from a real local workflow."""
from pathlib import Path
from tempfile import TemporaryDirectory

from artifacts import ArtifactRef, ArtifactStatus, ArtifactStore
from contamination import propagate_contamination
from llm import default_workflow_llm
from provenance.graph import ArtifactGraph
from provenance.impact_report import build_impact_report
from workflow.runner import run_baseline_workflow


ROOT = Path(__file__).resolve().parent


def main() -> None:
    with TemporaryDirectory(prefix="cureflow-impact-") as temporary:
        store = ArtifactStore(base_dir=temporary)
        run_id = "impact_report_sample"
        run_baseline_workflow(
            str(ROOT / "data" / "sample_company.csv"),
            run_id,
            store,
            default_workflow_llm(),
        )
        raw = store.get(run_id, "raw_data")
        raw_ref = ArtifactRef(run_id=run_id, artifact_id=raw.artifact_id, version=raw.version)
        store.update_status(run_id, raw.artifact_id, ArtifactStatus.INVALIDATED)
        graph = ArtifactGraph.from_store(store, run_id)
        result = propagate_contamination(graph, store, run_id, [raw_ref])
        report = build_impact_report(graph, result)

    output = ROOT / "artifacts" / "impact_report_sample.md"
    output.write_text(report, encoding="utf-8")
    print(f"wrote {output} ({len(report.splitlines())} lines)")


if __name__ == "__main__":
    main()
