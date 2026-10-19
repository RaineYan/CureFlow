"""Read-only audit of production prompting using generated non-user data.

The model boundary is replaced before invocation. No network or real API key.
Character/byte measurements are NOT tokenizer measurements or quality results.
"""
import csv
from collections import defaultdict
from io import BytesIO, StringIO
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from cureflow_app.server import make_handler
from cureflow_app.provider_runtime import ProviderRun
from llm.base import LLMResponse, UsageType


class Capture:
    def __init__(self):
        self.prompts = []

    def generate(self, prompt, *, task_name=""):
        self.prompts.append(prompt)
        evidence = json.loads(prompt.rsplit("<UNTRUSTED_EVIDENCE>", 1)[1].split("</UNTRUSTED_EVIDENCE>", 1)[0])
        ref = evidence["analysisTools"]["executedTools"][0]["evidenceRef"]
        return LLMResponse(text=json.dumps({
            "claim_text": "本地统计用于核对，不能独立证明业务结论。",
            "evidence_refs": [ref], "confidence": 0.5,
        }), usage_type=UsageType.SYNTHETIC, provider="doubao", model="offline-capture")


measurements = []
for count in (10, 100, 1000, 10000):
    stream = StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["record_id", "group", "measurement", "factor"])
    writer.writerows((f"R{i:06d}", f"G{i % 3}", round((i % 97) / 7, 3), i % 23) for i in range(count))
    csv_text = stream.getvalue()
    capture = Capture()
    provider = ProviderRun(mode="DOUBAO_CHAT_LIVE", provider="doubao", model="offline-capture",
                           live=True, client=capture, call_budget=6)
    with tempfile.TemporaryDirectory(prefix="first-analysis-audit-") as temporary:
        handler = object.__new__(make_handler(Path(temporary)))
        body = json.dumps({"filename": "generated.csv", "csvText": csv_text,
                           "analysisGoal": "exploratory_analysis", "providerMode": "doubao_chat",
                           "confirmLive": True, "confirmMaxCalls": 6}).encode()
        handler.path = "/api/analyze"
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile, handler.wfile = BytesIO(body), BytesIO()
        status = []
        handler.send_response = status.append
        handler.send_header = lambda *a: None
        handler.end_headers = lambda: None
        with patch("cureflow_app.server.DesktopProviderRuntime.resolve", return_value=provider):
            handler.do_POST()
        assert status == [200], json.loads(handler.wfile.getvalue()).get("error")
    prompt = capture.prompts[0]
    raw_evidence = prompt.rsplit("<UNTRUSTED_EVIDENCE>", 1)[1].split("</UNTRUSTED_EVIDENCE>", 1)[0]
    evidence = json.loads(raw_evidence)
    measurements.append({
        "generatedRows": count, "csvChars": len(csv_text), "csvBytes": len(csv_text.encode()),
        "actualCureFlowPromptChars": len(prompt), "promptBytes": len(prompt.encode()),
        "evidenceChars": len(raw_evidence), "instructionAndEnvelopeChars": len(prompt) - len(raw_evidence),
        "evidenceSectionChars": {k: len(json.dumps(v, ensure_ascii=False)) for k, v in evidence.items()},
        "capturedCalls": len(capture.prompts), "networkCalls": 0,
    })

logs = defaultdict(list)
for provider in ("openai", "qwen", "deepseek", "doubao"):
    for path in (REPO / ".cureflow-desktop-data" / f"{provider}_live_audit").glob("*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            usage = value.get("usage") or {}
            if value.get("protocol") == "generic_evidence_interpretation_v1" and usage.get("type") == "provider_reported":
                logs[provider].append((usage.get("inputTokens"), usage.get("outputTokens")))
        except (OSError, ValueError):
            pass
print(json.dumps({"measurementKind": "synthetic_data_character_audit_not_token_benchmark",
                  "measurements": measurements,
                  "existingReportedUsageAggregates": {
                      provider: {"records": len(rows),
                                 "inputRange": [min(r[0] for r in rows), max(r[0] for r in rows)],
                                 "outputRange": [min(r[1] for r in rows), max(r[1] for r in rows)]}
                      for provider, rows in logs.items()
                  }}, ensure_ascii=True, indent=2))
