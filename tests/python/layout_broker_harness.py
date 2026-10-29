"""Offline harness for the M1 layout preview broker tests.

Not a test module (the runner discovers only ``test_*.py``).  It builds the
canonical preflight/envelope wrapper around the real exporter fixtures and
provides a running loopback broker plus raw-socket helpers for adversarial
cases.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
FIXTURES = ROOT / "tests" / "fixtures" / "graph_contracts"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ue_agent_kit.layout_broker import LayoutBrokerClient, LayoutBrokerServer  # noqa: E402
from ue_agent_kit.layout_broker import protocol  # noqa: E402
from ue_agent_kit.layout_broker.descriptor import build_project_path_hash  # noqa: E402

PROJECT_NAME = "M1FixtureHost"
SESSION_ID = "m1-session"
PROJECT_PATH = "C:/Projects/M1FixtureHost/M1FixtureHost.uproject"
PROJECT_PATH_HASH = build_project_path_hash(PROJECT_PATH)
PLUGIN_BUILD_HASH = "sha256:" + hashlib.sha256(b"m1-offline").hexdigest()
GRAPH_FINGERPRINT = "sha1:" + "2" * 40

FIXTURE_NAMES = (
    "bpctx_export_linear.json",
    "bpctx_export_p4_branch.json",
    "bpctx_export_m1_fork.json",
)


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def fixture_inputs(name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Wrap a canonical exporter document exactly as the old worker does."""

    document = copy.deepcopy(load_fixture(name))
    graph = document["graphs"][0]
    preflight = {
        "schemaVersion": "ueak.graphlayoutpreflight/1.0",
        "available": True,
        "complete": True,
        "nodesTruncated": False,
        "pieState": "stopped",
        "packageDirty": False,
        "editable": True,
        "sessionId": SESSION_ID,
        "pluginBuildHash": PLUGIN_BUILD_HASH,
        "assetPath": document["assetPath"],
        "graphGuid": graph["guid"],
        "graphName": graph["name"],
        "packageRevision": document["revision"]["value"],
        "graphFingerprint": GRAPH_FINGERPRINT,
        "nodeCount": len(graph["nodes"]),
    }
    envelope = {
        "available": True,
        "readOnly": True,
        "assetPath": document["assetPath"],
        "graphGuid": graph["guid"],
        "packageRevision": document["revision"]["value"],
        "graphFingerprint": GRAPH_FINGERPRINT,
        "packageDirty": False,
        "exportedAtUtc": "2026-10-23T00:00:00Z",
        "sessionId": SESSION_ID,
        "pluginBuildHash": PLUGIN_BUILD_HASH,
        "context": document,
    }
    return preflight, envelope


def start_broker(case: unittest.TestCase, **overrides: Any) -> tuple[LayoutBrokerServer, str]:
    directory = tempfile.mkdtemp(prefix="ueak-m1-broker-")
    case.addCleanup(shutil.rmtree, directory, ignore_errors=True)
    descriptor_path = os.path.join(directory, "LayoutBroker.json")
    options: dict[str, Any] = {
        "descriptor_path": descriptor_path,
        "project_name": PROJECT_NAME,
        "project_path_hash": PROJECT_PATH_HASH,
        "expect_session_id": SESSION_ID,
    }
    options.update(overrides)
    server = LayoutBrokerServer(**options)
    server.start()
    case.addCleanup(server.stop)
    return server, descriptor_path


def client_for(server: LayoutBrokerServer, **overrides: Any) -> LayoutBrokerClient:
    descriptor = server.descriptor
    assert descriptor is not None
    options: dict[str, Any] = {
        "project_name": PROJECT_NAME,
        "project_path_hash": PROJECT_PATH_HASH,
        "session_id": SESSION_ID,
    }
    options.update(overrides)
    return LayoutBrokerClient(descriptor, **options)


def request_for(
    server: LayoutBrokerServer,
    preflight: dict[str, Any],
    envelope: dict[str, Any],
    **overrides: Any,
) -> dict[str, Any]:
    return client_for(server).build_request(preflight, envelope, **overrides)


def raw_exchange(
    port: int,
    data: bytes,
    *,
    timeout: float = 2.0,
    half_close: bool = True,
) -> bytes:
    """Send raw bytes and read at most one response line, tolerating resets."""

    connection = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    buffer = bytearray()
    try:
        connection.settimeout(timeout)
        connection.sendall(data)
        if half_close:
            try:
                connection.shutdown(socket.SHUT_WR)
            except OSError:
                pass
        try:
            while b"\n" not in buffer:
                chunk = connection.recv(65536)
                if not chunk:
                    break
                buffer.extend(chunk)
                if len(buffer) > protocol.MAX_MESSAGE_BYTES + 4096:
                    break
        except (OSError, TimeoutError):
            pass
    finally:
        connection.close()
    return bytes(buffer)


def parse_line(raw: bytes) -> dict[str, Any] | None:
    line = raw.split(b"\n", 1)[0]
    if not line.strip():
        return None
    try:
        value = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def error_code(raw: bytes) -> str | None:
    payload = parse_line(raw)
    if isinstance(payload, dict) and payload.get("ok") is False:
        error = payload.get("error")
        if isinstance(error, dict):
            code = error.get("code")
            return code if isinstance(code, str) else None
    return None


def encoded(request: dict[str, Any]) -> bytes:
    return protocol.encode_message(request)


__all__ = [
    "FIXTURE_NAMES",
    "FIXTURES",
    "GRAPH_FINGERPRINT",
    "PLUGIN_BUILD_HASH",
    "PROJECT_NAME",
    "PROJECT_PATH",
    "PROJECT_PATH_HASH",
    "ROOT",
    "SESSION_ID",
    "client_for",
    "encoded",
    "error_code",
    "fixture_inputs",
    "load_fixture",
    "parse_line",
    "raw_exchange",
    "request_for",
    "start_broker",
]
