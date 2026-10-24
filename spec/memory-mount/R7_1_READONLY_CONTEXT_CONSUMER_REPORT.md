# R7.1 Read-only Context Consumer — Report

- Date: 2026-10-24 (Asia/Hong_Kong)
- Model: DSH default `cline-pass/deepseek-v4.1-flash-1017` (non-Fast)
- Worktree: `E:\WorkSpace\UEAgentKit-WT\memory-mount-r7`
- Branch: `feature/memory-mount-r7`
- Start HEAD: `b8ea3a2f8fb1ae310b202405c26c9037cbb73e6b`
- Baseline: `main` `c5ce3419c6962da49e874586f4e5dbd1457a4d0d`
- Scope: purely offline Python/SQLite; zero UnrealEditor/UBT/UBA; no MCP/Memory integration.

## 1. Status

| Item | Status |
| --- | --- |
| Offline R7.1 context consumer | **PASS** |
| Determinism / bounded output / expiry / integrity safety | **PASS** |
| Backward compatibility with R7.0/R7.1 readiness | **PASS** (49 + 27 old R7 tests) |
| Performance observation at 5k/20k synthetic records | **PASS** (observations only, no threshold) |
| Production MCP tool / real issuer / signed packs / shared writes | blocked / not attempted |

**This is not production MCP integration.** R7.1 is a versioned DTO and projector inside
`ue_agent_kit.memory_mount`, consumed by tests only. No production service imports it.

## 2. API and DTO

Added `src/ue_agent_kit/memory_mount/context.py`:

```text
ContextLimits              hard projector bounds
AgentContextRequest        query + record/byte/token/pack/excerpt/time budgets
AgentContextProjector      project(request) -> AgentContextProjection
AgentContextProjection     schemaVersion=1 DTO with to_payload()/to_json_bytes()
```

The projector composes the existing verified-only `ReadOnlyMountAdapter`; it does not open
packs, build indexes, or persist mounts itself.

Output envelope (`kind=r7-agent-context-projection`, `schemaVersion=1`):

```text
trust {identity=verified-claims, text=untrusted-source-content,
       instructionPolicy=never-execute, authorAuthenticity=not-established}
packs [{packId, revision, contentHash, priority, scope, recordCount,
        integrityMode, receiptVerified, authorAuthenticity}]
items [{globalId, packId, recordId, recordType, subjectKey, title,
        excerpt, excerptTruncated, sourceKind, confidence, status, scopes,
        updatedAtUtc, contentSha256, matchReason, rank, provenance,
        textTrust, instructionPolicy, conflict?, selected?}]
recordCount, packCount, conflictCount, retrievalMode, truncated,
budgets {maxRecords, maxPacks, maxOutputBytes, maxApproxTokens,
         maxExcerptChars, timeoutMs, usedRecords, usedPacks, textChars,
         approxTokens, outputBytes, tokenApproxMethod=chars-div-4},
warnings
```

Every item and the top-level `trust` block label pack text as **untrusted source content**
with `instructionPolicy=never-execute`. Consumers must treat titles/excerpts as data, never
as instructions. This is a label and API boundary, not content sanitization.

## 3. Identity and authorization

- `AgentContextProjector` requires a `ReadOnlyMountAdapter` and checks `is_verified`.
- Demo (`DemoCallerContext`) and raw string callers are rejected with `r7-identity-required`.
- Claims are re-checked by the adapter/registry on every mount/query; an expired claim at
  query time fails `r7-identity-expired`.
- A project pack whose `projectKey` differs from the verified claim fails
  `r7-project-mismatch` before any context is projected.
- The test consumer example uses `TestClaimIssuer` + `HmacClaimVerifier` and is explicitly
  labeled **TEST-ONLY**; it is not a DSH token or production identity provider.

## 4. Query, precedence, dedupe, conflicts, excerpts

- Query flows through the existing R7 registry, so precedence remains
  **project > personal > shared**, with stable rank/tie-breaks and exact-content dedupe.
- Normal FTS5 trigram and 2-character Chinese substring fallback both work; the projection
  preserves `matchReason`.
- `includeConflicts=false` returns the winner plus conflict metadata only.
  `includeConflicts=true` returns all distinct content alternatives with `selected=true/false`.
- Excerpts use the adapter's bounded `bodyPreview`; the full `body` is never copied.
  `details` is never copied. `excerptTruncated` is exposed.
- Pack metadata is sorted by `packId`; items preserve the registry's deterministic order.
- Tests compare repeated projections byte-for-byte and compare record ordering when the
  same records are written in a different `records.jsonl` order.

## 5. Leakage controls

Projection payloads must not contain:

- local absolute pack/cache paths;
- claim ids, session ids, principal names, token/key material;
- record `details`;
- full record `body`;
- absolute `provenance.sourceRef` values (redacted to `[redacted-path]`).

Tests serialize the projection and assert the above. Cache paths, temp roots, `details`
sentinel values, full-body sentinels, session strings, and principal strings are absent.
`scope` metadata in packs exposes only `kind` and, for project packs, `projectKey` — not
`owner`.

## 6. Budgets and fail-closed behavior

| Budget | Default / enforcement |
| --- | --- |
| Records | default 8, hard max 20 |
| Packs | default 8, hard max 8; mounted > request -> `r7-context-pack-limit` |
| Excerpt | default 320 chars, hard max 1,000 |
| Serialized output | default 16,384 bytes, min 512 |
| Approx tokens | `ceil((title+excerpt chars)/4)`, default 4,096, min 64 |
| Timeout | default 250 ms, hard max 2,000 |
| Query | 1–128 chars, control chars rejected |

The projector drops items from the tail until the serialized byte and approximate-token
budgets fit; it sets `truncated=true` and adds `r7-context-output-truncated`. A single item
that cannot fit fails `r7-context-output-too-large`; partial success is not returned.
Invalid requests fail `r7-context-request-invalid`. Malformed adapter items fail
`r7-context-invalid-record`.

`chars-div-4` is a documented approximation, not a tokenizer-specific count and not a
production billing/SLA claim.

## 7. Safety regressions

| Case | Result |
| --- | --- |
| Demo/raw caller | `r7-identity-required` |
| Expired claim at query | `r7-identity-expired` |
| Wrong-project pack | `r7-project-mismatch` |
| Tampered manifest receipt | `r7-integrity-receipt-mismatch`, no context |
| Poisoned index body | index rebuilt from verified pack; context uses original text |
| Pack files after projection | SHA-256 unchanged |
| Mounted packs > context budget | `r7-context-pack-limit` |
| Malformed adapter item | `r7-context-invalid-record` |
| Output/token over budget | bounded truncation or `r7-context-output-too-large` |
| Leakage | no paths, claims, details, full body, or absolute provenance |
| Production import | none (`mcp_server.py`, `memory_service.py`, `cli.py` unchanged) |

## 8. Tests — actual results

```text
test_memory_mount.py                  49 PASS
test_memory_mount_readiness.py        27 PASS
test_memory_mount_context.py          22 PASS
combined R7                           98 PASS (2.794 s)
existing Memory regression            90 PASS
------------------------------------------------
total                                188 PASS
```

No failure, error, or skip. Logs:

- `Build/r7-isolated/Evidence/R71/final-base.log`
- `Build/r7-isolated/Evidence/R71/final-readiness.log`
- `Build/r7-isolated/Evidence/R71/final-context.log`
- `Build/r7-isolated/Evidence/R71/final-all-r7.log`
- `Build/r7-isolated/Evidence/R71/final-memory-regression.log`

The 22 context tests cover projection shape/trust labels, versioned JSON Schema, demo/raw
rejection, expiry, wrong project, pack limit, record/byte/token budgets, short Chinese,
normal FTS, dedupe, explicit conflict alternatives, stable/reversed order, leakage,
malformed adapter data, tampered receipt, and index poisoning.

## 9. Performance observations

Synthetic packs: 5,000 records/pack, 4 packs = 20,000 records. Adapter uses the verified
read-only path and receipts; all numbers are machine-specific observations, not thresholds.

| Workload | Median | p95 | Items | Output bytes | Approx tokens | Python peak |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 5k records (1 pack) | 9.677 ms | 13.035 ms | 8 | 8,185 | 154 | 95,420 B |
| 20k records (4 packs) | 35.844 ms | 41.448 ms | 8 | 9,124 | 154 | 375,814 B |
| 2-char Chinese over 20k | 21.438 ms | 25.397 ms | 8 | 9,208 | 154 | 375,826 B |

- Mount median for the 4 packs: ~274.5 ms; index reuse remains verified, not bypassed.
- `deterministic=true` for all repeated projections.
- `truncated=true` in these runs because more than 8 records matched the `maxRecords=8`
  budget; this is the record limit, not an output failure.
- Raw output: `Build/r7-isolated/Evidence/R71/context-microbench.json`.
- No conclusion about production latency, memory ceiling, or concurrency is claimed.

## 10. Compatibility

- Existing R7 v1 manifest/record/query/index APIs are unchanged; no provider/model change.
- The new DTO is `schemaVersion=1` and exists only in `memory_mount`; no old result fields
  were repurposed.
- `R7_MOUNT_CONTRACT.md` has an additive §7.6 and four context error codes.
- No schema v6, `coordination_memory.sqlite3`, backup, WorkspaceBridge token/whitelist,
  MCP tool, memory service, or CLI default behavior changed.
- No real issuer, signature trust, promotion, network mount, or shared write was added.

## 11. Remaining production decisions

R7.1 offline PASS does **not** authorize production integration. Human decisions still
required:

1. Real claim issuer/principal/session/project binding; replay cache, revocation, key
   rotation, clock policy, and transport binding.
2. Signed pack receipts, trusted-author keys, revocation, and canonical signed bytes.
3. Final precedence/permission policy, especially project vs personal overrides.
4. Shared pack distribution, publisher/revocation authority, PII scanning/redaction.
5. Promotion/write API, idempotency, user confirmation, and rollback.
6. Retention/audit for excerpts, query text, context projections, and cache.
7. MCP exposure: tool names, scopes, rate limits, output caps, audit events.
8. Official token budget policy/tokenizer; `chars-div-4` is only an approximation.
9. Context DTO governance and any v2/signature migration.
10. Multi-tenant/process concurrency and cross-person shared writes.

No production import is added. `mcp_server.py`, `memory_service.py`, `cli.py`, and
`memory_context.py` remain untouched.

## 12. Reproduction

```powershell
py -3.12 -m unittest discover -s tests/python -p "test_memory_mount.py" -v
py -3.12 -m unittest discover -s tests/python -p "test_memory_mount_readiness.py" -v
py -3.12 -m unittest discover -s tests/python -p "test_memory_mount_context.py" -v
py -3.12 -m unittest discover -s tests/python -p "test_memory_mount*.py" -v
py -3.12 tests/python/r7_1_context_microbench.py
py -3.12 tests/python/r7_1_context_consumer_example.py
```

The consumer example is test-only (`TestClaimIssuer`/`HmacClaimVerifier`); it is not a DSH
identity integration.

## 13. Evidence and DB safety

```text
Prior R7 baseline (earlier 2026-10-24 run):
  SHA-256 50A44D5959128C4C1C4DE5257CEC23B3AFCF24C170C0897D006B6F9928333789
  size    1,007,616 bytes
Current observation at R7.1 close:
  SHA-256 B606A1E1570641AA22487C0CD52855ECCF856381B505C371F289049B68BF12EB
  size    1,175,552 bytes
  sidecars -wal 0 bytes, -shm 32,768 bytes
  main mtime 2026-10-24T10:09:56Z; sidecar mtime 2026-10-24T10:23:07Z
```

The change predates this R7.1 turn (turn start 2026-10-24T11:20Z) and was made by another
process; R7.1 did not open, query, checkpoint, or write the coordination DB. No R7.1 source
or test file references `coordination_memory` / `UEAgentKit-Development`. Sidecars were not
deleted or modified.

No SQLite connection to the coordination DB, no writes/migrations/backups, no schema v6
changes, no DSH token/whitelist changes, no UE Editor/BuildPlugin/UBA, no Computer Use.

All synthetic benchmarks/tests use temporary directories or
`Build/r7-isolated/Evidence/R71`, which is git-ignored.
