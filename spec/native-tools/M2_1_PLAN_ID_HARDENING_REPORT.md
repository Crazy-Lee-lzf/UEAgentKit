# M2.1 Layout Broker — planId Hardening Report (offline)

- Date: 2026-10-24
- Worktree: `E:\WorkSpace\UEAgentKit-WT\layout-broker-m1`
- Branch: `feature/layout-broker-m1`
- Base: M2.0 tip `fdaec71`
- Status: **M2.1 OFFLINE-PROVEN**; no Editor runtime integration
- Scope: fix the forged-`planId` gap in the actual broker self-check and actual Python client; no layout
  algorithm, valid v1 bytes, menu, Bridge, MCP, UE, C++, or production project changes

## 0. Model/session

Same DSH session `session-7fd8360d-3ac6-47f5-898e-4d52afd3e59d`; runtime declares
`cline-pass/deepseek-v4.1-flash-1017`, non-Fast and no model switch as requested. No independently verifiable
model id is exposed to the agent. No Computer Use, UE Editor, UE compile, or other worktree was used.

## 1. The gap

M1/M2.0 validated `layoutHash` (hash of positions) and the plan shape, but did **not** recompute `planId`.
`planId` is a separate canonical hash that commits to:

```text
baseGraphHash + mode + positions[nodeId,x,y] + options{spacingX,spacingY,seed} + packing
```

A well-formed forged `planId` therefore passed `protocol.validate_response`, even though the M2 conformance
helper rejected it. The server's own self-check had the same blind spot, so the broker could emit a forged plan
id (if a compromised/buggy `preview_fn` returned one) and the client would accept it.

## 2. Fix

### 2.1 One shared pure helper

New `src/ue_agent_kit/layout_broker/planid.py`:

- imports only `graph_contracts.canonical` (`DOMAIN_LAYOUT_PLAN`, `short_digest`);
- no broker imports, therefore no import cycle with `protocol`, `server`, `client`, or `conformance`;
- `plan_id_from_result(result, *, spacing_x, spacing_y, target_width, seed)`:
  - requires `result.plan`, `positions`, `baseGraphHash`, `mode`;
  - constructs the exact projection used by `graph_contracts.layout:658-665`;
  - requires `plan.packing == {targetWidth, spacing: spacingX, algorithm: "skyline-v1"}`;
  - requires `plan.seed == request seed`;
  - excludes position `size` from the projection, matching `layout.py`;
  - returns `"layout:" + short_digest(projection, DOMAIN_LAYOUT_PLAN, 16)`.

`conformance.recompute_plan_id` now delegates to this helper (no second algorithm). `protocol.validate_plan_id`
wraps it with stable error handling and is used by both the broker and the client.

### 2.2 Broker self-check

`LayoutBrokerServer._run_and_reply` now:

1. validates the result shape/hash/node set as before;
2. calls `protocol.validate_plan_id(accepted, spacing_x=request.spacing_x, spacing_y=request.spacing_y,
   target_width=request.target_width, seed=request.seed)`;
3. only then builds/sends a success response.

A forged plan id is rejected **before** any success bytes are sent. The wire response remains the existing
bounded `native-preview-worker-failed` error (the specific reason is retained in
`server.status()["lastErrorCode"] = "native-preview-plan-id-mismatch"`). This preserves the established
fail-closed self-check behavior and does not add a new server-emitted wire code.

### 2.3 Actual Python client

`protocol.validate_response` (used by `LayoutBrokerClient.send`) now calls `validate_plan_id` with the original
`BrokerRequest` options after the existing result validation. A malicious broker that sends a success payload
with a forged `planId` is rejected locally with stable code `native-preview-plan-id-mismatch`; no result is
returned and no plan is accepted.

## 3. Why valid v1 bytes are unchanged

- The helper only compares an existing field; it does not modify a valid result, request, response, plan, or
  algorithm.
- All valid golden results still produce the same `planId`, `layoutHash`, and full JSON:
  - linear `layout:da31355db0366b21`
  - branch `layout:e893ed235881a163`
  - fork `layout:9c2b2a3b5bbc9b55`
- The three-fixture byte-equivalence test builds the direct `build_preview` result and the broker result and
  compares canonical JSON; no field changed.
- `layoutHash` remains the positions-only hash; `planId` adds the request-options/packing commitment.

## 4. Error code

`native-preview-plan-id-mismatch` is a **client-local / status** code:

- included in `protocol.CLIENT_ERROR_CODES` so client-side handling is stable;
- not emitted by the broker over the wire (self-check failures still map to `native-preview-worker-failed`);
- therefore the M2 response schema's wire error enum is unchanged.

## 5. Tests (actual server and actual client, not only conformance)

New `tests/python/test_layout_broker_plan_id_hardening.py`, 22 tests:

- shared helper matches all three golden `planId`s;
- helper commits to `spacingY`, `seed`, `targetWidth`/packing, and rejects incomplete plans;
- `protocol.validate_plan_id` rejects a forged well-formed plan id;
- **actual server** rejects a forged plan id and reports
  `lastErrorCode=native-preview-plan-id-mismatch`;
- **actual server** rejects a forged plan id **after positions and `layoutHash` are recomputed**;
- **actual client** rejects a malicious success payload with a forged plan id;
- **actual client** rejects the same after `layoutHash` is recomputed;
- actual client accepts a canonical result;
- three golden broker results are exactly equal to direct `build_preview`.

Existing M2 conformance test updated: the same forged response is now rejected by the actual M1 validator
(`native-preview-plan-id-mismatch`), not only by the conformance wrapper.

Combined offline broker tests: **140 passed** (97 M1 + 21 M2 + 22 M2.1). Full suite: **1381 tests, OK
(17 skipped), 129.1 s** (`Build/layout-broker/Evidence/full-python-suite-m2_1.log`).

## 6. Unchanged guards

The hardening does not touch:

- bad schema version / algorithm version rejection;
- wrong request id / project / session / context identity rejection;
- truncated, malformed, oversized, duplicate, replay, timeout, busy, cancellation, rate-limit paths;
- 1 MiB framing, token redaction, single in-flight job, 5 s total deadline;
- M1 full-only mode and the M2 v1.1 selection/incremental draft (still not accepted by the broker).

Existing tests for these paths remain green.

## 7. Limitations / runtime-unverified

- Offline Python only; no Editor, C++ client, FSocket, Ticker, receipt, menu, or UE automation.
- The C++ client must implement the same canonical projection (including `targetWidth`, `spacingX`,
  `spacingY`, `seed`, packing, and canonical hashing rules) before M2 runtime validation.
- M1 remains full-only; v1.1 scope remains a draft schema, not runtime code.
- No production cutover or fallback behavior is introduced.

## 8. Git / evidence

- Scoped local commit only, no push/merge/reset/clean/delete.
- Evidence: `Build/layout-broker/Evidence/m2_1-planid-probe.json`, test/probe logs, `SHA256SUMS.txt`.
- Stop after M2.1 offline; M2 runtime/C++ integration remains unauthorized.
