# M2.3 Runtime Acceptance — Run State (2026-10-24)

- Status: **Gates 1–4 PASS; P11 reload skipped; no M3**
- Branch: `feature/layout-broker-m1`, base `ac22145`
- Host: `Build/m2-native/M2NativeHost` + `Build/m2-native/P11AutomationHost`
- Engine: UE 5.6.1 (`E:\EPICGAME\UE_5.6`)
- No push/merge/reset/clean/delete; only this worktree and its Build hosts

## 1. Session/model

Same DSH session `session-7fd8360d-3ac6-47f5-898e-4d52afd3e59d`; runtime declares
`cline-pass/deepseek-v4.1-flash-1017`, non-Fast/max reasoning, no model switch. No independent model-id check.

## 2. Gate 1 — timeout / NullRHI exit 3

| Command | Result |
|---|---|
| `py -3.12 tools\run_m2_native_negative.py timeout` (normal renderer, no `-nullrhi`) | **PASS**, editor exit **0**, automation success, `native-preview-timeout`, no plan |

The pre-fix NullRHI run had the correct outcome but exited 3 with `GetRestoredDimensions`; it is not counted as
pass. The fixed run uses `UnrealEditor-Cmd -unattended -nop4 -nosplash` without NullRHI for this one scenario.
Evidence: `m2-native-negative-timeout.json`.

## 3. Gate 2 — real revision / fingerprint

| Command | Result |
|---|---|
| `py -3.12 tools\run_m2_native_negative.py revision` | **PASS**, Editor exit 0, `native-preview-revision-conflict`, no plan, undo 0→0, receipt saved=false |
| `py -3.12 tools\run_m2_native_negative.py fingerprint` | **PASS**, Editor exit 0, `native-preview-fingerprint-conflict`, no plan, undo 0→0, receipt saved=false |

The C++ stale test waits for the external broker response, mutates the real fixture, then calls `Tick`. Evidence:
`m2-native-negative-revision.json`, `m2-native-negative-fingerprint.json`, `native-stale-result.json`.

## 4. Gate 3 — >1 MiB request

Included in `UEAgentKit.Native.M2LayoutBrokerPositive`: padded 1.1 MB context produces local
`native-preview-request-too-large`, no plan, no socket request. The final positive automation run is **Success**,
editor exit **0**.

## 5. Gate 4 — P11 valid host

Created `Build/m2-native/P11AutomationHost` with project name `P11AutomationHost` and
`P11TestOnlyBuild.json`; `Build/tools` junction resolves the existing worker path. No P11 source changed.

| Command | Result |
|---|---|
| `P11FixtureContract` | **PASS** (exit 0, state Success, 0 errors) |
| `p11_build_scope_probe.py --evidence Build\m2-native\P11AutomationHost\Evidence` | **PASS** |
| `P11MenuContract` | **PASS** (exit 0, state Success, 0 errors) |
| `P11ScopeContract` | **PASS** (exit 0, state Success, 0 errors) |
| `P10FixtureContract` | **PASS** (exit 0, state Success) |
| `P11ReloadContract` | **SKIPPED** (second-process reload not run) |

Evidence: `P11HostFixture/`, `P11HostMenu2/`, `P11HostScope/`, `P10Regression/`, `p11-scope-probe.log`.

## 6. Final positive rerun

| Command | Result |
|---|---|
| `py -3.12 tools\run_m2_native_editor_test.py` | **PASS**, editor exit 0, automation 1/0/0, test `M2LayoutBrokerPositive` Success |

`planId=layout:bdfd3a70cd9a7a8b`,
`layoutHash=sha256:5a6637241c69d04ce910ac685fc83cd4fe05efd5f5c7f23503779583e0e533c9`;
real-snapshot Python parity exact; broker accepted=1/completed=1; receipt readOnly/no token.

## 7. Python regression

| Command | Result |
|---|---|
| `py -3.12 -m unittest discover -s tests\python -p "test_layout_broker_*.py"` | **140 passed** |
| `py -3.12 scripts\RunPythonTests.py full --quiet` | **1381 tests, OK (skipped=17), 118.6 s** |

No Python source changed in M2.3.

## 8. Evidence and hashes

`Build/m2-native/Evidence/` (git-ignored): final `m2-native-evidence.json`, `native-result.json`,
`ue-editor.log`, `broker.log`, `build.log`, negative/stale JSON + reports, `P10Regression/`,
`P11HostFixture/`, `P11HostMenu2/`, `P11HostScope/`, `SHA256SUMS.txt`.

## 9. Limitations / stop

- Timeout fix is a host-rendering workaround, not an engine fix.
- Cross-process/R1 fingerprint drift not run; same-process live recheck passed.
- P11 reload skipped.
- M3 menu/fallback/cutover and R2 Apply not started; NativeLayoutPreview remains off by default.
