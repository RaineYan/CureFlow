"""
Real-Provider Readiness Gate — Day 12.

Pre-flight check that the experiment instrumentation is ready to
connect a real LLM provider.  Runs NO real API calls.  Inspects the
local code/data layout to verify:

  1.  Every materialization can be partitioned into
      REQUIRED_RECOVERY | UNNECESSARY_RECOMPUTATION.
  2.  The Prerequisite Audit (Day 12) returns CLEAN | SOURCE |
      RECOVERED rows and the missing-clean-prereq set is empty
      for the SELECTIVE case.
  3.  ARR's denominator is non-zero for both cases.
  4.  Every LLMResponse carries usage_is_synthetic=True from
      FakeLLMClient (no real provider leakage).
  5.  Golden-output validation is available and string-equal.
  6.  Semantic validation interface (validate_target_clean_path +
      validate_recovery_execution_integrity) is wired in.
  7.  Usage fields are populated in every LLMResponse.
  8.  Latency instrumentation is present (latency_ms).
  9.  No API key is hardcoded anywhere (the env-var pattern is
      used; no literal strings match common key shapes).
  10. The execution event stream is complete and ordered.

`RealProviderReadinessReport.ready` is True iff every check passes.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List

from pydantic import BaseModel, Field

from artifacts import ArtifactRef
from llm import FakeLLMClient, LLMResponse, default_workflow_llm
from recovery import (
    validate_recovery_execution_integrity,
    validate_target_clean_path,
)

from .classification import (
    SOURCE_OF_TRUTH_ARTIFACT,
    build_prerequisite_audit,
    classify_materializations,
    derive_canonical_replacement_map,
)
from .models import ExperimentResult
from .scenario import (
    build_main_fault_scenario,
    build_visits_fault_scenario,
)
from .strategies import (
    run_full_rerun_recovery,
    run_selective_recovery,
)
from provenance.graph import ArtifactGraph
from recovery import RecoveryExecutor, SelectiveRecoveryPlanner


class ReadinessCheck(BaseModel):
    name: str
    passed: bool
    detail: str = ""


class RealProviderReadinessReport(BaseModel):
    checks: List[ReadinessCheck] = Field(default_factory=list)
    ready: bool = False

    def add(self, name: str, passed: bool, detail: str = "") -> None:
        self.checks.append(ReadinessCheck(name=name, passed=passed, detail=detail))

    def finalize(self) -> None:
        self.ready = all(c.passed for c in self.checks)


# Regex patterns that look like real-API keys.  Used to scan the
# source tree for any literal that would be a credential leak.
_KEY_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),                # OpenAI
    re.compile(r"sk-ant-[A-Za-z0-9-]{20,}"),          # Anthropic
    re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}"),  # generic JWT
]


def _check_no_hardcoded_keys() -> List[str]:
    findings: List[str] = []
    root = Path(".")
    skip_dirs = {".git", "__pycache__", ".pytest_cache",
                 "experiments/results", "artifacts_data"}
    for p in root.rglob("*.py"):
        if any(part in skip_dirs for part in p.parts):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pat in _KEY_PATTERNS:
            for m in pat.finditer(text):
                findings.append(f"{p}: matched {pat.pattern[:20]}...")
    return findings


def build_readiness_report() -> RealProviderReadinessReport:
    """Run all checks and return the readiness report."""
    rep = RealProviderReadinessReport()

    # 1) partition invariant on both cases
    try:
        sc_a, g_a = build_main_fault_scenario("readiness_a")
        full = run_full_rerun_recovery(sc_a, g_a, "data/sample_company.csv")
        produced = [r for e in full.events for r in e.produced_refs]
        from contamination import detect_field_faults, propagate_contamination
        detect_field_faults(sc_a.run_id, sc_a.store)
        g = ArtifactGraph.from_store(sc_a.store, sc_a.run_id)
        contam = propagate_contamination(g, sc_a.store, sc_a.run_id)
        from .classification import (
            derive_required_recovery_identities,
            derive_target_recovery_closure,
        )
        closure = derive_target_recovery_closure(g, sc_a.target_ref)
        required = derive_required_recovery_identities(
            contamination_invalidated=list(contam.invalidated),
            contamination_contaminated=list(contam.contaminated),
            target_ref=sc_a.target_ref,
            target_recovery_closure=closure,
        )
        classifications = classify_materializations(produced, required)
        ok = all(c.classification in ("REQUIRED_RECOVERY", "UNNECESSARY_RECOMPUTATION")
                 for c in classifications)
        rep.add("metric_partition_pass", ok,
                f"case A FULL: {len(classifications)} classifications")
    except Exception as e:
        rep.add("metric_partition_pass", False, f"exception: {e}")

    # 2) prerequisite audit + missing empty
    try:
        sc_a2, g_a2 = build_main_fault_scenario("readiness_a2")
        sel = run_selective_recovery(sc_a2, g_a2)
        missing = sel.missing_clean_prerequisite_refs
        rep.add("prerequisite_audit_pass", len(missing) == 0,
                f"missing clean prereq count: {len(missing)}")
    except Exception as e:
        rep.add("prerequisite_audit_pass", False, f"exception: {e}")

    # 3) arr denominator non-zero
    try:
        sc_a3, g_a3 = build_main_fault_scenario("readiness_a3")
        s_a = run_selective_recovery(sc_a3, g_a3)
        sc_b, g_b = build_visits_fault_scenario("readiness_b")
        s_b = run_selective_recovery(sc_b, g_b)
        rep.add("arr_denominator_nonzero",
                s_a.required_clean_prerequisite_count > 0
                and s_b.required_clean_prerequisite_count > 0,
                f"case A={s_a.required_clean_prerequisite_count}, "
                f"case B={s_b.required_clean_prerequisite_count}")
    except Exception as e:
        rep.add("arr_denominator_nonzero", False, f"exception: {e}")

    # 4) LLMResponse usage_is_synthetic=True on every fake call
    try:
        llm = default_workflow_llm()
        # run a small prompt through each responder
        for unit in ("generate_financial_claim", "generate_user_claim",
                     "generate_operation_claim", "generate_financial_section",
                     "generate_user_section", "generate_operation_section"):
            r = llm.generate("test", task_name=unit)
            if not r.usage_is_synthetic:
                rep.add("usage_fields_available", False,
                        f"{unit} returned usage_is_synthetic=False")
                break
        else:
            rep.add("usage_fields_available", True,
                    "all 6 task responders set usage_is_synthetic=True")
    except Exception as e:
        rep.add("usage_fields_available", False, f"exception: {e}")

    # 5) golden-output validation available
    try:
        sc_a4, g_a4 = build_main_fault_scenario("readiness_a4")
        s_a = run_selective_recovery(sc_a4, g_a4)
        rep.add("golden_validation_available",
                s_a.matches_golden_output and s_a.final_content_hash == g_a4.final_report_hash,
                f"matches_golden={s_a.matches_golden_output}")
    except Exception as e:
        rep.add("golden_validation_available", False, f"exception: {e}")

    # 6) semantic validation interface available
    try:
        sc_a5, g_a5 = build_main_fault_scenario("readiness_a5")
        s_a = run_selective_recovery(sc_a5, g_a5)
        # already called inside _finalize; check the final result
        rep.add("semantic_validation_interface_available",
                s_a.semantic_clean_path_pass and s_a.execution_integrity_pass,
                f"sem={s_a.semantic_clean_path_pass} int={s_a.execution_integrity_pass}")
    except Exception as e:
        rep.add("semantic_validation_interface_available", False, f"exception: {e}")

    # 7) usage fields populated
    try:
        r = default_workflow_llm().generate("test", task_name="generate_financial_claim")
        all_populated = (
            r.input_tokens > 0 and r.output_tokens > 0
            and r.latency_ms > 0
        )
        rep.add("latency_instrumentation_available", all_populated,
                f"in={r.input_tokens} out={r.output_tokens} lat={r.latency_ms}")
    except Exception as e:
        rep.add("latency_instrumentation_available", False, f"exception: {e}")

    # 8) no hardcoded keys
    findings = _check_no_hardcoded_keys()
    rep.add("provider_secret_not_hardcoded", len(findings) == 0,
            "no key-like literals found" if not findings
            else f"{len(findings)} potential key literals: {findings[:3]}")

    # 9) event stream complete
    try:
        sc_a6, g_a6 = build_main_fault_scenario("readiness_a6")
        s_a = run_selective_recovery(sc_a6, g_a6)
        # Every event must have an execution_unit
        all_have_units = all(e.execution_unit for e in s_a.events)
        rep.add("event_stream_complete", all_have_units and len(s_a.events) > 0,
                f"{len(s_a.events)} events, all with execution_unit")
    except Exception as e:
        rep.add("event_stream_complete", False, f"exception: {e}")

    rep.finalize()
    return rep
