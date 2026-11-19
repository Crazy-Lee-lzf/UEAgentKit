# UE Agent Kit 0.9.0（稳定版）

**状态：已发布（2026-11-19）。** 版本号 `0.9.0`，`pyproject.toml`、`UEAgentKit.uplugin` 与插件内各处版本字段一致。上一个版本是 [0.9.0 Developer Preview 1](RELEASE_0.9.0_PREVIEW.md)（`v0.9.0-dev1` 预发布），再往前是稳定版 [0.8.0](RELEASE_0.8.0.md)。

## 相对 0.9.0.dev1（DP1）的变化

- **工具面收窄（对 MCP 客户端是破坏性变更）**：81 个逐动词 Tool 名字不再注册，改为 12 个家族入口，见下面「工具面」一节。连接后不再一次涌出全量工具，而由档位/分组决定可见面。
- **统一原子 Blueprint patch**：`createNode` / `connect` / `disconnect` / `setPinDefault` / `createVariable` / `rewirePin` / `removeNode` 七种操作合并到同一组工具（`ue_preview_blueprint_patch_experimental` / `ue_apply_blueprint_patch_experimental` / `ue_save_blueprint_patch_experimental`）。一次 Apply 一个 Editor 事务，逐操作返回结果，失败回滚已执行部分，并做 Undo/Redo 校验；Save 仍然是独立动作。
- **Action Registry 节点创建**：`createNode` 可以用 `ue_search_node_actions` 返回的 `actionId` 创建 `K2Node_CallFunction` 与 `K2Node_DynamicCast`。可调用引擎函数、项目 C++ `BlueprintCallable` 和蓝图自建函数；Cast 目标类由引擎 spawner 决定。搜索会提前返回 `canCreate` / `rejectReason`。
- **Wildcard 连线**：`Select` / `PromotableOperator` 的 wildcard 端点可以通过 `connect` 连接，由 K2 schema 在写入时推导类型；结果里的 `resolvedPins` 是连接后的实际 pin 快照。`createNode` 仍不能创建这两类节点。
- **live-only 写入启动**：没有 SQLite 索引或 Revision Export 的机器，可以在 Live Editor 上启动写入模式。bridge-native 写入（如节点位置、edit session）不要求索引；patch 类写入仍会在使用时要求 Revision Export。代价是失去离线 Revision 证据。
- **实时索引 overlay**：`ue_get_asset` 命中会话级 overlay 时，会先用磁盘 Package SHA-256 校验记录；磁盘变化则自动向 Editor 重新取证。失败时返回 `overlayStale` / `staleCause`，不返回旧 canonical。`ue_refresh_asset_index_live` 可显式刷新单个 Blueprint，不写索引、不要求重启。
- **用户与 Agent 同时编辑**：位置批量移动 / edit session 可以在 Dirty Package 上开始，按节点期望坐标检测冲突，rollback 只还原 Agent 最后写入且用户未改的节点，并报告跳过的节点。结构 patch 仍要求干净 Package，两者边界不同。
- **子进程 workflow 收窄**：`workflow` 组默认不注册，需要 MCP `--enable-subprocess-workflow` 与项目 `[UEAgentKit] SubprocessWorkflow=True` 同时满足。每次 spawn 尝试记入会话内有界 ledger，可用只读的 `ue_get_subprocess_ledger` 查看。
- **旧 writer 工具移除**：pin-default / variable / rewire / node-remove 的独立 preview/apply/save 与 graph-edit 外壳不再注册；能力并入原子 patch。

## 工具面（本次的主要变化之一）

0.9.0.dev1 时代每个动作一个工具，连接后一次涌出百余个定义。0.9.0 把它收成两类：

**一、家族入口。** 十二个家族合成 `action` 入口，共有 **81 个旧名字不再注册、也不再出现在 `ue_get_capabilities` 里**：

| 入口 | 动作 |
|---|---|
| `ue_pie` | `state` / `worlds` / `list_actors` / `inspect_actor` / `scalars` / `list_properties` / `read_properties` |
| `ue_datatable` | `set_cell` / `set_row_fields` / `add_row` / `remove_row` / `rename_row` |
| `ue_memory` / `ue_memory_write` | 7 个只读动作 / 记录写入与 knowledge·work 子动作 |
| `ue_patch` | `plan` / `dry_run` / `apply` / `verify` / `rollback` / `state` / `refresh_index` |
| `ue_change_set` | `create` / `get` / `save_checkpoint` / `verify_checkpoint` |
| `ue_live_write` | `plan_batch` / `apply_batch` / `recover_batch` / `verify` / `verify_fast` / `verify_checkpoint` |
| `ue_asset_property` | `set` / `set_reference` / `set_structured` / `apply` / `undo` / `discard` 等 |
| `ue_set_property` | `blueprint_default` / `component_property` / `pin_default` / `material_parameter` |
| `ue_animation_scale_fix` | 比例审计修复的单资产与批量动作 |
| `ue_animation_retarget` | 重定向分析与后处理动作 |
| `ue_blueprint_edit` | `move_node` / `move_nodes` / `begin_session` / `end_session` / `save` |

参数、默认值、Plan/DryRun 回执与返回结构不变；成功结果多一个 `action` 字段回显本次动作。

**二、档位与会话可见面。** `--tool-profile` 限制一次会话能看到多少工具，`--tool-groups` 直接点名分组：

| 选择 | 可见工具数 | 定义体积 |
|---|---:|---:|
| `core`（CLI 默认） | 15 | ≈3.0k token |
| `read` | 17 | |
| `live` | 48 | |
| `live-write`（`RunMcp.cmd` 默认） | 58 | |
| `full` | 74 | ≈14.6k token |
| `--tool-groups query,workflow` | 25 | 预设之外的组合 |

档位只是**上限**：不会打开被开关关掉的组，也不会因为给了档位就让某个组变得可用。
`ue_get_capabilities` 回报 `toolProfile`（给分组时为 `custom`）与 `toolGroups`，
而它列出的工具与 `tools/list` 实际注册的完全一致。

## 仍需注意

- 0.9.0 的实验能力**仍然默认关闭**：Blueprint 结构 patch 与 Action Registry 建节点都需要显式 flag + 项目 ini 双门禁，且只对隔离测试工程开放。
- 实验 patch 不走稳定 Write Policy 白名单；只接受精确资产/图身份、干净 Package、非 PIE、版本/指纹校验和有界操作。
- 已验证的 patch 范围是顶层 K2 事件图；不写嵌套子图。
- `RunMcp.cmd` 目前只覆盖只读与 Pure Live；写入、实验和 P4 模式需要直接运行 `.venv\Scripts\python.exe scripts\ue-agent-mcp.py`。完整参数见 [构建与运行](BUILD_AND_RUN.md)。

## 开启方式

```text
稳定写入：--enable-write-tools + --enable-subprocess-workflow
          + 项目 [UEAgentKit] SubprocessWorkflow=True
          + 覆盖目标的 Write Policy
          + --tool-profile full（或 --tool-groups 点名分组）

实验 patch：--enable-experimental-graphpatch-edit
          + 项目 [UEAgentKit] ExperimentalGraphPatchEdit=True
          + --tool-profile live-write

Registry 创建：再加 --enable-experimental-node-spawn
          + 项目 [UEAgentKit] ExperimentalNodeSpawn=True
```

直接跑 `ue-agent-mcp.py` 时档位默认是 `core`（只有 query 组），忘了给档位就看不到写入工具。

## 安装

从 Release 下载 `UEAgentKit-0.9.0-UE5.6-Win64.zip`，把其中的 `UEAgentKit` 目录复制到项目 `Plugins/`。Python 使用对应的 `ue_agent_kit-0.9.0-py3-none-any.whl`。校验和见 Release 页的 `SHA256SUMS.txt`。

## 环境

Unreal Engine 5.6 / Windows x64 / Python 3.11–3.14。能力以冻结构建的 `ue_get_capabilities` 为准；不同开关下的工具集合不同，升级前先看 [专用 Tool 索引](TOOL_CATALOG.md) 的档位表。

用法与限制见 [Blueprint 实验用法](BLUEPRINT_AGENT_E2E_USAGE.md)，能力边界见 [能力状态](PROJECT_STATUS.md)。
