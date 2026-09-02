# UEAgentKit M3 Deterministic L0 → L1 Distillation — Detailed Plan

> Date: 2026-08-31
>
> Branch: `feature/memory-context`
>
> Baseline: `d38c23c70fdf710117e6bd31f738b20665c20cd9`
>
> State: **READY FOR IMPLEMENTATION**
>
> Risk: medium / required UE level: **U0**
>
> Scope: deterministic offline L0→L1 distillation, exact provenance/source binding, restart-safe `distilled` progression, conservative Evidence Chain verdict evaluation, automatic Knowledge-node placement, explicit CLI entry point, and M1/M2 regression/performance gates. No LLM, vector retrieval, prompt injection, P4, C++, UE, or background scheduler.

## 1. Objective

M2 made durable Writer observations available as bounded, append-only L0 events. M3 converts only evidence that can be proven from those structured observations into durable L1 Project Memory records.

The required pipeline is:

```text
M2 durable L0 event
  ↓
verify exact source artifact / inline evidence
  ↓
select deterministic rule
  ↓
build deterministic L1 identity + provenance
  ↓
create/reuse L1 record
  ↓
optionally update an explicitly linked Evidence Chain
  ↓
mark evaluated L0 event distilled
```

M3 must improve memory usefulness without turning observed data into speculative conclusions.

## 2. Authority and current repository facts

Current authoritative facts override historical wording in the Master/Midterm documents where they conflict:

```text
M1                              COMPLETE / REVIEWED / U0
M2                              COMPLETE / REVIEWED / U0
M2 checkpoint                   d38c23c70fdf710117e6bd31f738b20665c20cd9
Memory schema                   v4
memory_l0_events                exists, append-only except M3 distilled flag
memory_evidence_chains          exists
memory_records                  existing L1-compatible record store
Project dependencies            []
UE required for M3              no
P4 prerequisite                 no
```

M3 must not redesign M2 capture or Writer safety architecture unless actual repository facts prove a defect.

## 3. Explicit scope corrections from historical Midterm wording

### 3.1 No schema bump in M3

Historical planning anticipated future schema work, but the actual M2 v4 schema already contains every storage primitive required by M3:

```text
memory_l0_events.distilled
memory_records
memory_revisions
memory_artifacts
memory_relations
knowledge_nodes
memory_evidence_chains
```

Therefore:

```text
CURRENT_MEMORY_SCHEMA_VERSION remains 4
```

Do not introduce a v5 migration in M3. This preserves the currently planned M4 schema boundary for embedding metadata unless M4 later proves another change is necessary.

### 3.2 Required M3 trigger is explicit/offline only

Historical Midterm text proposed:

```text
explicit command
idle >30 s background trigger
startup async trigger
```

The current repository has no dedicated Memory background-job lifecycle, and M1 explicitly established first-Tool/request-path performance gates. M3 therefore freezes only this required trigger:

```text
ue-agent memory distill
```

Idle/startup scheduling is **deferred**, not silently implemented in M3. It may be reconsidered after the explicit path is stable and measured. M3 must not add daemon threads, timers, startup jobs, or work to the synchronous task path.

### 3.3 Do not invent missing Policy provenance

Current M2 inline `workflow_rejection` captures deterministic rejection identity but older M2 events do not contain the exact Policy digest that caused a rejection.

Therefore:

```text
old policy-rejection L0 with no exact policy digest
  → may become knownIssue
  → MUST NOT become projectRule
```

M3 may extend future policy-rejection capture to include the fixed Policy digest, then distill those new events into `projectRule` safely.

## 4. Non-goals

M3 does not implement:

```text
M4 vector / sqlite-vec / model2vec / RRF
M5 L2/L3 generation or prompt injection
M6 symbolic compression
P4 observation or source-control inference
new Writer operations
EditorBridge / C++ changes
UE execution
background idle/startup scheduler
model-generated hypotheses
LLM-assisted summarization
arbitrary artifact parsing outside the L0 allowlist
```

`pyproject.toml [project].dependencies` remains `[]`.

## 5. New service boundary

Preferred implementation:

```text
src/ue_agent_kit/memory_distill.py

MemoryDistillationService
DistillationBudget
DistillationResult
DistillationRuleResult
SourceBinding
```

The service is constructed with fixed paths/identity:

```text
memory_database
project_key
artifact_root        fixed M2 Writer work_root
index_database       fixed current index for source validation
policy_path          fixed Project Write Policy for policy-binding validation
```

All paths are normalized once. No caller-supplied arbitrary file path is accepted per L0 event.

## 6. Hard distillation bounds

Freeze the first M3 bounds:

```text
DISTILL_DEFAULT_MAX_EVENTS = 100
DISTILL_HARD_MAX_EVENTS    = 100
DISTILL_MAX_ARTIFACT_BYTES = 1 MiB per JSON artifact
DISTILL_MAX_OUTPUTS_EVENT  = 4
DISTILL_MAX_SOURCE_EVENTS  = 16 per derived L1 record
DISTILL_MAX_DETAILS_BYTES  = 8 KiB per derived L1 record
```

The 1 MiB artifact cap applies before JSON decode. Oversized artifacts are deferred/fail-closed rather than loaded unboundedly.

Distillation must use stable ordering:

```text
occurred_at_utc ASC, event_id ASC
```

No random ordering and no current-wall-clock data may enter deterministic content/identity.

## 7. L0 source verification

### 7.1 Artifact-backed L0

Before deriving any L1 record:

```text
artifact_ref must be relative
artifact_ref must resolve under fixed artifact_root
no path escape
artifact must exist
size <= 1 MiB
stream SHA-256 must equal event.artifact_digest
JSON must decode to an object where the rule requires JSON
```

If the artifact is missing, changed, oversized, or undecodable:

```text
no L1 output
L0 remains distilled=0
result marks event deferred with a deterministic reason
no crash of the whole batch
```

This allows a later retry if evidence becomes available again.

### 7.2 Inline rejection L0

`workflow_rejection` has no artifact. It may use only the bounded deterministic M2 fields:

```text
errorCode
operation
assetPaths
changeSetId
targetIdentity
future exact source binding fields explicitly added by M3
```

Never use exception text, stack trace, process output, usernames, or arbitrary request payload as L1 content.

## 8. Deterministic L1 identity and restart safety

Every derived L1 record must use a deterministic existing-format record id:

```text
mem_<32 lowercase hex>
```

Identity input:

```text
project_key
rule_id
ordered source_event_ids
output_index
```

Recommended:

```text
sha256(canonical JSON(identity payload))[:32]
```

Restart contract:

```text
create deterministic L1
→ if crash occurs before distilled flag update
→ next run computes same record id
→ exact existing record is verified/reused
→ event is then marked distilled
```

If the deterministic record id already exists but its provenance/content does not match the expected rule output, fail closed with a collision/tamper error. Do not overwrite it.

No additional UNIQUE schema is required.

## 9. Meaning of `distilled`

`distilled=1` means:

> this exact L0 observation has been deterministically evaluated by the current M3 rules.

It does **not** mean that the event necessarily produced an L1 record.

Valid examples:

```text
resident live_write before persistence
  → evaluated, insufficient for L1 fact
  → distilled=1 / 0 outputs

verified checkpoint
  → evaluated
  → one or more L1 outputs
  → distilled=1

artifact missing/digest mismatch
  → not safely evaluated
  → distilled remains 0
```

Add a dedicated M3 service path to update only `memory_l0_events.distilled`. Do not expose generic L0 UPDATE/DELETE.

## 10. Frozen deterministic rule set

Rule IDs are versioned constants. Changing semantics later requires a new rule version rather than silently changing historical identity.

### R1 — verified persisted write → `projectFact`

```text
rule_id      l1.verified-write.v1
inputs       verified checkpoint / verified checkpoint_set / verified trust-backed aggregate
output       projectFact
source       tool-observed
```

Do not generate a persisted fact from `live_write` resident success alone.

The rule must bind exact proven asset revisions from the durable evidence. If a required final asset revision cannot be derived, do not claim a valid persisted fact.

### R2 — non-policy deterministic rejection/failure → `knownIssue`

```text
rule_id      l1.workflow-rejection.v1
input        workflow_rejection or durable failed/partial boundary
output       knownIssue
source       tool-observed
```

Content must describe only:

```text
operation/error code
bounded affected asset(s)
lifecycle boundary
```

No causal speculation.

Recovery `partial` / `failed` / `blocked` is also eligible for a `knownIssue` when the durable recovery artifact proves the boundary.

### R3 — exact Policy rejection → `projectRule`

```text
rule_id      l1.policy-rejection.v1
input        policy-rejected L0 WITH exact policy digest binding
output       projectRule
source       tool-observed
```

M3 may extend future rejection capture so `policy-rejected` stores the exact fixed Policy digest in bounded L0 details.

Without that digest:

```text
projectRule output = forbidden
fallback            = knownIssue or evaluated/no-output according to available facts
```

### R4 — verified Semantic Diff → `projectFact`

```text
rule_id      l1.semantic-diff.v1
input        semantic_diff terminal verified/success artifact
output       projectFact
```

Only facts directly present in the durable semantic diff may be extracted. Missing/analysis-gap evidence never becomes a positive fact.

### R5 — proven supersession chain → `decisionRecord`

```text
rule_id      l1.supersession.v1
input        Change Set with superseded operation(s) + matching durable live-write evidence
output       decisionRecord
```

This rule requires exact target/value provenance from the durable Writer records. If the old/new target chain cannot be reconstructed exactly, produce no decision record.

### R6 — deterministic Impact Analysis → `projectFact` (source-gated)

Historical M3 planning includes Impact Analysis, but current M2 does not automatically capture an `impact_analysis` L0 source.

Therefore:

```text
rule implementation may exist for bounded synthetic/future L0
production output is source-gated
M3 must not rerun Impact Analysis merely to fabricate an L0 source
```

No new synchronous analysis is added to the Writer/task path.

## 11. L1 content contract

All automatically distilled records:

```text
source_kind = tool-observed
confidence  = deterministic fixed value from rule class, not model probability
source_ref  = distill:<rule_id>:<primary_event_id>
```

`details` must include a bounded provenance object:

```json
{
  "distillation": {
    "ruleId": "l1.verified-write.v1",
    "sourceEventIds": ["l0_..."],
    "sourceBindings": [
      {"kind": "assetRevision", "key": "/Game/...", "revision": "sha256:..."}
    ]
  }
}
```

Do not copy complete Writer JSON into `body` or `details`.

Store original L0 evidence pointers as `MemoryArtifact` entries where useful:

```text
artifact_kind = l0-source
artifact_ref  = existing relative artifact_ref
```

## 12. Source-binding and stale semantics

A key M3 invariant is:

> each derived record is bound to the actual source version that makes its statement true.

### 12.1 Asset-backed facts

For asset-backed facts, populate existing `MemoryRevision` entries:

```text
asset_path
exact persisted revision
revision_stable=True
```

Existing revision invalidation remains authoritative.

### 12.2 Policy-backed rules

Policy-backed records use a generic source binding in deterministic `details.distillation.sourceBindings`:

```text
kind     policyDigest
key      project-write-policy
revision sha256:...
```

M3 must add a narrow validator for these generic bindings. A newly generated tool-observed policy record may be promoted from `unverified` to `valid` only after the supplied fixed Policy file hashes to the expected digest.

When that digest later changes:

```text
valid/unverified → stale
reason = source-binding-mismatch
```

### 12.3 Index-generation / future Impact bindings

If R6 is activated by a real L0 source, bind:

```text
index generation id
plus exact relevant asset revisions when present
```

No synthetic `/Game/...` pseudo-path may be used to smuggle Policy/index digests into `memory_revisions`.

### 12.4 Validation entry point

M3 distillation runs generic source validation at the start/end of the explicit maintenance command. Existing `memory validate` asset-revision behavior must remain backward compatible.

Do not make request-time recall perform source hashing or index validation.

## 13. Knowledge-node automatic placement

For a record with a primary asset path:

```text
/Game/Characters/Hero/DA_HeroStats.DA_HeroStats
→ /project/content/characters/hero
```

Rules:

```text
strip /Game/
drop object/package basename
map directory segments through existing normalize_knowledge_path
ensure /project/content and each ancestor exists
use deterministic kn_<hash(path)> IDs for newly created nodes
reuse existing nodes by normalized path
attach the L1 record to the deepest directory node
```

For project-wide rules/issues with no asset path:

```text
/project
```

No node is created from arbitrary rejection text.

Creation must be parent-first and deterministic. Tests must prove no orphan and no cycle.

## 14. Evidence Chain evaluation

M2 already stores Evidence Chains with:

```text
supported
rejected
inconclusive
```

M3 may update an **existing explicitly linked chain** only from L0 events whose `hypothesis_id` already equals that chain id.

Conservative verdict rule:

```text
support signal only       → supported
reject/failure signal only→ rejected
both                       → inconclusive
no terminal evidence      → inconclusive
```

Terminal support signals are only explicitly linked successful/verified observations. Terminal reject signals are explicitly linked rejected/failed/stale observations.

Do not infer semantic support merely because two events share an asset/changeSet.

For post-hoc L1 provenance where old immutable L0 events have no `hypothesis_id`, keep `sourceEventIds` in L1 distillation metadata; do not mutate historical L0 solely to attach a chain.

No model-generated hypothesis is introduced.

## 15. Explicit CLI

Add:

```text
ue-agent memory distill
```

Required inputs/options:

```text
--memory-database
--project-key
--artifact-root
--index-database
--policy
--max-events        default 100, hard max 100
```

Output is bounded structured JSON:

```text
selectedCount
evaluatedCount
distilledCount
producedRecordCount
reusedRecordCount
deferredCount
failedCount
producedRecordIds (bounded)
deferred event ids/reason codes (bounded)
elapsedMs
pendingAfter
```

The command runs synchronously because it is explicitly invoked as offline maintenance. It must not be called implicitly by MCP request handling.

No new public MCP mutation tool is required in M3.

## 16. Interruption and resume contract

Distillation processes events one at a time in stable order with deterministic record identities.

Required crash/retry boundaries:

```text
before L1 create             → event remains pending
L1 created / before flag     → rerun reuses exact L1 then marks distilled
flag updated                 → rerun skips event
unsupported/no-output rule   → flag may be set after deterministic evaluation
source unavailable/tampered  → flag remains 0 for retry
```

`--max-events` itself provides bounded resumability. No checkpoint file or background worker is required.

## 17. Performance measurement

Extend the Memory benchmark with an M3 offline section, or add a small stdlib-only `MeasureMemoryDistillation.py` if keeping the existing M1/M2 report clearer.

Required deterministic fixture:

```text
100 pending L0 events
mix of verified, rejection, policy, semantic-diff, supersession/recovery cases
fixed small durable artifacts
```

Hard M3 gate:

```text
100 L0 deterministic distillation < 5 seconds
```

Also retain all existing gates:

```text
M1 first Tool delta p95        < 200 ms
M1 direct recall p95           < 300 ms
M1 task-end append p95         < 100 ms
M2 four-event capture p95      < 100 ms
```

Because M3 is explicit/offline, M3 must not measurably add work to Memory-disabled or normal first-Tool paths.

Do not put tight wall-clock assertions in unit tests; timing gates belong in benchmark scripts.

## 18. Test plan

Expected new focused module:

```text
tests/python/test_memory_distill.py
```

Required coverage:

### Core/idempotency

```text
verified event → deterministic L1
same event rerun → same record / no duplicate
crash-equivalent preexisting L1 + pending L0 → reuse + mark distilled
no-output event marks distilled
missing/tampered evidence stays pending
100 hard event bound
```

### Rule coverage

```text
R1 verified write → projectFact
R2 rejection/failure → knownIssue
R3 policy rejection WITH digest → projectRule
R3 policy rejection WITHOUT digest → never projectRule
R4 semantic diff → projectFact
R5 exact supersession → decisionRecord
R6 source-gated impact fixture → projectFact, without adding production capture
```

### Source binding/stale

```text
asset Revision change → asset-derived L1 stale
Policy digest change → policy-derived projectRule stale
future index binding mismatch → source-bound record stale
no source-type substitution with fake /Game paths
```

### Tree

```text
asset path → deterministic normalized node path
missing ancestors created parent-first
existing ancestors reused
no orphan/cycle
project-wide issue attaches to /project
```

### Evidence Chain

```text
support-only linked events → supported
reject-only → rejected
mixed → inconclusive
unlinked events do not influence verdict
no model hypothesis creation
```

### Request-path isolation

```text
normal MCP startup/first Tool does not invoke distillation
Memory disabled path does not instantiate/run distiller
no daemon/background thread created by M3
```

## 19. Likely implementation files

Expected changes are concentrated in:

```text
src/ue_agent_kit/memory_distill.py                 new
src/ue_agent_kit/memory_l0.py                     distilled/chain narrow mutation primitives
src/ue_agent_kit/memory_service.py                service facade
src/ue_agent_kit/project_memory.py                narrow status/reuse helpers if needed
src/ue_agent_kit/memory_tree.py                   deterministic path helper only if needed
src/ue_agent_kit/workflow_common.py               exact policy-digest rejection binding
src/ue_agent_kit/cli.py                           memory distill command
scripts/MeasureMemoryOverhead.py                  retain M1/M2 gates; optional M3 section
scripts/RunPythonTests.py                         memory domain includes new tests
tests/python/test_memory_distill.py                new
tests/python/test_memory_l0.py                    source-binding/chain regression
tests/python/test_cli.py or existing CLI tests    explicit command
benchmarks/memory/m3_memory_distillation_*.json   evidence
M3 Result document                                closure
```

No C++, Writer operation registry, Policy schema, published version, or required dependency change should be necessary.

## 20. Implementation slices

### M3-0 — baseline and rule-fixture setup

```text
read actual v4/L0/L1 helpers once
freeze exact rule IDs/constants in code
author deterministic fixtures
capture pre-M3 M1/M2 benchmark gate
```

Do not re-plan Track M architecture.

### M3-1 — distillation core

```text
MemoryDistillationService
stable pending selection
artifact verification
hard bounds
deterministic record IDs
restart-safe reuse
mark distilled primitive
```

### M3-2 — rule outputs / provenance

```text
R1-R6 rule functions
source bindings
asset revisions
Policy digest capture closure
bounded L1 content
```

### M3-3 — tree + Evidence Chain

```text
deterministic node placement
chain verdict evaluator for explicitly linked events only
```

### M3-4 — CLI / validation

```text
ue-agent memory distill
source-binding stale validation
bounded structured result
prove no background/request-path execution
```

### M3-5 — benchmark / closure

```text
100-event distillation benchmark
Memory G1
final G2 once
Result document
```

## 21. Validation Budget

M3 is medium risk but pure Python/SQLite, **U0**.

### G0

During implementation:

```text
focused test_memory_distill / memory_l0 / CLI tests
touched Ruff
RunPythonTests.py fast only at meaningful checkpoints
```

Do not run the full suite repeatedly.

### G1

After final functional source state:

```text
py -3.12 scripts/RunPythonTests.py domain memory
focused CLI / project-memory tests
M3 100-event benchmark gate
M1/M2 benchmark regression gate
```

### G2 — once at closure

```text
py -3.12 scripts/RunPythonTests.py full
Ruff once
compileall
ValidateRelease.py --skip-tests --skip-ruff
git diff --check
M1/M2 benchmark gate
M3 100-event benchmark gate
```

No UE/UBT/W4/W5/V2 heavy matrices.

## 22. Acceptance matrix

M3 is complete only if all required items pass:

```text
A1  Memory schema remains v4; existing v4 database opens unchanged
A2  explicit offline distill command exists; no implicit/background scheduler
A3  pending selection is stable and <=100 events
A4  artifact ref/root/size/digest is revalidated before derivation
A5  deterministic record identity prevents duplicate L1 on replay/restart
A6  distilled flag semantics are retry-safe
A7  verified persisted writes can produce projectFact with exact asset Revision
A8  deterministic rejection/failure produces bounded knownIssue without speculation
A9  projectRule requires exact Policy digest provenance
A10 old policy rejection lacking digest never becomes projectRule
A11 verified Semantic Diff produces only evidence-backed facts
A12 supersession produces decisionRecord only when exact old/new chain is provable
A13 source-gated Impact rule does not add synchronous Impact Analysis work
A14 asset-derived L1 becomes stale when its bound asset Revision changes
A15 policy-derived L1 becomes stale when its Policy digest changes
A16 automatic Knowledge-node placement is deterministic and has no orphan/cycle
A17 Evidence Chain verdict uses only explicitly linked L0 evidence
A18 no LLM/model call exists in distillation path
A19 no P4/C++/UE/new required dependency
A20 100-event deterministic distillation <5 s
A21 all M1 gates remain PASS
A22 M2 four-event capture p95 remains <100 ms
A23 Memory G1 PASS
A24 final reviewed source state has one closure G2 PASS
```

Any A4-A22 failure blocks M4.

## 23. Safety invariants preserved

M3 must preserve:

```text
Policy / Revision / canonical identity Writer gates
Dirty-package fail closed
exact session/transaction binding
Fast Verify != Strong Verify
explicit Save authorization
truthful partial states
exact recovery semantics
W4 bounds
M1 RecallBudget/deadline gates
M2 append-only/idempotent L0 semantics
fixed project / fixed artifact root
no arbitrary SQL/shell/UObject execution
```

Distillation is downstream interpretation of durable evidence. It may never make a failed or unverified Writer action appear successful.

## 24. Execution-Agent efficiency requirements

This stage is being used to compare local coding-Agent execution efficiency.

The Agent should:

```text
read all files needed for one M3 slice in one batch
implement one coherent slice before validation
avoid one-function-at-a-time status/read/test loops
do not re-argue frozen Plan decisions unless repository facts conflict
run Memory G1 once after final functional source state
run full suite once at G2
```

Final Result must report:

```text
total elapsed Agent time
G0/G1/G2/benchmark elapsed time
number of Memory-domain runs
number of full-suite runs
major implementation slices
longest unusual/debug step
tool-call count if the harness exposes it
UE runs (expected 0)
```

This reporting is observational, not a correctness gate.

## 25. Stop conditions requiring owner input

Stop only if:

```text
actual v4 schema cannot support restart-safe M3 without migration
frozen M1/M2 safety/performance contract must be weakened
correct source provenance requires fabricating missing historical evidence
implementation necessarily enters M4/M5/P4/C++/UE scope
100-event <5s gate cannot be met without an architecture tradeoff
another Agent's uncommitted work would be overwritten
```

Ordinary implementation bugs/test failures should be diagnosed and fixed without stopping for approval.

## 26. Expected closure

At successful closure:

```text
M1  efficiency/budget        COMPLETE
M2  deterministic L0        COMPLETE
M3  deterministic L1        COMPLETE
Memory schema               still v4
M4                           READY TO PLAN, not started
```

Do not commit/push/rebase/tag/release unless the current owner instruction explicitly authorizes that action.
