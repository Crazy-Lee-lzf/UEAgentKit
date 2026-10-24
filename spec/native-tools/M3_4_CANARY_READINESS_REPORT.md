# M3.4 Pre-M4 Z5/Z6 Operational Readiness — Report

- Date: 2026-10-24
- Worktree: `E:\WorkSpace\UEAgentKit-WT\layout-broker-m1`
- Branch: `feature/layout-broker-m1`, base M3.3 `1ba508e` + UX `b3e3ae9`
- Engine: UE 5.6.1 (no plugin build/change required this stage)
- Status: **Z5 CHECK-ONLY PASS on disposable fixtures; Z6 read-only evaluator PASS; production canary BLOCKED**
- `NativeLayoutPreview=off`, `LegacyLayoutPreviewFallback=true`; **M4 default-on NOT APPROVED**

## 1. Session/model

Same DSH session `session-7fd8360d-3ac6-47f5-898e-4d52afd3e59d`; runtime declares
`cline-pass/deepseek-v4.1-flash-1017`, non-Fast/max reasoning, no model switch. No Editor build or heavy
runtime was needed; no production project touched.

## 2. Z5 — canary eligibility preflight (design + check-only proof)

New offline module/tool: `src/ue_agent_kit/pre_m4/canary_preflight.py` and
`tools/m34_canary_preflight.py`.

Candidate policy:

- exactly one `.uproject` and an explicit `Saved/UEAgentKit/CanaryOptIn.json`
  (`ueak.canary-optin/1.0`, exact keys, enabled, matching project name);
- project path hash present in an explicit allowlist (`ueak.canary-allowlist/1.0`);
- a descriptor at the standard path that validates project name/hash, 1.0 contract, token shape,
  12-hour freshness, and a **PROVEN** current-user-only Windows DACL;
- fail closed on missing/unsafe marker, allowlist, descriptor, ACL, token, or freshness.

The tool is strictly read-only: it does not create a marker, descriptor, ACL, service, or project file.
Descriptor token values and absolute paths are not emitted. Broker start/stop remains manual; there is no
Editor `CreateProc` and no Windows service install.

Disposable check results (under `Build/m2-native/M34/Preflight`):

| Case | Decision | Evidence |
|---|---|---|
| valid marker/allowlist/protected descriptor | **ELIGIBLE** | `M34/preflight-eligible.json` |
| missing descriptor | **BLOCKED** (`descriptor-missing-unsafe-or-stale`) | `M34/preflight-blocked.json` |
| inherited/unsafe ACL (`UNVERIFIED`) | **BLOCKED** | `M34/preflight-unsafe.json` |
| missing marker / missing allowlist / stale descriptor | BLOCKED in unit tests | `M34/unit-tests.log` |

No token appears in the emitted JSON (checked against the fixture token). This is a **design/check-only proof
on disposable data**, not production eligibility approval.

## 3. Z6 — read-only telemetry evaluator (design + proof)

New offline module/tool: `src/ue_agent_kit/pre_m4/telemetry_eval.py` and
`tools/m34_telemetry_eval.py`.

It consumes existing M3.3 `z1`/`z2`/`z3` JSON only; it does not start an Editor/broker and emits no token,
absolute path, or raw error message. Metrics: native success, fallback, native failure, busy, error codes,
security/identity codes, unexpected writes, token-key observations, and latency p50/p95/max. Expected
dirty-test receipts are separated from unexpected writes.

Real isolated M3.3 evidence (`M34/telemetry-real.json`):

- attempts **42**, native success **7**, fallback **10**, native failure **25**, busy **3**;
- latency p50 **55 ms**, p95 **5185 ms** (timeout tests included), max 5199 ms;
- security/identity code observations **24** (expected negative-test cases);
- unexpected writes **0**, token fields **0**, expected dirty-test receipts **3**.

This is a **negative-heavy test matrix**, not production telemetry: the security/fallback/native-failure
threshold flags are expected here. The proposed production thresholds are:

- any token/DACL/identity/plan/stale failure;
- any `saved`/`compiled`/unexpected dirty write;
- fallback rate > **5%**;
- native failure rate > **2%**.

These are **UNAPPROVED PROPOSALS**, not alerting or production telemetry. Synthetic unit tests cover
fallback/security/write/token redaction; real M3.3 evidence is evaluated read-only.

## 4. Z5/Z6 production runbook

`M3_4_CANARY_RUNBOOK.md` defines the future manual canary and rollback playbook: eligibility, manual broker
start, descriptor ACL, opt-in config, positive/negative smoke checks, observability review, stop thresholds,
and a rollback-to-off procedure. It explicitly requires Z4 approval and is not executable in M3.4 because
production menu eligibility is not implemented.

## 5. What is proved vs design-only

**Proved locally**

- check-only preflight accepts one protected disposable descriptor and rejects missing/unsafe/stale inputs;
- token/path redaction and read-only operation;
- telemetry evaluator produces deterministic aggregates and flags security/write/token conditions on
  synthetic and real M3.3 data;
- 7 new unit tests pass; existing 140 broker tests and full Python suite (1388 tests, 17 skipped)
  remain green (no Python runtime code changed, only new offline modules/tests).

**Design-only / not proved**

- production project allowlist/marker deployment;
- manual broker installation/ACL/token lifecycle in a real project;
- production telemetry, alerting, dashboards, or automated rollback;
- production menu eligibility/canary execution;
- macro-instance parity; real production-scale graphs.

## 6. Risks / remaining M4 prerequisites

- **Z4 explicit user approval is NOT GRANTED.**
- Z5 production eligibility/manual broker runbook not approved or exercised.
- Z6 thresholds are proposals; no production telemetry exists.
- Legacy fallback is synchronous and can block a completion tick.
- No user-facing cancel UI beyond busy rejection.
- Real production-like graphs and rollback at production scale are not tested.
- M3.4 does not change startup, menu behavior, flags, or the plugin; M4/M5/M6/R2 remain not started.

## 7. Evidence

`Build/m2-native/Evidence/M34/` (git-ignored): `preflight-eligible.json`,
`preflight-blocked.json`, `preflight-unsafe.json`, `telemetry-real.json`, `unit-tests.log`,
`SHA256SUMS.txt`. Disposable preflight fixtures remain under `Build/m2-native/M34/Preflight/`.
