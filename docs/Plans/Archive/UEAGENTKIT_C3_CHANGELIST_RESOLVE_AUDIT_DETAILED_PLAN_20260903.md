# UEAgentKit C3 — Pending Changelist, Bounded Resolve & Audit — Detailed Plan

> **Publication note (2026-09-06):** pre-publication Track C hashes in this archived artifact are owner-local audit references. The public/sanitized Track C implementation is `5b705a7b693eff4af9ceb808df978f09e329dca9`. Raw P4 machine/user/client/depot identifiers are intentionally omitted from published probe evidence.
>
> Date: 2026-09-03
>
> Branch: `feature/source-control-collaboration`
>
> C3 product baseline: `5366a70cdc30e3c4b9a10234d4d9f1ee2a967e5e` (`C1/C2 COMPLETE / OWNER REVIEW PASS`)
>
> Main baseline at branch creation: `fdf6b5c12aceaefb0e61478bee7a9eefdf5ade76`
>
> Authority: `docs/Plans/UEAGENTKIT_P4_AGENT_OPERATION_BOUNDARY_DECISION_20260903.md`
>
> State: **READY FOR IMPLEMENTATION**
>
> Required UE level: **U0** for the C3 scope frozen here. No UE/UBT unless the scope is explicitly expanded to binary-package replay.

## 1. Goal

C3 turns the C1/C2 advisory layer into a bounded collaboration handoff layer:

```text
exact file set
→ inspect pending changelist / resolve state
→ create or update one pending CL when requested
→ move exact already-opened files into that CL
→ perform only bounded, conflict-free text resolve when explicitly requested
→ capture post-state + evidence
→ present a manual Submit / Revert / Delete handoff
→ STOP before the human-only final action
```

The intended responsibility boundary remains:

```text
Agent: understand / prepare / organize / reconcile / verify
Human: submit / revert / delete
```

C3 must not turn P4 into a Writer hard-block layer.

## 2. Non-goals / permanent prohibitions

C3 MUST NOT expose or execute:

```text
p4 submit
p4 revert / revert -a
p4 delete
p4 change -d
p4 obliterate
p4 lock / unlock
p4 integrate / merge
p4 shelve / unshelve
p4 resolve -af
p4 resolve -at
p4 resolve -ay
p4 resolve -f
p4 resolve -t
arbitrary p4 argv
shell passthrough
filesystem deletion as a substitute for p4 delete
```

C3 also does not:

- make P4 state a Writer fail-closed condition;
- automatically resolve `.uasset` / `.umap` content;
- automatically accept one side of a binary conflict;
- change published version / tag / release;
- start C4 Memory integration;
- start M6;
- introduce required third-party dependencies.

`pyproject.toml [project].dependencies` must remain `[]`.

## 3. Repo-grounded starting state

C1/C2 already provide:

```text
src/ue_agent_kit/source_control.py
  P4CommandRunner
  P4SourceControlService
  exact local / /Game path resolution
  p4 -G marshal transport
  bounded stdout/stderr
  status / opened / lock / have/head / local-diff awareness
  p4 edit
  explicit local writable override
  exact-file safe sync

src/ue_agent_kit/mcp_source_control_tools.py
  ue_source_control_status
  ue_source_control_prepare_write

source-control tool group
  opt-in via --enable-source-control
```

C1/C2 closure evidence:

```text
C1/C2 owner-review checkpoint   5366a70c
focused source-control          38 / 38 PASS
focused MCP                      8 / 8 PASS
source-control G1               46 / 46 PASS
full G2                       1014 / 1014 PASS (17 skipped)
A25 real edit                    PASS / owner ratified preserved fixture evidence
UE / UBT                         0 / U0
```

The A25 fixture remains opened in P4 and unchanged. C3 must not clean it up automatically.

## 4. C3-0 actual P4 2025.1 capability probe

Read-only probe on the current machine established:

```text
P4 client / server        2025.1 / 2810567
change -o                 supported
change -i                 supported; intended structured stdin path
reopen -c <change>        supported
resolve -n                supported
resolve -o                supported
resolve -am               supported
resolve -c <change>       supported
current A25 fixture       no file(s) to resolve
```

`p4 -G change -o` returns a structured marshaled record containing at least:

```text
Change
Client
User
Status
Description
FilesN...
```

Observed new-change values:

```text
Change = new
Client = <OWNER_P4_CLIENT>
User   = lzf
Status = new
```

Implementation MUST re-probe `change -i` structured stdin behavior with a fake fixture first. A real pending-CL mutation may be used only after the owner explicitly authorizes a safe acceptance fixture.

## 5. Runner extension contract

Extend the existing structured runner; do not build a second P4 execution layer.

Allowed new command shapes are narrowly frozen to:

```text
changes -s pending -c <currentClient>
change -o [<pendingChangeId>]
change -i                  # structured form input only
opened -c <pendingChangeId> <exactPaths...>
reopen -c <pendingChangeId> <exactPaths...>
resolve -n [-o] [-c <pendingChangeId>] <exactPaths...>
resolve -am [-c <pendingChangeId>] <exactPaths...>
```

The runner MUST validate command + option shape, not merely command name.

### 5.1 `change -i` stdin

`change -i` is the only C3 command allowed to receive stdin.

The public/service API must never accept arbitrary stdin bytes. Build one typed changelist form internally from validated fields.

For create:

```text
Change       new
Client       exact current provider client
User         exact current provider user
Status       new
Description  bounded caller-provided description
Files        omitted / empty; file movement happens only through reopen
```

For description update:

```text
read existing spec via change -o <id>
→ require pending/new state
→ require current user/client ownership
→ preserve all fields except Description
→ write structured form through change -i
```

Forbidden form manipulations:

```text
change deletion
owner/user reassignment
client reassignment
submitted change modification
-f / -u / -U
restricted/public type changes in C3
Jobs mutation
file list mutation through the change form
```

Description hard bound: **4096 UTF-8 bytes**.

## 6. Pending changelist model

Add bounded structured state, e.g.:

```text
SourceControlChangelistState
  changelistId
  status
  user
  client
  description
  files[]
  currentUserOwned
  currentClientOwned
  pending
  fileCount
  submitReady
  warnings[]
```

Hard bounds:

```text
list pending CLs            <= 50
files per C3 request        <= 16 (reuse C1/C2 bound)
files returned per CL       <= 100
changelist id               decimal positive integer only
no wildcard / depot revision syntax
```

`default` is observable, but C3 should organize review work into a numbered pending changelist when mutation is requested.

## 7. Changelist preparation semantics

Add one bounded preparation operation:

```text
prepare_changelist(
  exact_paths,
  description,
  changelist_id=None,
  change_set_id=None,
)
```

Behavior:

```text
no changelist_id
→ create one current-user/current-client pending CL
→ re-query exact created CL

existing changelist_id
→ require pending
→ require current user + current client ownership
→ optionally update description

then for every exact path
→ require mapped
→ require already opened in current client
→ require not localWritableOverride-only
→ reopen -c <id> exact file
→ capture post-state
```

C3 MUST NOT use `reopen -t`; it must not change file type.

If any file cannot be safely moved, return a per-file failed receipt and do not pretend the whole set is ready. Do not automatically undo successful moves because P4 revert is human-only and there is no cross-file atomicity claim.

## 8. Durable audit receipt

C3 should persist a small source-control audit receipt using the repository's existing bounded WorkRoot/output plumbing rather than modifying Writer transaction semantics.

Suggested location:

```text
Output/<WorkRoot>/source-control/sc_*.json
```

Receipt fields must be bounded and deterministic:

```text
schemaVersion
receiptId
operation                 create-changelist | update-description | reopen | resolve-text
occurredAtUtc
providerClient
providerUser
changelistId
changeSetId               optional exact UEAgentKit Change Set id
exactFiles[]
preState[]
postState[]
actionReceipts[]
manualFinalAction         none | submit | revert | delete
submitCapability          false
revertCapability          false
deleteCapability          false
```

Do not copy entire Writer journals or P4 command output into the receipt.

If `changeSetId` is supplied, validate it through the existing Change Set validator. C3 does **not** need to bump Change Set schema merely to create the association; the durable C3 receipt may provide the reverse lookup first.

## 9. Resolve awareness

Add a read-only exact-file resolve status operation.

It may use:

```text
p4 resolve -n -o <exact files>
```

Return normalized state such as:

```text
needsResolve
resolveKind             content | filename | filetype | branch | delete | unknown
baseRevision
fileType
mergeableText
changelistId
warnings[]
```

A provider error or malformed `-G` response must fail closed to `resolveStateUnknown`, not guess that the file is resolved.

## 10. Bounded text resolve

Initial C3 automation supports only conflict-free automatic merge for explicitly mergeable text files.

Allowed first-pass file extensions:

```text
.cpp
.h
.ini
.json
.csv
.py
```

The implementation may add `.txt` / `.md` only if tests freeze the behavior. It must not infer `.uasset` / `.umap` as text even when a caller asks.

Exact workflow:

```text
status + resolve preview
→ require exact current-client opened file
→ require needsResolve=true
→ require mergeable text extension / P4 text-like type
→ capture pre SHA-256 + P4 have/head/open/CL state
→ p4 resolve -am <exact file>
→ re-query resolve -n
→ if still unresolved: strong warning, no fake success
→ if resolved: capture post SHA-256 + status
→ validate no unresolved state remains
→ write bounded audit receipt
```

`resolve -am` is chosen because P4 skips content conflicts instead of accepting them forcibly.

C3 MUST NOT use:

```text
-af   accepts conflicted merge output
-at   overwrites yours with theirs
-ay   ignores theirs
-f    re-resolve previous outcome
-t    force text merge on binary
```

If `-am` cannot resolve cleanly, leave the file unresolved and return a strong warning.

## 11. Unreal binary resolve policy

For:

```text
.uasset
.umap
```

C3 initial scope is **awareness + reconciliation handoff only**.

Never execute blind binary content resolve.

Return:

```text
needsResolve=true
binaryReconciliationRequired=true
submitReady=false
warning=strong-warning
```

When UEAgentKit Change Set / Semantic Diff / durable Writer evidence exists, surface references to that evidence so a later dedicated binary-replay workflow can reason about:

```text
base/latest reconciliation
→ replay Agent-owned bounded operation
→ Strong Verify
→ Semantic Diff / Trust
```

But that binary replay is **not implemented in this C3 scope** and must not be simulated with `resolve -at/-ay`.

This keeps C3 U0.

## 12. Manual final-action handoff

C3 must make human-only operations explicit in payloads.

### Submit intent

```text
exact files
→ Writer/Trust evidence summary when available
→ P4 readiness / unresolved state
→ dedicated pending CL
→ exact file list + description
→ manualFinalAction=submit
→ STOP
```

### Revert intent

```text
exact already-opened files
→ organize into dedicated pending CL when safe/requested
→ present exact list + warnings
→ manualFinalAction=revert
→ STOP
```

### Delete intent

```text
Impact Analysis / references handled by existing tools
→ source-control layer only reports current P4 state / related CL context
→ manualFinalAction=delete
→ STOP
```

No C3 tool executes the final action.

## 13. Public surfaces

Keep source-control opt-in.

Recommended additions:

```text
ue_source_control_changelists(
  changelist_id: str = "",
)
  read-only pending CL state

ue_source_control_prepare_changelist(
  paths: list[str],
  description: str,
  changelist_id: str = "",
  change_set_id: str = "",
)
  create/update pending CL + reopen exact current-client files

ue_source_control_resolve_status(
  paths: list[str],
)
  read-only bounded resolve preview

ue_source_control_resolve_text(
  paths: list[str],
  changelist_id: str = "",
)
  explicit conflict-free text resolve only
```

Do not expose a generic `ue_source_control_command` tool.

Capabilities must continue to report:

```text
submitCapability=false
revertCapability=false
deleteCapability=false
arbitraryCommandExecution=false
shellPassthrough=false
```

Add explicit C3 capability flags only when the tool group is enabled, e.g.:

```text
pendingChangelistPreparation=true
boundedTextResolve=true
binaryAutomaticResolve=false
```

## 14. Implementation phases

### C3-0 — Contract / capability freeze

- re-run current Git/P4 facts;
- add fake P4 form-input support;
- confirm `-G change -o` parsing;
- confirm expected `change -i` marshaled stdin format without real mutation first;
- freeze allowed command/option matrix.

### C3-1 — Pending CL read + typed form model

- list/read pending CLs;
- validate current user/client ownership;
- typed create/update description form;
- no reopen yet.

### C3-2 — Exact-file reopen + audit receipt

- move exact already-opened current-client files to one pending CL;
- deterministic partial receipts;
- durable source-control audit record;
- optional `changeSetId` linkage.

### C3-3 — Resolve awareness

- exact-file `resolve -n/-o` parsing;
- strong warning for unknown/malformed state;
- binary-vs-text classification.

### C3-4 — Conflict-free text resolve

- preview;
- exact `resolve -am` only;
- post-query + SHA-256 evidence;
- unresolved conflict remains unresolved.

### C3-5 — Human handoff semantics

- submit/revert/delete intent output;
- no executable final operation;
- capability and test scan proving no bypass.

### C3-6 — Acceptance / Result

- one G1 affected-domain run;
- one final G2;
- optional real pending-CL/reopen/text-resolve acceptance only with explicitly owner-authorized fixtures;
- no UE/UBT in frozen scope.

## 15. Test strategy / Validation Budget

During edits:

```text
focused source-control tests only
focused MCP source-control tests only
```

Meaningful G0 checkpoint:

```text
py -3.12 scripts/RunPythonTests.py fast
```

Final functional source state — G1 once:

```text
py -3.12 scripts/RunPythonTests.py domain source-control
```

Final G2 once:

```text
py -3.12 scripts/RunPythonTests.py full
.venv\Scripts\python.exe -m ruff check src tests\python scripts
py -3.12 -m compileall -q src tests\python scripts
py -3.12 scripts\ValidateRelease.py --expected-version 0.7.0 --skip-tests --skip-ruff
git diff --check
```

Do not repeatedly run the full suite.

UE / UBT expected: **0**.

## 16. Acceptance contract

```text
A1  C1/C2 contracts remain backward-compatible                                  PASS required
A2  required dependencies remain []                                             PASS required
A3  source-control remains opt-in                                                PASS required
A4  pending CL list/read is bounded and structured                              PASS required
A5  change -i uses typed internal form only; no arbitrary stdin                 PASS required
A6  create CL limited to current user/current client pending CL                 PASS required
A7  update description cannot change owner/client/status/files                 PASS required
A8  change -d / -f / -u / -U remain unreachable                               PASS required
A9  reopen moves only exact already-opened current-client files                 PASS required
A10 reopen cannot change file type                                               PASS required
A11 partial reopen returns truthful per-file receipts                            PASS required
A12 source-control audit receipt is bounded and restart-readable                PASS required
A13 optional Change Set linkage validates exact UEAgentKit changeSetId           PASS required
A14 resolve status uses exact preview and fails closed on parse uncertainty      PASS required
A15 binary .uasset/.umap never enter automatic text resolve                      PASS required
A16 resolve -am only runs on exact whitelisted text files                        PASS required
A17 resolve conflict remains unresolved with strong warning                      PASS required
A18 resolve -af/-at/-ay/-f/-t are unreachable                                  PASS required
A19 post-resolve re-query proves resolved state before success                   PASS required
A20 post-resolve receipt includes pre/post SHA-256 + P4 state                    PASS required
A21 submit/revert/delete capability flags remain false                           PASS required
A22 no CLI/MCP/private runner bypass exposes submit/revert/delete                PASS required
A23 no P4-only state hard-blocks local Writer testing                            PASS required
A24 source-control G1 passes once at final source state                          PASS required
A25 full G2 / Ruff / compileall / ValidateRelease / diff-check pass              PASS required
A26 UE/UBT runs = 0                                                              PASS required
A27 real CL/reopen/resolve mutation uses only explicitly owner-authorized fixture PASS or owner-fixture BLOCKED
```

`A27 BLOCKED` is an acceptable truthful closure state if no safe real unresolved fixture is available; deterministic fake-fixture coverage must still pass.

## 17. Definition of done

C3 is complete when an Agent can safely organize exact local work into a reviewable pending changelist, expose resolve state, automatically resolve only conflict-free text merges, leave binary/conflicted work unresolved, and hand the exact state to a human for final Submit/Revert/Delete — without ever gaining the ability to execute those final operations.
