from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import layout_broker_harness as harness

from ue_agent_kit.layout_broker import LayoutBrokerClient
from ue_agent_kit.layout_broker import descriptor as descriptor_module


class ManualCliTests(unittest.TestCase):
    def test_cli_starts_serves_and_exits_without_leaving_a_descriptor(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak-m1-cli-") as directory:
            descriptor_path = str(Path(directory) / "LayoutBroker.json")
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(harness.ROOT / "tools" / "run_layout_broker.py"),
                    "--descriptor",
                    descriptor_path,
                    "--project-name",
                    harness.PROJECT_NAME,
                    "--project-path",
                    harness.PROJECT_PATH,
                    "--expect-session-id",
                    harness.SESSION_ID,
                    "--run-seconds",
                    "2.5",
                    "--log-level",
                    "ERROR",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
            try:
                line = process.stdout.readline()
                self.assertTrue(line, "the CLI did not print a status line")
                status = json.loads(line)
                self.assertTrue(status["ok"], status)
                self.assertEqual(status["address"], "127.0.0.1")
                self.assertNotIn("token", status)
                descriptor = descriptor_module.read_descriptor_file(
                    descriptor_path,
                    project_name=harness.PROJECT_NAME,
                    project_path_hash=harness.PROJECT_PATH_HASH,
                )
                self.assertNotIn(descriptor["token"], line)
                self.assertEqual(status["descriptorSecurity"]["state"], "PROVEN")

                client = LayoutBrokerClient.from_descriptor_file(
                    descriptor_path,
                    project_name=harness.PROJECT_NAME,
                    project_path_hash=harness.PROJECT_PATH_HASH,
                    session_id=harness.SESSION_ID,
                )
                preflight, envelope = harness.fixture_inputs("bpctx_export_linear.json")
                result = client.preview(preflight, envelope)
                self.assertIn("planId", result)
                self.assertEqual(process.wait(timeout=10), 0)
                self.assertFalse(Path(descriptor_path).exists())
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)
                if process.stdout is not None:
                    process.stdout.close()
                if process.stderr is not None:
                    process.stderr.close()


if __name__ == "__main__":
    unittest.main()
