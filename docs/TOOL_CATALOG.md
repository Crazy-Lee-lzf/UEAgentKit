# Tool 目录（面向用户与 AI Agent）

实际可调用的 Tool 及其输入/返回 Schema，**始终以当前服务器的 `ue_get_capabilities` 和 MCP 工具定义为准**。下面只列入口与归属，不承诺每个部署都注册同一组。

## 工具档位（`--tool-profile`）

工具数量会直接影响每个新会话的上下文体积，所以 MCP 启动时用**档位**限制可见工具面。
档位是**上限**：它只会隐藏，不会打开任何被开关关掉的组。

| 档位 | 可见分组 | 工具数 | 适用 |
|---|---|---:|---|
| `core`（默认） | query | **15** | 只做检索/分析/能力发现 |
| `read` | + memory | 29 | 需要 Project Memory 的只读会话 |
| `live` | + live-read、realtime | **60** | 连 Editor 做只读诊断（PIE / 图 / 资产） |
| `live-write` | + live-action | 70 | 需要受控写入（原子 patch、Niagara 参数） |
| `full` | + workflow、source-control | 137 | 离线批处理 / 源控；仍需各自的开关 |

```powershell
python scripts\ue-agent-mcp.py --enable-live-editor --tool-profile live ...
```

`ue_get_capabilities` 的 `toolProfile` 字段回报当前档位。档位与既有开关是**交集**：
例如 `--tool-profile full` 但没有 `--enable-live-editor`，仍然看不到 live 工具。

## 读取

| 领域 | 代表性 Tool | 说明 |
|---|---|---|
| 能力与状态 | `ue_get_capabilities`、`ue_get_project_status`、`ue_get_subprocess_ledger` | 先发现能力；ledger 用于确认有没有启动子进程 |
| 资产 / 符号 / 引用 | `ue_search`、`ue_get_asset`、`ue_find_references` | 索引查询；`ue_get_asset` 可能带 overlay 信息 |
| 影响与验证 | `ue_analyze_change_impact`、`ue_analyze_semantic_diff`、`ue_build_verification_plan`、`ue_evaluate_trust_verdict` | 只读分析 |
| 离线 GraphPatch 预览 | `ue_preview_graph_patch` | 只验证 IR，不连接 Editor、不写入 |
| Live Editor | `ue_editor_status`、`ue_get_selection`、`ue_get_open_assets`、`ue_get_dirty_assets`、`ue_get_current_level` | 当前 Editor 状态 |
| Live 图 / PIE | `ue_get_blueprint_graph_context`、`ue_get_focused_blueprint_graph_context`、`ue_get_blueprint_graph_layout_preflight`、`ue_pie` | 只读；PIE 全部走 `ue_pie(action=...)`：`state`、`worlds`、`list_actors`、`inspect_actor`、`scalars`、`list_properties`、`read_properties`（原 7 个逐动词 Tool 已合并，不再单独注册）。`list_actors` 按 object path 稳定排序并分页，Name/Class/Tag 为大小写不敏感子串过滤，Server/Client 世界分开；`scalars` / `list_properties` / `read_properties` 走反射（不以 Edit/BlueprintVisible 为门槛），容器按 `offset`/`limit` 分页，对象引用只返回身份（`objectRef.path` 可直接作为下一跳的 `objectPath`，逐跳显式调用、跨世界引用标记 `crossWorld` 并拒绝继续），不支持的类型返回 `unsupported-type` + 原因 |
| 资产 / 日志 | `ue_inspect_asset_live`、`ue_get_output_log`、`ue_get_compile_errors` | 只读诊断 |
| Niagara Stack | `ue_get_niagara_module_stack` | 按真实执行阶段只读 Module / Input 与来源 |
| Project Memory | `ue_memory_search`、`ue_memory_get`、`ue_memory_get_context`、`ue_memory_expand_node`、`ue_memory_get_evidence` | 需要 `--enable-project-memory` |
| 代码知识 | `ue_search(scope="symbols", profile="code")` | C++ 符号与静态调用点 |

## 写入

| 领域 | 代表性 Tool | 门禁 |
|---|---|---|
| 稳定 patch / Save / Verify | `ue_plan_patch`、`ue_dry_run_patch`、`ue_apply_patch`、`ue_verify_asset`、`ue_rollback_patch` | workflow 双门禁 + Write Policy |
| 配置写入 | `ue_set_datatable_*`、`ue_set_asset_*`、`ue_set_material_parameter`、`ue_set_blueprint_default`、`ue_set_component_property`、`ue_set_pin_default` | workflow 双门禁 + Write Policy |
| Live 单资产修改 | `ue_apply_asset_property_live`、`ue_undo_asset_property_live`、`ue_discard_asset_property_live`、`ue_save_authorized_asset` | workflow 双门禁 + Write Policy |
| Niagara Module Input | `ue_set_niagara_module_input` | 仅 Local 常量；一次修改一次 Undo；不自动 Save |
| 位置批量移动 / edit session | `ue_move_blueprint_node`、`ue_move_blueprint_nodes`、`ue_begin_blueprint_edit_session`、`ue_end_blueprint_edit_session` | workflow 双门禁 |
| 实验 Blueprint patch | `ue_preview_blueprint_patch_experimental`、`ue_apply_blueprint_patch_experimental`、`ue_save_blueprint_patch_experimental` | `--enable-experimental-graphpatch-edit` + ini |
| Action Registry 节点创建 | `ue_search_node_actions`（只读）+ `createNode` 的 `actionId` | 再加 `--enable-experimental-node-spawn` + ini |
| 实时索引 | `ue_refresh_asset_index_live` | 需要 Live Editor；不写索引 |
| Source Control | `ue_source_control_status`、`ue_source_control_prepare_write`、`ue_source_control_changelists`、`ue_source_control_prepare_changelist`、`ue_source_control_resolve_status`、`ue_source_control_resolve_text` | `--enable-source-control` + P4 CLI |

`workflow` 组只有在 MCP `--enable-write-tools`、`--enable-subprocess-workflow`、项目 `[UEAgentKit] SubprocessWorkflow=True` 同时成立时才会注册。实验 patch 与 Source Control 使用各自的门禁，不经过 Write Policy。

## 专用工具文档

| 功能领域 | 文档 |
|---|---|
| Additive 动画诊断与求值 | [Diagnosis](ADDITIVE_ANIMATION_DIAGNOSIS_TOOL.md) · [Evaluation](ADDITIVE_ANIMATION_EVALUATION_TOOL.md) |
| Additive Base Pose 修复 | [Plan](ADDITIVE_BASE_POSE_FIX_PLAN_TOOL.md) · [Write](ADDITIVE_BASE_POSE_FIX_WRITE_TOOL.md) |
| 动画比例审计 / 修复 | [Audit](ANIMATION_SCALE_AUDIT_TOOL.md) · [Fix](ANIMATION_SCALE_FIX_TOOL.md) · [Batch](ANIMATION_SCALE_FIX_BATCH_TOOL.md) |
| 角色接地 / 次级运动 | [Ground Contact](CHARACTER_GROUND_CONTACT_TOOL.md) · [Secondary Motion](SKELETAL_SECONDARY_MOTION_TOOL.md) |
| 重定向后处理 | [Retarget Postprocess](RETARGET_POSTPROCESS_TOOL.md) |
| Blueprint 实验写入 | [Blueprint 实验用法](BLUEPRINT_AGENT_E2E_USAGE.md) |

## 调用规则

- 先 `ue_get_capabilities`；缺失的 Tool 不能假装存在。
- 严格按当前 Schema 填参数；不猜 ID、路径、Receipt 或确认字符串。
- 只读诊断、规划、Editor 内存修改、Save / Commit 分开。
- 返回实际错误码和阻塞条件，不绕过 Dirty / Policy / Revision / Session 门禁。
- 修改失败时按对应文档执行 Undo / Discard / rollback，不继续叠加写入。
