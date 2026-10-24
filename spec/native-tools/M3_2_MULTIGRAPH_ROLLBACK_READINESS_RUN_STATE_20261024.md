# M3.2 Pre-M4 Multi-Graph and Rollback — Run State (2026-10-24)

- Status: **Z1 PASS, Z2 PARTIAL, Z3 PASS; Z5/Z6 review only; M4 blocked (Z4 not granted)**
- Branch: `feature/layout-broker-m1`, base `f8327e1`
- Engine: UE 5.6.1 (`E:\EPICGAME\UE_5.6`)
- Host: `Build/m2-native/M32/P11AutomationHost`
- No push/merge/reset/clean/delete, no Computer Use; serial Editor processes

## 1. Session/model

Same DSH session `session-7fd8360d-3ac6-47f5-898e-4d52afd3e59d`; runtime declares
`cline-pass/deepseek-v4.1-flash-1017`, non-Fast/max reasoning, no model switch.

## 2. Build and fixtures

| Command | Result |
|---|---|
| `pwsh -File scripts\BuildM2NativeHost.ps1` | Succeeded |
| `UnrealEditor-Cmd ... RunTests UEAgentKit.Native.M32Fixtures` | Success, exit 0 |

Manifest: event 2 nodes, function graph 2 nodes, cast/comment/knot graph 6 nodes.
Macro instance not created; no macro support is claimed.

## 3. Z1 parity

| Command | Result |
|---|---|
| `py -3.12 tools\run_m32_graph_parity.py` | **3/3 graphs pass** |

Each graph: real M3 menu with external M1 broker, Editor exit 0, native receipt, clean package, Python
`build_preview` planId/layoutHash/movedNodeIds parity. Values:
event `layout:e95a5be13ec09c3f`; function `layout:b5cf82ff03c23277`;
cast/comment/knot `layout:7bfa5fb1a0fe2345`. Evidence `M32/z1-multigraph-parity.json`.

## 4. Z2 subset

| Command | Result |
|---|---|
| `py -3.12 tools\run_m32_negative.py` | **5/5 subset pass** |

On the cast/comment/knot graph: dirty, revision, fingerprint, forged, token all fail closed, fallback=false, exit
0. PIE/busy/timeout/unavailable/DACL remain P11-only from M3.1; Z2 is partial. Evidence
`M32/z2-complex-negative.json`, `M32/Neg-*`.

## 5. Z3 rollback

| Command | Result |
|---|---|
| `py -3.12 tools\run_m32_rollback.py` | **PASS**, 4 separate Editor processes |

Sequence: off fixture → native with external broker → on/no broker legacy fallback → off legacy-only with no
descriptor. Receipts: native `native-broker`; fallback `legacy-fallback` with descriptor-missing retained; off
receipt has no native/fallback fields. Final config is off. Evidence `M32/z3-rollback.json`.

## 6. Regression

| Test | Result |
|---|---|
| P11 Fixture (M32) | Success |
| P11 MenuContract native off (rollback) | Success |
| P11 ScopeContract (M32) | Success |
| P11 ReloadContract (second process) | Success; positions/topology/DLL true |
| P10 Fixture | Success |
| Python broker | 140 passed (12.483 s) |
| Full Python suite | 1381 tests, OK (17 skipped), 121.393 s |

P11 final revisions: selection `sha256:cbeaf3c5…`, incremental `sha256:8b5187a4…`,
DLL `sha256:7a0ea89c…`.

## 7. Z5/Z6 review

Report documents project eligibility, manual broker/descriptor DACL install, opt-in rollout/labeling,
receipt/metrics observability, and proposed abort/rollback thresholds. No production integration, service
install, default-on, or telemetry service was added. Z4 approval is **NOT GRANTED**.

## 8. Limitations / stop

- Z2 is a subset on one additional graph; not all negative cases were rerun on all three.
- No macro-instance graph; cast/comment/reroute are real.
- Node-GUID audit is within the captured runs; no separate multi-process GUID replay on all graphs.
- P11/P10 remain isolated-host tests; no production project.
- M4/default-on, R2 Apply, M5/M6 retirement: NOT STARTED.

Evidence: `Build/m2-native/Evidence/M32/` (git-ignored), including `z1-multigraph-parity.json`,
`z2-complex-negative.json`, `z3-rollback.json`, graphs/captures, P11/P10 reports, Python logs and hashes.
