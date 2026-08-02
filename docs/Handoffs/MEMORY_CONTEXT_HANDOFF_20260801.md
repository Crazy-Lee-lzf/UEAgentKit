# `feature/memory-context` 交接文档

更新时间：2026-08-01  
工作区：`E:/WorkSpace/UEAgentKit-MemoryContext`  
分支：`feature/memory-context`  
起始基线：`4d1698f docs: define AI-native editor development model`

## 1. 新对话接手方式

新对话第一条消息建议直接使用：

```text
@WorkspaceBridge E:\WorkSpace\UEAgentKit-MemoryContext\docs\Handoffs\MEMORY_CONTEXT_HANDOFF_20260801.md

接手 feature/memory-context。按交接文档完成单人版 Memory/Context MVP。
先核对分支、工作树和现有 Schema v2 实现；不要重新泛化规划，也不要修改另一个 Worktree。
```

开始工作前必须确认：

```text
git branch --show-current
git status --short
git rev-parse HEAD
git rev-list --left-right --count origin/feature/memory-context...HEAD
```

预期分支为 `feature/memory-context`。不要在 `E:/WorkSpace/UEAgentKit` 中实现本任务，那个目录属于 `feature/live-editor-realtime-io`。

## 2. 分支目标

本分支负责让 Agent 能低 Token、可维护地理解并延续 UE 项目工作：

```text
Schema v2 Revision-aware Memory
→ Knowledge Tree
→ Active Work
→ 渐进式 Context
→ Evidence 按需展开
```

它与 Realtime I/O 的关系是：

- Memory/Context 告诉 Agent：项目是什么、为什么这样设计、当前做到哪里、应该看什么。
- Realtime I/O 告诉 Agent：运行中的 UE Editor 当前是什么状态，以及如何执行实时增删查改。
- 两条线以后通过 `TaskContext`、`ChangeSet`、`EvidenceReference`、`AssetIdentity` 和 `RevisionReference` 汇合。

## 3. 当前已完成基础

0.6.0 已经实现 Schema v2 平面记录库：

- 独立 SQLite/FTS5。
- 六类记录：`projectFact`、`projectRule`、`decisionRecord`、`knownIssue`、`taskRecord`、`runtimeEvidence`。
- 来源、Confidence、Scope、Revision Set、Artifact、Relation 和状态机。
- `valid/stale/conflicted/superseded/unverified`。
- Revision 变化自动 stale。
- Task Outcome 与 Workflow Evidence。
- 固定工程 MCP/CLI 和审计导出。

主要代码：

```text
src/ue_agent_kit/memory_schema.py
src/ue_agent_kit/project_memory.py
src/ue_agent_kit/memory_service.py
src/ue_agent_kit/memory_tasks.py
src/ue_agent_kit/memory_reports.py
src/ue_agent_kit/mcp_memory_tools.py
src/ue_agent_kit/mcp_server.py
```

主要规范和设计：

```text
spec/PROJECT_MEMORY.md
spec/MCP_SERVER.md
docs/MEMORY_ARCHITECTURE.md
docs/AI_NATIVE_UE_EDITOR.md
docs/BRANCH_WORKTREES.md
```

主要测试：

```text
tests/python/test_project_memory.py
tests/python/test_memory_service.py
tests/python/test_memory_tasks.py
tests/python/test_memory_cli.py
tests/python/test_mcp_server.py
tests/integration/memory_cli_smoke.py
tests/integration/mcp_memory_smoke.py
```

## 4. 本分支完成定义

本轮把 `feature/memory-context` 完成为**单人、本地、固定项目版 MVP**。

### 4.1 Schema v3：Knowledge Tree

新增稳定知识节点，至少包含：

```text
nodeId
projectKey
path
parentNodeId
nodeType
title
summary
createdAtUtc
updatedAtUtc
details
```

约束：

- Path 为规范化绝对知识路径，例如 `/project/combat/weapons`。
- 同一 `projectKey + path` 唯一。
- 根节点使用 `/project`。
- Parent 必须属于同一项目，不允许环。
- 删除有子节点或绑定记录的节点时必须拒绝。
- `nodeType` 首版允许 `project/system/feature/component/entity/implementation`，但不限制树深度。
- `memory_records` 增加可空 `node_id`，旧记录迁移后仍然有效；不要强制猜测旧记录所属节点。

### 4.2 Active Work

新增独立工作项，不把 TODO、阻塞和临时排查过程污染到长期知识中。

至少包含：

```text
workItemId
projectKey
title
status
priority
description
nextAction
blockedReason
owner
createdAtUtc
updatedAtUtc
completedAtUtc
details
```

状态：`planned/in_progress/blocked/done/cancelled`。

需要支持：

```text
start
add_todo
set_next_action
block
resume
complete
cancel
```

工作项可关联多个 Knowledge Node 和多个 `/Game/...Asset.Asset`。核心关系使用正规化关联表，不要只塞入不可查询 JSON。

### 4.3 渐进式披露

实现 0–4 级读取语义：

```text
L0  Path/Title/一句话摘要/状态/子节点数量/Active Work 标记
L1  节点摘要
L2  实现概览与相关有效记录摘要
L3  完整记录
L4  原始 Evidence/Artifact
```

默认只返回 L0/L1。必须由 Service 强制：

- 最大节点数、最大记录数、最大展开深度。
- 最大字符或 Token 近似预算。
- 默认过滤 `stale` 和 `superseded`。
- 截断时返回 `truncated=true` 和结构化 `nextActions`。

首版不要求分词模型，可以使用确定性的字符预算，例如约 `4 chars ≈ 1 token`，但必须有单元测试并明确它只是预算近似。

### 4.4 高层 Service 与 MCP Tool

保持现有 Tool 完全兼容，并新增高层入口。公共命名使用现有 `ue_` 前缀：

```text
ue_memory_get_context
ue_memory_expand_node
ue_memory_get_evidence
ue_memory_update_knowledge
ue_memory_update_work
```

最小职责：

- `ue_memory_get_context`
  - 接收 query、可选 node path、可选 asset paths、detail level、budget。
  - 返回 Project Profile、匹配节点、相关 Active Work、少量有效记录摘要和 `nextActions`。
- `ue_memory_expand_node`
  - 按精确 Path 展开指定深度和 Detail Level。
- `ue_memory_get_evidence`
  - 按精确 Record ID 读取 Evidence/Artifact，不做模糊猜测。
- `ue_memory_update_knowledge`
  - 使用明确 Action 管理节点和稳定知识。
  - 不允许 Agent 直接提交 SQL、Revision SHA 或任意数据库路径。
- `ue_memory_update_work`
  - 使用明确 Action 管理 Active Work 生命周期。

现有 Tool 不得删除、改名或改变既有返回语义：

```text
ue_memory_search
ue_memory_get
ue_memory_add_rule
ue_memory_record_finding
ue_memory_record_task
ue_memory_mark_superseded
ue_memory_validate
```

### 4.5 兼容与迁移

必须满足：

- `CURRENT_MEMORY_SCHEMA_VERSION` 升级到 3。
- 现有 Schema v2 数据库可原地迁移到 v3。
- 迁移后旧记录数量、内容摘要、Evidence 摘要、Revision、Scope、Relation 和状态不变。
- 旧记录允许 `node_id IS NULL`。
- 新建数据库直接得到 v3。
- 重复执行迁移不产生重复表、列或数据。
- Schema v2 审计导出字段继续存在；新增字段以兼容方式附加。
- 不允许破坏 0.6.0 CLI 和 MCP Smoke。

## 5. 推荐实现结构

可根据实际代码调整文件边界，但不要把所有逻辑继续堆入 `project_memory.py`。

```text
src/ue_agent_kit/memory_schema.py
    Schema v3 migration

src/ue_agent_kit/memory_tree.py
    KnowledgeNode、Path 规范化、CRUD、树查询

src/ue_agent_kit/active_work.py
    WorkItem、状态机、关联关系

src/ue_agent_kit/memory_context.py
    渐进式披露、预算、Context 组装、nextActions

src/ue_agent_kit/memory_service.py
    固定项目门面

src/ue_agent_kit/mcp_memory_tools.py
    MCP 参数校验和 Envelope
```

要求：

- 数据层函数保持确定性。
- Service 层负责固定项目、预算和跨表规则。
- MCP 层只做严格参数解析、错误映射和返回 Envelope。
- 禁止在 MCP Tool 内直接拼大量 SQL。
- 核心模型使用清晰 dataclass/enum，不用随意字典代替。

## 6. 建议实施顺序

1. 阅读现有 Schema v2、Service、MCP Tool 和测试。
2. 增加 Schema v3 Migration 及迁移测试。
3. 实现 Knowledge Tree 数据层与测试。
4. 实现 Active Work 数据层、状态机和测试。
5. 扩展 `ProjectMemoryService`。
6. 实现渐进式 Context 与预算测试。
7. 注册五个高层 MCP Tool。
8. 增加 CLI 或 Smoke 所需最小入口，不为追求界面数量重复实现逻辑。
9. 更新中英文规范和状态文档。
10. 运行完整门禁，做一次独立代码审查和错误路径检查。

## 7. 必测错误路径

至少覆盖：

- 非法 Path、重复 Path、缺失 Parent、跨项目 Parent。
- 删除非空节点、Parent 环。
- 非法 Work 状态迁移、已完成 Work 再次 Block。
- Context Budget 小于最小值或超过上限。
- Context 截断和 `nextActions`。
- 默认不返回 stale/superseded。
- `ue_memory_get_evidence` Record 不存在。
- MCP 旧缓存调用被严格参数校验拒绝。
- Schema v2 → v3 迁移保留所有旧数据。
- 固定项目不匹配。
- FTS5 与原有搜索仍正常。

## 8. 完成门禁

最低门禁：

```bat
scripts\python.cmd -m ruff check src tests\python
scripts\python.cmd -m unittest discover -s tests\python -p "test_*.py"
```

同时运行现有 Memory Smoke：

```bat
scripts\TestMemoryCli.cmd
scripts\TestMcpMemory.cmd
```

如果 Smoke 需要固定测试环境，必须记录实际命令、Fixture、输出目录和未执行原因；不能把未运行写成通过。

提交前：

```text
git diff --check
UTF-8 无 BOM
文本文件保持 CRLF
工作树只包含本分支相关修改
```

完成标准：

- 新旧测试全部通过。
- 新 MCP Tool 有稳定 Schema、annotations 和错误码。
- 文档中明确哪些已实现，哪些仍是后续方向。
- 不修改 Realtime I/O Worktree。
- 不创建 Tag/Release。
- 未经用户明确要求，不向 `main` 合并。

## 9. 非目标

本轮不要实现：

- 多人共享 Knowledge Service。
- 云端同步和账号体系。
- 向量数据库或 Embedding。
- 自动调用 LLM 总结节点。
- Realtime Editor CRUD。
- Change Set 的 UE Transaction 实现。
- 自动修改源资产来匹配 Memory。
- 任意 SQL、文件路径或数据库切换 Tool。
- 完整图形化知识树 UI。

这些属于后续阶段，不能为了“完整”扩大本分支范围。

## 10. Git 规则

- 只在 `feature/memory-context` 开发。
- 保持小而完整的提交，例如：
  - `feat: add knowledge tree schema`
  - `feat: add active work service`
  - `feat: add progressive memory context tools`
  - `docs: update memory context architecture`
- 公共协议若影响 Realtime I/O，应先形成独立 Commit，之后再由用户决定是否合入 `main`。
- 可以提交并推送本分支；不要自行 Merge、Tag 或 Release。
- 最终汇报必须列出：
  - Commit。
  - 修改文件。
  - 新增数据库表和 Tool。
  - 测试结果。
  - 兼容性结论。
  - 尚未完成的边界。
