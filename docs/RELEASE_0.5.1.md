# UE Agent Kit 0.5.1 发布说明

UE Agent Kit 0.5.1 面向 Unreal Engine 5.6，是 0.5.0 固定项目 MCP 工作流的协议与日常可用性补全版本。本版本不扩大任意执行面，而是在现有 Policy、Revision、Dry Run、Receipt、Backup、Verify 和 rollback 安全边界内，改进查询、状态、写入入口、错误诊断和 Client 兼容性。

## 主要变化

### 查询与协议

- 新增 `ue_get_capabilities` 和 `ue_get_project_status`。
- `ue_search` 支持 Asset Class、Symbol Kind、Path Prefix、稳定 `limit + 1` 分页和不透明 continuation token。
- `ue_get_asset` 支持 `identity`、`summary`、`metadata`、`symbols`、`references`、`graphs`、`nodes` 分 section 读取，并为列表 section 提供独立 continuation。
- `ue_find_references` 支持 `incoming`、`outgoing`、`both`，以及深度 1–3 和 `project_only`。
- 查询响应统一提供 Token Budget、估算 Token 数、截断原因和下一页信息。
- 错误统一为 `code`、`message`、`retryable`、`details`、`suggestedAction` Envelope，并保留兼容字段。

### Revision 新鲜度

写入前比较三种 Revision：

```text
immutable SQLite
Revision Export Canonical
当前磁盘 Package SHA-256
```

状态包括 `fresh`、`stale`、`partial`、`unavailable` 和只读模式下的 `unknown`。Plan、Dry Run 和 Commit 前会重新检查目标资产。Commit 后固定 SQLite 与 Revision Export 标记 stale；独立 Verify 不会误清除；精确 rollback 恢复原 Revision 并重新比较后才恢复 fresh。

### 高层安全写入 Tool

完整模式新增六个高层 Tool：

```text
ue_set_blueprint_default
ue_set_component_property
ue_set_pin_default
ue_set_asset_property
ue_set_material_parameter
ue_set_datatable_cell
```

这些 Tool 默认 `mode=Plan`，也可使用 `mode=DryRun` 自动执行 Plan → Unreal Dry Run。高层 Tool 不提供 Commit 模式；保存仍必须调用 `ue_apply_patch`，携带一次性 `dryRunReceipt` 和精确 `COMMIT <planId>` 确认。

### 诊断

新增并区分：

```text
policy-rejected
revision-conflict
dirty-package
workflow-timeout
ue-process-crashed
workflow-report-missing
workflow-report-invalid
```

响应可包含脱敏的 `diagnosticId`、`reportId`、`stage`、`exitCode`、`stdoutTail` 和 `stderrTail`，不会暴露本机数据库、工程、Package、报告或备份路径。

## MCP Client 兼容矩阵

新增：

```bat
scripts\TestMcpClients.cmd
```

矩阵运行两个独立真实 `stdio` 会话：

- 官方 Python MCP `ClientSession`。
- 不依赖 SDK 的原始 newline-delimited JSON-RPC Client。

两类 Client 必须协商相同 Protocol Version、发现相同 Tool Schema 和 annotations，并正确接收 `structuredContent`。每个 Tool 同时提供单条可解析 JSON Text Content 回退，错误使用相同 Envelope。

Claude Code 契约覆盖本地 `stdio`、Tool Description、Object 型 JSON Schema、annotations，以及固定 Database、Engine、Project、Policy 和文件路径不进入 Tool 参数。ChatGPT 相关结论仅限标准 MCP `tools/list`、`tools/call`、`structuredContent` 和文本回退协议兼容；本地自动化没有测试托管 ChatGPT UI、账号设置或远程 Transport。

## Tool 数量

默认只读模式：5 个 Tool。

固定项目完整模式：16 个 Tool，包括 5 个只读 Tool、6 个高层安全入口和 5 个底层工作流 Tool。

## 发布验证

```text
Python unittest                         134/134 passed
Read-only MCP stdio smoke               passed
Official SDK + raw JSON-RPC matrix      passed
MCP protocol version                    2025-11-25
UE5.6 Direct plugin build               passed
UE5.6 UAT Win64 package build           passed
Real high-level MCP workflow            passed
Commit -> Verify -> Rollback             passed
Final fixture SHA-256 restoration        passed
Immutable SQLite unchanged               passed
```

本批没有修改 11 类标量 Operation 的 UE 执行语义，因此没有重复运行完整 11 Dry Run + 11 Commit + 9 failure 标量矩阵；真实 MCP Commit、独立 Verify 和 rollback 闭环已重新运行。

## 从 0.5.0 升级

1. 停止正在运行的 UE Agent Kit MCP Server 和 Unreal Editor。
2. 替换项目或 Engine 中的 `UEAgentKit` 插件目录；不要在旧目录上混合覆盖 `Binaries`。
3. 重新安装 Python 可选 MCP 依赖时运行 `scripts\setup_python.cmd -WithMcp`。
4. 重新导出 Revision、重建 SQLite，并在启动完整模式前确认 `ue_get_project_status` 为 `fresh`。
5. Client 可继续使用旧 `offset` 参数；新集成建议使用 continuation token 和 `ue_get_capabilities`。
6. 现有底层五个写入工作流 Tool 保持兼容；常见修改建议迁移到六个 `ue_set_*` 高层入口。

Release ZIP 仅包含可安装的 UE5.6/Win64 插件、LICENSE 和简版发布说明。Python CLI、MCP Server、测试、脚本和完整规范请使用 GitHub Source archive 或完整仓库。

Release 资产 `UEAgentKit-0.5.1-UE5.6-Win64.zip` 的 SHA-256：

```text
27f7dd1b6b8375c6dfa5d9c0c6ff27ed1ec4db680bab7a5ee64852925f8f976a
```

## 仍不包含

- 任意 SQL、Shell、文件覆盖、Console、Python 或 UObject 调用。
- MCP 跨 Server 重启持久化 Plan、Receipt 或 continuation token。
- 多资产事务或任意多 Operation 事务。
- Live Editor 内存状态读取；该能力属于 0.5.2。
- 托管 ChatGPT UI 端到端自动化验证。
