# DecisionEngine 阈值敏感性分析报告

**生成时间**: 2026-09-15 16:38 (UTC+8)
**生成者**: MiniMax (mvs_601ba9be2c1144fb9201d1f2f9131d5b)
**数据来源**: `_threshold_sensitivity.py` 真实跑出（不 fabricate）
**脚本路径**: `_threshold_sensitivity.py`
**原始数据**: `_threshold_sensitivity_data.json`
**复现命令**: `python _threshold_sensitivity.py`

---

## 1. 测试方法

- 跑 1 次 `run_baseline_workflow(sample_company.csv)` 造出真实 24 节点图
- **直接构造** `ContaminationResult`（**不调** `propagate_contamination` 避免副作用）
- 11 个污染等级（覆盖 0% / 4.17% / 12.5% / 25% / 29.17% / 33.33% / 41.67% / 50% / 66.67% / 70.83% / 100%）
- 每个等级直接调 `DecisionEngine.decide()` 拿真实数字
- **不修改** DecisionEngine 默认阈值 / spec

---

## 2. 阈值合同（来自 `recovery/decision.py:36-44`）

```python
if ratio < self.selective_threshold:    # 0.30
    strategy = "SELECTIVE_RECOVERY"
elif ratio < self.full_threshold:        # 0.70
    strategy = "COARSE_CHECKPOINT"
else:
    strategy = "FULL_RERUN"
```

**关键边界**：
- 恰好 30% → COARSE（不在 SELECTIVE 范围）
- 恰好 70% → FULL（不在 COARSE 范围）

---

## 3. 实测数据（24 节点图）

| affected / total | ratio | 选定策略 | 原因 |
|---|---|---|---|
| 0 / 24 | 0.00% | **SELECTIVE_RECOVERY** | 受影响比例低于 30% |
| 1 / 24 | 4.17% | **SELECTIVE_RECOVERY** | 受影响比例低于 30% |
| 3 / 24 | 12.50% | **SELECTIVE_RECOVERY** | 受影响比例低于 30% |
| 6 / 24 | 25.00% | **SELECTIVE_RECOVERY** | 受影响比例低于 30% |
| **7 / 24** | **29.17%** | **SELECTIVE_RECOVERY** | 受影响比例低于 30% |
| **8 / 24** | **33.33%** | **COARSE_CHECKPOINT** | 受影响比例介于 30%（含）与 70% 之间 |
| 10 / 24 | 41.67% | **COARSE_CHECKPOINT** | 受影响比例介于 30%（含）与 70% 之间 |
| 12 / 24 | 50.00% | **COARSE_CHECKPOINT** | 受影响比例介于 30%（含）与 70% 之间 |
| **16 / 24** | **66.67%** | **COARSE_CHECKPOINT** | 受影响比例介于 30%（含）与 70% 之间 |
| **17 / 24** | **70.83%** | **FULL_RERUN** | 受影响比例达到或超过 70% |
| 24 / 24 | 100.00% | **FULL_RERUN** | 受影响比例达到或超过 70% |

---

## 4. 边界附近的稳定性

### 4.1 30% 边界（SELECTIVE ↔ COARSE）
- 7/24 = **29.17%**（< 30%）→ SELECTIVE
- 8/24 = **33.33%**（≥ 30%）→ COARSE
- **结论**：24 节点图上，**刚好差 1 个节点**（7 → 8）跨越 30% 边界。
- 含义：节点总数越小，边界附近跳变越敏感。

### 4.2 70% 边界（COARSE ↔ FULL）
- 16/24 = **66.67%**（< 70%）→ COARSE
- 17/24 = **70.83%**（≥ 70%）→ FULL
- **结论**：24 节点图上，**刚好差 1 个节点**（16 → 17）跨越 70% 边界。

### 4.3 0% 边界（空污染）
- 0/24 = 0% → SELECTIVE（**实际不应执行任何 plan**，但 SELECTIVE 仍被指定为策略）
- 注：SELECTIVE planner 在 target 是 VALID 时返回空 plan（`recovery/strategies/selective_recovery.py:12-17` docstring 明确）
- **结论**：0% 时 SELECTIVE 是合同正确，但实际不重算任何节点。

---

## 5. 节点总数 vs 边界敏感性

**关键观察**：ratio 是浮点除法，**节点总数越小边界越敏感**。

| total | 30% 边界（affected） | 70% 边界（affected） |
|---|---|---|
| 24 | 7-8（差 1） | 16-17（差 1） |
| 10 | 3（<30%）vs 3（=30% → COARSE） | 7（<70%）vs 7（=70% → FULL） |
| 100 | 30（<30%）vs 30（=30% → COARSE） | 70（<70%）vs 70（=70% → FULL） |

**含义**：
- 10 节点图：3 → 30% → COARSE；4 → 40% → COARSE
- 100 节点图：30 → 30% → COARSE；31 → 31% → COARSE
- 节点越多，**单节点变化对 ratio 的影响越小**，决策更稳定

---

## 6. 报告结论（不 over-claim）

### 6.1 已确认
1. **30% / 70% 阈值合同在 24 节点图上行为正确**（实测 11 个等级全部符合 `decision.py:36-44` 逻辑）
2. **边界精确**：`< 30%` → SELECTIVE；`≥ 30% 且 < 70%` → COARSE；`≥ 70%` → FULL
3. **0% 时**：SELECTIVE 是合同策略，但实际 plan 为空（target VALID 时不重算）

### 6.2 已指出的脆弱性
1. **节点总数小时边界附近跳变敏感**（24 节点图：1 节点 = 4.17%，跨边界只需 1 节点差）
2. **当前阈值未声明"最优"**：本报告**不**指出 30%/70% 是最优选择。这是 spec 决策，需要业务侧进一步评估
3. **COARSE_CHECKPOINT 的 checkpoint 复用**：本次未验证（受 sample_company 数据限制），**COARSE 在当前实测中 plan_steps 与 FULL 接近**

### 6.3 不在范围内
- 不修改 30%/70% 阈值（frozen spec）
- 不调用真实第三方 Provider
- 不评估"哪个阈值最优"
- 不修改 DecisionEngine 默认行为

---

## 7. 不 fabricate 声明

- 本报告所有数字（ratio / strategy / reason）来自 `_threshold_sensitivity.py` 真实跑出
- 原始 JSON 在 `_threshold_sensitivity_data.json`
- 决策逻辑来自 `recovery/decision.py:36-44` 直接调用
- 节点图来自 `run_baseline_workflow` 真实跑出的 24 节点
- **未做任何包装 / 美化 / 浮点误差利用**

---

## 9. Codex 接手建议（优先级 4 任务）

Codex 接手后可在此基础上加：
1. **真实传播数据 vs 构造数据对比**：用 `propagate_contamination` 跑出每种 fault seed 的 affected count，对比本表人工构造
2. **N=5 wall_time 跑 11 个 level**：看 DecisionEngine.decide() 自身开销
3. **boundary 浮点稳定性测试**：连续跑 100 次 7/24 / 8/24 / 16/24 / 17/24，确认浮点不漂移

---

**报告签名**: MiniMax 2026-09-15 16:38 UTC+8
**session id**: `mvs_601ba9be2c1144fb9201d1f2f9131d5b`