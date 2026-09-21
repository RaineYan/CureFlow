"""
Structured-output parsing — Day 13 + Day 13.5.

Day 13: extracted the FIRST valid JSON object from the response text
(plain JSON, fenced ```json```, or embedded-in-prose).

Day 13.5: tightened.  The accepted shapes are now EXACTLY:

  A.  The whole response is a JSON object (possibly with surrounding
      whitespace).
  B.  The whole response is a single ```json ... ``` (or ``` ... ```)
      fenced block.  Whitespace before/after the fence is allowed,
      but no other prose.

Anything else — including a JSON object embedded in free prose like
"Here you go: {...} hope this helps" — is rejected with
INVALID_RESPONSE.  The claim task surfaces this as a SCHEMA_ERROR,
and recovery can decide how to handle it (NOT silently repair).

Rationale: in a real LLM benchmark we want to OBSERVE format drift,
not paper over it.  A repair parser (if ever added) must be a
separate `REPAIR` action with its own cost.

This module has no external dependencies.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional, Tuple


# A single fenced JSON block.  Tolerant on:
#   - opening ``` with optional "json" tag (case-insensitive)
#   - any amount of leading/trailing whitespace
#   - trailing whitespace inside the fence
#   - bare ``` without language tag
# Intolerant on: more than one fence, or non-whitespace text outside
# the fence.
_FENCE_RE = re.compile(
    r"^\s*```(?:json)?\s*\n?(?P<body>.*?)\n?\s*```\s*$",
    re.IGNORECASE | re.DOTALL,
)


def extract_json_object(text: str) -> Tuple[Optional[dict], str]:
    """Return (parsed_dict, reason_if_failed).

    Accepted shapes (Day 13.5):
      A.  text.strip() parses as a JSON object directly
      B.  the entire text is a single ```json ... ``` (or ``` ... ```)
          fenced block, whose body parses as a JSON object

    Rejected (returns None, "INVALID_RESPONSE: ..."):
      - text containing a JSON object embedded in free prose
      - text with more than one fenced block
      - text with no JSON object
      - text whose JSON object has a syntax error

    The reason string is short and machine-parseable so downstream
    code can switch on it.
    """
    if text is None or not text.strip():
        return None, "INVALID_RESPONSE: empty response"

    s = text.strip()

    # ---- Path B: single fenced block -------------------------
    if s.startswith("```"):
        # If there is more than one ``` in the body, that is
        # already a non-conformant shape; reject.
        if s.count("```") > 2:
            return None, (
                "INVALID_RESPONSE: multiple fenced blocks present; "
                "expected exactly one"
            )
        m = _FENCE_RE.match(text)
        if m is None:
            return None, "INVALID_RESPONSE: malformed fence"
        body = m.group("body").strip()
        if not body:
            return None, "INVALID_RESPONSE: empty fence body"
        try:
            obj = json.loads(body)
        except json.JSONDecodeError as e:
            return None, f"INVALID_RESPONSE: json decode error: {e}"
        if not isinstance(obj, dict):
            return None, (
                f"INVALID_RESPONSE: top-level is {type(obj).__name__}, "
                f"expected object"
            )
        return obj, ""

    # ---- Path A: plain JSON object ----------------------------
    try:
        obj = json.loads(s)
    except json.JSONDecodeError:
        # Day 13.5: NO free-text scan.  We refuse to find the
        # first "{" and balance braces; if the model didn't return
        # a parseable JSON object (or a single fence), it's a
        # real failure we want to see.
        return None, (
            "INVALID_RESPONSE: response is not plain JSON; "
            "rejected (no free-text scan)"
        )
    if not isinstance(obj, dict):
        return None, (
            f"INVALID_RESPONSE: top-level is {type(obj).__name__}, "
            f"expected object"
        )
    return obj, ""


# Schema required by every claim / section generator:
#   { "claim_text": str, "evidence_refs": [ArtifactRef, ...],
#     "confidence": float (optional) }

_CLAIM_REQUIRED_KEYS = ("claim_text", "evidence_refs")
_CLAIM_OPTIONAL_KEYS = ("confidence",)


# Day 14 M2 — Provider error-envelope detection (narrow by design).
#
# Some Providers (observed: DashScope on TLS handshake EOF) return an
# HTTP 200 with a JSON body whose top-level shape is
#
#   { "error": "<kind>", "message": "<human-readable>" }
#
# instead of a real chat-completion payload.  These envelopes are
# *Provider-side* failures, NOT claim-schema failures.  The
# detection rule below is intentionally narrow: it only fires when
# the body has BOTH `error` (non-empty string) and `message`
# (non-empty string) AND does NOT have any of the legitimate claim
# keys.  A business payload that happens to contain an "error"
# field alongside `claim_text` is NOT an envelope and continues to
# the schema check unchanged.
def is_provider_error_envelope(
    obj: Any,
) -> Tuple[bool, Optional[str], Optional[str]]:
    """Return (is_envelope, kind, message).

    `kind` is the value of obj["error"] (e.g. "NETWORK_ERROR",
    "AUTH_ERROR").  `message` is the value of obj["message"]
    (truncated only by the caller, not here).  Returns
    (False, None, None) for any non-matching shape.
    """
    if not isinstance(obj, dict):
        return False, None, None
    err = obj.get("error")
    msg = obj.get("message")
    if not (isinstance(err, str) and err):
        return False, None, None
    if not (isinstance(msg, str) and msg):
        return False, None, None
    # Must NOT have legitimate claim keys; if it does, it is a real
    # business payload that happens to carry an "error" field.
    if "claim_text" in obj or "evidence_refs" in obj:
        return False, None, None
    return True, err, msg


# Reason prefix used by parse_claim_payload and surfaced verbatim
# to the runner so failure_classification can route these to
# INFRA_FAILURE / <envelope kind> instead of RESPONSE_SCHEMA_ERROR.
PROVIDER_ERROR_ENVELOPE_PREFIX = "PROVIDER_ERROR_ENVELOPE:"


def parse_claim_payload(text: str) -> Tuple[Optional[dict], list[str], str]:
    """Extract the claim payload from `text`.  Returns
    (payload_dict_or_None, issues, reason).  `issues` is a list
    of structured error strings; `reason` is a free-form short
    description.

    The payload dict is the extracted JSON object (post-parse) —
    evidence_refs are LEFT AS STRINGS here; the caller (claim_task)
    parses them into ArtifactRef.

    Day 14 M2: if the extracted object is a Provider error envelope
    (see `is_provider_error_envelope`), this returns
    (None, [...], "PROVIDER_ERROR_ENVELOPE: <kind>: <message[:200]>")
    WITHOUT raising the standard "missing required key" issue.  The
    caller is expected to propagate the prefix so upstream
    classification can route it to INFRA_FAILURE / <kind> instead of
    RESPONSE_SCHEMA_ERROR.
    """
    obj, err = extract_json_object(text)
    if obj is None:
        return None, [f"extract_json_object failed: {err}"], err
    # Day 14 M2: detect Provider error envelope BEFORE claim-schema
    # validation.  Narrow detection — see is_provider_error_envelope.
    is_env, env_kind, env_msg = is_provider_error_envelope(obj)
    if is_env:
        env_reason = (
            f"{PROVIDER_ERROR_ENVELOPE_PREFIX} {env_kind}: "
            f"{env_msg[:200]}"
        )
        return (
            None,
            [f"{PROVIDER_ERROR_ENVELOPE_PREFIX} {env_kind}", env_msg],
            env_reason,
        )
    issues: list[str] = []
    for k in _CLAIM_REQUIRED_KEYS:
        if k not in obj:
            issues.append(f"missing required key: {k!r}")
    if "claim_text" in obj and not isinstance(obj["claim_text"], str):
        issues.append("claim_text is not a string")
    if "evidence_refs" in obj and not isinstance(obj["evidence_refs"], list):
        issues.append("evidence_refs is not a list")
    for k in _CLAIM_OPTIONAL_KEYS:
        if k in obj and k == "confidence" and not isinstance(obj[k], (int, float)):
            issues.append("confidence is not a number")
    return obj, issues, "" if not issues else "; ".join(issues)
