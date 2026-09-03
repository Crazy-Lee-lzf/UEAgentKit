# UEAgentKit C1/C2 — P4 Minimum Dogfood — Result

> Date: 2026-09-03
>
> Branch: `feature/source-control-collaboration`
>
> Baseline: `fdf6b5c12aceaefb0e61478bee7a9eefdf5ade76`
>
> Plan: `docs/Plans/UEAGENTKIT_C1_C2_P4_MINIMUM_DOGFOOD_DETAILED_PLAN_20260903.md`
>
> State: **COMPLETE (A25 via owner-authorized fixture edit)**

## 1. Actual P4 CLI / server capability facts (C1-0)

Deterministic evidence saved under `benchmarks/source_control/c1_p4_capability_probe_20260903.json`.

| Fact | Observed value |
|---|---|
| Client executable | `p4.exe` @ `E:\Program Files\p4.exe` |
| Client version | `P4/NTX64/2025.1/2810567` |
| Server version | `P4D/NTX64/2025.1/2810567` (reachable, `P4PORT=localhost:1666`) |
| Client root / view | `E:/WorkSpace`; view `//depot/... → //<client>/...`; only the `//depot/Blender/...` subtree is actually depot-managed |
| `p4 -G` transport | Supported; tagged values are **raw `bytes`** keys/values (utf-8 normalized in product code) |
| `-G` record framing | Records are **consecutive marshal dicts with no separator byte** (proven by byte-level inspection); parsed precisely with `marshal.load` on a `BytesIO` |
| Tagged errors | `fstat`/`diff` on unmapped files return `code=error` records with **process exit code 0** → product state must parse records, never exit code alone |
| `fstat -a` | **Invalid option on this server** (`Usage: fstat ... Invalid option: -a`) → other-open facts come from `p4 opened -a` records instead |
| `p4 opened` / `opened -a` | Records carry `depotFile/user/client/action/change/type/rev/haveRev` |
| Local-change probe | `diff -se` / `diff -sd` are clean/empty for an unchanged exact path; benign error text `no such file(s)`, `not on client`, `up-to-date` does not imply dirt |
| Client options | `noallwrite` → non-opened synced files are readonly on disk (observed `0o100444`) |
| Path quoting / spaces | `where`/`fstat` round-trip paths containing spaces correctly through argv lists |
| Provider unavailable | Connection-refused fails fast (exit 1, tagged stderr); timeout behavior covered by bounded runner + fake fixture |

## 2. Provider / parser design

New module `src/ue_agent_kit/source_control.py` (stdlib only, zero new required dependencies):

```text
P4CommandRunner               structured, allowlisted argv runner (no shell)
P4SourceControlService        C1 status + C2 prepare_write
SourceControlFileState        bounded per-file P4 facts
SourceControlWarning          info / warning / strong-warning
SourceControlStatusResult     C1 payload
SourceControlPrepareResult    C2 receipts payload
ResolvedInputPath             local path / /Game path resolution
```

Runner properties:

- argv arrays only; `shell=True` is not representable and is absent from the module.
- Command allowlist `{info, where, fstat, opened, diff, sync, edit, client}` with per-command option allowlist (`-a`/`-se`/`-sd`/`-n`/`-o`); anything else, including every prohibited command, is rejected **inside the runner**.
- Prohibited regardless of caller: `submit revert delete obliterate unlock lock admin protect … resolve merge shelve unshelve integrate …` (see module `_PROHIBITED_COMMANDS`).
- Bounds: ≤16 files/request, ≤1024 path chars, single subprocess timeout 0.1–10 s (default 2 s), bounded stderr retention, no credentials/tickets in payloads.

Parsing: `p4 -G` → `marshal.load` streaming decoder → bytes normalized to utf-8 text. Human-formatted output is never parsed for product state. `-ztag` fallback was probed but is not needed.

## 3. Warning / readiness model

Severities: `info`, `warning`, `strong-warning`.

| State | Default C1/C2 behavior |
|---|---|
| clean / at head / own checkout | no warning (info `not-opened-for-edit` when not open) |
| not tracked by depot through this client | info `not-mapped`, `mapped=false` |
| opened by another user (ordinary) | warning `other-user-open`, continue |
| `+l` type held by another user | **strong** `exclusive-lock-other-user`; checkout attempt is surfaced as a failed receipt, not a rejection |
| workspace behind head | warning `behind-head` |
| workspace content differs from have | **strong** `local-differs-from-have` |
| provider unavailable / timeout | warning `source-control-unavailable`; every state still carries `localTestReady=true` |
| local writable override | **strong** `local-writable-override`, `localWritableOverride=true`, `submitReady=false` |

Readiness composition (P4 layer only; `writerSafetyReady` is never fabricated here):

```text
writerSafetyReady  left to the Writer (not produced by P4 layer)
sourceControlReady provider available + per-file query outcome
submitReady        opened in current client AND have present AND not other-locked
localTestReady     always true for valid inputs (P4 never blocks local testing)
```

## 4. Checkout / override / safe-sync evidence

All behaviors are covered by the deterministic fake fixture (32 unit tests) and the MCP contract tests (8 tests):

- `prepare_write` on a clean mapped file runs `p4 edit` and returns an `opened-for-edit` receipt; post-state shows opened-by-current-client and writable.
- Exclusive `+l` lock by another user: the `p4 edit` failure is returned as a receipt (`ok=false`), never converted into a Writer rejection.
- Local writable override is **disabled by default**; requires `allow_local_writable_override=true`; audits `beforeMode`/`afterMode`, forces `localWritableOverride=true` + `submitReady=false` + strong warning, and never auto-syncs over the file afterwards.
- Safe sync is performed only when **every** deterministic precondition is proven clean (mapped, exists, have present, behind head, not opened, no override, diff-verified unmodified, still readonly, no other lock) **and** explicitly requested (`request_safe_sync=true`). The precondition matrix is exercised in the fixture (skipped when not requested, when local content differs, when writable, when not behind head, etc.). Real-server safe sync was not run: no behind-head clean depot fixture exists in the current test depot (A17 kept truthful as `PASS` via deterministic fixture matrix + documented real limitation).
- No auto sync/revert/delete follow an override. No receipt ever performs or requests submit/revert/delete.

## 5. Prohibited-operation evidence

- No MCP tool, no CLI action, and no service method exposes submit/revert/delete.
- Runner unit tests assert every prohibited command and out-of-schema option token raises `SourceControlProhibitedOperationError`.
- Module scan test asserts the module contains no `shell=True`, no `os.system`, no `subprocess.call/Popen`.
- Fake fixture rejects any generic/unknown command (`whatever args`), i.e. it is not a generic shell.
- `ue_source_control_prepare_write` attempts checkout for locked files but stops before any human-only final operation; C3 changelist/resolve work is deferred by plan.

## 6. Real P4 read-only and mutation acceptance status

Read-only smoke (against the configured local server/client) — `PASS`:

- `ue_source_control_status` on the mapped example
  `E:/WorkSpace/Blender/XinYueHu/Blender_AutoLink_XinYueHu_Textures_Relative.py` reports
  `mapped=true`, `haveRev=1/headRev=1`, `localModified=false`, readonly, not opened.
- Unmapped example `E:/WorkSpace/UEAgentKit-Integration/pyproject.toml` reports `mapped=false`, `not-mapped` info.
- Paths with spaces round-trip as a single argv token.
- Provider identity/version are reported from `p4 info` without exposing credentials.

C2 real one-file edit acceptance (`A25`) — `PASS` via owner-authorized fixture:

```text
owner-designated fixture:
  E:/WorkSpace/Blender/XinYueHu/Blender_AutoLink_XinYueHu_Textures_Relative.py
pre-state : mapped, have=1/head=1, readonly, localModified=false
action    : bounded `p4 edit` (single exact file, no other mutation)
receipt   : {"action":"edit","ok":true,"message":"opened-for-edit"}
post-state: openedForEdit=true, openedByCurrentClient=true,
            action=edit, change=default, writable=true, submitReady=true
Agent then STOPPED. No revert/delete/sync by the harness.
```

> **Owner follow-up required:** the fixture file remains opened in changelist `default`
> for client `lzf_DESKTOP-8HD8KUO_8637`. Revert it manually (P4V / `p4 revert`) when ready.

## 7. Test counts / times

| Stage | Command | Result |
|---|---|---|
| G0 focused (new) | `py -3.12 test_source_control.py` | 32 tests OK (~4.2 s) |
| G0 focused (MCP) | `py -3.12 test_mcp_source_control_tools.py` | 8 tests OK (~1.1 s) |
| G1 | `py -3.12 scripts/RunPythonTests.py domain workflow` | 244 tests OK (27.8 s) |
| G2 | `py -3.12 scripts/RunPythonTests.py full` | **1008 tests OK (109.8 s, 17 skipped)** |
| G2 | `ruff check src tests scripts` | PASS |
| G2 | `py -3.12 -m compileall src tests/python scripts` | PASS |
| G2 | `ValidateRelease.py --expected-version 0.7.0 --skip-tests --skip-ruff` | PASSED 0.7.0 (3 schemas, 16 patch examples) |
| G2 | `git diff --check` | PASS |

## 8. Public surfaces

CLI (read-only):

```text
ue-agent source-control status <path...> [--project <root>]
```

MCP tools (registered when the server runs with `--enable-source-control`):

```text
ue_source_control_status(...)            read-only advisory status
ue_source_control_prepare_write(...)     advisory checkout/override/safe-sync assistance
```

Registry (`tool_registry.py`) adds group `source-control` with the two tools; the group is
opt-in (`source_control_enabled`), so every pre-existing mode/tool-list contract is unchanged.
`ue_get_capabilities` advertises `sourceControl` when enabled and always reports
`submitCapability=false`, `revertCapability=false`, `deleteCapability=false`,
`arbitraryCommandExecution=false`, `shellPassthrough=false`.

## 9. Files touched

```text
src/ue_agent_kit/source_control.py              new
src/ue_agent_kit/mcp_source_control_tools.py    new
src/ue_agent_kit/cli.py
src/ue_agent_kit/mcp_server.py
src/ue_agent_kit/tool_registry.py
tests/python/test_source_control.py             new (32 tests)
tests/python/test_mcp_source_control_tools.py   new (8 tests, MCP)
benchmarks/source_control/c1_p4_capability_probe_20260903.json   new evidence
```

Writer internals were not modified; P4 remains explicit assistance. No UE/UBT runs (expected 0).
