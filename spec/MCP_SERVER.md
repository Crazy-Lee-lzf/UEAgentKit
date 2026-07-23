# UE Agent Kit MCP Server

UE Agent Kit 0.5.x 通过本地 MCP `stdio` 提供稳定的高层查询和受控资产工作流。MCP 层不会开放任意 SQL、Shell、文件系统路径、Commandlet 参数或 UObject 调用。

## 模式

### 默认只读模式

固定一个不可变 SQLite 索引，只注册：

```text
ue_get_capabilities
ue_get_project_status
ue_search
ue_get_asset
ue_find_references
```

### 固定项目完整模式

使用 `-EnableWriteTools` 后，Server 启动时还必须固定：

```text
EngineRoot
ProjectPath
Policy
RevisionExport
WorkRoot
BackupRoot
```

并额外注册：

```text
ue_plan_patch
ue_dry_run_patch
ue_apply_patch
ue_verify_asset
ue_rollback_patch
```

只有再使用 `-EnableCommitTools`，且固定 Policy 的 `commitEnabled=true`，`ue_apply_patch` 与 rollback Commit 才能写入项目资产。

## 启动配置不是 Tool 参数

Database、Engine、Project、Policy、Revision Export、Work Root、Backup Root 和进程超时只能在 Server 启动时配置。任何 MCP Tool Schema 都不会出现这些字段，因此 Agent 不能在调用中切换工程、Policy、引擎、数据库或输出位置。完整模式还要求 SQLite `projectKey`、Revision Export `projectName` 与 `.uproject` 文件名完全一致。

## 查询 Tool

### `ue_get_capabilities`

返回当前 Server 版本、模式、实际注册的 Tool、可用 Operation、查询上限、响应契约和安全边界。只读模式不会把写入 Operation 标记为可用。

### `ue_get_project_status`

返回 Project Key、固定项目状态、Engine 版本、SQLite Schema、索引时间、Exporter 版本、统计信息、Workflow 模式、索引新鲜度状态和 Live Editor 可用性。

固定项目模式会比较 SQLite Revision、Revision Export Canonical Revision 和磁盘 Package SHA-256，返回 `fresh`、`stale`、`partial` 或 `unavailable`。默认只读模式没有固定 Project 与 Revision Export，必须明确返回 `state=unknown`，不能把未知状态报告为 fresh。Live Editor Bridge 未启用时返回 `state=unavailable`。详细契约见 `spec/INDEX_FRESHNESS.md`。

### `ue_search`

搜索 Asset 或 Symbol。

- Asset 支持 `asset_class` 和 `path_prefix`。
- Symbol 支持 `kind`、精确 `asset_path` 和 `path_prefix`。
- 保留 `offset` 兼容旧客户端；新客户端优先使用 `continuation_token`。
- Server 内部读取 `limit + 1` 条记录，准确判断是否仍有下一页。
- `max_output_tokens` 控制返回体的近似 Token Budget。

### `ue_get_asset`

按完整 Object Path 读取一个资产，支持以下 section：

```text
identity
summary
metadata
symbols
references
graphs
nodes
```

不传 `sections` 时保持 0.5.0 的完整读取语义。`symbols`、`references`、`graphs` 和 `nodes` 分别具有独立分页状态与 continuation token；后续请求可只携带该 section 返回的 Token。`graph_guid` 和 `node_guid` 可进一步限定 Graph/Node 结果。

### `ue_find_references`

按引用类型、源/目标 Symbol、源/目标资产过滤；至少需要一个条件。

- `direction=outgoing|incoming|both`。
- `depth` 为 1 至 3；大于 1 时必须提供锚点 `asset_path`。
- `project_only=true` 只返回目标资产也存在于当前 SQLite 索引中的边。
- 深层遍历不接受源/目标 Symbol 与目标资产端点组合，避免产生含义不稳定的跨层过滤。

## 索引新鲜度与写入生命周期

固定项目模式只允许对 `fresh` 目标创建 Plan：

```text
SQLite Revision == Revision Export Revision == disk Package SHA-256
```

Commit 成功后，磁盘 Package 已变化，但固定 SQLite 与 Revision Export 不会被原地改写，因此 Server 立即标记：

```text
fixedSnapshotsStale=true
sqliteIndexStale=true
revisionExportStale=true
```

独立 Verify 只确认 Commit Revision，不会清除 stale。只有 rollback 恢复到原 Revision 并重新通过三源比较，或在新会话中安全切换已验证的新快照，才能重新进入 `fresh`。

当前 `/Game/...` 项目 Package 支持 `.uasset` 与 `.umap` 比较；未知 Mount 不做路径猜测，而是返回 `unavailable`。单资产刷新和安全重载设计见 `spec/INDEX_FRESHNESS.md`。

## 分页与输出预算

- continuation token 是分页状态，不是 API Key、登录 Token 或模型 Token。
- Token 为当前 Server 会话生成的不透明随机值，不暴露查询文本和本机路径。
- Token 绑定 Tool、固定 SQLite 快照和原始查询参数，不能跨 Tool、跨索引快照或跨 Server 重启复用。
- `offset` 继续保留兼容性，但 Agent 应优先使用 continuation token。
- 查询响应返回 `hasMore`、兼容字段 `mayHaveMore`、`continuationToken` 和 `source`。
- `outputBudget` 返回 `maxTokens`、`estimatedTokens`、`truncated` 和 `truncationReason`。
- 截断原因包括 `page-limit`、`section-limit`、`token-budget` 和 `single-result-exceeds-token-budget`。

## 错误 Envelope

Tool 失败时统一返回：

```text
code
message
retryable
details
suggestedAction
```

`code` 是供客户端判断的稳定错误码；`retryable` 表示在不改变请求语义的前提下重试是否可能成功；`details` 必须经过路径脱敏；`suggestedAction` 给出下一步操作。保留 `type` 仅用于兼容旧客户端，不应作为协议判断依据。无效、过期、跨 Tool 或跨索引快照的分页 Token 统一返回 `invalid-continuation-token`。

## 写入工作流 Tool

### `ue_plan_patch`

输入资产路径、已注册 Operation、语义 Target 和 JSON Value。Server：

1. 从固定 SQLite 获取 Asset Class 与 SHA-256 Revision。
2. 生成单资产、单 Operation Patch。
3. 使用固定 Policy 与 Revision Export 纯校验。
4. 将 Patch 写入固定 Work Root。
5. 记录 Canonical JSON 摘要。

Plan 只在当前 Server 会话有效。

### `ue_dry_run_patch`

按 `planId` 调用现有 `RunPatch.ps1 -Mode DryRun`，并要求：

```text
saved=false
rolledBack=true
rollbackValueMatch=true
diskUnchanged=true
beforeRevision==afterRevision
```

成功后返回一次性 `dryRunReceipt`。

### `ue_apply_patch`

要求：

- Server 启动时启用 Commit。
- 固定 Policy 允许 Commit。
- Plan 与 Policy 摘要未变化。
- 新鲜、未使用且属于该 Plan 的 Dry Run Receipt。
- `confirmation` 精确等于 `COMMIT <planId>`。

成功后生成外部备份、Backup Manifest 和 `applyReceipt`。同一 Dry Run Receipt 不能重复 Commit。

### `ue_verify_asset`

使用独立 Unreal Editor 进程重新导出目标资产，并核对 Object Path 与 Commit 后 SHA-256 Revision。该 Tool 不修改项目资产，但会在固定 Work Root 写验证报告，因此 MCP Annotation 不是纯 read-only。

### `ue_rollback_patch`

分两阶段：

1. 默认 `mode=DryRun`，验证 Manifest、Policy、当前 Revision 和备份完整性，返回一次性 `rollbackDryRunReceipt`。
2. `mode=Commit` 要求 Receipt 和精确 `ROLLBACK <applyReceipt>`，执行原子恢复并由独立 UE 进程验证恢复后的 Revision。

## 会话锁与失效

- Policy SHA-256 在 Server 启动时锁定。
- 每个 Plan 的 Canonical JSON 摘要在创建时锁定。
- Policy 或 Plan 文件被外部修改后，后续 Dry Run/Commit 被拒绝。
- Plan、Dry Run Receipt、Apply Receipt 和 rollback Receipt 仅保存在内存中。
- Server 重启后全部失效，不支持跨会话恢复执行上下文。
- Commit 后固定 SQLite 与 Revision Export 不会自动改写；若保留修改并继续规划该资产，必须停止 Server，重新导出、重建索引并启动新会话。

## 文件和进程边界

- Work Root 必须是工具 `Output` 的子目录。
- Backup Root 必须是工具 `Backups` 的子目录。
- 解析后的真实路径会再次检查，防止 Junction/符号链接逃逸。
- 所有子进程 stdin 固定为 `DEVNULL`，不得占用 MCP 协议管道。
- 子进程 stdout/stderr 有固定截断上限；对 Agent 返回的错误和报告会脱敏本机配置路径。
- 当前仍只支持单文件 Package；发现 `.uexp`、`.ubulk` 等 Sidecar 时由既有执行器拒绝。

## SQLite 边界

- 使用 `mode=ro&immutable=1`。
- 启动和每次查询前拒绝活动 `-wal`、`-shm`、`-journal`。
- 不运行 Migration。
- 查询后索引目录文件集合与 SHA-256 必须不变。
- 重建索引前必须停止 MCP Server，完成构建并关闭所有写入连接后再启动。

## MCP SDK 与传输

```text
mcp>=1.27,<2
transport=stdio
```

当前不监听 TCP，不提供 HTTP/SSE。

## 集成测试

只读协议测试：

```bat
scripts\TestMcpStdio.cmd
```

完整 UE5.6 工作流测试：

```bat
scripts\TestMcpWorkflow.cmd ^
  -EngineRoot "E:\Path\To\UE_5.6" ^
  -ProjectPath "E:\Path\To\Project.uproject"
```

完整测试使用隔离 Scalar Fixture，最终必须恢复测试前 `.uasset` SHA-256。
