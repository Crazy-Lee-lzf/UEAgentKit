# M3.2 Pre-M4 Multi-Graph and Rollback Readiness — Report

- Date: 2026-10-24
- Worktree: `E:\WorkSpace\UEAgentKit-WT\layout-broker-m1`
- Branch: `feature/layout-broker-m1`, base M3.1 `f8327e1`
- Engine: UE **5.6.1**, disposable `Build/m2-native/M32/P11AutomationHost`
- Status: **Z1 PASS, Z2 PARTIAL (subset), Z3 PASS, Z5/Z6 REVIEW ONLY; M4 BLOCKED pending Z4**
- Default remains `NativeLayoutPreview=off`; no production project, default-on, install service, or M4 started

## 1. Session/model

Same DSH session `session-7fd8360d-3ac6-47f5-898e-4d52afd3e59d`; runtime declares
`cline-pass/deepseek-v4.1-flash-1017`, non-Fast/max reasoning, no model switch. Only this lane used the UE heavy
slot; Editor processes were serial.

## 2. Z1 — three real graphs, real external broker

Fixtures were created by real UE tooling under `/Game/M32/` in the disposable M32 host, saved once, then opened
in separate Editor processes. For each graph the actual M3 P11 menu action called the external Python M1
broker; the test captured the exact P9 preflight/envelope and a separate `build_preview` was run on those bytes.

| Graph | Shape | Nodes | GraphGuid | Native planId | layoutHash | Moved |
|---|---|---:|---|---|---|---:|
| Event (`BP_M32_EventV2`) | BeginPlay + PrintString | 2 | `edb77855-473a-6471-50d8-aa9a9af9b85b` | `layout:e95a5be13ec09c3f` | `sha256:3867a89f…` | 2 |
| Function (`BP_M32_FunctionV2`, `M32Function`) | function entry/result + call | 2 | `78c1a870-463a-c807-3d98-8a8d44e7e2ad` | `layout:b5cf82ff03c23277` | `sha256:9069f9a0…` | 2 |
| Cast/comment/knot (`BP_M32_ComplexV2`) | cast + comment + exec reroute/knot | 6 | `4c3317b5-46b5-75c3-ecc6-4ea7335f275e` | `layout:7bfa5fb1a0fe2345` | `sha256:f646b8d2…` | 6 |

All three cases: Editor exit **0**, automation **Success**, real `native-broker` receipt, no fallback, package
clean, `readOnly=true`, `saved=false`, `compiled=false`. Each receipt’s `planId`, `layoutHash`, and full
`movedNodeIds` matched canonical Python `build_preview` on the captured inputs. GraphGuid matched the fixture
manifest and each receipt. No macro-instance graph was created; macro support is **not claimed** (cast, comment,
and reroute/knot are real).

A node-GUID stability audit was limited to the captured node/moved IDs on these single-process runs; a
repeat-process GUID comparison across all three graphs was not run.

Evidence: `M32/z1-multigraph-parity.json`, `M32/GraphParity/capture-*.json`,
`M32/Graph-*/index.json`, `M32/m32-menu-*.json`, `M32/Graph-*.log`.

## 3. Z2 — negative matrix across a distinct graph

The full M3.1 matrix remains on the P11 fixture. On the **cast/comment/knot** graph, the following were rerun:

| Case | Code | Fallback | Plan | Exit |
|---|---|---|---:|---:|---:|
| dirty | `native-preview-package-dirty` | false | none | 0 |
| revision | `native-preview-revision-conflict` | false | none | 0 |
| fingerprint | `native-preview-fingerprint-conflict` | false | none | 0 |
| forged plan | `native-preview-plan-id-mismatch` | false | none | 0 |
| token | `native-preview-token-rejected` | false | none | 0 |

All no-fallback, no accepted plan, package clean, token absent from receipts. PIE, busy, unavailable/timeout,
and DACL remain P11-only in M3.1; they were not rerun on this graph. Z2 is therefore **PARTIAL**, not “all
graphs verified”.

Evidence: `M32/z2-complex-negative.json`, `M32/Neg-*/index.json`, `M32/Neg-*.log`.

## 4. Z3 — rollback drill (separate Editor restarts)

`M32/z3-rollback.json` records four independent processes:

1. P11 fixture with `NativeLayoutPreview=off` — legacy fixture succeeds.
2. `=on` + external broker — native `native-broker` receipt, fallback=false.
3. `=on`, broker absent — legacy fallback receipt, `nativeErrorCode=native-preview-broker-descriptor-missing`,
   fallback=true.
4. `=off`, broker absent — P11 menu legacy-only, exit 0, receipt has no native/fallback fields, descriptor
   absent.

Final host config is **off** (`NativeLayoutPreview=off`, `LegacyLayoutPreviewFallback=true`). Startup-flag
immutability is proven by the M3.1 six-case matrix (`flagReadOnce=true`); no new MCP configuration path exists.
P11 scope/reload in the fresh M32 host passed after the drill; selection/incremental remain exact legacy P11.

Evidence: `M32/z3-rollback.json`, `M32/Rollback-*/`, `M32/P11ScopeContract/`, `M32/P11ReloadContract/`.

## 5. Z5/Z6 review only — no integration

### Production eligibility options

- explicit project allowlist/marker (not project-name `P11AutomationHost`) plus a signed/allowlisted descriptor;
- per-project opt-in config, never default-on for arbitrary projects;
- separate canary project/host with manual broker ownership.

### Manual broker startup / descriptor

- user starts the Python broker manually; no Editor `CreateProc`, no Windows service install;
- descriptor at `<Project>/Saved/UEAgentKit/LayoutBroker.json`, current-user-only DACL, random token;
- broker verifies project path hash/session; descriptor contains no secret beyond the local token.

### Opt-in UI / rollout controls

- default-off config, visible “native preview” source/fallback label in the receipt/notification;
- no automatic menu cutover; legacy remains one config restart away;
- opt-in per project, with an explicit rollback-to-off procedure.

### Observability / abort thresholds (proposal)

- receipts: requestId, brokerVersion, algorithm, planId/layoutHash, nativeErrorCode, fallback, dirty/write flags;
- metrics: native failure rate, fallback rate, latency, descriptor/token failures, busy rejects;
- proposed stop thresholds: any security/identity failure, fallback >5%, native failure >2%, or any unexpected
  write; rollback = config off + restart, verify legacy.

Z6 is a **design proposal only**; no telemetry service, production descriptor install, or UI was added.

## 6. Regression

| Test | Result |
|---|---|
| UE 5.6.1 build | Succeeded |
| P11 Fixture (M32) | Success |
| P11 MenuContract with native off | Success (rollback drill) |
| P11 ScopeContract (M32) | Success |
| P11 ReloadContract (second process) | Success; positions/topology/DLL true |
| P10 Fixture | Success |
| Python broker | **140 passed** |
| Full Python suite | **1381 tests, OK (17 skipped), 121.393 s** |

P11 final revisions: selection `sha256:cbeaf3c5…`, incremental `sha256:8b5187a4…`,
DLL `sha256:7a0ea89c…`; scope/reload match.

## 7. Remaining M4 prerequisites

- **Z1**: satisfied for three isolated graphs; production parity still needs real production-shaped assets.
- **Z2**: PARTIAL — expand PIE/busy/timeout/unavailable/DACL and repeated actions across all three graphs.
- **Z3**: isolated rollback drill satisfied; production rollback runbook still required.
- **Z4**: **APPROVAL NOT GRANTED** — explicit user selection of M4/default-on is required.
- **Z5**: production eligibility and manual broker installation/DACL procedure not approved.
- **Z6**: observability/abort thresholds are proposal only; no production telemetry.

**M4 remains BLOCKED.** Do not enable default-on, install a broker service, or start M4/M5/M6 from this work.

## 8. Evidence

`Build/m2-native/Evidence/M32/` (git-ignored): `m32-fixtures.json`, `z1-multigraph-parity.json`,
`GraphParity/`, `Graph-*/`, `m32-menu-*.json`, `z2-complex-negative.json`, `Neg-*/`,
`z3-rollback.json`, `Rollback-*/`, `P11ScopeContract/`, `P11ReloadContract/`, `P10Fixture/`,
`python-layoutbroker.log`, `python-full.log`, `build.log`, `SHA256SUMS.txt`.
