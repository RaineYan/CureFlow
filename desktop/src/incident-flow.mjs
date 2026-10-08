const count = value => Math.max(0, Number(value) || 0);

// Reader-facing projection only. This never upgrades rule clues to confirmed
// errors or a planned recovery to an executed selective rerun.
export function buildIncidentFlow(result, { pendingRecovery = null, completedRecovery = null } = {}) {
  if (!result) {
    return {
      source: "尚未分析文件",
      dataClueCount: 0,
      claimReviewCount: 0,
      steps: [
        { key: "locate", title: "定位疑点", status: "待开始", detail: "先导入文件，核对工作表与字段。", view: "workspace" },
        { key: "impact", title: "查看影响", status: "待分析", detail: "确认错误后，才能判断哪些结果可能失效。", view: "workflow" },
        { key: "repair", title: "人工修正", status: "未开始", detail: "只对核实的项目生成修正版，不覆盖原件。", view: "recovery" },
        { key: "verify", title: "重新验证", status: "未开始", detail: "修正版重新分析后，才比较前后结果。", view: "recovery" },
      ],
      boundary: "没有文件结果时，不展示预制故障、节省数字或恢复结论。",
    };
  }

  const plan = result.currentRecoveryPlan;
  const dataClueCount = count(plan?.summary?.issueCount ?? result.repairPlan?.summary?.issueCount);
  const claimReviewCount = (result.claims || []).filter(
    claim => ["needs_review", "rejected"].includes(claim.grounding?.verdict),
  ).length;
  const aiReviewCount = count(result.interpretationReview?.records?.length);
  const impactCount = (plan?.impactedTools || []).length;
  const reusableCount = (plan?.reusableTools || []).length;
  const recoveryExecution = completedRecovery?.recoveryExecution;
  const actualExecutedCount = count(recoveryExecution?.executedToolKeys?.length);
  const actualReusedCount = count(recoveryExecution?.reusedToolKeys?.length);
  const firstIssue = plan?.issues?.[0];
  const location = firstIssue?.location;
  const firstLocation = location?.cell
    ? `${location.sheet ? `${location.sheet}!` : ""}${location.cell}`
    : null;
  const hasClues = dataClueCount > 0 || claimReviewCount > 0 || aiReviewCount > 0;
  const locating = hasClues
    ? `${dataClueCount} 项数据规则线索、${claimReviewCount} 条待复核断言、${aiReviewCount} 次模型解读人工复核${firstLocation ? `；首项在 ${firstLocation}` : ""}。`
    : "本次规则未标出数据或断言问题；这不等于绝对正确。";
  const impact = recoveryExecution?.actualSelectiveExecution
    ? `本次修正版实际重算 ${actualExecutedCount} 个本地工具、复用 ${actualReusedCount} 个；AI 解读与报告已重新生成。`
    : dataClueCount
    ? `若数据线索核实，计划使 ${impactCount} 个已执行工具失效、${reusableCount} 个工具可复用；其下游结论需重审。`
    : claimReviewCount
      ? "AI 断言未通过或需复核；报告相应陈述暂不采信，尚无逐句传播定位。"
      : aiReviewCount
        ? "已定位到被复核的模型解读条目及读者报告；原始数据和其他本地工具不受此次文字修正影响。"
      : "本次规则未识别失效步骤，暂无需要执行的恢复计划。";
  const repair = completedRecovery
    ? `已将 ${count(completedRecovery.appliedCount)} 项确认修正写入新副本；原文件未覆盖。`
    : pendingRecovery
      ? `修正版已载入，应用 ${count(pendingRecovery.appliedCount)} 项确认修正，等待复测。`
      : dataClueCount
        ? "先核对原始记录；只有确认后的项目才可生成修正版。"
        : claimReviewCount
          ? "逐条核对 AI 断言与引用；当前没有自动改写结论的修正。"
          : aiReviewCount
            ? `已人工复核 ${aiReviewCount} 次模型解读；原始结果保留，修正记录可追溯。`
          : "当前没有待执行的修正；仍可人工复查分析结论。";
  const verify = completedRecovery
    ? `已重新分析：规则线索 ${count(completedRecovery.beforeIssueCount)} → ${count(completedRecovery.afterIssueCount)} 项。`
    : pendingRecovery
      ? "新副本尚未重新分析，不能称为已恢复。"
      : aiReviewCount && !dataClueCount
        ? "读者报告已在本地重新生成，证据身份已核验；文字内容来自人工确认，未获机器真实性证明，也未调用模型。"
      : "尚无修正版复测；计划步骤和预计复用不等于实际节省。";

  return {
    source: result.input?.filename || "本次文件",
    dataClueCount,
    claimReviewCount,
    steps: [
      { key: "locate", title: "定位疑点", status: hasClues ? "待核对" : "规则检查完成", detail: locating, view: dataClueCount ? "workspace" : claimReviewCount ? "grounding" : "workspace" },
      { key: "impact", title: "查看影响", status: recoveryExecution?.actualSelectiveExecution ? "已执行" : dataClueCount ? "仅计划" : claimReviewCount ? "待复核" : aiReviewCount ? "已定位" : "暂无失效", detail: impact, view: aiReviewCount && !dataClueCount && !claimReviewCount ? "workspace" : "workflow" },
      { key: "repair", title: "人工修正", status: completedRecovery ? "已生成副本" : pendingRecovery ? "待复测" : aiReviewCount ? "已复核" : "未执行", detail: repair, view: aiReviewCount && !dataClueCount ? "workspace" : "recovery" },
      { key: "verify", title: "重新验证", status: completedRecovery ? "已复测" : aiReviewCount && !dataClueCount ? "报告已重建" : "未复测", detail: verify, view: aiReviewCount && !dataClueCount ? "workspace" : "recovery" },
    ],
    boundary: recoveryExecution?.actualSelectiveExecution
      ? "本次重算与复用数量来自实际执行记录；只表示本地分析工具的执行差异，不自动等同于调用费用或时间节省。"
      : aiReviewCount && !dataClueCount
        ? "模型解读的人工复核仅重建相关报告，不重新计算数据；证据身份检查不等于证明文字内容真实。底层断言若仍待核对，应另行处理。"
        : "数据项是规则线索，未经人工核实不能称为错误；当前显示的影响与复用仍是计划，修正版复测完成后才记录实际执行。",
  };
}
