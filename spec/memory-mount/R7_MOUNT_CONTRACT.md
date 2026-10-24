# R7 Memory Mount / Knowledge Pack — read-only contract

- Status: **review draft, first R7 segment**. Not implemented in any production service.
- Date: 2026-10-23 (Asia/Hong_Kong).
- Baseline: `main` `c5ce3419c6962da49e874586f4e5dbd1457a4d0d`.
- Scope: read-only contract, machine-readable schemas, and an isolated offline PoC.
- Normative keywords: **MUST**, **MUST NOT**, **SHOULD**, **MAY**.

> This document does not authorize writes, cross-person sharing, network sync, automatic
> maintenance, or a change to the built-in Project Memory schema v6. Those require a
> separate user decision and a separate implementation phase (see §14).

## 1. Purpose and layering

R7 introduces a **Knowledge Pack**: an immutable, reviewable directory artifact that can be
mounted read-only for the lifetime of one agent session. A mount is a *read grant*, not an
import into Project Memory.

```text
Knowledge Pack (on disk, immutable)
        │  verify manifest + content hash
        ▼
Pack Index (disposable, derived, read-only SQLite)
        │  session-scoped mount + permission check
        ▼
Memory Mount Registry (in-memory, no persistence)
        │  query / dedup / conflict resolution
        ▼
Agent-visible read results
```

Project Memory SQLite (schema v6) remains the authoritative, writable store for this
repository. A Knowledge Pack is **not** a Project Memory database, is **not** merged into
schema v6, and is **not** attached to the production MCP server by this segment.

### 1.1 Goals

1. Define a stable manifest with integrity, provenance, priority, scope, and compatibility.
2. Define mount / unmount / list / query boundaries and their failure modes.
3. Define how personal, project, and shared knowledge interact, including conflicts.
4. Define Chinese-capable FTS5 retrieval without changing schema v6.
5. Define index versioning, resource limits, timeouts, and stable error codes.
6. Prove the read-only path with offline tests that never open production Memory.

### 1.2 Non-goals (this segment)

- No write API, promotion, curation, or "save to pack" flow.
- No cross-person shared writes and no shared mutable catalog.
- No network fetch, registry service, auto-update, or background maintenance.
- No signature/trust-chain implementation; `provenance` is self-asserted data.
- No change to `src/ue_agent_kit/memory_schema.py`, `CURRENT_MEMORY_SCHEMA_VERSION`, or schema v6.
- No import of the PoC by `memory_service.py`, `mcp_server.py`, `cli.py`, or any default service.
- No modification of `coordination_memory.sqlite3`, its backups, or any real Memory data.
- No change to the trusted Session whitelist or to any permission configuration.

## 2. Artifact layout

A v1 pack is a directory:

```text
<name>.r7pack/
├── pack.json          # manifest, UTF-8, no BOM, LF
└── records.jsonl      # one canonical JSON record per line, UTF-8, no BOM, LF
```

Rules:

- `pack.json` and `records.jsonl` MUST be regular files inside the pack root.
- The pack root and both files MUST NOT be symlinks. Path traversal outside the pack root
  MUST be rejected (`r7-pack-path-invalid`).
- No other file is read or executed. A pack MUST NOT contain code, SQL, or scripts that the
  reader executes. Unknown files are ignored.
- If `recordCount` is `0`, `records.jsonl` MUST be a zero-byte file. Otherwise it MUST end
  with a single LF after the last record. Empty lines are invalid.
- A pack is immutable. The reader opens source files read-only and never writes to the pack.
- `pack.json` size MUST be ≤ 256 KiB; `records.jsonl` size MUST be ≤ 16 MiB.

### 2.1 Optional integrity receipt (v1-compatible)

A v1 pack MAY include `pack.integrity.json`, a sidecar that covers manifest fields
`contentHash` does not protect. Unknown files were already ignored by v1, so the sidecar is a
compatible extension; a pack without it remains a valid legacy v1 pack.

The receipt contains `receiptVersion=1`, `packId`, `contentHash`, `revision`, `priority`,
`recordCount`, `manifestDigest`, `scopeDigest`, and `provenanceDigest` (all SHA-256 over
canonical JSON). A present receipt MUST match every listed invariant; mismatch fails
`r7-integrity-receipt-mismatch`. A non-null `signature` fails
`r7-signature-unsupported`: R7 v1 does not claim a trusted author.

- No receipt: mount MAY proceed, but the result MUST report
  `integrityMode=records-only-unverified-manifest` and warn
  `r7-manifest-integrity-unverified`.
- Matching digest receipt: `integrityMode=manifest-digest-verified`; this detects
  accidental/local manifest tamper, not a malicious replacement of both manifest and receipt.
- `authorAuthenticity` remains `not-established` in both cases.

## 3. Manifest

### 3.1 Required fields

| Field | Type | Rules |
| --- | --- | --- |
| `schemaVersion` | integer | MUST be `1` in v1. A reader that does not support the value MUST fail `r7-manifest-unsupported-schema`. |
| `packId` | string | `^[a-z0-9][a-z0-9._-]{2,63}$`; globally identifies the pack revision line. |
| `contentHash` | string | `^sha256:[0-9a-f]{64}$`; digest of the exact `records.jsonl` bytes. |
| `revision` | string | 1–128 printable characters; producer-assigned revision label (for example `2026-10-23T15:00:00Z/1`). Not trusted for ordering across packs. |
| `provenance` | object | See §3.3. |
| `priority` | integer | `0..100`; required; higher wins inside the same scope tier. |
| `scope` | object | See §3.4. |
| `compatibility` | object | See §3.5. |
| `recordCount` | integer | `0..5000`; MUST equal the number of lines in `records.jsonl`. |

Optional fields: `title` (≤200 chars), `description` (≤2000 chars).

Unknown top-level or nested fields MUST be rejected (`r7-manifest-invalid`) so a pack cannot
silently carry semantics this reader does not enforce.

`contentHash` protects `records.jsonl` bytes only. Manifest fields such as `priority`,
`scope`, and `provenance` are self-asserted and are not covered by that digest. A future
signature phase MUST cover the canonical manifest plus `contentHash` if priority/scope need
to be tamper-evident.

### 3.2 Example

```json
{
  "schemaVersion": 1,
  "packId": "r7.example.project-rules",
  "contentHash": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "revision": "2026-10-23T15:00:00Z/1",
  "provenance": {
    "kind": "authored",
    "producer": "human:reviewer",
    "sourceRef": "spec/memory-mount/R7_MOUNT_CONTRACT.md",
    "createdAtUtc": "2026-10-23T15:00:00Z"
  },
  "priority": 80,
  "scope": {
    "kind": "project",
    "projectKey": "UEAgentKit",
    "visibility": "project"
  },
  "compatibility": {
    "minReaderVersion": 1,
    "maxReaderVersion": 1,
    "memorySchema": 6,
    "tokenizer": "trigram",
    "requiredFeatures": ["fts5-trigram", "cjk-substring-fallback"]
  },
  "recordCount": 1,
  "title": "Example project rules"
}
```

The hash above is illustrative; it will not match real content.

### 3.3 Provenance

`provenance` is **self-asserted metadata**, not a trust proof.

| Field | Required | Rules |
| --- | --- | --- |
| `kind` | yes | `authored`, `exported`, `derived`, or `imported`. |
| `producer` | yes | 1–128 chars; human, tool, or pipeline label. |
| `sourceRef` | yes | 0–512 chars; opaque source label; MUST NOT be resolved or fetched. |
| `createdAtUtc` | yes | ISO-8601 timestamp with timezone. |
| `signature` | no | Reserved. MUST be absent or `null`. A non-null value MUST fail `r7-signature-unsupported`; the PoC never claims a verified signature. |

Consumers MUST display provenance labels without converting them into a trust level.
Pack-level provenance describes the artifact, not each record: a `derived` or `imported`
pack MAY contain `model-inferred` records, and consumers MUST use the per-record
`sourceKind` rather than the pack provenance to judge origin. A producer MUST NOT label a
model-inferred record as `user-confirmed`; the reader preserves the per-record label but
cannot cryptographically prove it.

### 3.4 Scope

| Field | Required | Rules |
| --- | --- | --- |
| `kind` | yes | `personal`, `project`, or `shared`. |
| `projectKey` | when `kind=project` | Exact project key; otherwise absent. |
| `owner` | when `kind=personal` | Stable principal id; otherwise absent. |
| `visibility` | no | MUST equal `kind` if present. v1 does not allow cross-kind visibility. |

Semantics:

- `personal`: readable only by the matching `owner` principal; never indexed for others.
- `project`: readable only when the session's active `projectKey` matches exactly.
- `shared`: readable by any authenticated principal; MUST NOT contain personal data. This is
  a producer obligation, not an enforced DLP boundary.

A pack's `scope.kind` is a visibility domain. A record's `scopes` (asset/symbol/graph/etc.)
are knowledge bindings and do **not** grant visibility.

### 3.5 Compatibility

| Field | Required | Rules |
| --- | --- | --- |
| `minReaderVersion` | yes | `1` in v1. |
| `maxReaderVersion` | yes | Integer `≥ minReaderVersion`; reader version `1` MUST fall inside the range. |
| `memorySchema` | no | If present, MUST equal `6`. The pack records use schema-v6 record vocabulary but are not stored in schema v6. |
| `tokenizer` | yes | `trigram` in v1. |
| `requiredFeatures` | yes | Array of strings. Supported: `fts5-trigram`, `cjk-substring-fallback`. Any unknown feature fails `r7-manifest-incompatible`. |

Unsupported compatibility MUST fail before any index is built. Silent degradation is not
allowed.

## 4. Record format

Each line of `records.jsonl` is one canonical JSON object:

```json
{"body":"项目规则正文。","confidence":0.95,"recordId":"rule-001","recordType":"projectRule","scopes":[{"scopeKey":"UEAgentKit","scopeType":"project"}],"sourceKind":"user-confirmed","subjectKey":"project.rule.memory-mount.read-only","title":"R7 只读边界","updatedAtUtc":"2026-10-23T15:00:00Z"}
```

| Field | Required | Rules |
| --- | --- | --- |
| `recordId` | yes | `^[a-z0-9][a-z0-9._-]{2,95}$`; unique within the pack. |
| `recordType` | yes | `projectFact`, `projectRule`, `decisionRecord`, `knownIssue`, `taskRecord`, `runtimeEvidence`. |
| `subjectKey` | yes | 1–256 chars; stable logical identity used for conflict grouping. |
| `title` | yes | 1–512 chars. |
| `body` | yes | 1–32768 chars. |
| `sourceKind` | yes | `user-confirmed`, `tool-observed`, `model-inferred`. |
| `confidence` | no | `0.0..1.0`; default `1.0`. |
| `status` | no | `valid`, `unverified`, or `stale`; default per §4.1. `conflicted` and `superseded` are query-time derived and MUST NOT be stored in a pack. |
| `scopes` | no | ≤32 items; `scopeType` from the schema-v6 vocabulary; unique `(scopeType, scopeKey)`; `scopeKey` 1–512 chars. |
| `updatedAtUtc` | yes | ISO-8601 timestamp with timezone. |
| `details` | no | Strict JSON object; ≤16 KiB canonical JSON; never searched or returned by default. |

Line rules:

- Keys MUST be lexicographically sorted, separators MUST be `,` and `:`, and non-ASCII MUST
  be emitted as UTF-8 (not `\uXXXX`). The reader rejects non-canonical lines
  (`r7-pack-records-invalid`).
- Duplicate `recordId` within one pack is an integrity failure (`r7-pack-duplicate-id`).
- Control characters other than `\n`/`\t` inside strings are invalid JSON and are rejected.

### 4.1 Default status

```text
sourceKind=user-confirmed                         -> valid
sourceKind=tool-observed + stable revision set    -> valid   (reserved for a later revision of this contract)
sourceKind=tool-observed without revisions        -> unverified
sourceKind=model-inferred                         -> unverified
explicit status                                   -> preserved
```

v1 packs have no revision-set field; `tool-observed` therefore defaults to `unverified`.

### 4.2 Content identity

For each record the reader computes:

```text
recordContentSha256 = sha256(canonical_json({
  "recordType": ...,
  "subjectKey": ...,
  "title": ...,
  "body": ...,
  "scopes": [... sorted by (scopeType, scopeKey)],
  "details": {...}
}))
```

Global record key is `packId + ":" + recordId`. The pair `(recordType, subjectKey)` is the
logical identity used for conflict grouping. The content hash is used for exact dedup.

## 5. Source domains and precedence

### 5.1 Fixed tiers (read-only default)

| Tier | Scope kind | Rank | Rationale |
| --- | --- | ---: | --- |
| 0 | `shared` | 0 | Generic knowledge; lowest authority. |
| 1 | `personal` | 1 | Owner-specific, but cannot silently override project policy. |
| 2 | `project` | 2 | Project-specific policy and facts; wins by default. |

Within a tier, order is:

```text
higher manifest.priority
> stronger record status (valid > unverified > stale)
> newer updatedAtUtc
> lexical packId
> lexical recordId
```

This ordering is total and deterministic. Every result exposes the rank inputs so a reviewer
can see why one record won.

### 5.2 Deduplication

1. **Exact duplicate**: same `(recordType, subjectKey)` and same `recordContentSha256` across
   packs. The winner is chosen by §5.1 and returned once. No conflict marker is emitted,
   because the content agrees; alternate bodies and paths are not returned.
2. **Logical conflict**: same `(recordType, subjectKey)` but different `recordContentSha256`.
   The group is marked `unresolved`. No record is mutated, promoted, or superseded.
3. **No cross-subject dedup**: similar titles or bodies with different `subjectKey` are
   separate records.

### 5.3 Conflict response

By default a query returns one winner per logical identity plus:

```json
"conflict": {
  "groupId": "sha256:<first 16 hex of identity digest>",
  "state": "unresolved",
  "alternativeCount": 2,
  "alternativePackIds": ["pack.a", "pack.b"],
  "resolution": "project-tier-wins"
}
```

`alternativeCount` counts distinct non-winning contents; `alternativePackIds` lists the
packs that supplied those non-winning contents (the winning pack is excluded).

With `includeConflicts=true`, all distinct content representatives are returned, each with
`selected` true/false and the same `groupId`. The response still respects `limit` and output
caps. A conflict is never silently discarded: the winner always carries the marker.

## 6. Mount lifecycle

A mount is scoped to exactly one session principal and one active project key, both fixed
when the registry is constructed.

### 6.1 `mount(packPath)`

1. Resolve the pack path and reject symlinks / traversal (`r7-pack-path-invalid`).
2. Parse and validate the manifest. `pack.json` is never trusted to name arbitrary files.
3. Enforce manifest and record-count limits (`r7-pack-too-large`).
4. Check authorization immediately after the manifest is parsed, before any record bytes are
   read:
   - `shared`: any non-empty principal;
   - `project`: exact `projectKey` match (`r7-project-mismatch`);
   - `personal`: exact `owner` match (`r7-permission-denied`).
5. Read `records.jsonl`, verify its SHA-256 against `contentHash`
   (`r7-pack-hash-mismatch`), then parse and validate every line.
6. Build or reuse a read-only pack index (§8); open it read-only.
7. Insert into the in-memory registry. `mount` returns metadata only (no bodies).
8. On any failure, close resources and leave the registry unchanged.

Idempotency:

- Same `packId`, same `contentHash`, same resolved source path: return the existing mount
  (`alreadyMounted=true`). No second copy.
- Same `packId`, same `contentHash`, different source path: fail
  `r7-pack-already-mounted` rather than silently aliasing two locations.
- Same `packId`, different `contentHash`: fail `r7-pack-revision-conflict`. v1 has no
  multi-revision mounts.
- Mount count above `maxMountedPacks`: fail `r7-mount-limit`.

### 6.2 `unmount(packId)`

- Closes the index connection and removes the mount from this session only.
- Unknown `packId`: `r7-pack-not-mounted`.
- Does not delete the source pack. The derived index cache MAY be retained or deleted; it is
  never authoritative.
- Unmount is immediate; subsequent queries cannot see the pack.

### 6.3 `listMounts()`

Returns metadata for the current session only:

```text
packId, revision, contentHash, priority, scope {kind, projectKey, owner},
recordCount, indexFormatVersion, indexReused, alreadyMounted
```

Local filesystem paths and record bodies are **not** returned. Mount state is never persisted,
shared between processes, or exposed to another principal.

### 6.4 Authorization rules

- The registry MUST NOT accept a principal/project override on `mount` or `query`.
- Authorization is checked at mount time, and the mount is re-checked against the current
  registry binding before every query (belt-and-suspenders against future refactors).
- An empty principal is invalid at registry construction.
- Production entry points MUST use a verified caller context injected by the host. Raw CLI
  strings are an explicit `untrusted-demo` context; the read-only adapter rejects it.
- A pack cannot elevate visibility by `record.scopes`, `priority`, or provenance.
- No session whitelist, ACL file, or global config is read or modified.

## 7. Query

`query(text, filters...)` searches all mounts authorized for the session.

### 7.1 Query text

- Literal phrase semantics; FTS5 operators (`AND`, `OR`, `NEAR`, `*`, column filters) are
  **not** exposed.
- Trim surrounding whitespace, collapse internal runs of whitespace to one space.
- 1–128 Unicode characters; NUL/control characters rejected (`r7-query-invalid`).
- The query is always bound as a SQL parameter; it is never interpolated into SQL.

### 7.2 Retrieval

- **Long query (≥3 Unicode code points)**: FTS5 `trigram` tokenizer, quoted literal phrase.
  This makes Chinese substring search work without segmentation:
  `契约审核` matches `共享契约审核与知识包`.
- **Short query (1–2 code points), including short CJK**: FTS5 trigram cannot match; the
  reader performs a bounded `instr()` scan over `title`, `subjectKey`, and `body`, in that order. If the scan
  would exceed `maxShortQueryScanRecords`, fail `r7-query-too-broad` rather than scan an
  unbounded corpus.
- **Zero FTS hits** for a query ≤16 chars: MAY run the same bounded substring fallback and
  report `matchReason=cjk-substring-fallback`. This is a deterministic supplement, not a
  semantic search claim.
- If `fts5` with the `trigram` tokenizer is unavailable, mount fails
  `r7-fts5-trigram-unavailable`. The reader MUST NOT silently fall back to unicode61 and
  claim Chinese support.

### 7.3 Filters

```text
recordTypes   optional subset of the six v6 record types
scopeType     optional v6 scope type
scopeKey      requires scopeType
limit         1..50 (default 20)
includeConflicts  boolean (default false)
bodyPreviewChars 0..4000 (default 600)
timeoutMs     1..2000 (default 250)
```

Filters are applied inside SQL with bound parameters. `scopeKey` without `scopeType` is
`r7-query-invalid`.

### 7.4 Ordering and result shape

Results are ordered by the §5.1 rank, then retrieval score, then `packId`, then `recordId`.
Within a conflict group, alternatives follow the same rank order. Each item contains:

```text
globalId, packId, recordId, recordType, subjectKey, title,
bodyPreview, bodyTruncated, sourceKind, confidence, status,
scopes, updatedAtUtc, contentSha256, matchReason, score,
rank {scopeTier, priority, statusRank}, provenance {kind, producer, sourceRef},
conflict? {groupId, state, alternativeCount, alternativePackIds, resolution}
```

`details` is not returned in v1 (it can contain arbitrary producer JSON).

### 7.5 Result caps

- Candidate rows per pack are capped at `min(max(limit * 4, limit + 10), 200)` to bound
  dedup/conflict work. Hitting the cap emits `r7-result-candidate-cap`.
- More logical identities than `limit` emits `r7-result-limit-truncated`.
- The serialized result is capped at `maxOutputChars` (default 65,536). Items are appended
  until the cap; the remainder is dropped and the response carries `r7-output-truncated`.
  A single item that alone exceeds the cap fails `r7-output-too-large`.
- Query timeout aborts the whole query (`r7-query-timeout`); partial results are never
  returned as success.

### 7.6 Agent context projection (R7.1, additive)

R7.1 adds a separate, versioned read-only DTO (`schemaVersion=1`,
`kind=r7-agent-context-projection`) on top of `ReadOnlyMountAdapter`. It is not a query
replacement and does not change the v1 result shape.

- The projector requires a verified adapter/caller; demo or raw identity is rejected.
- It returns bounded `excerpt` text (never full `body` or `details`), pack metadata, stable
  order, and `conflicts` only when `includeConflicts=true`.
- Every item and the top-level `trust` block label pack text as
  `untrusted-source-content` with `instructionPolicy=never-execute`.
- `budgets` expose record/pack/excerpt counts, serialized byte count, and a documented
  character/4 approximate-token count. Exceeding a budget truncates from the tail; a single
  item that cannot fit fails `r7-context-output-too-large`.
- Local absolute `provenance.sourceRef` values are redacted; local cache paths, claims,
  session ids, token contents, `details`, and full records are not emitted.
- The dependency direction is `context -> adapter -> service`; no production service imports
  `memory_mount`, and no MCP tool is added.

## 8. Pack index

The pack index is a **derived, disposable, read-only** SQLite database. It is not Project
Memory and never shares a connection, schema version, or file with schema v6.

| Property | Value |
| --- | --- |
| Index format | `r7-pack-index-v1` |
| `PRAGMA user_version` | `1` (independent of memory schema v6) |
| Tokenizer | FTS5 `trigram` |
| Tables | `pack_index_meta`, `pack_records`, `pack_scopes`, `pack_records_fts` |
| FTS binding | external content over `pack_records`, `content_rowid='rowid'` |
| Cache key | `<cacheDir>/<packId>-<contentHash[7:19]>.sqlite3` |
| Build | temp file + atomic replace; never writes inside the pack |
| Open | `file:...?mode=ro&immutable=1`, `PRAGMA query_only=ON` |
| WAL | not used |
| Reuse | only when meta matches and every stored record content hash re-verifies |
| Rebuild | missing, corrupt, incompatible meta, or tampered file |

Required meta keys: `indexFormatVersion`, `packId`, `contentHash`, `revision`,
`recordCount`, `tokenizer`, `builtAtUtc`.

The cache directory MUST NOT be inside a pack root; index writes never touch pack bytes.

The index MUST be verified against the manifest content hash even when the cache filename
already contains a hash prefix. A hash-prefix filename is a cache hint, not a trust decision.
On reuse, the reader also recomputes every stored record's content hash and compares the
stored pack/record identity, status, confidence, and timestamp; a tamper in those fields is
treated as incompatible and triggers a rebuild. A corrupted index is rebuilt from the verified
pack; if rebuild fails, mount fails closed.

The cache directory is still **not a cryptographic trust boundary**: an attacker who can write
both the cache and its SQLite shadow tables may be able to poison FTS match data despite the
row checks. Production integration MUST either place the cache in a user-private directory,
rebuild per session, or add a signed cache digest. This PoC documents the limit rather than
claiming cache authenticity.

## 9. Resource limits

Defaults (overridable only by the embedding process, not by pack data):

| Limit | Default | Enforced at |
| --- | ---: | --- |
| Manifest bytes | 262,144 | mount |
| `records.jsonl` bytes | 16,777,216 | mount |
| Records per pack | 5,000 | mount |
| Mounted packs per session | 8 | mount |
| Total mounted records | 20,000 | mount |
| Record body chars | 32,768 | mount |
| Record title chars | 512 | mount |
| Subject key chars | 256 | mount |
| Scopes per record | 32 | mount |
| Scope key chars | 512 | mount |
| Record details bytes | 16,384 | mount |
| Query chars | 128 | query |
| Result limit | 50 | query |
| Candidate cap per pack | 200 | query |
| Timeout | 250 ms (max 2,000) | query |
| Output chars | 65,536 | query |
| Body preview chars | 600 (max 4,000) | query |
| Short-query scan records | 20,000 | query |

Limits are checked before work where possible and fail with the codes in §10. Pack-supplied
values cannot raise limits.

## 10. Error codes

All errors are structured:

```json
{"ok": false, "error": {"code": "r7-pack-hash-mismatch", "message": "...", "details": {}}}
```

| Code | Exit | Meaning |
| --- | ---: | --- |
| `r7-manifest-invalid` | 2 | Manifest shape/type/unknown-field failure. |
| `r7-manifest-unsupported-schema` | 2 | `schemaVersion` not `1`. |
| `r7-manifest-incompatible` | 2 | Reader compatibility or required feature failure. |
| `r7-signature-unsupported` | 2 | Non-null signature in v1. |
| `r7-identity-required` | 4 | Production entry point received an unverified/demo caller. |
| `r7-identity-claims-invalid` | 4 | Claim envelope/payload shape or time fields invalid. |
| `r7-identity-signature-invalid` | 4 | Claim HMAC/signature check failed. |
| `r7-identity-expired` | 4 | Verified claims expired. |
| `r7-identity-issuer-mismatch` | 4 | Claim issuer is not trusted. |
| `r7-identity-audience-mismatch` | 4 | Claim audience is not accepted. |
| `r7-identity-project-mismatch` | 4 | Claim project does not match the request. |
| `r7-integrity-receipt-required` | 6 | Adapter policy requires a manifest receipt. |
| `r7-integrity-receipt-invalid` | 7 | Receipt shape/type/encoding invalid. |
| `r7-integrity-receipt-mismatch` | 7 | Receipt invariant does not match the manifest. |
| `r7-context-request-invalid` | 2 | Context budget/query/filter invalid. |
| `r7-context-pack-limit` | 6 | Mounted packs exceed the context pack budget. |
| `r7-context-output-too-large` | 6 | One item cannot fit the context output/token budget. |
| `r7-context-invalid-record` | 7 | Adapter returned a malformed record projection. |
| `r7-pack-path-invalid` | 2 | Non-directory, traversal, or symlink path. |
| `r7-pack-not-found` | 3 | Pack files absent/unreadable. |
| `r7-pack-not-mounted` | 3 | Unmount/query references an unknown mount. |
| `r7-pack-too-large` | 6 | Manifest/records/count limit exceeded. |
| `r7-pack-hash-mismatch` | 7 | Content hash does not match bytes. |
| `r7-pack-records-invalid` | 7 | Malformed/non-canonical record line. |
| `r7-pack-duplicate-id` | 7 | Duplicate `recordId` within a pack. |
| `r7-pack-revision-conflict` | 5 | Same `packId`, different content hash. |
| `r7-pack-already-mounted` | 5 | Conflicting mount for the same identity. |
| `r7-mount-limit` | 6 | Mount count or total records exceeded. |
| `r7-permission-denied` | 4 | Personal pack owner mismatch or empty principal. |
| `r7-project-mismatch` | 4 | Project pack does not match active project. |
| `r7-query-invalid` | 2 | Empty/oversized/control-character query or bad filter. |
| `r7-query-too-broad` | 6 | Bounded scan would exceed its record cap. |
| `r7-query-timeout` | 6 | Deadline exceeded; no partial result. |
| `r7-output-too-large` | 6 | A single result item exceeds the output cap. |
| `r7-index-incompatible` | 7 | Index meta/version mismatch that cannot be rebuilt. |
| `r7-fts5-trigram-unavailable` | 7 | Runtime lacks FTS5 trigram. |
| `r7-io-error` | 1 | Read/build failure not classified above. |
| `r7-internal` | 1 | Invariant violation; fail closed. |

## 11. Security and privacy invariants

1. **Read-only**: the PoC opens pack files read-only and index files with `mode=ro` +
   `query_only`. It contains no `INSERT`/`UPDATE`/`DELETE` path for pack data at query time.
2. **No production coupling**: `memory_mount` MUST NOT be imported by production services;
   it does not open the coordination database, the Project Memory database, or any MCP
   transport.
3. **Path confinement**: only `<root>/pack.json` and `<root>/records.jsonl` are read.
   Symlinks and escapes are rejected. Pack content never names a path to open.
4. **No code execution**: JSON only; no `eval`, no shell, no SQL from pack content.
5. **Parameterized SQL**: manifest values and query text never become SQL text.
6. **Privacy**: personal packs are visible only to their owner; `listMounts` and query
   results do not expose local paths; bodies are preview-capped and `details` is omitted.
7. **Deny by default**: unknown visibility/scope/feature fails; there is no "best effort"
   permission mode.
8. **No elevation**: mounting a shared pack cannot unlock project or personal packs.
9. **No write-back**: query/conflict handling never mutates packs, indexes, or Project
   Memory. Conflict resolution is a read-time view.
10. **Fail closed on timeout/integrity**: no partial success on timeout; no stale index after
    a hash mismatch.

## 12. Relationship to Project Memory schema v6

- Schema v6 is unchanged. `CURRENT_MEMORY_SCHEMA_VERSION` stays `6`.
- The pack record vocabulary intentionally mirrors v6 types, source kinds, statuses, and
  scopes so a later, separately approved bridge can map them without inventing a new
  ontology.
- The pack index uses its own `user_version=1` and its own tables. It MUST NOT be attached
  to a v6 connection, and no v6 migration is added by R7.
- If a future phase imports packs into Project Memory, it MUST be a separate write contract
  with idempotency keys, provenance mapping, conflict policy, and user approval. This
  document does not authorize it.

## 13. Chinese retrieval note

Schema v6 uses FTS5 `unicode61`, where a run of Han characters is one token. P1.1 added a
bounded literal substring supplement for the production CLI; R7 does not change that path.
For packs, the index uses the `trigram` tokenizer, which is purpose-built for substring
matching and works for 3+ character Chinese phrases. Short (1–2 character) Chinese queries
use the bounded `instr()` fallback described in §7.2.

This is **literal substring retrieval**, not segmentation, embeddings, or semantic search.
Results and reports MUST NOT claim otherwise.

## 14. Decisions required before any R7 write phase

The read-only contract can be accepted independently. The following require explicit user
decisions and are **not** implemented:

1. **Trust model**: are packs signed? Which keys/authors are trusted? Is `provenance` enough
   for review-only use, or is a detached signature required before mounting?
2. **Principal identity**: what stable id backs `scope.owner` (OS user, DSH session, agent
   id)? How is it proven rather than self-asserted?
3. **Precedence policy**: should `project` always outrank `personal` (current default), or
   may a personal pack override non-safety project knowledge? Who approves exceptions?
4. **Shared pack distribution**: local directory only, or a reviewed repository/registry?
   Who may publish, and how are updates and revocations handled?
5. **Promotion/write path**: can a mounted pack record be promoted into Project Memory?
   If so, what idempotency key, status transition, and user confirmation are required?
6. **PII/redaction**: what may a shared pack contain, and what scanning/redaction is
   mandatory before publication?
7. **Retention and audit**: how long are index caches and query logs kept? Are query texts
   or record bodies logged? (Current PoC logs neither.)
8. **Production integration**: when, if ever, should `memory_mount` be exposed through MCP?
   Which tool names, scopes, and rate limits?
9. **Schema evolution**: keep the pack index separate indefinitely, or migrate to a future
   memory schema v7? If v7, it needs its own migration/review.
10. **Multi-user concurrency**: R7 v1 is session-local; cross-process catalogs and
    multi-writer coordination are explicitly out of scope.

## 15. Acceptance criteria for this segment

- [x] Normative manifest, mount/unmount/list/query, precedence, conflict, privacy, index,
      limits, timeout, and error-code contract.
- [x] Machine-readable manifest and record schemas.
- [x] Offline, isolated PoC under an independent module; no production imports.
- [x] Tests use only self-built fixtures and temporary/`Build/r7-isolated` databases.
- [x] No writes to `coordination_memory.sqlite3`, its backups, or real Memory data.
- [x] Schema v6 unchanged; no session whitelist changes; no push.
- [ ] User decisions in §14 (write/privacy boundaries) — intentionally open.
