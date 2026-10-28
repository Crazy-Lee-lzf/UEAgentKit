#!/usr/bin/env python3
"""Real MCP A-path probe for connecting a new GraphEdit node to existing pins.

Fixture: /Game/UEAgentKitAgentE2E/BP_AccessorE2E
  - compiled member variables AccessBool/AccessInt/... (from c8d9da4 fixture)
  - an existing unconnected K2 Branch in EventGraph
  - existing event exec outputs

The plan creates three new nodes:
  variable_get(AccessInt), variable_set(AccessInt), variable_get(AccessBool)

and four connections:
  new set.execute          <- existing event.then      (existing output -> new input)
  new getInt.AccessInt     -> new set.AccessInt        (new -> new)
  new getBool.AccessBool   -> existing branch.Condition(existing input <- new output)
  new set.then             -> existing branch.execute  (new output -> existing input)
"""

from __future__ import annotations

import argparse
import asyncio
import json
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
from mcp import ClientSession  # noqa: E402
from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: E402

ASSET_PATH = "/Game/UEAgentKitAgentE2E/BP_AccessorE2E.BP_AccessorE2E"


def _load_manifest(project_root: Path) -> dict[str, Any]:
    path = project_root / "Saved" / "UEAgentKitAccessorE2E" / "fixture-manifest.json"
    if not path.is_file():
        raise p5.ProbeError(f"fixture manifest is missing: {path}", code="fixture-shape")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise p5.ProbeError("fixture manifest is not an object", code="fixture-shape")
    return payload


def _find_node(manifest: Mapping[str, Any], class_suffix: str) -> dict[str, Any]:
    for node in manifest.get("nodes") or []:
        if isinstance(node, Mapping) and str(node.get("class", "")).endswith(class_suffix):
            return dict(node)
    raise p5.ProbeError(f"manifest has no {class_suffix} node", code="fixture-shape")


def _find_event_with_unlinked_then(manifest: Mapping[str, Any]) -> dict[str, Any]:
    for node in manifest.get("nodes") or []:
        if not isinstance(node, Mapping) or not str(node.get("class", "")).endswith("K2Node_Event"):
            continue
        for pin in node.get("pins") or []:
            if (
                isinstance(pin, Mapping)
                and pin.get("pinName") == "then"
                and pin.get("direction") == "output"
                and int(pin.get("linkCount", -1)) == 0
            ):
                return dict(node)
    raise p5.ProbeError("fixture has no unlinked K2 event 'then' output", code="fixture-shape")


def _pin(node: Mapping[str, Any], name: str, direction: str) -> dict[str, Any]:
    for pin in node.get("pins") or []:
        if (
            isinstance(pin, Mapping)
            and pin.get("pinName") == name
            and pin.get("direction") == direction
        ):
            return dict(pin)
    raise p5.ProbeError(f"node {node.get('guid')} has no {name}:{direction}", code="fixture-shape")


def _existing_ref(node: Mapping[str, Any], pin: Mapping[str, Any]) -> dict[str, str]:
    return {
        "nodeGuid": str(node["guid"]),
        "pinGuid": str(pin["pinGuid"]),
        "pinName": str(pin["pinName"]),
        "direction": str(pin["direction"]),
        "category": str(pin["category"]),
        "subCategory": str(pin["subCategory"]),
    }


def _accessor(
    temp_id: str,
    kind: str,
    variable: Mapping[str, Any],
    x: int,
    y: int,
) -> dict[str, Any]:
    return {
        "tempId": temp_id,
        "nodeType": kind,
        "x": x,
        "y": y,
        "variableName": str(variable["name"]),
        "variableGuid": str(variable["guid"]),
        "variableType": str(variable["variableType"]),
    }


def _context_variable(context: Mapping[str, Any], name: str) -> dict[str, Any]:
    for variable in context.get("variables") or []:
        if isinstance(variable, Mapping) and str(variable.get("name")) == name:
            pin_type = variable.get("type") or {}
            category = str(pin_type.get("category", ""))
            subcategory = str(pin_type.get("subcategory", ""))
            if category == "bool":
                token = "bool"
            elif category == "int":
                token = "int"
            elif category == "string":
                token = "string"
            elif category == "double" or (category == "real" and subcategory == "double"):
                token = "double"
            else:
                raise p5.ProbeError(f"unsupported variable type {category}/{subcategory}", code="fixture-shape")
            return {
                "name": str(variable.get("name")),
                "guid": str(variable.get("guid")),
                "variableType": token,
            }
    raise p5.ProbeError(f"fixture variable {name} is missing", code="fixture-shape")


def _build_plan(
    manifest: Mapping[str, Any],
    context: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    branch = _find_node(manifest, "K2Node_IfThenElse")
    event = _find_event_with_unlinked_then(manifest)
    branch_execute = _pin(branch, "execute", "input")
    branch_condition = _pin(branch, "Condition", "input")
    event_then = _pin(event, "then", "output")
    get_int = _accessor("tmp:get_int", "variable_get", _context_variable(context, "AccessInt"), -900, -200)
    set_int = _accessor("tmp:set_int", "variable_set", _context_variable(context, "AccessInt"), -600, -200)
    get_bool = _accessor("tmp:get_bool", "variable_get", _context_variable(context, "AccessBool"), -900, 100)
    nodes = [get_int, set_int, get_bool]
    connections = [
        {
            "from": "tmp:get_int",
            "fromPin": "AccessInt",
            "to": "tmp:set_int",
            "toPin": "AccessInt",
        },
        {
            "from": "tmp:set_int",
            "fromPin": "then",
            "toExisting": _existing_ref(branch, branch_execute),
        },
        {
            "from": "tmp:get_bool",
            "fromPin": "AccessBool",
            "toExisting": _existing_ref(branch, branch_condition),
        },
        {
            "fromExisting": _existing_ref(event, event_then),
            "to": "tmp:set_int",
            "toPin": "execute",
        },
    ]
    return nodes, connections, branch, event


class ConnectDriver(p5.Driver):
    async def open_and_focus(self, graph_guid: str | None = None) -> dict[str, Any]:
        opened = await self.read(p5.OPEN_ASSET, {"asset_path": ASSET_PATH})
        p5._require(
            opened.get("openAfter") is True or opened.get("alreadyOpen") is True,
            f"open_asset did not open {ASSET_PATH}: {opened}",
            code="asset-open-failed",
        )
        args: dict[str, Any] = {"asset_path": ASSET_PATH}
        if graph_guid:
            args["graph_guid"] = graph_guid
        focused = await self.read(p5.FOCUS_GRAPH, args)
        p5._require(focused.get("focused") is True, f"focus failed: {focused}", code="graph-not-focused")
        return focused

    async def graph_state(self) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        context_payload = await self.read(p5.GRAPH_CONTEXT, {})
        context, graph = p5._graph_from_context(context_payload)
        preflight = await self.read(p5.PREFLIGHT, {})
        return context, graph, preflight


def _created_by_temp(applied: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("tempId")): dict(item)
        for item in applied.get("createdNodes") or []
        if isinstance(item, Mapping)
    }


def _node_by_guid(graph: Mapping[str, Any], guid: str) -> dict[str, Any]:
    return p5._node_by_guid(graph, guid)


def _pin_by_guid(node: Mapping[str, Any], pin_guid: str) -> dict[str, Any]:
    for pin in node.get("pins") or []:
        if isinstance(pin, Mapping) and str(pin.get("id")) == pin_guid:
            return dict(pin)
    raise p5.ProbeError(f"pin {pin_guid} missing on {node.get('guid')}", code="fixture-shape")


def _edge_set(context_graph: Mapping[str, Any]) -> set[str]:
    return set(p5._edge_keys(context_graph))


async def _expect_preview_error(
    driver: ConnectDriver,
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
        "schemaVersion": "ueak.blueprint-connect-existing-pin-report/1.0",
        "mode": args.mode,
        "assetPath": ASSET_PATH,
        "ok": False,
        "steps": [],
        "negatives": [],
        "evidenceLevel": "A" if args.mode == "full" else "negative-only",
    }
    driver: ConnectDriver | None = None
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
                driver = ConnectDriver(session, project_root)

                if args.mode == "cross-project":
                    payload = await driver.call(p5.OPEN_ASSET, {"asset_path": ASSET_PATH})
                    code = p5._error_code(payload)
                    p5._require(
                        payload.get("ok") is False and code == "live-editor-project-mismatch",
                        f"cross-project guard did not reject: ok={payload.get('ok')} code={code}",
                        code="cross-project-guard",
                    )
                    report["crossProjectError"] = {
                        "code": code,
                        "message": payload.get("error", {}).get("message", ""),
                    }
                    report["steps"] = driver.steps
                    report["ok"] = True
                    return 0

                await driver.open_and_focus()
                context, event_graph, preflight = await driver.graph_state()
                graph_guid = str(event_graph["guid"])
                p5._require(
                    preflight.get("packageDirty") is False,
                    "fixture package is dirty at start",
                    code="fixture-not-clean",
                )
                manifest = _load_manifest(project_root)
                branch = _find_node(manifest, "K2Node_IfThenElse")
                event = _find_event_with_unlinked_then(manifest)
                branch_execute = _pin(branch, "execute", "input")
                branch_condition = _pin(branch, "Condition", "input")
                event_then = _pin(event, "then", "output")
                report["eventGraphGuid"] = graph_guid
                report["initialNodeCount"] = len(event_graph.get("nodes") or [])
                report["initialEdgeCount"] = len(p5._edge_keys(event_graph))
                report["existingPins"] = {
                    "branchNodeGuid": branch["guid"],
                    "branchExecutePinGuid": branch_execute["pinGuid"],
                    "branchConditionPinGuid": branch_condition["pinGuid"],
                    "eventNodeGuid": event["guid"],
                    "eventThenPinGuid": event_then["pinGuid"],
                }

                if args.mode == "gate-off":
                    variable = _context_variable(context, "AccessInt")
                    payload = await driver.call(
                        p5.GRAPH_EDIT_PREVIEW,
                        {
                            "assetPath": ASSET_PATH,
                            "graphGuid": graph_guid,
                            "nodes": [_accessor("tmp:off", "variable_get", variable, 0, 0)],
                            "connections": [],
                        },
                    )
                    code = p5._error_code(payload)
                    p5._require(
                        payload.get("ok") is False
                        and code in ("graph-edit-experimental-disabled", "live-editor-capability-missing"),
                        f"gate-off negative failed: ok={payload.get('ok')} code={code}",
                        code="gate-off",
                    )
                    report["gateOffError"] = {"code": code}
                    report["steps"] = driver.steps
                    report["ok"] = True
                    return 0

                # Negatives before the branch inputs become linked.
                int_variable = _context_variable(context, "AccessInt")
                bool_variable = _context_variable(context, "AccessBool")
                bad_node_guid = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
                bad_pin_guid = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff"
                wrong_type = _accessor("tmp:wrong", "variable_get", int_variable, 0, 900)
                report["negatives"].append(
                    await _expect_preview_error(
                        driver,
                        "wrong-type",
                        [wrong_type],
                        [
                            {
                                "from": "tmp:wrong",
                                "fromPin": "AccessInt",
                                "toExisting": _existing_ref(branch, branch_condition),
                            }
                        ],
                        graph_guid,
                        "graph-edit-pin-type-mismatch",
                    )
                )
                unknown_node = _accessor("tmp:unknown", "variable_get", bool_variable, 0, 900)
                report["negatives"].append(
                    await _expect_preview_error(
                        driver,
                        "unknown-existing-node",
                        [unknown_node],
                        [
                            {
                                "from": "tmp:unknown",
                                "fromPin": "AccessBool",
                                "toExisting": {
                                    **_existing_ref(branch, branch_condition),
                                    "nodeGuid": bad_node_guid,
                                },
                            }
                        ],
                        graph_guid,
                        "graph-edit-existing-node-not-found",
                    )
                )
                stale_pin = _accessor("tmp:stale_pin", "variable_get", bool_variable, 0, 900)
                report["negatives"].append(
                    await _expect_preview_error(
                        driver,
                        "stale-pin-guid",
                        [stale_pin],
                        [
                            {
                                "from": "tmp:stale_pin",
                                "fromPin": "AccessBool",
                                "toExisting": {
                                    **_existing_ref(branch, branch_condition),
                                    "pinGuid": bad_pin_guid,
                                },
                            }
                        ],
                        graph_guid,
                        "graph-edit-existing-pin-guid-mismatch",
                    )
                )
                stale_type = _accessor("tmp:stale_type", "variable_get", int_variable, 0, 900)
                report["negatives"].append(
                    await _expect_preview_error(
                        driver,
                        "stale-pin-category",
                        [stale_type],
                        [
                            {
                                "from": "tmp:stale_type",
                                "fromPin": "AccessInt",
                                "toExisting": {
                                    **_existing_ref(branch, branch_condition),
                                    "category": "int",
                                    "subCategory": "double",
                                },
                            }
                        ],
                        graph_guid,
                        "graph-edit-existing-pin-metadata-mismatch",
                    )
                )
                # Existing output -> wrong new input type: Python contract rejects
                # before the socket, so no live mutation can occur.
                payload = await driver.call(
                    p5.GRAPH_EDIT_PREVIEW,
                    {
                        "assetPath": ASSET_PATH,
                        "graphGuid": graph_guid,
                        "nodes": [{"tempId": "tmp:add", "nodeType": "int_add", "x": 0, "y": 900}],
                        "connections": [
                            {
                                "fromExisting": _existing_ref(event, event_then),
                                "to": "tmp:add",
                                "toPin": "A",
                            }
                        ],
                    },
                )
                code = p5._error_code(payload)
                p5._require(
                    payload.get("ok") is False and code == "graph-edit-pin-type-mismatch",
                    f"existing-output type mismatch expected graph-edit-pin-type-mismatch, got {code}",
                    code="negative-mismatch",
                )
                report["negatives"].append(
                    {"label": "existing-output-wrong-new-input", "errorCode": code, "boundary": "python-contract"}
                )
                driver.record("existing-output-wrong-new-input", errorCode=code, boundary="python-contract")

                # Positive A-path.
                nodes, connections, branch, event = _build_plan(manifest, context)
                preview, applied, saved, plan_id = await driver.preview_apply_save(
                    family="connect-existing",
                    preview_tool=p5.GRAPH_EDIT_PREVIEW,
                    apply_tool=p5.GRAPH_EDIT_APPLY,
                    save_tool=p5.GRAPH_EDIT_SAVE,
                    preview_args={
                        "assetPath": ASSET_PATH,
                        "graphGuid": graph_guid,
                        "nodes": nodes,
                        "connections": connections,
                    },
                    apply_prefix=p5.GRAPH_EDIT_APPLY_PREFIX,
                    save_prefix=p5.GRAPH_EDIT_SAVE_PREFIX,
                    apply_payload_key="preview",
                )
                before_hash = str(preview.get("beforeEdgeSetHash", ""))
                after_hash = str(applied.get("afterEdgeSetHash", ""))
                p5._require(before_hash.startswith("sha256:"), "preview missing beforeEdgeSetHash")
                p5._require(after_hash.startswith("sha256:"), "apply missing afterEdgeSetHash")
                p5._require(before_hash != after_hash, "edge-set hash did not change")
                p5._require(
                    applied.get("beforeEdgeSetHash") == before_hash,
                    "apply beforeEdgeSetHash does not echo preview",
                )
                p5._require(
                    saved.get("edgeSetHash") == after_hash,
                    "save edgeSetHash does not match apply after hash",
                )
                if preflight.get("rewireEdgeSetHash"):
                    p5._require(
                        preflight.get("rewireEdgeSetHash") == before_hash,
                        "preview beforeEdgeSetHash does not match preflight rewireEdgeSetHash",
                    )
                created = _created_by_temp(applied)
                p5._require(len(created) == 3, f"created {len(created)} of 3 nodes")
                context, event_graph, preflight = await driver.graph_state()
                p5._require(
                    len(event_graph.get("nodes") or []) == int(report["initialNodeCount"]) + 3,
                    "node count after connect Apply is wrong",
                )
                p5._require(
                    len(p5._edge_keys(event_graph)) == int(report["initialEdgeCount"]) + 4,
                    "edge count after connect Apply is wrong",
                )
                created_nodes = {
                    temp_id: _node_by_guid(
                        event_graph,
                        str(item["nodeId"]).removeprefix("n:"),
                    )
                    for temp_id, item in created.items()
                }
                created_set_then = p5._pin_by_name(
                    created_nodes["tmp:set_int"], "then", "output"
                )
                created_set_execute = p5._pin_by_name(
                    created_nodes["tmp:set_int"], "execute", "input"
                )
                created_bool_output = p5._pin_by_name(
                    created_nodes["tmp:get_bool"], "AccessBool", "output"
                )
                live_branch = _node_by_guid(event_graph, str(branch["guid"]))
                live_event = _node_by_guid(event_graph, str(event["guid"]))
                live_execute = _pin_by_guid(live_branch, branch_execute["pinGuid"])
                live_condition = _pin_by_guid(live_branch, branch_condition["pinGuid"])
                live_event_then = _pin_by_guid(live_event, event_then["pinGuid"])
                p5._require(len(live_execute.get("links") or []) == 1, "branch.execute link count != 1")
                p5._require(len(live_condition.get("links") or []) == 1, "branch.Condition link count != 1")
                p5._require(len(live_event_then.get("links") or []) == 1, "event.then link count != 1")
                p5._require(
                    live_execute["links"][0]["targetNodeGuid"]
                    == str(created["tmp:set_int"]["nodeId"]).removeprefix("n:")
                    and live_execute["links"][0]["targetPinId"] == created_set_then["id"],
                    "branch.execute is not linked from new set.then",
                )
                p5._require(
                    live_condition["links"][0]["targetNodeGuid"]
                    == str(created["tmp:get_bool"]["nodeId"]).removeprefix("n:")
                    and live_condition["links"][0]["targetPinId"] == created_bool_output["id"],
                    "branch.Condition is not linked from new bool getter",
                )
                p5._require(
                    live_event_then["links"][0]["targetNodeGuid"]
                    == str(created["tmp:set_int"]["nodeId"]).removeprefix("n:")
                    and live_event_then["links"][0]["targetPinId"] == created_set_execute["id"],
                    "event.then is not linked to new set.execute",
                )
                # Verify no other existing pin link changed.
                for node in manifest.get("nodes") or []:
                    if not isinstance(node, Mapping) or str(node.get("guid")) not in (
                        str(branch["guid"]),
                        str(event["guid"]),
                    ):
                        continue
                    for pin in node.get("pins") or []:
                        if not isinstance(pin, Mapping):
                            continue
                        if pin.get("pinName") in ("execute", "Condition", "then"):
                            continue
                        p5._require(
                            int(pin.get("linkCount", 0)) == 0,
                            f"unexpected link on existing pin {pin.get('pinName')}",
                        )
                relative_asset = ASSET_PATH.split("/Game/", 1)[1].split(".", 1)[0]
                asset_filename = project_root / "Content" / (relative_asset + ".uasset")
                disk_sha = p5._sha256_file(asset_filename)
                p5._require(
                    disk_sha == saved.get("assetSha256"),
                    f"disk SHA {disk_sha} != save {saved.get('assetSha256')}",
                    code="asset-hash",
                )
                expected = {
                    "schemaVersion": "ueak.blueprint-connect-existing-expected/1.0",
                    "assetPath": ASSET_PATH,
                    "assetFilename": str(asset_filename),
                    "assetSha256": disk_sha,
                    "eventGraphGuid": graph_guid,
                    "branchNodeGuid": str(branch["guid"]),
                    "branchExecutePinGuid": branch_execute["pinGuid"],
                    "branchConditionPinGuid": branch_condition["pinGuid"],
                    "eventNodeGuid": str(event["guid"]),
                    "eventThenPinGuid": event_then["pinGuid"],
                    "createdNodes": created,
                    "beforeEdgeSetHash": before_hash,
                    "afterEdgeSetHash": after_hash,
                    "finalNodeCount": len(event_graph.get("nodes") or []),
                    "finalEdgeCount": len(p5._edge_keys(event_graph)),
                }
                expected_path = project_root / "Saved" / "UEAgentKitAccessorE2E" / "connect-expected.json"
                p5._write_json(expected_path, expected)
                report["positive"] = {
                    "ok": True,
                    "planId": plan_id,
                    "createdNodes": created,
                    "beforeEdgeSetHash": before_hash,
                    "afterEdgeSetHash": after_hash,
                    "nodeCount": len(event_graph.get("nodes") or []),
                    "edgeCount": len(p5._edge_keys(event_graph)),
                    "assetSha256": disk_sha,
                    "expectedPath": str(expected_path),
                }
                driver.record(
                    "connect-existing",
                    planId=plan_id,
                    createdNodes=len(created),
                    beforeEdgeSetHash=before_hash,
                    afterEdgeSetHash=after_hash,
                    assetSha256=disk_sha,
                )

                # Already-linked existing input negative.
                extra = _accessor("tmp:linked", "variable_get", bool_variable, 0, 900)
                report["negatives"].append(
                    await _expect_preview_error(
                        driver,
                        "existing-input-already-linked",
                        [extra],
                        [
                            {
                                "from": "tmp:linked",
                                "fromPin": "AccessBool",
                                "toExisting": _existing_ref(branch, branch_condition),
                            }
                        ],
                        graph_guid,
                        "graph-edit-existing-pin-already-linked",
                    )
                )

                # Dirty-package negative: apply an extra unconnected getter, then
                # preview another plan, then Save the first plan to clean up.
                dirty_args = {
                    "assetPath": ASSET_PATH,
                    "graphGuid": graph_guid,
                    "nodes": [_accessor("tmp:dirty", "variable_get", int_variable, 300, 900)],
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
                        "nodes": [_accessor("tmp:dirty_blocked", "variable_get", int_variable, 600, 900)],
                        "connections": [],
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
                p5._require(
                    dirty_saved.get("saved") is True and dirty_saved.get("packageDirty") is False,
                    "dirty cleanup Save failed",
                    code="dirty-cleanup",
                )
                report["negatives"].append(
                    {"label": "dirty-package", "errorCode": dirty_code, "cleanupSaved": True}
                )
                driver.record("dirty-package", errorCode=dirty_code, cleanupSaved=True)

                context, event_graph, preflight = await driver.graph_state()
                p5._require(preflight.get("packageDirty") is False, "final package is dirty")
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
    args = build_parser().parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
