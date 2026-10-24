# M2.2 Native Editor LayoutBroker Client — First Slice Report

- Date: 2026-10-24
- Worktree: `E:\WorkSpace\UEAgentKit-WT\layout-broker-m1`
- Branch: `feature/layout-broker-m1`, base M2.1 `616bff3`
- Status: **M2.2 THIN RUNTIME SLICE — POSITIVE PASS; NEGATIVE MATRIX PARTIAL**
- UE: **5.6.1** (`E:\EPICGAME\UE_5.6`), `UnrealEditor-Cmd`, disposable host `Build/m2-native`
- Not authorized / not done: M3 menu migration, P11 menu/page changes, production Apply, push/merge/reset/clean/delete, Computer Use/SendInput, other worktrees/projects

## 0. Session/model

Same DSH session `session-7fd8360d-3ac6-47f5-898e-4d52afd3e59d`; runtime declares
`cline-pass/deepseek-v4.1-flash-1017`, non-Fast, max reasoning, no model switch. No independent model-id
verification is exposed. R1’s UE build/Editor slot was released for this slice; only this host was used.

## 1. What was built

New C++ only (no existing plugin source edited, no `Build.cs` change):

| File | Purpose |
|---|---|
| `NativeLayoutBrokerDescriptor.h/.cpp` | strict loopback descriptor parse, identity/freshness, regular/reparse check, current-user protected DACL proof |
| `NativeLayoutBrokerClient.h/.cpp` | `FRunnable` outbound loopback `FSocket`, 1 MiB NDJSON, 5 s deadline, cancel, no UObject |
| `NativeLayoutBrokerPlanId.h/.cpp` | canonical C++ `planId` recomputation matching Python `layout.py` projection/domain hash |
| `NativeLayoutPreviewService.h/.cpp` | game-thread snapshot/state machine, worker dispatch, Tick completion, planId/layoutHash/node-set/identity validation, live revision/fingerprint/dirty/PIE recheck, sanitized receipt |
| `NativeLayoutPreviewTest.cpp` | real P9 snapshot + external broker integration test; negative test |
| `tools/run_m2_native_editor_test.py` | external harness: starts Editor, waits for real snapshot/session, starts Python M1 broker in the harness, validates parity |
| `tools/run_m2_native_negative.py` | fake/real broker negative harness (spoof, oversize, timeout, token-error, wrong-token) |
| `scripts/BuildM2NativeHost.ps1` | disposable host build (`Build/m2-native`), plugin junction, AutoSDK MSVC junction |

The service is **never constructed by module startup**; there is no menu entry, no `CreateProc`, no Python
from the Editor, and no default connection. `NativeLayoutPreview=off` and `NativeToolRegistry=off` are read
with `GConfig` defaults false; the test uses the explicit snapshot seam and a test-only bypass.

## 2. Descriptor and identity

`FUEAgentKitLayoutBrokerDescriptor::Load` reads `<Project>/Saved/UEAgentKit/LayoutBroker.json` and requires:

- regular file, not directory/reparse point, 1–64 KiB;
- **protected DACL with exactly one allow ACE = current process user** (Win32 `GetNamedSecurityInfo`,
  `GetSecurityDescriptorControl`, `GetAclInformation`/`GetAce`, `EqualSid`); any failure is
  `native-preview-broker-descriptor-invalid`, no bypass;
- `address` exactly `127.0.0.1`, port 1–65535, token 32–128 lowercase hex;
- exact project name and `ProjectPathHash` (same `FPaths` normalization + SHA-1 as EditorBridge);
- contract `1.0` + algorithm `p0-layered/1.0.0`, maxConcurrentJobs 1, `startedAtUtc` within 12 h/not >5 min future;
- token is kept only in memory for the request; it is never logged or placed in a receipt.

Runtime evidence: the positive broker descriptor was `PROVEN / windows-dacl /
protected-dacl-current-user-only`; a copy written without hardening was rejected; a missing descriptor was
rejected before any connect.

## 3. Client and service behavior

- Snapshot is the existing `BuildP9MenuPreviewInputs` result (real P9 preflight + 10-field envelope), not a
  reimplementation.
- Request is compact NDJSON (condensed JSON writer), bounded to 1 MiB, deadline 5000 ms; the service keeps
  `spacingX/96`, `spacingY/64`, `targetWidth/1800`, `seed/0` for validation.
- Worker: loopback connect only, send-all, read one line with 1 MiB cap, 50 ms wait slices, total 5 s, atomic
  cancellation; no UObject/UClass access on the worker.
- Game-thread `Tick`: validates response schema/identity, exact node set from the canonical context, result
  vs plan positions, `layoutHash` via existing `ComputeLayoutHash`, and `planId` via the new canonical helper
  (targetWidth/spacingX/spacingY/seed/packing). Then re-resolves the exact graph and rebuilds the live
  preflight: dirty/revision/fingerprint/PIE drift fails closed.
- Receipt: `ok/planProduced/readOnly/saved/compiled/stale/workerMode/previewPath/requestId/brokerVersion/
  algorithm/assetPath(asset virtual path)/graphGuid/packageRevision/graphFingerprint/nodeCount/planId/
  layoutHash/movedCount/errorCode`; no token, descriptor path, or raw broker diagnostics.
- No Apply/Save/transaction/asset write from the service.

## 4. Build result

- Host: `Build/m2-native/M2NativeHost.uproject`, plugin junction to this worktree, `UE_SDKS_ROOT` junction.
- Engine: `E:\EPICGAME\UE_5.6`.
- Toolchain: VS 2022 14.44.35228 (engine warns it is not the preferred 14.38; build succeeds).
- First full plugin build: **117.6 s, Result: Succeeded** (79 actions); subsequent incremental builds ~10 s.
- Raw log: `Build/m2-native/Evidence/build.log` (+ `build-console.log`).

## 5. Real UE automation — positive

Command (harness, outside the Editor):

```text
UnrealEditor-Cmd.exe Build/m2-native/M2NativeHost.uproject
  -unattended -nop4 -nosplash -nullrhi -nopause -UEAgentKitAutomationChild
  -ExecCmds=Automation RunTests UEAgentKit.Native.M2LayoutBrokerPositive;Quit
  -TestExit=Automation Test Queue Empty
  -ReportExportPath=Build/m2-native/Evidence/Automation
```

The test creates a disposable `/Game/M2Native/BP_M2LayoutNative` Blueprint, opens its EventGraph, starts a
test-only EditorBridge, captures a **real P9 snapshot**, exports it, waits for an **externally started** Python
M1 broker, runs the service, and writes evidence.

Result:

- Editor exit **0**; automation `succeeded=1, failed=0, notRun=0`;
- test result **Success**;
- `nodeCount=4`, plan produced: `layout:bdfd3a70cd9a7a8b`,
  `layoutHash=sha256:5a6637241c69d04ce910ac685fc83cd4fe05efd5f5c7f23503779583e0e533c9`;
- Python `build_preview` on the exported real preflight/context equals the C++ result exactly
  (`real-snapshot-parity=true`), and canonical `layoutHash` matches;
- broker stats `accepted=1, completed=1, rejected=0`; descriptor DACL PROVEN;
- receipt `readOnly=true, saved=false, compiled=false`, package clean, token absent.
- Raw: `Evidence/ue-editor.log`, `Evidence/broker.log`, `Evidence/Automation/`, `Evidence/native-result.json`,
  `Evidence/m2-native-evidence.json`.

This is the genuine runtime slice. It is **not** M3: the menu still calls the legacy in-editor Python path.

## 6. Negative/adversarial runtime matrix

| Scenario | Transport | Expected | Result |
|---|---|---|---|
| missing descriptor | none | `native-preview-broker-descriptor-missing` | **PASS** (same test, before broker start) |
| default off (`StartPreview`) | none | `native-preview-disabled` | **PASS** |
| inherited/non-hardened DACL | local file | descriptor rejected | **PASS** |
| forged planId | fake broker success | `native-preview-plan-id-mismatch`, no plan | **PASS** (Editor exit 0, automation success) |
| >1 MiB response | fake broker | `native-preview-response-too-large` | **PASS** (Editor exit 0) |
| broker error token | fake broker error | `native-preview-token-rejected` | **PASS** (Editor exit 0) |
| wrong token | **real M1 broker**, descriptor token overwritten | `native-preview-token-rejected` | **PASS** (Editor exit 0) |
| response timeout | fake broker sleeps 7 s; normal renderer (no `-nullrhi`) | `native-preview-timeout`, no plan | **PASS** (Editor exit 0, automation success) |
| stale package revision | real UE fixture modified + saved after snapshot | `native-preview-revision-conflict`, no plan, no save/transaction | **PASS** (Editor exit 0; undo 0→0, receipt saved=false) |
| stale graph fingerprint | real UE fixture moved in memory, dirty cleared | `native-preview-fingerprint-conflict`, no plan, no save/transaction | **PASS** (Editor exit 0; undo 0→0, receipt saved=false) |
| >1 MiB **request** | real snapshot padded before request build | local `native-preview-request-too-large` | **PASS** (positive automation assertion; no socket send) |

Evidence: `Evidence/m2-native-negative-*.json`, `Evidence/Negative-*/index.json`.

## 7. Legacy P11 / P10 regression status

- No P11/P8/P9 menu, Build.cs, EditorBridge, or existing preview source was edited.
- `UEAgentKit.GraphLayout.P10FixtureContract` **passed** in the isolated host (exit 0, test state Success,
  4 log warnings only).
- A valid disposable P11 host was created under `Build/m2-native/P11AutomationHost` with project name
  `P11AutomationHost` and `P11TestOnlyBuild.json`; `Build/tools` is a junction to the repo `tools` directory so
  the existing P11 worker path resolves. P11 source was not edited.
- `UEAgentKit.GraphLayout.P11FixtureContract` **passed** (exit 0, test state Success, no errors).
- `tests/python/p11_build_scope_probe.py` generated real selection/incremental requests from that fixture.
- `UEAgentKit.GraphLayout.P11MenuContract` **passed** (exit 0, test state Success, no errors).
- `UEAgentKit.GraphLayout.P11ScopeContract` **passed** (exit 0, test state Success, no errors).
- `P11ReloadContract` requires a second-process reload and was **skipped** in this slice.
- P10/P11 source diffs are unchanged; P10 fixture, P11 fixture/menu/scope are green; P11 reload remains skipped.
- R1.3 warning context requested by the user: the R1 `CallFunction NodeGuid` warning (6 clean tests + 1 warning
  per Editor process) was **not modified** (R1 worktree untouched). This M2 host initially had an **own**
  comment-node NodeGuid warning; the fixture was changed to a minimal GUID-clean Blueprint, and the final run
  has no such warning. Cross-process `graphFingerprint` drift is **not** observed or fixed; the service guards
  it by re-reading the live preflight and failing closed on mismatch.

## 8. Feature flags / compatibility

- `NativeLayoutPreview=off`, `NativeToolRegistry=off` default; no startup service/menu/registry changes.
- The service has no production registration path; only explicit test seam invocation in this slice.
- `baseGraphHash` remains `sha256:<64>`; `graphFingerprint` remains `sha1:<40>`; envelope is the real ten-field
  P9 object without envelope sessionId.
- M1 remains full-only; 1.1 selection/incremental remains a draft schema and is not enabled.
- Existing M1/M2 Python artifacts are unchanged by this C++ slice; 140 Python broker tests remain green.
  Full Python regression: **1381 tests, OK (17 skipped), 118.6 s**
  (`Build/layout-broker/Evidence/full-python-suite-m2_2.log`).

## 9. T-gates (M2.2)

| Gate | Status |
|---|---|
| T1 compile UE 5.6.1 host + plugin | PASS |
| T2 real P9 snapshot → external broker → native client | PASS |
| T3 C++ planId/layoutHash/node-set/identity validation | PASS (positive + forged planId spoof) |
| T4 descriptor loopback/DACL/identity/freshness | PASS positive + inherited/missing negative |
| T5 default-off / no menu / no CreateProc | PASS in source and config-off assertion |
| T6 oversize/error/timeout/wrong-token | **PASS**: response >1 MiB, request >1 MiB local, broker error, real wrong token, timeout (Editor exit 0) |
| T7 revision/fingerprint dirty/PIE recheck | **PASS** for real UE revision and fingerprint drift; no plan/write/undo; PIE path implemented but not separately driven |
| T8 P10/P11 regression | P10 fixture **PASS**; P11 fixture/menu/scope **PASS**; P11 reload **SKIPPED** |
| T9 no Apply/Save/transaction/token leak | PASS in runtime receipt/dirty assertions |
| M3 menu/cutover | NOT STARTED |

## 10. Limitations / next steps

- The NullRHI `GetRestoredDimensions` fatal was avoided for the timeout case by running that one test with a
  normal renderer (still `UnrealEditor-Cmd`, no UI input); it now exits 0. This is a test-host workaround, not
  an engine fix.
- Revision/fingerprint checks are same-process live rechecks; **cross-process** fingerprint drift relative to
  R1 was not reproduced or fixed.
- P11 reload was skipped; P11 menu/scope were run in the new valid P11 host.
- Production snapshot wiring, feature-flag lifecycle, and menu/fallback are M3 work and not authorized.
- The Python harness starts the broker; there is deliberately no Editor-side process creation.

## 11. Evidence inventory

`Build/m2-native/Evidence/` (git-ignored): `ue-editor.log`, `broker.log`, `build.log`,
`build-console.log`, `snapshot-preflight.json`, `snapshot-context.json`, `native-result.json`,
`m2-native-evidence.json`, `m2-native-negative-*.json`, `native-stale-result.json`, `Automation/`,
`Negative-*/`, `P10Regression/`, `P11Regression/`, `P11HostFixture/`, `P11HostMenu2/`, `P11HostScope/`,
`SHA256SUMS.txt`.

No push/merge/reset/clean/delete; only this worktree and its disposable host were used.
