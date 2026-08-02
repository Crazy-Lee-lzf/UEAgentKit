# 构建与运行

## 1. 环境要求

```text
Windows 10/11
Unreal Engine 5.6
Visual Studio C++ Toolchain
PowerShell 5.1+
Python 3.11 或 3.12
```

UE Agent Kit 是 Editor-only C++ 插件，不用于打包后的游戏运行时。

## 2. 路径约定

```text
<TOOL_ROOT>     UEAgentKit 根目录
<UE_ROOT>       Unreal Engine 5.6 安装目录
<PROJECT_ROOT>  目标 UE 项目目录
```

## 3. 构建插件

本机路径可以自动识别时：

```bat
<TOOL_ROOT>\scripts\BuildPluginDirect.cmd
```

显式指定引擎和 MSVC：

```powershell
powershell.exe -ExecutionPolicy Bypass -File <TOOL_ROOT>\scripts\BuildPluginDirect.ps1 `
  -EngineRoot "<UE_ROOT>" `
  -MsvcToolsRoot "<MSVC_TOOLS_ROOT>"
```

构建输出：

```text
<TOOL_ROOT>\Build\Compiled\UEAgentKit
```

成功时应存在：

```text
Build\Compiled\UEAgentKit\Binaries\Win64\UnrealEditor-UEAgentKitEditor.dll
```

## 4. 安装到项目

推荐建立项目级 Junction：

```text
<PROJECT_ROOT>\Plugins\UEAgentKit
→ <TOOL_ROOT>\Build\Compiled\UEAgentKit
```

并在 `.uproject` 中启用：

```json
{
  "Name": "UEAgentKit",
  "Enabled": true,
  "TargetAllowList": ["Editor"]
}
```

修改 `.uproject` 前先备份。Junction 仅用于本地安装，不应进入 Git 或 P4。

## 5. 验证插件加载

启动编辑器或 `UnrealEditor-Cmd.exe`，日志中应出现：

```text
Mounting Project plugin UEAgentKit
```

## 6. 导出通用资产目录

批量导出 `/Game` 下的非 Blueprint 资产：

```bat
<TOOL_ROOT>\scripts\RunAssetCatalog.cmd ^
  -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject" ^
  -Root "/Game" ^
  -Output "<TOOL_ROOT>\Output\AssetCatalog"
```

导出单个资产：

```bat
<TOOL_ROOT>\scripts\RunAssetCatalog.cmd ^
  -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject" ^
  -Asset "/Game/Environment/SM_Wall" ^
  -Output "<TOOL_ROOT>\Output\SingleAsset"
```

常用参数：

```text
-Asset              单个资产路径
-Root               批量扫描的 Package 根路径
-Output             输出目录
-IncludeBlueprints  同时输出 Blueprint 的通用记录
-IncludeGenerated   包含 World Partition 外部 Actor/Object 包
-NoTags             不输出 Asset Registry Tags
-CompactJson        使用紧凑 JSON
```

默认排除 Blueprint，是因为 Blueprint 应通过深度导出获取更完整的 Symbol、Graph 和 Reference。默认排除外部 Actor/Object 包，是为了避免 World Partition 生成记录显著扩大索引。

输出结构：

```text
AssetCatalog\
├─ manifest.json
└─ canonical\
```

通用记录包含：

- Asset Path、Asset Name、Asset Class。
- Package Name、Package Path、Package Flags、Chunk ID。
- Asset Registry Tags。
- 包文件大小、修改时间和 SHA-256 Revision。
- Hard/Soft Package、Manage 和 Searchable Name 依赖边。

已注册的专用 Reader 会把稳定字段写入 `assetDetails`；未知类型继续使用通用 Asset Registry 记录。Reader Registry 通过 Asset Class 分发表选择实现；具体代码按 Mesh、Material、Animation/Data、Niagara 和 World 分组，新增 Reader 不需要继续扩大中心分发文件。当前覆盖 Static Mesh、Skeletal Mesh、Skeleton、Physics Asset、Material、Material Instance、Material Function、Texture2D、Anim Sequence、Anim Montage、Blend Space/Aim Offset、DataTable、通用 Data Asset 派生资产、Niagara System 和 World。Reader 明确不导出顶点、蒙皮权重、RenderData、Shader Bytecode、纹理像素、Chaos 模拟缓存或大型 BulkData；Texture2D 会区分 Source 元数据与 NullRHI 下可能不可用的 Platform Data。

## 7. 导出 Blueprint 语义

单个 Blueprint：

```bat
<TOOL_ROOT>\scripts\RunExport.cmd ^
  -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject" ^
  -Asset "/Game/Folder/BP_Name" ^
  -Profile logic ^
  -Format both
```

批量导出：

```bat
<TOOL_ROOT>\scripts\RunExport.cmd ^
  -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject" ^
  -Root "/Game" ^
  -Profile full ^
  -Format both ^
  -Output "<TOOL_ROOT>\Output\Blueprints"
```

Blueprint 参数：

```text
-Asset    单个 Blueprint 路径
-Root     批量扫描根路径
-Output   输出目录
-Profile  index|structure|logic|defaults|full|ai
-Format   json|bpctx|both
-Graph    只导出指定 Graph
```

## 8. 建立统一 SQLite 索引

先导入通用资产目录，再导入 Blueprint 深度结果：

```bat
cd /d <TOOL_ROOT>
scripts\ue-agent.cmd index build Output\AssetCatalog
scripts\ue-agent.cmd index build Output\Blueprints
scripts\ue-agent.cmd index stats
```

相同 Asset Path 的深度 Blueprint 记录优先级高于通用记录，不会被 `asset-index` 覆盖。

查询资产：

```bat
scripts\ue-agent.cmd search assets Door
scripts\ue-agent.cmd search assets --class StaticMesh
scripts\ue-agent.cmd search assets Manny --class Texture2D
```

查询 Blueprint Symbol：

```bat
scripts\ue-agent.cmd search symbols MaxWalkSpeed
```

查询依赖或反向引用：

```bat
scripts\ue-agent.cmd references --target-asset /Game/Environment/SM_Wall.SM_Wall
scripts\ue-agent.cmd references --asset /Game/Characters/BP_Player.BP_Player
```

## 9. 校验与执行 Patch

列出操作：

```bat
scripts\ue-agent.cmd patch operations
```

只读预校验：

```bat
scripts\ue-agent.cmd patch validate ^
  --patch <PATCH_JSON> ^
  --policy <POLICY_JSON> ^
  --export <REVISION_EXPORT> ^
  --report <VALIDATION_REPORT>
```

统一执行入口：

```bat
scripts\RunPatch.cmd ^
  -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject" ^
  -Patch "<PATCH_JSON>" ^
  -Policy "<POLICY_JSON>" ^
  -RevisionExport "<REVISION_EXPORT>" ^
  -Mode DryRun
```

提交模式：

```bat
scripts\RunPatch.cmd ^
  -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject" ^
  -Patch "<PATCH_JSON>" ^
  -Policy "<POLICY_JSON>" ^
  -RevisionExport "<REVISION_EXPORT>" ^
  -Mode Commit ^
  -Report "Output\Patch\commit-report.json" ^
  -BackupDir "Backups\Patches"
```

执行顺序固定为：Python 预校验 → 单资产/1–32 Operation 约束 → 确认全部 Operation 属于同一 Commandlet → 加载资产 → 再次检查 Policy 与磁盘 Revision → 预校验全部目标 → 顺序修改 → Dry Run 进程丢弃或 Commit 创建一次备份并保存一次。Blueprint 事务只编译一次；通用属性、Material 参数和 DataTable 字段仍分别要求精确 PropertyPath、参数和 RowStruct/字段白名单。

当前限制：

- 每次一个资产、1–32 个兼容 Operation；多 Operation 拒绝重复目标，且 DataTable Row 新增/删除/重命名必须单独执行。
- Blueprint 支持 `setVariableDefault`、`setComponentProperty`、`setPinDefault`、`setBlueprintDescription`。
- 非 Blueprint 标量属性使用 `setAssetProperty`；Data Asset Object/Class 与 Soft Object/Class 引用使用 `setAssetReferenceProperty`；顶层 Struct、Array、Set、Map 使用 `setAssetStructuredProperty`。三者都必须用 `AssetClass#Property.Path` 精确授权。
- Material Instance 支持 `setMaterialInstanceScalarParameter`、`setMaterialInstanceVectorParameter`、`setMaterialInstanceTextureParameter` 和 `setMaterialInstanceStaticSwitchParameter`；Policy 使用 `AssetClass#Type#ParameterName` 精确授权。 四类报告统一使用原生 JSON 值、Override、Expression GUID、结构化 `materialParameter` Diff，以及值/元数据/数组结构三层 Dry Run 恢复门禁；完整回归运行 `scripts\TestMaterialInstanceParameters.cmd`。
- DataTable 支持 `setDataTableCell`；Policy 使用 `AssetClass#RowStructPath#FieldName` 精确授权，首版仅修改现有 Row 的一个顶层标量字段。
- 变量和组件属性支持 Bool、整数、浮点、String、Name、Text。
- Pin 支持未连接、可编辑的输入 Pin，值为布尔、数值或字符串。
- 已验证普通 Blueprint、Widget、Anim、Actor Component、Function Library、Macro Library、Interface 和 Control Rig。
- 已验证 PrimaryAssetLabel/Data Asset、Texture2D、Static Mesh 和 InputAction；支持用点号进入嵌套 Struct 和普通 Enum 名称写入。
- 已验证 MaterialInstanceConstant 的 Global Scalar、Vector、Texture 与 Static Switch 参数 Dry Run、完整 Override/Static Parameter 回滚、Commit、备份和独立重载。Texture 引用额外要求 `allowedReferenceRoots` 与 `allowedReferenceClasses`；Static Switch 同时验证 Expression GUID 与 Override 状态。
- 已验证 DataTable `GameplayTagTableRow.DevComment` 的整 Row Dry Run 回滚、Commit、唯一备份、独立重载和过期 Revision 拒绝。
- 通用 `setAssetProperty` 仅允许可编辑、非 Transient 的 Bool、数值、String、Name、Text 或 Enum；不支持数组、Set、Map、对象引用和 Blueprint 结构性增删。
- Data Asset 对象/类引用必须使用专用 `setAssetReferenceProperty`：仅顶层 Object、Class、Soft Object、Soft Class，值为 `null` 或精确 `{referenceType, path}`，并受 `allowedAssetProperties`、`allowedReferenceRoots`、`allowedReferenceClasses` 三层授权。
- Data Asset Struct/容器必须使用 `setAssetStructuredProperty`：仅顶层 Struct、Array、Set、Map；Reader 导出递归 Schema，Struct 要求完整字段，Set/Map 按 Canonical JSON 唯一排序。当前支持 Bool、32 位以内整数、Float/Double、String、Name 和 Enum 叶子，不允许对象引用叶子。
- 已通过真实 UE5.6 Struct/Array/Set/Map Dry Run、Commit、独立重载、结构化 Diff 和四层逆序 rollback。
- 当前仅接受没有 `.uexp/.ubulk/.uptnl/.m.ubulk/.upayload` 等独立侧文件的单文件 Package。
- 多 Operation 事务回归运行 `scripts\TestMultiOperationTransactions.cmd`，覆盖 Data Asset 与 Blueprint 的 Dry Run、一次备份、一次保存、独立重载、Manifest 与整体 rollback。

## 10. Backup Manifest 与 Rollback

`RunPatch -Mode Commit` 成功后会自动创建 `<backup>.manifest.json`。Manifest 位于 `BackupDir` 内，记录：

- Patch、Policy 和 Commit Report 的 SHA-256。
- Asset Path、Asset Class、Operation、Target 与精确授权键；多 Operation Manifest 额外记录 `operationCount` 和逐 Operation `operations[]/authorizationKeys[]`。
- Commit 前后 Package Revision。
- 备份相对路径、Revision 和文件大小。

默认恢复仅校验：

```bat
scripts\RunRollback.cmd ^
  -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject" ^
  -Manifest "<BACKUP_MANIFEST>" ^
  -Policy "<POLICY_JSON>" ^
  -BackupRoot "<BACKUP_ROOT>" ^
  -Mode DryRun ^
  -Report "Output\Rollback\dryrun-report.json"
```

显式恢复：

```bat
scripts\RunRollback.cmd ^
  -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject" ^
  -Manifest "<BACKUP_MANIFEST>" ^
  -Policy "<POLICY_JSON>" ^
  -BackupRoot "<BACKUP_ROOT>" ^
  -Mode Commit ^
  -Report "Output\Rollback\commit-report.json"
```

Rollback Commit 固定执行：

1. 确认目标 UE 工程未在 Editor 或 Commandlet 中打开。
2. 重新校验 Manifest 位置、Policy 文件哈希和当前精确授权。
3. 要求当前 Package Revision 仍等于 Manifest 的 `afterRevision`。
4. 要求备份 Revision、文件大小和 `beforeRevision` 完全一致。
5. 拒绝 Sidecar Package；当前只恢复单 `.uasset`。
6. 先复制当前包到 `rollback-safety`，再用临时文件原子替换。
7. 写入唯一 rollback receipt；审计输出失败时自动恢复安全副本。
8. 启动独立 UE 进程重新导出资产，并核对恢复后的 Revision 与 Dirty 状态。

完整格式见 [`../spec/BACKUP_AND_ROLLBACK.md`](../spec/BACKUP_AND_ROLLBACK.md)。

## 11. Write Fixture Plan

用于写入回归的测试资产可以通过声明式 Plan 重建：

```bat
scripts\RunWriteFixturePlan.cmd ^
  -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject" ^
  -Plan "tests\fixtures\write_fixture_plan.example.json" ^
  -Mode Reset
```

执行链为：Python 只读预校验 → UE Commandlet 全量预校验 → 仅清理 Plan 明列目标 → 创建/复制并保存 → 第二个 UE 进程重新导出 → 精确验证类、Revision 和 Dirty 状态。

`Create` 拒绝既有目标；`Reset` 只删除 `fixtures[].targetAsset`，不会递归删除 Root。当前支持 `duplicateAsset`、`scalarAsset` 与 `blueprint`，且只接受没有 Sidecar 的单文件 Package。完整规范见 [`../spec/WRITE_FIXTURE_PLAN.md`](../spec/WRITE_FIXTURE_PLAN.md)。

## 12. Scalar Patch Regression

完整真实 UE 标量回归：

```bat
scripts\RunScalarPatchRegression.cmd ^
  -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject"
```

脚本使用插件原生 `UEAgentKitScalarWriteFixtureAsset`，固定覆盖 Bool、Byte、Int32、Int64、Float、Double、String、Name、Text、`FEnumProperty` 和 enum-backed Byte Property。

执行内容：

- 11 次独立 Dry Run，验证回滚值、磁盘 Revision 和文件 SHA-256 不变。
- 11 次顺序 Commit，每次验证备份、Manifest、独立 UE 重载和累计属性值。
- 正常完成时最终 Reset 回默认值。
- 9 次预期失败：未授权、过期 Revision、错误 JSON 类型、Byte 越界、非法 Enum、目标 Property 不存在、Dirty Package、真实 Package Sidecar、注入的保存失败。
- 所有失败均验证 `.uasset` 哈希和最终 Canonical Revision 不变。

输出默认位于 `Output\ScalarPatchRegression`，其中 `summary.json` 汇总所有报告。完整规范见 [`../spec/SCALAR_PATCH_REGRESSION.md`](../spec/SCALAR_PATCH_REGRESSION.md)。

## 13. 校验输出

通用资产目录：

```bat
python <TOOL_ROOT>\scripts\ValidateAssetCatalog.py ^
  --output <TOOL_ROOT>\Output\AssetCatalog ^
  --expect-exporter 0.7.0
```

校验器会检查：

- Manifest 与 Canonical 文件数量。
- Schema、Exporter、Project 和 Profile。
- Asset Symbol 与 Reference 唯一性。
- Registry 元数据和 Revision 格式。
- Summary 统计是否与实际数组一致。

Blueprint 输出至少应检查：

- Commandlet 退出码为 0。
- Manifest 的 Success/Failure 数量。
- Canonical JSON 可解析。
- BPCTX 第一行符合 `H|BPCTX|1|...`。

## 14. MCP Server（0.7.0）

安装 MCP SDK v1 可选依赖：

```bat
scripts\setup_python.cmd -WithMcp
```

验证固定 SQLite 数据库，不启动协议循环：

```bat
scripts\RunMcp.cmd ^
  -Database "<TOOL_ROOT>\.data\ue_agent_kit.sqlite3" ^
  -Check
```

启动本地 stdio Server：

```bat
scripts\RunMcp.cmd -Database "<TOOL_ROOT>\.data\ue_agent_kit.sqlite3"
```

启用独立持久化 Project Memory：

```bat
scripts\RunMcp.cmd ^
  -Database "<TOOL_ROOT>\.data\ue_agent_kit.sqlite3" ^
  -EnableProjectMemory ^
  -MemoryDatabase "<TOOL_ROOT>\.data\ue_agent_kit_memory.sqlite3"
```

Memory Database 可省略，默认使用 `<TOOL_ROOT>\.data\ue_agent_kit_memory.sqlite3`。该路径和索引中的 Project Key 在 Server 启动时固定，不出现在任何 MCP Tool 参数中。

运行官方 Python MCP Client 的 stdio 握手、Tool 发现和只读哈希验证：

```bat
scripts\TestMcpStdio.cmd
```

运行官方 SDK 与原始 JSON-RPC 双客户端兼容矩阵，检查 Tool Schema、annotations、`structuredContent`、JSON 文本回退和稳定错误 Envelope：

```bat
scripts\TestMcpClients.cmd
```

开发 MCP 子模块时优先运行对应专项测试，避免每次小改动都执行完整真实 UE 工作流：

```bat
scripts\TestMcpModules.cmd -Group Registry
scripts\TestMcpModules.cmd -Group Query
scripts\TestMcpModules.cmd -Group Live
scripts\TestMcpModules.cmd -Group Workflow
```

并行子任务使用预览优先的 Worktree 脚本；不带 `-Apply` 时只打印计划：

```bat
scripts\CreateAgentWorktrees.cmd
scripts\CreateAgentWorktrees.cmd -Apply
```

文件所有权、Sol/Luna 边界和测试层级见 [`PARALLEL_AGENT_DEVELOPMENT.md`](PARALLEL_AGENT_DEVELOPMENT.md)。

运行真实 UE5.6 Live Editor Bridge 联调；脚本使用临时 SQLite，启动并只关闭自己创建的测试 Editor：

```bat
scripts\TestMcpLiveEditor.cmd ^
  -EngineRoot "<UE_5.6>" ^
  -ProjectPath "<TEST_PROJECT>.uproject"
```

运行真实 UE5.6 Live Editor Write 闭环。脚本启动普通编辑器，在编辑器保持运行时执行 `Plan -> LIVE APPLY <planId>`，验证内存值、Dirty 和 Undo 事务，并强制要求磁盘 Package 与 SQLite 哈希保持不变：

```bat
scripts\TestMcpLiveWrite.cmd ^
  -EngineRoot "<UE_5.6>" ^
  -ProjectPath "<TEST_PROJECT>.uproject"
```

首版 Live Write 只支持已经打开、当前干净的非 Blueprint 资产顶层标量属性。应用后资产保持未保存状态，可在编辑器中检查或撤销；需要持久化时再调用现有授权保存流程。

连接已经运行且发布 Bridge Descriptor 的测试 Editor 时，可以加 `-UseExistingEditor`。不要对正式工程运行自动启动/关闭测试。

运行真实 UE5.6 配对快照刷新联调；脚本只使用隔离 Fixture，并在结束时精确恢复测试 Package，删除测试 Pointer、Generation 与备份目录：

```bat
scripts\TestMcpSnapshotRefresh.cmd ^
  -EngineRoot "<UE_5.6>" ^
  -ProjectPath "<TEST_PROJECT>.uproject"
```

Claude Code 项目级接入：

```bat
claude mcp add --transport stdio --scope project ue-agent-kit -- ^
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File ^
  "<TOOL_ROOT>\scripts\RunMcp.ps1" ^
  -Database "<TOOL_ROOT>\.data\ue_agent_kit.sqlite3"
```

默认只读模式暴露五个 Tool：

```text
ue_get_capabilities
ue_get_project_status
ue_search
ue_get_asset
ue_find_references
```

启用 `-EnableProjectMemory` 后额外注册：

```text
ue_memory_search
ue_memory_get
ue_memory_add_rule
ue_memory_record_finding
ue_memory_record_task
ue_memory_mark_superseded
ue_memory_validate
```

`ue_memory_record_task` 只记录已经形成最终结论的任务结果，并要求同时提供 Patch、Backup Manifest、Validation Evidence 和至少一个稳定 Revision。Artifact 引用必须是不可变 ID 或工程相对引用，不能传入绝对本机路径或 `..` 路径穿越。

服务器启动时固定数据库路径，Tool 参数不能更换数据库；SQLite 使用不可变只读快照并拒绝活动 Sidecar。`-EnableLiveEditor -ProjectPath <固定工程>` 会增加 10 个实时只读 Tool 和 8 个受限 Daily Action。Bridge 只绑定 `127.0.0.1`，使用随机会话令牌，并校验固定工程路径摘要、Plugin/Server 版本与 Capability；MCP 响应不暴露 Token、端口或 Descriptor 路径。未启用 Memory 时离线 5 Tool、Live 模式 23 Tool、工作流模式 26 Tool、组合模式 44 Tool 相互兼容；启用 Memory 后分别为 12、30、33、51。Output Log 最多返回 100 条并使用序号游标；实时资产检查和 Content Browser 同步不加载目标资产；Blueprint Graph 定位只支持普通 Blueprint Editor，并最多返回 100 个选中 Node。八个 Live Daily Action 仅接受精确 `/Game` 身份或当前 Editor World 的 ActorGuid，在 PIE/SIE 中拒绝运行且不保存 Package；工作流 Tool `ue_save_authorized_asset` 使用 Policy/Revision/Session 绑定的 Preview Receipt、备份、显式确认和独立验证保存一个授权资产；文件夹 Validation 最多匹配 500 个资产并最多返回 200 条问题。`ue_get_asset_state` 只读比较 Editor Memory、磁盘 Package、Revision Export 和 SQLite。高层写入入口默认只生成 Plan，也可执行 Dry Run，但不能直接 Commit。保存和恢复必须显式启用 Commit、通过 Policy，并提供一次性 Receipt 与精确确认短语。`ue_refresh_asset_index` 只接受一个精确授权资产和 Preview/Apply；它在固定 Work Root 中构建配对 Generation，验证后原子切换 Pointer，并要求新 MCP 会话加载新代。真实磁盘写入闭环使用 `scripts\TestMcpWorkflow.cmd`，编辑器内存写入闭环使用 `scripts\TestMcpLiveWrite.cmd`，真实刷新闭环使用 `scripts\TestMcpSnapshotRefresh.cmd`。完整契约见 [`../spec/MCP_SERVER.md`](../spec/MCP_SERVER.md) 与 [`../spec/LIVE_EDITOR_BRIDGE.md`](../spec/LIVE_EDITOR_BRIDGE.md)。

### Project Memory CLI

不启动 MCP 时，可以使用现有 `ue-agent` CLI 检查固定工程 Memory：

```bat
scripts\ue-agent.cmd memory status ^
  --memory-database ".data\ue_agent_kit_memory.sqlite3" ^
  --project-key "MyProject"

scripts\ue-agent.cmd memory search "player health" ^
  --memory-database ".data\ue_agent_kit_memory.sqlite3" ^
  --project-key "MyProject" ^
  --record-type taskRecord ^
  --scope-type asset ^
  --scope-key /Game/Characters/BP_Player.BP_Player

scripts\ue-agent.cmd memory get mem_0123456789abcdef0123456789abcdef ^
  --memory-database ".data\ue_agent_kit_memory.sqlite3" ^
  --project-key "MyProject"

scripts\ue-agent.cmd memory validate ^
  --memory-database ".data\ue_agent_kit_memory.sqlite3" ^
  --project-key "MyProject" ^
  --index-database ".data\ue_agent_kit.sqlite3"

scripts\ue-agent.cmd memory export ^
  --memory-database ".data\ue_agent_kit_memory.sqlite3" ^
  --project-key "MyProject" ^
  --output "Output\ProjectMemory\memory-audit.json"
```

`status`、`search` 和 `get` 在已初始化的当前 Schema 上不改变业务记录或状态；首次打开空库或旧 Schema 时会执行建库、Schema Migration 和必要的证据摘要回填。`validate` 只可能把 Revision 不匹配的记录持久化为 `stale`。`export` 不改变业务记录或状态，输出完整 Record、Status Event、双摘要与可重复 `snapshotSha256`，并且不写入 Memory DB 或 Index DB 的绝对路径。默认拒绝超过 10,000 条 Record 或 100,000 条 Status Event 的不完整导出。

进程级 CLI 回归：

```bat
scripts\TestMemoryCli.cmd
```

该回归使用独立临时数据库启动 `scripts\ue-agent.py`，并验证 Windows stdout/stderr 固定为 UTF-8、中文 Project Key、五个子命令、审计 CRLF 和路径脱敏。

Workflow 与 Memory 同时启用后，成功的 `ue_verify_asset` 和成功的 rollback Commit 都会返回 `memoryTaskEvidence.arguments`。该对象与 `ue_memory_record_task` 输入一致，应原样传递；它只包含脱敏 ID 和最终或恢复 Revision，不包含一次性 Receipt 或绝对路径。`scripts\TestMcpWorkflow.cmd` 会在真实 UE5.6 中同时启用 Workflow 与固定 Project Memory，持久化 succeeded/rolledBack Task，执行 Revision 失效与审计摘要校验，并确认测试 Package SHA-256 恢复且 immutable Index 未变化。

## 15. Release Validation 与 CI

发布前运行：

```bat
python scripts\ValidateRelease.py --require-release-docs
```

Validator 会统一检查版本来源、发布文档、Ruff、Python 全测、Schema、示例 Patch 和 Policy。`.github/workflows/release-validation.yml` 在 Python 3.11/3.12 上运行相同门禁并构建 wheel/sdist。GitHub Hosted Runner 不包含 Unreal Engine，因此 UE5.6 Direct Build、UAT Plugin Package 和真实资产回归仍是本地发布机门禁。

## 16. 安全行为

通用资产目录、Blueprint 导出、SQLite 查询、当前 MCP Tool 和 `ue-agent patch validate` 保持只读。`RunPatch -Mode DryRun` 会在内存中修改资产，但必须恢复原值并保持磁盘 SHA-256 不变；Blueprint 还会在修改和回滚后编译。

只有 `RunPatch -Mode Commit` 可以保存资产，并且必须满足：Policy 显式允许 Commit、项目/目录/类型/操作均授权、属性或参数精确授权、Revision 一致、Package 非 Dirty、备份创建成功，且 Blueprint 编译成功。成功后自动生成 Backup Manifest。`RunRollback -Mode Commit` 是唯一恢复入口，默认 Dry Run，并要求工程关闭、当前 Revision 未变化、备份完整和独立 UE 重载验证。Patch 写入通过 Unreal Editor API 完成；rollback 仅按已验证 Manifest 原子恢复单文件 Package。

## 17. 清理

可以安全清理：

```text
Build\DirectHost\
Build\Compiled\
Output\
```

删除 Junction 时只删除链接本身，不要递归删除目标目录。
