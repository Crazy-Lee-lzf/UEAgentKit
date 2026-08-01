# Live Editor Realtime I/O 本地 Agent 提示词

下面内容可直接复制给运行在本机仓库中的 Claude Code、Codex CLI 或其他代码 Agent。

```text
你现在负责 UEAgentKit 的 feature/live-editor-realtime-io 分支。

工作区：
E:\WorkSpace\UEAgentKit

必须先阅读：
1. docs\Handoffs\LIVE_EDITOR_REALTIME_IO_HANDOFF_20260801.md
2. docs\AI_NATIVE_UE_EDITOR.md
3. docs\BRANCH_WORKTREES.md
4. docs\PROJECT_STATUS.md
5. docs\COMPARISON_UE_LLM_TOOLKIT.md
6. spec\LIVE_EDITOR_BRIDGE.md
7. spec\MCP_SERVER.md

先执行并确认：
- git branch --show-current
- git status --short
- git rev-parse HEAD
- git rev-list --left-right --count origin/feature/live-editor-realtime-io...HEAD

预期分支必须是 feature/live-editor-realtime-io。工作树如有不属于本任务的修改，先只读分析并汇报，不要覆盖。

任务目标：
按交接文档完成首个 Realtime Foundation 纵向里程碑：
A. ue_get_editor_context 聚合实时上下文；
B. 通用 Batch Task 基础框架，首个 operation=scanCurrentWorld；
C. 最小 Change Set 协议，并让现有 Live Apply/Undo/Discard/Save/Verify 可选绑定 Change Set；
D. 保持当前 12 个 Live Write Operation 和全部旧 Tool 兼容。

核心产品原则：
- 运行中的 UE Editor 是日常开发主路径。
- 目标是复杂 UE 开发任务，不是简单移动 Actor。
- 底层是 CRUD，但 Agent 使用任务 Workflow → 领域 Operation → 基础 CRUD。
- 安全不能让所有读取和实时试验变得很慢。
- R0 读取不做 Backup、Revision Gate 或独立 UE 重载。
- Live Apply 依赖 Snapshot + Transaction + Read-back，默认不保存。
- 保存后才进入 Backup + Independent Verify。
- 禁止任意 Python、Console、Shell、SQL、文件系统路径、UObject Method、Save All 和通用任意 set_property。
- 批量扫描必须在 Plugin 内聚合，不能让 Agent 对每个 Actor 做一次 MCP 调用。
- 长任务必须支持进度、取消、资源上限和 Editor Session 失效。
- 不要另建第二套 IPC、Session、Envelope、Journal 或错误系统，必须复用现有架构。

工作方式：
1. 先审计现有 Bridge、Game Thread 调度、Capability、MCP 注册、Journal 和测试。
2. 直接形成最小实现方案并开始编码，不要再写一份泛化路线规划。
3. 每完成一个纵向阶段立即补测试并运行相关门禁。
4. 发现现有架构缺陷时可以修复，但要保持修改最小、说明证据，不做无关重构。
5. 新接口必须有严格 Schema、硬上限、稳定错误码和 annotations。
6. Context 返回 durationMs、截断信息和 nextActions。
7. Batch Task 首版只做 scanCurrentWorld，不实现任意规则语言。
8. Change Set 先管理现有 Live Write 生命周期，不重写 Transaction。
9. 所有文本文件 UTF-8 无 BOM、CRLF。
10. 不修改 E:\WorkSpace\UEAgentKit-MemoryContext。

最低测试：
- scripts\python.cmd -m ruff check src tests\python
- scripts\python.cmd -m unittest discover -s tests\python -p "test_*.py"
- scripts\BuildPluginDirect.cmd
- scripts\TestMcpLiveWriteFast.cmd

涉及完整保存闭环时：
- scripts\TestMcpLiveWriteRegression.cmd

不要伪报未运行测试。真实 UE 测试无法运行时，明确写出原因、缺失条件和已经完成的静态或单元验证。

Git 规则：
- 只在 feature/live-editor-realtime-io。
- 小而完整的 Commit。
- 可以 Push 当前功能分支。
- 不 Merge main。
- 不 Tag，不 Release。

最终汇报格式：
1. 完成的能力；
2. 关键架构与为什么这样实现；
3. 修改文件；
4. 新 Tool、Capability、错误码；
5. 性能数据；
6. 安全与可逆性；
7. 测试结果；
8. Commit；
9. 未完成范围和下一步。
```
