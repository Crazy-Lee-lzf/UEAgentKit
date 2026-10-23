"""Build a real read-only LayoutPlan from one focused Blueprint graph.

The Editor Bridge only supplies *data*: the P6 preflight identity plus the same
canonical full-context object used by the commandlet exporter.  This module
performs the single authoritative conversion and invokes the P0 Python layout
algorithm.  It never writes a Blueprint, opens a transaction, saves a package,
or treats a same-process preview as acceptance evidence.
"""

from __future__ import annotations

from typing import Any, Mapping

from .editor_bridge import LiveEditorError
from .graph_contracts import (
    BlueprintContextImportError,
    preview_graph_layout,
    typed_graph_from_blueprint_context,
)
from .graph_contracts.layout import recompute_layout_hash


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LiveEditorError(
            "graph-layout-preview-invalid-response",
            f"The Editor Bridge {name} is not a JSON object.",
        )
    return value


def _string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise LiveEditorError(
            "graph-layout-preview-invalid-response",
            f"The Editor Bridge {name} is missing or empty.",
        )
    return value


def _node_id(value: str) -> str:
    text = value.strip().lower()
    if text.startswith("n:"):
        text = text[2:]
    return "n:" + text


def _selected_scope(selection_payload: Any) -> list[str]:
    if not isinstance(selection_payload, dict):
        return []
    selected = selection_payload.get("selectedNodes")
    if not isinstance(selected, list):
        return []
    result: list[str] = []
    for item in selected:
        if isinstance(item, dict) and isinstance(item.get("nodeGuid"), str):
            result.append(_node_id(item["nodeGuid"]))
    return sorted(set(result))


def build_preview(
    preflight_payload: Mapping[str, Any],
    context_payload: Mapping[str, Any],
    *,
    mode: str = "full",
    scope_node_ids: list[str] | None = None,
    selection_payload: Mapping[str, Any] | None = None,
    spacing_x: int = 96,
    spacing_y: int = 64,
    target_width: int = 1800,
    seed: int = 0,
) -> dict[str, Any]:
    """Return a plan for exactly one focused graph, or fail closed.

    ``preflight_payload`` and ``context_payload`` must be raw Bridge results
    (not the MCP wrapper).  The caller is responsible for passing only the
    fixed-project Live Editor service.
    """

    if mode not in ("full", "selection", "incremental"):
        raise LiveEditorError(
            "graph-layout-preview-invalid-mode",
            "mode must be full, selection, or incremental.",
        )
    preflight = _mapping(preflight_payload, "preflight")
    if preflight.get("available") is not True:
        raise LiveEditorError(
            str(preflight.get("reasonCode") or "graph-layout-editor-unavailable"),
            str(preflight.get("reason") or "The focused graph preflight is unavailable."),
        )
    if preflight.get("complete") is not True or preflight.get("nodesTruncated") is True:
        raise LiveEditorError(
            "graph-layout-context-incomplete",
            "A truncated preflight cannot be used to build a complete LayoutPlan.",
        )
    if preflight.get("packageDirty") is not False:
        raise LiveEditorError(
            "graph-layout-package-dirty",
            "Preview requires a clean package so its on-disk revision is aligned.",
        )
    if preflight.get("pieState") != "stopped":
        raise LiveEditorError(
            "graph-layout-pie-active",
            "Preview requires PIE/SIE to be stopped.",
        )

    envelope = _mapping(context_payload, "context envelope")
    if envelope.get("available") is not True or envelope.get("readOnly") is not True:
        raise LiveEditorError(
            str(envelope.get("reasonCode") or "graph-layout-context-unavailable"),
            str(envelope.get("reason") or "The focused graph context is unavailable."),
        )
    asset_path = _string(preflight.get("assetPath"), "preflight.assetPath")
    graph_guid = _string(preflight.get("graphGuid"), "preflight.graphGuid")
    if _string(envelope.get("assetPath"), "context.assetPath") != asset_path:
        raise LiveEditorError(
            "graph-layout-context-identity-mismatch",
            "The context assetPath does not equal the exact focused preflight assetPath.",
        )
    if _string(envelope.get("graphGuid"), "context.graphGuid") != graph_guid:
        raise LiveEditorError(
            "graph-layout-context-identity-mismatch",
            "The context graphGuid does not equal the exact focused graph.",
        )
    if _string(envelope.get("packageRevision"), "context.packageRevision") != _string(
        preflight.get("packageRevision"), "preflight.packageRevision"
    ):
        raise LiveEditorError(
            "graph-layout-revision-conflict",
            "The context package revision does not equal the focused preflight revision.",
        )
    if envelope.get("packageDirty") is not False:
        raise LiveEditorError(
            "graph-layout-package-dirty",
            "The context was exported from a dirty package.",
        )
    if _string(envelope.get("graphFingerprint"), "context.graphFingerprint") != _string(
        preflight.get("graphFingerprint"), "preflight.graphFingerprint"
    ):
        raise LiveEditorError(
            "graph-layout-fingerprint-conflict",
            "The context fingerprint does not equal the focused preflight fingerprint.",
        )

    document = _mapping(envelope.get("context"), "context.context")
    if _string(document.get("assetPath"), "context.document.assetPath") != asset_path:
        raise LiveEditorError(
            "graph-layout-context-identity-mismatch",
            "The canonical export assetPath does not equal the focused asset.",
        )
    revision = _mapping(document.get("revision"), "context.document.revision")
    if revision.get("available") is not True or revision.get("value") != preflight.get(
        "packageRevision"
    ):
        raise LiveEditorError(
            "graph-layout-revision-conflict",
            "The canonical export package revision is unavailable or stale.",
        )
    if revision.get("packageDirty") is not False:
        raise LiveEditorError(
            "graph-layout-package-dirty",
            "The canonical export reports a dirty package.",
        )

    source_metadata = {
        "kind": "live-editor",
        "sessionId": _string(preflight.get("sessionId"), "preflight.sessionId"),
        "pluginBuildHash": _string(
            preflight.get("pluginBuildHash"), "preflight.pluginBuildHash"
        ),
        "exportedAtUtc": _string(envelope.get("exportedAtUtc"), "context.exportedAtUtc"),
    }
    try:
        graph = typed_graph_from_blueprint_context(
            document,
            graph_guid=graph_guid,
            source_metadata=source_metadata,
            graph_editable=bool(preflight.get("editable")),
        )
    except BlueprintContextImportError as exc:
        raise LiveEditorError(
            exc.code,
            f"Canonical context import rejected the graph: {exc}",
            details={"path": exc.path, **exc.context},
        ) from exc

    if graph["asset"]["path"] != asset_path:
        raise LiveEditorError(
            "graph-layout-context-identity-mismatch",
            "The imported graph asset does not equal the focused asset.",
        )
    if graph["asset"]["revision"] != preflight.get("packageRevision"):
        raise LiveEditorError(
            "graph-layout-revision-conflict",
            "The imported graph revision does not equal the focused revision.",
        )
    if graph["graph"]["guid"] != graph_guid:
        raise LiveEditorError(
            "graph-layout-context-identity-mismatch",
            "The imported graph GUID does not equal the focused GraphGuid.",
        )
    node_ids = {str(node["nodeId"]) for node in graph["nodes"]}
    if len(node_ids) != int(preflight.get("nodeCount", -1)):
        raise LiveEditorError(
            "graph-layout-context-incomplete",
            "The imported node count does not equal the focused preflight node count.",
        )

    scope: list[str] | None = None
    if mode != "full":
        if scope_node_ids is None:
            scope = _selected_scope(selection_payload)
        else:
            if not isinstance(scope_node_ids, list) or not scope_node_ids:
                raise LiveEditorError(
                    "graph-layout-preview-selection-empty",
                    "selection/incremental preview requires a non-empty scope.",
                )
            scope = sorted({_node_id(str(item)) for item in scope_node_ids})
        if not scope:
            raise LiveEditorError(
                "graph-layout-preview-selection-empty",
                "No selected node is available for selection/incremental preview.",
            )
        unknown = sorted(set(scope) - node_ids)
        if unknown:
            raise LiveEditorError(
                "graph-layout-preview-scope-unknown",
                "The preview scope contains nodes outside the exact focused graph.",
                details={"unknownNodeIds": unknown},
            )

    request: dict[str, Any] = {
        "mode": mode,
        "options": {"spacingX": spacing_x, "spacingY": spacing_y, "seed": seed},
        "packing": {"targetWidth": target_width},
    }
    if scope is not None:
        request["scope"] = scope
    result = preview_graph_layout(graph, request)
    if result.status != "ok" or not isinstance(result.plan, dict) or not result.plan:
        diagnostics = result.errors or [
            {
                "code": "graph-layout-preview-rejected",
                "severity": "error",
                "message": "The P0 layout algorithm rejected the graph.",
            }
        ]
        raise LiveEditorError(
            str(diagnostics[0].get("code") or "graph-layout-preview-rejected"),
            str(diagnostics[0].get("message") or "The layout plan was rejected."),
            details={"diagnostics": diagnostics},
        )
    plan = result.plan
    if plan.get("layoutHash") != recompute_layout_hash(plan):
        raise LiveEditorError(
            "graph-layout-preview-hash-mismatch",
            "The returned LayoutPlan hash is not canonical.",
        )
    if plan.get("baseGraphHash") != graph["semanticHash"]:
        raise LiveEditorError(
            "graph-layout-preview-semantic-mismatch",
            "The LayoutPlan base graph hash is not the imported graph.",
        )

    before = {
        str(node["nodeId"]): node["position"] for node in graph["nodes"]
    }
    after = {
        str(item["nodeId"]): {"x": item["x"], "y": item["y"]}
        for item in plan.get("positions", [])
    }
    positions: list[dict[str, Any]] = []
    for node in graph["nodes"]:
        node_id = str(node["nodeId"])
        old = before[node_id]
        new = after[node_id]
        positions.append(
            {
                "nodeId": node_id,
                "nodeGuid": node["nodeGuid"],
                "kind": node["kind"],
                "beforeX": int(old["x"]),
                "beforeY": int(old["y"]),
                "afterX": int(new["x"]),
                "afterY": int(new["y"]),
                "deltaX": int(new["x"]) - int(old["x"]),
                "deltaY": int(new["y"]) - int(old["y"]),
                "width": int(node["size"]["width"]),
                "height": int(node["size"]["height"]),
            }
        )
    positions.sort(key=lambda item: item["nodeId"])
    moved = [str(item) for item in plan.get("movedNodeIds", [])]
    return {
        "schemaVersion": "ueak.graphlayoutpreview/1.0",
        "readOnly": True,
        "source": "focused-blueprint-editor",
        "selectionPolicy": "most-recently-active-blueprint-editor",
        "focusScope": "toolkit-graph-tab",
        "assetPath": asset_path,
        "graphGuid": graph_guid,
        "graphName": _string(preflight.get("graphName"), "preflight.graphName"),
        "packageRevision": preflight["packageRevision"],
        "packageDirty": False,
        "graphFingerprint": preflight["graphFingerprint"],
        "sessionId": preflight["sessionId"],
        "pluginBuildHash": preflight["pluginBuildHash"],
        "editable": bool(preflight.get("editable")),
        "mode": mode,
        "scopeNodeIds": scope or [],
        "planId": plan["planId"],
        "layoutHash": plan["layoutHash"],
        "baseGraphHash": plan["baseGraphHash"],
        "nodeCount": len(graph["nodes"]),
        "pinCount": len(graph["pins"]),
        "edgeCount": len(graph["edges"]),
        "commentCount": len(graph["comments"]),
        "movedCount": len(moved),
        "movedNodeIds": moved,
        "positions": positions,
        "metrics": plan.get("metrics", {}),
        "warnings": plan.get("warnings", []),
        "islands": plan.get("islands", []),
        "sccs": plan.get("sccs", []),
        "plan": plan,
        "applyEligible": bool(preflight.get("editable")),
        "evidenceLevel": "same-process",
        "acceptanceEligible": False,
        "requiredAcceptanceEvidence": "independent-reload",
        "openAssets": False,
        "wroteBlueprint": False,
        "savedPackage": False,
    }


__all__ = ["build_preview"]
