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

from workflow.repair_analysis import is_derived_rate


ANALYSIS_GOALS = {
    "auto": "自动诊断",
    "exploratory_analysis": "完整探索分析流程",
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
METHOD_SOURCES = {
    "categorical_frequency": "https://www.itl.nist.gov/div898/software/dataplot/refman1/auxillar/tabuplot.htm",
    "numeric_distribution": "https://www.itl.nist.gov/div898/handbook/eda/section3/eda33e.htm",
    "group_location_scale": "https://www.itl.nist.gov/div898/handbook/eda/section3/boxplot.htm",
    "time_sequence": "https://www.itl.nist.gov/div898/handbook/pmc/section6/pmc622.htm",
    "categorical_cross_tab": "https://www.itl.nist.gov/div898/handbook/prc/section4/prc45.htm",
    "numeric_relationship": "https://www.itl.nist.gov/div898/handbook/eda/section3/eda33q.htm",
}
GOAL_TOOLS = {
    "exploratory_analysis": {
        "quality_audit", "distribution_profile", "rank_consistency",
        "relationship_screen", "group_comparison",
    },
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
            "analysisGoal 必须是 auto、exploratory_analysis、data_quality、score_audit、business_review、"
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


def _histogram(values: pd.Series, bin_count: int = 8) -> list[dict[str, Any]]:
    finite_values = [float(value) for value in values if math.isfinite(float(value))]
    if not finite_values:
        return []
    low, high = min(finite_values), max(finite_values)
    if low == high:
        return [{
            "label": f"{low:.4g}", "lower": _finite(low), "upper": _finite(high),
            "count": len(finite_values),
        }]
    width = (high - low) / bin_count
    counts = [0] * bin_count
    for value in finite_values:
        index = min(int((value - low) / width), bin_count - 1)
        counts[index] += 1
    return [
        {
            "label": f"{low + index * width:.4g}–{low + (index + 1) * width:.4g}",
            "lower": _finite(low + index * width),
            "upper": _finite(low + (index + 1) * width),
            "count": count,
        }
        for index, count in enumerate(counts)
    ]


def _tool_payload(tools: Iterable[dict[str, Any]], key: str) -> dict[str, Any]:
    tool = next((item for item in tools if item.get("key") == key), None)
    return dict((tool or {}).get("payload") or {})


def build_analysis_process(
    results: dict[str, Any], repair_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an extensible, UI-facing analysis process from actual tool outputs."""
    tools = list(results.get("executedTools") or [])
    quality = _tool_payload(tools, "quality_audit")
    distribution = _tool_payload(tools, "distribution_profile")
    relationships = _tool_payload(tools, "relationship_screen")
    groups = _tool_payload(tools, "group_comparison")
    ranks = _tool_payload(tools, "rank_consistency")
    repair_summary = (repair_plan or {}).get("summary") or {}
    visuals: list[dict[str, Any]] = []
    missing_series = [
        {"label": str(column), "value": int(count)}
        for column, count in (quality.get("missingByColumn") or {}).items()
        if count
    ]
    if missing_series:
        visuals.append({
            "key": "missingness", "type": "bar", "title": "各字段缺失数量",
            "subtitle": "用于决定回源核对、删除还是建模填补；系统不会自动猜填。",
            "unit": "个", "series": missing_series[:15],
        })
    for profile in (quality.get("categoricalProfiles") or [])[:3]:
        visuals.append({
            "key": f"category:{profile['column']}", "type": "bar",
            "title": f"{profile['column']} · 类别频数",
            "subtitle": f"有效记录 {profile['validCount']} 条；类别比例以有效记录为分母。",
            "unit": "条", "series": [
                {"label": item["label"], "value": item["count"]}
                for item in profile["frequencies"]
            ],
        })
    for field in (distribution.get("fields") or [])[:4]:
        if field.get("histogram"):
            visuals.append({
                "key": f"distribution:{field.get('column')}", "type": "histogram",
                "title": f"{field.get('column')} · 分布",
                "subtitle": f"有效值 {field.get('count', 0)} 个；异常候选 {field.get('outlierCount', 0)} 个。",
                "unit": "行", "series": [
                    {"label": item.get("label"), "value": item.get("count", 0)}
                    for item in field.get("histogram", [])
                ],
            })
    candidates = relationships.get("candidates") or []
    if candidates:
        visuals.append({
            "key": "relationships", "type": "correlation", "title": "字段关联强度",
            "subtitle": "显示 Pearson 相关系数；相关不等于因果。", "unit": "r",
            "series": [
                {
                    "label": f"{item.get('left')} × {item.get('right')}",
                    "value": item.get("coefficient"), "sampleSize": item.get("pairedRows"),
                }
                for item in candidates[:8]
            ],
        })
    trend = relationships.get("timeTrend") or {}
    if trend.get("points"):
        visuals.append({
            "key": "trend", "type": "line", "title": f"{trend.get('measureColumn')} · 时间趋势",
            "subtitle": f"按 {trend.get('timeColumn')} 排序，展示最多 60 个等距抽样点。",
            "unit": "", "series": trend.get("points"),
        })
    comparisons = groups.get("comparisons") or []
    if comparisons:
        visuals.append({
            "key": "groups", "type": "comparison", "title": "分组均值差异",
            "subtitle": "仅为描述性比较，尚未控制混杂因素或完成显著性检验。",
            "unit": "均值", "series": [
                {
                    "label": f"{item.get('categoryColumn')} · {item.get('measureColumn')}",
                    "highLabel": item.get("highestGroup"), "high": item.get("highestMean"),
                    "lowLabel": item.get("lowestGroup"), "low": item.get("lowestMean"),
                }
                for item in comparisons[:6]
            ],
        })
    for table in (groups.get("categoryCrossTabs") or [])[:2]:
        visuals.append({
            "key": f"cross:{table['rowColumn']}:{table['columnColumn']}",
            "type": "bar", "title": f"{table['rowColumn']} × {table['columnColumn']} · 分类交叉",
            "subtitle": f"基于 {table['pairedRows']} 条同时有值的记录；仅作计数，不代表显著关联。",
            "unit": "条", "series": [
                {"label": f"{item['row']} / {item['column']}", "value": item["count"]}
                for item in table["cells"][:12]
            ],
        })

    missing_count = int(quality.get("missingCellCount") or 0)
    duplicate_count = int(quality.get("duplicateRowCount") or 0)
    review_count = int(repair_summary.get("reviewCount") or 0)
    safe_fix_count = int(repair_summary.get("safeFixCount") or 0)
    preprocessing_items = [
        {
            "key": "missing", "label": "缺失值处理", "count": missing_count,
            "status": "review" if missing_count else "clear",
            "action": "回到来源核对缺失原因；只有明确规则时才删除或填补。" if missing_count else "本次未发现缺失单元格。",
        },
        {
            "key": "duplicates", "label": "重复记录", "count": duplicate_count,
            "status": "review" if duplicate_count else "clear",
            "action": "先确认数据粒度和业务主键，再决定是否去重。" if duplicate_count else "本次未发现完全重复记录。",
        },
        {
            "key": "format", "label": "格式与安全修正", "count": safe_fix_count,
            "status": "actionable" if safe_fix_count else "clear",
            "action": "可由用户勾选后在新副本中修正，不覆盖原件。" if safe_fix_count else "没有检测到可直接应用的安全格式修正。",
        },
        {
            "key": "review", "label": "异常与约束复核", "count": review_count,
            "status": "review" if review_count else "clear",
            "action": "逐项查看单元格并人工确认，不把统计异常直接当成错误。" if review_count else "当前规则没有生成待人工确认的修正项。",
        },
    ]
    finding_count = int((results.get("summary") or {}).get("findingCount") or 0)
    stage_refs = {str(tool.get("key")): tool.get("evidenceRef") for tool in tools}
    stages = [
        {"key": "understand", "number": 1, "title": "数据理解", "status": "completed",
         "summary": f"读取 {(results.get('summary') or {}).get('rowCount', 0)} 行，识别字段角色与分析目标。", "evidenceRefs": []},
        {"key": "preprocess", "number": 2, "title": "预处理检查",
         "status": "review_required" if any(item["count"] for item in preprocessing_items) else "completed",
         "summary": "先核对缺失、重复、格式和异常候选，不自动改写数据。",
         "evidenceRefs": [stage_refs[key] for key in ("quality_audit", "rank_consistency") if stage_refs.get(key)]},
        {"key": "visualize", "number": 3, "title": "探索性可视化",
         "status": "completed" if visuals else "limited",
         "summary": f"基于本地计算生成 {len(visuals)} 张图；没有足够数据时明确显示限制。",
         "evidenceRefs": [stage_refs[key] for key in ("distribution_profile", "relationship_screen", "group_comparison") if stage_refs.get(key)]},
        {"key": "analyze", "number": 4, "title": "按问题分析", "status": "completed",
         "summary": f"实际执行 {len(tools)} 个分析工具，产生 {finding_count} 项可核对线索。",
         "evidenceRefs": [value for value in stage_refs.values() if value]},
        {"key": "validate", "number": 5, "title": "验证与解释",
         "status": "review_required" if finding_count else "completed",
         "summary": "区分描述、相关和因果；结论仍需证据审计与业务复核。",
         "evidenceRefs": [value for value in stage_refs.values() if value]},
    ]
    config = results.get("analysisConfig") or {}
    extensions = [
        {"method": "数值分布与图表", "goal": "descriptive_profile",
         "status": "executed" if distribution.get("fields") else "configurable",
         "requires": "选择数值主指标；该字段至少有 4 个有效数值。",
         "missingFields": ["主指标"] if not config.get("measureColumn") else [],
         "result": f"已形成 {len(distribution.get('fields') or [])} 个数值画像。" if distribution.get("fields") else None},
        {"method": "分组数值描述", "goal": "group_comparison",
         "status": "executed" if comparisons else "configurable",
         "requires": "选择数值主指标和分组字段；至少两个组各有 2 条有效记录。只比较描述性差异。",
         "missingFields": [label for key, label in (("measureColumn", "主指标"), ("groupColumn", "分组字段")) if not config.get(key)],
         "result": f"已完成 {len(comparisons)} 项比较。" if comparisons else None},
        {"method": "双分类交叉计数", "goal": "exploratory_analysis",
         "status": "executed" if groups.get("categoryCrossTabs") else "configurable",
         "requires": "选择分组字段；文件中还需另一列低基数分类字段，至少 8 条共同有效记录。主指标可留空。",
         "missingFields": ["分组字段"] if not config.get("groupColumn") else [],
         "result": f"已完成 {len(groups.get('categoryCrossTabs') or [])} 项交叉计数。" if groups.get("categoryCrossTabs") else None},
        {"method": "时间顺序与采样检查", "goal": "trend_forecast",
         "status": "executed" if trend else "configurable",
         "requires": "选择时间字段和数值主指标；至少 4 个可比较时间点。当前只作描述，不生成预测。",
         "missingFields": [label for key, label in (("timeColumn", "时间字段"), ("measureColumn", "主指标")) if not config.get(key)],
         "result": f"已分析 {trend['comparableRows']} 个时间点。" if trend else None},
        {"method": "连续字段线性相关", "goal": "relationship_review",
         "status": "executed" if relationships.get("screenedPairCount") else "configurable",
         "requires": "选择主指标；还需另一列可比较数值，至少 8 条成对有效记录。相关不等于因果。",
         "missingFields": ["主指标"] if not config.get("measureColumn") else [],
         "result": f"已筛查 {relationships['screenedPairCount']} 对字段。" if relationships.get("screenedPairCount") else None},
    ]
    future_methods = [
        {"method": "假设检验", "requires": "明确零假设、独立/配对设计、主指标、分组、样本条件和多重比较方案；尚无执行器。"},
        {"method": "回归建模", "requires": "明确结果与解释字段、诊断方案及独立验证；尚无执行器。"},
        {"method": "聚类分群", "requires": "明确可比字段、缩放方案、群组稳定性和业务解释；尚无执行器。"},
        {"method": "预测与预警", "requires": "明确时间粒度、预测目标、历史跨度、基线与回测方案；尚无执行器。"},
        {"method": "因果评估", "requires": "明确处理、结果、时间、对照及识别假设；尚无执行器。"},
    ]
    iqr_screens = repair_summary.get("iqrScreening") or []
    method_decisions = [
        {
            "method": "缺失、重复与分类频数", "methodCardIds": ["quality_dimensions"],
            "status": "executed" if quality else "not_selected",
            "result": (f"检查 {quality.get('rowCount', 0)} 行；形成 {len(quality.get('categoricalProfiles') or [])} 个分类字段画像。"
                       if quality else "本次目标没有运行数据质量工具。"),
            "sourceUrl": METHOD_SOURCES["categorical_frequency"],
            "evidenceRef": stage_refs.get("quality_audit"),
        },
        {
            "method": "连续数值分布", "methodCardIds": ["eda_before_model"],
            "status": "executed" if distribution.get("fields") else "skipped",
            "result": (f"形成 {len(distribution['fields'])} 个数值字段画像。" if distribution.get("fields")
                       else "未运行分布工具，或没有足够的可比较数值。"),
            "sourceUrl": METHOD_SOURCES["numeric_distribution"],
            "evidenceRef": stage_refs.get("distribution_profile") if distribution.get("fields") else None,
        },
        {
            "method": "IQR 极端值线索", "methodCardIds": ["outlier_review"],
            "status": "executed" if any(item.get("status") == "applied" for item in iqr_screens) else "skipped",
            "result": (f"通过适用性检查 {sum(item.get('status') == 'applied' for item in iqr_screens)} 列；其余列不套用全列阈值。"
                       if iqr_screens else "本次没有可执行的 IQR 适用性结论。"),
            "sourceUrl": METHOD_SOURCES["group_location_scale"],
            "evidenceRef": stage_refs.get("distribution_profile") if any(item.get("status") == "applied" for item in iqr_screens) else None,
        },
        {
            "method": "时间顺序与采样间隔", "status": "executed" if trend else "skipped",
            "result": (f"按 {trend['timeColumn']} 分析 {trend['comparableRows']} 个可比时间点；不据此预测。"
                       if trend else "需要明确时间字段和数值主指标，且至少有 4 个可比点。"),
            "sourceUrl": METHOD_SOURCES["time_sequence"],
            "evidenceRef": stage_refs.get("relationship_screen") if trend else None,
        },
        {
            "method": "分组位置与离散度", "methodCardIds": ["effect_size"],
            "status": "executed" if comparisons else "skipped",
            "result": (f"完成 {len(comparisons)} 项描述性比较；不推断因果。" if comparisons
                       else "需要可用分组字段、数值主指标及每组足够记录。"),
            "sourceUrl": METHOD_SOURCES["group_location_scale"],
            "evidenceRef": stage_refs.get("group_comparison") if comparisons else None,
        },
        {
            "method": "分类交叉计数", "status": "executed" if groups.get("categoryCrossTabs") else "skipped",
            "result": (f"完成 {len(groups['categoryCrossTabs'])} 对分类字段交叉计数；组内比例以该组有效记录为分母。"
                       if groups.get("categoryCrossTabs") else "需要两个低基数分类字段和足够的共同有效记录。"),
            "sourceUrl": METHOD_SOURCES["categorical_cross_tab"],
            "evidenceRef": stage_refs.get("group_comparison") if groups.get("categoryCrossTabs") else None,
        },
        {
            "method": "连续字段线性相关", "status": "executed" if relationships.get("screenedPairCount", 0) else "skipped",
            "result": (f"成对筛查后达到展示阈值的关系 {relationships.get('candidateCount', 0)} 对；相关不等于因果。"
                       if relationships.get("screenedPairCount", 0)
                       else "需要至少两个可比较数值字段和足够的成对有效记录。"),
            "sourceUrl": METHOD_SOURCES["numeric_relationship"],
            "evidenceRef": stage_refs.get("relationship_screen") if relationships.get("screenedPairCount", 0) else None,
        },
        {
            "method": "排名范围约束", "status": "executed" if ranks.get("pairCount") else "skipped",
            "result": (f"核对 {ranks['pairCount']} 对排名与总人数列。" if ranks.get("pairCount")
                       else "未找到可核对的排名与总人数列对。"),
            "sourceUrl": None,
            "evidenceRef": stage_refs.get("rank_consistency") if ranks.get("pairCount") else None,
        },
    ]
    return {
        "version": "analysis_process_v1", "stages": stages,
        "preprocessing": {"items": preprocessing_items, "automaticMutation": False},
        "visualizations": visuals, "extensions": extensions,
        "futureMethods": future_methods,
        "methodDecisions": method_decisions,
        "boundary": "这是可扩展的数据分析主流程，不代表已执行所有统计或机器学习方法；只展示当前数据和字段条件支持的结果。",
    }


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
    execute_tool_keys: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Execute bounded local analysis and return traceable tool outputs.

    ``execute_tool_keys`` is used only by a validated recovery run.  It
    physically skips calculations for unaffected tools; omitted keeps the
    historical full execution behavior unchanged.
    """
    selected_goal = validate_analysis_goal(goal)
    selected_tools = GOAL_TOOLS[selected_goal]
    requested_tools = set(execute_tool_keys) if execute_tool_keys is not None else set(selected_tools)
    unknown_tools = requested_tools - set(selected_tools)
    if unknown_tools:
        raise ValueError("选择性重算包含本次分析目标未使用的工具")
    active_tools = set(selected_tools) & requested_tools
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
        if key not in active_tools:
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

    if "quality_audit" in active_tools:
        blank = frame.isna() | frame.apply(
            lambda column: column.map(lambda value: isinstance(value, str) and not value.strip())
        )
        missing_by_column = {
            str(column): int(blank[column].sum()) for column in frame.columns if int(blank[column].sum())
        }
        duplicate_count = int(frame.fillna("").astype(str).duplicated().sum())
        missing_cell_count = int(blank.to_numpy().sum())
        categorical_profiles: list[dict[str, Any]] = []
        for column in frame.columns:
            if _is_identifier(column) or column in {config["identifierColumn"], config["timeColumn"]}:
                continue
            nonblank = frame.loc[~blank[column], column].astype(str).str.strip()
            if len(nonblank) < 4 or pd.to_numeric(nonblank, errors="coerce").notna().mean() >= 0.8:
                continue
            counts = nonblank.value_counts()
            if not 2 <= len(counts) <= 20 or len(counts) > len(nonblank) / 2:
                continue
            categorical_profiles.append({
                "column": str(column), "validCount": int(len(nonblank)),
                "missingCount": int(blank[column].sum()), "categoryCount": int(len(counts)),
                "frequencies": [
                    {"label": str(label), "count": int(count),
                     "sharePct": _finite(100 * count / len(nonblank))}
                    for label, count in counts.head(8).items()
                ],
                "truncated": len(counts) > 8,
            })
    else:
        missing_by_column = {}
        duplicate_count = 0
        missing_cell_count = 0
        categorical_profiles = []
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
            "missingCellCount": missing_cell_count,
            "missingByColumn": missing_by_column,
            "duplicateRowCount": duplicate_count,
            "categoricalProfiles": categorical_profiles[:12],
        },
        quality_findings,
    )

    numeric_needed = bool(active_tools & {"distribution_profile", "relationship_screen", "group_comparison"})
    numeric = (
        _numeric_columns(frame, [config["identifierColumn"]] if config["identifierColumn"] else [])
        if numeric_needed else {}
    )
    if numeric_needed and config["measureColumn"] and config["measureColumn"] not in numeric:
        raise ValueError("选择的主指标不是可分析的数值字段（至少需要 4 个有效数值）")
    distribution_fields: list[dict[str, Any]] = []
    distribution_findings: list[dict[str, Any]] = []
    distribution_numeric = (
        {config["measureColumn"]: numeric[config["measureColumn"]]}
        if config["measureColumn"] and "distribution_profile" in active_tools
        else numeric if "distribution_profile" in active_tools else {}
    )
    iqr_policy = {
        item["column"]: item
        for item in (repair_plan or {}).get("summary", {}).get("iqrScreening", [])
        if item.get("column")
    }
    for column, series in distribution_numeric.items():
        values = series.dropna().astype(float)
        if len(values) < 4:
            continue
        q1 = float(values.quantile(0.25))
        q3 = float(values.quantile(0.75))
        iqr = q3 - q1
        assessment = iqr_policy.get(column)
        if is_derived_rate(column):
            assessment = {"status": "skipped", "method": "IQR_3.0", "reason": "比率或变化率需先核对分子、分母和计算口径，不做全列 IQR 判错。"}
            outlier_mask = pd.Series(False, index=series.index)
        elif repair_plan is not None:
            outlier_mask = (
                (series < assessment["lowerBound"]) | (series > assessment["upperBound"])
                if assessment and assessment.get("status") == "applied"
                else pd.Series(False, index=series.index)
            )
        else:
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
            "iqrScreening": assessment or {"status": "legacy", "method": "IQR_1.5"},
            "histogram": _histogram(values),
        }
        distribution_fields.append(field)
        if field["outlierCount"]:
            distribution_findings.append({
                "type": "distribution_outlier",
                "title": f"“{column}”有 {field['outlierCount']} 个统计异常候选",
                "severity": "review",
                "explanation": "按本次适用性检查后的 3×IQR 极端值规则筛出，仅表示需要核对，不等于数据错误。" if repair_plan is not None else "按 1.5×IQR 规则筛出，仅表示需要核对，不等于数据错误。",
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
    for rank_column, total_column in (
        _rank_total_pairs(frame.columns) if "rank_consistency" in active_tools else []
    ):
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
    screened_pairs = 0
    relationship_numeric = (
        _relationship_numeric_columns(
            frame, [config["identifierColumn"]] if config["identifierColumn"] else [],
        )
        if "relationship_screen" in active_tools else {}
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
            screened_pairs += 1
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
    if "relationship_screen" in active_tools and config["timeColumn"] and config["measureColumn"]:
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
            sample_indexes = sorted(set(
                [round(index * (len(prepared) - 1) / min(len(prepared) - 1, 59)) for index in range(min(len(prepared), 60))]
                if len(prepared) > 1 else [0]
            ))
            trend_points = []
            for index in sample_indexes:
                time_value = prepared.iloc[index]["time"]
                label = time_value.isoformat() if hasattr(time_value, "isoformat") else str(time_value)
                trend_points.append({"label": label, "value": _finite(prepared.iloc[index]["measure"])})
            cadence: dict[str, Any] = {
                "validTimeCount": int(sortable_time.notna().sum()),
                "missingOrInvalidTimeCount": int(sortable_time.isna().sum()),
                "duplicateTimeCount": int(prepared["time"].duplicated().sum()),
                "regularCadence": False,
                "gapCandidateCount": 0,
            }
            if time_method == "datetime_order":
                intervals = prepared["time"].drop_duplicates().diff().dt.total_seconds().dropna()
                positive = intervals[intervals > 0]
                if len(positive) >= 6:
                    median_interval = float(positive.median())
                    regular = float(((positive - median_interval).abs() <= 0.05 * median_interval).mean()) >= 0.8
                    cadence.update({
                        "regularCadence": regular,
                        "medianIntervalSeconds": _finite(median_interval),
                        "largestIntervalSeconds": _finite(positive.max()),
                        "gapCandidateCount": int((positive > 2 * median_interval).sum()) if regular else 0,
                    })
            trend_summary = {
                "timeColumn": config["timeColumn"],
                "measureColumn": config["measureColumn"],
                "comparableRows": int(len(prepared)),
                "firstValue": _finite(first_value),
                "lastValue": _finite(last_value),
                "absoluteChange": _finite(change),
                "changePct": _finite(change_pct) if change_pct is not None else None,
                "method": time_method,
                "cadence": cadence,
                "points": trend_points,
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
            if cadence["gapCandidateCount"]:
                correlation_findings.append({
                    "type": "time_gap_candidate",
                    "title": f"“{config['timeColumn']}”有 {cadence['gapCandidateCount']} 个采样间隔线索",
                    "severity": "review",
                    "explanation": "按本文件主要采样间隔比较得到；可能是计划停采或工况变化，不等于记录缺失。",
                    "details": {"timeColumn": config["timeColumn"], **cadence},
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
            "screenedPairCount": screened_pairs,
            "candidates": correlations[:10],
            "timeTrend": trend_summary,
            "blockedReason": relationship_blocker,
        },
        correlation_findings,
    )

    group_comparisons: list[dict[str, Any]] = []
    category_cross_tabs: list[dict[str, Any]] = []
    if "group_comparison" in active_tools and len(frame) >= 8 and numeric:
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
                groups = prepared.groupby("category")["measure"].agg(["count", "mean", "median", "std"])
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
                    "highestMedian": _finite(high["median"]),
                    "highestStd": _finite(high["std"]),
                    "highestCount": int(high["count"]),
                    "lowestGroup": str(groups.index[-1]),
                    "lowestMean": _finite(low["mean"]),
                    "lowestMedian": _finite(low["median"]),
                    "lowestStd": _finite(low["std"]),
                    "lowestCount": int(low["count"]),
                    "meanGap": _finite(float(high["mean"] - low["mean"])),
                    "groupCount": int(len(groups)),
                })
    if "group_comparison" in active_tools and len(frame) >= 8:
        category_columns: list[str] = []
        for column in frame.columns:
            if _is_identifier(column) or column in {config["identifierColumn"], config["timeColumn"], config["measureColumn"]}:
                continue
            values = frame[column].dropna().astype(str).str.strip()
            values = values[values != ""]
            if len(values) < 8 or pd.to_numeric(values, errors="coerce").notna().mean() >= 0.8 and column != config["groupColumn"]:
                continue
            if 2 <= values.nunique() <= 10:
                category_columns.append(str(column))
        if config["groupColumn"] in category_columns:
            pairs = [(config["groupColumn"], other) for other in category_columns if other != config["groupColumn"]]
        else:
            pairs = [(left, right) for index, left in enumerate(category_columns) for right in category_columns[index + 1:]]
        for left, right in pairs[:2]:
            prepared = frame[[left, right]].dropna().astype(str).apply(lambda series: series.str.strip())
            prepared = prepared[(prepared[left] != "") & (prepared[right] != "")]
            if len(prepared) < 8 or prepared[left].nunique() < 2 or prepared[right].nunique() < 2:
                continue
            counts = pd.crosstab(prepared[left], prepared[right])
            cells = [
                {"row": str(row), "column": str(column), "count": int(count),
                 "rowSharePct": _finite(100 * count / counts.loc[row].sum())}
                for row in counts.index for column, count in counts.loc[row].items()
            ]
            category_cross_tabs.append({
                "rowColumn": left, "columnColumn": right, "pairedRows": int(len(prepared)),
                "rowCategoryCount": int(len(counts.index)), "columnCategoryCount": int(len(counts.columns)),
                "cells": cells[:20], "truncated": len(cells) > 20,
            })
    group_comparisons.sort(key=lambda item: abs(item["meanGap"] or 0), reverse=True)
    group_findings = [
        {
            "type": "group_difference",
            "title": f"“{item['measureColumn']}”在“{item['categoryColumn']}”分组间存在描述性差异",
            "severity": "info",
            "explanation": (
                f"当前样本中“{item['highestGroup']}”均值 {item['highestMean']:.2f}，"
                f"“{item['lowestGroup']}”均值 {item['lowestMean']:.2f}；"
                f"两组中位数分别为 {item['highestMedian']:.2f}、{item['lowestMedian']:.2f}。"
                "该差异未经显著性或混杂因素检验。"
            ),
            "details": item,
        }
        for item in group_comparisons[:5]
    ]
    record_tool(
        "group_comparison",
        "分组差异探索",
        {"comparisonCount": len(group_comparisons), "comparisons": group_comparisons[:10],
         "categoryCrossTabs": category_cross_tabs},
        group_findings,
    )

    repair_summary = (repair_plan or {}).get("summary") or {}
    repair_ref = next((tool["evidenceRef"] for tool in tools if tool["key"] == "quality_audit"), "")
    if "quality_audit" in active_tools and repair_summary.get("safeFixCount", 0):
        recommendations.append({
            "priority": "high",
            "action": "先预览并导出安全修正版",
            "rationale": f"有 {repair_summary['safeFixCount']} 项仅涉及首尾空格的安全修正；应用前仍需用户勾选确认。",
            "evidenceRefs": [repair_ref],
            "automatic": False,
        })
    if "quality_audit" in active_tools and repair_summary.get("reviewCount", 0):
        recommendations.append({
            "priority": "high",
            "action": "按单元格位置核对人工复核项",
            "rationale": f"有 {repair_summary['reviewCount']} 项缺失、越界、重复或统计异常候选，系统不会猜测替换值。",
            "evidenceRefs": [repair_ref],
            "automatic": False,
        })
    if "relationship_screen" in active_tools and correlations:
        recommendations.append({
            "priority": "medium",
            "action": "围绕高相关字段补做业务验证",
            "rationale": "当前相关筛查只能发现共变线索；需要结合时间、分组和业务机制验证，不能直接写成因果结论。",
            "evidenceRefs": [_tool_ref(run_id, "relationship_screen")],
            "automatic": False,
        })
    if "group_comparison" in active_tools and group_comparisons:
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

    priority_refs = {_tool_ref(run_id, key) for key in active_tools}
    findings.sort(key=lambda item: (
        item["evidenceRefs"][0] not in priority_refs,
        {"high": 0, "medium": 1, "review": 2, "info": 3}.get(item["severity"], 4),
    ))
    findings = findings[:MAX_TOOL_FINDINGS]
    output = {
        "version": "local_analysis_tools_v1",
        "goal": selected_goal,
        "goalLabel": ANALYSIS_GOALS[selected_goal],
        "analysisConfig": config,
        "summary": {
            "executedToolCount": len(tools),
            "actuallyExecutedToolCount": len(tools),
            "reusedToolCount": 0,
            "findingCount": len(findings),
            "recommendationCount": len(recommendations),
            "rowCount": int(len(frame)),
        },
        "executedTools": tools,
        "execution": {
            "mode": "selected" if execute_tool_keys is not None else "full",
            "executedToolKeys": [tool["key"] for tool in tools],
            "reusedToolKeys": [],
        },
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
    output["analysisProcess"] = build_analysis_process(output, repair_plan)
    return output


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
            time_trend = payload.get("timeTrend") or {}
            safe_payload = {
                "method": payload.get("method"),
                "eligibleFieldCount": payload.get("eligibleFieldCount"),
                "eligibleFields": payload.get("eligibleFields", [])[:30],
                "candidateCount": payload.get("candidateCount"),
                "screenedPairCount": payload.get("screenedPairCount"),
                "candidates": payload.get("candidates", [])[:10],
                "timeTrend": {
                    key: time_trend.get(key) for key in (
                        "timeColumn", "measureColumn", "comparableRows", "firstValue",
                        "lastValue", "absoluteChange", "changePct", "method", "cadence",
                    )
                } if time_trend else None,
                "blockedReason": payload.get("blockedReason"),
            }
        else:
            safe_payload = {
                "comparisonCount": payload.get("comparisonCount"),
                "comparisons": [
                    {key: item.get(key) for key in (
                        "categoryColumn", "measureColumn", "highestMean", "highestCount",
                        "highestMedian", "highestStd", "lowestMean", "lowestCount",
                        "lowestMedian", "lowestStd", "meanGap", "groupCount",
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
