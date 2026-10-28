#!/usr/bin/env python3
"""Real MCP A-path for the fixed safe CallFunction allowlist."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import p5_agent_e2e_probe as p5  # noqa: E402
import connect_existing_pin_probe as cp  # noqa: E402
from ue_agent_kit.experimental_graph_edit import CALL_FUNCTION_ALLOWLIST  # noqa: E402
from mcp import ClientSession  # noqa: E402
from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: E402

ASSET_PATH = cp.ASSET_PATH


def _call_node(
    temp_id: str,
    function_name: str,
    x: int,
    y: int,
) -> dict[str, Any]:
    entry = CALL_FUNCTION_ALLOWLIST[function_name]
    return {
        "tempId": temp_id,
        "nodeType": "call_function",
        "x": x,
        "y": y,
        "callFunction": {
            "ownerClassPath": entry["ownerClassPath"],
            "functionName": function_name,
            "signatureDigest": entry["signatureDigest"],
            "pins": [dict(pin) for pin in entry["pins"]],
        },
    }


def _branch_with_unlinked_condition(manifest: Mapping[str, Any]) -> dict[str, Any]:
    for node in manifest.get("nodes") or []:
        if not isinstance(node, Mapping) or not str(node.get("class", "")).endswith(
            "K2Node_IfThenElse"
        ):
            continue
        for pin in node.get("pins") or []:
            if (
                isinstance(pin, Mapping)
                and pin.get("pinName") == "Condition"
                and pin.get("direction") == "input"
                and int(pin.get("linkCount", -1)) == 0
            ):
                return dict(node)
    raise p5.ProbeError("fixture has no Branch with unlinked Condition", code="fixture-shape")


def _node_by_guid(graph: Mapping[str, Any], guid: str) -> dict[str, Any]:
    for node in graph.get("nodes") or []:
        if isinstance(node, Mapping) and str(node.get("guid")).lower() == guid.lower():
            return dict(node)
    raise p5.ProbeError(f"node {guid} missing", code="node-missing")


def _pin(node: Mapping[str, Any], name: str, direction: str) -> dict[str, Any]:
    for pin in node.get("pins") or []:
        if (
            isinstance(pin, Mapping)
            and pin.get("name") == name
            and pin.get("direction") == direction
        ):
            return dict(pin)
    raise p5.ProbeError(f"pin {name}/{direction} missing", code="pin-missing")


async def _expect_preview_error(
    driver: cp.ConnectDriver,
    label: str,
    nodes: list[dict[str, Any]],
    connections: list[dict[str, Any]],
    graph_guid: str,
    expected_code: str,
) -> dict[str, Any]:
    payload = await driver.call(
        p5.GRAPH_EDIT_PREVIEW,
        {
            "assetPath": ASSET_PATH,
            "graphGuid": graph_guid,
            "nodes": nodes,
            "connections": connections,
        },
    )
    actual = p5._error_code(payload)
    p5._require(
        payload.get("ok") is False and actual == expected_code,
        f"{label} expected {expected_code}, got ok={payload.get('ok')} code={actual}",
        code="negative-mismatch",
    )
    driver.record(label, errorCode=actual)
    return {"label": label, "errorCode": actual}


async def _run(args: argparse.Namespace) -> int:
    project = args.project.resolve()
    project_root = project.parent
    report_root = args.out.resolve()
    report_root.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    server_project = args.project
    if args.mode == "cross-project":
        if args.wrong_project is None:
            raise SystemExit("--wrong-project is required for --mode cross-project")
        server_project = args.wrong_project
    server_args = [
        "-m",
        "ue_agent_kit.mcp_server",
        "--enable-live-editor",
        "--enable-experimental-graphpatch-edit",
        "--project",
        str(server_project.resolve()),
        "--database",
        str(args.database.resolve()),
        "--live-editor-timeout-seconds",
        "30",
    ]
    report: dict[str, Any] = {
        "schemaVersion": "ueak.blueprint-safe-callfunction-report/1.0",
        "mode": args.mode,
        "assetPath": ASSET_PATH,
        "ok": False,
        "steps": [],
        "negatives": [],
    }
    driver: cp.ConnectDriver | None = None
    try:
        params = StdioServerParameters(
            command=sys.executable,
            args=server_args,
            env=env,
            cwd=str(ROOT),
        )
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=timedelta(seconds=600),
            ) as session:
                await session.initialize()
                tools = {tool.name for tool in (await session.list_tools()).tools}
                required = {p5.GRAPH_EDIT_PREVIEW, p5.GRAPH_EDIT_APPLY, p5.GRAPH_EDIT_SAVE}
                missing = sorted(required - tools)
                p5._require(not missing, f"MCP server did not register: {missing}", code="tool-missing")
                report["toolCount"] = len(tools)
                driver = cp.ConnectDriver(session, project_root)

                if args.mode == "cross-project":
                    payload = await driver.call(p5.OPEN_ASSET, {"asset_path": ASSET_PATH})
                    code = p5._error_code(payload)
                    p5._require(
                        payload.get("ok") is False and code == "live-editor-project-mismatch",
                        f"cross-project guard did not reject: ok={payload.get('ok')} code={code}",
                        code="cross-project-guard",
                    )
                    report["crossProjectError"] = {"code": code}
                    report["steps"] = driver.steps
                    report["ok"] = True
                    return 0

                await driver.open_and_focus()
                context, event_graph, preflight = await driver.graph_state()
                graph_guid = str(event_graph["guid"])
                p5._require(preflight.get("packageDirty") is False, "fixture dirty at start")
                manifest = cp._load_manifest(project_root)
                branch = _branch_with_unlinked_condition(manifest)
                branch_condition = cp._pin(branch, "Condition", "input")
                branch_condition["linkCount"] = 0
                branch_ref = cp._existing_ref(branch, branch_condition)
                report["eventGraphGuid"] = graph_guid
                report["initialNodeCount"] = len(event_graph.get("nodes") or [])
                report["initialEdgeCount"] = len(p5._edge_keys(event_graph))
                report["branchNodeGuid"] = branch["guid"]
                report["branchConditionPinGuid"] = branch_condition["pinGuid"]

                clamp_node = _call_node("tmp:clamp", "Clamp", 0, 600)
                greater_node = _call_node("tmp:greater", "Greater_IntInt", 350, 600)
                plan_nodes = [clamp_node, greater_node]
                plan_connections = [
                    {
                        "from": "tmp:clamp",
                        "fromPin": "ReturnValue",
                        "to": "tmp:greater",
                        "toPin": "A",
                    },
                    {
                        "from": "tmp:greater",
                        "fromPin": "ReturnValue",
                        "toExisting": branch_ref,
                    },
                ]

                if args.mode == "gate-off":
                    payload = await driver.call(
                        p5.GRAPH_EDIT_PREVIEW,
                        {
                            "assetPath": ASSET_PATH,
                            "graphGuid": graph_guid,
                            "nodes": plan_nodes,
                            "connections": plan_connections,
                        },
                    )
                    code = p5._error_code(payload)
                    p5._require(
                        payload.get("ok") is False
                        and code in ("graph-edit-experimental-disabled", "live-editor-capability-missing"),
                        f"gate-off negative failed: {code}",
                        code="gate-off",
                    )
                    report["gateOffError"] = {"code": code}
                    report["steps"] = driver.steps
                    report["ok"] = True
                    return 0

                # Negative: unsupported owner/function and tampered digest.
                evil = _call_node("tmp:evil", "Abs_Int", 0, 900)
                evil["callFunction"]["functionName"] = "RandomInteger"
                report["negatives"].append(
                    await _expect_preview_error(
                        driver,
                        "unsupported-function",
                        [evil],
                        [],
                        graph_guid,
                        "graph-edit-call-function-not-allowlisted",
                    )
                )
                bad_digest = _call_node("tmp:baddigest", "Abs_Int", 0, 900)
                bad_digest["callFunction"]["signatureDigest"] = "sha256:" + "0" * 64
                report["negatives"].append(
                    await _expect_preview_error(
                        driver,
                        "tampered-digest",
                        [bad_digest],
                        [],
                        graph_guid,
                        "graph-edit-call-function-signature-mismatch",
                    )
                )

                preview, applied, saved, plan_id = await driver.preview_apply_save(
                    family="safe-callfunction",
                    preview_tool=p5.GRAPH_EDIT_PREVIEW,
                    apply_tool=p5.GRAPH_EDIT_APPLY,
                    save_tool=p5.GRAPH_EDIT_SAVE,
                    preview_args={
                        "assetPath": ASSET_PATH,
                        "graphGuid": graph_guid,
                        "nodes": plan_nodes,
                        "connections": plan_connections,
                    },
                    apply_prefix=p5.GRAPH_EDIT_APPLY_PREFIX,
                    save_prefix=p5.GRAPH_EDIT_SAVE_PREFIX,
                    apply_payload_key="preview",
                )
                p5._require(applied.get("applied") is True, "CallFunction Apply failed")
                p5._require(applied.get("undoVerified") is True, "Undo not verified")
                p5._require(applied.get("redoVerified") is True, "Redo not verified")
                p5._require(saved.get("saved") is True, "Save failed")
                created = {
                    item["tempId"]: item["nodeId"].split(":", 1)[1]
                    for item in applied.get("createdNodes") or []
                    if isinstance(item, Mapping)
                }
                p5._require(set(created) == {"tmp:clamp", "tmp:greater"}, "created node set mismatch")
                context, event_graph, preflight = await driver.graph_state()
                p5._require(
                    len(event_graph.get("nodes") or []) == int(report["initialNodeCount"]) + 2,
                    "node count delta != +2",
                )
                p5._require(
                    len(p5._edge_keys(event_graph)) == int(report["initialEdgeCount"]) + 2,
                    "edge count delta != +2",
                )
                clamp_live = _node_by_guid(event_graph, created["tmp:clamp"])
                greater_live = _node_by_guid(event_graph, created["tmp:greater"])
                p5._require(
                    str(clamp_live.get("class", "")).endswith("K2Node_CallFunction"),
                    "Clamp node class is not K2Node_CallFunction",
                )
                p5._require(
                    str(greater_live.get("class", "")).endswith("K2Node_CallFunction"),
                    "Greater node class is not K2Node_CallFunction",
                )
                for node, input_names, output_name, output_type in (
                    (clamp_live, {"Value", "Min", "Max"}, "ReturnValue", "int"),
                    (greater_live, {"A", "B"}, "ReturnValue", "bool"),
                ):
                    inputs = {
                        pin["name"]
                        for pin in node.get("pins") or []
                        if isinstance(pin, Mapping)
                        and pin.get("direction") == "input"
                        and not pin.get("hidden", False)
                    }
                    p5._require(inputs == input_names, f"input pins mismatch: {inputs}")
                    out_pin = _pin(node, output_name, "output")
                    p5._require(
                        str((out_pin.get("type") or {}).get("category", "")) == output_type,
                        f"output pin type mismatch for {output_name}",
                    )
                p5._require(
                    len(_pin(greater_live, "ReturnValue", "output").get("links") or []) == 1,
                    "Greater output link count != 1",
                )
                p5._require(
                    len(_pin(clamp_live, "ReturnValue", "output").get("links") or []) == 1,
                    "Clamp output link count != 1",
                )
                calls = [
                    ref
                    for ref in context.get("references") or []
                    if isinstance(ref, Mapping)
                    and ref.get("kind") == "calls"
                    and str(ref.get("nodeGuid", "")).lower()
                    in {created["tmp:clamp"].lower(), created["tmp:greater"].lower()}
                ]
                targets = {str(ref.get("targetPath", "")) for ref in calls}
                p5._require(
                    any("Clamp" in target for target in targets),
                    f"Clamp call reference missing: {targets}",
                )
                p5._require(
                    any("Greater_IntInt" in target for target in targets),
                    f"Greater call reference missing: {targets}",
                )
                relative_asset = ASSET_PATH.split("/Game/", 1)[1].split(".", 1)[0]
                asset_filename = project_root / "Content" / (relative_asset + ".uasset")
                disk_sha = p5._sha256_file(asset_filename)
                p5._require(disk_sha == saved.get("assetSha256"), "asset SHA mismatch")
                expected = {
                    "schemaVersion": "ueak.blueprint-safe-callfunction-expected/1.0",
                    "assetPath": ASSET_PATH,
                    "assetFilename": str(asset_filename),
                    "assetSha256": disk_sha,
                    "eventGraphGuid": graph_guid,
                    "clampNodeGuid": created["tmp:clamp"],
                    "greaterNodeGuid": created["tmp:greater"],
                    "branchNodeGuid": branch["guid"],
                    "branchConditionPinGuid": branch_condition["pinGuid"],
                    "finalNodeCount": len(event_graph.get("nodes") or []),
                    "finalEdgeCount": len(p5._edge_keys(event_graph)),
                    "targetPaths": sorted(targets),
                }
                expected_path = project_root / "Saved" / "UEAgentKitAccessorE2E" / "safe-callfunction-expected.json"
                p5._write_json(expected_path, expected)
                report["positive"] = {
                    "ok": True,
                    "planId": plan_id,
                    "createdNodes": created,
                    "nodeCount": len(event_graph.get("nodes") or []),
                    "edgeCount": len(p5._edge_keys(event_graph)),
                    "assetSha256": disk_sha,
                    "targetPaths": sorted(targets),
                    "expectedPath": str(expected_path),
                }
                driver.record(
                    "safe-callfunction",
                    planId=plan_id,
                    assetSha256=disk_sha,
                    nodeCount=len(event_graph.get("nodes") or []),
                    edgeCount=len(p5._edge_keys(event_graph)),
                )

                # Dirty package negative + cleanup Save.
                int_variable = cp._context_variable(context, "AccessInt")
                dirty_args = {
                    "assetPath": ASSET_PATH,
                    "graphGuid": graph_guid,
                    "nodes": [
                        {
                            "tempId": "tmp:dirty",
                            "nodeType": "variable_get",
                            "x": 300,
                            "y": 900,
                            "variableName": int_variable["name"],
                            "variableGuid": int_variable["guid"],
                            "variableType": int_variable["variableType"],
                        }
                    ],
                    "connections": [],
                }
                dirty_preview = p5._unwrap(
                    await driver.call(p5.GRAPH_EDIT_PREVIEW, dirty_args),
                    p5.GRAPH_EDIT_PREVIEW,
                )
                dirty_applied = p5._unwrap(
                    await driver.call(
                        p5.GRAPH_EDIT_APPLY,
                        {
                            "preview": dirty_preview,
                            "confirmation": dirty_preview["requiredConfirmation"],
                        },
                    ),
                    p5.GRAPH_EDIT_APPLY,
                )
                p5._require(dirty_applied.get("applied") is True, "dirty Apply failed")
                dirty_payload = await driver.call(
                    p5.GRAPH_EDIT_PREVIEW,
                    {
                        "assetPath": ASSET_PATH,
                        "graphGuid": graph_guid,
                        "nodes": plan_nodes,
                        "connections": plan_connections,
                    },
                )
                dirty_code = p5._error_code(dirty_payload)
                p5._require(
                    dirty_payload.get("ok") is False and dirty_code == "graph-edit-package-dirty",
                    f"dirty negative expected graph-edit-package-dirty, got {dirty_code}",
                    code="dirty-negative",
                )
                dirty_saved = p5._unwrap(
                    await driver.call(
                        p5.GRAPH_EDIT_SAVE,
                        {
                            "apply_result": dirty_applied,
                            "confirmation": dirty_applied["requiredSaveConfirmation"],
                        },
                    ),
                    p5.GRAPH_EDIT_SAVE,
                )
                p5._require(dirty_saved.get("saved") is True, "dirty cleanup Save failed")
                report["negatives"].append(
                    {"label": "dirty-package", "errorCode": dirty_code, "cleanupSaved": True}
                )

                context, event_graph, preflight = await driver.graph_state()
                p5._require(preflight.get("packageDirty") is False, "final package dirty")
                final_sha = p5._sha256_file(asset_filename)
                expected["assetSha256"] = final_sha
                expected["finalNodeCount"] = len(event_graph.get("nodes") or [])
                expected["finalEdgeCount"] = len(p5._edge_keys(event_graph))
                p5._write_json(expected_path, expected)
                report["finalNodeCount"] = len(event_graph.get("nodes") or [])
                report["finalEdgeCount"] = len(p5._edge_keys(event_graph))
                report["finalAssetSha256"] = final_sha
                report["planIds"] = driver.plan_ids
                report["steps"] = driver.steps
                report["ok"] = True
                return 0
    except p5.ProbeError as exc:
        report["error"] = {"code": exc.code, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        report["error"] = {"code": "unexpected", "message": p5._flatten_exception(exc)}
    finally:
        if driver is not None:
            report["steps"] = driver.steps
        p5._write_json(report_root / "probe-report.json", report)
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--mode", choices=("full", "gate-off", "cross-project"), default="full")
    parser.add_argument("--wrong-project", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_run(build_parser().parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
