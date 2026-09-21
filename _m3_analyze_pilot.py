"""M3 Phase A analysis + matched-pair + cross-provider comparison.

Reads M3 pilot 12 trial JSONs.  Computes by-strategy aggregates,
matched-pair reductions (FULL→SELECTIVE, COARSE→SELECTIVE), and
cross-provider comparison vs M1 (Qwen 60 trials).

Writes to m3_cross_provider/pilot/:
  - m3_trial_matrix.csv / .json
  - m3_strategy_summary.json
  - m3_matched_pair_analysis.json
  - m3_failure_forensic.json
  - m3_cross_provider_comparison.json

Does NOT modify Stage 1.5 / M1 / Stage 2 / M2 evidence.
"""
from __future__ import annotations
import csv
import json
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
M3 = ROOT / "experiments/results/exp02/m3_cross_provider"
M1 = ROOT / "experiments/results/exp02/m1_5repeat_real"
PILOT = M3 / "pilot"

from experiments.exp02_faults import CANONICAL_CASES  # type: ignore
CASE_META = {cid: {"scenario_id": sid, "fault_type": ft, "target_artifact_id": tgt}
             for cid, sid, ft, tgt in CANONICAL_CASES}

CASES = ["CASE_A", "CASE_B", "CASE_C", "CASE_D"]
STRATS = ["FULL_RERUN", "COARSE_CHECKPOINT", "SELECTIVE_RECOVERY"]


def build_trial_rows():
    rows = []
    for case in CASES:
        for strat in STRATS:
            trial_id = f"m3_{case}_{strat.lower()}_repeat_01"
            trial_path = PILOT / f"{trial_id}.json"
            if not trial_path.exists():
                continue
            t = json.loads(trial_path.read_text(encoding="utf-8"))
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
                "provider": "deepseek",  # override; trial JSON provider field hardcoded
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
        "calls": {
            "set": sorted(set(ok_calls)) if ok_calls else [],
            "min": min(ok_calls) if ok_calls else None,
            "max": max(ok_calls) if ok_calls else None,
        },
        "provider_reported_tokens": {
            "min": min(ok_tokens) if ok_tokens else None,
            "max": max(ok_tokens) if ok_tokens else None,
            "mean": round(st.mean(ok_tokens), 1) if ok_tokens else None,
        },
        "wall_time_ms": {
            "min": min(ok_walls) if ok_walls else None,
            "max": max(ok_walls) if ok_walls else None,
            "mean": round(st.mean(ok_walls), 1) if ok_walls else None,
        },
    }


def pair_reduction(rows, from_strat, to_strat):
    pair_rows = []
    for case in CASES:
        a = next((r for r in rows
                  if r["case_id"] == case and r["strategy"] == from_strat), None)
        b = next((r for r in rows
                  if r["case_id"] == case and r["strategy"] == to_strat), None)
        if not a or not b:
            pair_rows.append({"case_id": case, "status": "MISSING"})
            continue
        if not a["provider_execution_success"]:
            pair_rows.append({
                "case_id": case, "status": "EXCLUDED",
                "exclusion_reason": f"{from_strat} provider_execution_success=False",
            })
            continue
        if not b["provider_execution_success"]:
            pair_rows.append({
                "case_id": case, "status": "EXCLUDED",
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
            "case_id": case, "status": "COMPARABLE",
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
        return {
            "n": len(values),
            "mean": round(st.mean(values), 2),
            "median": round(st.median(values), 2),
            "sd": round(st.pstdev(values), 2) if len(values) > 1 else 0,
            "min": round(min(values), 2),
            "max": round(max(values), 2),
        }
    return {
        "from_strategy": from_strat,
        "to_strategy": to_strat,
        "comparable_pair_count": len(comparable),
        "excluded_pair_count": len(excluded),
        "excluded_pair_details": excluded,
        "calls_reduction_pct": stats([p["calls_reduction_pct"] for p in comparable]),
        "tokens_reduction_pct": stats([p["tokens_reduction_pct"] for p in comparable]),
        "wall_reduction_pct": stats([p["wall_reduction_pct"] for p in comparable]),
        "per_pair_details": pair_rows,
    }


def selective_stability(rows):
    sub = [r for r in rows if r["strategy"] == "SELECTIVE_RECOVERY"]
    return {
        "trials": [
            {k: r[k] for k in (
                "trial_id", "case_id", "provider_execution_success",
                "trial_valid", "recovery_success", "semantic_validation_success",
                "golden_match", "new_llm_calls", "provider_reported_tokens",
                "wall_time_ms")}
            for r in sub
        ],
        "consistency": {
            "all_4_provider_success": all(r["provider_execution_success"] for r in sub),
            "all_4_trial_valid": all(r["trial_valid"] for r in sub),
            "all_4_recovery_success": all(r["recovery_success"] for r in sub),
            "all_4_semantic_success": all(r["semantic_validation_success"] for r in sub),
            "all_4_same_call_count": len({r["new_llm_calls"] for r in sub if r["new_llm_calls"] is not None}) <= 1,
            "calls_set": sorted({r["new_llm_calls"] for r in sub if r["new_llm_calls"] is not None}),
        },
    }


def calls_determinism(rows):
    full = [r for r in rows if r["strategy"] == "FULL_RERUN"]
    sel = [r for r in rows if r["strategy"] == "SELECTIVE_RECOVERY"]
    full_calls = [r["new_llm_calls"] for r in full if r["new_llm_calls"] is not None]
    sel_calls = [r["new_llm_calls"] for r in sel if r["new_llm_calls"] is not None]
    return {
        "FULL": {
            "n": len(full_calls),
            "calls_set": sorted(set(full_calls)),
            "exact_6_count": sum(1 for c in full_calls if c == 6),
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
    return {
        "infra_failure": {
            "count": len(infra),
            "trials": [
                {
                    "trial_id": r["trial_id"],
                    "case_id": r["case_id"],
                    "strategy": r["strategy"],
                    "envelope_kind": r["envelope_kind"],
                    "failure_reason_excerpt": (r["failure_reason"] or "")[:200],
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
            "total_retried": sum(1 for r in rows if r["retry_attempt"] > 0),
            "now_ok_after_retry": sum(
                1 for r in rows
                if r["retry_attempt"] > 0 and r["retry_outcome"] == "NOW_OK"
            ),
            "still_crashed_after_retry": sum(
                1 for r in rows
                if r["retry_attempt"] > 0 and r["retry_outcome"] == "STILL_CRASHED"
            ),
            "trial_ids": [r["trial_id"] for r in rows if r["retry_attempt"] > 0],
        },
    }


def load_m1_full5():
    """Load M1 FULL_RERUN 20 calls data for cross-provider comparison."""
    rows = json.loads((M1 / "m1_trial_matrix.json").read_text(encoding="utf-8"))
    return rows


def cross_provider_comparison(m3_rows, m1_rows):
    m3_full = [r for r in m3_rows if r["strategy"] == "FULL_RERUN"]
    m3_sel = [r for r in m3_rows if r["strategy"] == "SELECTIVE_RECOVERY"]
    m3_coarse = [r for r in m3_rows if r["strategy"] == "COARSE_CHECKPOINT"]
    m1_full = [r for r in m1_rows if r["strategy"] == "FULL_RERUN"]
    m1_sel = [r for r in m1_rows if r["strategy"] == "SELECTIVE_RECOVERY"]

    def calls_reduction_mean(full, sel):
        # paired by case
        out = []
        for case in CASES:
            f = next((r for r in full if r["case_id"] == case), None)
            s = next((r for r in sel if r["case_id"] == case), None)
            if f and s and f["provider_execution_success"] and s["provider_execution_success"]:
                fc = f.get("new_llm_calls") or 0
                sc = s.get("new_llm_calls") or 0
                if fc:
                    out.append((fc - sc) / fc * 100)
        return round(st.mean(out), 2) if out else None

    def tokens_reduction_mean(full, sel):
        out = []
        for case in CASES:
            f = next((r for r in full if r["case_id"] == case), None)
            s = next((r for r in sel if r["case_id"] == case), None)
            if f and s and f["provider_execution_success"] and s["provider_execution_success"]:
                ft = f.get("provider_reported_tokens") or 0
                st_ = s.get("provider_reported_tokens") or 0
                if ft:
                    out.append((ft - st_) / ft * 100)
        return round(st.mean(out), 2) if out else None

    def wall_reduction_mean(full, sel):
        out = []
        for case in CASES:
            f = next((r for r in full if r["case_id"] == case), None)
            s = next((r for r in sel if r["case_id"] == case), None)
            if f and s and f["provider_execution_success"] and s["provider_execution_success"]:
                fw = f.get("wall_time_ms") or 0
                sw = s.get("wall_time_ms") or 0
                if fw:
                    out.append((fw - sw) / fw * 100)
        return round(st.mean(out), 2) if out else None

    return {
        "Qwen_M1_5repeat": {
            "n_trials": len(m1_rows),
            "FULL_calls_set": sorted({r.get("new_llm_calls") for r in m1_full
                                     if r.get("provider_execution_success")}),
            "SELECTIVE_calls_set": sorted({r.get("new_llm_calls") for r in m1_sel
                                          if r.get("provider_execution_success")}),
            "FULL_tokens_mean": round(st.mean([
                r.get("provider_reported_tokens") or 0
                for r in m1_full if r.get("provider_execution_success")
            ]), 1),
            "SELECTIVE_tokens_mean": round(st.mean([
                r.get("provider_reported_tokens") or 0
                for r in m1_sel if r.get("provider_execution_success")
            ]), 1),
            "FULL_SELECTIVE_calls_reduction_pct": calls_reduction_mean(m1_full, m1_sel),
            "FULL_SELECTIVE_tokens_reduction_pct": tokens_reduction_mean(m1_full, m1_sel),
            "FULL_SELECTIVE_wall_reduction_pct": wall_reduction_mean(m1_full, m1_sel),
        },
        "DeepSeek_M3_pilot": {
            "n_trials": len(m3_rows),
            "FULL_calls_set": sorted({r.get("new_llm_calls") for r in m3_full
                                     if r.get("provider_execution_success")}),
            "SELECTIVE_calls_set": sorted({r.get("new_llm_calls") for r in m3_sel
                                          if r.get("provider_execution_success")}),
            "FULL_tokens_mean": round(st.mean([
                r.get("provider_reported_tokens") or 0
                for r in m3_full if r.get("provider_execution_success")
            ]), 1),
            "SELECTIVE_tokens_mean": round(st.mean([
                r.get("provider_reported_tokens") or 0
                for r in m3_sel if r.get("provider_execution_success")
            ]), 1),
            "FULL_SELECTIVE_calls_reduction_pct": calls_reduction_mean(m3_full, m3_sel),
            "FULL_SELECTIVE_tokens_reduction_pct": tokens_reduction_mean(m3_full, m3_sel),
            "FULL_SELECTIVE_wall_reduction_pct": wall_reduction_mean(m3_full, m3_sel),
        },
    }


def main():
    rows = build_trial_rows()
    print(f"loaded {len(rows)} M3 trial rows")

    # 1. trial matrix
    csv_path = PILOT / "m3_trial_matrix.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    (PILOT / "m3_trial_matrix.json").write_text(
        json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"wrote {csv_path}")

    # 2. strategy summary
    per_strat = {s: by_strat_summary(rows, s) for s in STRATS}
    summary = {
        "experiment_id": "EXP-02",
        "stage": "M3 Phase A Pilot (1 repeat, real DeepSeek)",
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "base_url": "https://api.deepseek.com/v1",
        "total_trials": len(rows),
        "by_strategy": per_strat,
        "selective_stability": selective_stability(rows),
        "calls_determinism": calls_determinism(rows),
    }
    (PILOT / "m3_strategy_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # 3. matched-pair
    f2s = pair_reduction(rows, "FULL_RERUN", "SELECTIVE_RECOVERY")
    c2s = pair_reduction(rows, "COARSE_CHECKPOINT", "SELECTIVE_RECOVERY")
    (PILOT / "m3_matched_pair_analysis.json").write_text(
        json.dumps({
            "experiment_id": "EXP-02",
            "stage": "M3 matched-pair analysis",
            "full_to_selective_reduction": f2s,
            "coarse_to_selective_reduction": c2s,
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # 4. failure forensic
    (PILOT / "m3_failure_forensic.json").write_text(
        json.dumps(failure_forensic(rows), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # 5. cross-provider comparison
    m1_rows = load_m1_full5()
    (PILOT / "m3_cross_provider_comparison.json").write_text(
        json.dumps(cross_provider_comparison(rows, m1_rows), indent=2,
                  ensure_ascii=False),
        encoding="utf-8",
    )

    # stdout summary
    print()
    print("=== per-strategy (M3) ===")
    for s in STRATS:
        p = per_strat[s]
        print(f"  {s:22s} prov={p['provider_success_count']}/{p['total_trials']}  "
              f"valid={p['trial_valid_count']}/{p['total_trials']}  "
              f"calls_set={p['calls']['set']}  tokens_mean={p['provider_reported_tokens']['mean']}")
    print()
    print(f"=== FULL -> SELECTIVE ===")
    print(f"  comparable: {f2s['comparable_pair_count']}, excluded: {f2s['excluded_pair_count']}")
    if f2s['calls_reduction_pct']:
        c = f2s['calls_reduction_pct']
        print(f"  calls mean={c['mean']}% (min={c['min']} max={c['max']})")
    if f2s['tokens_reduction_pct']:
        t = f2s['tokens_reduction_pct']
        print(f"  tokens mean={t['mean']}% (min={t['min']} max={t['max']})")
    if f2s['wall_reduction_pct']:
        w = f2s['wall_reduction_pct']
        print(f"  wall mean={w['mean']}% (min={w['min']} max={w['max']})")
    print()
    print("=== SELECTIVE 4-case stability ===")
    for k, v in summary["selective_stability"]["consistency"].items():
        print(f"  {k}: {v}")
    print()
    print("=== calls determinism ===")
    cd = summary["calls_determinism"]
    print(f"  FULL: {cd['FULL']}")
    print(f"  SELECTIVE: {cd['SELECTIVE']}")


if __name__ == "__main__":
    main()
