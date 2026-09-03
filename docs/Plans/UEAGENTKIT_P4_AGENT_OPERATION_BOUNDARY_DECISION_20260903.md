# UEAgentKit P4 Agent Operation Boundary Decision — 2026-09-03

> Status: **PROVISIONAL DECISION / FROZEN FOR FUTURE TRACK C PLANNING**
>
> Scope: source-control behavior only. This document does not modify the current Track M / M4 implementation.
>
> Purpose: freeze the owner-approved boundary for what an Agent may and may not do with Perforce before C1/C2/C3 implementation begins.

## 1. Decision summary

UEAgentKit's P4 integration is an **advisory and preparation layer**, not a source-control enforcement firewall.

P4 state may produce warnings, stronger warnings, readiness flags, and suggested/manual follow-up actions, but **P4 state alone must not hard-block a local UEAgentKit write or local test**.

The permanent destructive-operation boundary is:

```text
Agent MAY:
  inspect P4 state
  diff
  checkout / p4 edit
  remove local readonly protection for local testing when necessary
  create pending changelists
  edit pending changelist descriptions
  reopen / move files between pending changelists
  sync with bounded safety checks
  inspect resolve state
  perform bounded resolve / merge work
  prepare exact file sets for human submit / revert / delete

Agent MUST NEVER:
  submit
  revert
  delete P4-managed files
```

`submit`, `revert`, and `delete` are **human-only final operations**. This is not a confirmation gate: the product must not expose executable Agent capabilities for these operations even when the user explicitly asks the Agent to perform them.

## 2. Precedence over older Track C wording

This decision supersedes older planning language in the Master/Midterm documents where Track C was described as a fail-closed P4 preflight or where Resolve was grouped with prohibited destructive operations.

Future C1/C2/C3 Detailed Plans must use this document as the source-control authority when those historical documents conflict.

This document does **not** weaken UEAgentKit's existing Writer safety gates. Revision freshness, canonical identity, Dirty-package state, session/transaction identity, verification, recovery proof, and other Writer-owned invariants remain independent fail-closed mechanisms.

## 3. P4 advisory model

P4 information should be classified into advisory severity, not direct rejection.

Example states:

| P4 state | Default UEAgentKit behavior |
|---|---|
| clean / latest / own checkout | normal |
| file not opened for edit | may run `p4 edit` if appropriate |
| opened for edit by another user | warning, continue |
| `binary+l` / locked by another user | **strong warning**, local test may still continue |
| local workspace behind head | warning, report exact state |
| provider unavailable | warning, continue with P4 readiness unknown |
| unresolved integration state | strong warning; local work may continue, but mark submit readiness false |
| local/depot divergence | strong warning; expose resolve/reconciliation options |

There is no P4 state whose sole presence automatically forces Writer rejection.

The UI/tool response should clearly distinguish:

```text
writerSafetyReady
sourceControlReady
submitReady
warnings[]
```

A local experiment can therefore be:

```text
writerSafetyReady = true
sourceControlReady = false
submitReady = false
```

without pretending that the P4 state is clean.

## 4. Local writable override

When a P4-managed file is readonly because it is not opened for edit, or when normal checkout cannot be obtained because another user owns an exclusive lock, the Agent may support a **local writable override** for local testing.

This must be explicit and audit-visible. It must never silently masquerade as a normal checkout.

Suggested state:

```json
{
  "openedForEdit": false,
  "lockedByOther": true,
  "localWritableOverride": true,
  "submitReady": false
}
```

Required behavior after a local writable override:

```text
- emit a strong warning;
- retain exact pre-write Revision / Writer evidence;
- do not claim the file is legitimately opened in P4;
- do not automatically sync over the local modified file;
- do not automatically revert it;
- do not automatically delete it;
- require explicit later reconciliation before submit readiness can become true.
```

This capability exists specifically to support local experimentation when depot collaboration state should not prevent a developer from testing an idea.

## 5. Resolve is allowed

Resolve is permitted because its purpose is to reconcile divergent workspace/depot states, not to publish or discard work by itself.

However, Resolve must be bounded and evidence-driven.

### 5.1 Text / mergeable files

Typical examples:

```text
.cpp
.h
.ini
.json
.csv
.py
```

The Agent may:

```text
read base / yours / theirs
→ perform three-way analysis
→ merge
→ inspect remaining conflicts
→ resolve
→ validate resulting content
→ place the file in a pending CL
```

Semantic assistance is encouraged where it improves ordinary text merging.

### 5.2 Unreal binary packages

Typical examples:

```text
.uasset
.umap
```

The Agent must not blindly treat these as line-mergeable text and must not perform a broad, content-uninspected "accept yours" or "accept theirs" policy.

Preferred approach when automation is possible:

```text
capture current P4 / Revision state
→ inspect UEAgentKit Change Set / Semantic Diff / durable evidence
→ reconcile to an appropriate latest/base state
→ replay the Agent-owned bounded change where possible
→ Strong Verify / Semantic Diff / Trust
→ mark resolve outcome
```

If a safe automatic reconciliation cannot be established, the Agent may leave the file unresolved, emit a strong warning, and present the human with the exact state and available choices. This does not by itself prohibit further local testing; it means the result is not submit-ready.

### 5.3 Resolve restrictions

Do not implement blind bulk behavior equivalent to "accept one side for every conflict without inspection".

Resolve operations should be bound to:

```text
exact file set
exact pre-resolve have/head/open state
exact change/revision evidence when available
post-resolve validation
pending changelist identity
```

## 6. Human-only destructive operations

The following operations are permanently human-only:

```text
P4 submit
P4 revert / revert -a
P4 delete
```

The restriction applies regardless of:

```text
user confirmation
a Policy option
batch mode
Agent confidence
source-control status
local shell availability
```

The Agent must not bypass the product boundary by issuing an equivalent shell command or by directly deleting a P4-managed workspace file to simulate `p4 delete`.

### 6.1 Why Revert remains human-only

A P4 revert can discard a workspace file containing more than the current Agent's edits:

```text
Agent changes
+ user changes
+ earlier pending work
+ changes from another local tool
```

UEAgentKit's own Writer Undo / Discard / checkpoint recovery / deterministic rollback remain allowed because they are bounded to UEAgentKit-owned transactions and evidence. They are not equivalent to a broad P4 revert.

### 6.2 Why Submit remains human-only

Submit changes depot/team-visible state. The Agent may prepare everything required for review, but publication remains a human responsibility.

### 6.3 Why Delete remains human-only

Deletion can invalidate references and can be difficult to reverse safely across a shared project. The Agent may run Impact Analysis, identify references, prepare a deletion candidate list and pending CL organization, but it must stop before performing the delete itself.

## 7. Changelist preparation is the Agent/human handoff boundary

Pending changelists are the preferred responsibility boundary.

### 7.1 User requests batch revert

Correct Agent behavior:

```text
identify the exact requested files
→ inspect opened/current CL state
→ create or select a dedicated pending CL
→ reopen/move the target files into that CL
→ present file list, warnings and CL id
→ STOP
```

The human then performs the actual revert manually, for example through P4V or an explicit local P4 command.

The Agent must not execute the final revert.

### 7.2 User requests submit

Correct Agent behavior:

```text
collect the exact UEAgentKit Change Set files
→ verify Writer / Semantic Diff / Trust state
→ inspect P4 readiness and unresolved warnings
→ create or select a pending review CL
→ reopen/move exact files into it
→ generate/update CL description
→ present diff/evidence/readiness summary
→ STOP
```

The human performs final review and submit.

### 7.3 User requests delete

Correct Agent behavior:

```text
identify exact deletion targets
→ run reference / Impact Analysis
→ report consequences
→ optionally organize related files/work into a dedicated pending CL
→ provide a manual deletion checklist
→ STOP
```

No Agent-side `p4 delete` or equivalent filesystem deletion is allowed.

## 8. Recommended future Track C shape

The previous Track C shape should be revised when implementation planning starts.

Recommended structure:

```text
C1  Source Control Awareness
    - provider state
    - opened/edit state
    - lock/exclusive-lock state
    - have/head revision
    - pending CL information
    - resolve state

C2  Advisory + Local Write Assistance
    - warning severity
    - p4 edit / checkout assistance
    - local writable override
    - safe sync assistance
    - no P4 hard-block of local Writer operation

C3  Changelist Preparation, Resolve & Audit
    - create/edit pending CL
    - reopen/move exact file sets
    - bounded resolve workflow
    - diff / readiness / evidence summary
    - manual-submit/manual-revert/manual-delete handoff

C4  Optional Memory Integration
    - durable source-control observations
    - no automatic personal ownership/maintainer inference
```

Permanently excluded from Agent capability planning:

```text
Agent Submit
Agent Revert
Agent Delete
```

## 9. Safety boundary between Writer and P4

The final architecture must preserve this distinction:

```text
Writer-owned correctness/safety
-------------------------------
Policy
Revision freshness
canonical identity
Dirty-package state
session / transaction binding
Save authorization
Strong Verify
Semantic Diff / Trust
exact recovery

These may fail closed.

P4 collaboration state
----------------------
checkout
lock
head/have
concurrent edit
resolve state
submit readiness

These produce advisory state, warnings, preparation actions and readiness flags.
They do not independently hard-block local testing.
```

## 10. Implementation acceptance examples

Future Track C tests should include at least these behaviors:

```text
1. another user has ordinary file opened for edit
   → warning only
   → local Writer may continue

2. another user owns binary+l lock
   → strong warning
   → checkout may fail
   → local writable override may permit local testing
   → submitReady=false

3. local file is clean but behind head
   → warning/state surfaced
   → bounded sync may be offered/performed

4. local file has divergence
   → resolve path available
   → Agent may resolve with bounded evidence

5. text conflict
   → Agent may three-way merge + resolve + validate

6. Unreal binary conflict
   → no blind bulk accept
   → use UE/Change Set evidence when possible
   → unresolved state may remain with warning

7. user asks "submit this"
   → Agent prepares dedicated pending CL + description + evidence
   → Agent does NOT submit

8. user asks "revert these 30 files"
   → Agent moves/reopens exact files into a dedicated pending CL
   → Agent does NOT revert

9. user asks "delete these assets"
   → Agent performs analysis/preparation only
   → Agent does NOT delete

10. attempts to bypass submit/revert/delete using direct shell or filesystem deletion
    → product capability remains unavailable
```

## 11. Current project decision

This boundary is intentionally frozen before Track C implementation so future C planning does not inherit the older overly restrictive fail-closed P4 model.

The intended product behavior is:

> **The Agent may understand, prepare, reconcile and organize source-control work, but the final Submit, Revert and Delete actions always remain manual. P4 collaboration risk should be visible and auditable without preventing deliberate local experimentation.**

No Track C code is implemented by this document, and no current Track M acceptance contract is changed.
