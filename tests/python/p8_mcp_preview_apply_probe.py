#!/usr/bin/env python3
"""P8 real MCP preview, explicit apply, Undo/Redo, separate Save, and readback.

The server is launched against the exact disposable P8AutomationHost with the
hidden P8 write flag.  The preview tool is the public read-only surface; the
write tool accepts only a canonical position-only request plus its exact
confirmation.  Save is a separate explicit harness call after the MCP apply.
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
)
from ue_agent_kit.p8_test_only import (  # noqa: E402
    APPLY_TOOL_NAME,
    FIXED_ASSET_PATH,
    SAVE_CONFIRMATION,
    confirmation_for_plan,
)

PREFLIGHT_TOOL = "ue_get_blueprint_graph_layout_preflight"
PREVIEW_TOOL = "ue_preview_blueprint_graph_layout"
P7_ASSET_PATH = "/Game/UEAgentKitP7/BP_LayoutP7.BP_LayoutP7"


class ProbeError(RuntimeError):
    pass


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
            "--enable-p8-test-only-graph-layout-apply",
        ],
        cwd=ROOT,
        env=env,
        encoding="utf-8",
        encoding_error_handler="replace",
    )
    error_log = args.out / "p8-mcp-server.stderr.log"
    error_log.parent.mkdir(parents=True, exist_ok=True)
    with error_log.open("w", encoding="utf-8", newline="\n") as log:
        async with stdio_client(parameters, errlog=log) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                initialized = await session.initialize()
                tools = await session.list_tools()
                names = [tool.name for tool in tools.tools]
                _require(PREVIEW_TOOL in names, "public preview tool was not registered")
                _require(APPLY_TOOL_NAME in names, "P8 test-only apply tool was not registered")
                _require("ue_apply_blueprint_graph_layout" not in names, "general graph-layout write tool exists")
                preview_tool = next(tool for tool in tools.tools if tool.name == PREVIEW_TOOL)
                apply_tool = next(tool for tool in tools.tools if tool.name == APPLY_TOOL_NAME)
                _require(preview_tool.annotations is not None, "preview tool has no annotations")
                _require(apply_tool.annotations is not None, "P8 apply tool has no annotations")
                _require(preview_tool.annotations.readOnlyHint is True, "preview tool is not read-only")
                _require(preview_tool.annotations.destructiveHint is False, "preview tool is destructive")
                _require(apply_tool.annotations.readOnlyHint is False, "P8 apply is marked read-only")
                _require(apply_tool.annotations.destructiveHint is True, "P8 apply is not destructive")

                capabilities = _tool_payload(await session.call_tool("ue_get_capabilities", {}))
                live_caps = capabilities.get("liveEditor", {})
                preview_cap = live_caps.get("graphLayoutPreview", {})
                p8_cap = live_caps.get("p8TestOnlyGraphLayoutApply", {})
                _require(preview_cap.get("available") is True, "preview capability is unavailable")
                _require(preview_cap.get("writesBlueprint") is False, "preview capability claims a write")
                _require(p8_cap.get("registered") is True, "P8 capability does not report registration")
                _require(p8_cap.get("publicGeneralWrite") is False, "P8 capability claims a general write")

                preflight_payload = _tool_payload(await session.call_tool(PREFLIGHT_TOOL, {}))
                _require(preflight_payload.get("ok") is True, f"preflight failed: {preflight_payload}")
                preflight = preflight_payload.get("result")
                _require(isinstance(preflight, dict), "preflight result is missing")
                _require(preflight.get("assetPath") == FIXED_ASSET_PATH, "preflight asset is not P8")
                _require(preflight.get("packageDirty") is False, "P8 fixture is not clean")
                _require(preflight.get("complete") is True, "P8 preflight is truncated")
                _require(preflight.get("pieState") == "stopped", "P8 fixture is not stopped")
                _write_json(args.out / "p8-bridge-preflight.json", preflight_payload)

                before = _positions(preflight)
                preview_payload = _tool_payload(
                    await session.call_tool(
                        PREVIEW_TOOL,
                        {
                            "mode": "full",
                            "spacing_x": 96,
                            "spacing_y": 64,
                            "target_width": 1800,
                            "seed": 0,
                        },
                    )
                )
                _write_json(args.out / "p8-mcp-preview-response.json", preview_payload)
                _require(preview_payload.get("ok") is True, f"preview failed: {preview_payload}")
                _require(preview_payload.get("readOnly") is True, "preview response is not read-only")
                preview = preview_payload.get("result")
                _require(isinstance(preview, dict), "preview result is missing")
                _require(preview.get("wroteBlueprint") is False, "preview wrote a Blueprint")
                _require(preview.get("savedPackage") is False, "preview saved a package")
                _require(preview.get("assetPath") == FIXED_ASSET_PATH, "preview asset mismatch")
                _require(preview.get("graphGuid") == preflight.get("graphGuid"), "preview graph mismatch")
                _require(preview.get("packageRevision") == preflight.get("packageRevision"), "preview revision mismatch")
                _require(preview.get("graphFingerprint") == preflight.get("graphFingerprint"), "preview fingerprint mismatch")
                _require(preview.get("nodeCount") == preflight.get("nodeCount"), "preview node count mismatch")
                _require(preview.get("movedCount", 0) > 0, "preview moved no node")
                _require(preview.get("acceptanceEligible") is False, "preview claims acceptance")
                _write_json(args.out / "p8-preview-result.json", preview)

                after_preview = _positions(
                    _tool_payload(await session.call_tool(PREFLIGHT_TOOL, {})).get("result")
                )
                _require(after_preview == before, "preview changed live node positions")

                plan = preview.get("plan")
                _require(isinstance(plan, dict), "preview has no full LayoutPlan")
                try:
                    request = build_graph_layout_apply_request(plan, preflight)
                except GraphLayoutApplyError as exc:
                    raise ProbeError(f"apply request rejected: {exc.code}: {exc}") from exc
                _write_json(args.out / "p8-layout-plan.json", plan)
                request_path = args.out / "p8-apply-request.json"
                _write_json(request_path, request)

                negative: dict[str, Any] = {}

                wrong_asset = copy.deepcopy(request)
                wrong_asset["assetPath"] = P7_ASSET_PATH
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

                unchanged = _positions(
                    _tool_payload(await session.call_tool(PREFLIGHT_TOOL, {})).get("result")
                )
                _require(unchanged == before, "a rejected write changed node positions")

                applied_payload = _tool_payload(
                    await session.call_tool(
                        APPLY_TOOL_NAME,
                        {"request": request, "confirmation": confirmation_for_plan(plan["planId"])},
                    )
                )
                _write_json(args.out / "p8-mcp-apply-response.json", applied_payload)
                _require(applied_payload.get("ok") is True, f"P8 MCP apply failed: {applied_payload}")
                _require(applied_payload.get("readOnly") is False, "P8 apply claims read-only")
                _require(applied_payload.get("destructive") is True, "P8 apply is not destructive")
                applied = applied_payload.get("result")
                _require(isinstance(applied, dict), "P8 apply result is missing")
                _require(applied.get("testOnly") is True, "P8 apply is not test-only")
                _require(applied.get("changed") is True, "P8 apply did not change positions")
                _require(applied.get("movedNodeCount") == len(plan["movedNodeIds"]), "P8 moved count mismatch")
                _require(applied.get("singleTransaction") is True, "P8 apply is not one transaction")
                _require(applied.get("undoVerified") is True, "P8 Undo verification failed")
                _require(applied.get("redoVerified") is True, "P8 Redo verification failed")
                _require(applied.get("structuralInvariant") is True, "P8 changed graph structure")
                _require(applied.get("topologyHashBefore") == applied.get("topologyHashAfter"), "P8 topology changed")
                _require(applied.get("saved") is False, "P8 apply auto-saved")
                _require(applied.get("compiled") is False, "P8 apply auto-compiled")
                _require(applied.get("packageDirtyAfterRedo") is True, "P8 apply did not dirty the package")

                target_positions = {
                    str(item["nodeId"]): (int(item["x"]), int(item["y"]))
                    for item in plan["positions"]
                }
                after_apply_payload = _tool_payload(await session.call_tool(PREFLIGHT_TOOL, {}))
                after_apply = after_apply_payload.get("result")
                _require(isinstance(after_apply, dict), "post-apply preflight missing")
                _require(after_apply.get("packageDirty") is True, "post-apply package is not dirty")
                _require(_positions(after_apply) == target_positions, "post-apply live positions do not equal the plan")

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

                service = _service(args.project)
                save_result = service.call_method(
                    "editor.saveBlueprintGraphLayoutP8TestOnly",
                    {"confirmation": SAVE_CONFIRMATION},
                )
                _require(save_result.get("saved") is True, "explicit P8 harness save failed")
                _require(save_result.get("packageDirty") is False, "P8 save left the package dirty")

                saved_preflight = service.call_method("editor.getBlueprintGraphLayoutPreflight", {})
                _require(saved_preflight.get("packageDirty") is False, "saved preflight is dirty")
                _require(_positions(saved_preflight) == target_positions, "saved positions do not equal the plan")
                post_revision = str(saved_preflight["packageRevision"])
                post_fingerprint = str(saved_preflight["graphFingerprint"])
                asset_path = (
                    args.project.parent
                    / "Content"
                    / "UEAgentKitP8"
                    / "BP_LayoutP8.uasset"
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
                    "schemaVersion": "ueak.p8.apply/1.0",
                    "ok": True,
                    "transport": "stdio-mcp",
                    "tool": APPLY_TOOL_NAME,
                    "previewTool": PREVIEW_TOOL,
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
                    "previewMetrics": preview.get("metrics", {}),
                    "previewWarnings": preview.get("warnings", []),
                    "negative": negative,
                    "applyResponse": applied,
                }
                _write_json(args.out / "p8-apply-result.json", result)
                summary = {
                    "schemaVersion": "ueak.p8.mcp/1.0",
                    "ok": True,
                    "toolCount": len(names),
                    "toolNames": names,
                    "previewToolRegistered": True,
                    "applyToolRegistered": True,
                    "generalWriteToolRegistered": False,
                    "destructive": True,
                    "readOnly": False,
                    "previewCapability": preview_cap,
                    "writeCapability": p8_cap,
                    "previewSha256": _sha256_file(args.out / "p8-mcp-preview-response.json"),
                    "applySha256": _sha256_file(args.out / "p8-mcp-apply-response.json"),
                    "resultSha256": _sha256_file(args.out / "p8-apply-result.json"),
                    "requestSha256": _sha256_file(request_path),
                    "planSha256": _sha256_file(args.out / "p8-layout-plan.json"),
                }
                _write_json(args.out / "p8-mcp-result.json", summary)
                return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        summary = asyncio.run(run(args))
    except Exception as exc:  # noqa: BLE001 - CLI reports the exact boundary
        import traceback

        traceback.print_exc()
        print(f"P8 MCP preview/apply probe failed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "toolCount": summary["toolCount"],
                "previewToolRegistered": True,
                "applyToolRegistered": True,
                "resultSha256": summary["resultSha256"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
