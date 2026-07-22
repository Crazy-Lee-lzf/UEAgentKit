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

执行顺序固定为：Python 预校验 → 单资产/单操作约束 → 按 Operation 选择 Commandlet → 加载资产 → 再次检查 Policy 与磁盘 Revision → 修改 → Dry Run 回滚或 Commit 备份并保存。Blueprint 操作额外执行编译；通用属性、Material 参数和 DataTable 字段分别要求精确 PropertyPath、参数和 RowStruct/字段白名单。

当前限制：

- 每次一个资产、一个 Operation。
- Blueprint 支持 `setVariableDefault`、`setComponentProperty`、`setPinDefault`、`setBlueprintDescription`。
- 非 Blueprint 支持 `setAssetProperty`；Policy 必须用 `AssetClass#Property.Path` 精确授权。
- Material Instance 支持 `setMaterialInstanceScalarParameter`、`setMaterialInstanceVectorParameter`、`setMaterialInstanceTextureParameter` 和 `setMaterialInstanceStaticSwitchParameter`；Policy 使用 `AssetClass#Type#ParameterName` 精确授权。
- DataTable 支持 `setDataTableCell`；Policy 使用 `AssetClass#RowStructPath#FieldName` 精确授权，首版仅修改现有 Row 的一个顶层标量字段。
- 变量和组件属性支持 Bool、整数、浮点、String、Name、Text。
- Pin 支持未连接、可编辑的输入 Pin，值为布尔、数值或字符串。
- 已验证普通 Blueprint、Widget、Anim、Actor Component、Function Library、Macro Library、Interface 和 Control Rig。
- 已验证 PrimaryAssetLabel/Data Asset、Texture2D、Static Mesh 和 InputAction；支持用点号进入嵌套 Struct 和普通 Enum 名称写入。
- 已验证 MaterialInstanceConstant 的 Global Scalar、Vector、Texture 与 Static Switch 参数 Dry Run、完整 Override/Static Parameter 回滚、Commit、备份和独立重载。Texture 引用额外要求 `allowedReferenceRoots` 与 `allowedReferenceClasses`；Static Switch 同时验证 Expression GUID 与 Override 状态。
- 已验证 DataTable `GameplayTagTableRow.DevComment` 的整 Row Dry Run 回滚、Commit、唯一备份、独立重载和过期 Revision 拒绝。
- 通用属性仅允许可编辑、非 Transient 的 Bool、数值、String、Name、Text 或 Enum；不支持数组、Set、Map、对象引用和 Blueprint 结构性增删。
- 当前仅接受没有 `.uexp/.ubulk/.uptnl/.m.ubulk/.upayload` 等独立侧文件的单文件 Package。

## 10. Backup Manifest 与 Rollback

`RunPatch -Mode Commit` 成功后会自动创建 `<backup>.manifest.json`。Manifest 位于 `BackupDir` 内，记录：

- Patch、Policy 和 Commit Report 的 SHA-256。
- Asset Path、Asset Class、Operation、Target 与精确授权键。
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
  --expect-exporter 0.4.4
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

## 14. 只读 MCP Server（0.5.0 开发中）

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

运行真实 MCP Client stdio 握手、Tool 发现和只读哈希验证：

```bat
scripts\TestMcpStdio.cmd
```

Claude Code 项目级接入：

```bat
claude mcp add --transport stdio --scope project ue-agent-kit -- ^
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File ^
  "<TOOL_ROOT>\scripts\RunMcp.ps1" ^
  -Database "<TOOL_ROOT>\.data\ue_agent_kit.sqlite3"
```

当前只暴露三个只读 Tool：

```text
ue_search
ue_get_asset
ue_find_references
```

服务器启动时固定数据库路径，Tool 参数不能更换数据库；SQLite 以 `mode=ro&immutable=1` 打开，不执行 Migration，也不会创建 `-wal/-shm`。若检测到活动 `-wal`、`-shm` 或 `-journal`，服务器拒绝查询。重建索引前先停止 MCP Server，写入完成并关闭连接后再重启。完整响应和上限见 [`../spec/MCP_SERVER.md`](../spec/MCP_SERVER.md)。

## 15. 安全行为

通用资产目录、Blueprint 导出、SQLite 查询、当前 MCP Tool 和 `ue-agent patch validate` 保持只读。`RunPatch -Mode DryRun` 会在内存中修改资产，但必须恢复原值并保持磁盘 SHA-256 不变；Blueprint 还会在修改和回滚后编译。

只有 `RunPatch -Mode Commit` 可以保存资产，并且必须满足：Policy 显式允许 Commit、项目/目录/类型/操作均授权、属性或参数精确授权、Revision 一致、Package 非 Dirty、备份创建成功，且 Blueprint 编译成功。成功后自动生成 Backup Manifest。`RunRollback -Mode Commit` 是唯一恢复入口，默认 Dry Run，并要求工程关闭、当前 Revision 未变化、备份完整和独立 UE 重载验证。Patch 写入通过 Unreal Editor API 完成；rollback 仅按已验证 Manifest 原子恢复单文件 Package。

## 16. 清理

可以安全清理：

```text
Build\DirectHost\
Build\Compiled\
Output\
```

删除 Junction 时只删除链接本身，不要递归删除目标目录。
