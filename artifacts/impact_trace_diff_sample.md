# FULL 与 SELECTIVE 执行差异

> usage type: `SYNTHETIC`；token 指标不得跨 usage type 比较。

| 指标 | FULL | SELECTIVE | 降低 |
|---|---:|---:|---:|
| 计划步骤 | 22 | 7 | 68.18% |
| 实际 task calls | 14 | 7 | 50.00% |
| 实际 LLM calls | 6 | 2 | 66.67% |
| fixture tokens | 899 | 318 | 64.63% |

- SELECTIVE 复用节点：4
- SELECTIVE 残余污染：0
- 两策略恢复输出一致：true
