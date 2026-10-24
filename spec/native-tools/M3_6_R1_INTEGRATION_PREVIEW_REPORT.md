# M3.6 / R1 Read-Only Integration Preview — Report

- Date: 2026-10-24
- M3 worktree: `E:\WorkSpace\UEAgentKit-WT\layout-broker-m1`
- Branch: `feature/layout-broker-m1` @ `14187e91e699a946049e9a13cd831af8ca161199`
- R1 branch: `feature/r1-node-registry` @ `0782749f5c9be6e70c8d2a242d70aeedce9805b8`
- Merge base: `b423b876ee73c7cbfb6d50e6f7e58d2dfca30566`
- Status: **READ-ONLY PLAN ONLY — no merge, no build, no runtime integration**
- M4 default-on remains **NOT APPROVED**; default flags remain off

## 1. Constraints honored

- No DSH session/model switch or restart; no Computer Use.
- No edit/compile/UBT/Editor run in the R1 worktree; R1 owns the heavy slot and its R1.4 runtime
  acceptance is **IN PROGRESS** and is **not counted as PASS** here.
- M3 worktree remained clean; only ignored `Build/integration-readiness/` candidate files and
  documentation were created. No C++ source was changed, applied, or compiled.
- No main/R1/R7/Lyra/Reforge/UETest changes; no push/merge/rebase/reset/clean/delete.

## 2. Exact merge-tree result

`git merge-tree --write-tree --messages 14187e9 0782749` produced tree
`32659d00e163ee4931a528d3afffbc5560bce20a` and exactly **one** textual conflict:

```
CONFLICT (content): Merge conflict in
Plugin/UEAgentKit/Source/UEAgentKitEditor/Private/UEAgentKitEditorModule.cpp
```

Conflict stages:

| Stage | Blob | Meaning |
|---|---|---|
| 1 | `fda1f7201ec66c4361cc5d7e7e4c718431741437` | merge base module |
| 2 | `676d35f57ad9ca174f5fab10346701e1ca8aeed4` | M3 module |
| 3 | `789ebd857f1e20388d586496e827a97660356808` | R1 module |

Conflict hunks (full marker file in ignored
`Build/integration-readiness/module-conflict.txt`):

1. **Include block**: M3 adds `#include "NativePreviewMenuSettings.h"`; R1 adds
   `#include "Native/NativeNodeRegistry.h"`. Both additions are required.
2. **Startup**: M3 adds `FUEAgentKitNativePreviewSettings::InitializeFromStartup(bAutomationChild)`
   immediately after the automation-child flag, outside the editor/host condition. R1 adds
   `FNativeNodeRegistry::Initialize(ParseMode(ReadConfiguredModeText()))` inside the editor/host
   condition, before `EditorBridge`.
3. **Shutdown**: R1 adds `FNativeNodeRegistry::Get().Shutdown()` after the bridge teardown; M3's
   existing P11/P9/P8/menu and bridge teardown is preserved.

The last two were non-overlapping auto-merges; the include additions were the only text conflict.
`Build/integration-readiness/UEAgentKitEditorModule.combined.cpp` is a **candidate only** with
provenance comments (not compiled, not applied).

## 3. Proposed combined ordering

Startup:

1. parse `UEAgentKitAutomationChild`;
2. initialize M3 `NativePreviewMenuSettings` once (outside the editor condition, so automation-child
   settings tests still observe it);
3. enter `GIsEditor && !commandlet && !automationChild`;
4. initialize R1 `FNativeNodeRegistry` from its independent `GGameIni` config (default off);
5. start `EditorBridge`;
6. start P8/P9/P11 menus as before.

Shutdown:

1. P11 menu `Shutdown()` (removes native ticker, resets the async service, unregisters ToolMenu);
2. P9/P8 menu shutdown;
3. `EditorBridge::Stop()` / reset;
4. `FNativeNodeRegistry::Get().Shutdown()`.

This keeps M3's ticker/service teardown before the bridge and R1's registry teardown after the bridge.
The registry has no ticker or socket; no ordering dependency on M3 was found.

## 4. Semantic / non-textual findings

1. **`NativeToolRegistry` config source is duplicated.**
   - M3 `NativePreviewMenuSettings` reads `NativeToolRegistry` from **`GEngineIni`** but the value is
     unused in M3.
   - R1 `FNativeNodeRegistry::ReadConfiguredModeText()` reads **`GGameIni`**
     (`Config/DefaultUEAgentKit.ini`).
   - Both default off, so default behavior is unchanged. After merge, choose one source of truth
     before any shadow/active experiment; do not wire M3's unused bool into R1.
2. **`NativeLayoutPreview` and `NativeToolRegistry` remain independent.**
   M3 native preview off/on does not initialize the R1 registry; R1 `off` does not create M3 service
   or menu. The candidate keeps separate init points.
3. **`BlueprintContextSha256` is additive.** R1 adds `HashUtf8Bytes`/`HmacSha256`; existing
   `HashText` used by M3 planId is unchanged. No merge conflict, but the combined build must re-run
   M3 exact planId/hash parity because the translation unit changes.
4. **Tools/manifests do not collide.** R1 adds `Plugin/UEAgentKit/Config/DefaultUEAgentKit.ini` and
   `Resources/NativeTools/`; M3 adds no resource/manifest of the same names. No `.uplugin` or
   `Build.cs` textual changes were found.
5. **Automation names do not collide.** M3 uses `UEAgentKit.Native.M3…/M32…/M35…`; R1 uses
   `UEAgentKit.Native.R1…` (9 automation tests).
6. **No menu/ticker coexists with R1.** R1 registry is game-thread synchronous and has no ticker;
   M3 P11 menu owns the only native ticker/service. Combine shutdown as above.
7. **R1 `EditorBridge` changes are additive** (`native.invoke` path); M3 does not edit that file in
   this range. A combined build is still required because the module registration and shared hash
   unit change.
8. **R1.4 runtime status is not evidence.** R1.4 cursor/pagination UE tests are in progress; the R1
   DLL cited in its state is still R1.3. This audit cannot count R1.4 as green.

## 5. Required combined regression after explicit merge approval

Do not merge from this report. A future approved integration must run, serially:

1. UE 5.6.1 combined build in an isolated host.
2. R1's **9** UE automations, including cursor vectors/pagination and context/catalog tests.
3. R1 offline **47** Python tests (R1.4 state) plus R1 cross-process checks where applicable.
4. P10 and P11 Fixture/Menu/Scope and independent second-process Reload.
5. Broker 1.0 **140** Python tests and full repository suite.
6. Default-off parity: no native service/registry manifest load, no broker connection, no menu
   side effects, no writes.
7. Security/side-effect checks: descriptor DACL/token, read-only receipts, dirty/undo/save/compile
   unchanged, no unexpected resource or test-name collisions.
8. R1.4 runtime results must be complete and reported **separately**; they cannot be inferred from
   this preview or from R1.3.

## 6. M4 gate

Still blocked:

- **Z4 explicit user approval is NOT GRANTED.**
- Z5 production/manual broker and project eligibility remain unapproved.
- Z6 operational telemetry/abort criteria remain proposals.
- This report is a read-only integration plan; it does not authorize merge, default-on, production
  canary, R2 Apply, or M5/M6 retirement.

## 7. Candidate/evidence

- Candidate (ignored, not applied):
  `Build/integration-readiness/UEAgentKitEditorModule.combined.cpp`
- Conflict marker file: `Build/integration-readiness/module-conflict.txt`
- Extracted modules: `module-base.cpp`, `module-m3.cpp`, `module-r1.cpp`
- Merge-tree output: `Build/integration-readiness/merge-tree-output.txt`
- SHA-256 hashes of these artifacts are captured in `M3_6_R1_INTEGRATION_PREVIEW_RUN_STATE`.
