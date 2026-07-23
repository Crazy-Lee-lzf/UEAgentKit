# UE Agent Kit 0.5.1 Release Notes

UE Agent Kit 0.5.1 targets Unreal Engine 5.6 and completes the protocol and daily-usability layer built on the fixed-project MCP workflow introduced in 0.5.0. It does not add an arbitrary execution surface. Query, planning, writes, diagnostics, and client compatibility remain bounded by the existing Policy, Revision, Dry Run, receipt, backup, verification, and rollback guarantees.

## Highlights

### Query and protocol contract

- Added `ue_get_capabilities` and `ue_get_project_status`.
- Added Asset Class, Symbol Kind, Path Prefix, accurate `limit + 1` pagination, and opaque continuation tokens to `ue_search`.
- Added selective `identity`, `summary`, `metadata`, `symbols`, `references`, `graphs`, and `nodes` sections to `ue_get_asset`, with independent continuations for list sections.
- Added incoming, outgoing, and bidirectional references, bounded depth 1–3, and `project_only` filtering.
- Added Token Budget metadata, estimated tokens, truncation reasons, and continuation information.
- Standardized errors as `code`, `message`, `retryable`, `details`, and `suggestedAction` envelopes while retaining compatibility fields.

### Revision freshness

Writes compare three Revision sources:

```text
immutable SQLite
Revision Export Canonical
current disk package SHA-256
```

States are `fresh`, `stale`, `partial`, `unavailable`, and `unknown` in read-only mode. The target is rechecked before Plan, Dry Run, and Commit. Commit marks fixed SQLite and Revision Export snapshots stale; independent Verify does not clear that state; exact rollback restores fresh only after the original Revision is restored and re-compared.

### High-level safe-change tools

Full mode adds six tools:

```text
ue_set_blueprint_default
ue_set_component_property
ue_set_pin_default
ue_set_asset_property
ue_set_material_parameter
ue_set_datatable_cell
```

They default to `mode=Plan` and may use `mode=DryRun` to run Plan → Unreal Dry Run. They cannot commit directly. Saving still requires `ue_apply_patch`, a one-time `dryRunReceipt`, and exact `COMMIT <planId>` confirmation.

### Diagnostics

The workflow distinguishes:

```text
policy-rejected
revision-conflict
dirty-package
workflow-timeout
ue-process-crashed
workflow-report-missing
workflow-report-invalid
```

Responses may include redacted `diagnosticId`, `reportId`, `stage`, `exitCode`, `stdoutTail`, and `stderrTail`, without exposing local database, project, package, report, or backup paths.

## MCP client compatibility matrix

Run:

```bat
scripts\TestMcpClients.cmd
```

The matrix starts two independent real `stdio` sessions:

- the official Python MCP `ClientSession`;
- a raw newline-delimited JSON-RPC client with no MCP SDK dependency.

Both clients must negotiate the same Protocol Version, discover the same Tool Schema and annotations, and receive `structuredContent`. Every Tool also returns one parseable JSON Text Content fallback, including errors.

The Claude Code contract covers local `stdio`, non-empty Tool descriptions, Object JSON Schemas, annotations, and the absence of fixed Database, Engine, Project, Policy, or filesystem paths from Tool arguments. ChatGPT-related claims are limited to standard MCP `tools/list`, `tools/call`, `structuredContent`, and text-fallback compatibility. Hosted ChatGPT UI, account settings, and remote transports were not exercised by local automation.

## Tool counts

Read-only mode: 5 Tools.

Fixed-project full mode: 16 Tools: 5 read-only Tools, 6 high-level safe-change Tools, and 5 lower-level workflow Tools.

## Release verification

```text
Python unittest                         134/134 passed
Read-only MCP stdio smoke               passed
Official SDK + raw JSON-RPC matrix      passed
MCP protocol version                    2025-11-25
UE5.6 Direct plugin build               passed
UE5.6 UAT Win64 package build           passed
Real high-level MCP workflow            passed
Commit -> Verify -> Rollback             passed
Final fixture SHA-256 restoration        passed
Immutable SQLite unchanged               passed
```

The Unreal execution semantics for the 11 scalar operation types did not change in this batch, so the complete 11 Dry Run + 11 Commit + 9 failure scalar matrix was not repeated. The real MCP Commit, independent Verify, and rollback workflow was rerun.

## Upgrade from 0.5.0

1. Stop running UE Agent Kit MCP servers and Unreal Editor.
2. Replace the project or Engine `UEAgentKit` plugin directory; do not merge new binaries into the old directory.
3. Run `scripts\setup_python.cmd -WithMcp` when refreshing the optional Python MCP environment.
4. Re-export Revisions, rebuild SQLite, and require `ue_get_project_status` to report `fresh` before full mode.
5. Existing clients may continue using `offset`; new integrations should use continuation tokens and `ue_get_capabilities`.
6. The five lower-level workflow Tools remain compatible; common changes should prefer the six `ue_set_*` tools.

The Release ZIP contains only the installable UE5.6/Win64 plugin, LICENSE, and brief release notes. Use the GitHub Source archive or full repository for the Python CLI, MCP server, tests, scripts, and complete specifications.

SHA-256 for `UEAgentKit-0.5.1-UE5.6-Win64.zip`:

```text
27f7dd1b6b8375c6dfa5d9c0c6ff27ed1ec4db680bab7a5ee64852925f8f976a
```

## Still excluded

- Arbitrary SQL, Shell, file overwrite, Console, Python, or UObject calls.
- Plan, receipt, or continuation-token persistence across server restarts.
- Multi-asset transactions or arbitrary multi-operation transactions.
- Live Editor memory inspection, which belongs to 0.5.2.
- Hosted ChatGPT UI end-to-end automation.
