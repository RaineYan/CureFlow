# CureFlow 合成决策 Fixture 自动评分报告

> **证据边界**：这 15 个 fixture 是人工构造的确定性决策引擎单元测试，不是真实 workflow 执行产生的外部金标准。期望策略、受影响节点和最低 reduction 均由 fixture 预先给定，因此下列 100% 表示“实现与合成测试合同一致”，不表示真实场景准确率或泛化能力。
>
> reduction 表示合成图中的计划重跑范围缩减，不替代 M1/M3 的真实 Provider 实验结果；不得把本报告称为真实 Provider benchmark 或独立标注金标准。

- Fixtures：15
- Precision（策略匹配率）：100.00%
- Recall（受影响节点识别率均值）：100.00%
- Reduction mean（图节点重跑缩减均值）：43.33%
- Passed：15/15

| Fixture | Case | 期望策略 | 实际策略 | Recall | Reduction | PASS |
|---|---|---|---|---:|---:|---|
| case_a_coarse.json | CASE_A | COARSE_CHECKPOINT | COARSE_CHECKPOINT | 100.00% | 50.00% | PASS |
| case_a_full.json | CASE_A | FULL_RERUN | FULL_RERUN | 100.00% | 0.00% | PASS |
| case_a_selective.json | CASE_A | SELECTIVE_RECOVERY | SELECTIVE_RECOVERY | 100.00% | 80.00% | PASS |
| case_b_coarse.json | CASE_B | COARSE_CHECKPOINT | COARSE_CHECKPOINT | 100.00% | 50.00% | PASS |
| case_b_full.json | CASE_B | FULL_RERUN | FULL_RERUN | 100.00% | 0.00% | PASS |
| case_b_selective.json | CASE_B | SELECTIVE_RECOVERY | SELECTIVE_RECOVERY | 100.00% | 80.00% | PASS |
| case_c_coarse.json | CASE_C | COARSE_CHECKPOINT | COARSE_CHECKPOINT | 100.00% | 50.00% | PASS |
| case_c_full.json | CASE_C | FULL_RERUN | FULL_RERUN | 100.00% | 0.00% | PASS |
| case_c_selective.json | CASE_C | SELECTIVE_RECOVERY | SELECTIVE_RECOVERY | 100.00% | 80.00% | PASS |
| case_d_coarse.json | CASE_D | COARSE_CHECKPOINT | COARSE_CHECKPOINT | 100.00% | 50.00% | PASS |
| case_d_full.json | CASE_D | FULL_RERUN | FULL_RERUN | 100.00% | 0.00% | PASS |
| case_d_selective.json | CASE_D | SELECTIVE_RECOVERY | SELECTIVE_RECOVERY | 100.00% | 80.00% | PASS |
| case_e_coarse.json | CASE_E | COARSE_CHECKPOINT | COARSE_CHECKPOINT | 100.00% | 50.00% | PASS |
| case_e_full.json | CASE_E | FULL_RERUN | FULL_RERUN | 100.00% | 0.00% | PASS |
| case_e_selective.json | CASE_E | SELECTIVE_RECOVERY | SELECTIVE_RECOVERY | 100.00% | 80.00% | PASS |
