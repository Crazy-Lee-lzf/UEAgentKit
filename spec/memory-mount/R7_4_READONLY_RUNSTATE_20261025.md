# R7.4 Production Host Gate Review — Run State

- Task: R7 Memory Mount production-host gate audit / freeze candidate / minimal plan
- Model: DSH default `cline-pass/deepseek-v4.1-flash-1017` (non-Fast)
- Worktree: `E:\WorkSpace\UEAgentKit-WT\memory-mount-r7`
- Branch: `feature/memory-mount-r7`
- Start HEAD: `2bead479eeb66b2a0750ab973bbcd8beede66386`
- Baseline: `main` `c5ce3419c6962da49e874586f4e5dbd1457a4d0d`
- Start status: clean
- Start time: 2026-10-25T03:00:44+08:00
- Status: **audit complete / PASS; HOST_AUTH_PENDING; production activation BLOCKED**

## 1. Completed

| Item | State |
| --- | --- |
| R7/R7.1/R7.2/R7.3 reports and code audit | done |
| DSH Desktop Bridge source/docs identity audit | done |
| WorkspaceBridge source/docs identity audit | done |
| SessionId-not-credential finding | verified |
| Bridge shared-token / named-pipe DACL finding | verified |
| WorkspaceBridge unauthenticated MCP finding | verified |
| Gate table with source dependencies | done |
| Default-off host wiring proposal | done |
| Byte-parity caveat (handler vs MCP wire) | documented |
| Production source/data changes | none |
| UE/Computer Use | none |

## 2. Baseline tests (actual)

```text
R7 suite              133 PASS
Memory regression      90 PASS
--------------------------------
total                 223 PASS
```

Logs:

- `Build/r7-isolated/Evidence/R74/baseline-r7-133.log`
- `Build/r7-isolated/Evidence/R74/baseline-memory-regression.log`

No 5k/20k benchmark was rerun. No UE process was started.

## 3. Key findings

- DSH bridge authenticates a shared 256-bit bearer token over a named-pipe DACL for the
  current Windows user; it does not expose a per-agent principal, OS SID, or project key.
- `sessionId` is explicitly **not** authorization (`bridge.js:535-543`); only the
  Host-recorded `cwd` allowlist is checked (`authorizeSession:559-575`).
- Bridge session/task DTOs expose `sessionId`, `cwd`, `workspaceId/path/title`, `requestId`,
  task status, but no principal/project identity.
- WorkspaceBridge MCP has no transport authentication (`HealthMiddleware` only,
  `authorization_servers: []`); normal `wb_*` calls and task records carry no principal.
  Only the optional Events surface resolves `ctx.principal` or an explicit local_principal.
- R7's verified claims/TTL are test-only HMAC; no real issuer, replay cache, revocation,
  signature trust, or shared/PII/retention policy exists.
- R7 remains default-off and read-only; no production import or tool registration was added.

## 4. Data safety

No production database, token, user workspace, or private session content was read. The
coordination DB was not opened/queried/checkpointed/written; no sidecars were modified.

```text
coordination_memory.sqlite3 observed at close:
  BF544ECA04401588355E89D8727555F993E1129758CDCDA1A73B90AB7A86581E
  1,187,840 bytes
  mtime 2026-10-24T18:44:06Z
  sidecars: none observed
```

This differs from the R7.3 close observation (`B606A1…`, 1,175,552 bytes) and predates this
R7.4 turn (`19:00Z`). It is an external change. R7.4 does not claim the DB was unchanged; it
only claims R7.4 did not access it and did not modify/delete sidecars.

## 5. Files

New:

```text
spec/memory-mount/R7_4_PRODUCTION_HOST_GATE_REVIEW.md
spec/memory-mount/R7_4_READONLY_RUNSTATE_20261025.md
```

No source, test, schema, config, token, whitelist, or production data file changed.

## 6. Commit / next gate

- Local R7.4 docs commit: `__R74_COMMIT__`
- No push, merge, rebase, reset, clean, or cross-branch cherry-pick.
- Next gate is a **human decision** on real issuer/principal/session/project binding,
  replay/revocation/TTL, signed packs/trusted authors, PII/retention/shared visibility,
  tokenizer authority, MCP scope/rate/logging, and promotion. Do not enable production flags
  or add the proposed `ue_get_staged_context` tool before those decisions.
- Stop after R7.4. No R7.5, real trusted issuer, signing key, network sync, shared publication,
  or production activation was started.

## 7. Reproduce audit

```powershell
py -3.12 -m unittest discover -s tests/python -p "test_memory_mount*.py"
```

Read-only audit sources (no token/user DB):

```text
E:\WorkSpace\DSH-Desktop-Bridge\src\bridge.js
E:\WorkSpace\DSH-Desktop-Bridge\src\pipe-server.js
E:\WorkSpace\DSH-Desktop-Bridge\src\store.js
E:\WorkSpace\DSH-Desktop-Bridge\client\bridge_cli.py
E:\codex\workspace-bridge\src\workspace_bridge\server.py
E:\codex\workspace-bridge\src\workspace_bridge\tasks.py
E:\codex\workspace-bridge\src\workspace_bridge\config.py
E:\codex\workspace-bridge\src\workspace_bridge\events\integration.py
```
