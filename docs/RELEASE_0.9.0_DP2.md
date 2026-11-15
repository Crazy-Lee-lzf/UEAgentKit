# UE Agent Kit 0.9.0 Developer Preview 2（候选）

**状态：候选，尚未发布。** 计划版本号为 `0.9.0.dev2`；发布前会在发布提交中统一 bump，当前 checkout 的版本号以 `pyproject.toml` / plugin 为准。没有 GitHub Tag 或 Release。最新公开预发布仍是 [0.9.0 Developer Preview 1](RELEASE_0.9.0_PREVIEW.md)；稳定版仍是 [0.8.0](RELEASE_0.8.0.md)。本页描述候选代码，不是生产授权。

## DP2 相对 DP1 的变化

- **统一原子 Blueprint patch**：`createNode` / `connect` / `disconnect` / `setPinDefault` / `createVariable` / `rewirePin` / `removeNode` 七种操作合并到同一组工具（`ue_preview/apply/save_blueprint_patch_experimental`）。一次 Apply 一个 Editor 事务，逐操作返回结果，失败回滚已执行部分，并做 Undo/Redo 校验；Save 仍然是独立动作。
- **Action Registry 节点创建**：`createNode` 可以用 `ue_search_node_actions` 返回的 `actionId` 创建 `K2Node_CallFunction` 与 `K2Node_DynamicCast`。可调用引擎函数、项目 C++ `BlueprintCallable` 和蓝图自建函数；Cast 目标类由引擎 spawner 决定。搜索会提前返回 `canCreate` / `rejectReason`。
- **Wildcard 连线**：`Select` / `PromotableOperator` 的 wildcard 端点可以通过 `connect` 连接，由 K2 schema 在写入时推导类型；结果里的 `resolvedPins` 是连接后的实际 pin 快照。`createNode` 仍不能创建这两类节点。
- **live-only 写入启动**：没有 SQLite 索引或 Revision Export 的机器，可以在 Live Editor 上启动写入模式。bridge-native 写入（如节点位置、edit session）不要求索引；patch 类写入仍会在使用时要求 Revision Export。代价是失去离线 Revision 证据。
- **实时索引 overlay**：`ue_get_asset` 命中会话级 overlay 时，会先用磁盘 Package SHA-256 校验记录；磁盘变化则自动向 Editor 重新取证。失败时返回 `overlayStale` / `staleCause`，不返回旧 canonical。`ue_refresh_asset_index_live` 可显式刷新单个 Blueprint，不写索引、不要求重启。
- **用户与 Agent 同时编辑**：位置批量移动 / edit session 可以在 Dirty Package 上开始，按节点期望坐标检测冲突，rollback 只还原 Agent 最后写入且用户未改的节点，并报告跳过的节点。结构 patch 仍要求干净 Package，两者边界不同。
- **子进程 workflow 收窄**：`workflow` 组默认不注册，需要 MCP `--enable-subprocess-workflow` 与项目 `[UEAgentKit] SubprocessWorkflow=True` 同时满足。每次 spawn 尝试记入会话内有界 ledger，可用只读的 `ue_get_subprocess_ledger` 查看。
- **旧 writer 工具移除**：pin-default / variable / rewire / node-remove 的独立 preview/apply/save 与 graph-edit 外壳不再注册；能力并入原子 patch。

## 仍需注意

- DP2 的核心新能力仍然是**默认关闭的实验能力**，不是生产默认。
- 实验 patch 不走稳定 Write Policy 白名单；只接受精确资产/图身份、干净 Package、非 PIE、版本/指纹校验和有界操作。
- 已验证的 patch 范围是顶层 K2 事件图；不写嵌套子图。
- `RunMcp.cmd` 目前只覆盖只读与 Pure Live；写入、实验和 P4 模式需要直接运行 `.venv\Scripts\python.exe scripts\ue-agent-mcp.py`。完整参数见 [构建与运行](BUILD_AND_RUN.md)。

## 开启方式

```text
稳定写入：--enable-write-tools + --enable-subprocess-workflow
          + 项目 [UEAgentKit] SubprocessWorkflow=True
          + 覆盖目标的 Write Policy

实验 patch：--enable-experimental-graphpatch-edit
          + 项目 [UEAgentKit] ExperimentalGraphPatchEdit=True

Registry 创建：再加 --enable-experimental-node-spawn
          + 项目 [UEAgentKit] ExperimentalNodeSpawn=True
```

## 环境

Unreal Engine 5.6 / Windows x64 / Python 3.11–3.14。能力以冻结构建的 `ue_get_capabilities` 为准；DP2 尚未发布，不提供安装包或 SHA-256。

用法与限制见 [Blueprint 实验用法](BLUEPRINT_AGENT_E2E_USAGE.md)，能力边界见 [能力状态](PROJECT_STATUS.md)。
