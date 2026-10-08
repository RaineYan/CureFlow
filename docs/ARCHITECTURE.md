# 架构导航

从产品使用者角度，CureFlow 有一条清晰的数据链路：

```text
CSV/XLSX → 本地服务读取与范围确认 → 确定性分析/规则检查
        → 图表和待核对线索 → 人工复核 → 修正版副本 → 重新分析
                                      └→ 可选 AI 解读及证据核验
```

**本地统计和 AI 解释是两条不同的责任链。** 图表来自当前文件的本地计算；云模型只在用户配置并主动调用后解释选定的摘要和证据。方法未满足字段或样本条件时，界面会说明跳过原因，不把“未执行”当作正常结果。

| 目录 | 主要职责 | 从哪里读起 |
| --- | --- | --- |
| [`desktop/src/`](../desktop/src/) | React 页面、分析记录与图表 | `App.jsx`、`analysis-memory.mjs` |
| [`desktop/electron/`](../desktop/electron/) | Windows 桌面窗口、文件桥接、启动本地服务和本机加密记录 | `main.cjs`、`preload.cjs` |
| [`cureflow_app/`](../cureflow_app/) | 本地 HTTP API、表格读取、模型连接与报告 | `server.py`、`key_store.py` |
| [`workflow/`](../workflow/) | 确定性分析、数据修正和工作流 | `analysis_tools.py`、`repair_analysis.py` |
| [`validation/`](../validation/) | 证据与语义核验 | `claim_grounding.py` |
| [`provenance/`](../provenance/)、[`contamination/`](../contamination/)、[`recovery/`](../recovery/) | 底层溯源、影响传播与恢复策略 | 各目录 `__init__.py` 与测试 |
| [`tests/`](../tests/) | Python 行为测试与接口契约 | `test_server.py`、`test_analysis_tools.py` |
| [`desktop/scripts/`](../desktop/scripts/) | 前端测试、打包与本机冒烟检查 | `ui-logic.test.mjs`、`smoke-test.cjs` |
| [`experiments/`](../experiments/)、[`archive/legacy-scripts/`](../archive/legacy-scripts/) | 研究与历史脚本；不是桌面产品的启动入口 | [研究历史](RESEARCH_HISTORY.md) |

## 一次分析的关键边界

1. Electron 通过本机后端处理文件；浏览器模式通过同源 `/api` 调用后端。文件读取范围由用户确认。
2. `workflow/analysis_tools.py` 按分析目标、字段角色和样本条件执行适用工具，返回实际计算结果、图表数据与跳过理由。
3. 前端只渲染这次运行的结果；历史记录匹配文件内容、工作表、目标、字段与模型配置，旧格式结果不应冒充新计算。
4. 人工复核确认后，修正逻辑导出新文件；重新导入才能得到修正后的实际复测结果。
5. 云模型可用于解读，但引用身份和本地统计要单独核验。AI 文本与人工改写均不替代原始记录的核对。

## 想添加功能时

- 新分析方法：先在 `workflow/analysis_tools.py` 定义适用性、结果结构和跳过理由；补 `tests/test_analysis_tools.py`，再把结果接入前端。
- 新文件读取或修正规则：检查 `cureflow_app/server.py`、`workflow/repair_analysis.py` 和 XLSX/CSV 往返测试。
- 新界面：修改 `desktop/src/`，运行 `npm run test:ui-logic` 与 `npm run build`；保持桌面和本机网页都能使用。
- 新模型适配：遵守 `llm/` 的抽象、服务端权限检查与证据最小化原则；不要把真实密钥写入测试。

详细历史研究设计仍保存在 [研究历史](RESEARCH_HISTORY.md)，但其中的旧命令和性能数字不作为当前产品验收依据。
