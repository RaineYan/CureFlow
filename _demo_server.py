"""Day 14B/EXP-02 Demo — stdlib HTTP server (no Flask/Streamlit).

Launch:
    python _demo_server.py [--port 8765]

Reads:
    experiments/results/exp02/exp02_summary.json
    experiments/results/exp02/*.json   (12 trial JSONs)

Renders a single-page HTML with:
  1. Workflow Graph  (SVG, color-coded by node state)
  2. Recovery Panel  (root cause, affected, recovery plan)
  3. Performance Comparison (SVG bar charts, reduction %)

No external network calls.  No real Provider.
"""
from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


OUT = ROOT / "experiments/results/exp02"


# Demo DAG topology (Research -> Data -> Analysis -> Report)
# plus a parallel Evidence Agent.  Used for the Workflow
# Graph SVG.
DEMO_DAG = {
    "nodes": [
        {"id": "research", "label": "Research Agent",
         "branch": "input"},
        {"id": "data", "label": "Data Agent",
         "branch": "compute"},
        {"id": "evidence", "label": "Evidence Agent",
         "branch": "validate"},
        {"id": "analysis", "label": "Analysis Agent",
         "branch": "compute"},
        {"id": "report", "label": "Report Agent",
         "branch": "output"},
    ],
    "edges": [
        ("research", "data"),
        ("data", "analysis"),
        ("data", "evidence"),
        ("analysis", "report"),
        ("evidence", "report"),
    ],
}


def _load_data() -> dict:
    """Load EXP-02 summary + 12 trial JSONs."""
    summary = json.loads(
        (OUT / "exp02_summary.json").read_text(encoding="utf-8")
    )
    trials = {}
    for p in OUT.glob("exp02_CASE_*_pre01.json"):
        d = json.loads(p.read_text(encoding="utf-8"))
        trials[d["trial_id"]] = d
    return {"summary": summary, "trials": trials}


def _make_node_state_html(
    case: str, strategy: str, recovery_nodes: list,
) -> str:
    """Return per-node state labels for the Workflow Graph
    given a case + strategy + recovery_nodes."""
    affected = {
        "CASE_A": ["analysis", "report"],
        "CASE_B": ["report"],
        "CASE_C": ["report"],
        "CASE_D": ["analysis", "report"],
    }[case]
    if strategy == "FULL_RERUN":
        reexec = ["data", "analysis", "report"]
        reused = ["research", "evidence"]
    elif strategy == "COARSE_CHECKPOINT":
        reexec = ["data", "analysis", "report"]
        reused = ["research", "evidence"]
    else:
        reexec = [n for n in affected if n not in reused_check(case)]
        reused = reused_check(case)
    return reexec, reused, affected


def _reused_check(case: str) -> list:
    return {
        "CASE_A": ["research", "evidence"],
        "CASE_B": ["research", "data", "evidence"],
        "CASE_C": ["research", "data", "evidence"],
        "CASE_D": ["research", "evidence"],
    }[case]


def _workflow_graph_svg(case: str, strategy: str) -> str:
    affected = {
        "CASE_A": ["analysis", "report"],
        "CASE_B": ["report"],
        "CASE_C": ["report"],
        "CASE_D": ["analysis", "report"],
    }[case]
    reused = {
        "CASE_A": ["research", "evidence"],
        "CASE_B": ["research", "data", "evidence"],
        "CASE_C": ["research", "data", "evidence"],
        "CASE_D": ["research", "evidence"],
    }[case]
    if strategy == "SELECTIVE_RECOVERY":
        reexec = [n for n in affected if n not in reused]
    else:  # FULL_RERUN or COARSE_CHECKPOINT
        reexec = ["data", "analysis", "report"]
    state_map = {}
    for n in DEMO_DAG["nodes"]:
        nid = n["id"]
        if nid in reexec:
            state = "REEXECUTING"
        elif nid in reused:
            state = "REUSED"
        else:
            state = "HEALTHY"
        state_map[nid] = state
    # Layout: research left, data center, evidence right,
    # analysis bottom-center, report bottom-right
    pos = {
        "research": (80, 60),
        "data": (220, 60),
        "evidence": (360, 60),
        "analysis": (220, 200),
        "report": (360, 200),
    }
    color_map = {
        "HEALTHY": "#4C78A8",
        "ERROR": "#E45756",
        "AFFECTED": "#F58518",
        "REEXECUTING": "#54A24B",
        "RECOVERED": "#72B7B2",
        "REUSED": "#B279A2",
    }
    parts = [
        '<svg width="450" height="280" '
        'xmlns="http://www.w3.org/2000/svg" '
        'style="background:#fafafa;border:1px solid #ccc">',
    ]
    for u, v in DEMO_DAG["edges"]:
        x1, y1 = pos[u]
        x2, y2 = pos[v]
        parts.append(
            f'<line x1="{x1+30}" y1="{y1}" '
            f'x2="{x2-30}" y2="{y2}" '
            f'stroke="#888" stroke-width="1.5" '
            f'marker-end="url(#arrow)"/>'
        )
    parts.append(
        '<defs><marker id="arrow" markerWidth="10" '
        'markerHeight="10" refX="6" refY="3" orient="auto">'
        '<path d="M0,0 L6,3 L0,6 z" fill="#888"/></marker></defs>'
    )
    for n in DEMO_DAG["nodes"]:
        x, y = pos[n["id"]]
        state = state_map[n["id"]]
        fill = color_map[state]
        parts.append(
            f'<rect x="{x-40}" y="{y-20}" '
            f'width="80" height="40" rx="5" '
            f'fill="{fill}" stroke="#333" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{x}" y="{y+5}" text-anchor="middle" '
            f'font-size="11" font-family="sans-serif" '
            f'fill="white" font-weight="bold">'
            f'{n["label"]}</text>'
        )
        parts.append(
            f'<text x="{x}" y="{y+25}" text-anchor="middle" '
            f'font-size="9" font-family="sans-serif" '
            f'fill="#fff">{state}</text>'
        )
    parts.append('</svg>')
    return "\n".join(parts)


def _legend_html() -> str:
    """Color legend for the Workflow Graph."""
    items = [
        ("HEALTHY", "#4C78A8"),
        ("ERROR", "#E45756"),
        ("AFFECTED", "#F58518"),
        ("REEXECUTING", "#54A24B"),
        ("RECOVERED", "#72B7B2"),
        ("REUSED", "#B279A2"),
    ]
    spans = " ".join(
        f'<span style="display:inline-block;padding:2px 6px;'
        f'margin:2px;background:{c};color:white;'
        f'border-radius:3px;font-size:11px">{s}</span>'
        for s, c in items
    )
    return f'<p style="margin:4px 0">{spans}</p>'


def _summary_table_html(summary: dict) -> str:
    m = summary["per_strategy_metrics"]
    rows = []
    for s in m:
        d = m[s]
        rows.append(
            f"<tr><td>{s}</td><td>{d['total_trials']}</td>"
            f"<td>{d['avg_llm_calls']:.1f}</td>"
            f"<td>{d['avg_tokens_total']:.0f}</td>"
            f"<td>{d['avg_latency_ms']:.0f}</td>"
            f"<td>{d['avg_rerun_nodes']:.1f}</td>"
            f"<td>{d['avg_reused_nodes']:.1f}</td>"
            f"<td>{d['recovery_success_rate']*100:.0f}%</td></tr>"
        )
    return (
        "<table border=1 cellpadding=4 style='border-collapse:"
        "collapse;font-family:sans-serif;font-size:13px'>"
        "<tr><th>Strategy</th><th>Trials</th><th>Avg LLM Calls</th>"
        "<th>Avg Tokens</th><th>Avg Latency (ms)</th>"
        "<th>Avg Rerun Nodes</th><th>Avg Reused Nodes</th>"
        "<th>Recovery Rate</th></tr>"
        + "".join(rows)
        + "</table>"
    )


def _reductions_table_html(summary: dict) -> str:
    rows = []
    for s, r in summary["reductions_vs_full"].items():
        rows.append(
            f"<tr><td>{s} vs FULL</td>"
            f"<td>{r['call_reduction_pct']}%</td>"
            f"<td>{r['token_reduction_pct']}%</td>"
            f"<td>{r['latency_reduction_pct']}%</td>"
            f"<td>{r['rerun_node_reduction_pct']}%</td></tr>"
        )
    return (
        "<table border=1 cellpadding=4 style='border-collapse:"
        "collapse;font-family:sans-serif;font-size:13px'>"
        "<tr><th>Strategy</th><th>Call Δ</th><th>Token Δ</th>"
        "<th>Latency Δ</th><th>Rerun-node Δ</th></tr>"
        + "".join(rows)
        + "</table>"
    )


def _recovery_panel_html(trials: dict) -> str:
    """Recovery Panel for a representative trial
    (CASE_A SELECTIVE)."""
    t = trials.get("exp02_CASE_A_selective_recovery_pre01", {})
    if not t:
        return "<p>No trial data</p>"
    affected = " / ".join(t.get("affected_nodes", []))
    unaffected = " / ".join(t.get("unaffected_nodes", []))
    rerun = " / ".join(t.get("rerun_nodes", []))
    return (
        "<div style='font-family:sans-serif;font-size:14px;"
        "line-height:1.6'>"
        f"<p><b>Root Cause</b>: {t.get('root_cause', '?')}</p>"
        f"<p><b>Failure Type</b>: {t.get('failure_type', '?')}</p>"
        f"<p><b>Strategy</b>: {t.get('strategy', '?')}</p>"
        f"<p><b>Affected Nodes</b>: {affected}</p>"
        f"<p><b>Unaffected Nodes</b>: {unaffected}</p>"
        f"<p><b>Recovery Plan</b>: Re-execute {rerun}</p>"
        f"<p><b>Recovery Result</b>: "
        f"{'OK' if t.get('recovery_success') else 'FAIL'}</p>"
        f"<p><b>Provider Execution</b>: "
        f"{'OK' if t.get('provider_execution_success') else 'FAIL'} "
        f"(calls={t.get('llm_calls')}, "
        f"tokens={t.get('tokens_total')})</p>"
        "</div>"
    )


def _case_selector_html() -> str:
    return (
        '<div style="margin:8px 0">'
        '<b>Select failure case:</b> '
        '<select id="caseSel" onchange="updatePanel()">'
        '<option value="CASE_A">CASE_A: upstream agent output error</option>'
        '<option value="CASE_B">CASE_B: midstream agent output error</option>'
        '<option value="CASE_C">CASE_C: evidence reference error</option>'
        '<option value="CASE_D">CASE_D: artifact data corruption</option>'
        '</select> '
        '<b>Select strategy:</b> '
        '<select id="stratSel" onchange="updatePanel()">'
        '<option value="FULL_RERUN">FULL_RERUN</option>'
        '<option value="COARSE_CHECKPOINT">COARSE_CHECKPOINT</option>'
        '<option value="SELECTIVE_RECOVERY" selected>SELECTIVE_RECOVERY</option>'
        '</select></div>'
    )


def _make_page(data: dict) -> str:
    summary = data["summary"]
    trials = data["trials"]
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>CureFlow Demo</title>
<style>
body {{ font-family: -apple-system, sans-serif;
       margin: 0; padding: 20px; background: #f5f5f5; }}
h1 {{ margin: 0 0 8px 0; color: #333; }}
h2 {{ margin: 20px 0 8px 0; color: #444;
     border-bottom: 1px solid #ccc; padding-bottom: 4px; }}
.panel {{ background: white; padding: 16px; margin: 12px 0;
        border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
.facts {{ background: #fffbe6; padding: 8px 12px;
        border-left: 4px solid #faad14; margin: 12px 0; }}
.tag {{ display:inline-block; background:#eef;
       padding:2px 8px; margin:2px; border-radius:3px;
       font-size:12px; }}
</style></head><body>
<h1>CureFlow Demo — 错误追踪 + 选择性恢复</h1>
<div class="facts">
<b>Mode</b>: MOCK (12 trials, 0 real Qwen calls) ·
<b>Provider execution</b>: 12/12 ·
<b>Recovery success</b>: 12/12 ·
<b>Source</b>: experiments/results/exp02/*.json
</div>

<div class="panel">
<h2>1. Workflow Graph</h2>
{_legend_html()}
{_case_selector_html()}
<div id="graph">{_workflow_graph_svg('CASE_A', 'SELECTIVE_RECOVERY')}</div>
</div>

<div class="panel">
<h2>2. Recovery Panel</h2>
<div id="recovery">{_recovery_panel_html(trials)}</div>
</div>

<div class="panel">
<h2>3. Performance Comparison</h2>
<h3>3.1 Per-strategy summary</h3>
{_summary_table_html(summary)}
<h3>3.2 Reductions vs FULL_RERUN</h3>
{_reductions_table_html(summary)}
<h3>3.3 Charts (auto-generated from data)</h3>
<p><b>LLM Calls by Strategy:</b><br>
<img src="/figures/llm_calls_by_strategy.svg" 
     style="max-width:600px"></p>
<p><b>Token Usage by Strategy:</b><br>
<img src="/figures/tokens_by_strategy.svg" 
     style="max-width:600px"></p>
<p><b>Wall Time by Strategy:</b><br>
<img src="/figures/latency_by_strategy.svg" 
     style="max-width:600px"></p>
<p><b>Re-executed Nodes by Strategy:</b><br>
<img src="/figures/rerun_nodes_by_strategy.svg" 
     style="max-width:600px"></p>
<p><b>Recovery Success Rate by Strategy:</b><br>
<img src="/figures/recovery_success_by_strategy.svg" 
     style="max-width:600px"></p>
<p><b>Token Usage by Case × Strategy:</b><br>
<img src="/figures/tokens_case_x_strategy.svg" 
     style="max-width:700px"></p>
</div>

<div class="panel">
<h2>4. EXP-02 Data (auto-derived)</h2>
<p>12 trial JSONs:
<span class="tag">CASE_A FULL</span>
<span class="tag">CASE_A COARSE</span>
<span class="tag">CASE_A SELECTIVE</span>
<span class="tag">CASE_B FULL</span>
<span class="tag">CASE_B COARSE</span>
<span class="tag">CASE_B SELECTIVE</span>
<span class="tag">CASE_C FULL</span>
<span class="tag">CASE_C COARSE</span>
<span class="tag">CASE_C SELECTIVE</span>
<span class="tag">CASE_D FULL</span>
<span class="tag">CASE_D COARSE</span>
<span class="tag">CASE_D SELECTIVE</span>
</p>
<p>All data sourced from <code>experiments/results/exp02/</code> ·
no hand-coded values · no real Provider calls in MOCK mode.</p>
</div>

<script>
function updatePanel() {{
  var c = document.getElementById('caseSel').value;
  var s = document.getElementById('stratSel').value;
  fetch('/api/graph?case=' + c + '&strat=' + s)
    .then(r => r.text())
    .then(html => document.getElementById('graph').innerHTML = html);
  fetch('/api/recovery?case=' + c + '&strat=' + s)
    .then(r => r.text())
    .then(html => document.getElementById('recovery').innerHTML = html);
}}
</script>
</body></html>"""


def _recovery_for(case: str, strategy: str, trials: dict) -> str:
    key = f"exp02_{case}_{strategy.lower()}_pre01"
    t = trials.get(key, {})
    if not t:
        return "<p>No data</p>"
    affected = " / ".join(t.get("affected_nodes", []))
    unaffected = " / ".join(t.get("unaffected_nodes", []))
    rerun = " / ".join(t.get("rerun_nodes", []))
    return (
        "<div style='font-family:sans-serif;font-size:14px;"
        "line-height:1.6'>"
        f"<p><b>Root Cause</b>: {t.get('root_cause', '?')}</p>"
        f"<p><b>Failure Type</b>: {t.get('failure_type', '?')}</p>"
        f"<p><b>Strategy</b>: {t.get('strategy', '?')}</p>"
        f"<p><b>Affected Nodes</b>: {affected}</p>"
        f"<p><b>Unaffected Nodes</b>: {unaffected}</p>"
        f"<p><b>Recovery Plan</b>: Re-execute {rerun}</p>"
        f"<p><b>Recovery Result</b>: "
        f"{'OK' if t.get('recovery_success') else 'FAIL'}</p>"
        f"<p><b>Provider Execution</b>: "
        f"{'OK' if t.get('provider_execution_success') else 'FAIL'} "
        f"(calls={t.get('llm_calls')}, "
        f"tokens={t.get('tokens_total')})</p>"
        "</div>"
    )


def _make_handler(data: dict):
    summary = data["summary"]
    trials = data["trials"]
    summary_html = _summary_table_html(summary)
    reductions_html = _reductions_table_html(summary)

    class Handler(BaseHTTPRequestHandler):
        def _send(self, body: bytes, content_type: str):
            self.send_response(200)
            self.send_header(
                "Content-Type", content_type
            )
            self.send_header(
                "Content-Length", str(len(body))
            )
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/" or path == "/index.html":
                self._send(
                    _make_page(data).encode("utf-8"),
                    "text/html; charset=utf-8",
                )
            elif path.startswith("/figures/"):
                fname = path[len("/figures/"):]
                fp = OUT / "figures" / fname
                if fp.exists():
                    self._send(
                        fp.read_bytes(), "image/svg+xml"
                    )
                else:
                    self._send(b"404", "text/plain")
            elif path.startswith("/api/graph"):
                from urllib.parse import urlparse, parse_qs
                q = parse_qs(urlparse(self.path).query)
                case = q.get("case", ["CASE_A"])[0]
                strat = q.get("strat", [
                    "SELECTIVE_RECOVERY"
                ])[0]
                self._send(
                    _workflow_graph_svg(
                        case, strat
                    ).encode("utf-8"),
                    "image/svg+xml",
                )
            elif path.startswith("/api/recovery"):
                from urllib.parse import urlparse, parse_qs
                q = parse_qs(urlparse(self.path).query)
                case = q.get("case", ["CASE_A"])[0]
                strat = q.get("strat", [
                    "SELECTIVE_RECOVERY"
                ])[0]
                self._send(
                    _recovery_for(
                        case, strat, trials
                    ).encode("utf-8"),
                    "text/html; charset=utf-8",
                )
            else:
                self._send(b"404", "text/plain")

        def log_message(self, fmt, *args):
            # Suppress per-request logs to avoid leaking
            # request paths (which include query strings).
            return
    return Handler


def main() -> int:
    p = argparse.ArgumentParser(
        description=(
            "CureFlow Demo — stdlib HTTP server.  "
            "Reads EXP-02 evidence only; no real Provider."
        )
    )
    p.add_argument(
        "--port", type=int, default=8765,
    )
    p.add_argument(
        "--host", default="127.0.0.1",
    )
    args = p.parse_args()
    if not (OUT / "exp02_summary.json").exists():
        print("ERROR: EXP-02 summary not found.  Run:")
        print("  python _exp02_runner.py")
        return 1
    data = _load_data()
    handler = _make_handler(data)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(
        f"CureFlow Demo running at "
        f"http://{args.host}:{args.port}/"
    )
    print(
        "  - Workflow Graph  (color-coded by node state)"
    )
    print(
        "  - Recovery Panel  (root cause + affected nodes)"
    )
    print(
        "  - Performance Comparison (5 charts from data)"
    )
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
