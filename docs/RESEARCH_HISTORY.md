# 历史研究与技术设计

本页从旧版根目录 README 保存而来，记录研究阶段的设计与历史测量。其中的命令、数字和目录示意可能不对应当前桌面产品；运行当前版本请先看 [快速开始](GETTING_STARTED.md) 和 [架构导航](ARCHITECTURE.md)。

以下内容说明底层研究工作流和历史阶段结果，不等同于当前桌面版所有功能均已完成产品验收。

---

## 1. 三件核心能力

| 能力 | 描述 | 实现位置 |
|---|---|---|
| **Semantic Artifact Provenance** | 每个 artifact 携带完整 provenance：谁产出、依赖谁、引用了哪些证据 | `provenance/decorator.py` + `provenance/graph.py` |
| **Contamination Tracking** | 错误沿数据流 / 引用边传播，BFS + dedup + 终止条件 | `contamination/propagation.py` + `contamination/validator.py` |
| **Dependency-aware Selective Recovery** | 基于污染图选 SELECTIVE/COARSE/FULL 三种恢复策略 | `recovery/strategy.py` + `recovery/strategies/*.py` + `recovery/executor.py` |
| **Claim Grounding Audit** | 逐条核对数字、证据身份、引用生命周期、因果外推与范围扩张；明确区分本次观测风险率和总体幻觉率 | `validation/claim_grounding.py` + [`CLAIM_GROUNDING.md`](../CLAIM_GROUNDING.md) |

---

## 2. 历史研究工作流：v2 算法升级

相比 v1 baseline，v2 在三个层面做了数据驱动改造：

| 子方向 | v1 | v2 | 价值 |
|---|---|---|---|
| **5 阶段调度** | 硬编码 if/elif | 数据驱动 `Stage` table + `Scheduler` | 加 1 阶段 = 1 行 table + 1 函数，0 行改 runner |
| **恢复策略** | 3 策略硬编码 | `Strategy` Protocol + `@register_strategy` registry | 加 1 策略 = 1 class + 1 decorator，0 行改 planner |
| **Artifact 缓存** | 无淘汰 KV | `SemanticLRUCache` (4 规则：LRU 兜底 + 中心性加权 + 失败拒绝 + size cap) | opt-in，默认 off（向后兼容） |

**v2 阶段记录**：当时新增 96 项测试（33 scheduler + 34 strategy + 29 cache），该阶段曾记录 731/731 通过。这是历史结果，不是当前工作区的全量测试结论；发布前须重新运行测试。

---

## 3. 5 阶段数据流

```
extract → metrics → claims → sections → merge
  │         │         │          │         │
  ▼         ▼         ▼          ▼         ▼
raw_data  fields   metrics    claims   report_merged
                    claims
```

每个 stage 是 `Stage(name=, inputs=, outputs=, execute=, validators=, retries=, on_failure=)` 声明式定义。

---

## 4. 三种恢复策略（v2 策略市场）

| 策略 | 行为 | 适用 |
|---|---|---|
| **SELECTIVE_RECOVERY** | 只重算污染图中的影响节点，其他复用 | 错误范围小，< 30% 节点受影响 |
| **COARSE_CHECKPOINT** | 重算 metrics/claims 之后，fields 复用 | 中等范围，30-70% 节点 |
| **FULL_RERUN** | 全部重算，0 复用 | 错误范围广，> 70% 节点 |

注册新策略：

```python
from recovery import register_strategy, REGISTRY

@register_strategy("MY_STRATEGY")
class MyStrategy:
    name = "MY_STRATEGY"
    def applicable(self, cr): ...
    def plan(self, cr, ctx): ...
    def dispatch_step(self, step, ctx): ...
```

加完即被 `RecoveryExecutor.execute()` 自动 dispatch。

---

## 5. 实验数字与产品边界

### 5.1 区分离线实验与真实模型
- 历史确定性实验可使用 `default_workflow_llm()` 的离线 fixture；这不是普通用户在桌面产品里选择的云模型。
- 桌面产品可按需连接 OpenAI、Qwen、DeepSeek、豆包；本地文件检查不依赖云模型。密钥由使用者提供并明确授权调用，不随安装包分发。
- fixture 的 token / calls 数字不代表第三方服务性能，也不能当作产品成本或成功率承诺。真实 Provider 的实验数字只适用于对应实验条件。

### 5.2 5-axis 指标（Stage 1.5 frozen）
| axis | 含义 |
|---|---|
| `provider_success` | LLM 返回可解析响应 |
| `recovery_execution_success` | 重试/恢复执行成功 |
| `output_contract_valid` | 输出匹配 schema |
| `semantic_validation_success` | 语义验证通过 |
| `trial_valid` | 4 个 axis 都 True |

**不可改**。M1（Qwen 60 trial）/ M3（DeepSeek 60 trial）evidence 锁在 `experiments/results/exp02/`。

### 5.3 当前验证方式
- 在当前源码和环境中运行 `python -m pytest tests/ -q`；不要把旧交接文档中的 609、731 或 960 项通过数当作当前验收结果。
- 历史冻结证据保留在原实验机器，不随源码上传。持有完整证据时运行 `python scripts/verify_evidence_sha.py`；没有证据不能声称已完成历史实验核验。后续改动仍须重新核验。
- 发布桌面版前还须运行 `cd desktop` 后的 `npm run smoke-test`，并在干净 Windows 机器上验安装、启动、文件导入和卸载。

---

## 6. 怎么跑

### 6.1 开发者安装依赖

需要 Python 3.10+，以及 Node.js 20.19+ 或 22.12+。使用仓库的 `package-lock.json` 安装桌面依赖，避免每台机器自行选取不同依赖版本。
```powershell
# 在源码根目录执行
pip install -r requirements.txt
```

### 6.2 跑测试
```powershell
python -m pytest tests/ -q
# 以下核验需要原实验机器上的冻结证据，公开源码不包含这些文件
python scripts/verify_evidence_sha.py
```

### 6.3 从源码启动桌面版
```powershell
cd desktop
npm ci
npm start
# 构建界面、启动 Electron，并自动启动本地服务
```

桌面端支持上传不超过 5 MB 的 `.csv` 或 `.xlsx`。XLSX 会读取全部可见且非空的工作表，每表最多 10000 行数据和 100 列。单表会自动识别中英文经营字段；其他矩形数值数据进入本地通用表格画像。多工作表文件会进入关系分析，区分训练集与实验集，列出共有字段、仅单表字段，并把“训练集有值而实验集为空”的列标为结构上的候选目标列。该判断只描述数据结构，不代替题目对业务含义或预测目标的定义。

详见 `desktop/README.md`。

### 6.4 跑 v2 性能 baseline
```powershell
# 项目根
python _v2_perf_baseline.py
# 输出 _v2_perf_report_data.json + _v2_perf_report.md
```

---

## 7. 目录结构

```
poc/
├── artifacts/         # Artifact schema + JSON file store + (v2) SemanticLRUCache
├── contamination/     # 错误传播 + 验证
├── llm/               # LLMClient 抽象 + FakeLLMClient + parse
├── provenance/        # 装饰器追踪 + ArtifactGraph
├── recovery/          # 策略市场 + executor
├── validation/        # 语义验证（numeric / direction / boundary）
├── workflow/          # 5 阶段数据驱动调度
│   ├── scheduler.py   # v2 Stage + Scheduler
│   ├── stages.py      # v2 BASELINE_TABLE
│   └── runner.py      # 向后兼容 run_baseline_workflow
├── cureflow_app/      # Desktop backend (HTTP API)
├── desktop/           # Electron + Vite + React 前端
├── tests/             # 自动化测试；数量以本次 pytest 结果为准
└── experiments/       # 实验代码；results/ 中冻结证据仅保留在本机
```

---

## 8. 当前限制

- 已有 Windows 安装包验证版，但尚需在另一台干净 Windows 机器完成安装、卸载及 XLSX 验收；当前没有受信任的代码签名，不应宣称已可大规模公开分发。
- 可接入真实第三方 Provider，但调用是否可用取决于使用者的有效密钥、模型权限、网络和明确授权；不保证所有模型始终可用。
- 局域网链接只适合可信网络试用；当前缺少 HTTPS、多用户隔离和正式账号体系，不应公开到互联网或用于敏感数据。
- AI 解读和统计线索不能代替原始记录核查与领域专家判断；当前选择性恢复计划与实际已执行的复测结果必须区分。
- 语言界面目前主要面向中文用户；尚未提供完整国际化。

历史 M3 实验报告与原始证据仅保留在实验机器，不随公开源码上传；其条件和数字不能外推为当前桌面版的整体表现。

---

## 9. 怎么贡献 / 扩展

- **加 1 阶段**：`workflow/stages.py` 写 1 函数 + `BASELINE_TABLE.append(Stage(...))`
- **加 1 策略**：`recovery/strategies/my_strategy.py` 写 1 class + `@register_strategy("MY")`
- **加 1 缓存规则**：`artifacts/cache.py` 继承 `EvictionPolicy` 写 1 class
- **加 1 fault type**：`contamination/propagation.py` 写 1 propagation 函数

每个改动应有相应测试，并以当前源码运行的全量测试、证据校验和桌面验收结果为准，不沿用历史通过数量。
