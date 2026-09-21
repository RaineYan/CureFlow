# CureFlow 决策阈值敏感性分析

> 保持现有 30%/70% 规则不变；本报告不声称阈值最优。

| affected | ratio | strategy |
|---:|---:|---|
| 0/100 | 0.00% | SELECTIVE_RECOVERY |
| 29/100 | 29.00% | SELECTIVE_RECOVERY |
| 30/100 | 30.00% | COARSE_CHECKPOINT |
| 31/100 | 31.00% | COARSE_CHECKPOINT |
| 50/100 | 50.00% | COARSE_CHECKPOINT |
| 69/100 | 69.00% | COARSE_CHECKPOINT |
| 70/100 | 70.00% | FULL_RERUN |
| 71/100 | 71.00% | FULL_RERUN |
| 100/100 | 100.00% | FULL_RERUN |

## 观察

- 恰好 30% 进入 COARSE_CHECKPOINT。
- 恰好 70% 进入 FULL_RERUN。
- 小图中增加一个节点可能跨越边界，因此比例决策具有离散跳变。
