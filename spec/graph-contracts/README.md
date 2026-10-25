# Graph Layout Contracts — Experimental Preview

> **Status:** local development and test-only. Not part of the published UE Agent Kit v0.8.0 write surface. **No general production Blueprint Graph Apply endpoint is authorized.**

## Purpose and available surfaces

Graph Layout uses a canonical plan to describe bounded, deterministic Blueprint node positions. The current experimental development tree includes read-only graph context/preflight and layout-plan preview paths. Their actual availability depends on the installed plugin, MCP tool registration, project identity and feature flags.

For end users and AI agents, start with:

- [AI Agent Quick Start](../../docs/AGENT_QUICKSTART_EN.md)
- [MCP Server contract](../MCP_SERVER.md)
- [Live Editor Bridge](../LIVE_EDITOR_BRIDGE.md)

Before calling a preview tool, query `ue_get_capabilities`. Do not infer that a method is available because its code or schema exists in the checkout.

The default-off, explicitly opted-in MCP Apply/Save loop is documented in
[Experimental MCP Apply](EXPERIMENTAL_MCP_APPLY.md). It is experimental and
requires both a server flag and an Editor project config opt-in.

The first default-off live createNode/connectPins slice (Branch/Sequence only)
is documented in [Experimental MCP Graph Edit](EXPERIMENTAL_MCP_GRAPH_EDIT.md).
It is not a general GraphPatch executor.

## Contracts and experimental reference implementation

The experimental reference implementation and schema/fixtures are under [`prototype/graph_contracts/`](../../prototype/graph_contracts/). These files remain in the repository because tests import the reference implementation. They do **not** authorize arbitrary writes.

The read-only preview describes a proposed `LayoutPlan`; it does not save or apply graph positions. A disposable-project test-only Apply, where enabled, requires exact project, asset, revision/fingerprint and confirmation checks. Dirty/PIE/stale/cross-project requests fail closed. Reroute nodes and comment frames also have additional safety restrictions.

## Compatibility and limitations

- The public v0.8.0 release does not provide one-click auto-arrange or general Graph Node CRUD.
- A local-only/default-off implementation must not be described as a broadly enabled feature.
- The authoritative interface is the runtime capability list and matching Tool Schema, not historical milestone reports.
- Internal design approvals, task handoffs and real-UE acceptance logs are maintained separately from public documentation.

For offline regression testing without starting Unreal, use the repository Python test runner from a clean checkout. This is not a substitute for real Editor compatibility verification.
