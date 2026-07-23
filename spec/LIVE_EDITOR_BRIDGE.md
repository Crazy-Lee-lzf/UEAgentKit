# Live Editor Bridge 规范

更新时间：2026-07-24

## 目标

Live Editor Bridge 让固定项目的本地 MCP Server 读取当前 Unreal Editor 内存状态，同时保持离线 SQLite 查询与写入安全模型不变。Bridge 只开放显式注册的高层只读能力，不提供任意 UObject、Console、Python、Shell、SQL 或文件系统接口。

## 进程结构

```text
MCP Client
→ UE Agent Kit Python MCP Server（stdio）
→ 127.0.0.1 临时 TCP 会话
→ UEAgentKit Editor-only Plugin
→ 注册的只读 Capability Handler
```

MCP Client 不接触 TCP 地址、端口、认证令牌或描述符路径。它只能调用 MCP Server 已注册的 Tool。

## 启用方式

MCP Server 只有同时提供以下固定启动参数时才注册 Live Editor Tool：

```text
--enable-live-editor
--project <fixed .uproject>
```

PowerShell 入口对应：

```text
-EnableLiveEditor
-ProjectPath <fixed .uproject>
```

Tool 参数不能覆盖固定项目或选择其他 Editor 端点。未启用时，原有 5/16 Tool 模式和响应保持兼容。

## 端点描述符

交互式 Editor 启动后，在固定项目内写入：

```text
<Project>/Saved/UEAgentKit/EditorBridge.json
```

字段：

```text
schemaVersion
address = 127.0.0.1
port
authToken
projectName
projectPathHash
pluginVersion
processId
sessionId
startedUtc
capabilities[]
```

约束：

- Listener 只绑定 `127.0.0.1` 和操作系统分配的临时端口。
- `authToken` 每次 Editor 会话随机生成，不返回 MCP Client。
- `projectPathHash` 使用规范化绝对 `.uproject` 路径的 SHA-1 摘要，仅用于固定项目身份匹配，不作为密码学认证；真正的会话认证由随机令牌完成。
- Plugin 与 MCP Server 版本必须完全一致。
- Descriptor 使用临时文件后原子替换。正常关闭时仅在令牌仍匹配的情况下删除。
- 异常退出可能留下 stale descriptor；客户端仍会因连接失败、PID/会话变化或握手失败而拒绝使用。测试脚本只会清理由其固定测试项目产生、且对应进程已不存在的 stale descriptor。

## 握手与请求

每次读取使用独立短连接：

```text
连接 localhost
→ hello(authToken, serverVersion, projectPathHash)
→ Plugin 返回 pluginVersion/project/session/capabilities
→ 单次注册 Capability 请求
→ 单次结果或错误
→ Plugin 主动关闭连接
```

协议使用 UTF-8、newline-delimited、紧凑单行 JSON。请求和响应 `schemaVersion` 当前为 `1.0`。

限制：

```text
最大并发 Client：8
最大请求：64 KiB
最大响应：1 MiB（Python Client）
默认超时：2 秒
可配置超时：0.1–30 秒
```

## 首批 MCP Tool

```text
ue_editor_status
ue_get_selection
ue_get_open_assets
ue_get_dirty_assets
ue_get_current_level
ue_get_pie_state
```

所有 Tool：

- `readOnlyHint=true`。
- `destructiveHint=false`。
- 无参数。
- 返回 `source=live-editor-memory`。
- 不生成磁盘 Revision，也不声称数据来自 SQLite。

### ue_editor_status

返回 Bridge 可用性、Plugin/Engine 版本、Project、PID、Session、Capability、PIE、当前 Level 和 Dirty Package 计数。Editor 未运行时仍返回成功的状态 Envelope，其中 `state=unavailable`，便于 Agent 稳定降级到离线索引。

### ue_get_selection

返回当前选择中的 Actor、Component、Asset 和普通 Object，去重后最多 200 项。对象信息限于名称、路径、Class、Package、Dirty 状态，以及 Actor Label/Level 或 Component Owner 等有界字段。

### ue_get_open_assets

通过 `UAssetEditorSubsystem` 返回当前打开的资产，最多 200 项。

### ue_get_dirty_assets

返回当前 Editor 内存中 Dirty 的 `/Game/` Package 及 Asset Registry 可见资产路径，最多 200 项。Dirty 内存状态不等于磁盘 Revision。

### ue_get_current_level

返回 Editor World、Persistent/Current Level、World Type、World Partition 和 Package Dirty 状态。

### ue_get_pie_state

返回：

```text
stopped
playing
simulating
```

当存在 Play World 时同时返回 World Path、World Type 和 Net Mode。

## 状态与 Revision 语义

Live Editor、磁盘和索引是三个不同事实源：

```text
Editor Memory     当前选择、打开资产、Dirty UObject/Package、PIE
Disk Package      已保存 .uasset/.umap Revision
Immutable Index   上次导出并构建的 SQLite Snapshot
```

规则：

- Dirty UObject 不生成虚假的磁盘 SHA-256 Revision。
- Live Tool 结果不清除或覆盖 SQLite/Revision Export 的 stale 状态。
- 写入 Plan 仍必须通过现有三源磁盘新鲜度门禁。
- 本批 Live Tool 不能保存、编译、运行 Console 或执行 UObject Method。

## 稳定错误码

```text
live-editor-unavailable
live-editor-timeout
live-editor-connection-closed
live-editor-version-mismatch
live-editor-project-mismatch
live-editor-authentication-failed
live-editor-authentication-required
live-editor-capability-unavailable
live-editor-protocol-error
```

错误响应沿用 MCP `code/message/retryable/details/suggestedAction` Envelope，不返回本机 Descriptor、Project 或 Token 路径。

## 威胁模型

Bridge 防止远程访问、错误项目连接、版本错配和 MCP Client 自选端点；它不试图防御已经控制同一 Windows 用户账户、可读取项目 `Saved` 目录并注入 Editor 进程的本地恶意程序。正式项目仍应依赖操作系统账户隔离、目录 ACL 和最小权限。

## 验证

```bat
scripts\TestMcpLiveEditor.cmd ^
  -EngineRoot "<UE_5.6>" ^
  -ProjectPath "<TEST_PROJECT>.uproject"
```

脚本会：

1. 拒绝干扰已有 Editor，或显式使用 `-UseExistingEditor`。
2. 启动测试项目的独立 Unreal Editor。
3. 等待匹配 PID 的 Descriptor。
4. 通过真实 MCP `stdio` Client 发现并调用 11 个 Tool。
5. 验证 Token、端口、Descriptor 和固定本机路径不进入 MCP 响应。
6. 验证临时 immutable SQLite 哈希和目录文件集合不变。
7. 仅关闭脚本自己创建的 Editor，并清理对应 Descriptor。
