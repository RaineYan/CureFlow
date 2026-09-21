# CureFlow

> 面向数据分析型多智能体工作流的**语义产物溯源**与**成本约束选择性恢复**系统
>
> **2-week POC** · **离线 deterministic fixture** · **本地优先**

CureFlow 是一个多智能体协作系统的中间件层：当一个由多个 AI agent 协作完成的"分析报告"流程中，**某个节点的产出出现错误**（数据腐败 / agent 输出错误 / 证据引用错误），CureFlow 能够：

1. **追溯错误**（Semantic Artifact Provenance）：从产出的报告回溯到每个中间节点、每个证据引用
2. **传播分析**（Contamination Tracking）：错误从源节点到下游节点的传播路径
3. **选择恢复**（Dependency-aware Selective Recovery）：基于依赖图自动判断**哪些节点需要重算**、**哪些可以复用**

---

## 1. 三件核心能力

| 能力 | 描述 | 实现位置 |
|---|---|---|
| **Semantic Artifact Provenance** | 每个 artifact 携带完整 provenance：谁产出、依赖谁、引用了哪些证据 | `provenance/decorator.py` + `provenance/graph.py` |
| **Contamination Tracking** | 错误沿数据流 / 引用边传播，BFS + dedup + 终止条件 | `contamination/propagation.py` + `contamination/validator.py` |
| **Dependency-aware Selective Recovery** | 基于污染图选 SELECTIVE/COARSE/FULL 三种恢复策略 | `recovery/strategy.py` + `recovery/strategies/*.py` + `recovery/executor.py` |
| **Claim Grounding Audit** | 逐条核对数字、证据身份、引用生命周期、因果外推与范围扩张；明确区分本次观测风险率和总体幻觉率 | `validation/claim_grounding.py` + [`CLAIM_GROUNDING.md`](CLAIM_GROUNDING.md) |

---

## 2. v2 算法升级

相比 v1 baseline，v2 在三个层面做了数据驱动改造：

| 子方向 | v1 | v2 | 价值 |
|---|---|---|---|
| **5 阶段调度** | 硬编码 if/elif | 数据驱动 `Stage` table + `Scheduler` | 加 1 阶段 = 1 行 table + 1 函数，0 行改 runner |
| **恢复策略** | 3 策略硬编码 | `Strategy` Protocol + `@register_strategy` registry | 加 1 策略 = 1 class + 1 decorator，0 行改 planner |
| **Artifact 缓存** | 无淘汰 KV | `SemanticLRUCache` (4 规则：LRU 兜底 + 中心性加权 + 失败拒绝 + size cap) | opt-in，默认 off（向后兼容） |

**v2 测试覆盖**：+96 测试（33 scheduler + 34 strategy + 29 cache），全量 **731/731 pytest PASS**。

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

## 5. 数字与限制

### 5.1 离线 deterministic fixture
- 所有 LLM 调用走 `default_workflow_llm()` = **项目内置离线 fixture**
- **不接第三方 Provider**，不消耗 API key
- 所有 token / calls 数字**不作为真实性能承诺**

### 5.2 5-axis 指标（Stage 1.5 frozen）
| axis | 含义 |
|---|---|
| `provider_success` | LLM 返回可解析响应 |
| `recovery_execution_success` | 重试/恢复执行成功 |
| `output_contract_valid` | 输出匹配 schema |
| `semantic_validation_success` | 语义验证通过 |
| `trial_valid` | 4 个 axis 都 True |

**不可改**。M1（Qwen 60 trial）/ M3（DeepSeek 60 trial）evidence 锁在 `experiments/results/exp02/`。

### 5.3 当前测试覆盖
- 960 pytest PASS（包含 provenance / recovery / Desktop / session-key multi-provider gate / claim grounding / task router）
- 不破 5-axis / 4-tuple / envelope classifier / provider adapter

---

## 6. 怎么跑

### 6.1 装依赖
```powershell
# 项目根
cd C:\Users\Lenovo\.minimax-agent-cn\projects\poc
pip install -r requirements.txt
```

### 6.2 跑测试
```powershell
python -m pytest tests/ -q
# 当前期望: 960 passed
```

### 6.3 跑 desktop POC
```powershell
cd desktop
npm install
npm start
# 第一次会 build vite + 启动 electron + 自动 spawn python backend
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
├── tests/             # 731 测试
└── experiments/       # M1 / M3 60 trial evidence（frozen）
```

---

## 8. 不在 POC 范围内

- 真实第三方 Provider 接入（仅 mock）
- 商业化 / 打包 / 安装程序
- 多用户 / 并发 / 部署
- 数据隐私合规
- 国际化（i18n）

详见 `experiments/results/exp02/M3_PHASE_B_REPORT.md` 看 M3 60 trial 横评。

---

## 9. 怎么贡献 / 扩展

- **加 1 阶段**：`workflow/stages.py` 写 1 函数 + `BASELINE_TABLE.append(Stage(...))`
- **加 1 策略**：`recovery/strategies/my_strategy.py` 写 1 class + `@register_strategy("MY")`
- **加 1 缓存规则**：`artifacts/cache.py` 继承 `EvictionPolicy` 写 1 class
- **加 1 fault type**：`contamination/propagation.py` 写 1 propagation 函数

每个改动加对应 `tests/test_*.py` 单元测试，**全量 731+ PASS** 才算完成。
