# AI Agent 快速接入指南

[English](AGENT_QUICKSTART_EN.md)

> 适用于通过 MCP 使用 UE Agent Kit 的用户及 AI Agent。**先确认服务端实际启用的模式，再调用 `ue_get_capabilities` 返回的 Tool**。本文不授权绕过任何项目或写入门禁。

## 1. 先确认运行模式

UE Agent Kit 的 Tool 是**按启动参数动态注册**的。连接后看不到某个 Tool，通常不是 Agent 调用错误，而是服务端没有开对应模式。

| 模式 | 启动方式 | 你应看到 |
|---|---|---|
| 离线只读 | `RunMcp.cmd -Database ...` | query、离线 GraphPatch 预览（Memory 需单独开启） |
| Pure Live 只读 | `RunMcp.cmd -EnableLiveEditor -PureLive -ProjectPath ...` | live-read，无索引、无写入 |
| Live + 索引 | `RunMcp.cmd -EnableLiveEditor -ProjectPath ... -Database ...` | live-read + query |
| 受控写入 | 直接 `.venv\Scripts\python.exe scripts\ue-agent-mcp.py`，开启 write-tools + subprocess + Policy | `workflow` 组 |
| 实验 Blueprint | 直接 `py -m ...`，开启 graphpatch / node-spawn 双门禁 | 原子 patch、节点创建 |
| P4 | 直接 `py -m ... --enable-source-control --project ...` | Source Control |

`RunMcp.cmd` 只覆盖只读和 Pure Live；写入与实验模式的完整命令见 [构建与运行](BUILD_AND_RUN.md)。受控写入需要同时满足：

```text
MCP:    --enable-write-tools --enable-subprocess-workflow
Project: Config/DefaultEngine.ini -> [UEAgentKit] SubprocessWorkflow=True
Policy: 目标资产/操作在 Write Policy 范围内
```

实验 Blueprint 写入不走 Write Policy，另需 `--enable-experimental-graphpatch-edit` + `[UEAgentKit] ExperimentalGraphPatchEdit=True`；创建 Registry 节点再加 `--enable-experimental-node-spawn` + `[UEAgentKit] ExperimentalNodeSpawn=True`。

## 2. 用户一次性接入

安装、插件接入、导出/索引、固定项目和 Policy 的完整命令见 [构建与运行](BUILD_AND_RUN.md)；最短版本在 [README 快速开始](../README.md)。Agent 不需要重复执行这些步骤，只需确认服务端已启动。

## 3. Agent 第一步：发现能力

连接后先调用 `ue_get_capabilities`，确认：

- 当前启用了哪些组（query / live-read / workflow / memory / source-control）。
- `workflow` 是否存在。不存在时，写入类 Tool 一律不可用，应先让用户检查双门禁。
- 实验 patch、`ue_search_node_actions` 是否出现。没出现就不要假装能调用。

不要根据 Markdown 总数、源码里的 handler 或历史 release notes 推断 Tool 已开放。

## 4. 只读查询

```text
ue_get_capabilities
→ ue_get_project_status
→ ue_search（先定位 Asset / Symbol）
→ ue_get_asset（只取必要详情）
→ ue_find_references（确认依赖与影响范围）
→ 需要当前 Editor 状态时，再调用 live-read 工具
```

读取结果要区分：

- **事实**：直接来自 Canonical JSON、BPCTX 或 SQLite。
- **推断**：根据引用、命名、调用关系得出的解释。
- **未知**：当前 Reader 或索引没有导出的字段。

SQLite 是保存时的快照；Live Editor 是当前内存。`ue_get_asset` 如果带有 overlay 信息，注意 `overlayCause`、`overlayRefreshed`、`overlayStale` 与 `staleCause`：`overlayStale=true` 时不能把该 canonical 当作当前磁盘事实。

## 5. 受控写入

**默认不写。** 仅在用户明确要求、服务端已开 workflow 门禁、目标受 Write Policy 覆盖、Revision / Dirty / 身份检查通过时执行：

```text
ue_patch(action="plan")
→ ue_patch(action="dry_run")
→ 向用户展示目标资产、Diff、风险，取得明确授权
→ ue_patch(action="apply")（一次性 Receipt / 精确确认）
→ 显式 Save（如需要）
→ ue_patch(action="verify")（独立验证）
→ 返回已保存/已验证/失败项
```

对已在 Editor 中打开、干净的单个非 Blueprint 资产，也可使用 `ue_asset_property(action="apply")` 修改内存值；它进入 Undo 栈并标记 Dirty，但**不保存**。保存必须另调 `ue_save_authorized_asset`；放弃用 `ue_asset_property(action="undo")` / `ue_asset_property(action="discard")`。

注意：

- 不要从自然语言猜 Receipt、Revision、confirmation 或 Backup 引用。
- Dry Run 通过不等于允许 Commit；Live Apply 成功不等于磁盘已保存；保存成功也不等于独立 Verify 通过。
- 失败时停止继续写入，返回错误码和当前状态；需要回滚时按 [备份与回滚契约](../spec/BACKUP_AND_ROLLBACK.md) 执行 Dry Run → 确认 → Commit → 验证。
- MCP 重启或快照刷新后，旧 Receipt 可能失效，需要重新 Plan。

## 6. 实验 Blueprint 结构写入

实验能力，默认关闭，只能在隔离测试工程使用。最短路径：

```text
ue_search_node_actions（可选，拿 actionId）
→ ue_preview_blueprint_patch_experimental（拿 planId / confirmation）
→ ue_apply_blueprint_patch_experimental（回传 preview 返回的确认）
→ ue_save_blueprint_patch_experimental（单独显式保存）
```

一次 Apply 一个事务；失败会返回逐操作结果并回滚已执行部分。具体操作、限制和错误码见 [Blueprint 实验用法](BLUEPRINT_AGENT_E2E_USAGE.md)。

相关：[当前能力状态](PROJECT_STATUS.md) · [构建与运行](BUILD_AND_RUN.md) · [程序开发工作流](DEVELOPER_WORKFLOWS.md) · [MCP 契约](../spec/MCP_SERVER.md)。
