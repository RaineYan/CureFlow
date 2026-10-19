"""Reuse evidence-bound AI items after an authorized local data correction.

No provider calls live here. Only server-owned manifests and exact, valid
artifact versions can authorize reuse. Summary text is always rebuilt locally.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from artifacts import ArtifactRef, ArtifactStatus, ArtifactStore
from cureflow_app.interpretation_review import RUN_ID

SCHEMA = "evidence_bound_ai_items_v1"
VALID = {ArtifactStatus.VALID.value, ArtifactStatus.RECOVERED.value}


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def evidence_refs(packet: dict) -> set[str]:
    return {m["artifactRef"] for m in packet.get("metrics", []) if m.get("artifactRef")} | {
        t["evidenceRef"] for t in packet.get("analysisTools", {}).get("executedTools", [])
        if t.get("evidenceRef")
    }


def snapshots(packet: dict, data_dir: Path) -> dict[str, str]:
    """Keep hashes, not raw artifact values or identifying table rows."""
    store = ArtifactStore(base_dir=str(data_dir / "artifacts"))
    store.reload()
    values = {}
    for metric in packet.get("metrics", []):
        if metric.get("artifactRef"):
            values[metric["artifactRef"]] = {k: metric.get(k) for k in ("key", "label", "value", "display")}
    for tool in packet.get("analysisTools", {}).get("executedTools", []):
        if tool.get("evidenceRef"):
            values[tool["evidenceRef"]] = {k: tool.get(k) for k in ("key", "status", "payload")}
    checked = {}
    for reference, safe_value in values.items():
        ref = ArtifactRef.from_ref_string(reference)
        artifact = store.get_version(ref.run_id, ref.artifact_id, ref.version)
        if artifact is None or artifact.validation_status not in VALID:
            raise ValueError("当前 AI 证据已失效或不存在，请重新执行本地分析")
        checked[reference] = digest([safe_value, artifact.value])
    return checked


def context_key(packet: dict, provider: str, model: str, brief: str) -> str:
    tools = packet.get("analysisTools") or {}
    method_pack = deepcopy(packet.get("methodPack"))
    if isinstance(method_pack, dict):
        for method in method_pack.get("methods", []):
            # Execution references are checked individually by snapshots. Their
            # run IDs are not changes to the method's assumptions or readiness.
            method.pop("executionEvidenceRefs", None)
    return digest({
        "schema": SCHEMA, "provider": provider, "model": model, "brief": brief,
        "goal": tools.get("goal"), "config": tools.get("analysisConfig"),
        "methodPack": method_pack, "dataSufficiency": packet.get("dataSufficiency"),
        "rowCount": (tools.get("summary") or {}).get("rowCount"),
        "columnCount": (tools.get("summary") or {}).get("columnCount"),
        "workbookRelations": packet.get("workbookRelations"),
    })


def _path(data_dir: Path, run_id: str) -> Path:
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        raise ValueError("AI 依赖记录的运行编号无效")
    return data_dir / "ai_dependency_manifests" / f"{run_id}.json"


def save_manifest(result: dict, packet: dict, data_dir: Path, fingerprint: str,
                  provider: str, model: str, brief: str) -> None:
    value = {
        "schema": SCHEMA, "runId": result["runId"], "inputFingerprint": fingerprint,
        "context": context_key(packet, provider, model, brief),
        "snapshots": snapshots(packet, data_dir),
        "interpretation": result["aiInterpretation"], "cost": result["cost"],
    }
    value["checksum"] = digest(value)
    path = _path(data_dir, result["runId"])
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.stem}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_source(result: dict, packet: dict, data_dir: Path, provider: str,
                model: str, brief: str) -> tuple[dict | None, str]:
    recovery = result.get("recoveryExecution") or {}
    source_id = recovery.get("sourceRunId")
    if not source_id:
        return None, "initial_analysis"
    try:
        value = json.loads(_path(data_dir, source_id).read_text(encoding="utf-8"))
        checksum = value.pop("checksum")
        if checksum != digest(value) or value.get("schema") != SCHEMA:
            return None, "invalid_dependency_manifest"
        if value.get("runId") != source_id or value.get("inputFingerprint") != recovery.get("sourceInputFingerprint"):
            return None, "source_fingerprint_mismatch"
        if value.get("context") != context_key(packet, provider, model, brief):
            return None, "provider_model_goal_or_context_changed"
        review_path = data_dir / "interpretation_reviews" / f"{source_id}.json"
        if review_path.exists():
            review = json.loads(review_path.read_text(encoding="utf-8"))
            # Human revisions/rejections must never be silently replaced by the
            # pre-review AI cache. Re-interpret from current evidence instead.
            if review.get("revision") != 0:
                return None, "source_human_review_changed"
        if not isinstance(value.get("snapshots"), dict) or not isinstance(value.get("interpretation"), dict):
            return None, "invalid_dependency_manifest"
        return value, "validated_dependency_manifest"
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None, "missing_or_unreadable_dependency_manifest"


def plan_recovery(result: dict, packet: dict, data_dir: Path, provider: str,
                  model: str, brief: str) -> dict:
    current = snapshots(packet, data_dir)
    source, reason = load_source(result, packet, data_dir, provider, model, brief)
    plan = {"mode": "full", "reason": reason, "source": source,
            "currentSnapshots": current, "keptFindings": [], "keptActions": []}
    if source is None:
        return plan
    old = source["snapshots"]
    unchanged = {ref for ref, value in current.items() if old.get(ref) == value}
    # Rows citing upstream references not explicitly projected in the provider
    # evidence packet cannot safely be reused under this protocol.
    for key, target in (("findings", "keptFindings"), ("actions", "keptActions")):
        for item in source["interpretation"].get(key, []):
            refs = item.get("evidenceRefs")
            if (isinstance(refs, list) and refs and set(refs) <= unchanged
                    and item.get("reviewStatus") != "rejected"):
                plan[target].append(deepcopy(item))
    changed = set(current) - unchanged
    all_old_rows = [*source["interpretation"].get("findings", []),
                    *source["interpretation"].get("actions", [])]
    if any(not item.get("evidenceRefs") or not set(item["evidenceRefs"]) <= set(old)
           for item in all_old_rows):
        return {**plan, "reason": "untrackable_old_dependencies"}
    # Unreferenced context in an old summary is never reused. If auxiliary
    # checks changed, expose them again with a current metric/tool reference.
    if not changed and not plan["keptFindings"]:
        return {**plan, "reason": "no_reusable_findings"}
    kept_count = len(plan["keptFindings"]) + len(plan["keptActions"])
    if not kept_count:
        return {**plan, "reason": "all_ai_items_affected"}
    if len(plan["keptFindings"]) >= 5 and changed:
        return {**plan, "reason": "finding_capacity_requires_full_analysis"}
    plan.update({"mode": "partial" if changed else "reuse_all", "reason": "exact_valid_evidence",
                 "changedRefs": sorted(changed), "unchangedRefs": sorted(unchanged)})
    return plan


def scoped_packet(packet: dict, plan: dict) -> dict:
    changed = set(plan["changedRefs"])
    scoped = deepcopy(packet)
    scoped["metrics"] = [
        {**m, "evidenceRefs": [m["artifactRef"]]}
        for m in scoped.get("metrics", []) if m.get("artifactRef") in changed
    ]
    tools = scoped.get("analysisTools") or {}
    tools["executedTools"] = [t for t in tools.get("executedTools", []) if t.get("evidenceRef") in changed]
    # Global recommendations can contain claims from unchanged tools. Supply
    # only evidence blocks being reinterpreted; regenerate narrative locally.
    tools["recommendations"] = []
    tools["limitations"] = []
    for method in (scoped.get("methodPack") or {}).get("methods", []):
        if "executionEvidenceRefs" in method:
            method["executionEvidenceRefs"] = [r for r in method["executionEvidenceRefs"] if r in changed]
    if not scoped["metrics"] and not any(t.get("key") in {"quality_audit", "rank_consistency"}
                                          for t in tools["executedTools"]):
        scoped["semanticChecks"] = {}
        scoped["repairPlan"] = {}
    return scoped


def merge_interpretation(plan: dict, fresh: dict | None, validate) -> dict:
    old_findings = plan["keptFindings"]
    old_actions = plan["keptActions"]
    findings = old_findings + ((fresh or {}).get("findings") or [])
    actions = old_actions + ((fresh or {}).get("actions") or [])
    # Derive the global summary from the final item set. Never retain the old
    # model's global summary or unscoped limitations after a correction.
    merged = validate({
        "summary": "；".join(item["explanation"] for item in findings)[:600],
        "findings": findings, "actions": actions,
        "limitations": ["结论仍需对照原始记录人工核对。", "相关性和分组差异不能直接解释为因果。"],
    }, set(plan["currentSnapshots"]))
    source_id = plan["source"]["runId"]
    for key, reused_count in (("findings", len(old_findings)), ("actions", len(old_actions))):
        for index, item in enumerate(merged[key]):
            item["itemId"] = digest([key, item])[:20]
            item["executionStatus"] = "reused" if index < reused_count else "regenerated"
            if index < reused_count:
                item["reusedFromRunId"] = source_id
    return merged
