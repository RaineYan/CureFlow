"""Deterministic W3C PROV-JSON-aligned export for ArtifactGraph.

This is an adapter only: ArtifactGraph remains the source of truth.  INPUT
relations map to standard ``prov:used``/``prov:wasDerivedFrom`` records;
EVIDENCE is retained as an explicit CureFlow extension as well as derivation.
The project claims alignment, not W3C certification.
"""
from __future__ import annotations

from typing import Any

from .graph import ArtifactGraph, EdgeType


def _entity_id(ref) -> str:
    return f"cf:entity:{ref.to_ref_string()}"


def _activity_id(ref) -> str:
    return f"cf:activity:{ref.to_ref_string()}"


def _agent_name(artifact) -> str:
    meta = artifact.meta or {}
    return str(meta.get("agent") or meta.get("provider") or meta.get("task_kind") or "local-tool")


def _agent_id(name: str) -> str:
    return "cf:agent:" + name.strip().lower().replace(" ", "-")


def export_prov_json(graph: ArtifactGraph) -> dict[str, Any]:
    """Export a stable PROV-JSON-shaped dictionary without mutating ``graph``."""
    entities, activities, agents = {}, {}, {}
    generated, associated = {}, {}
    node_refs = sorted(graph.nodes(), key=lambda r: (r.run_id, r.artifact_id, r.version))
    for index, ref in enumerate(node_refs, start=1):
        artifact = graph.artifact(ref)
        entity_id, activity_id = _entity_id(ref), _activity_id(ref)
        if artifact is None:
            entities[entity_id] = {"prov:label": ref.to_ref_string()}
            continue
        artifact_type = getattr(artifact.artifact_type, "value", artifact.artifact_type)
        entities[entity_id] = {
            "prov:label": f"{artifact.artifact_id}@v{artifact.version}",
            "prov:type": f"cureflow:{artifact_type}",
            "cureflow:runId": artifact.run_id,
            "cureflow:artifactId": artifact.artifact_id,
            "cureflow:version": artifact.version,
            "cureflow:validationStatus": getattr(
                artifact.validation_status, "value", artifact.validation_status,
            ),
        }
        activities[activity_id] = {
            "prov:label": artifact.producer_task,
            "cureflow:executionUnit": artifact.producer_task,
        }
        agent_name = _agent_name(artifact)
        agent_id = _agent_id(agent_name)
        agents.setdefault(agent_id, {"prov:label": agent_name, "prov:type": "prov:SoftwareAgent"})
        generated[f"_:generated{index}"] = {"prov:entity": entity_id, "prov:activity": activity_id}
        associated[f"_:associated{index}"] = {"prov:activity": activity_id, "prov:agent": agent_id}

    used, derived, evidence = {}, {}, {}
    edge_rows = sorted(
        graph.edges(),
        key=lambda item: (item[0].to_ref_string(), item[1].to_ref_string(), item[2].value),
    )
    for index, (source, target, edge_type) in enumerate(edge_rows, start=1):
        role = "cureflow:Input" if edge_type == EdgeType.INPUT else "cureflow:Evidence"
        used[f"_:used{index}"] = {
            "prov:activity": _activity_id(target), "prov:entity": _entity_id(source),
            "prov:role": role,
        }
        derived[f"_:derived{index}"] = {
            "prov:generatedEntity": _entity_id(target),
            "prov:usedEntity": _entity_id(source),
            "cureflow:edgeType": edge_type.value,
        }
        if edge_type == EdgeType.EVIDENCE:
            evidence[f"_:evidence{index}"] = {
                "cureflow:claimEntity": _entity_id(target),
                "cureflow:evidenceEntity": _entity_id(source),
            }
    return {
        "prefix": {
            "prov": "http://www.w3.org/ns/prov#",
            "cureflow": "https://cureflow.local/ns#",
            "cf": "https://cureflow.local/id/",
        },
        "entity": entities, "activity": activities, "agent": agents,
        "wasGeneratedBy": generated, "used": used,
        "wasDerivedFrom": derived, "wasAssociatedWith": associated,
        "cureflow:evidence": evidence,
        "cureflow:conformanceNote": "W3C PROV-JSON aligned export with explicit CureFlow extensions; not a certification claim.",
    }


__all__ = ["export_prov_json"]
