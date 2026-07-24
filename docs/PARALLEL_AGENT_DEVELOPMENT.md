# 并行 Agent 开发流程

本文定义 0.5.3 及后续版本的 Sol/Luna 分工方式，不改变 UE Agent Kit 的运行时行为。

## 角色

- **Sol**：冻结 Tool 契约、维护公共 Registry、处理跨模块集成、执行最终 UE5.6 验证并决定是否合并。
- **Luna**：在冻结契约和文件所有权内实现独立子任务，不得自行修改公共 Schema 或扩展安全边界。

Tool 顺序、模式归属、Annotation 和 Live Bridge Method 统一由 `src/ue_agent_kit/tool_registry.py` 管理。MCP 注册拆分为 Query、Live Read 和 Workflow 三个模块；Editor Bridge Reader 按 Status、Diagnostic、Asset、Graph 拆分。

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

每个 Luna 只能在自己的 Worktree 和分支工作。允许创建一个本地提交，但禁止 merge、rebase、push、修改版本号或标记最终发布状态。

## 测试层级

```text
开发中       scripts\TestMcpModules.cmd -Group <Registry|Query|Live|Workflow>
子任务完成   模块测试 + 所属 Python 文件 Ruff + C++ 变更时 UE5.6 增量编译
整批集成     Python 全测 + 全仓 Ruff + Direct Build + 仅运行本批实际触及的真实 UE 流程
```

Navigation 变更不重复运行 Commit/Rollback 或双会话刷新；Snapshot 生命周期变更必须运行双会话刷新。

## Luna 子任务模板

```text
Base commit:
Branch / Worktree:
冻结的 Tool 契约:
允许修改的文件:
禁止修改的文件:
实现要求:
安全约束:
专项测试:
本地提交信息:
完成报告: 修改文件、测试结果、限制、Commit Hash
```

任务必须明确参数、响应字段、稳定错误码、数量/时间上限、Annotation 和 Bridge Capability。仅写“实现 ue_open_asset”不构成有效子任务。

## 0.5.3 文件所有权

- Navigation Luna：新建 Editor Action Handler 和 Navigation 专项测试。
- Validation Luna：新建 Compile/Validation Handler 和专项测试。
- Protocol Luna：在 Sol 冻结契约后修改文档及任务专属 Fake/Schema 测试。
- Sol：`tool_registry.py`、公共 MCP 组合、统一错误契约、最终集成与提交。
