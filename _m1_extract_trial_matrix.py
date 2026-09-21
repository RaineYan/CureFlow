"""M1 — Trial-level extraction + statistics + matched-pair analysis.

Reads 60 trial JSONs (4 cases x 3 strategies x 5 repeats) from
experiments/results/exp02/m1_5repeat_real/repeat_NN/.  Cross-references
CANONICAL_CASES for fault_type / target_artifact_id.

Writes:
  - m1_trial_matrix.csv
  - m1_trial_matrix.json
  - m1_strategy_summary.json
  - m1_matched_pair_analysis.json
  - m1_failure_forensic.json

Read-only on Stage 1.5 / Stage 2 / M2 / historical evidence.
"""
from __future__ import annotations
import csv
import json
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
M1 = ROOT / "experiments/results/exp02/m1_5repeat_real"
STAGE2 = M1  # write M1 outputs to same dir

from experiments.exp02_faults import CANONICAL_CASES  # type: ignore
CASE_META = {cid: {"scenario_id": sid, "fault_type": ft, "target_artifact_id": tgt}
             for cid, sid, ft, tgt in CANONICAL_CASES}

CASES = ["CASE_A", "CASE_B", "CASE_C", "CASE_D"]
STRATS = ["FULL_RERUN", "COARSE_CHECKPOINT", "SELECTIVE_RECOVERY"]
REPEATS = ["repeat_01", "repeat_02", "repeat_03", "repeat_04", "repeat_05"]


def read_ledger(ledger_path: Path):
    if not ledger_path.exists():
        return None, None, None, None, 0
    first, last = None, None
    n = 0
    first_req, last_req = None, None
    with ledger_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            n += 1
            if first is None:
                first = rec
                first_req = rec.get("request_id")
            last = rec
            last_req = rec.get("request_id")
    return first_req, last_req, first, last, n


def build_trial_rows():
    rows = []
    for case in CASES:
        for strat in STRATS:
            for rep in REPEATS:
                trial_id = f"stage15_{case}_{strat.lower()}_{rep}"
                trial_path = M1 / rep / f"{trial_id}.json"
                if not trial_path.exists():
                    continue
                t = json.loads(trial_path.read_text(encoding="utf-8"))
                meta = CASE_META.get(case, {})
                first_req, last_req, _, _, ledger_n = read_ledger(
                    M1 / rep / f"{trial_id}_live_ledger.jsonl"
                )
                # extract provider envelope detection from error_message
                env_kind = None
                if t.get("infrastructure_failure_subtype", "").startswith("INFRA_FAILURE_"):
                    env_kind = t["infrastructure_failure_subtype"].replace("INFRA_FAILURE_", "")
                rows.append({
                    "trial_id": trial_id,
                    "case_id": t.get("case_id"),
                    "scenario_id": t.get("scenario_id") or meta.get("scenario_id", "N/A"),
                    "fault_type": meta.get("fault_type", "N/A"),
                    "target_artifact_id": meta.get("target_artifact_id", "N/A"),
                    "strategy": t.get("strategy"),
                    "repeat_index": rep,
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
                    "exception_type": t.get("error_type", "N/A"),
                    "envelope_kind": env_kind,
                    "first_request_id": first_req or "N/A",
                    "last_request_id": last_req or "N/A",
                    "ledger_call_count": ledger_n,
                    "retry_attempt": t.get("retry_attempt", 0),
                    "retry_outcome": t.get("retry_outcome", "NOT_RETRIED"),
                })
    return rows


def by_strat_summary(rows, strat):
    sub = [r for r in rows if r["strategy"] == strat]
    ok = [r for r in sub if r["provider_execution_success"]]
    valid = [r for r in sub if r["trial_valid"]]
    n = len(sub)
    ok_calls = [r["new_llm_calls"] for r in ok if r["new_llm_calls"] is not None]
    ok_tokens = [r["provider_reported_tokens"] for r in ok if r["provider_reported_tokens"] is not None]
    ok_walls = [r["wall_time_ms"] for r in ok if r["wall_time_ms"] is not None]
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
        "reused_artifacts_avg": "N/A",
    }


def pair_reduction(rows, from_strat, to_strat):
    """Match case × repeat pairs.  Only include when both endpoints
    have provider_execution_success=True."""
    pair_rows = []
    for case in CASES:
        for rep in REPEATS:
            a = next((r for r in rows
                      if r["case_id"] == case and r["strategy"] == from_strat
                      and r["repeat_index"] == rep), None)
            b = next((r for r in rows
                      if r["case_id"] == case and r["strategy"] == to_strat
                      and r["repeat_index"] == rep), None)
            if not a or not b:
                pair_rows.append({"case_id": case, "repeat_index": rep, "status": "MISSING"})
                continue
            if not a["provider_execution_success"]:
                pair_rows.append({
                    "case_id": case, "repeat_index": rep,
                    "status": "EXCLUDED",
                    "exclusion_reason": f"{from_strat} provider_execution_success=False",
                    f"{from_strat}_calls": a["new_llm_calls"],
                    f"{from_strat}_tokens": a["provider_reported_tokens"],
                    f"{to_strat}_calls": b["new_llm_calls"],
                    f"{to_strat}_tokens": b["provider_reported_tokens"],
                })
                continue
            if not b["provider_execution_success"]:
                pair_rows.append({
                    "case_id": case, "repeat_index": rep,
                    "status": "EXCLUDED",
                    "exclusion_reason": f"{to_strat} provider_execution_success=False",
                    f"{from_strat}_calls": a["new_llm_calls"],
                    f"{from_strat}_tokens": a["provider_reported_tokens"],
                    f"{to_strat}_calls": b["new_llm_calls"],
                    f"{to_strat}_tokens": b["provider_reported_tokens"],
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

    def stats(values):
        if not values:
            return None
        n = len(values)
        m = st.mean(values)
        med = st.median(values)
        sd = st.pstdev(values) if n > 1 else 0
        # 95% CI for the mean using t-distribution (large-n approximation
        # when n>=30: t=1.96).  For small n, we use the exact t value from
        # a hard-coded table (scipy-free).  When n<2, CI is N/A.
        if n < 2:
            ci_low = ci_high = None
            t_crit = None
            method = "N/A (n<2)"
        elif n >= 30:
            t_crit = 1.96
            se = sd / (n ** 0.5)
            ci_low = m - t_crit * se
            ci_high = m + t_crit * se
            method = "z-based 95% CI (n>=30)"
        else:
            # small-sample t critical values (two-tailed, alpha=0.05)
            t_table = {2: 12.706, 3: 4.303, 4: 3.182, 5: 2.776,
                       6: 2.571, 7: 2.447, 8: 2.365, 9: 2.306,
                       10: 2.262, 11: 2.228, 12: 2.201, 13: 2.179,
                       14: 2.160, 15: 2.145, 16: 2.131, 17: 2.120,
                       18: 2.110, 19: 2.101, 20: 2.093,
                       21: 2.086, 22: 2.080, 23: 2.074, 24: 2.069,
                       25: 2.064, 26: 2.060, 27: 2.056, 28: 2.052,
                       29: 2.048, 30: 2.045}
            t_crit = t_table.get(n, 2.045)  # fallback to n=30
            se = sd / (n ** 0.5)
            ci_low = m - t_crit * se
            ci_high = m + t_crit * se
            method = "t-based 95% CI (two-tailed, alpha=0.05)"
        return {
            "n": n, "mean": round(m, 3), "median": round(med, 3),
            "sd": round(sd, 3), "min": round(min(values), 3),
            "max": round(max(values), 3),
            "p25": round(sorted(values)[max(0, n // 4 - 1)], 3) if n >= 4 else None,
            "p75": round(sorted(values)[min(n - 1, 3 * n // 4)], 3) if n >= 4 else None,
            "ci_95_low": round(ci_low, 3) if ci_low is not None else None,
            "ci_95_high": round(ci_high, 3) if ci_high is not None else None,
            "ci_method": method,
            "t_critical": t_crit,
        }

    calls_vals = [p["calls_reduction_pct"] for p in comparable]
    tokens_vals = [p["tokens_reduction_pct"] for p in comparable]
    wall_vals = [p["wall_reduction_pct"] for p in comparable]

    # Per-case breakdown
    per_case = {}
    for case in CASES:
        case_pairs = [p for p in comparable if p["case_id"] == case]
        per_case[case] = {
            "n_comparable": len(case_pairs),
            "calls_reduction_mean_pct": round(st.mean([p["calls_reduction_pct"] for p in case_pairs]), 3) if case_pairs else None,
            "tokens_reduction_mean_pct": round(st.mean([p["tokens_reduction_pct"] for p in case_pairs]), 3) if case_pairs else None,
            "wall_reduction_mean_pct": round(st.mean([p["wall_reduction_pct"] for p in case_pairs]), 3) if case_pairs else None,
        }

    return {
        "from_strategy": from_strat,
        "to_strategy": to_strat,
        "comparable_pair_count": len(comparable),
        "excluded_pair_count": len(excluded),
        "excluded_pair_details": excluded,
        "calls_reduction_pct": stats(calls_vals),
        "tokens_reduction_pct": stats(tokens_vals),
        "wall_reduction_pct": stats(wall_vals),
        "per_case_breakdown": per_case,
        "per_pair_details": pair_rows,
    }


def selective_stability(rows):
    sub = [r for r in rows if r["strategy"] == "SELECTIVE_RECOVERY"]
    return {
        "trials": [
            {
                "trial_id": r["trial_id"],
                "case_id": r["case_id"],
                "repeat_index": r["repeat_index"],
                "provider_execution_success": r["provider_execution_success"],
                "trial_valid": r["trial_valid"],
                "recovery_success": r["recovery_success"],
                "semantic_validation_success": r["semantic_validation_success"],
                "golden_match": r["golden_match"],
                "new_llm_calls": r["new_llm_calls"],
                "provider_reported_tokens": r["provider_reported_tokens"],
                "wall_time_ms": r["wall_time_ms"],
            } for r in sub
        ],
        "consistency": {
            "all_20_provider_success": all(r["provider_execution_success"] for r in sub),
            "all_20_trial_valid": all(r["trial_valid"] for r in sub),
            "all_20_recovery_success": all(r["recovery_success"] for r in sub),
            "all_20_semantic_success": all(r["semantic_validation_success"] for r in sub),
            "all_20_same_call_count": len({r["new_llm_calls"] for r in sub}) == 1,
            "calls_set": sorted({r["new_llm_calls"] for r in sub}),
            "tokens_range": [
                min(r["provider_reported_tokens"] for r in sub),
                max(r["provider_reported_tokens"] for r in sub),
            ],
            "wall_range_ms": [
                min(r["wall_time_ms"] for r in sub),
                max(r["wall_time_ms"] for r in sub),
            ],
        },
    }


def calls_determinism(rows):
    full = [r for r in rows if r["strategy"] == "FULL_RERUN"]
    sel = [r for r in rows if r["strategy"] == "SELECTIVE_RECOVERY"]
    full_calls = [r["new_llm_calls"] for r in full if r["new_llm_calls"] is not None]
    sel_calls = [r["new_llm_calls"] for r in sel if r["new_llm_calls"] is not None]
    full_set = sorted(set(full_calls))
    sel_set = sorted(set(sel_calls))
    return {
        "FULL": {
            "n": len(full_calls),
            "calls_set": full_set,
            "exact_6_count": sum(1 for c in full_calls if c == 6),
            "deviations": [
                {"trial_id": r["trial_id"], "calls": r["new_llm_calls"]}
                for r in full if r["new_llm_calls"] != 6
            ],
        },
        "SELECTIVE": {
            "n": len(sel_calls),
            "calls_set": sel_set,
            "exact_2_count": sum(1 for c in sel_calls if c == 2),
            "deviations": [
                {"trial_id": r["trial_id"], "calls": r["new_llm_calls"]}
                for r in sel if r["new_llm_calls"] != 2
            ],
        },
    }


def failure_forensic(rows):
    infra_failure = [r for r in rows if r["provider_execution_success"] is False]
    runner_crash = []  # no crash since runner completed all 60
    experiment_outcome = [r for r in rows
                         if r["provider_execution_success"] is True
                         and r["trial_valid"] is False]
    # retry stats
    retried = [r for r in rows if r["retry_attempt"] > 0]
    return {
        "infra_failure": {
            "count": len(infra_failure),
            "trials": [
                {
                    "trial_id": r["trial_id"],
                    "case_id": r["case_id"],
                    "strategy": r["strategy"],
                    "repeat_index": r["repeat_index"],
                    "failure_subtype": r["failure_subtype"],
                    "envelope_kind": r["envelope_kind"],
                    "failure_reason_excerpt": (r["failure_reason"] or "")[:160],
                    "retry_attempt": r["retry_attempt"],
                    "retry_outcome": r["retry_outcome"],
                } for r in infra_failure
            ],
        },
        "experiment_outcome": {
            "count": len(experiment_outcome),
            "description": (
                "provider_success=True but trial_valid=False: real Qwen produced "
                "valid claim schema but the semantic-validation gate (RMP / ARR / "
                "unaffected_recomputed_count) did not pass on this real Provider output."
            ),
            "trial_ids": [r["trial_id"] for r in experiment_outcome],
        },
        "runner_implementation_error": {
            "count": len(runner_crash),
            "trials": [],
        },
        "retry_stats": {
            "total_retried": len(retried),
            "now_ok_after_retry": sum(1 for r in retried if r["retry_outcome"] == "NOW_OK"),
            "still_crashed_after_retry": sum(1 for r in retried if r["retry_outcome"] == "STILL_CRASHED"),
            "trial_ids": [r["trial_id"] for r in retried],
        },
    }


def main():
    rows = build_trial_rows()
    print(f"loaded {len(rows)} trial rows")

    # 1. trial matrix csv + json
    csv_path = STAGE2 / "m1_trial_matrix.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {csv_path}")

    json_path = STAGE2 / "m1_trial_matrix.json"
    json_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {json_path}")

    # 2. strategy summary
    per_strat = {s: by_strat_summary(rows, s) for s in STRATS}
    summary = {
        "experiment_id": "EXP-02",
        "stage": "M1 5-repeat real Qwen",
        "total_trials": len(rows),
        "by_strategy": per_strat,
        "selective_stability": selective_stability(rows),
        "calls_determinism": calls_determinism(rows),
    }
    summary_path = STAGE2 / "m1_strategy_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {summary_path}")

    # 3. matched-pair analysis
    full_to_sel = pair_reduction(rows, "FULL_RERUN", "SELECTIVE_RECOVERY")
    coarse_to_sel = pair_reduction(rows, "COARSE_CHECKPOINT", "SELECTIVE_RECOVERY")
    pair_analysis = {
        "experiment_id": "EXP-02",
        "stage": "M1 5-repeat matched-pair analysis",
        "full_to_selective_reduction": full_to_sel,
        "coarse_to_selective_reduction": coarse_to_sel,
    }
    pair_path = STAGE2 / "m1_matched_pair_analysis.json"
    pair_path.write_text(json.dumps(pair_analysis, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {pair_path}")

    # 4. failure forensic
    forensic = failure_forensic(rows)
    forensic_path = STAGE2 / "m1_failure_forensic.json"
    forensic_path.write_text(json.dumps(forensic, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {forensic_path}")

    # quick stdout
    print()
    print("=== per-strategy provider/valid rates ===")
    for s in STRATS:
        p = per_strat[s]
        print(f"  {s:22s} prov={p['provider_success_count']}/{p['total_trials']}  "
              f"valid={p['trial_valid_count']}/{p['total_trials']}  "
              f"calls_mean={p['calls']['mean']}  tokens_mean={p['provider_reported_tokens']['mean']}")
    print()
    print("=== FULL -> SELECTIVE matched-pair ===")
    f2s = full_to_sel
    print(f"  comparable: {f2s['comparable_pair_count']}, excluded: {f2s['excluded_pair_count']}")
    if f2s['calls_reduction_pct']:
        c = f2s['calls_reduction_pct']
        print(f"  calls   mean={c['mean']}%  SD={c['sd']}  95%CI=[{c['ci_95_low']},{c['ci_95_high']}]")
    if f2s['tokens_reduction_pct']:
        t = f2s['tokens_reduction_pct']
        print(f"  tokens  mean={t['mean']}%  SD={t['sd']}  95%CI=[{t['ci_95_low']},{t['ci_95_high']}]")
    if f2s['wall_reduction_pct']:
        w = f2s['wall_reduction_pct']
        print(f"  wall    mean={w['mean']}%  SD={w['sd']}  95%CI=[{w['ci_95_low']},{w['ci_95_high']}]")
    print()
    print("=== calls determinism ===")
    cd = summary["calls_determinism"]
    print(f"  FULL ({cd['FULL']['n']}): set={cd['FULL']['calls_set']}, exact_6={cd['FULL']['exact_6_count']}, deviations={len(cd['FULL']['deviations'])}")
    print(f"  SELECTIVE ({cd['SELECTIVE']['n']}): set={cd['SELECTIVE']['calls_set']}, exact_2={cd['SELECTIVE']['exact_2_count']}, deviations={len(cd['SELECTIVE']['deviations'])}")
    print()
    print("=== SELECTIVE 20-trial stability ===")
    sc = summary["selective_stability"]["consistency"]
    for k, v in sc.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
