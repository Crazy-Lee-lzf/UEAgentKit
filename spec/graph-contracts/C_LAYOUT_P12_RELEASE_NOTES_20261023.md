# Graph Layout Stage Preview — 2026-10-23

> This is a **development-stage preview record**, not a UE Agent Kit v0.9/1.0 release and not a production write authorization. The public v0.8.0 capability boundary remains documented in [Capability Status](../../docs/PROJECT_STATUS_EN.md).

## Scope of the experimental stage

- Read-only Blueprint graph context and preflight export.
- A deterministic Python layout-plan algorithm, including graph connectivity, reroute handling and bounded layout selection.
- Test-only Editor menu Preview and position-only Apply validation using disposable UE5.6.1 projects.
- An experimental MCP preview interface where explicitly registered.

The above refers to a specific local development stage. It **does not** guarantee that these functions are present in the public release or enabled by default.

## Safety boundaries

General Blueprint Graph editing, rewiring, Save All and unrestricted position Apply remain unavailable. Test-only Apply requires exact project and asset identity, current Revision/fingerprint, a valid test build marker, and explicit confirmation; the preview itself must not save or change the asset.

Dirty package, PIE/SIE, unsupported graph state, stale Revision and malformed reroute links must be rejected rather than guessed around.

## Verification and limitations

The original acceptance record describes headless UE5.6.1 automation of generated ToolMenus, selection/incremental layout, Undo/Redo and independent reload. Those results applied to **disposable test hosts**, not arbitrary user projects. They did not establish a manual end-user UI test or large-graph performance guarantee.

See [Graph Layout Contracts](README.md) and the [MCP Server contract](../MCP_SERVER.md) for safe discovery of the current interface. Detailed internal test logs and approval records are retained outside the public release tree.
