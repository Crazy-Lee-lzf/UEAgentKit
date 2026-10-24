# M3.0 Opt-In Native Preview Menu — Run State (2026-10-24)

- Status: **M3.0 PARTIAL (core paths pass); M4 not started**
- Branch: `feature/layout-broker-m1`, base `2ce408e`
- Host: `Build/m2-native/M3/P11AutomationHost` (marker, P11 fixture)
- Engine: UE 5.6.1 (`E:\EPICGAME\UE_5.6`)
- No push/merge/reset/clean/delete, no Computer Use, serial Editor runs

## 1. Session/model

Same DSH session `session-7fd8360d-3ac6-47f5-898e-4d52afd3e59d`; runtime declares
`cline-pass/deepseek-v4.1-flash-1017`, non-Fast/max reasoning, no model switch. No independent model-id check.

## 2. Build

| Command | Result |
|---|---|
| `pwsh -File scripts\BuildM2NativeHost.ps1` after M3 menu/settings/test | **Succeeded** (incremental, ~10 s) |

## 3. Feature flags

M3 host `Config/DefaultEngine.ini` was set per scenario:

```ini
[UEAgentKit]
NativeLayoutPreview=on|off
LegacyLayoutPreviewFallback=true|false
NativeToolRegistry=off
```

Read once at module startup by `FUEAgentKitNativePreviewSettings::InitializeFromStartup`. Final host config is
left **off** for legacy parity. Command-line `-UEAgentKitNativePreviewTest=...` is implemented for
`AutomationChild` + P11 host only but was not runtime-exercised (config path used).

## 4. M3 runtime scenarios

`py -3.12 tools\run_m3_menu_test.py <scenario>` with a real P11 fixture and external Python broker.

| Scenario | Exit | Automation | Key receipt |
|---|---:|---|---|
| happy | 0 | Success | native-broker `layout:e71b154ef087d25c`, hash `sha256:c070a4c2…`, Python parity |
| busy | 0 | Success | native plan, `busyRejects=1` |
| fallback | 0 | Success | `legacy-fallback`, native descriptor-missing retained |
| strict | 0 | Success | no plan, fallback=false |
| token | 0 | Success | no plan, token-rejected, fallback=false |
| dacl | 0 | Success | no plan, descriptor-invalid, fallback=false |

All: `readOnly=true`, `saved=false`, `compiled=false`, `packageDirtyAfter=false`; action return 0.030–0.037 s
(<0.5 s test bound). Receipts: `M3/m3-menu-*.json`; runs: `M3/m3-menu-run-*.json`; automation/logs:
`M3/M3Menu-*`.

## 5. P11/P10 regression

| Test | Result |
|---|---|
| `P11MenuContract` with native off (final DLL) | **Success**, exit 0, 0 errors |
| `P11FixtureContract` (M3 host) | Success, exit 0 |
| `P11ScopeContract` (M3 host, generated requests) | Success, exit 0 |
| `P11ReloadContract` (independent second process) | Success, exit 0; positions/topology/DLL true |
| `P10FixtureContract` (final DLL) | Success, exit 0 |

P11 reload revisions and DLL hash matched between scope and reload:
selection `sha256:7d199f76…`, incremental `sha256:d19ca7c6…`, DLL `sha256:9659ee7f…`.
No P11/P8/P9 menu or legacy source was changed for M3.

## 6. Python regression

| Command | Result |
|---|---|
| `py -3.12 -m unittest discover -s tests\python -p "test_layout_broker_*.py"` | **140 passed** |
| `py -3.12 scripts\RunPythonTests.py full --quiet` | **1381 tests, OK (skipped=17), 118.6 s** |

## 7. Evidence

`Build/m2-native/Evidence/M3/` (git-ignored): `m3-menu-run-*.json`, `m3-menu-*.json`,
`M3Menu-*/index.json`, `M3Menu-*-ue.log`, `P11MenuOffFinal/`, `P11FixtureContract/`,
`P11ScopeContract/`, `P11ReloadContract/`, `P10FixtureFinal/`, `p11-scope-probe.log`,
`SHA256SUMS.txt`.

## 8. Open items / stop

- Menu-level dirty/PIE/stale fail-closed automation is not separately run; the M2.3 service tests pass and
  fallback policy excludes those codes.
- The command-line test override is implemented but not runtime-exercised.
- Fallback executes the synchronous legacy Python path on the completion tick.
- M4/default-on, production menu cutover, R2 Apply, M5/M6 retirement: **not started**.
- No push; scoped local commit(s) only.
