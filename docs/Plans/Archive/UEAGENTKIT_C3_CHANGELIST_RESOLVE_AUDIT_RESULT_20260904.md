# UEAgentKit C3 — Pending Changelist, Bounded Resolve & Audit — Result

> **Publication note (2026-09-06):** pre-publication Track C hashes in this archived artifact are owner-local audit references. The public/sanitized Track C implementation is `5b705a7b693eff4af9ceb808df978f09e329dca9`. Raw P4 machine/user/client/depot identifiers are intentionally omitted from published probe evidence.
>
> Date: 2026-09-04
>
> Branch: `feature/source-control-collaboration`
>
> C3 product baseline: `5366a70cdc30e3c4b9a10234d4d9f1ee2a967e5e`
>
> Plan: `docs/Plans/Archive/UEAGENTKIT_C3_CHANGELIST_RESOLVE_AUDIT_DETAILED_PLAN_20260903.md`
>
> Authority: `docs/Plans/UEAGENTKIT_P4_AGENT_OPERATION_BOUNDARY_DECISION_20260903.md`
>
> State: **COMPLETE / OWNER CORRECTIVE REVIEW PASS / CHECKPOINTED**
>
> Public Track C feature checkpoint: `5b705a7b693eff4af9ceb808df978f09e329dca9` (`feat: close bounded P4 collaboration through C3`)
>
> Push: **none**

## 1. Verified repository facts

```text
worktree                 E:\WorkSpace\UEAgentKit-Integration
branch                   feature/source-control-collaboration
planning HEAD            8d3a39e786b0147eba3710eb62b633d892f59183
C3 closure checkpoint   09a4c55c59e6388d729b745a4479ca5d7f64801c
C3 product baseline      5366a70cdc30e3c4b9a10234d4d9f1ee2a967e5e  (ancestor verified)
working tree            clean before C3; only C3 files changed after execution
push / rebase / tag     none
```

## 2. Actual changed files

```text
M src/ue_agent_kit/source_control.py               C3 runner extension + changelist/resolve/audit surface
M src/ue_agent_kit/mcp_source_control_tools.py     4 new MCP tools + docstrings
M src/ue_agent_kit/tool_registry.py                4 new source-control ToolDefinitions
M src/ue_agent_kit/mcp_server.py                   strict-argument list, capability flags, audit root wiring
M tests/python/test_source_control.py              Fake P4 fixture + C3 unit matrix (78 tests)
M tests/python/test_mcp_source_control_tools.py    C3 MCP contract tests (16 tests)
?? benchmarks/source_control/c3_p4_capability_probe_20260904.json   real read-only probe evidence
```

No Writer/Memory/Knowledge module was modified while passing by. `pyproject.toml` is untouched; required dependencies remain `[]`. No UE/UBT run occurred.

## 3. Actual P4 capability facts used (C3)

Real read-only probe evidence: `benchmarks/source_control/c3_p4_capability_probe_20260904.json`.

```text
P4 client / server        2025.1 / 2810567 (reachable, P4PORT=<OWNER_P4PORT>)
client                    <OWNER_P4_CLIENT>
user                      <OWNER_P4_USER>
real mutations            none (no owner-designated C3 fixture)
read-only smoke           status (mapped, have=1/head=1, opened current, submitReady=true)
                          changelists list (ok, readOnly, pendingCount=0)
                          resolve preview (needsResolve=false on the unchanged A25 fixture)
```

## 4. Runner extension contract

`P4CommandRunner` keeps the existing argv-only architecture (`subprocess.run`, never a shell, bounded stdout/stderr files). The allowlist adds the frozen C3 family:

```text
changes -s pending -c <currentClient>
change -o [<pendingChangeId>]
change -i                      # only command allowed to carry typed stdin
opened -c <pendingChangeId> <exactPaths...>
reopen -c <pendingChangeId> <exactPaths...>
resolve -n [-o] [-c <pendingChangeId>] <exactPaths...>
resolve -am [-c <pendingChangeId>] <exactPaths...>
```

New commands are validated with an **exact typed argv grammar** (`changes`/`change`/`reopen`/`resolve`/`opened -c`), not by command name alone. Highlights:

- `change` allows only `-o`/`-i`; `-d`/`-f`/`-u`/`-U` are unreachable.
- `reopen` allows only `-c <id>`; `-t` (filetype change) and `-f` are unreachable.
- `resolve` allows only `-n`/`-o`/`-am`/`-c <id>`; `-af`/`-at`/`-ay`/`-f`/`-t`/`-a`/`-A`/`-N`/`-d`/`-v` are unreachable. Interactive bare `resolve <file>` is prohibited; `-am` cannot combine with `-n`/`-o`; `-c` values must be decimal positive integers without leading zeros.
- Changelist ids and client names are typed tokens; wildcard/depot/revision tokens stay invalid path arguments.
- `change -i` is the only command that may receive stdin, and only through the module's internal typed form marshaler (`_marshal_change_spec`). Arbitrary stdin is not accepted by any public API.
- `p4 -G` marshal output stays the only state source; malformed/truncated output and nonzero exit codes fail closed.

## 5. Pending changelist schema / API

New bounded structured state (all serialized with the existing `SourceControlFileState` conventions):

```text
SourceControlChangelistState
  changelistId, status, user, client, description, files[], fileCount
  currentUserOwned, currentClientOwned, pending
  submitReady, warnings[]
```

Hard bounds enforced:

```text
pending CLs listed             <= 50
files returned per CL          <= 100
files per request              <= 16 (unchanged)
description                    1 .. 4096 UTF-8 bytes
changelist id                  decimal positive integer only
default                        observable but never used as a C3 mutation target
```

Public service operations (all on `P4SourceControlService`; the structured `p4 -G` runner is reused, no second execution layer):

```text
changelists(changelist_id="")                         read-only list/single spec
prepare_changelist(paths, description, *,             create/update CL + reopen exact files
                   changelist_id=None, change_set_id="")
resolve_status(paths)                                 read-only exact preview
resolve_text(paths, *, changelist_id=None)            bounded conflict-free text resolve
```

MCP tools (registered only when the `source-control` group is opt-in):

```text
ue_source_control_changelists(...)          read
ue_source_control_prepare_changelist(...)   planning
ue_source_control_resolve_status(...)       read
ue_source_control_resolve_text(...)         planning
```

All four are in `STRICT_SOURCE_CONTROL_ARGUMENT_TOOL_NAMES` (extra inputs rejected). Capabilities now additionally report, only when the group is configured:

```text
pendingChangelistPreparation = true
boundedTextResolve            = true
binaryAutomaticResolve        = false
```

## 6. Create / edit / reopen behavior

### 6.1 Create

`change -i` receives a typed marshal spec (`Change=new`, exact current `Client`/`User`, `Status=new`, bounded `Description`, no Files). After creation the changelist number is parsed from the structured response and then **re-verified by re-reading the spec**: pending status plus exact current user/client ownership. If the number cannot be confirmed the operation fails closed (no guessed success). Files are never placed in the spec form; they move only through `reopen`.

### 6.2 Edit description

For an existing `changelist_id` the current spec is read via `change -o <id>`, ownership/status are checked, and a new form is built that preserves `Change`/`Client`/`User`/`Status`/`Files` and replaces only `Description`. The update is verified by re-reading the description. Owner/client reassignment, file-list mutation through the form, `-d`/`-f`/`-u`/`-U` remain impossible.

### 6.3 Reopen

Each exact path must be mapped, opened for edit by the **current user AND current client**, and not override-like (writable only through a local non-checkout override). `reopen -c <targetId> <exactFile>` is executed per file, then the post-state is verified with `opened -c <targetId> <exactFile...>` against the current user/client before any receipt claims success. Files already in the target CL are reported as `already-in-changelist` (no duplicate move). A partial move returns truthful per-file receipts and never hides behind a global `ok=true`.

## 7. Ownership / client validation

Current ownership always means **same user AND same client**, never user alone. This applies to:

- destination CL selection (create or reuse),
- description updates (must be owned pending),
- reopening files (must be opened by current user/client),
- resolve eligibility (same),
- post-state verification (`opened -c` records must carry current user/client).

Unowned or non-pending target changelists raise `SourceControlValidationError` before any mutation; the shared MCP error mapper returns `invalid-arguments`.

## 8. Audit receipt structure

Every mutation produces a bounded, deterministic, restart-readable receipt. Receipts are written atomically under `<audit_report_root>/source-control/sc_*.json` when an audit root is configured (the MCP server passes `--work-root`); otherwise they are returned in-memory with `persisted=false`. A write failure is surfaced as `persisted=false` and never turns a real mutation into a fake failure.

```text
schemaVersion, receiptId, operation            create-changelist | update-description | reopen | resolve-text
occurredAtUtc, providerUser, providerClient
changelistId, changeSetId (optional, validated)
exactFiles[], preState[], postState[]          bounded per-file evidence (P4 action/change/CL/revs/override/readiness)
actionReceipts[]                               per-file/per-step receipts with ok + code + message
manualFinalAction                              always "none" from the product layer
submitCapability=false, revertCapability=false, deleteCapability=false
```

A `changeSetId`, when supplied, is validated through the exact UEAgentKit Change Set id validator before the receipt is written; the C3 receipt provides the reverse lookup without changing the Change Set schema.

## 9. Resolve preview

`resolve_status` is read-only and runs `p4 resolve -n` over the exact mapped files (deliberately **without** `-o`: `resolve -o` prints base/yours/theirs content, which C3 does not parse and would risk the bounded-output limit; `-o` remains supported by the runner shape but is not used by automatic flows). Per-file normalized state:

```text
needsResolve, resolveKind (content|filename|filetype|branch|delete|unknown)
baseRevision, fileType, changelistId
mergeableText, binaryPackage, resolveStateUnknown
submitReady, warnings[]
```

Provider errors, malformed records, or a nonzero preview exit fail closed to `resolveStateUnknown`; the product never guesses that a file is resolved.

## 10. Text resolve eligibility

Automatic resolve is intentionally narrow. A file is eligible only when **all** hold:

```text
opened for edit by current user/client (no local override)
needsResolve == true
extension in { .cpp .h .ini .json .csv .py }      (frozen first pass)
P4 type is text-like (text/unicode/utf16), not binary
not a Unreal binary package (.uasset/.umap)
```

`.txt`/`.md` were **not** added (the plan permits them only if tests freeze the behavior; they were kept out of scope). The only merge primitive executed is `p4 resolve -am` on one exact file. Pre/post SHA-256 is captured streamed; a re-query (`resolve -n`) must prove the file no longer needs resolve before a receipt may claim success.

## 11. Conflict handling

If `-am` leaves a file unresolved (content conflict or a server that reports a merge but leaves the file unresolved), the product:

- leaves the file unresolved;
- never forces acceptance (`-af`/`-at`/`-ay`/`-f`/`-t` are unreachable);
- emits a `resolve-conflict-remains` receipt with `ok=false`, a strong warning, and `submitReady=false`;
- records pre/post SHA-256 evidence;
- writes the audit receipt with `ok=false` state.

A blocked or partial outcome is always reported as such.

## 12. Unreal binary packages (.uasset / .umap)

C3 is awareness + handoff only for Unreal binary packages:

- `resolve_status` reports `binaryPackage=true`, `binaryReconciliationRequired` semantics, a `strong-warning` `binary-package-resolve-required`, and `submitReady=false`.
- `resolve_text` refuses them before any resolve command runs; no `resolve -at/-ay/-af/-t`, no blind acceptance, no overwrite of either side, and no claim of UE-level reconciliation. The file remains unresolved for a future dedicated binary replay workflow.

C3 therefore stays **U0**; no UE/UBT was started.

## 13. Submit / revert / delete absence proof

- The capability contract reports `submitCapability=false`, `revertCapability=false`, `deleteCapability=false`, `arbitraryCommandExecution=false`, `shellPassthrough=false` in `ue_get_capabilities`.
- Every mutation receipt records the same three `false` flags and `manualFinalAction="none"`.
- No MCP tool, no CLI action, and no service method can execute submit/revert/delete. Runner unit tests assert every prohibited command and flag raises `SourceControlProhibitedOperationError`; the fake fixture rejects unknown/generic commands.
- The module still contains no `shell=True`, no `os.system`, no `subprocess.call/Popen` (existing module-scan test continues to pass).
- C1/C2 regression boundaries (same-user/different-client is not current checkout; submitReady requires current client + have + at head + no other lock; safe-sync failure never falls through to edit; override forces `submitReady=false`; malformed `-G` output fails closed; P4 state remains advisory) are preserved and covered by the extended unit matrix.

## 14. Fake-fixture mutation evidence

Deterministic fake `p4 -G` fixture (no real P4 dependency) drives the mutation matrices:

- changelist list only returns the current client's pending changes and honors ownership flags;
- `change -i` create applies a typed spec and returns the created number; description-only update preserves owner/client/status/files;
- reopen moves an exact already-opened current-client file into the target CL and removes it from the previous CL; post-state is verified via `opened -c`;
- unowned / non-pending destination CLs are rejected;
- partial reopen returns truthful per-file receipts (`not-mapped`, `not-open-current-client`, `override-only`, `post-verify-failed`);
- `resolve -n` preview drives needsResolve/kind; clean `-am` resolves; content conflict and server-leaves-unresolved both stay unresolved with strong warnings; binary and non-whitelisted files are never merged;
- audit receipts persist to `<audit_root>/source-control/sc_*.json` and are read back with all contract fields.

## 15. Real P4 read-only smoke (PASS) and real C3 mutation status

```text
real read-only smoke    PASS
  provider facts        2025.1/2810567, client/user as above
  status on A25 fixture mapped have=1/head=1 opened current submitReady=true
  changelists list       ok / readOnly / pendingCount=0 (no pending CLs on this client)
  resolve preview        needsResolve=false (fixture unchanged; nothing to resolve)

real C3 mutation         NONE — owner-fixture BLOCKED (A27)
```

No real `change -i`, `reopen`, or `resolve -am` was executed. The previously ratified A25 fixture was not reverted, not moved to another changelist, and its content remains unchanged (`localModified=false` / diff-clean). Deterministic fake-fixture coverage stands in for real mutation acceptance until the owner designates a safe C3 fixture.

## 16. Test counts / elapsed time

| Gate | Command | Result |
|---|---|---|
| G1 source-control | `py -3.12 scripts\RunPythonTests.py domain source-control` | **94 / 94 PASS / 12.394 s** |
| G2 full | `py -3.12 scripts\RunPythonTests.py full` | **1062 / 1062 PASS / 121.295 s (17 skipped)** |
| Ruff | `.venv\Scripts\ruff.exe check src tests\python scripts` | **PASS** |
| compileall | `py -3.12 -m compileall -q src tests\python scripts` | **PASS** |
| ValidateRelease | `py -3.12 scripts\ValidateRelease.py --expected-version 0.7.0 --skip-tests --skip-ruff` | **PASSED 0.7.0 (schemas 3, patch examples 16)** |
| git diff --check | — | **PASS** |
| UE / UBT | — | **0 (U0)** |

Unit/mutation module (`test_source_control.py`): 78 tests. MCP contract module (`test_mcp_source_control_tools.py`): 16 tests. Prior C1/C2 behavior remains covered inside those modules.

## 17. Acceptance contract status

```text
A1   C1/C2 contracts backward-compatible                       PASS
A2   required dependencies remain []                            PASS
A3   source-control remains opt-in                              PASS
A4   pending CL list/read bounded and structured                PASS
A5   change -i typed internal form only                         PASS
A6   create limited to current user/client pending CL           PASS
A7   description update cannot change owner/client/status/files PASS
A8   change -d / -f / -u / -U unreachable                       PASS
A9   reopen moves only exact already-opened current-client files PASS
A10  reopen cannot change file type                             PASS
A11  partial reopen truthful per-file receipts                  PASS
A12  audit receipt bounded and restart-readable                 PASS
A13  optional Change Set linkage validates exact changeSetId    PASS
A14  resolve status exact preview, fails closed on uncertainty  PASS
A15  binary .uasset/.umap never enter automatic text resolve    PASS
A16  resolve -am only on exact whitelisted text files           PASS
A17  resolve conflict remains unresolved with strong warning     PASS
A18  resolve -af/-at/-ay/-f/-t unreachable                     PASS
A19  post-resolve re-query proves resolved before success        PASS
A20  receipt includes pre/post SHA-256 + P4 state               PASS
A21  submit/revert/delete capability flags remain false          PASS
A22  no CLI/MCP/private runner bypass for submit/revert/delete   PASS
A23  no P4-only state hard-blocks local Writer testing          PASS (unchanged boundary)
A24  source-control G1 passes once at final source state         PASS
A25  full G2 / Ruff / compileall / ValidateRelease / diff-check  PASS
A26  UE/UBT runs = 0                                            PASS
A27  real CL/reopen/resolve mutation on owner-authorized fixture OWNER-FIXTURE BLOCKED (no fixture designated; deterministic fake coverage passes)
```

## 18. Honest blockers / deferred items

- **Real C3 mutation acceptance is BLOCKED** until the owner designates a safe real fixture (the frozen instructions forbid reusing the A25 fixture or treating its earlier ratification as new C3 authorization).
- `resolve -o` is supported by the runner shape but intentionally not used by automatic preview/merge flows to keep subprocess output bounded; this is a documented behavior choice within the frozen plan.
- `.txt`/`.md` were not added to the text-resolve extension set.
- The owner-designated audit root (`Output/<work-root>/source-control/`) is wired through the MCP `--enable-source-control` path; direct service callers get in-memory receipts unless they pass `audit_report_root`.
- The owner subsequently authorized the validated C3 checkpoint. The implementation was committed locally as `09a4c55c59e6388d729b745a4479ca5d7f64801c` (`feat:close-C3-changelist-resolve-audit`). No push occurred. A27 remains independently blocked until a real C3 mutation fixture is explicitly designated.


## 19. Owner corrective review — 2026-09-06

The first C3 implementation passed its fake-fixture gates but owner review found real-provider and truthfulness gaps that the original fake runner did not expose. The corrective pass was completed in the same uncommitted working tree; no real C3 mutation, Git commit, push, UE, or UBT run occurred.

Corrections closed:

```text
1. real change -i subprocess path
   removed the invalid simultaneous stdin=PIPE + input= usage; a mocked real-subprocess-shaped test now freezes this call contract.

2. real p4 -G changelist form schema
   Files0/Files1/... are parsed and marshaled as indexed fields; description update round-trips the complete structured change -o form except Description/code, then verifies exact file/user/client/status preservation. fileCount is the truthful total even when the returned list is capped at 100.

3. explicit changelist resolve scope
   resolve_text(changelist_id=...) now rejects files outside that exact CL with changelist-scope-mismatch and executes no merge; eligible scoped mutations always use resolve -am -c <id> <exactFile>.

4. post-mutation verification audit
   once change -i returns without a P4 rejection, later inability to identify/re-read/verify the mutation returns ok=false with mutationMayHaveOccurred=true and writes a durable audit receipt when an audit root is configured.

5. tagged P4 errors fail closed
   change -o / changes permission/provider errors are no longer reinterpreted as not-found or empty lists. Only a clearly identified nonexistent changelist is notFound.

6. binary classification
   binaryPackage is now reserved for .uasset/.umap. Generic binary/non-text files use genericBinary=true and binary-file-resolve-not-supported; they do not claim UE binary reconciliation semantics.

7. manual final-action handoff
   prepare_changelist accepts validated metadata none|submit|revert|delete. It is recorded in result/audit/warnings only; submitCapability/revertCapability/deleteCapability remain false and no executable final action was added.

8. real P4 no-resolve tagged response
   the real server returns generic=17/severity=2 `no file(s) to resolve` as a tagged error record for a clean exact-file preview. This exact benign record is now recognized as needsResolve=false, while other tagged resolve errors remain resolveStateUnknown/fail-closed.
```

Final focused / affected-domain evidence after the corrective source state:

```text
focused source-control       78 / 78 PASS / 6.200 s
focused MCP                  16 / 16 PASS / 2.704 s
source-control G1            94 / 94 PASS / 12.394 s
```

Real P4 read-only smoke after the corrective pass:

```text
provider                     P4D/NTX64/2025.1/2810567
client/user                  <OWNER_P4_CLIENT> / <OWNER_P4_USER>
pending changelists          0 / read-only query PASS
A25 resolve preview          needsResolve=false
                             resolveStateUnknown=false
                             submitReady=true
real C3 mutations            0
```

Final full validation:

```text
full G2                    1062 / 1062 PASS / 121.295 s / 17 skipped
Ruff                         PASS
compileall                   PASS
ValidateRelease 0.7.0        PASS
git diff --check             PASS
UE / UBT                     0 / U0
```

The first full-suite invocation through WebAgents lost its completion response after the transport/worker connection timed out; process inspection confirmed it had exited before any retry. Its result is not counted as evidence. A non-overlapping WorkspaceBridge background retry is the recorded G2 result above.

A27 remains **OWNER-FIXTURE BLOCKED**. The corrective pass executed only real read-only P4 queries and did not reuse, move, resolve, revert, or otherwise mutate the A25 fixture.

Current publication state: **C3 COMPLETE / owner corrective review PASS / public checkpoint `5b705a7` / A27 truthfully OWNER-FIXTURE BLOCKED / push none**.
