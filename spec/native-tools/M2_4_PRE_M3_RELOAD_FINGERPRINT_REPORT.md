# M2.4 Pre-M3 Gate Closure — P11 Reload and Cross-Process Fingerprint

- Date: 2026-10-24
- Worktree: `E:\WorkSpace\UEAgentKit-WT\layout-broker-m1`
- Branch: `feature/layout-broker-m1`, base M2.3 `fc5654e`
- Engine: UE **5.6.1**, `E:\EPICGAME\UE_5.6`
- Disposable hosts: `Build/m2-native/M2NativeHost` and
  `Build/m2-native/M24/P11AutomationHost`
- Status: **A PASS; B PASS on a valid fixture (R1.3 warning not reproduced); M3 not automatically unblocked**
- No menu cutover, Apply, production change, push/merge/reset/clean/delete, or Computer Use

## 1. Session/model and scope

Same DSH session `session-7fd8360d-3ac6-47f5-898e-4d52afd3e59d`; runtime declares
`cline-pass/deepseek-v4.1-flash-1017`, non-Fast/max reasoning, no model switch. Only this lane used the UE heavy
slot; all Editor runs were serial. The P11 legacy Preview and all existing menu/Bridge source remain untouched.

## 2. Gate A — P11 ReloadContract in two independent Editor processes

### 2.1 Valid host prerequisites

A fresh disposable host was created at `Build/m2-native/M24/P11AutomationHost`:

- project name exactly `P11AutomationHost`;
- `<Project>/P11TestOnlyBuild.json` marker;
- plugin junction to `Plugin/UEAgentKit`;
- `Build/m2-native/tools` junction so the existing P11 worker path (`ProjectDir/../../tools/...`) resolves.

No P11 source was changed. The fixture is the existing P11 15-node selection/incremental fixture, created by
`P11FixtureContract`.

### 2.2 Sequence and results

Process 1 (apply/save):

1. `UEAgentKit.GraphLayout.P11FixtureContract` — **Success**, exit 0, 0 errors
   (created `BP_LayoutP11`, `BP_LayoutP11Inc`, context/preflight evidence).
2. `tests/python/p11_build_scope_probe.py` — **PASS**; generated real selection/incremental requests.
3. `UEAgentKit.GraphLayout.P11MenuContract` — **Success**, exit 0, 0 errors.
4. `UEAgentKit.GraphLayout.P11ScopeContract` — **Success**, exit 0, 0 errors; it applied and saved positions and
   wrote `p11-scope-result.json`.

Process 2 (independent Editor):

5. `UEAgentKit.GraphLayout.P11ReloadContract` — **Success**, exit 0, 0 errors, `p11-reload-result.json`:
   - `selectionPositions=true`, `incrementalPositions=true`;
   - `selectionTopology=true`, `incrementalTopology=true`;
   - `dllVerified=true`;
   - both packages `Dirty=false`;
   - all four receipt booleans true.

Disk revisions before scope / after scope+save / reload:

| Asset | Before | After save | Reload |
|---|---|---|---|
| `BP_LayoutP11` | `sha256:9ad8d485851ca9200f3026f597a6a4c849e7d2fb56811343853f1931062e4e2c` | `sha256:fdefc479cb01cc778adb6198c0737739645146ff083496a168686adabbc67557` | same as after |
| `BP_LayoutP11Inc` | `sha256:4bbcb088fd64f3c55fc5c27cec8b0de07d035442c292348be7f582c8ca6f805f` | `sha256:29e44db453f713224b1f88b9c11eee62e17c9c5e05aedc96053cb29039c28ad0` | same as after |

Position-array hashes: selection
`sha256:e6ba967ce02319a42415eefccca4f7657c5853c3443acfba9f94e85f9ee92c9e`,
incremental `sha256:891e23dc0ffb6bc420fd42b4f6e641cbe650c015f20aa9be1c0783ef0b6d14ab`.
The reload process used the same Editor DLL hash as the scope process (`dllVerified=true`).

Machine-readable: `Evidence/M24/p11-reload-pair.json`; raw reports:
`M24/P11Fixture/`, `M24/P11MenuContract/`, `M24/P11ScopeContract/`, `M24/P11Reload/`, and the P11 host
`Evidence/p11-scope-result.json` / `p11-reload-result.json`.

P10 was rerun with the final DLL: `UEAgentKit.GraphLayout.P10FixtureContract` **Success**, exit 0, 0 errors.

## 3. Gate B — cross-process graphFingerprint stability

### 3.1 Method

A valid M2 fixture `/Game/M2Native/BP_M2LayoutNative` with a stable on-disk package was captured in **two
independent `UnrealEditor-Cmd` processes** using a new test-only
`UEAgentKit.Native.M2FingerprintCapture` test. Each run wrote only JSON evidence; it did not save or modify the
asset. Capture includes:

- `packageRevision` and a direct package-file SHA256;
- `graphFingerprint`;
- preflight node array;
- live `Graph->Nodes` order, GUID, name, class, position, size, pin count;
- canonical context node order;
- dirty flags.

### 3.2 Paired result

Both runs: exit **0**, automation **Success**, package clean.

| Field | Run 1 | Run 2 | Equal |
|---|---|---|---|
| packageRevision | `sha256:d5d491aa9a28dca5ca68b8455a3c3e5241a1107c130107b4ffa6ab1d2a68eb91` | same | yes |
| packageFileSha256 | `sha256:d5d491...` | same | yes |
| graphFingerprint | `sha1:7df8046312b16165e197a1e7c569153c3aa30408` | same | yes |
| nodeCount | 4 | 4 | yes |
| preflightNodes hash | `sha256:ccd26675...` | same | yes |
| liveNodeOrder hash | `sha256:42f72366...` | same | yes |
| contextNodeOrder hash | `sha256:bff304f8...` | same | yes |
| packageDirty / blueprintDirty | false / false | false / false | yes |

Machine-readable: `Evidence/M24/fingerprints-paired.json`; captures
`M24/Fingerprint/capture-1.json`, `capture-2.json`; raw Editor logs
`M24/Fingerprint/ue-editor-run1.log`, `ue-editor-run2.log`.

### 3.3 R1.3 warning investigation

**No cross-process drift was reproduced** on a valid, clean package. The R1.3 warning reports a **CallFunction
NodeGuid missing** in a test fixture. That is a fixture-integrity warning, not a production graph change:

- `BuildPreflight` rejects a live node with an invalid NodeGuid
  (`graph-layout-invalid-live-node`) before a native preview can be built;
- a missing GUID would prevent the native path from producing a preflight/plan at all, rather than silently
  changing a production fingerprint;
- no fingerprint contract or production behavior was changed.

Because the R1 worktree/fixture is protected and was not modified, the exact R1 fixture cause remains
**unreproduced**. This is recorded as a test-fixture defect hypothesis, not a proven production mismatch.

## 4. Regression

| Test | Result |
|---|---|
| P10 FixtureContract (final DLL) | Success |
| P11 FixtureContract | Success |
| P11 MenuContract | Success |
| P11 ScopeContract | Success |
| P11 ReloadContract (second process) | Success |
| Python broker tests | **140 passed** |
| Full Python suite | **1381 tests, OK (17 skipped), 118.6 s** (no Python source changed) |

## 5. M3 gate statement

- Gate A (P11 reload) is **clean**.
- Gate B (valid-fixture cross-process fingerprint) is **clean**.
- The R1.3 missing-NodeGuid fixture warning remains **unreproduced/unresolved**, but it is a fixture-integrity
  issue that native preflight rejects, not a production fingerprint mismatch.
- **M3 is not automatically unblocked.** A separate, explicitly scoped M3 menu/fallback task and review are
  still required; no M3 UI/menu code was touched.

## 6. Evidence

`Build/m2-native/Evidence/M24/` (git-ignored): `p11-reload-pair.json`, `fingerprints-paired.json`,
`Fingerprint/` (captures, paired JSON, raw Editor logs), `P11Fixture/`, `P11MenuContract/`,
`P11ScopeContract/`, `P11Reload/`, `P10Fixture/`, `p11-scope-probe.log`, `build.log`, `SHA256SUMS.txt`.
