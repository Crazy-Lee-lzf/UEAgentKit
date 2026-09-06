from __future__ import annotations

import asyncio
import importlib.util
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.agent_api import IndexQueryService  # noqa: E402
from ue_agent_kit.database import open_database  # noqa: E402
from ue_agent_kit.indexer import build_index  # noqa: E402
from ue_agent_kit.mcp_server import create_mcp_server  # noqa: E402
from ue_agent_kit.source_control import P4SourceControlService  # noqa: E402
from ue_agent_kit.tool_registry import (  # noqa: E402
    SOURCE_CONTROL_TOOL_NAMES,
    tool_names_for_mode,
)
from test_indexer_queries import (  # noqa: E402
    ASSET_A,
    REVISION_A,
    make_asset,
    make_generic_asset,
    write_export,
)
from test_source_control import FakeP4Runner  # noqa: E402

MCP_AVAILABLE = importlib.util.find_spec("mcp") is not None


def _readonly(path: Path) -> None:
    path.chmod(stat.S_IREAD)


@unittest.skipUnless(MCP_AVAILABLE, "optional mcp dependency is not installed")
class McpSourceControlToolsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(prefix="ueak_mcp_sc_")
        self.temp_root = Path(self.temporary_directory.name)
        self.database_path = self.temp_root / "ueak.sqlite3"
        export_root = self.temp_root / "export"
        write_export(
            export_root,
            [
                make_generic_asset(),
                make_asset(ASSET_A, profile="logic", revision=REVISION_A, rich=True),
            ],
        )
        with open_database(self.database_path) as connection:
            result = build_index(connection, export_root, self.database_path)
        self.assertEqual((result.added, result.failed), (2, 0))
        self.index_service = IndexQueryService(self.database_path)

        self.managed = self.temp_root / "managed.py"
        self.managed.write_text("print(1)\n", encoding="utf-8")
        _readonly(self.managed)
        self.locked = self.temp_root / "locked.bin"
        self.locked.write_bytes(b"\x00\x01")
        _readonly(self.locked)
        self.unmapped = self.temp_root / "unmapped.txt"
        self.unmapped.write_text("local\n", encoding="utf-8")
        self.merge_py = self.temp_root / "merge.py"
        self.merge_py.write_text("left = 1\n", encoding="utf-8")
        self.binary_asset = self.temp_root / "Asset.uasset"
        self.binary_asset.write_bytes(b"\x00\x01\x02")
        self.closed_py = self.temp_root / "closed.py"
        self.closed_py.write_text("closed\n", encoding="utf-8")
        _readonly(self.closed_py)

        f = self.temp_root.as_posix()
        self.world: dict[str, Any] = {
            "userName": "alice",
            "clientName": "alice_ws",
            "nextChangeId": 5100,
            "pendingChanges": {
                "2001": {
                    "status": "pending",
                    "user": "alice",
                    "client": "alice_ws",
                    "description": "alice review",
                    "files": [],
                },
            },
            "files": {
                f"{f}/managed.py": {
                    "depotFile": "//depot/Content/managed.py",
                    "headRev": "1",
                    "haveRev": "1",
                    "type": "text",
                    "headAction": "add",
                },
                f"{f}/locked.bin": {
                    "depotFile": "//depot/Content/locked.bin",
                    "headRev": "1",
                    "haveRev": "1",
                    "type": "binary+l",
                    "headAction": "add",
                    "openedBy": "bob",
                    "action": "edit",
                    "client": "bob_ws",
                    "lockedBy": "bob",
                    "exclusive": True,
                },
                f"{f}/merge.py": {
                    "depotFile": "//depot/Content/merge.py",
                    "headRev": "1",
                    "haveRev": "1",
                    "type": "text",
                    "headAction": "add",
                    "openedBy": "alice",
                    "client": "alice_ws",
                    "action": "edit",
                    "needsResolve": True,
                    "baseRev": "1",
                    "theirRev": "2",
                },
                f"{f}/Asset.uasset": {
                    "depotFile": "//depot/Content/Asset.uasset",
                    "headRev": "1",
                    "haveRev": "1",
                    "type": "binary",
                    "headAction": "add",
                    "openedBy": "alice",
                    "client": "alice_ws",
                    "action": "edit",
                    "needsResolve": True,
                    "baseRev": "1",
                    "theirRev": "2",
                },
                f"{f}/closed.py": {
                    "depotFile": "//depot/Content/closed.py",
                    "headRev": "1",
                    "haveRev": "1",
                    "type": "text",
                    "headAction": "add",
                    "needsResolve": True,
                },
            },
        }
        service = P4SourceControlService()
        service._runner = FakeP4Runner(self.world)
        self.source_control_service = service
        self.server = create_mcp_server(
            self.database_path,
            source_control_service=self.source_control_service,
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_source_control_tools_registered_with_mode(self) -> None:
        tools = asyncio.run(self.server.list_tools())
        expected = tool_names_for_mode(source_control_enabled=True)
        self.assertEqual([tool.name for tool in tools], expected)
        names = {tool.name: tool for tool in tools}
        for read_tool_name in (
            "ue_source_control_status",
            "ue_source_control_changelists",
            "ue_source_control_resolve_status",
        ):
            self.assertTrue(names[read_tool_name].annotations.readOnlyHint)
            self.assertFalse(names[read_tool_name].annotations.destructiveHint)
        for planning_tool_name in (
            "ue_source_control_prepare_write",
            "ue_source_control_prepare_changelist",
            "ue_source_control_resolve_text",
        ):
            self.assertFalse(names[planning_tool_name].annotations.readOnlyHint)
            self.assertFalse(names[planning_tool_name].annotations.destructiveHint)

    def test_capabilities_advertise_advisory_source_control(self) -> None:
        _, payload = asyncio.run(self.server.call_tool("ue_get_capabilities", {}))
        self.assertTrue(payload["ok"])
        contract = payload["sourceControl"]
        self.assertTrue(contract["configured"])
        self.assertTrue(contract["advisory"])
        self.assertEqual(contract["tools"], SOURCE_CONTROL_TOOL_NAMES)
        self.assertFalse(contract["submitCapability"])
        self.assertFalse(contract["revertCapability"])
        self.assertFalse(contract["deleteCapability"])
        self.assertFalse(contract["arbitraryCommandExecution"])
        self.assertFalse(contract["shellPassthrough"])
        self.assertTrue(contract["providerUnavailableDegradesToAdvisory"])
        self.assertTrue(contract["pendingChangelistPreparation"])
        self.assertTrue(contract["boundedTextResolve"])
        self.assertFalse(contract["binaryAutomaticResolve"])
        names = [item["name"] for item in payload["tools"]]
        self.assertIn("ue_source_control_status", names)
        self.assertIn("ue_source_control_prepare_write", names)
        self.assertIn("ue_source_control_changelists", names)
        self.assertIn("ue_source_control_prepare_changelist", names)
        self.assertIn("ue_source_control_resolve_status", names)
        self.assertIn("ue_source_control_resolve_text", names)

    def test_status_tool_is_read_only_and_structured(self) -> None:
        content, payload = asyncio.run(
            self.server.call_tool("ue_source_control_status", {"paths": [str(self.managed)]})
        )
        self.assertEqual(len(content), 1)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["readOnly"])
        self.assertEqual(payload["fileCount"], 1)
        state = payload["files"][0]
        self.assertTrue(state["mapped"])
        self.assertEqual(state["depotPath"], "//depot/Content/managed.py")
        self.assertTrue(state["localTestReady"])

    def test_status_tool_unmapped(self) -> None:
        _, payload = asyncio.run(
            self.server.call_tool("ue_source_control_status", {"paths": [str(self.unmapped)]})
        )
        state = payload["files"][0]
        self.assertFalse(state["mapped"])
        self.assertTrue(state["providerAvailable"])
        self.assertTrue(state["localTestReady"])

    def test_prepare_write_rejects_shell_and_unknown_arguments(self) -> None:
        for tool_name in (
            "ue_source_control_status",
            "ue_source_control_prepare_write",
            "ue_source_control_changelists",
            "ue_source_control_prepare_changelist",
            "ue_source_control_resolve_status",
            "ue_source_control_resolve_text",
        ):
            with self.subTest(tool=tool_name):
                with self.assertRaises(Exception) as raised:
                    asyncio.run(
                        self.server.call_tool(
                            tool_name,
                            {"paths": [str(self.managed)], "shell": "submit"},
                        )
                    )
                self.assertIn("Extra inputs are not permitted", str(raised.exception))

    def test_validation_errors_use_invalid_arguments(self) -> None:
        content, payload = asyncio.run(
            self.server.call_tool(
                "ue_source_control_status",
                {"paths": [str(self.managed)] * 17},
            )
        )
        self.assertEqual(len(content), 1)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "invalid-arguments")
        self.assertFalse(payload["error"]["retryable"])

    def test_prepare_write_overrides_without_human_capabilities(self) -> None:
        content, payload = asyncio.run(
            self.server.call_tool(
                "ue_source_control_prepare_write",
                {
                    "paths": [str(self.locked)],
                    "allow_local_writable_override": True,
                },
            )
        )
        self.assertEqual(len(content), 1)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["readOnly"])
        override = next((r for r in payload["receipts"] if r["action"] == "override"), None)
        self.assertIsNotNone(override)
        self.assertTrue(override["ok"])
        state = payload["files"][0]
        self.assertTrue(state["localWritableOverride"])
        self.assertFalse(state["submitReady"])
        self.assertFalse(state["openedForEdit"])
        codes = [warning["code"] for warning in state["warnings"]]
        self.assertIn("local-writable-override", codes)

    def test_provider_unavailable_still_returns_advisory_payload(self) -> None:
        self.world["downOn"] = ["info"]
        self.source_control_service.clear_provider_cache()
        _, payload = asyncio.run(
            self.server.call_tool("ue_source_control_status", {"paths": [str(self.managed)]})
        )
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["provider"]["available"])
        state = payload["files"][0]
        self.assertTrue(state["localTestReady"])
        self.assertFalse(state["providerAvailable"])

    # -- C3 MCP tools ---------------------------------------------------------
    def test_changelists_tool_lists_current_client_pending(self) -> None:
        content, payload = asyncio.run(self.server.call_tool("ue_source_control_changelists", {}))
        self.assertEqual(len(content), 1)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["readOnly"])
        self.assertEqual(payload["pendingCount"], 1)
        changelist = payload["changelists"][0]
        self.assertEqual(changelist["changelistId"], "2001")
        self.assertEqual(changelist["description"], "alice review")
        self.assertTrue(changelist["currentUserOwned"])
        self.assertTrue(changelist["currentClientOwned"])
        # No capability exposure in the read surface.
        self.assertNotIn("submitCapability", changelist)
        # invalid changelist id maps to invalid-arguments
        content, payload = asyncio.run(
            self.server.call_tool("ue_source_control_changelists", {"changelist_id": "abc"})
        )
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "invalid-arguments")

    def test_prepare_changelist_tool_creates_cl_and_reopens(self) -> None:
        content, payload = asyncio.run(
            self.server.call_tool(
                "ue_source_control_prepare_changelist",
                {
                    "paths": [str(self.merge_py)],
                    "description": "move managed into review",
                },
            )
        )
        self.assertEqual(len(content), 1)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["changelistId"], "5100")
        self.assertTrue(payload["changelistCreated"])
        self.assertEqual(payload["manualFinalAction"], "none")
        audit = payload["auditReceipt"]
        self.assertFalse(audit["submitCapability"])
        self.assertFalse(audit["revertCapability"])
        self.assertFalse(audit["deleteCapability"])
        state = payload["files"][0]
        self.assertEqual(state["change"], "5100")
        self.assertTrue(state["openedByCurrentClient"])
        # The world now reflects the move.
        self.assertEqual(
            self.world["pendingChanges"]["5100"]["files"], ["//depot/Content/merge.py"]
        )

    def test_prepare_changelist_manual_final_action_is_handoff_metadata(self) -> None:
        _, payload = asyncio.run(
            self.server.call_tool(
                "ue_source_control_prepare_changelist",
                {
                    "paths": [str(self.merge_py)],
                    "description": "prepare for human submit",
                    "manual_final_action": "submit",
                },
            )
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["manualFinalAction"], "submit")
        audit = payload["auditReceipt"]
        self.assertEqual(audit["manualFinalAction"], "submit")
        self.assertFalse(audit["submitCapability"])
        self.assertFalse(audit["revertCapability"])
        self.assertFalse(audit["deleteCapability"])

        _, invalid = asyncio.run(
            self.server.call_tool(
                "ue_source_control_prepare_changelist",
                {
                    "paths": [str(self.managed)],
                    "description": "bad action",
                    "manual_final_action": "execute",
                },
            )
        )
        self.assertFalse(invalid["ok"])
        self.assertEqual(invalid["error"]["code"], "invalid-arguments")

    def test_prepare_changelist_tool_rejects_unowned_cl(self) -> None:
        self.world["pendingChanges"]["7001"] = {
            "status": "pending",
            "user": "bob",
            "client": "bob_ws",
            "description": "bob",
            "files": [],
        }
        content, payload = asyncio.run(
            self.server.call_tool(
                "ue_source_control_prepare_changelist",
                {"paths": [str(self.managed)], "description": "x", "changelist_id": "7001"},
            )
        )
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "invalid-arguments")

    def test_resolve_status_tool_is_read_only(self) -> None:
        content, payload = asyncio.run(
            self.server.call_tool("ue_source_control_resolve_status", {"paths": [str(self.merge_py)]})
        )
        self.assertEqual(len(content), 1)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["readOnly"])
        state = payload["files"][0]
        self.assertTrue(state["needsResolve"])
        self.assertTrue(state["mergeableText"])
        self.assertEqual(payload["summary"]["needsResolve"], 1)
        # binary package flagged without any automatic action
        _, payload = asyncio.run(
            self.server.call_tool(
                "ue_source_control_resolve_status", {"paths": [str(self.binary_asset)]}
            )
        )
        binary = payload["files"][0]
        self.assertTrue(binary["binaryPackage"])
        codes = [warning["code"] for warning in binary["warnings"]]
        self.assertIn("binary-package-resolve-required", codes)

    def test_resolve_text_tool_merges_clean_text_and_refuses_binary(self) -> None:
        _, payload = asyncio.run(
            self.server.call_tool("ue_source_control_resolve_text", {"paths": [str(self.merge_py)]})
        )
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["allResolved"])
        self.assertEqual(payload["receipts"][0]["code"], "resolve-text-ok")
        self.assertEqual(payload["manualFinalAction"], "none")
        self.assertFalse(self.world["files"][self.merge_py.as_posix()].get("needsResolve"))
        # Binary package never enters automatic text resolve.
        _, payload = asyncio.run(
            self.server.call_tool("ue_source_control_resolve_text", {"paths": [str(self.binary_asset)]})
        )
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["binaryReconciliationRequired"])
        self.assertEqual(payload["receipts"][0]["code"], "binary-package-resolve-required")
        self.assertTrue(self.world["files"][self.binary_asset.as_posix()].get("needsResolve"))

    def test_resolve_text_tool_requires_open_in_current_client(self) -> None:
        content, payload = asyncio.run(
            self.server.call_tool("ue_source_control_resolve_text", {"paths": [str(self.closed_py)]})
        )
        self.assertEqual(len(content), 1)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["receipts"][0]["code"], "not-open-current-client")

    def test_provider_unavailable_mutation_degrades_without_fake_success(self) -> None:
        self.world["downOn"] = ["info"]
        self.source_control_service.clear_provider_cache()
        _, payload = asyncio.run(
            self.server.call_tool(
                "ue_source_control_prepare_changelist",
                {"paths": [str(self.managed)], "description": "x"},
            )
        )
        self.assertFalse(payload["provider"]["available"])
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["changelistId"], "")
        codes = [warning["code"] for warning in payload["warnings"]]
        self.assertIn("source-control-unavailable", codes)


if __name__ == "__main__":
    unittest.main()
