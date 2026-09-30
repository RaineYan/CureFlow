"""Unified, current-run impact report for the desktop product.

The existing recovery plan covers source-data clues.  This module adds AI
claim and evidence-reference failures to the same graph without upgrading a
rule clue to a confirmed error or a plan to an executed recovery.
"""
from __future__ import annotations

from typing import Any

from .current_recovery import impacted_tool_keys_for_issue_types


def _location(issue: dict[str, Any]) -> str:
    location = issue.get("location") or {}
    cell = str(location.get("cell") or "").strip()
    sheet = str(location.get("sheet") or "").strip()
    if cell:
        return f"{sheet}!{cell}" if sheet else cell
    column = str(location.get("column") or "").strip()
    return column or "未返回具体位置"


def build_current_impact_report(
    *,
    repair_plan: dict[str, Any],
    analysis_tools: dict[str, Any],
    claims: list[dict[str, Any]],
    recovery_plan: dict[str, Any],
    filename: str,
    sheet: str | None,
    input_fingerprint: str,
    recovery_execution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return traceable data/claim/evidence impact paths for one input."""
    tool_entries = {
        str(item.get("key")): item
        for item in analysis_tools.get("executedTools", [])
        if item.get("key")
    }
    available_keys = list(tool_entries)
    issues = list(repair_plan.get("issues") or [])
    repair_summary = repair_plan.get("summary") or {}
    data_clue_count = int(repair_summary.get("issueCount", len(issues)) or 0)

    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, str]] = []
    paths: list[dict[str, Any]] = []
    affected_tools: set[str] = set()
    report_node = "report:final"
    nodes[report_node] = {
        "id": report_node, "kind": "report", "label": "最终报告", "status": "current",
    }

    for index, issue in enumerate(issues[:30]):
        issue_id = str(issue.get("id") or f"issue-{index + 1}")
        node_id = f"data:{issue_id}"
        issue_type = str(issue.get("type") or "unknown")
        location = _location(issue)
        nodes[node_id] = {
            "id": node_id,
            "kind": "data_clue",
            "label": str(issue.get("title") or issue_type),
            "status": "review_required" if not issue.get("autoApplicable") else "safe_fix_candidate",
            "location": location,
            "confirmedError": False,
        }
        impacted, unknown = impacted_tool_keys_for_issue_types({issue_type}, available_keys)
        path_steps = [node_id]
        for key in impacted:
            tool_id = f"tool:{key}"
            affected_tools.add(key)
            tool = tool_entries[key]
            nodes[tool_id] = {
                "id": tool_id,
                "kind": "analysis_tool",
                "label": str(tool.get("label") or key),
                "status": "planned_invalidation",
                "evidenceRef": tool.get("evidenceRef"),
            }
            edges.append({"from": node_id, "to": tool_id, "type": "may_invalidate"})
            edges.append({"from": tool_id, "to": report_node, "type": "feeds"})
            path_steps.append(tool_id)
        if impacted:
            path_steps.append(report_node)
        paths.append({
            "id": f"path:data:{issue_id}",
            "kind": "data",
            "sourceLabel": f"{location} · {issue.get('title') or issue_type}",
            "steps": path_steps,
            "status": "planned_until_confirmed",
            "meaning": (
                "该位置是规则线索；人工确认并修正后，列出的分析工具及报告需要重新验证。"
                + (" 未知问题类型按全部工具失效处理。" if unknown else "")
            ),
        })

    claim_issue_count = 0
    evidence_issue_refs: set[str] = set()
    for index, claim in enumerate(claims):
        grounding = claim.get("grounding") or {}
        verdict = str(grounding.get("verdict") or "unknown")
        if verdict == "grounded":
            continue
        claim_issue_count += 1
        claim_id = f"claim:{index + 1}"
        nodes[claim_id] = {
            "id": claim_id,
            "kind": "ai_claim",
            "label": str(claim.get("content") or "未命名断言"),
            "status": verdict,
            "riskCodes": list(grounding.get("risk_codes") or []),
        }
        edges.append({"from": claim_id, "to": report_node, "type": "included_in"})
        source_nodes: list[str] = []
        for evidence_index, evidence in enumerate(grounding.get("evidence") or []):
            status = str(evidence.get("source_status") or "unknown")
            if status == "verified":
                continue
            ref = str(evidence.get("ref") or f"unresolved-{index + 1}-{evidence_index + 1}")
            evidence_id = f"evidence:{ref}"
            evidence_issue_refs.add(ref)
            nodes[evidence_id] = {
                "id": evidence_id,
                "kind": "evidence_reference",
                "label": ref,
                "status": status,
            }
            edges.append({"from": evidence_id, "to": claim_id, "type": "supports"})
            source_nodes.append(evidence_id)
        if not source_nodes:
            rule_id = f"claim-rule:{index + 1}"
            nodes[rule_id] = {
                "id": rule_id,
                "kind": "claim_rule",
                "label": "断言语义与数字规则",
                "status": "failed_or_review",
            }
            edges.append({"from": rule_id, "to": claim_id, "type": "checks"})
            source_nodes.append(rule_id)
        for source_id in source_nodes:
            paths.append({
                "id": f"path:{source_id}:{claim_id}",
                "kind": "claim_or_evidence",
                "sourceLabel": nodes[source_id]["label"],
                "steps": [source_id, claim_id, report_node],
                "status": "blocked_or_review",
                "meaning": "该证据或断言规则未通过；相应报告陈述不能直接采信，需人工复核或重新生成。",
            })

    reusable_keys = [key for key in available_keys if key not in affected_tools]
    total_problem_count = data_clue_count + claim_issue_count
    if total_problem_count:
        headline = (
            f"发现 {data_clue_count} 项数据线索、{claim_issue_count} 条未通过或待复核断言；"
            "已列出它们可能影响的分析工具和报告。"
        )
    else:
        headline = "当前规则未发现数据、AI 断言或证据引用问题；这不代表结果绝对正确。"

    recovery_executed = recovery_execution is not None
    actual_reuse = bool((recovery_execution or {}).get("reusedToolKeys"))
    return {
        "version": "current_unified_impact_v1",
        "source": {
            "filename": filename,
            "sheet": sheet,
            "inputFingerprint": input_fingerprint,
        },
        "headline": headline,
        "summary": {
            "dataClueCount": data_clue_count,
            "shownDataClueCount": len(issues[:30]),
            "claimIssueCount": claim_issue_count,
            "evidenceIssueCount": len(evidence_issue_refs),
            "affectedToolCount": len(affected_tools),
            "reusableToolCount": len(reusable_keys),
            "pathCount": len(paths),
        },
        "nodes": list(nodes.values()),
        "edges": edges,
        "paths": paths,
        "affectedTools": [
            {"key": key, "label": tool_entries[key].get("label", key)}
            for key in available_keys if key in affected_tools
        ],
        "reusableTools": [
            {"key": key, "label": tool_entries[key].get("label", key)}
            for key in reusable_keys
        ],
        "recoveryPlanStatus": recovery_plan.get("status"),
        "recoveryExecution": recovery_execution,
        "executionBoundary": {
            "dataCluesConfirmedAsErrors": False,
            "impactIsPlan": True,
            "recoveryExecuted": recovery_executed,
            "selectiveRecoveryExecuted": actual_reuse,
            "actualToolReuseRecorded": actual_reuse,
            "actualSavingsClaimed": False,
        },
    }
