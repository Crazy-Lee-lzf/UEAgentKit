# UE Agent Kit: Developer Workflows

[中文](DEVELOPER_WORKFLOWS.md)

**Call `ue_get_capabilities` first.** The stable release is 0.8.0; the latest prerelease is 0.9.0 Developer Preview 1; 0.9.0 Developer Preview 2 is still a candidate. The live and experimental tools below require registration on the deployed server.

## Find Blueprint and C++ code

1. Locate assets or symbols with `ue_search`; inspect them with `ue_get_asset` and `ue_find_references`.
2. For C++ symbols, use `ue_search(scope="symbols", profile="code")` and optionally `kind="function"`; use returned stable Symbol IDs to find static call sites.
3. To read the current Blueprint graph, use `ue_get_blueprint_graph_context(asset_path=...)` when you have the exact asset path, or `ue_get_focused_blueprint_graph_context()` when a graph is focused. Never guess a node when the graph identity is unknown.

An offline index is a saved snapshot; Live Editor describes in-memory state. C++ `calls` are **static call-site evidence**, not proof of runtime dispatch. See [C++ queries](CODE_KNOWLEDGE.md).

## Read-only PIE Server/Client inspection

The MCP server must be pinned to the project with Live Editor enabled and PIE running. Both indexed Live and **indexless pure-Live** startup are supported; actual availability depends on `ue_get_capabilities`.

```text
ue_get_pie_state()
ue_get_pie_world_contexts(max_contexts=16)
ue_list_pie_actors(
    world_context_id="<contextId from the World list>",   # or pie_instance_id=<integer>
    offset=0, limit=50,
    name_contains="", class_contains="", tag_contains=""
)
ue_inspect_pie_actor(
    pie_instance_id=<integer from the World list>,
    actor_path="<exact Actor path in that PIE World>"
)
```

`ue_list_pie_actors` lists Actor summaries (path/name/class/transform/NetRole/owner/tags) inside exactly one PIE World, ordered by object path and paginated; the name/class/tag filters are case-insensitive substrings and Server and Client worlds are never merged. `totalCount`/`truncated` say whether the page is the whole set; keep the same `worldContextId` and filters to reproduce the next page.

Pass **only one** of `actor_path`, `actor_guid`, `actor_name`, and `actor_label`. Choose the exact Server/Client `pieInstanceId` returned by the World list; do not guess. `world_path` can further constrain identity. The result includes Actor class, Transform, NetRole and bounded Component information, not runtime pin values or complete GAS state.

For runtime values, use read-only `ue_get_pie_scalars`: specify an exact PIE instance and Actor identity, optionally one Component, and 1–8 scalar property names. Resolved properties can identify their reflected declaring class. Inherited C++ ownership has native acceptance; Blueprint-declared provenance and PIE client scalar values are not equally covered. Report `pie-not-running`, `pie-instance-stale`, and `pie-actor-ambiguous` accurately and re-enumerate after restarts.

## Runtime properties and object navigation (PIE)

```text
ue_get_pie_world_contexts()                       # get the exact pieInstanceId first
ue_list_pie_object_properties(pie_instance_id=<int>, actor_path="<actor path>", name_contains="")
ue_read_pie_object_properties(
    pie_instance_id=<int>, actor_path="<actor path>",
    property_names=["Health", "Inventory", "OwnerState"], offset=0, limit=50
)
# the objectRef.path returned above can be the next hop target
ue_read_pie_object_properties(pie_instance_id=<int>, object_path="<objectRef.path>", property_names=["bIsActive"])
```

`ue_list_pie_object_properties` only lists names and kinds; `ue_read_pie_object_properties` reads values. `BlueprintVisible` / `Edit` is not a read gate, and unsupported types return `unsupported-type` with a reason instead of a value - never treat that as zero or empty.

Containers page with `offset` / `limit` and report `totalCount` / `hasMore`. Object references return `objectRef` (path/class/world) and are never expanded automatically; use `objectRef.path` as `object_path` for the next hop. A reference whose `worldKey` differs is refused.

## Niagara module stacks

```text
ue_get_niagara_module_stack(system_path="/Game/Dev/NS_DevStack.NS_DevStack", include_inputs=True)
ue_set_niagara_module_input(
    system_path="/Game/Dev/NS_DevStack.NS_DevStack", emitter_name="Emitter A",
    module_name="Spawn Rate", input_name="SpawnRate", value=25.0, expected_value_mode="Local"
)
```

Modules come back in engine order by execution stage - do not sort them. The `source` of an Input decides whether it can be written: only `Local` is editable. `ue_set_niagara_module_input` is one transaction per edit (Undo/Redo works), `saved=false` means an explicit Save is still required, Linked/Dynamic sources are refused with their `linkedTarget`, and the shared Module Script asset is always read-only.

## Blueprint and configuration changes

**Stable writes**: confirm the workflow gates (`--enable-write-tools` + `--enable-subprocess-workflow` + project `SubprocessWorkflow=True` + Write Policy), then Plan → Dry Run → explicit user approval → Apply → explicit Save → independent Verify. Check supported DataTable, DataAsset and Material Instance fields against the runtime Schema. P4 Submit remains a human action.

**Experimental atomic Blueprint patch**: off by default and **outside the stable Write Policy allowlist**. Only isolated test projects may enable MCP `--enable-experimental-graphpatch-edit` and project `[UEAgentKit] ExperimentalGraphPatchEdit=True`, using the exact confirmations returned by Preview. The seven operations are `createNode` / `connect` / `disconnect` / `setPinDefault` / `createVariable` / `rewirePin` / `removeNode`; one Apply is one transaction with rollback on failure, and Save is separate. It requires a clean package, stopped PIE, and exact graph identity; it provides no arbitrary graph CRUD, auto-compilation, or Save All.

**Action Registry node creation**: call `ue_search_node_actions` for an `actionId`, then use it in `createNode`. It can create `K2Node_CallFunction` (engine, project, or Blueprint-defined functions) and `K2Node_DynamicCast`. It requires `--enable-experimental-node-spawn` and project `ExperimentalNodeSpawn=True`. CustomEvent, event binding, Timeline, MathExpression, and `Select` / `PromotableOperator` themselves cannot be created this way.

**User and Agent editing the same graph**: position batches / edit sessions accept a dirty package and check per-node expected coordinates; the structural patch still requires a clean package. Rollback restores only nodes the Agent wrote last and the user has not changed.

See [Agent Quick Start](AGENT_QUICKSTART_EN.md) and [Blueprint experimental usage](BLUEPRINT_AGENT_E2E_USAGE_EN.md).
