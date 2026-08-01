from __future__ import annotations

from typing import Any

from .editor_bridge import LiveEditorBridgeService, LiveEditorError


def register_realtime_tools(
    *,
    server: Any,
    live_editor_service: LiveEditorBridgeService,
    read_annotations: Any,
    error_response: Any,
) -> None:
    @server.tool(annotations=read_annotations)
    def ue_get_editor_context() -> dict[str, Any]:
        """Aggregate the bounded current Editor working context in one read-only request."""
        try:
            return live_editor_service.call_tool("ue_get_editor_context")
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_get_editor_context", exc, read_only=True)
