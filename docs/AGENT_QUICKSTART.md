# AI Agent 快速接入指南

[English](AGENT_QUICKSTART_EN.md)

> 适用于通过 MCP 使用 UE Agent Kit 的用户及 AI Agent。**先确认实际安装版本，再调用运行时返回的 Tool**。以下只介绍安全接入路径，不授权绕过项目和写入门禁。

## 版本与权限边界

- 已发布 `v0.8.0` 的能力及限制见 [0.8.0 状态](PROJECT_STATUS.md) 和 [Release Notes](RELEASE_0.8.0.md)。本地开发分支可能包含额外的实验性功能，不代表 GitHub 用户已获得该能力。
- 普通查询优先只读；稳定写入 Tool 必须由服务端启动模式、固定工程及 Write Policy 授权。调用方**不能**在 Tool 参数中变更工程、引擎、数据库、Policy 或 Editor Endpoint。
- 例外：默认关闭的 `--enable-experimental-graphpatch-edit` Blueprint 结构写入不经过 Write Policy，只受实验 flag、工程 ini、精确 plan/confirmation、资产/图身份和 Clean/PIE 校验约束；不得用于生产项目。
- 蓝图通用 Graph Node CRUD、任意资产/Actor 编辑、任意 Shell/Python 执行、自动 Save All 均不是 v0.8.0 的开放能力。Native Registry / Native Layout Preview 和 R7 Staged Context Handoff 等后续研发功能不能因源码存在就视为默认可用。

## 1. 用户完成一次性环境接入

从仓库根目录执行。详细依赖、插件安装、命令参数和项目绑定见 [构建与运行](BUILD_AND_RUN.md) 与 [项目级配置](PROJECT_LEVEL_CONFIG.md)。

```bat
scripts\setup_python.cmd -WithMcp
scripts\RunAssetCatalog.cmd -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject" -Root "/Game" -Output "Output\AssetCatalog"
scripts\RunExport.cmd -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject" -Root "/Game" -Profile full -Format both -Output "Output\Blueprints"
scripts\ue-agent.cmd index build Output\AssetCatalog
scripts\ue-agent.cmd index build Output\Blueprints
scripts\RunMcp.cmd -Database "<TOOL_ROOT>\.data\ue_agent_kit.sqlite3" -Check
```

以上命令假设 UE 插件已按 [构建与运行](BUILD_AND_RUN.md) 安装，并由用户使用自己的 Engine/Project 路径完成项目配置；`<...>` 均为占位符，不能原样直接运行。单独启动只读 stdio MCP：

```bat
scripts\RunMcp.cmd -Database "<TOOL_ROOT>\.data\ue_agent_kit.sqlite3"
```

在 MCP 客户端中配置该启动命令并连接。Live Editor 和可写 Workflow 需要额外的**服务器启动配置**，不是 Agent 在一次调用里临时开启。

## 2. Agent 必须先发现能力

连接后首先调用 `ue_get_capabilities`，核实当前注册 Tool 及其读取/写入标记。具体参数、输出结构及错误码以实际 Tool Schema 和 [MCP 契约](../spec/MCP_SERVER.md) 为准。

不要仅凭 Markdown 中的总数、源代码里的实验性 handler 或测试报告推断 Tool 已开放。如果 Tool 未注册、Editor 不在线、工程身份不匹配或权限不足，就报告当前阻塞，不尝试绕过。

## 3. 从只读查询开始

在 Schema 支持的情况下，按以下顺序完成项目检索：

```text
ue_get_capabilities
→ ue_get_project_status
→ ue_search（先定位 Asset / Symbol）
→ ue_get_asset（只取必要的资产详情）
→ ue_find_references（确认依赖、影响范围）
→ 向用户输出 Asset Path、Revision、事实来源和未知项
```

读取结果必须区分*当前数据事实*、*基于调用关系的推断*、*未导出的未知数据*；不能把 SQLite 的冻结 Revision 当成实时 Editor Package 的状态。只有当前 Tool 明确可用时，才调用 Live Editor 读取工具。

## 4. 写入必须明确授权

**默认不写入**。仅当用户要求修改、服务端开放所需工具、目标受固定 Write Policy 覆盖、Revision/Dirty 检查通过时，才执行：

```text
ue_plan_patch
→ ue_dry_run_patch
→ 向用户展示具体 Diff / 目标 Asset / 风险，取得明确授权
→ ue_apply_patch（使用本会话产生的有效 Receipt/确认）
→ 按工作流执行显式 Save（如必要）
→ ue_verify_asset（独立验证）
→ 返回已保存/已验证与失败项
```

不要从自然语言猜测 Receipt、Revision、确认字符串或 Backup 引用。Dry Run 通过不等于允许 Commit；Live Apply 成功不等于磁盘已保存，保存成功也不等于独立验证通过。

失败时**停止进一步写入**，返回错误码和当前状态；满足条件时按 [备份与回滚契约](../spec/BACKUP_AND_ROLLBACK.md) 执行回滚 Dry Run → 显式确认 → Commit → 验证。服务端重启或索引快照刷新后，原会话 Receipt 可能失效，应重新规划并复核。

## 5. 发布状态与进一步阅读

- [当前正式版本的功能边界](PROJECT_STATUS.md)
- [详细 AI 使用指南](AI_USAGE.md)
- [程序开发与 PIE 诊断示例](DEVELOPER_WORKFLOWS.md)
- [MCP Tool 协议与调用边界](../spec/MCP_SERVER.md)
- [安装、配置与首次验证](BUILD_AND_RUN.md)
- [固定项目与 Write Policy](PROJECT_LEVEL_CONFIG.md)

本文是通用接入流程，不证明某个实验能力已经在你的服务器上可用。最终以当前已部署版本、`ue_get_capabilities`、服务端门禁和独立验证结果为准。
