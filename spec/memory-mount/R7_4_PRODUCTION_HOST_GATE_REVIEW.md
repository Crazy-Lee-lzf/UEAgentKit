# R7.4 Production Host Gate Review — Read-only Audit

- Date: 2026-10-25 (Asia/Hong_Kong)
- Model: DSH default `cline-pass/deepseek-v4.1-flash-1017` (non-Fast)
- Worktree: `E:\WorkSpace\UEAgentKit-WT\memory-mount-r7`
- Branch: `feature/memory-mount-r7`
- Start HEAD: `2bead479eeb66b2a0750ab973bbcd8beede66386`
- Baseline: `main` `c5ce3419c6962da49e874586f4e5dbd1457a4d0d`
- Mode: **audit and planning only**; no UE Editor/UBT/UBA, no Computer Use, no
  production MCP, no production Memory, no token/user-database read.

## 1. Status

| Item | Status |
| --- | --- |
| R7.4 read-only security-gate audit | **PASS (audit complete)** |
| R7.1–R7.3 behavior/regression baseline | **VERIFIED: 133 R7 + 90 Memory = 223 PASS** |
| Default-off staging and read-only properties | **VERIFIED** |
| Real host issuer/principal/session/project root | **HOST_AUTH_PENDING** |
| Signed pack / trusted author | **BLOCKED USER DECISION** |
| Shared knowledge visibility / PII / retention | **BLOCKED USER DECISION** |
| Production MCP/DSH activation | **BLOCKED — not attempted** |

**R7.4 is not a production integration and does not change any feature flag.** The only
artifacts added are this report and its runstate. No source file was modified.

## 2. Evidence scope and limits

Reviewed read-only:

- R7/R7.1/R7.2/R7.3 reports, `memory_mount/{staging,context,adapter,claims,service,integrity}.py`,
  `task_context.py`, `memory_service.py`, `memory_injection.py`, `query_protocol.py`;
- R7 tests, including the actual-service fixture/harness;
- DSH Desktop Bridge source/docs (`src/bridge,pipe-server,store,policy,index`, `client/bridge_cli.py`,
  `docs/PERMISSIONS.md`, `docs/COMPATIBILITY.md`);
- WorkspaceBridge source/docs (`server.py`, `tasks.py`, `config.py`, events identity code,
  connection docs).

Not read and not opened:

- `bridge-token` contents, `.credentials.yaml`, DSH session/transcript files, user workspace
  files, `coordination_memory.sqlite3`, Project Memory data, or any production database.

No token was copied, no real identity was minted, no signature key was created, no network
request was made, and no production process was started/restarted.

At R7.4 close the coordination DB was observed as
`2F5DB0FBB49E5ED886DFA792EC860E30151D41457828DC98E3906269D4869CCE`
(1,204,224 bytes, mtime `2026-10-24T19:10:40Z`). This differs from the prior R7.3 observation
(`B606A1…`); the mtime is after this R7.4 start (`19:00Z`), so an external writer changed it
during the audit. R7.4 did not open/query/write the DB; no sidecars existed at the close
observation.

## 3. Baseline verification

```text
R7 suite (49 base + 27 readiness + 22 context + 23 staging + 12 actual service)
  Ran 133 tests — OK
Memory regression (8 files)
  Ran 90 tests — OK
Total: 223 PASS
```

Logs: `Build/r7-isolated/Evidence/R74/baseline-r7-133.log` and
`baseline-memory-regression.log`. This confirms the R7.3 report's 223 PASS claim locally;
no 5k/20k benchmark was rerun.

## 4. Identity/session facts found

### 4.1 DSH Desktop Bridge

- Source checkout declares `VERSION = "0.4.0"` (`src/index.js:24`); the running Host was not
  probed, restarted, or reconfigured during this audit.
- The bridge transport is a Windows named pipe plus a **shared 256-bit bearer token**:
  `src/pipe-server.js:30-66` creates the pipe, `:68-94` checks `safeEqual(request.token, token)`,
  and `src/store.js:19-41` reads/creates one token file (`mode 0o600`). Any process running as
  the same Windows user that can read that file can use the bridge. There is no per-caller
  token, principal, subject, or session credential.
- The pipe's OS DACL is the transport boundary: current user and local administrators
  (`src/pipe-server.js:8-11`, `docs/COMPATIBILITY.md` §4.3). The bridge `health` payload
  (`src/bridge.js:865-882`) exposes versions, `hostInstanceId`, pid, policy paths, task counts,
  and capabilities — **no OS user SID, principal, or project key**.
- `src/bridge.js:535-543` explicitly says: *"Passing a sessionId is NOT authorization."*
  `findSession` (`:545-556`) and `authorizeSession` (`:559-575`) only require the Host-recorded
  `cwd` to be inside the bridge allowlist. Permission level never widens this decision.
- Session metadata returned by the bridge:
  - `sessions.list` summary (`:841-858`): `sessionId`, title projection, `cwd`,
    `workspaceId`, `workspacePath`, `workspaceTitle`, `workspaceless`, timestamps, running/
    blank/agentAvailable/agentPreset.
  - `sessions.get` (`:985-1006`): the above plus `access`, projections, queue.
  - `workspaces.list` (`:1305-1316`): `workspaceId`, path, title, sessionCount.
  There is **no projectKey**, no principal, and no authenticated owner in these DTOs.
- Task metadata (`publicTask`, `:122-141`) is `taskId`, `requestId`, `sessionId`,
  `workspacePath`, status, timestamps, turn, cursor, error, tool count, retryable. It has no
  caller identity. `tasks.submit` (`:1453-1538`) accepts caller `requestId`; it is idempotency,
  not authentication. `workspacePath` for an existing session is taken from the Host
  (`:1476-1481`), not trusted from the caller.
- The CLI reads the same shared token (`client/bridge_cli.py:65-84`) and sends `token` in
  every request; it does not send a principal.

**Conclusion:** DSH Bridge proves "same Windows user / allowlisted workspace", not "agent A vs
agent B", not "project X", and not "session S belongs to principal P". `sessionId` is a
resource selector, never a credential.

### 4.2 WorkspaceBridge MCP

- MCP transport has **no authentication middleware**: `server.py:991-1035` wraps the MCP
  app only with `HealthMiddleware`; `/.well-known/oauth-protected-resource/mcp` advertises
  `authorization_servers: []` (`:1011-1015`). It listens on localhost by default.
- `call_tool` creates a server-side `request_id = req_<uuid>` (`:935-951`) and calls
  `dispatch(request_id, name, args)`. `dispatch` (`:221-238`) has no principal argument.
- Task records (`tasks.py:159-177`) contain `task_id`, `task_type`, `operation`, `source_id`,
  `request_id`, status/timestamps, `metadata`; no principal. `_task_metadata` (`server.py:722-731`)
  copies root/cwd/path/workflow/etc, still no principal.
- Workspace root allowlists and `denied_workspaces` are path authorization, not caller identity.
- The only principal-aware surface is the optional Events integration:
  `events/integration.py:329-349` accepts `ctx.principal`, `ctx.session.principal`, or
  `ctx.request_context.principal`, and only as an explicit opt-in falls back to
  `local_principal` (`config.py:105-108`; default empty, `allow_unauthenticated_local=false`).
  This does not authenticate normal `wb_*` MCP tool calls, and `local_principal` is explicitly
  "operator-chosen constant, never a production default".
- Events are disabled by default (`config.py:93-96`).

**Conclusion:** WorkspaceBridge provides task IDs, request IDs and workspace paths, but no
reliable caller principal, session identity, or project identity for MCP operations. A
localhost unauthenticated MCP port is not an identity root.

## 5. R7-side facts

- Verified identity is a test-only HMAC contract (`claims.py:66-124`,
  `HmacClaimVerifier:152-292`): issuer, audience, `iat`/`exp`, principal, session, project,
  `claimId`; no real issuer, replay cache, revocation, or transport binding.
- `ReadOnlyMountAdapter` requires `VerifiedCallerContext`, confines pack names, and checks
  `packId/revision/contentHash/manifestDigest` pins (`staging.py:79-112`, `:327-...`).
- Default-off `StagedContextHandoff.build` returns the exact caller-supplied legacy bytes when
  disabled/no adapter/no pins/no mounted packs/empty query (`staging.py:327-...`,
  `HandoffResult`).
- Combined budget is enforced across local + mounted envelope; `chars-div-4` is the token
  approximation (`context.py:26`, `staging.py:114-...`). Production Task Context uses
  `estimate_json_tokens = serialized JSON bytes / 4` (`query_protocol.py:62-64`) and
  `_finalize_budget` (`task_context.py:1820-1842`). The two approximations are not the same;
  a host integration must define one authority at the wire boundary.
- Technical precedence is project > personal > shared (`service.py` scope tiers), local
  authority shadows mounted records; actual-service tests verify this.

## 6. Gate table

Legend: **VERIFIED** = locally proven in code/tests; **DESIGN ONLY** = interface/plan exists,
no production implementation; **BLOCKED USER DECISION** = human policy/trust decision required;
**HOST_AUTH_PENDING** = the host trust root is not proven.

| Gate | Candidate requirement | Status | Evidence / dependency | Local verify vs human |
| --- | --- | --- | --- | --- |
| G1 | Transport identity root | **BLOCKED USER DECISION** | DSH pipe DACL + shared token `pipe-server.js:30-94`, `store.js:19-41`; WorkspaceBridge no auth `server.py:991-1035`, `:1011-1015` | Gap verified; issuer choice human |
| G2 | Issuer + principal/session/project strong binding | **BLOCKED USER DECISION** | `bridge.js:535-575`; session DTO `:841-858`, `:985-1006`; no principal/project field; `claims.py:66-124` only verifier contract | Human must define issuer/key and binding |
| G3 | Session ID must not be a credential | **VERIFIED** | `bridge.js:535-543`; `authorizeSession:559-575`; `tasks.submit:1476-1481` | Local source/tests |
| G4 | Workspace→project mapping | **DESIGN ONLY** | `workspaceIndex:824-838`; `workspaces.list:1305-1316`; no projectKey; Task Context has `project.projectKey` (`task_context.py`) | Human decides mapping/config |
| G5 | Replay, revocation, TTL | **DESIGN ONLY** | HMAC `exp` checks `claims.py`; bridge token no expiry `store.js`; `requestId` idempotency only `bridge.js:1460-1462`; no replay cache/revocation | Human/issuer infrastructure |
| G6 | Signed pack / trusted author / revocation | **BLOCKED USER DECISION** | `integrity.py` accepts digest-only; non-null signature fails `SIGNATURE_UNSUPPORTED`; author is `not-established` | Key/trust policy human |
| G7 | Shared visibility + PII + retention | **BLOCKED USER DECISION** | Pack `scope` is self-asserted; no PII scan/retention; Bridge task retention only (`store.js:99-127`), not pack/context | Human policy |
| G8a | Technical precedence/dedupe/local-shadow | **VERIFIED** | `service.py:21,575-591` scope tiers; `staging.py` local authority keys; R7.2/R7.3 tests | Local tests |
| G8b | Final precedence/permission policy | **BLOCKED USER DECISION** | Whether project always dominates personal; who approves exceptions | Human |
| G9a | Current combined budget enforcement | **VERIFIED** | `context.py:26,182-261`; `staging.py:114-...`; `query_protocol.py:62-64`; byte/token caps; combined local+mounted | Local tests |
| G9b | Real tokenizer vs `chars-div-4` | **BLOCKED USER DECISION** | R7 `chars-div-4` vs `query_protocol.estimate_json_tokens` JSON-bytes/4; no model tokenizer | Human selects authority |
| G10 | MCP/DSH tool scope, rate, logging | **DESIGN ONLY** | Existing `ue_get_task_context` `task_context.py:1845-1879`; no new tool; no auth/scope/rate/log implementation | Human approves endpoint/scope |
| G11 | Promote/write explicit authorization | **BLOCKED USER DECISION** | R7 is read-only; `mcp_memory_tools.py` write tools are separate; no promotion API/confirmation | Human |
| G12 | Default-off exact legacy parity | **VERIFIED** | `StagedContextHandoff` disabled path; R7.2/R7.3 tests; 133 rerun | Local tests |
| G13 | Pack/DB read-only + no writes | **VERIFIED** | pack hash checks, `query_only`, no DM/bridge writes; R7.3 actual-service harness | Local tests |
| G14 | End-to-end host trust | **HOST_AUTH_PENDING** | No trusted issuer/principal source; see G1/G2 | Cannot be closed by R7 alone |

## 7. Which items can be closed locally, which cannot

**Locally verifiable now:**

- Session ID is not authorization; cwd allowlist is the only session gate (G3).
- DSH bridge has a shared token and no principal/project fields (G1 evidence).
- WorkspaceBridge MCP has no transport authentication; task records have no principal (G1/G2).
- R7 project>personal>shared and local shadow behavior (G8a).
- Combined byte/token caps, pin mismatch, default-off parity, read-only pack behavior (G9a,
  G12, G13).
- The actual-service fixture/harness exercises `TaskContextService.get_task_context`,
  `ProjectMemoryService.get_injection_context`, and `IndexQueryService` against disposable
  synthetic SQLite.

**Requires a human decision / external trust root:**

- A real issuer with signing keys and a trusted `ClaimVerifier` implementation.
- Binding OS user/agent → principal → session → project, including revocation/replay.
- Signed pack receipt/trusted-author keys.
- Shared knowledge visibility, PII/redaction, retention, and cross-team publication.
- Token budget/tokenizer authority, MCP tool exposure/scope/rate/logging.
- Promotion/write and user confirmation.
- Whether `project` always outranks `personal` in product policy.

## 8. Strict default-off host wiring proposal (not implemented)

This is a minimal plan, not authorization to write production code.

### 8.1 Feature/config

```text
[r7]
memory_mount_enabled = false          # default; no tool registration when false
memory_mount_require_receipt = true
memory_mount_pins = []                # packId/revision/contentHash/manifestDigest, host-frozen
memory_mount_max_output_bytes = 24576
memory_mount_max_approx_tokens = 6000
memory_mount_claim_audience = "<host-defined>"
```

With the flag false, `ue_get_task_context` and all existing tools remain byte-for-byte
unchanged; no `memory_mount` import is required. A separate **new** MCP tool is preferred over
changing the existing tool schema.

### 8.2 Endpoint

Proposed tool: `ue_get_staged_context` (read-only; new registration only behind the flag).

Inputs: `query`, optional `asset_paths` (≤10), optional `max_output_tokens` (bounded by host),
optional `include_conflicts` (default false). It must not accept `principal`, `sessionId`,
`projectKey`, `packPath`, or a pack list from the caller; those are host/transport data.

Handler data flow:

```text
MCP transport
  -> host authentication (ClaimVerifier) -> VerifiedCallerContext
  -> TaskContextService.get_task_context(...)              (actual read-only)
  -> ProjectMemoryService.get_injection_context(...)      (actual read-only)
  -> legacy_bytes = canonical_json_bytes(task_context)
  -> pins = frozen host config; adapter = ReadOnlyMountAdapter(verified caller, pinned pack root)
  -> StagedContextHandoff(enabled=flag, pinned_packs=pins).build(
         local_context=task_context, local_memory_snapshot=snapshot,
         request=AgentContextRequest(...), legacy_serialized=legacy_bytes)
  -> inactive: return exactly legacy_bytes at the raw response boundary
  -> active: return the versioned handoff DTO with combined budget
```

The host must not derive identity from `sessionId`, `workspacePath`, `requestId`, the bridge
token, or any MCP argument. If no `VerifiedCallerContext` is available, the handler must fail
closed (no legacy fallback, no pack access).

### 8.3 Byte parity caveat

The exact byte parity is proven at the **handler/serializer boundary** (R7.2/R7.3 use canonical
JSON bytes). The MCP SDK may re-serialize a returned dict, so exact MCP wire-byte parity is
**DESIGN ONLY/BLOCKED** unless the host provides a raw-response adapter that writes the
unchanged `legacy_bytes`. A tool returning a dict can preserve the logical tree but not
guarantee wire bytes. The host must test the actual transport serializer before freezing G12.

### 8.4 Verification plan

1. Feature off, no verified claims, no pins, no mounted packs, empty query: raw response equals
   the legacy task-context bytes exactly.
2. Missing/expired/demo claim: typed auth error; no legacy fallback; no pack access.
3. Session/project mismatch: reject; `sessionId` is only a selector after auth.
4. Pin revision/hash/manifest mismatch: reject before projection.
5. Pack files and DB SHA-256 before/after; no SQLite writes; `PRAGMA query_only`.
6. Combined byte/token cap at the raw envelope; mounted items trimmed, local authority not
   replaced.
7. Logs contain no pack text, claims, session secrets, or absolute paths.
8. All R7 133 + 90 regression tests remain green; actual-service synthetic fixture remains green.

## 9. Next minimal changes and approval gate

Recommended order after user approval (none implemented in R7.4):

1. **Decision record** for issuer/audience/principal/session/project binding, replay/revocation,
   TTL, and who owns the signing keys.
2. Host `ClaimVerifier` implementation (outside `memory_mount` default import) with OS/agent
   identity proof and a trusted broker; no bridge-token-as-identity shortcut.
3. Host config flag + frozen pin list, default false; no tool registered when off.
4. New `ue_get_staged_context` registration behind the flag; existing `ue_get_task_context`
   untouched.
5. Raw-response adapter for exact legacy bytes; otherwise explicitly accept logical JSON parity.
6. Canary tests against disposable synthetic DB/pack, then a read-only canary with one project;
   no promotion/shared write.
7. Signed pack and PII/retention policies before any shared knowledge.

Approval thresholds: G1–G7, G8b, G9b, G10, G11 human-approved; G3/G8a/G9a/G12/G13 remain
code-verified. **Do not freeze "production ready" while G14 is HOST_AUTH_PENDING.**

## 10. Risks

- Shared bridge token means any same-user process can act as the bridge caller; per-agent
  isolation is not present.
- Session ID/cwd/workspace are resource selectors, not identity; path mapping can be ambiguous.
- WorkspaceBridge MCP is unauthenticated on localhost; tunnel/port exposure would widen risk.
- Bridge `requestId` is idempotency, not replay protection; no revocation.
- Pack labels (`untrusted-source-content`) are not prompt sanitization; consumer must enforce
  quoting/capability boundaries in code.
- Two token approximations (`chars-div-4` vs JSON bytes/4) can cause inconsistent budgets.
- MCP SDK reserialization prevents claiming exact wire-byte parity without a raw adapter.
- PII/retention/shared visibility and signed-author trust remain unspecified.

## 11. Stop

R7.4 ends at this read-only gate review. Do not start R7.5, do not wire the tool, do not create a
real issuer/signature key, do not expose packs through MCP, do not promote or share knowledge,
and do not change default flags. Production activation requires the human decisions above.
