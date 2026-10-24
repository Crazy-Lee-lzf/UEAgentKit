# M2.0 Layout Broker — Run State (2026-10-24)

- Status: **M2.0 offline preparation complete**; awaiting review before any M2 runtime/C++ work
- Branch: `feature/layout-broker-m1`
- Base for this increment: M1 tip `f8489e6`
- Worktree: `E:\WorkSpace\UEAgentKit-WT\layout-broker-m1`
- Remote: **no push, no merge, no reset, no clean, no delete**
- UE: **not opened, not compiled**; R1 continues to own the only UE 5.6 heavy build / Editor slot
- C++ plugin source: **not edited**; no menu, bridge, MCP, or production route changes
- Legacy P11 preview: **intact**; no runtime integration or cutover

## 1. Model/session

Runtime declares `cline-pass/deepseek-v4.1-flash-1017`; the request required non-Fast and no model switch.
No independently verifiable model id is exposed to the agent. No model switch was requested. Same DSH session
and worktree as M1.

## 2. Work completed

1. Audited the actual result against `build_preview` (38 top-level keys, 23 plan keys, 11 position keys) and the
   actual C++ snapshot builders:
   - `BlueprintGraphLayoutApply.cpp` `BuildPreflight` / `BuildFocusedEditorPreflight`,
   - `EditorBridgeGraphHandlers.cpp` `BuildBlueprintGraphLayoutPreflightResult` / `BuildP9MenuPreviewInputs`.
   Found the real envelope is ten fields (no envelope-level `sessionId`/`pluginBuildHash`) and that preflight
   carries those values; M1's harness envelope was a synthetic superset. M2 tests use the source-shaped envelope.
2. Corrected this branch's N0 `baseGraphHash` example from `sha1` to `sha256` with a review note.
3. Added four 2020-12 JSON Schemas under `spec/native-tools/m2/` (request, response, descriptor, draft 1.1).
4. Added `tests/fixtures/layout_broker_m2/golden-cases.json` with two original exporter cases and one derived case.
5. Added `src/ue_agent_kit/layout_broker/conformance.py` (additive; M1 production paths do not import it) with
   schema validation, size checks, `layoutHash` and **`planId`** recomputation.
6. Added 21 offline tests in `tests/python/test_layout_broker_m2_conformance.py` and source-shaped fixture helpers.
7. Added `tools/layout_broker_m2_probe.py`; it writes `m2-conformance-probe.json`.
8. Verified selection/incremental source behavior on the real branch fixture and wrote a draft v1.1 scope
   proposal without changing the M1 broker.
9. Wrote the future C++ integration checklist inside the M2 report (no C++ created).

## 3. Commands and results

| Command | Result |
|---|---|
| `py -3.12 -m unittest discover -s tests\python -p "test_layout_broker_*.py"` | **118 passed** (97 M1 + 21 M2), 0 failed (~11.9 s) |
| `py -3.12 tools\layout_broker_m2_probe.py` | `ok: true`, 10/10 checks, 3/3 exact golden |
| `py -3.12 scripts\RunPythonTests.py full --quiet` | **1359 tests, OK (skipped=17), 121.4 s** |
| `py -3.12 -m compileall -q ...` | exit 0 |
| JSON Schema meta-validation | all four schemas valid Draft 2020-12 |

Golden request/response sizes (actual wire, UTF-8 compact + newline; response size varies by a few bytes with durationMs):

| Case | Request bytes | Response bytes |
|---|---:|---:|
| linear | 9,919 | ~4,568 |
| p4_branch | 30,842 | ~8,339 |
| m1_fork | 12,635 | ~5,221 |

Projected 700 KiB context + 512 preflight nodes budget: 856,175 bytes (< 1,048,576). This is a Python
projection, not a UE runtime measurement.

## 4. Evidence (ignored `Build/layout-broker/Evidence/`)

| File | Content |
|---|---|
| `m2-conformance-probe.json` | 10/10 checks, three golden cases, byte sizes, known M1 planId gap, limitations |
| `full-python-suite-m2.log` | final 1359-test repository run |
| `m1-offline-probe.json` | unchanged M1 evidence (97 tests, 3 parity) |
| `descriptor-acl.txt` | M1 Windows DACL proof |
| `git-status.txt` | post-commit clean tree and HEAD |
| `SHA256SUMS.txt` | digests of the evidence files (regenerate after any run) |

## 5. OFFLINE-PROVEN

- Schema conformance of actual M1 requests/responses/descriptors, including exact expected `planId`,
  `layoutHash`, and `baseGraphHash` on the three golden cases.
- 1 MiB request/response bound and projected worst-case budget.
- Identity/session/revision/fingerprint/node-count/dirty/PIE drift fail-closed behavior.
- Invalid/forged response rejection, including `layoutHash` recomputation and the M2-only `planId` check.
- Source capability for selection/incremental scope; v1.1 draft schema; M1 still rejects 1.1.
- Descriptor schema/loopback/token-shape constraints.
- Known M1 gap: M1 accepts a forged but well-formatted `planId`; M2 conformance rejects it.

## 6. Runtime-unverified / not done

- No live P11 preflight/envelope capture in this worktree; the snapshot shape is source-audited and the
  `context` member is the real exporter output.
- No C++ client, FSocket I/O, game-thread snapshot, Ticker lifecycle, receipt, notification, menu, or feature
  flag implementation.
- No UE automation, P10/P11 regression, Editor session/project, descriptor DACL on a real Editor, or latency
  measurement.
- No v1.1 broker implementation; no fallback/cutover; no Apply.
- No production project (Reforge/UETest/Lyra golden) touched.

## 7. R1 dependencies / next gate

- R1 must release the only UE 5.6 build/Editor slot before any M2 runtime work.
- M2 C++ work needs a separate branch/review based on this offline baseline; it must not edit R1, the
  EditorBridge protocol, menu code, or production routes.
- M2.0 stops here. No M3 menu/fallback, no M4 default-on, no M5/M6, no push/merge.

## 8. Git state

Scoped local commit(s) on `feature/layout-broker-m1` (no push; see `git log -1`), containing the N0 correction,
M2 schemas, golden manifest, conformance module, fixture helper, tests, probe, report, and this runstate.
