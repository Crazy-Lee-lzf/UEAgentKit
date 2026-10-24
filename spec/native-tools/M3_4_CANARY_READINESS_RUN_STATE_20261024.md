# M3.4 Pre-M4 Z5/Z6 Readiness — Run State (2026-10-24)

- Status: **Z5 CHECK-ONLY PASS (disposable), Z6 READ-ONLY PASS; production canary BLOCKED (Z4 missing)**
- Branch: `feature/layout-broker-m1`, base `b3e3ae9`
- No UE plugin build/change; no Editor/broker started for this stage; no production project touched
- No push/merge/reset/clean/delete, no Computer Use, no DSH restart

## 1. Session/model

Same DSH session `session-7fd8360d-3ac6-47f5-898e-4d52afd3e59d`; runtime declares
`cline-pass/deepseek-v4.1-flash-1017`, non-Fast/max reasoning, no model switch.

## 2. Z5 check-only preflight

| Command | Result |
|---|---|
| `py -3.12 tools\m34_canary_preflight.py ...CanaryProject...` | exit 0, **ELIGIBLE** |
| same, missing descriptor | exit 1, **BLOCKED** |
| same, inherited/UNVERIFIED ACL | exit 1, **BLOCKED** |
| `py -3.12 -m unittest tests.python.test_m34_canary_readiness -v` | 7 tests OK |
| `py -3.12 scripts\RunPythonTests.py full --quiet` | 1388 tests, OK (17 skipped) |
| `py -3.12 -m unittest discover -s tests\python -p "test_layout_broker_*.py"` | 140 passed |

Evidence: `M34/preflight-eligible.json`, `preflight-blocked.json`, `preflight-unsafe.json`.
Fixture project/descriptor/marker/allowlist are disposable under `Build/m2-native/M34/Preflight`;
the preflight tool itself performs no writes. Token scan of output: absent.

## 3. Z6 read-only evaluator

| Command | Result |
|---|---|
| `py -3.12 tools\m34_telemetry_eval.py --evidence Build\m2-native\Evidence\M33 --out Build\m2-native\Evidence\M34\telemetry-real.json` | exit 0 |

Real M3.3 aggregate: 42 attempts, 7 native success, 10 fallback, 25 native failure, 3 busy,
p50 55 ms, p95 5185 ms, 24 expected negative security codes, 0 unexpected writes, 0 token fields.
This is a negative-heavy test matrix, not production telemetry. Proposed thresholds (unapproved):
any security/write/token event, fallback >5%, native failure >2%.

## 4. Docs

- `spec/native-tools/M3_4_CANARY_READINESS_REPORT.md`
- `spec/native-tools/M3_4_CANARY_RUNBOOK.md` (manual canary + rollback playbook; design only)
- `spec/native-tools/UE_TEST_WINDOW_LAUNCH_UX.md` (minimized test Editor launch)

## 5. What is proved / not proved

**Proved:** read-only preflight fail-closed checks and redaction on disposable data; evaluator math and
synthetic fault cases; 7 new unit tests; no token/path emission; no production writes.

**Design-only:** production allowlist/marker installation, manual broker deployment, production telemetry/
alerts, menu eligibility, production rollback, macro instances, production-scale graphs.

## 6. M4 status

**M4 remains BLOCKED.** Z4 explicit user approval is missing; Z5 operational approval and Z6 production
observability are not established. NativeLayoutPreview remains off; legacy selection/incremental remain
legacy; broker 1.0 remains full-only.

## 7. Evidence

`Build/m2-native/Evidence/M34/` (git-ignored): preflight JSONs, `telemetry-real.json`, `unit-tests.log`,
`SHA256SUMS.txt`. No production project/descriptor was written.
