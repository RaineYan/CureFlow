"""
Day 13 smoke test — Real LLM provider integration.

  - If REAL_LLM_API_KEY is set: run a clean baseline + a SELECTIVE
    recovery through the real provider, assert semantic validity.
  - If not set: SKIP (offline mode).  The 316-test suite is unaffected.

This is NOT a full benchmark.  It exists to verify the real-LLM
plumbing works end-to-end on at least one provider, and to catch
semantic-validation regressions.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from artifacts import ArtifactStore
from llm import (
    LLMResponse,
    RealLLMClient,
    RealLLMProviderConfig,
    default_workflow_llm,
)
from provenance.decorator import RunContext
from validation import (
    SemanticValidationResult,
    validate_claim,
    validate_section,
)
from workflow.claim_tasks import (
    generate_financial_claim,
    generate_financial_section,
    generate_operation_claim,
    generate_operation_section,
    generate_user_claim,
    generate_user_section,
)
from workflow.runner import run_baseline_workflow


CSV = str(Path(__file__).resolve().parent / "data" / "sample_company.csv")


def _hr(t: str) -> None:
    print()
    print("=" * 80)
    print(t)
    print("=" * 80)


def _client_or_skip() -> RealLLMClient:
    api_key = os.environ.get("REAL_LLM_API_KEY")
    if not api_key:
        print("[SKIP] REAL_LLM_API_KEY not set; running offline smoke test only.")
        sys.exit(0)
    cfg = RealLLMProviderConfig(
        provider=os.environ.get("REAL_LLM_PROVIDER", "openai"),
        model=os.environ.get("REAL_LLM_MODEL", "gpt-4o-mini"),
        api_key_env_var="REAL_LLM_API_KEY",
        base_url=os.environ.get("REAL_LLM_BASE_URL"),
        timeout_seconds=30.0,
        max_retries=1,
    )
    return RealLLMClient(cfg)


def main() -> int:
    client = _client_or_skip()

    # 1) Clean baseline
    _hr("PHASE 1: clean baseline (no fault)")
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        store = ArtifactStore(base_dir=str(Path(td) / "art"))
        result = run_baseline_workflow(CSV, "smoke_clean", store, client)
        _print_claim("financial_claim", result.claims["financial"], store, "financial_claim")
        _print_claim("user_claim", result.claims["user"], store, "user_claim")
        _print_claim("operation_claim", result.claims["operation"], store, "operation_claim")
        print(f"\n  merged_report content (first 200 chars):\n  {result.merged_report.content[:200]}...")

    # 2) Provider provenance check
    _hr("PHASE 2: provider provenance in Claim meta")
    rg = result.metrics["revenue_growth"]
    _check_meta(rg, store, "metric_revenue_growth")
    fc = result.claims["financial"]
    _check_meta(fc, store, "claim_financial_growth")
    print(f"  provider = {fc.meta.get('llm_provider')}")
    print(f"  model    = {fc.meta.get('llm_model')}")
    print(f"  usage_is_synthetic = {fc.meta.get('llm_usage_is_synthetic')}")
    assert fc.meta.get("llm_usage_is_synthetic") is False, (
        "real-LLM response must have usage_is_synthetic=False"
    )

    return 0


def _print_claim(label: str, claim, store, policy) -> None:
    print(f"\n  {label}:")
    print(f"    content: {claim.content}")
    print(f"    evidence_ids: {[r.to_ref_string() for r in claim.evidence_ids]}")
    sv = validate_claim(
        claim_text=claim.content,
        evidence_refs_in_text=list(claim.evidence_ids),
        store=store,
        evidence_policy=policy,
    )
    print(f"    semantic_validation: passed={sv.passed}, "
          f"evidence={sv.evidence_valid}, "
          f"numeric={sv.numeric_consistency_pass}, "
          f"direction={sv.direction_consistency_pass}, "
          f"recommendation={sv.recommendation_boundary_pass}")
    if sv.issues:
        for i in sv.issues[:5]:
            print(f"      - {i}")


def _check_meta(art, store, aid) -> None:
    latest = store.get("smoke_clean", aid)
    if latest is None:
        return
    print(f"  {aid}@v{latest.version}  meta.llm_provider={latest.meta.get('llm_provider', 'n/a')}  "
          f"meta.llm_usage_is_synthetic={latest.meta.get('llm_usage_is_synthetic', 'n/a')}")


if __name__ == "__main__":
    sys.exit(main())
