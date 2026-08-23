# UE Agent Kit MCP Server

UE Agent Kit 0.7.0 通过本地 MCP `stdio` 提供稳定的高层查询、分层 Memory Context、实时 Editor Context/Batch/Change Set 和受控资产工作流。MCP 层不会开放任意 SQL、Shell、文件系统路径、Commandlet 参数或 UObject 调用。

## 模式

### 默认只读模式

固定一个不可变 SQLite 索引，只注册：

```text
ue_get_capabilities
ue_get_project_status
ue_search
ue_get_asset
ue_find_references
ue_get_task_context
ue_analyze_change_impact
ue_analyze_semantic_diff
ue_build_verification_plan
ue_evaluate_trust_verdict
```

### 固定项目 Live Editor 模式

使用 `-EnableLiveEditor -ProjectPath <固定 .uproject>` 后，在八个离线查询 Tool 之外注册：

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
ue_undo_asset_property_live
ue_discard_asset_property_live
ue_verify_live_write
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

Live Editor 能力分为三组，成功结果均标记 `source=live-editor-memory`，都不读取或改写 SQLite、不生成磁盘 Revision，也不改变索引 freshness：

- Live Read：10 个 Tool，`readOnlyHint=true`、`destructiveHint=false`。
- Live Action：8 个 Tool，`readOnlyHint=false`、`destructiveHint=false`；允许改变 Editor 选择、窗口、已加载状态或内存编译状态，但不保存 Package。
- Realtime Foundation：4 个 Tool，包括一个有界 Context 聚合读取和一个支持 Start/Status/Cancel 的分帧 Batch Task；只读取当前 Editor 内存与已加载 World。

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
- `ue_run_automation_test`：在隔离子进程中运行一个精确注册的 Automation Test，并返回结构化 Validation Evidence；不接受任意命令或测试前缀。
- `ue_get_editor_context`：在一次只读请求中聚合 Editor、World、Selection、Open Assets、Dirty Packages、Blueprint Graph Selection、Compile Errors 和 Output Log Cursor；返回每阶段耗时、截断状态和结构化 `nextActions`。
- `ue_start_batch_task`：启动一个固定 `scanCurrentWorld` 分帧扫描。默认最多 2000 个 Actor、每 Actor 100 个 Component、60 秒超时；Server 和 Bridge 仍执行更严格的硬上限。
- `ue_get_batch_task`：默认返回进度和聚合摘要；`include_details=true` 时使用 `detail_offset/detail_limit` 读取最多 5 个 Actor 的一页详情。
- `ue_cancel_batch_task`：取消当前 Editor Session 中唯一运行的 Batch Task。

Batch Task 只扫描当前已加载 World。Level 使用弱引用，Actor 不跨帧保存裸指针；每 Tick 同时受最多 256 个 Actor Slot 和约 2 ms 时间预算约束。World/Session 变化、PIE/SIE、超时或取消均返回明确终态。分页详情用于保证最坏响应仍低于 1 MiB Bridge 上限。

稳定错误包括通用连接/协议错误，以及 `live-editor-pie-active`、`live-editor-asset-not-found`、`live-editor-asset-load-failed`、`live-editor-asset-editor-unavailable`、`live-editor-asset-not-open`、`live-editor-world-unavailable`、`live-editor-actor-not-found`、`live-editor-actor-guid-ambiguous`、`live-editor-actor-not-selectable`、`live-editor-blueprint-required`、`live-editor-data-validation-unavailable`、`live-editor-folder-empty`、`live-editor-asset-limit-exceeded`、`live-editor-batch-task-busy`、`live-editor-batch-task-not-found`、`live-editor-batch-task-world-invalidated`、`live-editor-batch-task-timeout` 和 `live-editor-batch-task-failed`。

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

### `ue_get_task_context`

一次只读请求把任务相关的确定性事实源聚合为有界 Task Context：

```text
query                 必填，任务的自然语言描述
asset_paths           可选，精确 /Game Object Path，最多 10 个
work_item_id          可选，Active Work Item ID
change_set_id         可选，Change Set ID
include_live_context  默认 true
include_memory        默认 true
max_output_tokens     默认 4096，范围 256–32768
```

返回结构按 `request / project / targetAssets / relevantAssets / memory / activeWork / liveEditor / revisionState / changeSet / correlation / risks / nextExpansions / degradedSources / outputBudget` 组织。每个事实带 `source`；Revision 状态来自 SQLite / Revision Export / 磁盘 Package 三方 SHA-256 比较；Memory 摘要复用有界 `ue_memory_get_context`；Live Editor 摘要复用 Bridge 的 `editor.getEditorContext`；Change Set 复用 journal 状态机。

- `risks` 只包含确定性事实：`target-dirty-in-editor`、`asset-stale`、`asset-revision-unavailable`、`target-not-indexed`、`memory-stale-records`、`memory-conflicted-records`、`change-set-not-found`、`change-set-terminal`、`work-item-not-found`、`relevant-assets-search-failed` 等；不包含模型推断，禁止把猜测混成事实。
- 某一来源不可用时只降级对应 section（例如 Offline 模式 `revisionState.available=false, reason=revision-export-not-configured`，Live Editor 未启用时 `liveEditor.reason=live-editor-disabled`），不会让整个请求失败；降级明细在 `degradedSources` 中显式列出。
- `relevantAssets` 是 R0.2 的确定性相关资产候选集：只复用 immutable SQLite Index 的 Asset Search（query 分词，最多 8 个 term）加少量 Symbol Search 补充，按精确 `assetPath` 去重并与显式 `asset_paths` 互斥，固定排序（命中 term 数降序 → 首个命中 term 在 query 中的位置 → `assetPath` 字典序），上限 8 条。每条至少含 `assetPath / assetClass（可证明时）/ source / whyIncluded / matchKind`，可附 `matchedTerms / matchCount / matchedSymbol`；不产出 score/confidence，不遍历引用、不调用模型。搜索子源异常时按既有错误模型降级（`degradedSources.section=relevantAssets`；全部失败时 `relevantAssets=[]` 并追加 `relevant-assets-search-failed` info 风险），不伪造结果。
- `correlation` 是 R0.3 的确定性 Cross-source Correlation：只读、每请求现算、零持久化、零模型推断。仅用精确键做联接——Change Set 的 `editorSessionId` 与 Live Editor `sessionId`（相等即 `matches`，不等产生 `change-set-editor-session-mismatch` medium 风险）、资产路径集合交集（Change Set `affectedAssets` / Active Work `assetPaths` ↔ Editor dirty/open、相互之间）、Change Set ID 字面量在工作项文本字段中的出现、资产 scope 的 Memory Evidence（复用 scoped `search_records`，全部状态，每条附 `recordId/status/recordType/title`）。Change Set 只在显式 `change_set_id` 且 `found` 时参与，**绝不自动发现**，也不扫描 workflow 私有 `_change_sets`；`include_memory=false` / `include_live_context=false` / 来源降级时对应联接整体缺席，绝不伪造。链接种类固定（`change-set-editor-session / change-set-asset-in-editor / change-set-asset-memory-evidence / work-change-set-asset-overlap / work-references-change-set / work-asset-in-editor / work-asset-memory-evidence`），固定排序、上限 16 条；`summary` 如实报告 workItemsConsidered/Total、affectedAssetsSampled/Total、evidenceLookups、linksTruncated 等边界计数。无任何可关联来源时 `available=false, reason=insufficient-correlatable-sources`。Evidence 检索基于资产名 token 的 FTS 匹配，未命中只代表「本次确定性检索未命中」，不代表「不存在记录」。
- 输出受 `max_output_tokens` 强制约束：超预算时按固定优先级阶梯裁剪可展开内容（Change Set operations → Live Editor summary → Memory records/nodes → Active Work items → Relevant Assets 候选 metadata → 候选数量 → Correlation links → Correlation summary → 目标资产 metadata/summary → Revision comparisons → project stats → nextExpansions → risk details → 深层标识字段），并在 `outputBudget.truncated/truncationReason` 中显式报告；裁剪的展开路径进入 `nextExpansions`。候选与 Correlation 永不优先于 target identity、high risk 与 revision summary 等更高优先级信息。
- Memory 的 stale 检测复用现有 FTS 检索（按 asset scope + `stale` 状态过滤）；Memory FTS 对纯中文短语的匹配能力受上游 `unicode61` tokenizer 限制，检测不到只代表“本次检索未命中”，不代表“不存在 stale 记录”。

### `ue_analyze_change_impact`（R1）

对 1..8 个精确 `/Game` Object Path 目标做确定性的有界逆向引用影响分析。方向契约固定为 **consumer → target**（`references_table` 行归 consumer 资产所有，`target_asset_path` 是被引用目标），因此 `Target T ← Direct Consumer A ← Consumer C of A` 中 A/B 为 depth=1、C 为 depth=2。

```text
target_asset_paths  必填，1..8 个精确 /Game Object Path，不可重复
subject_kind        默认 asset-level；结构化 subject 枚举共 8 种，仅
                     asset-level 与 blueprint-symbol（subject=精确 symbol
                     stable_id，且必须属于唯一目标资产）被现有 Index 证据
                     机械支持；其余 6 种（data-table-row / searchable-name /
                     data-asset-object / material-instance-parent /
                     material-instance-parameter / blueprint-member）显式
                     返回 unsupported-impact-subject，不猜测
subject             结构化 subject 的精确 stable_id；asset-level 必须为空
max_depth           1..3，默认 2
max_consumers       1..100，默认 100
max_edges           1..1000，默认 500（证据行预算）
max_paths           1..100，默认 50（Impact Path 条数预算）
max_output_tokens   默认 4096，范围 256–32768
```

返回结构按 `request / direction / summary / targets / directConsumers / indirectConsumers / runtimeSensitiveConsumers / analysisGaps / validationTargets / risks / riskSummary / nextActions / outputBudget` 组织：

- 遍历是纯精确键的 BFS：每层只查询 `target_asset_path IN (frontier)`（按 500 一批分块），全局 visited 防环、BFS 保证每个 consumer 对每个 target 的 shortestDepth 稳定；同一 consumer 的多条引用边合并为一条记录（`impactedTargets[]` + `referenceKinds[]` + `evidence[]` + `paths[]`）。自引用（consumer == target）不作为 consumer 收录。
- `referenceKinds` 每项含 `rawReferenceKind / normalizedReferenceKind / source / edgeCount`；归一化类别固定为 `asset-reference / soft-reference / class-reference / blueprint-symbol-reference / searchable-name-reference / parent-reference / unknown-reference`，映射只基于 exporter 写入的 kind 事实（`inherits→parent-reference`、`depends-hard-package→asset-reference`、`casts/implements→class-reference`、`calls/macro-calls/interface-calls/reads/writes/returns/delegate-*→blueprint-symbol-reference`），未覆盖的 kind 原样保留并归一化为 `unknown-reference`，绝不根据资产名猜测。
- `runtimeSensitiveConsumers` 永远只含能被 Index 显式证明运行时消费语义的对象；当前 Index 无 runtime/editor 分类证据，固定返回 `classificationState=not-proven-with-current-evidence`、`items=[]`，不凭资产类型启发式猜测（运行时执行链属 R5）。
- `analysisGaps` 区分“没有找到 Consumer”（`no-consumer-evidence-in-index`，仅当该目标确无任何 incoming 引用行时出现）与“当前证据无法证明”（`unknown-reference-kind`、`runtime-sensitivity-not-proven`、`frontier-truncated`）；不索引的目标在 `targets[].found=false, reason=target-not-indexed` 显式表达。
- `validationTargets` 是确定性的建议验证范围：Tier 0 目标自身、Tier 1 Direct Consumers、Tier 2 有界 Indirect Consumers，按 tier → depth → assetPath 固定排序并给 `priorityOrder`；这是引用图的整理结果，不构成“已验证通过”的声明（Verification Plan 属 R3）。
- `risks` 只含确定性事实：`high-fanout-target`（直连消费者 ≥15）、`impact-analysis-truncated`、`impact-target-not-indexed`、`unknown-reference-kind`；风险等级只描述分析/修改范围风险，禁止 likely-to-break / confidence / modelScore。
- `summary` 如实报告 `targetCount / visitedAssetCount / visitedEdgeCount / directConsumerCount / indirectConsumerCount / maxDepthRequested / maxDepthReached / consumerLimit / edgeLimit / pathLimit / truncated / truncationReasons[] / frontierOmittedCount / omittedEdgeCount / omittedPathCount / pathCount / unknownReferenceKindCount`；任何超限部分不静默消失。
- 输出受 `max_output_tokens` 强制约束，裁剪阶梯固定：完整 Impact Path 明细 → consumer evidence → indirect consumer 列表（summary 计数保留）→ consumer referenceKinds → validationTargets → target identity 细节 → analysisGaps；summary、risks、nextActions 与 outputBudget 永不裁剪，裁剪原因显式返回。
- 该 Tool 是纯只读 Query 能力，Offline / Live / Workflow 全模式可用；核心事实全部来自 immutable Index，不依赖 Memory / Live Editor / Workflow。`ue_get_task_context` 的 `nextExpansions` 在有显式 `asset_paths` 时建议 `impact-analysis-explicit-targets`、仅有 `relevantAssets` 时给有界 `impact-analysis-relevant-asset-hint`，但不会在默认 Context 中自动展开 depth≥2 引用图。

### `ue_analyze_semantic_diff`（R2）

以显式 Change Set 为唯一入口，将固定 Plan intent 与选定 Workflow Evidence Stage 的 before/after 状态对齐。该 Tool 属 query 组，`readOnly=true`、确定性、零模型推断；它不会创建、保存、验证或修改 Change Set，也不会扫描私有 `_change_sets`。

```text
change_set_id       必填，当前 Workflow 中的显式 Change Set ID
stage               auto | live | persisted | verified，默认 auto
asset_paths         可选，最多 8 个不重复的精确 /Game Object Path
include_unchanged   默认 true
max_changes         默认 64，范围 1..128
max_output_tokens   默认 4096，范围 256..32768
```

请求不接受数据库、项目、Policy、before/after JSON 或任意本地路径；不自动发现 Change Set。请求阶段不完整时返回结构化 `semantic-diff-stage-unavailable` 并列出 `availableStages`，不会静默降级。

响应 schema 1.0 以资产为中心，顶层按 `request / changeSet / evidenceStage / assets / analysisGaps / risks / riskSummary / summary / nextActions / outputBudget` 组织。每个资产报告 `beforeRevision / afterRevision / revisionChanged / stageEvidenceRevision`，以及：

- `expectedChanges`：只来自固定 Plan target/value intent。
- `actualChanges`：只来自 before evidence 与 selected-stage after evidence 的比较，不复制 expected。
- `matchedChanges`：stable semantic identity 与语义值均一致。
- `unexpectedChanges`：actual 中无法匹配任何 expected 的变化。
- `missingExpectedChanges`：expected 在 selected stage 未被实际观察到。
- `unchangedCriticalFields`：Adapter 能机械证明保持不变的少量关键字段；证据不足进入 `analysisGaps`。

Change Entry 使用稳定 SHA-256 `changeId`、`assetPath/domain/operation/semanticPath/changeKind/beforeValue/afterValue/expectedValue/source/stage/status/details`。排序固定为 asset path → semantic path → change kind → stable ID；同一语义路径连续写入折叠为首个 before 到最终 expected/actual，并在 `details.operationChain` 保留中间 intent。资产过滤只改变返回视图，不改原 Evidence。输出硬上限为 8 资产、128 change、64 unchanged critical、32 gap；Token 裁剪优先去除辅助 details 和低优先级重复项，始终保留 Change Set、stage、资产身份、unexpected/missing 摘要、风险和 truncation 状态。

Stage 规则：

- `live`：LiveApply before/after transaction，仅证明 Editor Memory。
- `persisted`：Authorized Save 后的 Canonical 或 commandlet apply report，证明 disk-backed 状态。
- `verified`：独立进程重载的 Canonical，证明 reload 后语义状态；**不等于 R3 Trust Verdict**。
- `auto`：只选择所有返回 Operation 都完整具备的最高阶段，并显式返回 `selected/selectionReason/sources`。

四个 Adapter 只覆盖既有稳定受控写入：Data Asset scalar/object-class/soft reference/struct/array/set/map；DataTable cell/row-fields/add/remove/rename；Material Instance scalar/vector/texture/static-switch；Blueprint property/component/pin-default 窄写入。Set 使用无序 Canonical、Map 使用稳定 key identity、Struct 按字段、引用按资产/类路径；DataTable rename 保留 row-renamed 语义，Material Instance 区分 override add/remove/change。缺少完整 domain snapshot 时报告 gap，不扩展 Blueprint Graph、动画或通用 Writer。

expected no-op 使用独立 `noop_*` Change Set Operation：没有 LiveApply receipt、transaction、journal、save 或 independent verify，状态为 `no-op`，validation 为 `no-op`，saveState 为 `not-required`。仅当固定 baseline Canonical Revision 精确等于 Plan `expectedRevision` 时可形成 persisted no-op evidence；no-op 没有 live/verified stage。同一资产混合 no-op 与真实写时，无法证明统一最终 snapshot 的阶段保守 unavailable。

`ue_get_task_context` 只在显式 Change Set found 时建议 `semantic-diff-explicit-change-set`，不会自动运行 R2；Semantic Diff 出现 missing/unexpected 时，`nextActions` 可建议对显式资产调用 R1 `ue_analyze_change_impact`，不会在 R2 内遍历引用图。

R2 的最终真实 UE5.6 验收使用 DirectHost regression fixture，不冒充 Reforge：

```powershell
scripts/TestMcpLiveClosedLoop.ps1 `
  -EngineRoot E:\EPICGAME\UE_5.6 `
  -ProjectPath E:\WorkSpace\UEAgentKit\Build\DirectHost\HostProject.uproject

scripts/TestMcpBlueprintSemanticDiff.ps1 `
  -EngineRoot E:\EPICGAME\UE_5.6 `
  -ProjectPath E:\WorkSpace\UEAgentKit\Build\DirectHost\HostProject.uproject
```

ClosedLoop 覆盖 Data Asset、Material Instance、DataTable cell 与 rename 的 live/persisted/verified，共 12 个结果；全部 expected=actual=matched=1、unexpected=missing=0、`truncated=false`。恢复采用独立 Canonical fixture verification，5/5 通过；UE Reset 会重写语义等价的 package bytes，因此不把精确 hash 恢复作为该组测试的必要条件，冻结 Revision Export 与 SQLite 必须保持不变。

Blueprint commandlet 使用已有 `setVariableDefault`，persisted/verified 均 expected=actual=matched=1、unexpected=missing=0；verified actual 必须来自独立 `full` Canonical Export（含 `IncludeUnchangedDefaults`），不能复用 Commit report。Rollback 后 package hash 恢复，Revision Export 不变，Transactions fixture 清理。两组本地 summary 位于 `Output/McpLiveClosedLoopSmoke/semantic-diff-summary.json` 与 `Output/McpBlueprintSemanticDiffSmoke/semantic-diff-summary.json`，只作本地证据，不纳入提交。

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

执行前重新校验 Plan 文件摘要、固定 Policy、Operation Registry 和磁盘基线 Revision。当前注册表仍只开放十二个已验证 Operation；Python `OperationSpec` 提供 Target 字段、`valueKind` 与独立验证选择器，Bridge `LiveWriteOperationRegistry` 提供资产要求与执行器。调用 `editor.applyAssetPropertyLive` 时规范参数为 `operation + assetPath + target + value`，其中 `target` 是最多 32 字段的 JSON Object；旧扁平 Target 字段仅保留兼容。注册本身不授予权限，Plan、Policy、Revision、Confirmation、已加载/已打开/Clean Package、PIE/SIE 和资产域门禁仍逐层执行。现有属性、Material Instance 与 DataTable 的类型和安全限制保持不变。

成功时在 Game Thread 中通过 `FScopedTransaction`、`Modify()`、`PostEditChangeProperty()`（材质参数复用 `UMaterialEditingLibrary` 刷新）和 `MarkPackageDirty()` 修改当前 Editor 内存，返回 `operation`、`valueKind`、Before/After、Dirty、Editor Session 与 Undo 事务证据；成功修改额外返回稳定 `transactionId`（已提交 Editor Undo Transaction 的 GUID）供显式 Undo/Discard 使用；引用写入还返回 `referenceType`、`referenceConstraintClass`、`referencePath` 与 `resolvedReferenceClass`，结构化写入还返回 `structuredKind`、`structuredSchema`、`diff` 与 `diffTruncated`，材质参数写入还返回 `parameterName`、`parameterType` 与 `parameterAssociation=Global`，DataTable 写入还返回 `rowName`、`dataTableKind`（`cell`/`row-fields`/`row-add`/`row-remove`/`row-rename`）与 `rowStructPath`（`setDataTableCell` 附加 `fieldName`、`renameDataTableRow` 附加 `newRowName`）。失败会恢复原值、原 Dirty 状态并取消 Transaction，No-op 不制造 Undo 或 Dirty。该 Tool 始终返回 `saved=false`、`diskRevisionChanged=false`，不会调用 `SavePackage`；用户可在编辑器中检查或撤销，持久化仍需单独走现有授权保存流程。

### `ue_undo_asset_property_live` / `ue_discard_asset_property_live`

两个破坏性 Tool 显式回退一个精确已确认的 Live Apply，不需要新 Plan：Undo 通过 `GEditor->UndoTransaction(true)` 撤销（可 Redo），Discard 通过 `GEditor->UndoTransaction(false)` 丢弃。Bridge 记录键为 Asset Path + Transaction GUID，Workflow 通过同一 Transaction/Session 身份关闭对应 Receipt；不会按资产名模糊撤销，也不会任意遍历 Undo 历史。

```text
asset_path        = Apply 结果中的 assetPath
transaction_id    = Apply 结果中的 transactionId（精确 GUID）
editor_session_id = Apply 结果中的 editorSessionId
```

执行前重新校验 Session 身份、资产内存对象/Class/Package 身份、包未被保存、Editor Undo 栈顶就是该事务（`GetUndoContext` 的 TransactionId 与 PrimaryObject 匹配），并确认目标当前值仍等于本次 Apply 的 `afterValue`；目标被后续非事务修改时以 `live-editor-write-undo-target-changed` 拒绝，避免覆盖较新值。`GEditor->UndoTransaction(...)` 失败返回 `live-editor-write-undo-failed`，不得使用 Snapshot 伪装事务成功。回退成功后显式恢复写入前 Dirty 状态，并用写入前 Snapshot 读回验证目标值，Bridge 与 Workflow 记录同时删除（二次回退和后续 `ue_verify_live_write` 返回 not-found）。返回 `mode`（`LiveUndo`/`LiveDiscard`）、`operation`、`valueKind`、`assetPath`、`transactionId`、`editorSessionId`、回退前后值、`dirtyBefore`/`dirtyAfter` 与 `saved=false`/`diskRevisionChanged=false`；整个过程不触碰磁盘、SQLite 或 Revision Export。

### `ue_verify_live_write`（Apply → Save → Verify → Memory 闭环）

该规划类 Tool 把一次 Live Apply 收尾成标准闭环：`ue_apply_asset_property_live`（返回并 Journal 化 `liveApplyReceipt`）→ 用户显式 `ue_save_authorized_asset` Preview/Commit → `ue_verify_live_write(asset_path, live_apply_receipt="")`。显式 Receipt 精确选择记录；为空时选择该资产最新待处理记录，以兼容旧 Client。

- 每个发生实际变更的 Apply 记录原子写入固定 Work Root 的 `live-write-journal`；Authorized Save 更新为 saved，成功 Undo/Discard 或成功 Verify 删除。Server 重启只恢复通过 Schema、Project、Operation、Target、Transaction 和生命周期校验的记录。
- Apply/Save 返回 `journalPersisted`。Journal I/O 失败不会反转已经成功的 Editor 操作，只在 `ue_workflow_status.liveWriteJournal` 中报告 `pendingRecordCount`、`recoveredRecordCount` 与 `journalErrorCount`。
- 若目标包仍 Dirty：返回 `state=not-saved` 终态（`saved=false`、`verified=false`、`undoAvailable=true`、磁盘 Revision 未变），不伪装成功；`memoryTaskEvidence` 以 `cancelled` 结论记录“未持久化，可保存或撤销”，并明确 `independentReload=false`，因为该分支没有启动独立重载。下一步提示继续授权保存或 Undo/Discard；成功回退会直接关闭待处理 Live Apply 记录。
- 若包干净但未经过授权保存（外部保存/手工撤销/Session 分叉）：拒绝 `live-write-verify-save-unauthorized` 并要求重新 Plan。
- 若已授权保存：独立 Unreal 进程重载磁盘资产；Data Asset、Material Instance 与 DataTable 复用 `RunAssetCatalog.ps1`，Blueprint apply report 则复用既有 `RunExport.ps1 -Profile full -Format json -IncludeUnchangedDefaults`，同时保留 defaults/components 与 graph/pin Canonical。验证校验导出的资产路径、干净的 SHA-256 Revision 与磁盘一致、Revision 相对冻结索引已变化，并按 Kind 提取导出值与应用时 `afterValue` 做 JSON 级比对。DataTable Rename 按 `newRowName` 提取目标行，其余 DataTable 操作按 `rowName`；通过后返回 `state=verified`、`actualRevision`、`expectedValue`/`exportedValue`、`undoAvailable=false`，并生成独立重载 `memoryTaskEvidence`。
- `memoryRecorded=false` 恒定：Memory Task Record 由 `ue_memory_record_task` 落库，其失败会如实报错，本 Tool 从不声称 Memory 已写入。

该 Tool 不执行 Save All、不自动保存、不保存非授权资产、不让 Memory 反向覆盖源资产；本身不写磁盘、不修改 SQLite/Revision Export。

### Change Set

Change Set 是任务级 Live Write 容器，不替代单次 Plan、Transaction、Receipt、Save 或 Verify。启用 Workflow 时额外注册：

```text
ue_create_change_set(title, task_id="")
ue_get_change_set(change_set_id)
```

创建结果包含固定 `changeSetId`、`taskId`、`editorSessionId`、`title` 和 `status=planned`。以下既有 Tool 接受可选 `change_set_id`：

```text
ue_apply_asset_property_live
ue_undo_asset_property_live
ue_discard_asset_property_live
ue_save_authorized_asset
ue_verify_live_write
ue_apply_patch
ue_verify_asset
```

传入 Change Set 后，每个 Apply 会绑定其 `planId/assetPath/operation/transactionId/editorSessionId/liveApplyReceipt`。后续 Undo、Discard、Save 和 Verify 必须属于同一 Change Set，不能借用其他任务的 Receipt。

持久化 schema v2 保留完整 Operation 历史，状态包括：

```text
planned
applied
partially_applied
undone
discarded
saved
verified
no-op
failed
unknown
```

`ue_get_change_set` 还返回 `affectedAssets`、`transactionIds`、`validation` 和 `saveState` 聚合。成功 Undo/Discard/Verify 不删除历史 Operation；Server 重启后，无法用当前 Editor Session 和 Live Journal 重新证明的运行时状态标记为 `unknown`。最多保留 50 个 Change Set、每个最多 100 个 Operation；容量清理只删除终态记录，若全部仍活跃则拒绝新建，不静默丢失活跃任务。

`ue_apply_patch` 与 `ue_verify_asset` 的可选 `change_set_id` 为 commandlet workflow bridge，必须绑定同一 Plan/Apply receipt/资产。expected no-op 使用 `noop_*` Operation ID，公共 payload 返回 `noOp=true`；它是终态但没有伪造的 LiveApply receipt、transaction、journal、save 或 verify。Change Set journal 自身的持久化结果与 Live journal 分开报告。

`ue_set_material_parameter.parameter_type` 仅接受 `Scalar`、`Vector`、`Texture` 或 `StaticSwitch`，Server 映射到现有四个已注册 Operation。高层 Tool 只覆盖当前稳定 Operation；`ue_plan_patch` 继续保留，供已注册但尚无高层封装的 Operation 使用。

Live Write 稳定错误包括 `live-editor-write-disabled`、`live-editor-required`、`live-editor-write-confirmation-required`、`live-editor-write-not-allowed`、`live-editor-write-operation-unsupported`、`live-editor-write-asset-not-loaded`、`live-editor-write-asset-not-open`、`live-editor-write-blueprint-unsupported`、`live-editor-write-package-invalid`、`live-editor-write-package-dirty`、`live-editor-write-property-not-found`、`live-editor-write-property-not-editable`、`live-editor-write-property-type-unsupported`、`live-editor-write-value-invalid`、`live-editor-write-material-instance-required`、`live-editor-write-material-parameter-invalid`、`live-editor-write-material-parameter-not-found`、`live-editor-write-material-texture-invalid`、`live-editor-write-material-apply-failed`、`live-editor-write-data-table-required`、`live-editor-write-data-table-row-invalid`、`live-editor-write-data-table-row-not-found`、`live-editor-write-data-table-row-exists`、`live-editor-write-data-table-field-unsupported`、`live-editor-write-data-table-value-invalid`、`live-editor-write-data-table-row-referenced`、`live-editor-write-data-table-apply-failed`、`live-editor-write-undo-not-found`、`live-editor-write-undo-transaction-mismatch`、`live-editor-write-undo-session-mismatch`、`live-editor-write-undo-asset-mismatch`、`live-editor-write-undo-stack-mismatch`、`live-editor-write-undo-package-saved`、`live-editor-write-undo-target-changed`、`live-editor-write-undo-failed`、`live-editor-write-undo-verify-failed`、`live-write-verify-not-found`、`live-write-verify-save-unauthorized`、`live-editor-write-verify-not-loaded`、`live-write-verify-export-failed`、`live-write-verify-export-invalid`、`live-write-verify-revision-mismatch`、`live-write-verify-revision-unchanged`、`live-write-verify-value-mismatch` 和 `live-editor-write-apply-failed`。

### Live Write 回归分层

日常快速门禁运行 Scalar、Undo/Discard、Closed Loop 三组：

```bat
scripts\TestMcpLiveWriteFast.cmd -EngineRoot "<UE_5.6>" -ProjectPath "<TEST_PROJECT>.uproject"
```

阶段或发布前完整门禁运行 Scalar、Reference、Structured、Material、DataTable、Undo/Discard、Closed Loop 七组：

```bat
scripts\TestMcpLiveWriteRegression.cmd -EngineRoot "<UE_5.6>" -ProjectPath "<TEST_PROJECT>.uproject" -Suite Full
```

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

## R3 Verification Plan 与 Trust Verdict

Query 组新增两个全模式只读 Tool：

```text
ue_build_verification_plan
ue_evaluate_trust_verdict
```

两者都要求显式 `change_set_id`，并共享严格参数：`impact_depth=0..2`、最多 8 个 exact `required_automation_tests`、最多 8 个 exact `/Game` `extra_validation_assets`、`max_output_tokens=256..32768`。不接受自动 Change Set discovery、项目/数据库/Evidence 路径、任意 Assertion DSL 或任意 Evidence JSON。

Plan 输出包含确定性 `planId/planFingerprint`、scope、Assertions、risks 和 exact nextActions。Assertion family 固定为：

```text
persistence
semantic
freshness
compile
data-validation
reference-impact
automation
recovery
```

每条 Assertion 固定区分 `required/recommended/informational`、`pass/fail/unknown/not-applicable` 和 Evidence applicability。Stable ID 与排序由 rule version、Change Set、kind、subject 和 requirement 决定。

Evaluator 重新生成同一 Plan，只消费当前适用 Evidence，不执行 Compile、Validate、Automation、Save、Verify、Rollback 或 Writer。Verdict 规则固定：Required FAIL → `failed`；Required UNKNOWN 或 blocking risk → `insufficient-evidence`；Required 全关闭但 Recommended unresolved/non-blocking risk 存在 → `suspicious`；否则 → scoped `verified`。响应始终报告 verification scope 与 runtime/visual/performance/network/external/runtime-trace 等未覆盖维度；`verified` 不表示普遍正确。

0.8 reliability guidance 固定以下 Evidence Ladder；它是 `nextActions` 协议，不是 Server 内自动编排：

```text
live write applied
  -> ue_save_authorized_asset
persisted but not independently verified
  -> ue_verify_live_write / ue_verify_asset
verified persistence, semantic not checked
  -> ue_analyze_semantic_diff(stage=verified)
semantic state known, obligations unknown
  -> ue_build_verification_plan
required compile / validation / automation unknown
  -> run the exact registered action tool
required assertions closed
  -> ue_evaluate_trust_verdict
```

Persistence verified、独立 reload 或 Semantic Diff clean 均不能单独升级为整体 success。stale/dirty/policy block、Required FAIL/UNKNOWN 或 scoped Trust 未关闭时，Agent 必须保持 blocked/failed/insufficient-evidence 对应语义，不能因为“成功识别阻断”而输出 success。

R3 直接复用 R2 Semantic Diff 和 R1 Impact Analysis，不复制 Diff Adapter 或 Reference Graph。真实写入要求 verified semantic/persistence/freshness；expected no-op 使用 R2 persisted baseline exact-revision 特例，不伪造 Save/Verify。reference-sensitive operation 固定为 `setAssetReferenceProperty/removeDataTableRow/renameDataTableRow`，其 bounded R1 scope 为 Required，并可将最多 8 个 direct Blueprint consumers 升级为 Required Compile。

### Session-local Evidence Capture

Workflow session 的 `VerificationEvidenceStore` 仅由已注册 Tool wrapper 捕获：

```text
ue_compile_blueprint
ue_validate_asset
ue_validate_folder
ue_run_automation_test
```

契约为 `persistent=false / arbitraryIngest=false / projectBound=true / bounded=true`，最多 256 条。Compile capture 补固定项目 action 前后 disk SHA-256、Session 与 Dirty；Validation/Automation 保留 Validation Evidence 1.0。Automation 的 `revisionCoverage=not-applicable` 只证明 exact test 的 fixed project/session execution，不证明 asset Revision。Server restart 后 Store 丢失，Trust 必须返回 UNKNOWN/insufficient evidence，不能凭旧 receipt 重建 PASS。允许复用独立 Canonical/Revision persistence evidence，并在新 Session 重跑可安全重复的 exact Compile/Validation/Automation action；不允许导入任意 Evidence JSON。

R3 bounds：affected assets≤8、Assertions≤128、Evidence refs≤128、impact depth≤2。Token 裁剪优先移除 Evidence details、optional assertion details、informational Assertions 和 non-blocking risk messages，保留 Verdict、Required FAIL/UNKNOWN、blocking risk、scope 与 nextActions。

`ue_get_capabilities.verificationTrust` 和 `ue_get_project_status.verificationTrust` 暴露上述只读、deterministic、`modelInference=false`、auto-execute=false 与 Evidence Capture 边界。当前 Tool Count 契约：Offline 10、Offline+Memory 22、Live 43、Live+Memory 55、Workflow 60、Workflow+Memory 72、Live+Workflow 93、Combined+Memory 105。

## 分页与输出预算

- continuation token 是分页状态，不是 API Key、登录 Token 或模型 Token。
- Token 为当前 Server 会话生成的不透明随机值，不暴露查询文本和本机路径。
- Token 绑定 Tool、固定 SQLite 快照和原始查询参数，不能跨 Tool、跨索引快照或跨 Server 重启复用。
- `offset` 继续保留兼容性，但 Agent 应优先使用 continuation token。
- 查询响应返回 `hasMore`、兼容字段 `mayHaveMore`、`continuationToken` 和 `source`。
- `outputBudget` 返回 `maxTokens`、`estimatedTokens`、`truncated` 和 `truncationReason`。
- 截断原因包括 `page-limit`、`section-limit`、`token-budget` 和 `single-result-exceeds-token-budget`。

## Client 兼容契约

0.7.0 提供 `scripts\TestMcpClients.cmd`，通过两个独立真实 `stdio` 会话验证：

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

使用独立 Unreal Editor 进程重新导出目标资产，并核对 Object Path 与 Commit 后 SHA-256 Revision。非 Blueprint 使用 `RunAssetCatalog.ps1`；Blueprint apply report 使用既有 `RunExport.ps1` 的 `full/json/IncludeUnchangedDefaults` 路由，确保同一 Canonical 能验证变量 defaults、component overrides 与 pin defaults。该 Tool 不修改项目资产，但会在固定 Work Root 写验证报告，因此 MCP Annotation 不是纯 read-only。

### `ue_rollback_patch`

分两阶段：

1. 默认 `mode=DryRun`，验证 Manifest、Policy、当前 Revision 和备份完整性，返回一次性 `rollbackDryRunReceipt`。
2. `mode=Commit` 要求 Receipt 和精确 `ROLLBACK <applyReceipt>`，执行原子恢复并由独立 UE 进程验证恢复后的 Revision。

Blueprint saved-revision rollback 在 Editor 进程存在时额外 fail closed。只有 Bridge 对目标精确返回以下全部事实，才允许进入恢复：

```text
loaded=false
packageDirty=false
openInAssetEditor=false
state=not-loaded
```

任一事实缺失、unknown 或不满足时拒绝 rollback；不得根据进程存在、窗口不可见、资产名或磁盘状态推断内存对象安全。恢复仍要求 Manifest/Policy/current Revision/backup digest/receipt 全部匹配，并在 Commit 后用独立 UE 重载验证 exact restored Revision。

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
