"""Static/unit checks for the R1.5 external socket harness (no editor needed)."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tests.native_tools import r15_socket_harness as harness


class HarnessUnitTests(unittest.TestCase):
    def test_redact_response_removes_error_details(self) -> None:
        response = {"ok": False, "error": {"code": "x", "message": "m", "details": {"token": "s"}}}
        redacted = harness.redact_response(response)
        self.assertEqual(redacted["error"]["code"], "x")
        self.assertNotIn("details", redacted["error"])

    def test_wait_for_descriptor_matches_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "EditorBridge.json"
            path.write_text(json.dumps({"processId": 42, "port": 1}), encoding="utf-8")
            descriptor = harness.wait_for_descriptor(path, 42, timeout=1.0)
            self.assertEqual(descriptor["port"], 1)

    def test_sha256_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "asset.uasset"
            path.write_bytes(b"r1.5")
            self.assertEqual(harness.sha256_file(path), hashlib.sha256(b"r1.5").hexdigest())

    def test_hello_fields_are_at_request_root(self) -> None:
        client = harness.Client({
            "address": "127.0.0.1",
            "port": 1,
            "authToken": "A" * 64,
            "pluginVersion": "0.8.0",
            "projectPathHash": "sha1:test",
        })
        payload = client._hello_payload()
        self.assertEqual(payload["method"], "hello")
        self.assertIn("authToken", payload)
        self.assertNotIn("params", payload)

    def test_source_never_prints_auth_token(self) -> None:
        source = Path(harness.__file__).read_text(encoding="utf-8")
        self.assertNotIn("print(self.client.auth_token", source)
        self.assertNotIn("print(self.auth_token", source)
        self.assertIn('hello["authToken"] = "<redacted>"', source)


if __name__ == "__main__":
    unittest.main()
