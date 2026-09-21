# CureFlow 变更日志 (CHANGELOG)

> 本文件记录项目从 v1 到 v2 + 4 大方向的演进。每版本含日期 / 改动 / 测试数 / 关键约束。
>
> **格式约定**：
> - `[日期]` · 改动摘要
> - 测试数字：累计 pytest PASS（baseline 不破）
> - **证据原则**：M1/M3 60-trial evidence 始终 frozen，不被新 benchmark 替代

---

## v1 阶段（Day 1–13，2026-08-18 → 2026-08-29）

### [2026-08-18] 项目根 + artifacts/ 初始化
- Day 1 spec：`Artifact` schema + `ArtifactStore` JSON file backend
- 9 个 ArtifactStore 单元测试
- 累计：9 PASS

### [2026-08-18] workflow/tasks.py + provenance/decorator.py
- Day 2-3：CSV 解析 + 字段抽取 + 工具任务
- 累计：~50 PASS

### [2026-08-18] contamination/ + validation/
- Day 4-6：污染检测 + 错误传播 + 验证器
- 累计：~150 PASS

### [2026-08-19] 5 阶段 baseline workflow
- Day 6：`run_baseline_workflow` 端到端（extract → recompute → claim → section → merge）
- 累计：~250 PASS

### [2026-08-19] Day 13.5 LLM usage type 3 态枚举
- `UsageType` = `SYNTHETIC / PROVIDER_REPORTED / UNAVAILABLE`
- `FakeLLMClient` 输出 `SYNTHETIC`，token 标"fixture" 不被冒充 Provider
- 累计：~400 PASS

### [2026-08-19] Day 13.6 FINAL follow-up
- `propagate_contamination` 加 cross-version seed expansion → **后续 revert**（破坏版本语义）
- Day 7 版本语义锁定
- 累计：~500 PASS

### [2026-08-29] M1 (Qwen 5-repeat)
- 60 trial 真实 Provider 实验
- SELECTIVE 20/20 = 2 calls exact (SD=0)
- calls reduction 66.67% 95% CI [66.67, 66.67]
- 累计：574 PASS + 4 real Qwen records

### [2026-08-27] M2 envelope robustness
- `llm/parse.py` + `workflow/claim_tasks.py` 修 envelope 分类
- 9 regression tests
- HTTP 200 + provider envelope → `INFRA_FAILURE_<KIND>`（不是 `RESPONSE_SCHEMA_ERROR`）
- 累计：583 PASS

### [2026-08-29] M3 (DeepSeek) Provider Adapter + Phase A pilot + Phase B 5-repeat
- 新增 Provider Adapter（env var 开关）：SHA `fc2dfa73e6a33b58` / `1bf55418fa232426`
- Phase A：12 trial, 11/12 provider_success
- Phase B：52/60 provider_success, 17/20 FULL→SELECTIVE 66.67% calls
- **M3 evidence frozen**（SHA 锁 / 284 文件 / 0 mismatch）
- 累计：600 PASS

### [2026-08-29] M3B reconciliation
- 106/106 PASS
- 4 stats JSONs + 5 per-repeat retry summaries + report text + 284 historical SHA all match
- 累计：609 PASS

---

## v2 阶段（2026-09-11 → 2026-09-12）

### [2026-09-11] v2 调度器 → 数据驱动
- **新增** `workflow/scheduler.py`：`Stage` Pydantic model + `Scheduler` class + `WorkflowContext` + `TableValidationError`
- **新增** `workflow/stages.py`：5 个独立 stage 函数 + `BASELINE_TABLE` 声明表
- **改** `workflow/runner.py`：保留 `run_baseline_workflow` 旧签名，内部转调 `Scheduler().run(BASELINE_TABLE, ctx)`
- **新增** `tests/test_scheduler.py`：33 测试（含旧接口兼容 / 加 stage 0 行改 runner）
- 累计：642 PASS

### [2026-09-11] v2 策略市场 → 可插拔
- **新增** `recovery/strategy.py`：`Strategy` Protocol + `StrategyRegistry` + `@register_strategy`
- **新增** `recovery/strategies/{full_rerun,coarse_checkpoint,selective_recovery}.py`
- **重写** `recovery/planner.py`：薄 adapter 包装 registry
- **改** `recovery/executor.py`：`execute()` 通过 `strategy.dispatch_step(step, ctx)` 替代内部分支
- **改** `recovery/models.py`：`RecoveryPlan.strategy_name` 默认 `SELECTIVE_RECOVERY`（向后兼容）
- **新增** `tests/test_strategy_market.py`：34 测试
- 累计：676 PASS

### [2026-09-11] v2 Artifact 缓存 → 语义感知 LRU
- **新增** `artifacts/cache.py`：`SemanticLRUCache` + `EvictionPolicy` ABC + `LRUEvictionPolicy` + `SemanticLRUEvictionPolicy`
- **改** `artifacts/schema.py`：`Artifact` 加 `access_count` + `last_access_at`
- **改** `artifacts/store.py`：加 `enable_cache=False`（默认 off，安全网）+ 4 规则：LRU 兜底 + 中心性加权 + 失败拒绝 + size cap
- **改** `artifacts/__init__.py`：导出 cache 公开符号
- **新增** `tests/test_semantic_cache.py`：29 测试
- 累计：705 PASS

### [2026-09-11] 桌面端 POC + tests
- **新增** `desktop/` (Electron + Vite + React 19) + `cureflow_app/server.py`
- `RecoveryExecutor.execute()` 真接通 backend
- mock 透明：`mode: "OFFLINE_DETERMINISTIC_FIXTURE"` + disclaimer
- 不接 API key（用 `default_workflow_llm()` 离线 fixture）
- **新增** `tests/test_server.py` (10) + `tests/test_desktop_health_contract.py` (4) + `tests/test_desktop_health_contract.py` (4)
- **改** `cureflow_app/server.py` / `desktop/{electron,src}/` 等
- 累计：731 PASS

### [2026-09-12] 桌面端补强
- **新增** `desktop/scripts/smoke-test.cjs`（`npm run smoke-test`）
- **改** `desktop/electron/main.cjs`：30s health check + App.jsx "本地引擎未响应" banner
- **改** `desktop/package.json`：加 `dev:web:full`（vite + backend 并发）
- **改** `cureflow_app/server.py`：CORS env 化
- **改** `desktop/README.md` / root `README.md`：prerequisites + Desktop POC 段
- 累计：仍 731 PASS（已稳）

---

## 4 大方向（2026-09-13）

### [2026-09-13] v2 可解释影响图（v2 唯一缺口）
- **新增** `provenance/impact.py` + `provenance/impact_report.py`
- 合并 graph + propagation 输出**可读报告**（节点 + 路径 + Mermaid 图 + 复用建议）
- **新增** `tests/test_impact_report.py`
- **新增** `artifacts/impact_report_sample.md`：24 节点 / 41 边 / 11 路径 demo

### [2026-09-13] v2 跨策略智能决策引擎（v2 策略市场闭环）
- **新增** `recovery/decision.py` (66 行)：
  - `DecisionEngine.decide()` — 30% / 70% 阈值合同
  - `build_auto_plan()` — 自动选策略 + 调 registry
- **新增** `tests/test_recovery_decision.py`
- **边界合同**：`ratio < 0.30 → SELECTIVE` / `[0.30, 0.70) → COARSE` / `ratio >= 0.70 → FULL`

### [2026-09-13] v2 金标准 + 自动评分器
- **新增** `tests/golden_cases/` (15 fixture, case_a-e × full/coarse/selective)
- **新增** `_golden_evaluator.py` (run + render_report)
- **新增** `tests/test_golden_evaluator.py`
- **新增** `_golden_evaluator_report.md`：15/15 PASS / Precision 100% / Recall 100% / Reduction 43.33%
- **证据边界**：合成 fixture，不是真实 workflow 金标准

### [2026-09-13] v2 健壮性扩展
- **新增** `tests/test_robustness.py` (8.2 KB, 38 测试)：
  - 空 CSV / 1 行 / 10000 行 / 100MB cap / 6 metrics 0 denominator / GBK + utf-8-sig / 10 thread 并发
- **改** `workflow/tasks.py`：
  - `MAX_CSV_BYTES = 5 MB`
  - utf-8-sig 优先 + gbk fallback
  - `_safe_ratio`：0 denominator + non-finite 抛 ValueError
- 累计：810 PASS (+79)

---

## Mavis 评审与辅助（2026-09-13 → 2026-09-15）

### [2026-09-13] v2 perf baseline 量化
- **新增** `_v2_perf_baseline.py`（cache / 3 策略 / 小范围污染）
- **新增** `_v2_perf_report.md` (8.9 KB) — 不 fabricate 声明 + MOCK 标识 + 4 段
- **新增** `_v2_perf_report_data.json`

### [2026-09-13] README 重写
- **改** `README.md` (1287 → 6374 bytes)：产品视角 9 段

### [2026-09-14] 4 件集成评审
- **新增** `MAVIS_CODEX_INTEGRATED_REPORT.md` (4.4 KB)
  - 健壮性结论（38/38 PASS）
  - fixture 定位（合成 / 非真实金标准）
  - perf 纠偏（旧 `SELECTIVE 0/21` 作废）
  - SELECTIVE 关键前置（target 必须传播）
- **新增** `artifacts/collaboration/minimax_review.md` (15.8 KB)
  - 优先级 1 实施前审查
  - 13 指标 × 状态（8 已有 + 5 需新增）
  - **结论**：PASS WITH CONDITIONS

### [2026-09-14] v2 perf baseline 重测（小范围污染场景）
- **改** `_v2_perf_baseline.py`：加 `measure_small_contamination()`
- **修 fixture 缺陷**：构造污染后没改 store 状态（手动 `update_status` + 标 target 传播）
- 单字段污染：SELECTIVE **4 steps / 18 reused = 75% 复用率**（之前假数据 0/21 已作废）
- 全范围污染：3 策略都是 22/0（SELECTIVE 无优势）

### [2026-09-15] HANDOFF_TO_CODEX.md v2 增量 + CHANGELOG
- **改** `HANDOFF_TO_CODEX.md`：追加 v2 增量段（本文 §9-§15）
- **新增** `CHANGELOG.md`（本文件）：v1 → v2 → 4 大方向演进史

---

## 不在本变更日志范围（冻结 / 独立）

### 冻结 spec
- 5-axis 指标（`provider_success` / `recovery_execution_success` / `output_contract_valid` / `semantic_validation_success` / `trial_valid`）
- 4-tuple CANONICAL_CASES（`CASE_A/B/C/D`）
- envelope classifier 4 类（`INFRA_FAILURE_<KIND>`）
- provider adapter SHA `fc2dfa73e6a33b58` / `1bf55418fa232426`
- M1/M3 60-trial evidence（frozen）

### 待 Codex 接手（PASS WITH CONDITIONS）
- 优先级 1：实际执行级 v2 benchmark（`artifacts/collaboration/minimax_review.md` 已就绪）
- 优先级 2：影响图与 FULL/SELECTIVE trace diff
- 优先级 3：W3C PROV 兼容导出
- 优先级 4：30%/70% 阈值敏感性分析
- 优先级 5：可选成本-风险决策模式
- 优先级 6：组合污染与反事实恢复

---

**CHANGELOG 生成器**: MiniMax 2026-09-15 16:38 UTC+8
**session id**: `mvs_601ba9be2c1144fb9201d1f2f9131d5b`