"""
Deterministic semantic validation — Day 13 + Day 13.5.

Day 13: per-artifact numeric_consistency (loose, with closest-percentage
fallback) and aggregate-sign direction_check.

Day 13.5:
  - numeric_consistency_check: 1-to-1 expected/observed mapping;
    each required evidence metric MUST have a distinct matching
    percentage in the text.  "If a metric has no number in the
    text" is no longer auto-pass — it FAILs.
  - direction_check: per-metric (per growth evidence).  Each
    growth-metric's expected sign is checked against the text
    near that metric's percentage, not against a global
    "dominant sign".
  - validate_section: numbers are optional (a section is a
    narrative summary) BUT if numbers appear, each must
    1-to-1 match an upstream claim / metric.

This module has no LLM, no network.
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Set, Tuple

from pydantic import BaseModel, Field

from artifacts import Artifact, ArtifactRef, ArtifactStore


# =====================================================================
# Result models
# =====================================================================

class NumericConsistencyResult(BaseModel):
    # Day 13.6: full ArtifactRef string `run_id/artifact_id@vN` so
    # audit JSONs can directly locate run + identity + version.
    # Earlier Day 13.5 used only `<artifact_id>@vN`, which lost
    # the run_id context.
    evidence_ref: str          # e.g. "live_test_a/metric_revenue_growth@v1"
    expected_pct: float
    text_pct: Optional[float] = None
    diff_pp: Optional[float] = None
    within_tolerance: bool
    # Day 13.5: explicit reason for the match outcome.  One of:
    #   MATCHED                  -> text_pct != None, within_tolerance = True
    #   WRONG_NUMERIC_VALUE       -> text_pct != None, within_tolerance = False
    #   MISSING_NUMERIC_MENTION   -> text_pct = None, diff_pp = None
    #   UNSUPPORTED_EXTRA_NUMBER  -> not a per-evidence field; tracked
    #                                separately in `details.numeric_extras`
    match_status: str = "MISSING_NUMERIC_MENTION"


class SemanticValidationResult(BaseModel):
    passed: bool = False
    evidence_valid: bool = False
    numeric_consistency_pass: bool = False
    direction_consistency_pass: bool = False
    unsupported_claims_pass: bool = True   # default True; flipped if issues
    recommendation_boundary_pass: bool = True

    checked_refs: List[ArtifactRef] = Field(default_factory=list)
    issues: List[str] = Field(default_factory=list)
    details: Dict = Field(default_factory=dict)


# =====================================================================
# Recommendation language
# =====================================================================

_RECOMMENDATION_TOKENS = (
    "建议", "应当", "应该", "推荐", "需要采取", "必须采取", "采取",
    "consider", "should", "recommend", "we suggest", "it is advisable",
    "ought to", "must adopt",
)


# =====================================================================
# Direction vocabulary
# =====================================================================

_POSITIVE_TOKENS = (
    "增长", "上升", "提高", "增加", "扩大", "改善", "提升", "盈利",
    "increase", "grew", "growing", "grew by", "rose", "risen",
    "improved", "up by", "gains",
)
_NEGATIVE_TOKENS = (
    "下降", "减少", "降低", "下降至", "萎缩", "下滑", "亏损", "负增长",
    "decrease", "decreased", "declined", "fell", "dropped", "down by",
    "loss", "losses", "shrink",
)


# =====================================================================
# Growth-metric identification
# =====================================================================
# A growth metric is one whose semantics imply "+/- of some quantity"
# rather than a static ratio/margin.  For the POC we identify by
# artifact_id substring.  Adding a new growth metric means adding it
# here AND verifying direction_check handles it correctly.

_GROWTH_METRIC_KEYWORDS = (
    "growth",        # revenue_growth, order_growth, user_growth
    "_increase",
    "_decrease",
)


def _is_growth_metric(artifact_id: str) -> bool:
    return any(k in artifact_id for k in _GROWTH_METRIC_KEYWORDS)


# =====================================================================
# Evidence ref parsing
# =====================================================================

_REF_STRING_RE = re.compile(
    r"([A-Za-z0-9_\-]+)/([A-Za-z0-9_\-]+)@v(\d+)"
)


def parse_evidence_refs(text: str) -> List[ArtifactRef]:
    """Extract every `<run_id>/<artifact_id>@v<version>` token from
    `text`.  Order preserved; duplicates removed (stable)."""
    seen: Set[Tuple[str, str, int]] = set()
    out: List[ArtifactRef] = []
    for m in _REF_STRING_RE.finditer(text):
        run_id, artifact_id, ver = m.group(1), m.group(2), int(m.group(3))
        key = (run_id, artifact_id, ver)
        if key in seen:
            continue
        seen.add(key)
        out.append(ArtifactRef(
            run_id=run_id, artifact_id=artifact_id, version=ver,
        ))
    return out


# =====================================================================
# Sub-checks
# =====================================================================

def recommendation_boundary_check(text: str) -> Tuple[bool, List[str]]:
    """True iff `text` contains no recommendation / decision language.
    Both Chinese and English tokens are checked (case-insensitive)."""
    issues: List[str] = []
    lowered = text.lower()
    for tok in _RECOMMENDATION_TOKENS:
        if tok in text or tok.lower() in lowered:
            issues.append(f"recommendation token found: {tok!r}")
    return (len(issues) == 0), issues


# ---------------------------------------------------------------------
# Numeric consistency — Day 13.5: 1-to-1, required
# ---------------------------------------------------------------------

_PCT_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*%")
# match a percentage AND its character index, so we can find nearest-percentage
_PCT_WITH_POS = re.compile(r"(-?\d+(?:\.\d+)?)\s*%")


def _percent_matches_with_positions(text: str) -> List[Tuple[float, int]]:
    """Return [(value, char_index), ...] for each percentage in text."""
    out: List[Tuple[float, int]] = []
    for m in _PCT_WITH_POS.finditer(text):
        out.append((float(m.group(1)), m.start()))
    return out


def numeric_consistency_check(
    text: str,
    evidence: List[Artifact],
    *,
    tolerance_pp: float = 0.5,
    allow_no_numbers: bool = False,
) -> Tuple[bool, List[NumericConsistencyResult], List[str], List[dict]]:
    """Day 13.5 (post-live-gate-fix): 1-to-1 numeric match with
    explicit failure-type tags and a 4th return value for
    UNSUPPORTED_EXTRA_NUMBER records.

    For each evidence Artifact, compute expected_pct = value * 100.
    Each per-evidence outcome is one of:
      - `MATCHED`                  -> text_pct present, within_tolerance=True
      - `WRONG_NUMERIC_VALUE`       -> text_pct present, within_tolerance=False
      - `MISSING_NUMERIC_MENTION`   -> text_pct = None, diff_pp = None

    Per-evidence `text_pct` and `diff_pp` are `None` whenever there
    is no observed percentage to compare against.  This is the fix
    for the live-gate bug where the previous code wrote
    `text_pct=0.0, diff_pp=0.0` to mask missing numbers — that
    misrepresented the audit (and the `within_tolerance=False` flag
    was silently lying).

    The 4th return value (`extras`) is a list of dicts, one per
    text percentage that could not be matched to any expected
    metric, tagged with `match_status="UNSUPPORTED_EXTRA_NUMBER"`.

    Returns:
      (passed, results, issues, extras)
    """
    if not evidence:
        return True, [], [], []

    # 1) per-evidence expected pct
    expected: List[Tuple[Artifact, float]] = []
    for a in evidence:
        try:
            expected.append((a, float(a.value) * 100.0))
        except (TypeError, ValueError):
            # non-numeric evidence; numeric check is N/A.
            continue

    if not expected:
        return True, [], [], []

    text_pcts = _percent_matches_with_positions(text)
    if not text_pcts:
        if allow_no_numbers:
            return True, [], [], []
        # Claim MUST mention each metric.  Bugfix: text_pct and
        # diff_pp are None, NOT 0.0.  Each issue MUST also carry
        # the `MISSING_NUMERIC_MENTION` tag so downstream tests /
        # audit scripts can reliably filter on the failure type.
        issues = [
            f"Required evidence metric "
            f"{a.run_id}/{a.artifact_id}@v{a.version} "
            f"not numerically represented in text (no percentages "
            f"found) (MISSING_NUMERIC_MENTION)"
            for a, _ in expected
        ]
        results = [
            NumericConsistencyResult(
                evidence_ref=f"{a.run_id}/{a.artifact_id}@v{a.version}",
                expected_pct=ep,
                text_pct=None,
                diff_pp=None,
                within_tolerance=False,
                match_status="MISSING_NUMERIC_MENTION",
            )
            for a, ep in expected
        ]
        return False, results, issues, []

    # 2) Exhaustive 1-to-1 assignment.
    n = len(expected)
    m = len(text_pcts)

    from itertools import permutations, combinations

    best = None
    if m >= n:
        for perm in permutations(range(m), n):
            unmatched_e = 0
            unmatched_t = m - n
            sum_abs = 0.0
            assignment: List[Optional[int]] = []
            for i, (a, ep) in enumerate(expected):
                j = perm[i]
                tp = text_pcts[j][0]
                diff = tp - ep
                if abs(diff) > tolerance_pp:
                    unmatched_e += 1
                else:
                    sum_abs += abs(diff)
                assignment.append(j)
            score = (unmatched_e, unmatched_t, sum_abs)
            if best is None or score < best[0]:
                best = (score, assignment, unmatched_e, unmatched_t)
        _, assignment, unmatched_e, unmatched_t = best
    else:
        for evidence_indices in combinations(range(n), m):
            for text_perm in permutations(range(m), m):
                full_assignment: List[Optional[int]] = [None] * n
                matched_in_tolerance = 0
                sum_abs = 0.0
                for k, ei in enumerate(evidence_indices):
                    tp = text_pcts[text_perm[k]][0]
                    ep = expected[ei][1]
                    if abs(tp - ep) <= tolerance_pp:
                        matched_in_tolerance += 1
                        sum_abs += abs(tp - ep)
                        full_assignment[ei] = text_perm[k]
                unmatched_e = (n - m) + (m - matched_in_tolerance)
                unmatched_t = 0
                score = (unmatched_e, unmatched_t, sum_abs)
                if best is None or score < best[0]:
                    best = (score, full_assignment, unmatched_e, unmatched_t)
        _, assignment, unmatched_e, unmatched_t = best

    # 3) Build per-evidence results and issues.
    # Bugfix: when the evidence is unmatched, text_pct and diff_pp
    # are `None` (NOT 0.0).  Day 13.6: evidence_ref uses the full
    # `<run_id>/<artifact_id>@v<version>` string.
    results: List[NumericConsistencyResult] = []
    issues: List[str] = []
    for i, (a, ep) in enumerate(expected):
        j = assignment[i]
        full_ref = f"{a.run_id}/{a.artifact_id}@v{a.version}"
        if j is None:
            results.append(NumericConsistencyResult(
                evidence_ref=full_ref,
                expected_pct=ep,
                text_pct=None,
                diff_pp=None,
                within_tolerance=False,
                match_status="MISSING_NUMERIC_MENTION",
            ))
            issues.append(
                f"Required evidence metric {full_ref} "
                f"(expected ≈ {ep:.2f}%) has no matching percentage "
                f"in text (MISSING_NUMERIC_MENTION)"
            )
        else:
            tp = text_pcts[j][0]
            diff = tp - ep
            within = abs(diff) <= tolerance_pp
            results.append(NumericConsistencyResult(
                evidence_ref=full_ref,
                expected_pct=ep, text_pct=tp, diff_pp=diff,
                within_tolerance=within,
                match_status="MATCHED" if within else "WRONG_NUMERIC_VALUE",
            ))
            if not within:
                issues.append(
                    f"numeric mismatch (WRONG_NUMERIC_VALUE) for "
                    f"{full_ref}: "
                    f"expected ≈ {ep:.2f}%, text has ≈ {tp:.2f}% "
                    f"(diff {diff:+.2f}pp > tolerance {tolerance_pp}pp)"
                )

    # 4) Unmatched text percentages -> UNSUPPORTED_EXTRA_NUMBER
    used_text_indices = {j for j in assignment if j is not None}
    extras: List[dict] = []
    for j, (tp, _pos) in enumerate(text_pcts):
        if j not in used_text_indices:
            extras.append({
                "text_pct": tp,
                "match_status": "UNSUPPORTED_EXTRA_NUMBER",
            })
            issues.append(
                f"text contains unmatched percentage {tp:.2f}% "
                f"not grounded in any evidence (UNSUPPORTED_EXTRA_NUMBER)"
            )

    passed = (unmatched_e == 0 and unmatched_t == 0)
    return passed, results, issues, extras


def _choose_indices(n: int, m: int):
    """Yield every m-subset of range(n), when n > m."""
    from itertools import combinations
    yield from combinations(range(n), m)


# ---------------------------------------------------------------------
# Direction check — Day 13.5: per-metric
# ---------------------------------------------------------------------

def _sign(v) -> int:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0
    if f > 0:
        return 1
    if f < 0:
        return -1
    return 0


def _nearest_text_window(
    text: str, pos: int, window: int = 30,
) -> str:
    """Return the substring of `text` within `window` characters
    on either side of `pos` (clipped at boundaries)."""
    start = max(0, pos - window)
    end = min(len(text), pos + window)
    return text[start:end]


def _window_direction_sign(window: str) -> int:
    """Return the dominant direction sign within `window`:
    +1 if more positive tokens, -1 if more negative, 0 if tied/empty."""
    pos = sum(1 for tok in _POSITIVE_TOKENS if tok in window)
    neg = sum(1 for tok in _NEGATIVE_TOKENS if tok in window)
    if pos == 0 and neg == 0:
        return 0
    if pos > neg:
        return 1
    if neg > pos:
        return -1
    return 0


def direction_check(
    text: str, evidence: List[Artifact],
) -> Tuple[bool, List[str]]:
    """Day 13.5: per-metric direction consistency.

    For each GROWTH-metric evidence (identified by artifact_id
    keyword), we look for the closest text percentage and
    examine the surrounding 30-character window.  If the
    window's dominant direction sign disagrees with the
    evidence's sign, that's a failure.

    Non-growth evidence (ratios, margins) is NOT direction-
    checked; ratios themselves are directionless (47% is 47%
    whether revenue grew or shrank).  Day 13.5 POC scope.

    Sections that omit numbers have no per-metric check; the
    numeric_consistency_check is responsible for "if a number
    appears it must be grounded".  So if no growth metric has
    a number, we pass.
    """
    issues: List[str] = []
    if not evidence:
        return True, issues

    text_pcts = _percent_matches_with_positions(text)
    growth_evidence = [
        a for a in evidence if _is_growth_metric(a.artifact_id)
    ]
    if not growth_evidence:
        return True, issues

    for a in growth_evidence:
        ev_sign = _sign(a.value)
        if ev_sign == 0:
            continue
        # Find nearest text percentage to any mention of this
        # metric.  If text has no percentages, the per-metric
        # direction check has no data — we pass (the numeric
        # check would have already raised an issue if required).
        if not text_pcts:
            continue
        # If the metric name appears in text, anchor on the
        # nearest percentage to that mention.  Otherwise
        # anchor on the first percentage.
        anchor_pos: Optional[int] = None
        metric_label = a.artifact_id
        for m in re.finditer(re.escape(metric_label), text):
            anchor_pos = m.start()
            break
        if anchor_pos is None:
            anchor_pos = text_pcts[0][1]
        nearest = min(text_pcts, key=lambda pp: abs(pp[1] - anchor_pos))
        window = _nearest_text_window(text, nearest[1], window=30)
        win_sign = _window_direction_sign(window)
        if win_sign == 0:
            # No direction words near the number; pass.
            continue
        if win_sign != ev_sign:
            issues.append(
                f"per-metric direction contradiction for "
                f"{a.artifact_id}@v{a.version}: evidence is "
                f"{'positive' if ev_sign > 0 else 'negative'} "
                f"but text near {nearest[0]:.2f}% uses "
                f"{'negative' if win_sign < 0 else 'positive'}-"
                f"direction words"
            )
    return (len(issues) == 0), issues


# =====================================================================
# Per-claim / per-section validators
# =====================================================================

def _resolve_evidence_refs(
    evidence_refs: List[ArtifactRef], store: ArtifactStore,
) -> Tuple[List[Artifact], List[str]]:
    """Resolve evidence refs to actual Artifact objects.  Returns
    (artifacts, issues).  If a ref doesn't exist, that is an
    issue (no auto-snap to latest)."""
    artifacts: List[Artifact] = []
    issues: List[str] = []
    for r in evidence_refs:
        a = store.get_version(r.run_id, r.artifact_id, r.version)
        if a is None:
            issues.append(
                f"evidence ref not found: {r.run_id}/{r.artifact_id}@v{r.version}"
            )
            continue
        artifacts.append(a)
    return artifacts, issues


def _expected_evidence_identities(
    evidence_policy: str,
) -> Set[str]:
    """The set of metric / claim identities that SHOULD appear in
    the evidence refs for a given branch.  Used to verify the
    response didn't silently drop an evidence ref."""
    policy = {
        "financial_claim": {"metric_revenue_growth", "metric_profit_margin"},
        "financial_section": {"claim_financial_growth"},
        "user_claim": {"metric_user_growth"},
        "user_section": {"claim_user_growth"},
        "operation_claim": {"metric_conversion_rate", "metric_order_growth"},
        "operation_section": {"claim_operation"},
    }
    return policy.get(evidence_policy, set())


def validate_claim(
    *,
    claim_text: str,
    evidence_refs_in_text: List[ArtifactRef],
    store: ArtifactStore,
    evidence_policy: str,
    extra_input_ids: Optional[Iterable[str]] = None,
) -> SemanticValidationResult:
    """Validate a Claim artifact's content.  evidence_policy is one
    of the keys in _expected_evidence_identities.  extra_input_ids
    are the identities that the executor ALSO consumed (e.g.
    cost_ratio for the financial claim) — these are execution
    prerequisites but NOT required evidence.  A reference to them
    in evidence_refs is allowed but not required."""
    result = SemanticValidationResult()
    issues: List[str] = []

    # 1) evidence refs parse + exist
    artifacts, ev_issues = _resolve_evidence_refs(evidence_refs_in_text, store)
    result.checked_refs = list(evidence_refs_in_text)
    if ev_issues:
        issues.extend(ev_issues)
    result.evidence_valid = (len(ev_issues) == 0)

    # 2) evidence policy: must include all expected identities,
    #    may include execution-prerequisite identities.  A policy
    #    violation flips evidence_valid to False (so the overall
    #    `passed` becomes False).
    expected = _expected_evidence_identities(evidence_policy)
    seen_ids = {a.artifact_id for a in artifacts}
    missing = expected - seen_ids
    if missing:
        result.evidence_valid = False
        issues.append(
            f"evidence policy {evidence_policy!r} requires {sorted(missing)} "
            f"but response has {sorted(seen_ids)}"
        )

    # 3) numeric consistency — Day 13.5: 1-to-1, required.
    #    Claims MUST mention each required evidence metric with
    #    a matching number.  Day 13.5 follow-up: the function now
    #    returns a 4-tuple (passed, results, issues, extras) so
    #    UNSUPPORTED_EXTRA_NUMBER records are surfaced in the
    #    audit details.
    if artifacts:
        num_ok, num_results, num_issues, num_extras = (
            numeric_consistency_check(
                claim_text, artifacts, allow_no_numbers=False,
            )
        )
        result.numeric_consistency_pass = num_ok
        result.details["numeric"] = [r.model_dump() for r in num_results]
        result.details["numeric_extras"] = list(num_extras)
        issues.extend(num_issues)

    # 4) direction consistency — Day 13.5: per-metric.
    dir_ok, dir_issues = direction_check(claim_text, artifacts)
    result.direction_consistency_pass = dir_ok
    issues.extend(dir_issues)

    # 5) recommendation boundary
    rb_ok, rb_issues = recommendation_boundary_check(claim_text)
    result.recommendation_boundary_pass = rb_ok
    issues.extend(rb_issues)

    result.passed = (
        result.evidence_valid
        and result.numeric_consistency_pass
        and result.direction_consistency_pass
        and result.unsupported_claims_pass
        and result.recommendation_boundary_pass
    )
    result.issues = issues
    return result


def validate_section(
    *,
    section_text: str,
    section_evidence_refs: List[ArtifactRef],
    claim_evidence_refs: List[ArtifactRef],
    store: ArtifactStore,
    evidence_policy: str,
) -> SemanticValidationResult:
    """Validate a Section artifact.  Sections cite the upstream
    Claim as their evidence (NOT the underlying metrics).

    Day 13.5 rule: a section MAY omit numbers (it is a
    narrative summary).  But if a number appears, each must
    1-to-1 match a number in the upstream Claim's evidence
    chain.
    """
    result = SemanticValidationResult()
    issues: List[str] = []

    # 1) section's own evidence refs (the upstream Claim)
    artifacts, ev_issues = _resolve_evidence_refs(section_evidence_refs, store)
    result.checked_refs = list(section_evidence_refs)
    if ev_issues:
        issues.extend(ev_issues)
    result.evidence_valid = (len(ev_issues) == 0)

    # 2) section's evidence must include the upstream claim ref
    expected = _expected_evidence_identities(evidence_policy)
    seen_ids = {a.artifact_id for a in artifacts}
    if expected and not (expected & seen_ids):
        result.evidence_valid = False
        issues.append(
            f"section evidence policy {evidence_policy!r} requires at "
            f"least one of {sorted(expected)} but section cites {sorted(seen_ids)}"
        )

    # 3) numeric consistency — Day 13.5: section may omit numbers,
    #    but if numbers appear they must 1-to-1 match the upstream
    #    claim's metrics.
    claim_artifacts, _ = _resolve_evidence_refs(claim_evidence_refs, store)
    if claim_artifacts:
        num_ok, num_results, num_issues, num_extras = (
            numeric_consistency_check(
                section_text, claim_artifacts, allow_no_numbers=True,
            )
        )
        result.numeric_consistency_pass = num_ok
        result.details["numeric"] = [r.model_dump() for r in num_results]
        result.details["numeric_extras"] = list(num_extras)
        issues.extend(num_issues)
    else:
        result.numeric_consistency_pass = True

    # 4) direction consistency (same per-metric check)
    dir_ok, dir_issues = direction_check(section_text, claim_artifacts)
    result.direction_consistency_pass = dir_ok
    issues.extend(dir_issues)

    # 5) recommendation boundary
    rb_ok, rb_issues = recommendation_boundary_check(section_text)
    result.recommendation_boundary_pass = rb_ok
    issues.extend(rb_issues)

    result.passed = (
        result.evidence_valid
        and result.numeric_consistency_pass
        and result.direction_consistency_pass
        and result.unsupported_claims_pass
        and result.recommendation_boundary_pass
    )
    result.issues = issues
    return result
