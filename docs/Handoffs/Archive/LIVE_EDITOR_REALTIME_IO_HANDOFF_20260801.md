# `feature/live-editor-realtime-io` 本地 Agent 开发交接

更新时间：2026-08-01
工作区：`E:/WorkSpace/UEAgentKit`
分支：`feature/live-editor-realtime-io`
起始基线：`4d1698f docs: define AI-native editor development model`

## 1. 分支使命

本分支负责把运行中的 UE5 Editor 变成 Agent 的主要开发工作台：

```text
当前 Editor / PIE 上下文
→ 快速批量查询与诊断
→ 任务级 Change Set
→ 可逆实时增删查改
→ 编译、验证、撤销或授权保存
```

离线导出、SQLite、Revision Export 和 Commandlet 保留用于：

- 未加载资产和全项目查询。
- 批量离线分析。
- 保存后独立验证。
- Backup/rollback。
- CI。

实时 Editor I/O 是日常辅助开发主路径，但不能退化成任意 Python、Console、UObject Method、Save All 或无约束属性写入。

## 2. 当前基础

已经实现：

- 认证 localhost Editor Bridge。
- Editor 状态、选择、打开资产、Dirty、Level、PIE、Output Log 和编译诊断。
- 不触发加载的 Live Asset Inspection。
- 当前 Blueprint Graph/Node 选择。
- 资产打开/聚焦、Content Browser 同步、ActorGuid 聚焦。
- Blueprint 内存编译、官方 Data Validation 和 Automation。
- 12 个受控 Live Write Operation：
  - Data Asset 标量、引用、Struct、Array、Set、Map。
  - Material Instance Scalar/Vector/Texture/Static Switch。
  - DataTable Cell/RowFields/Add/Remove/Rename。
- Transaction、Snapshot、No-op、失败恢复。
- 精确 Undo/Discard。
- Authorized Save → Independent Verify。
- 可恢复 Live Apply Journal。
- Fast/Full 真实 UE5.6 回归。

核心代码：

```text
Plugin/UEAgentKit/Source/UEAgentKitEditor/Private/EditorBridge*.cpp
Plugin/UEAgentKit/Source/UEAgentKitEditor/Private/LiveWrite*.cpp
src/ue_agent_kit/editor_bridge.py
src/ue_agent_kit/mcp_server.py
src/ue_agent_kit/mcp_workflow_tools.py
src/ue_agent_kit/agent_workflow.py
```

核心规范：

```text
docs/AI_NATIVE_UE_EDITOR.md
docs/BRANCH_WORKTREES.md
docs/PROJECT_STATUS.md
docs/COMPARISON_UE_LLM_TOOLKIT.md
spec/LIVE_EDITOR_BRIDGE.md
spec/MCP_SERVER.md
```

## 3. 用户真正需要的开发场景

优先服务复杂任务，不以简单移动 Actor 为核心卖点：

1. Bug 诊断，例如枪械为什么打不中敌人。
2. 批量检查当前地图中的指定资产或 Component。
3. 审计和整理 DataTable。
4. 仿照已有角色、武器或技能树创建新内容。
5. 根据项目结构分析并实现新功能。

底层仍是 CRUD，但 Agent 使用模型应为：

```text
任务级 Workflow
→ 领域 Operation
→ 基础 Create/Read/Update/Delete/Compile/Validate
```

## 4. 首个开发里程碑：Realtime Foundation

本地 Agent 本轮不要尝试一次实现全部 Blueprint、Animation 和 Actor CRUD。先完成能明显提升速度和后续扩展能力的基础纵向切片。

### 4.1 统一 Editor Context

新增聚合型实时读取入口，减少 Agent 为一个问题连续调用多个 Tool。

建议 MCP 名称：

```text
ue_get_editor_context
```

一次返回有界摘要：

- Editor/Bridge Session 和 Engine/Plugin。
- 当前 Level、World Type、PIE/SIE。
- 当前选择摘要。
- 当前打开资产摘要。
- Dirty Package 摘要。
- 当前 Blueprint Graph/Node 选择摘要。
- 最近编译错误计数和 Output Log Cursor。
- 每部分是否截断及可继续调用的 `nextActions`。

要求：

- R0 只读。
- 不加载资产。
- 不修改选择或 Editor 状态。
- 在一个 Bridge Request 内收集，避免 MCP 多次往返。
- 对每部分设置硬上限。
- 返回 `durationMs` 和各阶段耗时，便于性能回归。
- Editor 未运行时保持稳定 `state=unavailable`。

### 4.2 Batch Task 基础框架

建立通用长查询任务，而不是让 Agent 对每个对象调用一次 Tool。

建议 Bridge/MCP 能力：

```text
start
status
result
cancel
```

首个 Operation 只实现：

```text
scanCurrentWorld
```

最小功能：

- 扫描当前 Editor World 的 Actor 和 Component。
- 支持 Class、Actor Label、Component Class、Asset Path 前缀、Data Layer 或当前选择范围中的可实现子集。
- 插件内聚合 Actor 数、Component 数、唯一资产数、按 Class 计数和有界明细。
- 默认返回摘要，详情分页或按 Issue Group 展开。
- 支持 Progress、Cancel、Partial Result 和明确终态。
- 长任务分帧执行，不能连续阻塞 Game Thread。
- 不加载未加载资产，不保存，不改变选择。
- 任务绑定 `editorSessionId`，Editor 重启后旧任务失效。

首版不要求通用规则语言；先提供固定、可测试的扫描字段和聚合。

### 4.3 Change Set 最小协议

建立任务级修改容器，但不要重写现有 Live Write Transaction。

最小模型：

```text
changeSetId
taskId
editorSessionId
title
status
operations
affectedAssets
transactionIds
validation
saveState
createdAtUtc
updatedAtUtc
```

状态至少：

```text
planned
applied
partially_applied
undone
discarded
saved
verified
failed
```

本轮要求：

- 现有 Live Apply 可选绑定一个 `changeSetId`。
- Apply/Undo/Discard/Save/Verify 生命周期更新 Change Set。
- 不绑定 Change Set 的旧调用继续兼容。
- Change Set 只保存必要的结构化引用，不复制完整巨大 Evidence。
- MCP Server 重启后，至少能从现有 Live Write Journal 恢复未完成 Change Set 的可确认状态；无法证明的状态标记为 `unknown`，不能伪报。
- 一个 Change Set 可引用多个 Transaction，但首版仍遵守现有单资产 Clean Package 门禁。

### 4.4 性能与安全预算

本里程碑原则：

- R0 查询不走 Backup、Revision Gate 或独立 UE。
- Context 聚合只读目标：常规项目下尽量 `< 250 ms`；无法保证时必须返回 `durationMs` 和截断信息。
- Batch Task 使用每帧预算避免编辑器明显卡顿。
- Live Apply 不启动独立 UE。
- 保存后才使用 Backup 与 Independent Verify。
- 禁止添加任意 Python、Console、Shell、SQL、文件路径、UObject Method 或 Save All。
- 禁止为了通用化开放任意 `set_property`。
- 新 Tool/Operation 必须可从 Capability/Discovery 禁用，并在执行边界再次检查。

## 5. 后续阶段，不属于本轮强制范围

Realtime Foundation 稳定后再推进：

- Weapon Hit Diagnostic。
- Map Asset Audit 规则集。
- DataTable Audit/Normalize Change Set。
- Blueprint Graph CRUD。
- Actor/Component 批量配置。
- Enhanced Input。
- Montage/AnimBP/Control Rig/Retarget。
- Clone Skill Tree。
- Feature Design/Impact Analysis。

本轮可以保留清晰扩展点，但不要提交空 Tool 或半实现接口。

## 6. 推荐代码边界

根据现有结构调整，但避免继续放大 `EditorBridge.cpp` 和 `mcp_server.py`。

```text
Plugin/.../EditorBridgeContextHandlers.cpp
Plugin/.../EditorBridgeBatchTaskHandlers.cpp
Plugin/.../EditorBridgeBatchTaskManager.{h,cpp}

src/ue_agent_kit/realtime_context.py
src/ue_agent_kit/realtime_tasks.py
src/ue_agent_kit/change_sets.py
src/ue_agent_kit/mcp_realtime_tools.py
```

原则：

- UE UObject 读取在 Game Thread。
- 耗时聚合尽量移出 Game Thread。
- Task Manager 有明确生命周期和资源上限。
- MCP 层不直接实现 UE 领域逻辑。
- Operation/Task Descriptor 与执行器分离。
- 返回结构使用稳定 Envelope 和错误码。
- 复用现有 Editor Session、Project Identity、Policy、Receipt、Journal 和错误映射。

## 7. 必须先审计的实现点

开始编码前读取并理解：

```text
Plugin/.../EditorBridge.cpp
Plugin/.../EditorBridge.h
Plugin/.../EditorBridgeStatusHandlers.cpp
Plugin/.../EditorBridgeGraphHandlers.cpp
Plugin/.../EditorBridgeDiagnosticHandlers.cpp
Plugin/.../EditorBridgeWriteHandlers.cpp
Plugin/.../LiveWriteTransaction.cpp
Plugin/.../LiveWriteOperationRegistry.cpp
src/ue_agent_kit/editor_bridge.py
src/ue_agent_kit/mcp_server.py
src/ue_agent_kit/agent_workflow.py
```

需要确认：

- Bridge 当前请求线程和 Game Thread 调度方式。
- Request/Response Envelope。
- Capability 注册和握手版本。
- 当前超时和最大输出限制。
- MCP Tool 注册模式。
- Journal 恢复位置。
- 真实 UE Smoke Fixture 和启动方式。

不要另起第二套 IPC、Session ID、错误 Envelope 或 Journal。

## 8. 测试和完成门禁

Python：

```bat
scripts\python.cmd -m ruff check src tests\python
scripts\python.cmd -m unittest discover -s tests\python -p "test_*.py"
```

C++：

```bat
scripts\BuildPluginDirect.cmd
```

现有 Live 回归至少保持：

```bat
scripts\TestMcpLiveWriteFast.cmd
```

涉及完整保存闭环时再运行：

```bat
scripts\TestMcpLiveWriteRegression.cmd
```

新增测试至少覆盖：

- Context 正常、截断、Editor unavailable。
- Context 不加载资产、不改变 Dirty/选择。
- Batch Start/Progress/Cancel/Complete。
- Editor Session 变化后任务失效。
- Batch 上限和非法过滤参数。
- Change Set 未绑定时旧行为兼容。
- Apply → Undo/Discard 更新 Change Set。
- Apply → Save → Verify 更新 Change Set。
- MCP 重启后的恢复或 `unknown` 语义。
- Capability 禁用后旧缓存调用仍被拒绝。
- `git diff --check`、UTF-8 无 BOM、CRLF。

不能声称运行了未实际运行的 UE 回归。

## 9. Git 与交付规则

- 只修改 `E:/WorkSpace/UEAgentKit`。
- 分支必须保持 `feature/live-editor-realtime-io`。
- 不修改 `E:/WorkSpace/UEAgentKit-MemoryContext`。
- 不自行合入 `main`。
- 不创建 Tag 或 Release。
- 每个提交必须是可编译、可测试的纵向小阶段。
- 可以提交并推送本分支。
- 最终交付必须包含：架构变化、新 Tool/Capability/错误码、性能测量、安全边界、测试结果、未完成范围和 Commit 列表。

## 10. 完成定义

本地 Agent 本轮完成时，开发者应该能够：

1. 一次调用获得当前 Editor 工作上下文，不再连续调用多个小 Tool。
2. 启动一个不会长时间卡住 Editor 的当前地图批量扫描任务，看到进度、取消并按摘要或详情取结果。
3. 将现有 Live Write 生命周期绑定到一个可查询的 Change Set。
4. 保持现有 12 个 Live Operation、Undo/Discard、Save/Verify 和全部旧 Tool 兼容。
5. 用真实 UE5.6 回归证明新增能力，而不是只通过 Mock。

## 11. 2026-08-01 当前执行交接（增量）

> 本节记录本轮 Agent 的实际执行状态。它不表示功能已经实现；下一位 Agent 应从“开始编码”继续，而不是把审计结果当成已提交代码。

### 11.1 Git checkpoint

- 工作区：`E:/WorkSpace/UEAgentKit`
- 分支：`feature/live-editor-realtime-io`
- 当前 HEAD：`53f8317578fe8f96b6867dc94e1bd75c1d6439c0`
- 当前提交：`docs: add realtime io agent handoff`
- 与 `origin/feature/live-editor-realtime-io`：`0 0`
- 写入本节前的工作树 checkpoint：干净；没有 staged、unstaged 或 untracked 文件。
- 本轮没有 Commit、Push、Merge、Tag 或 Release；当前未提交修改为本 handoff 文件和下一位 Agent 提示词文件，均不包含实现代码。
- `E:/WorkSpace/UEAgentKit-MemoryContext` 未修改。

### 11.2 已完成的工作

1. 已读取本任务要求的 prompt、handoff、架构、项目状态、Bridge/MCP 规范文档。
2. 已审计现有 C++ Editor Bridge：localhost TCP、newline-delimited JSON、认证、Session、Game Thread 调度、Capability、响应上限和现有 Builder。
3. 已审计 Python Bridge/MCP：参数归一化、不可用状态、Tool Registry、MCP 注册、Capability Discovery 和旧 live-action 索引兼容约束。
4. 已审计 Live Write Transaction、Journal、Apply/Undo/Discard/Save/Verify 生命周期及重启恢复边界。
5. 已确定 Realtime Foundation 的实现拆分：Context、`scanCurrentWorld` Batch Task、Change Set，以及旧 Live Write 行为兼容。

### 11.3 尚未完成的工作

- 尚未修改任何 C++、Python、测试或文档实现文件（本节交接记录除外）。
- 尚未实现 `ue_get_editor_context`。
- 尚未实现 Batch Task Manager、分帧扫描、进度、取消、Session/World 失效。
- 尚未实现 Change Set 或把现有 Live Write 工具接入 Change Set。
- 尚未运行 Ruff、Python 单元测试、Plugin Build、Live Write Fast/Regression 或真实 UE 回归。
- 因此不能声称已有编译通过、测试通过或 UE Editor 验证结果。

### 11.4 下一次 Commit 的建议门槛

下一次提交应先做一个可编译、可测试的 Context 垂直切片，不要等待全部 Realtime Foundation 完成。建议包含：

1. `realtime` ToolGroup 和 `ue_get_editor_context` ToolDefinition。
2. Context 参数归一化、统一错误响应和新增 Capability 校验。
3. MCP Tool 注册及 Capability Discovery contract。
4. C++ `editor.getEditorContext` Capability、路由和独立 Context Handler。
5. 有界 Context sections、`durationMs`、阶段耗时、`truncated` 和 `nextActions`。
6. Context 正常、不可用、截断以及不修改 Dirty/Selection 的测试。
7. 至少运行 Python lint/unit test，并记录真实结果。

完成上述门槛后再创建第一个实现 Commit；不要把仅有方案设计的状态误报为功能进度。

### 11.5 后续实现顺序

1. Context vertical slice：先完成并测试，再提交。
2. Batch Task：新增 `EditorBridgeBatchTaskManager.{h,cpp}`、Handler 和 Python realtime task 层；首版只支持 `scanCurrentWorld`。
3. Change Set：新增受控 Work Root 内的 Change Set 状态和可选 `change_set_id`，复用现有 Transaction、Receipt、Session、Journal 和错误系统。
4. Live Write 闭环：Apply → Undo/Discard → Save → Verify，并保持未绑定 Change Set 的旧调用行为完全不变。
5. 全量门禁：Ruff、Python unittest、Plugin Build、Live Write Fast；保存闭环完成后再运行 Regression。
6. 最后执行 `git diff --check`、UTF-8 无 BOM/CRLF 检查，并在真实 UE5.6 环境具备条件时运行回归。

### 11.6 下一位 Agent 的启动动作

```bat
git branch --show-current
git status --short
git rev-parse HEAD
git rev-list --left-right --count origin/feature/live-editor-realtime-io...HEAD
```

确认工作树仍干净后，直接从 Context vertical slice 开始。推荐先修改/新增：

```text
src/ue_agent_kit/tool_registry.py
src/ue_agent_kit/editor_bridge.py
src/ue_agent_kit/mcp_realtime_tools.py
src/ue_agent_kit/mcp_server.py
Plugin/UEAgentKit/Source/UEAgentKitEditor/Private/EditorBridge.h
Plugin/UEAgentKit/Source/UEAgentKitEditor/Private/EditorBridge.cpp
Plugin/UEAgentKit/Source/UEAgentKitEditor/Private/EditorBridgeContextHandlers.cpp
```

不要重新创建第二套 IPC、Session、Envelope、Journal、错误码系统；不要修改 `E:/WorkSpace/UEAgentKit-MemoryContext`；不要加入任意 Python、Console、Shell、SQL、文件系统路径、UObject Method、Save All 或通用 `set_property` 能力。

### 11.7 交接结论

当前状态是“审计和设计完成、实现尚未开始”；当前未提交差异仅包含 handoff 文档和下一位 Agent 提示词，不包含实现代码。与上一份进度报告相比，没有新增代码差异，也没有新增测试结果。下一步的最小可交付目标是完成 Context vertical slice 并形成第一个可验证 Commit。
## 12. 2026-08-02 实现完成与合并门禁

本节是后续实现记录，覆盖第 11.3–11.7 节的历史“尚未实现”状态；旧章节保留用于说明最初设计与实施顺序。

### 12.1 已完成能力

1. `ue_get_editor_context` 已完成真实 Editor 纵向闭环：一次有界只读请求返回 Editor、World、Selection、Open Assets、Dirty Packages、Blueprint Graph Selection、Compile Errors、Output Log Cursor、阶段耗时和 `nextActions`。
2. Batch Task Manager 已完成：首个固定 Operation 为 `scanCurrentWorld`，只扫描当前已加载 World，支持 Start/Status/Cancel、进度、部分结果、超时、Session/World 失效和明确终态。
3. Batch 扫描不再在启动请求中同步遍历整个 World；Level 使用弱引用，Actor 不跨帧保存裸指针。每 Tick 同时受最多 256 个 Actor Slot 和约 2 ms 时间预算约束。
4. Batch 状态默认只返回摘要；详情通过 `include_details/detail_offset/detail_limit` 分页读取，单页最多 5 个 Actor，避免超过 Python Bridge 的 1 MiB 单响应上限。
5. Change Set 已升级为 schema v2：持久化 `taskId/editorSessionId/title/status/operations/affectedAssets/transactionIds/validation/saveState`，复用现有 Plan、Transaction、Receipt、Save 和 Verify 生命周期。
6. Change Set 状态包括 `planned/applied/partially_applied/undone/discarded/saved/verified/failed/unknown`；Undo/Discard/Verify 后保留历史，重启后无法重新证明的内存状态明确标记为 `unknown`。
7. Change Set 容量清理只淘汰终态记录；活跃或未决记录不会被静默删除。混合 `verified/undone/discarded/failed` Operation 也按终态处理，避免无活动操作的记录永久占用容量。
8. Closed Loop 测试诊断会完整展开 Python `ExceptionGroup`，真实契约错误不再被 PowerShell 外层异常掩盖。

### 12.2 已通过门禁

- Python unittest：316/316。
- Ruff：通过。
- UE5.6 Plugin Direct Build：通过。
- 真实 UE5.6 Fast Regression：Scalar Write、Undo/Discard、Closed Loop 全部通过。
- 真实 UE5.6 Full Regression：Scalar、Reference、Structured、Material、DataTable、Undo/Discard、Closed Loop 七组全部通过。
- Closed Loop 额外验证 Editor Context、Batch 摘要/分页和 Change Set `planned → applied → saved → verified`；保存后由独立 Unreal 进程重载验证，SQLite Index 与冻结 Revision Export 哈希保持不变。

### 12.3 合并约束

- 本分支可进入与 `feature/memory-context` 的临时集成验证。
- 两分支生产代码预计可自动合并；文档与 Tool 数量断言需要按合并后实际注册量统一。
- 正式合并前仍需执行 `git diff --check`、UTF-8 无 BOM/CRLF 检查、完整 Python/Ruff、Plugin Build，并在临时合并副本中复跑关键门禁。
- 本交接不授权直接 Merge；正式分支合并由后续明确操作完成。
