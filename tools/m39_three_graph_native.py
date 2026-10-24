#!/usr/bin/env python3
"""M3.9 three-graph native preview determinism / fallback verification.

Runs the real M3Menu automation against the combined disposable host, twice per
graph, with an external loopback broker.  For every run it checks the receipt
against the canonical Python plan (planId/layoutHash/moved ids/positions), hashes
the disposable asset before/after, then exercises a legal legacy fallback and a
strict dirty negative.  Outputs a redacted JSON summary under Build/m39-readiness.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT / "src"), str(ROOT / "tools")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from ue_agent_kit.graph_layout_preview import build_preview  # noqa: E402
from ue_agent_kit.layout_broker.descriptor import build_project_path_hash  # noqa: E402
from ue_agent_kit.layout_broker.server import LayoutBrokerServer  # noqa: E402
from ue_launch import popen_minimized  # noqa: E402

ENGINE = Path(r"E:\EPICGAME\UE_5.6")
HOST = ROOT / "Build" / "m37-integration" / "P11AutomationHost"
PROJECT = HOST / "P11AutomationHost.uproject"
OUT = ROOT / "Build" / "m39-readiness"
EVIDENCE = OUT / "native"
DESCRIPTOR = HOST / "Saved" / "UEAgentKit" / "LayoutBroker.json"

ASSETS = [
    ("m35", "/Game/M35/BP_M35_Stress.BP_M35_Stress", HOST / "Content" / "M35" / "BP_M35_Stress.uasset"),
    ("p11", "/Game/UEAgentKitP11/BP_LayoutP11.BP_LayoutP11", HOST / "Content" / "UEAgentKitP11" / "BP_LayoutP11.uasset"),
    ("p10", "/Game/UEAgentKitP10/BP_LayoutP10.BP_LayoutP10", HOST / "Content" / "UEAgentKitP10" / "BP_LayoutP10.uasset"),
]
GRAPH = "EventGraph"
TEST = "UEAgentKit.Native.M3Menu"


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _positions_sha(plan: dict) -> str:
    positions = sorted(
        (
            {"nodeId": item.get("nodeId"), "x": item.get("x"), "y": item.get("y")}
            for item in plan.get("positions", [])
        ),
        key=lambda item: item["nodeId"] or "",
    )
    return "sha256:" + hashlib.sha256(
        json.dumps(positions, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _write_config(native: bool) -> None:
    (HOST / "Config").mkdir(parents=True, exist_ok=True)
    (HOST / "Config" / "DefaultEngine.ini").write_text(
        "[UEAgentKit]\n"
        f"NativeLayoutPreview={'on' if native else 'off'}\n"
        "LegacyLayoutPreviewFallback=true\n"
        "NativeToolRegistry=off\n",
        encoding="utf-8",
    )


def _run(graph_id: str, asset: str, scenario: str, capture: Path | None = None) -> tuple[int, dict, dict]:
    report = EVIDENCE / f"{graph_id}-{scenario}"
    report.mkdir(parents=True, exist_ok=True)
    command = [
        str(ENGINE / "Engine" / "Binaries" / "Win64" / "UnrealEditor-Cmd.exe"),
        str(PROJECT),
        "-unattended",
        "-nop4",
        "-nosplash",
        "-nullrhi",
        "-nopause",
        f"-UEAgentKitM3Scenario={scenario}",
        f"-UEAgentKitM3Asset={asset}",
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
    with log.open("wb") as handle:
        process = popen_minimized(command, cwd=str(HOST), stdout=handle, stderr=sys.stderr)
        exit_code = process.wait(timeout=240)
    automation = json.loads((report / "index.json").read_text(encoding="utf-8-sig"))
    evidence_path = HOST / "Evidence" / f"m32-menu-{graph_id}.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8")) if evidence_path.is_file() else {}
    return exit_code, automation, evidence


def _parity(receipt: dict, capture: Path) -> tuple[bool, dict]:
    if not capture.is_file():
        return False, {}
    payload = json.loads(capture.read_text(encoding="utf-8"))
    direct = build_preview(payload["preflight"], payload["envelope"])
    plan = direct.get("plan", {})
    ok = (
        receipt.get("planId") == plan.get("planId")
        and receipt.get("layoutHash") == plan.get("layoutHash")
        and sorted(receipt.get("movedNodeIds") or []) == sorted(plan.get("movedNodeIds") or [])
        and receipt.get("nodeCount") == len(plan.get("positions", []))
    )
    return ok, plan


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    _write_config(True)
    token = secrets.token_hex(32)
    if DESCRIPTOR.exists():
        DESCRIPTOR.unlink()
    server = LayoutBrokerServer(
        descriptor_path=str(DESCRIPTOR),
        project_name="P11AutomationHost",
        project_path_hash=build_project_path_hash(str(PROJECT)),
        expect_session_id=None,
        token=token,
    )
    server.start()
    started = datetime.now(timezone.utc).isoformat()

    results: list[dict] = []
    failures: list[str] = []
    fallback_ok = False
    fallback_receipt: dict = {}
    dirty_ok = False
    dirty_receipt: dict = {}
    try:
        for name, asset, asset_file in ASSETS:
            before = _sha(asset_file)
            runs = []
            for attempt in (1, 2):
                graph_id = f"m39-{name}-r{attempt}"
                capture = EVIDENCE / f"{graph_id}-capture.json"
                exit_code, automation, evidence = _run(graph_id, asset, "happy", capture)
                receipt = evidence.get("receipt") or {}
                parity, plan = _parity(receipt, capture)
                runs.append(
                    {
                        "attempt": attempt,
                        "exit": exit_code,
                        "automationFailed": automation.get("failed"),
                        "receipt": {
                            key: receipt.get(key)
                            for key in (
                                "ok", "planId", "layoutHash", "movedCount", "nodeCount",
                                "readOnly", "saved", "compiled", "packageDirtyBefore",
                                "packageDirtyAfter", "fallback", "previewPath", "workerMode",
                                "movedNodeIds",
                            )
                        },
                        "pythonParity": parity,
                        "pythonPlanId": plan.get("planId"),
                        "pythonLayoutHash": plan.get("layoutHash"),
                        "pythonPlanPositionsSha256": _positions_sha(plan),
                        "waitSeconds": evidence.get("waitSeconds"),
                        "invokeSeconds": evidence.get("invokeSeconds"),
                    }
                )
            after = _sha(asset_file)
            deterministic = (
                len(runs) == 2
                and runs[0]["receipt"]["planId"] == runs[1]["receipt"]["planId"]
                and runs[0]["receipt"]["layoutHash"] == runs[1]["receipt"]["layoutHash"]
                and sorted(runs[0]["receipt"].get("movedNodeIds") or [])
                == sorted(runs[1]["receipt"].get("movedNodeIds") or [])
                and runs[0]["pythonPlanPositionsSha256"] == runs[1]["pythonPlanPositionsSha256"]
            )
            asset_ok = before == after
            positive_ok = all(
                run["exit"] == 0
                and run["automationFailed"] == 0
                and run["receipt"]["ok"] is True
                and run["receipt"]["fallback"] is False
                and run["receipt"]["saved"] is False
                and run["receipt"]["compiled"] is False
                and run["receipt"]["packageDirtyAfter"] is False
                and run["pythonParity"] is True
                for run in runs
            )
            results.append(
                {
                    "graph": name,
                    "assetPath": asset,
                    "assetSha256Before": before,
                    "assetSha256After": after,
                    "assetUnchanged": asset_ok,
                    "deterministic": deterministic,
                    "positiveOk": positive_ok,
                    "runs": runs,
                }
            )
            if not (positive_ok and deterministic and asset_ok):
                failures.append(name)

        server.stop()
        if DESCRIPTOR.exists():
            DESCRIPTOR.unlink()
        exit_code, automation, evidence = _run("m39-fallback", ASSETS[1][1], "fallback")
        fallback_receipt = evidence.get("receipt") or {}
        fallback_ok = (
            exit_code == 0
            and automation.get("failed") == 0
            and fallback_receipt.get("ok") is True
            and fallback_receipt.get("fallback") is True
            and fallback_receipt.get("nativeErrorCode") == "native-preview-broker-descriptor-missing"
            and fallback_receipt.get("readOnly") is True
            and fallback_receipt.get("saved") is False
            and fallback_receipt.get("compiled") is False
        )
        if not fallback_ok:
            failures.append("fallback")

        exit_code, automation, evidence = _run("m39-dirty", ASSETS[0][1], "dirty")
        dirty_receipt = evidence.get("receipt") or {}
        dirty_ok = (
            exit_code == 0
            and automation.get("failed") == 0
            and dirty_receipt.get("ok") is False
            and dirty_receipt.get("fallback") is False
            and dirty_receipt.get("errorCode") == "native-preview-package-dirty"
            and dirty_receipt.get("saved") is False
            and dirty_receipt.get("compiled") is False
        )
        if not dirty_ok:
            failures.append("dirty-negative")
    finally:
        try:
            server.stop()
        except Exception:
            pass
        if DESCRIPTOR.exists():
            DESCRIPTOR.unlink()
        _write_config(False)

    summary = {
        "schemaVersion": "ueak.m39.three-graphs/1.0",
        "readOnly": True,
        "startedAtUtc": started,
        "completedAtUtc": datetime.now(timezone.utc).isoformat(),
        "graphs": results,
        "fallback": {
            "ok": bool(fallback_ok),
            "errorCode": fallback_receipt.get("nativeErrorCode"),
            "fallback": fallback_receipt.get("fallback"),
            "readOnly": fallback_receipt.get("readOnly"),
        },
        "dirtyNegative": {
            "ok": bool(dirty_ok),
            "errorCode": dirty_receipt.get("errorCode"),
            "fallback": dirty_receipt.get("fallback"),
        },
        "failures": failures,
        "ok": not failures,
    }
    (OUT / "three-graphs.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"ok": summary["ok"], "failures": failures, "graphs": [g["graph"] for g in results]}))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
