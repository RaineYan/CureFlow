export default function AIRecoverySummary({ result, activeView }) {
  const recovery = result?.aiRecovery;
  if (!recovery?.sourceRunId || !["overview", "recovery", "workflow"].includes(activeView)) return null;
  const reused = recovery.reusedFindingCount + recovery.reusedActionCount;
  const regenerated = recovery.regeneratedFindingCount + recovery.regeneratedActionCount;
  const cost = result.cost || {};
  return <section className="analysis-history-control" aria-label="AI 增量解读执行结果">
    <h2>AI 结论更新</h2>
    <p>{recovery.mode === "reuse_all" ? "证据一致，直接复用原有条目，本轮没有调用模型。"
      : recovery.mode === "partial" ? "仅将受影响证据交给 AI，保留证据一致的原有条目。"
      : "本次不能安全复用旧解读，已执行完整证据解读。"}</p>
    <p>保留 {reused} 条 · 重新生成 {regenerated} 条 · 本轮调用 {cost.llmCalls ?? 0} 次</p>
    <p>{cost.usageType === "provider_reported"
      ? `本轮实际用量：输入 ${cost.inputTokens} / 输出 ${cost.outputTokens} tokens`
      : recovery.mode === "reuse_all" ? "本轮模型用量：0 tokens"
      : "模型未上报完整用量，本轮 token 消耗未知。"}</p>
    {recovery.mode === "partial" && <small>未发送的证据块留在本机，合并后已重新核对全部引用；结论内容仍需人工复核。</small>}
  </section>;
}
