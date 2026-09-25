# 复用输入校验设计（待审核，未实施）

## 范围与现状

本设计针对 `recovery/` 中的历史多智能体 Artifact 工作流，不代表桌面端文件修正流程已使用 `RecoveryExecutor`。当前实现按精确 `ArtifactRef`（run、id、version）规划和读取，不能把“最新版本”简单当作正确版本。

- `SelectiveRecoveryStrategy.plan()` 从上游闭包生成 `reused_refs`，但通常不为这些引用生成 `REUSE` 步骤。
- `FullRerunStrategy`、`CoarseCheckpointStrategy` 会为部分输入生成显式 `REUSE` 步骤；`_exec_reuse()` 目前只记零调用日志。
- 执行器的 `_remapped()` 读取 `required_input_refs`，若有替换则读新版本，否则读原版本；它检查存在性，不检查状态。真实消费输入也可能不在 `plan.reused_refs` 中。
- `RecoveryExecutor.execute()` 原样返回 `plan.reused_refs`。末尾的 `validate_target_clean_path()` 与完整性检查是结果校验；独立的 `clean_path_audit()` 不在此执行循环内，且要求调用方重建恢复后的图。

## 建议的校验位置与失败语义

不能在“规划、执行入口、末尾审计”中只选一处。建议先在执行入口做无副作用预检，再在实际消费时复核，末尾保留结果审计：

1. 入口对 `plan.reused_refs`、显式 `REUSE` 目标和计划的 `required_input_refs` 建立引用清单；核对精确版本存在、当前状态允许使用、run 与计划相符，并检查声明的依赖是否能在计划/图中解释。对将被重算的坏目标不能套用复用状态规则。
2. 执行时在显式 `REUSE` 和 `_remapped()` 的实际读取处再次校验输入；前者不能代替后者。执行前检查不消除检查与使用之间的状态变化风险，必要时须借助 store 锁/快照或版本事务保证一致性。
3. 若入口预检失败，在任何工具或模型调用前返回 `RecoveryExecutionResult(success=False, failure_reason=...)`；`executed_steps`、实际 `reused_refs` 应为空，计划对象仍保留拟复用引用。若运行中失败，保留已执行步骤并准确标明部分执行，不得报告恢复成功或节省量。异常类型可在内部使用，但应由执行边界转为结构化失败。
4. 末尾对恢复后的图做 `clean_path_audit()`，将缺失/非干净引用作为验证结果；它不是输入预检的替代品。现有 `validate_target_clean_path()` 只沿可传播边走，不能证明所有非传播输入都安全。

## 什么才算“未使用陈旧数据”

`VALID`/`RECOVERED` 是必要条件，不是充分条件。最低可核对条件是精确引用存在、状态允许、实际消费的版本与计划引用或替换映射一致、依赖身份与图一致。版本号较旧不自动等于陈旧：固定版本可能是有意复现。若要证明外部源文件未变化，还需在创建 Artifact 时保存可信的源指纹/依赖指纹并在复用时比较；目前缺少这项通用合同，因此本轮设计不能承诺发现所有内容陈旧情形。时间戳本身也不足以证明内容新鲜。

## 后续实现前应补的测试

- 显式 `REUSE` 目标缺失或 `INVALIDATED`：执行前失败，零工具/模型调用，返回失败结果而非未捕获异常。
- 选择性计划的 `reused_refs` 在规划后缺失或变为 `INVALIDATED`：即使没有 `REUSE` 步骤也要失败。
- `required_input_refs` 不在 `reused_refs` 中、或被替换映射指向坏版本：消费前失败；验证精确版本而非笼统“最新”。
- 已规划后源状态变化、非传播边引用、以及正常旧版本复用：分别证明边界与成功路径。
- 现有 v1 输出及冻结实验回归。任何改变旧策略可接受输入的行为，都要先确认与“v1 byte-for-byte 等价”合同是否冲突；不靠改断言消除冲突。

本文件仅是设计建议；未修改执行器、策略、状态定义或冻结证据。实施前需单独审核失败结果结构和兼容性影响。
