"""Local, evidence-producing analysis tools for arbitrary tabular uploads.

The tools calculate before a provider is called.  They never ask a language
model to invent values, and they keep row values local.  A provider receives
only redacted aggregates from these results and may explain them, not replace
the underlying calculation.
"""
from __future__ import annotations

import math
import re
from io import StringIO
from typing import Any, Iterable

import pandas as pd


ANALYSIS_GOALS = {
    "auto": "自动诊断",
    "data_quality": "数据质量检查",
    "score_audit": "成绩与排名核验",
    "business_review": "经营数据分析",
    "industrial_review": "工业监测分析",
    "economic_review": "经济数据分析",
    "descriptive_profile": "现状与分布画像",
    "group_comparison": "群体与阶段比较",
    "relationship_review": "关联与候选驱动分析",
    "trend_forecast": "趋势与预测条件分析",
    "causal_evaluation": "措施与政策效果评估",
}
ANALYSIS_CONFIG_FIELDS = ("measureColumn", "groupColumn", "identifierColumn", "timeColumn")
MAX_TOOL_FINDINGS = 30
GOAL_TOOLS = {
    "data_quality": {"quality_audit", "rank_consistency"},
    "score_audit": {"distribution_profile", "rank_consistency", "group_comparison"},
    "business_review": {"distribution_profile", "relationship_screen", "group_comparison"},
    "industrial_review": {"quality_audit", "distribution_profile", "relationship_screen"},
    "economic_review": {"distribution_profile", "relationship_screen", "group_comparison"},
    "descriptive_profile": {"quality_audit", "distribution_profile"},
    "group_comparison": {"distribution_profile", "group_comparison"},
    "relationship_review": {"relationship_screen"},
    "trend_forecast": {"quality_audit", "relationship_screen"},
    "causal_evaluation": {"relationship_screen", "group_comparison"},
    "auto": {
        "quality_audit", "distribution_profile", "rank_consistency",
        "relationship_screen", "group_comparison",
    },
}


def validate_analysis_goal(value: Any) -> str:
    goal = str(value or "auto").strip()
    if goal not in ANALYSIS_GOALS:
        raise ValueError(
            "analysisGoal 必须是 auto、data_quality、score_audit、business_review、"
            "industrial_review、economic_review、descriptive_profile、group_comparison、"
            "relationship_review、trend_forecast 或 causal_evaluation"
        )
    return goal


def validate_analysis_config(value: Any, columns: Iterable[Any]) -> dict[str, str | None]:
    """Validate optional user-selected field roles against the current table."""
    names = [str(column) for column in columns]
    if value in (None, {}):
        return {field: None for field in ANALYSIS_CONFIG_FIELDS}
    if not isinstance(value, dict):
        raise ValueError("analysisConfig 必须是对象")
    unknown_keys = sorted(set(value) - set(ANALYSIS_CONFIG_FIELDS))
    if unknown_keys:
        raise ValueError("analysisConfig 包含不支持的字段角色")
    config: dict[str, str | None] = {}
    for field in ANALYSIS_CONFIG_FIELDS:
        selected = value.get(field)
        if selected in (None, ""):
            config[field] = None
            continue
        if not isinstance(selected, str) or selected not in names:
            raise ValueError(f"{field} 不属于当前读取范围")
        config[field] = selected
    selected_values = [item for item in config.values() if item]
    if len(selected_values) != len(set(selected_values)):
        raise ValueError("同一字段不能同时承担多个分析角色")
    return config


def _normalized(value: Any) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(value).strip().lower())


def _is_identifier(column: Any) -> bool:
    header = _normalized(column)
    return (
        header in {"id", "编号", "序号", "学号", "工号", "证件号", "姓名", "name"}
        or header.endswith("id")
        or any(token in header for token in ("学号", "证件号"))
    )


def _is_rank(column: Any) -> bool:
    return any(token in _normalized(column) for token in ("排名", "名次", "rank"))


def _is_count(column: Any) -> bool:
    return any(token in _normalized(column) for token in ("总人数", "人数", "数量", "count"))


def _is_score(column: Any) -> bool:
    return any(token in _normalized(column) for token in ("成绩", "得分", "分数", "score"))


def _column_letter(index: int) -> str:
    output = ""
    value = index + 1
    while value:
        value, remainder = divmod(value - 1, 26)
        output = chr(65 + remainder) + output
    return output


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 6) if math.isfinite(number) else None


def _numeric_columns(frame: pd.DataFrame, excluded: Iterable[str] = ()) -> dict[str, pd.Series]:
    output: dict[str, pd.Series] = {}
    excluded_names = set(excluded)
    for column in frame.columns:
        if str(column) in excluded_names or _is_identifier(column) or _is_rank(column) or _is_count(column):
            continue
        nonblank = frame[column].dropna()
        nonblank = nonblank[nonblank.astype(str).str.strip() != ""]
        converted = pd.to_numeric(nonblank, errors="coerce")
        if len(nonblank) >= 4 and float(converted.notna().mean()) >= 0.8:
            output[str(column)] = pd.to_numeric(frame[column], errors="coerce")
    return output


def _relationship_numeric_columns(
    frame: pd.DataFrame, excluded: Iterable[str] = (),
) -> dict[str, pd.Series]:
    """Return comparable numeric fields, including ranks but never identifiers."""
    output: dict[str, pd.Series] = {}
    excluded_names = set(excluded)
    for column in frame.columns:
        name = str(column)
        if name in excluded_names or _is_identifier(column):
            continue
        nonblank = frame[column].dropna()
        nonblank = nonblank[nonblank.astype(str).str.strip() != ""]
        converted = pd.to_numeric(nonblank, errors="coerce")
        if (
            len(nonblank) >= 4
            and float(converted.notna().mean()) >= 0.8
            and int(converted.nunique(dropna=True)) >= 2
        ):
            output[name] = pd.to_numeric(frame[column], errors="coerce")
    return output


def _tool_ref(run_id: str, key: str) -> str:
    return f"{run_id}/analysis_tool_{key}@v1"


def _rank_total_pairs(columns: Iterable[Any]) -> list[tuple[str, str]]:
    names = [str(column) for column in columns]
    totals = [name for name in names if _is_count(name)]
    pairs: list[tuple[str, str]] = []
    for rank in (name for name in names if _is_rank(name)):
        rank_base = re.sub(r"(排名|名次|rank)$", "", _normalized(rank))
        scored: list[tuple[int, str]] = []
        for total in totals:
            total_base = re.sub(r"(总人数|人数|数量|count)$", "", _normalized(total))
            common = 0
            for left, right in zip(rank_base, total_base):
                if left != right:
                    break
                common += 1
            scored.append((common, total))
        if scored:
            common, total = max(scored)
            if common > 0 or len(totals) == 1:
                pairs.append((rank, total))
    return pairs


def run_analysis_tools(
    csv_text: str,
    *,
    run_id: str,
    goal: str = "auto",
    sheet: str | None = None,
    source_row_numbers: Iterable[int] | None = None,
    repair_plan: dict[str, Any] | None = None,
    analysis_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute bounded local analysis and return traceable tool outputs."""
    selected_goal = validate_analysis_goal(goal)
    selected_tools = GOAL_TOOLS[selected_goal]
    frame = pd.read_csv(StringIO(csv_text), dtype=object)
    if frame.empty:
        raise ValueError("分析工具至少需要一行数据")
    frame.columns = [str(column).strip() or f"column_{index + 1}" for index, column in enumerate(frame.columns)]
    config = validate_analysis_config(analysis_config, frame.columns)
    supplied_rows = list(source_row_numbers or [])
    row_numbers = supplied_rows if len(supplied_rows) == len(frame) else list(range(2, len(frame) + 2))
    tools: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    recommendations: list[dict[str, Any]] = []

    def record_tool(key: str, label: str, payload: dict[str, Any], tool_findings: list[dict[str, Any]]) -> None:
        if key not in selected_tools:
            return
        evidence_ref = _tool_ref(run_id, key)
        tools.append({
            "key": key,
            "label": label,
            "status": "success",
            "findingCount": len(tool_findings),
            "artifactId": f"analysis_tool_{key}",
            "evidenceRef": evidence_ref,
            "payload": payload,
        })
        for finding in tool_findings:
            item = dict(finding)
            item["evidenceRefs"] = [evidence_ref]
            findings.append(item)

    blank = frame.isna() | frame.apply(
        lambda column: column.map(lambda value: isinstance(value, str) and not value.strip())
    )
    missing_by_column = {
        str(column): int(blank[column].sum()) for column in frame.columns if int(blank[column].sum())
    }
    duplicate_count = int(frame.fillna("").astype(str).duplicated().sum())
    quality_findings: list[dict[str, Any]] = []
    if missing_by_column:
        worst_column, worst_count = max(missing_by_column.items(), key=lambda item: item[1])
        quality_findings.append({
            "type": "missingness",
            "title": f"“{worst_column}”缺失最多",
            "severity": "medium",
            "explanation": f"该字段缺失 {worst_count} 行；缺失值需要回到来源核对，不能自动猜填。",
            "details": {"column": worst_column, "missingCount": worst_count},
        })
    if duplicate_count:
        quality_findings.append({
            "type": "duplicate_rows",
            "title": "存在完全重复记录",
            "severity": "medium",
            "explanation": f"检测到 {duplicate_count} 行完全重复记录；删除前需确认是否为合法重复。",
            "details": {"duplicateRowCount": duplicate_count},
        })
    record_tool(
        "quality_audit",
        "数据质量审计",
        {
            "rowCount": int(len(frame)),
            "columnCount": int(len(frame.columns)),
            "missingCellCount": int(blank.to_numpy().sum()),
            "missingByColumn": missing_by_column,
            "duplicateRowCount": duplicate_count,
        },
        quality_findings,
    )

    numeric = _numeric_columns(frame, [config["identifierColumn"]] if config["identifierColumn"] else [])
    if config["measureColumn"] and config["measureColumn"] not in numeric:
        raise ValueError("选择的主指标不是可分析的数值字段（至少需要 4 个有效数值）")
    distribution_fields: list[dict[str, Any]] = []
    distribution_findings: list[dict[str, Any]] = []
    distribution_numeric = (
        {config["measureColumn"]: numeric[config["measureColumn"]]}
        if config["measureColumn"] else numeric
    )
    for column, series in distribution_numeric.items():
        values = series.dropna().astype(float)
        if len(values) < 4:
            continue
        q1 = float(values.quantile(0.25))
        q3 = float(values.quantile(0.75))
        iqr = q3 - q1
        outlier_mask = ((series < q1 - 1.5 * iqr) | (series > q3 + 1.5 * iqr)) if iqr > 0 else pd.Series(False, index=series.index)
        outlier_rows = [
            {
                "sheet": sheet,
                "cell": f"{_column_letter(frame.columns.get_loc(column))}{row_numbers[int(index)]}",
                "row": row_numbers[int(index)],
            }
            for index in series.index[outlier_mask.fillna(False)][:10]
        ]
        field = {
            "column": column,
            "count": int(len(values)),
            "mean": _finite(values.mean()),
            "median": _finite(values.median()),
            "min": _finite(values.min()),
            "max": _finite(values.max()),
            "q1": _finite(q1),
            "q3": _finite(q3),
            "outlierCount": int(outlier_mask.fillna(False).sum()),
            "outlierLocations": outlier_rows,
        }
        distribution_fields.append(field)
        if field["outlierCount"]:
            distribution_findings.append({
                "type": "distribution_outlier",
                "title": f"“{column}”有 {field['outlierCount']} 个统计异常候选",
                "severity": "review",
                "explanation": "按 1.5×IQR 规则筛出，仅表示需要核对，不等于数据错误。",
                "details": field,
            })
    record_tool(
        "distribution_profile",
        "数值分布与异常筛查",
        {"eligibleFieldCount": len(distribution_fields), "fields": distribution_fields},
        distribution_findings[:10],
    )

    rank_checks: list[dict[str, Any]] = []
    rank_findings: list[dict[str, Any]] = []
    for rank_column, total_column in _rank_total_pairs(frame.columns):
        ranks = pd.to_numeric(frame[rank_column], errors="coerce")
        totals = pd.to_numeric(frame[total_column], errors="coerce")
        comparable = ranks.notna() & totals.notna()
        invalid = comparable & ((ranks < 1) | (totals < 1) | (ranks > totals))
        locations = [
            f"{sheet + '!' if sheet else ''}{_column_letter(frame.columns.get_loc(rank_column))}{row_numbers[int(index)]}"
            for index in frame.index[invalid][:20]
        ]
        check = {
            "rankColumn": rank_column,
            "totalColumn": total_column,
            "checkedRows": int(comparable.sum()),
            "invalidRows": int(invalid.sum()),
            "invalidLocations": locations,
        }
        rank_checks.append(check)
        if check["invalidRows"]:
            rank_findings.append({
                "type": "rank_consistency",
                "title": f"“{rank_column}”有 {check['invalidRows']} 行超出总人数",
                "severity": "high",
                "explanation": f"排名应处于 1 到“{total_column}”之间，请核对列出的单元格。",
                "details": check,
            })
    record_tool(
        "rank_consistency",
        "排名约束核验",
        {"pairCount": len(rank_checks), "checks": rank_checks},
        rank_findings,
    )

    correlations: list[dict[str, Any]] = []
    relationship_numeric = _relationship_numeric_columns(
        frame, [config["identifierColumn"]] if config["identifierColumn"] else [],
    )
    numeric_names = list(relationship_numeric)
    if len(frame) >= 8:
        pairs = (
            [(config["measureColumn"], other) for other in numeric_names if other != config["measureColumn"]]
            if config["measureColumn"]
            else [(left, right) for left_index, left in enumerate(numeric_names) for right in numeric_names[left_index + 1:]]
        )
        for left, right in pairs:
            paired = pd.concat([relationship_numeric[left], relationship_numeric[right]], axis=1).dropna()
            if len(paired) < 8 or paired.iloc[:, 0].nunique() < 2 or paired.iloc[:, 1].nunique() < 2:
                continue
            coefficient = float(paired.iloc[:, 0].corr(paired.iloc[:, 1]))
            if math.isfinite(coefficient) and abs(coefficient) >= 0.5:
                correlations.append({
                    "left": left,
                    "right": right,
                    "coefficient": round(coefficient, 4),
                    "pairedRows": int(len(paired)),
                })
    correlations.sort(key=lambda item: abs(item["coefficient"]), reverse=True)
    correlation_findings = [
        {
            "type": "correlation_candidate",
            "title": f"“{item['left']}”与“{item['right']}”在当前文件中相关",
            "severity": "info",
            "explanation": f"Pearson r={item['coefficient']:.2f}，基于 {item['pairedRows']} 行；相关性不能证明因果关系。",
            "details": item,
        }
        for item in correlations[:5]
    ]
    trend_summary: dict[str, Any] | None = None
    if config["timeColumn"] and config["measureColumn"]:
        time_values = frame[config["timeColumn"]]
        numeric_time = pd.to_numeric(time_values, errors="coerce")
        if float(numeric_time.notna().mean()) >= 0.8:
            sortable_time = numeric_time
            time_method = "numeric_time_order"
        else:
            sortable_time = pd.to_datetime(time_values, errors="coerce")
            time_method = "datetime_order"
        prepared = pd.DataFrame({
            "time": sortable_time,
            "measure": numeric[config["measureColumn"]],
        }).dropna().sort_values("time")
        if len(prepared) >= 4:
            first_value = float(prepared.iloc[0]["measure"])
            last_value = float(prepared.iloc[-1]["measure"])
            change = last_value - first_value
            change_pct = None if first_value == 0 else change / abs(first_value) * 100
            trend_summary = {
                "timeColumn": config["timeColumn"],
                "measureColumn": config["measureColumn"],
                "comparableRows": int(len(prepared)),
                "firstValue": _finite(first_value),
                "lastValue": _finite(last_value),
                "absoluteChange": _finite(change),
                "changePct": _finite(change_pct) if change_pct is not None else None,
                "method": time_method,
            }
            pct_text = "起始值为 0，变化率不适用" if change_pct is None else f"变化率 {change_pct:.2f}%"
            correlation_findings.append({
                "type": "time_trend",
                "title": f"“{config['measureColumn']}”按“{config['timeColumn']}”排序后发生变化",
                "severity": "info",
                "explanation": (
                    f"首个可比值 {first_value:.2f}，末个可比值 {last_value:.2f}，"
                    f"绝对变化 {change:.2f}，{pct_text}；这是描述性趋势，不证明原因。"
                ),
                "details": trend_summary,
            })
    relationship_blocker = None
    if len(frame) < 8:
        relationship_blocker = "有效记录少于 8 行，不能进行稳定的字段关联筛查。"
    elif len(numeric_names) < 2:
        relationship_blocker = (
            f"只识别到 {len(numeric_names)} 个可比较数值字段，关联分析至少需要 2 个。"
        )
    elif not correlations:
        relationship_blocker = "已完成字段关联筛查，但没有发现绝对相关系数达到 0.50 的候选关系。"
    record_tool(
        "relationship_screen",
        "字段关系探索",
        {
            "method": "pearson_pairwise_complete",
            "eligibleFieldCount": len(numeric_names),
            "eligibleFields": numeric_names[:30],
            "candidateCount": len(correlations),
            "candidates": correlations[:10],
            "timeTrend": trend_summary,
            "blockedReason": relationship_blocker,
        },
        correlation_findings,
    )

    group_comparisons: list[dict[str, Any]] = []
    if len(frame) >= 8 and numeric:
        categories = []
        if config["groupColumn"]:
            categories = [config["groupColumn"]]
        else:
            for column in frame.columns:
                if str(column) in numeric or _is_identifier(column) or str(column) == config["identifierColumn"]:
                    continue
                values = frame[column].dropna().astype(str).str.strip()
                level_count = int(values[values != ""].nunique())
                if 2 <= level_count <= 10:
                    categories.append(str(column))
        for category in categories[:3]:
            measures = [config["measureColumn"]] if config["measureColumn"] else list(numeric)[:5]
            for measure in measures:
                prepared = pd.DataFrame({
                    "category": frame[category].astype(str).str.strip(),
                    "measure": numeric[measure],
                }).dropna()
                prepared = prepared[prepared["category"] != ""]
                groups = prepared.groupby("category")["measure"].agg(["count", "mean"])
                groups = groups[groups["count"] >= 2].sort_values("mean", ascending=False)
                if len(groups) < 2:
                    continue
                high = groups.iloc[0]
                low = groups.iloc[-1]
                group_comparisons.append({
                    "categoryColumn": category,
                    "measureColumn": measure,
                    "highestGroup": str(groups.index[0]),
                    "highestMean": _finite(high["mean"]),
                    "highestCount": int(high["count"]),
                    "lowestGroup": str(groups.index[-1]),
                    "lowestMean": _finite(low["mean"]),
                    "lowestCount": int(low["count"]),
                    "meanGap": _finite(float(high["mean"] - low["mean"])),
                    "groupCount": int(len(groups)),
                })
    group_comparisons.sort(key=lambda item: abs(item["meanGap"] or 0), reverse=True)
    group_findings = [
        {
            "type": "group_difference",
            "title": f"“{item['measureColumn']}”在“{item['categoryColumn']}”分组间存在描述性差异",
            "severity": "info",
            "explanation": (
                f"当前样本中“{item['highestGroup']}”均值 {item['highestMean']:.2f}，"
                f"“{item['lowestGroup']}”均值 {item['lowestMean']:.2f}；该差异未经显著性或混杂因素检验。"
            ),
            "details": item,
        }
        for item in group_comparisons[:5]
    ]
    record_tool(
        "group_comparison",
        "分组差异探索",
        {"comparisonCount": len(group_comparisons), "comparisons": group_comparisons[:10]},
        group_findings,
    )

    repair_summary = (repair_plan or {}).get("summary") or {}
    repair_ref = next((tool["evidenceRef"] for tool in tools if tool["key"] == "quality_audit"), "")
    if "quality_audit" in selected_tools and repair_summary.get("safeFixCount", 0):
        recommendations.append({
            "priority": "high",
            "action": "先预览并导出安全修正版",
            "rationale": f"有 {repair_summary['safeFixCount']} 项仅涉及首尾空格的安全修正；应用前仍需用户勾选确认。",
            "evidenceRefs": [repair_ref],
            "automatic": False,
        })
    if "quality_audit" in selected_tools and repair_summary.get("reviewCount", 0):
        recommendations.append({
            "priority": "high",
            "action": "按单元格位置核对人工复核项",
            "rationale": f"有 {repair_summary['reviewCount']} 项缺失、越界、重复或统计异常候选，系统不会猜测替换值。",
            "evidenceRefs": [repair_ref],
            "automatic": False,
        })
    if "relationship_screen" in selected_tools and correlations:
        recommendations.append({
            "priority": "medium",
            "action": "围绕高相关字段补做业务验证",
            "rationale": "当前相关筛查只能发现共变线索；需要结合时间、分组和业务机制验证，不能直接写成因果结论。",
            "evidenceRefs": [_tool_ref(run_id, "relationship_screen")],
            "automatic": False,
        })
    if "group_comparison" in selected_tools and group_comparisons:
        recommendations.append({
            "priority": "medium",
            "action": "复核分组口径并比较样本量",
            "rationale": "分组均值差异可能受样本量和混杂因素影响，建议先核对分类口径与各组记录数。",
            "evidenceRefs": [_tool_ref(run_id, "group_comparison")],
            "automatic": False,
        })
    if not recommendations:
        relationship_tool = next(
            (tool for tool in tools if tool["key"] == "relationship_screen"), None,
        )
        blocker = (relationship_tool or {}).get("payload", {}).get("blockedReason")
        if selected_goal == "relationship_review" and blocker:
            action = "补齐关联分析条件"
            rationale = blocker
        else:
            action = "确认分析目标和关键结果字段"
            rationale = "当前分析没有产生明确线索；请检查字段角色与样本量后再运行。"
        fallback_ref = tools[0]["evidenceRef"] if tools else ""
        recommendations.append({
            "priority": "low",
            "action": action,
            "rationale": rationale,
            "evidenceRefs": [fallback_ref] if fallback_ref else [],
            "automatic": False,
        })

    priority_refs = {_tool_ref(run_id, key) for key in selected_tools}
    findings.sort(key=lambda item: (
        item["evidenceRefs"][0] not in priority_refs,
        {"high": 0, "medium": 1, "review": 2, "info": 3}.get(item["severity"], 4),
    ))
    findings = findings[:MAX_TOOL_FINDINGS]
    return {
        "version": "local_analysis_tools_v1",
        "goal": selected_goal,
        "goalLabel": ANALYSIS_GOALS[selected_goal],
        "analysisConfig": config,
        "summary": {
            "executedToolCount": len(tools),
            "findingCount": len(findings),
            "recommendationCount": len(recommendations),
            "rowCount": int(len(frame)),
        },
        "executedTools": tools,
        "findings": findings,
        "recommendations": recommendations[:5],
        "limitations": [
            "异常筛查只产生复核线索，不自动认定原值错误。",
            "相关和分组差异属于描述性结果，不能单独证明因果关系。",
            "缺失值、异常值和冲突值不会由模型猜测填充。",
        ],
        "privacy": {
            "calculationLocation": "local",
            "rawRowsSentToProvider": False,
            "directIdentifierValuesSentToProvider": False,
            "categoryValuesSentToProvider": False,
        },
    }


def provider_safe_tool_results(results: dict[str, Any]) -> dict[str, Any]:
    """Remove row values, locations and category labels for provider use."""
    safe_tools = []
    for tool in results.get("executedTools", []):
        payload = tool.get("payload") or {}
        safe_payload: dict[str, Any]
        if tool.get("key") == "quality_audit":
            safe_payload = {key: payload.get(key) for key in (
                "rowCount", "columnCount", "missingCellCount", "missingByColumn", "duplicateRowCount",
            )}
        elif tool.get("key") == "distribution_profile":
            safe_payload = {
                "eligibleFieldCount": payload.get("eligibleFieldCount"),
                "fields": [
                    {key: field.get(key) for key in (
                        "column", "count", "mean", "median", "min", "max", "q1", "q3", "outlierCount",
                    )}
                    for field in payload.get("fields", [])[:20]
                ],
            }
        elif tool.get("key") == "rank_consistency":
            safe_payload = {
                "pairCount": payload.get("pairCount"),
                "checks": [
                    {key: check.get(key) for key in (
                        "rankColumn", "totalColumn", "checkedRows", "invalidRows",
                    )}
                    for check in payload.get("checks", [])[:20]
                ],
            }
        elif tool.get("key") == "relationship_screen":
            safe_payload = {
                "method": payload.get("method"),
                "eligibleFieldCount": payload.get("eligibleFieldCount"),
                "eligibleFields": payload.get("eligibleFields", [])[:30],
                "candidateCount": payload.get("candidateCount"),
                "candidates": payload.get("candidates", [])[:10],
                "timeTrend": payload.get("timeTrend"),
                "blockedReason": payload.get("blockedReason"),
            }
        else:
            safe_payload = {
                "comparisonCount": payload.get("comparisonCount"),
                "comparisons": [
                    {key: item.get(key) for key in (
                        "categoryColumn", "measureColumn", "highestMean", "highestCount",
                        "lowestMean", "lowestCount", "meanGap", "groupCount",
                    )}
                    for item in payload.get("comparisons", [])[:10]
                ],
            }
        safe_tools.append({
            "key": tool.get("key"),
            "label": tool.get("label"),
            "status": tool.get("status"),
            "evidenceRef": tool.get("evidenceRef"),
            "payload": safe_payload,
        })
    return {
        "goal": results.get("goal"),
        "goalLabel": results.get("goalLabel"),
        "analysisConfig": results.get("analysisConfig"),
        "summary": results.get("summary"),
        "executedTools": safe_tools,
        "recommendations": results.get("recommendations", []),
        "limitations": results.get("limitations", []),
        "privacy": results.get("privacy"),
    }
