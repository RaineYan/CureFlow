"""
Scenario builders — Day 10.

Two deterministic fault scenarios:

  Case A — Revenue field fault  (build_main_fault_scenario)
    baseline clean v1
      -> field_revenue_2025@v2 = ground truth * 10  (INVALIDATED)
      -> 3 financial metrics @v2 CONTAMINATED
      -> claim_financial_growth @v2 CONTAMINATED
      -> claim_report_financial_section @v2 CONTAMINATED
      -> report_merged @v2 CONTAMINATED
    User / Operation branches: v1, VALID.

  Case B — Visits field fault  (build_visits_fault_scenario)
    baseline clean v1
      -> field_visits_2025@v2 = ground truth * 10  (INVALIDATED)
      -> metric_conversion_rate @v2 CONTAMINATED
      -> claim_operation @v2 CONTAMINATED
      -> claim_report_operation_section @v2 CONTAMINATED
      -> report_merged @v2 CONTAMINATED
    Financial / User branches + metric_order_growth: v1, VALID.

Each scenario returns a MainFaultScenario with:
  - run_id, store, target_ref, contamination
  - affected_artifact_identities    (set of artifact_ids that are
                                    INVALIDATED or CONTAMINATED at
                                    Phase Start)
  - available_unaffected_artifact_identities
                                   (artifact_ids in the target's
                                    relevant workflow closure that are
                                    NOT in affected)

Plus a GoldenClean for content-hash comparison.

Fairness: every scenario builds a brand-new tmpdir and a brand-new
ArtifactStore.  No two strategies ever share a store.
"""
from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Set, Tuple

from artifacts import (
    Artifact,
    ArtifactRef,
    ArtifactStore,
    ArtifactType,
)
from contamination import (
    ContaminationResult,
    detect_field_faults,
    propagate_contamination,
)
from llm import default_workflow_llm
from provenance.decorator import RunContext, track_provenance
from provenance.graph import ArtifactGraph

from .fault_injection import inject_field_value_tamper


DEFAULT_CSV = str(
    Path(__file__).resolve().parent.parent / "data" / "sample_company.csv"
)


# =====================================================================
# Reusable decorated metric recomputes (so we can @track_provenance
# metric recomputes inside a scenario without importing from tests).
# =====================================================================

@track_provenance(output_artifact_id="metric_revenue_growth", artifact_type=ArtifactType.METRIC)
def _mrg_dec(revenue_2025, revenue_2024, *, ctx):
    return (revenue_2025.value - revenue_2024.value) / revenue_2024.value


@track_provenance(output_artifact_id="metric_profit_margin", artifact_type=ArtifactType.METRIC)
def _mpm_dec(revenue_2025, cost_2025, *, ctx):
    return (revenue_2025.value - cost_2025.value) / revenue_2025.value


@track_provenance(output_artifact_id="metric_cost_ratio", artifact_type=ArtifactType.METRIC)
def _mcr_dec(cost_2025, revenue_2025, *, ctx):
    return cost_2025.value / revenue_2025.value


# =====================================================================
# Output containers
# =====================================================================

@dataclass
class GoldenClean:
    run_id: str
    store: ArtifactStore
    final_report: Artifact
    final_report_content: str
    final_report_hash: str


@dataclass
class MainFaultScenario:
    run_id: str
    store: ArtifactStore
    target_ref: ArtifactRef
    contamination: ContaminationResult
    case_id: str                          # "case_a" / "case_b"
    affected_artifact_identities: List[str]
    available_unaffected_artifact_identities: List[str]
    pre_fault_report_content: str
    target_closure_artifact_identities: Set[str]    # all ids reachable upstream of target


# =====================================================================
# Re-runnable field extraction (no raw_data re-save)
# =====================================================================

def rerun_field_extraction(
    csv_path: str,
    run_id: str,
    store: ArtifactStore,
) -> dict[str, Artifact]:
    import pandas as pd
    from artifacts import Artifact
    from workflow.tasks import (
        RAW_DATA_ARTIFACT_ID, YEAR_COLUMNS, _field_id, _ref,
    )

    df = pd.read_csv(csv_path)
    raw_ref = _ref(run_id, RAW_DATA_ARTIFACT_ID, 1)

    out: dict[str, Artifact] = {}
    for spec in YEAR_COLUMNS:
        column = spec["column"]
        year = spec["year"]
        aggregation = spec["aggregation"]
        semantic = spec["semantic"]
        if column not in df.columns:
            raise KeyError(f"Column '{column}' not in CSV {csv_path}")
        year_df = df.loc[df["year"] == year, column]
        if aggregation == "sum":
            value = float(year_df.sum())
        elif aggregation == "mean":
            value = float(year_df.mean())
        else:
            raise ValueError(f"Unknown aggregation: {aggregation!r}")
        existing = store.get(run_id, _field_id(column, year))
        next_version = (existing.version + 1) if existing else 1
        a = Artifact(
            artifact_id=_field_id(column, year),
            artifact_type=ArtifactType.FIELD,
            producer_task="extract_fields",
            run_id=run_id,
            version=next_version,
            value=value,
            input_artifact_ids=[raw_ref],
            meta={
                "column": column,
                "year": year,
                "aggregation": aggregation,
                "semantic": semantic,
            },
        )
        store.save(a)
        out[_field_id(column, year)] = a
    return out


# =====================================================================
# Re-runnable 6-metric computation
# =====================================================================

def rerun_all_metrics(
    run_id: str,
    store: ArtifactStore,
) -> dict[str, Artifact]:
    from artifacts import Artifact
    from workflow.tasks import _get_field, _ref, _field_id

    rev25 = _get_field(store, run_id, "revenue", 2025)
    rev24 = _get_field(store, run_id, "revenue", 2024)
    cost25 = _get_field(store, run_id, "cost", 2025)
    users25 = _get_field(store, run_id, "users", 2025)
    users24 = _get_field(store, run_id, "users", 2024)
    orders25 = _get_field(store, run_id, "orders", 2025)
    visits25 = _get_field(store, run_id, "visits", 2025)
    orders24 = _get_field(store, run_id, "orders", 2024)

    revenue_growth = (rev25 - rev24) / rev24
    profit_margin = (rev25 - cost25) / rev25
    cost_ratio = cost25 / rev25
    user_growth = users25 / users24
    conversion_rate = orders25 / visits25
    order_growth = orders25 / orders24

    def _save(metric_id: str, producer: str, value: float, refs: list) -> Artifact:
        existing = store.get(run_id, metric_id)
        next_version = (existing.version + 1) if existing else 1
        a = Artifact(
            artifact_id=metric_id,
            artifact_type=ArtifactType.METRIC,
            producer_task=producer,
            run_id=run_id,
            version=next_version,
            value=value,
            input_artifact_ids=refs,
            meta={"task_kind": "tool"},
        )
        store.save(a)
        return a

    return {
        "metric_revenue_growth": _save(
            "metric_revenue_growth", "compute_revenue_growth", revenue_growth,
            [_ref(run_id, _field_id("revenue", 2025)),
             _ref(run_id, _field_id("revenue", 2024))]),
        "metric_profit_margin": _save(
            "metric_profit_margin", "compute_profit_margin", profit_margin,
            [_ref(run_id, _field_id("revenue", 2025)),
             _ref(run_id, _field_id("cost", 2025))]),
        "metric_cost_ratio": _save(
            "metric_cost_ratio", "compute_cost_ratio", cost_ratio,
            [_ref(run_id, _field_id("cost", 2025)),
             _ref(run_id, _field_id("revenue", 2025))]),
        "metric_user_growth": _save(
            "metric_user_growth", "compute_user_growth", user_growth,
            [_ref(run_id, _field_id("users", 2025)),
             _ref(run_id, _field_id("users", 2024))]),
        "metric_conversion_rate": _save(
            "metric_conversion_rate", "compute_conversion_rate", conversion_rate,
            [_ref(run_id, _field_id("orders", 2025)),
             _ref(run_id, _field_id("visits", 2025))]),
        "metric_order_growth": _save(
            "metric_order_growth", "compute_order_growth", order_growth,
            [_ref(run_id, _field_id("orders", 2025)),
             _ref(run_id, _field_id("orders", 2024))]),
    }


# =====================================================================
# Golden clean baseline
# =====================================================================

def build_golden_clean_scenario(
    run_id: str = "run_golden",
    csv_path: str = DEFAULT_CSV,
) -> GoldenClean:
    from workflow.runner import run_baseline_workflow
    from .models import hash_content
    tmpdir = Path(tempfile.mkdtemp(prefix="poc_day10_golden_"))
    store = ArtifactStore(base_dir=str(tmpdir / "artifacts"))
    llm = default_workflow_llm()
    result = run_baseline_workflow(csv_path, run_id, store, llm)
    content = result.merged_report.content
    return GoldenClean(
        run_id=run_id,
        store=store,
        final_report=result.merged_report,
        final_report_content=content,
        final_report_hash=hash_content(content),
    )


# =====================================================================
# Internal: build the "affected / available" identity sets from a
# ContaminationResult and the target's relevant workflow closure.
# =====================================================================

def _identity_sets(
    contamination: ContaminationResult,
    graph: ArtifactGraph,
    target_ref: ArtifactRef,
) -> Tuple[Set[str], Set[str]]:
    affected: Set[str] = set()
    for r in list(contamination.invalidated) + list(contamination.contaminated):
        affected.add(r.artifact_id)
    # also include the target itself (even if its identity is unaffected
    # at Phase Start, it is the one we are recovering; we treat it as
    # always-affected for reuse accounting).
    affected.add(target_ref.artifact_id)

    # target's upstream closure: every artifact_id reachable from
    # the target via reverse BFS.
    closure: Set[str] = {target_ref.artifact_id}
    for ref in graph.get_ancestors(target_ref) | {target_ref}:
        closure.add(ref.artifact_id)

    # raw_data is the source of truth — no strategy re-materializes it.
    # Exclude it from both sets so it does not inflate reuse or
    # available counts.
    closure.discard("raw_data")

    available = closure - affected
    return affected, available


# =====================================================================
# Case A — Revenue field fault (Day 8 main case, identical structure)
# =====================================================================

def build_main_fault_scenario(
    run_id: str,
    csv_path: str = DEFAULT_CSV,
) -> Tuple[MainFaultScenario, GoldenClean]:
    from workflow.claim_tasks import (
        generate_financial_claim,
        generate_financial_section,
        merge_report,
    )
    from .models import hash_content
    from workflow.runner import run_baseline_workflow

    tmpdir = Path(tempfile.mkdtemp(prefix=f"poc_day10_faultA_{run_id}_"))
    store = ArtifactStore(base_dir=str(tmpdir / "artifacts"))
    llm = default_workflow_llm()
    result = run_baseline_workflow(csv_path, run_id, store, llm)
    pre_fault_content = result.merged_report.content

    inject_field_value_tamper(store, run_id, "field_revenue_2025", factor=10.0)

    ctx = RunContext(run_id=run_id, store=store)
    _mrg_dec(
        store.get(run_id, "field_revenue_2025"),
        store.get(run_id, "field_revenue_2024"),
        ctx=ctx,
    )
    _mpm_dec(
        store.get(run_id, "field_revenue_2025"),
        store.get(run_id, "field_cost_2025"),
        ctx=ctx,
    )
    _mcr_dec(
        store.get(run_id, "field_cost_2025"),
        store.get(run_id, "field_revenue_2025"),
        ctx=ctx,
    )

    fc = generate_financial_claim(
        store.get(run_id, "metric_revenue_growth"),
        store.get(run_id, "metric_profit_margin"),
        store.get(run_id, "metric_cost_ratio"),
        ctx=ctx, llm_client=llm,
    )
    fs = generate_financial_section(fc, ctx=ctx, llm_client=llm)
    merge_report(
        fs,
        store.get_version(run_id, "claim_report_user_section", 1),
        store.get_version(run_id, "claim_report_operation_section", 1),
        ctx=ctx,
    )

    detect_field_faults(run_id, store)
    g = ArtifactGraph.from_store(store, run_id)
    contamination = propagate_contamination(g, store, run_id)

    target = ArtifactRef(run_id=run_id, artifact_id="report_merged", version=2)
    affected, available = _identity_sets(contamination, g, target)

    golden = build_golden_clean_scenario(
        run_id=f"{run_id}_golden", csv_path=csv_path,
    )

    return MainFaultScenario(
        run_id=run_id,
        store=store,
        target_ref=target,
        contamination=contamination,
        case_id="case_a",
        affected_artifact_identities=sorted(affected),
        available_unaffected_artifact_identities=sorted(available),
        pre_fault_report_content=pre_fault_content,
        target_closure_artifact_identities=affected | available,
    ), golden


# =====================================================================
# Case B — Visits field fault
#   field_visits_2025@v2 = ground_truth * 10
#     -> metric_conversion_rate @v2 CONTAMINATED
#     -> claim_operation @v2 CONTAMINATED  (evidence: conversion_rate only)
#     -> claim_report_operation_section @v2 CONTAMINATED
#     -> report_merged @v2 CONTAMINATED  (tool INPUT)
#   Unaffected: all Financial / User / order_growth
# =====================================================================

def build_visits_fault_scenario(
    run_id: str,
    csv_path: str = DEFAULT_CSV,
) -> Tuple[MainFaultScenario, GoldenClean]:
    from workflow.claim_tasks import (
        generate_operation_claim,
        generate_operation_section,
        merge_report,
    )
    from .models import hash_content
    from workflow.runner import run_baseline_workflow

    tmpdir = Path(tempfile.mkdtemp(prefix=f"poc_day10_faultB_{run_id}_"))
    store = ArtifactStore(base_dir=str(tmpdir / "artifacts"))
    llm = default_workflow_llm()
    result = run_baseline_workflow(csv_path, run_id, store, llm)
    pre_fault_content = result.merged_report.content

    inject_field_value_tamper(store, run_id, "field_visits_2025", factor=10.0)

    ctx = RunContext(run_id=run_id, store=store)

    @track_provenance(output_artifact_id="metric_conversion_rate", artifact_type=ArtifactType.METRIC)
    def cr_dec(orders_2025, visits_2025, *, ctx):
        return orders_2025.value / visits_2025.value

    cr_dec(
        store.get(run_id, "field_orders_2025"),
        store.get(run_id, "field_visits_2025"),
        ctx=ctx,
    )
    # NB: metric_order_growth uses orders_2025 / orders_2024 — does
    # NOT depend on visits.  We DO NOT recompute it.  Its v1 stays
    # VALID.

    oc = generate_operation_claim(
        store.get(run_id, "metric_conversion_rate"),
        store.get(run_id, "metric_order_growth"),
        ctx=ctx, llm_client=llm,
    )
    os_ = generate_operation_section(oc, ctx=ctx, llm_client=llm)
    merge_report(
        store.get_version(run_id, "claim_report_financial_section", 1),
        store.get_version(run_id, "claim_report_user_section", 1),
        os_,
        ctx=ctx,
    )

    detect_field_faults(run_id, store)
    g = ArtifactGraph.from_store(store, run_id)
    contamination = propagate_contamination(g, store, run_id)

    target = ArtifactRef(run_id=run_id, artifact_id="report_merged", version=2)
    affected, available = _identity_sets(contamination, g, target)

    golden = build_golden_clean_scenario(
        run_id=f"{run_id}_golden", csv_path=csv_path,
    )

    return MainFaultScenario(
        run_id=run_id,
        store=store,
        target_ref=target,
        contamination=contamination,
        case_id="case_b",
        affected_artifact_identities=sorted(affected),
        available_unaffected_artifact_identities=sorted(available),
        pre_fault_report_content=pre_fault_content,
        target_closure_artifact_identities=affected | available,
    ), golden
