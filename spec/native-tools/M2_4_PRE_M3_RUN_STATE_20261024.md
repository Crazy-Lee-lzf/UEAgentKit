# M2.4 Pre-M3 Gate Closure — Run State (2026-10-24)

- Status: **A PASS; B PASS on valid fixture; R1.3 warning not reproduced; M3 not automatically unblocked**
- Branch: `feature/layout-broker-m1`, base `fc5654e`
- Worktree: `E:\WorkSpace\UEAgentKit-WT\layout-broker-m1`
- Engine: UE 5.6.1 (`E:\EPICGAME\UE_5.6`)
- Hosts: `Build/m2-native/M2NativeHost`, `Build/m2-native/M24/P11AutomationHost`
- No push/merge/reset/clean/delete; no Computer Use; serial Editor runs

## 1. Session/model

Same DSH session `session-7fd8360d-3ac6-47f5-898e-4d52afd3e59d`; runtime declares
`cline-pass/deepseek-v4.1-flash-1017`, non-Fast/max reasoning, no model switch. No independent model-id check.

## 2. Build

| Command | Result |
|---|---|
| `pwsh -File scripts\BuildM2NativeHost.ps1` after adding `M2FingerprintCapture` | **Succeeded** (~11 s incremental) |

## 3. P11 Reload two-process sequence

Fresh `Build/m2-native/M24/P11AutomationHost` with `P11TestOnlyBuild.json`, plugin junction, and
`Build/m2-native/tools` junction for the worker path. No P11 source changed.

| Process/order | Test/command | Exit | State |
|---|---|---|---|
| 1 | `P11FixtureContract` | 0 | Success, 0 errors |
| — | `p11_build_scope_probe.py --evidence Build\m2-native\M24\P11AutomationHost\Evidence` | 0 | PASS |
| 1 | `P11MenuContract` | 0 | Success, 0 errors |
| 1 | `P11ScopeContract` (apply+save, writes scope result) | 0 | Success, 0 errors |
| 2 (independent Editor) | `P11ReloadContract` | 0 | Success, 0 errors, all reload booleans true |
| — | `P10FixtureContract` (final DLL) | 0 | Success |

Revisions:

- selection before `sha256:9ad8d485...` → after/save `sha256:fdefc479...` → reload same;
- incremental before `sha256:4bbcb088...` → after/save `sha256:29e44db4...` → reload same;
- reload `selectionPositions`, `incrementalPositions`, both topology flags, and `dllVerified` all true;
- both packages clean; reload receipt written.

An earlier reload attempt failed `dllVerified=false` because its scope result came from a previous DLL build;
that run is retained as a negative/intermediate observation. After the current build, a clean P11 scope+reload
sequence passed. No test was skipped or counted as pass.

Evidence: `Build/m2-native/Evidence/M24/p11-reload-pair.json`; raw reports `M24/P11Fixture/`,
`P11MenuContract/`, `P11ScopeContract/`, `P11Reload/`; P11 host `Evidence/p11-scope-result.json` and
`p11-reload-result.json`.

## 4. Fingerprint pair

| Command | Result |
|---|---|
| `UnrealEditor-Cmd ... -ExecCmds=Automation RunTests UEAgentKit.Native.M2FingerprintCapture;Quit` (run 1) | exit 0, capture written |
| same, run 2, independent process | exit 0, capture written |

Paired comparison: package revision `sha256:d5d491...`, package file SHA256 same, graph fingerprint
`sha1:7df80463...`, nodeCount 4, preflight/live/context node arrays all hash-equal, dirty false.
Machine-readable: `M24/Fingerprint/fingerprints-paired.json`, `capture-1.json`, `capture-2.json`; raw logs
`ue-editor-run1.log`, `ue-editor-run2.log`.

R1.3 missing-CallFunction-NodeGuid warning was not reproduced on this valid fixture. `BuildPreflight` rejects
invalid NodeGuid, so no fingerprint contract change was made; the exact protected R1 fixture cause remains
unreproduced.

## 5. Python regression

| Command | Result |
|---|---|
| `py -3.12 -m unittest discover -s tests\python -p "test_layout_broker_*.py"` | **140 passed** |
| `py -3.12 scripts\RunPythonTests.py full --quiet` | **1381 tests, OK (skipped=17), 118.6 s** |

No Python source changed in M2.4.

## 6. Evidence and hashes

`Build/m2-native/Evidence/M24/` (git-ignored): `p11-reload-pair.json`, `fingerprints-paired.json`,
`Fingerprint/`, `P11Fixture/`, `P11MenuContract/`, `P11ScopeContract/`, `P11Reload/`, `P10Fixture/`,
`p11-scope-probe.log`, `build.log`, `SHA256SUMS.txt`.

## 7. Gate statement / next

- A: PASS.
- B: PASS on valid M2 fixture; R1.3 test-fixture warning unresolved/unreproduced.
- M3 remains gated: separate scoped M3 approval is required. No M3 menu/fallback or R2 Apply was started.
