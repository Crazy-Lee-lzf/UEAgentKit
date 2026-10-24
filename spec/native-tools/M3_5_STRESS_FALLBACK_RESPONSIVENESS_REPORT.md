# M3.5 Pre-M4 Large-Graph Stress and Fallback Responsiveness — Report

- Date: 2026-10-24
- Worktree: `E:\WorkSpace\UEAgentKit-WT\layout-broker-m1`
- Branch: `feature/layout-broker-m1`, base M3.4 `43d9f3a`
- Engine: UE **5.6.1**, disposable `Build/m2-native/M35/P11AutomationHost`
- Status: **PASS for the isolated stress/fallback slice; M4 remains BLOCKED**
- NativeLayoutPreview default **off**; legacy fallback true; broker 1.0 full only

## 1. Session/model

Same DSH session `session-7fd8360d-3ac6-47f5-898e-4d52afd3e59d`; runtime declares
`cline-pass/deepseek-v4.1-flash-1017`, non-Fast/max reasoning, no model switch. Editor processes were serial
and launched through `tools/ue_launch.py` minimized.

## 2. Stress fixture

Real disposable Blueprint `/Game/M35/BP_M35_Stress`:

| Graph | Nodes | Edges | Invalid node GUIDs | Invalid pins |
|---|---:|---:|---:|---:|
| EventGraph | **53** | **50** | 0 | 0 |
| M35Function | 21 | 20 | 0 | 0 |

The event graph is a BeginPlay-driven 50-call exec chain plus two comments; all 50 edges are real pin links.
The function graph is an entry-driven 20-call chain. Fixture creation/saving occurred only during setup in the
disposable host. The native menu measurement used the 53-node EventGraph; the function graph was created and
validated but not separately measured.

## 3. Native stress trials

Three independent Editor processes, each with an external real M1 broker, same on-disk package.

| Trial | Wall (Editor) | Invoke | Wait | Peak RSS | Plan | Moved | Broker |
|---|---:|---:|---:|---:|---|---:|---|
| 1 | 23.250 s | 0.057 s | 0.287 s | 3000 MB | `layout:a68a4cccba990d51` | 53 | accepted 1/completed 1 |
| 2 | 23.250 s | 0.056 s | 0.310 s | 3009 MB | same | 53 | accepted 1/completed 1 |
| 3 | 22.921 s | 0.057 s | 0.307 s | 2990 MB | same | 53 | accepted 1/completed 1 |

Wall time includes Editor startup/shutdown; action return **~0.057 s**, native completion **~0.29–0.31 s**,
p50 0.307 s / p95 0.310 s. Canonical Python `build_preview` on each exact P9 capture matched planId,
layoutHash, and all 53 movedNodeIds. Receipts: `native-broker`, fallback=false, readOnly, saved/compiled false,
packageDirtyAfter false. Descriptor DACL was PROVEN on every trial; token absent from evidence.

Three independent captures also matched graphGuid, node GUID order, pin GUIDs/links, package revision,
graphFingerprint, planId/hash/moved IDs; package SHA256 and Editor DLL were identical across runs.

## 4. Fallback responsiveness

Same 53-node graph, fallback enabled:

| Case | Action return | Completion wait | Synchronous fallback worker | Error retained |
|---|---:|---:|---:|---|
| broker missing | 0.048 s | 0.422 s | **0.422 s** | `native-preview-broker-descriptor-missing` |
| timeout | 0.057 s | 5.460 s | **0.427 s** | `native-preview-timeout` |

Native path waits only for its worker (~0.3 s). Fallback adds legacy Python work **~0.42 s** on this graph;
timeout adds the 5 s transport wait before that worker runs. The fallback runs on the completion tick and is
synchronous; at 53 nodes it is measurable but not a confirmed harmful blocking defect. No legacy semantics
were changed and no redesign was attempted. Larger graphs/production workloads remain a risk.

## 5. Reliability

- Three repeated native previews all succeeded with exact plans and no writes.
- Broker was stopped/restarted between trials; each new request got a fresh descriptor and one accepted job.
- Busy/repeat action: **1** busy rejection; first preview succeeded, readOnly/no writes.
- Ticker/service lifecycle source audit: ticker removed on terminal/shutdown/destructor; no duplicate ticker.
- No same-process thread/socket leak measurement was performed. Three isolated processes had stable ~3 GB RSS;
  peak RSS is sampled process memory, not allocator peak.
- Editor plugin shutdown **while pending** was not separately driven; source audit only. No crash observed in
  any completed run.

## 6. Regression

| Test | Result |
|---|---|
| UE 5.6.1 build | Succeeded |
| P11 Fixture/Menu/Scope | Success |
| P11 Reload (independent second process) | Success; positions/topology/DLL true |
| P10 Fixture | Success |
| Python broker | 140 passed |
| Full Python suite (M3.4 state) | 1388 tests, OK (17 skipped) |

## 7. M4 gate

**M4 remains BLOCKED.** This slice does not grant production approval:

- **Z4 explicit user approval still missing.**
- **Z5** production project eligibility/manual broker deployment not approved or exercised in a real project.
- **Z6** production telemetry/alerting and operational abort criteria not established; thresholds remain
  proposals.
- Stress is one 53-node event graph, not production-scale or diverse production graphs; function graph and
  macro-instance parity are not claimed.
- Fallback is synchronous (~0.42 s here), has no user-facing cancellation, and may scale poorly.
- Production rollback at scale and real production-like assets remain unproven.

No default-on, M4, R2 Apply, M5/M6 retirement, production config, push, or merge was performed.

## 8. Evidence

`Build/m2-native/Evidence/M35/` (git-ignored): `stress-metrics.json`, `repeatability.json`,
`stress-busy.json`, captures, receipts, UE logs, P11/P10 reports, Python logs, `SHA256SUMS.txt`.
