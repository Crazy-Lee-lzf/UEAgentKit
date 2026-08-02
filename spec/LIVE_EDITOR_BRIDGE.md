# Live Editor Bridge 规范

更新时间：2026-08-01

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
ue_run_automation_test
ue_get_editor_context
ue_start_batch_task
ue_get_batch_task
ue_cancel_batch_task
```

所有 Tool 均返回 `source=live-editor-memory`，不生成磁盘 Revision，也不声称数据来自 SQLite。

Live Read：

- 10 个 Tool，`readOnlyHint=true`、`destructiveHint=false`。
- 状态、选择、打开资产、Dirty 资产、关卡、PIE 和 Blueprint Graph 选择无参数；日志、编译诊断和实时资产检查只接受有界过滤或精确资产路径。

Live Action：

- 8 个 Tool，`readOnlyHint=false`、`destructiveHint=false`。
- 可以改变窗口、选择、资产加载状态或 Blueprint 内存编译状态，但不保存任何 Package。
- 只接受精确 `/Game/...Asset.Asset`、非根 `/Game/...` Package Path 或当前 Editor World 的 `ActorGuid`；PIE/SIE 期间拒绝执行。

Realtime Foundation：

- `ue_get_editor_context` 为只读聚合 Tool；`ue_start_batch_task` 和 `ue_cancel_batch_task` 为非破坏性 Action；`ue_get_batch_task` 为只读状态查询。
- Realtime Tool 不接受本机路径、端口、Token、数据库或任意 UObject Method。
- Context 和 Batch 只读取当前 Editor 内存与已加载 World，不加载未加载资产，不保存 Package，不改变选择。

### ue_get_editor_context

一次请求返回当前工作上下文的有界快照：

```text
editor
world
selection
openAssets
dirtyPackages
blueprintGraphSelection
compileErrors
outputLogCursor
durationMs
stageDurationsMs
nextActions
```

每个集合都有硬上限和 `truncated` 标记。若 Editor 不可用，返回稳定的 `state=unavailable`，而不是回退到 SQLite 或磁盘扫描。

### ue_start_batch_task / ue_get_batch_task / ue_cancel_batch_task

首个固定 Operation 为 `scanCurrentWorld`。它只扫描当前已加载 Editor World 中的 Level、Actor 和 Component，不触发 World Partition Cell 或资产加载。

执行模型：

- 同一 Editor Session 最多一个运行中的 Batch Task。
- Level 使用弱引用保存；Actor 不跨帧保存裸指针，每次从当前 Level Slot 获取并立即验证。
- 每 Tick 同时受 `MaxActorSlotsPerTick=256` 和约 `2 ms` 时间预算约束。
- 任务绑定 `editorSessionId` 与 World Identity；World 切换、PIE/SIE、超时或显式取消均进入明确终态。
- 终态包括 `completed/cancelled/failed/timed-out/invalidated`。

`ue_get_batch_task` 默认只返回进度和聚合摘要。Actor/Component 详情必须显式请求：

```text
include_details = true
detail_offset = 0..
detail_limit = 1..5
```

单页最多 5 个 Actor；响应返回 `returnedCount/totalAvailable/hasMore/nextOffset`。该分页契约用于确保最坏情况下的结果仍低于 Python Bridge 的 1 MiB 单响应上限。

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

当前边界（`main` 开发快照能力，不是 0.6.0 正式发布能力）：

- 仅接受已加载且已打开、当前不 Dirty 的非 Blueprint、非地图 `/Game` 单文件资产。
- 当前注册表只开放十二个显式 Operation：`setAssetProperty`、`setAssetReferenceProperty`、`setAssetStructuredProperty`、四个 Material Instance 参数 Operation，以及五个 DataTable Operation。Bridge 的规范请求为 `operation + assetPath + target + value`；`target` 是最多 32 个字段的 JSON Object，由 Operation Descriptor 声明必需字段并由具体资产域执行器继续验证。旧的顶层 `propertyPath`/`parameterName`/`rowName`/`newRowName`/`fieldName` 仅作为兼容输入合并进 `target`，新增 Operation 不再要求修改中央函数签名。
- 引用类型只支持 `Object`、`Class`、`SoftObject`、`SoftClass`（按 SoftClass → SoftObject → Class → Object 顺序识别），拒绝 Weak/Lazy Object、Interface、Delegate、固定数组和容器。
- 引用值沿用 `setAssetReferenceProperty` 契约：设置用 `{"referenceType": ..., "path": "/Game/...Object"}`，清空用 JSON `null`；清空时仍按实际 Property 报告 `referenceType`，`referencePath`/`beforeValue`/`afterValue` 用 JSON `null` 表示空引用。
- 结构化写入强制复用 `UEAgentKit::StructuredPropertyJson`（`GetKind`/`BuildSchema`/`ExportValue`/`ImportValue`/`CanonicalJson`/`JsonEqual`/`BuildDiff`），不引入第二套序列化逻辑；值在 Bridge 内按导出的稳定 Schema 导入并回读验证。固定数组（ArrayDim != 1）与非结构化属性在 Plan/Bridge 两个阶段都被拒绝；值类型不匹配、Struct 字段不完整、Set/Map 条目未按 Canonical JSON 唯一有序都会在 Plan 阶段被 Python 验证器拒绝。
- Bridge 独立校验：引用路径必须是合法 `/Game/...Object` Object Path（禁止 Subobject Path），`referenceType` 必须与实际 Property 类型完全一致，目标引用必须存在，Object/Class 必须满足 Property 的 Constraint Class；SoftObject/SoftClass 也会解析并验证实际 Class（第一版允许为了验证而加载目标）。
- 在 Game Thread 中使用 `FScopedTransaction` 与 `UObject::Modify()`，因此成功修改进入 Editor Undo 栈；修改前创建 Property Value Snapshot，任何失败都会恢复原值、恢复原 Dirty 状态并 `Transaction.Cancel()`，不留下部分修改、Dirty 状态或有效 Undo Transaction；No-op 不制造 Undo 或 Dirty。
- 当前十二个 Operation 共享 `UEAgentKitLiveWrite` Transaction/Evidence 基础层。中央 `EditorBridgeWriteHandlers.cpp` 只保留公共 PIE、资产、Package、Dirty 门禁、注册表分派和 Undo/Discard；`LiveWritePropertyOperations.cpp`、`LiveWriteMaterialOperations.cpp`、`LiveWriteDataTableOperations.cpp` 分别拥有资产域逻辑，`LiveWriteOperationRegistry` 负责名称、Target 字段、资产要求和执行器注册。增加同域 Operation 只需扩展对应域模块和 Python OperationSpec；增加新资产域只需新增一个域注册入口，不能绕过 Plan、Policy、Revision 或公共门禁。
- 材质参数写入强制复用离线 Patch Schema：Operation 名、`{"parameterName": ...}` Target 与值格式（Scalar=有限 JSON number、Vector=`{r,g,b,a}`、Texture=Object Path 字符串、StaticSwitch=JSON boolean）与 `AssetPatchCommandlet` 完全一致，不引入第二套 JSON 格式；只接受已加载的 `MaterialInstanceConstant`（拒绝 MID/非 MI），参数必须存在且是 Global Association，Texture 目标必须是可加载的 `/Game` Texture 资产；Policy 侧 `allowedMaterialParameters`（`class#Type#ParameterName`）授权由 Plan 阶段强制执行。材质参数的读回校验（值、Override、Expression GUID）与离线命令let一致，失败即回滚。
- DataTable 写入强制复用离线 Row Schema：行结构必须是可用的 `FTableRowBase` 派生结构，字段只支持 bool/Enum/数值/String/Name/Text 标量（与离线 `SetPropertyFromJson` 一致），`allowedDataTableFields`（`class#RowStruct#FieldName`）授权由 Plan 阶段强制执行；`setDataTableCell`/`setDataTableRowFields` 修改现有行的字段，其中 RowFields 请求是 1–32 个字段的原子子集，未请求字段保持原值，读回只校验请求字段并返回完整行；字段修改后调用 `HandleDataTableChanged(RowName)` 刷新 DataTable 观察者。`addDataTableRow` 创建行（目标行不得存在），`removeDataTableRow`/`renameDataTableRow` 要求 `value=true` 确认、目标行存在、`renameDataTableRow` 要求新行名不同且不存在，并再次执行 Searchable Name 引用影响门禁（有引用即拒绝）。快照为整表行深拷贝，失败时整表恢复；No-op 检测基于整表规范 JSON 相等（写同值不产生 Undo 或 Dirty）。
- 调用 `PostEditChangeProperty()` 并标记 Package Dirty，但绝不调用 `SavePackage`。
- 返回 `operation`、`valueKind`、`loadedByBridge=false`、Before/After、Dirty、Transaction、referenceType/referencePath/resolvedReferenceClass 和 Editor Session 证据；结构化写入额外返回 `structuredKind`、`structuredSchema`、`diff` 与 `diffTruncated`；材质参数写入返回 `parameterName`、`parameterType`、`parameterAssociation=Global`（并移除 `propertyPath`），`valueKind` 分别为 `material-scalar`/`material-vector`/`material-texture`/`material-static-switch`；DataTable 写入返回 `rowName`、`dataTableKind`、`rowStructPath`（并移除 `propertyPath`），`valueKind` 为 `data-table-cell`/`data-table-row-fields`/`data-table-row-add`/`data-table-row-remove`/`data-table-row-rename`；成功修改额外返回稳定 `transactionId`（已提交的 Editor Undo Transaction 的 GUID），供 `ue_undo_asset_property_live`/`ue_discard_asset_property_live` 精确回退；标量 Operation 的既有返回字段与值保持兼容。
- PIE/SIE、地图、Dirty Package、Blueprint、嵌套属性路径、不支持的属性类型、容器、非 MI 资产、不存在的材质参数、非 DataTable 资产、缺失或重复行、不支持的字段、被引用行与 DataTable 之外的 Live Apply 全部拒绝。

## Live Editor Write Undo / Discard

`editor.undoAssetPropertyLive`（`ue_undo_asset_property_live`）与 `editor.discardAssetPropertyLive`（`ue_discard_asset_property_live`）提供对最近一次已确认 Live Apply 的显式回退，替代依赖用户手动 Ctrl+Z：

- 只接受 `assetPath` + `transactionId` + `editorSessionId`（三者在 Apply 结果中返回）。Bridge 按精确 Asset Path + Transaction GUID 保存记录，不再以新记录静默覆盖旧 Transaction；当前 Clean Package 门禁仍保证一个资产同时只有当前未保存写入可撤销，已保存且不可撤销的旧记录会在下一次 Clean Apply 前清理。
- Undo 调用 `GEditor->UndoTransaction(true)`（可 Redo），Discard 调用 `GEditor->UndoTransaction(false)`（丢弃事务）；两者都只作用于一像素精确的已提交事务，绝不任意回滚编辑器历史或跨资产批量撤销。
- 回退前强制校验：记录存在且 `transactionId` 完全匹配（否则 `live-editor-write-undo-not-found`/`-transaction-mismatch`）、`editorSessionId` 与记录及当前 Bridge 会话一致（`-session-mismatch`）、资产仍是同一内存对象且 Class/Package 一致（`-asset-mismatch`）、包在写入后未被保存（`-package-saved`）、Undo 栈顶就是本次事务（`GetUndoContext` 的 TransactionId/PrimaryObject 匹配，否则 `-stack-mismatch`），并读回确认目标仍等于本次 Live Apply 的 `afterValue`；若目标被后续非事务修改，返回 `live-editor-write-undo-target-changed`，不得覆盖较新的值。
- `GEditor->UndoTransaction(...)` 执行失败返回 `live-editor-write-undo-failed`，不使用 Snapshot 冒充事务成功；回退成功后显式恢复写入前 Dirty 状态，并用保留 Snapshot 读回验证目标值（不一致时用 Snapshot 兜底恢复），验证失败返回 `live-editor-write-undo-verify-failed`；成功后 Bridge 与 Workflow 记录均删除，二次 Undo 或 Verify 返回 not-found。
- 返回 `action`（`undo-asset-property-live`/`discard-asset-property-live`）、`operation`、`valueKind`、`assetPath`、`transactionId`、回退前/后值、`dirtyBefore`/`dirtyAfter`、`transactionRecorded=false`、`saved=false` 与 `editorSessionId`；整个过程不触碰磁盘、SQLite 或 Revision Export。

该 Capability 不生成磁盘 Revision，也不修改 SQLite/Revision Export。用户检查后可以在编辑器中 Undo，或者通过现有 `ue_save_authorized_asset` Preview/Commit 流程显式保存。

## 可恢复 Workflow Journal 与版本语义

- MCP Workflow 将每个成功且有变更的 Live Apply 原子写入固定 Work Root 下的 `live-write-journal/live_*.json`；Authorized Save 更新同一记录，成功 Undo/Discard 或成功 Verify 删除记录。MCP Server 重启后只恢复通过 Project、Schema、Operation、Target、Transaction 和生命周期校验的记录；损坏或身份不匹配记录只计入状态，不会获得写入权限。
- `ue_verify_live_write` 可携带精确 `liveApplyReceipt`，为空时兼容地选择该资产最新待处理记录。同一资产的多个已保存待验证记录不会互相覆盖。Journal 写盘失败会返回 `journalPersisted=false`，但不会把已经成功的 Editor 修改或授权保存伪报成失败。
- 对外协议与正式包版本仍为 `0.6.0`；Bridge 和 Workflow 状态额外返回 `developmentLine=0.7.0-dev`，明确当前 `main` 能力尚未作为正式版本发布。

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
live-editor-write-undo-not-found
live-editor-write-undo-transaction-mismatch
live-editor-write-undo-session-mismatch
live-editor-write-undo-asset-mismatch
live-editor-write-undo-stack-mismatch
live-editor-write-undo-package-saved
live-editor-write-undo-target-changed
live-editor-write-undo-failed
live-editor-write-undo-verify-failed
live-editor-batch-task-busy
live-editor-batch-task-not-found
live-editor-batch-task-world-invalidated
live-editor-batch-task-timeout
live-editor-batch-task-failed
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
4. 通过真实 MCP `stdio` Client 发现并调用 22 个 Live/Realtime Tool；自管理无界面 Editor 验证 Graph Tool 的安全降级，真实选中 Node 的正向结果由 UE5.6 API 编译和 Schema/单元回归覆盖。
5. 验证 Token、端口、Descriptor 和固定本机路径不进入 MCP 响应。
6. 验证临时 immutable SQLite 哈希和目录文件集合不变。
7. 仅关闭脚本自己创建的 Editor，并清理对应 Descriptor。
