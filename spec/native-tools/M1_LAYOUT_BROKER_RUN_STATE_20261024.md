# M1 Layout Preview Broker — Run State (2026-10-24)

- Status: **M1 complete (offline)**; awaiting review before M2
- Branch: `feature/layout-broker-m1`
- Base: `b423b87`
- Worktree: `E:\WorkSpace\UEAgentKit-WT\layout-broker-m1`
- Remote: **no push, no merge, no reset, no clean, no delete** performed
- Protected areas: `Plugin/**`, P8/P9/P11 menu, `Build.cs`, `EditorBridge`, MCP routes — **not modified**

## 1. Environment

| Item | Value |
|---|---|
| OS | Windows (DESKTOP-8HD8KUO) |
| Python used | 3.12.10 (`py -3.12`) |
| Default `python` on PATH | 3.9.13 — **not** used for verification (repo requires 3.11–3.12) |
| UE Editor | not started |
| UE build | not run |
| Computer Use / UI automation | not used |
| Network | only ephemeral `127.0.0.1` sockets created by tests/probe |

Model: runtime-declared `cline-pass/deepseek-v4.1-flash-1017` (non-Fast, max reasoning). No independently
verifiable model identifier is exposed to the agent; see report §0.

## 2. Work completed

1. Reviewed N0 decisions (§4 protocol, §4.5 broker hard requirements, §8 threats), existing
   `graph_layout_preview.build_preview`, `mcp_graph_layout_preview`, and `tools/p9_layout_preview_worker.py`.
2. Confirmed EditorBridge is one-way (listener + accept only; no outbound `FSocket::Connect`); M1 does not
   assume reverse requests and does not touch the bridge.
3. Implemented `src/ue_agent_kit/layout_broker/`:
   - strict `ueak.layoutbroker/1.0` request/response validation and result self-check;
   - loopback-only single-process daemon with 1 MiB NDJSON framing, 5 s ceiling, token/project/session/
     algorithm identity, request-id dedup, single in-flight + busy, per-session rate limit, sanitized errors;
   - descriptor atomic write + Windows DACL harden/verify through `ctypes` (no `icacls`/subprocess);
   - offline client fixture with independent response/plan validation.
4. Added manual CLI `tools/run_layout_broker.py` (no autostart, required `--expect-session-id`,
   `--allow-unverified-acl` only for disposable hosts).
5. Added evidence probe `tools/layout_broker_offline_probe.py`.
6. Added a third derived parity fixture `bpctx_export_m1_fork.json` (linear + one forked terminal node).
7. Added 97 tests across protocol/server/client/CLI; added static isolation guards.
8. Ran the repository full Python suite.

## 3. Commands and results

| Command | Result |
|---|---|
| `py -3.12 -m unittest discover -s tests\python -p "test_layout_broker_*.py"` | **97 passed**, 0 failed (~10.1 s) |
| `py -3.12 tools\layout_broker_offline_probe.py` | `ok: true`, 13/13 checks, 3/3 exact parity |
| `py -3.12 scripts\RunPythonTests.py full --quiet` | **1338 tests, OK (skipped=17), 129.8 s**, see evidence log |
| `py -3.12 -m compileall -q src\ue_agent_kit\layout_broker tools ...` | exit 0 |
| ACL probe (sample descriptor) | `PROVEN / windows-dacl / protected-dacl-current-user-only`; `icacls` shows only `DESKTOP-8HD8KUO\Administrator:(F)` with no inherited allow ACE |

## 4. Evidence (ignored `Build/layout-broker/Evidence/`)

| File | SHA-256 |
|---|---|
| `m1-offline-probe.json` | `809ad07169ce0f4918f01c9dc171afc2705de6d4ed89f282a9f295d5dee035df` |
| `full-python-suite.log` | `12fbd268b282463b1f1d6aeaef726964b01724f1d3c2ef8bcccd4a15221c5089` |
| `descriptor-acl.txt` | `0c797a5f8201eba17201287965430938f9cbed1a46f5bbf2fbf69bddce1b8999` |
| `git-status.txt` | post-commit clean tree with branch/commit ids (regenerated after the final commit) |

The probe JSON contains: environment, broker identity, 13 checks, exact parity records, and explicit
limitations. The full-suite log contains the complete repository run output.

## 5. OFFLINE-PROVEN

- Protocol framing/validation/error codes; 1 MiB bounds; dedup; rate limit; single-inflight/busy; deadline;
  cancellation accounting; self-check; token/path redaction.
- Exact `build_preview` parity on three fixtures (full result equality, not spot-check only).
- Descriptor atomic publish, Windows DACL proof on this host, own-token-only removal.
- Manual CLI start/stop; ephemeral loopback listener; no autostart; no broker subprocess.
- Client rejection of forged/malformed/oversized plans.

## 6. Runtime-unverified / not done

- Editor-side client, reverse channel, descriptor read in C++, menu integration, receipts, notifications.
- P10/P11 regression, UE automation, real session/project values, real `<Project>/Saved/UEAgentKit` path.
- Large-graph latency and whether 5 s is sufficient.
- Shared-host/multi-user behavior; descriptor-directory ACL; TLS; signed descriptors.
- M2/M3/M4/M5/M6 and any cutover or Apply enablement.

No claim is made that Editor menu integration or production security has been demonstrated.

## 7. Stop point / next gate

M1 stops here. M2 (Editor snapshot client + shadow parity) requires a separate review/approval, a UE 5.6
build/host, and must preserve the legacy preview until parity + P10/P11 regression + rollback evidence exist.

## 8. Git state

Scoped local commit(s) on `feature/layout-broker-m1` (no push; see `git log -1`), containing the new broker
package, CLI/probe tools, tests, fixture, report, and this runstate. Existing files under `Plugin/`, `src/`
(non-broker), and `tests/` were not edited; the post-commit working tree is clean.
