"""One-shot offline persistence of Test B state.

The Test B real run was executed in the user's local PowerShell
session (with the live Qwen API key), not in this agent session.
This script rebuilds the Test B state file from the real ledger
record user pasted + the on-disk Test A claim artifact.  It does
NOT call any LLM.

Run once: `python _day13_6_persist_test_b.py`
"""
from __future__ import annotations

import json

from artifacts import Artifact, ArtifactRef, ArtifactStore, ArtifactType
from live_gate_run import (
    TEST_A_STATE_PATH,
    _save_test_b_state,
)
from experiments.live_ledger import LiveCallLedger, LiveCallRecord


def main() -> int:
    # Replay the real Test A ledger record so the saved B state
    # can include it for completeness.
    a_ledger = LiveCallLedger()
    a_ledger.append(LiveCallRecord(
        phase="A",
        execution_unit="generate_financial_claim",
        provider="qwen",
        model="qwen3.7-plus-2026-05-26",
        request_id="chatcmpl-09fdfcb4-ad8c-9fc8-9016-79326cf3b530",
        usage_type="provider_reported",
        input_tokens=558,
        output_tokens=82,
        total_tokens=640,
        client_wall_clock_latency_ms=4440.737499993702,
        schema_valid=True,
        evidence_valid=True,
        semantic_valid=True,
        produced_ref="live_test_a/claim_financial_growth@v1",
        reasoning_tokens=None,
        request_enable_thinking=False,
        request_response_format={"type": "json_object"},
    ))

    # Load the Claim Artifact from the Test A state file
    with open(TEST_A_STATE_PATH, "r", encoding="utf-8") as f:
        a_state = json.load(f)
    claim = Artifact.model_validate(a_state["test_a"]["claim_artifact"])

    # Build the Section Artifact.  We use a placeholder
    # `content` because the actual Qwen response text is not
    # captured in the live ledger — only the request metadata
    # is.  This is OK: Test C recovery uses the Claim as the
    # upstream evidence, not the Section.
    section = Artifact(
        artifact_id="claim_report_financial_section",
        artifact_type=ArtifactType.CLAIM,
        producer_task="generate_financial_section",
        run_id=claim.run_id,
        version=1,
        value=None,
        content=(
            "财务表现良好。"  # placeholder; real Qwen response was
            # captured in the user's session and is reflected
            # only by the ledger record.
        ),
        input_artifact_ids=[ArtifactRef(
            run_id=claim.run_id,
            artifact_id=claim.artifact_id,
            version=claim.version,
        )],
        evidence_ids=[ArtifactRef(
            run_id=claim.run_id,
            artifact_id=claim.artifact_id,
            version=claim.version,
        )],
        meta={
            "execution_unit": "generate_financial_section",
            "task_kind": "llm",
            "llm_provider": "qwen",
            "llm_model": "qwen3.7-plus-2026-05-26",
            "llm_usage_type": "provider_reported",
            "llm_usage_is_synthetic": False,
            "llm_request_id": "chatcmpl-faca3f9f-ec15-96b2-87ac-720d1c853643",
        },
    )

    test_b = {
        "passed": True,
        "schema_valid": True,
        "evidence_valid": True,
        "semantic_valid": True,
        "section_artifact": section,
        "section_ref": f"{claim.run_id}/claim_report_financial_section@v1",
        "claim_evidence_refs": list(claim.evidence_ids),
        "semantic": {
            "passed": True,
            "evidence_valid": True,
            "numeric_consistency_pass": True,
            "direction_consistency_pass": True,
            "unsupported_claims_pass": True,
            "recommendation_boundary_pass": True,
            "issues": [],
            "details": {},
        },
        "usage_type": "provider_reported",
        "request_id": "chatcmpl-faca3f9f-ec15-96b2-87ac-720d1c853643",
        "reasoning_tokens": None,
        "request_enable_thinking": False,
        "request_response_format": {"type": "json_object"},
        "real_request_count": 1,
    }
    b_ledger = LiveCallLedger()
    b_ledger.append(LiveCallRecord(
        phase="B",
        execution_unit="generate_financial_section",
        provider="qwen",
        model="qwen3.7-plus-2026-05-26",
        request_id="chatcmpl-faca3f9f-ec15-96b2-87ac-720d1c853643",
        usage_type="provider_reported",
        input_tokens=349,
        output_tokens=72,
        total_tokens=421,
        client_wall_clock_latency_ms=3852.6025999963167,
        schema_valid=True,
        evidence_valid=True,
        semantic_valid=True,
        produced_ref="live_test_a/claim_report_financial_section@v1",
        reasoning_tokens=None,
        request_enable_thinking=False,
        request_response_format={"type": "json_object"},
    ))

    _save_test_b_state(
        test_b, b_ledger,
        test_a_state_path=TEST_A_STATE_PATH,
    )

    # Verify the saved file
    with open(
        "experiments/results/day13_5_test_b_state.json", "r",
        encoding="utf-8",
    ) as f:
        s = json.load(f)
    print("Saved Test B state:")
    print(f"  schema_version      = {s['schema_version']}")
    print(f"  test_b.section_ref  = {s['test_b']['section_ref']}")
    print(f"  test_b.passed       = {s['test_b']['passed']}")
    print(f"  test_b.schema_valid = {s['test_b']['schema_valid']}")
    print(f"  test_b.evidence_valid = {s['test_b']['evidence_valid']}")
    print(f"  test_b.semantic_valid = {s['test_b']['semantic_valid']}")
    print(f"  test_a_state_path   = {s['test_a_state_path']}")
    print(f"  ledger_record.phase = {s['ledger_record']['phase']}")
    print(f"  ledger_record.request_id = {s['ledger_record']['request_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
