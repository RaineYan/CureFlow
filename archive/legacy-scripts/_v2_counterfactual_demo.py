from pathlib import Path

from artifacts import ArtifactRef
from contamination import ContaminationResult, PropagationEdge
from provenance.graph import EdgeType
from recovery.counterfactual import minimal_repair_families, render_counterfactual_report


if __name__ == "__main__":
    def ref(name): return ArtifactRef(run_id="v2_counterfactual", artifact_id=name, version=1)
    data, evidence, metric, claim, report, unrelated = map(ref, ("data_fault", "evidence_fault", "metric", "claim", "report", "unrelated"))
    contamination = ContaminationResult(
        run_id="v2_counterfactual", invalidated=[data, evidence, unrelated],
        contaminated=[metric, claim, report], unaffected=[],
        propagation_edges=[
            PropagationEdge(source=data, target=metric, via=EdgeType.INPUT),
            PropagationEdge(source=metric, target=claim, via=EdgeType.EVIDENCE),
            PropagationEdge(source=evidence, target=claim, via=EdgeType.EVIDENCE),
            PropagationEdge(source=claim, target=report, via=EdgeType.INPUT),
        ],
    )
    families = minimal_repair_families(contamination, report)
    output = Path(__file__).resolve().parent / "artifacts" / "counterfactual_repair_families.md"
    output.write_text(render_counterfactual_report(contamination, report, families), encoding="utf-8")
    print(f"REPORT={output}")
