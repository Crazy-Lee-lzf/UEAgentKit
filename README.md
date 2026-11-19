# UE Agent Kit

[English](README_EN.md)

UE Agent Kit 是一套面向 **Unreal Engine 5.6** 的开源项目分析与受控修改工具。它把 `.uasset`、Blueprint 语义、引用关系和运行中的 Editor 状态变成可检索的数据，并让 AI Agent 在明确的安全边界内执行窄范围修改。它不是通用图编辑器，也不是任意脚本执行器。

当前已发布版本为 **0.8.0**（稳定版）。当前最新公开预发布版本为 **0.9.0 Developer Preview 1（v0.9.0-dev1 / Python 0.9.0.dev1）**（2026-10-30）；下一版候选为 **0.9.0 Developer Preview 2**（计划版本 `0.9.0.dev2`，尚未发布）。运行环境：**Unreal Engine 5.6 / Windows x64 / Python 3.11–3.14**。

## 它能做什么

- **项目查询**：Asset Registry 资产目录，Static Mesh / Material / Texture / Animation / DataTable / Data Asset / Niagara / World 等专用读取，SQLite + FTS5 索引，正向和反向引用。
- **Blueprint 读取**：Graph / Node / Pin、变量读写位置、函数 / 宏 / 接口 / Cast / Delegate 语义，区分保存快照与当前 Editor 状态。
- **Live Editor**：当前 World、Selection、Open/Dirty Assets、Output Log、编译错误、PIE Server/Client 对象与指定标量属性。
- **受控写入**：Data Asset、Material Instance、DataTable、AnimSequence 辅助流程，以及 Blueprint 已注册的变量默认值 / 组件属性 / Pin 默认值等 Operation。写入受 Write Policy、Revision、Dirty Package、Transaction、显式 Save 和独立 Verify 约束。
- **实验 Blueprint 结构写入**（默认关闭）：一条原子 patch 支持 `createNode` / `connect` / `disconnect` / `setPinDefault` / `createVariable` / `rewirePin` / `removeNode` 七种操作；节点可通过 Action Registry 的 `actionId` 创建。
- **Agent 工作流**：Task Context、Impact Analysis、Change Set、Semantic Diff、Verification Plan、Trust Verdict，以及 revision-aware Project Memory。
- **P4 / Perforce**（opt-in）：mapping / opened / lock / have / head 查询，精确 `p4 edit`、受限 safe sync、pending changelist、reopen 和普通文本 resolve。Agent 不执行 Submit / Revert / Delete。

## 快速开始

### 1. 环境

```text
Windows 10 / 11
Unreal Engine 5.6
Visual Studio 2022 C++ Toolchain
PowerShell 5.1+
Python 3.11–3.14
P4 CLI（仅启用 Source Control 时需要）
```

### 2. 安装

```bat
git clone git@github.com:Crazy-Lee-lzf/UEAgentKit.git
cd UEAgentKit
scripts\setup_python.cmd -WithMcp
scripts\BuildPluginDirect.cmd -EngineRoot "<UE_5.6>"
```

插件也可以直接由项目编译，或安装预编译包：

```bat
scripts\InstallProjectPlugin.cmd -Mode Source  -ProjectPath "<PROJECT>.uproject"
scripts\InstallProjectPlugin.cmd -Mode Package -ProjectPath "<PROJECT>.uproject"
```

### 3. 建立项目索引

```bat
scripts\RunAssetCatalog.cmd -Root "/Game" -Output "Output\AssetCatalog"
scripts\RunExport.cmd -Root "/Game" -Profile full -Format both -Output "Output\Blueprints"
scripts\ue-agent.cmd index build Output\AssetCatalog
scripts\ue-agent.cmd index build Output\Blueprints
```

### 4. 启动只读 MCP

```bat
scripts\RunMcp.cmd -Database "<TOOL_ROOT>\.data\ue_agent_kit.sqlite3" -Check
scripts\RunMcp.cmd -Database "<TOOL_ROOT>\.data\ue_agent_kit.sqlite3"
```

### 5. 让 Agent 先发现能力

连接后先调用 `ue_get_capabilities`，再按 `ue_search` → `ue_get_asset` → `ue_find_references` 的顺序查询。没有注册的 Tool 就是当前不可用，不要根据本文或源码推断它已开放。

## 写入与实验能力

**稳定写入**需要同时满足：服务端启用 `--enable-write-tools`、`--enable-subprocess-workflow`，项目 `Config/DefaultEngine.ini` 设置 `[UEAgentKit] SubprocessWorkflow=True`，并且目标受 Write Policy 覆盖。缺任一项时 `workflow` 组不会出现在 capabilities 中。`RunMcp.cmd` 目前只覆盖只读和 Pure Live 模式；写入与实验模式需要直接运行 `.venv\Scripts\python.exe scripts\ue-agent-mcp.py`，完整命令见 [构建与运行](docs/BUILD_AND_RUN.md)。

直接启动时**还要给档位**：`ue-agent-mcp.py` 的 `--tool-profile` 默认是 `core`（只有 query 组），写入要加 `--tool-profile full`（或 `--tool-profile live-write`），也可以用 `--tool-groups` 点名分组，例如 `--tool-groups query,workflow`。档位是**上限**，不会打开被开关关掉的组；`ue_get_capabilities` 的 `toolProfile` / `toolGroups` 回报实际生效范围。全部工具与档位见 [专用 Tool 索引](docs/TOOL_CATALOG.md)。

**实验 Blueprint 结构写入**额外需要 MCP `--enable-experimental-graphpatch-edit` 与项目 `[UEAgentKit] ExperimentalGraphPatchEdit=True`；通过 Action Registry 创建节点时，再加 `--enable-experimental-node-spawn` 与 `[UEAgentKit] ExperimentalNodeSpawn=True`。一次 Apply 是一个 Editor 事务，失败会回滚已执行的操作；Save 永远是独立显式动作。它们不经过稳定 Write Policy 白名单，只允许隔离测试工程使用。用法与限制见 [Blueprint 实验用法](docs/BLUEPRINT_AGENT_E2E_USAGE.md)。

**没有索引或 Revision Export 的机器**可以在 Live Editor 上启动写入模式（live-only）：不需要 SQLite，但会失去离线 Revision 证据，patch 类写入仍会要求 Revision Export。读取侧新增会话级 overlay：`ue_get_asset` 发现磁盘包已变化时会自动向 Editor 重新取证；取不到时返回 `overlayStale` / `staleCause`，不会继续返回旧 canonical。`ue_refresh_asset_index_live` 可显式刷新单个 Blueprint，不写索引、不要求重启。

## 当前限制

- 不支持任意 Graph CRUD、任意 Level Actor 编辑、Material Graph / Niagara / Sequencer / Control Rig 写入。
- 实验 patch 只覆盖七种操作和顶层 K2 事件图；不能创建 CustomEvent、事件/委托绑定、Timeline、MathExpression，也不能创建 `Select` / `PromotableOperator` 本身。
- 不支持 PIE 输入注入、任意 Asset Import/Duplicate/Rename/Delete/Migrate，以及 Console / Python / Shell / UObject Method 执行。
- 不自动 Save All，不自动编译。
- Agent 不执行 P4 Submit / Revert / Delete，不自动解决 `.uasset` / `.umap` 内容冲突。

完整边界、替代入口和当前候选版本说明见 [能力状态](docs/PROJECT_STATUS.md)。

## 文档

- [AI Agent 快速接入](docs/AGENT_QUICKSTART.md) / [English](docs/AGENT_QUICKSTART_EN.md)
- [当前能力状态](docs/PROJECT_STATUS.md) / [English](docs/PROJECT_STATUS_EN.md)
- [0.9.0 Developer Preview 2（候选）](docs/RELEASE_0.9.0_DP2.md) / [English](docs/RELEASE_0.9.0_DP2_EN.md)
- [0.9.0 Developer Preview 1（已发布）](docs/RELEASE_0.9.0_PREVIEW.md) / [English](docs/RELEASE_0.9.0_PREVIEW_EN.md)
- [0.8.0 Release Notes](docs/RELEASE_0.8.0.md) / [English](docs/RELEASE_0.8.0_EN.md)
- [Blueprint 实验用法](docs/BLUEPRINT_AGENT_E2E_USAGE.md) / [English](docs/BLUEPRINT_AGENT_E2E_USAGE_EN.md)
- [专用 Tool 索引](docs/TOOL_CATALOG.md)
- [构建与运行](docs/BUILD_AND_RUN.md)
- [程序开发工作流](docs/DEVELOPER_WORKFLOWS.md) / [English](docs/DEVELOPER_WORKFLOWS_EN.md)
- [文档索引](docs/README.md)

## License

UE Agent Kit 使用 [MIT License](LICENSE)。

UE Agent Kit 是独立开源项目，与 Epic Games, Inc. 没有隶属、赞助或背书关系。Unreal 和 Unreal Engine 是 Epic Games, Inc. 的商标或注册商标。
