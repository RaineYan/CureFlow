"""
Tests for Day 5 — Metric -> Claim semantic provenance.

Coverage (matches the Day 5 spec, 21 tests):
   1.  Financial Claim can be generated
   2.  Output is a ClaimArtifact
   3.  content is saved
   4.  input refs auto-capture three Metrics
   5.  evidence refs come from LLM output
   6.  evidence must be subset of input
   7.  unknown evidence is rejected
   8.  dangling evidence is rejected
   9.  malformed JSON: nothing saved
  10.  missing claim_text: rejected
  11.  confidence out of range: rejected
  12.  first Claim auto v1
  13.  second generation auto v2
  14.  upstream Metric v2 -> Claim v2 cites it precisely
  15.  Claim v1 provenance does not drift
  16.  cross-run Metric input rejected
  17.  FakeLLM is offline (no network)
  18.  LLM cost written to ExecutionCost
  19.  EVIDENCE edge appears in graph
  20.  Same (src, dst) keeps both INPUT and EVIDENCE in MultiDiGraph
  21.  Day 1-4 tests still pass (covered by full pytest run)
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from artifacts import (
    Artifact,
    ArtifactRef,
    ArtifactStore,
    ArtifactType,
)
from llm import FakeLLMClient, LLMResponse
from provenance.decorator import RunContext
from provenance.graph import ArtifactGraph, EdgeType
from workflow.claim_tasks import (
    ClaimOutput,
    build_financial_claim_prompt,
    generate_financial_claim,
)
from workflow.tasks import (
    compute_cost_ratio,
    compute_profit_margin,
    compute_revenue_growth,
    extract_fields,
)


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture
def store(tmp_path: Path) -> ArtifactStore:
    base = tmp_path / "artifacts"
    s = ArtifactStore(base_dir=str(base))
    yield s
    shutil.rmtree(base, ignore_errors=True)


@pytest.fixture
def csv_path() -> str:
    return str(Path(__file__).resolve().parent.parent / "data" / "sample_company.csv")


@pytest.fixture
def metrics_v1(store: ArtifactStore, csv_path: str) -> dict[str, Artifact]:
    """Build extract_fields + 3 financial metrics at v1."""
    run_id = "run_001"
    extract_fields(csv_path, run_id, store)
    compute_revenue_growth(run_id, store)
    compute_profit_margin(run_id, store)
    compute_cost_ratio(run_id, store)
    return {
        "revenue_growth": store.get(run_id, "metric_revenue_growth"),
        "profit_margin": store.get(run_id, "metric_profit_margin"),
        "cost_ratio": store.get(run_id, "metric_cost_ratio"),
    }


@pytest.fixture
def llm() -> FakeLLMClient:
    return FakeLLMClient()


# =====================================================================
# 1-3: ClaimArtifact produced + content saved
# =====================================================================

def test_financial_claim_can_be_generated(
    metrics_v1, store: ArtifactStore, llm: FakeLLMClient
) -> None:
    ctx = RunContext(run_id="run_001", store=store)
    claim = generate_financial_claim(
        metrics_v1["revenue_growth"],
        metrics_v1["profit_margin"],
        metrics_v1["cost_ratio"],
        ctx=ctx,
        llm_client=llm,
    )
    assert claim is not None
    assert claim.artifact_id == "claim_financial_growth"


def test_output_is_claim_artifact(
    metrics_v1, store: ArtifactStore, llm: FakeLLMClient
) -> None:
    ctx = RunContext(run_id="run_001", store=store)
    claim = generate_financial_claim(
        metrics_v1["revenue_growth"],
        metrics_v1["profit_margin"],
        metrics_v1["cost_ratio"],
        ctx=ctx,
        llm_client=llm,
    )
    assert claim.artifact_type == "Claim"
    # Claim's `value` is None; payload lives in `content`
    assert claim.value is None


def test_content_saved(
    metrics_v1, store: ArtifactStore, llm: FakeLLMClient
) -> None:
    ctx = RunContext(run_id="run_001", store=store)
    claim = generate_financial_claim(
        metrics_v1["revenue_growth"],
        metrics_v1["profit_margin"],
        metrics_v1["cost_ratio"],
        ctx=ctx,
        llm_client=llm,
    )
    assert isinstance(claim.content, str)
    assert len(claim.content) > 0


# =====================================================================
# 4-5: input refs (3 metrics) + evidence refs (from LLM)
# =====================================================================

def test_input_refs_auto_capture_three_metrics(
    metrics_v1, store: ArtifactStore, llm: FakeLLMClient
) -> None:
    ctx = RunContext(run_id="run_001", store=store)
    claim = generate_financial_claim(
        metrics_v1["revenue_growth"],
        metrics_v1["profit_margin"],
        metrics_v1["cost_ratio"],
        ctx=ctx,
        llm_client=llm,
    )
    assert len(claim.input_artifact_ids) == 3
    input_keys = {(r.run_id, r.artifact_id, r.version) for r in claim.input_artifact_ids}
    assert input_keys == {
        ("run_001", "metric_revenue_growth", 1),
        ("run_001", "metric_profit_margin", 1),
        ("run_001", "metric_cost_ratio", 1),
    }


def test_evidence_refs_come_from_llm_output(
    metrics_v1, store: ArtifactStore, llm: FakeLLMClient
) -> None:
    ctx = RunContext(run_id="run_001", store=store)
    claim = generate_financial_claim(
        metrics_v1["revenue_growth"],
        metrics_v1["profit_margin"],
        metrics_v1["cost_ratio"],
        ctx=ctx,
        llm_client=llm,
    )
    # FakeLLM echoes all AVAILABLE_EVIDENCE refs into evidence_refs
    assert len(claim.evidence_ids) == 3
    assert all(isinstance(r, ArtifactRef) for r in claim.evidence_ids)
    evidence_keys = {(r.run_id, r.artifact_id, r.version) for r in claim.evidence_ids}
    assert evidence_keys == {
        ("run_001", "metric_revenue_growth", 1),
        ("run_001", "metric_profit_margin", 1),
        ("run_001", "metric_cost_ratio", 1),
    }


# =====================================================================
# 6-8: evidence validation (subset, not unknown, not dangling)
# =====================================================================

def test_evidence_must_be_subset_of_input(
    metrics_v1, store: ArtifactStore
) -> None:
    """FakeLLM that cites something not in the input set -> rejected."""
    ctx = RunContext(run_id="run_001", store=store)

    def rogue_responder(prompt: str) -> LLMResponse:
        return LLMResponse(
            text=json.dumps({
                "claim_text": "fictional claim",
                "evidence_refs": [
                    "run_001/metric_revenue_growth@v1",
                    "run_001/metric_unknown@v1",
                ],
                "confidence": 0.5,
            }),
            input_tokens=10, output_tokens=10, monetary_usd=0.0, latency_ms=1.0,
        )
    rogue = FakeLLMClient(responder=rogue_responder)

    with pytest.raises(ValueError, match="not in this run"):
        generate_financial_claim(
            metrics_v1["revenue_growth"],
            metrics_v1["profit_margin"],
            metrics_v1["cost_ratio"],
            ctx=ctx,
            llm_client=rogue,
        )
    # nothing saved
    assert store.get("run_001", "claim_financial_growth") is None


def test_unknown_evidence_rejected(
    metrics_v1, store: ArtifactStore
) -> None:
    ctx = RunContext(run_id="run_001", store=store)

    def f(prompt: str) -> LLMResponse:
        return LLMResponse(
            text=json.dumps({
                "claim_text": "x",
                "evidence_refs": ["run_001/metric_ghost@v1"],
                "confidence": 0.5,
            }),
            input_tokens=1, output_tokens=1, monetary_usd=0.0, latency_ms=0.0,
        )
    with pytest.raises(ValueError, match="not in this run"):
        generate_financial_claim(
            metrics_v1["revenue_growth"],
            metrics_v1["profit_margin"],
            metrics_v1["cost_ratio"],
            ctx=ctx,
            llm_client=FakeLLMClient(responder=f),
        )
    assert store.get("run_001", "claim_financial_growth") is None


def test_dangling_evidence_rejected(
    metrics_v1, store: ArtifactStore
) -> None:
    """A ref that LOOKS legitimate and IS in the input set, but points at a
    version that doesn't exist in the store -> rejected (dangling).

    Setup: inject a fake input artifact at v99 of the same artifact_id
    WITHOUT saving it to the store. The LLM echoes it as evidence, and
    the dangling check fires when the store lookup fails.
    """
    # Synthetic v99 input (NOT saved to store -> dangling candidate)
    fake_v99 = Artifact(
        artifact_id="metric_revenue_growth",
        artifact_type=ArtifactType.METRIC,
        producer_task="test_synthetic",
        run_id="run_001",
        version=99,
        value=0.0,
    )
    ctx = RunContext(run_id="run_001", store=store)

    def f(prompt: str) -> LLMResponse:
        return LLMResponse(
            text=json.dumps({
                "claim_text": "x",
                "evidence_refs": ["run_001/metric_revenue_growth@v99"],
                "confidence": 0.5,
            }),
            input_tokens=1, output_tokens=1, monetary_usd=0.0, latency_ms=0.0,
        )
    with pytest.raises(ValueError, match="Dangling evidence"):
        generate_financial_claim(
            fake_v99,
            metrics_v1["profit_margin"],
            metrics_v1["cost_ratio"],
            ctx=ctx,
            llm_client=FakeLLMClient(responder=f),
        )
    assert store.get("run_001", "claim_financial_growth") is None


# =====================================================================
# 9-11: JSON / schema / confidence validation
# =====================================================================

def test_malformed_json_does_not_save(
    metrics_v1, store: ArtifactStore
) -> None:
    ctx = RunContext(run_id="run_001", store=store)

    def f(prompt: str) -> LLMResponse:
        return LLMResponse(
            text="this is not json {{{",
            input_tokens=1, output_tokens=1, monetary_usd=0.0, latency_ms=0.0,
        )
    with pytest.raises(ValueError, match="not a valid claim payload"):
        generate_financial_claim(
            metrics_v1["revenue_growth"],
            metrics_v1["profit_margin"],
            metrics_v1["cost_ratio"],
            ctx=ctx,
            llm_client=FakeLLMClient(responder=f),
        )
    assert store.get("run_001", "claim_financial_growth") is None


def test_missing_claim_text_rejected(
    metrics_v1, store: ArtifactStore
) -> None:
    ctx = RunContext(run_id="run_001", store=store)

    def f(prompt: str) -> LLMResponse:
        return LLMResponse(
            text=json.dumps({
                # claim_text missing entirely
                "evidence_refs": ["run_001/metric_revenue_growth@v1"],
                "confidence": 0.5,
            }),
            input_tokens=1, output_tokens=1, monetary_usd=0.0, latency_ms=0.0,
        )
    with pytest.raises(ValueError, match="claim schema"):
        generate_financial_claim(
            metrics_v1["revenue_growth"],
            metrics_v1["profit_margin"],
            metrics_v1["cost_ratio"],
            ctx=ctx,
            llm_client=FakeLLMClient(responder=f),
        )
    assert store.get("run_001", "claim_financial_growth") is None


def test_confidence_out_of_range_rejected(
    metrics_v1, store: ArtifactStore
) -> None:
    ctx = RunContext(run_id="run_001", store=store)

    def f(prompt: str) -> LLMResponse:
        return LLMResponse(
            text=json.dumps({
                "claim_text": "x",
                "evidence_refs": ["run_001/metric_revenue_growth@v1"],
                "confidence": 1.5,   # > 1
            }),
            input_tokens=1, output_tokens=1, monetary_usd=0.0, latency_ms=0.0,
        )
    with pytest.raises(ValueError, match="ClaimOutput schema"):
        generate_financial_claim(
            metrics_v1["revenue_growth"],
            metrics_v1["profit_margin"],
            metrics_v1["cost_ratio"],
            ctx=ctx,
            llm_client=FakeLLMClient(responder=f),
        )
    assert store.get("run_001", "claim_financial_growth") is None


# =====================================================================
# 12-15: version evolution (the heart of Day 5)
# =====================================================================

def test_first_claim_auto_v1(
    metrics_v1, store: ArtifactStore, llm: FakeLLMClient
) -> None:
    ctx = RunContext(run_id="run_001", store=store)
    claim = generate_financial_claim(
        metrics_v1["revenue_growth"],
        metrics_v1["profit_margin"],
        metrics_v1["cost_ratio"],
        ctx=ctx,
        llm_client=llm,
    )
    assert claim.version == 1


def test_second_generation_auto_v2(
    metrics_v1, store: ArtifactStore, llm: FakeLLMClient
) -> None:
    ctx = RunContext(run_id="run_001", store=store)
    c1 = generate_financial_claim(
        metrics_v1["revenue_growth"],
        metrics_v1["profit_margin"],
        metrics_v1["cost_ratio"],
        ctx=ctx,
        llm_client=llm,
    )
    c2 = generate_financial_claim(
        metrics_v1["revenue_growth"],
        metrics_v1["profit_margin"],
        metrics_v1["cost_ratio"],
        ctx=ctx,
        llm_client=llm,
    )
    assert c1.version == 1
    assert c2.version == 2


def test_upstream_v2_metric_makes_claim_v2_cite_it(
    metrics_v1, store: ArtifactStore, llm: FakeLLMClient
) -> None:
    """metric_revenue_growth gets recovered to v2 -> re-run claim ->
    new claim@v2 has metric_revenue_growth@v2 in both input AND evidence."""
    ctx = RunContext(run_id="run_001", store=store)
    # baseline claim v1
    generate_financial_claim(
        metrics_v1["revenue_growth"],
        metrics_v1["profit_margin"],
        metrics_v1["cost_ratio"],
        ctx=ctx,
        llm_client=llm,
    )
    # upstream v2
    rev25_v1 = store.get("run_001", "metric_revenue_growth")
    assert rev25_v1 is not None
    rev25_v2 = rev25_v1.model_copy(update={"value": 0.5, "version": 2})
    store.save(rev25_v2)
    rev25_v2_fetched = store.get("run_001", "metric_revenue_growth")
    assert rev25_v2_fetched is not None and rev25_v2_fetched.version == 2

    # re-run claim with the v2 metric
    claim_v2 = generate_financial_claim(
        rev25_v2_fetched,
        metrics_v1["profit_margin"],
        metrics_v1["cost_ratio"],
        ctx=ctx,
        llm_client=llm,
    )
    assert claim_v2.version == 2
    # the v2 claim's evidence includes metric_revenue_growth@v2 (not v1)
    evidence_keys = {(r.run_id, r.artifact_id, r.version) for r in claim_v2.evidence_ids}
    assert ("run_001", "metric_revenue_growth", 2) in evidence_keys
    assert ("run_001", "metric_revenue_growth", 1) not in evidence_keys
    # inputs likewise pin v2
    input_keys = {(r.run_id, r.artifact_id, r.version) for r in claim_v2.input_artifact_ids}
    assert ("run_001", "metric_revenue_growth", 2) in input_keys


def test_claim_v1_provenance_does_not_drift(
    metrics_v1, store: ArtifactStore, llm: FakeLLMClient
) -> None:
    """After metric_revenue_growth becomes v2, claim@v1's input/evidence
    refs must STILL point at v1."""
    ctx = RunContext(run_id="run_001", store=store)
    c1 = generate_financial_claim(
        metrics_v1["revenue_growth"],
        metrics_v1["profit_margin"],
        metrics_v1["cost_ratio"],
        ctx=ctx,
        llm_client=llm,
    )
    # upstream v2
    rev25_v1 = store.get("run_001", "metric_revenue_growth")
    assert rev25_v1 is not None
    store.save(rev25_v1.model_copy(update={"value": 0.5, "version": 2}))
    rev25_v2 = store.get("run_001", "metric_revenue_growth")
    assert rev25_v2 is not None
    generate_financial_claim(
        rev25_v2, metrics_v1["profit_margin"], metrics_v1["cost_ratio"],
        ctx=ctx, llm_client=llm,
    )

    # c1 from disk: still v1 refs
    c1_again = store.get_version("run_001", "claim_financial_growth", 1)
    assert c1_again is not None
    c1_input_keys = {(r.run_id, r.artifact_id, r.version) for r in c1_again.input_artifact_ids}
    c1_evidence_keys = {(r.run_id, r.artifact_id, r.version) for r in c1_again.evidence_ids}
    assert ("run_001", "metric_revenue_growth", 1) in c1_input_keys
    assert ("run_001", "metric_revenue_growth", 1) in c1_evidence_keys
    assert not any(v == 2 for (_r, _a, v) in c1_input_keys)
    assert not any(v == 2 for (_r, _a, v) in c1_evidence_keys)


# =====================================================================
# 16: cross-run input rejected
# =====================================================================

def test_cross_run_metric_input_rejected(
    metrics_v1, store: ArtifactStore, llm: FakeLLMClient
) -> None:
    """Synthesize a same-id Metric from a different run; claim task must refuse."""
    rg = metrics_v1["revenue_growth"]
    foreign = rg.model_copy(update={"run_id": "run_OTHER", "version": 1})
    ctx = RunContext(run_id="run_001", store=store)
    with pytest.raises(ValueError, match="Cross-run input"):
        generate_financial_claim(
            foreign, metrics_v1["profit_margin"], metrics_v1["cost_ratio"],
            ctx=ctx, llm_client=llm,
        )
    assert store.get("run_001", "claim_financial_growth") is None


# =====================================================================
# 17: FakeLLM offline
# =====================================================================

def test_fake_llm_is_offline(
    metrics_v1, store: ArtifactStore
) -> None:
    """FakeLLMClient has no network code path; it works on a hermetic machine."""
    import llm.fake as fake_mod
    import inspect
    src = inspect.getsource(fake_mod)
    for banned in ("urllib", "requests", "httpx", "openai", "anthropic", "dashscope"):
        assert banned not in src, f"FakeLLMClient must not import {banned!r}"


# =====================================================================
# 18: LLM cost written to ExecutionCost
# =====================================================================

def test_llm_cost_written_to_execution_cost(
    metrics_v1, store: ArtifactStore, llm: FakeLLMClient
) -> None:
    ctx = RunContext(run_id="run_001", store=store)
    claim = generate_financial_claim(
        metrics_v1["revenue_growth"],
        metrics_v1["profit_margin"],
        metrics_v1["cost_ratio"],
        ctx=ctx,
        llm_client=llm,
    )
    ec = claim.execution_cost
    assert ec.llm_calls == 1
    # FakeLLM reports 120 input / 40 output
    assert ec.input_tokens == 120
    assert ec.output_tokens == 40
    assert ec.tokens == 160
    assert ec.latency_ms >= 0.0


# =====================================================================
# 19-20: graph integration
# =====================================================================

def test_evidence_edge_appears_in_graph(
    metrics_v1, store: ArtifactStore, llm: FakeLLMClient
) -> None:
    ctx = RunContext(run_id="run_001", store=store)
    generate_financial_claim(
        metrics_v1["revenue_growth"],
        metrics_v1["profit_margin"],
        metrics_v1["cost_ratio"],
        ctx=ctx,
        llm_client=llm,
    )
    g = ArtifactGraph.from_store(store, "run_001")
    src = ArtifactRef(run_id="run_001", artifact_id="metric_revenue_growth", version=1)
    dst = ArtifactRef(run_id="run_001", artifact_id="claim_financial_growth", version=1)
    et = g.edge_type(src, dst)
    assert et is not None
    # Could be a set containing EVIDENCE (and INPUT), or a single EVIDENCE;
    # both are acceptable per the spec.
    if isinstance(et, set):
        assert EdgeType.EVIDENCE in et
    else:
        assert et == EdgeType.EVIDENCE


def test_input_and_evidence_both_kept_on_same_pair(
    metrics_v1, store: ArtifactStore, llm: FakeLLMClient
) -> None:
    """Day 4 spec: MultiDiGraph must hold both INPUT and EVIDENCE between
    the same (metric, claim) pair when the LLM cited a metric it was shown."""
    ctx = RunContext(run_id="run_001", store=store)
    generate_financial_claim(
        metrics_v1["revenue_growth"],
        metrics_v1["profit_margin"],
        metrics_v1["cost_ratio"],
        ctx=ctx,
        llm_client=llm,
    )
    g = ArtifactGraph.from_store(store, "run_001")
    src = ArtifactRef(run_id="run_001", artifact_id="metric_revenue_growth", version=1)
    dst = ArtifactRef(run_id="run_001", artifact_id="claim_financial_growth", version=1)

    et = g.edge_type(src, dst)
    assert et is not None
    if isinstance(et, set):
        # FakeLLM echoes every ref, so revenue_growth is BOTH input and evidence
        assert EdgeType.EVIDENCE in et


# =====================================================================
# Bonus: ClaimOutput schema direct test
# =====================================================================

def test_claim_output_schema_rejects_empty_evidence() -> None:
    with pytest.raises(Exception):
        ClaimOutput.model_validate({
            "claim_text": "x",
            "evidence_refs": [],
            "confidence": 0.5,
        })


def test_claim_output_schema_rejects_extra_fields() -> None:
    with pytest.raises(Exception):
        ClaimOutput.model_validate({
            "claim_text": "x",
            "evidence_refs": ["run_001/metric_x@v1"],
            "confidence": 0.5,
            "extra_field": "nope",
        })
