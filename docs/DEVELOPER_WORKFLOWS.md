# UE Agent Kit：程序开发工作流

[English](DEVELOPER_WORKFLOWS_EN.md)

**先调用 `ue_get_capabilities`。** 最新公开预发布为 0.9.0 DP1（旧稳定版为 0.8.0）；下面的 Live/实验工具只在已部署服务器实际注册并启用时可用。

## Blueprint 与 C++ 查询

1. 使用 `ue_search` 找到资产或符号，再以 `ue_get_asset`、`ue_find_references` 获取具体对象和引用。
2. 查询 C++ 时，可用 `ue_search(scope="symbols", profile="code")`，需要函数时增加 `kind="function"`；用返回的稳定 Symbol ID 查静态调用来源。
3. 如果需要当前 Blueprint 图，在 Live Editor 中调用 `ue_get_focused_blueprint_graph_context()`；没有聚焦图时不猜测节点身份。

离线索引是保存时的快照，Live Editor 代表当前内存；C++ `calls` 是**静态调用点证据**，不能证明运行时实际执行。详见 [C++ 查询](CODE_KNOWLEDGE.md)。

## PIE Server/Client 只读排障

固定项目的 MCP 必须启用 Live Editor、Editor 正在 PIE。有索引 Live 与**无索引 Pure Live** 路径有候选验收证据；实际可用性仍以服务器的 `ue_get_capabilities` 与已注册 Tool 为准，不将候选证据等同于对全部发布用例的保证。

```text
ue_get_pie_state()
ue_get_pie_world_contexts(max_contexts=16)
ue_inspect_pie_actor(
    pie_instance_id=<World 列表返回的整数>,
    actor_path="<该 PIE World 的精确 Actor 路径>"
)
```

`ue_inspect_pie_actor` 的 `actor_path` / `actor_guid` / `actor_name` / `actor_label` **只能选一个**。从 World 列表选择确切的 Server/Client `pieInstanceId`，不要猜测；必要时传 `world_path`。返回 Actor 类、Transform、NetRole 及有界组件信息，不提供运行时 Pin 或完整 GAS 状态。

如需读取运行时属性，候选提供只读 ue_get_pie_scalars：指定精确 PIE instance、Actor 身份、可选一个组件及 1–8 个标量属性名。已解析属性包含反射声明类来源；C++ 继承属性已通过原生测试，但 Blueprint 自声明属性和 PIE Client 标量值尚未等量验收。遇到 `pie-not-running`、`pie-instance-stale`、`pie-actor-ambiguous` 等错误，重新枚举实例，不使用过期数据。

## Blueprint 和配置修改

**稳定配置写入**：按当前工具 Schema 核对目标、Revision 和最小 Write Policy，走 Plan → Dry Run → 用户明确确认 → Apply → 显式 Save → 独立 Verify。DataTable、DataAsset、Material Instance 的受支持字段以实际能力为准，P4 Submit 仍需人工完成。

**实验性 Blueprint GraphEdit**：默认关闭，**不受稳定 Write Policy 白名单约束**。仅隔离测试工程可同时设置 MCP `--enable-experimental-graphpatch-edit` 与工程 `[UEAgentKit] ExperimentalGraphPatchEdit=True`，并使用 Preview 返回的精确确认值。只支持有限节点/连线操作，不提供任意 Graph CRUD、自动编译或 Save All。任何 Dirty/PIE/身份/Revision 拒绝都不得绕过。

候选还包括有界 Kismet CallFunction 与同一 Blueprint 自有 Pure Function 调用，允许为新节点的未连接输入设定基础类型常量；仅 int 已完成原生输入常量验收。实验双门禁仍默认关闭，必须校验精确函数、签名和 Pin，并显式保存。

更多：[Agent Quickstart](AGENT_QUICKSTART.md) · [实验 GraphEdit 示例](BLUEPRINT_AGENT_E2E_USAGE.md)。
