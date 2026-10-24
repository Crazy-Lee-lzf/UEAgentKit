# M3.6 / R1 Integration Preview — Run State (2026-10-24)

- Status: **READ-ONLY PLAN ONLY; no merge/build/runtime; M4 blocked**
- M3 worktree: `E:\WorkSpace\UEAgentKit-WT\layout-broker-m1`
- M3 branch: `feature/layout-broker-m1` @ `14187e91e699a946049e9a13cd831af8ca161199` (clean)
- R1 branch: `feature/r1-node-registry` @ `0782749f5c9be6e70c8d2a242d70aeedce9805b8`
- Merge base: `b423b876ee73c7cbfb6d50e6f7e58d2dfca30566`
- R1 worktree was not touched/compiled/run; its R1.4 UE acceptance remains IN PROGRESS.

## 1. Session/model

Same DSH session `session-7fd8360d-3ac6-47f5-898e-4d52afd3e59d`; runtime declares
`cline-pass/deepseek-v4.1-flash-1017`, non-Fast/max reasoning, no model switch. No Computer Use.

## 2. Read-only merge-tree

| Command/result | Value |
|---|---|
| `git merge-tree --write-tree --messages 14187e9 0782749` | tree `32659d00e163ee4931a528d3afffbc5560bce20a` |
| textual conflicts | **exactly 1**: `Plugin/UEAgentKit/Source/UEAgentKitEditor/Private/UEAgentKitEditorModule.cpp` |
| base/M3/R1 module blobs | `fda1f720…` / `676d35f5…` / `789ebd85…` |

No other textual conflicts. Full output: ignored
`Build/integration-readiness/merge-tree-output.txt`.

## 3. Candidate (NOT APPLIED)

`Build/integration-readiness/UEAgentKitEditorModule.combined.cpp` is an unbuilt candidate containing:

- M3 settings include + startup initialization outside the editor condition;
- R1 `Native/NativeNodeRegistry.h` include + registry init inside the editor condition;
- P11/P9/P8 menu and bridge teardown unchanged;
- R1 registry shutdown after bridge teardown.

Provenance: generated from the three exact module blobs above. It was never copied into
`Plugin/.../UEAgentKitEditorModule.cpp`, never compiled, and never run. No source changes were made.

## 4. Semantic risks recorded

- `NativeToolRegistry` is read from **GEngineIni** by M3's unused settings field and from
  **GGameIni** (`DefaultUEAgentKit.ini`) by R1. Defaults agree (off) but only one source should govern
  future shadow/active work.
- R1's added `BlueprintContextSha256::HashUtf8Bytes/HmacSha256` are additive; M3 `HashText` is
  unchanged. Combined build must re-verify M3 planId/hash.
- M3 P11 menu owns the ticker/service; R1 registry has no ticker. Combined shutdown order is P11
  menu → P9/P8 → bridge → registry.
- R1 adds manifest/config/resources and 9 `UEAgentKit.Native.R1*` automations; M3 adds separate
  tools/fixtures. No name/resource collision found.
- R1.4 cursor/pagination UE runtime is still in progress; the R1 state still points to an R1.3 DLL.
  **No R1.4 pass is claimed.**

## 5. Future approved regression (not run)

UE 5.6 combined build; R1 9 automation (including cursor vectors/pagination); R1 47 offline tests;
P10/P11 fixture/menu/scope/2nd-process reload; broker 140; full Python suite; default-off parity;
descriptor/token/read-only/no-write checks. R1.4 runtime must finish independently.

## 6. Artifact hashes

All under `Build/integration-readiness/` (git-ignored):

| SHA-256 | Artifact |
|---|---|
| `148e07c5f919929aa90c86155e67058d905af1eaef0bc2eadef1b6ffffc1c72d` | `merge-tree-output.txt` |
| `5dd3744222c6945d4a33a4332e69633e60650eee47b135fb4c908dc242567520` | `module-base.cpp` |
| `a4b3384bf69938f6664116008a7b48fe0c07534c8466786343823cc65aaf211b` | `module-conflict.txt` |
| `c2584c42a496db2695a90012e882ec537bfcbc23b760f0a0c714b326519ed9aa` | `module-m3.cpp` |
| `034b58487be5fb1e42dbcda8983ff83c661f7309c17f4039cb84f38d84376366` | `module-r1.cpp` |
| `b1f220a3afbb280487abd14112553c86a93ab14e6141aa38f0fadb4fe2014c63` | `module.combined.cpp` |
| `df233fb27504ba5781c5e7bd19c8f869bc9303e606e3eab40d8eafb95df66c9a` | `sha256-r1.diff` |

## 7. Stop

M4/default-on remains **NOT APPROVED**. No merge, no source apply, no build, no production change,
no push. This is documentation and an ignored candidate only.
