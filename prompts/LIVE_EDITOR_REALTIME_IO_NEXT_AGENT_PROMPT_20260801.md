# Live Editor Realtime I/O 下一位 Agent 执行提示词

你现在接手 `UEAgentKit` 的 `feature/live-editor-realtime-io` 分支。不要把上一位 Agent 的审计和方案设计误认为功能已经实现。本轮的目标是**直接开始编码，并完成第一个可编译、可测试的 Realtime Context 垂直切片**。

## 1. 工作范围与当前状态

工作区：

```text
E:\WorkSpace\UEAgentKit
```

必须保持：

```text
分支：feature/live-editor-realtime-io
```

当前已知 checkpoint：

```text
HEAD：53f8317578fe8f96b6867dc94e1bd75c1d6439c0
提交：docs: add realtime io agent handoff
```

如果接手时这些文档尚未被上一位 Agent 提交，当前未提交修改可能包含：

```text
docs/Handoffs/LIVE_EDITOR_REALTIME_IO_HANDOFF_20260801.md
prompts/LIVE_EDITOR_REALTIME_IO_NEXT_AGENT_PROMPT_20260801.md
```

这两个文件都是交接资料，**不得 reset、checkout、删除或覆盖**。如果 `git status` 发现除此之外还有未知修改，先停止编码并汇报，不要擅自清理。

禁止修改：

```text
E:\WorkSpace\UEAgentKit-MemoryContext
```

## 2. 必须先做的检查

先执行并记录真实输出：

```bat
git branch --show-current
git status --short
git rev-parse HEAD
git rev-list --left-right --count origin/feature/live-editor-realtime-io...HEAD
```

然后阅读：

```text
prompts/LIVE_EDITOR_REALTIME_IO_LOCAL_AGENT_PROMPT_20260801.md
docs/Handoffs/LIVE_EDITOR_REALTIME_IO_HANDOFF_20260801.md
docs/AI_NATIVE_UE_EDITOR.md
docs/BRANCH_WORKTREES.md
docs/PROJECT_STATUS.md
docs/COMPARISON_UE_LLM_TOOLKIT.md
spec/LIVE_EDITOR_BRIDGE.md
spec/MCP_SERVER.md
```

本提示词与上述 prompt 或 handoff 冲突时，以更严格的安全、兼容性和验证要求为准；不要重新写一份泛化方案，直接进入下面的实现任务。

## 3. 本轮唯一主目标：Context 垂直切片

实现一个新的只读 MCP Tool：

```text
ue_get_editor_context
```

对应 Bridge 方法：

```text
editor.getEditorContext
```

它必须在一次 Bridge Request 内聚合当前运行中 UE Editor 的开发上下文，使调用方不再连续调用多个小型状态 Tool。

本轮不要同时实现 Batch Task 或 Change Set。它们保留给后续阶段；但实现时必须为后续复用现有 Session、Capability、Envelope、错误和 Journal 边界，不能另起一套架构。

## 4. Context 返回契约

Context 顶层至少包含：

```text
source
editor
world
selection
openAssets
dirtyPackages
blueprintGraphSelection
compileErrors
outputLogCursor
durationMs
stageDurationsMs
nextActions
```

实现要求：

- 所有 section 都必须有明确硬上限。
- 超过上限必须返回 `truncated`，不能无限扩大响应。
- 必须返回总耗时 `durationMs` 和阶段耗时 `stageDurationsMs`。
- `nextActions` 必须是稳定、可解释的后续建议，不得伪造已完成的验证。
- 复用现有 Builder：`BuildStatusResult()`、`BuildCurrentLevelResult()`、`BuildSelectionResult()`、`BuildOpenAssetsResult()`、`BuildDirtyAssetsResult()`、`BuildBlueprintGraphSelectionResult()`、`BuildCompileErrorsResult()`、`BuildOutputLogResult()`。
- 读取 Compile/Output Log 时使用有界参数；不要通过新接口暴露任意日志读取、任意路径读取或任意字段选择。
- 不加载资产，不保存资产，不修改选择，不改变 Dirty 状态，不改变 PIE/Editor 状态。
- 只读取当前已经存在的 Editor/World/Level 信息。
- Editor 不可用时必须沿用 Python Bridge 的稳定 `state=unavailable` 行为。

建议的 C++ 拆分：

```text
Plugin/UEAgentKit/Source/UEAgentKitEditor/Private/EditorBridgeContextHandlers.cpp
```

如确有必要，再在 `EditorBridge.h` 中声明：

```cpp
TSharedRef<FJsonObject> BuildEditorContextResult() const;
```

不要继续把大量 Context 逻辑堆进 `EditorBridge.cpp`。

## 5. Python/MCP 实现任务

优先修改或新增：

```text
src/ue_agent_kit/tool_registry.py
src/ue_agent_kit/editor_bridge.py
src/ue_agent_kit/mcp_realtime_tools.py
src/ue_agent_kit/mcp_server.py
```

具体要求：

1. 在 Tool Registry 增加 `realtime` ToolGroup。
2. 增加 `ue_get_editor_context` ToolDefinition。
3. Tool 定义使用 read-only annotations。
4. 将 `realtime` 纳入 live-enabled mode。
5. 新定义必须放在现有 live-action 定义之后，避免改变旧 live-action 索引切片测试。
6. 在 `_normalize_tool_params()` 中对新工具执行严格 Schema 校验、文本长度和整数边界校验；不要接受未知字段。
7. 在 `LiveEditorBridgeService.call_tool()` 中映射到 `editor.getEditorContext`。
8. 对新增 Context 方法执行 capability 校验。
9. 不要直接对所有旧方法启用全量 capability 校验；现有 C++ Capability 表并未覆盖全部旧 Workflow 内部方法，不能破坏旧 Live Write 流程。
10. 在 `mcp_realtime_tools.py` 使用现有统一 `_error_response()` 和 Bridge 错误映射。
11. 在 `mcp_server.py` 注册 realtime Tool，并纳入 Capability Discovery contract。
12. 不能创建第二个 IPC Client、第二种 Envelope、第二套 Session 或第二套错误码系统。

## 6. C++ Bridge 实现任务

必须复用现有 Bridge 的：

- localhost TCP；
- newline-delimited JSON；
- 认证；
- `SessionId`；
- `ProcessLine()`；
- Game Thread 调度；
- Response Envelope；
- Capability 表；
- 请求/响应大小限制；
- `FTSTicker` 生命周期。

具体实现：

1. 在 Capability 表加入 `editor.getEditorContext`。
2. 在 `ProcessLine()` 增加对应方法分支。
3. 新增独立 `EditorBridgeContextHandlers.cpp`。
4. 在一次请求中组合现有 Builder 结果。
5. 为每个阶段记录耗时，并设置整体硬上限。
6. 保证 Context 只读且不会隐式加载资产。
7. 遇到 Editor/World 不可用时返回与现有 Bridge 一致的稳定错误/状态。
8. 不新增任意 `UObject Method`、Console、Python、Shell、SQL、文件系统路径或通用 `set_property` 能力。

如果现有 Builder 的返回结构与 Context 需要的 section 不完全一致，优先做最小适配；不要顺手重构无关的旧 Handler。

## 7. 测试任务

至少新增或补充以下测试：

- Context 正常返回所有主要 section。
- Context 返回 `durationMs` 和 `stageDurationsMs`。
- 单个 section 超限时返回 `truncated`。
- Editor unavailable 时返回稳定 `state=unavailable`。
- Context 不加载资产。
- Context 不修改 Dirty 状态。
- Context 不修改 Selection。
- 未知参数、非法类型、越界参数被拒绝。
- Capability 禁用后 `editor.getEditorContext` 被拒绝。
- 旧 live-action Tool Registry 索引和旧 Live Write 行为不被破坏。

测试必须基于现有测试框架和 Mock/Fixture 方式，不要为了测试新增第二套 Bridge。

## 8. 本轮完成门槛

只有同时满足以下条件，才可以创建第一个实现 Commit：

1. Python Tool、Bridge 方法和 C++ Capability/路由已连通。
2. Context Handler 已独立实现并有硬上限。
3. 正常、截断、unavailable 和副作用保护测试已补齐。
4. 运行并记录真实结果：

```bat
scripts\python.cmd -m ruff check src tests\python
scripts\python.cmd -m unittest discover -s tests\python -p "test_*.py"
git diff --check
```

5. 如果 C++ 实现已进入本轮，则运行：

```bat
scripts\BuildPluginDirect.cmd
```

不能声称未运行的测试通过。若构建或测试因环境原因无法运行，必须记录具体命令、失败原因、已完成的静态检查和未验证范围。

建议 Commit message：

```text
feat: add realtime editor context tool
```

不要在本轮把 Batch Task、Change Set 或完整保存闭环半实现后混入同一个 Commit。

## 9. 后续阶段路线图

完成 Context Commit 后，再按以下顺序继续：

### 阶段二：Batch Task

新增：

```text
Plugin/UEAgentKit/Source/UEAgentKitEditor/Private/EditorBridgeBatchTaskManager.h
Plugin/UEAgentKit/Source/UEAgentKitEditor/Private/EditorBridgeBatchTaskManager.cpp
Plugin/UEAgentKit/Source/UEAgentKitEditor/Private/EditorBridgeBatchTaskHandlers.cpp
src/ue_agent_kit/realtime_tasks.py
```

首版只支持：

```text
operation=scanCurrentWorld
```

必须支持分帧、进度、取消、最大任务数、Actor/Component 扫描上限、Session/World 失效和有界摘要/详情；不得扫描时加载资产、保存或修改选择。

### 阶段三：Change Set

新增：

```text
src/ue_agent_kit/change_sets.py
```

在现有受控 Work Root/Journaling 边界内管理 Change Set，给现有 Live Apply/Undo/Discard/Save/Verify 增加可选 `change_set_id`。未绑定 Change Set 的旧调用行为必须完全不变。

### 阶段四：完整验证

```bat
scripts\python.cmd -m ruff check src tests\python
scripts\python.cmd -m unittest discover -s tests\python -p "test_*.py"
scripts\BuildPluginDirect.cmd
scripts\TestMcpLiveWriteFast.cmd
scripts\TestMcpLiveWriteRegression.cmd
```

只有真实执行后才能写入结果。真实 UE5.6 回归如果没有运行条件，必须明确说明阻塞原因。

## 10. 绝对禁止

- 不修改 `E:\WorkSpace\UEAgentKit-MemoryContext`。
- 不切换分支，不 Merge `main`，不 Tag，不 Release。
- 不执行 `git reset --hard`、删除未知修改或覆盖上一轮 handoff。
- 不创建第二套 IPC、Session、Envelope、Journal 或错误系统。
- 不加入任意 Python、Console、Shell、SQL、文件路径、UObject Method、Save All 或通用 `set_property`。
- 不把每个 Actor 拆成一次 MCP 调用；批量能力必须在 Plugin 内聚合。
- 不为了“先跑通”放宽 Schema、资源上限或 capability 检查。
- 不伪报编译、测试或真实 UE 回归结果。
- 不在未完成 Context 垂直切片前继续扩散到无关重构。

## 11. 最终汇报格式

完成本轮或遇到阻塞时，必须报告：

1. 实际完成的能力；
2. 修改文件；
3. 新增 Tool、Capability、错误码或状态；
4. Context 的硬上限和耗时数据；
5. 安全边界和副作用验证；
6. 实际运行的命令及结果；
7. 未完成范围和阻塞原因；
8. Commit hash（若已提交）；
9. 下一步建议。

现在开始：先检查 Git 状态并保留现有 handoff 修改，然后实现 Context vertical slice。不要再次停留在泛化设计阶段。
