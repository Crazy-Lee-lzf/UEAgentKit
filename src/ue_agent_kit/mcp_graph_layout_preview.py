"""The public read-only graph-layout preview MCP tool.

This is deliberately separate from the conditional P8 lab-only write surface.
It is registered for every fixed-project Live Editor server, accepts only
bounded layout options, and never accepts a project, asset, save, or command
parameter. Large graphs use the bounded paginated layout projection when the
Editor exposes it; the legacy full-context path remains for older Bridges.
"""

from __future__ import annotations

from typing import Any, Literal

from .editor_bridge import LiveEditorBridgeService, LiveEditorError
from .graph_layout_preview import build_preview
from .graph_layout_projection import (
    PROJECTION_METHOD,
    build_preview_from_projection,
    fetch_layout_projection,
)

PREVIEW_TOOL_NAME = "ue_preview_blueprint_graph_layout"
PREFLIGHT_METHOD = "editor.getBlueprintGraphLayoutPreflight"
CONTEXT_METHOD = "editor.getFocusedBlueprintGraphContext"


def register_graph_layout_preview_tool(
    *,
    server: Any,
    live_editor_service: LiveEditorBridgeService,
    read_annotations: Any,
    error_response: Any,
) -> None:
    @server.tool(annotations=read_annotations)
    def ue_preview_blueprint_graph_layout(
        mode: Literal["full", "selection", "incremental"] = "full",
        scope_node_ids: list[str] | None = None,
        spacing_x: int = 96,
        spacing_y: int = 64,
        target_width: int = 1800,
        seed: int = 0,
    ) -> dict[str, Any]:
        """Preview a complete read-only LayoutPlan for the exact focused Blueprint graph; never writes or saves."""
        try:
            if mode not in ("full", "selection", "incremental"):
                raise LiveEditorError(
                    "graph-layout-preview-invalid-mode",
                    "mode must be full, selection, or incremental.",
                )
            if not isinstance(spacing_x, int) or isinstance(spacing_x, bool) or not 1 <= spacing_x <= 4096:
                raise LiveEditorError(
                    "graph-layout-preview-invalid-options",
                    "spacing_x must be an integer from 1 through 4096.",
                )
            if not isinstance(spacing_y, int) or isinstance(spacing_y, bool) or not 1 <= spacing_y <= 4096:
                raise LiveEditorError(
                    "graph-layout-preview-invalid-options",
                    "spacing_y must be an integer from 1 through 4096.",
                )
            if not isinstance(target_width, int) or isinstance(target_width, bool) or not 256 <= target_width <= 16384:
                raise LiveEditorError(
                    "graph-layout-preview-invalid-options",
                    "target_width must be an integer from 256 through 16384.",
                )
            if not isinstance(seed, int) or isinstance(seed, bool) or not 0 <= seed <= 2_147_483_647:
                raise LiveEditorError(
                    "graph-layout-preview-invalid-options",
                    "seed must be a non-negative int32.",
                )
            if scope_node_ids is not None and (
                not isinstance(scope_node_ids, list)
                or not scope_node_ids
                or any(not isinstance(item, str) or not item for item in scope_node_ids)
            ):
                raise LiveEditorError(
                    "graph-layout-preview-invalid-scope",
                    "scope_node_ids must be a non-empty array of node-id strings.",
                )

            status = live_editor_service.status()
            if status.get("state") != "available":
                raise LiveEditorError(
                    str(status.get("reasonCode") or "live-editor-unavailable"),
                    str(status.get("reason") or "The fixed Live Editor is unavailable."),
                )
            capabilities = status.get("capabilities")
            if not isinstance(capabilities, list) or (
                CONTEXT_METHOD not in capabilities and PROJECTION_METHOD not in capabilities
            ):
                raise LiveEditorError(
                    "live-editor-capability-unavailable",
                    "The registered Editor Bridge exposes neither the focused graph context "
                    "nor the bounded layout projection.",
                )

            preflight = live_editor_service.call_method(PREFLIGHT_METHOD, {})
            selection: dict[str, Any] | None = None
            if mode != "full" and scope_node_ids is None:
                selection_payload = live_editor_service.call_tool(
                    "ue_get_blueprint_graph_selection"
                )
                selected = selection_payload.get("result")
                if isinstance(selected, dict):
                    selection = selected

            if PROJECTION_METHOD in capabilities:
                projection = fetch_layout_projection(live_editor_service, preflight)
                result = build_preview_from_projection(
                    preflight,
                    projection,
                    mode=mode,
                    scope_node_ids=scope_node_ids,
                    selection_payload=selection,
                    spacing_x=spacing_x,
                    spacing_y=spacing_y,
                    target_width=target_width,
                    seed=seed,
                )
            else:
                context = live_editor_service.call_method(CONTEXT_METHOD, {})
                result = build_preview(
                    preflight,
                    context,
                    mode=mode,
                    scope_node_ids=scope_node_ids,
                    selection_payload=selection,
                    spacing_x=spacing_x,
                    spacing_y=spacing_y,
                    target_width=target_width,
                    seed=seed,
                )
            return {
                "schemaVersion": "1.0",
                "tool": PREVIEW_TOOL_NAME,
                "ok": True,
                "readOnly": True,
                "source": "live-editor-memory",
                "result": result,
            }
        except LiveEditorError as exc:
            return error_response(PREVIEW_TOOL_NAME, exc, read_only=True)
        except (FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response(PREVIEW_TOOL_NAME, exc, read_only=True)


__all__ = ["PREVIEW_TOOL_NAME", "register_graph_layout_preview_tool"]
