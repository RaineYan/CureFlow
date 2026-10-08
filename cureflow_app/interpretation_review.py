"""Human review of one AI interpretation without another provider call.

The original run stays immutable.  A review revision changes only one finding
or action and deterministically rebuilds the reader-facing report.  Evidence
identity is checked against the exact artifacts from the source run; a human
confirmation is recorded separately from machine-verifiable facts.
"""
from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from pathlib import Path
from threading import RLock
from typing import Any, Callable
from uuid import uuid4

from artifacts import ArtifactRef, ArtifactStatus, ArtifactStore


RUN_ID = re.compile(r"desktop(?:_profile|_workbook)?_\d{8}T\d{6}Z_[0-9a-f]{8}\Z")
MAX_REVISIONS = 100
_REVIEW_LOCK = RLock()


class InterpretationReviewError(ValueError):
    """A stale, invalid, or unverified review request."""


def _manifest_path(data_dir: Path, run_id: str) -> Path:
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        raise InterpretationReviewError("分析编号无效")
    return data_dir / "interpretation_reviews" / f"{run_id}.json"


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.stem}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def store_interpretation_for_review(
    result: dict[str, Any], data_dir: Path, *, report_headline: str | None = None,
) -> None:
    """Save only report inputs and evidence identities, never raw rows or keys."""
    interpretation = result.get("aiInterpretation")
    if not interpretation:
        return
    run_id = result["runId"]
    path = _manifest_path(data_dir, run_id)
    if path.exists():
        raise InterpretationReviewError("本次分析编号已存在，不能覆盖原始复核记录")
    tools = result.get("analysisTools") or {}
    known_refs = {
        ref for metric in result.get("metrics") or []
        for ref in [metric.get("artifactRef"), *(metric.get("evidenceRefs") or [])]
        if isinstance(ref, str)
    }
    known_refs.update(
        tool["evidenceRef"] for tool in tools.get("executedTools") or []
        if isinstance(tool.get("evidenceRef"), str)
    )
    source = result.get("input") or {}
    context = {
        "reportHeadline": report_headline,
        "input": {key: source.get(key) for key in ("rowCount", "columnCount", "sheet", "analysisGoal")},
        "analysisTools": {
            "goal": tools.get("goal"), "goalLabel": tools.get("goalLabel"),
            "executedTools": [{"label": item.get("label")} for item in tools.get("executedTools") or []],
            "findings": [
                {"title": item.get("title"), "explanation": item.get("explanation")}
                for item in tools.get("findings") or []
            ],
            "recommendations": [
                {"action": item.get("action"), "rationale": item.get("rationale")}
                for item in tools.get("recommendations") or []
            ],
        },
        "repairPlan": {"summary": (result.get("repairPlan") or {}).get("summary") or {}},
        "workbookInventory": {
            "sheets": [
                {"sheet": item.get("sheet"), "role": item.get("role")}
                for item in (result.get("workbookInventory") or {}).get("sheets") or []
            ],
        },
    }
    _write_manifest(path, {
        "schemaVersion": 1, "runId": run_id,
        "inputFingerprint": source.get("inputFingerprint"),
        "revision": 0, "interpretation": interpretation,
        "knownEvidenceRefs": sorted(known_refs), "reportContext": context,
        "records": [],
    })


def _read_manifest(data_dir: Path, body: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    path = _manifest_path(data_dir, body.get("runId"))
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise InterpretationReviewError("未找到这次 AI 解读的本机记录，请重新分析") from exc
    except (OSError, ValueError) as exc:
        raise InterpretationReviewError("本机 AI 复核记录无法读取") from exc
    if manifest.get("schemaVersion") != 1 or manifest.get("runId") != body.get("runId"):
        raise InterpretationReviewError("AI 复核记录格式不匹配")
    if body.get("inputFingerprint") != manifest.get("inputFingerprint"):
        raise InterpretationReviewError("当前文件指纹与 AI 解读不一致")
    expected = body.get("expectedRevision")
    if isinstance(expected, bool) or not isinstance(expected, int) or expected != manifest.get("revision"):
        raise InterpretationReviewError("复核记录已变化，请刷新结果后重试")
    return path, manifest


def _checked_text(value: Any, label: str, limit: int = 1200) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise InterpretationReviewError(f"{label}不能为空或超过 {limit} 字")
    return value.strip()


def _verified_refs(refs: Any, manifest: dict[str, Any], data_dir: Path) -> list[str]:
    if not isinstance(refs, list) or not 1 <= len(refs) <= 12 or len(refs) != len(set(map(str, refs))):
        raise InterpretationReviewError("修正后的证据引用须为 1–12 条不重复的引用")
    store = ArtifactStore(base_dir=str(data_dir / "artifacts"))
    store.reload()
    known = set(manifest["knownEvidenceRefs"])
    checked: list[str] = []
    for value in refs:
        if not isinstance(value, str) or value not in known:
            raise InterpretationReviewError("不能引用本次分析未提供的证据")
        try:
            ref = ArtifactRef.from_ref_string(value)
        except (ValueError, TypeError) as exc:
            raise InterpretationReviewError("证据引用格式无效") from exc
        if ref.run_id != manifest["runId"]:
            raise InterpretationReviewError("不能引用另一轮分析的证据")
        artifact = store.get_version(ref.run_id, ref.artifact_id, ref.version)
        if artifact is None or artifact.validation_status not in {
            ArtifactStatus.VALID.value, ArtifactStatus.RECOVERED.value,
        }:
            raise InterpretationReviewError("引用的证据已失效或不存在")
        checked.append(value)
    return checked


def _review_interpretation_locked(
    body: dict[str, Any], data_dir: Path, *, apply: bool,
    render_report: Callable[[dict[str, Any]], str],
) -> dict[str, Any]:
    """Preview or apply one human decision and rebuild only the report."""
    path, manifest = _read_manifest(data_dir, body)
    if manifest["revision"] >= MAX_REVISIONS:
        raise InterpretationReviewError("本次分析复核版本已达上限，请重新分析")
    if body.get("confirmedAgainstSource") is not True:
        raise InterpretationReviewError("请先对照原始证据并确认人工复核")
    kind, index, decision = body.get("kind"), body.get("index"), body.get("decision")
    if kind not in {"finding", "action"} or decision not in {"revise", "reject"}:
        raise InterpretationReviewError("复核对象或操作无效")
    key = "findings" if kind == "finding" else "actions"
    interpretation = deepcopy(manifest["interpretation"])
    items = interpretation.get(key) or []
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(items):
        raise InterpretationReviewError("复核项目不存在")
    before = deepcopy(items[index])
    if before.get("reviewStatus") == "rejected":
        raise InterpretationReviewError("已撤回的项目不能再次修改")
    after = deepcopy(before)
    if decision == "reject":
        after["reviewStatus"] = "rejected"
        refs_verified = False
    else:
        if kind == "finding":
            after["title"] = _checked_text(body.get("title"), "结论标题", 180)
            after["explanation"] = _checked_text(body.get("explanation"), "结论内容")
        else:
            after["action"] = _checked_text(body.get("action"), "建议动作", 180)
            after["reason"] = _checked_text(body.get("reason"), "建议依据")
        after["evidenceRefs"] = _verified_refs(body.get("evidenceRefs"), manifest, data_dir)
        after["reviewStatus"] = "human_confirmed"
        refs_verified = True
    items[index] = after
    interpretation["summary"] = "AI 原始摘要已受人工复核影响，不再作为已核实结论；请查看本地计算及逐项复核记录。"
    report_interpretation = deepcopy(interpretation)
    for item_key in ("findings", "actions"):
        report_interpretation[item_key] = [
            item for item in report_interpretation.get(item_key) or []
            if item.get("reviewStatus") != "rejected"
        ]
    report = render_report({**manifest["reportContext"], "aiInterpretation": report_interpretation})
    revision = manifest["revision"] + 1
    note = body.get("reviewerNote", "")
    if not isinstance(note, str) or len(note) > 500:
        raise InterpretationReviewError("人工复核说明不能超过 500 字")
    record = {
        "revision": revision, "kind": kind, "index": index,
        "decision": decision, "before": before, "after": after,
        "reviewerNote": note.strip(),
        "evidenceIdentityVerified": refs_verified,
        "humanContentConfirmed": True,
        "affectedSteps": [f"ai_{kind}:{index}", "reader_report"],
        "reusedSteps": ["source_data", "local_analysis_tools", "unaffected_ai_items"],
    }
    appendix = ["", "## 人工复核的 AI 陈述", ""]
    for saved in [*manifest["records"], record]:
        item = saved["after"]
        label = item.get("title") or item.get("action") or f"第 {saved['index'] + 1} 项"
        status = "已撤回" if saved["decision"] == "reject" else "人工确认修改；证据身份已核验"
        appendix.append(f"- {label}：{status}。")
    report += "\n" + "\n".join(appendix)
    response = {
        "runId": manifest["runId"], "inputFingerprint": manifest["inputFingerprint"],
        "revision": revision, "interpretation": interpretation, "report": report,
        "record": record, "records": [*manifest["records"], record],
        "verification": {
            "sourceFingerprintMatched": True,
            "evidenceIdentityVerified": refs_verified,
            "humanContentConfirmed": True,
            "contentMachineVerified": False,
            "reportRegenerated": True,
            "providerCalled": False,
            "recomputedSteps": ["reader_report"],
            "reusedSteps": ["source_data", "local_analysis_tools", "unaffected_ai_items"],
        },
        "previewOnly": not apply,
    }
    if apply:
        manifest.update({"revision": revision, "interpretation": interpretation,
                         "records": response["records"]})
        _write_manifest(path, manifest)
    return response


def review_interpretation(
    body: dict[str, Any], data_dir: Path, *, apply: bool,
    render_report: Callable[[dict[str, Any]], str],
) -> dict[str, Any]:
    with _REVIEW_LOCK:
        return _review_interpretation_locked(body, data_dir, apply=apply, render_report=render_report)
