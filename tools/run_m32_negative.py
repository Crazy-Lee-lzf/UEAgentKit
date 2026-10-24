#!/usr/bin/env python3
"""Z2 subset: negative menu cases on the complex M32 graph (not the P11 fixture)."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT / "src"), str(ROOT / "tests" / "python")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from ue_agent_kit.layout_broker import protocol  # noqa: E402
from ue_agent_kit.layout_broker.descriptor import (  # noqa: E402
    build_project_path_hash,
    write_descriptor_atomic,
)
from ue_agent_kit.layout_broker.server import LayoutBrokerServer  # noqa: E402
from run_m3_menu_test import FakeBroker  # noqa: E402

ENGINE = Path("E:/EPICGAME/UE_5.6")
HOST = ROOT / "Build" / "m2-native" / "M32" / "P11AutomationHost"
PROJECT = HOST / "P11AutomationHost.uproject"
EVIDENCE = ROOT / "Build" / "m2-native" / "Evidence" / "M32"
DESCRIPTOR = HOST / "Saved" / "UEAgentKit" / "LayoutBroker.json"
MANIFEST = HOST / "Evidence" / "m32-fixtures.json"
TEST = "UEAgentKit.Native.M3Menu"
TOKEN = "b83f5d2a7c1e9460" * 4
EXPECTED = {
    "dirty": "native-preview-package-dirty",
    "revision": "native-preview-revision-conflict",
    "fingerprint": "native-preview-fingerprint-conflict",
    "forged": "native-preview-plan-id-mismatch",
    "token": "native-preview-token-rejected",
}


def write_descriptor(port: int, token: str = TOKEN) -> None:
    DESCRIPTOR.parent.mkdir(parents=True, exist_ok=True)
    write_descriptor_atomic(
        DESCRIPTOR,
        {
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
        },
    )


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    graph = next(g for g in manifest["graphs"] if g["kind"] == "cast-comment-knot")
    config = HOST / "Config" / "DefaultEngine.ini"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        "[UEAgentKit]\nNativeLayoutPreview=on\nLegacyLayoutPreviewFallback=true\n"
        "NativeToolRegistry=off\n",
        encoding="utf-8",
    )
    results = []
    for scenario, expected in EXPECTED.items():
        if DESCRIPTOR.exists():
            DESCRIPTOR.unlink()
        server = None
        fake = None
        if scenario == "token":
            server = LayoutBrokerServer(
                descriptor_path=str(DESCRIPTOR),
                project_name="P11AutomationHost",
                project_path_hash=build_project_path_hash(str(PROJECT)),
                expect_session_id=None,
                token=TOKEN,
            )
            server.start()
            write_descriptor(server.port, "0" * 64)
        elif scenario in ("revision", "fingerprint", "forged"):
            fake = FakeBroker(scenario)
            write_descriptor(fake.port)
        # dirty uses no descriptor; the preflight gate fails before connecting.
        report = EVIDENCE / f"Neg-{scenario}"
        report.mkdir(parents=True, exist_ok=True)
        command = [
            str(ENGINE / "Engine" / "Binaries" / "Win64" / "UnrealEditor-Cmd.exe"),
            str(PROJECT),
            "-unattended",
            "-nop4",
            "-nosplash",
            "-nopause",
            f"-UEAgentKitM3Scenario={scenario}",
            f"-UEAgentKitM3Asset={graph['assetPath']}",
            f"-UEAgentKitM3GraphName={graph['graphName']}",
            f"-UEAgentKitM32GraphId=neg_{scenario}",
            f"-ExecCmds=Automation RunTests {TEST};Quit",
            "-TestExit=Automation Test Queue Empty",
            f"-ReportExportPath={report}",
        ]
        log = EVIDENCE / f"Neg-{scenario}.log"
        try:
            with log.open("wb") as handle:
                process = subprocess.Popen(
                    command, cwd=str(HOST), stdout=handle, stderr=subprocess.STDOUT
                )
                exit_code = process.wait(timeout=180)
        finally:
            if server is not None:
                server.stop()
            if fake is not None:
                fake.close()
            if DESCRIPTOR.exists():
                DESCRIPTOR.unlink()
        automation_path = report / "index.json"
        automation = (
            json.loads(automation_path.read_text(encoding="utf-8-sig"))
            if automation_path.is_file()
            else {}
        )
        receipt_path = HOST / "Evidence" / f"m32-menu-neg_{scenario}.json"
        receipt = (
            json.loads(receipt_path.read_text(encoding="utf-8")).get("receipt", {})
            if receipt_path.is_file()
            else {}
        )
        ok = (
            exit_code == 0
            and automation.get("failed") == 0
            and any(t.get("state") == "Success" for t in automation.get("tests", []))
            and receipt.get("ok") is False
            and receipt.get("fallback") is False
            and receipt.get("errorCode") == expected
        )
        results.append(
            {
                "scenario": scenario,
                "expected": expected,
                "exitCode": exit_code,
                "automation": [
                    {"path": t.get("fullTestPath"), "state": t.get("state")}
                    for t in automation.get("tests", [])
                ],
                "receipt": receipt,
                "ok": ok,
            }
        )
        print(json.dumps({"scenario": scenario, "ok": ok, "code": receipt.get("errorCode")}))
    output = {
        "schemaVersion": "ueak.m32.negative/1.0",
        "generatedAtUtc": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "graph": graph,
        "ok": all(r["ok"] for r in results),
        "results": results,
        "note": "Subset on the cast/comment/knot graph; P11 fixture cases remain in M31.",
    }
    (EVIDENCE / "z2-complex-negative.json").write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"ok": output["ok"], "cases": len(results)}))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
