# UEAgentKit C1/C2 — P4 Minimum Dogfood — Detailed Plan

> **Publication note (2026-09-06):** pre-publication Track C hashes in this archived artifact are owner-local audit references. The public/sanitized Track C implementation is `5b705a7b693eff4af9ceb808df978f09e329dca9`. Raw P4 machine/user/client/depot identifiers are intentionally omitted from published probe evidence.
>
> Date: 2026-09-03
>
> Branch: `feature/source-control-collaboration`
>
> Branch baseline: `fdf6b5c12aceaefb0e61478bee7a9eefdf5ade76` (`main == origin/main` after M1-M5 integration and release-validation CI repair)
>
> Product baseline: `c0b01aac4201710466ae9c9a5ee39f8965704b36` (M5 COMPLETE)
>
> Prior planning checkpoint: `1c7e2ff39b28a9ff6d7a1bbf4d1151dfcc923d42` (merged into `main`; retained as historical planning ancestor)
>
> State: **IMPLEMENTED / OWNER CORRECTIVE REVIEW PASS / A25 OWNER-FIXTURE BLOCKED**
>
> Required UE level: **U0**
>
> Required runtime dependencies: **remain `[]`**
>
> Authority: `UEAGENTKIT_P4_AGENT_OPERATION_BOUNDARY_DECISION_20260903.md`

## 1. Goal

Implement the minimum Perforce layer required before real-project write-enabled dogfood:

```text
C1  Source Control Awareness
C2  Advisory + checkout/local-write assistance
```

This is intentionally not a P4 firewall. P4 collaboration state is advisory and auditable; it must not independently hard-block a local UEAgentKit Writer operation.

The permanent human-only boundary remains:

```text
Agent MUST NEVER execute:
  submit
  revert / revert -a
  p4 delete
  equivalent filesystem deletion of P4-managed files
```

C3 changelist preparation/resolve/audit and C4 Memory integration are deferred.

## 2. Repository-grounded baseline

Current repo search found no existing P4 provider/service/tool implementation. Track C therefore starts from a new narrow module rather than extending legacy source-control code.

Current machine read-only probe:

```text
p4.exe                   available on PATH
P4 CLI                   2025.1 / 2810567
P4D                      2025.1 / 2810567
configured local server  reachable
current test client       reachable
UEAgentKit Git worktree   not backed by a depot file (`p4 fstat pyproject.toml` -> no such file)
```

Do not assume the historical 2020.1 client version from old project notes. Implementation must capability-probe the actual CLI it runs against.

## 3. Frozen non-goals

C1/C2 does **not** implement:

```text
submit
revert
p4 delete
resolve / merge             -> C3
create/edit/reorganize CLs   -> C3
Memory capture of P4 state   -> C4
automatic ownership inference
P4Python dependency
generic arbitrary p4 command execution
shell command passthrough
UE/C++ changes
M6
Writer safety semantic changes
```

No UE/UBT process is required.

## 4. Critical safety distinction

Preserve two independent domains:

```text
Writer-owned safety
-------------------
Policy / Revision / canonical identity / Dirty state /
session / transaction / Save / Verify / recovery

May fail closed.

P4 collaboration state
----------------------
opened / otherOpen / lock / have/head / behind / mapping /
provider availability / local writable override

Produces warnings/readiness only.
Must not by itself reject local testing.
```

Example valid result:

```text
writerSafetyReady   true   (reported by Writer, not fabricated by P4 layer)
sourceControlReady  false
submitReady         false
localTestReady      true
```

## 5. C1-0 mandatory capability probe

Before implementing the parser/runner, probe the actual installed P4 CLI and save deterministic evidence under:

```text
benchmarks/source_control/c1_p4_capability_probe_<date>.json
```

Probe read-only only:

```text
p4 version/path
p4 info
-G tagged/marshalled output support
-ztag fallback behavior
p4 where on mapped + unmapped examples
p4 fstat fields on mapped files when available
p4 opened read behavior
headType / haveRev / headRev / action / change
otherOpen / otherLock representation
sync -n preview behavior
local-change detection commands needed by safe-sync design
Unicode/path-with-space handling
provider unavailable / timeout behavior
```

Preferred transport:

```text
p4 -G ...
→ Python stdlib `marshal` decode
```

because it avoids locale-sensitive human text parsing and adds no dependency.

If `-G` is not reliable for required commands, use a narrow `-ztag` parser. Do not silently parse ordinary human-formatted output for product state.

## 6. Provider architecture

Expected new module:

```text
src/ue_agent_kit/source_control.py
```

Narrow components:

```text
P4CommandRunner
P4SourceControlService
SourceControlFileState
SourceControlWarning
SourceControlStatusResult
SourceControlPrepareResult
```

The command runner must accept structured operations, not arbitrary command strings.

Initial internal allowlist for C1/C2:

```text
read:
  info
  where
  fstat
  opened
  diff/local-change probe
  sync -n

write:
  edit
  sync exact clean file only, if C1-0 proves safe preconditions
```

Explicitly prohibited even inside private runner APIs:

```text
submit
revert
delete
obliterate
unlock/lock stealing
admin commands
arbitrary command token arrays from MCP input
```

Use `subprocess` argv arrays only. No `shell=True`.

Required execution bounds:

```text
max files/request          16
max path chars/file        1024
single P4 subprocess       bounded timeout (initial 2 s, tune from evidence)
response/output            bounded; no unbounded stderr/stdout retention
credentials/tickets        never returned in tool payloads
```

Provider unavailable/timeouts degrade to advisory metadata; they do not make Writer fail.

## 7. Path identity

Public C1/C2 requests may accept:

```text
local filesystem paths
/Game package paths
```

For `/Game` packages, map only the normal project mount in C1/C2:

```text
/Game/Foo/Bar
→ <Project>/Content/Foo/Bar.uasset
or <Project>/Content/Foo/Bar.umap
```

Require exactly one existing candidate. Do not guess when both/neither exist.

Plugin mounts and external actor/package fan-out are deferred unless repository facts prove a narrow deterministic mapping already exists.

P4 `where` remains authoritative for depot/client mapping once a local path is resolved.

## 8. C1 — Source Control Awareness

### 8.1 Public state model

Per file expose bounded structured facts where available:

```text
inputPath
localPath
depotPath
clientPath
mapped
providerAvailable
fileType
exclusiveLockType          # e.g. +l when deterministically known
haveRev
headRev
headAction
openedForEdit
openedByCurrentClient
action
change
otherOpenUsers[]           # bounded
lockedByOther
otherLockUsers[]           # bounded
behindHead
localModified              # true/false/unknown
writable
localWritableOverride      # known from UEAgentKit action receipt when applicable
sourceControlReady
submitReady
localTestReady
warnings[]
```

Never expose passwords, tickets, arbitrary environment values, or raw P4 exception text.

### 8.2 Advisory severity

Stable severities:

```text
info
warning
strong-warning
```

Examples:

```text
clean/latest/own edit          no warning
not opened                    info
other user ordinary open      warning
binary+l / other lock         strong-warning
behind head                   warning
provider unavailable          warning
unresolved/divergent observed strong-warning
local writable override       strong-warning + submitReady=false
```

No severity value directly becomes a Writer rejection.

### 8.3 Public surfaces

Expected CLI:

```text
ue-agent source-control status ...
```

Expected MCP/tool surface:

```text
ue_source_control_status(...)
```

Tool is read-only.

Do not automatically add P4 calls to every `ue_get_task_context` request in C1/C2. Source-control network latency must not become a universal Task Context tax before real dogfood evidence exists.

## 9. C2 — Advisory + Local Write Assistance

Expected explicit tool:

```text
ue_source_control_prepare_write(...)
```

The Agent calls it before a Writer operation when source control assistance is desired. It prepares P4/local writability but does not decide Writer safety.

### 9.1 Order of operations

For an exact bounded file set:

```text
1. capture pre-state
2. optional safe sync, only if explicitly requested and proven clean
3. p4 edit/checkout where appropriate
4. if checkout cannot make file writable and explicit override is allowed:
     remove local readonly protection only
5. capture post-state
6. return receipts + warnings + readiness
```

No automatic submit/revert/delete follows.

### 9.2 Checkout / p4 edit

Allowed when:

```text
file is mapped
file exists
request identifies exact file
```

Another user's ordinary open is warning-only.
An exclusive lock may cause P4 edit to fail; that failure is surfaced, not converted into Writer rejection.

Do not force lock stealing.

### 9.3 Local writable override

Default: disabled.

Must require an explicit request flag such as:

```text
allow_local_writable_override=true
```

Behavior:

```text
remove local readonly attribute
record before/after filesystem mode
record that P4 checkout was not obtained
localWritableOverride=true
submitReady=false
strong warning
```

After override:

```text
no automatic sync over that modified file
no revert
no delete
no claim that the file is normally opened in P4
```

### 9.4 Safe sync assistance

Allowed only if C1-0 proves a deterministic way to establish:

```text
mapped
not opened for edit
no local writable override
no unresolved state
no local modification
behind head
```

Then an explicitly requested exact-file sync may run.

If cleanliness cannot be proven, skip sync and return a warning. **Do not weaken this check just to make sync convenient.**

Safe sync is assistance, not a prerequisite for local Writer execution.

## 10. No destructive-operation escape hatch

Tests must prove all of the following:

```text
no MCP tool for submit
no MCP tool for revert
no MCP tool for delete
no CLI action for submit/revert/delete
private P4 runner rejects prohibited operation enums
no generic `p4_command` string/tool exists
no filesystem-delete helper is exposed as a P4 workaround
```

Even an explicit user request does not change this product capability boundary.

Batch submit/revert/delete preparation belongs to C3 and stops before the final human operation.

## 11. Real P4 validation strategy

### 11.1 Default automated tests

Use a deterministic fake P4 executable/process fixture for:

```text
-G decoding
status field normalization
otherOpen/otherLock
behind-head state
provider unavailable/timeout
checkout success/failure
exclusive-lock warning
local writable override
safe-sync precondition matrix
prohibited command enforcement
path quoting / spaces
```

The fake fixture must validate exact argv and must not act as a generic shell.

### 11.2 Real read-only acceptance

Use the configured local P4 server/client for read-only C1 smoke:

```text
info
mapped/unmapped where/fstat
provider identity/version
```

No shared production depot is required.

### 11.3 Real C2 mutation acceptance

Because the current UEAgentKit Git worktree is not P4-managed, real `p4 edit` acceptance requires an **owner-designated safe, already mapped fixture file**.

Flow:

```text
owner identifies safe fixture
→ Agent captures pre-state
→ Agent performs bounded p4 edit / state verification only
→ optionally validate readonly transition
→ Agent stops
→ owner manually reverts/cleans the fixture
```

The test harness must not perform `p4 revert` for cleanup.

If no safe fixture is authorized, C1 may close and C2 real-mutation acceptance is reported `blocked` rather than bypassing the human-only rule.

## 12. Expected touched files

Likely surface:

```text
src/ue_agent_kit/source_control.py              new
src/ue_agent_kit/cli.py
src/ue_agent_kit/mcp_source_control_tools.py    new
src/ue_agent_kit/mcp_server.py                  registration only if required
src/ue_agent_kit/tool_registry.py               registration only if required
scripts/RunPythonTests.py
tests/python/test_source_control.py              new
tests/python/test_mcp_source_control_tools.py    new if MCP split is used
benchmarks/source_control/
```

Do not modify Writer internals merely to make P4 mandatory. C1/C2 is explicit assistance first.

## 13. Validation Budget

Required UE level: **U0**.

During implementation:

```text
focused source-control tests
focused CLI/MCP registry tests
no full suite after small edits
```

G0 when meaningful:

```text
python scripts/RunPythonTests.py fast
```

G1 after final source-control state:

```text
python scripts/RunPythonTests.py domain workflow
```

If source-control gets its own registered domain, use that instead and document the change.

Stage-specific acceptance:

```text
C1-0 capability probe
fake-provider matrix
real read-only local P4 smoke
prohibited command tests
optional owner-assisted one-file real edit acceptance
```

G2 once at closure:

```text
py -3.12 scripts/RunPythonTests.py full
.venv\Scripts\ruff.exe check src tests/python scripts
py -3.12 -m compileall src tests/python scripts
py -3.12 scripts/ValidateRelease.py --expected-version 0.7.0 --skip-tests --skip-ruff
git diff --check
```

No UE/UBT.

## 14. Acceptance matrix

```text
A1  no required dependency added                                      PASS
A2  actual P4 CLI capability probe recorded                           PASS
A3  structured -G or narrow -ztag parsing only                        PASS
A4  no shell=True / arbitrary P4 command passthrough                  PASS
A5  exact bounded file requests                                       PASS
A6  mapped/unmapped state truthful                                    PASS
A7  have/head/open/action/change normalized                           PASS
A8  other-open state produces warning only                            PASS
A9  exclusive lock produces strong warning only                       PASS
A10 provider unavailable degrades to advisory result                  PASS
A11 P4 state alone never becomes Writer hard-block                    PASS
A12 p4 edit assistance works in fake fixture                          PASS
A13 checkout failure remains warning/receipt                          PASS
A14 local writable override explicit + auditable                      PASS
A15 override forces submitReady=false                                 PASS
A16 no auto sync after override                                       PASS
A17 safe sync only when cleanliness is proven                         PASS*
A18 submit capability absent                                          PASS
A19 revert capability absent                                          PASS
A20 delete capability absent                                          PASS
A21 prohibited private runner operations rejected                     PASS
A22 no destructive filesystem-delete bypass                           PASS
A23 CLI/MCP contract tests PASS                                       PASS
A24 real local P4 read-only smoke PASS                                PASS
A25 real one-file edit acceptance PASS or owner-fixture BLOCKED       BLOCKED*
A26 portable G2 PASS                                                  PASS
A27 UE/UBT runs = 0                                                   PASS
```

`*` may be marked `blocked` only when the real environment cannot safely provide the required proof; do not fake it.

## 15. Stop / owner-decision conditions

Stop before broadening scope if implementation facts require any of:

```text
P4Python as required dependency
human-text-only parsing for correctness-critical state
unbounded generic P4 command execution
Writer hard-block based solely on P4 collaboration state
submit/revert/delete capability
automatic lock stealing
unsafe sync without deterministic local-change proof
real mutation testing that would require Agent revert for cleanup
```

## 16. Delivery boundary

At completion produce one Result:

```text
docs/Plans/UEAGENTKIT_C1_C2_P4_MINIMUM_DOGFOOD_RESULT_<date>.md
```

Report:

```text
actual P4 CLI/server capability facts
provider/parser design
warning/readiness model
checkout/override/safe-sync evidence
prohibited-operation evidence
real P4 read-only and mutation acceptance status
G0/G1/G2 counts/times
UE runs (expected 0)
```

Do not push, rebase, tag, release, or change published version without separate owner authorization.
