# Test Editor Window Launch UX

- Scope: isolated `tools/` UE automation harnesses only.
- Default: `UnrealEditor-Cmd` test Editors launch **minimized**, never hidden.
- No UE plugin/production C++ change; no user-opened Editor is touched.

## Launch behavior

`tools/ue_launch.py` provides:

- Windows `subprocess.STARTUPINFO` with `STARTF_USESHOWWINDOW` and
  `wShowWindow=SW_SHOWMINIMIZED (2)`;
- a bounded `EnumWindows` monitor for **only the child PID**, calling
  `ShowWindow(SW_MINIMIZE)` if UE Slate creates/shows a top-level window after startup;
- no `CREATE_NO_WINDOW`, no `SW_HIDE`, no `-nullrhi` added for window handling;
- non-Windows or monitor failure safely falls back to the normal process launch and records
  the monitor error only in the local validation evidence.

All current `tools/` Editor launchers use the helper: M2 positive/negative, M3 menu/settings,
M3.2 graph/negative/rollback, and M3.3 matrix. Broker lifecycle, logging, timeouts, exit codes,
fixtures, and unattended semantics are unchanged.

## Validation

`py -3.12 tools/validate_minimized_launch.py` ran a real UE 5.6.1 `P11MenuContract`:

- Editor exit **0**, Automation **Success**;
- child PID window found and minimized (`found=114`, `minimized=114` monitor polls);
- no other process/window targeted;
- evidence: `Build/m2-native/Evidence/M33/WindowUx/`.

A patched real multi-graph harness (`run_m32_graph_parity.py --only event`) also exited 0 with
native parity after the helper change. Headless/no-window environments remain acceptable.
