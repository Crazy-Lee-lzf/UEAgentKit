#!/usr/bin/env python3
"""Explicit M2.1 offline planId-hardening evidence probe."""

from __future__ import annotations

import copy
import json
import platform
import socket
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT / "src"), str(ROOT / "tests" / "python")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import layout_broker_harness as harness  # noqa: E402
import layout_broker_m2_fixtures as m2  # noqa: E402

from ue_agent_kit.graph_layout_preview import build_preview  # noqa: E402
from ue_agent_kit.graph_contracts.layout import recompute_layout_hash  # noqa: E402
from ue_agent_kit.layout_broker import BrokerClientError, LayoutBrokerClient  # noqa: E402
from ue_agent_kit.layout_broker import descriptor as descriptor_module  # noqa: E402
from ue_agent_kit.layout_broker import protocol  # noqa: E402
from ue_agent_kit.layout_broker.planid import plan_id_from_result  # noqa: E402

TOKEN = "6b8f2d4c1e9a7305" * 4


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def forged(result: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(result)
    value["planId"] = "layout:" + "0" * 16
    return value


def tampered(result: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(result)
    position = value["plan"]["positions"][0]
    node_id = position["nodeId"]
    position["x"] += 10
    for item in value["positions"]:
        if item["nodeId"] == node_id:
            item["afterX"] += 10
            item["deltaX"] += 10
            break
    digest = recompute_layout_hash(value["plan"])
    value["layoutHash"] = digest
    value["plan"]["layoutHash"] = digest
    return value


class FakeBroker:
    def __init__(self, factory: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        self._factory = factory
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(1)
        self._listener.settimeout(5.0)
        self.port = int(self._listener.getsockname()[1])
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            connection, _ = self._listener.accept()
            with connection:
                connection.settimeout(5.0)
                buffer = bytearray()
                while b"\n" not in buffer:
                    chunk = connection.recv(65536)
                    if not chunk:
                        return
                    buffer.extend(chunk)
                request = json.loads(bytes(buffer.split(b"\n", 1)[0]))
                connection.sendall(protocol.encode_message(self._factory(request)))
        except OSError:
            pass

    def close(self) -> None:
        try:
            self._listener.close()
        except OSError:
            pass


def main() -> int:
    evidence_dir = ROOT / "Build" / "layout-broker" / "Evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    checks: list[dict[str, Any]] = []
    golden_cases: list[dict[str, Any]] = []
    main_case = _CaseShim()
    server, _ = harness.start_broker(
        main_case,
        project_name=m2.PROJECT_NAME,
        project_path_hash=m2.PROJECT_PATH_HASH,
        expect_session_id=m2.SESSION_ID,
    )
    try:
        client = harness.client_for(
            server,
            project_name=m2.PROJECT_NAME,
            project_path_hash=m2.PROJECT_PATH_HASH,
            session_id=m2.SESSION_ID,
        )
        golden = m2.load_golden_cases()
        for case in golden["cases"]:
            preflight, envelope, _ = m2.actual_inputs(case)
            direct = build_preview(preflight, envelope)
            through = client.preview(preflight, envelope)
            exact = json.dumps(through, sort_keys=True) == json.dumps(direct, sort_keys=True)
            expected_id = plan_id_from_result(
                direct,
                spacing_x=golden["options"]["spacingX"],
                spacing_y=golden["options"]["spacingY"],
                target_width=golden["options"]["targetWidth"],
                seed=golden["options"]["seed"],
            )
            checks.append(
                {
                    "name": f"golden:{case['id']}",
                    "ok": exact and through["planId"] == case["expected"]["planId"] == expected_id,
                    "detail": f"{case['expected']['planId']} exact={exact}",
                }
            )
            golden_cases.append({"id": case["id"], "planId": direct["planId"], "exact": exact})
    finally:
        server.stop()
        main_case.cleanup()

    preflight, envelope, _ = m2.actual_inputs(m2.case_by_id("linear"))
    direct = build_preview(preflight, envelope)

    def forged_preview(p, e, **options):
        return forged(build_preview(p, e, **options))

    forged_case = _CaseShim()
    forged_server, _ = harness.start_broker(
        forged_case,
        project_name=m2.PROJECT_NAME,
        project_path_hash=m2.PROJECT_PATH_HASH,
        expect_session_id=m2.SESSION_ID,
        preview_fn=forged_preview,
    )
    try:
        forged_client = harness.client_for(
            forged_server,
            project_name=m2.PROJECT_NAME,
            project_path_hash=m2.PROJECT_PATH_HASH,
            session_id=m2.SESSION_ID,
        )
        server_rejected = False
        try:
            forged_client.preview(preflight, envelope)
        except BrokerClientError as exc:
            server_rejected = exc.code == protocol.ERROR_WORKER_FAILED
        checks.append(
            {
                "name": "actual-server-forged-plan-id",
                "ok": server_rejected
                and forged_server.status()["lastErrorCode"]
                == protocol.ERROR_PLAN_ID_MISMATCH,
                "detail": "self-check rejects before any success response",
            }
        )
    finally:
        forged_server.stop()
        forged_case.cleanup()

    def tampered_preview(p, e, **options):
        return tampered(build_preview(p, e, **options))

    tampered_case = _CaseShim()
    tampered_server, _ = harness.start_broker(
        tampered_case,
        project_name=m2.PROJECT_NAME,
        project_path_hash=m2.PROJECT_PATH_HASH,
        expect_session_id=m2.SESSION_ID,
        preview_fn=tampered_preview,
    )
    try:
        tampered_client = harness.client_for(
            tampered_server,
            project_name=m2.PROJECT_NAME,
            project_path_hash=m2.PROJECT_PATH_HASH,
            session_id=m2.SESSION_ID,
        )
        rejected_after_rehash = False
        try:
            tampered_client.preview(preflight, envelope)
        except BrokerClientError as exc:
            rejected_after_rehash = exc.code == protocol.ERROR_WORKER_FAILED
        checks.append(
            {
                "name": "actual-server-forged-plan-id-after-rehash",
                "ok": rejected_after_rehash
                and tampered_server.status()["lastErrorCode"]
                == protocol.ERROR_PLAN_ID_MISMATCH,
                "detail": "positions and layoutHash recomputed; planId still rejected",
            }
        )
    finally:
        tampered_server.stop()
        tampered_case.cleanup()

    fake = FakeBroker(
        lambda request: protocol.success_response(
            request["requestId"], forged(direct), 5
        )
    )
    fake_descriptor = descriptor_module.build_descriptor(
        port=fake.port,
        token=TOKEN,
        project_name=m2.PROJECT_NAME,
        project_path_hash=m2.PROJECT_PATH_HASH,
        process_id=99,
    )
    fake_client = LayoutBrokerClient(
        fake_descriptor,
        project_name=m2.PROJECT_NAME,
        project_path_hash=m2.PROJECT_PATH_HASH,
        session_id=m2.SESSION_ID,
    )
    client_rejected = False
    try:
        fake_client.preview(preflight, envelope)
    except BrokerClientError as exc:
        client_rejected = exc.code == protocol.ERROR_PLAN_ID_MISMATCH
    finally:
        fake.close()
    checks.append(
        {
            "name": "actual-client-forged-plan-id",
            "ok": client_rejected,
            "detail": "malicious success payload rejected locally with plan-id-mismatch",
        }
    )

    report = {
        "schemaVersion": "ueak.layoutbroker-m2_1-probe/1.0",
        "generatedAtUtc": utc_now(),
        "ok": all(check["ok"] for check in checks),
        "evidenceLevel": "OFFLINE-PROVEN",
        "runtimeClaim": "No UE Editor, UE build, C++, or UI automation was used.",
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "host": platform.node(),
        },
        "algorithm": dict(protocol.ALGORITHM),
        "golden": golden_cases,
        "checks": checks,
        "limitations": [
            "Offline Python only; C++ client and Editor socket/lifecycle are not exercised.",
            "M1 remains full-only; v1.1 selection/incremental is still a draft schema.",
            "The broker self-check maps the specific planId failure to worker-failed on the wire; "
            "the specific code is visible in server.status()/logs.",
        ],
    }
    output = evidence_dir / "m2_1-planid-probe.json"
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "evidence": str(output)}))
    return 0 if report["ok"] else 1


class _CaseShim:
    """Minimal unittest.TestCase-ish addCleanup holder for harness reuse."""

    def __init__(self) -> None:
        self._cleanups: list[Callable[[], None]] = []

    def addCleanup(self, function: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        self._cleanups.append(lambda: function(*args, **kwargs))

    def cleanup(self) -> None:
        while self._cleanups:
            self._cleanups.pop()()


if __name__ == "__main__":
    raise SystemExit(main())
