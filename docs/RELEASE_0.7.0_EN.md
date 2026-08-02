# UE Agent Kit 0.7.0 Release Notes

UE Agent Kit 0.7.0 targets Unreal Engine 5.6 and formally releases the Realtime Foundation, registry-driven Live Editor Write, Schema v3 Project Memory, frame-stepped batch tasks, and durable Change Sets. The release retains fixed-project, Policy, Revision, exact-confirmation, Transaction, Evidence, Undo/Discard, authorized-save, independent-verification, and rollback boundaries.

## Highlights

### Realtime Foundation

- `ue_get_editor_context` aggregates Editor, World, Selection, Open Assets, Dirty Packages, Blueprint Graph Selection, compile errors, and the Output Log cursor with stage timings.
- `scanCurrentWorld` processes only the loaded World under an approximately 2 ms frame budget and supports progress, cancellation, timeout, invalidation, and partial results.
- Batch tasks return summaries by default and expose bounded paged details.
- Change Set schema v2 durably records Task, Editor Session, Operation, Asset, Transaction, Save Receipt, and Validation lifecycle data.

### Registry-driven Live Editor Write

- `ue_apply_asset_property_live` routes 12 controlled Operations through `OperationSpec` metadata and domain executors.
- Data Asset scalar/reference/Struct/Array/Set/Map, Material Instance Scalar/Vector/Texture/Static Switch, and DataTable Cell/RowFields/Add/Remove/Rename operations are supported.
- Shared Transaction/Evidence, exact Undo/Discard, authorized one-asset Save, Independent Verify, and a recoverable journal are included.
- Failure paths restore values and Dirty state; no-op applies create neither Dirty nor Undo.

### Schema v3 Project Memory

- Stable paths and parent links provide arbitrary-depth Knowledge Trees.
- Active Work stores objectives, TODO items, blockers, and next actions separately from durable knowledge.
- Five-level progressive disclosure and server-enforced token budgets return summaries first.
- `ue_memory_get_context`, `ue_memory_expand_node`, `ue_memory_get_evidence`, `ue_memory_update_knowledge`, and `ue_memory_update_work` are included.
- Compatibility reads for the 0.6.0 low-level Memory API remain available.

### Large-project performance plan

- Added the Chinese internal development document `docs/PERFORMANCE_TEST_PLAN.md`.
- The physical fixture targets 160–180 GB with a 200 GB hard limit on SSD storage.
- The same benchmarks run as `NativeSSD` and `SimulatedHDD50`; the simulated profile uses a 50 MB/s sequential cap, 8/10/15 ms file-open seek profiles, and queue depth 1.
- Slow first-time indexing is acceptable; daily search, variable edits, small Blueprint edits, Compile, Undo, and one-asset Save are the primary interaction gates.

## Tool counts

```text
Offline             5 tools (17 with Memory)
Live               27 tools (39 with Memory)
Workflow           31 tools (43 with Memory)
Combined           53 tools (65 with Memory)
```

## Safety boundaries

Version 0.7.0 still does not expose arbitrary SQL, Shell, Python, filesystem access, UObject methods, Console Commands, automatic saving, Save All, unauthorized commits, PIE/SIE asset mutation, or unrestricted Blueprint/Material/Animation/Control Rig/Sequencer/Niagara graph mutation.

## Known limitations

Common Blueprint graph node mutation is not yet a published Operation. Shared Knowledge Service, team permissions, source-control conflict awareness, and the executable large-project performance fixture remain future work.

## Artifacts

The local machine does not currently contain a usable x64 MSVC/`cl.exe`, so this run cannot rebuild and claim a precompiled Win64 plugin package. The verified delivery is a source release:

```text
UEAgentKit-0.7.0-Source.zip
ue_agent_kit-0.7.0-py3-none-any.whl
SHA256SUMS.txt
release-manifest.json
UEAgentKit-0.7.0-LocalReleaseBundle.zip
```

The source ZIP is produced with `git archive` from a clean release commit and excludes `.git`, `.venv`, Build, Output, Backups, Intermediate, Saved, DerivedDataCache, logs, and caches. The bundle also includes the handoff and release notes.

After installing the Visual Studio Desktop development with C++ workload, x64 MSVC, and a Windows SDK, rerun `scripts\BuildRelease.ps1` to produce:

```text
UEAgentKit-0.7.0-UE5.6-Win64.zip
```

The old DLL must not be reused because its embedded version status does not match 0.7.0. Push, Tag, and a remote GitHub Release remain outside this local release.
