# UE Agent Kit

[English](README_EN.md)

> 将 Unreal Engine 资产和 Blueprint 转换为 AI 与开发者可检索、可追踪的项目知识。

UE Agent Kit 是一套面向 Unreal Engine 的开源只读资产分析工具。它通过 UE Editor 插件导出项目资产目录、Asset Registry 元数据、依赖关系和 Blueprint 语义，再使用 Python CLI 与 SQLite 建立项目级索引。

当前版本为 **0.2.6**，支持 **Unreal Engine 5.6**。工具仅执行只读导出、索引和查询，不修改或保存 `.uasset`。

> **AI Generated**：本项目的代码和文档主要由 AI 生成，并通过人工审查、UE 5.6 编译、自动化测试和真实工程回归验证。

## 可以用来做什么

- 列出项目中的 Static Mesh、Skeletal Mesh、Material、Texture、Animation、DataTable、Niagara、World 等资产。
- 按资产名称、路径或 Asset Class 搜索。
- 查询资产的 Hard/Soft Package 依赖和反向引用。
- 查看 Asset Registry Tags、Package 信息、文件大小和 SHA-256 Revision。
- 查询 Blueprint 变量在哪里被读取或写入。
- 查询函数、接口消息、宏、Dynamic Cast 和 Event Dispatcher 的调用关系。
- 查看 Blueprint 的 Graph、Node、Pin 和连接结构。

## 主要能力

### 通用资产目录

- 导出 Asset Registry 可见的项目资产。
- 默认排除 Blueprint 和 World Partition 外部 Actor/Object 包，避免与 Blueprint 深度导出重复或产生大量生成记录。
- 输出资产路径、Asset Class、Package、Chunk、Registry Tags、Revision 和依赖关系。
- Static Mesh 专用 Reader 额外输出 LOD/Section、材质槽、Nanite、Bounds、Lightmap、碰撞和 Socket。
- Skeletal Mesh 专用 Reader 输出 Skeleton/Physics Asset、LOD、材质槽、Bounds、骨骼摘要、Morph Target 和 Socket；Skeleton Reader 输出完整骨骼层级、参考姿势、Virtual Bone、Socket、兼容项和 Curve 元数据。
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

### 5. 校验通用资产导出

```bat
python scripts\ValidateAssetCatalog.py --output Output\AssetCatalog --expect-exporter 0.2.6
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
- [`spec/BPCTX_FORMAT.md`](spec/BPCTX_FORMAT.md)：BPCTX/1 格式规范。

完整文档索引见 [`docs/README.md`](docs/README.md)。

## 安全说明

当前版本完全只读。Commandlet 不保存项目资产，不直接编辑 `.uasset`。每个导出记录都可以包含原始包文件的 SHA-256 Revision，用于验证资产是否变化。

## License

UE Agent Kit 使用 [MIT License](LICENSE)。

本项目采用独立实现方式。第三方项目仅用于研究架构、工作流和 Unreal API 使用方式，相关规则见 [`docs/REFERENCE_POLICY.md`](docs/REFERENCE_POLICY.md)。

UE Agent Kit 是独立开源项目，与 Epic Games, Inc. 没有隶属、赞助或背书关系。Unreal 和 Unreal Engine 是 Epic Games, Inc. 在美国及其他地区的商标或注册商标。
