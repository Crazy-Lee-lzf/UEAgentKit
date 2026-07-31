# UE Agent Kit vs. ue-llm-toolkit

Updated: 2026-07-31

Comparison target: [`ColtonWilley/ue-llm-toolkit`](https://github.com/ColtonWilley/ue-llm-toolkit), `main` README as read on 2026-07-31.

This document compares public claims with verified UE Agent Kit capabilities. It does not infer undocumented internal guarantees, and tool count is not treated as a direct measure of quality.

## 1. Summary

The projects currently optimize for different goals:

- **ue-llm-toolkit** is a broad immediate-execution Unreal Editor control layer.
- **UE Agent Kit** is a project-wide offline knowledge, live-state, Revision-aware memory, and controlled-change workflow.

ue-llm-toolkit is substantially broader for direct editor production work. UE Agent Kit is substantially more explicit about deterministic indexing, Policy/Revision gates, backups, independent verification, rollback, and invalidating stale conclusions.

UE Agent Kit is not currently a breadth replacement for ue-llm-toolkit. Its differentiator is the integrity and traceability of project understanding and changes.

## 2. Architecture

| Dimension | UE Agent Kit | ue-llm-toolkit |
|---|---|---|
| Target engine | UE 5.6 | UE 5.7 |
| Primary interface | Local MCP stdio plus authenticated localhost Editor Bridge | localhost HTTP/JSON with optional MCP bridge |
| In-editor implementation | C++ Editor Plugin | Pure C++ Plugin |
| Project read model | Offline exports + SQLite/FTS + Live Editor | Primarily live Editor queries |
| Context model | Canonical/BPCTX + Revision-aware Project Memory | AI-maintained `domains/` summaries |
| Write model | Plan/Dry Run/Commit/Live Apply layers | Direct Tool/Operation calls |
| Safety emphasis | Fixed project, Policy, Revision, Receipt, backup, verification, rollback | Immediate local editor productivity |

## 3. Read capabilities

### UE Agent Kit strengths

- Editor-independent Asset/Symbol/Reference search through immutable SQLite snapshots.
- Canonical Blueprint graph semantics and stable project-wide indexing.
- Four-source state across Editor Memory, disk Package, Revision Export, and SQLite.
- Fresh/stale/partial/unavailable snapshot semantics.
- Revision-aware rules, findings, decisions, issues, tasks, and runtime evidence.
- Automatic stale transitions and coexistence of conflicting conclusions.

### ue-llm-toolkit strengths

Its README describes much broader specialized live reads for:

- Anim Blueprint state machines and transitions.
- Montages, Blend Spaces, and Anim Sequences.
- Control Rig and IK Retargeting.
- Detailed Levels and Actors.
- UMG, Enhanced Input, characters, and movement.
- Viewport screenshots, asset previews, PIE recordings, and gameplay diagnostics.

UE Agent Kit currently lacks most of these specialized readers or only exposes generic catalog/reference data for those assets.

## 4. Write capabilities

### UE Agent Kit

Verified persistent writes cover Blueprint defaults/components/pins, Data Asset scalar/reference/structured values, Material Instance parameters, DataTable fields and row operations, and atomic 1–32-operation single-asset transactions.

The first Live Editor Write slice changes one top-level scalar property on an already open, clean, non-Blueprint asset, records Undo, marks Dirty, and never saves automatically.

Persistent workflow:

```text
fixed project
+ Policy allowlist
+ Revision freshness
+ Plan
+ Dry Run or exact confirmation
+ external backup
+ independent reload verification
+ rollback
+ Memory Task Evidence
```

### ue-llm-toolkit

Its README describes substantially broader writes, including:

- Blueprint node creation/deletion/wiring/layout.
- Anim state machines, Montages, Blend Spaces, Anim Sequences.
- Control Rig and Retargeting.
- Actor spawn/transform/delete/arbitrary properties.
- Asset import/export/save/duplicate/rename/delete/migrate.
- Enhanced Input, UMG, character, and material operations.
- PIE input recording/replay and capture.
- Console, C++/Python/script execution.
- Build dispatch and editor lifecycle management.

For direct AI-driven editor production, ue-llm-toolkit is currently much more capable.

## 5. Same Unreal APIs, different semantics

Both projects ultimately call Unreal UObject, Blueprint, Graph, Compile, and Save APIs. The difference is the contract around those calls.

ue-llm-toolkit favors direct iteration:

```text
Agent selects Tool/Operation
→ Editor executes immediately
→ compile/save/test as needed
```

UE Agent Kit favors controlled mutation:

```text
read evidence
→ policy-limited Plan
→ Revision recheck
→ Dry Run or narrow Live Apply
→ exact confirmation
→ backup/save
→ independent verification
→ recoverable evidence
```

The first model is faster and broader. The second is slower to extend but better suited to environments where stale context, silent overwrite, and unverified saves are unacceptable.

## 6. Direct Live Write comparison

| Area | UE Agent Kit | ue-llm-toolkit |
|---|---|---|
| Current breadth | One open clean non-Blueprint scalar property | Broad Blueprint, animation, actor, asset, input, and widget operations |
| Pre-write state | Fixed snapshots + Policy Plan | Live queries followed by a modify operation |
| Undo | Explicit `FScopedTransaction` in the first slice | No uniform per-operation transaction guarantee documented in README |
| Automatic save | Never for Live Apply | Asset Save and Save All are available |
| Backup | External backup in persistent workflows | No uniform external Backup Manifest described in README |
| Verification | Independent UE reload and Revision/state comparison | Usually current-editor compile/query/test iteration |
| Rollback | Manifest and Revision-aware package restore | No uniform Revision-aware rollback contract described in README |

Current conclusion:

- Breadth: ue-llm-toolkit wins clearly.
- Auditability and controlled persistence: UE Agent Kit is more systematic.
- Interaction speed: ue-llm-toolkit is more direct.
- Limiting and recovering from bad changes: UE Agent Kit is more conservative.

## 7. What to learn and what not to copy

Useful ideas:

- Domain-specific Unreal tools.
- High-value animation, Control Rig, Retarget, and PIE debugging coverage.
- Read-domain → plan → execute workflow.
- Compact CLI help and output.
- Real game-development demand as the prioritization signal.

Ideas not to copy directly:

- Arbitrary UObject properties to maximize operation count.
- Console/Python/script as the universal escape hatch.
- Full graph mutation before stable identity, semantic diff, and recovery exist.
- Save All as the default AI persistence path.
- Relying only on the model to remember to plan.

## 8. UE Agent Kit strategy

Do not target short-term parity with “37 tools / 200+ operations.” Instead:

1. Complete the shared Live Transaction, Undo/Discard, Authorized Save, and Evidence foundation.
2. Complete 0.7.0 Context Packs, value-source tracing, execution tracing, impact analysis, and semantic diffs.
3. Add the highest-frequency Reforge write domains.
4. Require real UE5.6 regression, failure recovery, and independent verification for each new write domain.
5. Expand into graph, animation, actor, and PIE automation only after those foundations are stable.

This keeps UE Agent Kit narrower for some time, but preserves its core advantage: project-level understanding, controlled changes, evidence, and recoverability.
