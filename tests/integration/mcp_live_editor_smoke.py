from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.tool_registry import TOOL_DEFINITIONS_BY_NAME, tool_names_for_mode  # noqa: E402
SRC_ROOT = TOOL_ROOT / "src"
PYTHON_TESTS = TOOL_ROOT / "tests" / "python"
for search_path in (SRC_ROOT, PYTHON_TESTS):
    if str(search_path) not in sys.path:
        sys.path.insert(0, str(search_path))

from test_indexer_queries import (  # noqa: E402
    ASSET_A,
    REVISION_A,
    make_asset,
    make_generic_asset,
    write_export,
)
from ue_agent_kit.database import open_database  # noqa: E402
from ue_agent_kit.indexer import build_index  # noqa: E402

EXPECTED_TOOLS = tool_names_for_mode(live_editor_enabled=True)
LIVE_TOOLS = EXPECTED_TOOLS[5:]
READ_LIVE_TOOLS = [name for name in LIVE_TOOLS if TOOL_DEFINITIONS_BY_NAME[name].read_only]
ACTION_LIVE_TOOLS = [name for name in LIVE_TOOLS if not TOOL_DEFINITIONS_BY_NAME[name].read_only]



def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot(directory: Path) -> dict[str, str]:
    return {path.name: _sha256(path) for path in directory.iterdir() if path.is_file()}


def _assert_validation_evidence(
    result: dict[str, Any],
    *,
    scope: str,
    project_path_hash: str,
    editor_session_id: str,
    expected_revision_count: int | None,
) -> dict[str, Any]:
    evidence = result.get("validationEvidence")
    if not isinstance(evidence, dict):
        raise RuntimeError(f"Validation evidence is missing: {result}")
    if (
        evidence.get("schemaVersion") != "1.0"
        or not str(evidence.get("evidenceId", "")).startswith("validation:")
        or evidence.get("source") != "tool-observed"
        or evidence.get("scope") != scope
        or evidence.get("projectPathHash") != project_path_hash
        or evidence.get("editorSessionId") != editor_session_id
        or not str(evidence.get("startedAtUtc", "")).endswith("Z")
        or not str(evidence.get("completedAtUtc", "")).endswith("Z")
        or evidence.get("observedAtUtc") != evidence.get("completedAtUtc")
    ):
        raise RuntimeError(f"Validation evidence identity is invalid: {evidence}")
    revisions = evidence.get("revisionSet")
    if not isinstance(revisions, list):
        raise RuntimeError(f"Validation Revision Set is invalid: {evidence}")
    if expected_revision_count is None:
        if evidence.get("revisionCoverage") != "not-applicable" or revisions:
            raise RuntimeError(f"Automation Revision coverage is invalid: {evidence}")
    else:
        if len(revisions) != expected_revision_count:
            raise RuntimeError(f"Validation Revision Set size is invalid: {evidence}")
        for revision in revisions:
            if (
                not str(revision.get("revision", "")).startswith("sha256:")
                or revision.get("revision") != revision.get("revisionAfter")
                or revision.get("revisionAvailable") is not True
                or revision.get("revisionStable") is not True
            ):
                raise RuntimeError(f"Validation Revision entry is invalid: {revision}")
        expected_coverage = "complete" if evidence.get("dirtyAssetCount") == 0 else "partial"
        if evidence.get("revisionCoverage") != expected_coverage:
            raise RuntimeError(f"Validation Revision coverage is invalid: {evidence}")
    return evidence


async def _run(
    database: Path,
    project: Path,
    error_log: Path,
    actor_guid: str = "",
    startup_map: str = "",
) -> dict[str, Any]:
    before_hash = _sha256(database)
    before_files = _snapshot(database.parent)
    target_package = project.parent / "Content" / "UEAgentKitWriteTests" / "BP_PatchTarget.uasset"
    if not target_package.is_file():
        raise RuntimeError(f"Live action fixture package is missing: {target_package}")
    target_package_hash_before = _sha256(target_package)
    map_package: Path | None = None
    map_package_hash_before = ""
    if startup_map:
        if not startup_map.startswith("/Game/") or "." in startup_map or ".." in startup_map:
            raise RuntimeError(f"Invalid startup map package path: {startup_map}")
        map_package = project.parent / "Content" / Path(*startup_map.removeprefix("/Game/").split("/"))
        map_package = map_package.with_suffix(".umap")
        if not map_package.is_file():
            raise RuntimeError(f"Startup map package is missing: {map_package}")
        map_package_hash_before = _sha256(map_package)
    descriptor_path = project.parent / "Saved" / "UEAgentKit" / "EditorBridge.json"
    descriptor = json.loads(descriptor_path.read_text(encoding="utf-8-sig"))
    secret_token = str(descriptor["authToken"])
    secret_port = str(descriptor["port"])
    descriptor_session_id = str(descriptor["sessionId"])
    descriptor_project_path_hash = str(descriptor["projectPathHash"])
    descriptor_process_id = int(descriptor["processId"])
    descriptor_hash_before = _sha256(descriptor_path)
    parameters = StdioServerParameters(
        command="powershell.exe",
        args=[
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(TOOL_ROOT / "scripts" / "RunMcp.ps1"),
            "-Database",
            str(database),
            "-EnableLiveEditor",
            "-ProjectPath",
            str(project),
            "-LiveEditorTimeoutSeconds",
            "30",
        ],
        cwd=TOOL_ROOT,
        encoding="utf-8",
        encoding_error_handler="replace",
    )
    with error_log.open("w", encoding="utf-8", newline="\n") as stderr:
        async with stdio_client(parameters, errlog=stderr) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                initialized = await session.initialize()
                listed = await session.list_tools()
                capabilities_result = await session.call_tool("ue_get_capabilities", {})
                project_status_result = await session.call_tool("ue_get_project_status", {})
                live_results = {
                    tool: (await session.call_tool(tool, {})).structuredContent
                    for tool in LIVE_TOOLS[:6]
                }
                live_results["ue_get_output_log"] = (
                    await session.call_tool(
                        "ue_get_output_log",
                        {"minimum_verbosity": "warning", "since_sequence": 0, "limit": 50},
                    )
                ).structuredContent
                live_results["ue_get_compile_errors"] = (
                    await session.call_tool(
                        "ue_get_compile_errors",
                        {"since_sequence": 0, "limit": 50},
                    )
                ).structuredContent
                live_results["ue_inspect_asset_live"] = (
                    await session.call_tool(
                        "ue_inspect_asset_live",
                        {"asset_path": "/Game/UEAgentKitWriteTests/BP_PatchTarget.BP_PatchTarget"},
                    )
                ).structuredContent
                live_results["ue_get_blueprint_graph_selection"] = (
                    await session.call_tool("ue_get_blueprint_graph_selection", {})
                ).structuredContent
                action_results = {}
                target_asset = "/Game/UEAgentKitWriteTests/BP_PatchTarget.BP_PatchTarget"
                action_results["ue_sync_content_browser"] = (
                    await session.call_tool(
                        "ue_sync_content_browser",
                        {"asset_path": target_asset},
                    )
                ).structuredContent
                action_results["ue_open_asset"] = (
                    await session.call_tool("ue_open_asset", {"asset_path": target_asset})
                ).structuredContent
                action_results["ue_focus_asset"] = (
                    await session.call_tool("ue_focus_asset", {"asset_path": target_asset})
                ).structuredContent
                action_results["ue_compile_blueprint"] = (
                    await session.call_tool("ue_compile_blueprint", {"asset_path": target_asset})
                ).structuredContent
                action_results["ue_validate_asset"] = (
                    await session.call_tool(
                        "ue_validate_asset",
                        {"asset_path": target_asset, "max_issues": 50},
                    )
                ).structuredContent
                action_results["ue_validate_folder"] = (
                    await session.call_tool(
                        "ue_validate_folder",
                        {
                            "package_path": "/Game/UEAgentKitWriteTests",
                            "recursive": True,
                            "max_assets": 100,
                            "max_issues": 50,
                        },
                    )
                ).structuredContent
                action_results["ue_run_automation_test"] = (
                    await session.call_tool(
                        "ue_run_automation_test",
                        {
                            "test_name": "UEAgentKit.EditorBridge.LiveActionSmoke",
                            "timeout_seconds": 30,
                            "max_entries": 50,
                        },
                    )
                ).structuredContent
                post_automation_status = (
                    await session.call_tool("ue_editor_status", {})
                ).structuredContent
                action_results["ue_focus_actor"] = (
                    await session.call_tool(
                        "ue_focus_actor",
                        {"actor_guid": "11111111-1111-1111-1111-111111111111"},
                    )
                ).structuredContent
                if actor_guid:
                    action_results["ue_focus_actor_existing"] = (
                        await session.call_tool(
                            "ue_focus_actor",
                            {"actor_guid": actor_guid},
                        )
                    ).structuredContent

    tool_names = [tool.name for tool in listed.tools]
    if tool_names != EXPECTED_TOOLS:
        raise RuntimeError(f"Unexpected Live Editor Tool list: {tool_names}")
    for tool in listed.tools:
        properties = set(tool.inputSchema.get("properties", {}))
        forbidden = {
            "address",
            "port",
            "auth_token",
            "token",
            "endpoint",
            "project",
            "project_path",
            "filesystem_path",
            "uobject",
            "console",
            "python",
            "shell",
        }
        if properties.intersection(forbidden):
            raise RuntimeError(f"Tool exposes fixed or arbitrary bridge configuration: {tool.name} {properties}")
        if tool.name in LIVE_TOOLS:
            definition = TOOL_DEFINITIONS_BY_NAME[tool.name]
            if (
                not tool.annotations
                or tool.annotations.readOnlyHint != definition.read_only
                or tool.annotations.destructiveHint != definition.destructive
            ):
                raise RuntimeError(f"Live Tool annotations do not match the Registry: {tool.name} {tool.annotations}")

    capabilities = capabilities_result.structuredContent
    project_status = project_status_result.structuredContent
    if not capabilities or not capabilities["liveEditor"]["configured"]:
        raise RuntimeError(f"Live Editor capability discovery failed: {capabilities}")
    if capabilities["liveEditor"]["tools"] != LIVE_TOOLS:
        raise RuntimeError(f"Live Editor capability Tool list mismatch: {capabilities}")
    if capabilities["liveEditor"]["arbitraryEndpointArguments"]:
        raise RuntimeError("Live Editor capabilities claim arbitrary endpoint arguments")
    if not project_status or project_status["liveEditor"]["state"] != "available":
        raise RuntimeError(f"Live Editor project status failed: {project_status}")
    editor_status = live_results["ue_editor_status"]
    if not editor_status or not editor_status["ok"] or editor_status["result"]["state"] != "available":
        raise RuntimeError(f"ue_editor_status failed: {editor_status}")
    if editor_status["result"]["projectName"] != project.stem:
        raise RuntimeError(f"Live Editor project mismatch: {editor_status}")
    if editor_status["result"]["pluginVersion"] != capabilities["server"]["version"]:
        raise RuntimeError(f"Live Editor version mismatch: {editor_status}")
    for tool in READ_LIVE_TOOLS[1:]:
        payload = live_results[tool]
        if not payload or not payload["ok"] or payload["source"] != "live-editor-memory":
            raise RuntimeError(f"Live Tool failed: {tool} {payload}")
    current_level = live_results["ue_get_current_level"]["result"]
    if not current_level.get("available") or not current_level.get("currentLevelPath"):
        raise RuntimeError(f"Current level result is incomplete: {current_level}")
    pie_state = live_results["ue_get_pie_state"]["result"]
    if pie_state.get("state") not in {"stopped", "playing", "simulating"}:
        raise RuntimeError(f"PIE state is invalid: {pie_state}")

    output_log = live_results["ue_get_output_log"]["result"]
    if not output_log.get("available") or output_log.get("nextSequence") is None:
        raise RuntimeError(f"Output Log result is incomplete: {output_log}")
    if output_log.get("resultCount", 0) > 50 or output_log.get("matchedCount", 0) < output_log.get("resultCount", 0):
        raise RuntimeError(f"Output Log bounds are invalid: {output_log}")

    compile_errors = live_results["ue_get_compile_errors"]["result"]
    if compile_errors.get("diagnosticSource") != "captured-output-log" or compile_errors.get("historyComplete") is not False:
        raise RuntimeError(f"Compile diagnostic provenance is invalid: {compile_errors}")

    live_asset = live_results["ue_inspect_asset_live"]["result"]
    if live_asset.get("assetPath") != "/Game/UEAgentKitWriteTests/BP_PatchTarget.BP_PatchTarget":
        raise RuntimeError(f"Live asset path is invalid: {live_asset}")
    if live_asset.get("memory", {}).get("loadedByBridge") is not False:
        raise RuntimeError(f"Live asset inspection claimed it loaded the asset: {live_asset}")

    graph_selection = live_results["ue_get_blueprint_graph_selection"]["result"]
    if graph_selection.get("scope") != "ordinary-blueprint-editor":
        raise RuntimeError(f"Graph selection scope is invalid: {graph_selection}")
    if graph_selection.get("loadedByBridge") is not False:
        raise RuntimeError(f"Graph selection claimed it loaded an asset: {graph_selection}")
    if graph_selection.get("available"):
        graph = graph_selection.get("graph", {})
        if not graph.get("graphGuid") or graph_selection.get("selectedNodeCount", 0) > 100:
            raise RuntimeError(f"Graph selection result is incomplete or unbounded: {graph_selection}")
    elif graph_selection.get("reasonCode") not in {
        "no-ordinary-blueprint-editor",
        "no-focused-blueprint-graph",
        "blueprint-asset-unavailable",
    }:
        raise RuntimeError(f"Graph selection fallback is invalid: {graph_selection}")

    if ACTION_LIVE_TOOLS != [
        "ue_open_asset",
        "ue_focus_asset",
        "ue_sync_content_browser",
        "ue_focus_actor",
        "ue_compile_blueprint",
        "ue_validate_asset",
        "ue_validate_folder",
        "ue_run_automation_test",
    ]:
        raise RuntimeError(f"Unexpected Live Action Tool order: {ACTION_LIVE_TOOLS}")
    sync_result = action_results["ue_sync_content_browser"]
    if not sync_result or not sync_result.get("ok") or sync_result["result"].get("loadedByBridge") is not False:
        raise RuntimeError(f"Content Browser sync loaded or failed: {sync_result}")
    open_result = action_results["ue_open_asset"]
    if not open_result or not open_result.get("ok") or not open_result["result"].get("openAfter"):
        raise RuntimeError(f"Asset open failed: {open_result}")
    focus_result = action_results["ue_focus_asset"]
    if not focus_result or not focus_result.get("ok") or not focus_result["result"].get("focused"):
        raise RuntimeError(f"Asset focus failed: {focus_result}")
    compile_result = action_results["ue_compile_blueprint"]
    if not compile_result or not compile_result.get("ok") or not compile_result["result"].get("compiled"):
        raise RuntimeError(f"Blueprint compile did not execute: {compile_result}")
    if compile_result["result"].get("saved") is not False:
        raise RuntimeError(f"Blueprint compile claimed to save: {compile_result}")
    validate_asset = action_results["ue_validate_asset"]
    if not validate_asset or not validate_asset.get("ok") or validate_asset["result"].get("numRequested") != 1:
        raise RuntimeError(f"Single-asset validation failed: {validate_asset}")
    asset_evidence = _assert_validation_evidence(
        validate_asset["result"],
        scope="asset",
        project_path_hash=descriptor_project_path_hash,
        editor_session_id=descriptor_session_id,
        expected_revision_count=1,
    )
    if asset_evidence["revisionSet"][0]["revision"] != f"sha256:{target_package_hash_before}":
        raise RuntimeError(f"Single-asset validation Revision does not match the package: {asset_evidence}")
    validate_folder = action_results["ue_validate_folder"]
    if not validate_folder or not validate_folder.get("ok") or validate_folder["result"].get("matchedAssetCount", 0) < 1:
        raise RuntimeError(f"Folder validation failed: {validate_folder}")
    folder_evidence = _assert_validation_evidence(
        validate_folder["result"],
        scope="folder",
        project_path_hash=descriptor_project_path_hash,
        editor_session_id=descriptor_session_id,
        expected_revision_count=validate_folder["result"]["matchedAssetCount"],
    )
    automation = action_results["ue_run_automation_test"]
    if (
        not automation
        or not automation.get("ok")
        or automation["result"].get("testName") != "UEAgentKit.EditorBridge.LiveActionSmoke"
        or automation["result"].get("state") != "success"
        or automation["result"].get("successful") is not True
        or automation["result"].get("isolatedProcess") is not True
        or automation["result"].get("exitCode") != 0
        or automation["result"].get("timedOut") is not False
        or automation["result"].get("saved") is not False
    ):
        raise RuntimeError(f"Exact Automation Test action failed: {automation}")
    automation_evidence = _assert_validation_evidence(
        automation["result"],
        scope="automation",
        project_path_hash=descriptor_project_path_hash,
        editor_session_id=descriptor_session_id,
        expected_revision_count=None,
    )
    if automation_evidence.get("executionIsolation") != "isolated-unreal-editor-cmd":
        raise RuntimeError(f"Automation evidence isolation is invalid: {automation_evidence}")
    if (
        not post_automation_status
        or not post_automation_status.get("ok")
        or post_automation_status["result"].get("state") != "available"
        or post_automation_status["result"].get("processId") != descriptor_process_id
        or post_automation_status["result"].get("sessionId") != descriptor_session_id
        or post_automation_status["result"].get("processId") != editor_status["result"].get("processId")
        or post_automation_status["result"].get("sessionId") != editor_status["result"].get("sessionId")
    ):
        raise RuntimeError(
            f"Main Editor Bridge did not survive isolated Automation unchanged: {post_automation_status}"
        )
    if not descriptor_path.is_file() or _sha256(descriptor_path) != descriptor_hash_before:
        raise RuntimeError("Isolated Automation replaced or modified the main Editor Bridge descriptor")
    actor_failure = action_results["ue_focus_actor"]
    if not actor_failure or actor_failure.get("ok") or actor_failure["error"]["code"] != "live-editor-actor-not-found":
        raise RuntimeError(f"Missing ActorGuid was not rejected predictably: {actor_failure}")

    actor_success = action_results.get("ue_focus_actor_existing")
    if actor_guid:
        if (
            not actor_success
            or not actor_success.get("ok")
            or not actor_success["result"].get("selected")
            or not actor_success["result"].get("viewportFocused")
            or actor_success["result"].get("actorGuid") != actor_guid.lower()
            or actor_success["result"].get("dirtyPackageCountChanged") is not False
        ):
            raise RuntimeError(f"Existing ActorGuid was not selected and framed: {actor_success}")

    response_text = json.dumps(
        {
            "capabilities": capabilities,
            "projectStatus": project_status,
            "live": live_results,
            "actions": action_results,
        },
        ensure_ascii=False,
    )
    for secret in (secret_token, secret_port, str(descriptor_path), str(project.resolve())):
        if secret and secret in response_text:
            raise RuntimeError("MCP Live Editor responses exposed fixed endpoint, token, or local path")
    if "authToken" in response_text:
        raise RuntimeError("MCP Live Editor responses exposed descriptor authentication fields")
    if _sha256(database) != before_hash or _snapshot(database.parent) != before_files:
        raise RuntimeError("Live Editor MCP actions modified the immutable SQLite index")
    if _sha256(target_package) != target_package_hash_before:
        raise RuntimeError("Live Editor Daily Actions changed the fixture package on disk")
    if map_package is not None and _sha256(map_package) != map_package_hash_before:
        raise RuntimeError("Actor focus changed the startup map package on disk")

    return {
        "protocolVersion": initialized.protocolVersion,
        "serverName": initialized.serverInfo.name,
        "serverVersion": capabilities["server"]["version"],
        "tools": tool_names,
        "projectName": editor_status["result"]["projectName"],
        "engineVersion": editor_status["result"]["engineVersion"],
        "editorProcessId": editor_status["result"]["processId"],
        "liveSessionIdPresent": bool(editor_status["result"]["sessionId"]),
        "currentLevelAvailable": bool(current_level["available"]),
        "pieState": pie_state["state"],
        "selectionCount": live_results["ue_get_selection"]["result"]["count"],
        "openAssetCount": live_results["ue_get_open_assets"]["result"]["count"],
        "dirtyPackageCount": live_results["ue_get_dirty_assets"]["result"]["count"],
        "outputLogResultCount": output_log["resultCount"],
        "outputLogNextSequence": output_log["nextSequence"],
        "compileDiagnosticCount": compile_errors["diagnosticCount"],
        "compileHistoryComplete": compile_errors["historyComplete"],
        "liveAssetRegistryFound": live_asset["assetRegistry"]["found"],
        "liveAssetLoaded": live_asset["memory"]["loaded"],
        "liveAssetLoadedByBridge": live_asset["memory"]["loadedByBridge"],
        "blueprintGraphSelectionAvailable": bool(graph_selection.get("available")),
        "blueprintGraphSelectionReason": graph_selection.get("reasonCode", ""),
        "blueprintSelectedNodeCount": graph_selection.get("selectedNodeCount", 0),
        "contentBrowserSyncNoLoad": sync_result["result"]["loadedByBridge"] is False,
        "assetOpened": bool(open_result["result"]["openAfter"]),
        "assetFocused": bool(focus_result["result"]["focused"]),
        "blueprintCompileResult": compile_result["result"]["result"],
        "blueprintPackageDirtyAfter": bool(compile_result["result"].get("packageDirtyAfter")),
        "singleAssetValidationResult": validate_asset["result"]["result"],
        "singleAssetEvidenceCoverage": asset_evidence["revisionCoverage"],
        "singleAssetEvidenceRevision": asset_evidence["revisionSet"][0]["revision"],
        "folderEvidenceCoverage": folder_evidence["revisionCoverage"],
        "folderEvidenceRevisionCount": len(folder_evidence["revisionSet"]),
        "automationEvidenceCoverage": automation_evidence["revisionCoverage"],
        "folderValidationChecked": validate_folder["result"]["numChecked"],
        "automationTestState": automation["result"]["state"],
        "automationTestEntryCount": automation["result"]["entryCount"],
        "automationIsolatedProcess": automation["result"]["isolatedProcess"],
        "automationExitCode": automation["result"]["exitCode"],
        "mainEditorSurvivedAutomation": True,
        "missingActorRejected": actor_failure["error"]["code"] == "live-editor-actor-not-found",
        "existingActorFocused": bool(actor_success and actor_success.get("ok")),
        "secretsRedacted": True,
        "databaseHashUnchanged": True,
        "indexDirectoryUnchanged": True,
        "fixturePackageHashUnchanged": True,
        "startupMapHashUnchanged": map_package is None or _sha256(map_package) == map_package_hash_before,
        "serverLogLines": len(error_log.read_text(encoding="utf-8").splitlines()),
    }


def _build_temporary_index(root: Path) -> Path:
    index_root = root / "index"
    index_root.mkdir(parents=True)
    database = index_root / "ueak-live.sqlite3"
    export_root = root / "export"
    write_export(
        export_root,
        [
            make_generic_asset(),
            make_asset(ASSET_A, profile="logic", revision=REVISION_A, rich=True),
        ],
    )
    with open_database(database) as connection:
        result = build_index(connection, export_root, database)
    if (result.added, result.failed) != (2, 0):
        raise RuntimeError(f"Unexpected temporary index result: {result}")
    return database


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--error-log", type=Path)
    parser.add_argument("--actor-guid", default="")
    parser.add_argument("--startup-map", default="")
    args = parser.parse_args()
    project = args.project.resolve()
    with tempfile.TemporaryDirectory(prefix="ueak_mcp_live_") as temporary_root:
        root = Path(temporary_root)
        database = args.database.resolve() if args.database is not None else _build_temporary_index(root)
        error_log = (
            args.error_log.resolve()
            if args.error_log is not None
            else root / "logs" / "server-stderr.log"
        )
        error_log.parent.mkdir(parents=True, exist_ok=True)
        report = asyncio.run(
            _run(database, project, error_log, args.actor_guid, args.startup_map)
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
