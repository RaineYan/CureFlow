"""Day 14 — Real Provider wiring.

`Day14RecordingClient` is the ONLY sanctioned bridge
between a `RealLLMClient` and the strategy pipeline.
It auto-appends a `LiveCallRecord` to the supplied
`LiveCallLedger` for every `generate` call, BEFORE
any downstream validation runs.

`SentinelRealProviderClient` is an OFFLINE test stand-in
that has a clearly distinct class name (so a `REAL
mode + FakeLLM` regression is detectable by object
identity).  It records the call sequence so a test can
assert "FULL received exactly 6 logical LLM calls".

`assert_not_fake_client` is a runtime guard that a real
pilot must pass: it rejects any LLMClient whose class
name is `FakeLLMClient` (or the lower-case substring
"fake").  This catches the "key present but FakeLLM
routed" bug at the strategy entry point.

`MockHttpRealLLMClient` is an OFFLINE stand-in for
`RealLLMClient` that simulates a real HTTP 200
response with a deterministic `request_id` and
`usage_type=provider_reported`.  It is distinct from
`SentinelRealProviderClient` because it carries the
real-provider signal (chatcmpl-style request_id +
provider_reported usage) without touching the network.
Tests use this to validate the failure chain (real
Provider response → schema fail → ledger persisted) in
isolation.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from llm import LLMClient, LLMResponse, UsageType
from experiments.live_ledger import LiveCallLedger, LiveCallRecord


# =====================================================================
# Sentinel real provider — offline test stand-in
# =====================================================================

_FAKE_CLIENT_CLASS_NAMES = (
    "FakeLLMClient",
    "fake_llm_client",
    "fakellmclient",
)


def assert_not_fake_client(llm_client: Any) -> None:
    """Reject any LLMClient whose class name contains
    'fake' (case-insensitive).  This is the hard guard
    for the Day 14 real-mode bug: if the strategy
    receives a FakeLLMClient under REAL_PROVIDER mode,
    this raises immediately."""
    if llm_client is None:
        return
    name = type(llm_client).__name__
    if any(s in name.lower() for s in _FAKE_CLIENT_CLASS_NAMES):
        raise AssertionError(
            f"HARNESS_PROVIDER_ROUTING_ERROR: REAL_PROVIDER mode "
            f"received {name!r} (a Fake client).  Aborting before "
            f"any trial work to avoid silent fake routing."
        )


class SentinelRealProviderClient:
    """OFFLINE stand-in for RealLLMClient.  Class name
    explicitly does NOT contain 'fake' so it passes
    `assert_not_fake_client`.  Records every
    `generate` call to `self.calls` (a list of dicts
    with execution_unit + prompt snippet + response
    payload).

    Behavior:
      - return_value_json: dict that the test sets
        before each call; the client returns
        `LLMResponse(text=json.dumps(return_value_json))`.
      - latency_ms: artificial latency in ms.
      - input_tokens / output_tokens: synthetic but
        marked usage_type=PROVIDER_REPORTED so the
        ledger counts it.

    This is a TEST FIXTURE.  It must NEVER be wired
    into a real pilot.
    """

    def __init__(
        self,
        *,
        return_value_json: Optional[Dict[str, Any]] = None,
        latency_ms: float = 50.0,
        input_tokens: int = 100,
        output_tokens: int = 50,
        provider: str = "qwen",
        model: str = "qwen3.7-plus-2026-05-26",
    ) -> None:
        self.return_value_json = return_value_json or {
            "claim_text": "test sentinel claim",
            "evidence_refs": [],
            "confidence": 0.9,
        }
        self.latency_ms = latency_ms
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.provider = provider
        self.model = model
        self.calls: List[Dict[str, Any]] = []
        self._counter = 0

    def generate(self, prompt: str, *, task_name: str = "") -> LLMResponse:
        self._counter += 1
        unit = task_name or self._infer_unit(prompt)
        self.calls.append({
            "call_index": self._counter,
            "task_name": task_name,
            "execution_unit": unit,
            "prompt_snippet": prompt[:120],
        })
        # Parse the prompt's AVAILABLE_EVIDENCE block so
        # the sentinel echoes the actual refs the workflow
        # provided.  This avoids the "Cross-run evidence
        # ref not allowed" error that the workflow's
        # _parse_llm_output raises.
        evidence_refs = _parse_evidence_refs_from_prompt(
            prompt, expected_run_id=None,
        )
        # Build the response payload.  If the user
        # provided a return_value_json, use it but
        # overwrite evidence_refs with the prompt-derived
        # list (so cross-run validation passes).
        payload = dict(self.return_value_json)
        if evidence_refs:
            payload["evidence_refs"] = evidence_refs
        # LLMResponse forbids extra fields, so do NOT
        # pass total_tokens (it is derived from
        # input_tokens + output_tokens downstream).
        return LLMResponse(
            text=json.dumps(payload, ensure_ascii=False),
            provider=self.provider,
            model=self.model,
            usage_type=UsageType.PROVIDER_REPORTED,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            reasoning_tokens=None,
            monetary_usd=0.0,
            latency_ms=self.latency_ms,
            request_id=(
                f"chatcmpl-sentinel-{self._counter:04d}-"
                f"{int(time.time() * 1000)}"
            ),
        )

    def _infer_unit(self, prompt: str) -> str:
        # The build_claim_prompt includes the task_name
        # as a sentinel.  Look for known keywords.
        if "财务" in prompt or "financial" in prompt.lower():
            return "generate_financial_claim"
        if "用户" in prompt or "user" in prompt.lower():
            return "generate_user_claim"
        if "运营" in prompt or "operation" in prompt.lower():
            return "generate_operation_claim"
        if "section" in prompt.lower() or "摘要" in prompt:
            return "generate_financial_section"
        return "unknown"


# =====================================================================
# Day14RecordingClient — auto-append to LiveCallLedger
# =====================================================================

class Day14RecordingClient:
    """Wraps an LLMClient (typically RealLLMClient) and
    AUTO-APPENDS one LiveCallRecord per `generate` call
    to the supplied `LiveCallLedger`.  Even if downstream
    validation fails, the record persists.

    Public surface:
      .inner       — the wrapped LLMClient
      .ledger      — the LiveCallLedger being appended
      .records     — list of appended LiveCallRecord
                     (mirrors ledger.records, for tests
                     that want a stable, private view)
      .response_captures — list of ResponseCapture dicts
                     with sanitized response data (raw
                     response shape, parsed top-level
                     keys, sanitized assistant content
                     with secrets redacted).
      .persistence_path — optional path to a JSONL file
                     where each record is atomically
                     appended.  If None, persistence is
                     in-memory only.
      .generate(prompt, *, task_name) — delegates to
                     inner.generate, then appends and
                     persists BEFORE returning (so an
                     exception in the caller cannot lose
                     the record).
    """

    # Cap on sanitized_assistant_content length.
    MAX_CAPTURE_CHARS = 4000

    def __init__(
        self,
        inner: LLMClient,
        ledger: LiveCallLedger,
        *,
        phase_prefix: str = "",
        trial_id: str = "",
        case_id: str = "",
        strategy: str = "",
        persistence_path: Optional[Path] = None,
    ) -> None:
        assert_not_fake_client(inner)
        self.inner = inner
        self.ledger = ledger
        self.phase_prefix = phase_prefix
        self.trial_id = trial_id
        self.case_id = case_id
        self.strategy = strategy
        self.persistence_path = persistence_path
        self.records: List[LiveCallRecord] = []
        self.response_captures: List[Dict[str, Any]] = []
        # Provider-verified semantic state (5 layers,
        # not a single boolean).  Each is independently
        # set after a real response is processed.
        self.real_http_roundtrip_verified = False
        self.provider_identity_verified = False
        self.provider_authenticated = False
        self.provider_request_succeeded = False
        self.provider_verified = False  # legacy gate:
                                         # = request_succeeded

    def generate(self, prompt: str, *, task_name: str = "") -> LLMResponse:
        unit = task_name or _infer_execution_unit(prompt)
        # 1) issue the call.  If the inner client raises,
        #    we still want a record (UNAVAILABLE).
        response: Optional[LLMResponse] = None
        error_type: Optional[str] = None
        try:
            response = self.inner.generate(prompt, task_name=task_name)
        except Exception as e:
            error_type = type(e).__name__
            response = None
        # 2) build a LiveCallRecord and append to ledger.
        rec = self._build_record(
            unit=unit, prompt=prompt, response=response,
            error_type=error_type,
        )
        self.ledger.append(rec)
        self.records.append(rec)
        # 3) capture sanitized response (BEFORE returning
        #    so even an exception in the caller can
        #    introspect it).
        capture = self._build_response_capture(
            unit=unit, prompt=prompt, response=response,
            error_type=error_type,
        )
        self.response_captures.append(capture)
        # 4) append-safe persistence: write the record
        #    to disk BEFORE returning, so a process
        #    crash or traceback cannot lose the audit.
        self._persist(capture)
        # 5) Update the FIVE-LAYER provider-verified
        #    semantic state.  These are NOT the same
        #    boolean; they distinguish:
        #      - real_http_roundtrip_verified: did an
        #        actual HTTP request leave this process?
        #      - provider_identity_verified: is the
        #        provider/model the one we expected?
        #      - provider_authenticated: did the provider
        #        accept our auth (HTTP != 401)?
        #      - provider_request_succeeded: did we get
        #        a successful model inference response
        #        (HTTP 2xx + chatcmpl-* id + usage block)?
        #      - provider_verified: same as the
        #        "request_succeeded" gate (kept for
        #        back-compat with older tests).
        # A HTTP 401 flips:
        #   - real_http_roundtrip_verified = True
        #   - provider_identity_verified = True
        #   - provider_authenticated = False
        #   - provider_request_succeeded = False
        #   - provider_verified = False
        if response is not None:
            self.real_http_roundtrip_verified = True
            prov = (response.provider or "").lower()
            if prov == "qwen":
                self.provider_identity_verified = True
            # Authenticated = NO error envelope (no
            # "error" key in parsed JSON).
            parsed_keys = capture.get(
                "parsed_top_level_keys", []
            ) or []
            if "error" not in parsed_keys:
                self.provider_authenticated = True
            # request_succeeded = chatcmpl- id AND
            # usage_type=provider_reported.
            rid = response.request_id or ""
            utype = (
                response.usage_type.value
                if hasattr(response.usage_type, "value")
                else str(response.usage_type)
            )
            if rid.startswith("chatcmpl-") and utype == (
                "provider_reported"
            ):
                self.provider_request_succeeded = True
                self.provider_verified = True
        # 6) re-raise if the inner errored.
        if error_type is not None:
            raise RuntimeError(
                f"{error_type} from {type(self.inner).__name__}"
            )
        return response

    def _build_record(
        self,
        *,
        unit: str,
        prompt: str,
        response: Optional[LLMResponse],
        error_type: Optional[str],
    ) -> LiveCallRecord:
        if response is None:
            return LiveCallRecord(
                phase=self.phase_prefix,
                execution_unit=unit,
                provider=(
                    getattr(self.inner, "provider_name", lambda: "unknown")()
                    if hasattr(self.inner, "provider_name")
                    else "unknown"
                ),
                model=(
                    self.inner.config.model
                    if hasattr(self.inner, "config")
                    and getattr(self.inner, "config", None) is not None
                    else "unknown"
                ),
                request_id=None,
                usage_type=UsageType.UNAVAILABLE.value,
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
                client_wall_clock_latency_ms=0.0,
                schema_valid=False,
                evidence_valid=False,
                semantic_valid=False,
                produced_ref=None,
                error_type=error_type,
                error_message_sanitized=None,
                reasoning_tokens=None,
                request_enable_thinking=None,
                request_response_format=None,
                is_replayed=False,
            )
        return LiveCallRecord(
            phase=self.phase_prefix,
            execution_unit=unit,
            provider=response.provider or "unknown",
            model=response.model or "unknown",
            request_id=response.request_id,
            usage_type=(
                response.usage_type.value
                if hasattr(response.usage_type, "value")
                else str(response.usage_type)
            ),
            input_tokens=response.input_tokens or 0,
            output_tokens=response.output_tokens or 0,
            total_tokens=(
                (response.input_tokens or 0)
                + (response.output_tokens or 0)
            ),
            client_wall_clock_latency_ms=response.latency_ms or 0.0,
            schema_valid=True,
            evidence_valid=True,
            semantic_valid=True,
            produced_ref=None,
            error_type=error_type,
            error_message_sanitized=None,
            reasoning_tokens=response.reasoning_tokens,
            request_enable_thinking=None,
            request_response_format=None,
            is_replayed=False,
        )

    def _build_response_capture(
        self,
        *,
        unit: str,
        prompt: str,
        response: Optional[LLMResponse],
        error_type: Optional[str],
    ) -> Dict[str, Any]:
        """Build a sanitized response capture dict.
        Captures raw response shape, parsed top-level
        keys, and sanitized assistant content.  The
        sanitization step REMOVES:
          - API key patterns: r"sk-[A-Za-z0-9]{8,}"
          - Authorization headers
          - Bearer tokens
        Length is capped at MAX_CAPTURE_CHARS."""
        capture: Dict[str, Any] = {
            "execution_unit": unit,
            "request_id": (
                response.request_id if response is not None else None
            ),
            "provider": (
                response.provider if response is not None else None
            ),
            "model": (
                response.model if response is not None else None
            ),
            "usage_type": (
                response.usage_type.value
                if response is not None
                and hasattr(response.usage_type, "value")
                else (
                    str(response.usage_type)
                    if response is not None else None
                )
            ),
            "input_tokens": (
                response.input_tokens if response is not None else 0
            ),
            "output_tokens": (
                response.output_tokens if response is not None else 0
            ),
            "total_tokens": (
                (response.input_tokens or 0)
                + (response.output_tokens or 0)
                if response is not None else 0
            ),
            "latency_ms": (
                response.latency_ms if response is not None else 0.0
            ),
            "response_received": response is not None,
            "error_type": error_type,
            "raw_response_shape": {},
            "parsed_top_level_keys": [],
            "sanitized_assistant_content": None,
        }
        if response is None:
            return capture
        text = response.text or ""
        # Sanitize secrets
        sanitized = _redact_secrets(text)
        # Cap length
        if len(sanitized) > self.MAX_CAPTURE_CHARS:
            sanitized = sanitized[: self.MAX_CAPTURE_CHARS] + \
                f"\n[TRUNCATED at {self.MAX_CAPTURE_CHARS} chars]"
        capture["sanitized_assistant_content"] = sanitized
        # Parse shape
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                capture["raw_response_shape"] = {
                    "kind": "json_object",
                    "top_level_keys": sorted(parsed.keys()),
                }
                capture["parsed_top_level_keys"] = sorted(parsed.keys())
            elif isinstance(parsed, list):
                capture["raw_response_shape"] = {
                    "kind": "json_array",
                    "len": len(parsed),
                }
            else:
                capture["raw_response_shape"] = {
                    "kind": "json_scalar",
                    "type": type(parsed).__name__,
                }
        except Exception:
            capture["raw_response_shape"] = {
                "kind": "non_json",
                "len": len(text),
                "starts_with": text[:80] if text else "",
            }
        return capture

    def _persist(self, capture: Dict[str, Any]) -> None:
        """Append-safe persistence: write the record to
        disk atomically.  If `self.persistence_path` is
        set, the capture is JSON-line appended.  Failure
        to persist is non-fatal (logged to stderr).

        Scope of guarantee: in the currently covered
        Python exception paths (ValueError / RealLLMError
        / validation failure / process termination
        handled by the CLI), the record will reach
        disk.  Power loss / OS hard crash / filesystem
        corruption are NOT covered by this guarantee
        because we do not call os.fsync() — adding it
        is future work, not a current benchmark blocker.
        """
        if self.persistence_path is None:
            return
        try:
            self.persistence_path.parent.mkdir(
                parents=True, exist_ok=True,
            )
            with open(self.persistence_path, "a", encoding="utf-8") as f:
                f.write(
                    json.dumps(capture, ensure_ascii=False)
                    + "\n"
                )
        except Exception as e:
            sys.stderr.write(
                f"Day14RecordingClient: persist failed: {e}\n"
            )


# =====================================================================
# 5-LAYER PROVIDER STATE (canonical derivation)
# =====================================================================

def derive_5_layer_provider_state(
    *,
    response_received: bool,
    provider: Optional[str],
    model: Optional[str],
    request_id: Optional[str],
    usage_type: Optional[str],
) -> Dict[str, bool]:
    """Derive the canonical 5-layer provider state from
    a single live-call response.

    Why 5 layers and not 1 boolean?  A single
    `provider_verified` boolean conflates two distinct
    questions:
      1. Did an HTTP request actually leave this process
         and reach the Provider?
      2. Did the Provider return a successful inference
         response (chatcmpl-* id + usage block)?

    For a Provider HTTP 401 (auth rejected), the
    answer to (1) is YES and the answer to (2) is NO.
    The single-boolean view makes this look like "no
    Provider call happened", which is wrong: the call
    happened, it just failed at the auth layer.

    The 5 layers, in monotonic order:
      - real_http_roundtrip_verified  : HTTP layer reached
      - provider_identity_verified    : provider/model match
      - provider_authenticated        : Provider accepted auth
      - provider_request_succeeded    : Provider returned 2xx
                                        + chatcmpl id + usage
      - provider_verified (legacy)    : alias for
                                        request_succeeded;
                                        benchmark aggregate
    """
    return {
        "real_http_roundtrip_verified": bool(
            response_received
        ),
        "provider_identity_verified": bool(
            response_received
            and provider == "qwen"
            and model == "qwen3.7-plus-2026-05-26"
        ),
        "provider_authenticated": bool(
            response_received
            and provider == "qwen"
            and model == "qwen3.7-plus-2026-05-26"
            and bool(request_id)
            and str(request_id).startswith("chatcmpl-")
        ),
        "provider_request_succeeded": bool(
            response_received
            and provider == "qwen"
            and model == "qwen3.7-plus-2026-05-26"
            and bool(request_id)
            and str(request_id).startswith("chatcmpl-")
            and usage_type == "provider_reported"
        ),
        "provider_verified": bool(
            response_received
            and provider == "qwen"
            and model == "qwen3.7-plus-2026-05-26"
            and bool(request_id)
            and str(request_id).startswith("chatcmpl-")
            and usage_type == "provider_reported"
        ),
    }


# =====================================================================
# R3 replacement_reason derivation (PRE-R3 AUTH SEMANTICS FIX)
# =====================================================================

def derive_replacement_reason(
    *,
    previous_failure_class: Optional[str],
    previous_failure_subtype: Optional[str],
) -> str:
    """Derive the replacement_reason for a new trial
    based on the IMMEDIATE predecessor's failure
    classification.  The rule (PRE-R3 AUTH SEMANTICS
    FIX):

      - INFRA_FAILURE / HTTP_401_AUTH_ERROR
            → AUTH_CREDENTIAL_CORRECTION
      - INFRA_FAILURE / <other>
            → INFRA_FAILURE_CORRECTION_<subtype>
      - VALIDATION_FAILURE / <any>
            → PRE_RETRY_FORENSIC_HARDENING
      - UNCONFIRMED_PROVIDER_EXECUTION_FAILURE / <any>
            → PRE_RETRY_FORENSIC_HARDENING
      - default
            → PRE_RETRY_FORENSIC_HARDENING
    """
    if (
        previous_failure_class == "INFRA_FAILURE"
        and previous_failure_subtype == "HTTP_401_AUTH_ERROR"
    ):
        return "AUTH_CREDENTIAL_CORRECTION"
    if previous_failure_class == "INFRA_FAILURE":
        return (
            f"INFRA_FAILURE_CORRECTION_"
            f"{previous_failure_subtype or 'UNKNOWN'}"
        )
    return "PRE_RETRY_FORENSIC_HARDENING"


def _redact_secrets(text: str) -> str:
    """Redact API key / Authorization / Bearer tokens
    from a string.  Patterns:
      - sk-<8+ chars, with optional .*-masked suffix>
        e.g. "sk-ws-H.****...br4U" (provider's masked
        display) → <sk-redacted>
        or "sk-abcdefghijkl" (full key) → <sk-redacted>
      - Bearer <8+ chars>
      - Authorization: <header value>
    Returns the redacted text."""
    if not text:
        return text
    # Match sk- followed by 4+ chars (must NOT be a JSON
    # string terminator / brace / quote).  The chars
    # may include alphanumeric, ., -, _, * (for
    # provider-masked display like sk-***...xxx).
    redacted = re.sub(
        r"sk-[A-Za-z0-9._\-\*]{4,}",
        "<sk-redacted>",
        text,
    )
    redacted = re.sub(
        r"Bearer\s+[A-Za-z0-9._\-]{8,}",
        "Bearer <redacted>",
        redacted,
    )
    redacted = re.sub(
        r"(?i)(authorization\s*:\s*)([^\n]+)",
        r"\1<redacted>",
        redacted,
    )
    return redacted


def _infer_execution_unit(prompt: str) -> str:
    """Best-effort fallback when the caller did not
    pass task_name.  Looks for a known prefix in the
    prompt header."""
    if "财务 claim" in prompt or "financial claim" in prompt.lower():
        return "generate_financial_claim"
    if "用户 claim" in prompt or "user claim" in prompt.lower():
        return "generate_user_claim"
    if "运营 claim" in prompt or "operation claim" in prompt.lower():
        return "generate_operation_claim"
    if "财务 section" in prompt or "financial section" in prompt.lower():
        return "generate_financial_section"
    if "用户 section" in prompt or "user section" in prompt.lower():
        return "generate_user_section"
    if "运营 section" in prompt or "operation section" in prompt.lower():
        return "generate_operation_section"
    return "unknown"


# =====================================================================
# MockHttpRealLLMClient — simulates a real Provider
# HTTP 200 response WITHOUT touching the network.  The
# response is tagged `usage_type=provider_reported`
# (so the recording client marks `provider_verified=True`)
# and the `request_id` follows the real chatcmpl-*
# format.  This is a TEST FIXTURE only.
# =====================================================================

class MockHttpRealLLMClient:
    """An OFFLINE stand-in for `RealLLMClient` that
    simulates a real Provider HTTP 200 response.  The
    class name does NOT contain "fake", so it passes
    `assert_not_fake_client`.  Tests configure the
    `assistant_content_factory` to return any payload
    (valid claim, schema-broken, markdown, etc.) and
    the client wraps it in a chat-completions-shaped
    response with provider_reported usage.

    NO network.  NO api key.  Pure offline fixture.
    """

    def __init__(
        self,
        *,
        assistant_content_factory=None,
        input_tokens: int = 100,
        output_tokens: int = 50,
        latency_ms: float = 50.0,
        provider: str = "qwen",
        model: str = "qwen3.7-plus-2026-05-26",
    ) -> None:
        # Default factory: return a valid claim payload.
        if assistant_content_factory is None:
            def _default(prompt: str, task_name: str) -> str:
                return json.dumps({
                    "claim_text": "test mock claim",
                    "evidence_refs": [],
                    "confidence": 0.9,
                }, ensure_ascii=False)
            assistant_content_factory = _default
        self._factory = assistant_content_factory
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens
        self._latency_ms = latency_ms
        self._provider = provider
        self._model = model
        self.calls: List[Dict[str, Any]] = []
        self._counter = 0

    def generate(self, prompt: str, *, task_name: str = "") -> LLMResponse:
        self._counter += 1
        unit = task_name or _infer_execution_unit(prompt)
        self.calls.append({
            "call_index": self._counter,
            "task_name": task_name,
            "execution_unit": unit,
            "prompt_snippet": prompt[:120],
        })
        assistant_content = self._factory(prompt, unit)
        return LLMResponse(
            text=assistant_content,
            provider=self._provider,
            model=self._model,
            usage_type=UsageType.PROVIDER_REPORTED,
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
            reasoning_tokens=None,
            monetary_usd=0.0,
            latency_ms=self._latency_ms,
            # Use chatcmpl- style request_id so
            # provider_verified triggers (non-empty +
            # chatcmpl- prefix).
            request_id=(
                f"chatcmpl-mock-{self._counter:04d}-"
                f"{int(time.time() * 1000)}"
            ),
        )


# Match lines like:
#   "  live_test_a/metric_revenue_growth@v1  value=0.6078"
#   "live_test_a / metric_revenue_growth @ v1  value=0.6078"
# Captures the run_id, artifact_id, and version.
_AVAILABLE_EVIDENCE_RE = re.compile(
    r"^\s*([A-Za-z0-9_]+)\s*/\s*([A-Za-z0-9_]+)\s*@\s*v(\d+)\s",
    re.MULTILINE,
)


def _parse_evidence_refs_from_prompt(
    prompt: str,
    *,
    expected_run_id: Optional[str] = None,
) -> List[str]:
    """Parse the prompt's AVAILABLE_EVIDENCE block and
    return the ref-strings (e.g. "run_id/artifact_id@v1")
    that the workflow provided as inputs.  These are
    exactly the refs the LLM is allowed to cite as
    evidence.  If `expected_run_id` is given, only
    refs matching that run_id are returned.
    """
    out = []
    for m in _AVAILABLE_EVIDENCE_RE.finditer(prompt):
        run_id, aid, ver = m.group(1), m.group(2), m.group(3)
        if expected_run_id is not None and run_id != expected_run_id:
            continue
        out.append(f"{run_id}/{aid}@v{ver}")
    return out
