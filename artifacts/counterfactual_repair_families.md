# CureFlow v2 组合污染反事实恢复

- 目标：`v2_counterfactual/report@v1`
- 污染源：3
- 最小充分修复集合：4

| # | 修复节点 | target clean | run clean | residual |
|---:|---|---:|---:|---:|
| 1 | `v2_counterfactual/claim@v1` | true | false | 4 |
| 2 | `v2_counterfactual/report@v1` | true | false | 5 |
| 3 | `v2_counterfactual/data_fault@v1, v2_counterfactual/evidence_fault@v1` | true | false | 1 |
| 4 | `v2_counterfactual/evidence_fault@v1, v2_counterfactual/metric@v1` | true | false | 2 |

> “最小”指对当前 target 的集合包含关系最小，不等于货币成本最低，也不保证整个 run 无残余污染。
