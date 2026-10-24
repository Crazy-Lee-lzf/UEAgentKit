# M3.3 Z2 Expanded Cross-Graph Matrix — Report

- Date: 2026-10-24
- Worktree: `E:\WorkSpace\UEAgentKit-WT\layout-broker-m1`
- Branch: `feature/layout-broker-m1`, base M3.2 `4529c6d`
- Engine: UE **5.6.1**, disposable `Build/m2-native/M32/P11AutomationHost`
- Status: **Z1 PASS, Z2 PASS (36/36 isolated cases), Z3 PASS; M4 still BLOCKED by Z4/Z5/Z6**
- Default remains `NativeLayoutPreview=off`; no production/default-on or M4 work

## 1. Session/model

Same DSH session `session-7fd8360d-3ac6-47f5-898e-4d52afd3e59d`; runtime declares
`cline-pass/deepseek-v4.1-flash-1017`, non-Fast/max reasoning, no model switch. Serial UE 5.6.1 runs only.

## 2. Z1 fidelity and repeatability

Three real M32 graphs are used: Event, Function graph `M32Function`, and Cast/Comment/Knot EventGraph.
The Event graph now has a real connected exec edge (2 linked pins); the complex graph has 3 real connections
(6 linked pins). The context JSON’s top-level `edges` array is empty by exporter design, so connectivity is
recorded via actual pin `links` and the fixture manifest `linkedPins`, not inferred from node count.

| Graph | Nodes | Edges | Linked pins | GraphGuid | planId | layoutHash | Moved |
|---|---:|---:|---:|---|---|---|---:|
| Event | 2 | 1 | 2 | `48570e07…` | `layout:67ee45faf5eed3ac` | `sha256:42b914f4…` | 1 |
| Function | 2 | 0 | 0 | `78c1a870…` | `layout:b5cf82ff03c23277` | `sha256:9069f9a0…` | 2 |
| Cast/comment/knot | 6 | 3 | 6 | `4c3317b5…` | `layout:7bfa5fb1a0fe2345` | `sha256:f646b8d2…` | 6 |

Each graph was run twice in independent Editor processes with the real external M1 broker. Exact captured P9
inputs were separately passed to Python `build_preview`; planId, layoutHash, and **movedNodeIds** matched.
Both runs also matched on on-disk package SHA256, `graphGuid`, node GUID order, pin GUIDs/links,
`packageRevision`, `graphFingerprint`, and planId, with the same Editor DLL. No non-determinism was observed.
Macro instances are not claimed; cast/comment/knot are real.

Evidence: `M33/z1-multigraph-parity.json`, `M33/GraphParity/`, `M33/Guid/guid-repeatability.json`,
`M33/Guid/run1|run2/`.

## 3. Z2 expanded matrix — 36/36

Twelve cases were run on **each** of the three graph types (12 × 3). Every case had Editor exit 0 and real
Automation Success.

| Case | Expected | Graphs |
|---|---|---|
| dirty | `native-preview-package-dirty`, no fallback, no plan | 3/3 |
| PIE | `native-preview-pie-active`, no fallback, no plan | 3/3 |
| busy / repeat | native plan, one busy rejection | 3/3 |
| unavailable broker | fallback true, `native-preview-broker-unavailable` | 3/3 |
| timeout | fallback true, `native-preview-timeout` | 3/3 |
| descriptor missing | fallback true, `native-preview-broker-descriptor-missing` | 3/3 |
| invalid DACL | `native-preview-broker-descriptor-invalid`, no fallback | 3/3 |
| bad token | `native-preview-token-rejected`, no fallback | 3/3 |
| forged plan | `native-preview-plan-id-mismatch`, no fallback | 3/3 |
| revision drift | `native-preview-revision-conflict`, no fallback | 3/3 |
| fingerprint drift | `native-preview-fingerprint-conflict`, no fallback | 3/3 |
| oversized response | `native-preview-response-too-large`, no fallback | 3/3 |

All receipts were readOnly, saved=false, compiled=false; no preview asset write/undo; test tokens are absent
from evidence. Oversized **request** remains service-level M2.3 coverage; the response-oversize case is
graph-level here. No fake broker was used for positive/native cases; fake brokers were used only for injected
faults, and real brokers for positive/busy/token.

Evidence: `M33/z2-expanded-matrix.json`, `M33/<graph>_<case>/index.json`, `M33/<graph>_<case>-ue.log`,
`M33/<graph>_<case>-<case>-ue.log`.

## 4. Z3 rollback rerun

After fixture changes, `M32/z3-rollback.json` was rerun across four independent processes: off fixture →
on + external broker native → on/no-broker legacy fallback → off legacy-only/no descriptor. All checks passed;
final config is off. P11 selection/incremental scope and second-process reload passed in a fresh M33 host.

Evidence: `M33/z3-rollback.json`, `M33/Rollback-*`, `M33/P11ScopeContract/`, `M33/P11ReloadContract/`.

## 5. Regression

| Test | Result |
|---|---|
| UE 5.6.1 build | Succeeded |
| P11 Fixture | Success |
| P11 MenuContract (native off) | Success |
| P11 ScopeContract | Success |
| P11 ReloadContract (second process) | Success; positions/topology/DLL true |
| P10 Fixture | Success |
| Python broker | 140 passed (12.483 s) |
| Full Python suite | 1381 tests, OK (17 skipped), 121.393 s |

P11 revisions: selection `sha256:28f84c8f…`, incremental `sha256:2248dc3c…`,
DLL `sha256:662104af…`; scope and reload match.

## 6. Remaining production qualifications

- **Z1**: isolated 3-graph parity and cross-process GUID repeatability pass; production-shaped assets still
  required.
- **Z2**: isolated 36/36 matrix pass; production host/broker/rollback operations still need sign-off.
- **Z3**: isolated rollback pass; production rollback runbook not exercised.
- **Z4**: **APPROVAL NOT GRANTED** — explicit user M4/default-on approval is required.
- **Z5**: production project eligibility, manual broker install, descriptor DACL/token procedure not approved.
- **Z6**: observability/abort thresholds are unapproved proposals; no telemetry service exists.
- Macro-instance fixture is absent and not claimed.
- Oversized request is service-level only; response oversize is graph-level.

**M4 is not authorized.** No default-on, production integration, service install, R2 Apply, or M5/M6
retirement was performed.

## 7. Evidence

`Build/m2-native/Evidence/M33/` (git-ignored): `m32-fixtures.json`, `z1-multigraph-parity.json`,
`GraphParity/`, `Guid/`, `z2-expanded-matrix.json`, `<graph>_<case>/`, `z3-rollback.json`,
`P11Fixture/`, `P11MenuContract/`, `P11ScopeContract/`, `P11ReloadContract/`, `P10Fixture/`, `python-layoutbroker.log`, `python-full.log`, `build.log`,
`SHA256SUMS.txt`.
