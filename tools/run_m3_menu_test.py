#!/usr/bin/env python3
"""Run one M3.0 isolated menu scenario against a disposable P11 host."""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from ue_launch import popen_minimized
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT / "src"), str(ROOT / "tests" / "python")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from ue_agent_kit.graph_layout_preview import build_preview  # noqa: E402
from ue_agent_kit.layout_broker import protocol  # noqa: E402
from ue_agent_kit.layout_broker.descriptor import (  # noqa: E402
    build_project_path_hash,
    write_descriptor_atomic,
)
from ue_agent_kit.layout_broker.server import LayoutBrokerServer  # noqa: E402

ENGINE = Path("E:/EPICGAME/UE_5.6")
HOST_ROOT = ROOT / "Build" / "m2-native" / "M3" / "P11AutomationHost"
PROJECT = HOST_ROOT / "P11AutomationHost.uproject"
EVIDENCE = ROOT / "Build" / "m2-native" / "Evidence" / "M3"
SAVED = HOST_ROOT / "Saved" / "UEAgentKit"
DESCRIPTOR = SAVED / "LayoutBroker.json"
CONFIG = HOST_ROOT / "Config" / "DefaultEngine.ini"
TEST_NAME = "UEAgentKit.Native.M3Menu"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class FakeBroker:
    """One-shot loopback broker for stale/forged/timeout menu scenarios."""

    def __init__(self, scenario: str) -> None:
        self.scenario = scenario
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.listener.settimeout(60.0)
        self.port = int(self.listener.getsockname()[1])
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            connection, _ = self.listener.accept()
            with connection:
                connection.settimeout(20.0)
                buffer = bytearray()
                while b"\n" not in buffer:
                    chunk = connection.recv(65536)
                    if not chunk:
                        return
                    buffer.extend(chunk)
                request = json.loads(bytes(buffer.split(b"\n", 1)[0]))
                if self.scenario in ("timeout", "timeout-strict"):
                    time.sleep(7.0)
                    return
                if self.scenario == "oversize-response":
                    connection.sendall(b'{"x":"' + b"a" * 1100000 + b'"}\n')
                    return
                if self.scenario in ("revision", "fingerprint"):
                    time.sleep(0.5)
                snapshot = request["snapshot"]
                options = request["options"]
                result = build_preview(
                    snapshot["preflight"],
                    snapshot["contextEnvelope"],
                    mode="full",
                    spacing_x=int(options["spacingX"]),
                    spacing_y=int(options["spacingY"]),
                    target_width=int(options["targetWidth"]),
                    seed=int(options["seed"]),
                )
                if self.scenario == "forged":
                    result = copy.deepcopy(result)
                    result["planId"] = "layout:" + "0" * 16
                connection.sendall(
                    protocol.encode_message(
                        protocol.success_response(request["requestId"], result, 1)
                    )
                )
        except Exception:
            traceback.print_exc()

    def close(self) -> None:
        try:
            self.listener.close()
        except Exception:
            traceback.print_exc()


def write_config(native: bool, fallback: bool) -> None:
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(
        "[UEAgentKit]\n"
        f"NativeLayoutPreview={'on' if native else 'off'}\n"
        f"LegacyLayoutPreviewFallback={'true' if fallback else 'false'}\n"
        "NativeToolRegistry=off\n",
        encoding="utf-8",
    )


def write_descriptor(port: int, token: str, *, harden: bool = True) -> None:
    SAVED.mkdir(parents=True, exist_ok=True)
    desc = {
        "schemaVersion": protocol.DESCRIPTOR_SCHEMA_VERSION,
        "address": "127.0.0.1",
        "port": port,
        "token": token,
        "projectName": "P11AutomationHost",
        "projectPathHash": build_project_path_hash(str(PROJECT)),
        "brokerVersion": "0.1.0-m1",
        "supportedContractVersions": [protocol.PROTOCOL_SCHEMA_VERSION],
        "algorithms": [dict(protocol.ALGORITHM)],
        "startedAtUtc": utc_now(),
        "processId": 4242,
        "maxConcurrentJobs": 1,
    }
    if harden:
        write_descriptor_atomic(DESCRIPTOR, desc)
    else:
        DESCRIPTOR.write_text(json.dumps(desc, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "scenario",
        choices=[
            "happy",
            "busy",
            "fallback",
            "strict",
            "token",
            "dacl",
            "dirty",
            "revision",
            "fingerprint",
            "forged",
            "timeout",
            "timeout-strict",
            "unavailable",
        ],
    )
    args = parser.parse_args()
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    fallback = args.scenario not in ("strict", "timeout-strict")
    write_config(True, fallback)
    if DESCRIPTOR.exists():
        DESCRIPTOR.unlink()

    server: LayoutBrokerServer | None = None
    fake: FakeBroker | None = None
    broker_token = "8f2a6c4e9d1b7035" * 4
    try:
        if args.scenario in ("happy", "busy", "token"):
            server = LayoutBrokerServer(
                descriptor_path=str(DESCRIPTOR),
                project_name="P11AutomationHost",
                project_path_hash=build_project_path_hash(str(PROJECT)),
                expect_session_id=None,
                token=broker_token,
            )
            server.start()
            if args.scenario == "token":
                write_descriptor(server.port, "0" * 64)
        elif args.scenario == "dacl":
            write_descriptor(50000, broker_token, harden=False)
        elif args.scenario == "unavailable":
            write_descriptor(50001, broker_token)
        elif args.scenario in ("revision", "fingerprint", "forged", "timeout", "timeout-strict"):
            fake = FakeBroker(args.scenario)
            write_descriptor(fake.port, broker_token)

        report = EVIDENCE / f"M3Menu-{args.scenario}"
        report.mkdir(parents=True, exist_ok=True)
        command = [
            str(ENGINE / "Engine" / "Binaries" / "Win64" / "UnrealEditor-Cmd.exe"),
            str(PROJECT),
            "-unattended",
            "-nop4",
            "-nosplash",
            "-nopause",
            f"-UEAgentKitM3Scenario={args.scenario}",
            f"-ExecCmds=Automation RunTests {TEST_NAME};Quit",
            "-TestExit=Automation Test Queue Empty",
            f"-ReportExportPath={report}",
        ]
        log_path = EVIDENCE / f"M3Menu-{args.scenario}.log"
        with log_path.open("wb") as log:
            process = popen_minimized(
                command, cwd=str(HOST_ROOT), stdout=log, stderr=subprocess.STDOUT
            )
            exit_code = process.wait(timeout=180)
    finally:
        if server is not None:
            server.stop()
        if fake is not None:
            fake.close()
        if DESCRIPTOR.exists():
            DESCRIPTOR.unlink()

    ui_log = HOST_ROOT / "Saved" / "Logs" / "P11AutomationHost.log"
    if ui_log.is_file():
        shutil.copyfile(ui_log, EVIDENCE / f"M3Menu-{args.scenario}-ue.log")
    automation_path = report / "index.json"
    automation = (
        json.loads(automation_path.read_text(encoding="utf-8-sig"))
        if automation_path.is_file()
        else {}
    )
    evidence_path = HOST_ROOT / "Evidence" / f"m3-menu-{args.scenario}.json"
    evidence = (
        json.loads(evidence_path.read_text(encoding="utf-8"))
        if evidence_path.is_file()
        else {}
    )
    receipt = evidence.get("receipt") or {}

    checks: list[dict[str, Any]] = [
        {
            "name": "editor-exit",
            "ok": exit_code == 0,
            "detail": str(exit_code),
        },
        {
            "name": "automation",
            "ok": automation.get("failed") == 0
            and any(t.get("state") == "Success" for t in automation.get("tests", [])),
            "detail": f"failed={automation.get('failed')}",
        },
        {
            "name": "receipt",
            "ok": bool(receipt),
            "detail": receipt.get("previewPath", ""),
        },
    ]
    if args.scenario in ("happy", "busy"):
        checks.append(
            {
                "name": "native-plan",
                "ok": receipt.get("ok") is True
                and receipt.get("fallback") is False
                and receipt.get("previewPath") == "native-broker"
                and str(receipt.get("planId", "")).startswith("layout:")
                and str(receipt.get("layoutHash", "")).startswith("sha256:"),
                "detail": receipt.get("planId", ""),
            }
        )
    elif args.scenario == "fallback":
        checks.append(
            {
                "name": "legacy-fallback",
                "ok": receipt.get("ok") is True
                and receipt.get("fallback") is True
                and receipt.get("previewPath") == "legacy-fallback"
                and receipt.get("nativeErrorCode") == "native-preview-broker-descriptor-missing",
                "detail": receipt.get("planId", ""),
            }
        )
    elif args.scenario == "strict":
        checks.append(
            {
                "name": "strict-no-plan",
                "ok": receipt.get("ok") is False
                and receipt.get("fallback") is False
                and receipt.get("errorCode") == "native-preview-broker-descriptor-missing",
                "detail": receipt.get("errorCode", ""),
            }
        )
    elif args.scenario == "token":
        checks.append(
            {
                "name": "token-fail-closed",
                "ok": receipt.get("ok") is False
                and receipt.get("fallback") is False
                and receipt.get("errorCode") == "native-preview-token-rejected",
                "detail": receipt.get("errorCode", ""),
            }
        )
    elif args.scenario == "dacl":
        checks.append(
            {
                "name": "dacl-fail-closed",
                "ok": receipt.get("ok") is False
                and receipt.get("fallback") is False
                and receipt.get("errorCode") == "native-preview-broker-descriptor-invalid",
                "detail": receipt.get("errorCode", ""),
            }
        )
    elif args.scenario == "dirty":
        checks.append(
            {
                "name": "dirty-fail-closed",
                "ok": receipt.get("ok") is False
                and receipt.get("fallback") is False
                and receipt.get("errorCode") == "native-preview-package-dirty",
                "detail": receipt.get("errorCode", ""),
            }
        )
    elif args.scenario == "revision":
        checks.append(
            {
                "name": "revision-fail-closed",
                "ok": receipt.get("ok") is False
                and receipt.get("fallback") is False
                and receipt.get("errorCode") == "native-preview-revision-conflict",
                "detail": receipt.get("errorCode", ""),
            }
        )
    elif args.scenario == "fingerprint":
        checks.append(
            {
                "name": "fingerprint-fail-closed",
                "ok": receipt.get("ok") is False
                and receipt.get("fallback") is False
                and receipt.get("errorCode") == "native-preview-fingerprint-conflict",
                "detail": receipt.get("errorCode", ""),
            }
        )
    elif args.scenario == "forged":
        checks.append(
            {
                "name": "forged-plan-fail-closed",
                "ok": receipt.get("ok") is False
                and receipt.get("fallback") is False
                and receipt.get("errorCode") == "native-preview-plan-id-mismatch",
                "detail": receipt.get("errorCode", ""),
            }
        )
    elif args.scenario == "timeout":
        checks.append(
            {
                "name": "timeout-fallback",
                "ok": receipt.get("ok") is True
                and receipt.get("fallback") is True
                and receipt.get("previewPath") == "legacy-fallback"
                and receipt.get("nativeErrorCode") == "native-preview-timeout",
                "detail": receipt.get("planId", ""),
            }
        )
    elif args.scenario == "timeout-strict":
        checks.append(
            {
                "name": "timeout-strict",
                "ok": receipt.get("ok") is False
                and receipt.get("fallback") is False
                and receipt.get("errorCode") == "native-preview-timeout",
                "detail": receipt.get("errorCode", ""),
            }
        )
    elif args.scenario == "unavailable":
        checks.append(
            {
                "name": "unavailable-fallback",
                "ok": receipt.get("ok") is True
                and receipt.get("fallback") is True
                and receipt.get("previewPath") == "legacy-fallback"
                and receipt.get("nativeErrorCode") == "native-preview-broker-unavailable",
                "detail": receipt.get("planId", ""),
            }
        )
    if args.scenario == "busy":
        checks.append(
            {
                "name": "busy-rejected",
                "ok": int(evidence.get("busyRejects", 0)) >= 1,
                "detail": str(evidence.get("busyRejects")),
            }
        )
    checks.append(
        {
            "name": "zero-write",
            "ok": receipt.get("saved") is False
            and receipt.get("compiled") is False
            and evidence.get("packageDirtyAfter") is False,
            "detail": "saved/compiled/packageDirtyAfter",
        }
    )
    checks.append(
        {
            "name": "nonblocking-return",
            "ok": evidence.get("nonBlockingReturn") is True,
            "detail": str(evidence.get("invokeSeconds")),
        }
    )
    # Real snapshot parity for native results, and legacy fallback plan shape.
    parity_ok = True
    if args.scenario in ("happy", "busy") and receipt:
        document = json.loads(
            (HOST_ROOT / "Evidence" / "p11-context.json").read_text(encoding="utf-8")
        )
        preflight = json.loads(
            (HOST_ROOT / "Evidence" / "p11-preflight.json").read_text(encoding="utf-8")
        )
        preflight.update(
            {
                "available": True,
                "complete": True,
                "nodesTruncated": False,
                "pieState": "stopped",
                "packageDirty": False,
                "editable": True,
                "sessionId": "m3-parity",
                "pluginBuildHash": "sha256:"
                + __import__("hashlib").sha256(b"m3-parity").hexdigest(),
                "graphName": document["graphs"][0]["name"],
            }
        )
        envelope = {
            "schemaVersion": "ueak.graphlayoutcontext/1.0",
            "available": True,
            "readOnly": True,
            "assetPath": preflight["assetPath"],
            "graphGuid": preflight["graphGuid"],
            "packageRevision": preflight["packageRevision"],
            "packageDirty": False,
            "graphFingerprint": preflight["graphFingerprint"],
            "exportedAtUtc": "2026-10-24T00:00:00Z",
            "context": document,
        }
        direct = build_preview(preflight, envelope)
        parity_ok = (
            receipt.get("planId") == direct["planId"]
            and receipt.get("layoutHash") == direct["layoutHash"]
        )
        checks.append(
            {
                "name": "python-parity",
                "ok": parity_ok,
                "detail": direct["planId"],
            }
        )
    report_json = {
        "schemaVersion": "ueak.m3.menu-run/1.0",
        "generatedAtUtc": utc_now(),
        "ok": all(check["ok"] for check in checks),
        "scenario": args.scenario,
        "command": command,
        "editorExitCode": exit_code,
        "automation": {
            "failed": automation.get("failed"),
            "succeeded": automation.get("succeeded"),
            "tests": [
                {"path": t.get("fullTestPath"), "state": t.get("state")}
                for t in automation.get("tests", [])
            ],
        },
        "checks": checks,
        "limitations": [
            "M3.0 is a disposable P11-host slice, not a production menu cutover.",
            "Legacy fallback executes the existing synchronous Python worker on the game thread.",
        ],
    }
    (EVIDENCE / f"m3-menu-run-{args.scenario}.json").write_text(
        json.dumps(report_json, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"ok": report_json["ok"], "scenario": args.scenario}))
    return 0 if report_json["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
