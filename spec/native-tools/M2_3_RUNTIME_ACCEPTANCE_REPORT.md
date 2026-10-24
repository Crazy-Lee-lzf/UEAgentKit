# M2.3 Native Runtime Acceptance Report

- Date: 2026-10-24
- Worktree: `E:\WorkSpace\UEAgentKit-WT\layout-broker-m1`
- Branch: `feature/layout-broker-m1`
- Base: M2.2 `ac22145`
- Engine: UE **5.6.1** at `E:\EPICGAME\UE_5.6`
- Disposable host: `Build/m2-native/M2NativeHost.uproject`; P11 host under
  `Build/m2-native/P11AutomationHost`
- Scope: finish the M2.2 acceptance gaps only; no M3 menu, no Apply, no production cutover
- Status: **ACCEPTANCE GATES 1–4 PASS, with one explicit skip (P11 reload)**

## 1. Session/model

Same DSH session `session-7fd8360d-3ac6-47f5-898e-4d52afd3e59d`; runtime declares
`cline-pass/deepseek-v4.1-flash-1017`, non-Fast, max reasoning, no model switch. No independent model-id
verification is exposed. Only this lane used the UE heavy slot, serially.

## 2. Changes in this slice

Production behavior is unchanged except the test-only response-ready hook; the service already had the
revision/fingerprint checks.

| File | Change |
|---|---|
| `NativeLayoutPreviewService.h/.cpp` | added `HasWorkerResponseForTest()` (test seam only) |
| `NativeLayoutPreviewTest.cpp` | positive test now asserts a padded **>1 MiB request** is rejected locally; new `M2LayoutBrokerStale` test for real revision/fingerprint drift |
| `tools/run_m2_native_editor_test.py` | positive test name updated; evidence includes UE log/automation report |
| `tools/run_m2_native_negative.py` | `revision`/`fingerprint` scenarios; timeout uses a normal renderer (no `-nullrhi`); wrong-token real-broker scenario |
| `Build/m2-native/P11AutomationHost` + marker | disposable, valid P11 host; `Build/tools` junction for the existing P11 worker path |

No P11/P8/P9 menu, `Build.cs`, `EditorBridge`, existing preview, MCP route, or production project was edited.
`NativeLayoutPreview` remains off by default; the service is not created by module startup.

## 3. Gate 1 — timeout exit 3 fixed

The M2.2 timeout negative test observed the correct `native-preview-timeout`/no-plan but the Editor exited 3
after the test with `GetRestoredDimensions is not expected to be called on this platform`. That fatal comes from
UE’s NullRHI/Slate shutdown path for this longer-running headless case.

Fix: run the timeout scenario **without `-nullrhi`** (still `UnrealEditor-Cmd -unattended -nop4 -nosplash`,
no UI input, no Computer Use). Result:

- editor exit **0**;
- automation `succeeded=1, failed=0`;
- outcome `native-preview-timeout`, `planAccepted=false`;
- evidence `Evidence/m2-native-negative-timeout.json`.

This is a test-host workaround, not an engine fix; the positive and other quick tests remain on their existing
NullRHI path.

## 4. Gate 2 — real stale revision / fingerprint

New `UEAgentKit.Native.M2LayoutBrokerStale` uses a real UE snapshot, an external fake broker that returns a
valid result from the request snapshot, then mutates the live fixture **after** the worker response is ready and
before game-thread `Tick` validation.

| Scenario | Mutation | Observed code | Write assertions |
|---|---|---|---|
| revision | move call node + save package (revision changes) | `native-preview-revision-conflict` | no plan; undo 0→0; receipt saved=false/compiled=false; dirty false |
| fingerprint | move call node, clear dirty (fingerprint only) | `native-preview-fingerprint-conflict` | no plan; undo 0→0; receipt saved=false/compiled=false; dirty false |

Editor exit **0**, automation success in both runs. Evidence: `m2-native-negative-revision.json`,
`m2-native-negative-fingerprint.json`, `native-stale-result.json`, `Negative-revision/`,
`Negative-fingerprint/`.

## 5. Gate 3 — >1 MiB request local rejection

The positive automation test pads the real snapshot context by 1,100,000 bytes and calls the service again.
The service refuses before creating a client socket:

- `native-preview-request-too-large` from the local 1 MiB UTF-8 check;
- no plan, no socket request, no broker stats change.

The positive test’s `Success` covers this assertion; the request cap remains 1 MiB.

## 6. Gate 4 — P11 fixture/menu/scope in a valid host

The previous bare M2 host failed `P11Menu::IsAvailable()` because the P11 gate requires project name
`P11AutomationHost` and `<Project>/P11TestOnlyBuild.json`. A valid disposable host was created at
`Build/m2-native/P11AutomationHost` with that marker and a plugin junction. `Build/tools` is a junction to the
repo `tools` so the existing P11 worker path resolves; no P11 source was changed.

| Test | Result |
|---|---|
| `UEAgentKit.GraphLayout.P11FixtureContract` | **PASS** (exit 0, state Success, 0 errors) |
| `tests/python/p11_build_scope_probe.py` on the real fixture | **PASS** (selection/incremental requests generated) |
| `UEAgentKit.GraphLayout.P11MenuContract` | **PASS** (exit 0, state Success, 0 errors) |
| `UEAgentKit.GraphLayout.P11ScopeContract` | **PASS** (exit 0, state Success, 0 errors) |
| `UEAgentKit.GraphLayout.P11ReloadContract` | **SKIPPED** (requires second-process reload) |
| `UEAgentKit.GraphLayout.P10FixtureContract` | **PASS** (exit 0, state Success) |

Evidence: `P11HostFixture/`, `P11HostMenu2/`, `P11HostScope/`, `P10Regression/`, `p11-scope-probe.log`.

## 7. Final positive rerun

After all changes, the positive real-broker test was rerun (renamed
`UEAgentKit.Native.M2LayoutBrokerPositive` so automation prefix matching cannot pick up negative tests):

- editor exit **0**, automation succeeded 1 / failed 0 / notRun 0;
- test **Success**;
- `planId=layout:bdfd3a70cd9a7a8b`,
  `layoutHash=sha256:5a6637241c69d04ce910ac685fc83cd4fe05efd5f5c7f23503779583e0e533c9`;
- Python `build_preview` on the exported real snapshot is **exactly equal**;
- broker `accepted=1, completed=1, rejected=0`; receipt readOnly and no token.

## 8. Python regression

- Broker tests: **140 passed**.
- Full repository suite: **1381 tests, OK (17 skipped), 118.6 s**
  (`Build/layout-broker/Evidence/full-python-suite-m2_2.log`; no Python source changed in M2.3).

## 9. T-gates

| Gate | Status |
|---|---|
| G1 timeout clean exit 0 + expected code/no plan | **PASS** |
| G2 real revision conflict | **PASS** |
| G3 real fingerprint conflict | **PASS** |
| G4 >1 MiB request local rejection | **PASS** |
| G5 P10 fixture | **PASS** |
| G6 P11 fixture/menu/scope | **PASS** |
| G7 P11 reload | **SKIPPED** |
| G8 no Apply/Save/transaction from preview; token absent | **PASS** in runtime assertions |
| G9 cross-process fingerprint drift | **NOT RUN** (same-process live recheck only) |
| M3 menu/cutover | NOT STARTED |

## 10. Limitations

- The timeout fix is a host-rendering choice, not an upstream UE NullRHI repair.
- Cross-process/R1 fingerprint drift was not reproduced; the service rejects a live mismatch.
- P11 reload was skipped; P11 fixture/menu/scope passed in the disposable host.
- No production snapshot/menu wiring, no fallback, no Apply, no native registry; NativeLayoutPreview remains off.

## 11. Evidence

`Build/m2-native/Evidence/` (git-ignored): `m2-native-evidence.json`, `native-result.json`, `ue-editor.log`,
`broker.log`, `build.log`, `build-console.log`, `m2-native-negative-{spoof,oversize,timeout,token-error,
wrong-token,revision,fingerprint}.json`, `native-stale-result.json`, `Automation/`, `Negative-*/`,
`P10Regression/`, `P11HostFixture/`, `P11HostMenu2/`, `P11HostScope/`, `SHA256SUMS.txt`.
