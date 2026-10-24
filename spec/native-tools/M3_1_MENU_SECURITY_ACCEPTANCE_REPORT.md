# M3.1 Opt-In Preview Menu Security Acceptance — Report

- Date: 2026-10-24
- Worktree: `E:\WorkSpace\UEAgentKit-WT\layout-broker-m1`
- Branch: `feature/layout-broker-m1`, base M3.0 `d501e40`
- Engine: UE **5.6.1**, disposable hosts under `Build/m2-native`
- Status: **PASS for the isolated M3.1 gate**; production cutover/default-on/M4 **NOT approved**
- No push/merge/reset/clean/delete, no Computer Use, no Editor-side process creation

## 1. Session/model

Same DSH session `session-7fd8360d-3ac6-47f5-898e-4d52afd3e59d`; runtime declares
`cline-pass/deepseek-v4.1-flash-1017`, non-Fast/max reasoning, no model switch. Only this lane used the UE heavy
slot; Editor processes were serial.

## 2. Scoped code changes

| Change | Purpose |
|---|---|
| `NativeLayoutPreviewService` preflight gates | fail closed with `native-preview-package-dirty` / `native-preview-pie-active` before descriptor/socket work |
| `FUEAgentKitGraphLayoutP11Menu` worker-response test seam | lets stale-revision/fingerprint automation mutate only after the broker response is ready, without bypassing production rechecks |
| `NativePreviewMenuTest` scenarios | dirty, revision, fingerprint, forged plan, timeout fallback, timeout-strict, unavailable fallback, PIE |
| `NativePreviewMenuSettingsTest` + harness | runtime command-line override matrix and read-once proof |
| `run_m3_menu_test.py` | external real/fake broker scenarios; no Editor `CreateProc` |

No hard guard was weakened. No handwritten receipt is accepted by the production service; receipts are produced
by the real menu/service path.

## 3. Menu-level security / policy matrix

All scenarios use the real P11 menu action, real fixture, real service, and external harness broker (real or
one-shot fake). All Editor exits **0**, automation **Success**.

| Scenario | Native code | Fallback | Plan | Writes |
|---|---|---|---:|---:|
| package dirty before action | `native-preview-package-dirty` | **false** | no | none; dirty restored only after assertion |
| PIE active | `native-preview-pie-active` | **false** | no | none |
| live revision drift after response | `native-preview-revision-conflict` | **false** | no | no preview save/undo |
| live graph fingerprint drift | `native-preview-fingerprint-conflict` | **false** | no | no preview save/undo |
| forged planId | `native-preview-plan-id-mismatch` | **false** | no | none |
| wrong token | `native-preview-token-rejected` | **false** | no | none |
| invalid/DACL descriptor | `native-preview-broker-descriptor-invalid` | **false** | no | none |
| missing broker | `native-preview-broker-descriptor-missing` | **true** | legacy plan | none |
| unavailable broker | `native-preview-broker-unavailable` | **true** | legacy plan | none |
| timeout | `native-preview-timeout` | **true** | legacy plan | none |
| fallback disabled + missing broker | descriptor-missing | **false** | no | none |
| fallback disabled + timeout | timeout | **false** | no | none |
| repeated click while in flight | busy receipt | false | first request only | none |

Every receipt records `readOnly=true`, `saved=false`, `compiled=false`, accurate
`errorCode`/`nativeErrorCode`, `previewPath`, and `fallback`; dirty/revision/fingerprint repairs are confined to
the disposable fixture. The runtime action returned in **0.030–0.037 s**; broker work was asynchronous.

Stale/fingerprint tests wait for the worker response, then mutate the real UE graph. Revision is a real package
save; fingerprint is an in-memory move with the dirty flag cleared. The service’s live recheck, not a test
receipt, produces the conflict.

## 4. Startup override matrix

`FUEAgentKitNativePreviewSettings` is read once at module startup. The test-only override is honored only with
**both** `-UEAgentKitAutomationChild` and the valid `P11AutomationHost` marker.

| Case | Native | Fallback | Override applied |
|---|---:|---:|---:|
| child + `on` | on | true | true |
| child + `off` | off | true | true |
| child + `on-no-fallback` | on | false | true |
| child + unknown value | off | true | **false** |
| no `-UEAgentKitAutomationChild` + `on` | off | true | **false** |
| non-P11 project + child + `on` | off | true | **false** |

Six of six cases passed; the settings test also writes a `flagReadOnce` check after touching `GConfig` in memory.
No JSON-RPC/MCP request path sets these flags. `NativeToolRegistry` remains independent and off.

## 5. Source/lifecycle audit

- No native service is constructed while `NativeLayoutPreview=off`; the P11 action returns to the legacy path
  before service creation.
- The ticker is installed only for an in-flight native action and removed on terminal completion, shutdown, or
  destructor; `StartPreviewForMenu` failure still reaches terminal completion.
- The menu holds the service single-flight; repeat clicks return a busy receipt and do not start a second worker.
- Receipts contain no broker token or descriptor path; a scan of M31 JSON/log evidence for the harness token
  found **no match**. Service errors are bounded messages.
- Cancellation is the existing M2 worker/client cancellation on teardown; there is no user-facing cancel button
  in this slice.
- Fallback is restricted to missing/unavailable/timeout and runs the existing synchronous legacy path on the
  completion tick; it is documented as potentially blocking. No fallback exists for dirty/PIE/revision/
  fingerprint/token/DACL/plan/identity faults.

## 6. Regression

| Test | Result |
|---|---|
| P11 FixtureContract (fresh M31 host) | Success |
| P11 ScopeContract | Success |
| P11 ReloadContract (second independent process) | Success; positions/topology/DLL true |
| P11 MenuContract with native off | Success |
| P10 FixtureContract | Success |
| Python layout broker | **140 passed** (12.5 s) |
| Full Python suite | **1381 tests, OK (17 skipped), 123.254 s** |

P11 final revisions (M31 host):
selection `sha256:5bb52170e5865b55fac60ceec14eda3653520067912e23aceace7c1081012869`,
incremental `sha256:22c2767533c62afca1662e2191d9744a9e4dc286ef07f6199de72cd5a20fcdf3`;
scope/reload DLL `sha256:552b1a60430fd0e7eb3361084729f200a53c9aeec603b194199bcbfbdb0ee663`.

## 7. Remaining limitations

- Oversized **request** rejection is covered by the M2.3 service automation, not by a menu-level enlarged
  fixture; forged plan is menu-level.
- Fallback execution is the legacy synchronous Python path; native work is nonblocking, fallback completion may
  not be.
- The menu remains `P11AutomationHost`-gated. No production project, default-on, M4, R2 Apply, or legacy
  retirement was started.
- No user-facing progress/cancel UI; the busy receipt is the only repeat-action signal.

## 8. M4 prerequisites (Z1–Z6)

Do not start M4 until all are explicitly satisfied:

1. **Z1 parity ≥3 graphs**: native menu planId/layoutHash parity on at least three materially different
   Blueprint graphs (not only the P11 fixture), including comments/reroutes.
2. **Z2 expanded negative matrix**: repeat dirty/PIE/stale/token/DACL/forged/timeout/unavailable/busy on those
   graphs and with concurrent/repeated menu use.
3. **Z3 rollback drill**: prove default-off/legacy rollback and disabled-broker startup in a controlled host.
4. **Z4 explicit user approval**: user selects M4/default-on scope and accepts the production rollout.
5. **Z5 production eligibility**: define the real project/host gate and manual broker deployment; no Editor
   process creation.
6. **Z6 observability/abort criteria**: receipt/log telemetry, failure-rate thresholds, and a stop/rollback
   decision before any default-on.

## 9. Evidence

`Build/m2-native/Evidence/M31/` (git-ignored): `m3-menu-run-*.json`, `m3-menu-*.json`,
`M3Menu-*/index.json`, `M3Menu-*-ue.log`, `PIE/`, `Settings/` matrix and per-case reports,
`P11FixtureContract/`, `P11ScopeContract/`, `P11ReloadContract/`, `P10Fixture/`,
`python-layoutbroker.log`, `python-full.log`, `build.log`, `SHA256SUMS.txt`.
