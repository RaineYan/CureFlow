# H5 误判复现记录（未修复）

从项目根运行 `python -m docs.h5_false_positive_repro`，只使用临时 ArtifactStore 和本地语义规则，不调用真实模型。若默认临时目录不可写，可将任务专用环境变量 `CUREFLOW_REPRO_TMP` 指向可写目录。基线断言“营收同比增长60.78%，利润率达到47.56%”获得 `grounded`、无风险码；对照句只增加描述性的 `recommendation system`，不发出建议、不扩大结论范围。

2026-09-25 实测：

```text
baseline grounded [] []
neutral needs_review ['H5_SCOPE_EXPANSION'] ["recommendation token found: 'recommend'"]
REPRODUCED: neutral mention of recommendation system is marked H5
```

链路：`validation.semantic.recommendation_boundary_check()` 用子串 `recommend` 匹配英文文本，生成 `recommendation token found: 'recommend'`；`validation.claim_grounding._codes_from_semantic()` 据该 issue 标 H5。将下游判断从 `in` 改为 `startswith` 仍会命中同一 issue，因此不是有效修复。附文给出的“测试声明”样例既不含触发词，也缺少完整的合法证据构造，无法复现此问题。

可讨论的修复方向：为建议/范围类语义问题引入结构化原因；或在上游针对英文词语使用边界与上下文规则，避免把描述性名词当行动建议。不能简单删掉 H5，也不能仅改下游字符串匹配。确定合同后，把“描述 recommendation system 不产生 H5”和“真正建议仍产生 H5”放入常规回归测试，并保留现有 7 类风险码、3 态判定与本次观测风险率口径。

当前仅记录误判，不改生产规则。此复现脚本刻意验证“现状确实有误”，不加入日常 `tests/` 集，避免用一个已知失败的预期阻断无关的全量测试。
