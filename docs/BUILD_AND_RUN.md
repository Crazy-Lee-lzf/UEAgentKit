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

## 9. 校验与执行 Blueprint Patch

列出操作：

```bat
scripts\ue-agent.cmd patch operations
```

只读预校验：

```bat
scripts\ue-agent.cmd patch validate ^
  --patch <PATCH_JSON> ^
  --policy <POLICY_JSON> ^
  --export <BLUEPRINT_EXPORT> ^
  --report <VALIDATION_REPORT>
```

统一执行入口：

```bat
scripts\RunPatch.cmd ^
  -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject" ^
  -Patch "<PATCH_JSON>" ^
  -Policy "<POLICY_JSON>" ^
  -RevisionExport "<BLUEPRINT_EXPORT>" ^
  -Mode DryRun
```

提交模式：

```bat
scripts\RunPatch.cmd ^
  -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject" ^
  -Patch "<PATCH_JSON>" ^
  -Policy "<POLICY_JSON>" ^
  -RevisionExport "<BLUEPRINT_EXPORT>" ^
  -Mode Commit ^
  -Report "Output\Patch\commit-report.json" ^
  -BackupDir "Backups\Patches"
```

执行顺序固定为：Python 预校验 → 单资产/单操作约束 → 加载 Blueprint → 再次检查 Policy 与磁盘 Revision → 修改 → 编译 → Dry Run 回滚或 Commit 备份并保存。

当前限制：

- 每次一个 Blueprint、一个 Operation。
- 支持 `setVariableDefault`、`setComponentProperty`、`setPinDefault`。
- 变量和组件属性支持 Bool、整数、浮点、String、Name、Text。
- Pin 支持未连接、可编辑的输入 Pin，值为布尔、数值或字符串。
- 不支持数组、Set、Map、对象引用和 Blueprint 结构性增删。

## 10. 校验输出

通用资产目录：

```bat
python <TOOL_ROOT>\scripts\ValidateAssetCatalog.py ^
  --output <TOOL_ROOT>\Output\AssetCatalog ^
  --expect-exporter 0.3.1
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

## 11. 安全行为

通用资产目录、Blueprint 导出、SQLite 查询和 `ue-agent patch validate` 保持只读。`RunPatch -Mode DryRun` 会在内存中修改并编译 Blueprint，但必须回滚且保持磁盘 SHA-256 不变。

只有 `RunPatch -Mode Commit` 可以保存资产，并且必须满足：Policy 显式允许 Commit、项目/目录/类型/操作均授权、Revision 一致、Package 非 Dirty、备份创建成功且 Blueprint 编译成功。写入通过 Unreal Editor API 完成，不直接修改 `.uasset` 二进制。

## 12. 清理

可以安全清理：

```text
Build\DirectHost\
Build\Compiled\
Output\
```

删除 Junction 时只删除链接本身，不要递归删除目标目录。
