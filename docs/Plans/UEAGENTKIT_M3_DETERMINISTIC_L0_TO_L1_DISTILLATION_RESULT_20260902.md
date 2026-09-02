# UEAgentKit M3 Deterministic L0 → L1 Distillation — Result

> Date: 2026-09-02
>
> Branch: `feature/memory-context`
>
> Baseline: `d38c23c70fdf710117e6bd31f738b20665c20cd9`
>
> Stage state: **COMPLETE / G2 PASS**
>
> Risk: medium / required UE level: U0
>
> Execution mode: **interrupted-takeover**. A previous Agent stopped mid-task
> and left uncommitted M3 work plus unrelated sandbox/test-fixture edits. This
> Result covers both the takeover classification and the completed M3 contract.

## 1. Takeover classification (T0)

Verified git facts at takeover:

```text
HEAD                     2f4c85b7a0b6e07d50f91ffc65e3c6ba608dbcbd  (expected)
branch                   feature/memory-context, ahead 4 of origin
M3 product baseline      d38c23c70fdf710117e6bd31f738b20665c20cd9  (parent)
M3 Result                did not exist
M3 focused               17 / 17 PASS
Memory domain            219 / 219 PASS
focused MCP tests        19 / 19 ERROR  (AttributeError: temp_root)
```

The inherited dirty tree was separated into genuine M3 work and sandbox-only
scope creep:

```text
GENUINE M3 (kept)
  src/ue_agent_kit/memory_distill.py        new
  scripts/MeasureMemoryDistillation.py      new
  tests/python/test_memory_distill.py       new
  src/ue_agent_kit/memory_l0.py             policy_digest capture + mark_event_distilled
  src/ue_agent_kit/memory_service.py        distillation service facade
  src/ue_agent_kit/cli.py                   ue-agent memory distill command
  src/ue_agent_kit/workflow_common.py       policy-digest rejection binding
  scripts/RunPythonTests.py                 memory domain registers test_memory_distill
  benchmarks/memory/m3_memory_*.json        inherited benchmark evidence

SANDBOX-ONLY SCOPE CREEP (reverted, 20 files)
  19 tests/python/test_*.py                 tempfile.TemporaryDirectory -> repo .tmp
  scripts/MeasureMemoryOverhead.py          same migration
```

Every reverted file was diffed individually and confirmed to contain **zero**
M3 content (`distill`, `policy_digest`, etc.) before restoring it. No blanket
`git reset`/`clean`/`restore` was used; the M3 files were never touched.

Reverting was not merely cosmetic. Three concrete defects came from the
migration:

1. `tests/python/test_mcp_server.py` set `self.tmp_root` but kept 11
   references to `self.temp_root` plus one `self.temporary_directory` —
   19/19 `AttributeError` errors.
2. The migration was applied to only 19 of ~60 test modules, leaving the suite
   incoherent, and it wrote untracked `.tmp/` residue into the repository
   (`.tmp` is not gitignored).
3. Repo-relative `shutil.rmtree()` teardown aborts under the harness
   safe-delete bulk guard, while OS-temp `tempfile` cleanup is exempt. The
   migration therefore made test teardown environment-hostile for no benefit.

`tests/python/test_memory_distill.py` and
`scripts/MeasureMemoryDistillation.py` were moved to `tempfile` to match the
repository convention. The legacy `.tmp/debug_*`, `.tmp/memory_context_*` and
`.tmp/mcp_*` directories were classified as inherited temporary evidence and
left in place untracked.

## 2. Implemented contract

`src/ue_agent_kit/memory_distill.py` implements the frozen service boundary
(`MemoryDistillationService`, `DistillationBudget`, `DistillationResult`,
`DistillationRuleResult`, `SourceBinding`) with the frozen bounds, stable
`occurred_at_utc ASC, event_id ASC` pending selection, per-event artifact
revalidation (relative ref, root containment, ≤1 MiB, streaming SHA-256), and
the narrow `mark_event_distilled` L0 primitive.

Rules R1-R6 produce `projectFact`, `knownIssue`, `projectRule`,
`projectFact`, `decisionRecord` and source-gated `projectFact` respectively,
all `source_kind = tool-observed` with `source_ref =
distill:<rule_id>:<primary_event_id>` and bounded `details.distillation`
provenance. A historical policy rejection without an exact Policy digest can
never become a `projectRule`; `workflow_common` now captures the digest for
future rejections so those may.

The trigger is only the explicit offline `ue-agent memory distill` command. No
daemon, timer, startup job, or request-path work was added, and the M2 L0
allowlist is unchanged.

## 3. Contract gaps found at takeover and closed

### A. Deterministic Knowledge Node identity — confirmed and fixed

`_ensure_knowledge_path` called `create_knowledge_node` without `node_id`, so
newly created nodes used the `kn_<uuid4>` default. M3 now derives
`kn_<sha256(canonical{projectKey, knowledgePath})[:32]>` from the
`UNIQUE(project_key, path)` node identity, parent-first. Existing nodes are
still reused by normalized path. A cross-database test proves two independent
runs produce the same `kn_` id.

### B. Restart-safe deterministic L1 reuse — confirmed and fixed

Reuse previously only checked `project_key` equality and a `distill:` prefix.
A new narrow helper `project_memory.record_provenance_digest()` computes the
same canonical content/evidence digests that `create_memory_record` stores.
Reuse now requires the stored `content_sha256` and `evidence_sha256` to equal
the freshly computed expectation; otherwise the run fails closed with
`distill-record-content-mismatch` and the event stays pending. A record at the
deterministic id that came from another source fails closed with
`distill-record-collision`. Both are covered by tests.

### C. Source/index validation — confirmed disconnected and fixed

`index_database` was accepted and stored but never read, and the
`source_validation` parameter was dead. `validate_source_bindings()` now also
compares each distilled record's stable asset Revision set against the current
Revisions reported by the fixed immutable index, so a later index Revision
change (or a missing asset) marks the record stale with reason
`revision-set-mismatch`. Validation runs only inside the explicit offline
command; request-time recall performs no index access or hashing. Scoped to
`source_ref LIKE 'distill:%'`, so non-M3 records are unaffected.

### D. Supersession provenance — confirmed and fixed

`_rule_supersession` consumed the Change Set serialization alone and never
used its `connection`. R5 now requires a durable `live_write` journal for the
same Change Set, asset, and stable target key whose old/new values exactly
match the Change Set operation (canonical JSON equality). Without that
corroboration no `decisionRecord` is emitted. The provenance records both
event ids and `liveWriteEvidenceEventId`. A test proves a Change Set-only
supersession and a value-mismatched journal both produce nothing.

### E. R6 Impact Analysis — verified already source-gated

No production `impact_analysis` L0 source exists and `_classify_event` never
routes to R6, so no synchronous Impact Analysis was added. The M2 allowlist is
unchanged, and the focused test proves appending an `impact_analysis` event is
still rejected.

### F. Benchmark fixture coverage — confirmed inadequate and fixed

The 100-event fixture contained only verified / rejection / policy /
semantic-diff / live-write / no-op cases — no supersession and no recovery —
despite the script docstring claiming them. The fixture is now 80 single
events covering all rule families (including verified and partial recovery)
plus 10 supersession pairs (durable live-write journal + Change Set = 2 events,
1 `decisionRecord`), for exactly 100 events and 70 records.

## 4. CLI and evidence chain

`ue-agent memory distill` now also runs `validate_source_bindings()` and
`evaluate_evidence_chains()` and returns `sourceValidation` and
`evidenceChainVerdicts` alongside the distillation payload. Chain verdicts use
only explicitly linked L0 events (`hypothesis_id` = chain id). A CLI test
proves the command, its idempotent rerun, and the new payload sections.

## 5. Benchmark evidence

Canonical M3 report:

`benchmarks/memory/m3_memory_distillation_after_20260902.json`

```text
selected / evaluated / distilled        100 / 100 / 100
produced records                        70   (R1 20, R2 20, R3 10, R4 10, R5 10)
reused / deferred / failed                0 /   0 /   0
pendingAfter                                0
100-event distillation                 263.852 ms  < 5000 ms  PASS
```

M1/M2 regression report:

`benchmarks/memory/m3_m12_regression_20260902.json`

```text
first Tool Memory incremental p95        13.924 ms  < 200 ms  PASS
direct automatic recall p95              12.983 ms  < 300 ms  PASS
task-end append p95                      14.133 ms  < 100 ms  PASS
four-event L0 capture batch p95          15.026 ms  < 100 ms  PASS
automatic recall budget                  3 items / 1633 chars / 769 estimated tokens  PASS
no-hit recall                            0 items / 0 chars  PASS
```

The inherited 2026-08-31 benchmark drafts predated the Section 3.F fixture fix.
They were used only during takeover diagnosis and were removed during owner
closure cleanup. The `20260902` reports above are the authoritative M3 evidence.

## 6. Validation evidence

G0 (during implementation, after each coherent slice):

```text
test_memory_distill                     23 / 23 PASS
test_mcp_server                         19 / 19 PASS  (was 19 / 19 ERROR at takeover)
test_memory_cli                         10 / 10 PASS
test_task_context / test_project_memory 45 / 45 and 17 / 17 PASS
```

G1, run once after the final functional source state:

```text
memory domain                          226 / 226 PASS / 27.360 s
Ruff (src + tests/python)                            PASS
```

G2, run once after the final source state:

```text
full Python                            909 / 909 PASS / 95.298 s
Ruff (full src + tests/python)                       PASS
compileall                                           PASS
ValidateRelease 0.7.0                                PASS (--skip-tests --skip-ruff)
Schemas                                               3 PASS
Patch examples                                       16 PASS
git diff --check                                     PASS
UE / UBT                                 not run (required U0)
```

## 7. Acceptance matrix

```text
A1  Memory schema remains v4; existing v4 database opens unchanged          PASS
A2  explicit offline distill command; no implicit/background scheduler       PASS
A3  pending selection stable and <=100 events                                PASS
A4  artifact ref/root/size/digest revalidated before derivation              PASS
A5  deterministic record identity prevents duplicate L1 on replay/restart    PASS
A6  distilled flag semantics retry-safe                                      PASS
A7  verified persisted writes produce projectFact with exact asset Revision  PASS
A8  deterministic rejection/failure produces bounded knownIssue              PASS
A9  projectRule requires exact Policy digest provenance                      PASS
A10 old policy rejection lacking digest never becomes projectRule            PASS
A11 verified Semantic Diff produces only evidence-backed facts               PASS
A12 supersession produces decisionRecord only with durable live-write proof  PASS
A13 source-gated Impact rule adds no synchronous Impact Analysis work        PASS
A14 asset-derived L1 becomes stale when its bound asset Revision changes     PASS
A15 policy-derived L1 becomes stale when its Policy digest changes           PASS
A16 automatic Knowledge-node placement deterministic, no orphan/cycle        PASS
A17 Evidence Chain verdict uses only explicitly linked L0 evidence           PASS
A18 no LLM/model call exists in distillation path                            PASS
A19 no P4/C++/UE/new required dependency                                     PASS
A20 100-event deterministic distillation <5 s                                PASS
A21 all M1 gates remain PASS                                                PASS
A22 M2 four-event capture p95 remains <100 ms                               PASS
A23 Memory G1 PASS                                                          PASS
A24 final source state has one closure G2 PASS                              PASS
```

## 8. Execution efficiency

Total elapsed takeover-to-closure: **about 45 minutes**. The longest
validation steps were the single G2 full-suite run (909 tests / 95.298 s), the
Memory G1 domain run (27.360 s), and the two benchmark gates (0.264 s M3,
6.4 s M1/M2). The Memory
domain was run once at G1 and the full suite once at G2; UE runs were 0. The
harness did not expose a reliable aggregate tool-call count.

The dominant non-implementation cost was the T0 takeover classification: 25
inherited files had to be diffed and classified before any edit, which was the
correct price for preserving uncommitted M3 work without a blanket reset.

## 9. Main files

```text
src/ue_agent_kit/memory_distill.py            new
src/ue_agent_kit/project_memory.py            record_provenance_digest helper
src/ue_agent_kit/memory_l0.py                 policy_digest capture + mark_event_distilled
src/ue_agent_kit/memory_service.py            distillation service facade
src/ue_agent_kit/cli.py                       memory distill command + validation payload
src/ue_agent_kit/workflow_common.py           policy-digest rejection binding
scripts/MeasureMemoryDistillation.py          new
scripts/RunPythonTests.py                     memory domain registers test_memory_distill
scripts/MeasureMemoryOverhead.py              restored to repository tempfile convention
tests/python/test_memory_distill.py           new (23 tests)
tests/python/test_memory_cli.py               distill command test
benchmarks/memory/m3_memory_distillation_after_20260902.json
benchmarks/memory/m3_m12_regression_20260902.json
```

## 10. Owner closure cleanup

After independent closure review, the inherited untracked `.tmp/` debug/test
residue was confirmed to contain no tracked or canonical acceptance evidence
and was removed. The obsolete 2026-08-31 M3 benchmark drafts were also removed;
only the authoritative 2026-09-02 M3 and M1/M2 regression reports are retained.

At Agent completion no commit, push, rebase, tag, release, or version change
was performed. The owner subsequently authorized a local M3 checkpoint commit.
M4 (vector / sqlite-vec) remains READY TO PLAN, not started.
