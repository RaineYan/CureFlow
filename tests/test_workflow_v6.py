"""
Tests for Day 6 — 3-branch workflow + selective-version merge.

Coverage (24 tests, matches the Day 6 spec):
   1.  User Claim generated
   2.  Operation Claim generated
   3.  Financial Claim: input=3 metrics, evidence=2 metrics (cost_ratio excluded)
   4.  cost_ratio has INPUT edge to claim, NO EVIDENCE edge
   5.  Financial Section generated
   6.  User Section generated
   7.  Operation Section generated
   8.  three sections are independent versions
   9.  merge_report deterministic
  10.  merged report inputs = 3 sections
  11.  baseline workflow runs end-to-end
  12.  full graph from RawData reaches merged report
  13.  financial section ancestors contain no user metrics
  14.  financial section ancestors contain no operation metrics
  15.  user section ancestors contain no financial metrics
  16.  three branches only converge at merged_report
  17.  metric_revenue_growth v2 -> financial claim v2
  18.  financial section v2 generated
  19.  user/operation still at v1 after financial upgrade
  20.  merged_report v2 references fin@v2 + user@v1 + op@v1
  21.  merged_report v1 provenance does not drift
  22.  cross-run still isolated
  23.  FakeLLM fully offline
  24.  Day 1-5 tests still pass (covered by full pytest run)
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
from llm import FakeLLMClient, LLMResponse, default_workflow_llm
from provenance.decorator import RunContext
from provenance.graph import ArtifactGraph, EdgeType
from workflow.claim_tasks import (
    generate_financial_claim,
    generate_financial_section,
    generate_operation_claim,
    generate_operation_section,
    generate_user_claim,
    generate_user_section,
    merge_report,
)
from workflow.runner import run_baseline_workflow
from workflow.tasks import (
    compute_conversion_rate,
    compute_cost_ratio,
    compute_order_growth,
    compute_profit_margin,
    compute_revenue_growth,
    compute_user_growth,
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
def llm() -> FakeLLMClient:
    return default_workflow_llm()


@pytest.fixture
def baseline(store: ArtifactStore, csv_path: str, llm: FakeLLMClient):
    """One full baseline workflow run. Shared across most tests."""
    return run_baseline_workflow(csv_path, "run_001", store, llm)


# =====================================================================
# 1-2: User + Operation Claim
# =====================================================================

def test_user_claim_generated(baseline) -> None:
    uc = baseline.claims["user"]
    assert uc.artifact_type == "Claim"
    assert uc.artifact_id == "claim_user_growth"
    assert "50" in uc.content or "用户" in uc.content


def test_operation_claim_generated(baseline) -> None:
    oc = baseline.claims["operation"]
    assert oc.artifact_type == "Claim"
    assert oc.artifact_id == "claim_operation"
    # operation_claim is built from 2 inputs; FakeLLM echoes all refs
    assert len(oc.input_artifact_ids) == 2
    assert len(oc.evidence_ids) == 2


# =====================================================================
# 3-4: Financial Claim input=3, evidence=2; cost_ratio INPUT but not EVIDENCE
# =====================================================================

def test_financial_claim_has_3_inputs_and_2_evidence(baseline) -> None:
    fc = baseline.claims["financial"]
    assert len(fc.input_artifact_ids) == 3
    assert len(fc.evidence_ids) == 2
    input_ids = {(r.artifact_id, r.version) for r in fc.input_artifact_ids}
    assert ("metric_cost_ratio", 1) in input_ids
    evidence_ids = {(r.artifact_id, r.version) for r in fc.evidence_ids}
    assert ("metric_cost_ratio", 1) not in evidence_ids


def test_cost_ratio_has_input_edge_but_no_evidence_edge(
    baseline, store: ArtifactStore
) -> None:
    fc = baseline.claims["financial"]
    cr = baseline.metrics["cost_ratio"]
    g = ArtifactGraph.from_store(store, "run_001")
    src = ArtifactRef(run_id="run_001", artifact_id="metric_cost_ratio", version=cr.version)
    dst = ArtifactRef(run_id="run_001", artifact_id="claim_financial_growth", version=fc.version)

    et = g.edge_type(src, dst)
    assert et is not None, "INPUT edge must exist (cost_ratio is in input_artifact_ids)"
    # The edge must include INPUT but NOT EVIDENCE
    if isinstance(et, set):
        assert EdgeType.INPUT in et
        assert EdgeType.EVIDENCE not in et
    else:
        assert et == EdgeType.INPUT


# =====================================================================
# 5-7: three Sections
# =====================================================================

def test_financial_section_generated(baseline) -> None:
    fs = baseline.sections["financial"]
    assert fs.artifact_id == "claim_report_financial_section"
    # Section evidence == upstream financial claim (FakeLLM echoes)
    assert len(fs.evidence_ids) == 1
    assert fs.evidence_ids[0].artifact_id == "claim_financial_growth"


def test_user_section_generated(baseline) -> None:
    us = baseline.sections["user"]
    assert us.artifact_id == "claim_report_user_section"
    assert len(us.evidence_ids) == 1
    assert us.evidence_ids[0].artifact_id == "claim_user_growth"


def test_operation_section_generated(baseline) -> None:
    os_ = baseline.sections["operation"]
    assert os_.artifact_id == "claim_report_operation_section"
    assert len(os_.evidence_ids) == 1
    assert os_.evidence_ids[0].artifact_id == "claim_operation"


# =====================================================================
# 8: three sections are independent versions / execution units
# =====================================================================

def test_three_sections_are_independent(baseline) -> None:
    fs, us, os_ = baseline.sections["financial"], baseline.sections["user"], baseline.sections["operation"]
    ids = {fs.artifact_id, us.artifact_id, os_.artifact_id}
    assert len(ids) == 3
    # all start at v1
    assert fs.version == 1
    assert us.version == 1
    assert os_.version == 1
    # distinct producer_task
    tasks = {fs.producer_task, us.producer_task, os_.producer_task}
    assert len(tasks) == 3


# =====================================================================
# 9-10: merge_report deterministic, inputs = 3 sections
# =====================================================================

def test_merge_report_is_deterministic_tool(baseline) -> None:
    merged = baseline.merged_report
    assert merged.artifact_id == "report_merged"
    assert merged.meta["task_kind"] == "tool"
    assert merged.meta["semantic_role"] == "merged_report"
    assert merged.execution_cost.llm_calls == 0
    assert merged.execution_cost.tokens == 0
    # all three section contents appear in the merged text
    assert baseline.sections["financial"].content in merged.content
    assert baseline.sections["user"].content in merged.content
    assert baseline.sections["operation"].content in merged.content


def test_merged_report_inputs_are_three_sections(baseline) -> None:
    merged = baseline.merged_report
    input_ids = {(r.artifact_id, r.version) for r in merged.input_artifact_ids}
    assert ("claim_report_financial_section", 1) in input_ids
    assert ("claim_report_user_section", 1) in input_ids
    assert ("claim_report_operation_section", 1) in input_ids
    # merge_report evidence_ids is empty
    assert list(merged.evidence_ids) == []


# =====================================================================
# 11-12: full baseline + graph
# =====================================================================

def test_baseline_workflow_runs_end_to_end(baseline) -> None:
    assert baseline.run_id == "run_001"
    assert baseline.raw_data.artifact_type == "RawData"
    assert len(baseline.fields) == 10
    assert len(baseline.metrics) == 6
    assert set(baseline.claims.keys()) == {"financial", "user", "operation"}
    assert set(baseline.sections.keys()) == {"financial", "user", "operation"}
    assert baseline.merged_report.artifact_id == "report_merged"
    # cost summary aggregated
    assert baseline.total_llm_calls == 6  # 3 claims + 3 sections


def test_full_graph_reaches_merged_report(baseline, store: ArtifactStore) -> None:
    g = ArtifactGraph.from_store(store, "run_001")
    # raw_data -> ... -> report_merged
    raw = ArtifactRef(run_id="run_001", artifact_id="raw_data", version=1)
    merged = ArtifactRef(run_id="run_001", artifact_id="report_merged", version=1)
    assert g.has_path(raw, merged) is True


# =====================================================================
# 13-16: branch isolation
# =====================================================================

def _ancestor_ids(graph, artifact_id, run_id, version) -> set[str]:
    ref = ArtifactRef(run_id=run_id, artifact_id=artifact_id, version=version)
    return {a.artifact_id for a in graph.get_ancestors(ref)}


def test_financial_section_ancestors_no_user_metrics(
    baseline, store: ArtifactStore
) -> None:
    g = ArtifactGraph.from_store(store, "run_001")
    anc = _ancestor_ids(g, "claim_report_financial_section", "run_001", 1)
    assert "metric_user_growth" not in anc


def test_financial_section_ancestors_no_operation_metrics(
    baseline, store: ArtifactStore
) -> None:
    g = ArtifactGraph.from_store(store, "run_001")
    anc = _ancestor_ids(g, "claim_report_financial_section", "run_001", 1)
    for op_metric in ("metric_conversion_rate", "metric_order_growth"):
        assert op_metric not in anc


def test_user_section_ancestors_no_financial_metrics(
    baseline, store: ArtifactStore
) -> None:
    g = ArtifactGraph.from_store(store, "run_001")
    anc = _ancestor_ids(g, "claim_report_user_section", "run_001", 1)
    for fin_metric in ("metric_revenue_growth", "metric_profit_margin", "metric_cost_ratio"):
        assert fin_metric not in anc


def test_three_branches_converge_only_at_merged_report(
    baseline, store: ArtifactStore
) -> None:
    """Each section's downstream closure is just itself + merged_report.
    Sections do not feed into each other."""
    g = ArtifactGraph.from_store(store, "run_001")
    for section_id in ("claim_report_financial_section",
                       "claim_report_user_section",
                       "claim_report_operation_section"):
        ref = ArtifactRef(run_id="run_001", artifact_id=section_id, version=1)
        desc = {r.artifact_id for r in g.get_descendants(ref)}
        # only the merged report is downstream of any section
        assert desc == {"report_merged"}, (
            f"{section_id} has unexpected downstream: {desc}"
        )


# =====================================================================
# 17-21: mixed-version merge (the heart of Day 6)
# =====================================================================

def test_metric_v2_promotes_claim_to_v2(baseline, store: ArtifactStore) -> None:
    # baseline: all metrics v1, financial claim v1
    # tamper revenue_growth -> v2
    rg_v1 = store.get("run_001", "metric_revenue_growth")
    assert rg_v1 is not None
    store.save(rg_v1.model_copy(update={"value": 0.5, "version": 2}))
    rg_v2 = store.get("run_001", "metric_revenue_growth")
    assert rg_v2 is not None and rg_v2.version == 2

    # regenerate financial claim with v2 revenue_growth
    ctx = RunContext(run_id="run_001", store=store)
    fc_v2 = generate_financial_claim(
        rg_v2,
        store.get("run_001", "metric_profit_margin"),
        store.get("run_001", "metric_cost_ratio"),
        ctx=ctx, llm_client=default_workflow_llm(),
    )
    assert fc_v2.version == 2
    # input refs now point at v2 revenue_growth + v1 others
    input_keys = {(r.artifact_id, r.version) for r in fc_v2.input_artifact_ids}
    assert ("metric_revenue_growth", 2) in input_keys
    assert ("metric_profit_margin", 1) in input_keys
    assert ("metric_cost_ratio", 1) in input_keys


def test_financial_section_v2(baseline, store: ArtifactStore) -> None:
    rg_v1 = store.get("run_001", "metric_revenue_growth")
    assert rg_v1 is not None
    store.save(rg_v1.model_copy(update={"value": 0.5, "version": 2}))
    ctx = RunContext(run_id="run_001", store=store)
    fc_v2 = generate_financial_claim(
        store.get("run_001", "metric_revenue_growth"),
        store.get("run_001", "metric_profit_margin"),
        store.get("run_001", "metric_cost_ratio"),
        ctx=ctx, llm_client=default_workflow_llm(),
    )
    fs_v2 = generate_financial_section(fc_v2, ctx=ctx, llm_client=default_workflow_llm())
    assert fs_v2.version == 2
    assert fs_v2.input_artifact_ids[0].artifact_id == "claim_financial_growth"
    assert fs_v2.input_artifact_ids[0].version == 2


def test_user_and_operation_stay_v1_when_only_financial_upgrades(
    baseline, store: ArtifactStore
) -> None:
    """User / Operation claims and sections must NOT get bumped to v2
    just because Financial did."""
    rg_v1 = store.get("run_001", "metric_revenue_growth")
    assert rg_v1 is not None
    store.save(rg_v1.model_copy(update={"value": 0.5, "version": 2}))

    ctx = RunContext(run_id="run_001", store=store)
    fc_v2 = generate_financial_claim(
        store.get("run_001", "metric_revenue_growth"),
        store.get("run_001", "metric_profit_margin"),
        store.get("run_001", "metric_cost_ratio"),
        ctx=ctx, llm_client=default_workflow_llm(),
    )
    fs_v2 = generate_financial_section(fc_v2, ctx=ctx, llm_client=default_workflow_llm())

    # User and Operation: untouched
    uc = store.get("run_001", "claim_user_growth")
    us = store.get("run_001", "claim_report_user_section")
    oc = store.get("run_001", "claim_operation")
    os_ = store.get("run_001", "claim_report_operation_section")
    assert uc is not None and uc.version == 1
    assert us is not None and us.version == 1
    assert oc is not None and oc.version == 1
    assert os_ is not None and os_.version == 1


def test_merged_report_v2_pins_mixed_versions(
    baseline, store: ArtifactStore
) -> None:
    """The selective-recompute structure test: re-merge with fin v2,
    user v1, op v1. Merged v2 MUST cite the mixed version mix exactly."""
    rg_v1 = store.get("run_001", "metric_revenue_growth")
    assert rg_v1 is not None
    store.save(rg_v1.model_copy(update={"value": 0.5, "version": 2}))

    ctx = RunContext(run_id="run_001", store=store)
    fc_v2 = generate_financial_claim(
        store.get("run_001", "metric_revenue_growth"),
        store.get("run_001", "metric_profit_margin"),
        store.get("run_001", "metric_cost_ratio"),
        ctx=ctx, llm_client=default_workflow_llm(),
    )
    fs_v2 = generate_financial_section(fc_v2, ctx=ctx, llm_client=default_workflow_llm())

    # user/operation stay v1 (their sections come from baseline)
    fs_v1 = baseline.sections["financial"]
    us_v1 = baseline.sections["user"]
    os_v1 = baseline.sections["operation"]
    merged_v2 = merge_report(fs_v2, us_v1, os_v1, ctx=ctx)

    assert merged_v2.version == 2
    input_keys = {(r.artifact_id, r.version) for r in merged_v2.input_artifact_ids}
    assert input_keys == {
        ("claim_report_financial_section", 2),
        ("claim_report_user_section", 1),
        ("claim_report_operation_section", 1),
    }


def test_merged_report_v1_provenance_does_not_drift(
    baseline, store: ArtifactStore
) -> None:
    """After regenerating merged v2, v1 from disk must still point at v1 sections."""
    rg_v1 = store.get("run_001", "metric_revenue_growth")
    assert rg_v1 is not None
    store.save(rg_v1.model_copy(update={"value": 0.5, "version": 2}))

    ctx = RunContext(run_id="run_001", store=store)
    fc_v2 = generate_financial_claim(
        store.get("run_001", "metric_revenue_growth"),
        store.get("run_001", "metric_profit_margin"),
        store.get("run_001", "metric_cost_ratio"),
        ctx=ctx, llm_client=default_workflow_llm(),
    )
    fs_v2 = generate_financial_section(fc_v2, ctx=ctx, llm_client=default_workflow_llm())
    merged_v2 = merge_report(
        fs_v2,
        baseline.sections["user"],
        baseline.sections["operation"],
        ctx=ctx,
    )
    assert merged_v2.version == 2

    # v1 from disk
    v1 = store.get_version("run_001", "report_merged", 1)
    assert v1 is not None
    v1_keys = {(r.artifact_id, r.version) for r in v1.input_artifact_ids}
    assert v1_keys == {
        ("claim_report_financial_section", 1),
        ("claim_report_user_section", 1),
        ("claim_report_operation_section", 1),
    }


# =====================================================================
# 22-23: cross-run + offline
# =====================================================================

def test_cross_run_isolation(baseline, store: ArtifactStore) -> None:
    """Two runs of the same workflow must not bleed into each other."""
    csv = str(Path(__file__).resolve().parent.parent / "data" / "sample_company.csv")
    run_baseline_workflow(csv, "run_other", store, default_workflow_llm())

    assert store.get("run_001", "report_merged") is not None
    assert store.get("run_other", "report_merged") is not None
    assert store.get("run_001", "report_merged").run_id == "run_001"
    assert store.get("run_other", "report_merged").run_id == "run_other"


def test_fake_llm_is_offline() -> None:
    import llm.fake as fake_mod
    import inspect
    src = inspect.getsource(fake_mod)
    for banned in ("urllib", "requests", "httpx", "openai", "anthropic", "dashscope"):
        assert banned not in src, f"FakeLLMClient must not import {banned!r}"


# =====================================================================
# Bonus: per-task responder routing
# =====================================================================

def test_per_task_responder_routing(baseline) -> None:
    """FakeLLM must have routed each task to its dedicated responder."""
    import tempfile
    from llm import default_workflow_llm
    from workflow.tasks import extract_fields as ef
    from workflow.claim_tasks import generate_financial_claim as gfc

    llm2 = default_workflow_llm()
    assert len(llm2.calls) == 0

    s = ArtifactStore(base_dir=tempfile.mkdtemp())
    csv = str(Path(__file__).resolve().parent.parent / "data" / "sample_company.csv")
    ef(csv_path=csv, run_id="run_test", store=s)
    compute_revenue_growth("run_test", s)
    compute_profit_margin("run_test", s)
    compute_cost_ratio("run_test", s)
    ctx = RunContext(run_id="run_test", store=s)
    gfc(
        s.get("run_test", "metric_revenue_growth"),
        s.get("run_test", "metric_profit_margin"),
        s.get("run_test", "metric_cost_ratio"),
        ctx=ctx, llm_client=llm2,
    )
    task_names_called = [name for name, _ in llm2.calls if name]
    assert "generate_financial_claim" in task_names_called
    assert all(name == "generate_financial_claim" for name in task_names_called)
