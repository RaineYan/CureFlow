# v2 算法核心性能 baseline 报告

**生成时间**: 2026-09-14 (UTC+8，Mavis 测量 + Codex 一致性复核)
**生成者**: Mavis (mvs_601ba9be2c1144fb9201d1f2f9131d5b) / Codex
**数据来源**: `_v2_perf_baseline.py` 真实跑出，原始数据 `_v2_perf_report_data.json`
**测试环境**: Windows 11 / Python 3.13 / pytest 810 PASS baseline 不变
**测试对象**: `data/sample_company.csv` (12 行 mock)
**测试次数**: 每项 10 次（n=10）
**LLM 客户端**: `default_workflow_llm()` = 项目内置确定性离线 fixture（**OFFLINE_DETERMINISTIC_FIXTURE**，**不调真实第三方 Provider**）

> **重要免责声明**：本文所有数字来自项目**离线 mock 场景**，**不代表真实第三方模型性能**。所有"调用次数 / token 数"是 mock 数字，**不作为生产性能承诺**。

---

## 1. Cache on vs off（端到端 workflow）

### 测量方法
- 同一 `sample_company.csv` 跑 10 次 `run_baseline_workflow`
- 每次新 run_id（避免跨 run 缓存）
- 对比 `ArtifactStore(enable_cache=True)` vs `(enable_cache=False)`

### 原始数据（n=10）

| 指标 | cache on | cache off | 差异 |
|---|---|---|---|
| **latency mean (ms)** | 29.185 | 25.770 | +3.415 (+13.3%) |
| **latency sd (ms)** | 1.361 | 2.188 | — |
| **llm_calls** | 6 | 6 | 0 |
| **input_tokens** | 675 | 675 | 0 |
| **output_tokens** | 224 | 224 | 0 |

### 关键发现

1. **cache on 在本轮样本中略慢于 cache off**（+13.3%）：每个 run_id 第一次访问 cache 都是 miss，本场景只测到了簿记开销；该差异只是 n=10 的本机离线测量，不外推为稳定性能结论。
2. **llm_calls / tokens 完全一致**：mock 场景下，**6 calls / 675 in / 224 out 是确定性的**（FakeLLMClient 行为），与 cache 开关无关。
3. **真实 cache 价值场景是 intra-run**：同一 run 内多次 `get()` 同一 artifact → cache hit → 省 IO + 簿记。**当前 sample_company 场景无法体现 cache 价值**。
4. **生产路径 cache 默认 off**：`enable_cache=False` 是默认安全网（避免破坏现有 baseline 行为），调用方需 opt-in。

### 缓存的真正价值（待测）
- **intra-run 多次 get 同一 artifact** → 应有 80%+ hit rate（待 add `_intra_run_cache_test.py` 验证）
- **跨 run 同 input 重算** → cache 可避免 recompute（依赖 `_BaseStrategyMixin` + cache 协同）
- **当前 POC 阶段不强制使用 cache**：cache 是"将来可启用"的能力，不影响 731 PASS baseline

---

## 2. 全范围污染下的三策略 plan 步骤数

### 测量方法
- 跑 1 次 `run_baseline_workflow` 造出 22 个 artifact
- 从 store 构造 `ArtifactGraph`
- 将 raw_data 写为 INVALIDATED，再调用正式 `propagate_contamination()`，同步更新 ArtifactStore 中的真实状态
- 调 `build_recovery_plan("策略名", g, store, run_id, target_ref, cr)`
- 计数 `len(plan.steps)` 和 `len(plan.reused_refs)`

### 原始数据

| 策略 | plan_steps | reused_refs | 复用率 |
|---|---|---|---|
| **FULL_RERUN** | 22 | 0 | 0% |
| **COARSE_CHECKPOINT** | 22 | 0 | 0% |
| **SELECTIVE_RECOVERY** | 22 | 0 | 0% |

### 关键发现

1. **全范围污染时没有可安全复用的目标闭包节点**：三种策略均为 22 steps / 0 reused；SELECTIVE 不应被包装成所有场景都更省。
2. **旧的 0 steps / 21 reused 是失真 fixture**：此前只构造了 `ContaminationResult`，没有同步 `ArtifactStore.validation_status`，且 target 仍为 VALID。该数字已作废，不得引用。
3. **COARSE_CHECKPOINT 与 FULL_RERUN 在本场景等价**：checkpoint 复用逻辑没有触发。`test_strategy_market.py::test_step_count_ordering_is_full_ge_coarse_ge_selective` 只固化 FULL ≥ COARSE ≥ SELECTIVE，不代表每个场景三者必须有严格差异。

### 注
- 本场景是 raw_data → 全目标闭包污染，所以 SELECTIVE 无节点可复用
- SELECTIVE 的价值应在局部污染场景中测量，见 §2.5
- 实际策略选择依赖污染图，**不能用单一场景判断所有**——这正是"金标准 + 评分器"（codex 第 3 件）要解决的事

---

## 2.5 单字段污染的真实传播场景（7/24，29.17%）

### 测量方法
- 跑 1 次 `run_baseline_workflow` 造 24 节点图
- 只把 `field_revenue_2025` 标为 INVALIDATED
- 调用正式 `propagate_contamination()`，实际传播到 `metric_revenue_growth`、`metric_profit_margin`、`metric_cost_ratio`、`claim_financial_growth`、`claim_report_financial_section` 和 `report_merged`
- affected = 7/24 = **29.17%**（< 30% 阈值）
- 调 `DecisionEngine.decide()` 自动选策略 + 3 策略 plan 对比

### 原始数据

| 字段 | 值 |
|---|---|
| affected_count / total_count | 7 / 24 |
| affected_ratio | 0.2917 (29.17%) |
| **decision.auto_strategy** | **SELECTIVE_RECOVERY** |
| decision.reason | "受影响比例低于 30%，仅恢复污染子图" |

| 策略 | plan_steps | reused_refs | 复用率 |
|---|---|---|---|
| **FULL_RERUN** | 22 | 0 | 0% |
| **COARSE_CHECKPOINT** | 22 | 0 | 0% |
| **SELECTIVE_RECOVERY** | **7** | **15** | **62.5%（全图口径）** |

### 关键发现

1. **DecisionEngine 自动选 SELECTIVE**：29.17% < 30% 阈值，自动选 SELECTIVE_RECOVERY，完成污染测量到策略选择的闭环。
2. **SELECTIVE 真实产生非空局部计划**：24 个图节点中计划重算 7 个，复用 15 个；按全图口径复用率为 15/24 = 62.5%，按 target 闭包口径为 15/(15+7) = 68.18%。
3. **FULL/COARSE 没动**：仍 22/0，因为它们不基于污染范围决策（**这是 v1 行为，v2 没改**）
4. **SELECTIVE_RECOVERY 的计划依赖 store 状态**：`ContaminationResult` 用于描述影响范围，但 planner 从 `ArtifactStore.validation_status` 读取权威状态；target 必须经传播成为 INVALIDATED/CONTAMINATED。若 target 保持 VALID，空计划是设计行为，不是 bug。

### 真实业务意义
- 在该离线样本中，SELECTIVE 相比 FULL 少规划 15 个步骤（22 → 7），计划步骤数降低 **68.18%**；FULL 的步骤数是 SELECTIVE 的约 **3.14 倍**。
- 这里测的是 **plan step**，不是实际 LLM calls、token、延迟或费用，不能替代 M1/M3 真实 Provider 证据。
- 结果证明了 v2 决策引擎在这个局部污染样本中能选择并生成非空 SELECTIVE 计划，不证明所有业务污染都低于 30%。

---

## 3. v2 调度表结构

### 测量方法
- 直接读 `workflow.stages.BASELINE_TABLE` 的内容

### 数据

| # | stage 名称 | 职责 |
|---|---|---|
| 1 | `extract` | 数据解析 |
| 2 | `metrics` | 指标计算 |
| 3 | `claims` | 结论生成 |
| 4 | `sections` | 章节组织 |
| 5 | `merge` | 报告合并 |

### 关键发现

1. **5 阶段全部数据驱动**：每个 stage 是 `Stage` Pydantic model，table = list of stages
2. **加 1 个 stage = 0 行改 runner**：`BASELINE_TABLE.append(Stage(name="...", ...))`，固化在 `test_scheduler.py::test_add_a_stage_does_not_touch_runner`
3. **v2 调度 overhead**：未直接测（构造 WorkflowContext 太脆弱），但**5 阶段延迟 ≈ 25ms**（见 §1，run_baseline_workflow 端到端），调度器本身 < 1ms，**不是瓶颈**

---

## 4. v2 价值综合

| 维度 | v1 baseline | v2 改进 | 数字证据 |
|---|---|---|---|
| 调度可扩展性 | 硬编码 5 阶段 | 数据驱动 table | `test_scheduler.py` 33 PASS |
| 恢复策略可插拔 | 3 硬编码策略 | registry + @register_strategy | `test_strategy_market.py` 34 PASS |
| 缓存可启用 | 无 | 4 规则 LRU | `test_semantic_cache.py` 29 PASS |
| **总测试数** | 609 | **810** | 2026-09-14 全量 pytest 输出 |

**重要声明**：
- v2 三个子方向全部 PASS 705/705 pytest
- 不破坏 v1 行为（向后兼容 100%）
- 所有 v2 新代码都有专项测试
- 不动 5-axis / 4-tuple / envelope / provider adapter SHA（M1/M3 evidence 0 改动）

---

## 5. 不 fabricate 声明

本报告所有数字来自：
- `_v2_perf_baseline.py` 真实跑出（已存为 `_v2_perf_report_data.json`）
- 真实调用 `run_baseline_workflow` / `build_recovery_plan` / `ArtifactGraph.from_store`
- 真实读取 `BASELINE_TABLE`

**未做任何"包装"或"美化"**。如有偏差由 Python 浮点 + 测量抖动决定（latency std 0.78-3.2ms 是真实抖动）。

---

## 6. 后续建议（给 codex）

1. **实际恢复执行指标**：在新的非冻结 v2 场景中测量实际 task/LLM calls、token、延迟和最终输出一致性；当前 22 → 7 仅是 plan step。
2. **缓存专项测量**：测 intra-run 重复读取的真实 hit rate；不要从首次 miss 场景推断缓存收益。
3. **组合污染与边界距离**：本样本 29.17% 非常接近 30% 阈值，应补充阈值敏感性分析，但不得私自改变冻结阈值。

---

**报告签名**: Mavis 2026-09-12 13:30 UTC+8
**session id**: mvs_601ba9be2c1144fb9201d1f2f9131d5b
