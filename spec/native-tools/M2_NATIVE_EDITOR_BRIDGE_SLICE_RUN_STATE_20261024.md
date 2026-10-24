# M2.2 Native Editor LayoutBroker Client — Run State (2026-10-24)

- Status: **M2.2 thin runtime slice positive; negative matrix partial; M3 not started**
- Branch: `feature/layout-broker-m1`
- Base: M2.1 `616bff3`
- Worktree: `E:\WorkSpace\UEAgentKit-WT\layout-broker-m1`
- Disposable host: `Build/m2-native/M2NativeHost.uproject`
- Engine: `E:\EPICGAME\UE_5.6` (UE 5.6.1)
- Remote: no push/merge/reset/clean/delete
- Legacy P11/P8/P9 menu, EditorBridge, Build.cs, MCP routes: **not edited**

## 1. Model/session

Same DSH session `session-7fd8360d-3ac6-47f5-898e-4d52afd3e59d`; runtime declares
`cline-pass/deepseek-v4.1-flash-1017` (non-Fast, max reasoning) with no model switch. The agent has no
independent model-id verification. R1’s heavy UE slot was released; only this host was used.

## 2. Build

| Command | Result |
|---|---|
| `pwsh -File scripts\BuildM2NativeHost.ps1` | first full build **Succeeded**, 117.6 s, 79 actions; incremental ~10 s |
| Compiler | VS 2022 14.44.35228 (engine calls it non-preferred), Windows SDK 10.0.22621 |
| Warnings | plugin does not declare IKRig dependency (pre-existing); no M2 errors |

Raw: `Build/m2-native/Evidence/build.log`, `build-console.log`.

## 3. Positive runtime test

Command: `py -3.12 tools\run_m2_native_editor_test.py`.

- Real `UnrealEditor-Cmd` automation `UEAgentKit.Native.M2LayoutBrokerPositive`;
- test creates `/Game/M2Native/BP_M2LayoutNative`, captures real P9 snapshot;
- Python harness starts the M1 broker **outside** the Editor after snapshot/session export;
- native worker connects, service validates, test asserts.

Result:

- Editor exit **0**, automation succeeded 1 / failed 0;
- test **Success**;
- nodeCount 4, `planId=layout:bdfd3a70cd9a7a8b`,
  `layoutHash=sha256:5a6637241c69d04ce910ac685fc83cd4fe05efd5f5c7f23503779583e0e533c9`;
- exported real snapshot sent to Python `build_preview` produces exactly the same result
  (`real-snapshot-parity=true`);
- broker `accepted=1, completed=1, rejected=0`, descriptor DACL PROVEN;
- receipt readOnly true, saved/compiled false, token absent, package clean.

Evidence: `Evidence/native-result.json`, `m2-native-evidence.json`, `ue-editor.log`, `broker.log`,
`Automation/`.

## 4. Negative runtime tests

| Scenario | Harness | Result |
|---|---|---|
| missing descriptor | positive test before broker | PASS |
| native preview default off | positive test | PASS (`native-preview-disabled`) |
| inherited/non-hardened DACL | positive test | PASS (descriptor rejected) |
| forged planId spoof | fake broker success | PASS (`native-preview-plan-id-mismatch`, no plan, editor exit 0) |
| response >1 MiB | fake broker | PASS (`native-preview-response-too-large`, editor exit 0) |
| token error | fake broker error | PASS (`native-preview-token-rejected`, editor exit 0) |
| wrong token | **real M1 broker**, wrong descriptor token | PASS (`native-preview-token-rejected`, editor exit 0) |
| response timeout | fake broker sleeps 7 s; normal renderer (no `-nullrhi`) | PASS (`native-preview-timeout`, no plan, editor exit 0) |
| stale package revision | real UE fixture modified + saved after snapshot | PASS (`native-preview-revision-conflict`, no plan, undo 0→0, receipt saved=false) |
| stale graph fingerprint | real UE fixture moved in memory, dirty cleared | PASS (`native-preview-fingerprint-conflict`, no plan, undo 0→0, receipt saved=false) |
| request >1 MiB | padded real snapshot, local request build | PASS (`native-preview-request-too-large`, no socket send) |

Evidence: `Evidence/m2-native-negative-*.json`, `Evidence/Negative-*/index.json`.

## 5. Legacy regression attempts

| Test | Result |
|---|---|
| `UEAgentKit.GraphLayout.P10FixtureContract` | **Success** (exit 0; 4 log entries/warnings, no failure) |
| `UEAgentKit.GraphLayout.P11FixtureContract` | **Success** in `Build/m2-native/P11AutomationHost` with marker (exit 0; 0 errors) |
| P11 scope request generation | PASS (`p11_build_scope_probe.py` on real fixture) |
| `UEAgentKit.GraphLayout.P11MenuContract` | **Success** (exit 0; 0 errors) |
| `UEAgentKit.GraphLayout.P11ScopeContract` | **Success** (exit 0; 0 errors) |
| P11 reload | SKIPPED (second-process reload not run) |

No P10/P11 source was changed. R1.3 `CallFunction NodeGuid` warning was not touched (R1 worktree untouched);
this host’s own earlier comment-node warning was removed by simplifying/renaming its fixture. Cross-process
`graphFingerprint` drift was not reproduced; the service fails closed on a live fingerprint mismatch.

## 6. Python regression

| Command | Result |
|---|---|
| `py -3.12 -m unittest discover -s tests\python -p "test_layout_broker_*.py"` | **140 passed** |
| `py -3.12 scripts\RunPythonTests.py full --quiet` | **1381 tests, OK (skipped=17), 118.6 s** (`Build/layout-broker/Evidence/full-python-suite-m2_2.log`) |

## 7. Evidence inventory

`Build/m2-native/Evidence/` (git-ignored): `ue-editor.log`, `broker.log`, `build.log`, `build-console.log`,
`snapshot-preflight.json`, `snapshot-context.json`, `native-result.json`, `m2-native-evidence.json`,
`m2-native-negative-{spoof,oversize,timeout,token-error,wrong-token,revision,fingerprint}.json`,
`native-stale-result.json`, `Negative-*/`, `P11HostFixture/`, `P11HostMenu2/`, `P11HostScope/`,
`P10Regression/`, `P11Regression/`, `SHA256SUMS.txt`.

## 8. Limits / next gates

- The timeout test now uses a normal renderer instead of `-nullrhi`, avoiding the UE `GetRestoredDimensions` fatal; it exits 0. This is a test-host workaround, not an engine fix.
- Revision/fingerprint checks are same-process live rechecks; cross-process fingerprint drift against R1 was not reproduced.
- P11 reload remains skipped; P11 fixture/menu/scope passed in the valid P11 host.
- M3 menu/fallback, M4 cutover, Apply, native registry: not started/authorized.
- Production snapshot wiring is not enabled; only the test seam uses the service.

## 9. Git

Scoped local commit only (no push), containing new C++ files, build/test harness scripts, and M2.2 docs; no
existing plugin source/menu/Build.cs/EditorBridge edits.
