"""M3 Phase B analysis + cross-provider comparison.

Reads 60 Phase B trial JSONs from m3_cross_provider/repeats/repeat_NN/.
Reads 60 M1 trial JSONs from m1_5repeat_real/.
Computes:
  - m3_phase_b_strategy_summary.json
  - m3_phase_b_matched_pair_analysis.json
  - m3_phase_b_cross_provider_comparison.json
  - m3_phase_b_failure_forensic.json
  - m3_phase_b_trial_matrix.csv / .json
  - m3_phase_b_freeze_manifest.json

Polluted evidence (pilot_polluted_pre_provider_adapter_fix/) is NEVER read.
"""
from __future__ import annotations
import csv
import json
import statistics as st
import sys
import hashlib
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
M3 = ROOT / "experiments/results/exp02/m3_cross_provider"
REPEATS = M3 / "repeats"
M1 = ROOT / "experiments/results/exp02/m1_5repeat_real"
PILOT = M3 / "pilot"

from experiments.exp02_faults import CANONICAL_CASES  # type: ignore
CASE_META = {cid: {"scenario_id": sid, "fault_type": ft, "target_artifact_id": tgt}
             for cid, sid, ft, tgt in CANONICAL_CASES}

CASES = ["CASE_A", "CASE_B", "CASE_C", "CASE_D"]
STRATS = ["FULL_RERUN", "COARSE_CHECKPOINT", "SELECTIVE_RECOVERY"]
REPEAT_IDS = ["repeat_01", "repeat_02", "repeat_03", "repeat_04", "repeat_05"]


def build_rows():
    rows = []
    for rep in REPEAT_IDS:
        rep_dir = REPEATS / rep
        if not rep_dir.exists():
            continue
        for case in CASES:
            for strat in STRATS:
                trial_id = f"m3b_{case}_{strat.lower()}_{rep}"
                p = rep_dir / f"{trial_id}.json"
                if not p.exists():
                    continue
                t = json.loads(p.read_text(encoding="utf-8"))
                meta = CASE_META.get(case, {})
                env_kind = None
                sub = t.get("infrastructure_failure_subtype", "")
                if sub.startswith("INFRA_FAILURE_"):
                    env_kind = sub.replace("INFRA_FAILURE_", "")
                rows.append({
                    "trial_id": trial_id,
                    "case_id": t.get("case_id"),
                    "scenario_id": t.get("scenario_id") or meta.get("scenario_id", "N/A"),
                    "fault_type": meta.get("fault_type", "N/A"),
                    "target_artifact_id": meta.get("target_artifact_id", "N/A"),
                    "strategy": t.get("strategy"),
                    "repeat_index": rep,
                    "provider": "deepseek",
                    "model": t.get("model"),
                    "provider_execution_success": t.get("provider_success"),
                    "recovery_execution_success": t.get("recovery_execution_success"),
                    "output_contract_valid": t.get("output_contract_valid"),
                    "semantic_validation_success": t.get("semantic_validation_success"),
                    "trial_valid": t.get("trial_valid"),
                    "workflow_contract_success": t.get("workflow_contract_success"),
                    "recovery_success": t.get("recovery_success"),
                    "golden_match": t.get("golden_match"),
                    "new_llm_calls": t.get("llm_calls"),
                    "provider_reported_tokens": t.get("tokens_total"),
                    "wall_time_ms": t.get("wall_time_ms"),
                    "reused_artifacts": "N/A",
                    "failure_class": t.get("failure_type", "N/A"),
                    "failure_subtype": t.get("infrastructure_failure_subtype", "N/A"),
                    "failure_reason": t.get("error_message", "N/A"),
                    "envelope_kind": env_kind,
                    "retry_attempt": t.get("retry_attempt", 0),
                    "retry_outcome": t.get("retry_outcome", "NOT_RETRIED"),
                })
    return rows


def by_strat_summary(rows, strat):
    sub = [r for r in rows if r["strategy"] == strat]
    ok = [r for r in sub if r["provider_execution_success"]]
    valid = [r for r in sub if r["trial_valid"]]
    ok_calls = [r["new_llm_calls"] for r in ok if r["new_llm_calls"] is not None]
    ok_tokens = [r["provider_reported_tokens"] for r in ok if r["provider_reported_tokens"] is not None]
    ok_walls = [r["wall_time_ms"] for r in ok if r["wall_time_ms"] is not None]
    n = len(sub)
    return {
        "strategy": strat,
        "total_trials": n,
        "provider_success_count": len(ok),
        "provider_success_rate": round(len(ok) / n, 4) if n else None,
        "trial_valid_count": len(valid),
        "trial_valid_rate": round(len(valid) / n, 4) if n else None,
        "recovery_success_count": sum(1 for r in sub if r["recovery_success"]),
        "workflow_contract_success_count": sum(1 for r in sub if r["workflow_contract_success"]),
        "golden_match_count": sum(1 for r in sub if r["golden_match"]),
        "calls": {
            "mean": round(st.mean(ok_calls), 3) if ok_calls else None,
            "median": round(st.median(ok_calls), 3) if ok_calls else None,
            "sd": round(st.pstdev(ok_calls), 3) if len(ok_calls) > 1 else 0,
            "min": min(ok_calls) if ok_calls else None,
            "max": max(ok_calls) if ok_calls else None,
            "set": sorted(set(ok_calls)) if ok_calls else [],
        },
        "provider_reported_tokens": {
            "mean": round(st.mean(ok_tokens), 3) if ok_tokens else None,
            "median": round(st.median(ok_tokens), 3) if ok_tokens else None,
            "sd": round(st.pstdev(ok_tokens), 3) if len(ok_tokens) > 1 else 0,
            "min": min(ok_tokens) if ok_tokens else None,
            "max": max(ok_tokens) if ok_tokens else None,
        },
        "wall_time_ms": {
            "mean": round(st.mean(ok_walls), 1) if ok_walls else None,
            "median": round(st.median(ok_walls), 1) if ok_walls else None,
            "sd": round(st.pstdev(ok_walls), 1) if len(ok_walls) > 1 else 0,
            "min": min(ok_walls) if ok_walls else None,
            "max": max(ok_walls) if ok_walls else None,
        },
    }


def stats_with_ci(values, label):
    if not values:
        return {"n": 0, "label": label, "ci_method": "N/A (n=0)"}
    n = len(values)
    m = st.mean(values)
    med = st.median(values)
    sd = st.pstdev(values) if n > 1 else 0
    if n < 2:
        return {"n": n, "mean": round(m, 3), "median": round(med, 3), "sd": 0,
                "min": round(min(values), 3), "max": round(max(values), 3),
                "ci_95_low": None, "ci_95_high": None, "ci_method": "N/A (n<2)",
                "t_critical": None, "label": label}
    t_table = {2: 12.706, 3: 4.303, 4: 3.182, 5: 2.776, 6: 2.571,
               7: 2.447, 8: 2.365, 9: 2.306, 10: 2.262, 11: 2.228,
               12: 2.201, 13: 2.179, 14: 2.160, 15: 2.145, 16: 2.131,
               17: 2.120, 18: 2.110, 19: 2.101, 20: 2.093, 21: 2.086,
               22: 2.080, 23: 2.074, 24: 2.069, 25: 2.064, 26: 2.060,
               27: 2.056, 28: 2.052, 29: 2.048, 30: 2.045}
    if n >= 30:
        t_crit = 1.96
        method = "z-based 95% CI (n>=30)"
    else:
        t_crit = t_table.get(n, 2.045)
        method = "t-based 95% CI (two-tailed, alpha=0.05)"
    se = sd / (n ** 0.5)
    return {
        "n": n, "mean": round(m, 3), "median": round(med, 3),
        "sd": round(sd, 3), "min": round(min(values), 3),
        "max": round(max(values), 3),
        "p25": round(sorted(values)[max(0, n // 4 - 1)], 3) if n >= 4 else None,
        "p75": round(sorted(values)[min(n - 1, 3 * n // 4)], 3) if n >= 4 else None,
        "ci_95_low": round(m - t_crit * se, 3),
        "ci_95_high": round(m + t_crit * se, 3),
        "ci_method": method, "t_critical": t_crit, "label": label,
    }


def pair_reduction(rows, from_strat, to_strat):
    pair_rows = []
    for case in CASES:
        for rep in REPEAT_IDS:
            a = next((r for r in rows
                      if r["case_id"] == case and r["strategy"] == from_strat
                      and r["repeat_index"] == rep), None)
            b = next((r for r in rows
                      if r["case_id"] == case and r["strategy"] == to_strat
                      and r["repeat_index"] == rep), None)
            if not a or not b:
                pair_rows.append({"case_id": case, "repeat_index": rep,
                                  "status": "MISSING"})
                continue
            if not a["provider_execution_success"]:
                pair_rows.append({
                    "case_id": case, "repeat_index": rep,
                    "status": "EXCLUDED",
                    "exclusion_code": "PAIR_EXCLUDED_INFRA_FAILURE",
                    "exclusion_reason": f"{from_strat} provider_execution_success=False",
                })
                continue
            if not b["provider_execution_success"]:
                pair_rows.append({
                    "case_id": case, "repeat_index": rep,
                    "status": "EXCLUDED",
                    "exclusion_code": "PAIR_EXCLUDED_INFRA_FAILURE",
                    "exclusion_reason": f"{to_strat} provider_execution_success=False",
                })
                continue
            a_calls = a["new_llm_calls"] or 0
            b_calls = b["new_llm_calls"] or 0
            a_tok = a["provider_reported_tokens"] or 0
            b_tok = b["provider_reported_tokens"] or 0
            a_wall = a["wall_time_ms"] or 0
            b_wall = b["wall_time_ms"] or 0
            pair_rows.append({
                "case_id": case, "repeat_index": rep,
                "status": "COMPARABLE",
                f"{from_strat}_calls": a_calls,
                f"{to_strat}_calls": b_calls,
                "calls_reduction_pct": round((a_calls - b_calls) / a_calls * 100, 2) if a_calls else None,
                f"{from_strat}_tokens": a_tok,
                f"{to_strat}_tokens": b_tok,
                "tokens_reduction_pct": round((a_tok - b_tok) / a_tok * 100, 2) if a_tok else None,
                f"{from_strat}_wall_ms": a_wall,
                f"{to_strat}_wall_ms": b_wall,
                "wall_reduction_pct": round((a_wall - b_wall) / a_wall * 100, 2) if a_wall else None,
            })
    comparable = [p for p in pair_rows if p["status"] == "COMPARABLE"]
    excluded = [p for p in pair_rows if p["status"] == "EXCLUDED"]
    calls_vals = [p["calls_reduction_pct"] for p in comparable]
    tokens_vals = [p["tokens_reduction_pct"] for p in comparable]
    wall_vals = [p["wall_reduction_pct"] for p in comparable]
    per_case = {}
    for case in CASES:
        cp = [p for p in comparable if p["case_id"] == case]
        per_case[case] = {
            "n_comparable": len(cp),
            "calls_reduction_mean_pct": round(st.mean([p["calls_reduction_pct"] for p in cp]), 3) if cp else None,
            "tokens_reduction_mean_pct": round(st.mean([p["tokens_reduction_pct"] for p in cp]), 3) if cp else None,
            "wall_reduction_mean_pct": round(st.mean([p["wall_reduction_pct"] for p in cp]), 3) if cp else None,
        }
    return {
        "from_strategy": from_strat,
        "to_strategy": to_strat,
        "comparable_pair_count": len(comparable),
        "excluded_pair_count": len(excluded),
        "excluded_pair_details": excluded,
        "calls_reduction_pct": stats_with_ci(calls_vals, "calls_reduction_pct"),
        "tokens_reduction_pct": stats_with_ci(tokens_vals, "tokens_reduction_pct"),
        "wall_reduction_pct": stats_with_ci(wall_vals, "wall_reduction_pct"),
        "per_case_breakdown": per_case,
        "per_pair_details": pair_rows,
    }


def selective_stability(rows):
    sub = [r for r in rows if r["strategy"] == "SELECTIVE_RECOVERY"]
    return {
        "trials": [
            {k: r[k] for k in (
                "trial_id", "case_id", "repeat_index", "provider_execution_success",
                "trial_valid", "recovery_success", "semantic_validation_success",
                "golden_match", "new_llm_calls", "provider_reported_tokens",
                "wall_time_ms")}
            for r in sub
        ],
        "consistency": {
            "all_20_provider_success": all(r["provider_execution_success"] for r in sub),
            "all_20_trial_valid": all(r["trial_valid"] for r in sub),
            "all_20_recovery_success": all(r["recovery_success"] for r in sub),
            "all_20_semantic_success": all(r["semantic_validation_success"] for r in sub),
            "calls_set": sorted({r["new_llm_calls"] for r in sub if r["new_llm_calls"] is not None}),
        },
    }


def calls_determinism(rows):
    full = [r for r in rows if r["strategy"] == "FULL_RERUN"]
    sel = [r for r in rows if r["strategy"] == "SELECTIVE_RECOVERY"]
    coarse = [r for r in rows if r["strategy"] == "COARSE_CHECKPOINT"]
    full_calls = [r["new_llm_calls"] for r in full if r["new_llm_calls"] is not None]
    sel_calls = [r["new_llm_calls"] for r in sel if r["new_llm_calls"] is not None]
    coarse_calls = [r["new_llm_calls"] for r in coarse if r["new_llm_calls"] is not None]
    return {
        "FULL": {
            "n": len(full_calls),
            "calls_set": sorted(set(full_calls)),
            "exact_6_count": sum(1 for c in full_calls if c == 6),
        },
        "COARSE": {
            "n": len(coarse_calls),
            "calls_set": sorted(set(coarse_calls)),
        },
        "SELECTIVE": {
            "n": len(sel_calls),
            "calls_set": sorted(set(sel_calls)),
            "exact_2_count": sum(1 for c in sel_calls if c == 2),
        },
    }


def failure_forensic(rows):
    infra = [r for r in rows if r["provider_execution_success"] is False]
    runner = []
    exp_outcome = [r for r in rows
                   if r["provider_execution_success"] is True
                   and r["trial_valid"] is False]
    retried = [r for r in rows if r["retry_attempt"] > 0]
    return {
        "infra_failure": {
            "count": len(infra),
            "trials": [
                {
                    "trial_id": r["trial_id"],
                    "case_id": r["case_id"],
                    "strategy": r["strategy"],
                    "repeat_index": r["repeat_index"],
                    "envelope_kind": r["envelope_kind"],
                    "failure_reason_excerpt": (r["failure_reason"] or "")[:160],
                    "retry_attempt": r["retry_attempt"],
                    "retry_outcome": r["retry_outcome"],
                } for r in infra
            ],
        },
        "experiment_outcome": {
            "count": len(exp_outcome),
            "trial_ids": [r["trial_id"] for r in exp_outcome],
        },
        "runner_implementation_error": {
            "count": len(runner),
        },
        "retry_stats": {
            "total_retried": len(retried),
            "now_ok_after_retry": sum(1 for r in retried if r["retry_outcome"] == "NOW_OK"),
            "still_crashed_after_retry": sum(1 for r in retried if r["retry_outcome"] == "STILL_CRASHED"),
        },
    }


def cross_provider(m3b_rows, m1_rows):
    def sub(rows, strat):
        return [r for r in rows if r["strategy"] == strat]

    def reduction_mean(full, sel):
        out = []
        for case in CASES:
            for rep in REPEAT_IDS:
                f = next((r for r in full if r["case_id"] == case
                          and r["repeat_index"] == rep), None)
                s = next((r for r in sel if r["case_id"] == case
                          and r["repeat_index"] == rep), None)
                if (f and s and f["provider_execution_success"]
                        and s["provider_execution_success"]):
                    fc = f["new_llm_calls"] or 0
                    sc = s["new_llm_calls"] or 0
                    if fc:
                        out.append((fc - sc) / fc * 100)
        return out

    def tok_red_mean(full, sel):
        out = []
        for case in CASES:
            for rep in REPEAT_IDS:
                f = next((r for r in full if r["case_id"] == case
                          and r["repeat_index"] == rep), None)
                s = next((r for r in sel if r["case_id"] == case
                          and r["repeat_index"] == rep), None)
                if (f and s and f["provider_execution_success"]
                        and s["provider_execution_success"]):
                    ft = f["provider_reported_tokens"] or 0
                    st_ = s["provider_reported_tokens"] or 0
                    if ft:
                        out.append((ft - st_) / ft * 100)
        return out

    def wall_red_mean(full, sel):
        out = []
        for case in CASES:
            for rep in REPEAT_IDS:
                f = next((r for r in full if r["case_id"] == case
                          and r["repeat_index"] =="repeat_01" and rep == "repeat_01" and False), None)
        # simpler: use m1 rows directly with their repeat structure
        out = []
        for case in CASES:
            for rep_m3b in REPEAT_IDS:
                f = next((r for r in full if r["case_id"] == case
                          and r["repeat_index"] == rep_m3b), None)
                s = next((r for r in sel if r["case_id"] == case
                          and r["repeat_index"] == rep_m3b), None)
                if (f and s and f["provider_execution_success"]
                        and s["provider_execution_success"]):
                    fw = f["wall_time_ms"] or 0
                    sw = s["wall_time_ms"] or 0
                    if fw:
                        out.append((fw - sw) / fw * 100)
        return out

    m3b_full = sub(m3b_rows, "FULL_RERUN")
    m3b_sel = sub(m3b_rows, "SELECTIVE_RECOVERY")
    m3b_coarse = sub(m3b_rows, "COARSE_CHECKPOINT")
    m1_full = sub(m1_rows, "FULL_RERUN")
    m1_sel = sub(m1_rows, "SELECTIVE_RECOVERY")
    m1_coarse = sub(m1_rows, "COARSE_CHECKPOINT")

    m3b_full_set = sorted({r["new_llm_calls"] for r in m3b_full
                           if r["provider_execution_success"] and r["new_llm_calls"] is not None})
    m3b_sel_set = sorted({r["new_llm_calls"] for r in m3b_sel
                          if r["provider_execution_success"] and r["new_llm_calls"] is not None})
    m1_full_set = sorted({r["new_llm_calls"] for r in m1_full
                         if r["provider_execution_success"] and r["new_llm_calls"] is not None})
    m1_sel_set = sorted({r["new_llm_calls"] for r in m1_sel
                        if r["provider_execution_success"] and r["new_llm_calls"] is not None})

    m3b_calls_red = reduction_mean(m3b_full, m3b_sel)
    m3b_tok_red = tok_red_mean(m3b_full, m3b_sel)
    m3b_wall_red = wall_red_mean(m3b_full, m3b_sel)
    m1_calls_red = reduction_mean(m1_full, m1_sel)
    m1_tok_red = tok_red_mean(m1_full, m1_sel)
    m1_wall_red = wall_red_mean(m1_full, m1_sel)

    return {
        "Qwen_M1_5repeat": {
            "n_trials": len(m1_rows),
            "FULL_calls_set": m1_full_set,
            "SELECTIVE_calls_set": m1_sel_set,
            "FULL_tokens_mean": round(st.mean([
                r["provider_reported_tokens"] or 0
                for r in m1_full if r["provider_execution_success"]
            ]), 1),
            "SELECTIVE_tokens_mean": round(st.mean([
                r["provider_reported_tokens"] or 0
                for r in m1_sel if r["provider_execution_success"]
            ]), 1),
            "FULL_SELECTIVE_calls_reduction_pct": stats_with_ci(m1_calls_red, "calls_reduction_pct"),
            "FULL_SELECTIVE_tokens_reduction_pct": stats_with_ci(m1_tok_red, "tokens_reduction_pct"),
            "FULL_SELECTIVE_wall_reduction_pct": stats_with_ci(m1_wall_red, "wall_reduction_pct"),
        },
        "DeepSeek_M3B_5repeat": {
            "n_trials": len(m3b_rows),
            "FULL_calls_set": m3b_full_set,
            "SELECTIVE_calls_set": m3b_sel_set,
            "FULL_tokens_mean": round(st.mean([
                r["provider_reported_tokens"] or 0
                for r in m3b_full if r["provider_execution_success"]
            ]), 1),
            "SELECTIVE_tokens_mean": round(st.mean([
                r["provider_reported_tokens"] or 0
                for r in m3b_sel if r["provider_execution_success"]
            ]), 1),
            "FULL_SELECTIVE_calls_reduction_pct": stats_with_ci(m3b_calls_red, "calls_reduction_pct"),
            "FULL_SELECTIVE_tokens_reduction_pct": stats_with_ci(m3b_tok_red, "tokens_reduction_pct"),
            "FULL_SELECTIVE_wall_reduction_pct": stats_with_ci(m3b_wall_red, "wall_reduction_pct"),
        },
    }


def main():
    rows = build_rows()
    print(f"loaded {len(rows)} M3 Phase B trial rows")

    # 1. trial matrix
    csv_path = M3 / "m3_phase_b_trial_matrix.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    (M3 / "m3_phase_b_trial_matrix.json").write_text(
        json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"wrote {csv_path}")

    # 2. strategy summary
    per_strat = {s: by_strat_summary(rows, s) for s in STRATS}
    summary = {
        "experiment_id": "EXP-02",
        "stage": "M3 Phase B (5 repeats, real DeepSeek)",
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "base_url": "https://api.deepseek.com/v1",
        "total_trials": len(rows),
        "by_strategy": per_strat,
        "selective_stability": selective_stability(rows),
        "calls_determinism": calls_determinism(rows),
    }
    (M3 / "m3_phase_b_strategy_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # 3. matched-pair
    f2s = pair_reduction(rows, "FULL_RERUN", "SELECTIVE_RECOVERY")
    c2s = pair_reduction(rows, "COARSE_CHECKPOINT", "SELECTIVE_RECOVERY")
    (M3 / "m3_phase_b_matched_pair_analysis.json").write_text(
        json.dumps({
            "experiment_id": "EXP-02",
            "stage": "M3 Phase B matched-pair",
            "full_to_selective_reduction": f2s,
            "coarse_to_selective_reduction": c2s,
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # 4. failure forensic
    (M3 / "m3_phase_b_failure_forensic.json").write_text(
        json.dumps(failure_forensic(rows), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # 5. cross-provider
    m1 = json.loads((M1 / "m1_trial_matrix.json").read_text(encoding="utf-8"))
    (M3 / "m3_phase_b_cross_provider_comparison.json").write_text(
        json.dumps(cross_provider(rows, m1), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # 6. Phase B freeze manifest
    trial_shas = {}
    for r in rows:
        trial_id = r["trial_id"]
        # find the JSON file path
        for rep in REPEAT_IDS:
            cand = REPEATS / rep / f"{trial_id}.json"
            if cand.exists():
                trial_shas[rel := str(cand.relative_to(ROOT))] = (
                    hashlib.sha256(cand.read_bytes()).hexdigest()
                )
                break
    summary_shas = {}
    for f in [
        "m3_phase_b_trial_matrix.csv", "m3_phase_b_trial_matrix.json",
        "m3_phase_b_strategy_summary.json",
        "m3_phase_b_matched_pair_analysis.json",
        "m3_phase_b_failure_forensic.json",
        "m3_phase_b_cross_provider_comparison.json",
    ]:
        p = M3 / f
        if p.exists():
            summary_shas[f] = hashlib.sha256(p.read_bytes()).hexdigest()
    manifest = {
        "manifest_version": 1,
        "scope": "M3 Phase B evidence freeze (DeepSeek, 5 repeats, 60 trials)",
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "frozen_by": "M3 Phase B final analysis",
        "phase_b_output_dir": "experiments/results/exp02/m3_cross_provider/repeats/",
        "phase_b_summary_dir": "experiments/results/exp02/m3_cross_provider/",
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "base_url": "https://api.deepseek.com/v1",
        "trial_count": len(rows),
        "trial_layout": "4 cases x 3 strategies x 5 repeats = 60 trials",
        "trial_id_pattern": "m3b_<CASE>_<strategy>_repeat_NN",
        "trial_json_sha256": trial_shas,
        "summary_json_sha256": summary_shas,
        "provider_adapter_state": {
            "runner_sha16": "fc2dfa73e6a33b58",
            "retry_runner_sha16": "1bf55418fa232426",
            "frozen_at_phase_b_start": True,
            "modified_during_phase_b": False,
        },
        "forensic_only_dir": {
            "path": "experiments/results/exp02/m3_cross_provider/pilot_polluted_pre_provider_adapter_fix/",
            "policy": "FORENSIC_ONLY. Not used in any Phase B mean, SD, CI, matched-pair, cross-provider computation.",
        },
        "phase_a_freeze_manifest": {
            "path": "experiments/results/exp02/m3_cross_provider/pilot/m3_phase_a_freeze_manifest.json",
            "status": "preserved (not modified)",
        },
        "phase_b_gate_pending": "M3_PHASE_B_COMPLETE_READY_FOR_CROSS_PROVIDER_CONCLUSION or M3_PHASE_B_BLOCKED",
    }
    (M3 / "m3_phase_b_freeze_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # stdout
    print()
    print("=== per-strategy (M3 Phase B) ===")
    for s in STRATS:
        p = per_strat[s]
        print(f"  {s:22s} prov={p['provider_success_count']}/{p['total_trials']}  "
              f"valid={p['trial_valid_count']}/{p['total_trials']}  "
              f"calls_set={p['calls']['set']}  tokens_mean={p['provider_reported_tokens']['mean']}")
    print()
    print(f"=== FULL -> SELECTIVE ({f2s['comparable_pair_count']} comparable / {f2s['excluded_pair_count']} excluded) ===")
    c = f2s['calls_reduction_pct']
    print(f"  calls   n={c['n']}  mean={c['mean']}%  95%CI=[{c['ci_95_low']},{c['ci_95_high']}]")
    t = f2s['tokens_reduction_pct']
    print(f"  tokens  n={t['n']}  mean={t['mean']}%  95%CI=[{t['ci_95_low']},{t['ci_95_high']}]")
    w = f2s['wall_reduction_pct']
    print(f"  wall    n={w['n']}  mean={w['mean']}%  95%CI=[{w['ci_95_low']},{w['ci_95_high']}]")
    print()
    cd = summary["calls_determinism"]
    print("=== calls determinism ===")
    print(f"  FULL: {cd['FULL']}")
    print(f"  COARSE: {cd['COARSE']}")
    print(f"  SELECTIVE: {cd['SELECTIVE']}")


if __name__ == "__main__":
    main()
