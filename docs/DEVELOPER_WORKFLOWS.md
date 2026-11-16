# UE Agent Kit：程序开发工作流

[English](DEVELOPER_WORKFLOWS_EN.md)

**先调用 `ue_get_capabilities`。** 稳定版为 0.8.0；最新预发布为 0.9.0 Developer Preview 1；0.9.0 Developer Preview 2 仍是候选，未发布。下面的 Live / 实验工具只在已部署服务器实际注册时可用。

## Blueprint 与 C++ 查询

1. 用 `ue_search` 找到资产或符号，再用 `ue_get_asset`、`ue_find_references` 获取对象和引用。
2. 查询 C++ 时，可用 `ue_search(scope="symbols", profile="code")`，需要函数时加 `kind="function"`；用返回的稳定 Symbol ID 查静态调用来源。
3. 读取当前 Blueprint 图：有精确 asset path 时用 `ue_get_blueprint_graph_context(asset_path=...)`；已有聚焦图时用 `ue_get_focused_blueprint_graph_context()`。没有聚焦图或身份不匹配时不要猜节点。

离线索引是保存时的快照，Live Editor 是当前内存；C++ `calls` 是**静态调用点证据**，不能证明运行时实际执行。详见 [C++ 查询](CODE_KNOWLEDGE.md)。

## PIE Server/Client 只读排障

固定项目的 MCP 必须启用 Live Editor，Editor 正在 PIE。带索引 Live 与**无索引 Pure Live** 都可以启动；实际可用性以 `ue_get_capabilities` 为准。

```text
ue_get_pie_state()
ue_get_pie_world_contexts(max_contexts=16)
ue_list_pie_actors(
    world_context_id="<World 列表返回的 contextId>",   # 或 pie_instance_id=<整数>
    offset=0, limit=50,
    name_contains="", class_contains="", tag_contains=""
)
ue_inspect_pie_actor(
    pie_instance_id=<World 列表返回的整数>,
    actor_path="<该 PIE World 的精确 Actor 路径>"
)
```

`ue_list_pie_actors` 只列一个精确 PIE World 的 Actor 概要（path/name/class/transform/NetRole/owner/tags），按 object path 稳定排序并分页；Name/Class/Tag 都是大小写不敏感子串过滤，Server 与 Client 世界永不混合。`totalCount`/`truncated` 说明这一页是否是全部；翻页时保持同一 `worldContextId` 和过滤条件即可复现。

`ue_inspect_pie_actor` 的 `actor_path` / `actor_guid` / `actor_name` / `actor_label` **只能选一个**。从 World 列表选择确切的 Server/Client `pieInstanceId`，不要猜测；必要时传 `world_path`。返回 Actor 类、Transform、NetRole 及有界组件信息，不提供运行时 Pin 或完整 GAS 状态。

需要运行时属性时用只读 `ue_get_pie_scalars`：指定精确 PIE instance、Actor 身份、可选一个组件及 1–8 个标量属性名。已解析属性包含反射声明类来源；C++ 继承属性已通过原生验收，Blueprint 自声明属性和 PIE Client 标量值尚未等量覆盖。遇到 `pie-not-running`、`pie-instance-stale`、`pie-actor-ambiguous` 时重新枚举实例，不使用过期数据。

## 运行时属性与对象导航（PIE）

```text
ue_get_pie_world_contexts()                       # 先拿到精确 pieInstanceId
ue_list_pie_object_properties(
    pie_instance_id=<整数>, actor_path="<Actor 路径>",
    name_contains=""                              # 可选，过滤属性名
)
ue_read_pie_object_properties(
    pie_instance_id=<整数>, actor_path="<Actor 路径>",
    property_names=["Health", "Inventory", "OwnerState"],
    offset=0, limit=50                            # 容器元素分页
)
# 上一步返回的 objectRef.path 可以直接作为下一跳目标
ue_read_pie_object_properties(
    pie_instance_id=<整数>, object_path="<objectRef.path>",
    property_names=["bIsActive"]
)
```

`ue_list_pie_object_properties` 只列属性名与 kind，不读值；`ue_read_pie_object_properties` 才按名取值。两者都不以 `BlueprintVisible` / `Edit` 作为门槛，读不了的类型返回 `unsupported-type` 与原因且不返回值——**不要**把 unsupported 当成 0 或空。

容器用 `offset`/`limit` 分页，返回 `totalCount` / `hasMore`；对象引用只返回 `objectRef`（path/class/world），**不会**自动展开被引用对象。想继续看就显式再调一次，把 `objectRef.path` 当成 `object_path`。跨世界的引用会标记 `worldKey` 不一致并拒绝，不要绕过。

## Niagara 模块栈

```text
ue_get_niagara_module_stack(system_path="/Game/Dev/NS_DevStack.NS_DevStack", include_inputs=True)
ue_set_niagara_module_input(
    system_path="/Game/Dev/NS_DevStack.NS_DevStack",
    emitter_name="Emitter A", module_name="Spawn Rate", input_name="SpawnRate",
    value=25.0, expected_value_mode="Local"
)
```

`ue_get_niagara_module_stack` 按真实执行阶段返回模块，顺序即引擎顺序，不要自行排序；每个 Input 的 `source` 决定能不能改——只有 `Local` 可以直接写。`ue_set_niagara_module_input` 一次修改一个事务（可 Undo/Redo），`saved=false` 表示还需要显式 Save；来源是 Linked/Dynamic 时会拒绝并给出 `linkedTarget`，共享 Module Script 资产永远只读。

## Blueprint 和配置修改

**稳定写入**：先确认 workflow 门禁（`--enable-write-tools` + `--enable-subprocess-workflow` + 项目 `SubprocessWorkflow=True` + Write Policy），再走 Plan → Dry Run → 用户明确确认 → Apply → 显式 Save → 独立 Verify。DataTable、DataAsset、Material Instance 的受支持字段以实际 Schema 为准；P4 Submit 仍需人工完成。

**实验原子 Blueprint patch**：默认关闭，**不受稳定 Write Policy 白名单约束**。仅隔离测试工程可设置 MCP `--enable-experimental-graphpatch-edit` 与工程 `[UEAgentKit] ExperimentalGraphPatchEdit=True`，并回传 Preview 返回的精确确认值。七种操作是 `createNode` / `connect` / `disconnect` / `setPinDefault` / `createVariable` / `rewirePin` / `removeNode`；一次 Apply 一个事务，失败回滚，Save 独立。只接受干净 Package、非 PIE、精确图身份；不提供任意 Graph CRUD、自动编译或 Save All。

**Action Registry 节点创建**：先 `ue_search_node_actions` 拿 `actionId`，再在 `createNode` 中使用；可创建 `K2Node_CallFunction`（引擎 / 项目 / 蓝图自建函数）与 `K2Node_DynamicCast`。需要 `--enable-experimental-node-spawn` 与工程 `ExperimentalNodeSpawn=True`。CustomEvent、事件绑定、Timeline、MathExpression 以及 `Select` / `PromotableOperator` 本身不能这样创建。

**用户与 Agent 同时编辑**：位置批量移动 / edit session 允许 Dirty Package，按节点期望坐标检测冲突；结构 patch 仍要求干净 Package。rollback 只还原 Agent 最后写入且用户未改的节点。

更多：[Agent Quickstart](AGENT_QUICKSTART.md) · [Blueprint 实验用法](BLUEPRINT_AGENT_E2E_USAGE.md)。
