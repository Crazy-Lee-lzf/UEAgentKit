#!/usr/bin/env python3
"""M3.5 stress native/fallback measurements on the 53-node M35 graph."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psutil

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT / "src"), str(ROOT / "tools")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from run_m3_menu_test import FakeBroker  # noqa: E402
from ue_launch import popen_minimized  # noqa: E402
from ue_agent_kit.graph_layout_preview import build_preview  # noqa: E402
from ue_agent_kit.layout_broker import protocol  # noqa: E402
from ue_agent_kit.layout_broker.descriptor import (  # noqa: E402
    build_project_path_hash,
    write_descriptor_atomic,
)
from ue_agent_kit.layout_broker.security import verify_file_permissions  # noqa: E402
from ue_agent_kit.layout_broker.server import LayoutBrokerServer  # noqa: E402

ENGINE = Path("E:/EPICGAME/UE_5.6")
HOST = ROOT / "Build" / "m2-native" / "M35" / "P11AutomationHost"
PROJECT = HOST / "P11AutomationHost.uproject"
EVIDENCE = ROOT / "Build" / "m2-native" / "Evidence" / "M35"
DESCRIPTOR = HOST / "Saved" / "UEAgentKit" / "LayoutBroker.json"
TOKEN = "f2a9c4e7b1d60835" * 4
ASSET = "/Game/M35/BP_M35_Stress.BP_M35_Stress"
GRAPH = "EventGraph"
TEST = "UEAgentKit.Native.M3Menu"


def config(native: bool = True) -> None:
    (HOST / "Config").mkdir(parents=True, exist_ok=True)
    (HOST / "Config" / "DefaultEngine.ini").write_text(
        "[UEAgentKit]\n"
        f"NativeLayoutPreview={'on' if native else 'off'}\n"
        "LegacyLayoutPreviewFallback=true\nNativeToolRegistry=off\n",
        encoding="utf-8",
    )


def descriptor(port: int, token: str = TOKEN, *, harden: bool = True) -> None:
    DESCRIPTOR.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schemaVersion": protocol.DESCRIPTOR_SCHEMA_VERSION,
        "address": "127.0.0.1",
        "port": port,
        "token": token,
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
    }
    if harden:
        write_descriptor_atomic(DESCRIPTOR, payload)
    else:
        DESCRIPTOR.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def run_editor(scenario: str, graph_id: str, capture: Path | None) -> tuple[int, dict, dict, float, float]:
    report = EVIDENCE / f"{graph_id}-{scenario}"
    report.mkdir(parents=True, exist_ok=True)
    command = [
        str(ENGINE / "Engine" / "Binaries" / "Win64" / "UnrealEditor-Cmd.exe"),
        str(PROJECT),
        "-unattended",
        "-nop4",
        "-nosplash",
        "-nopause",
        f"-UEAgentKitM3Scenario={scenario}",
        f"-UEAgentKitM3Asset={ASSET}",
        f"-UEAgentKitM3GraphName={GRAPH}",
        f"-UEAgentKitM32GraphId={graph_id}",
    ]
    if capture is not None:
        command.append(f"-UEAgentKitM32Capture={capture}")
    command.extend(
        [
            f"-ExecCmds=Automation RunTests {TEST};Quit",
            "-TestExit=Automation Test Queue Empty",
            f"-ReportExportPath={report}",
        ]
    )
    log = EVIDENCE / f"{graph_id}-{scenario}-ue.log"
    peak = 0
    started = time.monotonic()
    with log.open("wb") as handle:
        process = popen_minimized(command, cwd=str(HOST), stdout=handle, stderr=sys.stdout)
        try:
            proc = psutil.Process(process.pid)
        except psutil.Error:
            proc = None
        while process.poll() is None:
            if proc is not None:
                try:
                    peak = max(peak, int(proc.memory_info().rss))
                except psutil.Error:
                    pass
            time.sleep(0.05)
        exit_code = process.wait()
    wall = time.monotonic() - started
    automation = json.loads((report / "index.json").read_text(encoding="utf-8-sig"))
    evidence_path = HOST / "Evidence" / (
        f"m32-menu-{graph_id}.json" if scenario not in ("pie",) else f"m3-menu-pie-{graph_id}.json"
    )
    evidence = json.loads(evidence_path.read_text(encoding="utf-8")) if evidence_path.is_file() else {}
    return exit_code, automation, evidence, wall, peak / (1024 * 1024)


def main() -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "Captures").mkdir(parents=True, exist_ok=True)
    config()
    attempts: list[dict[str, Any]] = []
    for index in range(1, 4):
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
        acl = verify_file_permissions(DESCRIPTOR).as_dict()
        capture = EVIDENCE / "Captures" / f"stress-{index}.json"
        if capture.exists():
            capture.unlink()
        try:
            exit_code, automation, evidence, wall, peak_mb = run_editor(
                "happy", f"stress_{index}", capture
            )
        finally:
            status = server.status()
            server.stop()
            if DESCRIPTOR.exists():
                DESCRIPTOR.unlink()
        receipt = evidence.get("receipt") or {}
        direct = None
        parity = False
        moved = False
        if capture.is_file():
            payload = json.loads(capture.read_text(encoding="utf-8"))
            direct = build_preview(payload["preflight"], payload["envelope"])
            parity = (
                receipt.get("planId") == direct.get("planId")
                and receipt.get("layoutHash") == direct.get("layoutHash")
            )
            moved = sorted(receipt.get("movedNodeIds") or []) == sorted(
                direct.get("movedNodeIds") or []
            )
        attempts.append(
            {
                "attempt": index,
                "exitCode": exit_code,
                "automation": [t.get("state") for t in automation.get("tests", [])],
                "wallSeconds": round(wall, 3),
                "peakRssMb": round(peak_mb, 2),
                "invokeSeconds": evidence.get("invokeSeconds"),
                "waitSeconds": evidence.get("waitSeconds"),
                "fallbackSeconds": evidence.get("fallbackSeconds"),
                "receipt": receipt,
                "pythonParity": parity,
                "movedParity": moved,
                "brokerStatus": status,
                "descriptorAcl": acl,
                "ok": exit_code == 0
                and automation.get("failed") == 0
                and receipt.get("ok") is True
                and receipt.get("previewPath") == "native-broker"
                and receipt.get("fallback") is False
                and receipt.get("saved") is False
                and receipt.get("compiled") is False
                and receipt.get("packageDirtyAfter") is False
                and parity
                and moved,
            }
        )
        print(json.dumps({"attempt": index, "ok": attempts[-1]["ok"], "wall": wall}))
    # Fallback cases on the same graph.
    fallback_cases: list[dict[str, Any]] = []
    for scenario in ("fallback", "timeout"):
        if DESCRIPTOR.exists():
            DESCRIPTOR.unlink()
        server = None
        fake = None
        if scenario == "timeout":
            fake = FakeBroker("timeout")
            descriptor(fake.port)
        try:
            exit_code, automation, evidence, wall, peak_mb = run_editor(
                scenario, f"stress_{scenario}", None
            )
        finally:
            if server is not None:
                server.stop()
            if fake is not None:
                fake.close()
            if DESCRIPTOR.exists():
                DESCRIPTOR.unlink()
        receipt = evidence.get("receipt") or {}
        fallback_cases.append(
            {
                "scenario": scenario,
                "exitCode": exit_code,
                "automation": [t.get("state") for t in automation.get("tests", [])],
                "wallSeconds": round(wall, 3),
                "peakRssMb": round(peak_mb, 2),
                "invokeSeconds": evidence.get("invokeSeconds"),
                "waitSeconds": evidence.get("waitSeconds"),
                "fallbackSeconds": evidence.get("fallbackSeconds"),
                "receipt": receipt,
                "ok": exit_code == 0
                and automation.get("failed") == 0
                and receipt.get("ok") is True
                and receipt.get("fallback") is True
                and receipt.get("saved") is False
                and receipt.get("compiled") is False,
            }
        )
        print(json.dumps({"scenario": scenario, "ok": fallback_cases[-1]["ok"]}))
    native_wait = [a["waitSeconds"] for a in attempts if isinstance(a["waitSeconds"], (int, float))]
    fallback_wait = [
        c["waitSeconds"] for c in fallback_cases if isinstance(c["waitSeconds"], (int, float))
    ]
    native_latency = [
        (a["receipt"].get("invokedAtUtc"), a["receipt"].get("completedAtUtc")) for a in attempts
    ]
    output = {
        "schemaVersion": "ueak.m35.stress/1.0",
        "generatedAtUtc": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "fixture": json.loads(
            (HOST / "Evidence" / "m35-stress-fixture.json").read_text(encoding="utf-8")
        ),
        "nativeAttempts": attempts,
        "fallbackCases": fallback_cases,
        "latencySummary": {
            "attempts": len(attempts),
            "nativeWait": native_wait,
            "fallbackWait": fallback_wait,
        },
        "ok": all(a["ok"] for a in attempts) and all(c["ok"] for c in fallback_cases),
        "limits": [
            "Peak RSS is a sampled process metric, not allocator peak.",
            "No production project/config was touched.",
            "Broker was external; no Editor CreateProc.",
        ],
    }
    (EVIDENCE / "stress-metrics.json").write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"ok": output["ok"], "native": len(attempts), "fallback": len(fallback_cases)}))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
