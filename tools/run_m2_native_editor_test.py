#!/usr/bin/env python3
"""M2.2 disposable-host UE automation harness.

Starts UnrealEditor-Cmd for the single native integration test, waits for the
test to export a real P9 snapshot + session, starts the Python M1 broker in this
harness process (outside the Editor), then validates the C++ result against the
same offline ``build_preview`` function on the exported real snapshot.

No Editor process starts the broker. Only one Editor is used.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from ue_launch import popen_minimized
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT / "src"), str(ROOT / "tests" / "python")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from ue_agent_kit.graph_layout_preview import build_preview  # noqa: E402
from ue_agent_kit.graph_contracts.layout import recompute_layout_hash  # noqa: E402
from ue_agent_kit.layout_broker import protocol  # noqa: E402
from ue_agent_kit.layout_broker.descriptor import build_project_path_hash  # noqa: E402
from ue_agent_kit.layout_broker.server import LayoutBrokerServer  # noqa: E402

HOST_ROOT = ROOT / "Build" / "m2-native"
HOST_PROJECT = HOST_ROOT / "M2NativeHost.uproject"
EVIDENCE = HOST_ROOT / "Evidence"
ENGINE = Path("E:/EPICGAME/UE_5.6")
EDITOR = ENGINE / "Engine" / "Binaries" / "Win64" / "UnrealEditor-Cmd.exe"
TEST_NAME = "UEAgentKit.Native.M2LayoutBrokerPositive"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def wait_for(path: Path, timeout: float, process: subprocess.Popen[Any]) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            return True
        if process.poll() is not None:
            return path.is_file()
        time.sleep(0.2)
    return False


def main() -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    for old in (
        EVIDENCE / "snapshot-ready.json",
        EVIDENCE / "snapshot-preflight.json",
        EVIDENCE / "snapshot-context.json",
        EVIDENCE / "native-result.json",
        EVIDENCE / "m2-native-evidence.json",
    ):
        if old.exists():
            old.unlink()
    descriptor_path = HOST_ROOT / "Saved" / "UEAgentKit" / "LayoutBroker.json"
    if descriptor_path.exists():
        descriptor_path.unlink()

    editor_log_path = EVIDENCE / "editor.log"
    broker_log_path = EVIDENCE / "broker.log"
    report_dir = EVIDENCE / "Automation"
    command = [
        str(EDITOR),
        str(HOST_PROJECT),
        "-unattended",
        "-nop4",
        "-nosplash",
        "-nullrhi",
        "-nopause",
        "-UEAgentKitAutomationChild",
        f"-ExecCmds=Automation RunTests {TEST_NAME};Quit",
        "-TestExit=Automation Test Queue Empty",
        f"-ReportExportPath={report_dir}",
    ]
    started = utc_now()
    with editor_log_path.open("wb") as editor_log:
        process = popen_minimized(
            command,
            cwd=str(HOST_ROOT),
            stdout=editor_log,
            stderr=subprocess.STDOUT,
        )
        ready_path = EVIDENCE / "snapshot-ready.json"
        if not wait_for(ready_path, 180.0, process):
            process.terminate()
            process.wait(timeout=30)
            (EVIDENCE / "m2-native-evidence.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": "ueak.m2.native-evidence/1.0",
                        "ok": False,
                        "blocked": "snapshot-ready marker did not appear",
                        "editorExitCode": process.returncode,
                        "command": command,
                        "generatedAtUtc": utc_now(),
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            print("BLOCKED: snapshot-ready marker did not appear", file=sys.stderr)
            return 2
        ready = read_json(ready_path)
        session_id = str(ready["sessionId"])
        project_name = str(ready["projectName"])
        project_hash = build_project_path_hash(str(HOST_PROJECT))

        logging_handle = broker_log_path.open("w", encoding="utf-8")
        import logging

        handler = logging.StreamHandler(logging_handle)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        broker_logger = logging.getLogger("ueak.layout_broker")
        broker_logger.addHandler(handler)
        broker_logger.setLevel(logging.DEBUG)

        server = LayoutBrokerServer(
            descriptor_path=str(descriptor_path),
            project_name=project_name,
            project_path_hash=project_hash,
            expect_session_id=session_id,
        )
        try:
            server.start()
            broker_status = server.status()
            try:
                editor_exit = process.wait(timeout=240)
            except subprocess.TimeoutExpired:
                process.terminate()
                editor_exit = process.wait(timeout=30)
                broker_status = server.status()
                server.stop()
                (EVIDENCE / "m2-native-evidence.json").write_text(
                    json.dumps(
                        {
                            "schemaVersion": "ueak.m2.native-evidence/1.0",
                            "ok": False,
                            "blocked": "Editor automation timed out",
                            "editorExitCode": editor_exit,
                            "command": command,
                            "generatedAtUtc": utc_now(),
                        },
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                print("BLOCKED: Editor automation timed out", file=sys.stderr)
                return 3
        finally:
            broker_status = server.status()
            server.stop()
            broker_logger.removeHandler(handler)
            handler.close()
            logging_handle.close()

    editor_log = HOST_ROOT / "Saved" / "Logs" / "M2NativeHost.log"
    if editor_log.is_file():
        shutil.copyfile(editor_log, EVIDENCE / "ue-editor.log")

    checks: list[dict[str, Any]] = []
    checks.append(
        {
            "name": "editor-exit",
            "ok": editor_exit == 0,
            "detail": f"editor exit={editor_exit}",
        }
    )
    automation_index = report_dir / "index.json"
    if automation_index.is_file():
        automation = read_json(automation_index)
        checks.append(
            {
                "name": "automation-report",
                "ok": automation.get("succeeded") == 1
                and automation.get("failed") == 0,
                "detail": json.dumps(
                    {
                        "succeeded": automation.get("succeeded"),
                        "failed": automation.get("failed"),
                        "notRun": automation.get("notRun"),
                    },
                    sort_keys=True,
                ),
            }
        )
    for name in ("snapshot-preflight.json", "snapshot-context.json", "native-result.json"):
        checks.append(
            {
                "name": f"evidence:{name}",
                "ok": (EVIDENCE / name).is_file(),
                "detail": str(EVIDENCE / name),
            }
        )
    if not all(check["ok"] for check in checks):
        (EVIDENCE / "m2-native-evidence.json").write_text(
            json.dumps(
                {
                    "schemaVersion": "ueak.m2.native-evidence/1.0",
                    "ok": False,
                    "checks": checks,
                    "editorExitCode": editor_exit,
                    "command": command,
                    "generatedAtUtc": utc_now(),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print("FAILED: missing Editor evidence", file=sys.stderr)
        return 4

    preflight = read_json(EVIDENCE / "snapshot-preflight.json")
    envelope = read_json(EVIDENCE / "snapshot-context.json")
    native = read_json(EVIDENCE / "native-result.json")
    options = {"spacing_x": 96, "spacing_y": 64, "target_width": 1800, "seed": 0}
    parity_error: str | None = None
    direct: dict[str, Any] | None = None
    try:
        direct = build_preview(preflight, envelope, **options)
    except Exception as exc:  # noqa: BLE001
        parity_error = f"{type(exc).__name__}: {exc}"
    native_result = native.get("result") if isinstance(native.get("result"), dict) else None
    checks.append(
        {
            "name": "native-ok",
            "ok": bool(native.get("ok")),
            "detail": str(native.get("code") or ""),
        }
    )
    checks.append(
        {
            "name": "native-plan-present",
            "ok": isinstance(native_result, dict),
            "detail": "result object present",
        }
    )
    if direct is not None and isinstance(native_result, dict):
        checks.append(
            {
                "name": "real-snapshot-parity",
                "ok": json.dumps(native_result, sort_keys=True)
                == json.dumps(direct, sort_keys=True),
                "detail": f"planId={direct.get('planId')}",
            }
        )
        checks.append(
            {
                "name": "canonical-layout-hash",
                "ok": native_result.get("layoutHash")
                == recompute_layout_hash(native_result.get("plan", {})),
                "detail": str(native_result.get("layoutHash")),
            }
        )
    else:
        checks.append(
            {
                "name": "real-snapshot-parity",
                "ok": False,
                "detail": parity_error or "missing native result",
            }
        )
    receipt = native.get("receipt") if isinstance(native.get("receipt"), dict) else {}
    receipt_text = json.dumps(receipt, sort_keys=True)
    checks.append(
        {
            "name": "receipt-no-token",
            "ok": bool(receipt) and server.token not in receipt_text,
            "detail": "token absent",
        }
    )
    checks.append(
        {
            "name": "receipt-read-only",
            "ok": receipt.get("readOnly") is True
            and receipt.get("saved") is False
            and receipt.get("compiled") is False,
            "detail": "readOnly/saved/compiled",
        }
    )
    checks.append(
        {
            "name": "broker-single-job",
            "ok": broker_status.get("completed") == 1
            and broker_status.get("accepted") == 1,
            "detail": json.dumps(
                {
                    "accepted": broker_status.get("accepted"),
                    "completed": broker_status.get("completed"),
                    "rejected": broker_status.get("rejected"),
                },
                sort_keys=True,
            ),
        }
    )
    report = {
        "schemaVersion": "ueak.m2.native-evidence/1.0",
        "generatedAtUtc": utc_now(),
        "startedAtUtc": started,
        "ok": all(check["ok"] for check in checks),
        "runtimeClaim": (
            "Real UE 5.6.1 UnrealEditor-Cmd automation + real P9 snapshot + external "
            "Python M1 broker; no Computer Use, no Editor UI input."
        ),
        "engine": str(ENGINE),
        "hostProject": str(HOST_PROJECT),
        "test": TEST_NAME,
        "command": command,
        "editorExitCode": editor_exit,
        "broker": broker_status,
        "checks": checks,
        "limitations": [
            "Only the one positive native path is exercised in this slice.",
            "The test uses a test-only snapshot injection seam after a real P9 capture; "
            "production snapshot wiring/menu is not enabled.",
            "The broker is started by the Python harness; no Editor CreateProc path exists.",
            "P10/P11 menu regression was not rerun against changed menu code (none changed).",
        ],
    }
    (EVIDENCE / "m2-native-evidence.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"ok": report["ok"], "evidence": str(EVIDENCE / "m2-native-evidence.json")}))
    return 0 if report["ok"] else 5


if __name__ == "__main__":
    raise SystemExit(main())
