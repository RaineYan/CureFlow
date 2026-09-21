"""Stage 2 — Strategy evaluation + matched-pair reduction.

Reads stage2_trial_matrix.json (built by _stage2_extract_trial_matrix.py)
and computes per-strategy aggregates + matched-pair reduction.

Writes:
  - stage2_strategy_summary.json

Reduction is computed ONLY when both endpoints in a pair have
provider_execution_success=True (i.e. both completed Provider execution).
Excluded pairs are reported explicitly with exclusion reason.
"""
import json
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

STAGE2 = ROOT / "experiments/results/exp02/stage2_evaluation"
matrix = json.loads((STAGE2 / "stage2_trial_matrix.json").read_text(encoding="utf-8"))

CASE_ORDER = ["CASE_A", "CASE_B", "CASE_C", "CASE_D"]
STRAT_ORDER = ["FULL_RERUN", "COARSE_CHECKPOINT", "SELECTIVE_RECOVERY"]


def per_strat_summary(rows, strat):
    sub = [r for r in rows if r["strategy"] == strat]
    ok = [r for r in sub if r["provider_execution_success"]]
    valid = [r for r in sub if r["trial_valid"]]
    calls_ok = [r["new_llm_calls"] for r in ok if r["new_llm_calls"] is not None]
    tokens_ok = [r["provider_reported_tokens"] for r in ok if r["provider_reported_tokens"] is not None]
    walls = [r["wall_time_ms"] for r in sub if r["wall_time_ms"] is not None]
    summary = {
        "strategy": strat,
        "trials_total": len(sub),
        "provider_execution_success_count": len(ok),
        "trial_valid_count": len(valid),
        "calls": {
            "avg_over_4": (sum(r["new_llm_calls"] or 0 for r in sub) / 4) if sub else None,
            "avg_over_ok": (sum(calls_ok) / len(calls_ok)) if calls_ok else None,
            "min": min((r["new_llm_calls"] or 0) for r in sub) if sub else None,
            "max": max((r["new_llm_calls"] or 0) for r in sub) if sub else None,
        },
        "provider_reported_tokens": {
            "total_over_4": sum(r["provider_reported_tokens"] or 0 for r in sub),
            "total_over_ok": sum(tokens_ok),
            "avg_over_4": (sum(r["provider_reported_tokens"] or 0 for r in sub) / 4) if sub else None,
            "avg_over_ok": (sum(tokens_ok) / len(tokens_ok)) if tokens_ok else None,
        },
        "wall_time_ms": {
            "avg_over_4": (sum(walls) / 4) if walls else None,
            "min": min(walls) if walls else None,
            "max": max(walls) if walls else None,
        },
        # reuse: not extracted (not in trial JSON) -> N/A
        "reused_artifacts_avg": "N/A",
    }
    return summary


def pair_reduction(rows, from_strat, to_strat):
    """Compute reduction for each case. Only include pair if both endpoints OK."""
    pair_rows = []
    for case in CASE_ORDER:
        a = next((r for r in rows if r["case_id"] == case and r["strategy"] == from_strat), None)
        b = next((r for r in rows if r["case_id"] == case and r["strategy"] == to_strat), None)
        if not a or not b:
            pair_rows.append({"case_id": case, "status": "MISSING_TRIAL"})
            continue
        if not a["provider_execution_success"]:
            pair_rows.append({
                "case_id": case,
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
                "case_id": case,
                "status": "EXCLUDED",
                "exclusion_reason": f"{to_strat} provider_execution_success=False",
                f"{from_strat}_calls": a["new_llm_calls"],
                f"{from_strat}_tokens": a["provider_reported_tokens"],
                f"{to_strat}_calls": b["new_llm_calls"],
                f"{to_strat}_tokens": b["provider_reported_tokens"],
            })
            continue
        # both OK -> compute reduction
        a_calls = a["new_llm_calls"] or 0
        b_calls = b["new_llm_calls"] or 0
        a_tok = a["provider_reported_tokens"] or 0
        b_tok = b["provider_reported_tokens"] or 0
        a_wall = a["wall_time_ms"] or 0
        b_wall = b["wall_time_ms"] or 0
        pair_rows.append({
            "case_id": case,
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
    if comparable:
        avg_calls = st.mean([p["calls_reduction_pct"] for p in comparable])
        avg_tok = st.mean([p["tokens_reduction_pct"] for p in comparable])
        avg_wall = st.mean([p["wall_reduction_pct"] for p in comparable])
    else:
        avg_calls = avg_tok = avg_wall = None
    return {
        "from_strategy": from_strat,
        "to_strategy": to_strat,
        "comparable_pair_count": len(comparable),
        "excluded_pair_count": len(excluded),
        "excluded_pair_details": excluded,
        "per_case": pair_rows,
        "avg_calls_reduction_pct": round(avg_calls, 2) if avg_calls is not None else "N/A",
        "avg_tokens_reduction_pct": round(avg_tok, 2) if avg_tok is not None else "N/A",
        "avg_wall_reduction_pct": round(avg_wall, 2) if avg_wall is not None else "N/A",
    }


# Build summary
per_strat = {s: per_strat_summary(matrix, s) for s in STRAT_ORDER}
full_to_sel = pair_reduction(matrix, "FULL_RERUN", "SELECTIVE_RECOVERY")
coarse_to_sel = pair_reduction(matrix, "COARSE_CHECKPOINT", "SELECTIVE_RECOVERY")

# SELECTIVE 4/4 stability
sel_rows = [r for r in matrix if r["strategy"] == "SELECTIVE_RECOVERY"]
sel_stability = {
    "trials": [
        {
            "case_id": r["case_id"],
            "provider_execution_success": r["provider_execution_success"],
            "trial_valid": r["trial_valid"],
            "recovery_success": r["recovery_success"],
            "semantic_validation_success": r["semantic_validation_success"],
            "golden_match": r["golden_match"],
            "new_llm_calls": r["new_llm_calls"],
            "provider_reported_tokens": r["provider_reported_tokens"],
            "wall_time_ms": r["wall_time_ms"],
            "failure_class": r["failure_class"],
            "failure_subtype": r["failure_subtype"],
        }
        for r in sel_rows
    ],
    "consistency": {
        "all_4_provider_success": all(r["provider_execution_success"] for r in sel_rows),
        "all_4_trial_valid": all(r["trial_valid"] for r in sel_rows),
        "all_4_recovery_success": all(r["recovery_success"] for r in sel_rows),
        "all_4_semantic_success": all(r["semantic_validation_success"] for r in sel_rows),
        "all_4_same_call_count": len({r["new_llm_calls"] for r in sel_rows}) == 1,
        "calls_set": sorted({r["new_llm_calls"] for r in sel_rows}),
        "tokens_range": [min(r["provider_reported_tokens"] for r in sel_rows),
                         max(r["provider_reported_tokens"] for r in sel_rows)],
        "wall_range_ms": [min(r["wall_time_ms"] for r in sel_rows),
                          max(r["wall_time_ms"] for r in sel_rows)],
    },
}

summary = {
    "experiment_id": "EXP-02",
    "stage": "Stage 2 Evaluation",
    "input": "stage2_trial_matrix.json (extracted from stage15_real_run2/)",
    "by_strategy": per_strat,
    "full_to_selective_reduction": full_to_sel,
    "coarse_to_selective_reduction": coarse_to_sel,
    "selective_stability": sel_stability,
}

out = STAGE2 / "stage2_strategy_summary.json"
out.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"summary written: {out}")
print()
print("=== by_strategy (provider/trial_valid counts) ===")
for s in STRAT_ORDER:
    p = per_strat[s]
    print(f"  {s:22s} prov={p['provider_execution_success_count']}/4  valid={p['trial_valid_count']}/4"
          f"  avg_calls_over4={p['calls']['avg_over_4']}  total_tokens={p['provider_reported_tokens']['total_over_4']}")
print()
print("=== FULL -> SELECTIVE ===")
print(f"  comparable={full_to_sel['comparable_pair_count']}, excluded={full_to_sel['excluded_pair_count']}")
print(f"  avg calls reduction:    {full_to_sel['avg_calls_reduction_pct']}%")
print(f"  avg tokens reduction:   {full_to_sel['avg_tokens_reduction_pct']}%")
print(f"  avg wall reduction:     {full_to_sel['avg_wall_reduction_pct']}%")
for p in full_to_sel["per_case"]:
    if p["status"] == "COMPARABLE":
        print(f"  {p['case_id']}: calls {p['FULL_RERUN_calls']}->{p['SELECTIVE_RECOVERY_calls']} ({p['calls_reduction_pct']}%),"
              f" tokens {p['FULL_RERUN_tokens']}->{p['SELECTIVE_RECOVERY_tokens']} ({p['tokens_reduction_pct']}%)")
    else:
        print(f"  {p['case_id']}: {p['status']} ({p.get('exclusion_reason')})")
print()
print("=== COARSE -> SELECTIVE ===")
print(f"  comparable={coarse_to_sel['comparable_pair_count']}, excluded={coarse_to_sel['excluded_pair_count']}")
for p in coarse_to_sel["per_case"]:
    if p["status"] == "COMPARABLE":
        print(f"  {p['case_id']}: calls {p['COARSE_CHECKPOINT_calls']}->{p['SELECTIVE_RECOVERY_calls']} ({p['calls_reduction_pct']}%),"
              f" tokens {p['COARSE_CHECKPOINT_tokens']}->{p['SELECTIVE_RECOVERY_tokens']} ({p['tokens_reduction_pct']}%)")
    else:
        print(f"  {p['case_id']}: {p['status']} ({p.get('exclusion_reason')})")
print()
print("=== SELECTIVE 4-case stability ===")
for k, v in sel_stability["consistency"].items():
    print(f"  {k}: {v}")
