"""M1 — SVG chart generation (stdlib only, no matplotlib/plotly).

Generates distribution + heatmap SVGs from m1_strategy_summary.json +
m1_matched_pair_analysis.json.

NEVER smooths or extrapolates.  Outliers are kept visible.
"""
import json
import statistics as st
from pathlib import Path

ROOT = Path(__file__).resolve().parent
M1 = ROOT / "experiments/results/exp02/m1_5repeat_real"
matrix = json.loads((M1 / "m1_trial_matrix.json").read_text(encoding="utf-8"))
summary = json.loads((M1 / "m1_strategy_summary.json").read_text(encoding="utf-8"))
pair = json.loads((M1 / "m1_matched_pair_analysis.json").read_text(encoding="utf-8"))

CASES = ["CASE_A", "CASE_B", "CASE_C", "CASE_D"]
STRATS = ["FULL_RERUN", "COARSE_CHECKPOINT", "SELECTIVE_RECOVERY"]
COLORS = {"FULL_RERUN": "#1f77b4", "COARSE_CHECKPOINT": "#ff7f0e",
          "SELECTIVE_RECOVERY": "#2ca02c"}


def lookup(case, strat, repeat, key):
    for r in matrix:
        if r["case_id"] == case and r["strategy"] == strat and r["repeat_index"] == repeat:
            return r.get(key)
    return None


def per_case_strat_bar(out_path, title, y_label, value_key, max_override=None, unit=""):
    W, H = 800, 400
    pad_l, pad_r, pad_t, pad_b = 90, 50, 60, 90
    plot_w = W - pad_l - pad_r
    plot_h = H - pad_t - pad_b
    repeats = ["repeat_01", "repeat_02", "repeat_03", "repeat_04", "repeat_05"]
    n_groups = len(CASES)
    group_w = plot_w / n_groups
    bar_w = group_w / (len(STRATS) + 1.0)
    all_vals = [lookup(c, s, r, value_key)
                for c in CASES for s in STRATS for r in repeats
                if lookup(c, s, r, value_key) is not None]
    ymax = max(all_vals) if all_vals else 1
    if max_override is not None:
        ymax = max(ymax, max_override)
    ymax = ymax * 1.18
    if ymax <= 0:
        ymax = 1
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="monospace">',
        f'<text x="{W/2}" y="22" text-anchor="middle" font-size="14" font-weight="bold">{title}</text>',
        f'<text x="20" y="{H/2}" text-anchor="middle" font-size="11" transform="rotate(-90 20 {H/2})">{y_label}</text>',
        f'<line x1="{pad_l}" y1="{pad_t}" x2="{pad_l}" y2="{H-pad_b}" stroke="black"/>',
        f'<line x1="{pad_l}" y1="{H-pad_b}" x2="{W-pad_r}" y2="{H-pad_b}" stroke="black"/>',
    ]
    n_ticks = 5
    for i in range(n_ticks + 1):
        v = ymax * i / n_ticks
        y = H - pad_b - (plot_h * i / n_ticks)
        lines.append(f'<line x1="{pad_l-3}" y1="{y}" x2="{pad_l}" y2="{y}" stroke="black"/>')
        lines.append(f'<text x="{pad_l-7}" y="{y+3}" text-anchor="end" font-size="9">{v:.0f}{unit}</text>')
    for ci, case in enumerate(CASES):
        gx = pad_l + ci * group_w + group_w / 2
        for si, strat in enumerate(STRATS):
            # gather 5 repeats, plot them stacked as small bars
            xs = []
            for ri, rep in enumerate(repeats):
                v = lookup(case, strat, rep, value_key)
                x = gx + (si - 1) * bar_w * 1.2 - bar_w * 0.6 + ri * (bar_w * 0.18)
                xs.append((x, v))
            for x, v in xs:
                if v is None or v == 0:
                    continue
                bh = plot_h * v / ymax
                y = H - pad_b - bh
                lines.append(f'<rect x="{x}" y="{y}" width="{bar_w*0.18}" height="{bh}" fill="{COLORS[strat]}" opacity="0.75"/>')
        lines.append(f'<text x="{gx}" y="{H-pad_b+15}" text-anchor="middle" font-size="11" font-weight="bold">{case}</text>')
    # legend
    for i, s in enumerate(STRATS):
        ly = pad_t + 10 + i * 18
        lines.append(f'<rect x="{W-pad_r-160}" y="{ly}" width="14" height="10" fill="{COLORS[s]}"/>')
        lines.append(f'<text x="{W-pad_r-140}" y="{ly+9}" font-size="10">{s}</text>')
    lines.append('</svg>')
    out_path.write_text("\n".join(lines), encoding="utf-8")


def heatmap_5axis(out_path, title, value_key, true_label="TRUE", false_label="FALSE"):
    """3-strategy × 4-case matrix where each cell is coloured by majority
    of 5 repeats (e.g. 5/5 = green, 4/5 = yellow, <4/5 = red)."""
    W, H = 540, 320
    pad_l, pad_r, pad_t, pad_b = 100, 30, 50, 50
    plot_w = W - pad_l - pad_r
    plot_h = H - pad_t - pad_b
    cell_w = plot_w / len(CASES)
    cell_h = plot_h / len(STRATS)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="monospace">',
        f'<text x="{W/2}" y="22" text-anchor="middle" font-size="14" font-weight="bold">{title}</text>',
    ]
    for ci, case in enumerate(CASES):
        for si, strat in enumerate(STRATS):
            # count true / false across 5 repeats
            n_true = sum(1 for r in matrix
                         if r["case_id"] == case and r["strategy"] == strat
                         and r[value_key] is True)
            n_total = sum(1 for r in matrix
                          if r["case_id"] == case and r["strategy"] == strat)
            x = pad_l + ci * cell_w
            y = pad_t + si * cell_h
            if n_total == 0:
                fill, label = "#cccccc", "N/A"
            elif n_true == n_total:
                fill, label = "#2ca02c", f"{n_true}/{n_total}"
            elif n_true >= 4:
                fill, label = "#bcbd22", f"{n_true}/{n_total}"
            elif n_true >= 2:
                fill, label = "#ff7f0e", f"{n_true}/{n_total}"
            else:
                fill, label = "#d62728", f"{n_true}/{n_total}"
            lines.append(f'<rect x="{x+2}" y="{y+2}" width="{cell_w-4}" height="{cell_h-4}" fill="{fill}" opacity="0.75"/>')
            lines.append(f'<text x="{x+cell_w/2}" y="{y+cell_h/2+4}" text-anchor="middle" font-size="13" fill="white" font-weight="bold">{label}</text>')
    for ci, case in enumerate(CASES):
        lines.append(f'<text x="{pad_l+ci*cell_w+cell_w/2}" y="{H-pad_b+18}" text-anchor="middle" font-size="11" font-weight="bold">{case}</text>')
    for si, strat in enumerate(STRATS):
        lines.append(f'<text x="{pad_l-10}" y="{pad_t+si*cell_h+cell_h/2+4}" text-anchor="end" font-size="11" font-weight="bold">{strat}</text>')
    lines.append('</svg>')
    out_path.write_text("\n".join(lines), encoding="utf-8")


def reduction_histogram(out_path, title, key_path, unit="%"):
    """Histogram of per-pair reduction values from matched-pair analysis."""
    f2s = pair["full_to_selective_reduction"]
    vals = [p[key_path] for p in f2s["per_pair_details"]
            if p["status"] == "COMPARABLE"]
    if not vals:
        return
    W, H = 700, 360
    pad_l, pad_r, pad_t, pad_b = 80, 40, 60, 60
    plot_w = W - pad_l - pad_r
    plot_h = H - pad_t - pad_b
    # 8 bins from 0 to 100
    n_bins = 8
    bin_w = 100 / n_bins
    counts = [0] * n_bins
    for v in vals:
        idx = min(n_bins - 1, int(v // bin_w))
        counts[idx] += 1
    ymax = max(counts) * 1.2 or 1
    bar_w = plot_w / n_bins * 0.8
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="monospace">',
        f'<text x="{W/2}" y="22" text-anchor="middle" font-size="14" font-weight="bold">{title}</text>',
        f'<text x="20" y="{H/2}" text-anchor="middle" font-size="11" transform="rotate(-90 20 {H/2})">count</text>',
        f'<text x="{W/2}" y="{H-10}" text-anchor="middle" font-size="11">reduction ({unit})</text>',
        f'<line x1="{pad_l}" y1="{pad_t}" x2="{pad_l}" y2="{H-pad_b}" stroke="black"/>',
        f'<line x1="{pad_l}" y1="{H-pad_b}" x2="{W-pad_r}" y2="{H-pad_b}" stroke="black"/>',
    ]
    n_ticks = 5
    for i in range(n_ticks + 1):
        v = ymax * i / n_ticks
        y = H - pad_b - (plot_h * i / n_ticks)
        lines.append(f'<line x1="{pad_l-3}" y1="{y}" x2="{pad_l}" y2="{y}" stroke="black"/>')
        lines.append(f'<text x="{pad_l-7}" y="{y+3}" text-anchor="end" font-size="9">{v:.0f}</text>')
    for i, c in enumerate(counts):
        x = pad_l + i * (plot_w / n_bins) + (plot_w / n_bins - bar_w) / 2
        bh = plot_h * c / ymax
        y = H - pad_b - bh
        lines.append(f'<rect x="{x}" y="{y}" width="{bar_w}" height="{bh}" fill="#2ca02c" opacity="0.8"/>')
        lines.append(f'<text x="{x+bar_w/2}" y="{y-3}" text-anchor="middle" font-size="10">{c}</text>')
        # x label
        xc = x + bar_w / 2
        xlbl = f"{int(i*bin_w)}-{int((i+1)*bin_w)}"
        lines.append(f'<text x="{xc}" y="{H-pad_b+15}" text-anchor="middle" font-size="9">{xlbl}</text>')
    # stats annotation
    s = f2s[key_path]
    stat_text = f"n={s['n']}  mean={s['mean']}{unit}  SD={s['sd']}  CI95=[{s['ci_95_low']},{s['ci_95_high']}]"
    lines.append(f'<text x="{W/2}" y="{H-30}" text-anchor="middle" font-size="11">{stat_text}</text>')
    lines.append('</svg>')
    out_path.write_text("\n".join(lines), encoding="utf-8")


def per_case_reduction_bar(out_path, title, key_path, unit="%"):
    """4 cases × 3 metrics grouped bar chart."""
    W, H = 720, 380
    pad_l, pad_r, pad_t, pad_b = 90, 40, 60, 70
    plot_w = W - pad_l - pad_r
    plot_h = H - pad_t - pad_b
    n_cases = len(CASES)
    group_w = plot_w / n_cases
    bar_w = group_w / 4.0
    metrics = [
        ("calls_reduction_pct", "calls", "#1f77b4"),
        ("tokens_reduction_pct", "tokens", "#ff7f0e"),
        ("wall_reduction_pct", "wall", "#2ca02c"),
    ]
    ymax = 100
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="monospace">',
        f'<text x="{W/2}" y="22" text-anchor="middle" font-size="14" font-weight="bold">{title}</text>',
        f'<text x="20" y="{H/2}" text-anchor="middle" font-size="11" transform="rotate(-90 20 {H/2})">reduction ({unit})</text>',
        f'<line x1="{pad_l}" y1="{pad_t}" x2="{pad_l}" y2="{H-pad_b}" stroke="black"/>',
        f'<line x1="{pad_l}" y1="{H-pad_b}" x2="{W-pad_r}" y2="{H-pad_b}" stroke="black"/>',
    ]
    n_ticks = 5
    for i in range(n_ticks + 1):
        v = ymax * i / n_ticks
        y = H - pad_b - (plot_h * i / n_ticks)
        lines.append(f'<line x1="{pad_l-3}" y1="{y}" x2="{pad_l}" y2="{y}" stroke="black"/>')
        lines.append(f'<text x="{pad_l-7}" y="{y+3}" text-anchor="end" font-size="9">{v:.0f}{unit}</text>')
    f2s = pair["full_to_selective_reduction"]
    for ci, case in enumerate(CASES):
        gx = pad_l + ci * group_w + group_w / 2
        for mi, (mkey, mname, mcolor) in enumerate(metrics):
            v = f2s["per_case_breakdown"][case].get(mkey)
            if v is None:
                continue
            x = gx + (mi - 1) * bar_w - bar_w / 2
            bh = plot_h * v / ymax
            y = H - pad_b - bh
            lines.append(f'<rect x="{x}" y="{y}" width="{bar_w*0.85}" height="{bh}" fill="{mcolor}" opacity="0.8"/>')
            lines.append(f'<text x="{x+bar_w*0.42}" y="{y-3}" text-anchor="middle" font-size="9">{v:.1f}</text>')
        lines.append(f'<text x="{gx}" y="{H-pad_b+15}" text-anchor="middle" font-size="11" font-weight="bold">{case}</text>')
    for mi, (mkey, mname, mcolor) in enumerate(metrics):
        ly = pad_t + 10 + mi * 18
        lines.append(f'<rect x="{W-pad_r-110}" y="{ly}" width="14" height="10" fill="{mcolor}"/>')
        lines.append(f'<text x="{W-pad_r-90}" y="{ly+9}" font-size="10">{mname}</text>')
    lines.append('</svg>')
    out_path.write_text("\n".join(lines), encoding="utf-8")


def main():
    per_case_strat_bar(M1 / "calls_distribution.svg",
                       "LLM Calls per (case, strategy) — 5 repeats each",
                       "calls", "new_llm_calls", max_override=7)
    per_case_strat_bar(M1 / "tokens_distribution.svg",
                       "Provider-Reported Tokens per (case, strategy) — 5 repeats each",
                       "tokens", "provider_reported_tokens", max_override=2700)
    per_case_strat_bar(M1 / "wall_distribution.svg",
                       "Wall Time (ms) per (case, strategy) — 5 repeats each",
                       "wall (ms)", "wall_time_ms", max_override=120000, unit="ms")
    heatmap_5axis(M1 / "trial_valid_heatmap.svg",
                  "trial_valid Heatmap (5/5 = green, 4/5 = yellow, ≤3/5 = red)",
                  "trial_valid")
    heatmap_5axis(M1 / "provider_success_heatmap.svg",
                  "provider_execution_success Heatmap",
                  "provider_execution_success")
    reduction_histogram(M1 / "calls_reduction_distribution.svg",
                        "FULL→SELECTIVE Calls Reduction per Pair (n=20)",
                        "calls_reduction_pct")
    reduction_histogram(M1 / "tokens_reduction_distribution.svg",
                        "FULL→SELECTIVE Tokens Reduction per Pair (n=20)",
                        "tokens_reduction_pct")
    reduction_histogram(M1 / "wall_reduction_distribution.svg",
                        "FULL→SELECTIVE Wall-Time Reduction per Pair (n=20)",
                        "wall_reduction_pct")
    per_case_reduction_bar(M1 / "per_case_reduction_summary.svg",
                          "FULL→SELECTIVE Reduction by Case (mean of 5 repeats)",
                          "calls_reduction_pct")
    print("M1 SVG charts written:")
    for f in sorted(M1.glob("*.svg")):
        print(f"  {f.name}  ({f.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
