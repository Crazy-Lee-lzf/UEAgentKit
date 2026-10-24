# RC0 — Unified Release Gate Review (M3.11) — 2026-10-25

- Candidate: `feature/m3-r1-integration-m38` @ `6f2608e43949663d68974983b63e9cd7e97922e2`
- Protected main: `c5ce3419c6962da49e874586f4e5dbd1457a4d0d` (clean; origin/main identical)
- Base/merge-base: `c5ce341`; ahead/behind **0/60**; diff **189 files, +75,052 / −35**
- Preflight `merge-tree --write-tree c5ce341 HEAD` → tree `64d49ad513e6d92d9c0a13b1eb60527516c660b3`, exit 0
- `git diff --check` → clean; candidate worktree clean
- Mode: read-only audit + light Python smoke; **no UE Editor/UBT, no production project, no push/merge**

## 1. Verdict

| Decision | Result |
|---|---|
| **MAIN-INTEGRATION (code merge, defaults OFF)** | **GO — with the conditions in §4** |
| **M4 / default-on** | **NO-GO** |
| **R7 production activation (MCP/Memory/pack)** | **NO-GO** (HOST_AUTH_PENDING; G1/G2/G6/G7 blocked) |
| **Release tag / 0.9 announcement** | **NO-GO in RC0** (not requested; version still 0.8.0) |

The candidate is a clean, additive, default-off integration of M3.9 + R1.6 + R7.4. It can be
merged into main as an unreleased integration, but it must not be advertised or activated as a
production/M4 feature.

## 2. Change shape (60 commits)

- **183 added / 6 modified / 0 deleted files.** All Python changes are additive; no existing
  `src/**` module was modified (26 new modules, 0 modified).
- 6 modified files are plugin C++ only:
  `BlueprintContextSha256.*`, `BlueprintGraphLayoutP11Menu.*`, `EditorBridge.cpp`,
  `UEAgentKitEditorModule.cpp`.
- Composition: 79 `.py`, 59 `.md`, 19 `.cpp`, 15 `.json`, 11 `.h`, 5 `.ps1`, 1 `.ini`; no binaries,
  no tracked `Build/**`.
- 3 merge commits: `7fd9a4e` (M3.7), `fc25ab6` (R1.6), `a2d9b9a` (R7.4). 15 `docs(native-tools)`,
  7 `docs(memory)` commits; the rest are feature/test/fix commits.
- Tracked bytes: 10,087,914 → 13,030,491 (+2,942,577). The dominant addition is the 1,016,853-byte
  `node-descriptor-golden.v1.json` (+ code/docs).

## 3. Static audit

| Area | Finding | Status |
|---|---|---|
| Package manifests | `pyproject.toml`, `requirements*.txt`, `UEAgentKit.uplugin`, `.gitignore` **unchanged** | PASS |
| Package discovery | `setuptools.find_packages('src')` = `ue_agent_kit`, `graph_contracts`, `layout_broker`, `memory_mount`, `pre_m4` | PASS |
| Dependency pinning | No new third-party dependency; Python 3.11–3.12 range unchanged | PASS |
| Test entry | `scripts/RunPythonTests.py` unchanged; suite auto-discovers new modules | PASS |
| Import side effects | One-process import of `ue_agent_kit`, `memory_mount`, `pre_m4`, `layout_broker`, `task_context`, `memory_service`: **0 new files, threads=1** | PASS |
| API/CLI defaults | `python -m ue_agent_kit.memory_mount --help` works; `verify`/`query` require an explicit pack; read-only; no default production path | PASS |
| Native defaults | `NativeToolRegistry=off` (DefaultUEAgentKit.ini; parser defaults off); `NativeLayoutPreview` zero-init false with test-only override on the disposable P11 host; `LegacyLayoutPreviewFallback=true` | PASS |
| MemoryMount default | `StagedContextHandoff(enabled=False)`; no host code constructs it with `enabled=True`; no MCP/tool registration references | PASS |
| MCP/tool exposure | `tool_registry.py`, `mcp_server.py`, `mcp_memory_tools.py` **unchanged**; no `memory_mount`/`staged_context`/`apply_blueprint_graph_layout` registration | PASS |
| Bridge endpoint | R1 adds a **conditional** `native.invoke` dispatch branch; capability advertised only when registry `Ready`; with off it returns `native-registry-unavailable` and is not advertised | PASS with caveat (§4.3) |
| Secrets/logging | No keys/tokens/private keys in added lines; 4 hits are synthetic `C:\\Users\\secret` redaction-test strings | PASS |
| Large files | One 1,016,853 B golden JSON under plugin `Resources`; no other >500 KB file | Non-blocking (§5.1) |
| Versioning | package/uplugin still `0.8.0`; no version bump | Non-blocking (§5.2) |
| Rollback | main is ancestor; merge is ff-able; revert path available | PASS |
| DB / external state | Coordination DB not opened/modified (mtime `2026-10-25 03:10:40`, unchanged); no production Memory DB, Lyra/Reforge | PASS |

## 4. Conditions for MAIN-INTEGRATION

1. **Defaults must remain OFF** in the merged commit: `NativeToolRegistry=off`,
   `NativeLayoutPreview=off/absent`, `StagedContextHandoff(enabled=False)`. A PR that flips any
   default is a blocker.
2. **No production activation claims**: merge as an unreleased integration candidate; do not tag
   `0.9`, do not advertise M4/R7 availability. Existing `graph-layout-preview-20261023` remains.
3. **Review the conditional `native.invoke` endpoint** (R1.5 shadow): it is reachable only when the
   registry is `Ready`, but it lives on the shared-token local bridge. Treat any future
   `shadow`/`active` canary as an M4 security item; merge itself keeps it off.
4. **Do not claim RC0 runtime**: no UE was executed in RC0. The plugin bytes are unchanged from
   M3.9 (`git diff 9f22f04 HEAD -- Plugin` empty in M3.10), whose DLL
   `sha256:A10379D4…447D` and UE acceptance remain the applicable pre-merge evidence. If CI can
   build UE, do it post-merge; RC0 does not.
5. **Keep R7.4 gate review authoritative**; R7 production remains blocked by host identity,
   signed packs, PII/retention, and tokenizer/endpoint decisions.

### 4.1 Must-fix before merging?

**None found for a default-off main merge.** The items above are merge conditions, not code
defects. The following are **must-fix before M4/default-on**, not before this merge:

- host issuer/principal/session/project binding and replay/revocation (G1/G2/G14);
- signed pack/trusted author and PII/retention policy (G6/G7);
- MCP/endpoint scope, rate, logging, and raw-byte response adapter;
- approved Z4/Z5/Z6 operational gates.

## 5. Non-blocking findings

1. **Golden JSON size.** `node-descriptor-golden.v1.json` is 1,016,853 B inside plugin `Resources`.
   It is only read in non-off registry modes; consider moving it to a test fixture outside the
   shipped plugin in a later cleanup (would require a UE build; not done here).
2. **Version metadata.** `0.8.0` remains for package and plugin; a version/release decision belongs
   to the eventual release, not this default-off merge.
3. **Docs volume.** 59 Markdown files incl. 15 native-tools and 7 memory docs add review load but
   no runtime risk; RC0 adds only this report and a short run-state.
4. **History shape.** 60 commits / 3 merges; a single `--no-ff` merge preserves provenance and is
   safe, but reviewers should use the commit-area map in §7.
5. **Dev-only tools** (`tools/run_m3_*`, `m39_*`) contain local engine/host paths; they are not
   imported by the package and are not MCP endpoints.

## 6. MAIN-INTEGRATION / rollback procedure (for the coordinator)

1. Freeze candidate `6f2608e`; confirm `git status --porcelain` empty.
2. Open one PR `feature/m3-r1-integration-m38` → `main` (base `c5ce341`); keep default-off
   conditions in the description.
3. Required checks: Python full suite (1522/17 expected), `native_tools` 57, `git diff --check`,
   defaults grep, no new MCP tool. UE build optional in CI; if absent, cite the identical plugin
   hash and M3.9 UE evidence, not a new runtime claim.
4. Merge with `--no-ff` (one merge commit) or fast-forward if policy prefers; **do not squash**
   without a decision, because the three integration merges are provenance.
5. Post-merge verify on main: `NativeToolRegistry=off`, `NativeLayoutPreview` absent/off,
   `StagedContextHandoff` default false, `find_packages` includes new modules, full suite green.
6. Rollback: `git revert -m 1 <merge-commit>` on main; never reset/force. If only docs cause
   trouble, revert the docs commit separately. Keep the candidate branch untouched.
7. RC0 does **not** create a tag or push. A release tag requires a later decision.

## 7. Minimal PR / change-set split (if review policy requires)

Single PR is technically safe (merge-tree clean, additive, tests green). If a split is mandatory,
use three path-scoped change-sets, in this order:

1. **R7 read-only memory_mount** — `src/ue_agent_kit/memory_mount/**`, its tests/schemas/docs
   (`spec/memory-mount/**`), independent of the plugin.
2. **R1 + M3 native editor surface** — `Plugin/UEAgentKit/**`, M3 native layout preview,
   P11 menu integration, R1 tests (`tests/native_tools/**`), M3 evidence docs.
3. **Pre-M4 Z5/Z6 + dev tools** — `src/ue_agent_kit/pre_m4/**`, `tools/m3*`/`m39*`, related tests
   and reports.

Order matters only for review; a single merge avoids cherry-pick risk. The 60-commit risk map:
3 merge commits (M3.7/R1.6/R7.4), 15 native-tools docs, 7 memory docs, 4 memory features, 4 native
tests, 3 native features; no deletions and no existing Python edits.

## 8. Integrity checklist

```text
candidate SHA       6f2608e43949663d68974983b63e9cd7e97922e2
main / origin SHA   c5ce3419c6962da49e874586f4e5dbd1457a4d0d
merge-base          c5ce3419c6962da49e874586f4e5dbd1457a4d0d
ahead/behind        0 / 60
merge-tree tree     64d49ad513e6d92d9c0a13b1eb60527516c660b3
diff                189 files, +75052 / -35
binary files        0
deleted files       0
tracked Build       0
diff --check        clean
candidate status    clean
plugin delta        none since M3.9 (DLL A10379D4…447D is pre-merge evidence)
DB                  not opened/modified (mtime 2026-10-25 03:10:40)
```

## 9. Evidence / limits

- M3.10 tests on the same code (docs-only commits after): full **1522/17 skipped OK**, R7 **133
  OK**, native_tools **57 OK**, R7.3 harness default-off byte parity true, Z5/Z6 PASS.
- RC0 additionally performed: git facts, merge-tree/diff-check, package discovery, one-process
  import smoke, CLI help, defaults/MCP/secrets/size scans. No UE run.
- This review is a merge gate for a default-off candidate, not a production release, M4 approval,
  or R7 activation.
