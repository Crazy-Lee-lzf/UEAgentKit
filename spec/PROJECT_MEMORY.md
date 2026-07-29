# Revision-aware Project Memory

## 1. 存储边界

Project Memory 使用独立的可写 SQLite 数据库，例如：

```text
<WorkRoot>/memory.sqlite3
```

它不得存放在 `index.sqlite3` 中。项目索引属于可冻结、可替换的只读 Snapshot；Project Memory 必须跨索引重建和 Snapshot 切换持续存在。

首版只使用 SQLite 和 FTS5，不引入向量数据库。Memory 数据库拥有独立的 Schema Version 和迁移记录，不复用索引数据库的 `PRAGMA user_version` 生命周期。

## 2. 记录类型

Schema v1 支持六类记录：

```text
projectFact
projectRule
decisionRecord
knownIssue
taskRecord
runtimeEvidence
```

- `projectFact`：项目事实，例如资产结构、配置值和已确认行为。
- `projectRule`：必须持续遵守的项目规范和安全约束。
- `decisionRecord`：已做出的技术或产品决策及其原因。
- `knownIssue`：已确认问题、影响、规避方式和状态。
- `taskRecord`：任务目标、进度、产物和最终结论。
- `runtimeEvidence`：Automation、Data Validation、日志或其他运行证据。

## 3. 统一字段

每条记录必须包含：

```text
recordId
projectKey
recordType
subjectKey
title
body
sourceKind
confidence
status
createdAtUtc
observedAtUtc
updatedAtUtc
```

可选关联包括：

```text
scopes
revisionSet
artifacts
relations
details
```

`recordId` 使用 `mem_<32 lowercase hex>`，创建后保持稳定。`subjectKey` 是同一逻辑问题的稳定主题键，用于冲突检测，但不具有唯一约束，因此冲突结论可以并存。

## 4. 来源

```text
user-confirmed
tool-observed
model-inferred
```

初始状态规则：

- `user-confirmed` 默认 `valid`。
- `tool-observed` 只有在携带非空且全部稳定的 Revision Set 时默认 `valid`；否则为 `unverified`。
- `model-inferred` 默认 `unverified`。

来源和状态必须分开存储。模型推断不能伪装成用户确认或工具观测。

## 5. Scope

一条记录可以绑定多个 Scope：

```text
project
asset
symbol
graph
node
dataTableRow
log
file
external
```

同一记录内不允许重复的 `scopeType + scopeKey`。

## 6. Revision Set

Revision Set 由以下字段组成：

```text
assetPath
revision
revisionStable
```

- `assetPath` 必须是 `/Game/...` Object Path。
- 同一记录内每个 Asset Path 最多出现一次。
- 只有 `revisionStable=true` 的绑定参与自动失效判断。
- 不稳定 Revision 可以保留为运行上下文，但不会被当作长期有效证据。

当当前索引中的稳定 Revision 缺失或与记录绑定值不一致时，记录自动转为 `stale`。自动失效不会删除记录，也不会自动恢复为 `valid`。

## 7. 状态机

```text
valid
stale
conflicted
superseded
unverified
```

允许的转换：

```text
valid       -> stale | conflicted | superseded
unverified  -> valid | stale | conflicted | superseded
conflicted  -> valid | stale | superseded
stale       -> valid | superseded
superseded  -> terminal
```

每次状态变化写入 `memory_status_events`，保留原状态、目标状态、原因、时间和结构化细节。

## 8. 冲突并存

以下类型参与自动冲突检测：

```text
projectFact
projectRule
decisionRecord
knownIssue
```

当同一 `projectKey + recordType + subjectKey` 下存在语义内容哈希不同的活动记录时：

1. 不覆盖或删除任何记录。
2. 所有冲突记录转为 `conflicted`。
3. 双向写入 `conflictsWith` Relation。
4. 来源、置信度和 Revision Set 继续独立保留。

`taskRecord` 和 `runtimeEvidence` 表示时序过程和证据，不自动按相同 Subject 判定冲突。

## 9. Supersede

显式替代要求旧记录和新记录具有相同的：

```text
projectKey
recordType
subjectKey
```

旧记录转为 `superseded`，保存 `supersededByRecordId`；新记录增加指向旧记录的 `supersedes` Relation。Supersede 不删除历史记录。

## 10. Artifact 关联

Artifact 用于连接：

```text
Patch
Backup Manifest
Validation Evidence
Automation Report
Log
External Document
```

首版 Artifact 只保存 `artifactKind`、`artifactRef` 和结构化 `details`，不复制外部文件内容。

## 11. Schema v1 表

```text
memory_schema_migrations
memory_records
memory_scopes
memory_revisions
memory_artifacts
memory_relations
memory_status_events
memory_records_fts
```

## 12. 首个实现边界

Schema v1 和 Python Storage API 提供：

- 创建、读取和筛选记录。
- 来源驱动的初始状态。
- Scope、Revision 和 Artifact 绑定。
- 冲突并存。
- 显式 Supersede。
- Revision Set 自动标记 Stale。
- FTS5 基础索引。

首个实现不包含 MCP Tool、向量检索、自动摘要、自动从对话提取记忆或跨项目数据库选择。这些能力在存储契约稳定后逐项增加。
## 13. 固定工程 Memory Service

`ProjectMemoryService` 在创建时固定：

```text
memory.sqlite3
projectKey
```

之后的创建、读取、查询、Supersede 和 Revision 校验都不能切换项目。Service 提供：

- 初始化与状态统计。
- 固定 Project Key 的记录创建和精确读取。
- FTS5 查询，以及 Record Type、Status 和 Scope 过滤。
- 默认查询 `valid`、`unverified` 和 `conflicted`，避免把 `stale` 或 `superseded` 当作当前事实。
- 读取固定索引数据库中的 `metadata.project_key` 与 `assets.revision_value`。
- 当索引 Project Key 不匹配时拒绝校验，不创建或修改 Memory 数据库。
- 当索引 Revision 缺失或变化时调用统一失效规则。

Service 仍不接受来自 Agent 的任意数据库路径。MCP 接入时，Memory 路径和 Project Key 必须由 Server 固定配置提供。
