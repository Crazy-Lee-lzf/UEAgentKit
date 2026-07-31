# Live Editor Bridge 规范

更新时间：2026-07-28

## 目标

Live Editor Bridge 让固定项目的本地 MCP Server 读取当前 Unreal Editor 内存状态，同时保持离线 SQLite 查询与写入安全模型不变。Bridge 只开放显式注册的高层读取与受限 Editor Action；任何磁盘保存仍必须通过固定项目 Workflow、Policy、Revision、Receipt、备份和独立验证，不提供任意 UObject、Console、Python、Shell、SQL 或文件系统接口。

## 进程结构

```text
MCP Client
→ UE Agent Kit Python MCP Server（stdio）
→ 127.0.0.1 临时 TCP 会话
→ UEAgentKit Editor-only Plugin
→ 注册的读取/受限 Action Capability Handler
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

Tool 参数不能覆盖固定项目或选择其他 Editor 端点。未启用时，离线 5 Tool 与固定项目 Workflow 模式 26 Tool 保持可用；启用后 Live 模式为 23 Tool，Combined 模式为 44 Tool。

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

## 当前 MCP Tool

```text
ue_editor_status
ue_get_selection
ue_get_open_assets
ue_get_dirty_assets
ue_get_current_level
ue_get_pie_state
ue_get_output_log
ue_get_compile_errors
ue_inspect_asset_live
ue_get_blueprint_graph_selection
ue_open_asset
ue_focus_asset
ue_sync_content_browser
ue_focus_actor
ue_compile_blueprint
ue_validate_asset
ue_validate_folder
```

所有 Tool 均返回 `source=live-editor-memory`，不生成磁盘 Revision，也不声称数据来自 SQLite。

Live Read：

- 10 个 Tool，`readOnlyHint=true`、`destructiveHint=false`。
- 状态、选择、打开资产、Dirty 资产、关卡、PIE 和 Blueprint Graph 选择无参数；日志、编译诊断和实时资产检查只接受有界过滤或精确资产路径。

Live Action：

- 7 个 Tool，`readOnlyHint=false`、`destructiveHint=false`。
- 可以改变窗口、选择、资产加载状态或 Blueprint 内存编译状态，但不保存任何 Package。
- 只接受精确 `/Game/...Asset.Asset`、非根 `/Game/...` Package Path 或当前 Editor World 的 `ActorGuid`；PIE/SIE 期间拒绝执行。

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

### ue_get_output_log

Bridge 注册为 `FOutputDevice`，保留最多 4096 条当前会话日志，并在启动时读取可用 Backlog。查询支持 `category`、`minimum_verbosity`、`keyword`、`since_sequence`、`since_utc`、`until_utc`、`pie_session_id` 和 `limit`；单次最多 100 条。每条结果包含单调递增 Sequence、UTC、Category、Verbosity、Thread、PIE Session 和是否来自 Backlog。缓冲淘汰数量通过 `droppedCount` 明确返回。

### ue_get_compile_errors

返回当前 Bridge 会话中捕获到的编译相关 Warning/Error，并补充当前已加载 `/Game/` Blueprint 的 `Status`、生成类和 Graph/变量计数，最多返回 100 项并标记截断。该结果明确返回 `diagnosticSource=captured-output-log` 和 `historyComplete=false`，因为它不是完整持久化 Message Log 历史。可按精确 Blueprint Object Path、Sequence、PIE Session 和数量过滤。

### ue_inspect_asset_live

只接受一个精确 `/Game/...Asset.Asset` Object Path。结果区分 Asset Registry 元数据和 Editor 内存状态，包括是否已加载、Package Dirty、是否在 Asset Editor 中打开、是否被选择，以及 Blueprint 编译状态。Bridge 使用 `StaticFindObject`，不会调用 `LoadObject`，并始终返回 `loadedByBridge=false`。

### ue_get_blueprint_graph_selection

无参数，只检查最近激活且 Editor Name 精确为 `BlueprintEditor` 的普通 Blueprint Editor。成功时返回 Blueprint Object Path、Focused Graph Path/Name/GUID/Class/Schema、可编辑状态，以及最多 100 个当前 Graph 中选中的 Node；每个 Node 仅返回 Path、Name、GUID、Class、Title 和二维位置。无普通 Blueprint Editor 或无 Focused Graph 时返回 `available=false` 与稳定 `reasonCode`。该 Tool 不扫描或强转 Material、Niagara、Control Rig 等其他编辑器，不加载资产，也不提供 Graph 编辑。

### ue_open_asset / ue_focus_asset / ue_sync_content_browser

三者都只接受一个精确 `/Game/...Asset.Asset`。`ue_open_asset` 允许通过 Asset Registry 加载并打开注册编辑器；`ue_focus_asset` 只聚焦已经加载且打开的资产，不隐式加载；`ue_sync_content_browser` 只使用 `FAssetData` 同步 Content Browser，并返回 `loadedByBridge` 证明是否意外加载。三者均返回 Package Dirty 前后状态或加载状态，且不保存。

### ue_focus_actor

只接受当前 Editor World 中的 `ActorGuid`。Bridge 只扫描 `EWorldType::Editor`，要求唯一匹配且 Actor 可选择，然后更新 Editor Selection 并调用视口聚焦。结果返回 ActorGuid、InstanceGuid、Path、Label、Level 和 Dirty Package 计数；PIE/SIE、无匹配、重复 GUID 或不可选择状态均稳定拒绝。

### ue_compile_blueprint

只接受一个精确 Blueprint Object Path。Bridge 可按 Asset Registry 加载目标，在内存中调用 UE5.6 Blueprint 编译 API，返回编译前后 `Status`、Package Dirty、耗时和当前 Bridge 会话捕获的最多 100 条编译 Warning/Error。该 Tool 不保存，也不编译任意命令或依赖列表。

### ue_validate_asset / ue_validate_folder

使用官方 `UEditorValidatorSubsystem::ValidateAssetsWithSettings`。单资产验证只接受精确 Object Path；文件夹验证只接受非根 `/Game/...` Package Path，可显式选择递归。Folder 在执行前统计并排序非 Redirector 资产，超过 `max_assets` 时拒绝；`max_assets` 硬上限 500，`max_issues` 硬上限 200。验证可临时加载资产并卸载本次加载项，不加载 External Objects，不保存 Package，并返回 Valid/Invalid/NotValidated、错误、警告、耗时和 Dirty Package 计数。

## 验证证据契约

`ue_validate_asset`、`ue_validate_folder` 和 `ue_run_automation_test` 的成功结果包含 `validationEvidence`：

- `schemaVersion=1.0`、唯一 `evidenceId` 和 `source=tool-observed`。
- 固定 `projectName`、脱敏 `projectPathHash`、`engineVersion`、`pluginVersion` 和父 `editorSessionId`。
- `startedAtUtc`、`completedAtUtc` 和 `observedAtUtc`。
- Asset/Folder 验证输出按 Asset Path 排序的 `revisionSet`；每项记录磁盘 Package 的验证前/后 SHA-256、Dirty 状态和 `revisionStable`。
- `revisionCoverage=complete` 仅在所有 Package Revision 可用、无 Dirty 内存状态且执行期间磁盘未变化时成立，否则为 `partial`。
- Automation Test 未声明资产输入，因此固定 `revisionCoverage=not-applicable`、空 `revisionSet` 和明确 `revisionRationale`；同时记录隔离子进程类型与 Process ID，不伪造 Asset Revision。

该 Evidence 可直接作为后续 Project Memory 的 `RuntimeEvidence` 来源，但资产发生 Revision 变化后必须重新验证。

## Live Editor Write

`editor.applyAssetPropertyLive` 是首个受控内存写入 Capability，由 Workflow Tool `ue_apply_asset_property_live` 间接调用。该 Tool 虽属于 Workflow 注册表，但执行时必须同时启用 Live Editor、Write Tools 和 Commit Tools；MCP Client 不能直接选择 Bridge Method，也不能绕过固定 Plan、Policy、Revision 或精确确认短语。

首版边界：

- 仅接受已加载且已打开、当前不 Dirty 的非 Blueprint `/Game` 资产。
- 仅接受一个顶层可编辑标量、Enum、String、Name 或 Text 属性。
- 在 Game Thread 中使用 `FScopedTransaction` 与 `UObject::Modify()`，因此修改进入 Editor Undo 栈。
- 调用 `PostEditChangeProperty()` 并标记 Package Dirty，但绝不调用 `SavePackage`。
- 返回 `loadedByBridge=false`、Before/After、Dirty、Transaction 和 Editor Session 证据。
- PIE/SIE、地图、Dirty Package、Blueprint、嵌套路径和不支持类型全部拒绝。

该 Capability 不生成磁盘 Revision，也不修改 SQLite/Revision Export。用户检查后可以在编辑器中 Undo，或者通过现有 `ue_save_authorized_asset` Preview/Commit 流程显式保存。

## 状态与 Revision 语义

Editor Memory、磁盘、Revision Export 和 SQLite 是四个不同事实源：

```text
Editor Memory     当前选择、打开资产、Dirty UObject/Package、PIE；没有加密 Revision
Disk Package      当前已保存 .uasset/.umap 的 SHA-256 Revision
Revision Export   当前 MCP 会话冻结的 Canonical Revision Snapshot
Immutable Index   当前 MCP 会话冻结的 SQLite Snapshot
```

规则：

- Dirty UObject 不生成虚假的磁盘 SHA-256 Revision。
- Live Tool 结果不清除或覆盖 SQLite/Revision Export 的 stale 状态。
- 写入 Plan 仍必须通过现有三源磁盘新鲜度门禁。
- Live Read 只观察已有状态。普通 Live Action 可以执行已注册的资产导航、Blueprint 编译和官方 Data Validation。`ue_apply_asset_property_live` 是独立的 Plan/Policy/Revision 约束内存写入，只标记 Dirty 并记录 Undo；所有 Live 路径都不能自动保存、运行 Console/Python/Shell、接受任意 UObject Method 或改变 SQLite/Revision Export。
- `ue_refresh_asset_index` 属于固定项目工作流而非 Live 只读 Tool；启用 Live Bridge 时，它会先通过 `ue_inspect_asset_live` 拒绝 Dirty 目标，再构建配对 Snapshot Generation。

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
live-editor-invalid-parameters
live-editor-protocol-error
live-editor-pie-active
live-editor-asset-not-found
live-editor-asset-load-failed
live-editor-asset-editor-unavailable
live-editor-asset-not-open
live-editor-world-unavailable
live-editor-actor-not-found
live-editor-actor-guid-ambiguous
live-editor-actor-not-selectable
live-editor-blueprint-required
live-editor-data-validation-unavailable
live-editor-folder-empty
live-editor-asset-limit-exceeded
live-editor-write-asset-not-loaded
live-editor-write-asset-not-open
live-editor-write-blueprint-unsupported
live-editor-write-package-invalid
live-editor-write-package-dirty
live-editor-write-property-not-found
live-editor-write-property-not-editable
live-editor-write-property-type-unsupported
live-editor-write-value-invalid
live-editor-write-apply-failed
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
4. 通过真实 MCP `stdio` Client 发现并调用 15 个 Tool；自管理无界面 Editor 验证 Graph Tool 的安全降级，真实选中 Node 的正向结果由 UE5.6 API 编译和 Schema/单元回归覆盖。
5. 验证 Token、端口、Descriptor 和固定本机路径不进入 MCP 响应。
6. 验证临时 immutable SQLite 哈希和目录文件集合不变。
7. 仅关闭脚本自己创建的 Editor，并清理对应 Descriptor。
