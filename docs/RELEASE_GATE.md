# CureFlow Windows 正式发行验收

当前公开仓库只发布源码。旧的 `desktop/release/` 文件是未签名的本机候选，不包含本轮全部改动，不能作为下载链接或正式安装包。下列项目实际通过并记录后，才考虑把新安装包上传到 GitHub Releases。

## 1. 签名准备

用户已表示拥有代码签名证书及另一台干净 Windows 电脑；证书目前尚未接入此项目的构建环境。**不要把证书、私钥、口令或其 Base64 内容发到聊天、提交到 Git，也不要写进配置文件。**

- PFX：由证书持有人在构建机的私有会话或安全凭据系统中配置 `WIN_CSC_LINK`（证书路径）及 `WIN_CSC_KEY_PASSWORD`；不要把值粘贴到仓库。electron-builder 26 的[官方说明](https://www.electron.build/v26/docs/features/code-signing/)列出了这两个变量。
- Windows 证书存储或硬件令牌：先确认该证书在构建机上可用于代码签名，再按[Windows 签名配置](https://www.electron.build/v26/docs/features/code-signing/code-signing-win/)选择对应证书；若有多个证书，应使用公开的证书主题名或指纹消除歧义，不能猜选。
- 云签名：按签名服务商的安全方式配置，不把访问凭据写入源码。

桌面构建已启用 `forceCodeSigning`；后置检查还要求安装程序、`CureFlow.exe` 和内置 `cureflow-backend.exe` 均为 Windows 认可的有效签名，且三者证书指纹一致。任何一项失败都不是正式发行包。签名成功也不保证 Windows SmartScreen 完全不提示，仍需在测试电脑上实际检查。

## 2. 构建机验收

以下命令在项目根和 `desktop` 目录分别执行，记录日期、Git 提交、通过/失败及输出摘要；不要把真实数据或密钥附在日志里。

```powershell
python -m pytest tests/ -q
Set-Location desktop
npm run test:ui-logic
npm run build
npm run smoke-test
npm run dist:win
npm run smoke:packaged -- release/win-unpacked/CureFlow.exe
```

`dist:win` 会自动运行 `release:verify`。需要单独复查时运行 `npm run release:verify`；脚本输出安装包、桌面程序和后端的 SHA-256，必须全部通过。签名后不要再编辑安装包，否则签名/哈希会失效。旧包存在于同一路径时，以本轮成功构建后的时间、版本和哈希为准；构建失败不能引用旧包。

## 3. 另一台干净 Windows 电脑验收

由证书持有人传输**刚通过签名检查的同一安装包**，在测试电脑上核对 SHA-256；测试电脑无须装 Python、Node.js、npm 或源码。建议使用 Windows 的文件属性“数字签名”核对发布者，或运行只读的 `Get-AuthenticodeSignature`。然后实际完成：

1. 安装、桌面快捷方式双击启动、退出后再启动；确认本地服务自动就绪，界面不要求命令行。
2. 用获准的去标识化 CSV 和 XLSX 分别导入；核对工作表、行列数、字段与原文件一致。不把内部测试样例冒充用户真实文件。
3. 本地检查、同类问题归组、逐格复核、保留原值、仅对人工确认项生成新副本；确认原文件未变。
4. 在无需 API Key 的情况下完成本地分析、报告查看及导出。
5. 如用户愿意且有权调用，再连接其自有模型密钥，确认最多 6 次调用；核对 AI 条目证据，修改或撤回一项，查看受影响的报告已重建、原始分析未被悄悄重跑。此项会产生真实费用，不能自动执行。
6. 主动开启本机加密保存，关闭再启动并重新选择相同文件，确认结果与复核进度找回；关闭保存后确认记录删除。不要在共享账户上测试真实敏感数据。
7. 卸载；核对程序移除。用户数据是否保留按界面和安装配置说明检查，不能默认声称卸载会清空数据。

将上述每一项的通过/失败、Windows 版本、安装包 SHA-256 和遇到的问题写成验收记录。任一关键步骤失败，应先修复并重新构建、重新签名、重新测试；不得沿用旧验收结果。

## 4. 公开发行边界

仅在同一哈希的安装包完成签名及测试电脑验收后，才上传 GitHub Releases，并清楚标注版本、SHA-256、支持的 Windows 环境、CSV/XLSX 限制、模型密钥自备、云端费用及已知限制。源码推送成功不等于安装包已发布。本项目目前没有自动更新功能，不应声称有。
