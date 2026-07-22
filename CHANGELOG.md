# Changelog

All notable changes to UE Agent Kit are documented here.

## Unreleased — 0.5.0 development

- Added the first read-only MCP checkpoint with `ue_search`, `ue_get_asset`, and `ue_find_references`.
- Uses local `stdio` and a fixed immutable SQLite snapshot.
- Does not expose shell execution, arbitrary SQL, UObject access, or write operations.

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
