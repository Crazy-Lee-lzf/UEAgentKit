#!/usr/bin/env python3
"""Exercise the M3.1 test-only startup flag override matrix."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from ue_launch import popen_minimized
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = Path("E:/EPICGAME/UE_5.6")
P11 = ROOT / "Build" / "m2-native" / "M3" / "P11AutomationHost"
M2 = ROOT / "Build" / "m2-native"
EVIDENCE = ROOT / "Build" / "m2-native" / "Evidence" / "M31" / "Settings"
TEST = "UEAgentKit.Native.M3Settings"


def write_config(host: Path) -> None:
    config = host / "Config" / "DefaultEngine.ini"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        "[UEAgentKit]\nNativeLayoutPreview=off\n"
        "LegacyLayoutPreviewFallback=true\nNativeToolRegistry=off\n",
        encoding="utf-8",
    )


def run_case(
    name: str,
    host: Path,
    child: bool,
    override: str | None,
    expected: dict[str, Any],
) -> dict[str, Any]:
    write_config(host)
    report = EVIDENCE / name
    report.mkdir(parents=True, exist_ok=True)
    command = [
        str(ENGINE / "Engine" / "Binaries" / "Win64" / "UnrealEditor-Cmd.exe"),
        str(host / ("M2NativeHost.uproject" if host.name == "m2-native" else f"{host.name}.uproject")),
        "-unattended",
        "-nop4",
        "-nosplash",
        "-nopause",
    ]
    if child:
        command.append("-UEAgentKitAutomationChild")
    if override is not None:
        command.append(f"-UEAgentKitNativePreviewTest={override}")
    command.extend(
        [
            f"-ExecCmds=Automation RunTests {TEST};Quit",
            "-TestExit=Automation Test Queue Empty",
            f"-ReportExportPath={report}",
        ]
    )
    log = EVIDENCE / f"{name}.log"
    with log.open("wb") as handle:
        process = popen_minimized(
            command, cwd=str(host), stdout=handle, stderr=subprocess.STDOUT
        )
        exit_code = process.wait(timeout=120)
    automation_path = report / "index.json"
    automation = (
        json.loads(automation_path.read_text(encoding="utf-8-sig"))
        if automation_path.is_file()
        else {}
    )
    settings_path = host / "Evidence" / "m3-settings.json"
    settings = (
        json.loads(settings_path.read_text(encoding="utf-8"))
        if settings_path.is_file()
        else {}
    )
    checks = {
        key: settings.get(key) == value for key, value in expected.items()
    }
    return {
        "name": name,
        "host": host.name,
        "automationChild": child,
        "override": override,
        "exitCode": exit_code,
        "automationState": [
            {"path": t.get("fullTestPath"), "state": t.get("state")}
            for t in automation.get("tests", [])
        ],
        "settings": settings,
        "expected": expected,
        "checks": checks,
        "ok": exit_code == 0
        and not automation.get("failed")
        and any(t.get("state") == "Success" for t in automation.get("tests", []))
        and all(checks.values()),
    }


def main() -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    cases = [
        (
            "child_on",
            P11,
            True,
            "on",
            {
                "nativeLayoutPreview": True,
                "legacyLayoutPreviewFallback": True,
                "testOverrideApplied": True,
                "nativeToolRegistry": False,
            },
        ),
        (
            "child_off",
            P11,
            True,
            "off",
            {
                "nativeLayoutPreview": False,
                "legacyLayoutPreviewFallback": True,
                "testOverrideApplied": True,
            },
        ),
        (
            "child_on_no_fallback",
            P11,
            True,
            "on-no-fallback",
            {
                "nativeLayoutPreview": True,
                "legacyLayoutPreviewFallback": False,
                "testOverrideApplied": True,
            },
        ),
        (
            "child_unknown",
            P11,
            True,
            "bogus",
            {
                "nativeLayoutPreview": False,
                "legacyLayoutPreviewFallback": True,
                "testOverrideApplied": False,
            },
        ),
        (
            "no_child_override_ignored",
            P11,
            False,
            "on",
            {
                "nativeLayoutPreview": False,
                "legacyLayoutPreviewFallback": True,
                "testOverrideApplied": False,
            },
        ),
        (
            "non_p11_override_ignored",
            M2,
            True,
            "on",
            {
                "nativeLayoutPreview": False,
                "legacyLayoutPreviewFallback": True,
                "testOverrideApplied": False,
            },
        ),
    ]
    results = [run_case(*case) for case in cases]
    output = {
        "schemaVersion": "ueak.m3.settings-matrix/1.0",
        "generatedAtUtc": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "ok": all(result["ok"] for result in results),
        "results": results,
    }
    (EVIDENCE / "m3-settings-matrix.json").write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"ok": output["ok"], "cases": len(results)}))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
