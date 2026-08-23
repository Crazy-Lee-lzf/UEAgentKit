# Changelog

All notable changes to UE Agent Kit are documented here.

## Unreleased

### 0.8.x Context / Analysis / Agent Reliability closeout

- Added deterministic Task Context, bounded reverse-reference Impact Analysis, Change-Set-bound Semantic Diff, Evidence-gated Verification Plans/Trust Verdicts, and a real-agent benchmark with deterministic ground truth across the R0–R4 line.
- Closed the benchmark result contract with machine-checkable status/trust/conflict/operation enums, exact task `targetAssets` semantics, Change Set identity across write/save/verify/R2/R3, and fail-closed success rules when required evidence is missing.
- Added a Trust Evidence next-action ladder while preserving the boundary that Trust tools never auto-save, compile, validate, run automation, verify, roll back, or ingest arbitrary evidence.
- Narrowed Blueprint pin-default normalization to typed empty/zero/false values, normalized only the mechanically derived reference edge authorized by an exact reference mutation, and hardened saved-Blueprint rollback to require exact unloaded/clean/not-open Bridge evidence.
- Added R4.1 repeat scheduling, alternating Full/Legacy ordering, frozen measurement fingerprints including metrics, failed-attempt retention, drift/mutation fail-closed checks, repeat distributions, ranges, token availability, timeout aggregation, and raw-summary regeneration checks.
- Retained all 24 formal R4.1 attempts with 12/12 paired fairness matches, zero measurement drift/infrastructure failures, and 24/24 exact recovery. Stale and Blueprint Full anchors reached 3/3 Trusted each; high-fanout bound violations and scalar final-claim typing failures remain documented rather than filtered.
- Completed a Read/Write Capability Gap Audit covering all 105 public tools and 18 registered Patch Operations. The 0.8 scope freezes with zero must-fix new tools; generic mutation families and R5 Value Provenance/Execution Trace remain evidence-driven deferred work.
- The latest published version remains 0.7.0. This closeout does not change package/plugin versions, create tags or release artifacts, or push remote state.

## 0.7.0 — realtime editor and layered memory foundation

- Refactored Live Editor Write into a registry-driven architecture intended to scale beyond the current 12 Operations: generic JSON `target` requests, central asset requirements, separate Property/Material/DataTable domain executors, and a shared Transaction/Evidence layer replace the former monolithic hard-coded dispatcher while retaining legacy flattened Target fields for compatibility.
- Added metadata-driven Python Live Write dispatch and independent verification through `OperationSpec`, eliminating the duplicate 12-operation whitelist and per-kind Target branching.
- Added a fixed-work-root Live Apply Journal with strict startup recovery, exact `liveApplyReceipt` verification, truthful `journalPersisted` reporting, and automatic closeout after Undo/Discard or successful independent Verify.
- Added Fast and Full Live Write regression suites and explicit status version semantics: published protocol/package version 0.7.0 and release line 0.7.0.

- Documented the adopted layered Project Memory architecture: arbitrary-depth Knowledge Tree, separate Active Work/TODO, five-level progressive disclosure, server-enforced token budgets, MCP-primary/thin-Skill usage, and one-local-MCP-per-developer plus shared knowledge-service collaboration.

- Added current project-status and roadmap documents covering implemented reads/writes, explicit gaps, Live Editor Write priorities, Context/Analysis, and collaboration direction.
- Added a dated architecture and capability comparison with `ColtonWilley/ue-llm-toolkit`, separating editor-control breadth from UE Agent Kit's Policy/Revision/backup/verification/rollback model.

- Added the first Policy/Revision-gated Live Editor Write path: `ue_apply_asset_property_live` applies one existing `setAssetProperty` Plan to an already open, clean non-Blueprint asset inside the running Editor, records an Undo transaction, marks the package Dirty, and never saves automatically.
- Added a real UE5.6 live-write regression that keeps ordinary `UnrealEditor.exe` running, rejects an invalid confirmation, changes the in-memory scalar value, reports Dirty/Undo evidence, and proves both the `.uasset` SHA-256 and immutable SQLite index remain unchanged.
- Extended the same `ue_apply_asset_property_live` Tool to Data Asset reference properties: `setAssetReferenceProperty` Plans now apply Object, Class, SoftObject, and SoftClass top-level references (including JSON null clears) to already open, clean Data Assets inside the running Editor, with the same Undo/Dirty/no-save behavior, an explicit Bridge Operation, and full failure restore. This capability is included in 0.7.0 and retains the same no-auto-save, Policy, Revision, Undo, and failure-restoration boundaries.
- Added a declarative reference live-write fixture plan (`reference_live_write_plan.json`), a real UE5.6 reference live-write regression harness (`TestMcpLiveReferenceWrite`), and Python contract coverage proving the Bridge receives the explicit Operation and passes reference JSON values through unchanged.
- Extended the same Tool to Data Asset structured properties: `setAssetStructuredProperty` Plans now apply top-level Struct, Array, Set, and Map values to already open, clean Data Assets inside the running Editor. The Bridge reuses the existing StructuredPropertyJson GetKind/BuildSchema/ExportValue/ImportValue/CanonicalJson/JsonEqual/BuildDiff path (no second serialization layer), snapshots the property, imports, read-backs, and verifies the value, then records Undo, marks Dirty, and reports `structuredKind`/`structuredSchema`/`diff`/`diffTruncated`; failures restore the value and the prior Dirty state and cancel the Transaction, and a no-op apply creates neither Dirty nor Undo. Fixed arrays (ArrayDim != 1), nested property paths, and non-structured properties are rejected during Plan validation or at the Bridge.
- Added a declarative structured live-write fixture plan (`structured_live_write_plan.json`), a real UE5.6 structured live-write regression harness (`TestMcpLiveStructuredWrite`) covering Plan rejections, confirmation rejection, no-op behavior, Struct/Array/Set/Map success cases, and a Dirty-package Bridge rejection while proving disk `.uasset` SHA-256, SQLite, and Revision Export hashes all remain unchanged, plus Python unit and executable smoke-contract coverage.
- Fixed the structured live-write no-op path to restore the deep-copied property snapshot (`Backup.Restore`) before restoring the prior Package Dirty state and cancelling the Transaction, so a no-op apply is strictly side-effect free even when Array/Set/Map container instances were rebuilt by `ImportValue`; added real UE5.6 no-op coverage for Struct, Array, Set, and Map values with `ue_inspect_asset_live` package-clean re-check and `.uasset`/SQLite/Revision hash invariance.
- Fixed an intermittent consecutive-run startup race where a previous UnrealEditor/MCP child-process teardown could leave the next MCP stdio server failing before session initialization (`McpError: Connection closed`): harnesses now confirm the Editor process has fully exited before removing the Session Descriptor, and the MCP client retries only startup-phase failures that occur before an MCP session was established (never once Tool/Plan/Apply/assertion execution began).
- Added a unified consecutive-run regression entry `TestMcpLiveWriteRegression` that runs the Scalar, Reference, and Structured live-write harnesses in order, checks each sub-test's real exit code, and preserves the failing sub-test's Output/Backups and log summary.

## 0.6.0 — revision-aware project memory

- Hardened release packaging to remove UAT `Intermediate`, `Saved`, `DerivedDataCache`, and `HostProject` directories and reject unexpected or incomplete plugin package contents before ZIP creation.
- Added a real UE5.6 Workflow-to-Memory regression that persists verified Commit and rollback Task Records, invalidates the superseded Commit Revision after rollback, verifies the Memory audit digest, restores the package SHA-256, and leaves the immutable index unchanged.
- Added a verified Workflow-to-Memory evidence handoff: `ue_verify_asset` now returns exact `ue_memory_record_task` arguments derived from the Patch digest, Backup Manifest, independent validation report, and final Revision.
- Added the same evidence handoff for verified rollback Commit results, producing `outcome=rolledBack` with the restored Revision and rollback validation report IDs.
- Added fixed-project Memory CLI status/search/get/validate commands and portable audit export with verified record digests and a stable snapshot SHA-256.
- Fixed the Windows CLI JSON transport to emit UTF-8 on redirected stdout/stderr, including non-ASCII Project Keys.
- Added evidence-bound Task Records that require a final outcome, Patch, Backup Manifest, Validation Evidence, and stable Revision Set.
- Added Project Memory Schema v2 with evidence-bound SHA-256 digests, v1 backfill, and read-time tamper detection for semantic content, Revision Sets, and Artifact bindings.
- Added the first Revision-aware Project Memory storage layer with an independent SQLite/FTS5 schema, six record types, source/status provenance, Scope/Revision/Artifact bindings, conflict coexistence, explicit supersede links, and automatic Revision-to-stale invalidation.
- Added a fixed-project Memory Service with FTS and Scope filtering, project-isolated reads/writes, status statistics, and direct validation against the current SQLite asset revisions.
- Added six opt-in fixed-project MCP Memory tools for search, exact read, user-confirmed rules, observed/inferred findings, explicit supersede, and Revision validation without exposing database or project path arguments.

## 0.5.5 — daily-development consolidation

- Added a portable release validator, Python 3.11/3.12 GitHub Actions matrix, package-build gate, and uploaded Python distribution artifact.
- Routed UAT BuildPlugin through the same resolved AutoSDK/MSVC toolchain used by the validated Direct Build path.
- Placed the Python wheel at the release root so manifest and SHA-256 entries resolve directly from the published directory.
- Added single-asset atomic transactions containing 1–32 compatible operations, with complete pre-validation, duplicate-target rejection, one backup, one Blueprint compile/save, process-discard Dry Run semantics, and whole-package rollback.
- Extended backup manifests with ordered per-operation targets, before/after values, and exact authorization-key evidence while preserving schema 1.0 compatibility for existing single-operation manifests.
- Preserved multi-field DataTable authorization evidence for single-operation row-field and row-add manifests.
- Added a real UE5.6 Data Asset and Blueprint transaction regression covering Dry Run, Commit, one backup, independent reload, manifest verification, whole-transaction rollback, and exact final Revision recovery.
- Added project/session/time-bound `validationEvidence` to Data Validation and exact Automation Test results.
- Added deterministically ordered Asset Revision Sets with before/after Package SHA-256, Dirty-state provenance, stability checks, and explicit complete/partial coverage.
- Marked asset-unspecified Automation evidence as `not-applicable` instead of inventing revisions, while recording isolated child-process provenance.
- Unified Scalar, Vector, Texture, and Static Switch Material Instance reports under a native-JSON `materialParameter` state/diff contract, including Override, source, Expression GUID, rollback metadata, and full-state rollback gates.
- Upgraded the Material Instance reader to version 2 with `override` and `expressionGuid` metadata for Scalar, Vector, Texture, and Static Switch overrides.
- Added a reproducible real UE5.6 four-parameter Dry Run/Commit/independent-export/rollback regression using declarative duplicate fixtures and exact final Revision recovery.
- Added the authenticated localhost Live Editor Bridge, live state/log/diagnostic/inspection tools, Blueprint Graph selection, bounded Daily Actions, exact-name Automation Tests, and Policy/Revision/session-bound authorized one-asset saves.
- Added immutable single-asset index refresh, paired Revision Export/SQLite generations, four-source asset state, and frozen workflow-session semantics.
- Centralized Tool Registry ownership and split MCP and Editor Bridge handlers by responsibility.
- Added atomic `setDataTableRowFields`, controlled DataTable row add/remove/rename, full-table restoration, and exact Searchable Name reference-impact gates at both Plan and UE execution time.
- Added `setAssetReferenceProperty` for Object/Class and Soft Object/Class Data Asset properties with exact reference authorization and `null` clearing.
- Added `setAssetStructuredProperty` for top-level Struct, Array, Set, and Map Data Asset properties with recursive schema, Canonical ordering, deep restoration, and structured diffs.
- Expanded high-level safe-write entry points to 12 and registered Patch Operations to 16.
- Current server modes are Offline 5, Live 23, Workflow 25, and Combined 43 tools.
- Current regression baseline is Ruff plus 202 Python tests, 16 example Patch schemas, three schema meta-validations, UE5.6 Direct Build and UAT BuildPlugin packaging, and real Blueprint/Data Asset/Material/DataTable Dry Run, Commit, independent reload, transaction, and rollback chains.

## 0.5.1 — MCP usability, freshness, and diagnostics

- Added a multi-client compatibility matrix covering the official Python MCP SDK, raw newline-delimited JSON-RPC stdio, Claude Code Tool Schema requirements, and standard structured-content/text-fallback response contracts.
- Added six high-level MCP safe-change tools for Blueprint defaults, component properties, pin defaults, generic asset properties, Material Instance parameters, and DataTable cells.
- Added `Plan` and `DryRun` high-level modes while keeping Commit exclusively behind the existing one-time receipt and exact-confirmation workflow.
- Added separate `policy-rejected`, `revision-conflict`, `dirty-package`, `ue-process-crashed`, `workflow-report-missing`, and `workflow-report-invalid` diagnostics.
- Added redacted `diagnosticId`, `reportId`, stage, exit-code, and process-output tails without exposing local report paths.
- Added three-source freshness comparison across immutable SQLite, Revision Export Canonical data, and current project package SHA-256.
- Required `fresh` target state before creating a Patch Plan, with separate `index-stale` and `index-freshness-unavailable` errors.
- Marked SQLite and Revision Export snapshots stale after Commit, preserved stale through independent verification, and cleared it only after exact verified rollback.
- Added bounded project freshness summaries to `ue_get_project_status` without exposing local package or snapshot paths.
- Documented the staged single-asset refresh and new-session immutable snapshot reload design.
- Added `ue_get_capabilities` for active Tool, Operation, limit, response-contract, and safety discovery.
- Added `ue_get_project_status` for fixed project, Engine, immutable index, workflow, freshness, and Live Editor state.
- Standardized MCP errors with `code`, `message`, `retryable`, `details`, and `suggestedAction` while retaining `type` for compatibility.
- Added index build metadata to the read-only status contract and fixed the Python package version reported by MCP.
- Extended unit, stdio, and real UE5.6 workflow coverage for the new protocol contract.
- Added opaque session-local continuation tokens bound to one Tool and immutable SQLite snapshot while retaining offset compatibility.
- Added accurate `limit + 1` pagination, Path Prefix filters, and bounded output Token Budgets with explicit truncation reasons.
- Added selective `ue_get_asset` sections with independent Symbol, Reference, Graph, and Node continuations.
- Added outgoing, incoming, and bidirectional reference queries with bounded depth 1-3 and project-only filtering.
- Added stable validation for Unreal paths, Symbol IDs, Graph GUIDs, Node GUIDs, and invalid continuation tokens.

## 0.5.0 — Fixed-project MCP workflow

- Added `ue_plan_patch`, `ue_dry_run_patch`, `ue_apply_patch`, `ue_verify_asset`, and `ue_rollback_patch` to the existing three read-only query tools.
- Added fixed server configuration for Database, Engine, Project, Policy, Revision Export, work root, and backup root; tool calls cannot replace these paths.
- Added digest locking for stored plans and Policy files.
- Added one-time dry-run receipts and exact confirmation phrases for both Commit and rollback Commit.
- Added independent Unreal reload verification after Commit and rollback.
- Added real MCP client integration coverage with final package SHA-256 restoration and unchanged immutable SQLite index files.
- Preserved the default three-tool read-only server mode.

## 0.4.4 — Safe write regression hardening

Release baseline: `daea768`

- Added reproducible Dirty Package rejection before property mutation.
- Added real package-sidecar rejection using a temporary `.uexp` fixture.
- Added save-failure injection after backup creation and verified disk protection, raw backup availability, and absence of a success manifest.
- Expanded the scalar rejection matrix from six to nine zero-write failure paths.
- Verified 11/11 scalar dry runs, 11/11 commits, nine expected failures, independent reloads, and final fixture reset.

## 0.4.3 — Complete scalar regression

- Added a native scalar Data Asset fixture.
- Covered Bool, Byte, Int32, Int64, Float, Double, String, Name, Text, `FEnumProperty`, and enum-backed Byte properties.
- Added sequential commit, backup manifest, independent reload, and reset verification.

## 0.4.2 — Reproducible write fixtures

- Added declarative Write Fixture Plans.
- Added safe Create/Reset behavior, source-class checks, target-root restrictions, plan revision locking, and independent UE reload verification.

## 0.4.1 — Auditable rollback

- Added automatic backup manifests after successful commits.
- Added validation-only rollback dry runs and explicit atomic restore.
- Added pre-rollback safety copies, revision conflict checks, receipts, and independent UE verification.

## 0.4.0 — Common non-Blueprint writes

- Added safe DataTable cell writes.
- Added Material Instance Global Scalar, Vector, Texture, and Static Switch parameter writes.
- Added generic scalar asset-property writes with policy, revision, backup, and dry-run protections.

## 0.3.x — Blueprint patch baseline

- Added policy-gated Blueprint patch validation and execution.
- Added variable defaults, component properties, pin defaults, and Blueprint descriptions.
- Expanded coverage to normal, Widget, Anim, Actor Component, Function Library, Macro Library, Interface, and Control Rig Blueprints.
