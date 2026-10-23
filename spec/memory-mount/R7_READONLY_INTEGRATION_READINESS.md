# R7 Read-only Integration Readiness / Trust Hardening

- Date: 2026-10-24 (Asia/Hong_Kong)
- Model: DSH default `cline-pass/deepseek-v4.1-flash-1017` (non-Fast); verified before changes
- Worktree: `E:\WorkSpace\UEAgentKit-WT\memory-mount-r7`
- Branch: `feature/memory-mount-r7`
- Start HEAD: `7d9b34d1d4b30454e114a4641bd76ae90535090f`
- Baseline: `main` `c5ce3419c6962da49e874586f4e5dbd1457a4d0d`
- Status: **read-only preparation complete; production integration NOT performed**

> This document is a threat-model and wiring proposal backed by an isolated PoC. It is not a
> production security claim. R7 is still not imported by `mcp_server.py`, `memory_service.py`,
> or any default service. No DSH token, Session whitelist, real Memory DB, or backup was used.

## 1. Scope and non-goals

### 1.1 In scope

1. Separate verified caller claims from untrusted input at the API boundary.
2. Add a v1-compatible manifest integrity receipt that protects fields `contentHash` does not.
3. Add an isolated read-only adapter example that accepts only verified claims.
4. Add negative regression for identity, receipt tamper, path/cache poisoning, short Chinese,
   and multi-pack conflicts.
5. Record microbench observations without inventing pass/fail thresholds.

### 1.2 Non-goals

- No production MCP/Memory integration; no modification of `mcp_server.py`,
  `memory_service.py`, `cli.py`, `memory_context.py`, or schema v6.
- No connection to DSH tokens, the coordination SQLite, its backups, or the Session whitelist.
- No cross-person shared writes, no pack promotion, no automatic maintenance, no network sync.
- No cryptographic author trust: the receipt is digest-only; non-null signatures are rejected.
- No claim that the local HMAC claim verifier is production authentication.

## 2. Identity boundary

### 2.1 Untrusted input

The following are **never** treated as identity:

| Input | Trust |
| --- | --- |
| CLI `--principal` / `--project-key` | `untrusted-demo`; printed warning; not authentication |
| Pack directory name passed to the adapter | untrusted; confined to `pack_root` |
| Query text, filters, pack records, manifest provenance | untrusted data |
| Raw string passed where a caller context is required | rejected (`r7-identity-required`) |

The demo CLI now explicitly prints:

```text
warning: r7-untrusted-demo-identity (--principal/--project-key are not authentication)
```

and its JSON response contains `identity.trustMode = "untrusted-demo"`,
`identity.verified = false`.

### 2.2 Verified context

`claims.py` defines two distinct frozen types:

```text
VerifiedCallerContext   principal, sessionId, projectKey, issuer, audience,
                        issuedAt, expiresAt, claimId
DemoCallerContext       explicit untrusted demo identity
```

`VerifiedCallerContext` cannot be constructed directly: its private token makes direct
construction raise `TypeError`. It is returned only by a `ClaimVerifier` after verification.
This is an **accidental-misuse** boundary, not a Python sandbox against adversarial code.

The injection contract is:

```python
class ClaimVerifier(Protocol):
    def verify(self, envelope: str) -> VerifiedCallerContext: ...

registry = MemoryMountRegistry.from_verified(
    caller=verified_context,
    cache_dir=...,
)
```

Host responsibilities:

1. Authenticate the external caller with the host's real mechanism.
2. Authorize the requested project/session.
3. Inject claims; do not forward raw CLI labels as verified claims.
4. Keep the verifier/key material outside pack content and query input.

### 2.3 Fail-closed checks

`VerifiedCallerContext.assert_valid()` rejects:

| Check | Error |
| --- | --- |
| wrong audience | `r7-identity-audience-mismatch` |
| wrong project | `r7-identity-project-mismatch` |
| expired claims | `r7-identity-expired` |
| issue time too far in the future | `r7-identity-claims-invalid` |

`MemoryMountRegistry.from_verified()` rejects `DemoCallerContext` or a raw string with
`r7-identity-required`. The registry re-checks expiry before every `mount` and `query`, so a
session that expires cannot keep querying.

### 2.4 Test-only HMAC example

`HmacClaimVerifier` / `TestClaimIssuer` are a local example, not a production token format:

- HMAC-SHA256 over canonical payload;
- issuer, audience, `iat`/`exp`, claim id, principal, session, project;
- max TTL and clock-skew checks;
- test secret only; no key management, revocation, DSH bridge, or whitelist semantics.

The tests exercise valid claims, tampered payload, expired claims, wrong issuer/audience, and
direct-construction rejection. A real deployment must replace this pair with its own verifier.

Known limits of this example: there is no replay cache (a valid envelope can be replayed until
`exp`), no revocation list, no key rotation, and no binding to a transport channel. A host must
add those controls; `claimId` is present but the PoC does not persist it.

## 3. Manifest integrity receipt

### 3.1 Gap in v1

`contentHash` covers only the exact bytes of `records.jsonl`. It does not cover manifest
`priority`, `scope`, `revision`, `provenance`, or `recordCount`. An attacker or an accidental
edit can change a pack from `shared` to `project`, raise `priority`, or alter provenance while
the records hash still matches.

### 3.2 Compatible extension

R7 adds an **optional sidecar** `pack.integrity.json`; v1 readers already ignore unknown files,
so existing fixtures need no change. No manifest field is added, no v1 fixture is broken, and
no v2 manifest is required for this step.

Receipt fields:

```text
receiptVersion  1
packId          manifest.packId
contentHash     manifest.contentHash
revision        manifest.revision
priority        manifest.priority
recordCount     manifest.recordCount
manifestDigest  sha256(canonical_json(manifest))
scopeDigest     sha256(canonical_json(manifest.scope))
provenanceDigest sha256(canonical_json(manifest.provenance))
signature       absent/null in v1; non-null -> r7-signature-unsupported
createdAtUtc    optional, ISO-8601
```

Verification is fail-closed if the receipt is present but any invariant differs
(`r7-integrity-receipt-mismatch`). A malformed receipt is
`r7-integrity-receipt-invalid`. A symlinked or escaping receipt is
`r7-pack-path-invalid`.

### 3.3 Trust levels

| State | `integrityMode` | `receiptVerified` | `authorAuthenticity` |
| --- | --- | ---: | --- |
| No receipt (legacy v1) | `records-only-unverified-manifest` | false | `not-established` |
| Matching digest receipt | `manifest-digest-verified` | true | `not-established` |
| Non-null signature | rejected | — | not claimed |

Important distinction:

- A digest receipt detects **accidental or local manifest tamper** only if the receipt itself
  is not replaced. It is not a signature.
- A malicious actor who can rewrite both `pack.json` and `pack.integrity.json` can produce a
  new matching pair. Only a signed receipt with a trusted key can establish author authenticity,
  and v1 deliberately rejects signatures rather than pretending to verify them.
- `signed=false` and `authorAuthenticity=not-established` are always present in the report.

### 3.4 Compatibility / v2 draft

Current path: sidecar, v1-compatible. If a future version wants the receipt embedded in the
manifest, or wants a detached signature, it MUST be a new schema draft (v2) with:

- a signature algorithm/key-id/revocation model;
- a canonicalization rule for the signed bytes;
- a migration that still accepts v1 packs as legacy-unverified;
- explicit user approval for trusted authors.

No v1 fixture was changed. Existing `pack.json` files remain valid; they simply report
`records-only-unverified-manifest` until a receipt is added.

## 4. Read-only adapter example

`adapter.py` is intentionally not imported by production services. It demonstrates a future
integration boundary:

```python
adapter = ReadOnlyMountAdapter(
    caller=verified_caller,          # required; demo/raw strings rejected
    pack_root=...,                   # root confinement
    cache_dir=...,
    require_integrity_receipt=True,  # strict policy example
)
adapter.mount("r7.pack.name")        # name is untrusted; regex + resolve + parent check
adapter.query("知识")
```

Controls:

- `VerifiedCallerContext` required at construction; no `principal`/`project_key` arguments.
- `packName` must match `^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$`; `..`, separators, absolute paths,
  and drive letters are rejected; resolved parent must equal `pack_root`.
- Optional receipt requirement (`r7-integrity-receipt-required`); default `True` in the adapter
  example, while the base registry remains v1-compatible.
- Cache poisoning is detected by the existing meta + row/content-hash verification and the
  index is rebuilt from the verified pack.
- `query` accepts only query text/filters; identity comes from the injected context.

The adapter is a wiring example, not a server: it has no MCP transport, no persistence, no
multi-user catalog, and no write API.

## 5. Negative regression matrix

| Area | Negative case | Result |
| --- | --- | --- |
| Identity | direct `VerifiedCallerContext(...)` | `TypeError` |
| Identity | tampered HMAC payload | `r7-identity-signature-invalid` |
| Identity | expired claims | `r7-identity-expired` |
| Identity | wrong issuer / audience | issuer/audience mismatch |
| Identity | demo context into `from_verified`/adapter | `r7-identity-required` |
| Identity | raw string caller | `r7-identity-required` |
| Identity | expiry after mount, before query | `r7-identity-expired` |
| Receipt | no receipt | legacy mode + `r7-manifest-integrity-unverified` |
| Receipt | priority tamper | `r7-integrity-receipt-mismatch` |
| Receipt | scope tamper (`scopeDigest`) | `r7-integrity-receipt-mismatch` |
| Receipt | provenance tamper (`provenanceDigest`) | `r7-integrity-receipt-mismatch` |
| Receipt | receipt contentHash tamper | `r7-integrity-receipt-mismatch` |
| Receipt | non-null signature | `r7-signature-unsupported` |
| Adapter | `../`, `a/b`, `a\b`, drive path, `.`, `..`, empty | `r7-pack-path-invalid` |
| Adapter | missing required receipt | `r7-integrity-receipt-required` |
| Adapter | demo context / raw string | `r7-identity-required` |
| Query | 2-character Chinese on project/shared conflict | fallback match + project winner |
| Index | tampered index body | rebuild from verified pack |
| Production | `memory_service.py`, `mcp_server.py`, `cli.py` | no `memory_mount` import |

## 6. Microbenchmark observations

Environment: Python 3.12.10, SQLite 3.49.1, Windows, local SSD, 5,000 records
(~2.13 MB records.jsonl, ~5.50 MB index). No pass/fail threshold is asserted.

| Metric | Observation |
| --- | ---: |
| Pack write + canonical JSON fixture | 33.5 ms |
| Load + manifest/receipt/record verification | 181.4 ms |
| First mount (build index) | 349.0 ms |
| Remount (index reuse) | 245.5 ms |
| FTS5 trigram query, limit 20 | median 8.40 ms, p95 8.97 ms |
| 2-char short-query fallback, limit 20 | median 6.07 ms, p95 6.40 ms |
| Index size | 5,496,832 bytes |
| `indexReused` | true |

The index-reuse path is not automatically faster in this observation because it re-verifies
every stored record content hash before reuse. That is an intentional integrity trade-off, not
a performance claim. The microbench script is
`tests/python/r7_mount_microbench.py`; raw output is
`Build/r7-isolated/Evidence/microbench.json`.

## 7. Test results

| Suite | Result |
| --- | ---: |
| Original R7 suite (`test_memory_mount.py`) | **49/49 PASS**, 1.385 s |
| Readiness suite (`test_memory_mount_readiness.py`) | **27/27 PASS**, 0.468 s |
| Combined R7 discovery | **76/76 PASS**, 1.834 s |
| Existing Memory regression (8 files) | **90/90 PASS** |
| Total real tests this round | **166 PASS**, 0 fail / 0 error / 0 skip |

Readiness suite split: 10 identity tests, 17 integrity/adapter/negative tests.

No production files were modified. The only new/changed paths are under
`src/ue_agent_kit/memory_mount/`, `tests/python/`, and `spec/memory-mount/`.

## 8. Residual risks and human decisions

The following require explicit human adjudication before production integration or
cross-person shared writes:

1. **Identity source**: which authority signs claims (DSH session, OS user, MCP auth, enterprise
   IdP)? What is the principal/session/project binding, TTL, revocation, clock policy, replay
   cache, and transport binding?
2. **Signature/trust**: are pack receipts signed? Which keys/authors are trusted? How are keys
   rotated/revoked? R7 v1 currently rejects signatures rather than overclaiming.
3. **Precedence**: does `project > personal > shared` remain permanent? May personal knowledge
   override non-safety project knowledge? Who approves exceptions?
4. **Shared distribution**: local reviewed directory only, or a registry? Who may publish,
   update, and revoke a shared pack?
5. **Promotion/write path**: may a mounted pack record be promoted into Project Memory? What
   idempotency key, status transition, user confirmation, and rollback are required?
6. **PII/redaction**: what may a shared pack contain, and what scanning is mandatory?
7. **Retention/audit**: how long are index caches, claim logs, query texts, and evidence kept?
   Who can read them?
8. **Production surface**: when/if R7 is exposed through MCP, what tool names, scopes, rate
   limits, and audit events apply? It must remain unimported until then.
9. **Schema evolution**: keep the sidecar/digest approach, or move to a v2 manifest with
   embedded signatures? Any v2 must preserve v1 legacy behavior.
10. **Multi-user concurrency**: R7 is session-local; cross-process catalogs and multi-writer
    coordination need a separate design.

No default production import was added. `mcp_server.py`, `memory_service.py`, `cli.py`, and
`memory_context.py` remain untouched.

## 9. Files, commands, commit

New PoC files:

- `src/ue_agent_kit/memory_mount/claims.py`
- `src/ue_agent_kit/memory_mount/integrity.py`
- `src/ue_agent_kit/memory_mount/adapter.py`
- `tests/python/test_memory_mount_readiness.py`
- `tests/python/r7_mount_microbench.py`
- `spec/memory-mount/r7-pack-integrity.schema.json`
- `spec/memory-mount/r7-claim-envelope.schema.json`

Modified: `service.py`, `records.py`, `cli.py`, `errors.py`, `__init__.py`,
`R7_MOUNT_CONTRACT.md`, `R7_READONLY_POC_REPORT.md`.

Commands:

```powershell
py -3.12 -m unittest discover -s tests/python -p "test_memory_mount.py" -v
py -3.12 -m unittest discover -s tests/python -p "test_memory_mount_readiness.py" -v
py -3.12 -m unittest discover -s tests/python -p "test_memory_mount*.py" -v
py -3.12 tests/python/r7_mount_microbench.py
```

Local commit: `__R7_READINESS_COMMIT__` (code + readiness docs; no push).

## 10. Explicit non-claims

- The HMAC verifier is a test fixture, not DSH authentication.
- A digest receipt is not a signature and does not establish a trusted author.
- The adapter is not a server, does not persist mounts, and is not wired into production.
- No real Memory database, backup, Session whitelist, or cross-person shared store was touched.
- No performance threshold or production SLA is claimed from the microbench.
