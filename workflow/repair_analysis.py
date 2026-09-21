"""Deterministic, review-first repair planning for tabular uploads.

The planner never mutates a source file.  It identifies exact cells, separates
safe normalization from judgment calls, and emits a stable plan that the UI
can preview before a corrected copy is exported.
"""
from __future__ import annotations

import csv
import hashlib
import math
import re
from collections import defaultdict
from io import StringIO
from typing import Any, Iterable


MAX_REPAIR_ISSUES = 250
FORMULA_ERRORS = {"#DIV/0!", "#N/A", "#NAME?", "#NULL!", "#NUM!", "#REF!", "#VALUE!"}
MANUAL_REPLACEABLE_TYPES = {
    "formula_error",
    "missing_value",
    "score_out_of_range",
    "non_positive_count",
    "rank_out_of_range",
    "statistical_outlier",
}


def _normalized_header(value: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value.strip().lower())


def _column_letter(index: int) -> str:
    value = index + 1
    output = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        output = chr(65 + remainder) + output
    return output


def _number(value: str) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _is_identifier(header: str) -> bool:
    normalized = _normalized_header(header)
    return normalized in {"id", "编号", "序号", "学号", "工号", "证件号"} or normalized.endswith("id") or "学号" in normalized


def _is_score(header: str) -> bool:
    normalized = _normalized_header(header)
    return any(token in normalized for token in ("成绩", "得分", "分数", "score"))


def _is_rank(header: str) -> bool:
    normalized = _normalized_header(header)
    return any(token in normalized for token in ("排名", "名次", "rank"))


def _is_count(header: str) -> bool:
    normalized = _normalized_header(header)
    return any(token in normalized for token in ("总人数", "人数", "数量", "count"))


def _quantile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def _pair_rank_totals(headers: list[str]) -> dict[str, str]:
    totals = [header for header in headers if _is_count(header)]
    pairs: dict[str, str] = {}
    for rank in (header for header in headers if _is_rank(header)):
        rank_key = _normalized_header(rank).replace("排名", "").replace("名次", "").replace("rank", "")
        scored = []
        for total in totals:
            total_key = _normalized_header(total).replace("总人数", "").replace("人数", "").replace("数量", "").replace("count", "")
            common = len(os_prefix(rank_key, total_key))
            scored.append((common, total))
        if scored:
            best_common, best_total = max(scored)
            if best_common > 0 or len(totals) == 1:
                pairs[rank] = best_total
    return pairs


def os_prefix(left: str, right: str) -> str:
    """Return the common prefix without importing a platform path helper."""
    length = 0
    for a, b in zip(left, right):
        if a != b:
            break
        length += 1
    return left[:length]


def _issue_id(issue_type: str, sheet: str | None, cell: str, original: str) -> str:
    raw = f"{issue_type}\0{sheet or ''}\0{cell}\0{original}"
    return "fix_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def build_repair_plan(
    csv_text: str,
    *,
    filename: str,
    sheet: str | None = None,
    source_row_numbers: Iterable[int] | None = None,
) -> dict[str, Any]:
    reader = csv.DictReader(StringIO(csv_text))
    headers = list(reader.fieldnames or [])
    rows = list(reader)
    supplied_rows = list(source_row_numbers or [])
    row_numbers = supplied_rows if len(supplied_rows) == len(rows) else list(range(2, len(rows) + 2))
    issue_count = 0
    safe_fix_count = 0
    review_count = 0
    affected_rows: set[int] = set()
    issues: list[dict[str, Any]] = []

    def add_issue(
        *, issue_type: str, title: str, row_index: int, column: str,
        original: str, proposed: str | None, severity: str,
        auto_applicable: bool, reason: str, evidence: dict[str, Any] | None = None,
    ) -> None:
        nonlocal issue_count, safe_fix_count, review_count
        source_row = row_numbers[row_index]
        column_index = headers.index(column)
        cell = f"{_column_letter(column_index)}{source_row}"
        issue_count += 1
        safe_fix_count += int(auto_applicable)
        review_count += int(not auto_applicable)
        affected_rows.add(source_row)
        if len(issues) >= MAX_REPAIR_ISSUES:
            return
        issues.append({
            "id": _issue_id(issue_type, sheet, cell, original),
            "type": issue_type,
            "title": title,
            "severity": severity,
            "location": {
                "sheet": sheet,
                "row": source_row,
                "column": column,
                "cell": cell,
                "dataIndex": row_index,
            },
            "originalValue": original,
            "proposedValue": proposed,
            "autoApplicable": auto_applicable,
            "action": "replace_cell" if auto_applicable else "manual_review",
            "reason": reason,
            "evidence": evidence or {},
        })

    non_empty_by_column = {
        header: sum(bool(str(row.get(header, "")).strip()) for row in rows)
        for header in headers
    }
    for row_index, row in enumerate(rows):
        for header in headers:
            raw = "" if row.get(header) is None else str(row.get(header))
            stripped = raw.strip()
            if raw and stripped != raw:
                add_issue(
                    issue_type="trim_whitespace", title="单元格包含首尾空格",
                    row_index=row_index, column=header, original=raw, proposed=stripped,
                    severity="low", auto_applicable=True,
                    reason="仅移除首尾空格，不改变单元格的有效字符。",
                )
            if stripped.upper() in FORMULA_ERRORS:
                add_issue(
                    issue_type="formula_error", title="公式或引用错误",
                    row_index=row_index, column=header, original=raw, proposed=None,
                    severity="high", auto_applicable=False,
                    reason="错误值需要回到公式和上游引用核对，不能由模型猜测替换。",
                )
            if not stripped and rows and non_empty_by_column[header] / len(rows) >= 0.5:
                add_issue(
                    issue_type="missing_value", title="主要字段存在缺失值",
                    row_index=row_index, column=header, original="", proposed=None,
                    severity="medium", auto_applicable=False,
                    reason="该字段在多数记录中有值，但当前单元格为空；缺失值没有可靠依据时不得自动填充。",
                    evidence={"columnNonEmptyRate": round(non_empty_by_column[header] / len(rows), 4)},
                )
            number = _number(stripped) if stripped else None
            if number is not None and _is_score(header) and not 0 <= number <= 100:
                add_issue(
                    issue_type="score_out_of_range", title="成绩超出常用 0–100 范围",
                    row_index=row_index, column=header, original=raw, proposed=None,
                    severity="high", auto_applicable=False,
                    reason="范围异常可能来自小数点、单位或录入错误，必须结合原始记录人工判断。",
                    evidence={"expectedRange": [0, 100]},
                )
            if number is not None and _is_count(header) and number <= 0:
                add_issue(
                    issue_type="non_positive_count", title="计数字段不是正数",
                    row_index=row_index, column=header, original=raw, proposed=None,
                    severity="high", auto_applicable=False,
                    reason="人数或数量应结合业务定义核对，系统不会自动改写。",
                )

    seen_rows: dict[tuple[str, ...], int] = {}
    for row_index, row in enumerate(rows):
        signature = tuple(str(row.get(header, "")).strip() for header in headers)
        if signature in seen_rows and headers:
            add_issue(
                issue_type="duplicate_row", title="发现完全重复记录",
                row_index=row_index, column=headers[0], original=str(row.get(headers[0], "")), proposed=None,
                severity="medium", auto_applicable=False,
                reason="删除记录会改变数据规模；需确认不是合法重复后再处理。",
                evidence={"duplicatesSourceRow": row_numbers[seen_rows[signature]]},
            )
        else:
            seen_rows[signature] = row_index

    for rank_header, total_header in _pair_rank_totals(headers).items():
        for row_index, row in enumerate(rows):
            rank = _number(str(row.get(rank_header, "")).strip())
            total = _number(str(row.get(total_header, "")).strip())
            if rank is not None and total is not None and (rank < 1 or rank > total):
                add_issue(
                    issue_type="rank_out_of_range", title="排名超出总人数范围",
                    row_index=row_index, column=rank_header,
                    original=str(row.get(rank_header, "")), proposed=None,
                    severity="high", auto_applicable=False,
                    reason="排名必须位于 1 与对应总人数之间；需核对排名或总人数来源。",
                    evidence={"totalColumn": total_header, "totalValue": total},
                )

    for header in headers:
        if _is_identifier(header) or _is_rank(header) or _is_count(header):
            continue
        numeric = [
            (index, parsed)
            for index, row in enumerate(rows)
            if (parsed := _number(str(row.get(header, "")).strip())) is not None
        ]
        if len(numeric) < 8:
            continue
        values = [value for _, value in numeric]
        q1, q3 = _quantile(values, 0.25), _quantile(values, 0.75)
        iqr = q3 - q1
        if iqr <= 0:
            continue
        lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        for row_index, value in numeric:
            if value < lower or value > upper:
                add_issue(
                    issue_type="statistical_outlier", title="IQR 统计异常值",
                    row_index=row_index, column=header,
                    original=str(rows[row_index].get(header, "")), proposed=None,
                    severity="medium", auto_applicable=False,
                    reason="统计异常不等于数据错误，只作为人工复核线索，不自动删除或改值。",
                    evidence={"lowerBound": lower, "upperBound": upper, "method": "IQR_1.5"},
                )

    return {
        "version": "repair_plan_v1",
        "source": {
            "filename": filename,
            "sheet": sheet,
            "rowCount": len(rows),
            "columnCount": len(headers),
        },
        "summary": {
            "issueCount": issue_count,
            "safeFixCount": safe_fix_count,
            "reviewCount": review_count,
            "affectedRows": len(affected_rows),
            "shownIssueCount": len(issues),
            "truncated": issue_count > len(issues),
        },
        "issues": issues,
        "policy": {
            "sourceFileModified": False,
            "automaticChangesRequireApproval": True,
            "manualReviewNeverAutoApplied": True,
        },
    }


def approved_safe_fixes(plan: dict[str, Any], approved_ids: Iterable[str]) -> list[dict[str, Any]]:
    requested = list(dict.fromkeys(str(item) for item in approved_ids))
    by_id = {item["id"]: item for item in plan.get("issues", [])}
    unknown = [item for item in requested if item not in by_id]
    if unknown:
        raise ValueError("修正项不存在或不属于当前文件")
    unsafe = [item for item in requested if not by_id[item].get("autoApplicable")]
    if unsafe:
        raise ValueError("人工复核项不能自动应用")
    return [by_id[item] for item in requested]


def approved_manual_corrections(
    plan: dict[str, Any],
    corrections: Any,
    *,
    confirmed: bool,
) -> list[dict[str, Any]]:
    """Validate exact user-entered replacements without inferring a value.

    Deleting duplicate rows is deliberately excluded because it changes the
    dataset grain. Formula entry is also rejected here to avoid turning an
    untrusted text field into an executable spreadsheet formula.
    """
    if corrections in (None, []):
        return []
    if not isinstance(corrections, list):
        raise ValueError("人工修正必须是列表")
    if not confirmed:
        raise ValueError("应用人工修正前必须明确确认")
    by_id = {item["id"]: item for item in plan.get("issues", [])}
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in corrections:
        if not isinstance(entry, dict):
            raise ValueError("人工修正项格式无效")
        issue_id = str(entry.get("issueId") or "")
        if not issue_id or issue_id in seen:
            raise ValueError("人工修正项缺少唯一问题编号或存在重复")
        issue = by_id.get(issue_id)
        if issue is None:
            raise ValueError("人工修正项不存在或不属于当前文件")
        if issue.get("autoApplicable") or issue.get("type") not in MANUAL_REPLACEABLE_TYPES:
            raise ValueError("该问题不能通过单元格替换处理")
        value = entry.get("newValue")
        if isinstance(value, (dict, list)):
            raise ValueError("人工修正值必须是单个值")
        normalized = "" if value is None else str(value)
        if len(normalized) > 5000:
            raise ValueError("人工修正值过长")
        if normalized.lstrip().startswith("="):
            raise ValueError("为避免公式注入，界面暂不写入公式；请在 Excel 中核对公式")
        approved = dict(issue)
        approved["proposedValue"] = normalized
        approved["action"] = "replace_cell_confirmed"
        approved["userConfirmed"] = True
        output.append(approved)
        seen.add(issue_id)
    return output


def apply_safe_fixes_to_csv(csv_text: str, fixes: list[dict[str, Any]]) -> str:
    return apply_confirmed_fixes_to_csv(csv_text, fixes)


def apply_confirmed_fixes_to_csv(csv_text: str, fixes: list[dict[str, Any]]) -> str:
    """Apply already validated safe or manual cell replacements to a copy."""
    reader = csv.DictReader(StringIO(csv_text))
    headers = list(reader.fieldnames or [])
    rows = list(reader)
    for fix in fixes:
        location = fix["location"]
        row_index = int(location["dataIndex"])
        column = str(location["column"])
        if not 0 <= row_index < len(rows) or column not in headers:
            raise ValueError("修正位置已失效，请重新分析文件")
        if str(rows[row_index].get(column, "")) != str(fix["originalValue"]):
            raise ValueError("原始值已变化，请重新分析文件")
        rows[row_index][column] = str(fix["proposedValue"])
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=headers, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()
