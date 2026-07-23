# Changelog

All notable changes to UE Agent Kit are documented here.

## Unreleased

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
