# UE Agent Kit 0.5.0 发布说明

UE Agent Kit 0.5.0 面向 Unreal Engine 5.6，将 0.4.4 已验证的 Patch、Policy、Revision、Dry Run、Backup Manifest、独立验证和 rollback 能力封装为本地 MCP 工作流。

## MCP Tool

默认只读模式：

```text
ue_search
ue_get_asset
ue_find_references
```

固定项目完整模式额外提供：

```text
ue_plan_patch
ue_dry_run_patch
ue_apply_patch
ue_verify_asset
ue_rollback_patch
```

## 安全模型

- 仅使用本地 `stdio`，不监听 TCP 端口。
- Database、Engine、Project、Policy、Revision Export、工作目录和备份目录在 Server 启动时固定。
- SQLite 使用不可变只读快照，并拒绝活动 `-wal`、`-shm` 和 `-journal`。
- Tool 不接收任意文件路径、Shell、SQL、Commandlet 或 UObject 调用参数。
- Patch 仍限制为单资产、单 Operation，并由 Policy 精确授权。
- Plan 与 Policy 使用会话级摘要锁，外部修改后立即拒绝。
- Commit 必须先成功 Dry Run，并提交一次性 Receipt 与精确 `COMMIT <planId>`。
- rollback Commit 必须先成功 rollback Dry Run，并提交一次性 Receipt 与精确 `ROLLBACK <applyReceipt>`。
- Commit 后通过独立 UE 进程重新加载并核对 SHA-256 Revision。
- MCP Server 重启后所有 Plan 与 Receipt 失效。
- 保留 Commit 后的新修改时，继续规划该资产前必须停止 Server，并重新导出 Revision、重建 SQLite 索引。

## 已验证的真实闭环

- 八个 Tool 通过真实 MCP Client 发现与调用。
- Dry Run 后 `.uasset` SHA-256 不变。
- 错误 Commit / rollback 确认短语被拒绝。
- Dry Run Receipt 不可重复使用。
- Commit 后独立 UE 重载 Revision 一致。
- rollback Dry Run 不写磁盘。
- rollback Commit 后独立验证通过，最终 `.uasset` SHA-256 与测试前完全相同。
- 不可变 SQLite 索引目录文件集合和哈希保持不变。

## 发布验证

```text
Python tests                       118/118 passed
Read-only MCP stdio smoke          passed
Full eight-tool MCP workflow       passed
Scalar Dry Run                     11/11 passed
Scalar Commit                      11/11 passed
Expected zero-write failures        9/9 passed
Final scalar fixture reset         passed
UE5.6 Direct plugin build          passed
UE5.6 UAT Win64 package build      passed
Release ZIP validation             passed
```

Release 资产 `UEAgentKit-0.5.0-UE5.6-Win64.zip` 的 SHA-256：

```text
a1516bcc0e63d1e00c7628ad5a9c2fcc69fdf8c9452cbff401f00bc990eab4e2
```

该 ZIP 仅包含可安装的 UE5.6/Win64 插件。Python CLI、MCP Server、脚本、规范和测试请使用 GitHub Release 自动生成的 Source archive 或完整仓库。

## 启动方式

默认只读模式：

```bat
scripts\RunMcp.cmd -Database "<TOOL_ROOT>\.data\ue_agent_kit.sqlite3"
```

完整工作流模式：

```bat
scripts\RunMcp.cmd ^
  -Database "<TOOL_ROOT>\.data\ue_agent_kit.sqlite3" ^
  -EnableWriteTools ^
  -EnableCommitTools ^
  -EngineRoot "E:\Path\To\UE_5.6" ^
  -ProjectPath "E:\Path\To\Project.uproject" ^
  -Policy "E:\Path\To\write-policy.json" ^
  -RevisionExport "E:\Path\To\RevisionExport"
```

不传 `-EnableCommitTools` 时，可使用 Plan 和 Dry Run，但不能保存或恢复资产。

## 仍不包含

- 任意 SQL、Shell、文件覆盖和 UObject 调用。
- MCP 跨 Server 重启持久化 Plan/Receipt。
- 多资产事务或单资产多 Operation 原子事务。
- Blueprint 图节点和专用图资产编辑。
- Array、Set、Map、任意 Struct 和通用对象引用写入。
