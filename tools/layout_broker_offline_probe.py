#!/usr/bin/env python3
"""Explicit offline evidence run for the M1 layout preview broker.

Starts one broker on an ephemeral loopback port, proves exact parity with the
existing ``build_preview`` function on the repository fixtures, exercises a
small set of adversarial protocol cases, and writes a JSON evidence file under
``Build/layout-broker/Evidence`` (ignored by git).

This is a harness, not an autostart path: nothing else invokes it, and the
broker it starts exits with the script.
"""

from __future__ import annotations

import argparse
import json
import platform
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TESTS = ROOT / "tests" / "python"
for entry in (str(SRC), str(TESTS)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from ue_agent_kit.graph_layout_preview import build_preview  # noqa: E402
from ue_agent_kit.layout_broker import LayoutBrokerClient, LayoutBrokerServer  # noqa: E402
from ue_agent_kit.layout_broker import protocol  # noqa: E402
from ue_agent_kit.layout_broker.descriptor import build_project_path_hash  # noqa: E402
from layout_broker_harness import (  # noqa: E402
    FIXTURE_NAMES,
    PROJECT_NAME,
    SESSION_ID,
    error_code,
    fixture_inputs,
    parse_line,
    raw_exchange,
)

PROJECT_PATH = "C:/Projects/M1FixtureHost/M1FixtureHost.uproject"


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )


def _check(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail}


def _raw_error(port: int, data: bytes) -> str | None:
    return error_code(raw_exchange(port, data, timeout=4.0))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        default=ROOT / "Build" / "layout-broker" / "Evidence",
    )
    args = parser.parse_args(argv)
    evidence_dir: Path = args.evidence_dir
    evidence_dir.mkdir(parents=True, exist_ok=True)
    descriptor_path = evidence_dir / "m1-probe-descriptor.json"

    project_hash = build_project_path_hash(PROJECT_PATH)
    server = LayoutBrokerServer(
        descriptor_path=str(descriptor_path),
        project_name=PROJECT_NAME,
        project_path_hash=project_hash,
        expect_session_id=SESSION_ID,
    )
    started_at = _utc_now()
    checks: list[dict[str, Any]] = []
    parity: list[dict[str, Any]] = []
    descriptor_security: dict[str, str] | None = None
    port = 0
    try:
        descriptor = server.start()
        port = int(descriptor["port"])
        descriptor_security = server.status()["descriptorSecurity"]
        checks.append(_check("loopback_address", descriptor["address"] == "127.0.0.1", "descriptor address"))
        checks.append(
            _check(
                "descriptor_acl_proven",
                bool(descriptor_security and descriptor_security["state"] == "PROVEN"),
                json.dumps(descriptor_security, sort_keys=True),
            )
        )
        checks.append(
            _check(
                "token_absent_from_status",
                server.token not in json.dumps(server.status(), ensure_ascii=False),
                "status excludes the session token",
            )
        )

        client = LayoutBrokerClient.from_descriptor_file(
            str(descriptor_path),
            project_name=PROJECT_NAME,
            project_path_hash=project_hash,
            session_id=SESSION_ID,
        )
        for name in FIXTURE_NAMES:
            preflight, envelope = fixture_inputs(name)
            direct = build_preview(preflight, envelope)
            via_broker = client.preview(preflight, envelope)
            exact = json.dumps(direct, sort_keys=True) == json.dumps(via_broker, sort_keys=True)
            parity.append(
                {
                    "fixture": name,
                    "nodeCount": direct["nodeCount"],
                    "movedCount": direct["movedCount"],
                    "planId": direct["planId"],
                    "layoutHash": direct["layoutHash"],
                    "exactResultEqual": exact,
                }
            )
            checks.append(_check(f"parity:{name}", exact, "broker result equals build_preview"))

        preflight, envelope = fixture_inputs(FIXTURE_NAMES[0])
        request = client.build_request(preflight, envelope)
        bad_token_request = dict(request)
        bad_token_request["token"] = "0" * 64
        checks.append(
            _check(
                "bad_token_rejected",
                _raw_error(port, protocol.encode_message(bad_token_request))
                == protocol.ERROR_TOKEN_REJECTED,
                "token rejected",
            )
        )
        wrong_project = dict(request)
        wrong_project["projectName"] = "AnotherProject"
        checks.append(
            _check(
                "wrong_project_rejected",
                _raw_error(port, protocol.encode_message(wrong_project))
                == protocol.ERROR_PROJECT_MISMATCH,
                "project rejected",
            )
        )
        duplicate = client.build_request(preflight, envelope)
        client.send(duplicate)
        checks.append(
            _check(
                "duplicate_request_rejected",
                _raw_error(port, protocol.encode_message(duplicate))
                == protocol.ERROR_DUPLICATE_REQUEST,
                "replay rejected",
            )
        )
        checks.append(
            _check(
                "oversized_request_rejected",
                _raw_error(port, b"x" * (protocol.MAX_MESSAGE_BYTES + 32))
                == protocol.ERROR_REQUEST_TOO_LARGE,
                "1 MiB bound enforced",
            )
        )
        malformed = raw_exchange(port, b"{not-json}\n")
        malformed_payload = parse_line(malformed) or {}
        malformed_error = (malformed_payload.get("error") or {}).get("code")
        checks.append(
            _check(
                "malformed_json_rejected",
                malformed_error == protocol.ERROR_REQUEST_INVALID,
                "malformed request rejected",
            )
        )
    finally:
        server.stop()

    checks.append(
        _check("descriptor_removed_on_stop", not descriptor_path.exists(), "descriptor cleanup")
    )
    checks.append(
        _check(
            "listener_closed_on_stop",
            port > 0 and not _port_open(port),
            "loopback port no longer accepts",
        )
    )

    report = {
        "schemaVersion": "ueak.m1.offline-probe/1.0",
        "generatedAtUtc": _utc_now(),
        "startedAtUtc": started_at,
        "ok": all(item["ok"] for item in checks),
        "evidenceLevel": "OFFLINE-PROVEN",
        "runtimeClaim": "No Unreal Editor, no production connection, no UE build was used.",
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "host": platform.node(),
            "repositoryRoot": str(ROOT),
        },
        "broker": {
            "brokerVersion": protocol.BROKER_VERSION,
            "protocol": protocol.PROTOCOL_SCHEMA_VERSION,
            "algorithm": dict(protocol.ALGORITHM),
            "projectName": PROJECT_NAME,
            "projectPathHash": project_hash,
            "descriptorSecurity": descriptor_security,
            "fixtures": list(FIXTURE_NAMES),
        },
        "parity": parity,
        "checks": checks,
        "limitations": [
            "Offline Python proof only; Editor client, menu integration and P11 regression were not run.",
            "M1 protocol supports mode=full only; selection/incremental stay on the legacy path.",
            "Request-id dedup is bounded to 1024 ids per broker process.",
            "A timed-out pure job cannot be interrupted; the slot stays busy until it returns.",
            "Descriptor directory ACL inherits from the host; only the descriptor file is hardened/verified.",
        ],
    }
    output = evidence_dir / "m1-offline-probe.json"
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "evidence": str(output)}, ensure_ascii=False))
    return 0 if report["ok"] else 1


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
