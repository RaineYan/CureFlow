"""AI review reuses exact local evidence and changes only the report projection."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cureflow_app.interpretation_review import (
    InterpretationReviewError,
    review_interpretation,
    store_interpretation_for_review,
)
from cureflow_app.server import _build_plain_language_report, analyze_csv


@pytest.fixture
def reviewed_run(tmp_path: Path):
    sample = Path(__file__).resolve().parents[1] / "data" / "sample_company.csv"
    result = analyze_csv(
        sample.read_text(encoding="utf-8"), "private.csv", tmp_path,
    )
    evidence = result["metrics"][0]["artifactRef"]
    result["input"] = {"inputFingerprint": "fingerprint-1", "rowCount": 4, "columnCount": 5, "analysisGoal": "data_quality"}
    result["aiInterpretation"] = {
        "summary": "原始 AI 摘要", "findings": [{
            "title": "原结论", "explanation": "需要复核的解释", "evidenceRefs": [evidence],
            "confidence": "medium",
        }], "actions": [{"action": "原建议", "reason": "请核对", "evidenceRefs": [evidence]}],
        "limitations": [],
    }
    store_interpretation_for_review(result, tmp_path)
    body = {
        "runId": result["runId"], "inputFingerprint": "fingerprint-1", "expectedRevision": 0,
        "kind": "finding", "index": 0, "decision": "revise",
        "title": "核对后结论", "explanation": "已对照原始记录的保守表述",
        "evidenceRefs": [evidence], "confirmedAgainstSource": True,
        "reviewerNote": "根据原始记录复核",
    }
    return tmp_path, result, body


def _run(data_dir: Path, body: dict, *, apply: bool = False) -> dict:
    return review_interpretation(body, data_dir, apply=apply, render_report=_build_plain_language_report)


def test_preview_does_not_persist_and_apply_rebuilds_only_report(reviewed_run):
    data_dir, result, body = reviewed_run
    preview = _run(data_dir, body)
    assert preview["previewOnly"] is True
    assert preview["verification"] == {
        "sourceFingerprintMatched": True, "evidenceIdentityVerified": True,
        "humanContentConfirmed": True, "contentMachineVerified": False,
        "reportRegenerated": True, "providerCalled": False,
        "recomputedSteps": ["reader_report"],
        "reusedSteps": ["source_data", "local_analysis_tools", "unaffected_ai_items"],
    }
    assert "核对后结论" in preview["report"]
    assert "原始 AI 摘要" not in preview["report"]
    assert _run(data_dir, body)["revision"] == 1
    applied = _run(data_dir, body, apply=True)
    assert applied["previewOnly"] is False
    assert applied["record"]["before"]["title"] == "原结论"
    assert applied["interpretation"]["actions"] == result["aiInterpretation"]["actions"]
    with pytest.raises(InterpretationReviewError, match="已变化"):
        _run(data_dir, body, apply=True)


def test_reject_excludes_bad_item_without_modifying_other_actions(reviewed_run):
    data_dir, _, body = reviewed_run
    rejected = _run(data_dir, {**body, "decision": "reject"}, apply=True)
    assert rejected["interpretation"]["findings"][0]["reviewStatus"] == "rejected"
    assert rejected["interpretation"]["actions"][0]["action"] == "原建议"
    assert "已撤回" in rejected["report"]
    assert rejected["verification"]["evidenceIdentityVerified"] is False


@pytest.mark.parametrize("change,expected", [
    ({"inputFingerprint": "different"}, "指纹"),
    ({"evidenceRefs": ["other/run@v1"]}, "未提供"),
    ({"confirmedAgainstSource": False}, "人工复核"),
    ({"title": ""}, "标题"),
])
def test_invalid_or_unconfirmed_review_is_rejected(reviewed_run, change, expected):
    data_dir, _, body = reviewed_run
    with pytest.raises(InterpretationReviewError, match=expected):
        _run(data_dir, {**body, **change}, apply=True)


def test_manifest_has_no_raw_table_or_api_key(reviewed_run):
    data_dir, result, _ = reviewed_run
    path = data_dir / "interpretation_reviews" / f"{result['runId']}.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["revision"] == 0
    assert "private.csv" not in path.read_text(encoding="utf-8")
    assert "2024,1,1000000,600000,5000,200,2500" not in path.read_text(encoding="utf-8")


def test_report_keeps_local_findings_and_shows_reviewed_ai_finding(reviewed_run):
    _, result, _ = reviewed_run
    result["analysisTools"] = {
        "goal": "data_quality", "executedTools": [],
        "findings": [{"title": "本地发现", "explanation": "由工具计算"}],
    }
    report = _build_plain_language_report(result)
    assert "本地发现" in report
    assert "## 模型解读（需人工核对）" in report
    assert "原结论" in report
