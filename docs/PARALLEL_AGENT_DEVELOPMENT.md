# 并行 Agent 开发流程

本文定义多 Agent / 多 Worktree 并行开发的隔离与所有权规则，不改变 UE Agent Kit 的运行时行为。

> **强制前置规范**：任何 Agent 在制定 Detailed Plan、Blocker Closure Plan 或执行新 Track 前，必须先阅读 [`DEVELOPMENT_WORKFLOW.md`](DEVELOPMENT_WORKFLOW.md)。测试分级、UE lease、性能采样、文档粒度和阶段收口规则以该文档为准；本文件只补充并行开发专属规则。

## 角色

- **Sol**：冻结 Tool 契约、维护公共 Registry、处理跨模块集成、执行最终 UE5.6 验证并决定是否合并。
- **Luna**：在冻结契约和文件所有权内实现独立子任务，不得自行修改公共 Schema 或扩展安全边界。

Tool 顺序、模式归属、Annotation 和 Live Bridge Method 统一由 `src/ue_agent_kit/tool_registry.py` 管理。MCP 注册拆分为 Query、Live Read、Live Action 和 Workflow 四个模块；Editor Bridge 能力按 Status、Diagnostic、Asset、Graph、Navigation、Validation 拆分。

## Worktree

先预览：

```bat
scripts\CreateAgentWorktrees.cmd
```

确认后创建：

```bat
scripts\CreateAgentWorktrees.cmd -Apply
```

默认生成：

```text
E:\WorkSpace\UEAgentKit-worktrees\navigation   feat/053-navigation
E:\WorkSpace\UEAgentKit-worktrees\validation   feat/053-validation
E:\WorkSpace\UEAgentKit-worktrees\protocol     feat/053-protocol
```

每个 Agent 只能在自己的 Worktree 和分支工作。允许在用户明确授权后创建本地 checkpoint commit，但禁止自行 merge、rebase、push、修改版本号或标记最终发布状态。

## 测试与 UE 资源

测试分级统一采用 [`DEVELOPMENT_WORKFLOW.md`](DEVELOPMENT_WORKFLOW.md) 的 G0-G3 / U0-U3 规则。

并行开发额外遵守：

```text
开发中        各 Agent 只跑自身改动对应的 G0/G1 focused/domain tests
阶段收口      各 Track 只在自己的 G2 边界跑一次 full regression
跨 Track 集成 integration branch 统一跑 G3，证明组合状态
```

禁止多个 Agent 因各自小提交同时重复跑全量 suite；禁止把 `ValidateRelease` 内嵌的 full tests 与外部 full tests 在同一 closure pass 重复执行。

同一台机器同时只能有一个 UE lease owner。UE lease 包括 UnrealEditor、UnrealEditor-Cmd、UBT / Direct Build、fixture Reset、snapshot refresh、真实 UE acceptance 和大规模 UE fixture generation。其他 Agent 在此期间只能执行 Python / SQLite / Web / 静态分析 / 文档等 U0 工作。

Navigation 等不触及 persistence/recovery 的变更不重复运行 Commit/Rollback 或双会话刷新；Snapshot 生命周期变更才运行对应真实 UE 刷新路径。真实 UE 验收只覆盖本批实际改变的状态机，不回归无关历史矩阵。

## Agent 子任务模板

```text
Base commit:
Branch / Worktree:
冻结的 Tool 契约:
允许修改的文件:
禁止修改的文件:
实现要求:
安全约束:
Validation Budget (G0/G1/G2 + U-level):
专项测试:
本地提交信息:
完成报告: 修改文件、测试结果、限制、Commit Hash
```

任务必须明确参数、响应字段、稳定错误码、数量/时间上限、Annotation 和 Bridge Capability。仅写“实现 ue_open_asset”不构成有效子任务。

## 文件所有权原则

历史 0.5.3 的 Navigation / Validation / Protocol 分工继续作为文件所有权示例，但后续 Track 不再固定为 Sol/Luna 命名。当前规则是：

- 每个并行 Track 在 Detailed Plan 中声明自己的 worktree、branch、允许修改文件和禁止修改文件。
- 公共 Registry、Schema、跨 Track 契约只能由明确的 integration owner 修改，或在计划中提前冻结冲突解决方式。
- 一个 Agent 不得 reset / clean / revert 另一个 Agent 的 worktree。
- 需要共享 UE/C++ 资源时遵守单机 UE lease，不以“另一个 worktree”作为可并行启动 Unreal 的理由。
