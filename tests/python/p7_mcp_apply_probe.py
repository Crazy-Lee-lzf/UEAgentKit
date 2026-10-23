#!/usr/bin/env python3
"""P7 real MCP stdio test-only write, undo/redo, explicit save, and readback.

The server is launched against the exact disposable P7AutomationHost project
with one hidden lab flag.  The MCP tool itself only accepts a canonical
position-only request and its confirmation phrase; project, asset, save, and
compile targets are not client parameters.  Save is a separate explicit
harness call after the MCP apply.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mcp import ClientSession  # noqa: E402
from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: E402

from ue_agent_kit import __version__  # noqa: E402
from ue_agent_kit.editor_bridge import (  # noqa: E402
    LiveEditorBridgeConfig,
    LiveEditorBridgeService,
)
from ue_agent_kit.graph_contracts import (  # noqa: E402
    GraphLayoutApplyError,
    build_graph_layout_apply_request,
    preview_graph_layout,
    typed_graph_from_blueprint_context,
)
from ue_agent_kit.graph_contracts.layout import recompute_layout_hash  # noqa: E402
from ue_agent_kit.p7_test_only import (  # noqa: E402
    APPLY_TOOL_NAME,
    FIXED_ASSET_PATH,
    SAVE_CONFIRMATION,
    confirmation_for_plan,
)

TOOL_NAME = "ue_get_blueprint_graph_layout_preflight"
PLAN_OPTIONS = {"spacingX": 96, "spacingY": 64, "seed": 0}
PACKING = {"targetWidth": 1800}
P6_ASSET_PATH = "/Game/UEAgentKitP6/BP_LayoutP6.BP_LayoutP6"


class ProbeError(RuntimeError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProbeError(f"missing JSON file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ProbeError(f"invalid JSON file: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProbeError(f"expected a JSON object: {path}")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _require(value: bool, message: str) -> None:
    if not value:
        raise ProbeError(message)


def _service(project: Path) -> LiveEditorBridgeService:
    return LiveEditorBridgeService(
        LiveEditorBridgeConfig(project_path=project, timeout_seconds=10.0),
        server_version=__version__,
    )


def _positions(preflight: Mapping[str, Any]) -> dict[str, tuple[int, int]]:
    nodes = preflight.get("nodes")
    _require(isinstance(nodes, list), "preflight nodes are not an array")
    return {
        str(node["nodeId"]): (int(node["x"]), int(node["y"]))
        for node in nodes
        if isinstance(node, dict) and "nodeId" in node
    }


def _tool_payload(result: Any) -> dict[str, Any]:
    payload = getattr(result, "structuredContent", None)
    if not isinstance(payload, dict):
        raise ProbeError(f"MCP tool returned no structured content: {payload}")
    return payload


def _error_code(payload: Mapping[str, Any]) -> str:
    error = payload.get("error")
    return str(error.get("code", "")) if isinstance(error, dict) else ""


def _build_graph(
    context: Mapping[str, Any],
    metadata: Mapping[str, Any],
    preflight: Mapping[str, Any],
) -> dict[str, Any]:
    _require(preflight.get("assetPath") == metadata.get("assetPath"), "bridge/export assetPath mismatch")
    _require(preflight.get("graphGuid") == metadata.get("graphGuid"), "bridge/export graphGuid mismatch")
    _require(preflight.get("packageRevision") == metadata.get("packageRevision"), "bridge/export revision mismatch")
    _require(
        preflight.get("graphFingerprint") == metadata.get("graphFingerprint"),
        "bridge/export fingerprint mismatch",
    )
    _require(preflight.get("nodeCount") == metadata.get("nodeCount"), "bridge/export node count mismatch")
    _require(preflight.get("packageDirty") is False, "P7 fixture is not clean")
    _require(preflight.get("complete") is True, "P7 preflight is truncated")
    _require(preflight.get("pieState") == "stopped", "P7 fixture is not stopped")
    _require(preflight.get("assetPath") == FIXED_ASSET_PATH, "P7 preflight asset is not the fixed asset")
    source_metadata = {
        "kind": "live-editor",
        "sessionId": preflight.get("sessionId", ""),
        "pluginBuildHash": preflight.get("pluginBuildHash", ""),
        "exportedAtUtc": metadata.get("exportedAtUtc", ""),
    }
    graph = typed_graph_from_blueprint_context(
        context,
        graph_guid=str(metadata["graphGuid"]),
        source_metadata=source_metadata,
    )
    _require(graph["asset"]["path"] == preflight["assetPath"], "imported graph asset mismatch")
    _require(graph["asset"]["revision"] == preflight["packageRevision"], "imported graph revision mismatch")
    _require(graph["graph"]["guid"] == preflight["graphGuid"], "imported graph GUID mismatch")
    return graph


def _plan_and_request(
    graph: Mapping[str, Any],
    preflight: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    plan_result = preview_graph_layout(
        graph,
        {"mode": "full", "options": dict(PLAN_OPTIONS), "packing": dict(PACKING)},
    )
    _require(plan_result.status == "ok", f"layout rejected: {plan_result.errors}")
    _require(plan_result.plan is not None, "layout returned no plan")
    plan = plan_result.plan
    _require(plan["layoutHash"] == recompute_layout_hash(plan), "plan layoutHash is not canonical")
    _require(plan["baseGraphHash"] == graph["semanticHash"], "plan semantic base is not the imported graph")
    _require(bool(plan["movedNodeIds"]), "layout did not move any node")
    _require(len(plan["positions"]) == len(graph["nodes"]), "layout did not position every node")
    try:
        request = build_graph_layout_apply_request(plan, preflight)
    except GraphLayoutApplyError as exc:
        raise ProbeError(f"apply request rejected: {exc.code}: {exc}") from exc
    _require(request["assetPath"] == preflight["assetPath"], "request asset mismatch")
    _require(request["graphGuid"] == preflight["graphGuid"], "request graph GUID mismatch")
    _require(
        request["expectedPackageRevision"] == preflight["packageRevision"],
        "request revision mismatch",
    )
    _require(
        request["expectedGraphFingerprint"] == preflight["graphFingerprint"],
        "request fingerprint mismatch",
    )
    return plan, request


async def run(args: argparse.Namespace) -> dict[str, Any]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "ue_agent_kit.mcp_server",
            "--database",
            str(args.database),
            "--enable-live-editor",
            "--project",
            str(args.project),
            "--enable-p7-test-only-graph-layout-apply",
        ],
        cwd=ROOT,
        env=env,
        encoding="utf-8",
        encoding_error_handler="replace",
    )
    error_log = args.out / "p7-mcp-server.stderr.log"
    error_log.parent.mkdir(parents=True, exist_ok=True)
    with error_log.open("w", encoding="utf-8", newline="\n") as log:
        async with stdio_client(parameters, errlog=log) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                initialized = await session.initialize()
                tools = await session.list_tools()
                names = [tool.name for tool in tools.tools]
                _require(APPLY_TOOL_NAME in names, "P7 test-only apply tool was not registered")
                _require("ue_apply_blueprint_graph_layout" not in names, "general graph-layout write tool exists")
                apply_tool = next(tool for tool in tools.tools if tool.name == APPLY_TOOL_NAME)
                _require(apply_tool.annotations is not None, "P7 tool has no annotations")
                _require(apply_tool.annotations.readOnlyHint is False, "P7 tool is marked read-only")
                _require(apply_tool.annotations.destructiveHint is True, "P7 tool is not marked destructive")

                capabilities = _tool_payload(await session.call_tool("ue_get_capabilities", {}))
                contract = capabilities.get("liveEditor", {}).get("p7TestOnlyGraphLayoutApply", {})
                _require(contract.get("registered") is True, "P7 capability does not report registration")
                _require(contract.get("publicGeneralWrite") is False, "P7 capability claims a general write")

                preflight_payload = _tool_payload(await session.call_tool(TOOL_NAME, {}))
                _require(preflight_payload.get("ok") is True, f"P7 read preflight failed: {preflight_payload}")
                preflight = preflight_payload.get("result")
                _require(isinstance(preflight, dict), "P7 preflight result is missing")
                _write_json(args.out / "p7-bridge-preflight.json", preflight_payload)

                context = _read_json(args.context)
                metadata = _read_json(args.metadata)
                expected = _read_json(args.expected)
                _require(_sha256_file(args.context) == metadata.get("contextSha256"), "exporter context hash changed")
                _require(_sha256_file(args.preflight) == metadata.get("preflightSha256"), "preflight hash changed")
                _require(
                    preflight.get("pluginBuildHash") == metadata.get("pluginBuildHash"),
                    "bridge/export plugin build mismatch",
                )
                _require(preflight.get("packageRevision") == expected.get("packageRevision"), "fixture revision mismatch")
                _require(preflight.get("graphFingerprint") == expected.get("graphFingerprint"), "fixture fingerprint mismatch")
                _require(preflight.get("topologyHash") is None, "preflight must not invent topologyHash")

                graph = _build_graph(context, metadata, preflight)
                plan, request = _plan_and_request(graph, preflight)
                plan_path = args.out / "p7-layout-plan.json"
                request_path = args.out / "p7-apply-request.json"
                _write_json(plan_path, plan)
                _write_json(request_path, request)

                before = _positions(preflight)
                negative: dict[str, Any] = {}

                wrong_asset = copy.deepcopy(request)
                wrong_asset["assetPath"] = P6_ASSET_PATH
                payload = _tool_payload(
                    await session.call_tool(
                        APPLY_TOOL_NAME,
                        {"request": wrong_asset, "confirmation": confirmation_for_plan(plan["planId"])},
                    )
                )
                negative["wrongAsset"] = {"ok": payload.get("ok"), "code": _error_code(payload)}
                _require(payload.get("ok") is False, "wrong-asset apply unexpectedly succeeded")
                _require(_error_code(payload) == "graph-layout-test-only-asset-rejected", "wrong-asset code changed")

                wrong_hash = copy.deepcopy(request)
                wrong_hash["plan"]["layoutHash"] = "sha256:" + ("0" * 64)
                payload = _tool_payload(
                    await session.call_tool(
                        APPLY_TOOL_NAME,
                        {"request": wrong_hash, "confirmation": confirmation_for_plan(plan["planId"])},
                    )
                )
                negative["alteredLayoutHash"] = {"ok": payload.get("ok"), "code": _error_code(payload)}
                _require(payload.get("ok") is False, "altered-layoutHash apply unexpectedly succeeded")
                _require(
                    _error_code(payload) == "graph-layout-layout-hash-mismatch",
                    f"layoutHash code changed: {payload}",
                )

                stale = copy.deepcopy(request)
                stale["expectedPackageRevision"] = "sha256:" + ("1" * 64)
                payload = _tool_payload(
                    await session.call_tool(
                        APPLY_TOOL_NAME,
                        {"request": stale, "confirmation": confirmation_for_plan(plan["planId"])},
                    )
                )
                negative["staleRevision"] = {"ok": payload.get("ok"), "code": _error_code(payload)}
                _require(payload.get("ok") is False, "stale apply unexpectedly succeeded")
                _require(_error_code(payload) == "graph-layout-revision-conflict", "stale code changed")

                payload = _tool_payload(
                    await session.call_tool(
                        APPLY_TOOL_NAME,
                        {"request": request, "confirmation": "WRONG CONFIRMATION"},
                    )
                )
                negative["wrongConfirmation"] = {"ok": payload.get("ok"), "code": _error_code(payload)}
                _require(payload.get("ok") is False, "wrong-confirmation apply unexpectedly succeeded")
                _require(
                    _error_code(payload) == "graph-layout-test-only-confirmation-required",
                    "confirmation code changed",
                )

                unchanged = _positions(preflight)
                _require(unchanged == before, "a rejected write changed node positions")

                applied_payload = _tool_payload(
                    await session.call_tool(
                        APPLY_TOOL_NAME,
                        {"request": request, "confirmation": confirmation_for_plan(plan["planId"])},
                    )
                )
                _write_json(args.out / "p7-mcp-apply-response.json", applied_payload)
                _require(applied_payload.get("ok") is True, f"P7 MCP apply failed: {applied_payload}")
                _require(applied_payload.get("readOnly") is False, "P7 apply claims read-only")
                _require(applied_payload.get("destructive") is True, "P7 apply is not destructive")
                applied = applied_payload.get("result")
                _require(isinstance(applied, dict), "P7 apply result is missing")
                _require(applied.get("testOnly") is True, "P7 apply is not test-only")
                _require(applied.get("changed") is True, "P7 apply did not change positions")
                _require(applied.get("movedNodeCount") == len(plan["movedNodeIds"]), "P7 moved count mismatch")
                _require(applied.get("singleTransaction") is True, "P7 apply is not one transaction")
                _require(applied.get("undoVerified") is True, "P7 Undo verification failed")
                _require(applied.get("redoVerified") is True, "P7 Redo verification failed")
                _require(applied.get("structuralInvariant") is True, "P7 changed graph structure")
                _require(applied.get("topologyHashBefore") == applied.get("topologyHashAfter"), "P7 topology changed")
                _require(applied.get("saved") is False, "P7 apply auto-saved")
                _require(applied.get("compiled") is False, "P7 apply auto-compiled")
                _require(applied.get("packageDirtyAfterRedo") is True, "P7 apply did not dirty the package")

                after_apply_payload = _tool_payload(await session.call_tool(TOOL_NAME, {}))
                after_apply = after_apply_payload.get("result")
                _require(isinstance(after_apply, dict), "post-apply preflight missing")
                _require(after_apply.get("packageDirty") is True, "post-apply package is not dirty")
                _require(
                    _positions(after_apply) == {str(p["nodeId"]): (int(p["x"]), int(p["y"])) for p in plan["positions"]},
                    "post-apply live positions do not equal the plan",
                )

                payload = _tool_payload(
                    await session.call_tool(
                        APPLY_TOOL_NAME,
                        {"request": request, "confirmation": confirmation_for_plan(plan["planId"])},
                    )
                )
                negative["dirtySecondApply"] = {"ok": payload.get("ok"), "code": _error_code(payload)}
                _require(payload.get("ok") is False, "dirty second apply unexpectedly succeeded")
                _require(
                    _error_code(payload) == "graph-layout-test-only-preflight-rejected",
                    "dirty second-apply code changed",
                )
                after_dirty_payload = _tool_payload(await session.call_tool(TOOL_NAME, {}))
                after_dirty = after_dirty_payload.get("result")
                _require(isinstance(after_dirty, dict), "post-dirty-failure preflight missing")
                _require(
                    _positions(after_dirty)
                    == {str(p["nodeId"]): (int(p["x"]), int(p["y"])) for p in plan["positions"]},
                    "dirty failure moved positions",
                )

                service = _service(args.project)
                save_result = service.call_method(
                    "editor.saveBlueprintGraphLayoutTestOnly",
                    {"confirmation": SAVE_CONFIRMATION},
                )
                _require(save_result.get("saved") is True, "explicit P7 harness save failed")
                _require(save_result.get("packageDirty") is False, "P7 save left the package dirty")

                saved_preflight = service.call_method("editor.getBlueprintGraphLayoutPreflight", {})
                _require(saved_preflight.get("packageDirty") is False, "saved preflight is dirty")
                _require(
                    _positions(saved_preflight)
                    == {str(p["nodeId"]): (int(p["x"]), int(p["y"])) for p in plan["positions"]},
                    "saved positions do not equal the plan",
                )
                post_revision = str(saved_preflight["packageRevision"])
                post_fingerprint = str(saved_preflight["graphFingerprint"])
                asset_path = (
                    args.project.parent
                    / "Content"
                    / "UEAgentKitP7"
                    / "BP_LayoutP7.uasset"
                )
                _require(_sha256_file(asset_path) == post_revision, "saved revision is not the on-disk asset hash")

                targets = [
                    {
                        "nodeId": str(position["nodeId"]),
                        "x": int(position["x"]),
                        "y": int(position["y"]),
                    }
                    for position in plan["positions"]
                ]
                result = {
                    "schemaVersion": "ueak.p7.apply/1.0",
                    "ok": True,
                    "transport": "stdio-mcp",
                    "tool": APPLY_TOOL_NAME,
                    "toolCount": len(names),
                    "serverName": getattr(initialized, "serverInfo", None).name
                    if getattr(initialized, "serverInfo", None) is not None
                    else "",
                    "assetPath": FIXED_ASSET_PATH,
                    "graphGuid": preflight["graphGuid"],
                    "planId": plan["planId"],
                    "layoutHash": plan["layoutHash"],
                    "nodeCount": len(targets),
                    "movedNodeCount": applied["movedNodeCount"],
                    "preRevision": preflight["packageRevision"],
                    "postRevision": post_revision,
                    "preFingerprint": preflight["graphFingerprint"],
                    "postFingerprint": post_fingerprint,
                    "topologyHash": applied["topologyHashBefore"],
                    "topologyInvariant": applied["topologyHashBefore"] == applied["topologyHashAfter"],
                    "undoVerified": applied["undoVerified"],
                    "redoVerified": applied["redoVerified"],
                    "singleTransaction": applied["singleTransaction"],
                    "saved": True,
                    "savePolicy": "explicit-separate-harness-save-after-mcp-apply",
                    "saveResult": save_result,
                    "assetSha256": post_revision,
                    "targets": targets,
                    "planPositions": plan["positions"],
                    "beforePositions": applied["positionsBefore"],
                    "afterPositions": applied["positionsAfter"],
                    "negative": negative,
                    "applyResponse": applied,
                }
                _write_json(args.out / "p7-apply-result.json", result)
                summary = {
                    "schemaVersion": "ueak.p7.mcp/1.0",
                    "ok": True,
                    "toolCount": len(names),
                    "toolNames": names,
                    "applyToolRegistered": True,
                    "generalWriteToolRegistered": False,
                    "destructive": True,
                    "readOnly": False,
                    "capability": contract,
                    "applySha256": _sha256_file(args.out / "p7-mcp-apply-response.json"),
                    "resultSha256": _sha256_file(args.out / "p7-apply-result.json"),
                    "requestSha256": _sha256_file(request_path),
                    "planSha256": _sha256_file(plan_path),
                }
                _write_json(args.out / "p7-mcp-result.json", summary)
                return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--expected", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        summary = asyncio.run(run(args))
    except Exception as exc:  # noqa: BLE001 - CLI reports the exact boundary
        import traceback

        traceback.print_exc()
        print(f"P7 MCP apply probe failed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "toolCount": summary["toolCount"],
                "applyToolRegistered": True,
                "resultSha256": summary["resultSha256"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
