# UE Agent Kit

UE Agent Kit is an open-source toolkit for AI-assisted inspection, search, editing, validation, and automation of Unreal Engine projects.

The project currently provides a read-only UE5.6 Blueprint analysis and indexing foundation. It can export Blueprint structure and logic, build a searchable SQLite/FTS index, and provide compact project context for AI tools.

The long-term goal is to enable AI agents to safely create, inspect, update, and delete Unreal Engine project content through controlled operations with dry-run, compile validation, structured diff, explicit save, and rollback.

## Current capabilities

- UE5.6 Editor-only C++ plugin.
- Single-asset and batch Blueprint export commandlet.
- Blueprint classes, interfaces, variables, defaults, components, functions, graphs, nodes, pins, and links.
- Canonical JSON, BPCTX/1, and manifest output.
- Stable asset revision and SHA-256 fingerprints.
- Symbol and reference extraction for inheritance, interface implementation, variable reads/writes, function calls, and macro calls.
- SQLite/FTS project index with incremental import and project identity isolation.
- CLI search for assets, symbols, references, and indexed Blueprint context.
- Chinese paths and offline development environments.

The current version is read-only and does not modify or save `.uasset` files.

## Project direction

```text
Inspect and search
→ Load minimal project context
→ Generate a declarative change plan
→ Dry run
→ Apply controlled edits
→ Compile and validate
→ Show a structured diff
→ Explicitly save or roll back
```

Planned coverage includes Blueprint, Widget Blueprint, Animation Blueprint, Control Rig, Material, Niagara, DataTable, Behavior Tree, StateTree, and other Unreal Editor assets.

## Naming

`UE Agent Kit` is the top-level project and repository name.

`Blueprint Context` remains the name of the Blueprint-specific subsystem and formats, including `BlueprintContextAnalysis`, `BlueprintContextExportCommandlet`, and `BPCTX/1`.

## Documentation

- [`docs/README.md`](docs/README.md): documentation index.
- [`docs/CURRENT_STATUS.md`](docs/CURRENT_STATUS.md): implementation status and current limitations.
- [`docs/PRODUCT_VISION.md`](docs/PRODUCT_VISION.md): product goals and target workflows.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): system architecture.
- [`docs/SAFE_WRITE_MODEL.md`](docs/SAFE_WRITE_MODEL.md): safe editing, validation, and rollback model.
- [`docs/ROADMAP.md`](docs/ROADMAP.md): development roadmap.
- [`spec/BPCTX_FORMAT.md`](spec/BPCTX_FORMAT.md): BPCTX/1 format specification.

## Development status

The repository is in an early development stage. Public interfaces, file layouts, and command names may change before the first stable release.

## License

The project will be released under the MIT License. Third-party projects are used primarily as architectural and behavioral references; their source code is not copied into this project unless separately reviewed, attributed, and licensed.

## Disclaimer

UE Agent Kit is an independent open-source project and is not affiliated with, endorsed by, or sponsored by Epic Games, Inc.

Unreal and Unreal Engine are trademarks or registered trademarks of Epic Games, Inc. in the United States and elsewhere.
