# C++ Code Knowledge

UEAgentKit indexes project C++ into the existing SQLite knowledge model without introducing a parallel code schema.

## One-command workflow

Use the integrated workflow after the UEAgentKit Editor plugin is available to the target project:

```powershell
scripts\RunCodeKnowledge.ps1 `
  -EngineRoot E:\EPICGAME\UE_5.6 `
  -ProjectPath E:\Path\Project.uproject `
  -Database E:\Path\ue_agent_kit.sqlite3
```

The workflow performs, in order:

1. source-file Code Index (`profile=code`, `asset_class=CppSourceFile`);
2. conservative C++ type/reference extraction;
3. UE Reflection export through `ReflectionExportCommandlet`;
4. reflection merge into the same SQLite database;
5. `code_knowledge_summary.json` output.

`-CodeOnly` skips UE Reflection when only the source index is required.

## Identity and evidence

Type identity is semantic and does not depend on file path, line number, or content hash:

```text
cpp:type:<qualified-cpp-name>
cpp:function:<owner-cpp-name>::<function-name>
cpp:property:<owner-cpp-name>::<property-name>
cpp:parameter:<owner-cpp-name>::<function-name>::<parameter-name>
```

C1 source extraction is deliberately narrow:

- class / struct / enum;
- inheritance;
- quoted `#include`.

It does not attempt function bodies, call graphs, member-use, or a custom full C++ parser.

C2 UE Reflection is authoritative for reflected facts and exports project modules only:

- UClass / UScriptStruct / UEnum;
- UFunction / FProperty;
- superclass and implemented interfaces;
- flags and metadata;
- function parameter and return C++ types.

Reflection records are merged only when their `cpp:type:*` owner already exists in the C1 source index. Unmatched reflected types are reported rather than creating orphan code types.

## Impact analysis

`subject_kind=code-symbol` accepts exact project-relative source owner paths such as:

```text
Source/Game/Public/MyActor.h
```

Trusted code edges are:

- `inherits` — source-level or reflection-enriched type evidence;
- `include` — file-level conservative evidence and may over-report type impact;
- `implements` — accepted only when `details_json.evidence == "ue-reflection"`.

The code-symbol BFS remains restricted to `profile=code` consumers.


## Query surface

Code Knowledge reuses the normal read-only query tools rather than defining parallel code-only tools:

- `ue_search(scope="assets", profile="code")` finds indexed source files;
- `ue_search(scope="symbols", profile="code")` finds C1 types and C2 reflected functions/properties;
- `ue_get_asset("Source/...", sections=[...])` reads one source pseudo-asset and its symbols/references;
- `ue_find_references(...)` accepts exact project-relative indexed source paths;
- `ue_analyze_change_impact(..., subject_kind="code-symbol")` performs bounded trusted-edge impact analysis;
- `ue_get_project_status.codeKnowledge` reports current code/reflection counts and import state.
- `ue_get_task_context` includes `profile=code` in deterministic relevant-asset discovery and accepts safe project-relative source targets.

## Freshness

Code freshness uses source SHA-256 as the content authority. File size and mtime are retained as diagnostics and fast evidence but do not independently make unchanged content stale.

## Current acceptance evidence

- Python full-suite count is recorded from the latest frozen checkpoint; rerun `scripts/RunPythonTests.py full --quiet` before release.
- UE 5.6 plugin build: passed.
- Reforge small project: 14 source files; C1 indexed 6 types and UE Reflection matched 6/6 reflected types.
- UE 5.6 reflection fixture: real UHT/Editor validation for UCLASS, USTRUCT, UENUM, UINTERFACE, UFUNCTION, and UPROPERTY; export produced 4 types, 2 functions, and 3 properties with 0 UE warnings/errors.
- Synthetic scale check: 1,949 files / 411,239 LOC completed without indexing failures. This is performance/scale evidence only, not a substitute for acceptance on the original large production project.

## Known limits

- The original ~1,949-file production project is not available on the current machine, so final production-scale semantic acceptance remains deferred.
- C1 intentionally fails closed on ambiguous duplicate fully-qualified type identities and unsupported declaration shapes.
- `#include` proves a file dependency, not actual use of a specific type.
- Non-reflected functions/properties are not indexed semantically by C2.
