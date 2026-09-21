"""Offline v2 recovery benchmark built on real recovery execution events.

This module is deliberately isolated from EXP-02 evidence.  It uses the
deterministic fixture LLM and labels all token counts as SYNTHETIC.
"""
from __future__ import annotations

import json
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from artifacts import Artifact, ArtifactRef, ArtifactStatus, ArtifactStore, ArtifactType
from contamination import ContaminationResult, propagate_contamination
from experiments.exp02_faults import (
    inject_agent_output_error,
    inject_evidence_reference_error,
)
from experiments.models import hash_content
from experiments.scenario import (
    DEFAULT_CSV,
    GoldenClean,
    MainFaultScenario,
    build_main_fault_scenario,
    build_visits_fault_scenario,
)
from experiments.strategies import (
    run_coarse_checkpoint_recovery,
    run_full_rerun_recovery,
    run_selective_recovery,
)
from llm import default_workflow_llm
from provenance.decorator import RunContext
from provenance.graph import ArtifactGraph
from recovery import build_recovery_plan
from recovery.decision import DecisionEngine
from workflow.claim_tasks import (
    generate_financial_section,
    generate_operation_section,
    merge_report,
)
from workflow.runner import run_baseline_workflow


STRATEGIES = ("FULL_RERUN", "COARSE_CHECKPOINT", "SELECTIVE_RECOVERY", "AUTO")


@dataclass(frozen=True)
class ScenarioSpec:
    scenario_id: str
    description: str
    factory: Callable[[str], tuple[MainFaultScenario, GoldenClean]]


def _latest_bad_refs(store: ArtifactStore, run_id: str) -> list[ArtifactRef]:
    latest = {}
    for artifact in store.list_all_versions_by_run(run_id):
        current = latest.get(artifact.artifact_id)
        if current is None or artifact.version > current.version:
            latest[artifact.artifact_id] = artifact
    refs = [
        ArtifactRef(run_id=run_id, artifact_id=a.artifact_id, version=a.version)
        for a in latest.values()
        if a.validation_status in {
            ArtifactStatus.INVALIDATED.value,
            ArtifactStatus.CONTAMINATED.value,
        }
    ]
    return sorted(refs, key=lambda ref: (ref.artifact_id, ref.version))


def _clean_base(run_id: str) -> tuple[ArtifactStore, GoldenClean, str]:
    base = Path(tempfile.mkdtemp(prefix="cureflow_v2_bench_"))
    store = ArtifactStore(base_dir=str(base / "artifacts"))
    result = run_baseline_workflow(DEFAULT_CSV, run_id, store, default_workflow_llm())
    report = result.merged_report
    golden = GoldenClean(
        run_id=run_id,
        store=store,
        final_report=report,
        final_report_content=report.content,
        final_report_hash=hash_content(report.content),
    )
    return store, golden, report.content


def _assemble_scenario(
    run_id: str,
    store: ArtifactStore,
    golden: GoldenClean,
    contamination: ContaminationResult,
    pre_fault_report_content: str,
) -> tuple[MainFaultScenario, GoldenClean]:
    graph = ArtifactGraph.from_store(store, run_id)
    target = store.get(run_id, "report_merged")
    if target is None:
        raise RuntimeError("scenario has no report_merged target")
    target_ref = ArtifactRef(run_id=run_id, artifact_id=target.artifact_id, version=target.version)
    affected = sorted({r.artifact_id for r in contamination.invalidated + contamination.contaminated})
    closure = {r.artifact_id for r in graph.get_ancestors(target_ref)} | {target_ref.artifact_id}
    return MainFaultScenario(
        run_id=run_id,
        store=store,
        target_ref=target_ref,
        contamination=contamination,
        case_id="v2_extension",
        affected_artifact_identities=affected,
        available_unaffected_artifact_identities=sorted(closure - set(affected)),
        pre_fault_report_content=pre_fault_report_content,
        target_closure_artifact_identities=closure,
    ), golden


def _small(run_id: str) -> tuple[MainFaultScenario, GoldenClean]:
    return build_visits_fault_scenario(run_id=run_id, csv_path=DEFAULT_CSV)


def _medium(run_id: str) -> tuple[MainFaultScenario, GoldenClean]:
    return build_main_fault_scenario(run_id=run_id, csv_path=DEFAULT_CSV)


def _full_scope(run_id: str) -> tuple[MainFaultScenario, GoldenClean]:
    store, golden, content = _clean_base(run_id)
    raw = store.get(run_id, "raw_data")
    assert raw is not None
    raw_ref = ArtifactRef(run_id=run_id, artifact_id=raw.artifact_id, version=raw.version)
    store.update_status(run_id, raw.artifact_id, ArtifactStatus.INVALIDATED)
    graph = ArtifactGraph.from_store(store, run_id)
    contamination = propagate_contamination(graph, store, run_id, [raw_ref])
    return _assemble_scenario(run_id, store, golden, contamination, content)


def _evidence_reference(run_id: str) -> tuple[MainFaultScenario, GoldenClean]:
    store, golden, content = _clean_base(run_id)
    bad_claim, _ = inject_evidence_reference_error(
        store, run_id, "claim_financial_growth",
    )
    # ArtifactGraph deliberately rejects dangling refs.  Materialise the wrong
    # evidence identity so this remains a semantic reference error rather than
    # failing earlier as a malformed graph.
    store.save(Artifact(
        artifact_id="corrupted_evidence_0",
        artifact_type=ArtifactType.FIELD,
        producer_task="v2_benchmark_wrong_evidence",
        run_id=run_id,
        version=999,
        value=-999.0,
        meta={"benchmark_only": True, "semantic_role": "wrong_evidence"},
    ))
    store.update_status(run_id, bad_claim.artifact_id, ArtifactStatus.INVALIDATED)
    ctx = RunContext(run_id=run_id, store=store)
    llm = default_workflow_llm()
    financial_section = generate_financial_section(bad_claim, ctx=ctx, llm_client=llm)
    user_section = store.get(run_id, "claim_report_user_section")
    operation_section = store.get(run_id, "claim_report_operation_section")
    assert user_section is not None and operation_section is not None
    merge_report(financial_section, user_section, operation_section, ctx=ctx)
    graph = ArtifactGraph.from_store(store, run_id)
    seed = ArtifactRef(run_id=run_id, artifact_id=bad_claim.artifact_id, version=bad_claim.version)
    contamination = propagate_contamination(graph, store, run_id, [seed])
    return _assemble_scenario(run_id, store, golden, contamination, content)


def _combined(run_id: str) -> tuple[MainFaultScenario, GoldenClean]:
    scenario, golden = build_main_fault_scenario(run_id=run_id, csv_path=DEFAULT_CSV)
    store = scenario.store
    bad_operation, _ = inject_agent_output_error(store, run_id, "claim_operation")
    store.update_status(run_id, bad_operation.artifact_id, ArtifactStatus.INVALIDATED)
    ctx = RunContext(run_id=run_id, store=store)
    operation_section = generate_operation_section(
        bad_operation, ctx=ctx, llm_client=default_workflow_llm(),
    )
    financial_section = store.get(run_id, "claim_report_financial_section")
    user_section = store.get(run_id, "claim_report_user_section")
    assert financial_section is not None and user_section is not None
    merge_report(financial_section, user_section, operation_section, ctx=ctx)
    graph = ArtifactGraph.from_store(store, run_id)
    seeds = list(scenario.contamination.invalidated) + [
        ArtifactRef(run_id=run_id, artifact_id=bad_operation.artifact_id, version=bad_operation.version)
    ]
    newly = propagate_contamination(graph, store, run_id, seeds)
    bad = _latest_bad_refs(store, run_id)
    invalidated = [
        ref for ref in bad
        if store.get_version(ref.run_id, ref.artifact_id, ref.version).validation_status
        == ArtifactStatus.INVALIDATED.value
    ]
    contaminated = [ref for ref in bad if ref not in invalidated]
    contamination = ContaminationResult(
        run_id=run_id,
        invalidated=invalidated,
        contaminated=contaminated,
        unaffected=sorted(graph.nodes() - set(bad), key=lambda r: (r.artifact_id, r.version)),
        propagation_edges=sorted(
            list(scenario.contamination.propagation_edges) + list(newly.propagation_edges),
            key=lambda e: (e.source.artifact_id, e.target.artifact_id, e.via.value),
        ),
    )
    return _assemble_scenario(
        run_id, store, golden, contamination, scenario.pre_fault_report_content,
    )


SCENARIOS = (
    ScenarioSpec("small_operation", "单字段运营分支污染", _small),
    ScenarioSpec("medium_financial", "单字段财务分支污染", _medium),
    ScenarioSpec("full_scope", "raw_data 顶层全范围污染", _full_scope),
    ScenarioSpec("evidence_reference", "财务 Claim 证据引用污染", _evidence_reference),
    ScenarioSpec("combined", "财务数据与运营 Agent 输出组合污染", _combined),
)


def _execute(scenario: MainFaultScenario, golden: GoldenClean, requested: str) -> dict:
    graph = ArtifactGraph.from_store(scenario.store, scenario.run_id)
    decision = DecisionEngine().decide(scenario.contamination, graph)
    chosen = decision.strategy_name if requested == "AUTO" else requested
    plan = build_recovery_plan(
        chosen, graph, scenario.store, scenario.run_id,
        scenario.target_ref, scenario.contamination,
    )
    started = time.perf_counter()
    try:
        if chosen == "FULL_RERUN":
            result = run_full_rerun_recovery(scenario, golden, DEFAULT_CSV)
        elif chosen == "COARSE_CHECKPOINT":
            result = run_coarse_checkpoint_recovery(scenario, golden, DEFAULT_CSV)
        else:
            result = run_selective_recovery(scenario, golden)
        wall_ms = (time.perf_counter() - started) * 1000.0
        checks = {
            "semantic_clean_path": result.semantic_clean_path_pass,
            "execution_integrity": result.execution_integrity_pass,
            "output_equivalence": result.matches_golden_output,
        }
        return {
            "requested_strategy": requested,
            "chosen_strategy": chosen,
            "decision": {
                "strategy": decision.strategy_name,
                "affected_count": decision.affected_count,
                "total_count": decision.total_count,
                "affected_ratio": round(decision.affected_ratio, 6),
                "reason": decision.reason,
            },
            "success": result.success,
            "failure_reason": result.failure_reason,
            "plan_steps": len(plan.steps),
            "planned_reused_artifacts": len(plan.reused_refs),
            "actual_task_calls": result.task_calls,
            "actual_tool_task_calls": result.tool_task_calls,
            "actual_llm_task_calls": result.llm_task_calls,
            "actual_llm_calls": result.llm_calls,
            "fixture_input_tokens": result.synthetic_input_tokens,
            "fixture_output_tokens": result.synthetic_output_tokens,
            "usage_type": "SYNTHETIC",
            "wall_time_ms": round(wall_ms, 4),
            "recomputed_artifacts": result.artifact_materializations,
            "reused_artifacts": result.actively_reused_count,
            "revalidation_checks": checks,
            "revalidated_artifacts": sum(bool(value) for value in checks.values()),
            "residual_contamination": len(_latest_bad_refs(scenario.store, scenario.run_id)),
            "baseline_output_fingerprint": golden.final_report_hash,
            "recovered_output_fingerprint": result.final_content_hash,
            "output_equivalent": result.matches_golden_output,
        }
    except Exception as exc:  # benchmark records unsupported execution paths
        return {
            "requested_strategy": requested,
            "chosen_strategy": chosen,
            "decision": {
                "strategy": decision.strategy_name,
                "affected_count": decision.affected_count,
                "total_count": decision.total_count,
                "affected_ratio": round(decision.affected_ratio, 6),
                "reason": decision.reason,
            },
            "success": False,
            "failure_reason": f"{type(exc).__name__}: {exc}",
            "plan_steps": len(plan.steps),
            "planned_reused_artifacts": len(plan.reused_refs),
            "actual_task_calls": 0,
            "actual_tool_task_calls": 0,
            "actual_llm_task_calls": 0,
            "actual_llm_calls": 0,
            "fixture_input_tokens": 0,
            "fixture_output_tokens": 0,
            "usage_type": "SYNTHETIC",
            "wall_time_ms": round((time.perf_counter() - started) * 1000.0, 4),
            "recomputed_artifacts": 0,
            "reused_artifacts": 0,
            "revalidation_checks": {},
            "revalidated_artifacts": 0,
            "residual_contamination": len(_latest_bad_refs(scenario.store, scenario.run_id)),
            "baseline_output_fingerprint": golden.final_report_hash,
            "recovered_output_fingerprint": None,
            "output_equivalent": False,
        }


def _add_reductions(rows: list[dict]) -> None:
    full = next(row for row in rows if row["requested_strategy"] == "FULL_RERUN")
    dimensions = {
        "plan_step_reduction_vs_full": "plan_steps",
        "task_call_reduction_vs_full": "actual_task_calls",
        "llm_call_reduction_vs_full": "actual_llm_calls",
        "fixture_token_reduction_vs_full": None,
    }
    full_tokens = full["fixture_input_tokens"] + full["fixture_output_tokens"]
    for row in rows:
        for output, field in dimensions.items():
            if field is None:
                base, value = full_tokens, row["fixture_input_tokens"] + row["fixture_output_tokens"]
            else:
                base, value = full[field], row[field]
            row[output] = round((base - value) / base, 6) if base else 0.0


def run_benchmark() -> dict:
    cases = []
    for spec in SCENARIOS:
        rows = []
        for requested in STRATEGIES:
            scenario, golden = spec.factory(f"v2_{spec.scenario_id}_{requested.lower()}")
            rows.append(_execute(scenario, golden, requested))
        _add_reductions(rows)
        cases.append({
            "scenario_id": spec.scenario_id,
            "description": spec.description,
            "runs": rows,
        })
    return {
        "schema_version": "1.0",
        "mode": "OFFLINE_DETERMINISTIC_FIXTURE",
        "usage_note": "All token counts are fixture/synthetic, not provider-reported.",
        "scenario_count": len(cases),
        "run_count": sum(len(case["runs"]) for case in cases),
        "cases": cases,
    }


def render_report(data: dict) -> str:
    lines = [
        "# CureFlow v2 实际恢复执行 Benchmark", "",
        "> 离线确定性 fixture；token 均为 synthetic/fixture token，不代表第三方 Provider。", "",
        f"- 场景：{data['scenario_count']}",
        f"- 执行路径：{data['run_count']}",
        "- reduction 分别按 plan、实际 task call、实际 LLM call 和 fixture token 独立计算。", "",
    ]
    for case in data["cases"]:
        lines.extend([
            f"## {case['scenario_id']} — {case['description']}", "",
            "| 请求/实际策略 | 影响范围 | 成功 | plan | task | LLM calls | fixture tokens | reused | residual | output 等价 | task reduction |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ])
        for row in case["runs"]:
            tokens = row["fixture_input_tokens"] + row["fixture_output_tokens"]
            decision = row["decision"]
            lines.append(
                f"| {row['requested_strategy']}/{row['chosen_strategy']} | "
                f"{decision['affected_count']}/{decision['total_count']} | "
                f"{str(row['success']).lower()} | {row['plan_steps']} | "
                f"{row['actual_task_calls']} | {row['actual_llm_calls']} | {tokens} | "
                f"{row['reused_artifacts']} | {row['residual_contamination']} | "
                f"{str(row['output_equivalent']).lower()} | "
                f"{row['task_call_reduction_vs_full']:.2%} |"
            )
        failures = [row for row in case["runs"] if not row["success"]]
        if failures:
            lines.extend(["", "失败路径（保留真实结果，不静默跳过）："])
            lines.extend(
                f"- `{row['requested_strategy']}`：{row['failure_reason']}"
                for row in failures
            )
        lines.append("")
    lines.extend([
        "## 证据边界", "",
        "- 本报告证明的是离线恢复执行行为，不替代 M1/M3 真实 Provider evidence。",
        "- plan step、task call、LLM call、token 和 wall time 是不同指标，禁止互相替代。",
        "- 单个样本的 reduction 不外推为所有工作流的普遍性能。", "",
    ])
    return "\n".join(lines)


def write_outputs(output_dir: Path) -> tuple[Path, Path]:
    data = run_benchmark()
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "benchmark_data.json"
    report_path = output_dir / "benchmark_report.md"
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(render_report(data), encoding="utf-8")
    return json_path, report_path


__all__ = ["SCENARIOS", "STRATEGIES", "render_report", "run_benchmark", "write_outputs"]
