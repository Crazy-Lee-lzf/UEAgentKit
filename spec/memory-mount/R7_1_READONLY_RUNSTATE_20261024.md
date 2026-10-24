# R7.1 Read-only Context Consumer — Run State

- Task: R7.1 isolated read-only knowledge-pack agent context consumption
- Model: DSH default `cline-pass/deepseek-v4.1-flash-1017` (non-Fast)
- Worktree: `E:\WorkSpace\UEAgentKit-WT\memory-mount-r7`
- Branch: `feature/memory-mount-r7`
- Start HEAD: `b8ea3a2f8fb1ae310b202405c26c9037cbb73e6b`
- Baseline: `main` `c5ce3419c6962da49e874586f4e5dbd1457a4d0d`
- Start status: clean
- Start time: 2026-10-24T19:20:25+08:00
- Status: **offline PASS; production integration BLOCKED / not attempted**

## 1. Completed

| Item | State |
| --- | --- |
| `AgentContextRequest` / `ContextLimits` / `AgentContextProjection` | done |
| `AgentContextProjector` atop verified `ReadOnlyMountAdapter` | done |
| Project > personal > shared precedence preserved | done (existing registry) |
| Explicit conflict alternatives via `includeConflicts` | done |
| Untrusted-source-content labels on all pack text | done |
| Byte/token/record/pack/excerpt/timeout budgets | done |
| Demo/raw/expired/wrong-project rejection | done |
| Chinese 2-char fallback + normal FTS | done |
| Deterministic repeated/ordered projections | done |
| Leakage/path/claim/details/full-body regression | done |
| Tampered receipt and index-poisoning regression | done |
| Versioned context JSON Schema | done |
| Test-only external consumer example | done |
| Production MCP/Memory untouched | done |

## 2. Tests (actual)

```text
test_memory_mount.py                  49 PASS
test_memory_mount_readiness.py        27 PASS
test_memory_mount_context.py          22 PASS
combined R7                           98 PASS (2.794 s)
existing Memory regression            90 PASS
------------------------------------------------
total                                188 PASS
```

No failure, error, or skip. Logs under `Build/r7-isolated/Evidence/R71/`:

- `final-base.log`
- `final-readiness.log`
- `final-context.log`
- `final-all-r7.log`
- `final-memory-regression.log`
- `context-microbench.json`
- `consumer-example.json`

## 3. Performance observations

5k/20k synthetic records, `maxRecords=8`, 16 KB / 4096-token budgets; observations only.

| Workload | Median | p95 | Output bytes | Approx tokens | Python peak |
| --- | ---: | ---: | ---: | ---: | ---: |
| 5k records / 1 pack | 9.677 ms | 13.035 ms | 8,185 | 154 | 95,420 B |
| 20k records / 4 packs | 35.844 ms | 41.448 ms | 9,124 | 154 | 375,814 B |
| 2-char Chinese / 20k | 21.438 ms | 25.397 ms | 9,208 | 154 | 375,826 B |

Mount median ~274.5 ms for 4 packs. `deterministic=true`; `truncated=true` because more
than 8 records matched `maxRecords=8`. No production latency/SLA claim.

## 4. Data safety

```text
Prior R7 baseline: 50A44D... / 1,007,616 bytes / 0 sidecars
Current at close:  B606A1E1570641AA22487C0CD52855ECCF856381B505C371F289049B68BF12EB
                  1,175,552 bytes; -wal 0, -shm 32,768
Main mtime:       2026-10-24T10:09:56Z (before this turn's 11:20Z start)
```

The change is external (another process) and predates this turn. R7.1 did not open, query,
checkpoint, or write the coordination DB; sidecars were not deleted/modified. No R7.1 code
or test references `coordination_memory` or `UEAgentKit-Development`.

- No SQLite connection to the coordination DB; no writes/migrations/backups.
- No schema v6 changes; no WorkspaceBridge token/whitelist changes.
- No UE Editor/UBT/UBA/Computer Use; no network.
- No production imports in `mcp_server.py` / `memory_service.py` / `cli.py`.

## 5. Files

New:

```text
src/ue_agent_kit/memory_mount/context.py
tests/python/test_memory_mount_context.py
tests/python/r7_1_context_microbench.py
tests/python/r7_1_context_consumer_example.py
spec/memory-mount/r7-agent-context-projection.schema.json
spec/memory-mount/R7_1_READONLY_CONTEXT_CONSUMER_REPORT.md
spec/memory-mount/R7_1_READONLY_RUNSTATE_20261024.md
```

Modified:

```text
src/ue_agent_kit/memory_mount/__init__.py
src/ue_agent_kit/memory_mount/errors.py
spec/memory-mount/R7_MOUNT_CONTRACT.md
```

## 6. Commit / next gate

- Local R7.1 code+docs commit: `a6f39a9651e65ddb862324a413ec0ae71d392275`
- No push, merge, rebase, reset, clean, or cross-branch cherry-pick.
- Next gate is human decision on real issuer, signed receipts, PII/retention, precedence,
  promotion/write, MCP exposure, and multi-tenant policy. R7.1 remains an isolated offline
  consumer, not a production MCP tool.

## 7. Reproduce

```powershell
py -3.12 -m unittest discover -s tests/python -p "test_memory_mount.py" -v
py -3.12 -m unittest discover -s tests/python -p "test_memory_mount_readiness.py" -v
py -3.12 -m unittest discover -s tests/python -p "test_memory_mount_context.py" -v
py -3.12 -m unittest discover -s tests/python -p "test_memory_mount*.py" -v
py -3.12 tests/python/r7_1_context_microbench.py
py -3.12 tests/python/r7_1_context_consumer_example.py
```
