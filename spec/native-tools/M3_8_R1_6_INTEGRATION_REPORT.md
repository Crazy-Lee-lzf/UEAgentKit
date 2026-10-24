# M3.8 — R1.6 merge into the isolated M3 integration — Report

- Date: 2026-10-25
- Worktree: `E:\WorkSpace\UEAgentKit-WT\m3-r1-integration` (existing; no new worktree)
- Branch: `feature/m3-r1-integration-m38` (local only, from M3.7 `979c340`)
- M3.7 base: `979c340c01b08e293d72d7650669ab10d7320a39`
- R1.6 merged: `918fd042e512327a2a5b767b18435752172db85e`
- Merge commit: `fc25ab6d824ad37764e617cbd7b9b705618859f7` (clean; no conflicts)
- Status: **PASS for the isolated build/tests; M4/default-on remains BLOCKED**
- No push; no main/R1/R7 source changes; no production assets.

## 1. Merge

R1.6 is seven commits ahead of M3.7's merge base `5aa60ad` (R1.4): cursor-pagination
doc/evidence fixes, the R1.5 loopback harness, and R1.6 socket-boundary hardening. Merge was done
with `--no-ff`; Git reported "Automatic merge went well". Changed files:

- `NativeNodeRegistry.cpp/.h` (+56/−? and +3), `NativeNodeRegistryTests.cpp` (+50)
- `scripts/RunR15SocketAcceptance.ps1`, `scripts/RunR16Boundary.ps1`
- `tests/native_tools/r15_socket_harness.py`, `test_r15_socket_harness.py`, `test_r16_seam_source.py`
- R1.5/R1.6 reports and run-state docs

## 2. Builds

| Item | Result |
|---|---|
| Combined M3.8 host (`Build/m37-integration/P11AutomationHost`) | **Succeeded**, exit 0, 7 actions, 17.5 s (incremental over M3.7 objects) |
| R1 host rebuilt from M3.8 source (`Build/r1-native/R1AutomationHost`, junction to this worktree's plugin) | **Succeeded**, exit 0, 99 actions, 126.6 s |
| Final combined DLL | 4,483,072 B, `sha256:A10379D4581FD4EA6D002DF3CD1F35142AC4BA277EFD753EC612FFA1A6D2447D` |
| Toolchain | VS 2022 14.44.35228 / MSVC 14.44.35207; UBA disabled; `-NoHotReload` |

The 4,483,072 B DLL is the one used by all evidence below. (An earlier 4,483,584 B build of the
same source from the Blueprint-host target was superseded by the R1 host rebuild; R1 automation was
re-run on the final DLL.)

## 3. R1 native socket acceptance

### 3.1 In-editor R1 automation

`UEAgentKit.Native.R1` on the combined host: **9/9 Success, 0 failed, 0 notRun**
(`R1Automation/index.json`; `succeeded=9`). Covers catalog, context filter, cursor vectors,
descriptor parity, lifecycle, pagination, read-only, shadow tools, untrusted spawner.

### 3.2 R1.5/R1.6 loopback socket boundary (real minimized Editor processes)

`scripts/RunR16Boundary.ps1 -SkipBuild -PerfRuns 1` on the M3.8 combined DLL:

| Case | Checks | Result |
|---|---:|---|
| perf-1 (shadow, 46 checks incl. catalog/context parity, page-256 bound) | 46 | PASS |
| capture | 4 | PASS |
| stale (cross-Editor cursor) | 2 | PASS |
| expiry (2 s TTL) | 4 | PASS |
| shutdown (held socket closed) | 2 | PASS |
| off (`NativeToolRegistry=off`) | 3 | PASS |

Summary `r16-runs-summary.json`: `{"failures": [], "ok": true}`.

**Deviation, recorded:** the R1.6 report used 3 perf processes; this integration run used
`PerfRuns=1` to bound time. Functional coverage is unchanged; the single perf sample is not a
performance claim.

**Baseline freshness:** `descriptorDigest` includes `source.pluginBuildHash`, so the R1.6
baseline catalog from its own DLL cannot match this combined DLL. A catalog export was produced
from the current DLL (`R1Catalog` on the rebuilt R1 host) and used as the shadow baseline; the
46/46 check then passed. Without that step, the only failure was the expected cross-build digest
comparison (45/46 otherwise).

**Harness inputs:** the R1.6 harness reads ignored evidence baselines
(`Build/r1-native/Evidence/R15|R14|R16/...`). Those were copied from the R1 worktree (test inputs,
not source). The shutdown case initially failed only because `Start-Process py` inherited the
wrong working directory; running the script from the worktree root fixed it without changing R1
source semantics.

## 4. M3 native Preview on the combined build

`Build/m38-integration/run_combined_m3_m38.py` on `/Game/M35/BP_M35_Stress` (53 nodes):
**8/8 cases ok** (exit 0 each):

| Case | Result |
|---|---|
| native-happy (external broker + Python parity) | plan `layout:d85879b3c51c092c`, moved 53, readOnly, no save/compile, clean |
| fallback (descriptor missing) | `native-preview-broker-descriptor-missing` |
| timeout | `native-preview-timeout` |
| dirty | `native-preview-package-dirty` |
| fingerprint drift | `native-preview-fingerprint-conflict` |
| revision drift | `native-preview-revision-conflict` |
| bad token | `native-preview-token-rejected` |
| invalid DACL | `native-preview-broker-descriptor-invalid` |

Receipt `readOnly=true`, `saved=false`, `compiled=false`, `packageDirtyAfter=false`,
`movedCount=53`, `waitSeconds≈0.319`.

## 5. P11 legacy fallback on the combined build

Fresh `Build/m37-integration/P11Only/P11AutomationHost` (plugin junction to the same M3.8 source):

- `P11FixtureContract`: Success (15-node fixture, fan-out/merge/branch/comment).
- `P11ScopeContract`: Success; selection scope 5 / moved 2, incremental scope 3 / moved 2,
  outside/fixed unchanged, Undo/Redo/Save, no-op clean.
- `P11ReloadContract`: Success in a second process.
- `P11MenuContract`: Success; read-only receipt `planId=layout:528200c7b0452e09`,
  `readOnly=true`, `saved=false`, `compiled=false`, dirty before/after false.
- Default-off: with `NativeLayoutPreview=off` and `NativeToolRegistry=off`, startup logged
  `Native node registry is off (default).` and `P11MenuContract` still passed on the legacy path.

## 6. Python

| Suite | Result |
|---|---|
| Repository full (`RunPythonTests.py full`) | **1388 tests, 17 skipped, OK**, 125.680 s |
| `tests/native_tools` | **57 tests, OK**, 14.877 s |
| `git diff --check` | clean |

## 7. Safety / scope

- Defaults remain off (`NativeToolRegistry=off`, `NativeLayoutPreview=off`); no default-on change.
- R1.6 adds only registry boundary checks/tests; no write endpoint added.
- No production project, Lyra/Reforge, R2/M4, M5/M6, canary, push, or main/R1/R7 edits.
- Editors were `-unattended -NullRHI`, minimized, serial; no Computer Use.

## 8. Limits

- One 53-node graph and one combined native trial; no production-scale or macro-instance proof.
- One perf process (see deviation).
- Human-visible GUI remains out of scope/not verified (ToolMenus API/action only, as in P11).
- M4 remains blocked by missing Z4/Z5/Z6 approvals.

## 9. Evidence

| File | Bytes | SHA256 |
|---|---:|---|
| `Build/m38-integration/build.log` | 17,412 | `D0F92134…BA3E` |
| `Build/m38-integration/build-r1host.log` | 12,050 | `095691BC…52BF` |
| `Evidence/R1Automation/index.json` | 7,553 | `16745400…2DD4` |
| `R16/r16-runs-summary.json` | 39 | `73BD2795…0E97` |
| `R16/perf-1/socket.json` | 58,700 | `06475476…E28F` |
| `R16/capture/socket.json` | 46,707 | `6A04124A…7B4F` |
| `R16/stale/socket.json` | 3,040 | `B6FCF55B…1E4B` |
| `R16/expiry/socket.json` | 25,109 | `471A0486…69F3` |
| `R16/shutdown/socket.json` | 458 | `64A0441F…B59D` |
| `R16/off/socket.json` | 6,399 | `AF5D052C…3BCE` |
| `Evidence/M3/combined-m3.json` | 15,610 | `1AB1114D…EA18` |
| `m32-menu-combined_native.json` | 4,018 | `E1FF6BBF…1F84` |
| P11 scope/reload/menu `index.json` | 1,116 / 1,118 / 1,786 | `4F8301B3…` / `4FD739B5…` / `22130CF3…` |
| P11 `menu-invocation.json` | 1,023 | `C32C4805…C4CF` |
| `python-full.log` | 4,287 | `90A5AB27…BCC6` |
| `python-native-tools-2.log` | 162 | `48657038…FBD7` |
| Final DLL | 4,483,072 | `A10379D4…447D` |

All `Build/**` paths are gitignored and are not committed.
