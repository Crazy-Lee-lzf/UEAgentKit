from __future__ import annotations

import json
import socket
import sys
import threading
import unittest
import uuid
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import layout_broker_harness as harness  # noqa: E402

from ue_agent_kit.graph_layout_preview import build_preview  # noqa: E402
from ue_agent_kit.layout_broker import BrokerClientError, LayoutBrokerClient  # noqa: E402
from ue_agent_kit.layout_broker import descriptor as descriptor_module  # noqa: E402
from ue_agent_kit.layout_broker import protocol  # noqa: E402

TOKEN = "7b1e4d9a2c6f8053" * 4

ResponseFactory = Callable[[dict[str, Any]], bytes | None]


class FakeBroker:
    """One-shot loopback server that returns a caller-chosen hostile response."""

    def __init__(self, factory: ResponseFactory) -> None:
        self._factory = factory
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(1)
        self._listener.settimeout(5.0)
        self.port = int(self._listener.getsockname()[1])
        self.request: dict[str, Any] | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

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
                line = bytes(buffer.split(b"\n", 1)[0])
                self.request = json.loads(line.decode("utf-8"))
                response = self._factory(self.request)
                if response is not None:
                    connection.sendall(response)
        except OSError:
            pass

    def close(self) -> None:
        try:
            self._listener.close()
        except OSError:
            pass


class ClientAdversarialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.preflight, self.envelope = harness.fixture_inputs("bpctx_export_linear.json")
        self.result = build_preview(self.preflight, self.envelope)

    def client_for(self, fake: FakeBroker) -> LayoutBrokerClient:
        descriptor = descriptor_module.build_descriptor(
            port=fake.port,
            token=TOKEN,
            project_name=harness.PROJECT_NAME,
            project_path_hash=harness.PROJECT_PATH_HASH,
            process_id=4321,
        )
        self.addCleanup(fake.close)
        return LayoutBrokerClient(
            descriptor,
            project_name=harness.PROJECT_NAME,
            project_path_hash=harness.PROJECT_PATH_HASH,
            session_id=harness.SESSION_ID,
        )

    def test_valid_fake_response_is_accepted(self) -> None:
        def factory(request: dict[str, Any]) -> bytes:
            return protocol.encode_message(
                protocol.success_response(request["requestId"], self.result, 7)
            )

        fake = FakeBroker(factory)
        client = self.client_for(fake)
        accepted = client.preview(self.preflight, self.envelope)
        self.assertEqual(accepted["planId"], self.result["planId"])

    def test_wrong_request_id_is_rejected(self) -> None:
        def factory(request: dict[str, Any]) -> bytes:
            return protocol.encode_message(
                protocol.success_response(str(uuid.uuid4()), self.result, 7)
            )

        with self.assertRaises(BrokerClientError) as caught:
            fake = FakeBroker(factory)
            client = self.client_for(fake)
            client.preview(self.preflight, self.envelope)
        self.assertEqual(caught.exception.code, protocol.ERROR_RESPONSE_INVALID)

    def test_extra_response_field_is_rejected(self) -> None:
        def factory(request: dict[str, Any]) -> bytes:
            payload = protocol.success_response(request["requestId"], self.result, 7)
            payload["debugPlan"] = {"x": 1}
            return protocol.encode_message(payload)

        with self.assertRaises(BrokerClientError) as caught:
            fake = FakeBroker(factory)
            self.client_for(fake).preview(self.preflight, self.envelope)
        self.assertEqual(caught.exception.code, protocol.ERROR_RESPONSE_INVALID)

    def test_token_echo_is_rejected(self) -> None:
        def factory(request: dict[str, Any]) -> bytes:
            result = json.loads(json.dumps(self.result))
            result["graphName"] = TOKEN
            return protocol.encode_message(
                protocol.success_response(request["requestId"], result, 7)
            )

        with self.assertRaises(BrokerClientError) as caught:
            fake = FakeBroker(factory)
            self.client_for(fake).preview(self.preflight, self.envelope)
        self.assertEqual(caught.exception.code, protocol.ERROR_RESPONSE_INVALID)
        self.assertNotIn(TOKEN, caught.exception.message)

    def test_oversized_response_is_rejected(self) -> None:
        def factory(request: dict[str, Any]) -> bytes:
            del request
            return b"x" * (protocol.MAX_MESSAGE_BYTES + 128)

        with self.assertRaises(BrokerClientError) as caught:
            fake = FakeBroker(factory)
            self.client_for(fake).preview(self.preflight, self.envelope)
        self.assertEqual(caught.exception.code, protocol.ERROR_RESPONSE_TOO_LARGE)

    def test_truncated_response_is_rejected(self) -> None:
        def factory(request: dict[str, Any]) -> bytes:
            del request
            return b'{"schemaVersion":'

        with self.assertRaises(BrokerClientError) as caught:
            fake = FakeBroker(factory)
            self.client_for(fake).preview(self.preflight, self.envelope)
        self.assertEqual(caught.exception.code, protocol.ERROR_RESPONSE_INVALID)

    def test_bad_layout_hash_is_rejected(self) -> None:
        def factory(request: dict[str, Any]) -> bytes:
            result = json.loads(json.dumps(self.result))
            bad = "sha256:" + "0" * 64
            result["layoutHash"] = bad
            result["plan"]["layoutHash"] = bad
            return protocol.encode_message(
                protocol.success_response(request["requestId"], result, 7)
            )

        with self.assertRaises(BrokerClientError) as caught:
            fake = FakeBroker(factory)
            self.client_for(fake).preview(self.preflight, self.envelope)
        self.assertEqual(caught.exception.code, protocol.ERROR_PLAN_HASH_MISMATCH)

    def test_out_of_range_plan_coordinate_is_rejected(self) -> None:
        def factory(request: dict[str, Any]) -> bytes:
            result = json.loads(json.dumps(self.result))
            result["positions"][0]["afterX"] = protocol.MAX_COORDINATE + 1
            result["positions"][0]["deltaX"] = (
                result["positions"][0]["afterX"] - result["positions"][0]["beforeX"]
            )
            return protocol.encode_message(
                protocol.success_response(request["requestId"], result, 7)
            )

        with self.assertRaises(BrokerClientError) as caught:
            fake = FakeBroker(factory)
            self.client_for(fake).preview(self.preflight, self.envelope)
        self.assertEqual(caught.exception.code, protocol.ERROR_PLAN_INVALID)

    def test_changed_node_set_is_rejected(self) -> None:
        from ue_agent_kit.graph_contracts.layout import recompute_layout_hash

        def factory(request: dict[str, Any]) -> bytes:
            result = json.loads(json.dumps(self.result))
            result["plan"]["positions"].pop()
            recomputed = recompute_layout_hash(result["plan"])
            result["plan"]["layoutHash"] = recomputed
            result["layoutHash"] = recomputed
            return protocol.encode_message(
                protocol.success_response(request["requestId"], result, 7)
            )

        with self.assertRaises(BrokerClientError) as caught:
            fake = FakeBroker(factory)
            self.client_for(fake).preview(self.preflight, self.envelope)
        self.assertEqual(caught.exception.code, protocol.ERROR_PLAN_INVALID)

    def test_two_response_lines_are_rejected(self) -> None:
        def factory(request: dict[str, Any]) -> bytes:
            payload = protocol.success_response(request["requestId"], self.result, 7)
            return protocol.encode_message(payload) + b'{"ok":true}\n'

        with self.assertRaises(BrokerClientError) as caught:
            fake = FakeBroker(factory)
            self.client_for(fake).preview(self.preflight, self.envelope)
        self.assertEqual(caught.exception.code, protocol.ERROR_RESPONSE_INVALID)

    def test_error_message_with_token_is_rejected(self) -> None:
        def factory(request: dict[str, Any]) -> bytes:
            return protocol.encode_message(
                protocol.error_response(
                    request["requestId"], protocol.ERROR_WORKER_FAILED, TOKEN
                )
            )

        with self.assertRaises(BrokerClientError) as caught:
            fake = FakeBroker(factory)
            self.client_for(fake).preview(self.preflight, self.envelope)
        self.assertEqual(caught.exception.code, protocol.ERROR_RESPONSE_INVALID)

    def test_closed_port_is_reported_as_broker_unavailable(self) -> None:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
        probe.close()
        descriptor = descriptor_module.build_descriptor(
            port=port,
            token=TOKEN,
            project_name=harness.PROJECT_NAME,
            project_path_hash=harness.PROJECT_PATH_HASH,
        )
        client = LayoutBrokerClient(
            descriptor,
            project_name=harness.PROJECT_NAME,
            project_path_hash=harness.PROJECT_PATH_HASH,
            session_id=harness.SESSION_ID,
        )
        with self.assertRaises(BrokerClientError) as caught:
            client.preview(self.preflight, self.envelope)
        self.assertEqual(caught.exception.code, protocol.ERROR_BROKER_UNAVAILABLE)

    def test_locally_invalid_request_never_reaches_the_socket(self) -> None:
        fake = FakeBroker(lambda request: b"{}\n")
        client = self.client_for(fake)
        request = client.build_request(self.preflight, self.envelope)
        request["token"] = "0" * 64
        with self.assertRaises(BrokerClientError) as caught:
            client.send(request)
        self.assertEqual(caught.exception.code, protocol.ERROR_TOKEN_REJECTED)
        self.assertIsNone(fake.request)


if __name__ == "__main__":
    unittest.main()
