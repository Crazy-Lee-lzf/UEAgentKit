# UEAgentKit Master Plan Correction Notes

> Date: 2026-08-27
>
> Scope: corrections identified while reviewing `UEAGENTKIT_MASTER_DEVELOPMENT_PLAN_20260827.md` and `UEAGENTKIT_MIDTERM_EXECUTION_SPEC_20260827.md`.
>
> This document records plan-level corrections only. It does not authorize implementation or commits.

## Status: all applied 2026-08-27

All seven corrections were verified against the source text and applied to
`UEAGENTKIT_MASTER_DEVELOPMENT_PLAN_20260827.md` and
`UEAGENTKIT_MIDTERM_EXECUTION_SPEC_20260827.md`.

Two corrections were applied with an added scope adjustment, and the review found
three additional defects not listed below. All are recorded in section 9.

No code was changed. No commits were made.

## 1. W4 stage numbering

Resolved.

`UEAGENTKIT_W4_MULTI_OPERATION_BOUNDED_BATCH_DETAILED_PLAN_20260826.md` is the authoritative W4 phase definition:

```text
W4-0 ... W4-7
```

Master/Midterm plans should reference that definition instead of maintaining a second W4 phase table.

## 2. Memory schema versions

Current proposal uses Schema v4 for both M2 and M4. Split the migrations:

```text
v3 -> v4   M2: memory_l0_events
v4 -> v5   M4: memory_embeddings / vector metadata
```

Each structural database change must have an unambiguous schema version.

## 3. Query embedding contract

M4 currently says that the query path generates no embeddings. That is not valid for vector retrieval.

Correct contract:

```text
Distill/index path:
  generate and persist record embeddings

Query path:
  generate exactly one query embedding
  do not regenerate corpus/record embeddings
```

M4 also needs a deterministic, resumable backfill path for L1 records created before vector support is enabled.

## 4. Track X dependency graph

The task cards and the drawn DAG are inconsistent.

Expected dependency shape:

```text
W4 + D1
  |- X1 Widget BP -> X2 Anim BP -> X3 Graph Writer + demand gate
  |- X4 Level Actor -> X6 Asset Performance
  `- X5 C++ Symbols
```

X1, X4, and X5 do not need to be serialized unless a new technical dependency is found.

## 5. P4 feasibility probe

Before freezing the C1 Source Control result schema, add a small capability probe against UE5.6 + Perforce.

Confirm which fields are available through generic `ISourceControlState` and which require Perforce-specific APIs, especially:

```text
checkedOutBy
locked / lockedBy
depotPath
headRevision
haveRevision
changelist
```

Freeze the public C1 schema only after this probe.

## 6. Memory evidence revision binding

Do not bind every distilled fact only to an Asset SHA-256.

Use evidence-specific revision bindings, for example:

```text
Asset fact       -> asset Revision
Policy rule      -> Policy digest
Impact result    -> index generation + relevant asset revisions
Change Set fact  -> checkpoint / Change Set revision set
P4 observation   -> provider observation/head revision where available
```

This prevents project rules from remaining falsely fresh after their real source changes.

## 7. P4 ownership inference

Observed checkout/lock history should not automatically become a durable claim that a person is the owner or maintainer of a directory.

Prefer factual observations such as contributor frequency. Promote ownership only from explicit project configuration, team rules, or user-confirmed evidence.

## 8. Current priority

The corrections above should be applied before their corresponding Track begins. They do not block continuing W4.

```text
Current main line:
W3 complete
-> W4 bounded batch

Before M4:
fix schema/query embedding contracts

Before Track X:
fix DAG

Before P4 C1:
run feasibility probe
```

## 9. Applied disposition and additional findings

### 9.1 Per-correction disposition

| # | Verified | Applied to | Note |
|---|---|---|---|
| 1 | yes | master:293 | Note said "Resolved" but master:293 still read `W4-0 … W4-6`; the authoritative table at master:332 was already correct |
| 2 | yes | master:430, master:559, spec:551, spec:721 | Scope added, see 9.2 |
| 3 | yes | master:564, spec:~750 | Gate was unsatisfiable as written; backfill sub-task added |
| 4 | yes | spec §9.1, §9.2, §9.4 | Track X redrawn as three independent branches |
| 5 | yes | spec C1 | Bounded exit added, see 9.2 |
| 6 | yes | master:495, spec M3 | Per-evidence binding table; `revision_set` is now a set, not a single hash |
| 7 | yes | master:672/759, spec C1/C4/M3 | Tool renamed; explicit prohibition added |

### 9.2 Scope added beyond the note

**Correction 2** — the note did not state what happens when the vector extra is never
installed. The `v4 → v5` migration must run and create the table regardless of
dependency state; otherwise two databases both reporting v5 have different shapes and
later migrations cannot determine a safe starting point. Added as a gate.

**Correction 5** — "freeze only after the probe" had no exit if no multi-user Perforce
environment is available. Added a three-way field verdict
(`generic` / `provider` / `unavailable`) and an explicit bounded exit: unconfirmed
fields ship as explicitly nullable with a documented reason rather than blocking C1.

**Correction 7** — tightened beyond the note. Inferring ownership from checkout history
would store a `model-inferred` conclusion as `tool-observed`, which is a source-grading
violation rather than only an accuracy problem. It also turns the memory database into a
record of individuals' activity patterns, which is outside this project's purpose. Both
reasons are now stated in the spec, and the tool was renamed
`ue_get_asset_ownership` → `ue_get_asset_checkout_state` so the name matches what it
returns.

### 9.3 Additional defects found during review

Not in the original note. Found by checking every task card's `前置条件` field against
the drawn DAG:

```text
V1 precondition   Card says none (may start immediately);
                  DAG incorrectly hung it under T0. Fixed.

M6 precondition   Card says M5 + W5; DAG omitted the W5 edge entirely. Fixed.

W4 phase count    An earlier summary described the W4 plan as having 10 sub-phases.
                  The plan defines 8 (W4-0 … W4-7). No document contained this error;
                  recorded here only to prevent it re-entering one.
```

### 9.4 Verification performed

```text
grep residual 'W4-0 … W4-6'                    0 hits
grep residual 'ue_get_asset_ownership'         0 hits
grep residual '查询路径零嵌入生成' as a gate    0 hits
                                               (1 hit remains inside the explanation
                                                of why the phrasing is invalid)
schema versions                                v3→v4 at M2 only, v4→v5 at M4 only
card preconditions vs DAG                      all 25 cards now consistent
```

Documents changed: 3. Code changed: none. Commits: none.
