# CureFlow

**导入表格 → 检查数据与分析方法 → 人工核对 → 导出修正版并复测。**

CureFlow 是本地优先的 Windows 桌面数据分析工具，支持 CSV 和 XLSX。文件读取、规则检查和统计图表可以在本机完成；AI 解读是可选功能，只有你配置自己的模型服务并主动发起调用时才会使用。统计异常是复核线索，不会被自动当作错误。

> **当前仓库提供源码，尚未发布通过签名和干净机器验收的当前版 Windows 安装包。** 想试用请按下面的步骤从源码启动。

![CureFlow 导入与检查界面](docs/images/cureflow-import.png)

## 从 fork 到运行（Windows PowerShell）

需要 Python 3.10+ 和 Node.js 20.19+ 或 22.12+。以下命令从仓库根目录开始；若已 fork，请把克隆地址换成自己的仓库地址。

```powershell
git clone https://github.com/RaineYan/CureFlow.git
cd CureFlow
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:CUREFLOW_PYTHON = (Resolve-Path .\.venv\Scripts\python.exe).Path
cd desktop
npm ci
npm start
```

Electron 会打开桌面窗口并启动本地分析服务。首次体验可以手动导入仓库的 [`data/sample_company.csv`](data/sample_company.csv)；样例文件不会自动加载，也不会调用 AI。文件上限为 5 MB。遇到启动问题、想用本机或局域网网页版、或需要连接自己的模型，请看 [完整快速开始](docs/GETTING_STARTED.md)。

## 第一次分析怎么做

1. 导入 CSV/XLSX，确认工作表、表头、字段和读取范围。
2. 选择分析目标；需要时指定数值主指标、分组或时间字段，再运行本地检查。
3. 在分析记录中查看图表、方法选择和具体问题线索。对照原始资料人工核实。
4. 只对确认的值生成修正版副本，重新导入并复测；原文件不会被覆盖。

云端 AI 解读需要用户自己的有效 API Key 和调用授权。密钥不在仓库中；不要把真实密钥、原始私人表格或生成的运行记录提交到 fork。详见 [桌面版使用与隐私说明](desktop/README.md)。

## 找到要改的地方

| 想做什么 | 从这里开始 |
| --- | --- |
| 运行桌面版或本机网页 | [快速开始](docs/GETTING_STARTED.md) |
| 理解文件读取、统计、AI 与页面的数据链路 | [架构导航](docs/ARCHITECTURE.md) |
| 修改界面 | [`desktop/src/`](desktop/src/) |
| 修改本地 HTTP 服务与密钥处理 | [`cureflow_app/`](cureflow_app/) |
| 修改确定性分析与数据修正 | [`workflow/`](workflow/) |
| 添加测试、提交 fork 的改动 | [贡献指南](CONTRIBUTING.md) |
| 查看历史研究工作流 | [研究历史](docs/RESEARCH_HISTORY.md) 与 [`archive/legacy-scripts/`](archive/legacy-scripts/) |

公开仓库中的历史实验脚本已归档，仍被测试引用的入口保留在根目录。历史实验数字不等于当前桌面版的产品表现；当前版本以实际运行和测试结果为准。

## 验证与许可

```powershell
python -m pytest tests -q
cd desktop
npm run test:ui-logic
npm run build
```

这些本地检查不要求 API Key；真实模型调用需另行授权。自动化检查见 [CI 工作流](.github/workflows/ci.yml)。项目以 [MIT License](LICENSE) 提供；第三方依赖遵循各自许可。
