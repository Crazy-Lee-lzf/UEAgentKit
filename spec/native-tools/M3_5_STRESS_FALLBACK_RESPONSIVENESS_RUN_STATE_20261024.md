# M3.5 Stress and Fallback Responsiveness — Run State (2026-10-24)

- Status: **PASS (isolated 53-node stress + fallback measurement); M4 BLOCKED**
- Branch: `feature/layout-broker-m1`, base `43d9f3a`
- Host: `Build/m2-native/M35/P11AutomationHost`
- Editor processes serial, launched minimized via `tools/ue_launch.py`; no Computer Use
- No push/merge/reset/clean/delete; no default-on/M4

## 1. Session/model

Same DSH session `session-7fd8360d-3ac6-47f5-898e-4d52afd3e59d`; runtime declares
`cline-pass/deepseek-v4.1-flash-1017`, non-Fast/max reasoning, no model switch.

## 2. Build/fixture

| Command | Result |
|---|---|
| `pwsh -File scripts\BuildM2NativeHost.ps1` | Succeeded |
| `UnrealEditor-Cmd ... RunTests UEAgentKit.Native.M35StressFixtures` | Success, exit 0 |

Fixture: `/Game/M35/BP_M35_Stress`; EventGraph **53 nodes / 50 edges**, function graph 21/20;
all node GUIDs/pins valid, 2 comments.

## 3. Native stress trials

| Command | Result |
|---|---|
| `py -3.12 tools\run_m35_stress.py` | 3/3 native trials pass; fallback cases pass |

Native: action return 0.055–0.057 s; completion 0.287–0.310 s; plan `layout:a68a4cccba990d51`,
moved 53; Python parity all; peak RSS 2990–3009 MB; broker accepted/completed 1 each; DACL PROVEN.
Repeatability (`tools/compare_m35_repeat.py`): graphGuid/node GUID order/pins/package revision/
fingerprint/plan/moved all equal across three independent processes; package SHA
`sha256:32df4f15…`, DLL `sha256:d755b7b9…`.

## 4. Fallback measurements

| Case | Action | Wait | Fallback worker | Error |
|---|---:|---:|---:|---|
| broker missing | 0.048 s | 0.422 s | 0.422 s | descriptor-missing |
| timeout | 0.057 s | 5.460 s | 0.427 s | timeout |

Busy: `py -3.12 tools\run_m35_busy.py` → 1 busy rejection, native plan, no writes.
Fallback is synchronous on the completion tick; no harmful defect confirmed at 53 nodes, but scale risk
remains. No legacy semantics changed.

## 5. Regression

| Test | Result |
|---|---|
| P11 Fixture/Menu/Scope | Success |
| P11 Reload (separate process) | Success; positions/topology/DLL true |
| P10 Fixture | Success |
| Python broker | 140 passed (12.2 s) |
| Full Python suite | 1388 tests, OK (17 skipped) |

P11 revisions: selection `sha256:e80e4b80…`, incremental `sha256:79f74861…`,
DLL `sha256:d755b7b9…`.

## 6. Limits / M4 gate

- One 53-node event graph measured; function graph not measured, macro not claimed.
- No production-scale or production-asset proof; no user-facing cancel/shutdown-while-pending test.
- No same-process thread/socket leak measurement; three isolated processes only.
- **Z4 approval missing; Z5 production/manual broker approval missing; Z6 operational telemetry missing.**
- NativeLayoutPreview remains off; no M4/R2/M5/M6 work.

Evidence: `Build/m2-native/Evidence/M35/` (git-ignored), including `stress-metrics.json`,
`repeatability.json`, `stress-busy.json`, captures/receipts/logs, P11/P10 reports, Python logs,
`SHA256SUMS.txt`.
