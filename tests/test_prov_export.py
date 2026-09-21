"""W3C PROV-aligned export adapter tests."""
import json

from artifacts import Artifact, ArtifactRef, ArtifactStore, ArtifactType
from provenance.graph import ArtifactGraph
from provenance.prov_export import export_prov_json


def _graph(tmp_path):
    store = ArtifactStore(str(tmp_path / "store"))
    raw = store.save(Artifact(artifact_id="raw", artifact_type=ArtifactType.RAW_DATA, producer_task="load", run_id="prov", version=1, meta={"task_kind": "tool"}))
    raw_ref = ArtifactRef(run_id="prov", artifact_id="raw", version=1)
    metric = store.save(Artifact(artifact_id="metric", artifact_type=ArtifactType.METRIC, producer_task="compute", run_id="prov", version=1, input_artifact_ids=[raw_ref], meta={"task_kind": "tool"}))
    metric_ref = ArtifactRef(run_id="prov", artifact_id="metric", version=1)
    store.save(Artifact(artifact_id="claim", artifact_type=ArtifactType.CLAIM, producer_task="claim_agent", run_id="prov", version=1, input_artifact_ids=[metric_ref], evidence_ids=[metric_ref], meta={"task_kind": "llm", "provider": "fixture"}))
    return ArtifactGraph.from_store(store, "prov")


def test_export_is_deterministic(tmp_path) -> None:
    graph = _graph(tmp_path)
    assert export_prov_json(graph) == export_prov_json(graph)


def test_export_is_json_serializable(tmp_path) -> None:
    assert json.loads(json.dumps(export_prov_json(_graph(tmp_path))))


def test_entities_include_version_and_status(tmp_path) -> None:
    data = export_prov_json(_graph(tmp_path))
    claim = data["entity"]["cf:entity:prov/claim@v1"]
    assert claim["cureflow:version"] == 1
    assert claim["cureflow:validationStatus"] == "VALID"


def test_each_artifact_has_generation_activity(tmp_path) -> None:
    data = export_prov_json(_graph(tmp_path))
    assert len(data["wasGeneratedBy"]) == len(data["entity"]) == 3


def test_input_and_evidence_roles_are_distinct(tmp_path) -> None:
    roles = {record["prov:role"] for record in export_prov_json(_graph(tmp_path))["used"].values()}
    assert roles == {"cureflow:Input", "cureflow:Evidence"}


def test_evidence_extension_only_contains_evidence_edges(tmp_path) -> None:
    evidence = export_prov_json(_graph(tmp_path))["cureflow:evidence"]
    assert len(evidence) == 1
    assert next(iter(evidence.values()))["cureflow:claimEntity"].endswith("claim@v1")


def test_agents_distinguish_tool_and_fixture_provider(tmp_path) -> None:
    agents = export_prov_json(_graph(tmp_path))["agent"]
    assert "cf:agent:tool" in agents
    assert "cf:agent:fixture" in agents


def test_empty_graph_exports_empty_sections() -> None:
    data = export_prov_json(ArtifactGraph())
    assert data["entity"] == {}
    assert data["used"] == {}
    assert "not a certification claim" in data["cureflow:conformanceNote"]
