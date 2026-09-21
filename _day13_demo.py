"""
Day 13 demo — Real LLM Provider Integration + Semantic Validation.

What this demo shows, all offline (no real API call):

  1. RealLLMClient construction + provider config from env.
  2. Mocked HTTP call returns a structured LLMResponse with
     usage_is_synthetic=False and real token counts.
  3. The robust JSON parser handles fence-wrapped and prose-wrapped
     JSON from real models.
  4. The semantic validator runs 4 sub-checks on each claim and
     each section.
  5. The structured output propagates provider / model /
     usage_is_synthetic into the Claim artifact's meta.
  6. The smoke test script (separate file) actually invokes a
     real provider if REAL_LLM_API_KEY is set.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from llm import (
    LLMResponse,
    RealLLMClient,
    RealLLMError,
    RealLLMErrorKind,
    RealLLMProviderConfig,
    extract_json_object,
    parse_claim_payload,
)
from validation import (
    SemanticValidationResult,
    direction_check,
    numeric_consistency_check,
    parse_evidence_refs,
    recommendation_boundary_check,
    validate_claim,
    validate_section,
)
from artifacts import Artifact, ArtifactRef, ArtifactStore, ArtifactType
import tempfile


def _hr(t: str) -> None:
    print()
    print("=" * 80)
    print(t)
    print("=" * 80)


def main() -> None:
    _hr("1. RealLLMClient construction (no API call)")
    from llm.providers.real import _redact_key
    print("  _redact_key('AAAA...32chars'): " + _redact_key("A" * 32))

    cfg = RealLLMProviderConfig(
        provider="openai",
        model="gpt-4o-mini",
        api_key_env_var="REAL_LLM_API_KEY",
    )
    print(f"  RealLLMProviderConfig.has_api_key() = {cfg.has_api_key()}")
    if not cfg.has_api_key():
        # fake it for the demo
        import os
        os.environ["REAL_LLM_API_KEY"] = "TEST_DEMO_KEY"
    try:
        client = RealLLMClient(cfg)
        print(f"  RealLLMClient(provider={client.provider_name}, model={client.model_name})")
    except RealLLMError as e:
        print(f"  RealLLMClient not constructed: {e.kind.value}")

    _hr("2. Mocked HTTP -> LLMResponse with real usage")
    payload = {
        "id": "req-demo-001",
        "choices": [{"message": {"role": "assistant", "content": json.dumps({
            "claim_text": "revenue grew 60.78%",
            "evidence_refs": ["run_x/metric_revenue_growth@v1"],
            "confidence": 0.9,
        })}}],
        "usage": {"prompt_tokens": 110, "completion_tokens": 38, "total_tokens": 148},
    }
    raw = json.dumps(payload).encode("utf-8")
    class _Resp:
        def read(self): return raw
        def __enter__(self): return self
        def __exit__(self, *a): return False
    with patch("urllib.request.urlopen", new=lambda *a, **kw: _Resp()):
        r = client.generate("test", task_name="generate_financial_claim")
    print(f"  text = {r.text!r}")
    print(f"  input_tokens={r.input_tokens}  output_tokens={r.output_tokens}")
    print(f"  provider={r.provider!r}  model={r.model!r}")
    print(f"  usage_is_synthetic={r.usage_is_synthetic}  request_id={r.request_id!r}")

    _hr("3. Robust JSON parser")
    samples = [
        ('{"a": 1}', "plain"),
        ('```json\n{"a": 1}\n```', "fenced"),
        ('Here you go: {"a": 1, "b": 2}.', "in-prose"),
    ]
    for s, label in samples:
        obj, err = extract_json_object(s)
        print(f"  {label:<8} -> {obj!r} (err={err!r})")

    _hr("4. Semantic validator sub-checks (offline)")
    cases = [
        ("positive, no recommendation",
         "revenue grew 60.78%, margin 47.56%.", True, True, True),
        ("negative-direction contradiction",
         "revenue declined 60.78%, margin 47.56%.", True, True, True),
        ("with recommendation",
         "revenue grew 60.78%. 建议 increase investment.", True, True, True),
    ]
    for label, text, _, _, _ in cases:
        rec_ok, rec_issues = recommendation_boundary_check(text)
        print(f"  recommendation_boundary [{label:<35}]: passed={rec_ok}  issues={rec_issues}")

    _hr("5. Full validate_claim on a synthetic scenario")
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        store = ArtifactStore(base_dir=str(Path(td) / "art"))
        # build 3 metrics
        for aid, val in (
            ("metric_revenue_growth", 0.6078),
            ("metric_profit_margin", 0.4756),
            ("metric_cost_ratio", 0.5243),
        ):
            a = Artifact(
                artifact_id=aid, artifact_type=ArtifactType.METRIC,
                producer_task="compute", run_id="r1", version=1,
                value=val, input_artifact_ids=[],
                meta={"task_kind": "tool"},
            )
            store.save(a)
        rg = store.get_version("r1", "metric_revenue_growth", 1)
        pm = store.get_version("r1", "metric_profit_margin", 1)
        cr = store.get_version("r1", "metric_cost_ratio", 1)
        text = (
            f"2025 revenue grew 60.78%, margin 47.56%. "
            f"evidence: {rg.run_id}/{rg.artifact_id}@v{rg.version},"
            f"{pm.run_id}/{pm.artifact_id}@v{pm.version}."
        )
        sv = validate_claim(
            claim_text=text,
            evidence_refs_in_text=[
                ArtifactRef(run_id=rg.run_id, artifact_id=rg.artifact_id, version=rg.version),
                ArtifactRef(run_id=pm.run_id, artifact_id=pm.artifact_id, version=pm.version),
            ],
            store=store,
            evidence_policy="financial_claim",
        )
        print(f"  validate_claim(financial, happy): passed={sv.passed}")
        for f in ("evidence_valid", "numeric_consistency_pass",
                  "direction_consistency_pass", "recommendation_boundary_pass"):
            print(f"    {f} = {getattr(sv, f)}")

    _hr("6. Smoke test")
    print("  _day13_smoke_test.py will:")
    print("    - SKIP if REAL_LLM_API_KEY is not set")
    print("    - otherwise run a clean baseline through a real provider")
    print("    - assert semantic validity of every claim")
    print("    - assert usage_is_synthetic=False on every Claim meta")

    _hr("7. Day 13 pytest summary")
    print("  Day 1  = 21 tests  (artifacts + store)")
    print("  Day 2  = 18 tests  (workflow tasks)")
    print("  Day 3  = 16 tests  (decorator)")
    print("  Day 4  = 22 tests  (graph)")
    print("  Day 5  = 22 tests  (fake LLM + claim tasks)")
    print("  Day 6  = 24 tests  (3-branch workflow)")
    print("  Day 7  = 23 tests  (fault injection + contamination)")
    print("  Day 8  = 33 tests  (selective recovery)")
    print("  Day 9  = 30 tests  (3-strategy harness v1)")
    print("  Day 10 = 32 tests  (event-based metrics + Case B)")
    print("  Day 11 = 24 tests  (classification + UPR/ARR/RAF/TAF)")
    print("  Day 12 = 22 tests  (prerequisite audit + readiness gate)")
    print("  Day 13 = 29 tests  (real provider + semantic validation)")
    print("  ----")
    print("  TOTAL = 316 tests")


if __name__ == "__main__":
    main()
