#!/usr/bin/env python3
"""Z3 rollback drill: native -> legacy fallback -> off legacy, separate processes."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT / "src"), str(ROOT / "tests" / "python")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from ue_agent_kit.layout_broker.descriptor import build_project_path_hash  # noqa: E402
from ue_agent_kit.layout_broker.server import LayoutBrokerServer  # noqa: E402

ENGINE = Path("E:/EPICGAME/UE_5.6")
HOST = ROOT / "Build" / "m2-native" / "M32" / "P11AutomationHost"
PROJECT = HOST / "P11AutomationHost.uproject"
EVIDENCE = ROOT / "Build" / "m2-native" / "Evidence" / "M32"
MANIFEST = HOST / "Evidence" / "m32-fixtures.json"
DESCRIPTOR = HOST / "Saved" / "UEAgentKit" / "LayoutBroker.json"


def config(native: bool) -> None:
    (HOST / "Config").mkdir(parents=True, exist_ok=True)
    (HOST / "Config" / "DefaultEngine.ini").write_text(
        "[UEAgentKit]\n"
        f"NativeLayoutPreview={'on' if native else 'off'}\n"
        "LegacyLayoutPreviewFallback=true\nNativeToolRegistry=off\n",
        encoding="utf-8",
    )


def run(test: str, args: list[str], name: str) -> tuple[int, dict]:
    report = EVIDENCE / name
    report.mkdir(parents=True, exist_ok=True)
    command = [
        str(ENGINE / "Engine" / "Binaries" / "Win64" / "UnrealEditor-Cmd.exe"),
        str(PROJECT),
        "-unattended",
        "-nop4",
        "-nosplash",
        "-nullrhi",
        "-nopause",
    ] + args + [
        f"-ExecCmds=Automation RunTests {test};Quit",
        "-TestExit=Automation Test Queue Empty",
        f"-ReportExportPath={report}",
    ]
    log = EVIDENCE / f"{name}.log"
    with log.open("wb") as handle:
        process = subprocess.Popen(
            command, cwd=str(HOST), stdout=handle, stderr=subprocess.STDOUT
        )
        exit_code = process.wait(timeout=180)
    automation_path = report / "index.json"
    automation = (
        json.loads(automation_path.read_text(encoding="utf-8-sig"))
        if automation_path.is_file()
        else {}
    )
    return exit_code, automation


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    graph = next(g for g in manifest["graphs"] if g["kind"] == "event")
    results = []
    # 0: establish the P11 fixture using legacy/default-off.
    config(False)
    exit_code, automation = run("UEAgentKit.GraphLayout.P11FixtureContract", [], "Rollback-P11Fixture")
    results.append({"step": "legacy-fixture", "exit": exit_code, "automation": automation.get("tests", [])})

    # 1: native with external broker.
    config(True)
    if DESCRIPTOR.exists():
        DESCRIPTOR.unlink()
    server = LayoutBrokerServer(
        descriptor_path=str(DESCRIPTOR),
        project_name="P11AutomationHost",
        project_path_hash=build_project_path_hash(str(PROJECT)),
        expect_session_id=None,
        token="c9a1f4e7b2d60835" * 4,
    )
    server.start()
    try:
        exit_code, automation = run(
            "UEAgentKit.Native.M3Menu",
            [
                "-UEAgentKitM3Scenario=happy",
                f"-UEAgentKitM3Asset={graph['assetPath']}",
                f"-UEAgentKitM3GraphName={graph['graphName']}",
                "-UEAgentKitM32GraphId=rollback_native",
            ],
            "Rollback-Native",
        )
    finally:
        server.stop()
        if DESCRIPTOR.exists():
            DESCRIPTOR.unlink()
    native_evidence = HOST / "Evidence" / "m32-menu-rollback_native.json"
    native = (
        json.loads(native_evidence.read_text(encoding="utf-8")).get("receipt", {})
        if native_evidence.is_file()
        else {}
    )
    results.append({"step": "native", "exit": exit_code, "automation": automation.get("tests", []), "receipt": native})

    # 2: broker absent, fallback allowed.
    config(True)
    exit_code, automation = run(
        "UEAgentKit.Native.M3Menu",
        [
            "-UEAgentKitM3Scenario=fallback",
            f"-UEAgentKitM3Asset={graph['assetPath']}",
            f"-UEAgentKitM3GraphName={graph['graphName']}",
            "-UEAgentKitM32GraphId=rollback_fallback",
        ],
        "Rollback-Fallback",
    )
    fallback_evidence = HOST / "Evidence" / "m32-menu-rollback_fallback.json"
    fallback = (
        json.loads(fallback_evidence.read_text(encoding="utf-8")).get("receipt", {})
        if fallback_evidence.is_file()
        else {}
    )
    results.append({"step": "fallback", "exit": exit_code, "automation": automation.get("tests", []), "receipt": fallback})

    # 3: off restart, legacy only, no descriptor/broker.
    config(False)
    if DESCRIPTOR.exists():
        DESCRIPTOR.unlink()
    exit_code, automation = run("UEAgentKit.GraphLayout.P11MenuContract", [], "Rollback-Off")
    legacy_receipt_path = HOST / "Saved" / "UEAgentKit" / "P11Preview" / "menu-invocation.json"
    legacy = (
        json.loads(legacy_receipt_path.read_text(encoding="utf-8"))
        if legacy_receipt_path.is_file()
        else {}
    )
    results.append(
        {
            "step": "off-legacy",
            "exit": exit_code,
            "automation": automation.get("tests", []),
            "receipt": legacy,
            "descriptorPresent": DESCRIPTOR.exists(),
        }
    )
    checks = {
        "fixtureExit0": results[0]["exit"] == 0,
        "nativeExit0": results[1]["exit"] == 0
        and results[1]["receipt"].get("previewPath") == "native-broker"
        and results[1]["receipt"].get("fallback") is False,
        "fallbackExit0": results[2]["exit"] == 0
        and results[2]["receipt"].get("fallback") is True
        and results[2]["receipt"].get("nativeErrorCode") == "native-preview-broker-descriptor-missing",
        "offExit0": results[3]["exit"] == 0,
        "offReceiptHasNoNativeFields": "fallback" not in results[3]["receipt"]
        and "nativeErrorCode" not in results[3]["receipt"],
        "offNoDescriptor": results[3]["descriptorPresent"] is False,
    }
    output = {
        "schemaVersion": "ueak.m32.rollback/1.0",
        "generatedAtUtc": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "checks": checks,
        "ok": all(checks.values()),
        "results": results,
        "finalConfig": "NativeLayoutPreview=off",
    }
    (EVIDENCE / "z3-rollback.json").write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"ok": output["ok"], "checks": checks}))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
