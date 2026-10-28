from __future__ import annotations

import asyncio
import contextlib
import io
import json
import socketserver
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any

TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import ue_agent_kit.mcp_server as mcp_server  # noqa: E402
from ue_agent_kit import __version__  # noqa: E402
from ue_agent_kit.database import open_database, set_metadata  # noqa: E402
from ue_agent_kit.editor_bridge import (  # noqa: E402
    LiveEditorBridgeConfig,
    LiveEditorBridgeService,
    LiveEditorError,
)
from ue_agent_kit.mcp_pure_live_tools import (  # noqa: E402
    PURE_LIVE_PIE_TOOL_NAMES,
    PURE_LIVE_READ_TOOL_NAMES,
    build_pure_live_capabilities,
)
from ue_agent_kit.tool_registry import TOOL_DEFINITIONS_BY_NAME  # noqa: E402


def _write_project(root: Path, name: str = "PureLiveHost") -> Path:
    project = root / f"{name}.uproject"
    project.write_text('{ "FileVersion": 3 }', encoding="utf-8")
    return project


def _valid_index(path: Path, project_key: str = "ueak-pure-live-test") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    with open_database(path, readonly=False, migrate=True) as connection:
        set_metadata(connection, "project_key", project_key)
        set_metadata(connection, "last_indexed_at_utc", "2026-10-28T00:00:00.000Z")
        set_metadata(connection, "last_export_utc", "2026-10-28T00:00:00.000Z")
        set_metadata(connection, "last_manifest_schema", "1.1")
        set_metadata(connection, "last_exporter_version", __version__)
        set_metadata(connection, "last_profile", "asset-index")
    return path


def _write_descriptor(project: Path, *, project_name: str, hash_override: str | None = None) -> None:
    config = LiveEditorBridgeConfig(project)
    descriptor = {
        "schemaVersion": "1.0",
        "address": "127.0.0.1",
        "port": 1,
        "authToken": "c" * 48,
        "projectName": project_name,
        "projectPathHash": hash_override or config.project_path_hash,
        "pluginVersion": __version__,
        "capabilities": ["editor.getPieWorldContexts", "editor.inspectPieActor"],
    }
    path = config.descriptor_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(descriptor), encoding="utf-8")


class _FakeLive:
    """Bounded fake with the same surface the live tools use."""

    def __init__(self, project: Path) -> None:
        self.config = LiveEditorBridgeConfig(project, timeout_seconds=1.0)
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def status(self) -> dict[str, Any]:
        return {
            "configured": True,
            "state": "unavailable",
            "reasonCode": "live-editor-unavailable",
        }

    def call_tool(self, tool_name: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((tool_name, params))
        raise LiveEditorError("live-editor-unavailable", "synthetic pure-live fake")

    def call_method(self, method: str, params: dict[str, Any] | None = None, **_: Any) -> dict[str, Any]:
        raise LiveEditorError("live-editor-unavailable", "synthetic pure-live fake")


def _tool_names(server: Any) -> list[str]:
    return sorted(tool.name for tool in asyncio.run(server.list_tools()))


def _call(server: Any, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    _, payload = asyncio.run(server.call_tool(name, arguments))
    return payload


class PureLiveServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="p0pie-pure-live-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = _write_project(self.root)

    def _server(self) -> Any:
        return mcp_server.create_mcp_server(
            None,
            live_editor_service=_FakeLive(self.project),
            pure_live=True,
        )

    def test_pure_live_construction_never_constructs_index_service(self) -> None:
        original = mcp_server.IndexQueryService

        class _Explode:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                raise AssertionError("IndexQueryService must not be constructed in pure-live mode")

        mcp_server.IndexQueryService = _Explode  # type: ignore[assignment]
        try:
            server = self._server()
        finally:
            mcp_server.IndexQueryService = original  # type: ignore[assignment]
        names = _tool_names(server)
        self.assertEqual(names, sorted(PURE_LIVE_READ_TOOL_NAMES))
        self.assertEqual(len(names), 17)

    def test_only_bounded_read_tools_are_registered(self) -> None:
        names = set(_tool_names(self._server()))
        for required in (
            "ue_get_capabilities",
            "ue_get_project_status",
            "ue_editor_status",
            "ue_get_pie_world_contexts",
            "ue_inspect_pie_actor",
            "ue_get_pie_scalars",
            "ue_get_output_log",
            "ue_get_compile_errors",
            "ue_get_focused_blueprint_graph_context",
            "ue_inspect_asset_live",
        ):
            self.assertIn(required, names)
        for forbidden in (
            "ue_search",
            "ue_get_asset",
            "ue_find_references",
            "ue_find_world_actors",
            "ue_get_world_summary",
            "ue_analyze_change_impact",
            "ue_get_task_context",
            "ue_memory_search",
            "ue_apply_asset_property_live",
            "ue_save_authorized_asset",
            "ue_set_pin_default",
            "ue_preview_blueprint_graph_layout",
            "ue_get_editor_context",
            "ue_start_batch_task",
            "ue_analyze_animation_retarget",
            "ue_diagnose_animation_scale",
            "ue_open_asset",
            "ue_focus_asset",
            "ue_compile_blueprint",
            "ue_validate_asset",
            "ue_run_automation_test",
            "ue_focus_blueprint_graph",
        ):
            self.assertNotIn(forbidden, names)

    def test_capability_contract_is_explicit_and_index_free(self) -> None:
        payload = _call(self._server(), "ue_get_capabilities", {})
        self.assertEqual(payload["mode"], "pure-live")
        self.assertTrue(payload["readOnly"])
        self.assertEqual(
            payload["index"],
            {
                "configured": False,
                "required": False,
                "opened": False,
                "state": "not-opened",
                "reasonCode": "pure-live-mode",
            },
        )
        self.assertFalse(payload["offline"]["searchAvailable"])
        self.assertFalse(payload["workflow"]["configured"])
        self.assertFalse(payload["projectMemory"]["configured"])
        self.assertFalse(payload["sourceControl"]["configured"])
        self.assertFalse(payload["liveEditor"]["writeSupported"])
        self.assertFalse(payload["liveEditor"]["serverStartupRequiresIndexDatabase"])
        self.assertEqual(payload["liveEditor"]["tools"], list(PURE_LIVE_READ_TOOL_NAMES))
        self.assertEqual(
            [item["name"] for item in payload["tools"]],
            list(PURE_LIVE_READ_TOOL_NAMES),
        )
        for item in payload["tools"]:
            self.assertTrue(item["readOnly"], item["name"])
            self.assertFalse(item["destructive"], item["name"])
        pie = payload["liveEditor"]["pieRuntimeInspection"]
        self.assertEqual(pie["tools"], list(PURE_LIVE_PIE_TOOL_NAMES))
        self.assertFalse(pie["requiresDatabase"])
        self.assertIn("pie-instance-ambiguous", pie["errorCodes"])
        for flag, value in payload["safety"].items():
            self.assertFalse(value, flag)

    def test_project_status_reports_no_index_and_no_fake_data(self) -> None:
        payload = _call(self._server(), "ue_get_project_status", {})
        self.assertEqual(payload["mode"], "pure-live")
        self.assertFalse(payload["index"]["opened"])
        self.assertFalse(payload["index"]["required"])
        self.assertEqual(payload["project"]["name"], "PureLiveHost")
        self.assertTrue(payload["project"]["pathHash"].startswith("sha1:"))
        self.assertEqual(payload["liveEditor"]["state"], "unavailable")
        self.assertEqual(payload["liveEditor"]["reasonCode"], "live-editor-unavailable")

    def test_missing_editor_returns_clear_unavailable_error(self) -> None:
        server = self._server()
        for tool_name, arguments in (
            ("ue_get_pie_world_contexts", {"max_contexts": 4}),
            ("ue_inspect_pie_actor", {"pie_instance_id": 0, "actor_name": "X"}),
            ("ue_get_selection", {}),
        ):
            payload = _call(server, tool_name, arguments)
            self.assertFalse(payload["ok"], tool_name)
            self.assertEqual(payload["error"]["code"], "live-editor-unavailable", tool_name)
        # The status surface reports availability instead of failing.
        status = _call(server, "ue_editor_status", {})
        self.assertTrue(status["ok"])
        self.assertEqual(status["result"]["state"], "unavailable")
        self.assertEqual(status["result"]["reasonCode"], "live-editor-unavailable")

    def test_no_sqlite_file_is_created_or_modified(self) -> None:
        before = sorted(p.name for p in self.root.rglob("*"))
        server = self._server()
        _call(server, "ue_get_capabilities", {})
        _call(server, "ue_get_pie_world_contexts", {"max_contexts": 2})
        after = sorted(p.name for p in self.root.rglob("*"))
        self.assertEqual(before, after)
        self.assertFalse(list(self.root.rglob("*.sqlite3")))
        self.assertFalse(list(self.root.rglob("*.sqlite")))
        self.assertFalse(list(self.root.rglob("*.db")))

    def test_cross_project_descriptor_is_rejected(self) -> None:
        _write_descriptor(self.project, project_name="SomeoneElsesProject")
        service = LiveEditorBridgeService(
            LiveEditorBridgeConfig(self.project, timeout_seconds=0.5),
            server_version=__version__,
        )
        server = mcp_server.create_mcp_server(None, live_editor_service=service, pure_live=True)
        payload = _call(server, "ue_get_pie_world_contexts", {"max_contexts": 2})
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "live-editor-project-mismatch")

    def test_wrong_project_hash_descriptor_is_rejected(self) -> None:
        _write_descriptor(self.project, project_name=self.project.stem, hash_override="sha1:" + "0" * 40)
        service = LiveEditorBridgeService(
            LiveEditorBridgeConfig(self.project, timeout_seconds=0.5),
            server_version=__version__,
        )
        server = mcp_server.create_mcp_server(None, live_editor_service=service, pure_live=True)
        payload = _call(server, "ue_get_pie_state", {})
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "live-editor-project-mismatch")


class PureLiveValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="p0pie-pure-live-val-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = _write_project(self.root)

    def test_pure_live_rejects_database_path(self) -> None:
        with self.assertRaises(ValueError):
            mcp_server.create_mcp_server(
                _valid_index(self.root / "index.sqlite3"),
                live_editor_service=_FakeLive(self.project),
                pure_live=True,
            )

    def test_pure_live_requires_live_service(self) -> None:
        with self.assertRaises(ValueError):
            mcp_server.create_mcp_server(None, pure_live=True)

    def test_pure_live_rejects_workflow_memory_and_source_control(self) -> None:
        fake = _FakeLive(self.project)
        cases = (
            {"workflow_service": object()},
            {"memory_service": object()},
            {"source_control_service": object()},
            {"experimental_graph_layout_apply": True},
            {"experimental_graphpatch_edit": True},
        )
        for kwargs in cases:
            with self.subTest(kwargs=tuple(kwargs)):
                with self.assertRaises(ValueError):
                    mcp_server.create_mcp_server(None, live_editor_service=fake, pure_live=True, **kwargs)

    def test_ordinary_mode_still_requires_a_valid_index(self) -> None:
        fake = _FakeLive(self.project)
        with self.assertRaises(ValueError):
            mcp_server.create_mcp_server(None, live_editor_service=fake)
        with self.assertRaises(FileNotFoundError):
            mcp_server.create_mcp_server(self.root / "missing.sqlite3", live_editor_service=fake)
        zero = self.root / "zero.sqlite3"
        zero.write_bytes(b"")
        with self.assertRaises(RuntimeError):
            mcp_server.create_mcp_server(zero, live_editor_service=fake)
        wrong = self.root / "wrong.sqlite3"
        connection = sqlite3.connect(wrong)
        connection.execute("PRAGMA user_version = 1")
        connection.execute("CREATE TABLE assets(id)")
        connection.commit()
        connection.close()
        with self.assertRaises(RuntimeError):
            mcp_server.create_mcp_server(wrong, live_editor_service=fake)

    def test_valid_index_still_builds_the_ordinary_server(self) -> None:
        database = _valid_index(self.root / "valid.sqlite3")
        server = mcp_server.create_mcp_server(database, live_editor_service=_FakeLive(self.project))
        names = _tool_names(server)
        self.assertIn("ue_search", names)
        self.assertIn("ue_get_pie_world_contexts", names)
        self.assertGreater(len(names), len(PURE_LIVE_READ_TOOL_NAMES))

    def test_cli_parser_and_pure_live_argument_validation(self) -> None:
        parser = mcp_server.build_parser()
        default = parser.parse_args([])
        self.assertIsNone(default.database)
        self.assertFalse(default.pure_live)

        good = parser.parse_args(
            ["--pure-live", "--enable-live-editor", "--project", str(self.project)]
        )
        mcp_server._validate_pure_live_arguments(good)

        bad_cases = (
            ["--pure-live", "--project", str(self.project)],
            ["--pure-live", "--enable-live-editor"],
            [
                "--pure-live",
                "--enable-live-editor",
                "--project",
                str(self.project),
                "--database",
                str(self.root / "db.sqlite3"),
            ],
            [
                "--pure-live",
                "--enable-live-editor",
                "--project",
                str(self.project),
                "--enable-project-memory",
            ],
            [
                "--pure-live",
                "--enable-live-editor",
                "--project",
                str(self.project),
                "--enable-write-tools",
            ],
            [
                "--pure-live",
                "--enable-live-editor",
                "--project",
                str(self.project),
                "--enable-experimental-graphpatch-edit",
            ],
        )
        for argv in bad_cases:
            with self.subTest(argv=argv):
                with self.assertRaises(ValueError):
                    mcp_server._validate_pure_live_arguments(parser.parse_args(argv))

    def test_cli_check_runs_without_any_database(self) -> None:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = mcp_server.main(
                [
                    "--pure-live",
                    "--enable-live-editor",
                    "--project",
                    str(self.project),
                    "--check",
                ]
            )
        self.assertEqual(code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["mode"], "pure-live")
        self.assertFalse(payload["index"]["opened"])
        self.assertFalse(payload["writeToolsEnabled"])
        self.assertFalse(list(self.root.rglob("*.sqlite3")))

    def test_cli_check_rejects_conflicting_flags(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = mcp_server.main(
                [
                    "--pure-live",
                    "--enable-live-editor",
                    "--project",
                    str(self.project),
                    "--database",
                    str(self.root / "db.sqlite3"),
                    "--check",
                ]
            )
        self.assertEqual(code, 1)
        payload = json.loads(stderr.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "invalid-arguments")


class PureLiveLauncherAndRegistryTests(unittest.TestCase):
    def test_runner_has_explicit_pure_live_switch_and_keeps_default_index_path(self) -> None:
        source = (TOOL_ROOT / "scripts" / "RunMcp.ps1").read_text(encoding="utf-8")
        self.assertIn("[switch]$PureLive", source)
        self.assertIn("--pure-live", source)
        self.assertIn("PureLive requires EnableLiveEditor", source)
        self.assertIn("PureLive does not accept Database", source)
        # Default mode keeps asserting the fixed index and passing it.
        self.assertIn('Assert-UeakPath -Path $Database -Description "UE Agent Kit SQLite index"', source)
        self.assertIn('$Arguments = @($EntryPoint, "--database", $Database)', source)
        self.assertNotIn("Write-Host", source)

    def test_exposed_names_are_real_read_only_registry_entries(self) -> None:
        for name in PURE_LIVE_READ_TOOL_NAMES:
            definition = TOOL_DEFINITIONS_BY_NAME.get(name)
            self.assertIsNotNone(definition, name)
            self.assertTrue(definition.read_only, name)
            self.assertFalse(definition.destructive, name)
        self.assertEqual(len(set(PURE_LIVE_READ_TOOL_NAMES)), len(PURE_LIVE_READ_TOOL_NAMES))
        self.assertNotIn("ue_search", PURE_LIVE_READ_TOOL_NAMES)
        self.assertNotIn("ue_apply_asset_property_live", PURE_LIVE_READ_TOOL_NAMES)
        self.assertNotIn("ue_memory_search", PURE_LIVE_READ_TOOL_NAMES)

    def test_capability_builder_has_no_mutable_or_offline_surface(self) -> None:
        payload = build_pure_live_capabilities(
            server_name="UE Agent Kit",
            server_version=__version__,
            live_status={"configured": True, "state": "available"},
        )
        self.assertEqual(payload["mode"], "pure-live")
        self.assertTrue(payload["liveEditor"]["readOnly"])
        self.assertFalse(payload["liveEditor"]["writeSupported"])
        self.assertFalse(payload["offline"]["configured"])
        self.assertFalse(payload["safety"]["indexOpened"])
        names = {item["name"] for item in payload["tools"]}
        self.assertEqual(names, set(PURE_LIVE_READ_TOOL_NAMES))


class _BridgeServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    version = __version__
    project_name = "PureLiveHost"
    requests: list[dict[str, Any]] = []
    capabilities = ["editor.getPieWorldContexts", "editor.inspectPieActor"]


class _BridgeHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        hello = json.loads(self.rfile.readline().decode("utf-8"))
        self._send(
            hello["requestId"],
            {
                "pluginVersion": self.server.version,  # type: ignore[attr-defined]
                "projectName": self.server.project_name,  # type: ignore[attr-defined]
                "sessionId": "pure-live-session",
                "capabilities": self.server.capabilities,  # type: ignore[attr-defined]
            },
        )
        request = json.loads(self.rfile.readline().decode("utf-8"))
        self.server.requests.append(request)  # type: ignore[attr-defined]
        if request.get("method") == "editor.getPieWorldContexts":
            self._send(
                request["requestId"],
                {
                    "state": "playing",
                    "running": True,
                    "readOnly": True,
                    "requiresDatabase": False,
                    "code": "",
                    "maxContexts": 2,
                    "contextCount": 1,
                    "serverCount": 1,
                    "clientCount": 0,
                    "returnedCount": 1,
                    "truncated": False,
                    "identitiesUnique": True,
                    "pieInstanceIds": [0],
                    "items": [
                        {
                            "contextId": "pie-0-listenServer",
                            "pieInstanceId": 0,
                            "serverOrClient": "server",
                            "isServer": True,
                            "isClient": False,
                            "netModeName": "listenServer",
                            "worldPath": "/Temp/UEDPIE_0_PureLive.PureLive:PersistentLevel",
                        }
                    ],
                },
            )
            return
        self._send_error(request["requestId"], "pie-not-running", "synthetic")

    def _send(self, request_id: str, result: dict[str, Any]) -> None:
        self.wfile.write(
            json.dumps(
                {"schemaVersion": "1.0", "requestId": request_id, "ok": True, "result": result}
            ).encode("utf-8")
            + b"\n"
        )

    def _send_error(self, request_id: str, code: str, message: str) -> None:
        self.wfile.write(
            json.dumps(
                {
                    "schemaVersion": "1.0",
                    "requestId": request_id,
                    "ok": False,
                    "error": {"code": code, "message": message},
                }
            ).encode("utf-8")
            + b"\n"
        )


class PureLiveRealBridgeTests(unittest.TestCase):
    """Real FastMCP tool call over a real localhost bridge, with zero SQLite."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="p0pie-pure-live-bridge-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = _write_project(self.root)
        _BridgeServer.requests = []
        self.server = _BridgeServer(("127.0.0.1", 0), _BridgeHandler)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 2.0)

        config = LiveEditorBridgeConfig(self.project, timeout_seconds=2.0)
        descriptor = {
            "schemaVersion": "1.0",
            "address": "127.0.0.1",
            "port": self.server.server_address[1],
            "authToken": "d" * 48,
            "projectName": config.project_name,
            "projectPathHash": config.project_path_hash,
            "pluginVersion": __version__,
            "capabilities": ["editor.getPieWorldContexts", "editor.inspectPieActor"],
        }
        descriptor_path = config.descriptor_path
        descriptor_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor_path.write_text(json.dumps(descriptor), encoding="utf-8")

    def test_pie_listing_through_real_fastmcp_without_index(self) -> None:
        service = LiveEditorBridgeService(
            LiveEditorBridgeConfig(self.project, timeout_seconds=2.0),
            server_version=__version__,
        )
        server = mcp_server.create_mcp_server(None, live_editor_service=service, pure_live=True)
        before = sorted(p.name for p in self.root.rglob("*"))
        payload = _call(server, "ue_get_pie_world_contexts", {"max_contexts": 2})
        after = sorted(p.name for p in self.root.rglob("*"))
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["result"]["pieInstanceIds"], [0])
        self.assertEqual(payload["result"]["serverCount"], 1)
        self.assertFalse(payload["result"]["requiresDatabase"])
        self.assertEqual(_BridgeServer.requests[-1]["method"], "editor.getPieWorldContexts")
        self.assertEqual(_BridgeServer.requests[-1]["params"], {"maxContexts": 2})
        self.assertEqual(before, after)
        self.assertFalse(list(self.root.rglob("*.sqlite3")))


if __name__ == "__main__":
    unittest.main()
