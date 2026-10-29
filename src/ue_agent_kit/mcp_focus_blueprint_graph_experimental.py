"""Opt-in MCP tool that opens and focuses one exact Blueprint graph.

The bounded experimental write tools all require the ordinary Blueprint Editor to
be showing the exact target graph.  Opening an asset is not enough: without an
explicit graph focus a background or headless Editor may report no focused graph,
and every write family then fails closed with ``graph-layout-graph-not-focused``.

This tool is registered only together with ``--enable-experimental-graphpatch-edit``
because it exists to support those opt-in write tools.  It never saves, compiles
or mutates a package.
"""

from __future__ import annotations

import re
from typing import Any

TOOL_NAME = "ue_focus_blueprint_graph"
METHOD = "editor.focusBlueprintGraph"
SCHEMA_VERSION = "ueagentkit.focus-blueprint-graph/1.0"

_GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _validate_game_object_path(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("asset_path must be one exact object path like /Game/Folder/BP.BP")
    if len(value) > 512 or not value.startswith("/Game/") or "." not in value.rsplit("/", 1)[-1]:
        raise ValueError("asset_path must be one exact /Game object path including the object name.")
    if ".." in value or "\\" in value or any(character.isspace() for character in value):
        raise ValueError("asset_path must not contain whitespace, '..' or backslashes.")
    return value


def register_focus_blueprint_graph_tool(
    *,
    server: Any,
    live_editor_service: Any,
    tool_annotations_type: Any,
    error_response: Any,
) -> None:
    annotations = tool_annotations_type(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )

    @server.tool(annotations=annotations)
    def ue_focus_blueprint_graph(
        asset_path: str,
        graph_guid: str | None = None,
    ) -> dict[str, Any]:
        """Open and focus one exact Blueprint graph for the bounded live write tools.

        Pass an exact Blueprint object path and, optionally, the canonical GUID of
        one top-level graph.  Without graph_guid the Blueprint's first top-level
        event graph is focused.  The Editor must be running without PIE.  This
        action does not save or compile anything, but it does open the Blueprint
        Editor, so it is an opt-in action rather than a read-only query.
        """
        try:
            params: dict[str, Any] = {"assetPath": _validate_game_object_path(asset_path)}
            if graph_guid:
                if not isinstance(graph_guid, str) or _GUID.fullmatch(graph_guid) is None:
                    raise ValueError("graph_guid must be a canonical hyphenated GUID when supplied.")
                params["graphGuid"] = graph_guid.lower()
            result = live_editor_service.call_method(METHOD, params, timeout_seconds=30.0)
        except Exception as exc:  # noqa: BLE001 - normalized MCP error envelope
            return error_response(TOOL_NAME, exc, read_only=False)
        return {
            "schemaVersion": SCHEMA_VERSION,
            "tool": TOOL_NAME,
            "ok": True,
            "readOnly": False,
            "experimental": True,
            "productionDefault": False,
            "source": "live-editor-memory",
            "result": result,
        }
