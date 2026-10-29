# UE Agent Kit: Developer Workflows

[中文](DEVELOPER_WORKFLOWS.md)

**Call `ue_get_capabilities` first.** The latest public prerelease is v0.9.0-dev1; v0.8.0 remains the previous stable release. Live and experimental tools below must be registered on the deployed server.

## Find Blueprint and C++ code

1. Locate assets or symbols with `ue_search`; inspect them with `ue_get_asset` and `ue_find_references`.
2. For C++ symbols, use `ue_search(scope="symbols", profile="code")` and optionally `kind="function"`; use returned stable Symbol IDs to find static call sites.
3. For the focused Blueprint, call `ue_get_focused_blueprint_graph_context()` in Live Editor. Never guess a node when no graph is focused.

An offline index is a saved snapshot; Live Editor describes in-memory state. C++ `calls` are **static call-site evidence**, not proof of runtime dispatch. See [C++ queries](CODE_KNOWLEDGE.md).

## Read-only PIE Server/Client inspection

The MCP server must be pinned to the project with Live Editor enabled and PIE running. Indexed Live and **indexless pure-Live** startup have candidate acceptance evidence. Actual availability depends on the deployed server's `ue_get_capabilities` and registered tools; candidate testing does not guarantee every final-package workflow.

```text
ue_get_pie_state()
ue_get_pie_world_contexts(max_contexts=16)
ue_inspect_pie_actor(
    pie_instance_id=<integer from the World list>,
    actor_path="<exact Actor path in that PIE World>"
)
```

Pass **only one** of `actor_path`, `actor_guid`, `actor_name`, and `actor_label`. Choose the exact Server/Client `pieInstanceId` returned by the World list; do not guess. `world_path` can further constrain identity. The result includes Actor class, Transform, NetRole and bounded Component information, not runtime pin values or complete GAS state.

For runtime values, the candidate offers read-only ue_get_pie_scalars: specify an exact PIE instance and Actor identity, optionally one Component, and 1–8 scalar property names. Resolved properties can identify their reflected declaring class. Inherited C++ ownership passed native validation; Blueprint-declared provenance and PIE client scalar values remain unverified. Report `pie-not-running`, `pie-instance-stale`, and `pie-actor-ambiguous` accurately and re-enumerate after restarts.

## Blueprint and configuration changes

**Stable configuration writes:** verify the target, Revision and narrow Write Policy against the registered Schema; Plan → Dry Run → explicit user approval → Apply → explicit Save → independent Verify. Check supported DataTable, DataAsset and Material Instance fields at runtime. P4 Submit remains a human action.

**Experimental Blueprint GraphEdit:** off by default and **outside the stable Write Policy allowlist**. Only isolated test projects may enable both MCP `--enable-experimental-graphpatch-edit` and project `[UEAgentKit] ExperimentalGraphPatchEdit=True`, using the exact confirmations returned by Preview. Only bounded nodes/connections are supported: no arbitrary Graph CRUD, auto-compilation, or Save All. Never bypass dirty/PIE/identity/Revision rejection.

The candidate also includes constrained Kismet CallFunction and same-Blueprint-owned Pure Function nodes with typed literals on newly created, unlinked inputs. Only an int literal has native acceptance. Keep double opt-in off by default, check exact function/signature/pin identity, and explicitly Save.

See [Agent Quick Start](AGENT_QUICKSTART_EN.md) and [Experimental GraphEdit](BLUEPRINT_AGENT_E2E_USAGE.md).
