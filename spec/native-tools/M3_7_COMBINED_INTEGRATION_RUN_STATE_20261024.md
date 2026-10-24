# M3.7 Combined Integration — Run State (2026-10-24)

- Status: **PASS (isolated combined build/tests); M4 blocked**
- Worktree: `E:\WorkSpace\UEAgentKit-WT\m3-r1-integration` (new, disposable)
- Branch: `feature/m3-r1-integration-m37` (local only)
- M3 base: `f63ecfb57ab8b1552abda0745c0083a14409f3a4`
- R1 merged: `5aa60ad5859d84c0ff496a1c34ae0c891cd64100`
- Merge: `7fd9a4e`; flag-source fix: `d055f3a`
- No push; no M3/R1/main source changes outside this worktree

## 1. Branch fact

R1 worktree's current HEAD is `ee3abbd` (two documentation-only commits after `5aa60ad`). Per explicit
instruction, exact `5aa60ad` was merged. `5aa60ad..ee3abbd` changes only the R1.4 report and runstate.

## 2. Merge/build

| Step | Result |
|---|---|
| `git worktree add -b feature/m3-r1-integration-m37 ... f63ecfb` | created |
| `git merge --no-ff --no-commit 5aa60ad` | one conflict in `UEAgentKitEditorModule.cpp` |
| conflict resolution | both includes, both init/teardown lifecycles; no ours/theirs blanket |
| scoped fix | M3 unused `NativeToolRegistry` GEngineIni read removed; R1 `GGameIni` authoritative |
| combined `Build.bat UnrealEditor Win64 Development ...` | **Succeeded** exit 0, 94 actions |
| DLL SHA-256 | `4e94531882f8e371a11aa5a3724164d43a5a53677911dde3bd6d72d1d0631106` |

Toolchain: VS 2022 14.44.35228 / MSVC 14.44.35207 / Windows SDK 10.0.22621. Warnings: IKRig plugin
dependency (2), non-preferred MSVC notice, existing RetargetValidation C4996; no new M3/R1 warnings.

## 3. R1 combined automation

| Run | Result |
|---|---|
| `R1Automation-A` | 9 Success / 0 failed / 0 notRun, Editor exit 0 |
| `R1Automation-B` | 9 Success / 0 failed / 0 notRun, Editor exit 0 |
| `R1Automation-C` (M3 off) | 9 Success / 0 failed, Editor exit 0 |

Catalog: 512 descriptors, available 762, truncated=true, fingerprint `sha256:b09fb1387e188e02`,
pluginSetHash `sha256:1d034d339ee271ad`, pluginBuildHash = combined DLL. A/B descriptor actionIds,
order, digests equal; only `discoveredAtUtc` differs. `R1CursorVectors`/`R1Pagination` passed in the
9-test suite; `R1ShadowTools` passed (socket T8 not claimed).

R1 Python: **47 tests OK** after copying existing R1 catalog/context/T4 evidence into the new worktree.

## 4. M3 combined cases

`Build/m37-integration/run_combined_m3.py` on 53-node `/Game/M35/BP_M35_Stress`:

| Case | Exit | Receipt |
|---|---:|---|
| native happy | 0 | native plan `layout:a68a4cccba990d51`, moved 53, Python parity, wait 0.288 s |
| fallback missing | 0 | legacy fallback, native descriptor-missing, worker 0.529 s |
| timeout | 0 | fallback, timeout retained, worker 0.413 s (wait 5.443 s) |
| dirty | 0 | `native-preview-package-dirty`, fallback false |
| fingerprint | 0 | `native-preview-fingerprint-conflict`, fallback false |
| revision | 0 | `native-preview-revision-conflict`, fallback false |
| token | 0 | `native-preview-token-rejected`, fallback false |
| DACL | 0 | `native-preview-broker-descriptor-invalid`, fallback false |

All readOnly/no save/compile/dirty; DACL PROVEN for native. One combined 53-node trial; M3.5's
three-trial result is separate and not re-counted.

P11 fresh combined host: Fixture/Menu/Scope + second-process Reload Success; selection/incremental legacy.
P10 Success. A first P11 probe on a reused host failed because copied assets were already scope-applied;
fresh host passed and is authoritative.

## 5. Python

| Test | Result |
|---|---|
| broker 140 | 140 passed |
| R1 native_tools 47 | 47 passed |
| full suite | 1388 tests, OK (17 skipped) |
| `git diff --check` | clean |

Full suite initially failed M3.4's real-M33-evidence test because the new worktree lacked ignored M3.3
evidence; copying that evidence made it pass. Documented, not hidden.

## 6. Flags / safety

- `NativeLayoutPreview=off` and `NativeToolRegistry=off` startup logged registry off and legacy P11.
- Native on/registry off produced native plans; registry off/default.
- M3 compatibility field no longer reads a second registry config source.
- No token leak, writes, dirty/undo, or cross-service initialization observed.
- No Computer Use, hidden windows, Editor-spawned Python/CMD, push, merge to M3/R1/main, or production
  changes.

## 7. Stop

**M4 remains BLOCKED:** Z4 approval missing, Z5 production eligibility/manual broker approval missing,
Z6 operational telemetry missing. Stop after evidence; no default-on/M4/R2/M5/M6.

## 8. Evidence

`Build/m37-integration/Evidence/` (ignored): build log, DLL hash, R1 A/B/C automation, catalog/context
exports, R1 47 log, M3 combined JSON/capture/logs, P11Only/P10 reports, broker/full Python logs,
startup logs, SHA256SUMS.
