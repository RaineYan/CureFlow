"""Adaptive tabular ingestion and deterministic profiling for Desktop uploads.

The established business workflow remains unchanged.  This module decides
whether uploaded columns can be mapped to that contract; unrelated tables are
profiled locally instead of being forced into revenue/cost semantics.
"""
from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd

from artifacts import Artifact, ArtifactRef, ArtifactStore, ArtifactType


BUSINESS_REQUIRED = ("year", "revenue", "cost", "users", "orders", "visits")
BUSINESS_ALIASES = {
    "year": {"year", "年份", "年度", "年"},
    "month": {"month", "月份", "月"},
    "revenue": {
        "revenue", "sales", "turnover", "salesrevenue", "营业收入", "营收", "销售额", "收入",
    },
    "cost": {"cost", "costs", "expense", "expenses", "operatingcost", "成本", "费用", "运营成本"},
    "users": {"users", "usercount", "activeusers", "mau", "用户", "用户数", "活跃用户", "月活用户"},
    "orders": {"orders", "ordercount", "订单", "订单数", "成交订单"},
    "visits": {"visits", "traffic", "pageviews", "pv", "访问", "访问量", "访客数", "流量"},
}
DATE_ALIASES = {"date", "datetime", "timestamp", "time", "日期", "时间", "统计日期"}
MAX_GENERIC_ROWS = 10_000
MAX_GENERIC_COLUMNS = 100


def _normalized_header(value: Any) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(value).strip().lower())


def _read_csv_text(csv_text: str) -> pd.DataFrame:
    try:
        frame = pd.read_csv(StringIO(csv_text))
    except pd.errors.EmptyDataError as exc:
        raise ValueError("数据文件必须包含表头和至少一行数据") from exc
    if frame.empty:
        raise ValueError("数据文件必须包含至少一行数据")
    if len(frame) > MAX_GENERIC_ROWS:
        raise ValueError("通用表格最多支持 10000 行数据")
    if len(frame.columns) > MAX_GENERIC_COLUMNS:
        raise ValueError("通用表格最多支持 100 列")
    frame.columns = [str(column).strip() or f"column_{index + 1}" for index, column in enumerate(frame.columns)]
    return frame


def prepare_tabular_csv(csv_text: str) -> tuple[str, dict[str, Any]]:
    """Return normalized CSV and a truthful detected-schema description."""
    frame = _read_csv_text(csv_text)
    aliases = {
        alias: canonical
        for canonical, values in BUSINESS_ALIASES.items()
        for alias in values
    }
    mapped: dict[str, str] = {}
    rename: dict[str, str] = {}
    for column in frame.columns:
        canonical = aliases.get(_normalized_header(column))
        if canonical and canonical not in mapped:
            mapped[canonical] = column
            rename[column] = canonical

    derived: list[str] = []
    missing_time_parts = [name for name in ("year", "month") if name not in mapped]
    business_measures_present = all(
        name in mapped for name in BUSINESS_REQUIRED if name != "year"
    )
    if missing_time_parts and business_measures_present:
        date_column = next(
            (column for column in frame.columns if _normalized_header(column) in DATE_ALIASES),
            None,
        )
        if date_column is not None:
            parsed = pd.to_datetime(frame[date_column], errors="coerce")
            if parsed.notna().mean() >= 0.8:
                if "year" not in mapped:
                    frame["year"] = parsed.dt.year
                    mapped["year"] = f"{date_column} → year"
                    derived.append("year")
                if "month" not in mapped:
                    frame["month"] = parsed.dt.month
                    mapped["month"] = f"{date_column} → month"
                    derived.append("month")

    frame = frame.rename(columns=rename)
    missing = [column for column in BUSINESS_REQUIRED if column not in frame.columns]
    mode = "business" if not missing else "generic_profile"
    return frame.to_csv(index=False, lineterminator="\n"), {
        "analysisMode": mode,
        "analysisModeLabel": "经营指标分析" if mode == "business" else "通用表格分析",
        "detectedColumns": [str(column) for column in frame.columns],
        "columnMapping": mapped,
        "derivedColumns": derived,
        "missingBusinessFields": missing,
    }


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _ref(run_id: str, artifact_id: str) -> ArtifactRef:
    return ArtifactRef(run_id=run_id, artifact_id=artifact_id, version=1)


def _ref_text(reference: ArtifactRef) -> str:
    return reference.to_ref_string()


def _display(metric: str, value: float | int) -> str:
    if metric in {"completeness", "outlier_rate"}:
        return f"{float(value) * 100:.2f}%"
    return f"{int(value):,}"


SEMANTIC_KIND_LABELS = {
    "identifier": "标识符",
    "score": "连续成绩",
    "rank": "顺序排名",
    "count": "计数",
    "numeric": "连续数值",
    "category": "分类",
    "text": "文本",
}


def _semantic_kind(column: Any, values: pd.Series, numeric_candidate: bool) -> str:
    header = _normalized_header(column)
    if (
        header in {"id", "学号", "编号", "序号", "工号", "证件号"}
        or header.endswith("id")
        or "学号" in header
    ):
        return "identifier"
    if numeric_candidate and any(token in header for token in ("排名", "名次", "rank")):
        return "rank"
    if numeric_candidate and any(token in header for token in ("总人数", "人数", "数量", "count")):
        return "count"
    if numeric_candidate and any(token in header for token in ("成绩", "得分", "分数", "score")):
        return "score"
    if numeric_candidate:
        return "numeric"
    unique_count = int(values.astype(str).nunique(dropna=True)) if len(values) else 0
    if len(values) >= 10 and unique_count <= max(12, int(len(values) * 0.25)):
        return "category"
    return "text"


def _data_sufficiency(row_count: int) -> dict[str, Any]:
    """Describe what a deterministic profile may and may not conclude.

    This is deliberately a transparent rule, not a statistical confidence
    score.  A small table can still support schema and missing-value facts,
    but it cannot support population-level or trend claims.
    """
    if row_count < 4:
        return {
            "level": "insufficient",
            "label": "证据不足",
            "summary": f"当前只有 {row_count} 行数据，只能核对字段、原始值和缺失情况，不能据此判断分布、趋势、相关性或异常模式。",
            "allowedConclusions": ["字段名称与类型", "本文件中的缺失单元格", "本文件中的重复记录与原始值范围"],
            "blockedConclusions": ["总体分布与趋势", "字段相关性或因果关系", "可靠的异常值与排名判断"],
            "reasonCodes": ["TOO_FEW_ROWS_FOR_DISTRIBUTION"],
        }
    if row_count < 30:
        return {
            "level": "limited",
            "label": "样本有限",
            "summary": f"当前有 {row_count} 行数据，可做描述性画像；分布与异常结果对新增样本较敏感，不宜外推到总体。",
            "allowedConclusions": ["字段与缺失画像", "当前样本的描述统计", "用于人工复核的初步异常线索"],
            "blockedConclusions": ["总体规律或稳定趋势", "因果关系", "脱离当前文件的预测结论"],
            "reasonCodes": ["SMALL_SAMPLE_DESCRIPTIVE_ONLY"],
        }
    return {
        "level": "descriptive",
        "label": "可做描述统计",
        "summary": f"当前有 {row_count} 行数据，足以生成当前文件的描述性画像；仍不能仅凭本报告证明因果关系或总体规律。",
        "allowedConclusions": ["字段与缺失画像", "当前样本的描述统计", "用于人工复核的异常线索"],
        "blockedConclusions": ["未经检验的因果关系", "总体幻觉率", "超出当前文件范围的预测结论"],
        "reasonCodes": [],
    }


def analyze_generic_table(csv_text: str, filename: str, data_dir: Path) -> dict[str, Any]:
    """Profile an arbitrary rectangular table with deterministic evidence."""
    frame = _read_csv_text(csv_text)
    run_id = f"desktop_profile_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{uuid4().hex[:8]}"
    upload_dir = data_dir / "uploads" / run_id
    upload_dir.mkdir(parents=True, exist_ok=False)
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename).name)[:100] or "table.csv"
    csv_path = upload_dir / safe_name
    csv_path.write_text(csv_text, encoding="utf-8")

    store = ArtifactStore(base_dir=str(data_dir / "artifacts"))
    raw = Artifact(
        artifact_id="raw_data",
        artifact_type=ArtifactType.RAW_DATA,
        producer_task="profile_tabular_data",
        run_id=run_id,
        value={
            "sha256": hashlib.sha256(csv_text.encode("utf-8")).hexdigest(),
            "row_count": int(len(frame)),
            "columns": [str(column) for column in frame.columns],
        },
        meta={"csv_path": str(csv_path), "source_filename": Path(filename).name},
    )
    store.save(raw)
    raw_ref = _ref(run_id, "raw_data")

    blank_mask = frame.isna() | frame.apply(lambda column: column.map(lambda value: isinstance(value, str) and not value.strip()))
    total_cells = max(int(frame.shape[0] * frame.shape[1]), 1)
    missing_cells = int(blank_mask.to_numpy().sum())
    completeness = 1.0 - missing_cells / total_cells
    duplicate_rows = int(frame.duplicated().sum())
    sufficiency = _data_sufficiency(len(frame))

    numeric: dict[str, pd.Series] = {}
    distribution_numeric: dict[str, pd.Series] = {}
    column_profiles: list[dict[str, Any]] = []
    outlier_cells = 0
    numeric_cells = 0
    for column in frame.columns:
        nonblank = frame.loc[~blank_mask[column], column]
        converted = pd.to_numeric(nonblank, errors="coerce")
        numeric_candidate = bool(len(nonblank)) and float(converted.notna().mean()) >= 0.8
        semantic_kind = _semantic_kind(column, nonblank, numeric_candidate)
        is_numeric = numeric_candidate and semantic_kind != "identifier"
        profile: dict[str, Any] = {
            "column": str(column),
            "non_empty": int((~blank_mask[column]).sum()),
            "missing": int(blank_mask[column].sum()),
            "missing_rate": float(blank_mask[column].mean()),
            "kind": semantic_kind,
            "kindLabel": SEMANTIC_KIND_LABELS[semantic_kind],
            "distributionEligible": semantic_kind in {"score", "numeric"},
        }
        if is_numeric:
            values = converted.dropna().astype(float)
            numeric[str(column)] = values
            if semantic_kind in {"score", "numeric"}:
                distribution_numeric[str(column)] = values
                numeric_cells += int(len(values))
            q1 = float(values.quantile(0.25))
            q3 = float(values.quantile(0.75))
            iqr = q3 - q1
            count = (
                int(((values < q1 - 1.5 * iqr) | (values > q3 + 1.5 * iqr)).sum())
                if semantic_kind in {"score", "numeric"} and len(values) >= 4 and iqr > 0
                else 0
            )
            outlier_cells += count
            profile.update({
                "min": _finite(values.min()),
                "max": _finite(values.max()),
                "mean": _finite(values.mean()),
                "std": _finite(values.std(ddof=0)),
                "outliers_iqr": count,
            })
        column_profiles.append(profile)

    outlier_rate = outlier_cells / numeric_cells if numeric_cells else 0.0
    semantic_summary: dict[str, Any] = {
        "identifiers": [],
        "scoreFields": [],
        "rankChecks": [],
        "categoryFields": [],
        "reviewItems": [],
    }
    profile_by_column = {item["column"]: item for item in column_profiles}
    for column in frame.columns:
        name = str(column)
        profile = profile_by_column[name]
        nonblank = frame.loc[~blank_mask[column], column]
        if profile["kind"] == "identifier":
            duplicate_count = int(nonblank.astype(str).duplicated().sum())
            item = {
                "column": name,
                "missingCount": int(profile["missing"]),
                "duplicateCount": duplicate_count,
            }
            semantic_summary["identifiers"].append(item)
            if item["missingCount"] or duplicate_count:
                semantic_summary["reviewItems"].append(
                    f"标识字段“{name}”有 {item['missingCount']} 个缺失值、{duplicate_count} 个重复值"
                )
        elif profile["kind"] == "score":
            values = pd.to_numeric(nonblank, errors="coerce").dropna().astype(float)
            negative_count = int((values < 0).sum())
            item = {
                "column": name,
                "count": int(len(values)),
                "mean": _finite(values.mean()),
                "median": _finite(values.median()),
                "min": _finite(values.min()),
                "max": _finite(values.max()),
                "negativeCount": negative_count,
            }
            semantic_summary["scoreFields"].append(item)
            if negative_count:
                semantic_summary["reviewItems"].append(
                    f"成绩字段“{name}”出现 {negative_count} 个负值，建议核对原始记录或缺考规则"
                )
        elif profile["kind"] == "category":
            counts = nonblank.astype(str).value_counts(dropna=False)
            semantic_summary["categoryFields"].append({
                "column": name,
                "levelCount": int(len(counts)),
                "topValues": [
                    {"value": str(value), "count": int(count)}
                    for value, count in counts.head(5).items()
                ],
            })

    count_columns = {
        re.sub(r"(总人数|人数|数量|count)$", "", _normalized_header(column)): str(column)
        for column in frame.columns
        if profile_by_column[str(column)]["kind"] == "count"
    }
    for column in frame.columns:
        name = str(column)
        if profile_by_column[name]["kind"] != "rank":
            continue
        prefix = re.sub(r"(排名|名次|rank)$", "", _normalized_header(column))
        total_column = count_columns.get(prefix)
        if not total_column:
            continue
        rank_values = pd.to_numeric(frame[column], errors="coerce")
        total_values = pd.to_numeric(frame[total_column], errors="coerce")
        comparable = rank_values.notna() & total_values.notna()
        invalid = comparable & ((rank_values < 1) | (total_values < 1) | (rank_values > total_values))
        item = {
            "rankColumn": name,
            "totalColumn": total_column,
            "checkedRows": int(comparable.sum()),
            "outOfRangeRows": int(invalid.sum()),
        }
        semantic_summary["rankChecks"].append(item)
        if item["outOfRangeRows"]:
            semantic_summary["reviewItems"].append(
                f"“{name}”有 {item['outOfRangeRows']} 行超出“{total_column}”允许范围"
            )
    profile_artifact = Artifact(
        artifact_id="field_column_profiles",
        artifact_type=ArtifactType.FIELD,
        producer_task="profile_tabular_data",
        run_id=run_id,
        input_artifact_ids=[raw_ref],
        value=column_profiles,
    )
    store.save(profile_artifact)
    profile_ref = _ref(run_id, "field_column_profiles")

    metric_values: list[tuple[str, str, float | int, list[ArtifactRef]]] = [
        ("row_count", "数据行数", int(len(frame)), [raw_ref]),
        ("column_count", "字段数量", int(len(frame.columns)), [raw_ref]),
        ("completeness", "非空完整率", completeness, [profile_ref]),
        ("numeric_columns", "数值字段", len(numeric), [profile_ref]),
        ("duplicate_rows", "重复行", duplicate_rows, [raw_ref]),
        ("outlier_rate", "IQR 异常值率", outlier_rate, [profile_ref]),
    ]
    metric_refs: dict[str, ArtifactRef] = {}
    metrics: list[dict[str, Any]] = []
    for key, label, value, inputs in metric_values:
        artifact_id = f"metric_{key}"
        artifact = Artifact(
            artifact_id=artifact_id,
            artifact_type=ArtifactType.METRIC,
            producer_task="profile_tabular_data",
            run_id=run_id,
            input_artifact_ids=inputs,
            value=value,
        )
        store.save(artifact)
        reference = _ref(run_id, artifact_id)
        metric_refs[key] = reference
        metrics.append({
            "key": key,
            "label": label,
            "value": value,
            "display": "不可评估" if key == "outlier_rate" and sufficiency["level"] == "insufficient" else _display(key, value),
            "artifactRef": _ref_text(reference),
            "evidenceRefs": [_ref_text(item) for item in inputs],
        })

    worst = max(column_profiles, key=lambda item: item["missing_rate"])
    claims_spec = [
        (
            "data_quality",
            f"数据集包含 {len(frame):,} 行、{len(frame.columns)} 列，非空完整率为 {completeness * 100:.2f}%。",
            [metric_refs["row_count"], metric_refs["column_count"], metric_refs["completeness"]],
        ),
        (
            "missingness",
            f"缺失率最高的字段是“{worst['column']}”，缺失率为 {worst['missing_rate'] * 100:.2f}%。",
            [profile_ref, metric_refs["completeness"]],
        ),
        (
            "distribution",
            (
                f"识别出 {len(numeric)} 个数值字段，其中 {len(distribution_numeric)} 个适合连续分布检查；但当前仅 {len(frame)} 行，样本不足以可靠执行 IQR 异常判断。"
                if sufficiency["level"] == "insufficient"
                else f"识别出 {len(numeric)} 个数值字段，其中 {len(distribution_numeric)} 个适合连续分布检查；按 1.5×IQR 规则标记 {outlier_cells:,} 个异常单元格，占可检查数值单元格的 {outlier_rate * 100:.2f}%。排名、计数和标识符不参与异常值判断。"
            ),
            [profile_ref, metric_refs["numeric_columns"], metric_refs["outlier_rate"]],
        ),
    ]
    claims: list[dict[str, Any]] = []
    for domain, content, evidence in claims_spec:
        artifact_id = f"claim_{domain}"
        claim = Artifact(
            artifact_id=artifact_id,
            artifact_type=ArtifactType.CLAIM,
            producer_task="build_profile_claims",
            run_id=run_id,
            input_artifact_ids=evidence,
            evidence_ids=evidence,
            content=content,
        )
        store.save(claim)
        evidence_text = [_ref_text(item) for item in evidence]
        claims.append({
            "domain": domain,
            "content": content,
            "artifactRef": _ref_text(_ref(run_id, artifact_id)),
            "evidenceRefs": evidence_text,
            "grounding": {
                "verdict": "grounded",
                "evidence_coverage": 1.0,
                "risk_codes": [],
                "evidence": [
                    {"ref": reference, "source_status": "verified"}
                    for reference in evidence_text
                ],
            },
        })

    lines = [
        "# 通用表格分析报告",
        "",
        "## 先说结论",
        "",
        f"- 数据充分性：**{sufficiency['label']}**",
        f"- {sufficiency['summary']}",
        f"- 可以确认：{'；'.join(sufficiency['allowedConclusions'])}",
        f"- 不能据此确认：{'；'.join(sufficiency['blockedConclusions'])}",
        "",
        "## 当前文件的可核对事实",
        "",
        f"- 数据规模：{len(frame):,} 行 × {len(frame.columns)} 列",
        f"- 非空完整率：{completeness * 100:.2f}%",
        f"- 重复行：{duplicate_rows:,}",
        f"- 数值字段：{len(numeric)}",
        (
            "- IQR 异常值：不可评估（有效样本少于 4 行）"
            if sufficiency["level"] == "insufficient"
            else f"- IQR 异常值：{outlier_cells:,}（{outlier_rate * 100:.2f}%）"
        ),
        "",
        "## 字段画像",
        "",
        "| 字段 | 类型 | 缺失率 | 均值 | 最小值 | 最大值 | IQR 异常数 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for item in column_profiles[:30]:
        def number(name: str) -> str:
            value = item.get(name)
            return "—" if value is None else f"{float(value):.4g}"
        label = str(item["column"]).replace("|", "\\|")
        lines.append(
            f"| {label} | {item['kindLabel']} | {item['missing_rate'] * 100:.2f}% | "
            f"{number('mean')} | {number('min')} | {number('max')} | {item.get('outliers_iqr', '—')} |"
        )
    if len(column_profiles) > 30:
        lines.extend(["", f"其余 {len(column_profiles) - 30} 个字段未在报告中展开。"])
    lines.extend(["", "## 可操作检测", ""])
    if semantic_summary["identifiers"]:
        for item in semantic_summary["identifiers"]:
            lines.append(
                f"- 标识字段“{item['column']}”：缺失 {item['missingCount']}，重复 {item['duplicateCount']}"
            )
    if semantic_summary["scoreFields"]:
        for item in semantic_summary["scoreFields"]:
            lines.append(
                f"- 成绩字段“{item['column']}”：{item['count']:,} 个有效值，平均 {item['mean']:.2f}，中位数 {item['median']:.2f}，范围 {item['min']:.2f}–{item['max']:.2f}，负值 {item['negativeCount']}"
            )
    if semantic_summary["rankChecks"]:
        for item in semantic_summary["rankChecks"]:
            lines.append(
                f"- 排名范围“{item['rankColumn']} ≤ {item['totalColumn']}”：核对 {item['checkedRows']:,} 行，越界 {item['outOfRangeRows']} 行"
            )
    if semantic_summary["categoryFields"]:
        for item in semantic_summary["categoryFields"]:
            top_text = "、".join(
                f"{entry['value']}（{entry['count']}）" for entry in item["topValues"]
            )
            lines.append(
                f"- 分类字段“{item['column']}”：{item['levelCount']} 类；主要取值 {top_text}"
            )
    lines.extend(["", "## 需要人工核对", ""])
    if semantic_summary["reviewItems"]:
        lines.extend(f"- {item}" for item in semantic_summary["reviewItems"])
    else:
        lines.append("- 未发现标识重复、成绩负值或排名越界。")

    return {
        "runId": run_id,
        "mode": "OFFLINE_DETERMINISTIC_PROFILE",
        "modeLabel": "通用表格 · 本地确定性分析",
        "metrics": metrics,
        "claims": claims,
        "dataSufficiency": sufficiency,
        "semanticSummary": semantic_summary,
        "stages": [
            {"key": "extract", "label": "数据解析", "status": "success"},
            {"key": "schema", "label": "模式识别", "status": "success"},
            {"key": "quality", "label": "质量画像", "status": "success"},
            {"key": "statistics", "label": "统计分析", "status": "success"},
            {"key": "merge", "label": "报告合并", "status": "success"},
        ],
        "report": "\n".join(lines),
        "disclaimer": "该报告是本地确定性表格画像，不把任意字段强行解释为业务含义；充分性标签是透明行数规则，不是统计置信度。标识符、排名和计数字段不会被当作连续测量值执行 IQR；统计筛查不代表业务异常或因果结论。",
        "cost": {
            "llmCalls": 0,
            "inputTokens": 0,
            "outputTokens": 0,
            "totalTokens": 0,
            "latencyMs": 0.0,
            "usageType": "not_applicable",
            "estimatedListPriceUsd": None,
        },
        "provider": {"name": "local", "model": "deterministic-tabular-profiler", "live": False},
        "groundingSummary": {
            "method": "deterministic_profile_grounding_v1",
            "total_claims": len(claims),
            "grounded_claims": len(claims),
            "needs_review_claims": 0,
            "rejected_claims": 0,
            "observed_risk_rate_pct": None,
            "population_hallucination_rate_available": False,
            "interpretation": "本次未调用生成式模型，因此幻觉风险率不适用；展示内容均直接来自确定性表格画像及其证据引用。",
        },
        "routing": {"calls": 0, "automaticFallback": False, "mode": "generic_profile"},
    }


def analyze_generic_workbook(
    tables: list[dict[str, str]], filename: str, data_dir: Path
) -> dict[str, Any]:
    """Analyze a multi-sheet workbook as related tables, preserving roles."""
    if len(tables) < 2:
        raise ValueError("工作簿关系分析至少需要两个可见非空工作表")
    frames = [
        {"sheet": table["sheet"], "csvText": table["csvText"], "frame": _read_csv_text(table["csvText"])}
        for table in tables
    ]
    run_id = f"desktop_workbook_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{uuid4().hex[:8]}"
    upload_dir = data_dir / "uploads" / run_id
    upload_dir.mkdir(parents=True, exist_ok=False)
    store = ArtifactStore(base_dir=str(data_dir / "artifacts"))
    raw_refs: list[ArtifactRef] = []
    sheet_profiles: list[dict[str, Any]] = []

    for index, item in enumerate(frames, start=1):
        frame = item["frame"]
        safe_sheet = re.sub(r"[^A-Za-z0-9._-]+", "_", str(item["sheet"]))[:50] or f"sheet_{index}"
        csv_path = upload_dir / f"{index:02d}_{safe_sheet}.csv"
        csv_path.write_text(item["csvText"], encoding="utf-8")
        blank = frame.isna() | frame.apply(
            lambda column: column.map(lambda value: isinstance(value, str) and not value.strip())
        )
        non_empty = {str(column): int((~blank[column]).sum()) for column in frame.columns}
        missing_rate = {str(column): float(blank[column].mean()) for column in frame.columns}
        profile = {
            "sheet": str(item["sheet"]),
            "rows": int(len(frame)),
            "columns": [str(column) for column in frame.columns],
            "non_empty": non_empty,
            "missing_rate": missing_rate,
            "completeness": 1.0 - float(blank.to_numpy().sum()) / max(int(frame.shape[0] * frame.shape[1]), 1),
        }
        sheet_profiles.append(profile)
        artifact_id = f"raw_sheet_{index}"
        raw = Artifact(
            artifact_id=artifact_id,
            artifact_type=ArtifactType.RAW_DATA,
            producer_task="profile_workbook_tables",
            run_id=run_id,
            value={
                "sha256": hashlib.sha256(item["csvText"].encode("utf-8")).hexdigest(),
                "sheet": item["sheet"],
                "row_count": int(len(frame)),
                "columns": profile["columns"],
            },
            meta={"csv_path": str(csv_path), "source_filename": Path(filename).name},
        )
        store.save(raw)
        raw_refs.append(_ref(run_id, artifact_id))

    def role_index(patterns: tuple[str, ...], fallback: int) -> int:
        for index, profile in enumerate(sheet_profiles):
            normalized = _normalized_header(profile["sheet"])
            if any(pattern in normalized for pattern in patterns):
                return index
        return fallback

    training_index = role_index(("训练", "train"), 0)
    experiment_index = role_index(("实验", "test", "experiment"), 1 if len(frames) > 1 else 0)
    if experiment_index == training_index:
        experiment_index = next(index for index in range(len(frames)) if index != training_index)
    training = sheet_profiles[training_index]
    experiment = sheet_profiles[experiment_index]
    shared = [column for column in training["columns"] if column in experiment["columns"]]
    training_only = [column for column in training["columns"] if column not in experiment["columns"]]
    experiment_only = [column for column in experiment["columns"] if column not in training["columns"]]
    target_candidates = [
        column
        for column in shared
        if training["non_empty"][column] / max(training["rows"], 1) >= 0.8
        and experiment["non_empty"][column] / max(experiment["rows"], 1) <= 0.2
    ]
    time_columns = [
        column for column in shared
        if any(token in _normalized_header(column) for token in ("time", "date", "时间", "日期"))
    ]
    feature_columns = [
        column for column in shared
        if column not in target_candidates and column not in time_columns
    ]
    label_columns = [
        column for column in experiment_only
        if experiment["non_empty"][column] / max(experiment["rows"], 1) >= 0.8
    ]
    schema = {
        "training_sheet": training["sheet"],
        "experiment_sheet": experiment["sheet"],
        "shared_columns": shared,
        "training_only_columns": training_only,
        "experiment_only_columns": experiment_only,
        "target_candidates": target_candidates,
        "feature_columns": feature_columns,
        "time_columns": time_columns,
        "label_columns": label_columns,
        "sheets": sheet_profiles,
    }
    schema_artifact = Artifact(
        artifact_id="field_workbook_schema",
        artifact_type=ArtifactType.FIELD,
        producer_task="detect_workbook_roles",
        run_id=run_id,
        input_artifact_ids=raw_refs,
        value=schema,
    )
    store.save(schema_artifact)
    schema_ref = _ref(run_id, "field_workbook_schema")

    metric_values: list[tuple[str, str, int | float]] = [
        ("sheet_count", "有效工作表", len(sheet_profiles)),
        ("training_rows", "训练集行数", training["rows"]),
        ("experimental_rows", "实验集行数", experiment["rows"]),
        ("shared_columns", "共有字段", len(shared)),
        ("target_candidates", "候选目标列", len(target_candidates)),
        ("feature_columns", "共有候选特征", len(feature_columns)),
    ]
    metric_refs: dict[str, ArtifactRef] = {}
    metrics: list[dict[str, Any]] = []
    for key, label, value in metric_values:
        artifact_id = f"metric_{key}"
        metric = Artifact(
            artifact_id=artifact_id,
            artifact_type=ArtifactType.METRIC,
            producer_task="profile_workbook_tables",
            run_id=run_id,
            input_artifact_ids=[schema_ref],
            value=value,
        )
        store.save(metric)
        reference = _ref(run_id, artifact_id)
        metric_refs[key] = reference
        metrics.append({
            "key": key,
            "label": label,
            "value": value,
            "display": f"{int(value):,}",
            "artifactRef": _ref_text(reference),
            "evidenceRefs": [_ref_text(schema_ref)],
        })

    target_text = "、".join(f"“{column}”" for column in target_candidates) or "未发现"
    label_text = "、".join(f"“{column}”" for column in label_columns) or "未发现"
    claims_spec = [
        (
            "workbook_structure",
            f"工作簿包含 {len(sheet_profiles)} 个有效工作表；“{training['sheet']}”有 {training['rows']:,} 行，“{experiment['sheet']}”有 {experiment['rows']:,} 行。",
        ),
        (
            "target_detection",
            f"训练集有值而实验集主要为空的候选目标列：{target_text}。这是基于跨表缺失模式的候选识别，不等同于题目定义确认。",
        ),
        (
            "cross_sheet_quality",
            f"两表共有 {len(shared)} 个字段，实验集独有且数据完整的标签列为：{label_text}；共有候选特征列为 {len(feature_columns)} 个，是否可用仍取决于缺失率和题目定义。",
        ),
    ]
    claims: list[dict[str, Any]] = []
    for domain, content in claims_spec:
        artifact_id = f"claim_{domain}"
        evidence = [schema_ref]
        claim = Artifact(
            artifact_id=artifact_id,
            artifact_type=ArtifactType.CLAIM,
            producer_task="build_workbook_claims",
            run_id=run_id,
            input_artifact_ids=evidence,
            evidence_ids=evidence,
            content=content,
        )
        store.save(claim)
        evidence_text = [_ref_text(schema_ref)]
        claims.append({
            "domain": domain,
            "content": content,
            "artifactRef": _ref_text(_ref(run_id, artifact_id)),
            "evidenceRefs": evidence_text,
            "grounding": {
                "verdict": "grounded",
                "evidence_coverage": 1.0,
                "risk_codes": [],
                "evidence": [{"ref": evidence_text[0], "source_status": "verified"}],
            },
        })

    lines = [
        "# 多工作表关系分析报告",
        "",
        "## 工作表角色",
        "",
        "| 角色 | 工作表 | 行数 | 列数 | 完整率 |",
        "|---|---|---:|---:|---:|",
        f"| 训练集 | {training['sheet']} | {training['rows']:,} | {len(training['columns'])} | {training['completeness'] * 100:.2f}% |",
        f"| 实验集 | {experiment['sheet']} | {experiment['rows']:,} | {len(experiment['columns'])} | {experiment['completeness'] * 100:.2f}% |",
        "",
        "## 跨表字段关系",
        "",
        f"- 共有字段：{'、'.join(shared) or '无'}",
        f"- 实验集独有字段：{'、'.join(experiment_only) or '无'}",
        f"- 候选目标列：{'、'.join(target_candidates) or '未发现'}",
        f"- 时间列：{'、'.join(time_columns) or '未发现'}",
        f"- 共有候选特征：{'、'.join(feature_columns) or '未发现'}",
        "",
        "## 各表字段完整率",
        "",
        "| 字段 | 训练集非空率 | 实验集非空率 | 关系判断 |",
        "|---|---:|---:|---|",
    ]
    for column in sorted(set(training["columns"]) | set(experiment["columns"])):
        train_rate = training["non_empty"].get(column, 0) / max(training["rows"], 1)
        experiment_rate = experiment["non_empty"].get(column, 0) / max(experiment["rows"], 1)
        if column in target_candidates:
            relation = "候选目标列"
        elif column in label_columns:
            relation = "实验集标签列"
        elif column in shared:
            relation = "共有字段"
        elif column in training_only:
            relation = "仅训练集"
        else:
            relation = "仅实验集"
        safe_column = column.replace("|", "\\|")
        lines.append(f"| {safe_column} | {train_rate * 100:.2f}% | {experiment_rate * 100:.2f}% | {relation} |")

    return {
        "runId": run_id,
        "mode": "OFFLINE_DETERMINISTIC_WORKBOOK_PROFILE",
        "modeLabel": "多工作表 · 本地确定性关系分析",
        "metrics": metrics,
        "claims": claims,
        "stages": [
            {"key": "extract", "label": "读取全部工作表", "status": "success"},
            {"key": "schema", "label": "识别表间角色", "status": "success"},
            {"key": "quality", "label": "比较字段完整率", "status": "success"},
            {"key": "relations", "label": "检测目标与特征", "status": "success"},
            {"key": "merge", "label": "生成关系报告", "status": "success"},
        ],
        "report": "\n".join(lines),
        "disclaimer": "该结果依据工作表名称、共有字段和跨表缺失模式进行确定性识别；候选目标列仍需结合题目说明确认，未执行预测或填补。",
        "cost": {
            "llmCalls": 0,
            "inputTokens": 0,
            "outputTokens": 0,
            "totalTokens": 0,
            "latencyMs": 0.0,
            "usageType": "not_applicable",
            "estimatedListPriceUsd": None,
        },
        "provider": {"name": "local", "model": "deterministic-workbook-profiler", "live": False},
        "groundingSummary": {
            "method": "deterministic_workbook_grounding_v1",
            "total_claims": len(claims),
            "grounded_claims": len(claims),
            "needs_review_claims": 0,
            "rejected_claims": 0,
            "observed_risk_rate_pct": None,
            "population_hallucination_rate_available": False,
            "interpretation": "本次未调用生成式模型，因此幻觉风险率不适用；候选目标列来自确定性结构推断，不代表题目语义已确认。",
        },
        "routing": {"calls": 0, "automaticFallback": False, "mode": "workbook_profile"},
        "workbookSchema": schema,
    }
