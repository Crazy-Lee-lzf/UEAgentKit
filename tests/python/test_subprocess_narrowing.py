"""0-3.5: the workflow subprocess group is default-off, ledgered, and no fallback.

Four guards:
1. registration -- the group only exists when both gates are open;
2. refusal      -- a blocked attempt is ``workflow-subprocess-disabled`` and never runs;
3. allow        -- with the gates open the runner is called and the ledger has a pid;
4. no fallback  -- a failing new-path patch keeps its own error code and spawns nothing.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
TESTS_ROOT = Path(__file__).resolve().parent
for directory in (str(SRC_ROOT), str(TESTS_ROOT)):
    if directory not in sys.path:
        sys.path.insert(0, directory)

from test_indexer_queries import make_generic_asset, write_export  # noqa: E402

from ue_agent_kit.agent_api import IndexQueryService  # noqa: E402
from ue_agent_kit.agent_workflow import PatchWorkflowConfig, PatchWorkflowService, WorkflowError  # noqa: E402
from ue_agent_kit.database import open_database  # noqa: E402
from ue_agent_kit.editor_bridge import LiveEditorError  # noqa: E402
from ue_agent_kit.indexer import build_index  # noqa: E402
from ue_agent_kit.mcp_server import create_mcp_server  # noqa: E402
from ue_agent_kit.subprocess_ledger import get_session_ledger, reset_session_ledger  # noqa: E402
from ue_agent_kit.workflow_common import (  # noqa: E402
    ProcessResult,
    subprocess_workflow_enabled_for_project,
)

ASSET = "/Game/N1/BP_A.BP_A"
DISABLED_CODE = "workflow-subprocess-disabled"


class _FakeRunner:
    def __init__(self, pid: int = 4242, error: BaseException | None = None) -> None:
        self.pid = pid
        self.error = error
        self.calls: list[list[str]] = []

    def __call__(self, arguments: list[str], cwd: Path, timeout_seconds: int) -> ProcessResult:
        self.calls.append(list(arguments))
        if self.error is not None:
            raise self.error
        return ProcessResult(0, "stdout", "", 12.5, self.pid)


def _layout(root: Path) -> dict[str, Path]:
    tool_root = root / "Tool"
    engine_root = root / "Engine"
    project_root = root / "Project"
    (tool_root / "scripts").mkdir(parents=True)
    (tool_root / "Output").mkdir()
    (tool_root / "Backups").mkdir()
    for script in ("RunPatch.ps1", "RunRollback.ps1", "RunAssetCatalog.ps1"):
        (tool_root / "scripts" / script).write_text("", encoding="utf-8")
    binaries = engine_root / "Engine" / "Binaries" / "Win64"
    binaries.mkdir(parents=True)
    (binaries / "UnrealEditor-Cmd.exe").write_bytes(b"")
    project_path = project_root / "Game.uproject"
    project_path.parent.mkdir(parents=True)
    project_path.write_bytes(b"{}")
    policy_path = project_root / "policy.json"
    policy_path.write_text(
        json.dumps({"schemaVersion": "1.0", "allowedProjectNames": ["Game"]}),
        encoding="utf-8",
    )
    return {
        "tool_root": tool_root,
        "engine_root": engine_root,
        "project_path": project_path,
        "policy_path": policy_path,
        "work_root": tool_root / "Output" / "McpWorkflow",
        "backup_root": tool_root / "Backups" / "McpBackup",
    }


def _config(paths: dict[str, Path], *, enabled: bool) -> PatchWorkflowConfig:
    return PatchWorkflowConfig(
        tool_root=paths["tool_root"],
        engine_root=paths["engine_root"],
        project_path=paths["project_path"],
        policy_path=paths["policy_path"],
        revision_export=None,
        work_root=paths["work_root"],
        backup_root=paths["backup_root"],
        subprocess_enabled=enabled,
    )


def _index(database_path: Path) -> IndexQueryService:
    export_root = database_path.parent / "export"
    write_export(export_root, [make_generic_asset()])
    with open_database(database_path) as connection:
        build_index(connection, export_root, database_path)
    return IndexQueryService(database_path)


class SubprocessWorkflowGateTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_session_ledger()
        self._tmp = tempfile.TemporaryDirectory(prefix="ueak_subprocess_")
        self.root = Path(self._tmp.name)
        self.paths = _layout(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()
        reset_session_ledger()

    def test_registration_requires_both_gates(self) -> None:
        index = _index(self.root / "index" / "ueak.sqlite3")
        disabled = create_mcp_server(
            self.root / "index" / "ueak.sqlite3",
            workflow_config=_config(self.paths, enabled=False),
        )
        disabled_names = {tool.name for tool in asyncio.run(disabled.list_tools())}
        self.assertNotIn("ue_set_pin_default", disabled_names)
        self.assertNotIn("ue_apply_patch", disabled_names)
        self.assertIn("ue_get_subprocess_ledger", disabled_names)

        enabled = create_mcp_server(
            self.root / "index" / "ueak.sqlite3",
            workflow_config=_config(self.paths, enabled=True),
        )
        enabled_names = {tool.name for tool in asyncio.run(enabled.list_tools())}
        self.assertIn("ue_set_pin_default", enabled_names)
        self.assertIn("ue_apply_patch", enabled_names)
        self.assertGreater(len(enabled_names), len(disabled_names))
        self.assertIsNotNone(index)

    def test_disabled_gate_blocks_before_the_command_line(self) -> None:
        runner = _FakeRunner()
        service = PatchWorkflowService(
            _index(self.root / "index" / "ueak.sqlite3"),
            _config(self.paths, enabled=False),
            process_runner=runner,
        )
        with self.assertRaises(WorkflowError) as raised:
            service._run_script("RunPatch.ps1", [], stage="plan", report_path=self.root / "r.json")
        self.assertEqual(raised.exception.code, DISABLED_CODE)
        self.assertIn("--enable-subprocess-workflow", str(raised.exception))
        self.assertEqual(runner.calls, [])
        stats = get_session_ledger().stats()
        self.assertEqual(stats["spawnedCount"], 0)
        self.assertEqual(stats["blockedCount"], 1)
        self.assertEqual(stats["entries"][0]["outcome"], "blocked")
        self.assertEqual(stats["entries"][0]["errorCode"], DISABLED_CODE)
        self.assertIsNone(stats["entries"][0]["pid"])

    def test_enabled_gate_records_a_spawn_with_pid(self) -> None:
        runner = _FakeRunner(pid=999)
        service = PatchWorkflowService(
            _index(self.root / "index" / "ueak.sqlite3"),
            _config(self.paths, enabled=True),
            process_runner=runner,
        )
        result = service._run_script(
            "RunPatch.ps1",
            [],
            stage="plan",
            report_path=self.root / "r.json",
        )
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(len(runner.calls), 1)
        stats = get_session_ledger().stats()
        self.assertEqual(stats["spawnedCount"], 1)
        self.assertEqual(stats["blockedCount"], 0)
        entry = stats["entries"][0]
        self.assertEqual(entry["outcome"], "spawned")
        self.assertEqual(entry["pid"], 999)
        self.assertEqual(entry["commandlet"], "plan")
        self.assertEqual(entry["errorCode"], "")

    def test_ini_gate_requires_an_explicit_true(self) -> None:
        ini = self.paths["project_path"].parent / "Config" / "DefaultEngine.ini"
        ini.parent.mkdir(parents=True)
        self.assertFalse(subprocess_workflow_enabled_for_project(self.paths["project_path"]))
        ini.write_text("[UEAgentKit]\nSubprocessWorkflow=False\n", encoding="utf-8")
        self.assertFalse(subprocess_workflow_enabled_for_project(self.paths["project_path"]))
        ini.write_text("[UEAgentKit]\nSubprocessWorkflow=True\n", encoding="utf-8")
        self.assertTrue(subprocess_workflow_enabled_for_project(self.paths["project_path"]))


class _Server:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, *, annotations: Any = None):
        def decorator(function):
            self.tools[function.__name__] = function
            return function

        return decorator


class _LiveEditor:
    """A live Editor whose patch Apply is fault-injected."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def status(self) -> dict[str, Any]:
        return {
            "state": "available",
            "capabilities": [
                "editor.previewBlueprintPatchExperimental",
                "editor.applyBlueprintPatchExperimental",
                "editor.saveBlueprintPatchExperimental",
            ],
        }

    def call_method(self, method: str, params: Any = None, *, timeout_seconds: Any = None) -> dict[str, Any]:
        self.calls.append(method)
        raise LiveEditorError("graph-patch-plan-stale", "Injected stale plan.")


def _error_response(tool: str, exc: BaseException, *, read_only: bool) -> dict[str, Any]:
    return {
        "ok": False,
        "tool": tool,
        "readOnly": read_only,
        "error": {"code": getattr(exc, "code", "error"), "message": str(exc)},
    }


class _Annotations:
    def __init__(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)


class NoFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_session_ledger()

    def tearDown(self) -> None:
        reset_session_ledger()

    def test_failing_patch_keeps_its_code_and_spawns_nothing(self) -> None:
        from ue_agent_kit.mcp_patch_experimental import (
            APPLY_TOOL_NAME,
            register_patch_experimental_tools,
        )

        server = _Server()
        register_patch_experimental_tools(
            server=server,
            live_editor_service=_LiveEditor(),
            tool_annotations_type=_Annotations,
            error_response=_error_response,
        )
        response = server.tools[APPLY_TOOL_NAME](
            plan_id="sha256:" + "a" * 64,
            asset_path=ASSET,
            graph_guid="11111111-2222-4333-8444-555555555555",
            operations=[
                {
                    "opId": "p1",
                    "kind": "setPinDefault",
                    "nodeGuid": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
                    "pinName": "A",
                    "pinGuid": "11111111-2222-4333-8444-555555555555",
                    "type": "bool",
                    "value": "true",
                }
            ],
            confirmation="SET EXPERIMENTAL BLUEPRINT PATCH " + "sha256:" + "a" * 64,
        )
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "graph-patch-plan-stale")
        stats = get_session_ledger().stats()
        self.assertEqual(stats["count"], 0)
        self.assertEqual(stats["spawnedCount"], 0)


if __name__ == "__main__":
    unittest.main()
