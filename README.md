# UE Agent Kit

[English](README_EN.md)

> 将 Unreal Engine 资产和 Blueprint 转换为 AI 与开发者可检索、可追踪的项目知识。

UE Agent Kit 是一套面向 Unreal Engine 的开源资产分析、索引与受控写入工具。它通过 UE Editor 插件导出项目资产目录、Asset Registry 元数据、依赖关系和 Blueprint 语义，再使用 Python CLI 与 SQLite 建立项目级索引，并通过 Policy、Revision、Dry Run 和备份保护显式写入。

当前版本为 **0.3.6**，支持 **Unreal Engine 5.6**。除八类 Blueprint 和通用标量属性写入外，现已支持 Material Instance Global Scalar、Vector 与 Texture 参数的精确白名单写入、独立引用资产授权和完整 Override 数组回滚。

> **AI Generated**：本项目的代码和文档主要由 AI 生成，并通过人工审查、UE 5.6 编译、自动化测试和真实工程回归验证。

## 可以用来做什么

- 列出项目中的 Static Mesh、Skeletal Mesh、Material、Texture、Animation、DataTable、Niagara、World 等资产。
- 按资产名称、路径或 Asset Class 搜索。
- 查询资产的 Hard/Soft Package 依赖和反向引用。
- 查看 Asset Registry Tags、Package 信息、文件大小和 SHA-256 Revision。
- 查询 Blueprint 变量在哪里被读取或写入。
- 查询函数、接口消息、宏、Dynamic Cast 和 Event Dispatcher 的调用关系。
- 查看 Blueprint 的 Graph、Node、Pin 和连接结构。
- 使用 Policy、Revision 和导出快照校验 Patch，并对授权 Blueprint、非 Blueprint 标量属性或 Material Instance Scalar 参数执行 Dry Run 或显式 Commit。

## 主要能力

### 通用资产目录

- 导出 Asset Registry 可见的项目资产。
- 默认排除 Blueprint 和 World Partition 外部 Actor/Object 包，避免与 Blueprint 深度导出重复或产生大量生成记录。
- 输出资产路径、Asset Class、Package、Chunk、Registry Tags、Revision 和依赖关系。
- Static Mesh 专用 Reader 额外输出 LOD/Section、材质槽、Nanite、Bounds、Lightmap、碰撞和 Socket。
- Skeletal Mesh 专用 Reader 输出 Skeleton/Physics Asset、LOD、材质槽、Bounds、骨骼摘要、Morph Target 和 Socket；Skeleton Reader 输出完整骨骼层级、参考姿势、Virtual Bone、Socket、兼容项和 Curve 元数据。
- Physics Asset Reader 输出预览 Mesh、Body→Bone 映射、Shape 统计、禁碰撞对、Constraint 两端骨骼/参考帧和 Profile。
- Material Reader 输出 Domain、Blend Mode、Shading Model、双面/薄表面、Opacity Mask 和 Expression Class 摘要；Material Instance Reader 输出 Parent、渲染属性以及 Scalar/Vector/Texture/Font/Static Switch 参数覆盖。
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

当前支持四种 Blueprint Operation、非 Blueprint 的 `setAssetProperty`，以及 `setMaterialInstanceScalarParameter`。每次执行仅允许一个资产和一个 Operation；通用属性由 `allowedAssetProperties` 精确授权，Material 参数由 `allowedMaterialParameters` 以 `AssetClass#Scalar#ParameterName` 精确授权。Material Instance 首版只支持唯一的 Global Scalar 参数；Dry Run 会恢复完整 Scalar Override 数组。当前仍只接受没有独立 Package 侧文件的单文件资产。

### 6. 校验通用资产导出

```bat
python scripts\ValidateAssetCatalog.py --output Output\AssetCatalog --expect-exporter 0.3.6
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
- [`docs/ROADMAP.md`](docs/ROADMAP.md)：0.4.0、0.4.x 和 0.5.0 的版本目标与安全边界。
- [`spec/BPCTX_FORMAT.md`](spec/BPCTX_FORMAT.md)：BPCTX/1 格式规范。
- [`spec/PATCH_SCHEMA.md`](spec/PATCH_SCHEMA.md)：声明式 Patch、Policy、Revision 和纯校验安全边界。

完整文档索引见 [`docs/README.md`](docs/README.md)。

## 安全说明

只读导出器和 `ue-agent patch validate` 不修改 UObject 或资产文件。实际写入只能通过独立的 `BlueprintPatch` 或 `AssetPatch` Commandlet，由 `RunPatch` 在预校验通过后按 Operation 分发。

- 默认使用 `DryRun`，磁盘 Revision 必须保持不变。
- `Commit` 同时要求命令行显式选择和 Policy 的 `commitEnabled=true`。
- 仅允许 Policy 授权的项目、目录、Asset Class 和 Operation；通用属性和 Material 参数还要求各自的精确白名单。
- 保存前创建外部备份；Blueprint 编译失败、Revision 冲突、Dirty Package、目标解析、参数查找或类型校验失败时禁止保存。
- 当前执行器只处理单资产、单操作，避免部分保存。
- 工具通过 UE Editor API 修改并保存资产，不直接编辑 `.uasset` 二进制。

## License

UE Agent Kit 使用 [MIT License](LICENSE)。

本项目采用独立实现方式。第三方项目仅用于研究架构、工作流和 Unreal API 使用方式，相关规则见 [`docs/REFERENCE_POLICY.md`](docs/REFERENCE_POLICY.md)。

UE Agent Kit 是独立开源项目，与 Epic Games, Inc. 没有隶属、赞助或背书关系。Unreal 和 Unreal Engine 是 Epic Games, Inc. 在美国及其他地区的商标或注册商标。
