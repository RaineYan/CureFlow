const displayNumber = value => new Intl.NumberFormat("zh-CN", { maximumSignificantDigits: 8 }).format(value);

export function reviewIssueTitle(issue) {
  if (issue?.type !== "statistical_outlier") return issue?.title || "待核对线索";
  const value = Number(issue.originalValue);
  const lower = Number(issue.evidence?.lowerBound);
  const upper = Number(issue.evidence?.upperBound);
  if (!Number.isFinite(value) || !Number.isFinite(lower) || !Number.isFinite(upper)) return issue.title;
  if (value > upper) return `${issue.location?.column || "该字段"}：高于统计筛查上限`;
  if (value < lower) return `${issue.location?.column || "该字段"}：低于统计筛查下限`;
  return issue.title;
}

export function explainReviewIssue(issue) {
  if (issue?.type !== "statistical_outlier") return issue?.reason || "请对照源文件核对这一项。";
  const value = Number(issue.originalValue);
  const lower = Number(issue.evidence?.lowerBound);
  const upper = Number(issue.evidence?.upperBound);
  if (!Number.isFinite(value) || !Number.isFinite(lower) || !Number.isFinite(upper)) return issue.reason;
  const side = value > upper ? "上限" : value < lower ? "下限" : null;
  if (!side) return issue.reason;
  const boundary = side === "上限" ? upper : lower;
  const distance = Math.abs(value - boundary);
  const field = issue.location?.column || "该字段";
  const sample = Number(issue.evidence?.numericCount);
  const sampleText = Number.isInteger(sample) && sample > 0 ? `（${sample} 个有效数值）` : "";
  const quartiles = Number.isFinite(issue.evidence?.q1) && Number.isFinite(issue.evidence?.q3)
    ? `中间一半数值位于 ${displayNumber(issue.evidence.q1)}–${displayNumber(issue.evidence.q3)}；`
    : "";
  const method = issue.evidence?.method === "IQR_3.0" ? "3×IQR 极端值" : issue.evidence?.method === "IQR_1.5" ? "1.5×IQR" : "IQR";
  return `为什么标记：${field}全列${sampleText}${quartiles}按 ${method} 方法得到统计筛查区间 ${displayNumber(lower)}–${displayNumber(upper)}。当前值 ${displayNumber(value)} 比${side} ${displayNumber(boundary)} ${side === "上限" ? "高" : "低"} ${displayNumber(distance)}。这不说明原值错误；请核对原始记录、单位和分组或时间背景，确认后再决定是否修改。`;
}
