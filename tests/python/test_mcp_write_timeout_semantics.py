"""Regression: a mutating Live Editor Tool must not fake a safe retry.

Background
----------
``workflow_live`` re-raises any Editor Bridge exception that carries a ``code``
verbatim (``WorkflowError(str(exc.code), ...)``), and ``mcp_server`` decided
``retryable`` purely from the code.  A ``live-editor-timeout`` /
``live-editor-connection-closed`` that happens *after* an Apply request was
dispatched is therefore reported to the agent as a retryable failure with the
read-only guidance "retry the read-only request", even for destructive Tools
such as ``ue_apply_asset_property_live``.  The Editor may already hold the write,
so an automatic retry can duplicate or mask a real modification.

These tests pin the corrected contract: the dispatch state of a mutating Tool is
reported as indeterminate and is never advertised as retryable, while the
read-only behaviour is unchanged.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
TEST_ROOT = TOOL_ROOT / "tests" / "python"
for entry in (str(SRC_ROOT), str(TEST_ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from test_indexer_queries import (  # noqa: E402
    ASSET_A,
    REVISION_A,
    make_asset,
    write_export,
)
from test_mcp_server import FakeLiveEditorService, FakeWorkflowService  # noqa: E402
from ue_agent_kit.agent_workflow import WorkflowError  # noqa: E402
from ue_agent_kit.database import open_database  # noqa: E402
from ue_agent_kit.editor_bridge import LiveEditorError  # noqa: E402
from ue_agent_kit.indexer import build_index  # noqa: E402
from ue_agent_kit.mcp_server import (  # noqa: E402
    _error_response,
    create_mcp_server,
)


class TimeoutOnApplyWorkflow(FakeWorkflowService):
    """Fake Workflow that loses the Apply response after dispatch."""

    def apply_asset_property_live(self, plan_id, confirmation, change_set_id=""):
        raise WorkflowError(
            "live-editor-timeout",
            "The configured Live Editor Bridge did not respond before the timeout.",
        )


class WriteTimeoutSemanticsTests(unittest.TestCase):
    def test_write_timeout_is_indeterminate_and_not_retryable(self) -> None:
        response = _error_response(
            "ue_apply_asset_property_live",
            LiveEditorError(
                "live-editor-timeout",
                "The configured Live Editor Bridge did not respond before the timeout.",
            ),
            read_only=False,
        )
        error = response["error"]
        self.assertFalse(response["readOnly"])
        self.assertFalse(error["retryable"])
        self.assertTrue(error["details"]["indeterminate"])
        self.assertEqual(error["details"]["dispatchState"], "unknown")
        self.assertIn("Do not repeat this write", error["suggestedAction"])
        self.assertNotIn("read-only request", error["suggestedAction"])

    def test_write_connection_closed_is_indeterminate(self) -> None:
        response = _error_response(
            "ue_discard_asset_property_live",
            WorkflowError(
                "live-editor-connection-closed",
                "The Editor Bridge closed the connection before returning a response.",
            ),
            read_only=False,
        )
        error = response["error"]
        self.assertFalse(error["retryable"])
        self.assertTrue(error["details"]["indeterminate"])

    def test_read_only_timeout_keeps_the_published_retry_contract(self) -> None:
        response = _error_response(
            "ue_get_editor_context",
            LiveEditorError(
                "live-editor-timeout",
                "The configured Live Editor Bridge did not respond before the timeout.",
            ),
            read_only=True,
        )
        error = response["error"]
        self.assertTrue(error["retryable"])
        self.assertNotIn("indeterminate", error["details"])
        self.assertIn("read-only request", error["suggestedAction"])

    def test_other_write_errors_keep_their_existing_contract(self) -> None:
        response = _error_response(
            "ue_apply_asset_property_live",
            WorkflowError("policy-rejected", "The fixed Policy does not allow this write."),
            read_only=False,
        )
        error = response["error"]
        self.assertFalse(error["retryable"])
        self.assertNotIn("indeterminate", error["details"])

    def test_non_destructive_live_actions_stay_retryable(self) -> None:
        # ue_open_asset / ue_validate_asset / ue_run_automation_test are reported with
        # read_only=False but repeating them is harmless, so a transport timeout must
        # keep the published retryable contract instead of claiming a lost write.
        for tool in ("ue_open_asset", "ue_validate_asset", "ue_run_automation_test"):
            with self.subTest(tool=tool):
                response = _error_response(
                    tool,
                    LiveEditorError(
                        "live-editor-timeout",
                        "The configured Live Editor Bridge did not respond before the timeout.",
                    ),
                    read_only=False,
                )
                self.assertTrue(response["error"]["retryable"])
                self.assertNotIn("indeterminate", response["error"]["details"])

    def test_mcp_tool_surface_reports_indeterminate_for_apply(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            export = root / "export"
            write_export(
                export,
                [make_asset(ASSET_A, profile="asset-index", revision=REVISION_A, rich=True)],
            )
            database_path = root / "index.sqlite"
            with open_database(database_path) as connection:
                build_index(connection, export, database_path)
            server = create_mcp_server(
                database_path,
                workflow_service=TimeoutOnApplyWorkflow(),
                live_editor_service=FakeLiveEditorService(),
            )
            _, payload = asyncio.run(
                server.call_tool(
                    "ue_apply_asset_property_live",
                    {"plan_id": "plan_test", "confirmation": "APPLY plan_test"},
                )
            )
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "live-editor-timeout")
        self.assertFalse(payload["error"]["retryable"])
        self.assertTrue(payload["error"]["details"]["indeterminate"])


if __name__ == "__main__":
    unittest.main()
