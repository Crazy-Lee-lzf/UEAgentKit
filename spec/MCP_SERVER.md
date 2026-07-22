# UE Agent Kit MCP Server

UE Agent Kit 的 MCP 接入层只暴露稳定的高层能力，不开放任意 SQL、Shell、文件读写或 UObject 调用。

## 传输与依赖

首版仅使用本地 `stdio`：

```bat
scripts\setup_python.cmd -WithMcp
scripts\RunMcp.cmd -Database "<TOOL_ROOT>\.data\ue_agent_kit.sqlite3"
scripts\TestMcpStdio.cmd
```

MCP Python SDK 固定在稳定 v1 范围：

```text
mcp>=1.27,<2
```

服务器启动时固定数据库路径。MCP Tool 参数不能选择其他数据库，也不能修改或迁移数据库；每次查询均通过 SQLite `mode=ro&immutable=1` 重新打开。索引必须是无活动 Writer 的静态快照；存在 `-wal`、`-shm` 或 `-journal` 时拒绝查询。

## 只读 Tool

### `ue_search`

统一搜索资产或 Symbol：

```text
scope=assets  → query + asset_class
scope=symbols → query + kind + asset_path + include_details
```

默认 `limit=20`，MCP 层最大 `100`。

### `ue_get_asset`

按完整 Object Path 获取一个资产：

```text
/Game/Characters/BP_Player.BP_Player
```

返回资产元数据、受限数量的 Symbol、Reference、Graph 和 Node。四类明细都有独立硬上限，默认不展开 `details_json`。

### `ue_find_references`

按引用类型、源资产、源/目标 Symbol 或目标资产查找依赖边。必须至少提供一个过滤条件，避免无意导出整个引用表。

## 响应契约

成功响应至少包含：

```json
{
  "schemaVersion": "1.0",
  "tool": "ue_search",
  "ok": true,
  "projectKey": "ProjectName",
  "databaseSchemaVersion": 1,
  "readOnly": true
}
```

错误通过结构化响应返回，不把数据库路径暴露为 Tool 参数：

```json
{
  "schemaVersion": "1.0",
  "tool": "ue_search",
  "ok": false,
  "readOnly": true,
  "error": {
    "code": "invalid-arguments",
    "type": "ValueError",
    "message": "..."
  }
}
```

## 安全边界

- 仅 `stdio`，当前不监听 TCP 端口。
- 数据库固定于服务器启动参数，Tool 调用不能覆盖。
- SQLite 使用 `mode=ro&immutable=1`，不运行 Migration，也不创建 WAL/SHM Sidecar。
- 验证数据库 Schema 与 FTS5 表后才启动服务器；查询前后都检查不存在活动 SQLite Sidecar。
- 搜索、Symbol、Reference 和 Node 数量均有硬上限。
- `ue_find_references` 禁止无过滤条件的全表读取。
- 当前三个 Tool 均不会启动 Unreal Editor、加载 UObject 或写入资产。
- 重建索引前必须停止 MCP Server；完成索引并关闭所有 Writer 后再启动，以获得新的不可变快照。

后续写入 Tool 必须继续复用现有 Patch、Policy、Revision、Dry Run、备份、独立验证和 rollback 层，不会直接开放底层 Commandlet 参数。
