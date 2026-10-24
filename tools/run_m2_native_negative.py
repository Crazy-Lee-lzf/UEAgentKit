#!/usr/bin/env python3
"""Run one M2.2 native negative scenario in the disposable UE host.

The Python harness owns the fake/real broker and descriptor; the Editor never
starts a process. Scenarios: spoof, oversize, timeout, token-error.
"""

from __future__ import annotations

import argparse
import copy
import json
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
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

HOST_ROOT = ROOT / "Build" / "m2-native"
HOST_PROJECT = HOST_ROOT / "M2NativeHost.uproject"
EVIDENCE = HOST_ROOT / "Evidence"
ENGINE = Path("E:/EPICGAME/UE_5.6")
EDITOR = ENGINE / "Engine" / "Binaries" / "Win64" / "UnrealEditor-Cmd.exe"
TEST_NAME = "UEAgentKit.Native.M2LayoutBrokerNegative"
STALE_TEST_NAME = "UEAgentKit.Native.M2LayoutBrokerStale"
DESCRIPTOR = HOST_ROOT / "Saved" / "UEAgentKit" / "LayoutBroker.json"


class FakeBroker:
    def __init__(self, scenario: str) -> None:
        self.scenario = scenario
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.listener.settimeout(20.0)
        self.port = int(self.listener.getsockname()[1])
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _response(self, request: dict[str, Any]) -> bytes:
        request_id = request.get("requestId")
        if self.scenario == "token-error":
            return protocol.encode_message(
                protocol.error_response(
                    request_id, protocol.ERROR_TOKEN_REJECTED, "The session token was rejected."
                )
            )
        snapshot = request.get("snapshot") or {}
        preflight = snapshot.get("preflight") or {}
        envelope = snapshot.get("contextEnvelope") or {}
        options = request.get("options") or {}
        result = build_preview(
            preflight,
            envelope,
            mode="full",
            spacing_x=int(options.get("spacingX", 96)),
            spacing_y=int(options.get("spacingY", 64)),
            target_width=int(options.get("targetWidth", 1800)),
            seed=int(options.get("seed", 0)),
        )
        if self.scenario == "spoof":
            result = copy.deepcopy(result)
            result["planId"] = "layout:" + "0" * 16
        return protocol.encode_message(
            protocol.success_response(request_id, result, 1)
        )

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
                if self.scenario == "timeout":
                    time.sleep(7.0)
                    return
                if self.scenario == "oversize":
                    connection.sendall(b"x" * (protocol.MAX_MESSAGE_BYTES + 1024))
                    return
                connection.sendall(self._response(request))
        except OSError:
            pass

    def close(self) -> None:
        try:
            self.listener.close()
        except OSError:
            pass


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "scenario",
        choices=["spoof", "oversize", "timeout", "token-error", "wrong-token", "revision", "fingerprint"],
    )
    parser.add_argument("--no-nullrhi", action="store_true")
    args = parser.parse_args()
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    DESCRIPTOR.parent.mkdir(parents=True, exist_ok=True)
    if DESCRIPTOR.exists():
        DESCRIPTOR.unlink()
    stale_result = EVIDENCE / "native-negative-result.json"
    if stale_result.exists():
        stale_result.unlink()
    fake: FakeBroker | None = None
    real_server: LayoutBrokerServer | None = None
    token = "7c1e5a93b8d24f60" * 4
    try:
        if args.scenario == "wrong-token":
            real_server = LayoutBrokerServer(
                descriptor_path=str(DESCRIPTOR),
                project_name=HOST_PROJECT.stem,
                project_path_hash=build_project_path_hash(str(HOST_PROJECT)),
                token="9d2f6a41c8e37b50" * 4,
            )
            real_server.start()
            wrong_descriptor = dict(real_server.descriptor or {})
            wrong_descriptor["token"] = token
            write_descriptor_atomic(DESCRIPTOR, wrong_descriptor)
        else:
            fake = FakeBroker(args.scenario)
            write_descriptor_atomic(
                DESCRIPTOR,
                {
                    "schemaVersion": protocol.DESCRIPTOR_SCHEMA_VERSION,
                    "address": "127.0.0.1",
                    "port": fake.port,
                    "token": token,
                    "projectName": HOST_PROJECT.stem,
                    "projectPathHash": build_project_path_hash(str(HOST_PROJECT)),
                    "brokerVersion": "0.1.0-m1",
                    "supportedContractVersions": [protocol.PROTOCOL_SCHEMA_VERSION],
                    "algorithms": [dict(protocol.ALGORITHM)],
                    "startedAtUtc": utc_now(),
                    "processId": 4242,
                    "maxConcurrentJobs": 1,
                },
            )
        log_path = EVIDENCE / f"negative-{args.scenario}-editor.log"
        report_dir = EVIDENCE / f"Negative-{args.scenario}"
        test_name = STALE_TEST_NAME if args.scenario in ("revision", "fingerprint") else TEST_NAME
        command = [
            str(EDITOR),
            str(HOST_PROJECT),
            "-unattended",
            "-nop4",
            "-nosplash",
        ]
        if not args.no_nullrhi and args.scenario != "timeout":
            command.append("-nullrhi")
        command += [
            "-nopause",
            "-UEAgentKitAutomationChild",
            f"-UEAgentKitM2Scenario={args.scenario}",
            f"-ExecCmds=Automation RunTests {test_name};Quit",
            "-TestExit=Automation Test Queue Empty",
            f"-ReportExportPath={report_dir}",
        ]
        with log_path.open("wb") as log:
            process = subprocess.Popen(
                command, cwd=str(HOST_ROOT), stdout=log, stderr=subprocess.STDOUT
            )
            try:
                exit_code = process.wait(timeout=120)
            except subprocess.TimeoutExpired:
                process.terminate()
                exit_code = process.wait(timeout=30)
    finally:
        if fake is not None:
            fake.close()
        if real_server is not None:
            real_server.stop()
        try:
            DESCRIPTOR.unlink()
        except OSError:
            pass

    result_name = "native-stale-result.json" if args.scenario in ("revision", "fingerprint") else "native-negative-result.json"
    result_path = EVIDENCE / result_name
    result = json.loads(result_path.read_text(encoding="utf-8-sig")) if result_path.is_file() else {}
    index_path = report_dir / "index.json"
    automation = json.loads(index_path.read_text(encoding="utf-8-sig")) if index_path.is_file() else {}
    expected = {
        "spoof": "native-preview-plan-id-mismatch",
        "oversize": "native-preview-response-too-large",
        "timeout": "native-preview-timeout",
        "token-error": "native-preview-token-rejected",
        "wrong-token": "native-preview-token-rejected",
        "revision": "native-preview-revision-conflict",
        "fingerprint": "native-preview-fingerprint-conflict",
    }[args.scenario]
    ok = (
        exit_code == 0
        and result.get("scenario") == args.scenario
        and result.get("code") == expected
        and result.get("ok") is True
        and automation.get("succeeded") == 1
        and automation.get("failed") == 0
    )
    report = {
        "schemaVersion": "ueak.m2.native-negative-evidence/1.0",
        "generatedAtUtc": utc_now(),
        "ok": ok,
        "scenario": args.scenario,
        "expectedCode": expected,
        "editorExitCode": exit_code,
        "result": result,
        "automation": {
            "succeeded": automation.get("succeeded"),
            "failed": automation.get("failed"),
        },
        "command": command,
        "limitations": [
            "token-error uses a fake broker error response, not a real token comparison.",
            "revision/fingerprint drift is not part of this scenario.",
            "No Editor CreateProc; the harness owns the fake broker.",
        ],
    }
    (EVIDENCE / f"m2-native-negative-{args.scenario}.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"ok": ok, "scenario": args.scenario, "evidence": str(EVIDENCE)}))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
