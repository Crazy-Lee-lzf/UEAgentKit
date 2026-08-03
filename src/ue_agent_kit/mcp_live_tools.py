from __future__ import annotations

from typing import Any, Literal

from .editor_bridge import LiveEditorBridgeService, LiveEditorError


def register_live_read_tools(
    *,
    server: Any,
    live_editor_service: LiveEditorBridgeService,
    read_annotations: Any,
    error_response: Any,
) -> None:
    @server.tool(annotations=read_annotations)
    def ue_editor_status() -> dict[str, Any]:
        """Return fixed-project Live Editor availability, version, session, PIE, level, and Dirty summary."""
        return {
            "schemaVersion": "1.0",
            "tool": "ue_editor_status",
            "ok": True,
            "readOnly": True,
            "source": "live-editor-memory",
            "result": live_editor_service.status(),
        }

    @server.tool(annotations=read_annotations)
    def ue_get_selection() -> dict[str, Any]:
        """Return the bounded Actor, Component, Asset, and Object selection from the fixed Editor session."""
        try:
            return live_editor_service.call_tool("ue_get_selection")
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_get_selection", exc, read_only=True)

    @server.tool(annotations=read_annotations)
    def ue_get_open_assets() -> dict[str, Any]:
        """Return assets currently opened in registered asset editors in the fixed Editor session."""
        try:
            return live_editor_service.call_tool("ue_get_open_assets")
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_get_open_assets", exc, read_only=True)

    @server.tool(annotations=read_annotations)
    def ue_get_dirty_assets() -> dict[str, Any]:
        """Return bounded Dirty /Game packages and their Asset Registry paths from Editor memory."""
        try:
            return live_editor_service.call_tool("ue_get_dirty_assets")
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_get_dirty_assets", exc, read_only=True)

    @server.tool(annotations=read_annotations)
    def ue_get_current_level() -> dict[str, Any]:
        """Return the fixed Editor world, persistent/current level, World Partition, and Dirty state."""
        try:
            return live_editor_service.call_tool("ue_get_current_level")
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_get_current_level", exc, read_only=True)

    @server.tool(annotations=read_annotations)
    def ue_get_pie_state() -> dict[str, Any]:
        """Return whether the fixed Editor is stopped, playing, or simulating in editor."""
        try:
            return live_editor_service.call_tool("ue_get_pie_state")
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_get_pie_state", exc, read_only=True)

    @server.tool(annotations=read_annotations)
    def ue_get_output_log(
        category: str = "",
        minimum_verbosity: Literal[
            "fatal",
            "error",
            "warning",
            "display",
            "log",
            "verbose",
            "veryverbose",
        ] = "log",
        keyword: str = "",
        since_sequence: int = 0,
        since_utc: str = "",
        until_utc: str = "",
        pie_session_id: int = -1,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Read bounded current-session Output Log entries using category, severity, text, UTC, PIE, and sequence filters."""
        try:
            return live_editor_service.call_tool(
                "ue_get_output_log",
                {
                    "category": category,
                    "minimumVerbosity": minimum_verbosity,
                    "keyword": keyword,
                    "sinceSequence": since_sequence,
                    "sinceUtc": since_utc,
                    "untilUtc": until_utc,
                    "pieSessionId": pie_session_id,
                    "limit": limit,
                },
            )
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_get_output_log", exc, read_only=True)

    @server.tool(annotations=read_annotations)
    def ue_get_compile_errors(
        asset_path: str = "",
        since_sequence: int = 0,
        pie_session_id: int = -1,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Return captured compiler-related warnings/errors plus current loaded Blueprint compile status."""
        try:
            return live_editor_service.call_tool(
                "ue_get_compile_errors",
                {
                    "assetPath": asset_path,
                    "sinceSequence": since_sequence,
                    "pieSessionId": pie_session_id,
                    "limit": limit,
                },
            )
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_get_compile_errors", exc, read_only=True)

    @server.tool(annotations=read_annotations)
    def ue_inspect_asset_live(asset_path: str) -> dict[str, Any]:
        """Inspect one exact /Game asset in Asset Registry and current Editor memory without loading or modifying it."""
        try:
            return live_editor_service.call_tool(
                "ue_inspect_asset_live",
                {"assetPath": asset_path},
            )
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_inspect_asset_live", exc, read_only=True)

    @server.tool(annotations=read_annotations)
    def ue_get_blueprint_graph_selection() -> dict[str, Any]:
        """Return the focused Graph and bounded selected Nodes from the most recently active ordinary Blueprint Editor."""
        try:
            return live_editor_service.call_tool("ue_get_blueprint_graph_selection")
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_get_blueprint_graph_selection", exc, read_only=True)

    # Retarget read-only tools keep the live-read group order matching the
    # Tool Registry while living in their own module.
    from .mcp_retarget_tools import register_retarget_tools

    register_retarget_tools(
        server=server,
        live_editor_service=live_editor_service,
        read_annotations=read_annotations,
        error_response=error_response,
    )
