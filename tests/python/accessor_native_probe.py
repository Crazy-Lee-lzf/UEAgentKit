#!/usr/bin/env python3
"""Native A-path probe for bounded member-variable accessor nodes.

This probe speaks MCP over stdio to a real MCP server, which talks to a real
UE 5.6.1 Editor through the Editor Bridge socket.  It never calls the C++
experimental methods directly.

Fixture: /Game/UEAgentKitAgentE2E/BP_AccessorE2E
  - compiled member variables: AccessBool, AccessInt, AccessDouble, AccessLabel
  - a function graph with a local variable AccessLocal

Sequence:
  1. Event graph: create four variable_get + four variable_set nodes, connect
     each getter to its setter, and drive one setter from a sequence exec pin.
  2. Function graph: create/connect a getter and setter for AccessInt.
  3. Dirty-package negative: Apply an extra getter without Save, prove the next
     Preview fails with graph-edit-package-dirty, then Save the applied plan.
  4. Negative requests: wrong type, stale GUID, missing/inherited/component/local
     variable, reserved pin name, unsupported type.
  5. Read back the canonical context and write expected.json for the fresh
     process exporter verification.
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
EVENT_VARIABLES = ("AccessBool", "AccessInt", "AccessDouble", "AccessLabel")
EXPECTED_TYPES = {
    "AccessBool": "bool",
    "AccessInt": "int",
    "AccessDouble": "double",
    "AccessLabel": "string",
}
FUNCTION_GRAPH_NAME = "AccessorFunction"
LOCAL_VARIABLE_NAME = "AccessLocal"


def _guid_digits(value: Any) -> str:
    return str(value).replace("-", "").lower()


def _sha256_file(path: Path) -> str:
    return p5._sha256_file(path)


def _variable_type_token(variable: Mapping[str, Any]) -> str:
    pin_type = variable.get("type")
    if not isinstance(pin_type, Mapping):
        raise p5.ProbeError(f"variable {variable.get('name')} has no type", code="fixture-shape")
    category = str(pin_type.get("category", ""))
    subcategory = str(pin_type.get("subcategory", ""))
    if category == "bool":
        return "bool"
    if category == "int":
        return "int"
    if category == "string":
        return "string"
    if category == "double" or (category == "real" and subcategory == "double"):
        return "double"
    raise p5.ProbeError(
        f"variable {variable.get('name')} has unsupported type {category}/{subcategory}",
        code="fixture-shape",
    )


def _context_variable(context: Mapping[str, Any], name: str) -> dict[str, Any]:
    for variable in context.get("variables") or []:
        if isinstance(variable, Mapping) and str(variable.get("name")) == name:
            return dict(variable)
    raise p5.ProbeError(f"fixture variable {name} is missing", code="fixture-shape")


def _load_fixture_manifest(project_root: Path) -> dict[str, Any]:
    path = project_root / "Saved" / "UEAgentKitAccessorE2E" / "fixture-manifest.json"
    if not path.is_file():
        raise p5.ProbeError(f"fixture manifest is missing: {path}", code="fixture-shape")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise p5.ProbeError("fixture manifest is not an object", code="fixture-shape")
    return manifest


def _accessor(temp_id: str, kind: str, variable: Mapping[str, Any], x: int, y: int) -> dict[str, Any]:
    variable_type = _variable_type_token(variable)
    return {
        "tempId": temp_id,
        "nodeType": kind,
        "x": x,
        "y": y,
        "variableName": str(variable["name"]),
        "variableGuid": str(variable["guid"]),
        "variableType": variable_type,
    }


def _expected_accessor_pins(node_type: str, variable: Mapping[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    variable_type = _variable_type_token(variable)
    variable_name = str(variable["name"])
    if node_type == "variable_get":
        return {}, {variable_name: variable_type}
    return (
        {"execute": "exec", variable_name: variable_type},
        {"then": "exec", "Output_Get": variable_type},
    )


def _event_plan(context: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    variables = {name: _context_variable(context, name) for name in EVENT_VARIABLES}
    nodes: list[dict[str, Any]] = [
        {"tempId": "tmp:seq", "nodeType": "sequence", "x": -1100, "y": 0}
    ]
    connections: list[dict[str, str]] = []
    for index, name in enumerate(EVENT_VARIABLES):
        y = -360 + index * 220
        getter = _accessor(f"tmp:get_{name}", "variable_get", variables[name], -800, y)
        setter = _accessor(f"tmp:set_{name}", "variable_set", variables[name], -500, y)
        nodes.extend((getter, setter))
        connections.append(
            {
                "from": getter["tempId"],
                "fromPin": name,
                "to": setter["tempId"],
                "toPin": name,
            }
        )
    connections.append(
        {
            "from": "tmp:seq",
            "fromPin": "then_0",
            "to": "tmp:set_AccessInt",
            "toPin": "execute",
        }
    )
    return nodes, connections


def _function_plan(context: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    variable = _context_variable(context, "AccessInt")
    getter = _accessor("tmp:fn_get", "variable_get", variable, -400, 0)
    setter = _accessor("tmp:fn_set", "variable_set", variable, -100, 0)
    return [getter, setter], [
        {
            "from": "tmp:fn_get",
            "fromPin": "AccessInt",
            "to": "tmp:fn_set",
            "toPin": "AccessInt",
        }
    ]


class AccessorDriver(p5.Driver):
    async def open_and_focus(self, graph_guid: str | None = None) -> dict[str, Any]:
        opened = await self.read(p5.OPEN_ASSET, {"asset_path": ASSET_PATH})
        p5._require(
            opened.get("openAfter") is True or opened.get("alreadyOpen") is True,
            f"open_asset did not open {ASSET_PATH}: {opened}",
            code="asset-open-failed",
        )
        arguments: dict[str, Any] = {"asset_path": ASSET_PATH}
        if graph_guid:
            arguments["graph_guid"] = graph_guid
        focused = await self.read(p5.FOCUS_GRAPH, arguments)
        p5._require(
            focused.get("focused") is True,
            f"focus_blueprint_graph did not focus the requested graph: {focused}",
            code="graph-not-focused",
        )
        return focused

    async def graph_state(self) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        context_payload = await self.read(p5.GRAPH_CONTEXT, {})
        context, graph = p5._graph_from_context(context_payload)
        preflight = await self.read(p5.PREFLIGHT, {})
        return context, graph, preflight


def _created_by_temp(applied: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    created: dict[str, dict[str, Any]] = {}
    for item in applied.get("createdNodes") or []:
        if isinstance(item, Mapping):
            created[str(item.get("tempId"))] = dict(item)
    return created


def _verify_accessor_node(
    context: Mapping[str, Any],
    graph: Mapping[str, Any],
    node_guid: str,
    node_type: str,
    variable: Mapping[str, Any],
) -> dict[str, Any]:
    node = p5._node_by_guid(graph, node_guid)
    expected_class = (
        "/Script/BlueprintGraph.K2Node_VariableGet"
        if node_type == "variable_get"
        else "/Script/BlueprintGraph.K2Node_VariableSet"
    )
    expected_input, expected_output = _expected_accessor_pins(node_type, variable)
    p5._require(node.get("class") == expected_class, f"{node_guid} class mismatch: {node.get('class')}")
    properties = node.get("properties")
    p5._require(isinstance(properties, Mapping), f"{node_guid} has no reflected properties")
    reference = str(properties.get("VariableReference", ""))
    p5._require(
        f'MemberName="{variable["name"]}"' in reference
        and _guid_digits(variable["guid"]) in _guid_digits(reference),
        f"{node_guid} VariableReference does not bind {variable['name']} / {variable['guid']}: {reference}",
        code="binding-mismatch",
    )
    for pin_name, pin_type in expected_input.items():
        pin = p5._pin_by_name(node, pin_name, "input")
        actual_category = str((pin.get("type") or {}).get("category", ""))
        if pin_type == "exec":
            p5._require(actual_category == "exec", f"{node_guid}.{pin_name} is not exec")
        elif pin_type == "double":
            p5._require(
                actual_category in ("real", "double"),
                f"{node_guid}.{pin_name} is not double: {actual_category}",
            )
        else:
            p5._require(
                actual_category == pin_type,
                f"{node_guid}.{pin_name} is {actual_category}, expected {pin_type}",
            )
    for pin_name, pin_type in expected_output.items():
        pin = p5._pin_by_name(node, pin_name, "output")
        actual_category = str((pin.get("type") or {}).get("category", ""))
        if pin_type == "exec":
            p5._require(actual_category == "exec", f"{node_guid}.{pin_name} is not exec")
        elif pin_type == "double":
            p5._require(
                actual_category in ("real", "double"),
                f"{node_guid}.{pin_name} is not double: {actual_category}",
            )
        else:
            p5._require(
                actual_category == pin_type,
                f"{node_guid}.{pin_name} is {actual_category}, expected {pin_type}",
            )
    return {
        "nodeGuid": node_guid,
        "nodeType": node_type,
        "nodeClass": node.get("class"),
        "variableName": variable["name"],
        "variableGuid": variable["guid"],
        "variableType": _variable_type_token(variable),
        "inputPins": sorted(expected_input),
        "outputPins": sorted(expected_output),
    }


def _references_for(
    context: Mapping[str, Any],
    node_guid: str,
    expected_kind: str,
    variable_name: str,
) -> dict[str, Any]:
    matches = [
        reference
        for reference in context.get("references") or []
        if isinstance(reference, Mapping)
        and str(reference.get("nodeGuid")) == node_guid
        and str(reference.get("kind")) == expected_kind
        and str(reference.get("targetName")) == variable_name
    ]
    p5._require(
        len(matches) == 1,
        f"expected one {expected_kind} reference for {node_guid}/{variable_name}, got {len(matches)}",
        code="reference-mismatch",
    )
    return dict(matches[0])


async def _expect_preview_error(
    driver: AccessorDriver,
    label: str,
    node: Mapping[str, Any],
    expected_code: str,
    graph_guid: str,
) -> dict[str, Any]:
    payload = await driver.call(
        p5.GRAPH_EDIT_PREVIEW,
        {
            "assetPath": ASSET_PATH,
            "graphGuid": graph_guid,
            "nodes": [dict(node)],
            "connections": [],
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
        "schemaVersion": "ueak.blueprint-accessor-native-report/1.0",
        "mode": args.mode,
        "assetPath": ASSET_PATH,
        "ok": False,
        "steps": [],
        "negatives": [],
        "evidenceLevel": "A" if args.mode == "full" else "negative-only",
    }
    driver: AccessorDriver | None = None
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
                driver = AccessorDriver(session, project_root)

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
                event_graph_guid = str(event_graph["guid"])
                p5._require(
                    preflight.get("packageDirty") is False,
                    "fixture package is dirty at start",
                    code="fixture-not-clean",
                )
                for name in EVENT_VARIABLES:
                    variable = _context_variable(context, name)
                    p5._require(
                        _variable_type_token(variable) == EXPECTED_TYPES[name],
                        f"fixture variable {name} has wrong type",
                        code="fixture-shape",
                    )
                report["eventGraphGuid"] = event_graph_guid
                report["initialEventNodeCount"] = len(event_graph.get("nodes") or [])
                report["initialEventEdgeCount"] = len(p5._edge_keys(event_graph))
                report["variables"] = {
                    name: {
                        "guid": _context_variable(context, name)["guid"],
                        "type": EXPECTED_TYPES[name],
                    }
                    for name in EVENT_VARIABLES
                }

                if args.mode == "gate-off":
                    variable = _context_variable(context, "AccessInt")
                    payload = await driver.call(
                        p5.GRAPH_EDIT_PREVIEW,
                        {
                            "assetPath": ASSET_PATH,
                            "graphGuid": event_graph_guid,
                            "nodes": [_accessor("tmp:off", "variable_get", variable, 0, 0)],
                            "connections": [],
                        },
                    )
                    code = p5._error_code(payload)
                    p5._require(
                        payload.get("ok") is False
                        and code in ("graph-edit-experimental-disabled", "live-editor-capability-missing"),
                        f"gate-off negative did not fail closed: ok={payload.get('ok')} code={code}",
                        code="gate-off",
                    )
                    report["gateOffError"] = {
                        "code": code,
                        "message": payload.get("error", {}).get("message", ""),
                    }
                    report["steps"] = driver.steps
                    report["ok"] = True
                    return 0

                # 1) Event graph: four getters + four setters + sequence.
                event_nodes, event_connections = _event_plan(context)
                preview, applied, saved, plan_id = await driver.preview_apply_save(
                    family="accessor-event",
                    preview_tool=p5.GRAPH_EDIT_PREVIEW,
                    apply_tool=p5.GRAPH_EDIT_APPLY,
                    save_tool=p5.GRAPH_EDIT_SAVE,
                    preview_args={
                        "assetPath": ASSET_PATH,
                        "graphGuid": event_graph_guid,
                        "nodes": event_nodes,
                        "connections": event_connections,
                    },
                    apply_prefix=p5.GRAPH_EDIT_APPLY_PREFIX,
                    save_prefix=p5.GRAPH_EDIT_SAVE_PREFIX,
                    apply_payload_key="preview",
                )
                p5._require(
                    str(applied.get("variableBindingHash", "")).startswith("sha256:"),
                    "apply did not return variableBindingHash",
                    code="binding-hash",
                )
                p5._require(
                    applied.get("variableBindingHash") == saved.get("variableBindingHash"),
                    "apply/save variableBindingHash mismatch",
                    code="binding-hash",
                )
                created = _created_by_temp(applied)
                p5._require(
                    len(created) == len(event_nodes),
                    f"created {len(created)} of {len(event_nodes)} event nodes",
                    code="created-count",
                )
                accessor_evidence: list[dict[str, Any]] = []
                context, event_graph, preflight = await driver.graph_state()
                for name in EVENT_VARIABLES:
                    variable = _context_variable(context, name)
                    getter = created[f"tmp:get_{name}"]
                    setter = created[f"tmp:set_{name}"]
                    p5._require(
                        getter.get("variableName") == name
                        and setter.get("variableName") == name
                        and getter.get("variableGuid") == variable["guid"]
                        and setter.get("variableGuid") == variable["guid"]
                        and getter.get("variableType") == EXPECTED_TYPES[name]
                        and setter.get("variableType") == EXPECTED_TYPES[name],
                        f"createdNodes metadata mismatch for {name}: {getter} / {setter}",
                        code="created-metadata",
                    )
                    accessor_evidence.append(
                        _verify_accessor_node(
                            context,
                            event_graph,
                            str(getter["nodeId"]).removeprefix("n:"),
                            "variable_get",
                            variable,
                        )
                    )
                    accessor_evidence.append(
                        _verify_accessor_node(
                            context,
                            event_graph,
                            str(setter["nodeId"]).removeprefix("n:"),
                            "variable_set",
                            variable,
                        )
                    )
                    _references_for(
                        context,
                        str(getter["nodeId"]).removeprefix("n:"),
                        "reads",
                        name,
                    )
                    _references_for(
                        context,
                        str(setter["nodeId"]).removeprefix("n:"),
                        "writes",
                        name,
                    )
                p5._require(
                    len(event_graph.get("nodes") or [])
                    == int(report["initialEventNodeCount"]) + len(event_nodes),
                    "event node count after accessor Apply is wrong",
                    code="event-state",
                )
                p5._require(
                    len(p5._edge_keys(event_graph))
                    == int(report["initialEventEdgeCount"]) + len(event_connections),
                    "event edge count after accessor Apply is wrong",
                    code="event-state",
                )
                report["event"] = {
                    "ok": True,
                    "planId": plan_id,
                    "nodeCount": len(event_graph.get("nodes") or []),
                    "edgeCount": len(p5._edge_keys(event_graph)),
                    "createdAccessors": accessor_evidence,
                    "assetSha256": saved.get("assetSha256"),
                    "packageRevision": saved.get("packageRevision"),
                    "variableBindingHash": saved.get("variableBindingHash"),
                }
                driver.record(
                    "accessor-event",
                    planId=plan_id,
                    createdNodes=len(created),
                    nodeCount=len(event_graph.get("nodes") or []),
                    edgeCount=len(p5._edge_keys(event_graph)),
                    assetSha256=saved.get("assetSha256"),
                    variableBindingHash=saved.get("variableBindingHash"),
                )

                # 2) Function graph: one getter + one setter for AccessInt.
                manifest = _load_fixture_manifest(project_root)
                function_graph = next(
                    (
                        graph
                        for graph in manifest.get("graphs") or []
                        if isinstance(graph, Mapping)
                        and str(graph.get("kind")) == "function"
                        and str(graph.get("name")) == FUNCTION_GRAPH_NAME
                    ),
                    None,
                )
                p5._require(
                    function_graph is not None,
                    f"fixture function graph {FUNCTION_GRAPH_NAME} is missing from the manifest",
                    code="fixture-shape",
                )
                function_guid = str(function_graph["guid"])
                await driver.open_and_focus(function_guid)
                context, function_live, preflight = await driver.graph_state()
                p5._require(
                    str(function_live["guid"]) == function_guid,
                    "focused function graph identity changed",
                    code="function-graph",
                )
                function_variable = _context_variable(context, "AccessInt")
                function_nodes, function_connections = _function_plan(context)
                preview, applied, saved, plan_id = await driver.preview_apply_save(
                    family="accessor-function",
                    preview_tool=p5.GRAPH_EDIT_PREVIEW,
                    apply_tool=p5.GRAPH_EDIT_APPLY,
                    save_tool=p5.GRAPH_EDIT_SAVE,
                    preview_args={
                        "assetPath": ASSET_PATH,
                        "graphGuid": function_guid,
                        "nodes": function_nodes,
                        "connections": function_connections,
                    },
                    apply_prefix=p5.GRAPH_EDIT_APPLY_PREFIX,
                    save_prefix=p5.GRAPH_EDIT_SAVE_PREFIX,
                    apply_payload_key="preview",
                )
                function_created = _created_by_temp(applied)
                p5._require(
                    len(function_created) == len(function_nodes),
                    f"created {len(function_created)} of {len(function_nodes)} function nodes",
                    code="created-count",
                )
                context, function_live, preflight = await driver.graph_state()
                function_evidence = []
                for temp_id, node_type in (("tmp:fn_get", "variable_get"), ("tmp:fn_set", "variable_set")):
                    item = function_created[temp_id]
                    function_evidence.append(
                        _verify_accessor_node(
                            context,
                            function_live,
                            str(item["nodeId"]).removeprefix("n:"),
                            node_type,
                            function_variable,
                        )
                    )
                report["function"] = {
                    "ok": True,
                    "graphGuid": function_guid,
                    "graphName": FUNCTION_GRAPH_NAME,
                    "planId": plan_id,
                    "nodeCount": len(function_live.get("nodes") or []),
                    "edgeCount": len(p5._edge_keys(function_live)),
                    "createdAccessors": function_evidence,
                    "assetSha256": saved.get("assetSha256"),
                    "variableBindingHash": saved.get("variableBindingHash"),
                }
                driver.record(
                    "accessor-function",
                    planId=plan_id,
                    graphGuid=function_guid,
                    createdNodes=len(function_created),
                    assetSha256=saved.get("assetSha256"),
                    variableBindingHash=saved.get("variableBindingHash"),
                )

                # 3) Dirty-package negative, then Save the applied plan.
                await driver.open_and_focus(event_graph_guid)
                context, event_graph, preflight = await driver.graph_state()
                extra_variable = _context_variable(context, "AccessBool")
                extra_node = _accessor("tmp:dirty_get", "variable_get", extra_variable, 200, 800)
                dirty_args = {
                    "assetPath": ASSET_PATH,
                    "graphGuid": event_graph_guid,
                    "nodes": [extra_node],
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
                p5._require(
                    dirty_applied.get("applied") is True
                    and dirty_applied.get("undoVerified") is True
                    and dirty_applied.get("redoVerified") is True,
                    f"dirty fixture Apply failed: {dirty_applied}",
                    code="dirty-setup",
                )
                second_preview = await driver.call(
                    p5.GRAPH_EDIT_PREVIEW,
                    {
                        "assetPath": ASSET_PATH,
                        "graphGuid": event_graph_guid,
                        "nodes": [
                            _accessor("tmp:dirty_blocked", "variable_get", extra_variable, 400, 800)
                        ],
                        "connections": [],
                    },
                )
                second_code = p5._error_code(second_preview)
                p5._require(
                    second_preview.get("ok") is False and second_code == "graph-edit-package-dirty",
                    f"dirty package negative expected graph-edit-package-dirty, got {second_code}",
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
                    "dirty fixture cleanup Save failed",
                    code="dirty-cleanup",
                )
                report["negatives"].append(
                    {"label": "dirty-package", "errorCode": second_code, "cleanupSaved": True}
                )
                driver.record("dirty-package", errorCode=second_code, cleanupSaved=True)
                context, event_graph, preflight = await driver.graph_state()

                # 4) Contract negatives against the clean live event graph.
                int_variable = _context_variable(context, "AccessInt")
                stale_guid = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
                inherited = {
                    "tempId": "tmp:inherited",
                    "nodeType": "variable_get",
                    "x": 0,
                    "y": 900,
                    "variableName": "bHidden",
                    "variableGuid": stale_guid,
                    "variableType": "bool",
                }
                component = {
                    "tempId": "tmp:component",
                    "nodeType": "variable_get",
                    "x": 200,
                    "y": 900,
                    "variableName": "RootComponent",
                    "variableGuid": stale_guid,
                    "variableType": "string",
                }
                local = {
                    "tempId": "tmp:local",
                    "nodeType": "variable_get",
                    "x": 400,
                    "y": 900,
                    "variableName": LOCAL_VARIABLE_NAME,
                    "variableGuid": stale_guid,
                    "variableType": "int",
                }
                negatives = [
                    (
                        "wrong-type",
                        _accessor("tmp:wrong_type", "variable_get", int_variable, 0, 900)
                        | {"variableType": "bool"},
                        "graph-edit-variable-type-mismatch",
                    ),
                    (
                        "stale-guid",
                        _accessor("tmp:stale", "variable_get", int_variable, 200, 900)
                        | {"variableGuid": stale_guid},
                        "graph-edit-variable-identity-mismatch",
                    ),
                    (
                        "missing-variable",
                        {
                            "tempId": "tmp:missing",
                            "nodeType": "variable_get",
                            "x": 400,
                            "y": 900,
                            "variableName": "AccessMissing",
                            "variableGuid": stale_guid,
                            "variableType": "int",
                        },
                        "graph-edit-variable-not-found",
                    ),
                    ("inherited-member", inherited, "graph-edit-variable-not-found"),
                    ("component-property", component, "graph-edit-variable-not-found"),
                    ("function-local", local, "graph-edit-variable-not-found"),
                ]
                for label, node, code in negatives:
                    report["negatives"].append(
                        await _expect_preview_error(
                            driver, label, node, code, event_graph_guid
                        )
                    )

                # Reserved pin name and unsupported type are rejected by the
                # Python contract before the socket, proving the same boundary.
                for label, node, code in (
                    (
                        "reserved-name",
                        {
                            "tempId": "tmp:reserved",
                            "nodeType": "variable_get",
                            "x": 0,
                            "y": 1000,
                            "variableName": "execute",
                            "variableGuid": stale_guid,
                            "variableType": "int",
                        },
                        "graph-edit-variable-name-invalid",
                    ),
                    (
                        "unsupported-type",
                        {
                            "tempId": "tmp:vector",
                            "nodeType": "variable_get",
                            "x": 200,
                            "y": 1000,
                            "variableName": "AccessInt",
                            "variableGuid": int_variable["guid"],
                            "variableType": "vector",
                        },
                        "graph-edit-variable-type-invalid",
                    ),
                ):
                    payload = await driver.call(
                        p5.GRAPH_EDIT_PREVIEW,
                        {
                            "assetPath": ASSET_PATH,
                            "graphGuid": event_graph_guid,
                            "nodes": [node],
                            "connections": [],
                        },
                    )
                    actual = p5._error_code(payload)
                    p5._require(
                        payload.get("ok") is False and actual == code,
                        f"{label} expected {code}, got {actual}",
                        code="negative-mismatch",
                    )
                    report["negatives"].append(
                        {"label": label, "errorCode": actual, "boundary": "python-contract"}
                    )
                    driver.record(label, errorCode=actual, boundary="python-contract")

                # Final state and expected artifact for the fresh-process export.
                context, event_graph, preflight = await driver.graph_state()
                p5._require(
                    preflight.get("packageDirty") is False,
                    "package is dirty after the final Save",
                    code="final-state",
                )
                relative_asset = ASSET_PATH.split("/Game/", 1)[1].split(".", 1)[0]
                asset_filename = project_root / "Content" / (relative_asset + ".uasset")
                disk_sha = _sha256_file(asset_filename)
                expected = {
                    "schemaVersion": "ueak.blueprint-accessor-expected/1.0",
                    "assetPath": ASSET_PATH,
                    "assetFilename": str(asset_filename),
                    "assetSha256": disk_sha,
                    "eventGraphGuid": event_graph_guid,
                    "functionGraphGuid": function_guid,
                    "variables": report["variables"],
                    "eventAccessors": report["event"]["createdAccessors"],
                    "functionAccessors": report["function"]["createdAccessors"],
                    "eventNodeCount": len(event_graph.get("nodes") or []),
                    "eventEdgeCount": len(p5._edge_keys(event_graph)),
                    "functionNodeCount": report["function"]["nodeCount"],
                    "functionEdgeCount": report["function"]["edgeCount"],
                    "variableBindingHash": dirty_saved.get("variableBindingHash"),
                }
                expected_path = project_root / "Saved" / "UEAgentKitAccessorE2E" / "expected.json"
                p5._write_json(expected_path, expected)
                report["expectedPath"] = str(expected_path)
                report["finalEventNodeCount"] = len(event_graph.get("nodes") or [])
                report["finalEventEdgeCount"] = len(p5._edge_keys(event_graph))
                report["finalAssetSha256"] = disk_sha
                report["planIds"] = driver.plan_ids
                report["steps"] = driver.steps
                report["ok"] = True
                return 0
    except p5.ProbeError as exc:
        report["error"] = {"code": exc.code, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001 - always write the artifact
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
