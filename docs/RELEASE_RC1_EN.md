# UE Agent Kit RC1 — Internal Experimental Technology Preview

**Status: internal experimental technology preview. This is not the 0.8.0 stable release and not a published GitHub Release.**
**The package version remains 0.8.0.** No version bump, tag, or GitHub Release was created. The public distribution channel and publication form remain undecided.

## 1. What this RC1 is

RC1 adds a set of **default-off** experimental Blueprint structure-editing capabilities on top of the 0.8.0 stable read-only and controlled-write surface, validated in a real UE 5.6.1 environment. It is intended for internal technical preview, research, and controlled testing — not as a production default.

It is not:

- a replacement or version-number extension of the 0.8.0 stable surface;
- a completed GitHub Release or tag;
- an arbitrary Blueprint GraphPatch executor;
- a complete ue-llm-toolkit replacement;
- an independent OwnerTrust / T10 service.

## 2. Evidence scope

The following results come from controller-local internal acceptance records. **Raw logs are not stored in this repository and are not re-run by this page.** They prove only the stated environment and scope; they do not authorize every project or production use.

| Evidence | Result |
|---|---|
| Full Python suite | **1840 tests / 18 skipped / 0 failed** |
| Native actions | **57 actions, BUILD SUCCEEDED** |
| World Partition / OFPA verification | **132/132 external Actor packages matched official Actor Descriptors** (disposable UE fixture; not 132 performance tests) |
| Five Blueprint MCP write families | Real MCP E2E on an isolated UE 5.6.1 disposable host: Preview → Apply → Undo/Redo verification → explicit Save → fresh-process reload |
| Stable reads / index / Memory | The published 0.8.0 asset/Blueprint reads, SQLite/FTS5 index, Project Memory, and Knowledge Web capabilities |

**The five-family A-level evidence must not be read as production-default write safety.** It is engineering acceptance of a default-off experimental write surface in a specific UE 5.6.1 test environment.

## 3. Blueprint reads and bounded writes

Stable capabilities include Blueprint Graph / Node / Pin / variable / function / macro / interface / Cast / Delegate semantic reads, canonical-context and graph-structure discovery, pin-default and variable discovery, and exact asset/graph targeting. See the [Blueprint graph matrix](BLUEPRINT_GRAPH_READ_WRITE_MATRIX.md) and runtime `ue_get_capabilities` for the authoritative tool and boundary list.

RC1 adds five **experimental** write families:

- bounded GraphEdit: eight allowlisted node types with controlled exec/data connections;
- unlinked input-pin defaults: bool / int / double / string;
- top-level user-variable creation: bool / int / double / string;
- precise single-edge rewire;
- controlled removal of an allowlisted isolated node.

Every family requires Preview, an exact `planId` / confirmation, in-call Undo/Redo verification, a separate explicit Save, and a fresh-process disk reload check. Nothing auto-saves or auto-compiles.

## 4. Default-off and authorization boundary

Experimental writes require both:

```ini
[UEAgentKit]
ExperimentalGraphPatchEdit=True
```

```powershell
ue-agent-mcp --enable-live-editor --enable-experimental-graphpatch-edit
```

Every Apply and Save must also carry the exact confirmation returned by Preview/Apply. Before mutation, the tools require:

- exact asset and graph identity (`assetPath` + `graphGuid`);
- a clean package and stopped PIE/SIE;
- matching on-disk revision and graph fingerprint;
- applicable edge-set hash / node-identity checks;
- a supported top-level K2 graph.

These experimental tools **do not go through the project Write Policy allowlist**. Write Policy still governs the stable write domains; it must not be read as covering these five experimental tools. This repository also has no independent OwnerTrust / T10 service. The actual boundary is the MCP CLI flag, project ini, exact plan/confirmation, and the loopback EditorBridge descriptor (random `authToken` + project-path digest).

## 5. Explicit limitations

- **Default OFF.** Without both opt-ins, the experimental tools are not registered or fail closed. Do not enable them by default in a production project.
- **Save receipts are valid only in the current Editor process.** A restart requires a new Preview/Apply; old receipts cannot be reused.
- **No automatic compile or save.** Variable creation stores a pending default that may require a later explicit compile to appear on the generated-class CDO.
- **Nested and non-top-level graph writes are not supported.** Read-only discovery and writing are separate boundaries.
- **Complex Blueprints, multi-asset concurrency, and concurrent Editor sessions are not covered.** The acceptance run proves one controlled sequence.
- **Live-only MCP still needs a usable index DB.** This is a current startup limitation, not a new RC1 safety guarantee.
- **No OwnerTrust, no full LLMToolkit replacement, and no arbitrary GraphPatch.** Do not infer general-purpose production Blueprint automation.
- Removal/rewire are limited to the documented allowlists and single-object scope; other node, variable, pin types, and bulk operations remain unsupported.

## 6. Usage

For the complete Preview → Apply → Save sequence, parameter-casing differences, error handling, and the rule that confirmation strings must never be hand-written, see:

- [Blueprint Agent end-to-end usage](BLUEPRINT_AGENT_E2E_USAGE.md)
- [Blueprint graph read/write matrix](BLUEPRINT_GRAPH_READ_WRITE_MATRIX.md)

Do not treat placeholder paths, one-off plans, or test assets in the examples as production defaults.

## 7. Release status

This RC1 is currently an **internal experimental technology preview only**:

- the version was not changed (still 0.8.0);
- no tag was created or pushed;
- no GitHub Release was created;
- the public publication form remains a user/controller decision. Publishing the complete current `main` history directly may include internal development material and is not the recommended path at this time.

Before publication, follow the [RC1 release checklist](RC1_RELEASE_CHECKLIST.md) and wait for an explicit distribution-channel decision.
