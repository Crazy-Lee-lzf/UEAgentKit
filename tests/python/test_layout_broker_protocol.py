from __future__ import annotations

import copy
import sys
import unittest
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import layout_broker_harness as harness  # noqa: E402

from ue_agent_kit.graph_layout_preview import build_preview  # noqa: E402
from ue_agent_kit.layout_broker import descriptor as descriptor_module  # noqa: E402
from ue_agent_kit.layout_broker import protocol  # noqa: E402

TOKEN = "9f3c1a7e5b2d4f60" * 4


def _request(
    preflight: dict,
    envelope: dict,
    **overrides: object,
) -> dict:
    request = {
        "schemaVersion": protocol.PROTOCOL_SCHEMA_VERSION,
        "requestId": str(uuid.uuid4()),
        "sessionId": harness.SESSION_ID,
        "projectName": harness.PROJECT_NAME,
        "projectPathHash": harness.PROJECT_PATH_HASH,
        "token": TOKEN,
        "sentAtUtc": "2026-10-24T00:00:00Z",
        "deadlineMs": 5000,
        "algorithm": dict(protocol.ALGORITHM),
        "options": {
            "mode": "full",
            "spacingX": 96,
            "spacingY": 64,
            "targetWidth": 1800,
            "seed": 0,
        },
        "snapshot": {
            "preflight": copy.deepcopy(preflight),
            "contextEnvelope": copy.deepcopy(envelope),
        },
    }
    request.update(overrides)
    return request


class BrokerProtocolCase(unittest.TestCase):
    def setUp(self) -> None:
        self.preflight, self.envelope = harness.fixture_inputs("bpctx_export_linear.json")
        self.request = _request(self.preflight, self.envelope)

    def validate(self, request: dict):
        return protocol.validate_request(
            request,
            project_name=harness.PROJECT_NAME,
            project_path_hash=harness.PROJECT_PATH_HASH,
            token=TOKEN,
            expected_session_id=harness.SESSION_ID,
        )

    def assert_code(self, code: str, request: dict) -> None:
        with self.assertRaises(protocol.BrokerProtocolError) as caught:
            self.validate(request)
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn(TOKEN, str(caught.exception))


class RequestValidationTests(BrokerProtocolCase):
    def test_valid_request_is_typed_and_drops_the_token(self) -> None:
        validated = self.validate(self.request)
        self.assertEqual(validated.request_id, self.request["requestId"])
        self.assertEqual(validated.mode, "full")
        self.assertEqual(validated.deadline_ms, 5000)
        self.assertEqual(validated.preflight["assetPath"], self.preflight["assetPath"])
        self.assertFalse(hasattr(validated, "token"))
        self.assertNotIn(TOKEN, repr(validated))

    def test_unknown_top_level_field_is_refused(self) -> None:
        self.request["surprise"] = 1
        self.assert_code(protocol.ERROR_UNSUPPORTED_FIELD, self.request)

    def test_missing_required_field_is_refused(self) -> None:
        del self.request["sentAtUtc"]
        self.assert_code(protocol.ERROR_REQUEST_INVALID, self.request)

    def test_bad_token_is_refused_without_echoing_it(self) -> None:
        self.request["token"] = "b" * 64
        self.assert_code(protocol.ERROR_TOKEN_REJECTED, self.request)

    def test_malformed_token_is_refused(self) -> None:
        self.request["token"] = "not-hex"
        self.assert_code(protocol.ERROR_TOKEN_REJECTED, self.request)

    def test_wrong_schema_version_is_refused(self) -> None:
        self.request["schemaVersion"] = "ueak.layoutbroker/2.0"
        self.assert_code(protocol.ERROR_VERSION_UNSUPPORTED, self.request)

    def test_wrong_project_name_is_refused(self) -> None:
        self.request["projectName"] = "OtherProject"
        self.assert_code(protocol.ERROR_PROJECT_MISMATCH, self.request)

    def test_wrong_project_path_hash_is_refused(self) -> None:
        self.request["projectPathHash"] = "sha1:" + "0" * 40
        self.assert_code(protocol.ERROR_PROJECT_MISMATCH, self.request)

    def test_wrong_session_is_refused(self) -> None:
        self.request["sessionId"] = "another-session"
        self.assert_code(protocol.ERROR_IDENTITY_MISMATCH, self.request)

    def test_unsupported_algorithm_is_refused(self) -> None:
        self.request["algorithm"] = {"id": "p0-layered", "version": "9.9.9"}
        self.assert_code(protocol.ERROR_VERSION_UNSUPPORTED, self.request)

    def test_selection_mode_is_refused_by_m1(self) -> None:
        self.request["options"]["mode"] = "selection"
        self.assert_code(protocol.ERROR_REQUEST_INVALID, self.request)

    def test_option_bounds_are_enforced(self) -> None:
        for key, value in (
            ("spacingX", 0),
            ("spacingY", 4097),
            ("targetWidth", 255),
            ("seed", -1),
        ):
            with self.subTest(key=key):
                request = copy.deepcopy(self.request)
                request["options"][key] = value
                self.assert_code(protocol.ERROR_REQUEST_INVALID, request)

    def test_deadline_above_five_seconds_is_refused(self) -> None:
        self.request["deadlineMs"] = 5001
        self.assert_code(protocol.ERROR_REQUEST_INVALID, self.request)

    def test_command_like_field_is_refused(self) -> None:
        self.request["command"] = "calc.exe"
        self.assert_code(protocol.ERROR_UNSUPPORTED_FIELD, self.request)

    def test_command_like_option_field_is_refused(self) -> None:
        self.request["options"]["exec"] = "powershell"
        self.assert_code(protocol.ERROR_UNSUPPORTED_FIELD, self.request)

    def test_command_like_snapshot_field_is_refused(self) -> None:
        self.request["snapshot"]["preflight"]["command"] = "calc.exe"
        self.assert_code(protocol.ERROR_UNSUPPORTED_FIELD, self.request)

    def test_snapshot_session_mismatch_is_refused(self) -> None:
        self.request["snapshot"]["preflight"]["sessionId"] = "not-the-request-session"
        self.assert_code(protocol.ERROR_IDENTITY_MISMATCH, self.request)

    def test_non_virtual_asset_path_is_refused(self) -> None:
        self.request["snapshot"]["preflight"]["assetPath"] = r"C:\secret\asset.uasset"
        self.assert_code(protocol.ERROR_REQUEST_INVALID, self.request)

    def test_naive_timestamp_is_refused(self) -> None:
        self.request["sentAtUtc"] = "2026-10-24T00:00:00"
        self.assert_code(protocol.ERROR_REQUEST_INVALID, self.request)

    def test_boolean_is_not_an_integer(self) -> None:
        self.request["options"]["seed"] = True
        self.assert_code(protocol.ERROR_REQUEST_INVALID, self.request)

    def test_non_object_payload_is_refused(self) -> None:
        with self.assertRaises(protocol.BrokerProtocolError) as caught:
            self.validate([1, 2, 3])  # type: ignore[arg-type]
        self.assertEqual(caught.exception.code, protocol.ERROR_REQUEST_INVALID)


class ResponseValidationTests(BrokerProtocolCase):
    def setUp(self) -> None:
        super().setUp()
        self.validated = self.validate(self.request)
        self.result = build_preview(self.preflight, self.envelope)
        self.payload = protocol.success_response(
            self.validated.request_id, self.result, 12
        )

    def accept(self, payload: dict) -> dict:
        return protocol.validate_response(payload, self.validated, token=TOKEN)

    def test_valid_success_response_passes(self) -> None:
        accepted = self.accept(copy.deepcopy(self.payload))
        self.assertEqual(accepted["planId"], self.result["planId"])
        self.assertEqual(accepted["layoutHash"], self.result["layoutHash"])

    def test_wrong_request_id_is_refused(self) -> None:
        self.payload["requestId"] = str(uuid.uuid4())
        with self.assertRaises(protocol.BrokerProtocolError) as caught:
            self.accept(self.payload)
        self.assertEqual(caught.exception.code, protocol.ERROR_RESPONSE_INVALID)

    def test_unknown_response_field_is_refused(self) -> None:
        self.payload["debug"] = "C:/temp/x"
        with self.assertRaises(protocol.BrokerProtocolError) as caught:
            self.accept(self.payload)
        self.assertEqual(caught.exception.code, protocol.ERROR_RESPONSE_INVALID)

    def test_token_echo_is_refused(self) -> None:
        self.payload["result"]["graphName"] = TOKEN
        with self.assertRaises(protocol.BrokerProtocolError) as caught:
            self.accept(self.payload)
        self.assertEqual(caught.exception.code, protocol.ERROR_RESPONSE_INVALID)
        self.assertNotIn(TOKEN, str(caught.exception))

    def test_layout_hash_mismatch_is_refused(self) -> None:
        self.payload["result"]["layoutHash"] = "sha256:" + "0" * 64
        self.payload["result"]["plan"]["layoutHash"] = "sha256:" + "0" * 64
        with self.assertRaises(protocol.BrokerProtocolError) as caught:
            self.accept(self.payload)
        self.assertEqual(caught.exception.code, protocol.ERROR_PLAN_HASH_MISMATCH)

    def test_out_of_range_coordinate_is_refused(self) -> None:
        position = self.payload["result"]["positions"][0]
        position["afterX"] = protocol.MAX_COORDINATE + 1
        position["deltaX"] = position["afterX"] - position["beforeX"]
        with self.assertRaises(protocol.BrokerProtocolError) as caught:
            self.accept(self.payload)
        self.assertEqual(caught.exception.code, protocol.ERROR_PLAN_INVALID)

    def test_missing_node_is_refused(self) -> None:
        self.payload["result"]["positions"].pop()
        self.payload["result"]["nodeCount"] -= 1
        with self.assertRaises(protocol.BrokerProtocolError) as caught:
            self.accept(self.payload)
        self.assertIn(
            caught.exception.code,
            (protocol.ERROR_PLAN_INVALID, protocol.ERROR_IDENTITY_MISMATCH),
        )

    def test_error_response_is_raised_with_stable_code(self) -> None:
        payload = protocol.error_response(
            self.validated.request_id, protocol.ERROR_RATE_LIMITED, "slow down", retryable=True
        )
        with self.assertRaises(protocol.BrokerProtocolError) as caught:
            self.accept(payload)
        self.assertEqual(caught.exception.code, protocol.ERROR_RATE_LIMITED)
        self.assertTrue(caught.exception.retryable)

    def test_unknown_error_code_is_refused(self) -> None:
        payload = protocol.error_response(
            self.validated.request_id, "native-preview-made-up", "nope"
        )
        with self.assertRaises(protocol.BrokerProtocolError) as caught:
            self.accept(payload)
        self.assertEqual(caught.exception.code, protocol.ERROR_RESPONSE_INVALID)

    def test_boolean_duration_is_refused(self) -> None:
        self.payload["durationMs"] = True
        with self.assertRaises(protocol.BrokerProtocolError) as caught:
            self.accept(self.payload)
        self.assertEqual(caught.exception.code, protocol.ERROR_RESPONSE_INVALID)


class FramingTests(unittest.TestCase):
    def test_one_mib_boundary(self) -> None:
        payload = {"value": "x" * (protocol.MAX_MESSAGE_BYTES - 32)}
        encoded = protocol.encode_message(payload)
        self.assertLessEqual(len(encoded), protocol.MAX_MESSAGE_BYTES)
        self.assertEqual(protocol.decode_message(encoded), payload)

    def test_oversized_message_is_refused(self) -> None:
        payload = {"value": "x" * protocol.MAX_MESSAGE_BYTES}
        with self.assertRaises(protocol.BrokerProtocolError) as caught:
            protocol.encode_message(payload)
        self.assertEqual(caught.exception.code, protocol.ERROR_RESPONSE_TOO_LARGE)
        with self.assertRaises(protocol.BrokerProtocolError) as caught:
            protocol.decode_message(b"x" * (protocol.MAX_MESSAGE_BYTES + 1))
        self.assertEqual(caught.exception.code, protocol.ERROR_REQUEST_TOO_LARGE)

    def test_non_json_and_non_object_are_refused(self) -> None:
        for data in (b"not json\n", b"[1,2]\n", b"", b"\n", b"\xff\xfe\n"):
            with self.subTest(data=data):
                with self.assertRaises(protocol.BrokerProtocolError) as caught:
                    protocol.decode_message(data)
                self.assertEqual(caught.exception.code, protocol.ERROR_REQUEST_INVALID)

    def test_echo_request_id_only_accepts_canonical_uuid(self) -> None:
        request_id = str(uuid.uuid4())
        self.assertEqual(protocol.echo_request_id({"requestId": request_id}), request_id)
        self.assertIsNone(protocol.echo_request_id({"requestId": "x" * 400}))
        self.assertIsNone(protocol.echo_request_id("not-a-dict"))

    def test_sanitize_message_removes_token_and_local_paths(self) -> None:
        token = "9f3c1a7e5b2d4f60" * 4
        text = (
            f"boom {token} C:\\Users\\secret\\x.json C:/Users/secret/y.json "
            "\\\\server\\share\\y //server/share/z"
        )
        cleaned = protocol.sanitize_message(text, token)
        self.assertNotIn(token, cleaned)
        self.assertNotIn("C:\\Users", cleaned)
        self.assertNotIn("C:/Users", cleaned)
        self.assertNotIn("\\\\server", cleaned)
        self.assertNotIn("//server", cleaned)
        self.assertLessEqual(len(cleaned), 400)
        self.assertNotIn("\n", protocol.sanitize_message("line\nbreak"))

    def test_project_path_hash_matches_editor_normalization(self) -> None:
        backslashes = protocol.build_project_path_hash(r"C:\Projects\Demo\Demo.uproject")
        forward = protocol.build_project_path_hash("C:/Projects/Demo/Demo.uproject")
        lower = protocol.build_project_path_hash("c:/projects/demo/demo.uproject")
        self.assertEqual(backslashes, forward)
        self.assertEqual(forward, lower)
        self.assertRegex(backslashes, r"^sha1:[0-9a-f]{40}$")


class DescriptorValidationTests(unittest.TestCase):
    def descriptor(self) -> dict:
        return descriptor_module.build_descriptor(
            port=51234,
            token="d" * 64,
            project_name=harness.PROJECT_NAME,
            project_path_hash=harness.PROJECT_PATH_HASH,
            process_id=1234,
        )

    def test_valid_descriptor_passes(self) -> None:
        validated = descriptor_module.validate_descriptor(
            self.descriptor(),
            project_name=harness.PROJECT_NAME,
            project_path_hash=harness.PROJECT_PATH_HASH,
        )
        self.assertEqual(validated["address"], "127.0.0.1")
        self.assertEqual(validated["maxConcurrentJobs"], 1)

    def test_extra_field_is_refused(self) -> None:
        payload = self.descriptor()
        payload["extra"] = "x"
        with self.assertRaises(protocol.BrokerProtocolError) as caught:
            descriptor_module.validate_descriptor(payload)
        self.assertEqual(caught.exception.code, protocol.ERROR_DESCRIPTOR_INVALID)

    def test_non_loopback_address_is_refused(self) -> None:
        for address in ("0.0.0.0", "::1", "localhost", "192.168.1.5"):
            with self.subTest(address=address):
                payload = self.descriptor()
                payload["address"] = address
                with self.assertRaises(protocol.BrokerProtocolError):
                    descriptor_module.validate_descriptor(payload)

    def test_stale_descriptor_is_refused(self) -> None:
        from datetime import datetime, timedelta, timezone

        payload = self.descriptor()
        payload["startedAtUtc"] = descriptor_module.format_utc(
            datetime.now(timezone.utc) - timedelta(hours=13)
        )
        with self.assertRaises(protocol.BrokerProtocolError) as caught:
            descriptor_module.validate_descriptor(payload)
        self.assertEqual(caught.exception.code, protocol.ERROR_DESCRIPTOR_INVALID)

    def test_missing_algorithm_is_refused(self) -> None:
        payload = self.descriptor()
        payload["algorithms"] = [{"id": "p0-layered", "version": "0.0.1"}]
        with self.assertRaises(protocol.BrokerProtocolError) as caught:
            descriptor_module.validate_descriptor(payload)
        self.assertEqual(caught.exception.code, protocol.ERROR_VERSION_UNSUPPORTED)

    def test_bad_token_shape_is_refused(self) -> None:
        payload = self.descriptor()
        payload["token"] = "Z" * 64
        with self.assertRaises(protocol.BrokerProtocolError):
            descriptor_module.validate_descriptor(payload)

    def test_other_project_is_refused(self) -> None:
        with self.assertRaises(protocol.BrokerProtocolError) as caught:
            descriptor_module.validate_descriptor(
                self.descriptor(),
                project_name="AnotherProject",
            )
        self.assertEqual(caught.exception.code, protocol.ERROR_PROJECT_MISMATCH)


class SourceGuardTests(unittest.TestCase):
    """Static guards for the M1 isolation promises."""

    def package_text(self) -> str:
        root = harness.SRC / "ue_agent_kit" / "layout_broker"
        return "\n".join(
            path.read_text(encoding="utf-8") for path in sorted(root.glob("*.py"))
        )

    def test_broker_package_never_spawns_a_process(self) -> None:
        text = self.package_text()
        for forbidden in (
            "import subprocess",
            "subprocess.",
            "os.system(",
            "os.popen(",
            "Popen(",
            "CreateProcess(",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)

    def test_broker_package_never_imports_the_editor_bridge(self) -> None:
        self.assertNotIn("editor_bridge", self.package_text())

    def test_server_never_connects_out_and_pins_loopback(self) -> None:
        server_text = (
            harness.SRC / "ue_agent_kit" / "layout_broker" / "server.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("create_connection", server_text)
        self.assertNotIn("0.0.0.0", server_text)
        self.assertIn('bind(("127.0.0.1", 0))', server_text)

    def test_request_path_modules_do_not_touch_the_filesystem(self) -> None:
        root = harness.SRC / "ue_agent_kit" / "layout_broker"
        for name in ("server.py", "protocol.py", "client.py"):
            with self.subTest(module=name):
                text = (root / name).read_text(encoding="utf-8")
                self.assertNotIn("open(", text)
                self.assertNotIn("write_text", text)
                self.assertNotIn("write_bytes", text)


if __name__ == "__main__":
    unittest.main()
