"""Three-scenario CureFlow A/B pilot: offline fixture vs OpenAI Responses.

The runner is safe-by-default.  Without every live gate it executes only the
offline baseline and writes a dry-run report.  A live run permits exactly
three logical calls and configures zero automatic retries.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from artifacts import Artifact, ArtifactRef, ArtifactStore, ArtifactType
from llm.base import LLMClient, LLMResponse, UsageType
from llm.fake import default_workflow_llm
from llm.openai_audit import audit_openai_response
from llm.openai_responses import OpenAIResponsesClient, OpenAIResponsesConfig
from llm.parse import parse_claim_payload
from validation import validate_claim
from workflow.claim_tasks import build_claim_prompt


LIVE_CONFIRM_ENV = "CUREFLOW_ENABLE_OPENAI_LIVE"
OPENAI_MODEL = "gpt-5.6-luna"
MAX_LIVE_CALLS = 3

# Official list-price snapshot, not provider-billed cost.
PRICE_SNAPSHOT = {
    "as_of": "2026-09-15",
    "model": OPENAI_MODEL,
    "input_usd_per_million_tokens": 0.20,
    "output_usd_per_million_tokens": 1.20,
    "source": "https://developers.openai.com/api/docs/models/gpt-5.6-luna",
}


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    task_name: str
    analyst_role: str
    instruction: str
    evidence_policy: str
    metrics: tuple[Artifact, ...]

    @property
    def prompt(self) -> str:
        return build_claim_prompt(
            self.analyst_role, list(self.metrics), self.instruction
        )


def _metric(run_id: str, artifact_id: str, value: float) -> Artifact:
    return Artifact(
        artifact_id=artifact_id,
        artifact_type=ArtifactType.METRIC,
        producer_task="openai_ab_fixed_fixture",
        run_id=run_id,
        value=value,
        meta={"fixture": True, "not_real_company_data": True},
    )


def fixed_scenarios(run_id: str = "openai_ab_fixed") -> tuple[Scenario, ...]:
    revenue = _metric(run_id, "metric_revenue_growth", 0.6078)
    margin = _metric(run_id, "metric_profit_margin", 0.4756)
    cost = _metric(run_id, "metric_cost_ratio", 0.5244)
    users = _metric(run_id, "metric_user_growth", 0.50)
    conversion = _metric(run_id, "metric_conversion_rate", 0.0932)
    orders = _metric(run_id, "metric_order_growth", 0.7742)
    return (
        Scenario(
            "financial",
            "generate_financial_claim",
            "financial analyst",
            "Write a short Chinese claim about the financial branch metrics.",
            "financial_claim",
            (revenue, margin, cost),
        ),
        Scenario(
            "user",
            "generate_user_claim",
            "user analyst",
            "Write a short Chinese claim about user growth, using the MAU metric.",
            "user_claim",
            (users,),
        ),
        Scenario(
            "operation",
            "generate_operation_claim",
            "operations analyst",
            "Write a short Chinese claim about operations (orders + conversion rate).",
            "operation_claim",
            (conversion, orders),
        ),
    )


def _save_metrics(store: ArtifactStore, scenarios: Iterable[Scenario]) -> None:
    seen: set[tuple[str, str, int]] = set()
    for scenario in scenarios:
        for metric in scenario.metrics:
            key = (metric.run_id, metric.artifact_id, metric.version)
            if key not in seen:
                store.save(metric)
                seen.add(key)


def _estimated_list_price_usd(response: LLMResponse) -> float | None:
    if response.usage_type != UsageType.PROVIDER_REPORTED:
        return None
    value = (
        response.input_tokens
        * PRICE_SNAPSHOT["input_usd_per_million_tokens"]
        + response.output_tokens
        * PRICE_SNAPSHOT["output_usd_per_million_tokens"]
    ) / 1_000_000
    return round(value, 10)


def evaluate_response(
    response: LLMResponse,
    *,
    scenario: Scenario,
    store: ArtifactStore,
    expect_openai: bool,
) -> dict:
    payload, parse_issues, parse_reason = parse_claim_payload(response.text)
    refs: list[ArtifactRef] = []
    ref_errors: list[str] = []
    if payload is not None and not parse_issues:
        for raw in payload.get("evidence_refs", []):
            try:
                refs.append(ArtifactRef.from_ref_string(raw))
            except Exception as exc:
                ref_errors.append(f"{raw!r}: {type(exc).__name__}")

    semantic = None
    if payload is not None and not parse_issues and not ref_errors:
        semantic = validate_claim(
            claim_text=payload["claim_text"],
            evidence_refs_in_text=refs,
            store=store,
            evidence_policy=scenario.evidence_policy,
            extra_input_ids=[m.artifact_id for m in scenario.metrics],
        )

    protocol_audit = (
        audit_openai_response(response, expected_model=OPENAI_MODEL).to_dict()
        if expect_openai
        else None
    )
    schema_valid = payload is not None and not parse_issues and not parse_reason
    semantic_valid = bool(semantic and semantic.passed)
    protocol_valid = bool(protocol_audit["accepted"]) if protocol_audit else True
    return {
        "scenarioId": scenario.scenario_id,
        "provider": response.provider,
        "model": response.model,
        "requestId": response.request_id,
        "usageType": response.usage_type.value,
        "inputTokens": response.input_tokens,
        "outputTokens": response.output_tokens,
        "latencyMs": response.latency_ms,
        "estimatedListPriceUsd": _estimated_list_price_usd(response),
        "estimateIsProviderBill": False,
        "schemaValid": schema_valid,
        "semanticValid": semantic_valid,
        "accepted": schema_valid and semantic_valid and protocol_valid,
        "parseIssues": parse_issues,
        "referenceErrors": ref_errors,
        "semanticIssues": semantic.issues if semantic else [],
        "protocolAudit": protocol_audit,
        "claimText": payload.get("claim_text") if payload else None,
        "evidenceRefs": payload.get("evidence_refs", []) if payload else [],
    }


def run_provider(
    client: LLMClient,
    *,
    scenarios: tuple[Scenario, ...],
    store: ArtifactStore,
    expect_openai: bool,
) -> list[dict]:
    return [
        evaluate_response(
            client.generate(s.prompt, task_name=s.task_name),
            scenario=s,
            store=store,
            expect_openai=expect_openai,
        )
        for s in scenarios
    ]


def live_gate(*, execute_live: bool, confirm_max_calls: int | None) -> dict:
    key_present = bool(os.environ.get("OPENAI_API_KEY"))
    local_confirmed = os.environ.get(LIVE_CONFIRM_ENV) == "1"
    count_confirmed = confirm_max_calls == MAX_LIVE_CALLS
    allowed = execute_live and key_present and local_confirmed and count_confirmed
    blockers: list[str] = []
    if not execute_live:
        blockers.append("EXECUTE_LIVE_NOT_REQUESTED")
    if not key_present:
        blockers.append("API_KEY_ENV_MISSING")
    if not local_confirmed:
        blockers.append("LOCAL_LIVE_CONFIRMATION_MISSING")
    if not count_confirmed:
        blockers.append("MAX_CALL_CONFIRMATION_MISMATCH")
    return {
        "allowed": allowed,
        "maxLiveCalls": MAX_LIVE_CALLS,
        "automaticRetries": 0,
        "store": False,
        "blockers": blockers,
        "apiKeyValueExposed": False,
    }


def run_ab(
    *,
    output_dir: Path,
    execute_live: bool = False,
    confirm_max_calls: int | None = None,
) -> dict:
    gate = live_gate(
        execute_live=execute_live, confirm_max_calls=confirm_max_calls
    )
    scenarios = fixed_scenarios()
    with tempfile.TemporaryDirectory(prefix="cureflow-openai-ab-") as temp:
        store = ArtifactStore(base_dir=temp)
        _save_metrics(store, scenarios)
        offline = run_provider(
            default_workflow_llm(),
            scenarios=scenarios,
            store=store,
            expect_openai=False,
        )
        live: list[dict] = []
        if gate["allowed"]:
            live = run_provider(
                OpenAIResponsesClient(
                    OpenAIResponsesConfig(
                        model=OPENAI_MODEL,
                        max_retries=0,
                        max_output_tokens=1200,
                    )
                ),
                scenarios=scenarios,
                store=store,
                expect_openai=True,
            )

    report = {
        "experimentId": "OPENAI_AB_PILOT_V1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "mode": "LIVE_AB" if live else "DRY_RUN_OFFLINE_ONLY",
        "scenarioCount": len(scenarios),
        "gate": gate,
        "priceSnapshot": PRICE_SNAPSHOT,
        "offline": offline,
        "openai": live,
        "summary": {
            "offlineAccepted": sum(1 for row in offline if row["accepted"]),
            "openaiAccepted": sum(1 for row in live if row["accepted"]),
            "openaiProviderReportedTokens": sum(
                row["inputTokens"] + row["outputTokens"]
                for row in live
                if row["usageType"] == "provider_reported"
            ),
            "openaiEstimatedListPriceUsd": round(
                sum(row["estimatedListPriceUsd"] or 0.0 for row in live), 10
            ),
        },
        "limitations": [
            "Fixed inputs are synthetic POC fixtures, not real company data.",
            "Estimated list price is calculated from provider-reported tokens; it is not a bill.",
            "Three calls are a pilot, not a statistically powered quality benchmark.",
            "This experiment does not replace or rerun M1/M3 evidence.",
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "openai_ab_pilot.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "openai_ab_pilot.md").write_text(
        render_markdown(report), encoding="utf-8"
    )
    return report


def render_markdown(report: dict) -> str:
    lines = [
        "# CureFlow OpenAI A/B Pilot",
        "",
        f"- Mode: `{report['mode']}`",
        f"- Scenarios: {report['scenarioCount']}",
        f"- Max live calls: {report['gate']['maxLiveCalls']}",
        f"- Automatic retries: {report['gate']['automaticRetries']}",
        f"- OpenAI accepted: {report['summary']['openaiAccepted']}/{len(report['openai'])}",
        f"- Provider-reported tokens: {report['summary']['openaiProviderReportedTokens']}",
        f"- Estimated list price USD: {report['summary']['openaiEstimatedListPriceUsd']:.10f}",
        "",
        "| path | scenario | accepted | schema | semantic | tokens | latency ms |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for path in ("offline", "openai"):
        for row in report[path]:
            lines.append(
                f"| {path} | {row['scenarioId']} | {str(row['accepted']).lower()} | "
                f"{str(row['schemaValid']).lower()} | {str(row['semanticValid']).lower()} | "
                f"{row['inputTokens'] + row['outputTokens']} | {row['latencyMs']:.2f} |"
            )
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in report["limitations"])
    return "\n".join(lines) + "\n"

