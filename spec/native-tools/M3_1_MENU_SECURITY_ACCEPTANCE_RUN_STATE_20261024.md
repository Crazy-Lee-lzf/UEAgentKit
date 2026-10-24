# M3.1 Menu Security Acceptance — Run State (2026-10-24)

- Status: **PASS for isolated M3.1**; M4/default-on not started
- Branch: `feature/layout-broker-m1`, base `d501e40`
- Engine: UE 5.6.1 (`E:\EPICGAME\UE_5.6`)
- Hosts: `Build/m2-native/M3/P11AutomationHost`, `Build/m2-native/M31/P11AutomationHost`
- No push/merge/reset/clean/delete, no Computer Use, serial Editor runs

## 1. Session/model

Same DSH session `session-7fd8360d-3ac6-47f5-898e-4d52afd3e59d`; runtime declares
`cline-pass/deepseek-v4.1-flash-1017`, non-Fast/max reasoning, no model switch.

## 2. Build

| Command | Result |
|---|---|
| `pwsh -File scripts\BuildM2NativeHost.ps1` after service gates/menu seams/PIE/settings tests | **Succeeded** |

## 3. Menu security scenarios

`py -3.12 tools\run_m3_menu_test.py <scenario>` with external Python broker/fake; no Editor `CreateProc`.

| Scenario | Exit | Automation | Receipt |
|---|---:|---|---|
| dirty | 0 | Success | `native-preview-package-dirty`, fallback=false, no plan |
| revision | 0 | Success | `native-preview-revision-conflict`, fallback=false, no plan |
| fingerprint | 0 | Success | `native-preview-fingerprint-conflict`, fallback=false, no plan |
| forged | 0 | Success | `native-preview-plan-id-mismatch`, fallback=false, no plan |
| token | 0 | Success | `native-preview-token-rejected`, fallback=false |
| dacl | 0 | Success | `native-preview-broker-descriptor-invalid`, fallback=false |
| fallback | 0 | Success | descriptor-missing, `legacy-fallback` plan |
| unavailable | 0 | Success | broker-unavailable, `legacy-fallback` plan |
| timeout | 0 | Success | timeout, `legacy-fallback` plan |
| strict | 0 | Success | descriptor-missing, fallback=false |
| timeout-strict | 0 | Success | timeout, fallback=false |
| busy | 0 | Success | native plan, one busy rejection |
| happy | 0 | Success | native `layout:e71b154ef087d25c`, hash `sha256:c070a4c2…`, Python parity |

All receipts: `readOnly=true`, `saved=false`, `compiled=false`, `packageDirtyAfter=false`; action return
0.030–0.037 s. Evidence: `M31/m3-menu-run-*.json`, `M31/m3-menu-*.json`, `M31/M3Menu-*`.

## 4. PIE menu-level

`py -3.12 ... UnrealEditor-Cmd ... -ExecCmds=Automation RunTests UEAgentKit.Native.M3MenuPIE`
with `NativeLayoutPreview=on`, no broker.

- Editor exit **0**, automation **Success**.
- Real PIE started; menu action returned without waiting; receipt code `native-preview-pie-active`,
  fallback=false, no plan, clean package.
- Evidence: `M31/PIE/index.json`, `M31/PIE/m3-menu-pie.json`.

## 5. Command-line override matrix

`py -3.12 tools\run_m3_settings_test.py` — **6/6 cases pass**:

| Case | Result |
|---|---|
| child + `on` | native on, fallback true, override true |
| child + `off` | native off, fallback true, override true |
| child + `on-no-fallback` | native on, fallback false |
| child + unknown | ignored (off, override false) |
| no child + `on` | ignored (off, override false) |
| non-P11 host + child + `on` | ignored (off, override false) |

The test also touched `GConfig` after startup and confirmed the startup snapshot did not change
(`flagReadOnce=true`). Evidence: `M31/Settings/m3-settings-matrix.json` and per-case reports/logs.
No MCP/JSON-RPC setter exists.

## 6. Regression

| Test | Result |
|---|---|
| P11 FixtureContract (fresh M31 host) | Success |
| P11 ScopeContract | Success |
| P11 ReloadContract (second process) | Success; positions/topology/DLL true |
| P11 MenuContract (native off) | Success |
| P10 FixtureContract | Success |
| Python layout broker | 140 passed (12.5 s) |
| Full Python suite | 1381 tests, OK (17 skipped), 123.254 s |

P11 final revisions: selection `sha256:5bb52170…`, incremental `sha256:22c27675…`,
DLL `sha256:552b1a60…`; reload matches scope.

## 7. Evidence / audit

`Build/m2-native/Evidence/M31/` (git-ignored): all run/receipt JSON, `M3Menu-*` Automation indexes and UE logs,
`PIE/`, `Settings/`, `P11*`, `P10Fixture/`, Python logs, `build.log`, `SHA256SUMS.txt`.

Source audit: no service when off; ticker installed/removed per action and shutdown; single-flight busy guard;
receipts contain no broker token (token scan clean); fallback only missing/unavailable/timeout; no fallback for
security/identity/policy faults; no user cancel button (worker cancellation on teardown only).

## 8. Limitations / stop

- Oversized **request** is proven at the service level in M2.3; not repeated as an enlarged menu fixture.
- Fallback runs the old synchronous Python path on the completion tick.
- Menu remains disposable-host gated; production cutover/default-on/M4, R2 Apply, M5/M6 retirement not started.
- M4 requires Z1–Z6 (report §8), including ≥3-graph parity, expanded negatives, rollback drill, and explicit
  user approval. Stop here.
