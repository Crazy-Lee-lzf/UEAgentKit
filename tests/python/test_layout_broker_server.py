from __future__ import annotations

import json
import os
import socket
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import layout_broker_harness as harness  # noqa: E402

from ue_agent_kit.graph_layout_preview import build_preview  # noqa: E402
from ue_agent_kit.layout_broker import BrokerClientError, protocol  # noqa: E402
from ue_agent_kit.layout_broker import descriptor as descriptor_module  # noqa: E402
from ue_agent_kit.layout_broker import security  # noqa: E402


def _slow_preview(delay: float):
    def preview(preflight, envelope, **options):
        time.sleep(delay)
        return build_preview(preflight, envelope, **options)

    return preview


class BrokerServerCase(unittest.TestCase):
    def setUp(self) -> None:
        self.server, self.descriptor_path = harness.start_broker(self)
        self.preflight, self.envelope = harness.fixture_inputs("bpctx_export_linear.json")

    def exchange(self, data: bytes, **kwargs) -> dict:
        payload = harness.parse_line(
            harness.raw_exchange(self.server.port, data, **kwargs)
        )
        self.assertIsNotNone(payload, "the broker did not return a JSON response line")
        return payload  # type: ignore[return-value]

    def assert_error(self, payload: dict, code: str) -> None:
        self.assertFalse(payload.get("ok"), payload)
        error = payload.get("error")
        self.assertIsInstance(error, dict)
        self.assertEqual(error.get("code"), code, payload)
        self.assertNotIn(self.server.token, json.dumps(payload, ensure_ascii=False))

    def request(self, **overrides) -> dict:
        return harness.request_for(
            self.server, self.preflight, self.envelope, **overrides
        )


class HappyPathAndParityTests(BrokerServerCase):
    def test_three_fixtures_match_the_existing_preview_exactly(self) -> None:
        client = harness.client_for(self.server)
        for name in harness.FIXTURE_NAMES:
            with self.subTest(fixture=name):
                preflight, envelope = harness.fixture_inputs(name)
                direct = build_preview(preflight, envelope)
                through_broker = client.preview(preflight, envelope)
                self.assertEqual(through_broker, direct)
                self.assertEqual(through_broker["planId"], direct["planId"])
                self.assertEqual(through_broker["layoutHash"], direct["layoutHash"])
                self.assertEqual(
                    through_broker["movedNodeIds"], direct["movedNodeIds"]
                )
                self.assertEqual(through_broker["nodeCount"], direct["nodeCount"])

    def test_descriptor_is_written_with_proven_permissions_and_loopback_address(self) -> None:
        payload = json.loads(Path(self.descriptor_path).read_text(encoding="utf-8"))
        self.assertEqual(payload["address"], "127.0.0.1")
        self.assertEqual(payload["maxConcurrentJobs"], 1)
        self.assertEqual(payload["supportedContractVersions"], [protocol.PROTOCOL_SCHEMA_VERSION])
        status = security.verify_file_permissions(self.descriptor_path)
        self.assertTrue(status.proven, status)
        self.assertEqual(self.server.status()["descriptorSecurity"]["state"], "PROVEN")

    def test_stop_removes_the_descriptor_and_ends_the_listener(self) -> None:
        self.server.stop()
        self.assertFalse(Path(self.descriptor_path).exists())
        with self.assertRaises(BrokerClientError) as caught:
            harness.client_for(self.server).preview(self.preflight, self.envelope)
        self.assertEqual(caught.exception.code, protocol.ERROR_BROKER_UNAVAILABLE)

    def test_token_never_appears_in_logs_or_responses(self) -> None:
        with self.assertLogs("ueak.layout_broker", level="DEBUG") as captured:
            harness.client_for(self.server).preview(self.preflight, self.envelope)
            rejected = self.request()
            rejected["token"] = "0" * 64
            self.assert_error(
                self.exchange(harness.encoded(rejected)), protocol.ERROR_TOKEN_REJECTED
            )
            self.server.stop()
        joined = "\n".join(captured.output)
        self.assertNotIn(self.server.token, joined)
        self.assertNotIn(self.server.token, json.dumps(self.server.status()))

    def test_start_is_idempotent(self) -> None:
        again = self.server.start()
        self.assertEqual(again["port"], self.server.port)
        self.assertEqual(self.server.descriptor["token"], self.server.token)

    def test_bad_client_does_not_stop_the_broker(self) -> None:
        harness.raw_exchange(self.server.port, b"garbage\n")
        harness.client_for(self.server).preview(self.preflight, self.envelope)
        # The response is sent before the completed counter is published; under
        # full-suite load the server thread can lag briefly.  Poll with a bound
        # instead of asserting an instantaneous value.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and self.server.status()["completed"] < 1:
            time.sleep(0.01)
        self.assertEqual(self.server.status()["completed"], 1)


class RejectionTests(BrokerServerCase):
    def test_bad_token_is_rejected(self) -> None:
        request = self.request()
        request["token"] = "b" * 64
        self.assert_error(self.exchange(harness.encoded(request)), protocol.ERROR_TOKEN_REJECTED)

    def test_unknown_schema_version_is_rejected(self) -> None:
        request = self.request()
        request["schemaVersion"] = "ueak.layoutbroker/2.0"
        self.assert_error(
            self.exchange(harness.encoded(request)), protocol.ERROR_VERSION_UNSUPPORTED
        )

    def test_wrong_project_is_rejected(self) -> None:
        request = self.request()
        request["projectName"] = "AnotherProject"
        self.assert_error(
            self.exchange(harness.encoded(request)), protocol.ERROR_PROJECT_MISMATCH
        )

    def test_wrong_project_hash_is_rejected(self) -> None:
        request = self.request()
        request["projectPathHash"] = "sha1:" + "0" * 40
        self.assert_error(
            self.exchange(harness.encoded(request)), protocol.ERROR_PROJECT_MISMATCH
        )

    def test_wrong_session_is_rejected(self) -> None:
        request = self.request()
        request["sessionId"] = "another-session"
        self.assert_error(
            self.exchange(harness.encoded(request)), protocol.ERROR_IDENTITY_MISMATCH
        )

    def test_wrong_algorithm_is_rejected(self) -> None:
        request = self.request()
        request["algorithm"] = {"id": "p0-layered", "version": "2.0.0"}
        self.assert_error(
            self.exchange(harness.encoded(request)), protocol.ERROR_VERSION_UNSUPPORTED
        )

    def test_duplicate_request_id_is_rejected_without_replaying_the_plan(self) -> None:
        client = harness.client_for(self.server)
        request = client.build_request(self.preflight, self.envelope)
        first = client.send(request)
        self.assertIn("planId", first)
        with self.assertRaises(BrokerClientError) as caught:
            client.send(request)
        self.assertEqual(caught.exception.code, protocol.ERROR_DUPLICATE_REQUEST)
        self.assertGreaterEqual(self.server.status()["duplicates"], 1)

    def test_unknown_field_is_rejected(self) -> None:
        request = self.request()
        request["launch"] = "not-allowed"
        self.assert_error(
            self.exchange(harness.encoded(request)), protocol.ERROR_UNSUPPORTED_FIELD
        )

    def test_command_field_is_rejected(self) -> None:
        request = self.request()
        request["command"] = "cmd.exe /c calc"
        self.assert_error(
            self.exchange(harness.encoded(request)), protocol.ERROR_UNSUPPORTED_FIELD
        )

    def test_invalid_option_is_rejected(self) -> None:
        request = self.request()
        request["options"]["spacingX"] = 0
        self.assert_error(
            self.exchange(harness.encoded(request)), protocol.ERROR_REQUEST_INVALID
        )

    def test_malformed_json_is_rejected(self) -> None:
        payload = self.exchange(b"{not-json}\n")
        self.assert_error(payload, protocol.ERROR_REQUEST_INVALID)
        self.assertIsNone(payload["requestId"])

    def test_empty_line_is_rejected(self) -> None:
        self.assert_error(self.exchange(b"\n"), protocol.ERROR_REQUEST_INVALID)

    def test_truncated_json_is_rejected(self) -> None:
        line = json.dumps(self.request()).encode("utf-8")[:-5]
        payload = self.exchange(line)
        self.assert_error(payload, protocol.ERROR_REQUEST_INVALID)

    def test_truncated_without_newline_is_rejected(self) -> None:
        payload = self.exchange(b'{"schemaVersion":"ueak.layoutbroker/1.0"')
        self.assert_error(payload, protocol.ERROR_REQUEST_INVALID)

    def test_oversized_request_is_rejected_and_service_survives(self) -> None:
        payload = self.exchange(
            b"x" * (protocol.MAX_MESSAGE_BYTES + 64), timeout=4.0
        )
        self.assert_error(payload, protocol.ERROR_REQUEST_TOO_LARGE)
        harness.client_for(self.server).preview(self.preflight, self.envelope)

    def test_slow_sender_times_out(self) -> None:
        server, _ = harness.start_broker(self, read_timeout=0.25)
        preflight, envelope = harness.fixture_inputs("bpctx_export_linear.json")
        request = harness.request_for(server, preflight, envelope)
        partial = harness.encoded(request)[:100]
        payload = harness.parse_line(
            harness.raw_exchange(server.port, partial, half_close=False, timeout=2.0)
        )
        self.assertIsNotNone(payload)
        self.assert_error(payload, protocol.ERROR_TIMEOUT)

    def test_rate_limit_rejects_the_third_distinct_request(self) -> None:
        server, _ = harness.start_broker(self, rate_limit=2)
        client = harness.client_for(server)
        client.preview(self.preflight, self.envelope)
        client.preview(self.preflight, self.envelope)
        with self.assertRaises(BrokerClientError) as caught:
            client.preview(self.preflight, self.envelope)
        self.assertEqual(caught.exception.code, protocol.ERROR_RATE_LIMITED)


class TimeoutBusyAndCancelTests(BrokerServerCase):
    def test_slow_job_times_out_and_then_blocks_new_work_until_it_finishes(self) -> None:
        server, _ = harness.start_broker(self, preview_fn=_slow_preview(0.6))
        client = harness.client_for(server)
        with self.assertRaises(BrokerClientError) as caught:
            client.preview(self.preflight, self.envelope, deadline_ms=150)
        self.assertEqual(caught.exception.code, protocol.ERROR_TIMEOUT)
        with self.assertRaises(BrokerClientError) as caught:
            client.preview(self.preflight, self.envelope)
        self.assertEqual(caught.exception.code, protocol.ERROR_BUSY)
        time.sleep(0.7)
        result = client.preview(self.preflight, self.envelope)
        self.assertIn("planId", result)
        status = server.status()
        self.assertGreaterEqual(status["timedOut"], 1)
        self.assertGreaterEqual(status["busy"], 1)

    def test_partial_request_close_is_rejected_without_killing_the_broker(self) -> None:
        request = self.request()
        connection = socket.create_connection(("127.0.0.1", self.server.port), timeout=2.0)
        connection.sendall(harness.encoded(request)[:64])
        connection.close()
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            status = self.server.status()
            if status["rejected"] + status["cancelled"] >= 1:
                break
            time.sleep(0.01)
        status = self.server.status()
        self.assertGreaterEqual(status["rejected"] + status["cancelled"], 1)
        harness.client_for(self.server).preview(self.preflight, self.envelope)

    def test_client_reset_after_a_full_request_is_counted_as_cancelled(self) -> None:
        import struct
        import threading

        gate = threading.Event()

        def gated(preflight, envelope, **options):
            self.assertTrue(gate.wait(2.0))
            time.sleep(0.05)
            return build_preview(preflight, envelope, **options)

        server, _ = harness.start_broker(self, preview_fn=gated)
        connection = socket.create_connection(("127.0.0.1", server.port), timeout=2.0)
        connection.sendall(harness.encoded(harness.request_for(server, self.preflight, self.envelope)))
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and server.status()["accepted"] < 1:
            time.sleep(0.01)
        self.assertEqual(server.status()["accepted"], 1)
        # Winsock uses two 16-bit fields; POSIX struct linger uses two ints.
        linger_format = "hh" if os.name == "nt" else "ii"
        try:
            connection.setsockopt(
                socket.SOL_SOCKET,
                socket.SO_LINGER,
                struct.pack(linger_format, 1, 0),
            )
        finally:
            connection.close()
        time.sleep(0.15)
        gate.set()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if server.status()["cancelled"] >= 1:
                break
            time.sleep(0.01)
        self.assertGreaterEqual(server.status()["cancelled"], 1)
        harness.client_for(server).preview(self.preflight, self.envelope)

    def test_total_deadline_includes_the_time_spent_reading(self) -> None:
        server, _ = harness.start_broker(
            self, total_deadline=0.4, read_timeout=1.0, preview_fn=_slow_preview(0.8)
        )
        request = harness.request_for(
            server, self.preflight, self.envelope, deadline_ms=5000
        )
        connection = socket.create_connection(("127.0.0.1", server.port), timeout=2.0)
        started = time.monotonic()
        try:
            time.sleep(0.25)
            connection.sendall(harness.encoded(request))
            connection.shutdown(socket.SHUT_WR)
            buffer = bytearray()
            connection.settimeout(2.0)
            while b"\n" not in buffer:
                chunk = connection.recv(65536)
                if not chunk:
                    break
                buffer.extend(chunk)
        finally:
            connection.close()
        elapsed = time.monotonic() - started
        payload = harness.parse_line(bytes(buffer))
        self.assertIsNotNone(payload)
        self.assert_error(payload, protocol.ERROR_TIMEOUT)
        self.assertLess(elapsed, 0.75, "the total deadline must bound read + job time")

    def test_client_disappearing_after_a_full_request_does_not_kill_the_broker(self) -> None:
        connection = socket.create_connection(("127.0.0.1", self.server.port), timeout=2.0)
        connection.sendall(harness.encoded(self.request()))
        connection.close()
        time.sleep(0.1)
        harness.client_for(self.server).preview(self.preflight, self.envelope)
        self.assertGreaterEqual(self.server.status()["accepted"], 2)


class ResultSelfCheckTests(BrokerServerCase):
    def test_tampered_plan_is_rejected_by_the_broker_self_check(self) -> None:
        def tampered(preflight, envelope, **options):
            result = build_preview(preflight, envelope, **options)
            result["positions"][0]["afterX"] += 1000
            result["positions"][0]["deltaX"] += 1000
            return result

        server, _ = harness.start_broker(self, preview_fn=tampered)
        client = harness.client_for(server)
        with self.assertRaises(BrokerClientError) as caught:
            client.preview(self.preflight, self.envelope)
        self.assertEqual(caught.exception.code, protocol.ERROR_WORKER_FAILED)
        self.assertEqual(server.status()["lastErrorCode"], protocol.ERROR_PLAN_INVALID)

    def test_oversized_result_is_refused_instead_of_truncated(self) -> None:
        def oversized(preflight, envelope, **options):
            result = build_preview(preflight, envelope, **options)
            result["warnings"] = ["x" * 1_100_000]
            return result

        server, _ = harness.start_broker(self, preview_fn=oversized)
        client = harness.client_for(server)
        with self.assertRaises(BrokerClientError) as caught:
            client.preview(self.preflight, self.envelope)
        self.assertEqual(caught.exception.code, protocol.ERROR_WORKER_FAILED)

    def test_error_messages_are_sanitized(self) -> None:
        holder: list[str] = []

        def failing(preflight, envelope, **options):
            raise RuntimeError(
                f"boom {holder[0]} C:\\Users\\secret\\plan.json"
            )

        server, _ = harness.start_broker(self, preview_fn=failing)
        holder.append(server.token)
        client = harness.client_for(server)
        with self.assertRaises(BrokerClientError) as caught:
            client.preview(self.preflight, self.envelope)
        self.assertEqual(caught.exception.code, protocol.ERROR_WORKER_FAILED)
        self.assertNotIn(server.token, caught.exception.message)
        self.assertNotIn("C:\\Users", caught.exception.message)


class DescriptorLifecycleTests(unittest.TestCase):
    def test_atomic_write_failure_leaves_the_previous_descriptor_untouched(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory(prefix="ueak-m1-descriptor-") as directory:
            path = os.path.join(directory, "LayoutBroker.json")
            Path(path).write_text('{"previous": true}\n', encoding="utf-8")
            descriptor = descriptor_module.build_descriptor(
                port=51234,
                token="e" * 64,
                project_name=harness.PROJECT_NAME,
                project_path_hash=harness.PROJECT_PATH_HASH,
                process_id=42,
            )
            with mock.patch.object(
                descriptor_module.os, "replace", side_effect=OSError("injected failure")
            ):
                with self.assertRaises(OSError):
                    descriptor_module.write_descriptor_atomic(path, descriptor)
            self.assertEqual(
                Path(path).read_text(encoding="utf-8"), '{"previous": true}\n'
            )
            leftovers = [name for name in os.listdir(directory) if name.endswith(".tmp")]
            self.assertEqual(leftovers, [])

    def test_unverified_permissions_fail_closed_unless_explicitly_allowed(self) -> None:
        import tempfile

        from ue_agent_kit.layout_broker import LayoutBrokerServer

        unverified = security.FileSecurityStatus(
            security.UNVERIFIED, "test", "forced-by-test"
        )
        with tempfile.TemporaryDirectory(prefix="ueak-m1-descriptor-") as directory:
            path = os.path.join(directory, "LayoutBroker.json")
            with mock.patch.object(
                descriptor_module, "restrict_file_permissions", return_value=unverified
            ), mock.patch.object(
                descriptor_module, "verify_file_permissions", return_value=unverified
            ):
                with self.assertRaises(protocol.BrokerProtocolError):
                    LayoutBrokerServer(
                        descriptor_path=path,
                        project_name=harness.PROJECT_NAME,
                        project_path_hash=harness.PROJECT_PATH_HASH,
                    ).start()
                self.assertFalse(Path(path).exists())

                allowed = LayoutBrokerServer(
                    descriptor_path=path,
                    project_name=harness.PROJECT_NAME,
                    project_path_hash=harness.PROJECT_PATH_HASH,
                    allow_unverified_acl=True,
                )
                allowed.start()
                try:
                    self.assertEqual(
                        allowed.status()["descriptorSecurity"]["state"], "UNVERIFIED"
                    )
                finally:
                    allowed.stop()

    def test_descriptor_is_not_removed_when_its_token_changed(self) -> None:
        server, path = harness.start_broker(self)
        token = "1" * 64
        replacement = descriptor_module.build_descriptor(
            port=server.port,
            token=token,
            project_name=harness.PROJECT_NAME,
            project_path_hash=harness.PROJECT_PATH_HASH,
        )
        descriptor_module.write_descriptor_atomic(path, replacement)
        server.stop()
        self.assertTrue(Path(path).exists())
        stored = json.loads(Path(path).read_text(encoding="utf-8"))
        self.assertEqual(stored["token"], token)
        Path(path).unlink(missing_ok=True)

    def test_missing_or_invalid_descriptor_file_is_refused_by_the_client(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory(prefix="ueak-m1-descriptor-") as directory:
            missing = os.path.join(directory, "missing.json")
            with self.assertRaises(protocol.BrokerProtocolError) as caught:
                descriptor_module.read_descriptor_file(missing)
            self.assertEqual(caught.exception.code, protocol.ERROR_DESCRIPTOR_MISSING)

            invalid = os.path.join(directory, "invalid.json")
            Path(invalid).write_text("{not json", encoding="utf-8")
            with self.assertRaises(protocol.BrokerProtocolError) as caught:
                descriptor_module.read_descriptor_file(invalid)
            self.assertEqual(caught.exception.code, protocol.ERROR_DESCRIPTOR_INVALID)


if __name__ == "__main__":
    unittest.main()
