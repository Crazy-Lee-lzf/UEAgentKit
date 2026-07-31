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

### 固定项目 Live Editor 模式

使用 `-EnableLiveEditor -ProjectPath <固定 .uproject>` 后，在五个离线查询 Tool 之外注册：

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

该模式不要求启用写入。MCP Server 从固定项目 `Saved/UEAgentKit/EditorBridge.json` 读取 localhost 临时端点，并校验随机认证令牌、规范化项目路径摘要、Plugin/Server 版本和注册 Capability。地址、端口、令牌、Descriptor 和任意本机路径不会成为 Tool 参数或响应字段。Editor 未运行时 `ue_editor_status` 稳定报告 `state=unavailable`，其余 Live Tool 返回可重试错误，离线 SQLite Tool 保持可用。详细协议见 `spec/LIVE_EDITOR_BRIDGE.md`。

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
ue_set_blueprint_default
ue_set_component_property
ue_set_pin_default
ue_set_asset_property
ue_apply_asset_property_live
ue_set_asset_reference_property
ue_set_asset_structured_property
ue_set_material_parameter
ue_set_datatable_cell
ue_set_datatable_row_fields
ue_add_datatable_row
ue_remove_datatable_row
ue_rename_datatable_row
ue_plan_patch
ue_dry_run_patch
ue_apply_patch
ue_verify_asset
ue_get_asset_state
ue_refresh_asset_index
ue_rollback_patch
```

只有再使用 `-EnableCommitTools`，且固定 Policy 的 `commitEnabled=true`，`ue_apply_patch` 与 rollback Commit 才能写入项目资产。`ue_refresh_asset_index` 不修改 `.uasset`，但会修改 UE Agent Kit 自己的活动索引 Generation，因此标记为 `readOnlyHint=false`、`destructiveHint=false`。

### `ue_get_asset_state`

只接受一个精确 `/Game/...Asset.Asset`，并只在固定项目工作流模式注册。该 Tool 为 `readOnlyHint=true`，汇总：

```text
memory           可选 Live Editor 已加载/Dirty/打开/选择状态；不提供 Revision
disk             当前 .uasset/.umap SHA-256
revisionExport   会话冻结 Canonical Revision
sqlite           会话冻结 SQLite Revision
```

总体状态包括 `synchronized`、`memory-dirty`、`disk-newer-than-snapshots`、`sqlite-outdated`、`revision-export-outdated`、`persistent-sources-diverged` 和 `incomplete`。结果同时返回 `saveRequired`、`indexRefreshRequired`、`refreshBlockedByDirtyMemory` 与 `recommendedAction`。`loaded-saved` 只表示内存 Package 未 Dirty，不等于内存与磁盘经过加密哈希证明相同。

### `ue_refresh_asset_index`

该 Tool 只接受：

```text
asset_path = 一个精确、Policy 授权的 /Game/...Asset.Asset
mode = Preview | Apply
```

它不接受数据库、Revision Export、Work Root、输出目录、Commandlet 参数或任意文件路径。`Preview` 独立导出并验证目标资产，但不切换活动快照。`Apply` 会：

1. 拒绝 Dirty Live Editor Package；Editor 在线但无法可信读取 Dirty 状态时也拒绝。
2. 独立导出一个资产，并要求 Canonical Revision 等于当前磁盘 Package SHA-256。
3. 在固定 Work Root 中构建下一代 Revision Export 和 SQLite Pair。
4. 校验项目身份、Schema、FTS5、`PRAGMA integrity_check`、目标 Revision、Sidecar 和磁盘空间。
5. 将 Generation 目录发布后，原子替换固定 `active-snapshot.json` Pointer。
6. 使当前会话的 Plan、Dry Run、Apply 与 rollback Receipt 失效，并拒绝后续工作流调用。

当前 MCP 会话始终读取启动时冻结的旧快照；Apply 不会热替换活动 SQLite 连接。新 MCP 会话解析 Pointer 后才读取新 Generation。配置中原始 SQLite 与 Revision Export 保持不变。

## 启动配置不是 Tool 参数

Database、Engine、Project、Policy、Revision Export、Work Root、Backup Root、进程超时、Live Editor 启用状态和 Live Editor 超时只能在 Server 启动时配置。任何 MCP Tool Schema 都不会出现这些字段，因此 Agent 不能在调用中切换工程、Policy、引擎、数据库、Bridge 端点或输出位置。写入完整模式还要求 SQLite `projectKey`、Revision Export `projectName` 与 `.uproject` 文件名完全一致；Live Editor 与写入模式组合时必须使用同一个固定 `.uproject`。

## Live Editor Tool

Live Editor 能力分为两类，成功结果均标记 `source=live-editor-memory`，都不读取或改写 SQLite、不生成磁盘 Revision，也不改变索引 freshness：

- Live Read：10 个 Tool，`readOnlyHint=true`、`destructiveHint=false`。
- Live Action：7 个 Tool，`readOnlyHint=false`、`destructiveHint=false`；允许改变 Editor 选择、窗口、已加载状态或内存编译状态，但不保存 Package。

- `ue_editor_status`：Bridge 可用性、Plugin/Engine 版本、Project、PID、Session、Capability、PIE、当前关卡和 Dirty Package 计数。
- `ue_get_selection`：Actor、Component、Asset 和 Object 当前选择，去重后最多 200 项。
- `ue_get_open_assets`：`UAssetEditorSubsystem` 中打开的资产，最多 200 项。
- `ue_get_dirty_assets`：Dirty `/Game/` Package 和 Asset Registry 路径，最多 200 项。
- `ue_get_current_level`：Editor World、Persistent/Current Level、World Partition 和 Dirty 状态。
- `ue_get_pie_state`：`stopped`、`playing` 或 `simulating`，以及 Play World/Net Mode。
- `ue_get_output_log`：4096 条内存环形缓冲，支持 Category、最低 Verbosity、关键词、UTC 范围、PIE Session、`since_sequence` 和最多 100 条分页读取。
- `ue_get_compile_errors`：读取 Bridge 当前会话捕获到的编译相关 Warning/Error，并补充最多 100 个已加载 Blueprint 的 `Status` 与截断信息；返回 `historyComplete=false`，不冒充完整 Message Log 历史。
- `ue_inspect_asset_live`：只接受精确 `/Game/...Asset.Asset`，返回 Asset Registry 和已加载对象状态；使用 `StaticFindObject`，不触发加载，并明确返回 `loadedByBridge=false`。
- `ue_get_blueprint_graph_selection`：无参数，只读取最近激活的普通 Blueprint Editor，返回 Focused Graph GUID 和最多 100 个当前 Graph 选中 Node GUID；不支持其他 Graph Editor，也不提供编辑。
- `ue_open_asset`：加载一个 Asset Registry 中存在的精确资产并使用注册 Asset Editor 打开；返回加载、打开和 Dirty 前后状态，不保存。
- `ue_focus_asset`：只聚焦已经打开的精确资产；不加载未打开资产。
- `ue_sync_content_browser`：使用 `FAssetData` 在 Content Browser 定位精确资产，不加载目标。
- `ue_focus_actor`：按当前 Editor World 的唯一 `ActorGuid` 选择并聚焦视口；不存在、重复或不可选择时稳定拒绝。
- `ue_compile_blueprint`：加载一个精确 Blueprint，在内存中编译并返回前后状态、Dirty 变化和当前会话诊断；不保存。
- `ue_validate_asset`：使用官方 `UEditorValidatorSubsystem` 验证一个精确资产，最多返回 200 条问题。
- `ue_validate_folder`：验证一个非根 `/Game/...` Package Path，可选递归；匹配资产数必须不超过 `max_assets`，硬上限 500，最多返回 200 条问题。

稳定错误包括通用连接/协议错误，以及 `live-editor-pie-active`、`live-editor-asset-not-found`、`live-editor-asset-load-failed`、`live-editor-asset-editor-unavailable`、`live-editor-asset-not-open`、`live-editor-world-unavailable`、`live-editor-actor-not-found`、`live-editor-actor-guid-ambiguous`、`live-editor-actor-not-selectable`、`live-editor-blueprint-required`、`live-editor-data-validation-unavailable`、`live-editor-folder-empty` 和 `live-editor-asset-limit-exceeded`。

## 查询 Tool

### `ue_get_capabilities`

返回当前 Server 版本、模式、实际注册的 Tool、可用 Operation、查询上限、响应契约和安全边界。只读模式不会把写入 Operation 标记为可用。

### `ue_get_project_status`

返回 Project Key、固定项目状态、Engine 版本、SQLite Schema、索引时间、Exporter 版本、统计信息、Workflow 模式、索引新鲜度状态和 Live Editor 可用性。

固定项目模式会比较会话冻结的 SQLite Revision、配对 Revision Export Canonical Revision 和磁盘 Package SHA-256，返回 `fresh`、`stale`、`partial` 或 `unavailable`。默认只读模式没有固定 Project 与 Revision Export，必须明确返回 `state=unknown`，不能把未知状态报告为 fresh。刷新 Apply 后旧会话仍可查询旧索引，但 `workflow.indexLifecycle.restartRequired=true`，所有新工作流动作返回 `snapshot-refresh-restart-required`。详细契约见 `spec/INDEX_FRESHNESS.md`。

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

## 高层安全写入 Tool

常见修改优先使用以下 Tool，Agent 不需要填写底层 Operation 名称或 Patch Target：

```text
ue_set_blueprint_default    variable_name + value
ue_set_component_property   component_name + property_path + value
ue_set_pin_default           graph_guid + node_guid + pin_name + value
ue_set_asset_property        property_path + value
ue_set_asset_reference_property  property_path + reference object or null
ue_set_asset_structured_property   property_path + stable structured value
ue_set_material_parameter    parameter_name + parameter_type + value
ue_set_datatable_cell        row_name + field_name + value
ue_set_datatable_row_fields   row_name + values
ue_add_datatable_row          row_name + values
ue_remove_datatable_row       row_name
ue_rename_datatable_row       row_name + new_row_name
```


`ue_set_asset_reference_property` maps to `setAssetReferenceProperty` and accepts `null` or an exact `{referenceType, path}` object. It only targets Data Asset top-level Object/Class/Soft Object/Soft Class properties and does not broaden the scalar `ue_set_asset_property` contract.

`ue_set_asset_structured_property` maps to `setAssetStructuredProperty`. It replaces one Data Asset top-level Struct/Array/Set/Map with the Reader-exported stable schema; Struct fields must be complete and Set/Map entries must be uniquely sorted by Canonical JSON. Commit remains a separate `ue_apply_patch` step.

`ue_add_datatable_row` accepts an optional 0–32-field scalar object. `ue_remove_datatable_row` and `ue_rename_datatable_row` generate low-level Operations with the required explicit `value=true` acknowledgement. All three still follow Plan → Dry Run → one-time receipt → explicit Commit and the existing backup/verification/rollback gates.

所有高层 Tool 都要求完整 Unreal Object Path，并支持：

```text
mode=Plan     仅创建并校验严格 Plan，默认值
mode=DryRun   自动执行 Plan -> Unreal Dry Run，返回 planId 与 dryRunReceipt
```

高层 Tool **不提供 Commit 模式**。实际保存仍必须调用 `ue_apply_patch`，携带高层 Dry Run 返回的一次性 `dryRunReceipt`，并使用精确 `COMMIT <planId>` 确认。这样高层易用性不会绕过 Policy、Revision、新鲜度、备份、验证或 rollback 安全门。

### `ue_apply_asset_property_live`

该破坏性 Tool 属于 Workflow 写入 Tool；执行时必须同时启用 Live Editor、Workflow Write 与 Commit 三项启动能力。它不接受任意资产、属性或 Policy，而是只接受当前会话已有的高层 Plan 结果（`ue_set_asset_property(mode=Plan)`、`ue_set_asset_reference_property(mode=Plan)`、`ue_set_asset_structured_property(mode=Plan)` 或 `ue_set_material_parameter(mode=Plan)`）：

```text
plan_id      = 当前会话有效 Plan ID
confirmation = LIVE APPLY <planId>
```

执行前重新校验 Plan 文件摘要、固定 Policy、允许的 `setAssetProperty`/`setAssetReferenceProperty`/`setAssetStructuredProperty`/`setMaterialInstanceScalarParameter`/`setMaterialInstanceVectorParameter`/`setMaterialInstanceTextureParameter`/`setMaterialInstanceStaticSwitchParameter`/`setDataTableCell`/`setDataTableRowFields`/`addDataTableRow`/`removeDataTableRow`/`renameDataTableRow` Operation 和磁盘基线 Revision。调用 `editor.applyAssetPropertyLive` 时必须显式传递 `operation`，Bridge 不靠 Value 猜测。Editor Bridge 只接受已经加载且已在资产编辑器中打开、Package 当前干净、位于 `/Game` 的非 Blueprint 单文件资产；`setAssetProperty` 只接受一个顶层可编辑标量/Enum/String/Name/Text 属性，`setAssetReferenceProperty` 只接受 Data Asset 顶层 Object/Class/SoftObject/SoftClass 引用属性（引用值为 `{"referenceType": ..., "path": "/Game/...Object"}` 或 JSON `null` 清空），`setAssetStructuredProperty` 只接受 Data Asset 顶层 Struct/Array/Set/Map 结构化属性（值必须按 Reader 导出的稳定 Schema 验证，Struct 字段必须完整、Set/Map 条目必须按 Canonical JSON 唯一有序；固定数组与容器值会被拒绝），四个材质参数 Operation 只接受已加载的 `MaterialInstanceConstant` 的 Global Association 参数（Target 为 `{"parameterName": ...}`，值格式与离线 Patch Schema 完全一致：Scalar=有限 JSON number、Vector=`{r,g,b,a}`、Texture=Object Path 字符串、StaticSwitch=JSON boolean；参数不存在或 Texture 无法加载即拒绝）。五个 DataTable Operation 只接受已加载的 DataTable（Target 为 `{"rowName": ...}`，`setDataTableCell` 附加 `fieldName`、`renameDataTableRow` 附加 `newRowName`），行结构必须是可用行；字段写入强制复用现有 Row Schema（标量字段：bool/Enum/数值/String/Name/Text）与 `allowedDataTableFields` 授权，Add/Remove/Rename 保持离线契约（`value=true` 确认、行存在性/目标行冲突/引用影响门禁），单资产单 Transaction 失败整体恢复。PIE/SIE、Dirty Package、嵌套属性路径、地图、Blueprint、容器、非 MI 资产、不存在的材质参数、非 DataTable 资产、缺失或重复行、不支持的字段、被 Searchable Name 引用的行和 DataTable 之外的不支持属性类型全部拒绝。

成功时在 Game Thread 中通过 `FScopedTransaction`、`Modify()`、`PostEditChangeProperty()`（材质参数复用 `UMaterialEditingLibrary` 刷新）和 `MarkPackageDirty()` 修改当前 Editor 内存，返回 `operation`、`valueKind`、Before/After、Dirty、Editor Session 与 Undo 事务证据；引用写入还返回 `referenceType`、`referenceConstraintClass`、`referencePath` 与 `resolvedReferenceClass`，结构化写入还返回 `structuredKind`、`structuredSchema`、`diff` 与 `diffTruncated`，材质参数写入还返回 `parameterName`、`parameterType` 与 `parameterAssociation=Global`，DataTable 写入还返回 `rowName`、`dataTableKind`（`cell`/`row-fields`/`row-add`/`row-remove`/`row-rename`）与 `rowStructPath`（`setDataTableCell` 附加 `fieldName`、`renameDataTableRow` 附加 `newRowName`）。失败会恢复原值、原 Dirty 状态并取消 Transaction，No-op 不制造 Undo 或 Dirty。该 Tool 始终返回 `saved=false`、`diskRevisionChanged=false`，不会调用 `SavePackage`；用户可在编辑器中检查或撤销，持久化仍需单独走现有授权保存流程。

`ue_set_material_parameter.parameter_type` 仅接受 `Scalar`、`Vector`、`Texture` 或 `StaticSwitch`，Server 映射到现有四个已注册 Operation。高层 Tool 只覆盖当前稳定 Operation；`ue_plan_patch` 继续保留，供已注册但尚无高层封装的 Operation 使用。

Live Write 稳定错误包括 `live-editor-write-disabled`、`live-editor-required`、`live-editor-write-confirmation-required`、`live-editor-write-not-allowed`、`live-editor-write-operation-unsupported`、`live-editor-write-asset-not-loaded`、`live-editor-write-asset-not-open`、`live-editor-write-blueprint-unsupported`、`live-editor-write-package-invalid`、`live-editor-write-package-dirty`、`live-editor-write-property-not-found`、`live-editor-write-property-not-editable`、`live-editor-write-property-type-unsupported`、`live-editor-write-value-invalid`、`live-editor-write-material-instance-required`、`live-editor-write-material-parameter-invalid`、`live-editor-write-material-parameter-not-found`、`live-editor-write-material-texture-invalid`、`live-editor-write-material-apply-failed`、`live-editor-write-data-table-required`、`live-editor-write-data-table-row-invalid`、`live-editor-write-data-table-row-not-found`、`live-editor-write-data-table-row-exists`、`live-editor-write-data-table-field-unsupported`、`live-editor-write-data-table-value-invalid`、`live-editor-write-data-table-row-referenced`、`live-editor-write-data-table-apply-failed` 和 `live-editor-write-apply-failed`。

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

## Validation Evidence

Live Validation 和 Automation 结果保留 Editor Bridge 返回的 `validationEvidence`，MCP Server 不重写 Evidence ID、项目哈希、Editor Session、UTC 时间或 Revision Set。`ue_get_capabilities` 的 `liveActionContract` 声明 Evidence Schema 版本、项目绑定和 Revision Set 绑定语义。

## 分页与输出预算

- continuation token 是分页状态，不是 API Key、登录 Token 或模型 Token。
- Token 为当前 Server 会话生成的不透明随机值，不暴露查询文本和本机路径。
- Token 绑定 Tool、固定 SQLite 快照和原始查询参数，不能跨 Tool、跨索引快照或跨 Server 重启复用。
- `offset` 继续保留兼容性，但 Agent 应优先使用 continuation token。
- 查询响应返回 `hasMore`、兼容字段 `mayHaveMore`、`continuationToken` 和 `source`。
- `outputBudget` 返回 `maxTokens`、`estimatedTokens`、`truncated` 和 `truncationReason`。
- 截断原因包括 `page-limit`、`section-limit`、`token-budget` 和 `single-result-exceeds-token-budget`。

## Client 兼容契约

0.6.0 提供 `scripts\TestMcpClients.cmd`，通过两个独立真实 `stdio` 会话验证：

```text
官方 Python MCP ClientSession
不依赖 SDK 的原始 newline-delimited JSON-RPC Client
```

矩阵要求两类 Client 协商相同 MCP Protocol Version，发现相同 Tool 顺序与 JSON Schema，并正确接收 `structuredContent`。每个 Tool 同时保留可解析的单条 JSON Text Content 回退，错误也使用相同 Envelope，便于只消费文本内容的 Host 保持兼容。

Claude Code 契约检查本地 `stdio`、非空 Tool Description、Object 型 `inputSchema`、完整 annotations，以及 Tool 参数中不存在固定 Database、Project、Engine、Policy 或文件路径。标准 MCP/ChatGPT Host 契约只验证 `tools/list`、`tools/call`、`structuredContent` 和文本回退；本地自动化不会声称测试托管 ChatGPT UI、账号配置或远程 Transport。

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

写入流程进一步区分：

```text
data-table-row-referenced    DataTable 删除/重命名目标 Row 存在精确 Searchable Name 引用
policy-rejected             固定 Policy 拒绝资产、Operation 或语义 Target
revision-conflict           Plan Revision 与当前 Revision Export 不一致
dirty-package               Revision Export 记录目标 Package 为 Dirty
workflow-timeout             UE 子进程超过固定超时
ue-process-crashed           检测到 Fatal、Assertion、访问冲突或已知崩溃退出码
workflow-report-missing      子进程未生成要求的结构化报告
workflow-report-invalid      报告不是有效 JSON Object
```

子进程错误 `details` 可包含脱敏的 `diagnosticId`、`reportId`、`stage`、`exitCode`、`stdoutTail` 和 `stderrTail`。`reportId` 只用于关联当前会话诊断，不暴露本机报告路径。成功的 Dry Run、Commit、Verify 和 rollback 响应也返回对应 `reportId`。

## 底层写入工作流 Tool

### `ue_plan_patch`

输入资产路径、已注册 Operation、语义 Target 和 JSON Value。Server：

1. 从固定 SQLite 获取 Asset Class 与 SHA-256 Revision。
2. 生成单资产、单 Operation Patch。
3. 使用固定 Policy 与 Revision Export 纯校验。
4. 对 `removeDataTableRow` / `renameDataTableRow`，从 immutable SQLite 精确查询目标 `DataTable Object Path::RowName` 的 `depends-searchable-name` 引用。
5. 存在引用时返回 `data-table-row-referenced`，删除临时 Plan 目录，不生成可执行 Plan；无引用时在响应中返回 `referenceImpact`。
6. 将 Patch 写入固定 Work Root。
7. 记录 Canonical JSON 摘要。

Plan 只在当前 Server 会话有效。

该 Plan 检查是提前反馈，不替代执行期安全判断。UE Commandlet 在 Remove/Rename 实际修改前使用当前 Asset Registry 和完整 `FAssetIdentifier(Package, Object, RowName)` 再次检查；发现 Referencer 时以退出码 17 零写入拒绝。当前不自动重写引用方，也不使用字符串模糊扫描。

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

完整测试使用隔离 Scalar Fixture，最终必须恢复测试前 `.uasset` SHA-256。启用 Workflow 与 Project Memory 后，它还必须持久化 verified Commit 与 rollback Task Record，验证 rollback 后旧 Commit Record 变为 `stale`、rollback Record 保持 `valid`、Memory Audit 摘要可重算，并确认 immutable SQLite Index 目录完全不变。

DataTable Row 引用影响真实回归：

```bat
scripts\TestDataTableRowReferenceImpact.cmd ^
  -EngineRoot "E:\Path\To\UE_5.6" ^
  -ProjectPath "E:\Path\To\Project.uproject"
```

该回归要求：

- 精确 Searchable Name 引用可被导出为 `DataTable Object Path::RowName`。
- Remove/Rename 都在 UE 执行阶段被拒绝。
- 目标 Row 与 Revision 完全不变。

Data Asset 引用属性真实回归：

```bat
scripts\TestDataAssetReferenceProperties.cmd ^
  -EngineRoot "E:\Path\To\UE_5.6" ^
  -ProjectPath "E:\Path\To\Project.uproject"
```

该回归要求：

- Reader 精确识别 Object、Class、Soft Object、Soft Class 四种属性类型。
- 四种引用与 `null` 清空共 5 次 Dry Run 均恢复内存值且磁盘 Revision 不变。
- 四种引用 Commit 后可由独立 UE 进程重新读取。
- 四层逆序 rollback 后全部引用为空，最终 Revision 与初始值完全一致。

Data Asset Struct/容器属性真实回归：

```bat
scripts\TestDataAssetStructuredProperties.cmd ^
  -EngineRoot "E:\Path\To\UE_5.6" ^
  -ProjectPath "E:\Path\To\Project.uproject"
```

该回归要求：

- Reader v2 为 Struct、Array、Set、Map 导出递归 Schema 和稳定值。
- Struct 字段、Array 索引、Set 增删和 Map 键增删/嵌套值修改生成结构化 Diff。
- 四种 Dry Run 均完成深恢复且磁盘 Revision 不变。
- 四次 Commit 可独立重载，四层逆序 rollback 后最终 Revision 与初始值完全一致。

## Project Memory 模式

Project Memory 是可选的独立持久化层，不属于会被替换的 immutable Index Snapshot。启动参数：

```text
--enable-project-memory
--memory-database <fixed-memory.sqlite3>
```

若只启用 Project Memory，Server Mode 为 `fixed-project-memory`。Memory Database 和 Project Key 均在启动时固定：数据库路径来自 Server 配置，Project Key 来自当前索引的 `metadata.project_key`。六个 `ue_memory_*` Tool 均不接受数据库、索引、项目或文件系统路径参数。

Tool 分组：

```text
Read:
  ue_memory_search
  ue_memory_get

Persistent planning actions:
  ue_memory_add_rule
  ue_memory_record_finding
  ue_memory_record_task
  ue_memory_mark_superseded
  ue_memory_validate
```

`ue_memory_record_task` 固定写入 `taskRecord + tool-observed`，只接受终态 `succeeded`、`failed`、`rolledBack` 或 `cancelled`。它必须绑定最终结论、Patch、Backup Manifest、Validation Evidence 和至少一个稳定 Revision；Artifact 引用不能是绝对本机路径或父目录穿越路径。

`ue_memory_validate` 使用当前固定 SQLite 的 `assets.revision_value`，仅更新 Memory Status，不修改索引或 Unreal Asset。完整数据模型和状态规则见 [`PROJECT_MEMORY.md`](PROJECT_MEMORY.md)。

## Workflow 与 Project Memory 证据交接

同时启用 Workflow 和 Project Memory 时，`ue_get_capabilities.projectMemory` 声明：

```text
workflowEvidenceHandoff = true
workflowEvidenceSourceTools = [ue_verify_asset, ue_rollback_patch]
workflowEvidenceTargetTool = ue_memory_record_task
workflowEvidenceArgumentsPath = memoryTaskEvidence.arguments
```

`ue_verify_asset` 只有在独立 UE 重载确认 Asset Path 与最终 Revision 完全一致后，才返回 `outcome=succeeded` 的 `memoryTaskEvidence`。`ue_rollback_patch` 只有在 Commit 恢复和独立验证均成功后，才返回 `outcome=rolledBack` 的同格式证据包。两者的 `arguments` 都可以直接作为 `ue_memory_record_task` 参数。Agent 不得从日志文本、文件路径或会话 Receipt 自行重建证据。

仅启用 Memory、未启用 Workflow 时，`workflowEvidenceHandoff=false`，不会暗示存在可验证的写入证据来源。
