"""Evidence semantics for native OpenAI Responses API records.

This is a parallel protocol-specific audit.  It intentionally does not alter
the frozen Chat Completions ``chatcmpl-*`` evidence gate.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from .base import LLMResponse, UsageType
from .openai_responses import is_openai_response_id
from .parse import parse_claim_payload


@dataclass(frozen=True)
class OpenAIResponsesAudit:
    protocol: str
    expected_model: str
    provider_identity_verified: bool
    response_received: bool
    protocol_request_id_verified: bool
    usage_reported: bool
    schema_valid: bool
    request_succeeded: bool
    accepted: bool
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict:
        return asdict(self)


def audit_openai_response(
    response: LLMResponse,
    *,
    expected_model: str,
) -> OpenAIResponsesAudit:
    identity = response.provider == "openai" and response.model == expected_model
    response_received = bool(response.request_id or response.text)
    protocol_id = is_openai_response_id(response.request_id)
    usage_reported = response.usage_type == UsageType.PROVIDER_REPORTED
    payload, issues, reason = parse_claim_payload(response.text)
    schema_valid = payload is not None and not issues and not reason
    request_succeeded = identity and protocol_id and usage_reported
    accepted = request_succeeded and schema_valid

    reasons: list[str] = []
    if not identity:
        reasons.append("PROVIDER_IDENTITY_MISMATCH")
    if not response_received:
        reasons.append("NO_RESPONSE")
    if not protocol_id:
        reasons.append("INVALID_RESPONSES_REQUEST_ID")
    if not usage_reported:
        reasons.append("USAGE_NOT_PROVIDER_REPORTED")
    if not schema_valid:
        try:
            raw = json.loads(response.text)
        except (json.JSONDecodeError, TypeError):
            raw = None
        reasons.append(
            "PROVIDER_ERROR_ENVELOPE"
            if isinstance(raw, dict) and "error" in raw and "message" in raw
            else "CLAIM_SCHEMA_INVALID"
        )

    return OpenAIResponsesAudit(
        protocol="openai_responses_v1",
        expected_model=expected_model,
        provider_identity_verified=identity,
        response_received=response_received,
        protocol_request_id_verified=protocol_id,
        usage_reported=usage_reported,
        schema_valid=schema_valid,
        request_succeeded=request_succeeded,
        accepted=accepted,
        reason_codes=tuple(reasons),
    )

