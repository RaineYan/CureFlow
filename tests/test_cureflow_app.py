from __future__ import annotations

import json
from pathlib import Path

import pytest

from cureflow_app.server import ApiInputError, analyze_csv, load_recovery_evidence


def test_analyze_sample_csv_returns_traceable_workflow(tmp_path: Path) -> None:
    csv_path = Path(__file__).resolve().parent.parent / "data" / "sample_company.csv"
    result = analyze_csv(csv_path.read_text(encoding="utf-8"), csv_path.name, tmp_path)

    assert result["mode"] == "OFFLINE_DETERMINISTIC_FIXTURE"
    assert [stage["status"] for stage in result["stages"]] == ["success"] * 5
    assert len(result["metrics"]) == 6
    assert result["cost"]["llmCalls"] == 6
    assert result["reportRef"].startswith(result["runId"] + "/")
    assert all(metric["artifactRef"] for metric in result["metrics"])
    assert all(metric["evidenceRefs"] for metric in result["metrics"])
    assert json.loads(json.dumps(result, ensure_ascii=False))["runId"] == result["runId"]


def test_analyze_rejects_empty_csv(tmp_path: Path) -> None:
    with pytest.raises(ApiInputError, match="不能为空"):
        analyze_csv("", "empty.csv", tmp_path)


def test_recovery_evidence_stays_explicitly_mock() -> None:
    evidence = load_recovery_evidence()
    assert evidence["experimentId"] == "EXP-02"
    assert evidence["mode"] == "MOCK"
    assert evidence["providerCallsCount"] == 0
    assert "NOT real" in evidence["disclaimer"]
