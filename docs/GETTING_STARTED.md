# 快速开始

本页适合刚 fork 或克隆 CureFlow、希望在 Windows 上运行源码的人。当前没有与最新界面对应的正式安装包；源码运行需要 Python 和 Node.js。

## 1. 准备环境

- Windows 10/11、Python 3.10+、Node.js 20.19+ 或 22.12+、Git。
- 在 PowerShell 中先检查 `python --version`、`node --version`、`npm --version`。
- Python 依赖列在仓库根目录的 `requirements.txt`；前端依赖由 `desktop/package-lock.json` 固定。无需提供 API Key 即可运行本地检查。

在仓库根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:CUREFLOW_PYTHON = (Resolve-Path .\.venv\Scripts\python.exe).Path
cd desktop
npm ci
```

不必激活虚拟环境；`CUREFLOW_PYTHON` 告诉桌面程序和局域网启动脚本使用刚安装依赖的解释器。它只对当前 PowerShell 窗口生效。如果已经在 `desktop` 目录，下面的命令继续从这里运行。

## 2. 启动桌面版

```powershell
npm start
```

程序会构建界面、打开 Electron 窗口，并启动本地分析服务。启动后可导入仓库的 `data/sample_company.csv` 练习；选择文件时从 `desktop` 目录返回上一级到 `data`。样例不会自动加载到产品界面。

在工作台确认文件和字段，再运行本地检查。查看“已做过的分析”中的分析概览、图表与线索。若要验证修正，先核实原值，再导出副本并重新导入。完整操作和隐私边界见 [桌面版说明](../desktop/README.md)。

## 3. 本机网页版（可选）

在仓库根目录新开 PowerShell 窗口，让 `python` 命令优先使用虚拟环境，然后启动网页和后端：

```powershell
$env:PATH = "$(Resolve-Path .\.venv\Scripts);$env:PATH"
cd desktop
npm run dev:web:full
```

浏览器打开 `http://127.0.0.1:5173/`。网页版和桌面版共用主要分析代码；本机分析记录的加密保存仅支持 Windows 桌面版。局域网访问请看 [桌面版说明中的专门章节](../desktop/README.md#六通过局域网链接在手机或其他电脑访问)，不要把开发服务公开到互联网。

## 4. 可选 AI 与密钥

本地文件检查不调用云模型。需要 AI 解读时，在应用设置里选择真实服务商、输入自己的密钥并明确发起调用。不要把密钥写进 `.env` 以外的共享文件，更不要提交到 Git；仓库的 `.gitignore` 会排除常见本机密钥文件，但提交前仍须检查暂存内容。桌面版的可选本机密钥保存使用 Windows 用户环境加密。

## 5. 验证修改

在仓库根目录：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

在 `desktop` 目录：

```powershell
npm run test:ui-logic
npm run build
```

首次运行全量 Python 测试可能需要几分钟；涉及真实模型的测试默认不运行。提交前还要运行 `git status`，确认没有把本机文件、密钥或构建输出暂存。贡献流程见 [CONTRIBUTING.md](../CONTRIBUTING.md)。

## 常见问题

| 现象 | 处理 |
| --- | --- |
| 桌面窗口提示本地服务未就绪 | 检查 `CUREFLOW_PYTHON` 是否指向安装依赖的解释器；在界面点击“重启本地服务”。 |
| `npm ci` 报 Node 版本不支持 | 安装符合要求的 Node.js 版本后重新执行。 |
| Excel 公式结果为空 | 在 Excel 中重新计算并保存，再导入 `.xlsx`；旧 `.xls` 需先另存为 `.xlsx`。 |
| 只看到少数分析方法 | 指定与目标匹配的数值、分组或时间字段；方法仍会按样本量和数据类型判断适用性。 |
| AI 连接失败 | 先使用本地检查，再核对所选服务商的密钥、模型权限和网络；不要把密钥放进截图或 issue。 |
