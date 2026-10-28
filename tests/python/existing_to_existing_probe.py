#!/usr/bin/env python3
"""Real MCP A-path probe: one existing output -> one existing unlinked input.

Uses nodes=[] and exactly one connection. The fixture has an unlinked K2 Event
`then` output and a K2 Branch with unlinked `execute` input.
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
import connect_existing_pin_probe as cp  # noqa: E402
from mcp import ClientSession  # noqa: E402
from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: E402

ASSET_PATH = cp.ASSET_PATH


def _find_branch_with_unlinked_execute(manifest: Mapping[str, Any]) -> dict[str, Any]:
    for node in manifest.get("nodes") or []:
        if not isinstance(node, Mapping) or not str(node.get("class", "")).endswith("K2Node_IfThenElse"):
            continue
        for pin in node.get("pins") or []:
            if (
                isinstance(pin, Mapping)
                and pin.get("pinName") == "execute"
                and pin.get("direction") == "input"
                and int(pin.get("linkCount", -1)) == 0
            ):
                return dict(node)
    raise p5.ProbeError("fixture has no Branch with unlinked execute", code="fixture-shape")


def _build_connection(
    event: Mapping[str, Any],
    branch: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    event_then = cp._pin(event, "then", "output")
    branch_execute = cp._pin(branch, "execute", "input")
    connection = {
        "fromExisting": cp._existing_ref(event, event_then),
        "toExisting": cp._existing_ref(branch, branch_execute),
    }
    return connection, {"eventThen": event_then, "branchExecute": branch_execute}


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
        "schemaVersion": "ueak.blueprint-existing-existing-report/1.0",
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
                p5._require(
                    preflight.get("packageDirty") is False,
                    "fixture package is dirty at start",
                    code="fixture-not-clean",
                )
                manifest = cp._load_manifest(project_root)
                event = cp._find_event_with_unlinked_then(manifest)
                branch = _find_branch_with_unlinked_execute(manifest)
                connection, pins = _build_connection(event, branch)
                report["eventGraphGuid"] = graph_guid
                report["initialNodeCount"] = len(event_graph.get("nodes") or [])
                report["initialEdgeCount"] = len(p5._edge_keys(event_graph))
                report["eventNodeGuid"] = event["guid"]
                report["eventThenPinGuid"] = pins["eventThen"]["pinGuid"]
                report["branchNodeGuid"] = branch["guid"]
                report["branchExecutePinGuid"] = pins["branchExecute"]["pinGuid"]

                if args.mode == "gate-off":
                    payload = await driver.call(
                        p5.GRAPH_EDIT_PREVIEW,
                        {
                            "assetPath": ASSET_PATH,
                            "graphGuid": graph_guid,
                            "nodes": [],
                            "connections": [connection],
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

                # Contract / live negatives.
                report["negatives"].append(
                    await _expect_preview_error(
                        driver,
                        "empty-plan",
                        [],
                        [],
                        graph_guid,
                        "graph-edit-empty-plan",
                    )
                )
                int_variable = cp._context_variable(context, "AccessInt")
                report["negatives"].append(
                    await _expect_preview_error(
                        driver,
                        "nodes-with-existing-only-edge",
                        [
                            {
                                "tempId": "tmp:get",
                                "nodeType": "variable_get",
                                "x": 0,
                                "y": 900,
                                "variableName": int_variable["name"],
                                "variableGuid": int_variable["guid"],
                                "variableType": int_variable["variableType"],
                            }
                        ],
                        [connection],
                        graph_guid,
                        "graph-edit-existing-to-existing-plan",
                    )
                )
                # Same node on both ends: use Branch.then -> Branch.Condition.
                branch_then = cp._pin(branch, "then", "output")
                branch_condition = cp._pin(branch, "Condition", "input")
                report["negatives"].append(
                    await _expect_preview_error(
                        driver,
                        "same-node",
                        [],
                        [
                            {
                                "fromExisting": cp._existing_ref(branch, branch_then),
                                "toExisting": cp._existing_ref(branch, branch_condition),
                            }
                        ],
                        graph_guid,
                        "graph-edit-self-connection",
                    )
                )
                report["negatives"].append(
                    await _expect_preview_error(
                        driver,
                        "type-mismatch",
                        [],
                        [
                            {
                                "fromExisting": cp._existing_ref(event, pins["eventThen"]),
                                "toExisting": cp._existing_ref(branch, branch_condition),
                            }
                        ],
                        graph_guid,
                        "graph-edit-pin-type-mismatch",
                    )
                )
                bad_node_guid = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
                bad_pin_guid = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff"
                report["negatives"].append(
                    await _expect_preview_error(
                        driver,
                        "unknown-existing-node",
                        [],
                        [
                            {
                                "fromExisting": {**cp._existing_ref(event, pins["eventThen"]), "nodeGuid": bad_node_guid},
                                "toExisting": cp._existing_ref(branch, pins["branchExecute"]),
                            }
                        ],
                        graph_guid,
                        "graph-edit-existing-node-not-found",
                    )
                )
                report["negatives"].append(
                    await _expect_preview_error(
                        driver,
                        "stale-pin-guid",
                        [],
                        [
                            {
                                "fromExisting": {**cp._existing_ref(event, pins["eventThen"]), "pinGuid": bad_pin_guid},
                                "toExisting": cp._existing_ref(branch, pins["branchExecute"]),
                            }
                        ],
                        graph_guid,
                        "graph-edit-existing-pin-guid-mismatch",
                    )
                )
                report["negatives"].append(
                    await _expect_preview_error(
                        driver,
                        "stale-pin-metadata",
                        [],
                        [
                            {
                                "fromExisting": {
                                    **cp._existing_ref(event, pins["eventThen"]),
                                    "subCategory": "not-exec",
                                },
                                "toExisting": cp._existing_ref(branch, pins["branchExecute"]),
                            }
                        ],
                        graph_guid,
                        "graph-edit-existing-pin-metadata-mismatch",
                    )
                )

                # Positive A-path: nodes=[] and exactly one existing edge.
                preview, applied, saved, plan_id = await driver.preview_apply_save(
                    family="existing-existing",
                    preview_tool=p5.GRAPH_EDIT_PREVIEW,
                    apply_tool=p5.GRAPH_EDIT_APPLY,
                    save_tool=p5.GRAPH_EDIT_SAVE,
                    preview_args={
                        "assetPath": ASSET_PATH,
                        "graphGuid": graph_guid,
                        "nodes": [],
                        "connections": [connection],
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
                    int(applied.get("createdNodeCount", -1)) == 0,
                    "nodes=[] plan unexpectedly created nodes",
                )
                p5._require(
                    int(applied.get("createdConnectionCount", -1)) == 1,
                    "nodes=[] plan did not create exactly one connection",
                )
                p5._require(
                    applied.get("beforeEdgeSetHash") == before_hash
                    and saved.get("edgeSetHash") == after_hash,
                    "edge-set hash chain mismatch",
                )
                context, event_graph, preflight = await driver.graph_state()
                p5._require(
                    len(event_graph.get("nodes") or []) == int(report["initialNodeCount"]),
                    "existing-existing plan changed node count",
                )
                p5._require(
                    len(p5._edge_keys(event_graph)) == int(report["initialEdgeCount"]) + 1,
                    "existing-existing plan did not add exactly one edge",
                )
                live_event = cp._node_by_guid(event_graph, str(event["guid"]))
                live_branch = cp._node_by_guid(event_graph, str(branch["guid"]))
                live_then = cp._pin_by_guid(live_event, pins["eventThen"]["pinGuid"])
                live_execute = cp._pin_by_guid(live_branch, pins["branchExecute"]["pinGuid"])
                p5._require(len(live_then.get("links") or []) == 1, "event.then link count != 1")
                p5._require(len(live_execute.get("links") or []) == 1, "branch.execute link count != 1")
                p5._require(
                    live_then["links"][0]["targetNodeGuid"] == str(branch["guid"])
                    and live_then["links"][0]["targetPinId"] == pins["branchExecute"]["pinGuid"],
                    "event.then is not linked to branch.execute",
                )
                p5._require(
                    live_execute["links"][0]["targetNodeGuid"] == str(event["guid"])
                    and live_execute["links"][0]["targetPinId"] == pins["eventThen"]["pinGuid"],
                    "branch.execute is not linked from event.then",
                )
                relative_asset = ASSET_PATH.split("/Game/", 1)[1].split(".", 1)[0]
                asset_filename = project_root / "Content" / (relative_asset + ".uasset")
                disk_sha = p5._sha256_file(asset_filename)
                p5._require(disk_sha == saved.get("assetSha256"), "asset SHA mismatch")
                expected = {
                    "schemaVersion": "ueak.blueprint-existing-existing-expected/1.0",
                    "assetPath": ASSET_PATH,
                    "assetFilename": str(asset_filename),
                    "assetSha256": disk_sha,
                    "eventGraphGuid": graph_guid,
                    "eventNodeGuid": event["guid"],
                    "eventThenPinGuid": pins["eventThen"]["pinGuid"],
                    "branchNodeGuid": branch["guid"],
                    "branchExecutePinGuid": pins["branchExecute"]["pinGuid"],
                    "beforeEdgeSetHash": before_hash,
                    "afterEdgeSetHash": after_hash,
                    "finalNodeCount": len(event_graph.get("nodes") or []),
                    "finalEdgeCount": len(p5._edge_keys(event_graph)),
                }
                expected_path = project_root / "Saved" / "UEAgentKitAccessorE2E" / "existing-existing-expected.json"
                p5._write_json(expected_path, expected)
                report["positive"] = {
                    "ok": True,
                    "planId": plan_id,
                    "beforeEdgeSetHash": before_hash,
                    "afterEdgeSetHash": after_hash,
                    "nodeCount": len(event_graph.get("nodes") or []),
                    "edgeCount": len(p5._edge_keys(event_graph)),
                    "assetSha256": disk_sha,
                    "expectedPath": str(expected_path),
                }
                driver.record(
                    "existing-existing",
                    planId=plan_id,
                    beforeEdgeSetHash=before_hash,
                    afterEdgeSetHash=after_hash,
                    assetSha256=disk_sha,
                )

                # Already-linked target negative.
                report["negatives"].append(
                    await _expect_preview_error(
                        driver,
                        "already-linked-input",
                        [],
                        [connection],
                        graph_guid,
                        "graph-edit-existing-pin-already-linked",
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
                        "nodes": [],
                        "connections": [connection],
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
    return asyncio.run(_run(build_parser().parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
