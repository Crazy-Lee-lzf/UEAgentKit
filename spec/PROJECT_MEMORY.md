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

## 11. Schema v2 表

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

Schema v2 在 `memory_records` 中增加 `evidence_sha256`。打开可写数据库时会自动把 Schema v1 记录迁移到 v2，并基于现有 Source、Revision Set 和 Artifact 绑定回填摘要。

两个摘要职责不同：

- `contentSha256`：覆盖 Record Type、Subject、Title、Body、Scope 与 details，用于语义内容比较和冲突检测。
- `evidenceSha256`：覆盖 Project Key、`contentSha256`、Source、Confidence、Revision Set 与 Artifact 绑定，用于审计证据完整性。

读取记录时会重新计算两个摘要。正文、Scope、details、Revision 或 Artifact 被数据库外部修改后，读取会以摘要不匹配失败；状态迁移和 Supersede 关系不改变这两个摘要。

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

## 14. MCP Tool 契约

Project Memory 默认关闭。Server 只有在启动时显式配置 `--enable-project-memory` 后才注册：

```text
ue_memory_search
ue_memory_get
ue_memory_add_rule
ue_memory_record_finding
ue_memory_record_task
ue_memory_mark_superseded
ue_memory_validate
```

固定边界：

- Memory 数据库路径只在 Server 启动时配置，Tool 参数不能传入或覆盖。
- Project Key 从已验证的固定 SQLite 索引读取，Tool 参数不能选择其他项目。
- `ue_memory_add_rule` 固定写入 `projectRule + user-confirmed`，仅应在用户明确确认规则后调用。
- `ue_memory_record_finding` 只接受 `tool-observed` 或 `model-inferred`，不能伪装成用户确认。
- `ue_memory_record_task` 固定写入 `taskRecord + tool-observed`，要求最终结论、完整三类 Artifact 和至少一个稳定 Revision。
- Finding 类型只允许 `projectFact`、`decisionRecord`、`knownIssue` 和 `runtimeEvidence`。
- `ue_memory_search` 默认排除 `stale` 与 `superseded`，需要审计历史时必须显式请求对应状态。
- `ue_memory_validate` 只读取 Server 固定索引中的 Revision，并可能把不匹配记录持久化为 `stale`。
- Memory 写入不修改 Unreal Asset，但属于持久化状态变化，因此 annotations 不是 Read Only，也不是 Destructive。

启用 Memory 后的 Tool 数量：

```text
Offline + Memory   12
Live + Memory      30
Workflow + Memory  33
Combined + Memory  51
```

未启用 Memory 时继续保持原有 5/23/26/44 Tool 契约。

## 15. Task Outcome 契约

`ue_memory_record_task` 用于保存一个已经结束并形成最终结论的工作任务，而不是保存进行中的临时计划。记录固定为：

```text
recordType = taskRecord
sourceKind = tool-observed
status = valid
```

允许的终态：

```text
succeeded
failed
rolledBack
cancelled
```

每条 Task Outcome 必须同时绑定：

```text
patch
backupManifest
validationEvidence
至少一个 revisionStable=true 的 Revision
非空最终结论
```

Artifact 只保存稳定 ID 或工程相对引用，不复制文件内容，也不接受盘符绝对路径、POSIX 绝对路径或 `..` 父目录穿越。Project Scope 自动补全并且必须匹配 Server 固定 Project Key。

Task Outcome 是不可变审计记录。后续出现新的最终结论时创建新记录，并通过 `ue_memory_mark_superseded` 显式取代旧记录；不得原地覆盖历史证据。

## 16. CLI 与审计导出

现有 `ue-agent` CLI 提供：

```text
memory status
memory search
memory get
memory validate
memory export
```

每个子命令都要求固定 `--memory-database` 与 `--project-key`，也可以通过 `UEAK_MEMORY_DATABASE` 和 `UEAK_PROJECT_KEY` 提供默认值。CLI 不接受 SQL、数据库表名或任意查询表达式。 空库或旧 Schema 首次打开时允许执行受控建库、Migration 和证据摘要回填；已经处于当前 Schema 时，`status`、`search`、`get` 和 `export` 不改变业务记录或状态。

`memory validate` 额外读取固定 `--index-database`，只比较 `assets.revision_value` 并更新 Memory Status，不修改索引。`memory export` 生成 Schema 1.0 审计 JSON：

```text
projectKey
memorySchemaVersion
recordCount / statusEventCount
countsByType / countsByStatus
records[]
statusEvents[]
integrity.allRecordDigestsVerified
integrity.snapshotSha256
```

导出前逐条重新验证 `contentSha256` 与 `evidenceSha256`。任何记录被数据库外部篡改时导出失败。`snapshotSha256` 不包含 `generatedAtUtc`，因此相同数据会生成相同摘要。报告不包含 Memory DB、Index DB 或本机工作目录的绝对路径。

为了避免静默截断，默认最大值为 10,000 条 Record 和 100,000 条 Status Event；超过上限时整体失败，不生成部分审计快照。


CLI 的 stdout/stderr 在入口处固定为 UTF-8，确保 Windows 管道、中文 Project Key 和 JSON 调用方不依赖系统代码页。`scripts\TestMemoryCli.cmd` 使用独立子进程验证该协议边界。

## 17. Workflow Evidence Handoff

成功的 `ue_verify_asset` 会返回：

```json
{
  "memoryTaskEvidence": {
    "schemaVersion": "1.0",
    "tool": "ue_memory_record_task",
    "arguments": {
      "task_key": "patch:<planId>",
      "title": "Verified patch <planId>",
      "conclusion": "...",
      "outcome": "succeeded",
      "patch_ref": "patch:<patchDigest>",
      "backup_manifest_ref": "backup-manifest:<manifestId>",
      "validation_evidence_ref": "validation-evidence:<reportId>",
      "revision_set": [
        {
          "assetPath": "/Game/...",
          "revision": "sha256:...",
          "revisionStable": true
        }
      ]
    }
  }
}
```

`arguments` 与 `ue_memory_record_task` 的输入 Schema 完全一致。启用 Workflow 与 Project Memory 时，Agent 应把该对象原样传入目标 Tool，不得修改或自行构造 Patch、Backup Manifest、Validation Evidence 与 Revision 引用。

证据来源：

- `patch_ref` 来自已 Commit Plan 的 Canonical Patch SHA-256。
- `backup_manifest_ref` 来自 Commit 创建并验证存在的 Backup Manifest ID。
- `validation_evidence_ref` 来自独立 UE 重载导出的脱敏 `reportId`。
- `revision_set` 来自独立重载后与 Commit 目标一致的最终 SHA-256 Revision。

证据包不持久化一次性 `applyReceipt`，也不包含 Work Root、Backup Root、Project Path 或报告文件的绝对路径。只有独立验证成功后才生成 `outcome=succeeded` 的证据包；Dry Run、Commit 未验证或验证失败时不会生成成功 Task Evidence。

成功的 rollback Commit 使用相同外层结构，但 `task_key=rollback:<planId>`、`outcome=rolledBack`、Revision Set 绑定恢复后的 pre-Commit Revision，Validation Artifact 同时记录脱敏 rollback Report ID 与独立 verification Report ID。rollback Dry Run、确认失败、恢复失败或 Revision 不匹配时不生成 rolledBack Evidence。

`scripts\TestMcpWorkflow.cmd` 的真实 UE5.6 回归会在同一固定工程会话中记录 succeeded 与 rolledBack Task，随后运行 `ue_memory_validate`：Commit 后 Revision 在 rollback 后必须变为 `stale`，恢复 Revision 对应的 rolledBack Record 必须保持 `valid`。回归同时验证 Audit Snapshot SHA-256、Package SHA-256 恢复和 immutable Index 零修改。

## 18. 后续 Schema v3 方向：分层知识树与 Active Work

本节是已经确定的后续架构方向，不属于 0.6.0 当前实现。当前稳定实现仍是 Schema v2 的平面 Record、Scope、Revision、Artifact 与状态机。

计划中的 Schema v3 增加：

```text
memory_nodes
work_items
work_item_nodes
memory_records.node_id
node_revision / owner / visibility_scope
```

设计原则：

- `memory_nodes` 使用 `parent_id + path + depth` 支持任意深度 Knowledge Tree，不使用固定三层字段。
- 默认从 Project Profile、System、Feature/Entity、Implementation 逐层展开，但可以继续向下细分。
- 现有六类 Record 继续保存知识性质、来源、状态和证据；Knowledge Node 负责知识归属与导航。
- 当前目标、进行中任务、TODO、阻塞、待确认决策与下一步进入独立 Active Work，不与长期知识混存。
- Work Item 完成后只提取稳定结论更新 Knowledge Node；Patch、Validation、Revision 和 Task Evidence 继续自动保存。
- 旧 Record ID、Digest、Revision Set 和 Artifact 必须在迁移中保持不变；尚未归类的记录先绑定 `/project/unclassified`。

完整设计见 [`../docs/MEMORY_ARCHITECTURE.md`](../docs/MEMORY_ARCHITECTURE.md)。

## 19. 渐进式披露与 Token Budget

后续 Memory 查询按五级披露：

```text
Level 0  Path、标题、一句话摘要、状态和子节点数量
Level 1  Project/System/Feature 节点摘要
Level 2  主要类、资产、入口、数据流、依赖与 Known Issue
Level 3  Rule、Decision、Finding、Revision 与详细记录
Level 4  Patch、日志、Blueprint Node、Validation Report 等原始证据
```

服务端必须强制限制返回条数、展开深度和预算，默认过滤 `stale` 与 `superseded`。普通任务的 Memory 上下文目标为约 1,000–2,500 Token，不允许依赖 Skill 中的文字提醒来约束弱 Agent。

## 20. MCP 与 Skill

后续采用 MCP 为主体、Skill 为薄层引导：

- MCP Server 负责 Knowledge Tree、Active Work、渐进式披露、Token Budget、去重、冲突、Revision stale、权限和自动 Evidence。
- 日常只保留一个约 400–800 Token 的 `project-memory` Skill，说明读取顺序与写入原则。
- 不把读取、写入、维护和 TODO 拆成多个长 Skill。
- 审计与 Schema Migration 可以使用按需加载的专用 Skill。

计划中的高层 Tool：

```text
memory_get_context
memory_expand_node
memory_get_evidence
memory_update_knowledge
memory_update_work
```

这些名称是后续契约目标，0.6.0 当前仍使用本文件第 14 节列出的 `ue_memory_*` Tool。

## 21. 多人协作部署方向

多人协作不共用一个可直接控制所有 UE 编辑器的中央 MCP。推荐架构为：

```text
每名开发者：Local Agent → Local MCP → Local UEAgentKit Plugin / UE Editor
整个团队：Local MCP → Shared Knowledge Service
```

本地 MCP 保留 Editor Session、Dirty、PIE、Output Log、Workspace、Policy、Receipt 与未保存内存状态。共享服务保存 Project/Team Knowledge Tree、公共规则与决策、Known Issue、团队 Active Work、负责人、Changelist 和审计引用。

Scope 计划分为：

```text
/project/...   项目共享
/team/...      团队共享
/user/...      个人私有
/session/...   当前本地会话
```

共享层使用 PostgreSQL 或等价服务端数据库与 API；本地 SQLite 继续用于资产索引、缓存、个人和 Session 数据。禁止把一个可写 SQLite 文件放在 NAS 后由多人直接并发访问。共享节点更新使用 `nodeId + expectedRevision` 乐观并发，冲突时返回 `knowledge-conflict`，不允许静默 Last Write Wins。
