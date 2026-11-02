# P1 fix record — N1 native blockers (2026-11-02)

Scope: repair the two product defects that N1 found by running the P1 plugin in
a real UE 5.6.1 Editor. This round changed production C++ plus tests only; **no
UE build, Editor, commandlet or Computer Use session was started**, and N1, the
Host project, `main` and every other worktree were left untouched.

| Item | Value |
| --- | --- |
| Branch | `feature/p1-node-move-defect-fix-20261030` |
| Base | `c53b95997da465c6d37fbbe025803478797e163b` (clean at start) |
| BUG1 fix | `16f931971134009414323dcf66d5f97db7959301` |
| BUG2 fix | `7cd0820048a505cad1b602a60ae71114113770f2` |
| Changed code | `BlueprintEditSession.cpp`, `EditorBridgeWriteHandlers.cpp` |
| Changed tests | `test_blueprint_edit_session_mcp.py`, `test_live_fast_verify_contract.py` |

Evidence source (read-only N1 worktree
`n1-blueprint-native-acceptance-20261031` @
`888620400bcd47b5d2e8619800490fe093076a0b`):

- `Output/n1-acceptance/defects.json` — `N1-DEF-01`, `N1-DEF-02`, both marked
  `blocker`;
- `docs/N1_NATIVE_BEHAVIOR_EXECUTION_20261102.md` — the native run report;
- Editor PID `19432`, Editor session `9fc368a2-4a85-8df7-1124-75aee11c99f6`,
  plugin DLL SHA-256
  `FA9F2EBB60BF049EB31FEDB360A536C1C10A0FB0FEFC01E5002EFAC34C725C1F`, compiled
  from P1 `c53b959`.

Both defects below were **reproduced natively by N1**. The fixes in this round
are proven by Python static/mock regression only; **the native re-verification
has not been executed** and is gated in the last section.

---

## BUG1 / N1-DEF-01 — token generated uppercase, every consumer requires lowercase

### Reproduction (N1, native)

`editor.beginBlueprintEditSession` returned a 64-character token; passing that
exact token to `editor.moveBlueprintNodes` with an otherwise valid payload
failed with `node-move-batch-invalid-parameters` /
`editSessionToken must be the exact 64-character lowercase hex token returned by
editor.beginBlueprintEditSession.` The identical node payload without a token
succeeded (`changed=true`), so the token alone was rejected.

Root cause: `FBlueprintEditSessionManager::Begin` built the token from
`FGuid::NewGuid().ToString(EGuidFormats::Digits)`, which formats through `%08X`
and is therefore uppercase. `IsCanonicalEditSessionToken` accepts only
`[0-9a-f]`, and `End()` compares with `ESearchCase::CaseSensitive`, so
lowercasing at the caller could never work either. The documented contract — a
64-character lowercase token returned by `begin` — was unsatisfiable.

### Fix (choice and non-choice)

`Begin` now generates both halves with `EGuidFormats::DigitsLower`
(`%08x%08x%08x%08x`, verified in
`E:\EPICGAME\UE_5.6\Engine\Source\Runtime\Core\Private\Misc\Guid.cpp`), giving
exactly 64 lowercase hex characters, and asserts the generated value is
canonical.

Deliberately **not** done: the validator was not relaxed to accept uppercase and
`End` was not made case-insensitive. That would have hidden a protocol
inconsistency instead of fixing it. Unchanged by design:

- randomness / non-reuse: two independent `FGuid::NewGuid()` calls;
- session binding: Bridge session id, process id, asset, graph, fingerprint and
  positions still captured and re-proved exactly as before;
- Dirty protection: the `blueprint-edit-session-package-dirty` gate at `begin`
  and the per-batch Dirty proof are untouched;
- strictness: uppercase, truncated, non-hex, empty and foreign tokens still fail
  closed with `blueprint-edit-session-token-invalid`.

The full chain is consistent after the fix — `Begin` stores the token,
`FEditSessionBeginInfo::Token` echoes the same string,
`EditorBridgeBlueprintEditSession.cpp` emits it as `editSessionToken` verbatim,
the move handler and the `end` parser both require canonical lowercase, and
`mcp_workflow_tools` requires `[0-9a-f]{64}` via `fullmatch`.

Out of scope, intentionally: `EditorBridge.cpp` still builds its **auth** token
with `EGuidFormats::Digits`. That value is opaque — it is only compared to the
copy stored in the descriptor (`RequestToken != AuthToken`), never validated by
a case-sensitive regex — so it is self-consistent and not the same defect.

### Tests

`tests/python/test_blueprint_edit_session_mcp.py`:

- `test_token_generation_is_strict_lowercase_hex` — the token assignment uses
  `DigitsLower`, contains no standalone `EGuidFormats::Digits`, and keeps two
  `FGuid::NewGuid()` calls;
- `test_token_consumers_stay_strict_lowercase_and_case_sensitive` — the
  validator still accepts only `0-9a-f` and both match/`End` comparisons remain
  `ESearchCase::CaseSensitive`;
- `test_python_wrapper_token_regex_stays_anchored_lowercase` — the wrapper keeps
  `[0-9a-f]{64}` and still uses `fullmatch` (not `match`/`search`);
- `TokenProtocolRoundTripTests` — `begin` → `move` → `end` forwards the returned
  token verbatim; uppercase, truncated, 65-char and non-hex variants never reach
  the bridge; a well-shaped foreign token is forwarded and the Editor's
  fail-closed code is surfaced unchanged.

---

## BUG2 / N1-DEF-02 — successful retained fast verify reported `operation-unsupported`

### Reproduction (N1, native)

`editor.openAsset` on the isolated `MI_N1FastVerify`, then
`editor.applyAssetPropertyLive` (scalar `0.25 → 0.375`) returned
`changed=true` and a `transactionId`; the immediately following
`editor.verifyAssetPropertyLiveFast` with that exact id failed with
`live-fast-verify-operation-unsupported`.

Root cause: the handler used one negated compound condition:

```cpp
else if (!TransactionId.IsEmpty()
    && !ReadRetainedLiveWriteFastValue(...)) { return false; }
else { /* live-fast-verify-operation-unsupported */ }
```

The retained read returns `true` on success, so on success the `else if` is
`false` and control fell into the final `else`. The observed code was none of
the helper's failure codes (`transaction-not-found`, `target-changed`,
`target-not-found`, `read-failed`), which is exactly what proves the read
succeeded and was then misreported.

### Fix

The branch is now split. Presence of a `transactionId` selects the retained
path; inside it a failed read returns `false` with the helper's own precise
code, and a successful read falls through to the shared value comparison,
`Result->SetField("value", ...)`, `OutResult = Result; return true;`:

```cpp
else if (!TransactionId.IsEmpty())
{
    if (!ReadRetainedLiveWriteFastValue(...)) { return false; }
}
else
{
    OutErrorCode = TEXT("live-fast-verify-operation-unsupported");
    return false;
}
```

Behaviour kept:

- the four stateless operations (`setAssetProperty`, `setVariableDefault`,
  `setComponentProperty`, `setPinDefault`) keep their own earlier branches;
- the `unsupported` else is still reached only when there is **no**
  `transactionId`, so no new unsafe stateless Material/DataTable flow exists;
- fail-closed codes are unchanged and still owned by
  `EditorBridgeRetainedLiveWrite.cpp`: missing/foreign/expired record →
  `live-fast-verify-transaction-not-found`; remapped target →
  `live-fast-verify-target-changed` / `-target-not-found`; read error →
  `live-fast-verify-read-failed`;
- the helper keeps the `const TSharedRef<FJsonObject>& OutResult` signature from
  `87f6d6d` — not reverted.

Note: `test_tool_registry` bounds
`EditorBridgeWriteHandlers.cpp` at 24 700 characters. The explanatory comment
was kept to one line so the file stays at 24 637; the full rationale lives here.

### Tests

`tests/python/test_live_fast_verify_contract.py`:

- `test_retained_success_does_not_fall_through_to_unsupported` — the guard is
  exactly `else if (!TransactionId.IsEmpty())`, the retained read is nested in
  the body, and the failure path does not overwrite the helper's code;
- `test_supported_operations_and_stateless_unsupported_are_unchanged` — the four
  stateless operations remain and the `unsupported` code appears exactly once,
  after the transaction branch;
- `test_retained_failure_codes_stay_fail_closed_in_the_helper` — all four
  fail-closed codes remain, and the shared tail still performs the value
  comparison and returns `true`.

---

## Regression evidence

Python 3.12, `unittest`, no Editor or Unreal process started:

- targeted modules: `test_blueprint_edit_session_mcp` 23,
  `test_live_fast_verify_contract` 7, `test_tool_registry` 4 — all pass;
- full `py -3.12 -B -m unittest discover -s tests/python`: **1951 passed,
  20 skipped** (previous baseline 1943 passed, 20 skipped; the 8 added tests are
  this round's);
- `git diff --check` clean at both fix commits;
- bounded AST audit: 267 Python files under `src/` and `tests/python/` parsed,
  0 syntax errors.

Old-code / new-code discrimination was proven without touching the worktree: the
four structural predicates were re-evaluated against the `git show HEAD:` blobs
and **all four fail on the pre-fix source** and pass on the fix
(lowercase generator, no uppercase `Digits` in the assignment, standalone
transaction guard, nested retained read).

What this does **not** prove: UE C++ compilation, the native value read-back, or
`verified=true` from a real Material/DataTable. Only the next N1 run can.

---

## Native re-verification gate (N1's next round, not executed here)

P1 did not start a build. N1 must, in its own worktree and Host:

1. Merge P1 `16f9319` and `7cd0820` into N1 `8886204` and run one incremental
   compile, together with N1's already-committed-but-not-recompiled
   `N1-TOOL-01` (DataTable fixture script name), `N1-TOOL-02` (close the
   edit-session probe in `finally`, record token shape) and `N1-TOOL-03`
   (`-ExecCmds` split on `;`).
2. Start a **new** Editor PID and clear the fixture's saved-Dirty /
   already-begun session state; do not continue from dirty memory or a stale
   descriptor.
3. Run and record:
   - **R2** — 10 consecutive token batches on the 259-node K2 graph;
   - **R3** — large-graph rollback (`end(mode=rollback)`) including the >128-node
     path;
   - **R4** — finalizer fault seam (`UEAgentKit.BlueprintEditSession.FailNextFinalize`)
     with its `test-seam:finalizer-fault` premise;
   - **R6** — `applyAssetPropertyLive` → `verifyAssetPropertyLiveFast` on the MI
     scalar and the DataTable row, asserting `verified=true` and the retained
     negative cases still fail closed.
4. Only if all evidence matches may the blocked N1 criteria be flipped to pass.

Two DSH sessions must not edit the same hot files at the same time; N1 runs after
this P1 round is closed.
