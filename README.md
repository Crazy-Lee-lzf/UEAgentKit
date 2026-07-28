# UE Agent Kit

[English](README_EN.md)

> 将 Unreal Engine 资产和 Blueprint 转换为 AI 与开发者可检索、可追踪的项目知识。

UE Agent Kit 是一套面向 Unreal Engine 的开源资产分析、索引与受控写入工具。它通过 UE Editor 插件导出项目资产目录、Asset Registry 元数据、依赖关系和 Blueprint 语义，再使用 Python CLI 与 SQLite 建立项目级索引，并通过 Policy、Revision、Dry Run 和备份保护显式写入。

当前已发布版本为 **0.5.5**，支持 **Unreal Engine 5.6**。本版本完成 0.5.x 日常开发能力收口：受限 Live Editor、Daily Actions、四源资产状态、授权保存与索引刷新、DataTable/Data Asset/Material 扩展写入、验证证据绑定、单资产多 Operation 原子事务，以及可重复 Release Validation 与 CI。

> **发布状态**：0.5.x 已完成。当前模式为 Offline 5、Live 23、Workflow 25、Combined 43 Tool；下一阶段为 0.6.0 Revision-aware Project Memory。

> **AI Generated**：本项目的代码和文档主要由 AI 生成，并通过人工审查、UE 5.6 编译、自动化测试和真实工程回归验证。

## 可以用来做什么

- 列出项目中的 Static Mesh、Skeletal Mesh、Material、Texture、Animation、DataTable、Niagara、World 等资产。
- 按资产名称、路径或 Asset Class 搜索。
- 查询资产的 Hard/Soft Package 依赖和反向引用。
- 查看 Asset Registry Tags、Package 信息、文件大小和 SHA-256 Revision。
- 查询 Blueprint 变量在哪里被读取或写入。
- 查询函数、接口消息、宏、Dynamic Cast 和 Event Dispatcher 的调用关系。
- 查看 Blueprint 的 Graph、Node、Pin 和连接结构。
- 使用 Policy、Revision 和导出快照校验 Patch，并对授权 Blueprint、非 Blueprint 标量属性、Data Asset Object/Class/Soft 引用、Struct/Array/Set/Map、Material Instance 参数，以及 DataTable 单元格、多字段和 Row 结构操作执行 Dry Run 或显式 Commit。
- 为成功 Commit 自动生成 Backup Manifest，并在当前 Revision 仍匹配时显式回滚和独立验证恢复结果。
- 使用声明式 Write Fixture Plan 在安全测试目录内创建或重置测试资产，并独立验证类、Revision 与 Dirty 状态。
- 通过本地 MCP Server，让 Agent 搜索资产/Symbol、读取单资产和查询引用，并使用 12 个高层安全写入 Tool 自动生成严格 Plan 或执行 Dry Run，不开放 Shell、任意 SQL 或 UObject。
- 对 Bool、整数、浮点、String、Name、Text 和两类 Enum 执行真实 Dry Run/Commit/重载矩阵，并验证未授权、过期 Revision、错误类型、越界、非法 Enum、属性不存在、Dirty Package、Sidecar 和保存失败均零写入拒绝。

## 主要能力

### 通用资产目录

- 导出 Asset Registry 可见的项目资产。
- 默认排除 Blueprint 和 World Partition 外部 Actor/Object 包，避免与 Blueprint 深度导出重复或产生大量生成记录。
- 输出资产路径、Asset Class、Package、Chunk、Registry Tags、Revision 和依赖关系。
- Static Mesh 专用 Reader 额外输出 LOD/Section、材质槽、Nanite、Bounds、Lightmap、碰撞和 Socket。
- Skeletal Mesh 专用 Reader 输出 Skeleton/Physics Asset、LOD、材质槽、Bounds、骨骼摘要、Morph Target 和 Socket；Skeleton Reader 输出完整骨骼层级、参考姿势、Virtual Bone、Socket、兼容项和 Curve 元数据。
- Physics Asset Reader 输出预览 Mesh、Body→Bone 映射、Shape 统计、禁碰撞对、Constraint 两端骨骼/参考帧和 Profile。
- Material Reader 输出 Domain、Blend Mode、Shading Model、双面/薄表面、Opacity Mask 和 Expression Class 摘要；Material Instance Reader version 2 输出 Parent、渲染属性以及 Scalar/Vector/Texture/Font/Static Switch 参数覆盖，并为四类可写参数导出 Override 与 Expression GUID。
- Material Function Reader 输出描述、库暴露状态、输入/输出稳定 GUID、类型、默认预览值以及 Expression Class 摘要。
- Texture2D Reader 输出 Source 尺寸/格式、Platform Data 可用性、压缩、sRGB、LOD Group、Mip、Filter、寻址、Streaming 和 Virtual Texture 设置，不读取像素或 BulkData。
- Anim Sequence Reader 输出 Skeleton、时长/采样、Additive、Root Motion、Notify、Curve 和 Sync Marker；Anim Montage Reader 输出 Section、Slot、Segment、Notify 和 Branching Point 摘要。
- Blend Space / Aim Offset Reader 输出轴配置与稳定排序样本；DataTable Reader 输出 Row Struct、排序后的 Row Name 和结构化行数据。
- 通用 Data Asset Reader 只读导出 Edit/Blueprint/Config/Searchable 属性、PrimaryAssetId 和对象/软对象路径，覆盖 Input Action、Input Mapping Context、Primary Asset Label 等派生资产。
- Niagara System Reader 输出系统 Warmup/Fixed Tick/Bounds、User Parameter、Emitter、Script、Renderer、事件处理器和 Simulation Stage 摘要，不读取模拟缓存或 GPU 数据。
- World Reader 输出 Persistent Level、World Settings、Streaming/World Partition、Actor/Component 类别计数与有界明细，并在 Actor Descriptor 元数据可用时只读输出外部 Actor 摘要，不主动加载外部 Actor，也不触发 BeginPlay 或关卡保存。
- Reader Registry 使用按 Asset Class 注册的分发表；Mesh、Material、Animation/Data、Niagara 和 World Reader 独立编译，未知类型仍安全回退到通用 Asset Registry 记录。
- 不批量加载所有 UObject，适合项目级快速扫描。

### Blueprint 深度分析

- 读取父类、接口、变量、默认值、组件、函数和 Graph。
- 导出 Node、Pin、连接关系及常用节点属性。
- 识别变量读写、函数和宏调用、接口消息、Dynamic Cast、Delegate、继承与接口实现。
- 分析 Hard/Soft Package、Soft Object/Class、Manage 和 Searchable Name 引用。

### SQLite 索引

- 将通用资产目录和 Blueprint 深度导出合并到同一个数据库。
- 使用 SQLite/FTS5 增量索引资产、Symbol 和 Reference。
- 支持中文路径、Unicode 内容、分页和 Asset Class 筛选。

## 快速开始

### 环境要求

```text
Windows 10 / 11
Unreal Engine 5.6
Visual Studio C++ Toolchain
PowerShell 5.1+
Python 3.11 或 3.12
```

### 1. 构建插件

```bat
scripts\BuildPluginDirect.cmd
```

默认输出：

```text
Build\Compiled\UEAgentKit
```

### 2. 导出通用资产目录

```bat
scripts\RunAssetCatalog.cmd -Root "/Game" -Output "Output\AssetCatalog"
```

默认会导出非 Blueprint 资产。导出单个资产：

```bat
scripts\RunAssetCatalog.cmd -Asset "/Game/Environment/SM_Wall" -Output "Output\SingleAsset"
```

### 3. 导出 Blueprint 语义

```bat
scripts\RunExport.cmd -Root "/Game" -Profile full -Format both -Output "Output\Blueprints"
```

导出单个 Blueprint：

```bat
scripts\RunExport.cmd -Asset "/Game/Characters/BP_Player" -Profile logic -Format both
```

### 4. 建立统一索引

两个导出目录可以依次导入同一个数据库：

```bat
scripts\ue-agent.cmd index build Output\AssetCatalog
scripts\ue-agent.cmd index build Output\Blueprints
scripts\ue-agent.cmd index stats
```

查询示例：

```bat
scripts\ue-agent.cmd search assets --class StaticMesh
scripts\ue-agent.cmd search assets Manny --class Texture2D
scripts\ue-agent.cmd search symbols MaxWalkSpeed
scripts\ue-agent.cmd references --target-asset /Game/LevelPrototyping/Materials/M_FlatCol.M_FlatCol
```

### 5. 校验并执行 Patch

先导出目标资产以获得当前 Revision。Blueprint 使用深度导出；非 Blueprint 使用通用资产目录：

```bat
scripts\RunExport.cmd ^
  -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject" ^
  -Asset "/Game/UEAgentKitWriteTests/BP_PatchTarget" ^
  -Profile full ^
  -Format json ^
  -Output "Output\PatchRevision"
```

仅执行 JSON、Policy 与 Revision 预校验：

```bat
scripts\ue-agent.cmd patch validate ^
  --patch examples\patches\set-variable-default.json ^
  --policy config\write-policy.example.json ^
  --export Output\PatchRevision ^
  --report Output\Patch\validation-report.json
```

内存 Dry Run：修改 UObject、读取结果并恢复原值；Blueprint 会在修改和回滚后编译，非 Blueprint 会触发编辑通知；不保存 `.uasset`：

```bat
scripts\RunPatch.cmd ^
  -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject" ^
  -Patch "examples\patches\set-variable-default.json" ^
  -Policy "config\write-policy.example.json" ^
  -RevisionExport "Output\PatchRevision" ^
  -Mode DryRun
```

显式 Commit：Policy 中还必须设置 `commitEnabled=true`。执行器会先创建外部 `.uasset` 备份，再保存单个授权资产；Blueprint 还必须编译成功：

```bat
scripts\RunPatch.cmd ^
  -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject" ^
  -Patch "examples\patches\set-variable-default.json" ^
  -Policy "config\write-policy.example.json" ^
  -RevisionExport "Output\PatchRevision" ^
  -Mode Commit ^
  -BackupDir "Backups\Patches"
```

Commit 成功后，`RunPatch` 会在同一备份目录自动生成 `<backup>.manifest.json`。恢复命令默认只校验、不写磁盘：

```bat
scripts\RunRollback.cmd ^
  -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject" ^
  -Manifest "Backups\Patches\<backup>.manifest.json" ^
  -Policy "config\write-policy.example.json" ^
  -BackupRoot "Backups\Patches" ^
  -Mode DryRun
```

显式恢复使用 `-Mode Commit`。目标工程必须关闭；恢复前会保存当前包，恢复后自动启动独立 UE 进程重新导出并核对 SHA-256 Revision。

完整标量回归：

```bat
scripts\RunScalarPatchRegression.cmd ^
  -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject"
```

脚本会创建隔离的原生 Data Asset Fixture，执行 11/11 Dry Run、11/11 Commit、9/9 预期失败，并在正常完成时 Reset 回默认值。

DataTable 单 Row 多字段原子回归：

```bat
scripts\TestDataTableRowFields.cmd ^
  -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject"
```

脚本会对一个现有 Row 的两个字段执行 Dry Run、Commit、独立重载、rollback Dry Run 和 rollback Commit，并验证最终 Revision 与原始字段值均恢复。

DataTable Row 结构操作回归：

```bat
scripts\TestDataTableRowOperations.cmd ^
  -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject"
```

脚本依次执行 Add Dry Run、Add Commit、Rename Commit、Remove Commit，再按 Remove → Rename → Add 逆序执行三层 rollback；每一步均通过独立 UE 进程重新导出验证 Row 集合，最终 Package Revision 必须与初始值完全一致。

Data Asset Struct/容器属性回归：

```bat
scripts\TestDataAssetStructuredProperties.cmd ^
  -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject"
```

脚本依次验证 Struct、Array、Set、Map 的稳定 JSON、结构化 Diff、Dry Run 深恢复、Commit 独立重载和四层逆序 rollback，最终 Package Revision 必须与初始值完全一致。

单资产多 Operation 原子事务回归：

```bat
scripts\TestMultiOperationTransactions.cmd ^
  -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject"
```

脚本分别对 Data Asset 和 Blueprint 执行两个 Operation：Dry Run 使用 `process-discard` 且磁盘 Revision 不变；Commit 只创建一个 Package 备份、只保存一次，并生成包含全部 Operation 与授权键的 Manifest；随后通过独立 UE 进程验证结果并整体 rollback，最终 Revision 必须与各自基线完全一致。

当前支持四种 Blueprint Operation、标量 `setAssetProperty`、Data Asset 专用 `setAssetReferenceProperty` 与 `setAssetStructuredProperty`、四种 Material Instance 参数 Operation，以及 DataTable 字段和 Row 操作。每次执行仍严格限制为一个资产，但可在同一原子事务中包含 1–32 个兼容 Operation；多 Operation 会统一预校验、创建一次备份、编译/保存一次，并由一个 Manifest 记录全部 Operation。属性、引用目标、Material 参数和 DataTable 字段继续使用逐目标精确 Policy 授权。`setAssetStructuredProperty` 只替换顶层 Struct、Array、Set 或 Map，使用显式 `valueType` 包络；Struct 必须包含完整字段，Set/Map 必须按 Canonical JSON 唯一排序，并返回递归结构化 Diff。当前仍只接受没有独立 Package 侧文件的单文件资产。

### 6. 启动 MCP Server（0.5.5）

先安装可选 MCP 依赖并确认 SQLite 索引可读：

```bat
scripts\setup_python.cmd -WithMcp
scripts\RunMcp.cmd -Database "<TOOL_ROOT>\.data\ue_agent_kit.sqlite3" -Check
scripts\TestMcpStdio.cmd
scripts\TestMcpClients.cmd
```

0.5.5 可以连接固定工程的受限 Live Editor Bridge：

```bat
scripts\TestMcpLiveEditor.cmd ^
  -EngineRoot "<UE_5.6>" ^
  -ProjectPath "<TEST_PROJECT>.uproject"

scripts\TestMcpSnapshotRefresh.cmd ^
  -EngineRoot "<UE_5.6>" ^
  -ProjectPath "<TEST_PROJECT>.uproject"
```

服务器对 MCP Client 仍只使用本地 `stdio`。默认模式为 5 个离线只读 Tool；`-EnableLiveEditor -ProjectPath <固定工程>` 增加 10 个实时只读 Tool 和 8 个受限 Daily Action，共 23 个 Live Tool；固定 Engine、Project、Policy 和 Revision Export 后的工作流为 25 个；两者组合时共 43 个 Tool。实时读取提供有界 Output Log、编译诊断、不触发加载的内存资产检查，以及普通 Blueprint Editor 的当前 Graph/Node 定位；Daily Action 提供资产打开/聚焦、Content Browser 同步、ActorGuid 聚焦、Blueprint 内存编译和官方 Data Validation，均不保存资产；工作流模式提供四源资产状态和安全单资产索引刷新：

```bat
claude mcp add --transport stdio --scope project ue-agent-kit -- ^
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File ^
  "<TOOL_ROOT>\scripts\RunMcp.ps1" ^
  -Database "<TOOL_ROOT>\.data\ue_agent_kit.sqlite3"
```

添加后可用 `claude mcp list` 或 Claude Code 内的 `/mcp` 检查连接。Live Editor 模式通过固定工程 `Saved/UEAgentKit/EditorBridge.json` 发现仅绑定 `127.0.0.1` 的临时端点，并执行随机令牌、工程路径摘要、版本和 Capability 握手；Tool 参数不能指定端口、令牌或任意 UObject/Console/Python/Shell。实时读取包括 4096 条 Output Log 环形缓冲、编译诊断、不触发加载的精确 `/Game/...Asset.Asset` 检查，以及只支持普通 Blueprint Editor 的聚焦 Graph 与最多 100 个选中 Node；相关读取始终报告 `loadedByBridge=false`。Daily Action 仅接受精确 `/Game` 身份或当前 Editor World `ActorGuid`，在 PIE/SIE 中拒绝执行；资产打开/聚焦、Content Browser 同步、Blueprint 内存编译和官方 Data Validation 均不保存 Package，并明确返回 Dirty 状态。`ue_get_asset_state` 区分 Editor Memory、磁盘 Package、Revision Export 和 SQLite，且不会为内存状态伪造 Revision。完整写入模式使用 `-EnableWriteTools`；只有同时使用 `-EnableCommitTools` 且 Policy 允许 Commit，才能保存或恢复资产。Plan 要求 SQLite、Revision Export 与磁盘 Package Revision 一致；十二个高层安全变更 Tool 默认只生成 Plan，也可自动执行 Dry Run，但不能直接 Commit。Commit 后固定快照会标记 stale，rollback 恢复原 Revision 后才重新 fresh。`ue_refresh_asset_index` 仅接受一个 Policy 授权的精确资产路径，并通过 Preview/Apply 生成配对 Revision Export + SQLite Generation；Apply 后当前会话继续读取冻结旧代且拒绝新工作流，重启 MCP 后新会话才读取新代。完整契约见 [`spec/MCP_SERVER.md`](spec/MCP_SERVER.md)、[`spec/LIVE_EDITOR_BRIDGE.md`](spec/LIVE_EDITOR_BRIDGE.md) 与 [`spec/INDEX_FRESHNESS.md`](spec/INDEX_FRESHNESS.md)。

### 7. 校验通用资产导出

```bat
python scripts\ValidateAssetCatalog.py --output Output\AssetCatalog --expect-exporter 0.5.5
```

完整参数和安装说明见 [`docs/BUILD_AND_RUN.md`](docs/BUILD_AND_RUN.md)。

## 输出结构

通用资产目录：

```text
Output\AssetCatalog\
├─ manifest.json
└─ canonical\
```

Blueprint 深度导出：

```text
Output\Blueprints\
├─ manifest.json
├─ canonical\
└─ bpctx\
```

- **Canonical JSON**：稳定的资产事实模型。
- **BPCTX/1**：面向 AI 的紧凑 Blueprint 文本格式。
- **SQLite Index**：项目级 Asset、Symbol 和 Reference 检索。

## 文档

- [`docs/BUILD_AND_RUN.md`](docs/BUILD_AND_RUN.md)：构建、安装、导出和查询。
- [`docs/AI_USAGE.md`](docs/AI_USAGE.md)：AI 使用资产索引与 Blueprint 语义的方式。
- [`docs/RELEASE_0.5.5.md`](docs/RELEASE_0.5.5.md)：0.5.x 日常开发能力、原子事务、验证证据与正式发布收口。
- [`docs/RELEASE_0.5.1.md`](docs/RELEASE_0.5.1.md)：0.5.1 查询协议、高层安全写入、诊断和 Client 兼容矩阵。
- [`docs/RELEASE_0.5.0.md`](docs/RELEASE_0.5.0.md)：0.5.0 固定项目 MCP 工作流发布说明。
- [`docs/RELEASE_0.4.4.md`](docs/RELEASE_0.4.4.md)：0.4.4 正式发布范围、验证结果和升级说明。
- [`CHANGELOG.md`](CHANGELOG.md)：版本变更摘要。
- [`docs/ROADMAP.md`](docs/ROADMAP.md)：0.5.x 日常 MCP、0.6.0 Revision-aware Project Memory、后续分析与协作能力路线。
- [`spec/BPCTX_FORMAT.md`](spec/BPCTX_FORMAT.md)：BPCTX/1 格式规范。
- [`spec/PATCH_SCHEMA.md`](spec/PATCH_SCHEMA.md)：声明式 Patch、Policy、Revision 和纯校验安全边界。
- [`spec/BACKUP_AND_ROLLBACK.md`](spec/BACKUP_AND_ROLLBACK.md)：Backup Manifest、rollback、审计回执和恢复验证规范。
- [`spec/WRITE_FIXTURE_PLAN.md`](spec/WRITE_FIXTURE_PLAN.md)：测试资产 Plan、Create/Reset 和独立重载验证规范。
- [`spec/SCALAR_PATCH_REGRESSION.md`](spec/SCALAR_PATCH_REGRESSION.md)：完整标量类型、正向写入和失败路径真实 UE 回归规范。
- [`spec/MCP_SERVER.md`](spec/MCP_SERVER.md)：MCP Tool、stdio、固定配置和响应契约。
- [`spec/LIVE_EDITOR_BRIDGE.md`](spec/LIVE_EDITOR_BRIDGE.md)：受限 localhost IPC、固定工程握手、实时读取与 Daily Actions。
- [`spec/INDEX_FRESHNESS.md`](spec/INDEX_FRESHNESS.md)：三源 Revision 新鲜度、stale 生命周期与安全快照重载。

完整文档索引见 [`docs/README.md`](docs/README.md)。

## 安全说明

只读导出器、SQLite 查询、当前 MCP Tool 和 `ue-agent patch validate` 不修改 UObject 或资产文件。实际写入只能通过独立的 `BlueprintPatch` 或 `AssetPatch` Commandlet，由 `RunPatch` 在预校验通过后按 Operation 分发。

- 默认使用 `DryRun`，磁盘 Revision 必须保持不变。
- `Commit` 同时要求命令行显式选择和 Policy 的 `commitEnabled=true`。
- 仅允许 Policy 授权的项目、目录、Asset Class 和 Operation；通用属性和 Material 参数还要求各自的精确白名单。
- 保存前创建外部备份；成功 Commit 自动生成不可覆盖的 Manifest，记录授权键、Policy 哈希和变更前后 Revision。
- rollback 默认 Dry Run；Commit 要求工程关闭、当前文件仍等于 Commit 后 Revision、备份哈希与大小一致，并在替换前创建安全副本。
- Blueprint 编译失败、Revision 冲突、Dirty Package、目标解析、参数查找或类型校验失败时禁止保存。
- 当前执行器只处理单资产；每个资产支持 1–32 个兼容 Operation。多 Operation 事务拒绝重复目标和 DataTable Row 新增/删除/重命名，并避免部分保存。
- Patch 通过 UE Editor API 修改并保存资产；rollback 仅按已验证 Manifest 原子恢复完整 Package，不解析或局部改写 `.uasset` 二进制。

## 发布门禁

跨平台发布检查统一通过：

```bat
python scripts\ValidateRelease.py --require-release-docs
```

该命令校验 Python/Plugin/C++ 版本源、双语发布文档、Ruff、完整 Python 测试、3 份 JSON Schema、16 个 Patch 示例和示例 Policy。GitHub Actions 在 Python 3.11 与 3.12 上执行同一门禁并构建 Python Distribution；UE5.6 Plugin 编译和真实资产回归仍由具备引擎环境的本地发布机执行。

## License

UE Agent Kit 使用 [MIT License](LICENSE)。

本项目采用独立实现方式。第三方项目仅用于研究架构、工作流和 Unreal API 使用方式，相关规则见 [`docs/REFERENCE_POLICY.md`](docs/REFERENCE_POLICY.md)。

UE Agent Kit 是独立开源项目，与 Epic Games, Inc. 没有隶属、赞助或背书关系。Unreal 和 Unreal Engine 是 Epic Games, Inc. 在美国及其他地区的商标或注册商标。
