# DEF-07 — fixture concurrency guard (2026-11-03)

Scope: make `scripts/RunWriteFixturePlan.ps1` fail closed when the target
`.uproject` is already open in an Unreal Editor. This is a real code change plus
offline tests; **no Unreal Editor, `UnrealEditor-Cmd`, UBT or fixture write was
executed**, and N1, `main`, the Host project, P4 and GitHub were not touched.

| Item | Value |
| --- | --- |
| Branch | `feature/p1-node-move-defect-fix-20261030` |
| Base | `7004dcc` (clean) |
| Changed | `scripts/RunWriteFixturePlan.ps1`, new `scripts/FixtureConcurrencyGuard.ps1` |
| Tests | new `tests/powershell/FixtureConcurrencyGuard.Tests.ps1`, new `tests/python/test_fixture_concurrency_guard.py` |

## What DEF-07 actually is

Earlier wording described the script as force-closing the Editor. That is not
what it does, and N1's read-only audit confirms it: the pre-fix script contained
no process termination at all (no process-kill API, no `CloseMainWindow`, no
machine shutdown). The `-Force` switches were only `Get-ChildItem`,
`New-Item -Force` and the bounded recursive delete of its own verification
output.

The real risk is concurrency:

- `RunWriteFixturePlan.ps1` launches `UnrealEditor-Cmd.exe -unattended
  -run=WriteFixturePlan` against `$ProjectPath` without checking whether an
  interactive `UnrealEditor.exe` already holds that exact project.
- Two processes opening the same project and writing the same `.uasset` files
  is unsafe; `-unattended` never prompts.
- The script also launches an independent re-export commandlet, a second
  project-open that has the same problem.

So DEF-07 is a **missing exclusivity check**, not a missing kill. The fix adds
the check and keeps the no-termination property.

## What changed

### 1. New read-only guard module

`scripts/FixtureConcurrencyGuard.ps1` answers one question: does any
`UnrealEditor*.exe` process hold this exact `.uproject`? It only reads state.

- Process data comes from a local `Get-CimInstance Win32_Process` query
  (`ProcessId`, `Name`, `CommandLine`, `CreationDate`). No remote/WMI endpoint,
  no injected filter, no dependency on `EditorBridge.json`, and no descriptor is
  read, rewritten or deleted.
- Command lines are tokenised with a quote-aware splitter. Project arguments are
  taken only from `X.uproject`, `-project=<path>` and `-project <path>` tokens.
  A raw substring test is never used, so `MyProject.uproject` cannot match
  `MyProject2.uproject`, and `...\A\Target.uproject` cannot match
  `...\B\Target.uproject`.
- Paths are normalised with `GetFullPath`, separator folding, trailing-separator
  trimming, and case-insensitive comparison. Every component is walked to follow
  junctions/symlinks, so a link and its real target compare equal in both
  directions.

### 2. Three gates, fail-closed

| Stage | Location | Purpose |
| --- | --- | --- |
| `entry` | immediately after `$ProjectPath` resolves | refuses before any directory is created, plan validated or report written |
| `write` | immediately before `& $EditorCmd @Arguments` | authoritative check as late as possible before the commandlet opens the project |
| `export` | immediately before the recursive delete of `VerificationOutput` | the re-export opens the project again, so exclusivity is re-proved before any cleanup |

A blocked gate throws, so no commandlet starts, no asset is written and the
verification directory is not cleared.

Stable codes:

- `fixture-project-in-use` — an Editor process demonstrably holds this project;
- `fixture-project-lock-unverifiable` — the state cannot be proven;
- `fixture-project-lock-clear` — no Editor holds this project (allow).

Fail-closed matrix:

| Observed state | Decision |
| --- | --- |
| No `UnrealEditor*` process | allow |
| Editor holds the same project (any case, quotes, `/` vs `\`, link alias) | block `-in-use` |
| Editor holds a different absolute project | allow |
| Editor command line unreadable / empty | block `-unverifiable` |
| Editor with no project argument (for example the project browser) | block `-unverifiable` |
| Relative project path in the command line (working directory is not exposed) | block `-unverifiable` |
| 8.3 short name on either side | block `-unverifiable` |
| One side UNC, the other a drive letter (mapped/DFS alias possible) | block `-unverifiable` |
| `Win32_Process` enumeration fails | block `-unverifiable` |
| Target path missing/relative | block `-unverifiable` |

The false-positive cost is deliberate: an Editor whose ownership cannot be proven
blocks fixture writes for *every* project until it is closed. That is the
requested trade (safe refusal over an unattended write under an open Editor).

### 3. Non-destructive default Mode

`-Mode` now defaults to `Create`, which refuses existing targets and cannot
delete them. `Reset` stays available through `[ValidateSet]` and must be asked
for explicitly. All 30 in-repository call sites audited under `scripts/` pass
`-Mode` explicitly, so their behaviour is unchanged.

### 4. Audit snapshot

Each blocking/allowing decision at the `write` and `export` stages is written to
`concurrency-guard-write.json` / `concurrency-guard-export.json` next to the
report (schema `ueak.fixture-concurrency-guard/1.0`): target project, decision,
code, per-process classification and a 1024-character command-line excerpt.
Report writing is best-effort and can never turn a block into an allow.

### 5. No bypass switch

There is no runtime flag that skips the guard. The only injection seam is the
`-ProcessSnapshot` **function parameter** used by the offline harness; the
production wrapper never supplies it, and the static test asserts that.

## Deliberately not done

- **No process termination, ever.** The script still cannot close a user's
  Editor; blocking is the whole remedy.
- **No project lock file.** A real mutex/lock would need cooperation from the
  commandlet and the Editor; adding one only on the script side would be
  decorative. Recorded as a possible follow-up.
- **`-ConfirmDelete` for the verification delete** (N1's third suggestion) was
  not adopted: it would break every existing caller and is outside this
  defect's concurrency scope.

## Residual risk (honest limits)

- **TOCTOU window.** The `write` gate runs immediately before the launch, but a
  human can still start an Editor in the remaining milliseconds. Only a
  project-level lock shared with the commandlet could remove that window.
- **Drive mapping / `subst`.** Mixed UNC and drive-letter paths are treated as
  unverifiable and block. Two different drive letters that map to the same share
  are not detected as identical; that would require resolving every user's
  mappings, which is itself ambiguous.
- **Non-Editor writers.** An external script writing the same assets is out of
  scope; the guard only looks at Unreal Editor processes.
- **Other entry points.** Only `RunWriteFixturePlan.ps1` is gated. Calling
  `RunAssetCatalog.ps1` or `UnrealEditor-Cmd.exe` directly, or a future
  commandlet launcher, does not get this check. Wiring the guard into every
  Unreal launcher is a separate follow-up and would change scripts outside this
  defect's scope.
- The guard changes no fixture semantics, no plan schema and no commandlet code.

## Test evidence

Run without starting Unreal:

- `tests/powershell/FixtureConcurrencyGuard.Tests.ps1`: **35/35 assertions pass**
  under both `pwsh` 7 and Windows PowerShell 5.1 (the `.cmd` entry uses 5.1).
  Cases cover: no editor, same project, different case, `/` separators,
  `-project=` and `-project <path>` forms, quoted paths with spaces, same file
  name in another directory, name-prefix non-match, non-Editor process, mixed
  editors, unreadable command line, project-less editor, relative path, missing
  target, symlinked project (both directions), UNC normalisation, UNC↔drive
  ambiguity, and the audit report contents.
- `tests/python/test_fixture_concurrency_guard.py`: **11 tests pass** — static
  ordering/no-termination/fail-closed/no-bypass contract, plus a functional run
  of the PowerShell harness and PowerShell parser checks on both scripts.
- Old-code discrimination, verified against `git show HEAD:` without touching
  the worktree: **5/5** new assertions fail on the pre-fix wrapper (no entry
  gate, no write gate, no export gate, no guard module, destructive default).
- Full `py -3.12 -B -m unittest discover -s tests/python`: **1962 passed,
  20 skipped** (previous baseline 1951/20; the 11 added tests are new).
- `git diff --check` clean.

## Native verification status

**Not performed and not claimed.** The guard itself is ordinary OS-level
PowerShell and is exercised offline, but the end-to-end command was not run
because this round must not start Unreal. A later run should, with a disposable
project: (a) open the project in an interactive Editor and confirm the script
blocks before creating anything and writes `fixture-project-in-use`; (b) close
it and confirm `fixture-project-lock-clear` plus a normal fixture run; (c) open
the project *during* a run's write phase and confirm the `export` gate blocks
before the verification directory is cleared. None of that is proven here.
