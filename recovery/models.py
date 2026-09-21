"""
Recovery models — Day 8.

Definitions:
  - RecoveryAction: one of REUSE / TOOL_RECOMPUTE / REPROMPT
    (4 others — RETRY, MODEL_FALLBACK, ROLLBACK, HUMAN_VERIFY —
     are reserved as enum values but not implemented yet)
  - RecoveryStep: one ordered entry in a plan
  - RecoveryPlan: the full ordered list plus fault sources and
    reusable existing artifacts
  - ExecutedRecoveryStep: post-execution record of what actually
    ran (REUSE has produced_ref=None)
  - RecoveryExecutionResult: the overall outcome of an executor run

Invariant: target_ref always points to the CURRENT (potentially bad)
artifact. The executor produces a new version (next_v) and the
replacement_map captures old -> new.
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from artifacts import ArtifactRef


class RecoveryAction(str, Enum):
    REUSE = "REUSE"
    TOOL_RECOMPUTE = "TOOL_RECOMPUTE"
    REPROMPT = "REPROMPT"
    # Reserved for future; not implemented in Day 8
    RETRY = "RETRY"
    MODEL_FALLBACK = "MODEL_FALLBACK"
    ROLLBACK = "ROLLBACK"
    HUMAN_VERIFY = "HUMAN_VERIFY"


class RecoveryStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order: int
    action: RecoveryAction
    target_ref: ArtifactRef            # the current (potentially bad) version
    execution_unit: str
    reason: str
    required_input_refs: List[ArtifactRef] = Field(default_factory=list)


class RecoveryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    fault_sources: List[ArtifactRef] = Field(default_factory=list)
    target_ref: ArtifactRef
    steps: List[RecoveryStep] = Field(default_factory=list)
    reused_refs: List[ArtifactRef] = Field(default_factory=list)
    # Day 14 v2: name of the strategy that produced this plan.  The
    # executor uses this to look up the strategy in
    # `recovery.strategy.REGISTRY` and dispatch each step through
    # `strategy.dispatch_step(step, ctx)`.  Defaults to
    # "SELECTIVE_RECOVERY" so every v1 plan built by
    # `SelectiveRecoveryPlanner()` (which did not stamp a name)
    # still routes through the correct strategy.
    strategy_name: str = "SELECTIVE_RECOVERY"


class ExecutedRecoveryStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: RecoveryAction
    replaced_ref: ArtifactRef
    produced_ref: Optional[ArtifactRef] = None
    execution_unit: str
    input_refs: List[ArtifactRef] = Field(default_factory=list)
    tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    llm_calls: int = 0
    latency_ms: float = 0.0


class RecoveryExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    plan: RecoveryPlan
    replacement_map: Dict[str, str] = Field(default_factory=dict)  # old ref_str -> new ref_str
    executed_steps: List[ExecutedRecoveryStep] = Field(default_factory=list)
    reused_refs: List[ArtifactRef] = Field(default_factory=list)
    final_target_refs: List[ArtifactRef] = Field(default_factory=list)
    success: bool = False
    failure_reason: str = ""


# =====================================================================
# Day 13.6 FINAL-FINAL: CleanPathAuditResult
# =====================================================================
#
# A clean-path audit returns a STRUCTURED result, not a bare bool.
# The audit traces every visited ref in the post-recovery graph
# so a False result is always debuggable: which exact
# (run_id, artifact_id, version) is in a bad state, and why.
#
# `clean_path` semantic (locked by tests):
#   - target itself must be VALID or RECOVERED
#   - upstream chain (walking propagating edges: INPUT for tool,
#     EVIDENCE for LLM) must be all VALID or RECOVERED
#   - INVALIDATED / CONTAMINATED are NEVER clean
#   - the audit must NOT touch the historical fault trail
#     (old @v2 INVALIDATED/CONTAMINATED stays as-is)

class CleanPathAuditResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    target_ref: ArtifactRef

    # Every exact ArtifactRef the audit visited, in BFS order.
    # Includes the target itself.
    visited_refs: List[ArtifactRef] = Field(default_factory=list)

    # Refs whose validation_status is one of the clean states
    # (VALID or RECOVERED).  Always == visited_refs - non_clean_refs
    # - missing_refs.
    clean_refs: List[ArtifactRef] = Field(default_factory=list)

    # Refs whose validation_status is INVALIDATED or CONTAMINATED.
    # Empty when `passed=True`.  Each entry is a dict with the
    # ref and the offending status so a False result is debuggable
    # from the JSON alone.
    non_clean_refs: List[dict] = Field(default_factory=list)

    # Refs that the graph said were upstream but the store
    # returned None for.  Missing data — audit cannot conclude.
    missing_refs: List[ArtifactRef] = Field(default_factory=list)

    # True iff the caller rebuilt the ArtifactGraph from the
    # post-recovery store before calling the audit.  When False,
    # the audit is suspect: a stale pre-recovery graph would
    # not know about the recovered @v3 nodes and would falsely
    # report a bad path.
    graph_rebuilt_after_recovery: bool = False

    # Human-readable reason.  Empty when `passed=True`.  When
    # `passed=False`, the reason names the first offending ref
    # and its status.
    reason: str = ""
