# UE Agent Kit

[English](README_EN.md)

UE Agent Kit 是一套面向 **Unreal Engine 5.6** 的开源项目分析与受控修改工具，主要用于让 AI Agent 和开发者能够可靠地理解 Unreal 资产、Blueprint 语义、引用关系和编辑器状态，并在明确的安全边界内执行修改。

当前已发布版本为 **0.8.0**，支持 **Unreal Engine 5.6 / Windows / Python 3.11–3.12**。

## 适合解决什么问题

Unreal 项目的大量信息存在于 `.uasset`、Blueprint Graph、Asset Registry 和运行中的 Editor 状态中，普通代码搜索无法直接回答：

- 某个资产依赖谁、又被谁引用？
- 某个 Blueprint 变量在哪里读写？
- 某个 DataTable / Material Instance / Data Asset 当前真实值是什么？
- 修改一个资产会影响哪些其它资产？
- AI 修改后，磁盘上的结果是否真的和计划一致？
- 多人使用 P4 时，当前文件是否已 checkout、落后、锁定或存在 unresolved 状态？

UE Agent Kit 将这些信息转换为结构化、可检索、可验证的数据，并提供受控写入工作流。

```text
UE Editor / Asset Registry
        ↓
Canonical JSON / Blueprint semantic export
        ↓
SQLite / FTS5 project index
        ↓
MCP / CLI queries
        ↓
Plan → Policy → Revision → Apply → Save → Verify → Diff / Trust
```

## 主要能力

### 资产与 Blueprint 查询

- 项目级 Asset Registry 导出和 SQLite/FTS5 索引。
- Static Mesh、Skeletal Mesh、Material、Texture、Animation、DataTable、Data Asset、Niagara、World 等专用读取。
- Blueprint Graph / Node / Pin、变量读写、函数/宏/接口调用、Dynamic Cast、Delegate 等语义关系。
- Hard / Soft Package、Soft Object/Class、Searchable Name 等引用查询。
- 当前 Editor、World、Selection、Open Assets、Dirty Packages、Output Log 和 Blueprint 编译诊断。

### Agent 工作流

- Task Context 和相关资产发现。
- Reverse Reference Impact Analysis。
- Change-Set 绑定的 Semantic Diff。
- Evidence-gated Verification Plan 和 Trust Verdict。
- Revision-aware Project Memory，可保存规则、发现、决策、任务结果和验证证据。
- 只读 Knowledge Web，用于浏览项目知识和关系。

### 受控写入

支持的主要写入域包括：

- Blueprint：变量默认值、组件属性、Pin 默认值、描述等已注册 Operation。
- Data Asset：标量、Object/Class/Soft 引用、Struct/Array/Set/Map。
- Material Instance：Scalar / Vector / Texture / Static Switch 参数。
- DataTable：Cell、Row Fields、Add / Remove / Rename Row。
- AnimSequence 的有限实时修复与重定向辅助流程。

写入不是任意脚本执行。所有写入都受 **Write Policy、Revision、目标身份、Dirty Package、Transaction、显式保存和独立验证** 约束。

## P4 / Perforce 集成

Source Control 功能默认关闭，需要显式启用。0.8.0 可以：

- 查询 mapping、opened、owner/client、lock、have/head 和 pending changelist。
- 对精确文件执行 `p4 edit`。
- 在证据充分时执行严格 safe sync。
- 创建/更新当前用户与 client 的 pending changelist，并对精确已打开文件执行 `reopen`。
- 预览 resolve 状态，并对满足条件的普通文本文件执行受限 `resolve -am`。
- 生成持久化审计记录和人工最终操作提示。

安全边界：

- **Agent 不执行 P4 Submit。**
- **Agent 不执行 P4 Revert。**
- **Agent 不执行 P4-managed Delete。**
- 不提供 generic P4 argv 或 shell passthrough。
- `.uasset` / `.umap` 不自动选择 yours/theirs，也不自动进行内容 Resolve。

## 安装

### 环境要求

```text
Windows 10 / 11
Unreal Engine 5.6
Visual Studio 2022 C++ Toolchain
PowerShell 5.1+
Python 3.11 或 3.12
P4 CLI（仅在启用 Source Control 时需要）
```

### 1. Clone

```bash
git clone git@github.com:Crazy-Lee-lzf/UEAgentKit.git
cd UEAgentKit
```

### 2. Python 环境

需要 MCP 时：

```bat
scripts\setup_python.cmd -WithMcp
```

### 3. 构建 UE5.6 Plugin

```bat
scripts\BuildPluginDirect.cmd -EngineRoot "<UE_5.6>"
```

默认输出：

```text
Build\Compiled\UEAgentKit
```

也可以直接把源码 Plugin 链接到项目并由项目编译：

```bat
scripts\InstallProjectPlugin.cmd -Mode Source -ProjectPath "<PROJECT>.uproject"
```

使用预编译 Package：

```bat
scripts\InstallProjectPlugin.cmd -Mode Package -ProjectPath "<PROJECT>.uproject"
```

## 建立项目索引

导出普通资产：

```bat
scripts\RunAssetCatalog.cmd -Root "/Game" -Output "Output\AssetCatalog"
```

导出 Blueprint 语义：

```bat
scripts\RunExport.cmd -Root "/Game" -Profile full -Format both -Output "Output\Blueprints"
```

建立 SQLite 索引：

```bat
scripts\ue-agent.cmd index build Output\AssetCatalog
scripts\ue-agent.cmd index build Output\Blueprints
scripts\ue-agent.cmd index stats
```

查询示例：

```bat
scripts\ue-agent.cmd search assets --class StaticMesh
scripts\ue-agent.cmd search symbols MaxWalkSpeed
scripts\ue-agent.cmd references --target-asset /Game/Characters/BP_Player.BP_Player
```

## 启动 MCP

UE Agent Kit 使用本地 `stdio` MCP。固定项目模式下，数据库、工程、Policy、Revision Export 和 Work Root 在服务器启动时确定，Tool 调用不能任意替换这些路径。

```bat
scripts\RunMcp.cmd -Database "<TOOL_ROOT>\.data\ue_agent_kit.sqlite3" -Check
```

完整参数与固定项目配置见：

- [构建与运行](docs/BUILD_AND_RUN.md)
- [AI 使用说明](docs/AI_USAGE.md)
- [项目级配置](docs/PROJECT_LEVEL_CONFIG.md)
- [MCP Server 契约](spec/MCP_SERVER.md)
- [Live Editor Bridge 契约](spec/LIVE_EDITOR_BRIDGE.md)

## Write Policy

Write Policy 是 UE Agent Kit 的项目级写入白名单。它控制：

- 哪些项目允许写入；
- 哪些 `/Game/...` 根目录允许修改；
- 哪些 Asset Class 和 Operation 被允许；
- 哪些属性、DataTable 字段、Material 参数或引用目标可写；
- 是否允许 Commit，以及是否要求 Revision / Clean Package 等条件。

仓库提供 [示例 Policy](config/write-policy.example.json)。真实项目应创建自己的最小 Policy，而不是直接放开整个 `/Game`。

建议首次接入按以下顺序：

```text
Read-only audit
→ 建立索引
→ 确认 P4 mapping
→ 创建最小 Write Policy
→ 选择测试资产
→ Plan / Dry Run
→ 显式写入
→ Save / Verify / Diff
```

## 当前限制

0.8.0 不提供：

- 任意 Blueprint Graph 节点 CRUD / 自动连线。
- 任意 Level Actor Spawn/Delete/Transform 编辑。
- 任意 Material Graph / Niagara / Sequencer / Control Rig 写入。
- 任意 Unreal Python、Console Command、Shell 或 UObject Method 执行。
- 自动 Save All。
- Agent 侧 P4 Submit / Revert / Delete。

这些限制是当前产品安全模型的一部分，不应通过通用脚本入口绕过。

## 文档

- [文档索引](docs/README.md)
- [项目能力状态](docs/PROJECT_STATUS.md)
- [公开 Roadmap](docs/ROADMAP.md)
- [0.8.0 Release Notes](docs/RELEASE_0.8.0.md)
- [Memory Architecture](docs/MEMORY_ARCHITECTURE.md)
- [Build and Run](docs/BUILD_AND_RUN.md)

## License

UE Agent Kit 使用 [MIT License](LICENSE)。

UE Agent Kit 是独立开源项目，与 Epic Games, Inc. 没有隶属、赞助或背书关系。Unreal 和 Unreal Engine 是 Epic Games, Inc. 的商标或注册商标。
