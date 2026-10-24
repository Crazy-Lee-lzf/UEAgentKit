#!/usr/bin/env python3
"""Z1 multi-graph parity: real UE menu -> external M1 broker -> Python build_preview."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from ue_launch import popen_minimized
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT / "src"), str(ROOT / "tests" / "python")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from ue_agent_kit.graph_layout_preview import build_preview  # noqa: E402
from ue_agent_kit.layout_broker.descriptor import build_project_path_hash  # noqa: E402
from ue_agent_kit.layout_broker.server import LayoutBrokerServer  # noqa: E402

ENGINE = Path("E:/EPICGAME/UE_5.6")
HOST = ROOT / "Build" / "m2-native" / "M32" / "P11AutomationHost"
PROJECT = HOST / "P11AutomationHost.uproject"
EVIDENCE = ROOT / "Build" / "m2-native" / "Evidence" / "M32"
CAPTURES = EVIDENCE / "GraphParity"
TEST = "UEAgentKit.Native.M3Menu"


def config(native: bool = True, fallback: bool = True) -> None:
    (HOST / "Config").mkdir(parents=True, exist_ok=True)
    (HOST / "Config" / "DefaultEngine.ini").write_text(
        "[UEAgentKit]\n"
        f"NativeLayoutPreview={'on' if native else 'off'}\n"
        f"LegacyLayoutPreviewFallback={'true' if fallback else 'false'}\n"
        "NativeToolRegistry=off\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default=None)
    args = parser.parse_args()
    manifest_path = HOST / "Evidence" / "m32-fixtures.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    graphs = manifest["graphs"]
    if args.only:
        graphs = [g for g in graphs if g["kind"] == args.only]
    CAPTURES.mkdir(parents=True, exist_ok=True)
    config()
    results: list[dict[str, Any]] = []
    for item in graphs:
        kind = item["kind"]
        gid = kind.replace("-", "_")
        descriptor = HOST / "Saved" / "UEAgentKit" / "LayoutBroker.json"
        if descriptor.exists():
            descriptor.unlink()
        token = "a7e4c1d9b2f60358" * 4
        server = LayoutBrokerServer(
            descriptor_path=str(descriptor),
            project_name="P11AutomationHost",
            project_path_hash=build_project_path_hash(str(PROJECT)),
            expect_session_id=None,
            token=token,
        )
        server.start()
        report = EVIDENCE / f"Graph-{gid}"
        report.mkdir(parents=True, exist_ok=True)
        capture = CAPTURES / f"capture-{gid}.json"
        if capture.exists():
            capture.unlink()
        command = [
            str(ENGINE / "Engine" / "Binaries" / "Win64" / "UnrealEditor-Cmd.exe"),
            str(PROJECT),
            "-unattended",
            "-nop4",
            "-nosplash",
            "-nopause",
            f"-UEAgentKitM3Scenario=happy",
            f"-UEAgentKitM3Asset={item['assetPath']}",
            f"-UEAgentKitM3GraphName={item['graphName']}",
            f"-UEAgentKitM32GraphId={gid}",
            f"-UEAgentKitM32Capture={capture}",
            f"-ExecCmds=Automation RunTests {TEST};Quit",
            "-TestExit=Automation Test Queue Empty",
            f"-ReportExportPath={report}",
        ]
        log = EVIDENCE / f"Graph-{gid}.log"
        try:
            with log.open("wb") as handle:
                process = popen_minimized(
                    command, cwd=str(HOST), stdout=handle, stderr=subprocess.STDOUT
                )
                exit_code = process.wait(timeout=180)
        finally:
            server.stop()
            if descriptor.exists():
                descriptor.unlink()
        automation_path = report / "index.json"
        automation = (
            json.loads(automation_path.read_text(encoding="utf-8-sig"))
            if automation_path.is_file()
            else {}
        )
        host_evidence = HOST / "Evidence" / f"m32-menu-{gid}.json"
        evidence = (
            json.loads(host_evidence.read_text(encoding="utf-8"))
            if host_evidence.is_file()
            else {}
        )
        receipt = evidence.get("receipt") or {}
        direct: dict[str, Any] | None = None
        parity = False
        moved_match = False
        if capture.is_file():
            captured = json.loads(capture.read_text(encoding="utf-8"))
            direct = build_preview(captured["preflight"], captured["envelope"])
            parity = (
                receipt.get("planId") == direct.get("planId")
                and receipt.get("layoutHash") == direct.get("layoutHash")
            )
            moved_match = sorted(receipt.get("movedNodeIds") or []) == sorted(
                direct.get("movedNodeIds") or []
            )
        checks = {
            "editorExit0": exit_code == 0,
            "automationSuccess": automation.get("failed") == 0
            and any(t.get("state") == "Success" for t in automation.get("tests", [])),
            "nativePlan": receipt.get("ok") is True
            and receipt.get("planProduced") is True
            and receipt.get("previewPath") == "native-broker"
            and receipt.get("fallback") is False,
            "readOnly": receipt.get("readOnly") is True
            and receipt.get("saved") is False
            and receipt.get("compiled") is False,
            "clean": evidence.get("packageDirtyAfter") is False,
            "planIdParity": parity,
            "movedNodeIdsParity": moved_match,
            "graphGuid": receipt.get("graphGuid") == item["graphGuid"],
            "nodeCount": receipt.get("nodeCount") == item["nodeCount"],
        }
        result = {
            "kind": kind,
            "assetPath": item["assetPath"],
            "graphName": item["graphName"],
            "graphGuid": item["graphGuid"],
            "nodeCount": item["nodeCount"],
            "editorExitCode": exit_code,
            "automation": [
                {"path": t.get("fullTestPath"), "state": t.get("state")}
                for t in automation.get("tests", [])
            ],
            "receipt": receipt,
            "direct": direct,
            "checks": checks,
            "ok": all(checks.values()),
        }
        results.append(result)
        if host_evidence.is_file():
            shutil.copyfile(host_evidence, EVIDENCE / f"m32-menu-{gid}.json")
        print(json.dumps({"kind": kind, "ok": result["ok"], "checks": checks}))
    output = {
        "schemaVersion": "ueak.m32.graph-parity/1.0",
        "generatedAtUtc": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "ok": all(r["ok"] for r in results),
        "graphs": results,
    }
    (EVIDENCE / "z1-multigraph-parity.json").write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"ok": output["ok"], "graphs": len(results)}))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
