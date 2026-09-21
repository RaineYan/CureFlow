# 断言证据审计与“幻觉率”口径

CureFlow Desktop 在每次分析后，对 3 条业务结论执行确定性证据审计。审计不调用另一个大模型，不修改原语义校验器，也不把审核意见写回原始产物。

## 输出

- 每条结论：`grounded`、`needs_review` 或 `rejected`
- 证据覆盖率与每条引用的生命周期状态：`verified`、`suspect`、`blocked`、`missing`
- 风险代码：
  - `H1_UNSUPPORTED_ASSERTION`：缺少可支持该断言的证据
  - `H2_DANGLING_REFERENCE`：引用指向不存在的产物版本
  - `H3_NUMERIC_DRIFT`：数字、方向或额外百分比与证据不一致
  - `H4_EVIDENCE_MISATTRIBUTION`：证据存在，但不属于该业务结论要求的证据类型
  - `H5_SCOPE_EXPANSION`：使用“所有、必然、始终”等超出样本范围的措辞
  - `H6_CAUSAL_EXTRAPOLATION`：仅凭相关性指标声称因果关系
  - `SOURCE_NOT_VERIFIED`：引用源处于可疑、污染或失效状态

## 关于“幻觉率”

接口字段 `groundingSummary.observed_risk_rate_pct` 的分母是**本次运行的结论数**，分子是被规则标记为 `needs_review` 或 `rejected` 的结论数。它只能回答“这次生成的结论中有多少被当前规则标记”，不能代表某模型在总体任务上的真实幻觉率。

若要报告模型级幻觉率，需要单独建设带人工标注的盲测集，预注册样本、标签规则和置信区间，并区分：

1. 证据可核验断言；
2. 无法由现有数据判定的断言；
3. 与证据矛盾的断言；
4. 缺少证据的断言。

当前 UI 因此明确显示“本次观测风险率”，并固定附带“不代表模型总体幻觉率”的说明。

## 任务路由边界

`TaskAwareLLMClient` 把 evidence claim 标记为 strong 能力偏好，把 grounded summary 标记为 default 能力偏好，并记录调用成功、失败、延迟与熔断状态。它只包裹已由 Desktop Provider gate 授权的 client：

- 不自动启用 OpenAI；
- 不自动换 Provider 或模型；
- 不自动 fallback；
- 连续失败达到阈值后停止后续调用，避免级联消耗；
- OpenAI 仍受每次运行 6-call 硬上限与 kill switch 约束。
