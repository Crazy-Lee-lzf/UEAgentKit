# M3.0 Opt-In Native Preview Menu + Legacy Fallback — Isolated Slice Report

- Date: 2026-10-24
- Worktree: `E:\WorkSpace\UEAgentKit-WT\layout-broker-m1`
- Branch: `feature/layout-broker-m1`, base M2.4 `2ce408e`
- Engine: UE **5.6.1**, disposable hosts under `Build/m2-native`
- Status: **M3.0 CORE PASS / TEST-MATRIX PARTIAL** (see §8); M4/default-on NOT started
- No production menu cutover, no R2 Apply, no push/merge/reset/clean/delete, no Computer Use

## 1. Session/model

Same DSH session `session-7fd8360d-3ac6-47f5-898e-4d52afd3e59d`; runtime declares
`cline-pass/deepseek-v4.1-flash-1017`, non-Fast/max reasoning, no model switch. Only this lane used the UE
heavy slot; Editor runs were serial.

## 2. Feature flags (read once at startup)

New `NativePreviewMenuSettings.h/.cpp`, initialized once in `UEAgentKitEditorModule::StartupModule`:

- `[UEAgentKit] NativeLayoutPreview=off|on` (default **off**);
- `[UEAgentKit] LegacyLayoutPreviewFallback=true|false` (default **true**);
- `[UEAgentKit] NativeToolRegistry` read independently, default off; no registry behavior added.

Flags are immutable after startup; no MCP parameter or runtime toggle can change them. The test-only command-line
override `-UEAgentKitNativePreviewTest=on|off|on-no-fallback` is honored only when **both**
`-UEAgentKitAutomationChild` and the disposable `P11AutomationHost` marker are present. The runtime tests used the
host `Config/DefaultEngine.ini` path; the command-line override is implemented but not separately exercised.

With `NativeLayoutPreview=off`, module startup and the P11 action are byte-for-byte the pre-M3 legacy behavior;
no native service is constructed and no broker connection is attempted.

## 3. Async menu dispatch

`FUEAgentKitGraphLayoutP11Menu` keeps the existing ToolMenu section/entry names. `ExecutePreview` now:

1. returns immediately if `NativeLayoutPreview=off` and runs the existing synchronous legacy path unchanged;
2. if on, captures the real P9 snapshot via `FUEAgentKitNativeLayoutPreviewService::StartPreviewForMenu()` on the
   game thread and registers a 0.05 s `FTSTicker` callback;
3. the existing M2 worker (`FSocket`, 5 s/1 MiB) runs off the game thread; `Tick` validates the response;
4. completion writes the same `Saved/UEAgentKit/P11Preview/menu-invocation.json` receipt and shows a
   **non-modal** Slate notification (suppressed in automation), never a modal dialog;
5. no vendor Python/CMD/PowerShell is spawned by C++; the broker is external.

Measured invocation return: **0.030–0.037 s** while the plan was still in flight (test assertion `<0.5 s`).

Selection/incremental are not translated: the P11 menu path remains full-preview only; the existing P11
scope/incremental tests continue to use the legacy Apply path. The broker's 1.0 `mode=full` contract was not
changed; 1.1 remains draft and is not accepted.

## 4. Fallback policy

When native fails and `LegacyLayoutPreviewFallback=true`, the dispatcher may run the existing legacy Python
preview on the game thread and keep the legacy plan, but only for transport-unavailability faults:

- `native-preview-broker-descriptor-missing`
- `native-preview-broker-unavailable`
- `native-preview-timeout`

The final receipt preserves `fallback=true`, `previewPath=legacy-fallback`, `nativePreviewPath=native-broker`,
`nativeErrorCode` and `nativeErrorMessage`, plus the legacy plan fields.

No fallback is performed for security/identity/policy failures: token rejection, descriptor-invalid/DACL,
revision/fingerprint conflict, dirty/PIE, identity/plan/hash mismatch. Those remain strict native failures, so a
security fault cannot be hidden by a legacy plan. The fallback itself is the existing synchronous Python path
and may block when it runs; the native action remains nonblocking.

## 5. Isolated runtime results

All runs used a fresh `Build/m2-native/M3/P11AutomationHost` with the P11 marker and fixture, external Python
broker (no Editor `CreateProc`), and serial Editor processes.

| Scenario | Editor exit | Automation | Receipt |
|---|---:|---|---|
| `happy` native broker | 0 | Success | `native-broker`, plan `layout:e71b154ef087d25c`, hash `sha256:c070a4c2aafbe14cc205b6dea2fbd5ff7de5356f16c5e7274248de8633c7b7fc` |
| `busy` repeat action | 0 | Success | native plan; **1** busy rejection; no duplicate request |
| `fallback` missing broker + fallback on | 0 | Success | `legacy-fallback`, plan produced, `nativeErrorCode=native-preview-broker-descriptor-missing` |
| `strict` missing broker + fallback off | 0 | Success | no plan, `fallback=false`, descriptor-missing |
| `token` real broker wrong token | 0 | Success | no plan, `fallback=false`, `native-preview-token-rejected` |
| `dacl` inherited-ACL descriptor | 0 | Success | no plan, `fallback=false`, `native-preview-broker-descriptor-invalid` |
| P11 menu default off | 0 | Success | legacy synchronous path unchanged |
| P11 fixture / scope / reload | 0 / 0 / 0 | Success | final M3 DLL; reload positions/topology/DLL true |
| P10 fixture | 0 | Success | unchanged |

Happy native receipt matched Python `build_preview` on the real P11 preflight/context exactly
(`layout:e71b154ef087d25c`, same hash). All receipts `readOnly=true`, `saved=false`, `compiled=false`,
`packageDirtyAfter=false`; no asset write/transaction was observed.

P11 reload final revisions (M3 host):
selection `sha256:7d199f76c952bd3aaa82f222b818eb56663eed38a479840cce492b62e1713695`,
incremental `sha256:d19ca7c643a724dbcfcdb27c2e0b767dec3cf2b3a0af86fadd864c9e8140f4c1`;
scope, reload and DLL hashes match across the two independent processes.

## 6. Off legacy parity

With `NativeLayoutPreview=off`, `P11MenuContract` passed in the M3 host (exit 0, Success, 0 errors), including
the existing node/pin menu, receipt, and synchronous Python assertions. No existing P11 test was changed or
skipped.

## 7. Python / offline regression

- Python layout broker tests: **140 passed**.
- Full Python suite: **1381 tests, OK (17 skipped), 118.6 s** (no Python source changed in M3.0).

## 8. Gate matrix / status

| Gate | Status |
|---|---|
| flags read once, default off, fallback default true | PASS (source + runtime config) |
| off legacy parity | PASS (`P11MenuContract`) |
| on native happy + exact plan/hash parity | PASS |
| nonblocking action / tick lifecycle | PASS (0.03–0.04 s return) |
| broker busy/repeat | PASS |
| missing-broker fallback with retained code | PASS |
| fallback-disabled strict failure | PASS |
| token / DACL strict failure | PASS |
| zero write / read-only receipt | PASS |
| selection/incremental remain legacy | PASS via P11 scope tests; no native scope route |
| P10/P11 fixture/menu/scope/reload | PASS |
| **menu-level dirty/PIE/stale fail-closed** | **NOT SEPARATELY AUTOMATED**; service-level M2.3 tests pass, and fallback policy excludes those codes |
| command-line test override | implemented, **not runtime-exercised** (config path used) |
| fallback legacy nonblocking | fallback itself is the synchronous legacy path (documented limitation) |

**M3.0 is marked PARTIAL**, not a full production cutover: core opt-in/fallback/security/no-write paths are
runtime-proven, but menu-level dirty/PIE/stale automation and the command-line override path remain open.

## 9. Limitations

- The menu is still `P11AutomationHost`-gated; this is a disposable-host slice, not a production project menu.
- Fallback runs the old synchronous Python path inside the completion tick; it can block that one completion.
- There is no progress UI or cancellation control beyond the busy receipt; the service is single-flight.
- The broker is started manually by the test harness; no Editor-side process creation exists.
- M3.0 does not enable default-on (M4), does not change P11 selection/incremental to native, and does not
  introduce R2 Apply.

## 10. Evidence

`Build/m2-native/Evidence/M3/` (git-ignored): `m3-menu-run-{happy,busy,fallback,strict,token,dacl}.json`,
`m3-menu-*.json` receipts, `M3Menu-*/index.json`, `M3Menu-*-ue.log`, `P11MenuOffFinal/`,
`P11FixtureContract/`, `P11ScopeContract/`, `P11ReloadContract/`, `P10FixtureFinal/`, `p11-scope-probe.log`,
`SHA256SUMS.txt`. No push/merge/reset/clean/delete was performed.
