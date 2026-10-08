"""Stage 2 — SVG chart generation (stdlib only, no matplotlib/plotly).

Generates 4 SVG charts from stage2_trial_matrix.json:
  - calls_comparison.svg      (calls per (case, strategy))
  - tokens_comparison.svg     (provider_reported_tokens per (case, strategy))
  - wall_comparison.svg       (wall_time_ms per (case, strategy))
  - validity_comparison.svg   (provider_success / trial_valid per (case, strategy))

Data is NEVER smoothed or extrapolated.  Missing values (provider_success=False)
are drawn as a hatched "no data" cell with a label.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STAGE2 = ROOT / "experiments/results/exp02/stage2_evaluation"
matrix = json.loads((STAGE2 / "stage2_trial_matrix.json").read_text(encoding="utf-8"))

CASES = ["CASE_A", "CASE_B", "CASE_C", "CASE_D"]
STRATS = ["FULL_RERUN", "COARSE_CHECKPOINT", "SELECTIVE_RECOVERY"]
COLORS = {"FULL_RERUN": "#1f77b4", "COARSE_CHECKPOINT": "#ff7f0e",
          "SELECTIVE_RECOVERY": "#2ca02c"}


def lookup(case, strat, key):
    for r in matrix:
        if r["case_id"] == case and r["strategy"] == strat:
            return r.get(key)
    return None


def bar_chart_svg(out_path, title, y_label, value_key, max_override=None, unit=""):
    W, H = 720, 360
    pad_l, pad_r, pad_t, pad_b = 80, 40, 50, 70
    plot_w = W - pad_l - pad_r
    plot_h = H - pad_t - pad_b
    n_cases = len(CASES)
    group_w = plot_w / n_cases
    bar_w = group_w / (len(STRATS) + 1.0)  # 3 bars + gap per case
    # compute max
    all_vals = [lookup(c, s, value_key) for c in CASES for s in STRATS
                if lookup(c, s, value_key) is not None]
    ymax = max(all_vals) if all_vals else 1
    if max_override is not None:
        ymax = max(ymax, max_override)
    ymax = ymax * 1.15
    if ymax <= 0:
        ymax = 1
    lines = [f'<?xml version="1.0" encoding="UTF-8"?>',
             f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="monospace">',
             f'<text x="{W/2}" y="22" text-anchor="middle" font-size="14" font-weight="bold">{title}</text>',
             f'<text x="20" y="{H/2}" text-anchor="middle" font-size="11" transform="rotate(-90 20 {H/2})">{y_label}</text>',
             f'<line x1="{pad_l}" y1="{pad_t}" x2="{pad_l}" y2="{H-pad_b}" stroke="black"/>',
             f'<line x1="{pad_l}" y1="{H-pad_b}" x2="{W-pad_r}" y2="{H-pad_b}" stroke="black"/>']
    # y-axis ticks
    n_ticks = 5
    for i in range(n_ticks + 1):
        v = ymax * i / n_ticks
        y = H - pad_b - (plot_h * i / n_ticks)
        lines.append(f'<line x1="{pad_l-3}" y1="{y}" x2="{pad_l}" y2="{y}" stroke="black"/>')
        lines.append(f'<text x="{pad_l-7}" y="{y+3}" text-anchor="end" font-size="9">{v:.0f}{unit}</text>')
    # bars
    for ci, case in enumerate(CASES):
        gx = pad_l + ci * group_w + group_w / 2
        for si, strat in enumerate(STRATS):
            v = lookup(case, strat, value_key)
            x = gx + (si - 1) * bar_w - bar_w / 2
            if v is None or v == 0 and lookup(case, strat, "provider_execution_success") is False:
                # hatched "no data" rectangle
                lines.append(f'<rect x="{x}" y="{H-pad_b-2}" width="{bar_w*0.9}" height="14" fill="url(#hatch)" stroke="#888"/>')
                lines.append(f'<text x="{x+bar_w*0.45}" y="{H-pad_b+10}" text-anchor="middle" font-size="8" fill="#888">N/A</text>')
                continue
            v = v or 0
            bh = plot_h * v / ymax
            y = H - pad_b - bh
            lines.append(f'<rect x="{x}" y="{y}" width="{bar_w*0.9}" height="{bh}" fill="{COLORS[strat]}" opacity="0.85"/>')
            lines.append(f'<text x="{x+bar_w*0.45}" y="{y-3}" text-anchor="middle" font-size="9">{v:.0f}</text>')
        # case label
        lines.append(f'<text x="{gx}" y="{H-pad_b+15}" text-anchor="middle" font-size="11" font-weight="bold">{case}</text>')
    # legend
    lx = pad_l + 10
    ly = pad_t + 10
    lines.append(f'<rect x="{W-pad_r-160}" y="{pad_t-5}" width="155" height="65" fill="white" stroke="#ccc"/>')
    for i, s in enumerate(STRATS):
        ly2 = pad_t + 10 + i * 18
        lines.append(f'<rect x="{W-pad_r-150}" y="{ly2}" width="14" height="10" fill="{COLORS[s]}"/>')
        lines.append(f'<text x="{W-pad_r-130}" y="{ly2+9}" font-size="10">{s}</text>')
    lines.append('<defs>'
                 '<pattern id="hatch" patternUnits="userSpaceOnUse" width="6" height="6">'
                 '<path d="M0,6 L6,0" stroke="#888" stroke-width="1"/></pattern>'
                 '</defs>')
    lines.append('</svg>')
    out_path.write_text("\n".join(lines), encoding="utf-8")


def heatmap_svg(out_path, title, value_key, true_label="TRUE", false_label="FALSE", na_label="N/A"):
    W, H = 540, 320
    pad_l, pad_r, pad_t, pad_b = 100, 30, 50, 50
    plot_w = W - pad_l - pad_r
    plot_h = H - pad_t - pad_b
    cell_w = plot_w / len(CASES)
    cell_h = plot_h / len(STRATS)
    lines = [f'<?xml version="1.0" encoding="UTF-8"?>',
             f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="monospace">',
             f'<text x="{W/2}" y="22" text-anchor="middle" font-size="14" font-weight="bold">{title}</text>']
    for ci, case in enumerate(CASES):
        for si, strat in enumerate(STRATS):
            v = lookup(case, strat, value_key)
            x = pad_l + ci * cell_w
            y = pad_t + si * cell_h
            if v is True:
                fill = "#2ca02c"
                label = true_label
            elif v is False:
                fill = "#d62728"
                label = false_label
            else:
                fill = "#cccccc"
                label = na_label
            lines.append(f'<rect x="{x+2}" y="{y+2}" width="{cell_w-4}" height="{cell_h-4}" fill="{fill}" opacity="0.75"/>')
            lines.append(f'<text x="{x+cell_w/2}" y="{y+cell_h/2+4}" text-anchor="middle" font-size="11" fill="white" font-weight="bold">{label}</text>')
    # axis labels
    for ci, case in enumerate(CASES):
        lines.append(f'<text x="{pad_l+ci*cell_w+cell_w/2}" y="{H-pad_b+18}" text-anchor="middle" font-size="11" font-weight="bold">{case}</text>')
    for si, strat in enumerate(STRATS):
        lines.append(f'<text x="{pad_l-10}" y="{pad_t+si*cell_h+cell_h/2+4}" text-anchor="end" font-size="11" font-weight="bold">{strat}</text>')
    lines.append('</svg>')
    out_path.write_text("\n".join(lines), encoding="utf-8")


def main():
    bar_chart_svg(STAGE2 / "calls_comparison.svg",
                  "New LLM Calls by Case and Strategy (Stage 1.5 Run 2)",
                  "new LLM calls", "new_llm_calls", unit="")
    bar_chart_svg(STAGE2 / "tokens_comparison.svg",
                  "Provider-Reported Tokens by Case and Strategy (Stage 1.5 Run 2)",
                  "tokens", "provider_reported_tokens", unit="")
    bar_chart_svg(STAGE2 / "wall_comparison.svg",
                  "Wall Time (ms) by Case and Strategy (Stage 1.5 Run 2)",
                  "wall time (ms)", "wall_time_ms", unit="ms")
    heatmap_svg(STAGE2 / "validity_comparison.svg",
                "trial_valid Heatmap (Stage 1.5 Run 2)",
                "trial_valid", true_label="VALID", false_label="INVALID")
    heatmap_svg(STAGE2 / "provider_success_comparison.svg",
                "provider_execution_success Heatmap (Stage 1.5 Run 2)",
                "provider_execution_success", true_label="OK", false_label="CRASHED")
    print("SVG charts written:")
    for f in sorted(STAGE2.glob("*_comparison.svg")):
        print(f"  {f}  ({f.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
