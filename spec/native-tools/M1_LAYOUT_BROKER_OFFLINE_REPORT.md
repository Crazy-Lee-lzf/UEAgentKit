# M1 Layout Preview Broker — Offline Implementation Report

- Date: 2026-10-24
- Worktree: `E:\WorkSpace\UEAgentKit-WT\layout-broker-m1`
- Branch: `feature/layout-broker-m1`
- Base commit: `b423b87` (`main`-lineage; see `git log -1`)
- Scope: **M1 only** — offline Python broker + protocol/adversarial tests + offline client fixture
- Status: **OFFLINE-PROVEN** for the Python protocol/daemon as described below; **runtime-unverified** for every Editor/UE interaction
- Stop point: no M2/M3, no menu cutover, no Editor client, no production MCP route, no native registry, no Apply

## 0. Model/session condition

The session runtime context declares `cline-pass/deepseek-v4.1-flash-1017`; the task additionally required the
non-Fast variant and maximum reasoning. No environment variable, file, or API probe available to this agent
exposes a signed/verifiable model identifier (checked `DSH_*` environment; no model field is provided), so this is
recorded as a **runtime-declared** condition, not an independently verified one. No model switch was requested
during the work; the agent has no independent way to detect one.

## 1. Result summary

M1 delivers an opt-in, manual-start, loopback-only Python broker that:

1. listens on `127.0.0.1` with an OS-assigned ephemeral port (literal bind + `SO_EXCLUSIVEADDRUSE` on Windows);
2. speaks a versioned, one-line JSON protocol (`ueak.layoutbroker/1.0`) bounded to 1 MiB per direction;
3. verifies session token, project name, project path hash, Editor session id, and algorithm identity before work;
4. reuses the existing pure `graph_layout_preview.build_preview` function unchanged (no fork, no second algorithm);
5. enforces one in-flight job, request-id replay rejection, bounded per-session rate limiting, and a hard 5 s ceiling;
6. publishes an atomically written descriptor whose Windows DACL is hardened and **proven** current-user-only;
7. fails closed on malformed/oversized/unknown-field/preview-error input and survives malicious clients;
8. never includes the session token in logs, status, errors, or responses;
9. ships an offline client fixture that independently re-validates every response, including canonical
   `layoutHash` recomputation and node-set equality;
10. changes **none** of the existing P8/P9/P11 menu, `Build.cs`, `EditorBridge`, or MCP tool routes.

Parity with the existing preview is exact on three canonical exporter fixtures: the complete result objects are
canonically JSON-equal (same `planId`, `layoutHash`, `movedNodeIds`, positions, plan and metadata), not merely
field spot-checks.

## 2. Files delivered

New production code (all new; no existing module edited):

| Path | Purpose |
|---|---|
| `src/ue_agent_kit/layout_broker/__init__.py` | package surface; import has no side effects |
| `src/ue_agent_kit/layout_broker/protocol.py` | schema constants, strict request/response validation, canonical result checks, redaction, project-path hash |
| `src/ue_agent_kit/layout_broker/security.py` | Windows DACL harden/verify via `ctypes`/`advapi32` (no subprocess), POSIX fallback |
| `src/ue_agent_kit/layout_broker/descriptor.py` | descriptor build/validate, atomic publish, own-token-only removal, client-side read |
| `src/ue_agent_kit/layout_broker/server.py` | single-process loopback daemon, framing, dedup, rate limit, deadline, self-check |
| `src/ue_agent_kit/layout_broker/client.py` | offline client fixture/harness (not wired to Editor or MCP) |
| `tools/run_layout_broker.py` | the only manual start entry point (explicit CLI, no autostart) |
| `tools/layout_broker_offline_probe.py` | explicit offline evidence runner (parity + negative checks) |

New tests/fixtures:

| Path | Tests |
|---|---|
| `tests/python/layout_broker_harness.py` | fixture wrapper + raw-socket helpers (not a test module) |
| `tests/python/test_layout_broker_protocol.py` | 47 unit tests: request/response/descriptor/framing/redaction + static isolation guards |
| `tests/python/test_layout_broker_server.py` | 34 socket tests: parity, identity, dedup, size, timeout, cancellation, busy, rate limit, self-check, descriptor lifecycle |
| `tests/python/test_layout_broker_client.py` | 13 malicious-broker tests |
| `tests/python/test_layout_broker_cli.py` | 1 real subprocess CLI smoke test |
| `tests/fixtures/graph_contracts/bpctx_export_m1_fork.json` | third parity fixture (derived offline from `bpctx_export_linear.json`: adds one forked terminal node; see §7) |

Untouched (verified by `git status` showing only untracked new files): `Plugin/**`, `src/ue_agent_kit/editor_bridge.py`,
`src/ue_agent_kit/graph_layout_preview.py`, `mcp_graph_layout_preview.py`, all P8/P9/P10/P11 sources and tests,
`tests/python/test_mcp_server.py`, `tools/p8_layout_preview_worker.py`, `tools/p9_layout_preview_worker.py`.

## 3. Protocol as implemented

### 3.1 Framing

- TCP on `127.0.0.1:<ephemeral>`; no UDP, no host option, no other interface.
- One request line, one response line, UTF-8 JSON, `\n` terminated.
- The 5 s ceiling is **total per connection**: time spent reading the line plus time spent running the job
  (default and hard maximum 5 s; a request that spends 4.9 s being read gets only 0.1 s of job time).
- Request read stops at 1 MiB (`MAX_MESSAGE_BYTES`); a longer line is rejected with
  `native-preview-request-too-large` and the connection is closed. The broker never buffers more than the cap.
- Response encoding is size-checked **before** send; an oversized result becomes a bounded
  `ok=false` error, never a truncated line.
- No pipelining: the server reads exactly one line per connection and closes after one response.

### 3.2 Request (strict, closed schema)

Required top-level keys: `schemaVersion`, `requestId`, `sessionId`, `projectName`, `projectPathHash`, `token`,
`sentAtUtc`, `deadlineMs`, `algorithm`, `options`, `snapshot`. Unknown keys are rejected
(`native-preview-unsupported-field`), including exact execution-shaped names (`path`, `command`, `exec`,
`shell`, `script`, `url`, `args`, `cwd`, `env`, `process`, `spawn`, …).

- `schemaVersion` must equal `ueak.layoutbroker/1.0`.
- `requestId` must be a canonical lowercase UUID; it is single-use per broker process.
- `token` must be 32–128 lowercase hex and is compared with `hmac.compare_digest`; it is **not** stored in the
  validated request object.
- `deadlineMs` is an integer 100–5000; values above 5 s are rejected.
- `algorithm` must be exactly `{"id":"p0-layered","version":"1.0.0"}`.
- `options` accepts exactly `mode, spacingX, spacingY, targetWidth, seed` with the same bounds as the existing
  MCP tool (`1..4096`, `256..16384`, int32 seed) and **`mode="full"` only** in M1.
- `snapshot` accepts exactly `preflight, contextEnvelope`. Both must be JSON objects; `preflight.assetPath` must
  be a virtual content path beginning with `/`; session ids in preflight/envelope must equal the request session;
  no path/command keys are accepted at these levels.

`projectName`/`projectPathHash` must equal the broker's configured identity. `projectPathHash` is computed with
the same normalization as `EditorBridge.cpp:85-99` (`FPaths::ConvertRelativePathToFull` → forward slashes →
lowercase → SHA-1), matching `editor_bridge.py:65-68`.

### 3.3 Response

Success: `schemaVersion`, `requestId`, `ok=true`, `readOnly=true`, `algorithm`, `durationMs`, `result`.
`result` is the complete, unmodified `build_preview` return object (37 keys); both broker and client validate:

- exact result key set (no unknown keys);
- `schemaVersion == ueak.graphlayoutpreview/1.0`, `readOnly is true`, no write/save/dirty flags;
- `assetPath`/`graphGuid`/`sessionId`/`nodeCount` identity vs the request snapshot;
- unique node ids, exact position shape, integer coordinates within ±10,000,000, consistent deltas;
- `movedCount == len(movedNodeIds)`, unique and a subset of result nodes;
- `plan.positions` covers the same node set and agrees with `result.positions`;
- `layoutHash` matches `recompute_layout_hash(plan)` (canonical re-computation);
- the session token does not appear anywhere in the serialized result.

Failure: `schemaVersion`, `requestId`, `ok=false`, `error{code,message,retryable}`. Messages are bounded to 400
characters, control characters removed, the token replaced, and local absolute/UNC paths replaced with `<path>`.
When a request cannot yield a valid UUID, `requestId` is `null` (the only deviation from “response must carry a
requestId”; there is nothing trustworthy to echo).

### 3.4 Stable error codes

| Condition | Code |
|---|---|
| token missing/malformed/mismatch | `native-preview-token-rejected` |
| descriptor/protocol/algorithm version mismatch | `native-preview-version-unsupported` |
| project name/hash mismatch | `native-preview-project-mismatch` |
| session id not the bound Editor session, or snapshot session mismatch | `native-preview-identity-mismatch` |
| unknown/execution-shaped field | `native-preview-unsupported-field` |
| missing/wrong-type/out-of-bounds/naive timestamp | `native-preview-request-invalid` |
| request line > 1 MiB | `native-preview-request-too-large` |
| repeated `requestId` | `native-preview-duplicate-request` |
| a timed-out job still occupies the single slot | `native-preview-busy` |
| > rate limit per session per 60 s | `native-preview-rate-limited` |
| read or job deadline exceeded | `native-preview-timeout` |
| client disappeared before the response could be sent | recorded as cancelled (no response) |
| preview exception, self-check failure, or response serialization failure | `native-preview-worker-failed` |
| client-side only: descriptor missing/invalid, broker unavailable, response invalid/too large, plan hash/shape mismatch | same `native-preview-*` namespace |

`native-preview-busy` is named in the N0 contract (§6.3 P4) but missing from the §4.4 list; M1 adds it, plus
`-duplicate-request`, `-unsupported-field`, `-request-invalid`, `-rate-limited`. The Editor-side client is
expected to map them into its own stable UI codes.

## 4. Descriptor and Windows ACL

`build_descriptor()` emits exactly the fields in N0 §4.1: `schemaVersion`, `address`, `port`, `token`,
`projectName`, `projectPathHash`, `brokerVersion`, `supportedContractVersions`, `algorithms`, `startedAtUtc`,
`processId`, `maxConcurrentJobs=1`. `address` is always `127.0.0.1`; the token is 32 random bytes from
`secrets.token_hex` (64 hex chars); the descriptor is rejected by clients when older than 12 h.

Publishing:

1. the target directory is created if needed;
2. a temp file is created in the same directory (`mkstemp`), written, flushed, and `fsync`ed;
3. its DACL is replaced with a **protected** DACL granting `FILE_ALL_ACCESS` only to the current process user
   (`SetEntriesInAclW` + `SetNamedSecurityInfoW` with `PROTECTED_DACL_SECURITY_INFORMATION`), using `ctypes`
   — no `icacls`, no PowerShell, no subprocess;
4. `os.replace(temp, target)` publishes atomically;
5. the final file is re-read (`GetNamedSecurityInfoW` + `GetSecurityDescriptorControl` + ACE walk) and accepted
   as **PROVEN** only when: not a directory/reparse point, DACL present and protected, every ACE is
   `ACCESS_ALLOWED`, no inheritance flags, and the sole ACE SID is the current user.

On this host the result is `{"state":"PROVEN","mechanism":"windows-dacl",
"detail":"protected-dacl-current-user-only"}`. Removal on shutdown re-reads the file and deletes it only when
its token still equals this broker's token (`hmac.compare_digest`).

If hardening or verification cannot prove the DACL, the descriptor is deleted and startup fails closed. The
explicit `--allow-unverified-acl` flag permits startup in **UNVERIFIED** state for a disposable host only; the
status line then reports `UNVERIFIED`. Limitation: the descriptor *directory* ACL is inherited (not hardened);
only the token-bearing file is proven.

## 5. Security model (implemented controls)

| Threat | Control | Evidence |
|---|---|---|
| remote/non-loopback access | literal `127.0.0.1` bind, post-bind address check, `SO_EXCLUSIVEADDRUSE` | source guard test + socket tests |
| port hijack/duplicate listener | `SO_EXCLUSIVEADDRUSE` (Windows), no `SO_REUSEADDR` | source |
| token guessing | 256-bit random token, constant-time compare, 1 MiB frame | unit tests |
| token leakage to logs/status/errors | structured outputs exclude it; sanitizer; tests capture logs | `test_token_never_appears_in_logs_or_responses` |
| token leakage in descriptor | DACL proven current-user-only; UNVERIFIED requires explicit disposable-host opt-in | `test_descriptor_is_written_with_proven_permissions…` |
| replay | single-use requestId, bounded 1024-entry history, duplicate refused without cached plan | `test_duplicate_request_id_is_rejected…` |
| request flooding | 10 valid requests / session / 60 s, bounded 256 session buckets | `test_rate_limit_rejects_the_third…` |
| concurrent work | one executor worker + busy refusal while a timed-out job runs | `test_slow_job_times_out_and_then_blocks…` |
| oversized request/response | 1 MiB read cap, 1 MiB encode cap; oversize response becomes error | `test_oversized_request…`, `test_oversized_result…` |
| malformed/truncated JSON | fail closed with stable code; connection closed; process survives | `test_malformed_json…`, `test_truncated…` |
| unknown/execution-shaped fields | closed schema at every broker-owned level + forbidden exact key names | protocol + server tests |
| malicious/forged plan | broker self-check + independent client re-validation + hash recomputation + node-set check | `ResultSelfCheckTests`, client adversarial tests |
| slow/hung pure job | deadline wait, timeout response, slot stays busy until the thread returns | timeout/busy test |
| client cancellation | EOF/reset recorded, no response, broker continues | cancellation tests |

Not provided (by design for M1): TLS, OS-level sandboxing of the broker process, signed descriptors, protection
against a same-user malicious process (the trust boundary is the Windows user account, same as the existing
Editor Bridge), or protection against a compromised Python interpreter.

## 6. Reuse of the existing algorithm (no fork)

The server calls the imported `build_preview` directly:

```python
self._preview_fn(request.preflight, request.context_envelope,
                 mode=request.mode, spacing_x=..., spacing_y=...,
                 target_width=..., seed=...)
```

`preview_fn` exists only as a test seam for slow/tampered/failing jobs; the default is the real function and
production CLI never overrides it. `git status` confirms `graph_layout_preview.py`, `graph_contracts/**`, and
the old workers are unmodified. M1 does not support `selection`/`incremental` because the N0 request schema has
no scope field; those remain on the legacy path until M2/M3 review.

## 7. Parity evidence

The offline probe and tests compare the broker result with a direct `build_preview` call on the same inputs.
All three comparisons are exact under canonical JSON (`json.dumps(..., sort_keys=True)` equality), not just
field spot-checks:

| Fixture | Nodes | Moved | planId | layoutHash (prefix) | Exact |
|---|---:|---:|---|---|---|
| `bpctx_export_linear.json` (real exporter fixture, includes a comment) | 4 | 3 | `layout:da31355db0366b21` | `sha256:e40d1dfdd3d91550…` | yes |
| `bpctx_export_p4_branch.json` (real exporter fixture) | 10 | 10 | `layout:e893ed235881a163` | `sha256:6002739f1afedd45…` | yes |
| `bpctx_export_m1_fork.json` (derived offline: linear + one forked terminal node, valid canonical document) | 5 | 5 | `layout:9c2b2a3b5bbc9b55` | `sha256:91b399fdf5f7ea02…` | yes |

The third fixture is explicitly **derived**, not a fourth independent UE export; the repository contains only
two canonical `bpctx_export_*` fixtures. It was validated by the same `typed_graph_from_blueprint_context` +
`build_preview` path before use. Full values and hashes are in
`Build/layout-broker/Evidence/m1-offline-probe.json`.

## 8. Test results

Executed on this host with Python 3.12.10 (the repository requires 3.11–3.12; the machine's default `python`
is 3.9, so all commands used `py -3.12`):

| Suite | Result |
|---|---|
| `test_layout_broker_protocol.py` | 48 passed |
| `test_layout_broker_server.py` | 35 passed |
| `test_layout_broker_client.py` | 13 passed |
| `test_layout_broker_cli.py` | 1 passed (real subprocess, descriptor publish + request + auto-exit cleanup) |
| M1 total | **97 passed**, 0 failed (~10.1 s) |
| Repository full suite | see `Build/layout-broker/Evidence/full-python-suite.log`; **1338 tests, OK (17 skipped), 129.8 s** |

The 35 server tests cover: 3 parity fixtures; descriptor/loopback/ACL; log token absence; bad token/version/
project/hash/session/algorithm; duplicate id; unknown/command fields; invalid options; malformed/empty/truncated
JSON; oversized request; slow sender; rate limit; slow-job timeout then busy then recovery; partial-close and
TCP-reset cancellation; broker self-check rejection of tampered/oversized results; sanitized error messages;
atomic write failure preserving the previous file; UNVERIFIED ACL fail-closed/opt-in; changed-token descriptor
not deleted; missing/invalid descriptor.

Static guards assert the package never imports `subprocess`, calls `os.system`/`os.popen`/`Popen`/
`CreateProcess`, imports `editor_bridge`, contains `0.0.0.0`, connects out from the server, or opens files in
the request path (`server.py`, `protocol.py`, `client.py`).

## 9. What is OFFLINE-PROVEN vs runtime-unverified

**OFFLINE-PROVEN**

- Protocol framing, validation, error codes, dedup, rate limit, single-inflight/busy, deadline, cancellation
  accounting, self-check, sanitization.
- Exact parity with `build_preview` on the three fixtures.
- Descriptor atomic publish + Windows DACL proof on this host + own-token-only removal.
- Manual CLI start/stop, ephemeral loopback port, no autostart, no subprocess from the broker.
- Client rejection of forged/malformed/oversized plans.

**Runtime-unverified (not done, no claim)**

- Any connection from the Unreal Editor (EditorBridge has no reverse request path; no Editor-side client exists).
- P11/P8/P9 menu behavior, `CreateProc` avoidance at runtime, non-modal notifications, receipts.
- P10/P11 regression, package-dirty/revision/fingerprint re-checks after the result, UE automation.
- Real Editor session id, real project path hash, real descriptor location under `<Project>/Saved/UEAgentKit`.
- Performance/latency for large graphs (100+ nodes) and whether a 5 s deadline is sufficient.
- Multi-user/shared-host behavior; descriptor-directory ACL; token rotation; Windows Defender/EDR interaction.
- M2 shadow snapshot parity, M3 opt-in menu with legacy fallback, M4 default-on, M5/M6 retirement.

No UE Editor was started, no UE build was run, and no Computer Use/UI automation was used.

## 10. Deviations / decisions requiring review

1. **`baseGraphHash` is `sha256:`** in the actual `build_preview` result, while N0 §4.3’s sample shows `sha1:`.
   M1 validates what the implementation produces (`sha256:<64 hex>`); the Editor must not hard-code the sample.
2. **M1 is `mode=full` only.** The request schema has no scope field; supporting selection/incremental requires
   a protocol addition and a parity decision (M2/M3).
3. **Session binding requires `--expect-session-id`.** The descriptor has no session field by contract, so the
   broker cannot infer the Editor session; the CLI now requires the operator to supply it, and preflight/envelope
   sessions must match. The Python server API keeps it optional for embedding/tests, but any production start
   must pass it.
4. **New broker error codes** (`-duplicate-request`, `-unsupported-field`, `-request-invalid`, `-rate-limited`,
   `-busy`) extend the N0 §4.4 list; Editor-side mapping is an M2/M3 task.
5. **Descriptor path is explicit CLI input.** The N0 production path remains
   `<Project>/Saved/UEAgentKit/LayoutBroker.json`; M1 does not write under a project by default.
6. **A timed-out pure job cannot be interrupted.** The slot remains busy until the Python call returns; the
   process is disposable. This is honest fail-closed behavior, not preemption.
7. **Dedup history is bounded to 1024 request ids per process**; a replay older than the window is not detected.
   Rate limiting is per session id, not per token or process.
8. **No receipt is written.** Receipts are an Editor-side artifact (N0 §7.2); M1 has no Editor integration and
   therefore produces none. The token is structurally excluded from status/errors/logs.
9. **The descriptor directory ACL is inherited**; only the file is hardened/verified. On a shared host the
   parent directory could expose the file name, not its contents; this is part of the disposable-host limitation.
10. **The third parity fixture is derived**, not an independent UE export (only two canonical fixtures exist).

## 11. How to run (manual, disposable host)

```powershell
py -3.12 tools/run_layout_broker.py `
  --descriptor Build\layout-broker\Evidence\LayoutBroker.json `
  --project-name M1FixtureHost `
  --project-path C:\Projects\M1FixtureHost\M1FixtureHost.uproject `
  --expect-session-id m1-session
```

- The CLI prints one JSON status line (no token, no descriptor path) and serves until Ctrl+C / SIGTERM
  (graceful stop removes its own descriptor) or `--run-seconds N`.
- `--allow-unverified-acl` is the only way to run without a proven DACL; never use it on a shared host.
- Offline evidence run (starts and stops its own broker):

```powershell
py -3.12 tools\layout_broker_offline_probe.py
```

Nothing in the package starts a listener on import; there is no MCP registration, no Editor hook, no Windows
service, no scheduled task, and no menu entry.

## 12. Evidence inventory and runstate

Evidence (ignored by git, under `Build/layout-broker/Evidence/`):

| File | Content |
|---|---|
| `m1-offline-probe.json` | 13 checks (all true), 3 exact-parity records with planId/layoutHash, environment, limitations |
| `full-python-suite.log` | repository full-suite output for this change set |
| `git-status.txt` | post-commit clean tree with branch/commit ids |
| `descriptor-acl.txt` | `icacls` view of a hardened sample descriptor + internal verification state (sample deleted after capture) |

Runstate is in `spec/native-tools/M1_LAYOUT_BROKER_RUN_STATE_20261024.md` with commands, results, evidence
hashes, and the exact stop point.

## 13. Next gates (not started)

- **M2**: Editor-side snapshot client + shadow comparison against legacy preflight/context data, descriptor
  read/verify in C++, receipt keys, feature flags; requires UE runtime and a separate review.
- **M3**: menu opt-in with legacy fallback, async receipt/notification, P11/P10 regression parity.
- Before any cutover: Z1–Z6 evidence, receipt key preservation, dirty/PIE/truncated negatives, rollback drill,
  and the §9.3 cutover criteria. M1 does not authorize any of this.

**M1 stops here pending review.**
