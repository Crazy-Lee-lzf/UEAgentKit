# Dual-Branch and Worktree Workflow

Updated: 2026-08-01

## Current workspace

```text
E:/WorkSpace/UEAgentKit
    branch: feature/live-editor-realtime-io

E:/WorkSpace/UEAgentKit-MemoryContext
    branch: feature/memory-context
```

The directories share one Git object database but have separate working trees, indexes, checked-out branches, and uncommitted changes, so both can remain open in VS Code for parallel development.

## Branch ownership

### feature/live-editor-realtime-io

Owns running-Editor context, real-time CRUD, batch tasks, PIE diagnostics, Change Sets, transactions, Undo/Discard, and pre-save workflows.

### feature/memory-context

Owns the Knowledge Tree, Active Work, Context Packs, Evidence, Revision invalidation, project semantics, and long-running task continuity.

### main

Accepts complete, tested, documented milestones. It is the common baseline and release source rather than a long-lived experimental branch.

## Shared contracts

Project/Asset/Editor Session identity, Task Context, Change Set, operation result envelopes, Evidence, Revision/freshness, error models, and token/result budgets must not be designed independently on both branches.

Shared contracts should land as focused commits in `main`, then both feature branches synchronize from `main`.

## Synchronization rule

Normal flow is only:

```text
main → feature/live-editor-realtime-io
main → feature/memory-context
```

Feature branches do not merge directly into one another. Shared work moves through `main`. Synchronize early when shared schemas, identity, MCP registration, error handling, test baselines, or cross-track dependencies change.

## Merge cadence

Merge usable vertical milestones instead of accumulating months of divergence. A 1–2 week candidate cadence is reasonable, but a complete boundary matters more than a calendar date.

A milestone must:

1. provide a complete behavior boundary;
2. preserve published compatibility or document migration;
3. pass Ruff, Python tests, and `git diff --check`;
4. compile under UE5.6 when C++ changes;
5. include real UE5.6 regression for Editor behavior;
6. update bilingual public documentation;
7. avoid exposing arbitrary Python, console, UObject, or Save All in default agent mode.

## First milestones

### Realtime I/O

- unified realtime Query and Batch Task requests;
- current Editor Context;
- progress, cancellation, summary, and expansion for batch tasks;
- minimum Change Set lifecycle;
- existing Live Write operations integrated into the common model.

### Memory/Context

- Knowledge Tree nodes, paths, and parent/child links;
- existing Memory Records bound to Knowledge Nodes;
- minimum Active Work model;
- compatibility reads for the 0.6.0 Memory API;
- minimum progressive Context query.

### First integration point

```text
TaskContext
ChangeSet
EvidenceReference
AssetIdentity
RevisionReference
```

## Git principles

- fetch before starting work in either Worktree;
- regularly synchronize both feature branches with `main`;
- use PR or equivalent review for integration;
- never force-push `main`;
- do not commit local VS Code workspaces, caches, build output, or test projects;
- after pushes verify `origin/<branch>...HEAD = 0 0`.
