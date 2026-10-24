# M3.7 Isolated M3 + R1 Combined Integration — Report

- Date: 2026-10-24
- Integration worktree: `E:\WorkSpace\UEAgentKit-WT\m3-r1-integration`
- Branch: `feature/m3-r1-integration-m37` (new, local only)
- Base M3: `f63ecfb57ab8b1552abda0745c0083a14409f3a4`
- Merged R1: `5aa60ad5859d84c0ff496a1c34ae0c891cd64100`
- Merge commit: `7fd9a4e`; scoped flag-source fix: `d055f3a`
- Status: **PASS for the isolated combined build/tests; M4 remains BLOCKED**

## 1. Preconditions and branch fact

The requested R1 SHA `5aa60ad` exists and is the R1.4 real-UE acceptance commit. The R1 worktree had
advanced two **documentation-only** commits to `ee3abbd` (`aa68a90`, `ee3abbd`), changing only
`R1_4_CURSOR_PAGINATION_REAL_UE_REPORT.md` and `R1_NATIVE_TOOLS_RUN_STATE_20261024.md`. Per the explicit
instruction to merge exact `5aa60ad`, that commit was used; no extra R1 commit was squashed or merged.
The source content used is R1.4's `869b5b4` plus its acceptance report.

No M3/R1/main source was changed. The new worktree is disposable and local; no push.

## 2. Merge resolution

`git merge --no-ff --no-commit 5aa60ad` produced exactly the expected one-file conflict in
`UEAgentKitEditorModule.cpp`. Resolution preserved both sides:

- includes: `NativePreviewMenuSettings.h` **and** `Native/NativeNodeRegistry.h`;
- startup: M3 settings init outside the editor condition, then R1 registry init inside it before
  `EditorBridge`, then existing bridge/P8/P9/P11 startup;
- shutdown: P11/P9/P8 menu teardown, bridge stop/reset, then R1 registry shutdown.

No blanket ours/theirs was used. Candidate provenance remains in the M3.6 ignored
`Build/integration-readiness/`; this worktree contains the actual resolved source.

Scoped semantics fix: M3 `NativePreviewMenuSettings` previously read an unused `NativeToolRegistry`
from `GEngineIni`. R1 owns that flag via `GGameIni` (`DefaultUEAgentKit.ini`). The unused M3 read was
removed and the compatibility field is fixed false; `NativeLayoutPreview` remains `GEngineIni`, default
off. No contract/flag behavior changed (`d055f3a`).

## 3. Combined build

| Item | Result |
|---|---|
| Command | `Build.bat UnrealEditor Win64 Development -Project=.../P11AutomationHost.uproject -plugin=.../UEAgentKit.uplugin -NoUBA -NoHotReload -WaitMutex` |
| Result | **Succeeded**, exit 0, 94 actions, 119.4 s |
| Toolchain | VS 2022 14.44.35228 / MSVC 14.44.35207, Windows SDK 10.0.22621 |
| DLL SHA-256 | `4e94531882f8e371a11aa5a3724164d43a5a53677911dde3bd6d72d1d0631106` |
| Warnings | IKRig plugin dependency warnings (2), non-preferred MSVC notice, existing RetargetValidation C4996; no new M3/R1 warning |
| M3/R1 errors | none |

The R1 catalog/context identity reports this exact DLL hash (`pluginBuildHash`), confirming the
R1 registry ran against the combined build.

## 4. R1 acceptance in combined build

- **9/9** automation in each of three independent Editor processes (`R1Automation-A`, `-B`, `-C`):
  `R1Catalog`, `R1ContextFilter`, `R1CursorVectors`, `R1DescriptorParity`, `R1Lifecycle`,
  `R1Pagination`, `R1ReadOnly`, `R1ShadowTools`, `R1UntrustedSpawner`; 0 failed/notRun.
- Catalog summary: **512 descriptors**, available **762**, `truncated=true`, complete=false,
  fingerprint `sha256:b09fb1387e188e02`, pluginSetHash `sha256:1d034d339ee271ad`,
  pluginBuildHash equals the combined DLL.
- Cursor HMAC golden and pagination assertions pass in `R1CursorVectors`/`R1Pagination`; no token
  values were emitted in the tested evidence. R1 47 Python tests pass.
- R1 catalog A/B identity: descriptor IDs, order, and digests identical; only per-run
  `discoveredAtUtc` differs. `R1ShadowTools` passed; this does **not** claim the `native.invoke`
  socket path T8, which was not executed.
- R1 Python: `tests/native_tools` **47 tests OK** after supplying the existing R1 catalog/context/T4
  evidence (without that evidence, the suite's evidence-dependent classes skip; raw run recorded).

## 5. M3 acceptance in combined build

Eight menu/service cases on the 53-node M35 stress graph, all exit 0/Automation Success:

| Case | Result |
|---|---|
| native happy (external broker) | plan `layout:a68a4cccba990d51`, moved 53, Python planId/layoutHash/moved parity; wait 0.288 s |
| broker missing fallback | legacy fallback plan, worker 0.529 s, descriptor-missing retained |
| timeout fallback | fallback true, worker 0.413 s, wait 5.443 s (transport) |
| dirty | `native-preview-package-dirty`, no fallback/plan |
| fingerprint drift | `native-preview-fingerprint-conflict`, no fallback/plan |
| revision drift | `native-preview-revision-conflict`, no fallback/plan |
| bad token | `native-preview-token-rejected`, no fallback/plan |
| invalid DACL | `native-preview-broker-descriptor-invalid`, no fallback/plan |

All receipts readOnly, saved/compiled false, package clean; descriptor DACL PROVEN on native.
The 53-node combined native case was one trial; the earlier M3.5 three-trial stress is not re-counted as
combined evidence.

P11 in a fresh combined host: Fixture, Menu, Scope, and independent second-process Reload all Success;
selection/incremental remained legacy. P10 Fixture Success. An initial P11 probe on a reused host failed
because its copied P11 assets were already scope-applied; the clean P11Only host passed and is the
authoritative result.

## 6. Default-off / independent flags

- A combined startup with `NativeLayoutPreview=off` and R1 `NativeToolRegistry=off` logged
  `Native node registry is off (default).`; P11 MenuContract passed legacy-only.
- NativeLayoutPreview on with registry off produced native plans; registry logs remained off/default.
- The M3 compatibility field no longer reads `GEngineIni` for `NativeToolRegistry`; R1's `GGameIni`
  path is the sole registry source. No cross-initialization or second service was observed.
- No token leak, dirty/save/compile/undo in M3 receipts. Defaults remain off; legacy fallback true.

## 7. Python / repository

| Test | Result |
|---|---|
| Broker 140 | **140 passed** |
| R1 native_tools 47 | **47 passed** (with R1 evidence) |
| Full repository suite | **1388 tests, OK (17 skipped)** |
| `git diff --check` | clean |

The full suite initially failed the M3.4 `test_real_m33_evidence_read_only` because the new worktree
lacked the ignored M3.3 evidence; copying that evidence (test fixture, not source) made it pass. This is
recorded rather than hidden.

## 8. M4 gate

**M4 / default-on remains BLOCKED.** This is a disposable combined acceptance, not production approval:

- Z4 explicit user approval is still missing.
- Z5 production project/broker eligibility and manual deployment are not approved.
- Z6 production telemetry/alerting/abort criteria remain proposals.
- No production project, R2 general Apply, M5/M6 retirement, default-on, canary, or push was performed.
- One 53-node graph and one combined native trial; no production-scale or macro-instance proof.
- R1.4 was merged as exact `5aa60ad`; R1's later docs-only commits were not merged.

## 9. Evidence

`Build/m37-integration/Evidence/` (ignored): build log, DLL hash, R1 automation A/B/C indexes and
catalog/context exports, R1 Python 47 log, M3 combined JSON/capture/logs, P11Only/P10 reports,
broker/full Python logs, startup logs, and SHA256SUMS.
