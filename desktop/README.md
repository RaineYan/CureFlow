# CureFlow 桌面版

CureFlow 是一款面向表格数据分析、AI 结论核验和局部恢复的 Windows 桌面软件。普通使用者可以直接安装，无需另行安装 Python、Node.js 或项目源码。

## 一、普通用户如何使用

### 1. 安装

双击以下安装包：

```text
desktop/release/CureFlow-Setup-0.1.0-x64.exe
```

安装包目前尚未购买 Windows 代码签名证书，因此系统可能显示“未知发布者”。在确认安装包来源可信后，可选择“更多信息”→“仍要运行”。

### 2. 连接 AI 模型

启动 CureFlow 后，可以选择以下任一真实模型服务：

- OpenAI
- 通义千问（Qwen）
- DeepSeek

在连接界面输入使用者自己的 API Key，然后点击“仅本次会话配置”。API Key 只保存在当前应用进程的内存中：

- 不写入配置文件；
- 不写入分析报告、审计文件或日志；
- 不经过 CureFlow 本地 HTTP 接口；
- 关闭软件后自动失效；
- 不包含在安装包中。

每次真实分析都需要使用者再次确认“本次最多 6 次调用”。系统不会自动重试，也不会在模型之间静默切换。

### 3. 导入数据并分析

桌面版支持不超过 5 MB 的 `.csv` 和 `.xlsx` 文件。

- CSV：支持 UTF-8 等常见文本编码。
- XLSX：读取所有可见且非空的工作表。
- 每个工作表最多读取 10,000 行数据和 100 列。
- 单工作表会执行字段识别、完整性检查、重复值检查、数值分布、异常值、排名约束、字段关系和分组差异等适用分析。
- 多工作表会比较共有字段、单表字段和表间结构关系，并识别可能的训练集、实验集或候选目标列。
- Excel 公式读取的是文件中最后保存的计算结果。上传前应在 Excel 中重新计算并保存。

CureFlow 会先检查数据是否足以支持所选分析目标。没有足够样本、目标字段、分组字段或时间字段时，系统会明确说明哪些问题无法回答，不会把“工具执行成功”冒充为“得出了可靠结论”。

## 二、隐私和数据边界

- CSV/XLSX 的读取、结构解析和确定性统计在本机完成。
- 选择真实云模型后，工作流需要的指标、字段摘要和证据上下文会发送给所选服务商。
- 原始表格不会以文件附件形式上传，但摘要中可能包含与分析有关的字段名称、统计值或样例信息。
- 使用真实数据前，应确认你拥有相应的数据处理权限，并遵守所在组织及模型服务商的隐私规定。
- CureFlow 不会把 AI 文本直接当成事实；关键结论仍需通过本地统计结果和证据引用校验。

## 三、开发环境要求

仅开发者从源码运行时需要以下环境：

- Python 3.10 或更高版本：`python --version`
- Node.js 18 或更高版本：`node --version`

在项目根目录安装 Python 依赖：

```powershell
Set-Location 'C:\Users\Lenovo\.minimax-agent-cn\projects\poc'
pip install -r requirements.txt
```

运行完整测试：

```powershell
Set-Location 'C:\Users\Lenovo\.minimax-agent-cn\projects\poc'
python -m pytest tests -q
```

当前交付版本的完整基线为 `1091 passed`，必须保持零失败。

## 四、从源码启动桌面版

```powershell
Set-Location 'C:\Users\Lenovo\.minimax-agent-cn\projects\poc\desktop'
npm install
npm start
```

`npm start` 会构建 React 界面、启动 Electron，并自动启动本地 Python 服务。运行数据保存在当前 Windows 用户的应用数据目录，不写回源码目录。

如果需要指定其他 Python 解释器，可在当前 PowerShell 会话中设置 `CUREFLOW_PYTHON` 后再启动。

## 五、生成 Windows 安装包

在 Windows 上执行：

```powershell
Set-Location 'C:\Users\Lenovo\.minimax-agent-cn\projects\poc\desktop'
python -m pip install pyinstaller
npm install
npm run dist:win
```

安装包输出到：

```text
desktop/release/CureFlow-Setup-<版本号>-x64.exe
```

该安装包已经包含本地 Python 后端。接收者不需要安装 Python、Node.js、npm，也不需要源码目录。

## 六、模型环境变量（仅供开发者备用）

图形界面的会话密钥输入框是正常使用方式。开发调试时仍可通过当前进程的环境变量提供密钥和本地授权：

- OpenAI：`OPENAI_API_KEY`、`CUREFLOW_ENABLE_OPENAI_LIVE=1`
- Qwen：`QWEN_API_KEY`、`CUREFLOW_ENABLE_QWEN_LIVE=1`
- DeepSeek：`DEEPSEEK_API_KEY`、`CUREFLOW_ENABLE_DEEPSEEK_LIVE=1`

不要把真实 API Key 写入源码、README、配置文件、测试、截图或聊天记录。

## 七、当前产品边界

- 产品界面只提供真实 Provider，不再把离线确定性 fixture 作为普通用户模型。
- “恢复与复用”中的实际文件检查和恢复计划来自本次上传文件；历史 EXP-02 数字只用于明确标注的实验参考区，不能冒充本次真实调用表现。
- AI 可以解释经过本地工具计算的结果并给出修正建议，但不会自动填造缺失数据，也不能替代领域专家审核。
- 当前安装包未做商业代码签名；公开大规模分发前应增加证书签名、自动更新和正式隐私条款。
