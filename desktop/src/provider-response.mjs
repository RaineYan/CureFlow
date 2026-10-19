export function assertLiveProviderResult(result, selectedProvider) {
  const verifiedReuse = result.provider?.reused === true
    && result.provider.calledThisRun === false
    && result.aiRecovery?.mode === "reuse_all"
    && result.aiRecovery.evidenceVerified === true
    && result.aiRecovery.reusedFindingCount > 0
    && result.aiRecovery.sourceRunId === result.recoveryExecution?.sourceRunId
    && typeof result.aiRecovery.sourceRunId === "string"
    && result.cost?.llmCalls === 0 && result.cost.totalTokens === 0;
  if (!result.provider?.live || !Number.isInteger(result.cost?.llmCalls) || (result.cost.llmCalls < 1 && !verifiedReuse)) {
    throw new Error("本次未完成真实模型调用，已阻止展示报告；请检查模型连接并重试");
  }
  const expectedMode = `${selectedProvider.mode.toUpperCase()}_LIVE`;
  if (result.mode !== expectedMode || result.provider.name !== selectedProvider.key) {
    throw new Error("返回结果的模型与本次选择不一致，已阻止展示；请重新选择模型并运行");
  }
}
