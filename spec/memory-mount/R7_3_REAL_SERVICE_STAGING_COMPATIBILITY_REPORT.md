# R7.3 Actual-Service Read-only Staging Compatibility — Report

- Date: 2026-10-24 (Asia/Hong_Kong)
- Model: DSH default `cline-pass/deepseek-v4.1-flash-1017` (non-Fast)
- Worktree: `E:\WorkSpace\UEAgentKit-WT\memory-mount-r7`
- Branch: `feature/memory-mount-r7`
- Start HEAD: `f6d08c0ab340b603da4e4b5b1705e9118bd9a040`
- Baseline: `main` `c5ce3419c6962da49e874586f4e5dbd1457a4d0d`
- Mode: pure offline Python/SQLite; no UE Editor/UBT/UBA/ShaderCompileWorker; no Computer Use.

## 1. Status and stage gate

| Item | Status |
| --- | --- |
| Actual `TaskContextService` + `ProjectMemoryService` + `IndexQueryService` compatibility | **PASS** |
| Default-off exact legacy bytes from actual task-context serialization | **PASS** |
| Active local-authority + mounted-untrusted staging | **PASS** |
| Actual index search and short-Chinese mounted retrieval | **PASS** |
| Combined budget / pin / trust labels / cross-project failures | **PASS** |
| Real host authentication / replay / revocation | **HOST_AUTH_PENDING** |
| Signed pack receipt / trusted author | **BLOCKED** (user decision) |
| Production MCP/DSH exposure, promotion, shared writes | **BLOCKED** (not attempted) |

**This is not production MCP/DSH integration.** Identity remains the local test HMAC
(`TestClaimIssuer` / `HmacClaimVerifier`). No trusted issuer, signature, PII policy,
promotion, retention, or production mount was introduced.

## 2. Actual services used

The R7.3 harness builds disposable synthetic SQLite databases and calls the real classes,
not fake output shapes:

```text
ue_agent_kit.indexer.build_index          -> real SQLite index/FTS fixture
ue_agent_kit.agent_api.IndexQueryService  -> actual asset/symbol search
ue_agent_kit.memory_service.ProjectMemoryService
  .add_record / .build_context
  .get_injection_context                  -> actual read-only L2/L3 snapshot
ue_agent_kit.task_context.TaskContextService
  .get_task_context                       -> actual task-context DTO
ue_agent_kit.memory_mount.ReadOnlyMountAdapter / AgentContextProjector / StagedContextHandoff
```

Fixtures are synthetic only:

- index export/assets: `test_indexer_queries.make_asset` + `write_export` + real `build_index`;
- project key: `测试项目` (actual test project key), no production path;
- Memory: new disposable `memory.sqlite3` with schema v6, one local `projectRule`
  (`rule.local`), no production Memory DB;
- pack: synthetic `r73.*` pack with test-only receipt and records;
- database locations: temp dirs in tests, ignored `Build/r7-isolated/Evidence/R73` in the
  executable harness.

No `coordination_memory.sqlite3`, backup, real Project Memory, DSH token, or Session whitelist
was opened.

## 3. Default-off parity

The host's actual task-context payload is serialized with the canonical JSON encoder. With
`StagedContextHandoff(enabled=False)`, the result is exactly those bytes:

```text
result.active == false
result.reason == "feature-disabled"
result.serialized == canonical_json_bytes(host.task_context)
```

Tested against the actual `TaskContextService.get_task_context` output, not a fabricated local
DTO. The test uses `==` on bytes; no re-serialization or normalization occurs in inactive mode.

## 4. Active staging result

Active path uses the actual local task context and actual memory injection snapshot, plus the
test-only verified adapter and pinned synthetic pack:

```text
sections[0] local-authority    actual TaskContextService + ProjectMemoryService snapshot
sections[1] mounted-knowledge  R7.1 projection of synthetic mounted pack
```

Observed harness summary:

```json
{
  "actualServices": {
    "taskContextTool": "ue_get_task_context",
    "projectKey": "测试项目",
    "relevantAssets": 1,
    "memoryAvailable": true,
    "memorySnapshotId": "ctxsnap_ec06d172e5672b7d483ca7dc8cd8d297",
    "memoryTextChars": 38
  },
  "defaultOffByteParity": true,
  "disabledReason": "feature-disabled",
  "active": true,
  "activeReason": "active",
  "activeSections": ["local-authority", "mounted-knowledge"],
  "mountedRecordCount": 1,
  "shadowedMountedItems": 1,
  "outputBytes": 3795,
  "approxTokens": 18,
  "localRulePresent": true,
  "packLocalOverridePresent": false
}
```

The synthetic mounted pack has priority 100 and a record with the same `subjectKey`
(`rule.local`) as the actual local project rule. The local key shadows it; the other mounted
record (`rule.mounted`) remains. The serialized output contains the actual local rule and does
not contain the pack's local-override sentinel.

## 5. Scenario matrix (12 targeted actual-service tests)

| # | Scenario | Result |
| ---: | --- | --- |
| 1 | Default-off exact actual task-context bytes | PASS |
| 2 | Active actual local authority + mounted knowledge; local shadow | PASS |
| 3 | Actual `IndexQueryService` search (`BP_TestActor` → 1 relevant asset) | PASS |
| 4 | Short Chinese query (`契约`) mounted fallback | PASS |
| 5 | Multi-pack project > shared source priority with `includeConflicts` | PASS |
| 6 | Combined local + mounted byte/token budget with actual context | PASS |
| 7 | Manifest/revision/hash pin mismatch | PASS |
| 8 | Expired verified claim before actual-service handoff | PASS |
| 9 | Actual other-project task context rejected | PASS |
| 10 | Invalid claimed session (empty session id) rejected | PASS |
| 11 | Malicious pack instructions remain in mounted section only | PASS |
| 12 | No production DB path, no pack writes, no sensitive full body/details | PASS |

Actual test class: `tests/python/test_memory_mount_real_service_compat.py` — **12/12 PASS**.
Harness: `tests/python/r7_3_real_service_host_harness.py` — exit 0.

## 6. Security details

- Local `task_context` is treated as local authority; it is never replaced by pack text.
- A mounted item with a local-authority `subjectKey` is removed before serialization even when
  its pack priority is 100. The actual local `rule.local` remains.
- All mounted text is inside `untrusted-source-content` / `never-execute`; malicious
  `IGNORE PREVIOUS`, fake `<system>` markup, command/URL text does not enter the local section.
- Pins include `packId + revision + contentHash + manifestDigest`; changing the pack or manifest
  fails active handoff.
- Expired claim and actual wrong-project local context fail closed; there is no fallback from
  untrusted to authoritative.
- Output has no local absolute cache paths, claim/session/principal values, record `details`,
  or full body; only bounded excerpts and manifests/pins metadata.
- Pack files are SHA-256 compared before/after; unchanged. No production DB connection or write.

Replay cache, real revocation, trusted issuer, signed receipt, and PII policy remain
`HOST_AUTH_PENDING` / user decisions; the test HMAC is not a production identity.

## 7. Tests — actual results

```text
R7 prior suite (49 + 27 + 22 + 23)     121 PASS
R7.3 actual-service tests               12 PASS
combined R7                            133 PASS (5.433 s)
existing Memory regression              90 PASS
------------------------------------------------
total                                  223 PASS
```

No failure, error, or skip. Logs under `Build/r7-isolated/Evidence/R73/`:

- `final-all-r7.log`
- `final-memory-regression.log`
- `host-harness.json`
- `host-harness.err` (empty)

The 90 existing tests are the same prior Memory regression set; no production source file was
modified in R7.3.

## 8. Performance / scope

No new benchmark was required for R7.3; the previous R7.2 5k/20k observational figures remain
stage-local and are not production SLAs. The actual-service harness envelope is 3,795 bytes /
18 approximate tokens for the one-pack scenario; this is an observation, not a threshold.

## 9. Compatibility / minimal fixes

The actual service shapes matched the R7.2 host seam without changing production code:

- `TaskContextService.get_task_context` provides `project.projectKey`, `request.query`,
  `memory.summary` and `outputBudget`;
- `ProjectMemoryService.get_injection_context` provides `text`, `snapshotId`, `injectionHash`,
  `available`, `stale`, counts, and token estimate;
- `LocalAuthoritySnapshot.from_context` already accepted these fields.

No production `task_context.py`, `memory_service.py`, `memory_injection.py`, `database.py`,
`mcp_server.py`, `mcp_memory_tools.py`, or `cli.py` change was made. No new schema version or
production mount was added.

## 10. Remaining user decisions

1. Real issuer/principal/session/project binding, replay cache, revocation, key rotation,
   transport binding (`HOST_AUTH_PENDING`).
2. Signed pack receipts / trusted-author keys / revocation.
3. PII/redaction policy for local task context and shared packs.
4. Final precedence/permissions and whether local authority always wins.
5. Promotion/write API, idempotency, confirmation, rollback.
6. Retention/audit for local snapshots, mounted excerpts, and handoff envelopes.
7. Production MCP/DSH exposure, tool names/scopes/rate limits.
8. Official token budget/tokenizer; `chars-div-4` remains an approximation.
9. Multi-tenant/concurrent mount catalog and cross-team publishing.

## 11. Reproduction

```powershell
py -3.12 -m unittest discover -s tests/python -p "test_memory_mount*.py" -v
py -3.12 tests/python/r7_3_real_service_host_harness.py
```

The harness uses actual `TaskContextService`, `ProjectMemoryService.get_injection_context`,
`IndexQueryService`, and disposable synthetic SQLite; identity is the TEST-ONLY HMAC pair.

## 12. DB note

Prior R7 baseline observed `50A44D…` / 1,007,616 bytes. At R7.3 close the coordination DB was
already externally changed to `B606A1E1570641AA22487C0CD52855ECCF856381B505C371F289049B68BF12EB`
/ 1,175,552 bytes (`-wal` 0, `-shm` 32,768), with mtime before this R7.3 turn. This was not
caused by R7.3; R7.3 did not open, query, checkpoint, or write it, and did not modify sidecars.
No R7.3 source/test references `coordination_memory` or `UEAgentKit-Development`.

**STOP after R7.3.** No R7.4, trusted issuer, signed pack, production MCP exposure, network
sync, promotion, shared write, or PII/retention approval was started.
