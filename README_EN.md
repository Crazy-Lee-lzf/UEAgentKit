# UE Agent Kit

[中文](README.md)

UE Agent Kit is an open-source AI development toolkit for Unreal Engine. It combines a UE Editor plugin, a Python CLI, a SQLite project index, and a future MCP bridge so AI agents can inspect, search, modify, validate, and roll back Unreal Engine project changes through controlled workflows.

The current implementation is still read-only and focused on Blueprint analysis. It does not modify or save `.uasset` files.

## Current capabilities

- Read Blueprint classes, parents, interfaces, variables, defaults, components, and functions.
- Export graphs, nodes, pins, and complete link relationships.
- Detect inheritance, interface implementation, variable reads/writes, function and macro calls, interface messages, dynamic casts, and delegate relationships.
- Model event dispatchers, events, function entries, member variables, and local variables as queryable symbols.
- Distinguish input, output, return, and in-out parameters, including value/reference and const semantics, and record upstream return-value sources.
- Resolve delegate creation, binding, unbinding, and broadcasting, including dispatcher and handler relationships.
- Export Asset Registry hard and soft package dependencies with Game, EditorOnly, Build, and related properties, with reverse dependency queries.
- Generate stable asset revisions and SHA-256 fingerprints.
- Normalize UE load-time transient pin IDs and Property Bag object names so Canonical JSON and BPCTX remain reproducible across editor processes.
- Export Canonical JSON, BPCTX/1, and manifests.
- Build a SQLite/FTS project index.
- Search assets, symbols, and references through the CLI.
- Support incremental indexing, project identity isolation, Unicode paths, and offline environments.

The project has been compiled and validated against real UE 5.6 projects.

## Long-term goal

```text
Inspect project
→ Locate relevant assets and logic
→ Generate a declarative change plan
→ Dry run
→ Create, update, or delete UE content
→ Compile and validate dependencies
→ Produce a structured diff
→ Explicitly save
→ Roll back on failure
```

The long-term scope includes Blueprint, Widget Blueprint, Anim Blueprint, Control Rig, Material, Niagara, DataTable, Behavior Tree, StateTree, and other Unreal Editor asset types.

## Quick start

Build the plugin:

```bat
scripts\BuildPluginDirect.cmd
```

Run an export:

```bat
scripts\RunExport.cmd -Asset "/Game/Folder/BP_Name" -Profile logic -Format both
```

Build and query the index:

```bat
scripts\ue-agent.cmd index build --export-root Output\Export
scripts\ue-agent.cmd index stats
scripts\ue-agent.cmd search assets Door
scripts\ue-agent.cmd search symbols MaxWalkSpeed
```

See [`docs/BUILD_AND_RUN.md`](docs/BUILD_AND_RUN.md) for details.

## Safety model

- Never edit `.uasset` binaries directly.
- Future write operations default to dry-run mode.
- Asset revisions are checked before applying patches.
- Compile errors, revision conflicts, or backup failures block saving.
- Production projects remain read-only by default; mutation tests run only in explicitly authorized sandboxes.

## License

UE Agent Kit is licensed under the [MIT License](LICENSE).

The project follows an independent-implementation policy. Third-party repositories are primarily used as architecture, workflow, and Unreal API references rather than copied source code. Any redistributed dependency will be documented with its exact version, license, source, and hash.

UE Agent Kit is an independent open-source project and is not affiliated with, endorsed by, or sponsored by Epic Games, Inc. Unreal and Unreal Engine are trademarks or registered trademarks of Epic Games, Inc. in the United States and elsewhere.
