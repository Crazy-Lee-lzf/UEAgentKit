# C++ Code Knowledge

UEAgentKit indexes project C++ into the existing SQLite knowledge model without introducing a parallel code schema.

## One-command workflow

Use the integrated workflow after the UEAgentKit Editor plugin is available to the target project:

```powershell
scripts\RunCodeKnowledge.ps1 `
  -EngineRoot "<UE_5.6>" `
  -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject" `
  -Database "<TOOL_ROOT>\.data\ue_agent_kit.sqlite3"
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
- quoted `#include`;
- function / method declarations and definitions with declaration and definition
  anchors (`assetPath`, `line`, `endLine`) in `symbols.details_json`, including
  `UCLASS`/`UFUNCTION`/`GENERATED_BODY`-decorated declarations, namespaces,
  out-of-line `Namespace::Type::Method` definitions, operators and modifiers;
- conservative `calls` edges for call sites it can prove.

C1 never expands macros, never instantiates templates, and never invents a target
for an unresolved name. Same-name overload sets fail closed (`ambiguous-overload`)
and two files defining the same unqualified signature fail closed
(`ambiguous-definition`): neither gets a `cpp:function:*` row, so no call site can
link to them. Translation-unit-local helpers are therefore not addressable
project-wide.

Each `calls` edge carries explicit provenance in `details_json`:

```json
{
  "evidence": "cpp-source",
  "resolution": "member | same-scope | qualified | unique-free-function | base-member",
  "dispatch": "static | virtual | unknown",
  "certainty": "definitive | static-target-only",
  "callSite": {"assetPath": "Source/...", "line": 42, "column": 9}
}
```

`certainty=definitive` means the call token and its static C++ target are both
proven. A call to a virtual function, or to a reflection-owned target whose C++
modifiers are no longer available, is kept only as `static-target-only`:
the static call anchor is real, but the runtime callee is not claimed.
`Obj->Method()`, `Super::Method()`, delegates, function-pointer invocations,
macros, and Blueprint/reflection dispatch are reported as `unknown` reasons
(`unknown-receiver`, `unknown-super`, `unknown-scope`, `ambiguous-name`) and never
become edges. `calls` is not a trusted impact edge.

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

`calls` edges are intentionally excluded: they are function-level, may be
virtual, and must not inflate type-level consumer sets.

The code-symbol BFS remains restricted to `profile=code` consumers.


## Query surface

Code Knowledge reuses the normal read-only query tools rather than defining parallel code-only tools:

- `ue_search(scope="assets", profile="code")` finds indexed source files;
- `ue_search(scope="symbols", profile="code")` finds C1 types/functions and C2 reflected functions/properties;
  `kind="function"` narrows to callable symbols;
- `ue_get_asset("Source/...", sections=[...])` reads one source pseudo-asset and its symbols/references
  (symbols are attached to their declaration asset, call references to the implementation asset);
- `ue_find_references(...)` accepts exact project-relative indexed source paths and stable symbol ids:
  `source_symbol_id` returns the callees of one function, `target_symbol_id` returns its callers;
- `ue_analyze_change_impact(..., subject_kind="code-symbol")` performs bounded trusted-edge impact analysis;
- `ue_get_project_status.codeKnowledge` reports current code/reflection counts and import state;
- `ue_get_task_context` includes `profile=code` in deterministic relevant-asset discovery and accepts safe project-relative source targets.

## Freshness

Code freshness uses source SHA-256 as the content authority. File size and mtime are retained as diagnostics and fast evidence but do not independently make unchanged content stale.

## Known limits

- Validate the indexed project scope and freshness before relying on results for production changes.
- C1 intentionally fails closed on ambiguous duplicate fully-qualified type identities, same-name overload sets, cross-file duplicate definitions, and unsupported declaration shapes.
- `#include` proves a file dependency, not actual use of a specific type.
- Non-reflected functions/properties are not indexed semantically by C2.
- `calls` edges are static call-site evidence only. Virtual dispatch, delegates, function pointers, macro-generated calls, reflection/Blueprint invocation, and calls through object pointers are reported as unknown and are not chained.
- Call scanning covers parsed definition bodies; default arguments, constructor initializer lists, one-line class bodies, and `.cpp`-only class bodies are not scanned.
- `codeKnowledge` counts exposed through `ue_get_project_status` do not yet break out `calls` / virtual call counts; use `ue_find_references(kind="calls")` or `index stats.referenceKinds` for those numbers.
