# 参与 CureFlow

欢迎 fork 后试用、修复问题或改进文档。第一次运行请从 [快速开始](docs/GETTING_STARTED.md) 开始；查代码入口看 [架构导航](docs/ARCHITECTURE.md)。

## 提交改动

1. Fork 仓库，在自己的 fork 中创建描述用途的分支。
2. 先复现问题。修复时尽量保持改动聚焦；行为变化补测试，界面变化核对桌面版和本机网页。
3. 在仓库根目录运行 `python -m pytest tests -q`；修改前端时再到 `desktop` 运行 `npm run test:ui-logic` 和 `npm run build`。
4. 用 `git status`、`git diff --check` 和 `git diff --cached --check` 检查提交。只暂存本次需要的源码、测试与文档，再发起 Pull Request，说明改动前后的行为及验证结果。

CI 会在 Windows 上检查核心 Python 路径与桌面前端。真实模型调用、签名安装包和干净机器验收不属于普通 PR 的自动检查。

## 数据、密钥和实验记录

- 不提交真实 API Key、访问令牌、`.env`、本机配置、原始私人表格、用户报告或运行日志。
- 单元测试使用合成数据和模拟模型；不要为了“演示”将真实用户数据复制进 `data/`。
- `desktop/release-*/`、`desktop/build/`、`desktop/dist/`、`experiments/results/` 等产物由 `.gitignore` 排除。不要用 `git add -f` 强行上传。
- 发现可疑密钥时，不要在公开 issue 中粘贴原文；先停止使用并在服务商处轮换，再提交脱敏后的复现步骤。

## 仓库约定

- 产品入口是 `desktop/` 和 `cureflow_app/`；根目录保留少量仍被测试调用的历史入口，不作为新功能放置位置。
- 新的探索性实验放 `experiments/`。`archive/legacy-scripts/` 是历史归档，不应继续添加产品逻辑。
- 文档以当前可运行源码为准。不要把旧研究数字、仅计划的恢复步骤或本机预览包写成正式发布能力。
- 当前尚无完成签名与另一台干净 Windows 电脑验收的安装包；发行步骤见 [RELEASE_GATE.md](docs/RELEASE_GATE.md)。

代码按仓库根目录的 [MIT License](LICENSE) 提供；依赖项保留各自的许可。
