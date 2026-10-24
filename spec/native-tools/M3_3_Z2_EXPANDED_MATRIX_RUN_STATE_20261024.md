# M3.3 Z2 Expanded Matrix — Run State (2026-10-24)

- Status: **Z1 PASS, Z2 PASS 36/36, Z3 PASS; M4 blocked (Z4 not granted)**
- Branch: `feature/layout-broker-m1`, base `4529c6d`
- Host: `Build/m2-native/M32/P11AutomationHost`
- No push/merge/reset/clean/delete, no Computer Use, serial Editor processes

## 1. Session/model

Same DSH session `session-7fd8360d-3ac6-47f5-898e-4d52afd3e59d`; runtime declares
`cline-pass/deepseek-v4.1-flash-1017`, non-Fast/max reasoning, no model switch.

## 2. Build/fixtures

| Command | Result |
|---|---|
| `pwsh -File scripts\BuildM2NativeHost.ps1` | Succeeded |
| `UnrealEditor-Cmd ... RunTests UEAgentKit.Native.M32Fixtures` | Success |

Fixture manifest: event 2 nodes / 1 edge / 2 linked pins; function 2/0/0; cast-comment-knot 6/3/6.
No macro fixture; no macro claim.

## 3. Z1 parity / GUID repeatability

| Command | Result |
|---|---|
| `py -3.12 tools\run_m32_graph_parity.py` (twice) | 3/3 each; planId/layoutHash/movedNodeIds Python parity |
| `py -3.12 tools\compare_m33_guid.py` | 3/3 equal across runs |

Package SHA256/DLL: event `c63e1d7a…`, function `1968048f…`, complex `333ae79e…`; DLL `662104af…`.
Both runs matched graphGuid, node GUID order, pin GUIDs/links, packageRevision, fingerprint, planId.
Evidence: `M33/z1-multigraph-parity.json`, `M33/Guid/guid-repeatability.json`.

## 4. Z2 matrix

| Command | Result |
|---|---|
| `py -3.12 tools\run_m33_matrix.py` | **36/36 cases pass** |

12 cases × 3 graphs: dirty, PIE, busy, unavailable, timeout, descriptor-missing fallback, DACL, token,
forged, revision, fingerprint, oversized response. All exit 0/Automation Success; 3/3 each. Evidence
`M33/z2-expanded-matrix.json`, `M33/<graph>_<case>/`, logs.

## 5. Z3 rollback

`py -3.12 tools\run_m32_rollback.py` → PASS 4 separate processes; final config off.
Evidence `M33/z3-rollback.json`.

## 6. Regression

| Test | Result |
|---|---|
| P11 Fixture/Menu/Scope | Success |
| P11 Reload (second process) | Success; positions/topology/DLL true |
| P10 Fixture | Success |
| Python broker | 140 passed (12.483 s) |
| Full Python suite | 1381 tests, OK (17 skipped), 121.393 s |

P11 revisions: selection `sha256:28f84c8f…`, incremental `sha256:2248dc3c…`,
DLL `sha256:662104af…`.

## 7. Limits / stop

- Isolated host only; no production assets, default-on, install service, or M4.
- Oversized request remains service-level M2.3; response oversize tested per graph.
- Macro fixture absent/not claimed.
- Z4 approval NOT GRANTED; Z5/Z6 production qualifications remain design-only.
- No push/merge; scoped local commit only.
