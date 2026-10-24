# M3.9 — Pre-M4 Z5/Z6 combined-branch readiness — Report

- Date: 2026-10-25
- Worktree: `E:\WorkSpace\UEAgentKit-WT\m3-r1-integration` (existing)
- Branch: `feature/m3-r1-integration-m38`
- Base M3.8: `1f093f9c6d2b8bf5064ed3e7b202b5a53b78cb50`
- Source commit: `c73806854a32b17febc3ec40fd1d52f363abfab6`
- UE DLL (unchanged this stage): 4,483,072 B,
  `sha256:A10379D4581FD4EA6D002DF3CD1F35142AC4BA277EFD753EC612FFA1A6D2447D`
- Status: **Z5 check-only PASS on disposable fixtures; Z6 read-only evaluator PASS; production canary/telemetry and M4 remain BLOCKED**
- No new worktree/branch/session; no push; no main/R1/R7 edits; no default-on.

## 1. Gate status

| Gate | Status | Meaning |
|---|---|---|
| Z5 eligibility (disposable, check-only) | **PASS** | eligible fixture accepted; missing marker/allowlist/ACL/stale descriptor fail closed; no token/path emitted |
| Z5 production eligibility | **BLOCKED** | requires Z4 approval, a named production project, approved path-hash allowlist, manually installed broker and owner sign-off; none granted |
| Z6 read-only evaluator | **PASS** | deterministic aggregates over explicit positive-only and negative-heavy datasets |
| Z6 production telemetry/alerting | **BLOCKED** | no live production stream, alert routing, dashboards, or approved thresholds |
| Three-graph Native Preview determinism | **PASS** | 3 graphs × 2 runs, receipt = canonical Python plan, asset SHA unchanged |
| Legal legacy fallback | **PASS** | broker missing yields a fallback plan, readOnly, no save/compile |
| Strict dirty negative | **PASS** | dirty package rejected, no fallback, not reported as success |
| Legacy P11 menu | **PASS** | rerun on the combined build; registry off log; read-only receipt |
| Legacy P11 selection/incremental/reload | **PASS (reused)** | M3.8 evidence remains authoritative; no plugin/C++ change in M3.9 |
| M4 / default-on | **BLOCKED** | Z4/Z5/Z6 approvals still missing |

## 2. Z5 — read-only eligibility check on disposable fixtures

Command:

```powershell
py -3.12 tools/m39_z5_check.py
```

The tool builds disposable opt-in projects under `Build/m39-readiness/z5` and calls the existing
`run_canary_preflight` (no source change to Z5). Cases:

| Case | Decision | Failed check |
|---|---|---|
| eligible (marker + allowlist + protected descriptor) | ELIGIBLE | — |
| missing marker | BLOCKED | `marker-missing-or-invalid` |
| allowlist denies the project hash | BLOCKED | `allowlist-missing-or-denied` |
| inherited/unsafe descriptor ACL | BLOCKED | `descriptor-missing-unsafe-or-stale` |
| 13-hour stale descriptor | BLOCKED | `descriptor-missing-unsafe-or-stale` |

Redaction: `tokenEmitted=false`, `absolutePathEmitted=false`. Facts contain only project name,
project-path **hash**, descriptor basename, ACL state, age, broker version. No token value or
absolute project path is emitted. The check never writes a project, marker, descriptor, ACL or
service.

**Production gap (decision only, not implemented):** an approved production canary still needs a
named project + owner, an approved allowlist entry, a manually installed/started broker with a
protected descriptor, Z4 approval, and an operator. M3.9 does not deploy or enable any of this.

## 3. Z6 — dataset separation and false-positive criteria

Command:

```powershell
py -3.12 tools/m39_z6_datasets.py
```

Two explicit input populations are built from combined-branch evidence and evaluated separately by
the existing read-only evaluator:

| Dataset | Attempts | Native success | Fallback | Native failure | Security | Unexpected writes | Token fields | Breaches |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Positive-only (M3.9 six native happy runs) | 6 | 6 | 0 | 0 | 0 | 0 | 0 | **none** |
| Negative-heavy (M3.8 combined matrix) | 8 | 1 | 2 | 5 | 5 | **0** | **0** | fallback-rate, native-failure-rate, any-security-failure (expected) |

Criteria recorded in `z6-datasets.json`:

- positive-only stream: **0** breaches, security failures, writes and token fields;
- negative-heavy stream: expected security/fallback/failure flags, but still **0** writes and **0**
  tokens; it must never be used as a production rate baseline;
- only positive-only streams may be compared with the proposed, **unapproved** thresholds
  (fallback > 5%, native failure > 2%, any security/write).

Latency (receipt wall time): positive-only p50 **105 ms**, p95 **400 ms**, max 400 ms; negative-heavy
p50 379 ms / p95 5,541 ms (includes the intentional timeout case). This is a small sample, not an
SLA.

### Z6 defects reviewed and fixed

1. **Token-key detection was too narrow.** `_TOKEN_KEY_RE` matched only the exact key `token`, so a
   receipt containing `authToken`/`brokerToken` would not be flagged. Fixed to match any key
   containing `token`, with a regression test
   (`test_token_named_keys_with_prefixes_are_detected`); the test also asserts the value never
   appears in evaluator output. Commit `c738068`.
2. **Input contract gap (documented, not core-changed).** The evaluator consumes the `z1/z2/z3`
   evidence shape; the combined-branch `combined-m3.json` has a different top-level shape. The
   M3.9 dataset tool adapts it into the documented `z2-expanded-matrix.json` shape rather than
   widening the evaluator's implicit contract. A future production telemetry ingestion schema
   remains an M4 decision.
3. No Z5 functional defect was found; its production deployment remains an approval/operations
   gap, not a code gap.

## 4. Three-graph Native Preview determinism

Command: `py -3.12 tools/m39_three_graph_native.py` (external loopback broker, two runs per graph,
real `UEAgentKit.Native.M3Menu` automation, minimized `-NullRHI` editors).

| Graph | Nodes / moved | planId | layoutHash (prefix) | Deterministic | Python parity | Asset SHA unchanged |
|---|---|---:|---|---|---|---|
| `/Game/M35/BP_M35_Stress` | 53 / 53 | `layout:0ffbc88b92b04fba` | `sha256:6a855e98…df97` | yes | yes | yes (`sha256:31b3409e…08fe`) |
| `/Game/UEAgentKitP11/BP_LayoutP11` | 15 / 15 | `layout:8e53b79547580557` | `sha256:c8390924…3f09` | yes | yes | yes (`sha256:e80e4b80…268a`) |
| `/Game/UEAgentKitP10/BP_LayoutP10` | 11 / 11 | `layout:b334bcc4771c3de5` | `sha256:d236e734…c025` | yes | yes | yes (`sha256:3d52f788…0c7a`) |

Determinism compares receipt `planId`, `layoutHash`, sorted `movedNodeIds`, and the canonical Python
plan position hash across the two runs. Every receipt reported `readOnly=true`, `saved=false`,
`compiled=false`, `packageDirtyAfter=false`. Wait/invoke samples: M35 ≈ 0.320 s / 0.062 s;
P11 ≈ 0.066 s / 0.044 s; P10 ≈ 0.043 s / 0.041 s.

Fallback: broker stopped/descriptor removed → `fallback=true`,
`nativeErrorCode=native-preview-broker-descriptor-missing`, plan produced, readOnly, no write.
Negative: dirty package → `ok=false`, `fallback=false`,
`errorCode=native-preview-package-dirty`, no save/compile — an error is not disguised as success.

## 5. Legacy P11 compatibility

M3.9 changed only Python pre-M4 code, tools and tests; no C++/plugin source changed and no rebuild
was performed. The M3.8 P11 evidence therefore remains valid (selection scope 5/moved 2,
incremental scope 5/moved 2, Undo/Redo/Save, second-process reload, menu receipt). A fresh
`P11MenuContract` run on the same combined build passed with registry-off logging and receipt
`planId=layout:528200c7b0452e09`, `readOnly=true`, dirty before/after false.

## 6. Python regression

| Suite | Result |
|---|---|
| Repository full (`RunPythonTests.py full`) | **1389 tests, 17 skipped, OK**, 141.491 s |
| `tests/native_tools` | **57 tests, OK**, 17.894 s |
| Z5/Z6 targeted tests | 8 tests OK |

## 7. Evidence (Build/m39-readiness, gitignored)

| File | Bytes | SHA256 |
|---|---:|---|
| `three-graphs.json` | 17,526 | `BD1F6E14…9C52` |
| `z5/z5-preflight.json` | 2,522 | `8208C76F…8849` |
| `z6/z6-datasets.json` | 3,045 | `2C4C7743…8559` |
| `python-full.log` | 4,288 | `481E1EC5…7404` |
| `python-native-tools.log` | 162 | `3E5B4C06…0FEF` |
| `P11Menu/index.json` | 1,786 | `64C0EF00…2A4C` |
| P11Only `menu-invocation.json` | 1,023 | `F113AEA6…5167` |

No token, absolute path or production credential is present in the emitted summaries.

## 8. Limitations / M4 decision items

- Three disposable graphs and six positive runs only; no production-scale or macro-instance proof.
- Z5 production eligibility needs Z4 approval, a named owner, approved allowlist, manual broker
  installation and operator sign-off — **not implemented**.
- Z6 production telemetry needs a live stream, alert routing, approved thresholds and retention;
  current numbers are test matrices, not production observability.
- Positive latency p50/p95 is a 6-sample observation, not an SLA.
- Legacy fallback remains synchronous and can block a tick; no cancel UI beyond busy rejection.
- Defaults stay off (`NativeLayoutPreview=off`, `NativeToolRegistry=off`); M4/default-on is not
  approved and was not started.

## 9. Reproduce

```powershell
py -3.12 tools/m39_three_graph_native.py
py -3.12 tools/m39_z5_check.py
py -3.12 tools/m39_z6_datasets.py
py -3.12 scripts/RunPythonTests.py full
py -3.12 -m unittest discover -s tests/native_tools -p "test_*.py"
```

All UE runs used the disposable `Build/m37-integration/P11AutomationHost`, `-NullRHI`, minimized,
serial; no production project, Lyra, Reforge or broker service was touched.
