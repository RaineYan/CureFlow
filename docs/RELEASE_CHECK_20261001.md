# 2026-10-01 源码提交核对

## 本轮改动

1. 桌面选择文件与浏览器拖放共用 UTF-8 / GB18030 CSV 解码、XLSX 字节传输和文件大小检查。
2. 共用读取模块放在 Electron 的打包目录，测试覆盖没有 `desktop/src/` 的宿主加载场景。
3. 启动验收使用独立用户目录和端口，轮询服务健康状态；Windows 前端构建直接调用 Vite 的 JS 入口，不再直接 spawn `.cmd`。
4. 后端打包将历史实验结果改为可选资源，并使用指定的 `CUREFLOW_PYTHON`；分别检查干净源码与带本机研究文件时的打包参数。
5. 中文安装说明同步按钮文案、Node 版本下限、锁文件安装方式和常见问题；个人软著材料、截图核对文件不纳入源码提交。

当前工作区此前已有的工具输入签名、精确证据复用校验、建议语义误报修复及其测试一并核对保留。未更改冻结实验 Provider 配置或锁定脚本，未调用真实模型。

## 验证记录

环境：Windows，Python 3.13.7，Node.js 24.17.0。

- `npm run test:ui-logic`：42 passed，0 failed。
- `npm run build`：通过。
- `npm run smoke-test -- --rebuild`：通过，确实重新构建并启动 Electron 及本地后端。
- `python -m pytest tests/test_desktop_build.py tests/test_desktop_health_contract.py -q`：54 passed。
- `python _check_provider_consistency.py`：`OK: 4 providers consistent`。
- `python scripts/verify_evidence_sha.py`：`Verified 284 files, 0 mismatch`，依赖原实验机上未公开的证据文件。
- `python -m pytest tests/ -q -p no:cacheprovider`：`1220 passed in 302.38s (0:05:02)`。最终全量回归使用单独测试临时目录；未新增跳过或降低断言要求。
- `git diff --check`：通过。PowerShell 文件换行提示为仓库 CRLF 规则，不是内容错误。
- 待提交文件与现有 Git 历史已扫描常见 Provider Token、GitHub Token、私钥及长凭据字面量模式；仅发现测试占位串，没有发现真实凭据。此项不是对任意秘密或权属问题的全面保证。

## 未宣称完成的事项

- 本轮没有重新生成 Windows 安装包，没有做另一台干净 Windows 机器的安装验收，也没有配置代码签名。
- 新增打包测试核对 PowerShell 编排参数，并以测试替身代替 PyInstaller；不将其描述成完整安装包构建成功。
- 本轮未重新执行真实 Provider 实验或付费调用。
- 软著申报 PDF 和固定源码副本未改动；本轮产品源码更新不自动覆盖申报材料。
- GitHub 上传须确认实际仓库地址和访问权限；本地测试通过不等于远程推送成功。

## 源码与研究数据

历史 `experiments/results/` 原始数据、运行审计及本机密钥不随代码发布。全量历史回归中有用例依赖这些文件，持有源码不等于持有完整实验档案。不要创建假证据、放宽断言或添加跳过来伪装历史验证成功。桌面文件分析和源码构建不以这些历史证据为前提。
