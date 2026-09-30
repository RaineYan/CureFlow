# CureFlow 变更日志 (CHANGELOG)

## [2026-10-01] 文件导入一致性与源码发布准备

- 桌面文件选择器与拖拽上传共用 CSV/XLSX 读取规则，修复 GBK 中文文件在桌面选择器中乱码的问题；统一空文件、大小上限检查，并检查读取期间文件大小变化。
- 共享读取模块纳入 Electron 打包目录，新增无前端源码目录条件下的加载回归测试。
- 冒烟测试改为等待真实服务就绪，隔离临时端口和用户目录；修复 Windows 直接启动 `npm.cmd` 的构建失败，支持 `--rebuild`。
- 后端打包不再把私有历史实验文件当作必需资源，支持显式选择 `CUREFLOW_PYTHON`；新增有/无历史文件两种打包参数回归测试。
- 同步 Node 版本要求、锁文件安装方式、当前界面操作说明和敏感材料忽略规则。此次不改变软著申报快照，也不重建或发布安装包。
- 一并整理原工作区的分析工具输入签名、精确证据复用校验、建议语义误报修复及对应测试；冻结实验策略和证据不变。
- 本轮验证的最终结果见 `docs/RELEASE_CHECK_20261001.md`。

## [2026-09-22] 本机网页模型连接与文件导入修复

- 本机 `dev:web:full` 增加仅同源回环页面可用的会话密钥配置；OpenAI、Qwen、DeepSeek 均可在本机页面连接，已有环境密钥需要显式授权。局域网模式继续禁止网页提交密钥。
- 修复连接按钮误把点击事件当作“使用已有密钥”的问题；修复成功分析时将后端 `*_LIVE` 模式误判为所选模型不一致的问题。
- CSV/XLSX 增加实际拖放、读取进度、同一文件重新选择、旧版 XLS 与错误文件类型提示；解释文件窗口只显示受支持的表格格式。
- 验证：模型配置与服务接口定向 115 PASS，界面逻辑 14 PASS，前端构建成功，全量 `1136 passed in 306.20s`。浏览器实际导入测试 CSV，正确读到 4 行、3 列及对应字段；本机配置入口拒绝无效密钥且未启用模型。未替用户授权或发起真实模型调用。

## [2026-09-22] 真实文件工作流与演示隔离

- 允许未连接云模型时先检查实际上传文件；生成报告仍要求已就绪的真实 Provider 和本次调用确认。
- 产品界面移除内置样例、历史模拟恢复数据和计时器驱动的“恢复成功”动画。恢复页只显示当前文件的未执行计划；完成修正版重新分析后才显示实际复测。
- 浏览器 CSV 在结构检查前按 UTF-8 / GB18030 解码；切换分析问题、字段角色或模型后清除旧报告，防止结果与选择不一致。
- 对返回结果检查输入指纹、真实模型调用次数及所选模型身份，不符时阻止报告展示。
- 明确局域网文件会传到运行 CureFlow 的电脑，当前无 HTTPS；不要上传敏感文件。冻结实验与内部测试样例保留在产品界面之外。
- 验证：前端构建通过；UTF-8/GBK 中文解码检查通过；前端契约 42 PASS；全量 `1111 passed in 284.17s`。未发起真实模型调用，未重建 Windows 安装包。

## [2026-09-22] 手机局域网链接

- 网页版 API 改为同源请求，手机不会再把请求发到自身的 `127.0.0.1`。
- Vite 通过 `/api` 转发至仅监听本机的 Python 服务，减少局域网暴露面。
- 新增 `npm run dev:web:lan:full`，同一 Wi-Fi 下可使用启动输出中的 `Network` 链接与本次随机访问码访问；未授权请求不能进入 `/api`。
- 窄屏不再隐藏五项任务导航，并调整面板、指标和操作按钮布局。
- 链接版不通过 HTTP 接收 API Key；模型密钥由运行服务的电脑统一配置。
- 验证：未授权 `/api/health` 返回 401，正确访问码返回 200；390px 手机尺寸访问码页可正常显示；前端构建成功；全量测试 `1105 passed in 288.63s`。未进行真实模型调用。

## [2026-09-21] 分析目标与实际执行一致性修复

- 用户明确选择分析问题后，CSV/XLSX 按所选目标执行本地计算并交给已授权的模型解读。经营字段不会再把明确问题转入旧六指标流程，也不会因此要求文件同时包含 2024、2025 年数据。
- 明确目标时保留原始列名及主指标、分组、时间、标识字段选择，避免报告与原文件字段名称不一致。
- 前端默认选中可见的“数据可靠吗？”，与发送给后端的分析目标一致。未指定目标的旧 API `auto` 路径继续兼容。
- 无效主指标在模型解析/调用前拒绝，返回可读的输入错误。
- 新增 13 项回归用例：六种分析问题 × CSV/XLSX，以及无效主指标拦截。模型调用采用测试替身，未进行真实 Provider 调用。
- 验证：相关测试 98 PASS；完整测试 `1104 passed in 282.32s (0:04:42)`；`npm run build` 成功。
- 本次交付为源码与前端构建更新；既有 Windows 安装包尚未重新生成。

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
