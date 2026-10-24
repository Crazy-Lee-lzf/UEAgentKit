# R7.3 Actual-Service Staging Compatibility — Run State

- Task: R7.3 actual TaskContextService / ProjectMemoryService disposable host compatibility
- Model: DSH default `cline-pass/deepseek-v4.1-flash-1017` (non-Fast)
- Worktree: `E:\WorkSpace\UEAgentKit-WT\memory-mount-r7`
- Branch: `feature/memory-mount-r7`
- Start HEAD: `f6d08c0ab340b603da4e4b5b1705e9118bd9a040`
- Baseline: `main` `c5ce3419c6962da49e874586f4e5dbd1457a4d0d`
- Start status: clean
- Start time: 2026-10-24T20:12:59+08:00
- Status: **actual-service read-only compatibility PASS; HOST_AUTH_PENDING; production BLOCKED**

## 1. Completed

| Item | State |
| --- | --- |
| Actual `TaskContextService.get_task_context` fixture | done |
| Actual `ProjectMemoryService.get_injection_context` | done |
| Actual `IndexQueryService` search fixture | done |
| New disposable synthetic SQLite project DB | done (temp/Build R73) |
| Default-off exact actual task-context bytes | done |
| Active actual local authority + mounted knowledge | done |
| Local rule shadow vs pack priority 100 | done |
| Actual short Chinese mounted fallback | done |
| Multi-pack project > shared priority | done |
| Combined byte/token budget | done |
| Pin revision/content/manifest mismatch | done |
| Expired claim / wrong project / invalid session | done |
| Malicious pack text label isolation | done |
| No production DB path or pack writes | done |
| Production MCP/Memory unchanged | done |

## 2. Tests (actual)

```text
R7 prior suite (49 + 27 + 22 + 23)   121 PASS
R7.3 actual-service tests             12 PASS
combined R7                          133 PASS (5.433 s)
existing Memory regression            90 PASS
---------------------------------------------
total                                223 PASS
```

No failure, error, or skip. Logs under `Build/r7-isolated/Evidence/R73/`:

- `final-all-r7.log`
- `final-memory-regression.log`
- `host-harness.json`
- `host-harness.err` (empty)

## 3. Actual harness result

```text
actualServices.projectKey=测试项目
actualServices.taskContextTool=ue_get_task_context
actualServices.relevantAssets=1
actualServices.memoryAvailable=true
actualServices.memorySnapshotId=ctxsnap_ec06d172e5672b7d483ca7dc8cd8d297
defaultOffByteParity=true
active=true
activeSections=[local-authority, mounted-knowledge]
mountedRecordCount=1
shadowedMountedItems=1
outputBytes=3795
approxTokens=18
localRulePresent=true
packLocalOverridePresent=false
```

Executable command:

```powershell
py -3.12 tests/python/r7_3_real_service_host_harness.py
```

## 4. Data safety

```text
Prior R7 baseline: 50A44D... / 1,007,616 bytes / 0 sidecars
Current at close:  B606A1E1570641AA22487C0CD52855ECCF856381B505C371F289049B68BF12EB
                  1,175,552 bytes; -wal 0, -shm 32,768
```

The coordination DB change is external and predates this turn. R7.3 did not open, query,
checkpoint, or write it; sidecars were not deleted/modified. Synthetic R7.3 SQLite/index/memory
databases live only under temp dirs or `Build/r7-isolated/Evidence/R73`.

- No schema v6, backup, token/whitelist, network, or shared-write change.
- No UE Editor/UBT/UBA/ShaderCompileWorker, no Computer Use.
- No production imports in `mcp_server.py`, `memory_service.py`, `memory_injection.py`,
  `task_context.py`, `mcp_memory_tools.py`, or `cli.py`.

## 5. Files

New:

```text
tests/python/r7_3_real_service_fixture.py
tests/python/r7_3_real_service_host_harness.py
tests/python/test_memory_mount_real_service_compat.py
spec/memory-mount/R7_3_REAL_SERVICE_STAGING_COMPATIBILITY_REPORT.md
spec/memory-mount/R7_3_READONLY_RUNSTATE_20261024.md
```

No production source modified. R7.2 `staging.py`/DTO did not need a R7.3 fix; actual service
outputs matched the host seam.

## 6. Commit / stage gate

- Local R7.3 code+docs commit: `f3d10490644629dfef576687ed5e82f1f3ccc3b9`
- No push, merge, rebase, reset, clean, or cross-branch cherry-pick.
- Stage gate: R7.3 actual-service compatibility PASS is still **not** production MCP/DSH
  integration. Real issuer/session/replay/revocation, signed receipts, PII/retention,
  permissions, promotion, and production activation require user decisions.
- Stop after R7.3; no R7.4, trusted issuer, signing, network sync, or promotion started.

## 7. Reproduce

```powershell
py -3.12 -m unittest discover -s tests/python -p "test_memory_mount*.py" -v
py -3.12 tests/python/r7_3_real_service_host_harness.py
```
