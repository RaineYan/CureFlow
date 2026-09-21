"""Offline tests for the bounded three-scenario OpenAI A/B pilot."""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

from llm import LLMResponse, UsageType
from experiments.openai_ab_pilot import (
    MAX_LIVE_CALLS,
    OPENAI_MODEL,
    PRICE_SNAPSHOT,
    evaluate_response,
    fixed_scenarios,
    live_gate,
    run_ab,
)
from artifacts import ArtifactStore


def _openai_response(scenario) -> LLMResponse:
    refs = [m.ref().to_ref_string() if hasattr(m, "ref") else f"{m.run_id}/{m.artifact_id}@v{m.version}" for m in scenario.metrics]
    required = {
        "financial": refs[:2],
        "user": refs,
        "operation": refs,
    }[scenario.scenario_id]
    text = {
        "financial": "营收同比增长 60.78%，利润率达到 47.56%。",
        "user": "平均月活用户同比增长 50%。",
        "operation": "订单同比增长 77.42%，访问转化率为 9.32%。",
    }[scenario.scenario_id]
    return LLMResponse(
        text=json.dumps(
            {"claim_text": text, "evidence_refs": required, "confidence": 0.9},
            ensure_ascii=False,
        ),
        input_tokens=100,
        output_tokens=40,
        latency_ms=1000,
        provider="openai",
        model=OPENAI_MODEL,
        usage_type=UsageType.PROVIDER_REPORTED,
        request_id=f"resp_{scenario.scenario_id}",
    )


def _ref_string(metric) -> str:
    return f"{metric.run_id}/{metric.artifact_id}@v{metric.version}"


def test_fixed_matrix_is_exactly_three_scenarios() -> None:
    scenarios = fixed_scenarios()
    assert [s.scenario_id for s in scenarios] == ["financial", "user", "operation"]
    assert len(scenarios) == MAX_LIVE_CALLS == 3


def test_user_prompt_requires_percentage_not_raw_ratio() -> None:
    scenario = fixed_scenarios()[1]
    prompt = scenario.prompt
    user_ref = _ref_string(scenario.metrics[0])
    assert user_ref in prompt
    assert "user_growth ≈ 50.00%" in prompt
    assert (
        "Write the percentage form with % or 百分之; do NOT write the raw ratio "
        "0.5 as though it were an absolute MAU value."
    ) in prompt


def test_operations_prompt_requires_both_percentages() -> None:
    scenario = fixed_scenarios()[2]
    prompt = scenario.prompt
    assert "evidence_refs MUST include BOTH" in prompt
    assert "conversion_rate ≈ 9.32%" in prompt
    assert "order_growth    ≈ 77.42%" in prompt
    for metric in scenario.metrics:
        assert _ref_string(metric) in prompt


def test_live_observed_user_raw_ratio_remains_rejected(tmp_path: Path) -> None:
    """Prompt fix must not weaken or silently repair the semantic gate."""
    scenario = fixed_scenarios()[1]
    store = ArtifactStore(base_dir=str(tmp_path))
    store.save(scenario.metrics[0])
    response = _openai_response(scenario).model_copy(update={
        "text": json.dumps({
            "claim_text": "MAU为0.5，反映当前月活跃用户规模。",
            "evidence_refs": [_ref_string(scenario.metrics[0])],
            "confidence": 0.9,
        }, ensure_ascii=False),
    })
    row = evaluate_response(
        response, scenario=scenario, store=store, expect_openai=True
    )
    assert row["schemaValid"] is True
    assert row["protocolAudit"]["accepted"] is True
    assert row["semanticValid"] is False
    assert row["accepted"] is False
    assert any("MISSING_NUMERIC_MENTION" in issue for issue in row["semanticIssues"])


def test_price_snapshot_is_explicit_and_not_a_bill() -> None:
    assert PRICE_SNAPSHOT["as_of"] == "2026-09-15"
    assert PRICE_SNAPSHOT["input_usd_per_million_tokens"] == 0.20
    assert PRICE_SNAPSHOT["output_usd_per_million_tokens"] == 1.20


def test_live_gate_needs_every_confirmation() -> None:
    os.environ.pop("OPENAI_API_KEY", None)
    os.environ.pop("CUREFLOW_ENABLE_OPENAI_LIVE", None)
    gate = live_gate(execute_live=True, confirm_max_calls=3)
    assert gate["allowed"] is False
    assert "API_KEY_ENV_MISSING" in gate["blockers"]
    assert "LOCAL_LIVE_CONFIRMATION_MISSING" in gate["blockers"]


def test_wrong_call_confirmation_blocks_live() -> None:
    os.environ["OPENAI_API_KEY"] = "not-real"
    os.environ["CUREFLOW_ENABLE_OPENAI_LIVE"] = "1"
    try:
        gate = live_gate(execute_live=True, confirm_max_calls=2)
        assert gate["allowed"] is False
        assert "MAX_CALL_CONFIRMATION_MISMATCH" in gate["blockers"]
    finally:
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("CUREFLOW_ENABLE_OPENAI_LIVE", None)


def test_dry_run_makes_no_network_calls(tmp_path: Path) -> None:
    os.environ.pop("OPENAI_API_KEY", None)
    with patch("llm.openai_responses.urllib.request.urlopen") as network:
        report = run_ab(output_dir=tmp_path)
    network.assert_not_called()
    assert report["mode"] == "DRY_RUN_OFFLINE_ONLY"
    assert report["summary"]["offlineAccepted"] == 3
    assert report["openai"] == []


def test_dry_run_writes_both_reports(tmp_path: Path) -> None:
    run_ab(output_dir=tmp_path)
    assert (tmp_path / "openai_ab_pilot.json").exists()
    md = (tmp_path / "openai_ab_pilot.md").read_text(encoding="utf-8")
    assert "DRY_RUN_OFFLINE_ONLY" in md
    assert "Estimated list price" in md


def test_valid_openai_responses_pass_protocol_and_semantics(tmp_path: Path) -> None:
    scenarios = fixed_scenarios()
    store = ArtifactStore(base_dir=str(tmp_path))
    for scenario in scenarios:
        for metric in scenario.metrics:
            if store.get_version(metric.run_id, metric.artifact_id, 1) is None:
                store.save(metric)
        row = evaluate_response(
            _openai_response(scenario),
            scenario=scenario,
            store=store,
            expect_openai=True,
        )
        assert row["accepted"] is True, row
        assert row["schemaValid"] is True
        assert row["semanticValid"] is True
        assert row["protocolAudit"]["accepted"] is True


def test_provider_tokens_drive_cost_estimate(tmp_path: Path) -> None:
    scenario = fixed_scenarios()[1]
    store = ArtifactStore(base_dir=str(tmp_path))
    for metric in scenario.metrics:
        store.save(metric)
    row = evaluate_response(
        _openai_response(scenario),
        scenario=scenario,
        store=store,
        expect_openai=True,
    )
    assert row["estimatedListPriceUsd"] == 0.000068
    assert row["estimateIsProviderBill"] is False


def test_missing_required_evidence_fails_semantics(tmp_path: Path) -> None:
    scenario = fixed_scenarios()[0]
    store = ArtifactStore(base_dir=str(tmp_path))
    for metric in scenario.metrics:
        store.save(metric)
    response = _openai_response(scenario)
    payload = json.loads(response.text)
    payload["evidence_refs"] = payload["evidence_refs"][:1]
    response = response.model_copy(update={"text": json.dumps(payload)})
    row = evaluate_response(
        response, scenario=scenario, store=store, expect_openai=True
    )
    assert row["protocolAudit"]["accepted"] is True
    assert row["semanticValid"] is False
    assert row["accepted"] is False


def test_user_reported_smoke_is_labelled_and_truncated() -> None:
    path = Path("artifacts/openai_single_call_smoke_user_reported.json")
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["evidence_type"] == "USER_REPORTED_SANITIZED_LIVE_OUTPUT"
    assert record["request_id_is_truncated"] is True
    assert record["accepted"] is True
    assert record["input_tokens"] == 99
    assert record["output_tokens"] == 43
