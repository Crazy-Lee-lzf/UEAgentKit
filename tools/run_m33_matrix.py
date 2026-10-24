#!/usr/bin/env python3
"""M3.3 Z2 expanded matrix across the three M32 graphs."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from ue_launch import popen_minimized

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT / "src"), str(ROOT / "tests" / "python"), str(ROOT / "tools")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from run_m3_menu_test import FakeBroker  # noqa: E402
from ue_agent_kit.layout_broker import protocol  # noqa: E402
from ue_agent_kit.layout_broker.descriptor import (  # noqa: E402
    build_project_path_hash,
    write_descriptor_atomic,
)
from ue_agent_kit.layout_broker.server import LayoutBrokerServer  # noqa: E402

ENGINE = Path("E:/EPICGAME/UE_5.6")
HOST = ROOT / "Build" / "m2-native" / "M32" / "P11AutomationHost"
PROJECT = HOST / "P11AutomationHost.uproject"
EVIDENCE = ROOT / "Build" / "m2-native" / "Evidence" / "M33"
DESCRIPTOR = HOST / "Saved" / "UEAgentKit" / "LayoutBroker.json"
MANIFEST = HOST / "Evidence" / "m32-fixtures.json"
TOKEN = "d4b7e2a9c1f60853" * 4
MENU = "UEAgentKit.Native.M3Menu"
PIE = "UEAgentKit.Native.M3PieMenu"


def config() -> None:
    (HOST / "Config").mkdir(parents=True, exist_ok=True)
    (HOST / "Config" / "DefaultEngine.ini").write_text(
        "[UEAgentKit]\nNativeLayoutPreview=on\nLegacyLayoutPreviewFallback=true\n"
        "NativeToolRegistry=off\n",
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


def run_test(
    test: str,
    graph: dict,
    scenario: str,
    graph_id: str,
) -> tuple[int, dict, dict]:
    report = EVIDENCE / f"{graph_id}-{scenario}"
    report.mkdir(parents=True, exist_ok=True)
    command = [
        str(ENGINE / "Engine" / "Binaries" / "Win64" / "UnrealEditor-Cmd.exe"),
        str(PROJECT),
        "-unattended",
        "-nop4",
        "-nosplash",
        "-nopause",
    ]
    if test == MENU:
        command.append(f"-UEAgentKitM3Scenario={scenario}")
    command.extend(
        [
            f"-UEAgentKitM3Asset={graph['assetPath']}",
            f"-UEAgentKitM3GraphName={graph['graphName']}",
            f"-UEAgentKitM32GraphId={graph_id}",
            f"-ExecCmds=Automation RunTests {test};Quit",
            "-TestExit=Automation Test Queue Empty",
            f"-ReportExportPath={report}",
        ]
    )
    log = EVIDENCE / f"{graph_id}-{scenario}-ue.log"
    with log.open("wb") as handle:
        process = popen_minimized(
            command, cwd=str(HOST), stdout=handle, stderr=subprocess.STDOUT
        )
        exit_code = process.wait(timeout=240)
    automation_path = report / "index.json"
    automation = (
        json.loads(automation_path.read_text(encoding="utf-8-sig"))
        if automation_path.is_file()
        else {}
    )
    if test == MENU:
        path = HOST / "Evidence" / f"m32-menu-{graph_id}.json"
    else:
        path = HOST / "Evidence" / f"m3-menu-pie-{graph_id}.json"
    evidence = (
        json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    )
    return exit_code, automation, evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default=None)
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    graphs = manifest["graphs"]
    if args.only:
        graphs = [g for g in graphs if g["kind"] == args.only]
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    config()
    results = []
    for graph in graphs:
        gid = graph["kind"].replace("-", "_")
        # Cases that need no broker.
        cases = [
            ("dirty", None, "native-preview-package-dirty", "strict"),
            ("fallback", None, "native-preview-broker-descriptor-missing", "fallback"),
            ("unavailable", None, "native-preview-broker-unavailable", "fallback"),
            ("dacl", None, "native-preview-broker-descriptor-invalid", "strict"),
        ]
        for scenario, broker_kind, expected, kind in cases:
            if DESCRIPTOR.exists():
                DESCRIPTOR.unlink()
            server = None
            fake = None
            if scenario == "dacl":
                descriptor(50001, harden=False)
            elif scenario == "unavailable":
                descriptor(50001)
            try:
                exit_code, automation, evidence = run_test(
                    MENU, graph, scenario, f"{gid}_{scenario}"
                )
            finally:
                if server is not None:
                    server.stop()
                if fake is not None:
                    fake.close()
                if DESCRIPTOR.exists():
                    DESCRIPTOR.unlink()
            receipt = evidence.get("receipt") or {}
            ok = (
                exit_code == 0
                and automation.get("failed") == 0
                and any(t.get("state") == "Success" for t in automation.get("tests", []))
                and receipt.get("readOnly") is True
                and receipt.get("saved") is False
                and receipt.get("compiled") is False
                and evidence.get("packageDirtyAfter") is False
            )
            if kind == "fallback":
                ok = ok and receipt.get("ok") is True and receipt.get("fallback") is True
                ok = ok and receipt.get("nativeErrorCode") == expected
            else:
                ok = ok and receipt.get("ok") is False and receipt.get("fallback") is False
                ok = ok and receipt.get("errorCode") == expected
            results.append(
                {
                    "graph": graph["kind"],
                    "scenario": scenario,
                    "expected": expected,
                    "kind": kind,
                    "exit": exit_code,
                    "automation": automation.get("tests", []),
                    "receipt": receipt,
                    "ok": ok,
                }
            )
            print(json.dumps({"graph": graph["kind"], "case": scenario, "ok": ok}))
        # Broker-injected cases.
        for scenario, expected, kind in [
            ("revision", "native-preview-revision-conflict", "strict"),
            ("fingerprint", "native-preview-fingerprint-conflict", "strict"),
            ("forged", "native-preview-plan-id-mismatch", "strict"),
            ("timeout", "native-preview-timeout", "fallback"),
            ("oversize-response", "native-preview-response-too-large", "strict"),
        ]:
            if DESCRIPTOR.exists():
                DESCRIPTOR.unlink()
            fake = FakeBroker(scenario)
            descriptor(fake.port)
            try:
                exit_code, automation, evidence = run_test(
                    MENU, graph, scenario, f"{gid}_{scenario}"
                )
            finally:
                fake.close()
                if DESCRIPTOR.exists():
                    DESCRIPTOR.unlink()
            receipt = evidence.get("receipt") or {}
            ok = (
                exit_code == 0
                and automation.get("failed") == 0
                and any(t.get("state") == "Success" for t in automation.get("tests", []))
                and receipt.get("readOnly") is True
                and receipt.get("saved") is False
                and receipt.get("compiled") is False
                and evidence.get("packageDirtyAfter") is False
            )
            if kind == "fallback":
                ok = ok and receipt.get("ok") is True and receipt.get("fallback") is True
                ok = ok and receipt.get("nativeErrorCode") == expected
            else:
                ok = ok and receipt.get("ok") is False and receipt.get("fallback") is False
                ok = ok and receipt.get("errorCode") == expected
            results.append(
                {
                    "graph": graph["kind"],
                    "scenario": scenario,
                    "expected": expected,
                    "kind": kind,
                    "exit": exit_code,
                    "automation": automation.get("tests", []),
                    "receipt": receipt,
                    "ok": ok,
                }
            )
            print(json.dumps({"graph": graph["kind"], "case": scenario, "ok": ok}))
        # Real broker: busy and token.
        for scenario in ("busy", "token"):
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
            if scenario == "token":
                descriptor(server.port, "0" * 64)
            else:
                descriptor(server.port)
            try:
                exit_code, automation, evidence = run_test(
                    MENU, graph, scenario, f"{gid}_{scenario}"
                )
            finally:
                server.stop()
                if DESCRIPTOR.exists():
                    DESCRIPTOR.unlink()
            receipt = evidence.get("receipt") or {}
            ok = (
                exit_code == 0
                and automation.get("failed") == 0
                and any(t.get("state") == "Success" for t in automation.get("tests", []))
                and receipt.get("readOnly") is True
                and receipt.get("saved") is False
                and receipt.get("compiled") is False
                and evidence.get("packageDirtyAfter") is False
            )
            if scenario == "busy":
                ok = ok and receipt.get("ok") is True and receipt.get("fallback") is False
                ok = ok and int(evidence.get("busyRejects", 0)) >= 1
            else:
                ok = ok and receipt.get("ok") is False and receipt.get("fallback") is False
                ok = ok and receipt.get("errorCode") == "native-preview-token-rejected"
            results.append(
                {
                    "graph": graph["kind"],
                    "scenario": scenario,
                    "expected": "native-preview-token-rejected"
                    if scenario == "token"
                    else "native-plan-busy",
                    "kind": "strict" if scenario == "token" else "native",
                    "exit": exit_code,
                    "automation": automation.get("tests", []),
                    "receipt": receipt,
                    "ok": ok,
                }
            )
            print(json.dumps({"graph": graph["kind"], "case": scenario, "ok": ok}))
        # PIE.
        exit_code, automation, evidence = run_test(
            PIE, graph, "pie", f"{gid}_pie"
        )
        receipt = evidence.get("receipt") or {}
        ok = (
            exit_code == 0
            and automation.get("failed") == 0
            and any(t.get("state") == "Success" for t in automation.get("tests", []))
            and receipt.get("ok") is False
            and receipt.get("fallback") is False
            and receipt.get("errorCode") == "native-preview-pie-active"
            and evidence.get("packageDirtyAfter") is False
        )
        results.append(
            {
                "graph": graph["kind"],
                "scenario": "pie",
                "expected": "native-preview-pie-active",
                "kind": "strict",
                "exit": exit_code,
                "automation": automation.get("tests", []),
                "receipt": receipt,
                "ok": ok,
            }
        )
        print(json.dumps({"graph": graph["kind"], "case": "pie", "ok": ok}))

    output = {
        "schemaVersion": "ueak.m33.z2-matrix/1.0",
        "generatedAtUtc": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "ok": all(r["ok"] for r in results),
        "cases": len(results),
        "results": results,
        "note": "Oversized request is service-level M2.3; response oversize is here. "
        "Macro graph is not claimed.",
    }
    (EVIDENCE / "z2-expanded-matrix.json").write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"ok": output["ok"], "cases": len(results)}))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
