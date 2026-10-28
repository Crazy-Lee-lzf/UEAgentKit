#!/usr/bin/env python3
"""Real MCP A-path probe for one exact existing-edge disconnect.

Fixture has two pre-existing Event `then` -> Branch `execute` edges. The probe
removes exactly one with operation=disconnect, nodes=[], and proves the other
edge and all nodes remain.
"""

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
from mcp import ClientSession  # noqa: E402
from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: E402

ASSET_PATH = cp.ASSET_PATH


def _pin_ref(node: Mapping[str, Any], pin: Mapping[str, Any]) -> dict[str, str]:
    pin_type = pin.get("type") or {}
    return {
        "nodeGuid": str(node.get("guid")),
        "pinGuid": str(pin.get("id")),
        "pinName": str(pin.get("name")),
        "direction": str(pin.get("direction")),
        "category": str(pin_type.get("category", "")),
        "subCategory": str(pin_type.get("subcategory", "")),
    }


def _edges(graph: Mapping[str, Any]) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    nodes = {
        str(node.get("guid")): node
        for node in graph.get("nodes") or []
        if isinstance(node, Mapping)
    }
    for node in nodes.values():
        for pin in node.get("pins") or []:
            if not isinstance(pin, Mapping) or pin.get("direction") != "output":
                continue
            for link in pin.get("links") or []:
                target = nodes.get(str(link.get("targetNodeGuid")))
                if target is None:
                    continue
                target_pin = next(
                    (
                        candidate
                        for candidate in target.get("pins") or []
                        if isinstance(candidate, Mapping)
                        and str(candidate.get("id")) == str(link.get("targetPinId"))
                    ),
                    None,
                )
                if target_pin is None:
                    continue
                edges.append(
                    {
                        "from": _pin_ref(node, pin),
                        "to": _pin_ref(target, target_pin),
                        "fromNode": node,
                        "toNode": target,
                        "fromPin": pin,
                        "toPin": target_pin,
                    }
                )
    return edges


async def _expect_preview_error(
    driver: cp.ConnectDriver,
    label: str,
    nodes: list[dict[str, Any]],
    connections: list[dict[str, Any]],
    graph_guid: str,
    expected_code: str,
    operation: str = "disconnect",
) -> dict[str, Any]:
    payload = await driver.call(
        p5.GRAPH_EDIT_PREVIEW,
        {
            "assetPath": ASSET_PATH,
            "graphGuid": graph_guid,
            "operation": operation,
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
        "schemaVersion": "ueak.blueprint-exact-disconnect-report/1.0",
        "mode": args.mode,
        "assetPath": ASSET_PATH,
        "ok": False,
        "steps": [],
        "negatives": [],
        "evidenceLevel": "A" if args.mode == "full" else "negative-only",
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
                edges = _edges(event_graph)
                required_edges = 1 if args.mode == "gate-off" else 2
                p5._require(
                    len(edges) >= required_edges,
                    f"fixture must expose at least {required_edges} existing edges, got {len(edges)}",
                    code="fixture-shape",
                )
                target = edges[0]
                protected = edges[1] if len(edges) > 1 else edges[0]
                report["eventGraphGuid"] = graph_guid
                report["initialNodeCount"] = len(event_graph.get("nodes") or [])
                report["initialEdgeCount"] = len(edges)
                report["targetEdge"] = {"from": target["from"], "to": target["to"]}
                report["protectedEdge"] = {"from": protected["from"], "to": protected["to"]}

                if args.mode == "gate-off":
                    payload = await driver.call(
                        p5.GRAPH_EDIT_PREVIEW,
                        {
                            "assetPath": ASSET_PATH,
                            "graphGuid": graph_guid,
                            "operation": "disconnect",
                            "nodes": [],
                            "connections": [
                                {"fromExisting": target["from"], "toExisting": target["to"]}
                            ],
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

                target_connection = {
                    "fromExisting": target["from"],
                    "toExisting": target["to"],
                }
                protected_connection = {
                    "fromExisting": protected["from"],
                    "toExisting": protected["to"],
                }
                # Connect mode on an already-linked exact input must fail as
                # connect, not be silently treated as disconnect.
                report["negatives"].append(
                    await _expect_preview_error(
                        driver,
                        "connect-mode-already-linked",
                        [],
                        [target_connection],
                        graph_guid,
                        "graph-edit-existing-pin-already-linked",
                        operation="connect",
                    )
                )
                report["negatives"].append(
                    await _expect_preview_error(
                        driver,
                        "disconnect-with-nodes",
                        [
                            {
                                "tempId": "tmp:get",
                                "nodeType": "variable_get",
                                "x": 0,
                                "y": 900,
                                "variableName": "AccessInt",
                                "variableGuid": cp._context_variable(context, "AccessInt")["guid"],
                                "variableType": "int",
                            }
                        ],
                        [target_connection],
                        graph_guid,
                        "graph-edit-disconnect-invalid-plan",
                    )
                )
                report["negatives"].append(
                    await _expect_preview_error(
                        driver,
                        "disconnect-wrong-pin-guid",
                        [],
                        [
                            {
                                "fromExisting": {
                                    **target["from"],
                                    "pinGuid": "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff",
                                },
                                "toExisting": target["to"],
                            }
                        ],
                        graph_guid,
                        "graph-edit-existing-pin-guid-mismatch",
                    )
                )
                report["negatives"].append(
                    await _expect_preview_error(
                        driver,
                        "disconnect-wrong-metadata",
                        [],
                        [
                            {
                                "fromExisting": {**target["from"], "subCategory": "not-exec"},
                                "toExisting": target["to"],
                            }
                        ],
                        graph_guid,
                        "graph-edit-existing-pin-metadata-mismatch",
                    )
                )

                # Positive disconnect of exactly the target edge.
                preview, applied, saved, plan_id = await driver.preview_apply_save(
                    family="disconnect",
                    preview_tool=p5.GRAPH_EDIT_PREVIEW,
                    apply_tool=p5.GRAPH_EDIT_APPLY,
                    save_tool=p5.GRAPH_EDIT_SAVE,
                    preview_args={
                        "assetPath": ASSET_PATH,
                        "graphGuid": graph_guid,
                        "operation": "disconnect",
                        "nodes": [],
                        "connections": [target_connection],
                    },
                    apply_prefix=p5.GRAPH_EDIT_APPLY_PREFIX,
                    save_prefix=p5.GRAPH_EDIT_SAVE_PREFIX,
                    apply_payload_key="preview",
                )
                p5._require(preview.get("operation") == "disconnect", "preview did not echo disconnect")
                before_hash = str(preview.get("beforeEdgeSetHash", ""))
                after_hash = str(applied.get("afterEdgeSetHash", ""))
                p5._require(before_hash.startswith("sha256:"), "missing beforeEdgeSetHash")
                p5._require(after_hash.startswith("sha256:"), "missing afterEdgeSetHash")
                p5._require(before_hash != after_hash, "edge hash did not change")
                p5._require(int(applied.get("createdNodeCount", -1)) == 0, "disconnect created nodes")
                p5._require(int(applied.get("createdConnectionCount", -1)) == 0, "disconnect created edges")
                p5._require(int(applied.get("removedConnectionCount", -1)) == 1, "disconnect removed != 1")
                p5._require(saved.get("edgeSetHash") == after_hash, "save edge hash mismatch")

                context, event_graph, preflight = await driver.graph_state()
                after_edges = _edges(event_graph)
                p5._require(
                    len(after_edges) == int(report["initialEdgeCount"]) - 1,
                    "disconnect edge count delta != -1",
                )
                p5._require(
                    len(event_graph.get("nodes") or []) == int(report["initialNodeCount"]),
                    "disconnect changed node count",
                )
                keys = {
                    (
                        edge["from"]["nodeGuid"],
                        edge["from"]["pinGuid"],
                        edge["to"]["nodeGuid"],
                        edge["to"]["pinGuid"],
                    )
                    for edge in after_edges
                }
                target_key = (
                    target["from"]["nodeGuid"],
                    target["from"]["pinGuid"],
                    target["to"]["nodeGuid"],
                    target["to"]["pinGuid"],
                )
                protected_key = (
                    protected["from"]["nodeGuid"],
                    protected["from"]["pinGuid"],
                    protected["to"]["nodeGuid"],
                    protected["to"]["pinGuid"],
                )
                p5._require(target_key not in keys, "target edge still exists")
                p5._require(protected_key in keys, "protected edge disappeared")
                relative_asset = ASSET_PATH.split("/Game/", 1)[1].split(".", 1)[0]
                asset_filename = project_root / "Content" / (relative_asset + ".uasset")
                disk_sha = p5._sha256_file(asset_filename)
                p5._require(disk_sha == saved.get("assetSha256"), "asset SHA mismatch")
                expected = {
                    "schemaVersion": "ueak.blueprint-exact-disconnect-expected/1.0",
                    "assetPath": ASSET_PATH,
                    "assetFilename": str(asset_filename),
                    "assetSha256": disk_sha,
                    "eventGraphGuid": graph_guid,
                    "targetEdge": {"from": target["from"], "to": target["to"]},
                    "protectedEdge": {"from": protected["from"], "to": protected["to"]},
                    "beforeEdgeSetHash": before_hash,
                    "afterEdgeSetHash": after_hash,
                    "finalNodeCount": len(event_graph.get("nodes") or []),
                    "finalEdgeCount": len(after_edges),
                }
                expected_path = project_root / "Saved" / "UEAgentKitAccessorE2E" / "disconnect-expected.json"
                p5._write_json(expected_path, expected)
                report["positive"] = {
                    "ok": True,
                    "planId": plan_id,
                    "beforeEdgeSetHash": before_hash,
                    "afterEdgeSetHash": after_hash,
                    "nodeCount": len(event_graph.get("nodes") or []),
                    "edgeCount": len(after_edges),
                    "assetSha256": disk_sha,
                    "expectedPath": str(expected_path),
                }
                driver.record(
                    "disconnect",
                    planId=plan_id,
                    beforeEdgeSetHash=before_hash,
                    afterEdgeSetHash=after_hash,
                    assetSha256=disk_sha,
                )

                # Already-disconnected target negative.
                report["negatives"].append(
                    await _expect_preview_error(
                        driver,
                        "already-disconnected",
                        [],
                        [target_connection],
                        graph_guid,
                        "graph-edit-disconnect-edge-not-found",
                    )
                )

                # Dirty-package negative + cleanup Save.
                dirty_args = {
                    "assetPath": ASSET_PATH,
                    "graphGuid": graph_guid,
                    "nodes": [
                        {
                            "tempId": "tmp:dirty",
                            "nodeType": "variable_get",
                            "x": 300,
                            "y": 900,
                            "variableName": "AccessInt",
                            "variableGuid": cp._context_variable(context, "AccessInt")["guid"],
                            "variableType": "int",
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
                        "operation": "disconnect",
                        "nodes": [],
                        "connections": [protected_connection],
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
                expected["finalEdgeCount"] = len(_edges(event_graph))
                p5._write_json(expected_path, expected)
                report["finalNodeCount"] = len(event_graph.get("nodes") or [])
                report["finalEdgeCount"] = len(_edges(event_graph))
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
