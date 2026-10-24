# M3.10 — Unified Default-Off Integration Candidate (M3.9 + R1.6 + R7.4) — Report

- Date: 2026-10-25
- Worktree: `E:\WorkSpace\UEAgentKit-WT\m3-r1-integration` (existing; no new worktree/session)
- Branch: `feature/m3-r1-integration-m38`
- Base: `9f22f049f6644367f8debcc7ce9988c76d9ba59c` (M3.9 PASS, clean)
- R7.4 source (read-only): `54050b9c0df02fbd8de0ff4bbc9131734bb0b7f6`
- Merge commit: `a2d9b9ab8125a861a3009749ee172583bf16215f` (parents `9f22f04` + `54050b9`)
- Preflight: `git merge-tree --write-tree HEAD 54050b9` → tree `718d72dd…`, exit 0, zero conflicts; common ancestor `c5ce341`.
- Result: **PASS for an isolated, default-off integration candidate; production/M4 remains BLOCKED.**
- No push, no main/R1/R7 edits, no reset/rebase/clean, no production project or broker.

## 1. Merge scope (provenance)

- Ordinary `git merge --no-ff 54050b9` in this worktree; no conflict resolution was needed.
- Merge diff vs M3.9: **45 files, +13,002**, all R7 additions (memory_mount Python package,
  schemas, R7 tests/harnesses, R7 docs).
- `git diff 9f22f04 HEAD -- Plugin` is **empty**: no C++/plugin/INI change, so the M3.9 DLL
  `sha256:A10379D4581FD4EA6D002DF3CD1F35142AC4BA277EFD753EC612FFA1A6D2447D` remains the
  pre-merge plugin artifact. No UE build/editor was run in M3.10.
- M3.9 tools and evidence were retained (3-graph evidence unchanged, see §5).

## 2. Acceptance

| # | Check | Status |
|---|---|---|
| 1 | R7 APIs + M3/R1 modules import/load together | **PASS** |
| 2 | Full Python suite incl. R7 133 + native_tools | **PASS** (1522 / 57) |
| 3 | R7.3 real-service disposable harness: default-off exact legacy bytes; active only with test-only adapter | **PASS** |
| 4 | M3 Z5/Z6 check-only tools; P11 defaults and R1 registry untouched | **PASS** |
| 5 | M3.9 3-graph evidence retained; no false claim of combined-R7 runtime | **PASS / PARTIAL as labelled** |
| 6 | `git diff --check`, no new endpoints, no default-on, no DB writes | **PASS** |
| 7 | Report + scoped docs commit | **PASS** |

### 2.1 Import/load together

```text
ue_agent_kit.memory_mount (StagedContextHandoff, ReadOnlyMountAdapter, ClaimVerifier, ...)
ue_agent_kit.pre_m4.canary_preflight / telemetry_eval
ue_agent_kit.task_context.TaskContextService / memory_service.ProjectMemoryService
ue_agent_kit.graph_contracts.typed_graph (ueak.typedgraph/1.0)
```

All imported in one process with `PYTHONPATH=src`; no module shadowing or duplicate contract.

### 2.2 Python regression

| Suite | Result |
|---|---|
| Full merged suite (`RunPythonTests.py full`) | **1522 tests, 17 skipped, OK**, 133.649 s |
| R7 subset (`test_memory_mount*`) | **133 tests OK**, 5.800 s |
| `tests/native_tools` | **57 tests OK**, 14.631 s |

The full suite is exactly M3.9's 1389 plus R7's 133; Memory regressions are inside the full run.

### 2.3 R7.3 real-service harness

`py -3.12 tests/python/r7_3_real_service_host_harness.py` (exit 0), disposable synthetic SQLite:

- `defaultOffByteParity=true`, `disabledReason=feature-disabled`;
- `active=true`, `activeSections=["local-authority","mounted-knowledge"]` only with the
  test-only HMAC verifier + `ReadOnlyMountAdapter`; `identity="TEST-ONLY HMAC verifier; not
  DSH/production"`;
- actual `TaskContextService.get_task_context` / `ProjectMemoryService.get_injection_context` /
  `IndexQueryService`; `mountedRecordCount=1`; local rule present; `packLocalOverridePresent=false`;
  `shadowedMountedItems=1`; `approxTokens=18`, `outputBytes=3795`.
- No real issuer/principal/session token, no production DB, no pack write.

### 2.4 Z5/Z6 and defaults

- `m39_z5_check.py`: ELIGIBLE + 4 fail-closed cases; `tokenEmitted=false`,
  `absolutePathEmitted=false`.
- `m39_z6_datasets.py`: positive-only 6 attempts / 0 breaches; negative-heavy 8 attempts with
  expected security/fallback/failure flags, 0 writes, 0 tokens.
- `NativeToolRegistry=off` in `DefaultUEAgentKit.ini`; `NativeLayoutPreview` default remains off;
  `StagedContextHandoff(enabled=False)` default; no `memory_mount`/`staged_context` reference in
  MCP/tool registry code (grep outside the package returns nothing). No new endpoint registered.

## 3. What was not done (explicit)

- No UE editor/UBT/UBA/ShaderCompileWorker; no runtime claim that the merged R7 tree was exercised
  inside the combined plugin. R7 is Python-only and the plugin diff is empty, so the M3.9 DLL and
  its runtime evidence remain valid **as pre-merge evidence**, not as a combined-R7 runtime test.
- No production MCP/DSH activation, no real issuer/signature, no pack write/promotion, no shared
  publication, no PII/retention policy, no tokenizer-authority decision.
- No identity inference from `sessionId`, `cwd`, `requestId`, bridge token, or MCP args.
- No changes to the coordination DB, main, R1/R7 worktrees, Reforge, Lyra, or production projects.

## 4. Security gate status (unchanged from R7.4)

| Gate | Status |
|---|---|
| G1 transport identity root | **BLOCKED USER DECISION** |
| G2 issuer/principal/session/project binding | **BLOCKED USER DECISION** |
| G3 session ID is not a credential | VERIFIED (source/tests) |
| G6 signed pack/trusted author | **BLOCKED USER DECISION** |
| G7 shared visibility/PII/retention | **BLOCKED USER DECISION** |
| G12 default-off exact legacy parity | **VERIFIED** (harness) |
| G13 pack/DB read-only | **VERIFIED** |
| G14 end-to-end host trust | **HOST_AUTH_PENDING** |
| M4/default-on | **BLOCKED** (Z4/Z5/Z6 approvals still missing) |

This candidate does not freeze production readiness; G14 remains pending.

## 5. Retained evidence

- M3.9 3-graph evidence `Build/m39-readiness/three-graphs.json` unchanged:
  `sha256:BD1F6E145F9306A247794254F09CE473FA8A32B49B1E34D82E7F2A2F10319C52`.
- M3.9 Z5/Z6 artifacts unchanged (`z5-preflight 8208C76F…`, `z6-datasets 2C4C7743…`).
- New M3.10 logs under `Build/m310/`:

| File | Bytes | SHA256 |
|---|---:|---|
| python-full.log | 4,509 | `1702D20C…DEA4` |
| python-native.log | 162 | `B4DB39E9…91DB` |
| python-r7-133.log | 326 | `1A802248…5EAB` |
| harness.log | 731 | `D4FA0C19…7629` |
| z5.log | 118 | `64766F98…A5DF` |
| z6.log | 198 | `78F47BE3…DA03` |
| DLL (unchanged) | 4,483,072 | `A10379D4…447D` |

## 6. Commands

```powershell
git merge-tree --write-tree HEAD 54050b9
git merge --no-ff 54050b9
py -3.12 scripts\RunPythonTests.py full
py -3.12 -m unittest discover -s tests\native_tools -p "test_*.py"
py -3.12 -m unittest test_memory_mount test_memory_mount_context test_memory_mount_readiness `
  test_memory_mount_real_service_compat test_memory_mount_staging
py -3.12 tests\python\r7_3_real_service_host_harness.py
py -3.12 tools\m39_z5_check.py
py -3.12 tools\m39_z6_datasets.py
git diff --check
```

## 7. Limitations

- This is an isolated integration candidate, not a production release or M4 authorization.
- R7.4 remains an audit: host auth, issuer, signed packs, shared visibility/PII/retention, and
  tokenizer authority are human decisions.
- Three disposable graphs and one test-only HMAC identity only; no production-scale evidence.
- No combined-R7 UE runtime was executed (not needed for a Python-only merge; not claimed).
