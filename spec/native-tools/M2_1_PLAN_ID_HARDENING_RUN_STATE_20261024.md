# M2.1 planId Hardening — Run State (2026-10-24)

- Status: **M2.1 offline hardening complete**; awaiting review
- Branch: `feature/layout-broker-m1`
- Base: M2.0 tip `fdaec71`
- Worktree: `E:\WorkSpace\UEAgentKit-WT\layout-broker-m1`
- Remote: **no push, no merge, no reset, no clean, no delete**
- UE / C++ / menu / Bridge / MCP / production projects: **not touched**
- R1: still owns the only UE 5.6 heavy build / Editor slot

## 1. Model/session

Same DSH session `session-7fd8360d-3ac6-47f5-898e-4d52afd3e59d`; runtime declares
`cline-pass/deepseek-v4.1-flash-1017`, non-Fast, no model switch as requested. No independently verifiable
model id is exposed to the agent.

## 2. Gap and fix

M2.0 found that `protocol.validate_response` (and therefore the actual Python client) checked `layoutHash`
but not `planId`; the broker self-check had the same gap. A forged well-formed `planId` could pass. M2.1 adds:

- `src/ue_agent_kit/layout_broker/planid.py`: one pure canonical helper with no broker imports;
- `protocol.validate_plan_id(...)`, used by the broker self-check and by `validate_response` (actual client);
- `conformance.recompute_plan_id` delegates to the same helper (no second algorithm);
- stable client-local code `native-preview-plan-id-mismatch`.

The helper pins `baseGraphHash`, `mode`, positions (`nodeId/x/y`, no `size`), original options
(`spacingX`, `spacingY`, `seed`), and the expected packing (`targetWidth`, `spacing`, `skyline-v1`);
it rejects packing/seed tampering.

## 3. Commands and results

| Command | Result |
|---|---|
| `py -3.12 -m unittest discover -s tests\python -p "test_layout_broker_*.py"` | **140 passed** (97 M1 + 21 M2 + 22 M2.1) |
| `py -3.12 tools\layout_broker_m2_1_probe.py` | `ok: true`, 6/6 checks |
| `py -3.12 scripts\RunPythonTests.py full --quiet` | **1381 tests, OK (skipped=17), 129.1 s** |
| `py -3.12 -m compileall -q ...` | exit 0 |

M2.1 probe checks:

- three golden cases remain exact (`linear`, `p4_branch`, `m1_fork`);
- actual broker rejects a forged plan id;
- actual broker rejects a forged plan id after positions + `layoutHash` are recomputed;
- actual Python client rejects a malicious success payload with a forged plan id.

## 4. Evidence (ignored `Build/layout-broker/Evidence/`)

| File | Content |
|---|---|
| `m2_1-planid-probe.json` | 6/6 checks, three exact golden planIds, server/client rejection |
| `full-python-suite-m2_1.log` | final repository run after M2.1 |
| `m2-conformance-probe.json` | unchanged M2.0 probe |
| `m1-offline-probe.json` | unchanged M1 probe |

## 5. OFFLINE-PROVEN

- Shared helper reproduces all three golden `planId`s and commits to the request options/packing.
- Actual broker self-check rejects forged plan ids before any success response, including after
  `layoutHash` recomputation.
- Actual Python client rejects forged success payloads locally.
- Three golden results remain exactly equal; no valid v1 bytes changed.
- Existing guards (version, request id, identity, context, truncation, size, token, replay, timeout,
  busy, cancellation, rate limit) remain green.

## 6. Runtime-unverified / limits

- No Editor, C++ client, FSocket, Ticker, receipt, menu, UE automation, or production project.
- C++ must implement the same projection (targetWidth/spacingX/spacingY/seed/packing) using canonical
  hashing before M2 runtime validation.
- M1 remains full-only; v1.1 selection/incremental remains a draft schema, not active.
- The broker maps a self-check planId failure to `native-preview-worker-failed` on the wire; the specific
  `native-preview-plan-id-mismatch` reason is in `server.status()`/logs.
- No fallback, cutover, or Apply behavior is added.

## 7. Stop point

M2.1 stops here. M2 runtime/C++ integration remains unauthorized; R1 must release the UE slot and a separate
review must approve the future C++ branch.

## 8. Git state

Scoped local commit(s) on `feature/layout-broker-m1` (no push; see `git log -1`), containing the helper,
protocol/server/client enforcement, M2.1 tests/probe/report, and this runstate.
