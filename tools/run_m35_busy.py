#!/usr/bin/env python3
"""M3.5 busy/repeat stress check on the 53-node graph."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT / "src"), str(ROOT / "tools")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from ue_launch import popen_minimized
from ue_agent_kit.layout_broker import protocol
from ue_agent_kit.layout_broker.descriptor import build_project_path_hash, write_descriptor_atomic
from ue_agent_kit.layout_broker.server import LayoutBrokerServer

ENGINE = Path("E:/EPICGAME/UE_5.6")
HOST = ROOT / "Build" / "m2-native" / "M35" / "P11AutomationHost"
PROJECT = HOST / "P11AutomationHost.uproject"
DESCRIPTOR = HOST / "Saved" / "UEAgentKit" / "LayoutBroker.json"
EVIDENCE = ROOT / "Build" / "m2-native" / "Evidence" / "M35"
TOKEN = "a1f6c3e9b2d70458" * 4


def main() -> int:
    (HOST / "Config").mkdir(parents=True, exist_ok=True)
    (HOST / "Config" / "DefaultEngine.ini").write_text(
        "[UEAgentKit]\nNativeLayoutPreview=on\nLegacyLayoutPreviewFallback=true\n"
        "NativeToolRegistry=off\n",
        encoding="utf-8",
    )
    if DESCRIPTOR.exists():
        DESCRIPTOR.unlink()
    server = LayoutBrokerServer(
        descriptor_path=str(DESCRIPTOR),
        project_name="P11AutomationHost",
        project_path_hash=build_project_path_hash(str(PROJECT)),
        expect_session_id=None,
        token=TOKEN,
    )
    server.start()
    write_descriptor_atomic(
        DESCRIPTOR,
        {
            "schemaVersion": protocol.DESCRIPTOR_SCHEMA_VERSION,
            "address": "127.0.0.1",
            "port": server.port,
            "token": TOKEN,
            "projectName": "P11AutomationHost",
            "projectPathHash": build_project_path_hash(str(PROJECT)),
            "brokerVersion": "0.1.0-m1",
            "supportedContractVersions": [protocol.PROTOCOL_SCHEMA_VERSION],
            "algorithms": [dict(protocol.ALGORITHM)],
            "startedAtUtc": datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
            "processId": 4242,
            "maxConcurrentJobs": 1,
        },
    )
    report = EVIDENCE / "stress-busy"
    report.mkdir(parents=True, exist_ok=True)
    command = [
        str(ENGINE / "Engine" / "Binaries" / "Win64" / "UnrealEditor-Cmd.exe"),
        str(PROJECT),
        "-unattended",
        "-nop4",
        "-nosplash",
        "-nopause",
        "-UEAgentKitM3Scenario=busy",
        "-UEAgentKitM3Asset=/Game/M35/BP_M35_Stress.BP_M35_Stress",
        "-UEAgentKitM3GraphName=EventGraph",
        "-UEAgentKitM32GraphId=stress_busy",
        "-ExecCmds=Automation RunTests UEAgentKit.Native.M3Menu;Quit",
        "-TestExit=Automation Test Queue Empty",
        f"-ReportExportPath={report}",
    ]
    log = EVIDENCE / "stress-busy-ue.log"
    try:
        with log.open("wb") as handle:
            proc = popen_minimized(command, cwd=str(HOST), stdout=handle, stderr=sys.stdout)
            exit_code = proc.wait(timeout=180)
    finally:
        status = server.status()
        server.stop()
        if DESCRIPTOR.exists():
            DESCRIPTOR.unlink()
    automation = json.loads((report / "index.json").read_text(encoding="utf-8-sig"))
    evidence = json.loads(
        (HOST / "Evidence" / "m32-menu-stress_busy.json").read_text(encoding="utf-8")
    )
    receipt = evidence.get("receipt") or {}
    output = {
        "schemaVersion": "ueak.m35.busy/1.0",
        "exitCode": exit_code,
        "automation": [t.get("state") for t in automation.get("tests", [])],
        "busyRejects": evidence.get("busyRejects"),
        "receipt": receipt,
        "brokerStatus": status,
        "ok": exit_code == 0
        and automation.get("failed") == 0
        and int(evidence.get("busyRejects", 0)) >= 1
        and receipt.get("ok") is True
        and receipt.get("saved") is False
        and receipt.get("compiled") is False,
    }
    (EVIDENCE / "stress-busy.json").write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"ok": output["ok"], "busy": output["busyRejects"]}))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
