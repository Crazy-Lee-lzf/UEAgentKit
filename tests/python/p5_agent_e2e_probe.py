#!/usr/bin/env python3
"""P5 real-Agent Blueprint end-to-end probe.

The probe speaks MCP over stdio to a real UE Agent Kit MCP server, which talks
to a real running UE 5.6 Editor through the Editor Bridge loopback socket.  It
never calls the C++ experimental methods directly and never edits Blueprint
JSON offline.

Sequence on the disposable HostProject asset BP_AgentE2E:

1. GraphEdit: create eight allowlisted nodes and two connections.
2. PinDefaults: set one bool, int, double and string unlinked input default.
3. VariableCreate: create one bool, int, double and string user variable.
4. Rewire: move branch.Condition from not.ReturnValue to bool_or.ReturnValue.
5. NodeRemove: delete the now-isolated not node.
6. Read the final canonical context and write expected.json for the fresh
   process reload commandlet.

Every Apply is confirmed with the exact plan-bound phrase and every write family
is explicitly saved before the next family starts, so each step sees a clean
package and a fresh on-disk revision.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
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

from mcp import ClientSession  # noqa: E402
from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: E402

ASSET_PATH = "/Game/UEAgentKitAgentE2E/BP_AgentE2E.BP_AgentE2E"

GRAPH_EDIT_PREVIEW = "ue_preview_blueprint_graph_edit_experimental"
GRAPH_EDIT_APPLY = "ue_apply_blueprint_graph_edit_experimental"
GRAPH_EDIT_SAVE = "ue_save_blueprint_graph_edit_experimental"
PIN_DEFAULT_PREVIEW = "ue_preview_blueprint_pin_defaults_experimental"
PIN_DEFAULT_APPLY = "ue_apply_blueprint_pin_defaults_experimental"
PIN_DEFAULT_SAVE = "ue_save_blueprint_pin_defaults_experimental"
VARIABLE_PREVIEW = "ue_preview_blueprint_variable_create_experimental"
VARIABLE_APPLY = "ue_apply_blueprint_variable_create_experimental"
VARIABLE_SAVE = "ue_save_blueprint_variable_create_experimental"
REWIRE_PREVIEW = "ue_preview_blueprint_rewire_experimental"
REWIRE_APPLY = "ue_apply_blueprint_rewire_experimental"
REWIRE_SAVE = "ue_save_blueprint_rewire_experimental"
NODE_REMOVE_PREVIEW = "ue_preview_blueprint_node_remove_experimental"
NODE_REMOVE_APPLY = "ue_apply_blueprint_node_remove_experimental"
NODE_REMOVE_SAVE = "ue_save_blueprint_node_remove_experimental"
OPEN_ASSET = "ue_open_asset"
FOCUS_GRAPH = "ue_focus_blueprint_graph"
GRAPH_CONTEXT = "ue_get_focused_blueprint_graph_context"
PREFLIGHT = "ue_get_blueprint_graph_layout_preflight"

GRAPH_EDIT_APPLY_PREFIX = "CREATE EXPERIMENTAL GRAPH NODES "
GRAPH_EDIT_SAVE_PREFIX = "SAVE EXPERIMENTAL GRAPH NODES "
PIN_DEFAULT_APPLY_PREFIX = "SET EXPERIMENTAL BLUEPRINT PIN DEFAULTS "
PIN_DEFAULT_SAVE_PREFIX = "SAVE EXPERIMENTAL BLUEPRINT PIN DEFAULTS "
VARIABLE_APPLY_PREFIX = "CREATE EXPERIMENTAL BLUEPRINT VARIABLES "
VARIABLE_SAVE_PREFIX = "SAVE EXPERIMENTAL BLUEPRINT VARIABLES "
REWIRE_APPLY_PREFIX = "REWIRE EXPERIMENTAL BLUEPRINT PIN "
REWIRE_SAVE_PREFIX = "SAVE EXPERIMENTAL BLUEPRINT REWIRE "
NODE_REMOVE_APPLY_PREFIX = "REMOVE EXPERIMENTAL BLUEPRINT NODE "
NODE_REMOVE_SAVE_PREFIX = "SAVE EXPERIMENTAL BLUEPRINT NODE REMOVE "

NODE_PLAN = [
    {"tempId": "tmp:not", "nodeType": "not_bool", "x": -900, "y": -240},
    {"tempId": "tmp:or", "nodeType": "bool_or", "x": -900, "y": -20},
    {"tempId": "tmp:branch", "nodeType": "branch", "x": -500, "y": -240},
    {"tempId": "tmp:seq", "nodeType": "sequence", "x": -180, "y": -240},
    {"tempId": "tmp:add", "nodeType": "int_add", "x": -900, "y": 260},
    {"tempId": "tmp:branch2", "nodeType": "branch", "x": -500, "y": 200},
    {"tempId": "tmp:concat", "nodeType": "string_concat", "x": -500, "y": 460},
    {"tempId": "tmp:dadd", "nodeType": "double_add", "x": -900, "y": 520},
]
CONNECTION_PLAN = [
    {"from": "tmp:not", "fromPin": "ReturnValue", "to": "tmp:branch", "toPin": "Condition"},
    {"from": "tmp:branch", "fromPin": "then", "to": "tmp:seq", "toPin": "execute"},
]
VARIABLE_PLAN = [
    {"name": "AgentFlag", "type": "bool", "defaultValue": "true"},
    {"name": "AgentCount", "type": "int", "defaultValue": "7"},
    {"name": "AgentScale", "type": "double", "defaultValue": "1.5"},
    {"name": "AgentLabel", "type": "string", "defaultValue": "agent"},
]
VARIABLE_CATEGORY = {
    "bool": ("bool", "None"),
    "int": ("int", "None"),
    "double": ("real", "double"),
    "string": ("string", "None"),
}


class ProbeError(RuntimeError):
    def __init__(self, message: str, *, code: str = "probe-failed") -> None:
        super().__init__(message)
        self.code = code


def _require(value: bool, message: str, *, code: str = "probe-failed") -> None:
    if not value:
        raise ProbeError(message, code=code)


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _tool_payload(result: Any) -> dict[str, Any]:
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured
    content = getattr(result, "content", None) or []
    for item in content:
        text = getattr(item, "text", None)
        if isinstance(text, str):
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
    text_parts = [
        text
        for item in content
        if isinstance(text := getattr(item, "text", None), str) and text
    ]
    detail = " | ".join(text_parts)[:1500]
    raise ProbeError(
        f"MCP tool returned no structured payload (isError={getattr(result, 'isError', None)}): {detail or structured!r}"
    )


def _flatten_exception(exc: BaseException) -> str:
    parts: list[str] = []
    seen: set[int] = set()

    def visit(item: BaseException) -> None:
        if id(item) in seen:
            return
        seen.add(id(item))
        nested = getattr(item, "exceptions", None)
        if nested:
            for child in nested:
                visit(child)
            return
        text = f"{type(item).__name__}: {item}"
        parts.append(text)

    visit(exc)
    return " | ".join(parts) if parts else f"{type(exc).__name__}: {exc}"


def _error_code(payload: Mapping[str, Any]) -> str:
    error = payload.get("error")
    return str(error.get("code", "")) if isinstance(error, dict) else ""


def _unwrap(payload: Mapping[str, Any], tool: str) -> dict[str, Any]:
    if payload.get("ok") is False:
        raise ProbeError(
            f"{tool} failed: {_error_code(payload)}: {payload.get('error')}",
            code=_error_code(payload) or "tool-error",
        )
    result = payload.get("result")
    _require(isinstance(result, dict), f"{tool} returned no result object")
    return result


def _graph_from_context(payload: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    _require(bool(payload.get("available")), f"focused graph context unavailable: {payload}")
    context = payload.get("context")
    _require(isinstance(context, dict), "focused graph context has no context object")
    graphs = context.get("graphs")
    _require(isinstance(graphs, list), "canonical context has no graphs array")
    graph_guid = str(payload.get("graphGuid", "")).lower()
    for graph in graphs:
        if isinstance(graph, dict) and str(graph.get("guid", "")).lower() == graph_guid:
            return context, graph
    raise ProbeError(f"canonical context does not contain graph {graph_guid}")


def _node_by_guid(graph: Mapping[str, Any], node_guid: str) -> dict[str, Any]:
    for node in graph.get("nodes") or []:
        if isinstance(node, dict) and str(node.get("guid", "")).lower() == node_guid:
            return node
    raise ProbeError(f"node {node_guid} is missing from the canonical context")


def _pin_by_name(node: Mapping[str, Any], pin_name: str, direction: str) -> dict[str, Any]:
    for pin in node.get("pins") or []:
        if (
            isinstance(pin, dict)
            and str(pin.get("name")) == pin_name
            and str(pin.get("direction")) == direction
        ):
            return pin
    raise ProbeError(f"pin {node.get('guid')}.{pin_name} ({direction}) is missing")


def _edge_keys(graph: Mapping[str, Any]) -> list[str]:
    keys: list[str] = []
    for node in graph.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        for pin in node.get("pins") or []:
            if not isinstance(pin, dict) or pin.get("direction") != "output":
                continue
            for link in pin.get("links") or []:
                if not isinstance(link, dict):
                    continue
                keys.append(
                    f"{node['guid']}|{pin['id']}>{link['targetNodeGuid']}|{link['targetPinId']}"
                )
    return sorted(keys)


def _build_expected(
    asset_filename: str,
    asset_sha256: str,
    graph: Mapping[str, Any],
    variables: list[dict[str, str]],
    context_variables: list[dict[str, Any]],
    deleted_node_guids: list[str],
) -> dict[str, Any]:
    nodes = []
    for node in graph.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        pins = []
        for pin in node.get("pins") or []:
            if not isinstance(pin, dict):
                continue
            pin_type = pin.get("type") if isinstance(pin.get("type"), dict) else {}
            pins.append(
                {
                    "name": pin.get("name"),
                    "id": pin.get("id"),
                    "direction": pin.get("direction"),
                    "category": pin_type.get("category", ""),
                    "subcategory": pin_type.get("subcategory", ""),
                    "defaultValue": pin.get("defaultValue", ""),
                }
            )
        nodes.append({"guid": node.get("guid"), "class": node.get("class"), "pins": pins})
    edges = []
    for node in graph.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        for pin in node.get("pins") or []:
            if not isinstance(pin, dict) or pin.get("direction") != "output":
                continue
            for link in pin.get("links") or []:
                if not isinstance(link, dict):
                    continue
                edges.append(
                    {
                        "fromNodeGuid": node.get("guid"),
                        "fromPinId": pin.get("id"),
                        "toNodeGuid": link.get("targetNodeGuid"),
                        "toPinId": link.get("targetPinId"),
                    }
                )
    live_types = {
        str(item.get("name")): item.get("type") or {}
        for item in context_variables
        if isinstance(item, dict)
    }
    variable_items = []
    for variable in variables:
        fallback_category, fallback_subcategory = VARIABLE_CATEGORY[variable["type"]]
        live_type = live_types.get(variable["name"], {})
        variable_items.append(
            {
                "name": variable["name"],
                "category": live_type.get("category") or fallback_category,
                "subcategory": live_type.get("subcategory") or fallback_subcategory,
                "defaultValue": variable["defaultValue"],
            }
        )
    return {
        "schemaVersion": "ueak.blueprint-agent-e2e-expected/1.0",
        "assetPath": ASSET_PATH,
        "assetFilename": asset_filename,
        "assetSha256": asset_sha256,
        "graphGuid": graph.get("guid"),
        "nodes": nodes,
        "edges": edges,
        "variables": variable_items,
        "deletedNodeGuids": deleted_node_guids,
    }


class Driver:
    def __init__(self, session: ClientSession, project_root: Path) -> None:
        self.session = session
        self.project_root = project_root
        self.steps: list[dict[str, Any]] = []
        self.plan_ids: dict[str, str] = {}
        self.last_apply_flags: dict[str, Any] = {}

    async def call(self, tool: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        result = await self.session.call_tool(tool, dict(arguments or {}))
        try:
            return _tool_payload(result)
        except ProbeError as exc:
            raise ProbeError(f"{tool}: {exc}", code="tool-payload") from exc

    async def read(self, tool: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = await self.call(tool, arguments)
        if payload.get("ok") is False:
            raise ProbeError(
                f"{tool} failed: {_error_code(payload)}",
                code=_error_code(payload) or "tool-error",
            )
        result = payload.get("result")
        return result if isinstance(result, dict) else payload

    async def call_expect_error(
        self, tool: str, arguments: Mapping[str, Any], codes: tuple[str, ...]
    ) -> dict[str, Any]:
        payload = await self.call(tool, arguments)
        actual = _error_code(payload)
        if payload.get("ok") is not False or actual not in codes:
            raise ProbeError(
                f"{tool} expected one of {codes}, got ok={payload.get('ok')} code={actual}",
                code="unexpected-tool-result",
            )
        return payload

    def record(self, name: str, **fields: Any) -> None:
        entry = {"step": name, **fields}
        if self.last_apply_flags:
            entry["applyFlags"] = dict(self.last_apply_flags)
        self.steps.append(entry)

    async def open_and_read(self) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        opened = await self.read(OPEN_ASSET, {"asset_path": ASSET_PATH})
        _require(
            opened.get("openAfter") is True,
            f"open_asset did not open the fixture: {opened}",
            code="asset-open-failed",
        )
        focused = await self.read(FOCUS_GRAPH, {"asset_path": ASSET_PATH})
        _require(
            focused.get("focused") is True,
            f"focus_blueprint_graph did not focus the default event graph: {focused}",
            code="graph-not-focused",
        )
        context_payload = await self.read(GRAPH_CONTEXT, {})
        try:
            context, graph = _graph_from_context(context_payload)
        except ProbeError as exc:
            raise ProbeError(
                f"{exc} (openAsset={json.dumps(opened)[:400]})",
                code="graph-not-focused",
            ) from exc
        preflight = await self.read(PREFLIGHT, {})
        _require(
            preflight.get("packageDirty") is False,
            f"fixture package is dirty at start: {preflight.get('packageDirty')}",
            code="fixture-not-clean",
        )
        return context, graph, preflight

    async def preview_apply_save(
        self,
        *,
        family: str,
        preview_tool: str,
        apply_tool: str,
        save_tool: str,
        preview_args: Mapping[str, Any],
        apply_prefix: str,
        save_prefix: str,
        apply_payload_key: str = "preview_result",
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
        preview = _unwrap(await self.call(preview_tool, preview_args), preview_tool)
        plan_id = str(preview.get("planId", ""))
        _require(plan_id.startswith("sha256:"), f"{family} preview has no planId")
        confirmation = str(preview.get("requiredConfirmation") or "")
        _require(
            confirmation.startswith(apply_prefix),
            f"{family} preview did not return the expected confirmation",
            code="confirmation-missing",
        )
        applied = _unwrap(
            await self.call(apply_tool, {apply_payload_key: preview, "confirmation": confirmation}),
            apply_tool,
        )
        _require(applied.get("applied") is True, f"{family} apply did not report applied")
        _require(applied.get("undoVerified") is True, f"{family} Undo was not verified")
        _require(applied.get("redoVerified") is True, f"{family} Redo was not verified")
        _require(applied.get("saved") is False, f"{family} apply unexpectedly saved")
        _require(applied.get("compiled") is False, f"{family} apply unexpectedly compiled")
        save_confirmation = str(applied.get("requiredSaveConfirmation") or "")
        _require(
            save_confirmation.startswith(save_prefix),
            f"{family} apply did not return the expected save confirmation",
            code="confirmation-missing",
        )
        saved = _unwrap(
            await self.call(
                save_tool,
                {"apply_result": applied, "confirmation": save_confirmation},
            ),
            save_tool,
        )
        _require(saved.get("saved") is True, f"{family} save did not report saved")
        _require(
            saved.get("packageDirty") is False,
            f"{family} package is still dirty after Save",
        )
        _require(saved.get("compiled") is False, f"{family} Save unexpectedly compiled")
        self.plan_ids[family] = plan_id
        self.last_apply_flags = {
            "applied": applied.get("applied"),
            "undoVerified": applied.get("undoVerified"),
            "redoVerified": applied.get("redoVerified"),
            "applySaved": applied.get("saved"),
            "applyCompiled": applied.get("compiled"),
            "saveSaved": saved.get("saved"),
            "savePackageDirty": saved.get("packageDirty"),
        }
        return preview, applied, saved, plan_id

    async def step_graph_edit(
        self, graph_guid: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        preview, applied, saved, plan_id = await self.preview_apply_save(
            family="graph-edit",
            preview_tool=GRAPH_EDIT_PREVIEW,
            apply_tool=GRAPH_EDIT_APPLY,
            save_tool=GRAPH_EDIT_SAVE,
            preview_args={
                "assetPath": ASSET_PATH,
                "graphGuid": graph_guid,
                "nodes": NODE_PLAN,
                "connections": CONNECTION_PLAN,
            },
            apply_prefix=GRAPH_EDIT_APPLY_PREFIX,
            save_prefix=GRAPH_EDIT_SAVE_PREFIX,
            apply_payload_key="preview",
        )
        created = {
            str(item["tempId"]): str(item["nodeId"]).removeprefix("n:")
            for item in applied.get("createdNodes") or []
            if isinstance(item, dict)
        }
        _require(len(created) == len(NODE_PLAN), f"graph edit created {len(created)} nodes")
        self.record(
            "graph-edit",
            planId=plan_id,
            createdNodes=created,
            createdNodeCount=applied.get("createdNodeCount"),
            createdConnectionCount=applied.get("createdConnectionCount"),
            nodeCountAfter=applied.get("nodeCountAfter"),
            linkCountAfter=applied.get("linkCountAfter"),
            assetSha256=saved.get("assetSha256"),
            packageRevision=saved.get("packageRevision"),
        )
        return created, saved

    async def read_state(self) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        context_payload = await self.read(GRAPH_CONTEXT, {})
        context, graph = _graph_from_context(context_payload)
        preflight = await self.read(PREFLIGHT, {})
        return context, graph, preflight

    async def step_pin_defaults(
        self, graph_guid: str, created: Mapping[str, str]
    ) -> tuple[list[dict[str, str]], dict[str, Any]]:
        context, graph, preflight = await self.read_state()
        defaults = []
        targets = [
            ("tmp:branch2", "Condition", "input", "bool", "true"),
            ("tmp:add", "A", "input", "int", "7"),
            ("tmp:dadd", "A", "input", "double", "1.5"),
            ("tmp:concat", "A", "input", "string", "agent"),
        ]
        for temp_id, pin_name, direction, value_type, value in targets:
            node = _node_by_guid(graph, created[temp_id])
            pin = _pin_by_name(node, pin_name, direction)
            _require(
                not (pin.get("links") or []),
                f"{pin_name} is unexpectedly linked before the default edit",
                code="fixture-shape",
            )
            defaults.append(
                {
                    "nodeGuid": node["guid"],
                    "pinName": pin["name"],
                    "pinGuid": pin["id"],
                    "type": value_type,
                    "value": value,
                }
            )
        preview, applied, saved, plan_id = await self.preview_apply_save(
            family="pin-defaults",
            preview_tool=PIN_DEFAULT_PREVIEW,
            apply_tool=PIN_DEFAULT_APPLY,
            save_tool=PIN_DEFAULT_SAVE,
            preview_args={
                "asset_path": ASSET_PATH,
                "graph_guid": graph_guid,
                "defaults": defaults,
            },
            apply_prefix=PIN_DEFAULT_APPLY_PREFIX,
            save_prefix=PIN_DEFAULT_SAVE_PREFIX,
        )
        self.record(
            "pin-defaults",
            planId=plan_id,
            defaults=defaults,
            assetSha256=saved.get("assetSha256"),
            packageRevision=saved.get("packageRevision"),
        )
        return defaults, saved

    async def step_variables(self) -> tuple[list[dict[str, str]], dict[str, Any]]:
        preview, applied, saved, plan_id = await self.preview_apply_save(
            family="variables",
            preview_tool=VARIABLE_PREVIEW,
            apply_tool=VARIABLE_APPLY,
            save_tool=VARIABLE_SAVE,
            preview_args={"asset_path": ASSET_PATH, "variables": VARIABLE_PLAN},
            apply_prefix=VARIABLE_APPLY_PREFIX,
            save_prefix=VARIABLE_SAVE_PREFIX,
        )
        self.record(
            "variables",
            planId=plan_id,
            createdVariables=applied.get("createdVariables"),
            assetSha256=saved.get("assetSha256"),
            packageRevision=saved.get("packageRevision"),
        )
        return VARIABLE_PLAN, saved

    async def step_rewire(
        self, graph_guid: str, created: Mapping[str, str]
    ) -> dict[str, Any]:
        context, graph, preflight = await self.read_state()
        branch = _node_by_guid(graph, created["tmp:branch"])
        not_node = _node_by_guid(graph, created["tmp:not"])
        or_node = _node_by_guid(graph, created["tmp:or"])
        target_pin = _pin_by_name(branch, "Condition", "input")
        old_pin = _pin_by_name(not_node, "ReturnValue", "output")
        new_pin = _pin_by_name(or_node, "ReturnValue", "output")
        _require(
            len(target_pin.get("links") or []) == 1
            and target_pin["links"][0]["targetPinId"] == old_pin["id"],
            "branch.Condition is not linked from not.ReturnValue",
            code="fixture-shape",
        )
        edge_hash = str(preflight.get("rewireEdgeSetHash", ""))
        _require(
            edge_hash.startswith("sha256:"),
            "preflight did not expose rewireEdgeSetHash",
            code="missing-edge-hash",
        )
        preview, applied, saved, plan_id = await self.preview_apply_save(
            family="rewire",
            preview_tool=REWIRE_PREVIEW,
            apply_tool=REWIRE_APPLY,
            save_tool=REWIRE_SAVE,
            preview_args={
                "asset_path": ASSET_PATH,
                "graph_guid": graph_guid,
                "operation": "rewire",
                "target_node_guid": branch["guid"],
                "target_pin_name": target_pin["name"],
                "target_pin_guid": target_pin["id"],
                "old_source_node_guid": not_node["guid"],
                "old_source_pin_name": old_pin["name"],
                "old_source_pin_guid": old_pin["id"],
                "new_source_node_guid": or_node["guid"],
                "new_source_pin_name": new_pin["name"],
                "new_source_pin_guid": new_pin["id"],
                "expected_edge_set_hash": edge_hash,
            },
            apply_prefix=REWIRE_APPLY_PREFIX,
            save_prefix=REWIRE_SAVE_PREFIX,
        )
        applied_hash = str(applied.get("edgeSetHashAfter", ""))
        saved_hash = str(saved.get("edgeSetHashAfter", ""))
        _require(
            applied_hash == saved_hash,
            f"rewire apply/save edge hash mismatch: apply={applied_hash} save={saved_hash}",
            code="rewire-hash-mismatch",
        )
        self.record(
            "rewire",
            planId=plan_id,
            expectedEdgeSetHash=edge_hash,
            edgeSetHashAfter=applied.get("edgeSetHashAfter"),
            assetSha256=saved.get("assetSha256"),
            packageRevision=saved.get("packageRevision"),
        )
        return saved

    async def step_node_remove(
        self, graph_guid: str, created: Mapping[str, str]
    ) -> dict[str, Any]:
        context, graph, preflight = await self.read_state()
        not_node = _node_by_guid(graph, created["tmp:not"])
        _require(
            not (not_node.get("pins") and any(pin.get("links") for pin in not_node["pins"])),
            "not node still has links after rewire",
            code="fixture-shape",
        )
        edge_hash = str(preflight.get("nodeRemoveEdgeSetHash", ""))
        _require(
            edge_hash.startswith("sha256:"),
            "preflight did not expose nodeRemoveEdgeSetHash",
            code="missing-edge-hash",
        )
        preview, applied, saved, plan_id = await self.preview_apply_save(
            family="node-remove",
            preview_tool=NODE_REMOVE_PREVIEW,
            apply_tool=NODE_REMOVE_APPLY,
            save_tool=NODE_REMOVE_SAVE,
            preview_args={
                "asset_path": ASSET_PATH,
                "graph_guid": graph_guid,
                "node_guid": not_node["guid"],
                "expected_node_class": not_node["class"],
                "expected_edge_set_hash": edge_hash,
                "removed_edges": [],
            },
            apply_prefix=NODE_REMOVE_APPLY_PREFIX,
            save_prefix=NODE_REMOVE_SAVE_PREFIX,
        )
        _require(
            preview.get("isolatedNode") is True,
            "node removal preview did not report an isolated node",
        )
        self.record(
            "node-remove",
            planId=plan_id,
            expectedEdgeSetHash=edge_hash,
            deletedNodeGuid=not_node["guid"],
            assetSha256=saved.get("assetSha256"),
            packageRevision=saved.get("packageRevision"),
        )
        return saved


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
        "schemaVersion": "ueak.blueprint-agent-e2e-report/1.0",
        "mode": args.mode,
        "assetPath": ASSET_PATH,
        "ok": False,
        "steps": [],
    }
    driver: Driver | None = None
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
                experimental = {
                    GRAPH_EDIT_PREVIEW,
                    GRAPH_EDIT_APPLY,
                    GRAPH_EDIT_SAVE,
                    PIN_DEFAULT_PREVIEW,
                    VARIABLE_PREVIEW,
                    REWIRE_PREVIEW,
                    NODE_REMOVE_PREVIEW,
                }
                missing = sorted(experimental - tools)
                _require(not missing, f"MCP server did not register: {missing}", code="tool-missing")
                report["toolCount"] = len(tools)

                driver = Driver(session, project_root)
                if args.mode == "cross-project":
                    payload = await driver.call(OPEN_ASSET, {"asset_path": ASSET_PATH})
                    code = _error_code(payload)
                    _require(
                        payload.get("ok") is False and code == "live-editor-project-mismatch",
                        f"cross-project guard did not reject the call: ok={payload.get('ok')} code={code}",
                        code="cross-project-guard",
                    )
                    report["crossProjectError"] = {
                        "code": code,
                        "message": payload.get("error", {}).get("message", ""),
                        "requestedProject": str(server_project),
                    }
                    report["steps"] = driver.steps
                    report["ok"] = True
                    return 0
                context, graph, preflight = await driver.open_and_read()
                graph_guid = str(graph["guid"])
                report["graphGuid"] = graph_guid
                report["initialNodeCount"] = len(graph.get("nodes") or [])
                report["initialEdgeCount"] = len(_edge_keys(graph))

                if args.mode == "cross-project":
                    payload = await driver.call(OPEN_ASSET, {"asset_path": ASSET_PATH})
                    code = _error_code(payload)
                    _require(
                        payload.get("ok") is False and code == "live-editor-project-mismatch",
                        f"cross-project guard did not reject the call: ok={payload.get('ok')} code={code}",
                        code="cross-project-guard",
                    )
                    report["crossProjectError"] = {
                        "code": code,
                        "message": payload.get("error", {}).get("message", ""),
                        "requestedProject": str(server_project),
                    }
                    report["steps"] = driver.steps
                    report["ok"] = True
                    return 0

                if args.mode == "gate-off":
                    payload = await driver.call_expect_error(
                        GRAPH_EDIT_PREVIEW,
                        {
                            "assetPath": ASSET_PATH,
                            "graphGuid": graph_guid,
                            "nodes": NODE_PLAN[:1],
                            "connections": [],
                        },
                        ("graph-edit-experimental-disabled", "live-editor-capability-missing"),
                    )
                    report["gateOffError"] = {
                        "code": _error_code(payload),
                        "message": payload.get("error", {}).get("message", ""),
                    }
                    report["steps"] = driver.steps
                    report["ok"] = True
                    return 0

                created, _ = await driver.step_graph_edit(graph_guid)
                context, graph, preflight = await driver.read_state()
                expected_nodes = int(report["initialNodeCount"]) + len(NODE_PLAN)
                _require(
                    len(graph.get("nodes") or []) == expected_nodes,
                    f"expected {expected_nodes} nodes after GraphEdit, got {len(graph.get('nodes') or [])}",
                    code="graph-edit-state",
                )
                _require(
                    len(_edge_keys(graph)) == 2,
                    "expected two edges after GraphEdit",
                    code="graph-edit-state",
                )
                report["nodeCountAfterGraphEdit"] = len(graph.get("nodes") or [])
                report["edgeCountAfterGraphEdit"] = len(_edge_keys(graph))

                await driver.step_pin_defaults(graph_guid, created)
                _, _, preflight = await driver.read_state()
                _require(
                    preflight.get("packageDirty") is False,
                    "package is dirty after pin-default Save",
                    code="save-state",
                )

                await driver.step_variables()
                await driver.step_rewire(graph_guid, created)
                final_save = await driver.step_node_remove(graph_guid, created)

                context, graph, preflight = await driver.read_state()
                final_edges = _edge_keys(graph)
                final_nodes = graph.get("nodes") or []
                _require(
                    len(final_nodes) == expected_nodes - 1 and len(final_edges) == 2,
                    f"unexpected final state nodes={len(final_nodes)} edges={len(final_edges)}",
                    code="final-state",
                )
                for node in final_nodes:
                    _require(
                        str(node.get("guid")) != created["tmp:not"],
                        "deleted node is still present in the live graph",
                        code="final-state",
                    )
                variables = context.get("variables") or []
                names = {str(item.get("name")) for item in variables if isinstance(item, dict)}
                _require(
                    names == {item["name"] for item in VARIABLE_PLAN},
                    f"live variables mismatch: {sorted(names)}",
                    code="final-state",
                )

                relative_asset = ASSET_PATH.split("/Game/", 1)[1].split(".", 1)[0]
                asset_filename = project_root / "Content" / (relative_asset + ".uasset")
                disk_sha = _sha256_file(asset_filename)
                save_sha = str(final_save.get("assetSha256", ""))
                _require(
                    disk_sha == save_sha,
                    f"disk asset hash {disk_sha} does not match the Save result {save_sha}",
                    code="asset-hash",
                )
                expected = _build_expected(
                    str(asset_filename),
                    disk_sha,
                    graph,
                    VARIABLE_PLAN,
                    variables,
                    [created["tmp:not"]],
                )
                expected_path = project_root / "Saved" / "UEAgentKitAgentE2E" / "expected.json"
                _write_json(expected_path, expected)
                report["expectedPath"] = str(expected_path)
                report["finalNodeCount"] = len(final_nodes)
                report["finalEdgeCount"] = len(final_edges)
                report["finalEdgeKeys"] = final_edges
                report["finalAssetSha256"] = disk_sha
                report["variables"] = sorted(names)
                report["deletedNodeGuid"] = created["tmp:not"]
                report["planIds"] = driver.plan_ids
                report["steps"] = driver.steps
                report["ok"] = True
                return 0
    except ProbeError as exc:
        report["error"] = {"code": exc.code, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001 - report every failure in the artifact
        report["error"] = {"code": "unexpected", "message": _flatten_exception(exc)}
    finally:
        if driver is not None:
            report["steps"] = driver.steps
        _write_json(report_root / "probe-report.json", report)
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
