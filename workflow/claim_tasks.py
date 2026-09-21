"""
Claim tasks — Day 5 + Day 6.

Day 5: Financial Claim (Metric -> Claim).
Day 6: User Claim, Operation Claim, three Report Sections, deterministic merge_report.

input_artifact_ids   = what the LLM was actually shown in its prompt
evidence_ids         = what the LLM's output explicitly cites

For section tasks the evidence is the upstream Claim (not the underlying
metrics) — this is what makes the chain "Metric -> Claim -> Section"
readable on the graph.

merge_report is a deterministic Tool. It uses _run_deterministic_tool
instead of @track_provenance because the merged text belongs in
`content`, not `value` (the decorator would put it in `value`).
"""
from __future__ import annotations

import json
import time
from typing import Callable, List

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from artifacts import (
    Artifact,
    ArtifactRef,
    ArtifactType,
    ExecutionCost,
)
from llm.base import LLMClient
from provenance.decorator import RunContext
from workflow.domain_knowledge import select_method_pack


# =====================================================================
# Output schema (LLM must emit this)
# =====================================================================

class ClaimOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim_text: str = Field(min_length=1)
    evidence_refs: List[str] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


# =====================================================================
# Prompt builders
# =====================================================================

def _evidence_block_lines(artifacts: List[Artifact]) -> list[str]:
    lines: list[str] = []
    for a in artifacts:
        ref_str = f"{a.run_id}/{a.artifact_id}@v{a.version}"
        payload = a.value if a.artifact_type == "Metric" else a.content
        lines.append(f"  {ref_str}  value={payload}")
    return lines


def build_claim_prompt(
    task_name: str,
    artifacts: List[Artifact],
    instruction: str,
    *,
    output_role: str = "claim",
) -> str:
    """Build the user-prompt for an LLM semantic task.

    Day 13.5 fix for the section/claim schema-mismatch bug:
    The LLM was seeing `task_name = "financial section writer"` and
    outputting `section_text` / `content` / `report_text` instead of
    `claim_text`.  The parser requires `claim_text` (because both
    Claim and Report Section share the same Artifact contract under
    Day 5/6 — `content` is the canonical "narrative text" field on
    the persisted Artifact, regardless of whether the producer was
    a financial claim or a financial section).

    To stop the model from drifting field names:
      - For `output_role="section"`, prepend a hard rule that names
        the exact fields and forbids aliases.
      - Always end with the same JSON template.

    Day 13.5 follow-up fix for the live-gate Test A failure:
    For the financial claim task, the live Qwen response omitted
    `metric_profit_margin` from `evidence_refs` and also failed
    to numerically state `metric_revenue_growth`.  Both failures
    are prompt-side, not model-side.  When we detect the financial
    claim task (by `task_name` containing "financial"), we
    additionally:
      - enumerate the exact ref strings the response MUST cite,
        computed from the available Artifacts (no hard-coded IDs)
      - enumerate the percentage values the response MUST state
        in `claim_text`, computed from `Artifact.value` (no
        hard-coded numbers)
      - explicitly say "DO NOT omit either required evidence ref".

    OpenAI A/B follow-up: the same validator contract applies to the user
    and operations branches.  Their prompts therefore enumerate the exact
    required refs and percentage values too.  In particular, a ratio such
    as ``0.5`` must be rendered as ``50.00%`` in claim_text; the validator
    remains unchanged and no silent repair is performed.

    The parser is unchanged.  Per Day 13.5 spec: no silent repair.
    """
    role_intro = {
        "claim":   f"You are a {task_name}.",
        "section": (
            f"You are a {task_name}.  You are writing a section of an "
            "analytical report, but you MUST still produce the SAME "
            "JSON shape that a claim would.  Specifically:"
        ),
    }.get(output_role, f"You are a {task_name}.")

    lines = [role_intro, ""]
    if output_role == "claim":
        # The fixed business workflow also receives the same curated method
        # guardrails as arbitrary-table interpretation.  These lines constrain
        # interpretation only; AVAILABLE_EVIDENCE below remains the sole source
        # of facts and citation identities for this run.
        method_pack = select_method_pack(
            ["year", "revenue", "cost", "users", "orders", "visits"],
            goal="business_review",
            row_count=24,
            max_cards=9,
        )
        lines.extend([
            "ANALYSIS METHOD KNOWLEDGE (guidance only, NOT run evidence):",
            "  - Never cite a method card as proof of a fact in this dataset.",
            "  - Use the cards to enforce assumptions and prohibited conclusions:",
        ])
        for method in method_pack["methods"]:
            prohibited = "; ".join(method["blockedClaims"])
            lines.append(
                f"  - [{method['title']}] {method.get('methodCore', method['method'])} PROHIBITED: {prohibited}"
            )
        lines.append("")
    if output_role == "section":
        lines.extend([
            "HARD RULES — your response will be rejected if violated:",
            "  1. Field names are EXACTLY: claim_text, evidence_refs, confidence.",
            "     Do NOT use section_text, content, report_text, summary, or any other name.",
            "  2. `claim_text` carries the section narrative (the prose the reader sees).",
            "  3. `evidence_refs` must contain the exact upstream Claim ref string(s).",
            "  4. `confidence` is a float in [0, 1].",
            "",
        ])

    # Financial Claim: required-evidence + required-numeric contract.
    # The validator (`validation/semantic.py::_expected_evidence_identities`)
    # requires `metric_revenue_growth` + `metric_profit_margin` for the
    # `financial_claim` policy.  The validator's numeric check requires
    # both to be expressed as percentages in `claim_text`.  We mirror
    # that contract in the prompt so the LLM does not silently drop
    # either one.  All values are derived from the actual Artifact
    # objects in `artifacts` — no hard-coded numbers.
    #
    # Day 13.6: cost_ratio contract is OPTIONAL.  INPUT ≠ EVIDENCE.
    # The model is allowed to (a) include cost_ratio in evidence_refs
    # (in which case the validator's numeric check requires its
    # percentage in claim_text), OR (b) omit cost_ratio from
    # evidence_refs (in which case the model MUST NOT mention or use
    # cost_ratio in claim_text at all).  The model cannot use INPUT-
    # only information in claim_text without also citing it.
    if output_role == "claim" and "financial" in task_name:
        rg = next((a for a in artifacts if a.artifact_id == "metric_revenue_growth"), None)
        pm = next((a for a in artifacts if a.artifact_id == "metric_profit_margin"), None)
        cr = next((a for a in artifacts if a.artifact_id == "metric_cost_ratio"), None)
        if rg is not None and pm is not None:
            rg_pct = _percent_str(rg.value)
            pm_pct = _percent_str(pm.value)
            rg_ref = f"{rg.run_id}/{rg.artifact_id}@v{rg.version}"
            pm_ref = f"{pm.run_id}/{pm.artifact_id}@v{pm.version}"
            cr_ref = (
                f"{cr.run_id}/{cr.artifact_id}@v{cr.version}"
                if cr is not None else None
            )
            cr_pct = _percent_str(cr.value) if cr is not None else None
            lines.extend([
                "FINANCIAL CLAIM CONTRACT (validator will reject if violated):",
                f"  - evidence_refs MUST include BOTH:",
                f"      1) {rg_ref}",
                f"      2) {pm_ref}",
                "    Do NOT omit either required evidence ref.",
                "  - claim_text MUST explicitly state the numerical value",
                "    for EACH required evidence metric, as a percentage:",
                f"      * revenue_growth ≈ {rg_pct}  (use ref {rg_ref})",
                f"      * profit_margin  ≈ {pm_pct}  (use ref {pm_ref})",
                "  - The two percentages above are derived from the",
                "    actual Artifact values; do NOT invent different numbers.",
            ])
            if cr is not None and cr_ref is not None and cr_pct is not None:
                lines.extend([
                    f"  - cost_ratio ({cr_ref}) is OPTIONAL evidence:",
                    "      * If you DO NOT include it in evidence_refs,",
                    "        you MUST NOT mention or use cost_ratio in claim_text.",
                    "      * If you DO include it in evidence_refs, the validator",
                    "        will require its numerical value in claim_text:",
                    f"        cost_ratio ≈ {cr_pct}  (use ref {cr_ref})",
                    "  - The INPUT list contains cost_ratio as an execution",
                    "    prerequisite, but INPUT ≠ EVIDENCE.  You cannot use",
                    "    cost_ratio information in claim_text without citing",
                    "    its ArtifactRef in evidence_refs.",
                ])
            lines.append("")

    # User Claim: user_growth is a ratio in the Artifact but the semantic
    # validator requires a percentage mention in claim_text.  State both the
    # exact ref and the exact display value so providers cannot mistake 0.5
    # for an absolute MAU value.
    elif output_role == "claim" and "user" in task_name:
        ug = next((a for a in artifacts if a.artifact_id == "metric_user_growth"), None)
        if ug is not None:
            ug_ref = f"{ug.run_id}/{ug.artifact_id}@v{ug.version}"
            ug_pct = _percent_str(ug.value)
            lines.extend([
                "USER CLAIM CONTRACT (validator will reject if violated):",
                f"  - evidence_refs MUST include: {ug_ref}",
                "  - claim_text MUST explicitly state user_growth as a percentage:",
                f"      * user_growth ≈ {ug_pct}  (use ref {ug_ref})",
                f"  - Write the percentage form with % or 百分之; do NOT write the raw ratio {ug.value!s} as though it were an absolute MAU value.",
                "",
            ])

    # Operations Claim: both metrics are required evidence and both must be
    # numerically represented as percentages, mirroring operation_claim's
    # existing evidence and numeric validation policy.
    elif output_role == "claim" and "operation" in task_name:
        cv = next((a for a in artifacts if a.artifact_id == "metric_conversion_rate"), None)
        og = next((a for a in artifacts if a.artifact_id == "metric_order_growth"), None)
        if cv is not None and og is not None:
            cv_ref = f"{cv.run_id}/{cv.artifact_id}@v{cv.version}"
            og_ref = f"{og.run_id}/{og.artifact_id}@v{og.version}"
            lines.extend([
                "OPERATIONS CLAIM CONTRACT (validator will reject if violated):",
                "  - evidence_refs MUST include BOTH:",
                f"      1) {cv_ref}",
                f"      2) {og_ref}",
                "  - claim_text MUST explicitly state BOTH values as percentages:",
                f"      * conversion_rate ≈ {_percent_str(cv.value)}  (use ref {cv_ref})",
                f"      * order_growth    ≈ {_percent_str(og.value)}  (use ref {og_ref})",
                "  - Use % or 百分之 for each value; do NOT emit raw ratios.",
                "",
            ])

    lines.append(f"Task instruction: {instruction}")
    lines.append("")
    lines.append("AVAILABLE_EVIDENCE (cite ONLY from this list, exact strings):")
    lines.extend(_evidence_block_lines(artifacts) or ["  (none)"])
    lines.append("")
    lines.append("Output STRICT JSON only, no other text:")
    lines.append("{")
    lines.append('  "claim_text": "...",')
    lines.append('  "evidence_refs": ["<run_id>/<artifact_id>@v<version>", ...],')
    lines.append('  "confidence": 0.0')
    lines.append("}")
    return "\n".join(lines)


def _percent_str(value) -> str:
    """Format `value` as a percentage with two decimals, e.g. 0.6078
    -> '60.78%'.  Used by the financial-claim prompt to make the
    expected numeric state explicit to the LLM.  Values are derived
    from the live Artifact — never hard-coded in the builder."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "N/A"
    return f"{f * 100.0:.2f}%"


# Day 5 backwards-compat alias
build_financial_claim_prompt = build_claim_prompt


# =====================================================================
# Internal: shared validation + save for LLM Claim tasks
# =====================================================================

def _parse_llm_output(
    response: LLMResponse,
    inputs: List[Artifact],
    run_id: str,
    store,
    artifact_id: str,
    producer_task: str,
) -> Artifact:
    """Shared validation + save path for all LLM Claim tasks.

    Raises ValueError on any validation failure (no half-baked Artifact).
    """
    # 1. cross-run
    for a in inputs:
        if a.run_id != run_id:
            raise ValueError(
                f"Cross-run input not allowed: got run_id={a.run_id!r} from "
                f"{a.artifact_id}@v{a.version}, expected {run_id!r}"
            )

    # 2. parse + validate JSON.  Day 13: use the robust extractor so
    #    real-model responses wrapped in ```json``` fences or with
    #    surrounding prose are handled.  The strict ClaimOutput
    #    schema is still enforced; the only change is the front-door
    #    parsing.
    from llm.parse import parse_claim_payload, PROVIDER_ERROR_ENVELOPE_PREFIX
    payload, parse_issues, parse_reason = parse_claim_payload(response.text)
    # Day 14 M2: if the parse failure is a Provider error envelope
    # (e.g. DashScope HTTP 200 with {"error": "NETWORK_ERROR", ...}),
    # propagate the reason verbatim so upstream classification can
    # route it to INFRA_FAILURE / <envelope kind> instead of the
    # standard RESPONSE_SCHEMA_ERROR.  The envelope check is done
    # BEFORE the payload-is-None wrapper so the prefix is not
    # hidden inside a generic message.  The claim schema is not
    # relaxed: legitimate business payloads with claim_text are
    # still validated strictly.
    if parse_reason.startswith(PROVIDER_ERROR_ENVELOPE_PREFIX):
        raise ValueError(parse_reason)
    if payload is None:
        raise ValueError(
            f"LLM output is not a valid claim payload: {parse_reason}"
        )
    if parse_issues:
        # Schema-level issues are still fatal
        raise ValueError(
            f"LLM output failed claim schema: {'; '.join(parse_issues)}"
        )
    try:
        parsed = ClaimOutput.model_validate(payload)
    except ValidationError as e:
        raise ValueError(f"LLM output failed ClaimOutput schema: {e}") from e

    # 3. parse evidence refs
    evidence_refs: List[ArtifactRef] = []
    for s in parsed.evidence_refs:
        try:
            evidence_refs.append(ArtifactRef.from_ref_string(s))
        except Exception as e:
            raise ValueError(f"Bad evidence ref string {s!r}: {e}") from e

    # 4. evidence must be subset of inputs + exist in store
    input_keys = {(a.run_id, a.artifact_id, a.version) for a in inputs}
    for er in evidence_refs:
        if er.run_id != run_id:
            raise ValueError(
                f"Cross-run evidence ref not allowed: {er.to_ref_string()}"
            )
        if (er.run_id, er.artifact_id, er.version) not in input_keys:
            raise ValueError(
                f"Evidence ref {er.to_ref_string()} is not in this run's "
                f"input set. The LLM cited something it was not given."
            )
        if store.get_version(er.run_id, er.artifact_id, er.version) is None:
            raise ValueError(
                f"Dangling evidence ref: {er.to_ref_string()} does not exist "
                f"in the store for run_id={run_id!r}"
            )

    # 5. next version
    existing = store.list_versions(run_id, artifact_id)
    next_v = (max(existing) + 1) if existing else 1

    # 6. build ClaimArtifact
    input_refs = [
        ArtifactRef(run_id=a.run_id, artifact_id=a.artifact_id, version=a.version)
        for a in inputs
    ]
    claim = Artifact(
        artifact_id=artifact_id,
        artifact_type=ArtifactType.CLAIM,
        producer_task=producer_task,
        run_id=run_id,
        version=next_v,
        value=None,
        content=parsed.claim_text,
        input_artifact_ids=input_refs,
        evidence_ids=evidence_refs,
        confidence=parsed.confidence,
        execution_cost=ExecutionCost(
            tokens=response.input_tokens + response.output_tokens,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            monetary_usd=response.monetary_usd,
            latency_ms=response.latency_ms,
            llm_calls=1,
        ),
        meta={
            "execution_unit": producer_task,
            "task_kind": "llm",
            # Day 13.5: thread the 3-state usage_type through to
            # the claim meta so downstream audit JSONs can filter
            # real-provider measurements.  usage_is_synthetic is
            # kept as a derived boolean for back-compat with
            # Day 13 audit scripts.
            "llm_provider": response.provider,
            "llm_model": response.model,
            "llm_usage_type": response.usage_type.value,
            "llm_usage_is_synthetic": response.usage_is_synthetic,
            "llm_request_id": response.request_id,
        },
    )
    return store.save(claim)


# =====================================================================
# Internal: deterministic tool runner (used by merge_report)
# =====================================================================

def _run_deterministic_tool(
    fn: Callable[..., str],
    inputs: List[Artifact],
    *,
    run_id: str,
    store,
    artifact_id: str,
    producer_task: str,
    extra_meta: dict | None = None,
) -> Artifact:
    """Run a deterministic tool, capture input refs, save ClaimArtifact
    with the function's return string stored in `content`.
    """
    for a in inputs:
        if a.run_id != run_id:
            raise ValueError(
                f"Cross-run input not allowed: got run_id={a.run_id!r} from "
                f"{a.artifact_id}@v{a.version}, expected {run_id!r}"
            )
    start = time.perf_counter()
    body = fn(*inputs)
    elapsed_ms = (time.perf_counter() - start) * 1000.0

    input_refs = [
        ArtifactRef(run_id=a.run_id, artifact_id=a.artifact_id, version=a.version)
        for a in inputs
    ]
    existing = store.list_versions(run_id, artifact_id)
    next_v = (max(existing) + 1) if existing else 1

    meta = {
        "execution_unit": producer_task,
        "task_kind": "tool",
    }
    if extra_meta:
        meta.update(extra_meta)

    artifact = Artifact(
        artifact_id=artifact_id,
        artifact_type=ArtifactType.CLAIM,
        producer_task=producer_task,
        run_id=run_id,
        version=next_v,
        value=None,
        content=body,
        input_artifact_ids=input_refs,
        evidence_ids=[],
        execution_cost=ExecutionCost(
            tokens=0, input_tokens=0, output_tokens=0,
            monetary_usd=0.0, latency_ms=elapsed_ms, llm_calls=0,
        ),
        meta=meta,
    )
    return store.save(artifact)


# =====================================================================
# Public claim tasks (one per branch)
# =====================================================================

def generate_financial_claim(
    revenue_growth: Artifact,
    profit_margin: Artifact,
    cost_ratio: Artifact,
    *,
    ctx: RunContext,
    llm_client: LLMClient,
) -> Artifact:
    """Financial Claim: 3 inputs (revenue_growth + profit_margin + cost_ratio)."""
    inputs = [revenue_growth, profit_margin, cost_ratio]
    prompt = build_claim_prompt(
        "financial analyst", inputs,
        "Write a short Chinese claim about the financial branch metrics.",
    )
    response = llm_client.generate(prompt, task_name="generate_financial_claim")
    return _parse_llm_output(
        response, inputs, ctx.run_id, ctx.store,
        artifact_id="claim_financial_growth",
        producer_task="generate_financial_claim",
    )


def generate_user_claim(
    user_growth: Artifact,
    *,
    ctx: RunContext,
    llm_client: LLMClient,
) -> Artifact:
    """User Claim: 1 input (user_growth MAU)."""
    inputs = [user_growth]
    prompt = build_claim_prompt(
        "user analyst", inputs,
        "Write a short Chinese claim about user growth, using the MAU metric.",
    )
    response = llm_client.generate(prompt, task_name="generate_user_claim")
    return _parse_llm_output(
        response, inputs, ctx.run_id, ctx.store,
        artifact_id="claim_user_growth",
        producer_task="generate_user_claim",
    )


def generate_operation_claim(
    conversion_rate: Artifact,
    order_growth: Artifact,
    *,
    ctx: RunContext,
    llm_client: LLMClient,
) -> Artifact:
    """Operation Claim: 2 inputs (conversion_rate + order_growth)."""
    inputs = [conversion_rate, order_growth]
    prompt = build_claim_prompt(
        "operations analyst", inputs,
        "Write a short Chinese claim about operations (orders + conversion rate).",
    )
    response = llm_client.generate(prompt, task_name="generate_operation_claim")
    return _parse_llm_output(
        response, inputs, ctx.run_id, ctx.store,
        artifact_id="claim_operation",
        producer_task="generate_operation_claim",
    )


# =====================================================================
# Report sections (LLM, evidence = upstream Claim only)
# =====================================================================

def _generate_section(
    section_name: str,
    upstream_claim: Artifact,
    *,
    ctx: RunContext,
    llm_client: LLMClient,
    artifact_id: str,
    task_name: str,
) -> Artifact:
    inputs = [upstream_claim]
    prompt = build_claim_prompt(
        f"{section_name} section writer", inputs,
        (
            f"Write the {section_name} section of the report, citing "
            f"the upstream claim.  Use the SAME JSON schema as a claim "
            f"(claim_text / evidence_refs / confidence); do not invent "
            f"section_text or content fields."
        ),
        output_role="section",
    )
    response = llm_client.generate(prompt, task_name=task_name)
    return _parse_llm_output(
        response, inputs, ctx.run_id, ctx.store,
        artifact_id=artifact_id,
        producer_task=task_name,
    )


def generate_financial_section(
    financial_claim: Artifact,
    *,
    ctx: RunContext,
    llm_client: LLMClient,
) -> Artifact:
    return _generate_section(
        "financial", financial_claim,
        ctx=ctx, llm_client=llm_client,
        artifact_id="claim_report_financial_section",
        task_name="generate_financial_section",
    )


def generate_user_section(
    user_claim: Artifact,
    *,
    ctx: RunContext,
    llm_client: LLMClient,
) -> Artifact:
    return _generate_section(
        "user", user_claim,
        ctx=ctx, llm_client=llm_client,
        artifact_id="claim_report_user_section",
        task_name="generate_user_section",
    )


def generate_operation_section(
    operation_claim: Artifact,
    *,
    ctx: RunContext,
    llm_client: LLMClient,
) -> Artifact:
    return _generate_section(
        "operation", operation_claim,
        ctx=ctx, llm_client=llm_client,
        artifact_id="claim_report_operation_section",
        task_name="generate_operation_section",
    )


# =====================================================================
# merge_report (deterministic Tool, no LLM)
# =====================================================================

def _merge_text(
    financial_section: Artifact,
    user_section: Artifact,
    operation_section: Artifact,
) -> str:
    return (
        f"[Financial]\n{financial_section.content}\n\n"
        f"[User]\n{user_section.content}\n\n"
        f"[Operation]\n{operation_section.content}"
    )


def merge_report(
    financial_section: Artifact,
    user_section: Artifact,
    operation_section: Artifact,
    *,
    ctx: RunContext,
) -> Artifact:
    """Deterministic concatenation of the three sections.

    Tool task: no LLM call. Cost = tokens=0, llm_calls=0, latency_ms=measured.
    input_artifact_ids = (fin, user, op) sections.
    evidence_ids is empty — concatenation is not a semantic citation.
    """
    return _run_deterministic_tool(
        _merge_text,
        [financial_section, user_section, operation_section],
        run_id=ctx.run_id,
        store=ctx.store,
        artifact_id="report_merged",
        producer_task="merge_report",
        extra_meta={"semantic_role": "merged_report"},
    )
