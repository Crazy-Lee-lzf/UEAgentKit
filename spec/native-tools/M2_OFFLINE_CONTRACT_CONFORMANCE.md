# M2.0 Layout Broker — Offline Contract Conformance and Editor Integration Preparation

- Date: 2026-10-24
- Worktree: `E:\WorkSpace\UEAgentKit-WT\layout-broker-m1`
- Branch: `feature/layout-broker-m1`, based on M1 tip `f8489e6`
- Status: **M2.0 OFFLINE-PROVEN** for schema/conformance tooling and source-audited contract; **runtime-unverified** for every Editor/UE interaction
- Scope: offline audit, schemas, golden fixtures, validator/tests, scope-extension analysis, C++ checklist
- Not authorized / not done: UE Editor, UE compile, C++ plugin edits, menu integration, production cutover, push/merge/reset/clean/delete

## 0. Model/session condition and R1 lane

The runtime context declares `cline-pass/deepseek-v4.1-flash-1017`; the request required the non-Fast variant and no
model switch. No independent verifiable model id is exposed to the agent, so this is a runtime-declared condition.
No model switch was requested. The R1 lane continues to own the only UE 5.6 heavy build / Editor slot; this work
used Python only and did not open UE, compile UE, or touch any other worktree.

## 1. What this M2.0 change contains

1. A **source audit** of the wire contract against:
   - the actual `build_preview` return object (`graph_layout_preview.py`),
   - the actual canonical exporter fixtures (two original, one explicitly derived),
   - the actual C++ snapshot builders `BuildPreflight`, `BuildFocusedEditorPreflight`,
     `BuildBlueprintGraphLayoutPreflightResult`, and `BuildP9MenuPreviewInputs`.
2. Four machine-readable JSON Schema documents (2020-12) for request, response, descriptor, and a **draft** 1.1
   scope extension.
3. A deterministic golden-case manifest derived from the real exporter fixtures and the real algorithm.
4. An additive Python conformance module (`src/ue_agent_kit/layout_broker/conformance.py`) plus 21 offline tests
   covering canonical hash/planId, size bounds, identity/session/revision/fingerprint drift, invalid hashes,
   missing/extra fields, forged responses, descriptor shape, and the scope proposal.
5. An explicit offline adversarial probe (`tools/layout_broker_m2_probe.py`) that writes evidence under
   `Build/layout-broker/Evidence/m2-conformance-probe.json`.
6. A correction to this branch's N0 doc copy: `baseGraphHash` is `sha256:<64>`, not `sha1:<40>`.
7. A future C++ integration checklist in §8. **No C++ was written.**

## 2. Source audit: what was actually verified

### 2.1 Method and limits

- Read-only inspection of `EditorBridgeGraphHandlers.cpp`, `BlueprintGraphLayoutApply.cpp`,
  `graph_layout_preview.py`, `graph_contracts/layout.py`, and the canonical `bpctx_export_*.json` fixtures.
- **No live P11 preflight/envelope capture exists in this worktree.** The M1 harness envelope was itself a
  synthetic superset (it added `sessionId`/`pluginBuildHash` at the envelope level). M2 therefore uses a
  source-shaped envelope that mirrors `BuildP9MenuPreviewInputs` exactly, and a preflight that carries the
  real `BuildPreflight` + transport fields. The `context` member is the **actual canonical exporter output**,
  not invented data. This distinction is explicit in the golden manifest and probe limitations.
- The third context fixture (`bpctx_export_m1_fork.json`) is derived from the linear fixture (one forked
  terminal node); the other two are original exporter fixtures.

### 2.2 Actual P9/P11 envelope (`BuildP9MenuPreviewInputs`)

Exactly ten top-level fields, no `sessionId`/`pluginBuildHash` at this level:

| Field | JSON type | Notes |
|---|---|---|
| `schemaVersion` | string | const `ueak.graphlayoutcontext/1.0` |
| `available` | bool | true on success |
| `readOnly` | bool | true |
| `assetPath` | string | `/Game/...` object path |
| `graphGuid` | string | lowercase hyphenated GUID |
| `packageRevision` | string | `sha256:<64>` package revision |
| `packageDirty` | bool | false for preview |
| `graphFingerprint` | string | `sha1:<40>` |
| `exportedAtUtc` | string | ISO-8601 UTC |
| `context` | object | canonical exporter document |

The envelope does **not** carry `sessionId`/`pluginBuildHash`; the P9 builder copies those into the
**preflight** from the context response. M1 code correctly treats envelope session as optional; M2 schema pins
the ten actual fields.

### 2.3 Actual preflight (`BuildPreflight` + transport)

| Field | JSON type | Notes |
|---|---|---|
| `schemaVersion` | string | `ueak.graphlayoutpreflight/1.0` |
| `assetPath` | string | `/Game/...` |
| `graphGuid` | string | lowercase GUID |
| `graphClassPath` | string | class path of the graph object |
| `schemaClassPath` | string | must contain `EdGraphSchema_K2` for preview |
| `packageRevision` | string | `sha256:<64>` |
| `packageDirty` | bool | must be false |
| `graphFingerprint` | string | `sha1:<40>` |
| `graphFingerprintAlgorithm` | string | `sha1-length-prefixed-graph-state-v1` |
| `evidenceLevel` | string | `same-process` |
| `acceptanceEligible` | bool | false |
| `requiredAcceptanceEvidence` | string | `independent-reload` |
| `nodeCount` | integer | full graph node count |
| `commentCount` | integer | |
| `rerouteCount` | integer | |
| `nodes` | array (≤512) | objects with `nodeId`, `guid`, `classPath`, `kind`, `x`, `y`, `width`, `height`, `inputPinCount`, `outputPinCount`, `inputLinkCount`, `outputLinkCount`, optional `knot{inputType,outputType,inputSources[],outputSinks[]}` |
| `source` | string | `focused-blueprint-editor` |
| `readOnly` | bool | true |
| `editorName` | string | `BlueprintEditor` |
| `blueprintPath` | string | `/Game/...` |
| `graphName` | string | |
| `editable` | bool | drives `applyEligible` in the result |
| `lastActivationTime` | number | double; not used by the algorithm |
| `totalNodeCount` | integer | |
| `nodesReturned` | integer | |
| `nodesTruncated` | bool | must be false |
| `complete` | bool | must be true |
| `available` | bool | must be true |
| `selectionPolicy` | string | `most-recently-active-blueprint-editor` |
| `focusScope` | string | `toolkit-graph-tab` |
| `sessionId` | string | Editor Bridge session; must equal request session |
| `pluginVersion` | string | |
| `projectName` | string | Editor project name |
| `pieState` | enum string | `stopped` required |
| `pluginBuildHash` | string | `sha256:<64>` or 16 hex; **optional in C++ but required by `build_preview` and M2 schema** |

Consequences:

- If the C++ binary hash cannot be computed, `BuildBlueprintGraphLayoutPreflightResult` omits
  `pluginBuildHash`; `build_preview` then fails. The M2 client must reject this snapshot locally
  (`native-preview-worker-failed` / no plan) rather than send it.
- `preflight.nodes` is transport metadata; the algorithm uses the canonical `context`. A 512-node truncation
  already implies `complete=false`, and the P9 builder refuses to export context, so a valid request never
  contains a truncated preflight.

### 2.4 Actual `build_preview` result (38 keys)

| Field | JSON type | Notes |
|---|---|---|
| `schemaVersion` | string | `ueak.graphlayoutpreview/1.0` |
| `readOnly` | bool | true |
| `source` | string | `focused-blueprint-editor` |
| `selectionPolicy` | string | |
| `focusScope` | string | |
| `assetPath`, `graphGuid`, `graphName` | string | identity |
| `packageRevision` | string | |
| `packageDirty` | bool | false |
| `graphFingerprint` | string | `sha1:<40>` |
| `sessionId` | string | |
| `pluginBuildHash` | string | copied from preflight |
| `editable` | bool | |
| `mode` | string | `full` for 1.0 |
| `scopeNodeIds` | array[string] | empty for full |
| `planId` | string | `layout:<16 hex>` |
| `layoutHash` | string | `sha256:<64>` |
| `baseGraphHash` | string | **`sha256:<64>`** (graph semantic hash) |
| `nodeCount`, `pinCount`, `edgeCount`, `commentCount` | integer | |
| `movedCount` | integer | |
| `movedNodeIds` | array[string] | |
| `positions` | array[11-field object] | see below |
| `metrics` | object | `crossings`, `edgeLength`, `overlapCount`, `area` ints; `aspectRatio` number; `bbox{width,height}` ints |
| `warnings` | array | often empty; may carry diagnostics |
| `islands` | array[object] | `islandId`, `nodeIds[]`, `bbox{x,y,width,height}` |
| `sccs` | array[array[string]] | empty in the three fixtures |
| `plan` | object | 23 fields, see below |
| `applyEligible` | bool | `editable` |
| `evidenceLevel` | string | `same-process` |
| `acceptanceEligible` | bool | false |
| `requiredAcceptanceEvidence` | string | `independent-reload` |
| `openAssets`, `wroteBlueprint`, `savedPackage` | bool | all false |

`positions[]` exact fields: `nodeId`, `nodeGuid`, `kind`, `beforeX`, `beforeY`, `afterX`, `afterY`, `deltaX`,
`deltaY`, `width`, `height` — all coordinates/sizes integer; `delta == after - before`.

`plan` keys (23): `schemaVersion` (`ueak.layoutplan/1.0`), `planId`, `baseGraphHash`, `mode`, `seed`,
`scopeNodeIds`, `fixedNodeIds`, `packing{targetWidth,spacing,algorithm:"skyline-v1"}`, `positions[]`
(`nodeId`, `x`, `y`, optional `size{width,height,source}`), `movedNodeIds`, `layoutHash`,
`sizeBeforeHash`, `sizeAfterHash`, `status:"ok"`, `errorCode:""`, `metrics`, `warnings`, `islands`, `sccs`,
`timingMs`, `evidenceLevel`, `acceptanceEligible`, `requiredAcceptanceEvidence`.

`baseGraphHash` correction: the N0 §4.3 sample previously showed `sha1:`; the actual value is
`sha256:<64>`. This branch's N0 copy is corrected with a review note, and all M2 schemas pin `sha256`.

### 2.5 C++ serialization / deserialization requirements

**Writer (Editor → broker)**

1. One compact UTF-8 JSON object + one `\n`, no BOM, no pretty-printing, no trailing data.
2. Integral fields (`deadlineMs`, coordinates, counts, `seed`) must be emitted as JSON integers (`5000`, not
   `5000.0`). Python's `json` parses `5000.0` as float and M1 rejects it. Add a pre-send golden check.
3. Booleans must be JSON booleans, not 0/1 or strings.
4. Hashes and GUIDs lowercase; `layoutHash`/`baseGraphHash` `sha256:<64>`; `graphFingerprint` `sha1:<40>`.
5. No `NaN`, `Infinity`, or duplicate object keys.
6. Encoded size ≤ 1 MiB including the newline. The C++ client must check the final encoded line, not an
   estimate. Actual context is C++-bounded at 700 KiB and preflight nodes at 512 entries; the measured
   projection in §5 is 856,175 bytes, leaving ~168 KiB headroom.
7. No token, local path, or command is ever accepted from a request field; the request carries only identity,
   options, and the snapshot.

**Reader (broker → Editor)**

1. Bound the read to 1 MiB + one line; reject truncated/multi-line input.
2. Parse numbers as numbers; require zero fractional part for integer fields before casting.
3. Validate exact field sets, identity, session, node set, `layoutHash`, and `planId`.
4. `planId` **must be recomputed** by the C++ client using the request options; M2.1 added the same
   check to the Python broker/client. Projection:
   `{baseGraphHash, mode, positions[{nodeId,x,y}], options{spacingX,spacingY,seed}, packing}` using the
   canonical JSON/hash rules already required by N0 §11.1.
5. `durationMs`/`timingMs` are telemetry only; never use them in hashes or as plan evidence.
6. A broker error never gets its raw message shown to the user; keep the stable code and a bounded own
   description; sanitize before receipt.

## 3. Machine-readable schemas and golden manifest

| File | Purpose |
|---|---|
| `spec/native-tools/m2/layoutbroker-request-1.0.schema.json` | closed request schema; raw snapshot members keep additional properties |
| `spec/native-tools/m2/layoutbroker-response-1.0.schema.json` | success/error union; 38-key result, 23-key plan, 11-key positions |
| `spec/native-tools/m2/layoutbroker-descriptor-1.0.schema.json` | exact descriptor fields, loopback const, token/project hash patterns |
| `spec/native-tools/m2/layoutbroker-request-1.1-draft.schema.json` | **draft only** optional scope extension; not accepted by M1 |
| `tests/fixtures/layout_broker_m2/golden-cases.json` | three cases (two original, one derived) with expected `planId`, `layoutHash`, `baseGraphHash`, counts |

The schemas are 2020-12 and are meta-validated with `Draft202012Validator.check_schema`. They cannot express
token equality, project identity, total byte size, or canonical hashes; the conformance module does.

`src/ue_agent_kit/layout_broker/conformance.py` (additive, not imported by M1 production paths) provides:

- `schema_errors(document, schema_name)` using `jsonschema` (optional dev dependency; import of the module is safe
  without it);
- `encoded_size` / `check_message_size` (1 MiB NDJSON);
- `recompute_plan_id(result, spacing_x, spacing_y, target_width, seed)` — delegates to the shared
  `planid.py` helper using the exact `layout.py:658-665` projection;
- `validate_request_conformance`, `validate_response_conformance`, `validate_preview_result_conformance`;
- `validate_descriptor_conformance`.

## 4. Is the contract actually followed? Measured conformance

The M2 probe starts one loopback broker and drives the actual golden requests/responses:

| Case | Origin | Request bytes | Response bytes | `planId` | Result |
|---|---|---:|---:|---|---|
| linear | original exporter | 9,919 | ~4,568 | `layout:da31355db0366b21` | schema + semantic pass, exact golden |
| p4_branch | original exporter | 30,842 | ~8,339 | `layout:e893ed235881a163` | schema + semantic pass, exact golden |
| m1_fork | derived from linear | 12,635 | ~5,221 | `layout:9c2b2a3b5bbc9b55` | schema + semantic pass, exact golden |

Projected worst case (700 KiB context + 512 preflight node entries + envelope/request overhead): **856,175
bytes**, below 1,048,576. This is a Python projection, not a UE runtime measurement.

## 5. M1 full-only limitation vs P11 selection/incremental

Actual source behavior was verified directly:

- `build_preview(mode="selection"|"incremental", scope_node_ids=[...])` is supported by the existing pure
  function; scope nodes are validated against the graph, unknown scope is rejected, outside nodes stay fixed,
  and `plan.scopeNodeIds`/`fixedNodeIds` are populated.
- On the real `bpctx_export_p4_branch` fixture, scope
  `n:c45aac7b-4d1e-d6df-866e-e1985e0ce6e0` moves exactly 1 node, keeps 9 outside nodes at delta 0, and
  produces `plan.mode=selection` (same for `incremental`).
- M1's wire intentionally accepts `mode="full"` only because the N0 1.0 request schema has no scope field.

Proposed **1.1 extension** (draft schema, not implemented in the broker):

```json
"schemaVersion": "ueak.layoutbroker/1.1",
"options": {
  "mode": "selection",
  "scopeNodeIds": ["n:<guid>", "..."],
  "spacingX": 96, "spacingY": 64, "targetWidth": 1800, "seed": 0
}
```

Rules: `scopeNodeIds` required iff `mode != full`, 1–4096 canonical `n:<guid>` strings; the broker passes them
as `scope_node_ids` to the same `build_preview`; response echoes the request version; 1.0 documents remain
byte-for-byte valid and M1 behavior is untouched.

Why no broker code in M2.0: accepting 1.1 inside `LayoutBrokerServer` would change the production acceptance
surface and the descriptor's `supportedContractVersions` while runtime integration is not authorized. The
extension is therefore proposed with a draft schema and source-capability tests; it requires a separate review
and an opt-in implementation (ideally `enable_scope_extension=False` by default) in M2/M3.

**Broker unavailability**: M1/M2 do not fall back. A missing/invalid broker yields a stable error and no plan.
Legacy P11 fallback belongs only to the later opt-in migration (M3 `LegacyFallback=true`); M5+ may remove it.
No production cutover is implied or performed.

## 6. planId gap found by this audit — fixed in M2.1

M2.0 found that M1's response validator recomputed `layoutHash` but **did not recompute `planId`**. A forged
response with a validly formatted but wrong `planId` passed `protocol.validate_response`. M2.1 adds the shared
canonical helper and enforces it in both the actual broker self-check and the actual Python client; see
`M2_1_PLAN_ID_HARDENING_REPORT.md`. The C++ client must implement the same projection with the request options
(including `spacingY`, `targetWidth` and `packing`, which are not fully present in the result).

## 7. Test and probe evidence (offline only)

| Run | Result |
|---|---|
| M1 tests (unchanged) | 97 passed |
| M2 conformance tests | 21 passed |
| M2.1 planId hardening tests | 22 passed (actual server + actual client rejection) |
| Broker tests combined | 140 passed (~12.4 s) |
| M2 offline probe | 10/10 checks, 3/3 exact golden |
| Full repository suite | **1359 tests, OK (17 skipped), 121.4 s** (see `full-python-suite-m2.log`) |

M2 tests cover: schema meta-validity; actual ten-field envelope shape; golden parity; schema + semantic
request/response conformance; descriptor schema; 18 structural request violations; response shape violations;
forged `planId`/`layoutHash`/token echo/wrong request id; identity/revision/fingerprint/node-count/dirty/PIE
drift; session drift; 1 MiB size and projected budget; draft 1.1 acceptance vs M1 rejection; scope source
capability.

They do **not** cover UE automation, Editor sockets, C++ serialization, ticket/lifecycle behavior, or receipts.

## 8. C++ integration implementation checklist (future M2; no code written)

### 8.1 Preconditions / ownership

- [ ] R1 has released the only UE 5.6 heavy build/Editor slot; no concurrent heavy UE task.
- [ ] Separate C++ branch/worktree from the reviewed M2.0 doc/schema baseline; do not edit `EditorBridge`
      protocol/port/token/capability code.
- [ ] P11 legacy preview remains enabled and unchanged; `NativeLayoutPreview=off` initially.
- [ ] Disposable host only; no production projects (Reforge/UETest/Lyra golden); no menu cutover.

### 8.2 New components (names illustrative; no existing file edits)

- [ ] `FUEAgentKitLayoutBrokerDescriptor`: read `<Project>/Saved/UEAgentKit/LayoutBroker.json`; reject missing,
      non-regular, reparse point/link, address != `127.0.0.1`, port outside 1–65535, token not 32–128 lowercase
      hex, projectName/projectPathHash mismatch, unsupported contract/algorithm, age > 12 h or future > 5 min.
- [ ] DACL check: prove the descriptor file is current-user-only (protected DACL, single allow ACE, no
      inheritance); if not provable, refuse and report `native-preview-broker-descriptor-invalid`. Never copy
      the token into logs, receipts, notifications, crash reports, or telemetry.
- [ ] `FUEAgentKitLayoutBrokerClient` (`FRunnable`, worker thread): `FSocket` connect to loopback only, no
      Editor/UObject access, no file writes, no Python, no `CreateProc`; send one compact UTF-8 NDJSON line;
      receive one line with a 1 MiB cap; total deadline ≤5 s; cancellation flag closes the socket; never
      block the game thread.
- [ ] `FUEAgentKitNativePreviewService` (game thread + `FTSTicker`): snapshot → serialize → hand off → poll
      completion → validate → receipt/notification; single in-flight job; terminal state always written.
- [ ] Menu entry remains out of scope until M3; no `ToolMenus` change in M2.

### 8.3 Game-thread snapshot

- [ ] Reuse `BuildBlueprintGraphLayoutPreflightResult` and `BuildP9MenuPreviewInputs` (or their exact builders);
      do not introduce a second context format or re-serialize nodes differently.
- [ ] Copy `sessionId`/`pluginBuildHash` into preflight exactly as P9 does; reject if `pluginBuildHash` is empty
      (Python `build_preview` requires it).
- [ ] Require `available`, `complete`, `nodesTruncated=false`, `packageDirty=false`, `pieState=stopped`,
      `schemaClassPath` contains `EdGraphSchema_K2`, `editable` as needed; resolve the exact focused graph once.
- [ ] Record snapshot identity: assetPath, graphGuid, graphName, packageRevision, graphFingerprint, nodeCount,
      context `schemaVersion`, sessionId, pluginBuildHash. No file writes, no transaction, no save, no compile.
- [ ] Encode request to compact UTF-8; verify exact byte length ≤1 MiB before spawning/worker send; reject with
      `native-preview-request-too-large` locally if over.
- [ ] Keep request options (spacingX/spacingY/targetWidth/seed/mode/scope) until validation; `planId` needs them.

### 8.4 Worker I/O

- [ ] Connect `127.0.0.1:<descriptor.port>` only; no hostname resolution, no IPv6, no external interface.
- [ ] Send `schemaVersion: ueak.layoutbroker/1.0`, fresh UUID requestId, sessionId, project identity, token,
      UTC timestamp, deadlineMs 100–5000, algorithm, options, snapshot.
- [ ] Read exactly one line, cap 1 MiB; reject extra lines/trailing bytes, truncated data, non-UTF-8.
- [ ] On timeout/cancel, close the socket and mark the job terminal; do not leave a ghost job. Do not reuse a
      requestId after a timeout (broker dedup will reject it).
- [ ] Single in-flight: a second request returns `native-preview-busy` until the first job returns; the worker
      thread/state must not be re-entered.

### 8.5 Completion / validation (game thread)

- [ ] Ticker observes completion without blocking; state machine: Idle → Snapshotting → Queued → Connecting →
      Sending → Awaiting → Validating → Completed/Failed/Cancelled.
- [ ] Validate response `schemaVersion`, `requestId`, `ok`, `readOnly`, `algorithm`, `durationMs`.
- [ ] Validate exact result keys and plan shape; recompute `layoutHash` (canonical) and `planId` (with request
      options); require node set equality, identity equality, moved/node counts, coordinate bounds.
- [ ] Re-check current packageRevision and graphFingerprint after the result; any drift → terminal
      `native-preview-revision-conflict`/`-fingerprint-conflict`, no plan displayed, no planId/layoutHash kept
      as valid evidence.
- [ ] Never apply, save, compile, transact, or open assets from a preview result; zero writes is a hard
      invariant, including error paths.
- [ ] Receipt: preserve P11 keys (`ok`, `planProduced`, `readOnly`, `saved`, `compiled`, dirty before/after,
      context kind/GUID/pin, assetPath/graphGuid/packageRevision/graphFingerprint, planId/layoutHash/counts)
      and add `workerMode`, `previewPath`, `requestId`, `brokerVersion`, `algorithm`, `stale`, `errorCode`.
      Token/descriptor path/raw broker diagnostics must not appear; sanitize any broker message.
- [ ] Non-modal notification only; headless test seam may suppress it; receipt is the required evidence.

### 8.6 Feature flags / rollback

- [ ] `NativeToolRegistry=off` remains non-fatal; missing/broken registry must not block legacy preview.
- [ ] `NativeLayoutPreview=off` by default; test-only command-line override only; MCP parameters cannot set it.
- [ ] `LegacyLayoutPreviewFallback=true` during M3/M4; broker failure falls back to legacy only there, never in
      M1/M2 offline code.
- [ ] Rollback: flag off → next request uses legacy within one restart; receipt `previewPath` correct; no
      descriptor/token residue; no menu residue.
- [ ] Cutover gates: Z1–Z6, exact `planId`/`layoutHash`/`movedNodeIds` parity on ≥3 graphs, P10/P11 regression
      not reduced, all negatives stable, rollback drill, user approval. No cutover in M2.

### 8.7 Exact identity / path / token checklist

- [ ] Use the Editor's existing `ProjectPathHash` (same normalization as `EditorBridge.cpp:85-99`); never
      recompute with a different separator/case rule.
- [ ] Descriptor projectName/projectPathHash must equal the running project; a mismatch is terminal.
- [ ] Request `sessionId` must equal the Editor Bridge `SessionId` and the preflight session; envelope
      `sessionId` is absent by contract (do not invent one).
- [ ] Token comes only from the descriptor; never from argv, config, logs, receipts, or user input.
- [ ] Descriptor path must be the exact `<Project>/Saved/UEAgentKit/LayoutBroker.json`; do not search broadly
      or follow links.
- [ ] Error messages to users/receipts use stable codes and bounded descriptions only; raw broker/exception
      text is discarded or sanitized.

### 8.8 Test plan for the future C++ work (runtime, not this lane)

- [ ] Golden wire conformance against the M2 schemas + `conformance.py` before enabling any menu path.
- [ ] Serialization golden: integer fields without `.0`; 1 MiB rejection; UTF-8/CRLF/BOM/multi-line negatives.
- [ ] Broker down/timeout/cancel/busy/duplicate; descriptor missing/stale/wrong-project/weak-DACL.
- [ ] Forged response: wrong requestId/planId/layoutHash/node set/identity; token echo.
- [ ] Zero-write assertions: dirty/revision/undo/transaction/save/compile unchanged; graph fingerprint same.
- [ ] P10/P11 regression and legacy fallback drill; no UE automation is claimed by M2.0.

## 9. R1 integration dependencies and stop point

- R1 owns the only UE 5.6 build/Editor slot; M2 runtime work cannot start until R1 releases it and a separate
  review authorizes the C++ branch.
- R1's native registry/actionId work is not required for the preview wire; it only changes future action
  descriptors. No R1 file/worktree is touched here.
- P11 legacy preview and its receipts remain intact; M2 adds offline artifacts only.
- **Stop at M2.0.** No M2 runtime, no M3 menu/fallback, no M4 default-on, no M5/M6, no push/merge.

## 10. Evidence inventory

`Build/layout-broker/Evidence/` (git-ignored):

| File | Content |
|---|---|
| `m2-conformance-probe.json` | 10/10 checks, three golden cases with byte sizes/planIds, offline/R1 limitations |
| `m1-offline-probe.json` | unchanged M1 probe (97-test baseline) |
| `full-python-suite-m2.log` | final repository run after M2 additions (1359 tests) |
| `git-status.txt` / `SHA256SUMS.txt` | post-commit state and digests |

No Editor, UE build, UI automation, production route, or other worktree was used.
