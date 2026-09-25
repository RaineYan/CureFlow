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
# These are conservative product guards, not universal statistical cutoffs.
MIN_IQR_VALUES = 30
MIN_IQR_DISTINCT = 8
MAX_IQR_EXTREME_RATE = 0.03
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


def is_derived_rate(header: str) -> bool:
    """A rate needs its numerator, denominator and comparison basis to be audited."""
    normalized = _normalized_header(header)
    return any(token in normalized for token in (
        "率", "百分比", "占比", "比率", "涨幅", "跌幅", "增长比",
        "rate", "ratio", "growthpct", "growthpercent", "percentchange",
    ))


def _is_time(header: str) -> bool:
    normalized = _normalized_header(header)
    if any(token in normalized for token in ("时间", "日期", "时刻")):
        return True
    return normalized in {"time", "date", "timestamp", "datetime"} or normalized.endswith((
        "timestamp", "datetime", "eventtime", "sampletime", "recordtime",
        "measurementtime", "observationtime", "eventdate", "recorddate",
    ))


def _is_group(header: str) -> bool:
    normalized = _normalized_header(header)
    return any(token in normalized for token in (
        "分组", "组别", "阶段", "数据集", "工况", "批次", "类别", "场景",
        "group", "cohort", "phase", "batch", "class",
    ))


def _structured_baseline(
    headers: list[str], rows: list[dict[str, str]],
    analysis_config: dict[str, Any] | None = None,
) -> str | None:
    """Find an explicit reason not to compare every row with one global fence."""
    if len(rows) < MIN_IQR_VALUES:
        return None
    roles = analysis_config if isinstance(analysis_config, dict) else {}
    for role, label in (("timeColumn", "时间"), ("groupColumn", "分组")):
        chosen = roles.get(role)
        if isinstance(chosen, str) and chosen in headers:
            return f"本次已指定{label}字段“{chosen}”，不能把不同{label}位置混在一个全列 IQR 阈值中"
    for header in headers:
        if not (_is_time(header) or _is_group(header)):
            continue
        nonempty = [str(row.get(header, "") or "").strip() for row in rows]
        nonempty = [value for value in nonempty if value]
        if len(nonempty) < 0.8 * len(rows):
            continue
        distinct = len(set(nonempty))
        if _is_time(header) and distinct >= min(10, len(rows) // 2):
            return f"存在时间字段“{header}”，全列阈值可能把正常的阶段变化误判为异常"
        if _is_group(header) and 2 <= distinct <= min(20, max(2, len(rows) // 5)):
            return f"存在分组字段“{header}”，应先按组比较，不能混用一个全列阈值"
    return None


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
    analysis_config: dict[str, Any] | None = None,
    issue_offset: int = 0,
    issue_limit: int = MAX_REPAIR_ISSUES,
    issue_type_filter: str | None = None,
    column_filter: str | None = None,
    include_issue_ids: Iterable[str] = (),
) -> dict[str, Any]:
    if issue_offset < 0 or not 1 <= issue_limit <= MAX_REPAIR_ISSUES:
        raise ValueError("问题分页范围无效")
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
    counts_by_type: dict[str, int] = defaultdict(int)
    counts_by_column: dict[str, int] = defaultdict(int)
    iqr_screening: list[dict[str, Any]] = []
    matching_issue_count = 0
    requested_ids = {str(item) for item in include_issue_ids}

    def add_issue(
        *, issue_type: str, title: str, row_index: int, column: str,
        original: str, proposed: str | None, severity: str,
        auto_applicable: bool, reason: str, evidence: dict[str, Any] | None = None,
    ) -> None:
        nonlocal issue_count, safe_fix_count, review_count, matching_issue_count
        source_row = row_numbers[row_index]
        column_index = headers.index(column)
        cell = f"{_column_letter(column_index)}{source_row}"
        issue_count += 1
        safe_fix_count += int(auto_applicable)
        review_count += int(not auto_applicable)
        affected_rows.add(source_row)
        counts_by_type[issue_type] += 1
        counts_by_column[column] += 1
        issue_id = _issue_id(issue_type, sheet, cell, original)
        matches = (not issue_type_filter or issue_type == issue_type_filter) and (not column_filter or column == column_filter)
        if matches:
            matching_issue_count += 1
        on_page = matches and issue_offset <= matching_issue_count - 1 < issue_offset + issue_limit
        if not on_page and issue_id not in requested_ids:
            return
        issues.append({
            "id": issue_id,
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

    structured_baseline = _structured_baseline(headers, rows, analysis_config)
    for header in headers:
        numeric = [
            (index, parsed)
            for index, row in enumerate(rows)
            if (parsed := _number(str(row.get(header, "")).strip())) is not None
        ]
        if not numeric:
            continue
        assessment: dict[str, Any] = {"column": header, "validCount": len(numeric), "method": "IQR_3.0"}

        def skip_iqr(reason: str) -> None:
            iqr_screening.append({**assessment, "status": "skipped", "reason": reason, "candidateCount": 0})

        if _is_identifier(header) or _is_rank(header) or _is_count(header) or _is_time(header) or _is_group(header):
            skip_iqr("该列是标识、排名、计数、时间或分组字段，不适合按连续测量值套用 IQR。")
            continue
        if is_derived_rate(header):
            skip_iqr("这是比率或变化率；离群数值不能说明计算错误。需先核对分子、分母、基准期与公式口径，本次不生成逐格 IQR 复核项。")
            continue
        if len(numeric) < MIN_IQR_VALUES:
            skip_iqr(f"只有 {len(numeric)} 个有效数值，未达到本产品 {MIN_IQR_VALUES} 个的保守筛查门槛。")
            continue
        if len({value for _, value in numeric}) < MIN_IQR_DISTINCT:
            skip_iqr("不同数值过少，更像等级或离散编码，IQR 不能可靠解释其含义。")
            continue
        if structured_baseline:
            skip_iqr(structured_baseline)
            continue
        values = [value for _, value in numeric]
        q1, median, q3 = (_quantile(values, fraction) for fraction in (0.25, 0.5, 0.75))
        iqr = q3 - q1
        if iqr <= 0:
            skip_iqr("中间一半数值没有可用的跨度，无法建立 IQR 筛查界限。")
            continue
        left, right = median - q1, q3 - median
        if min(left, right) <= 0 or max(left, right) / min(left, right) > 3:
            skip_iqr("分布明显偏斜，固定 IQR 界限容易把正常长尾误当成异常；请结合业务分布核对。")
            continue
        # Mild 1.5×IQR points are not used. Even 3×IQR extreme points belong
        # only in exploratory profiling, not a cell-correction queue.
        lower, upper = q1 - 3 * iqr, q3 + 3 * iqr
        flagged = [(row_index, value) for row_index, value in numeric if value < lower or value > upper]
        if len(flagged) >= 5 and len(flagged) / len(values) > MAX_IQR_EXTREME_RATE:
            skip_iqr(
                f"全列有 {len(flagged)} / {len(values)} 个值越过极端界限，"
                "集中出现的偏离可能是混合分布或真实工况，不应生成逐格‘错误’队列。"
            )
            continue
        iqr_screening.append({
            **assessment, "status": "applied", "candidateCount": len(flagged),
            "reason": "数值量、离散程度和分布形状通过保守检查；越过 3×IQR 界限的值仅作为分布探索线索，不是修正问题。",
            "lowerBound": lower, "upperBound": upper,
        })
        # Distribution candidates are exploratory evidence, not repairable
        # defects. Keep the assessment for charts, never add cell-repair issues.

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
            "matchingIssueCount": matching_issue_count,
            "iqrScreening": iqr_screening,
            "byType": dict(sorted(counts_by_type.items(), key=lambda entry: (-entry[1], entry[0]))),
            "byColumn": dict(sorted(counts_by_column.items(), key=lambda entry: (-entry[1], entry[0]))),
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
