#!/usr/bin/env python3
"""Validate the minimized test-Editor launch on one real UE 5.6 automation."""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from ue_launch import monitor_state, popen_minimized

ROOT = Path(__file__).resolve().parents[1]
ENGINE = Path("E:/EPICGAME/UE_5.6")
HOST = ROOT / "Build" / "m2-native" / "M33" / "P11AutomationHost"
PROJECT = HOST / "P11AutomationHost.uproject"
EVIDENCE = ROOT / "Build" / "m2-native" / "Evidence" / "M33" / "WindowUx"


def main() -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    report = EVIDENCE / "P11MenuContract"
    report.mkdir(parents=True, exist_ok=True)
    command = [
        str(ENGINE / "Engine" / "Binaries" / "Win64" / "UnrealEditor-Cmd.exe"),
        str(PROJECT),
        "-unattended",
        "-nop4",
        "-nosplash",
        "-nopause",
        "-ExecCmds=Automation RunTests UEAgentKit.GraphLayout.P11MenuContract;Quit",
        "-TestExit=Automation Test Queue Empty",
        f"-ReportExportPath={report}",
    ]
    log = EVIDENCE / "P11MenuContract-ue.log"
    with log.open("wb") as handle:
        process = popen_minimized(
            command, cwd=str(HOST), stdout=handle, stderr=sys.stdout
        )
        exit_code = process.wait(timeout=180)
    time.sleep(0.5)
    state = monitor_state(process.pid)
    output = {
        "schemaVersion": "ueak.m33.window-ux/1.0",
        "generatedAtUtc": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "command": command,
        "exitCode": exit_code,
        "startupShowWindow": "SW_SHOWMINIMIZED (2)",
        "monitor": state,
        "headlessAllowed": state.get("found", 0) == 0,
        "ok": exit_code == 0 and (state.get("found", 0) == 0 or state.get("minimized", 0) > 0),
    }
    (EVIDENCE / "window-ux-result.json").write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"ok": output["ok"], "exit": exit_code, "monitor": state}))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
