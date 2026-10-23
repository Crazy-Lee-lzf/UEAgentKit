from __future__ import annotations

import json
from typing import Any

from .editor_bridge import LiveEditorBridgeService, LiveEditorError
from .p8_test_only import (
    APPLY_METHOD,
    APPLY_TOOL_NAME,
    FIXED_ASSET_PATH,
    FIXED_PROJECT_PATH,
    MAX_REQUEST_BYTES,
)


def register_graph_layout_p8_test_only_tool(
    *,
    server: Any,
    live_editor_service: LiveEditorBridgeService,
    tool_annotations_type: Any,
    error_response: Any,
) -> None:
    """Register the one P8 destructive lab tool under a fixed project gate."""

    annotations = tool_annotations_type(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=False,
    )

    @server.tool(annotations=annotations)
    def ue_apply_blueprint_graph_layout_p8_test_only(
        request: dict[str, Any],
        confirmation: str,
    ) -> dict[str, Any]:
        """P8 lab-only: apply one canonical position-only LayoutPlan to the exact disposable BP_LayoutP8 graph.

        This tool exists only in the isolated P8 host.  It never accepts a
        project, asset, endpoint, save, compile, or command parameter.
        """
        try:
            if not isinstance(request, dict):
                raise LiveEditorError(
                    "live-editor-invalid-parameters",
                    "request must be one canonical graph layout apply request object.",
                )
            encoded = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if len(encoded) > MAX_REQUEST_BYTES:
                raise LiveEditorError(
                    "live-editor-invalid-parameters",
                    "request exceeds the P8 test-only size limit.",
                )
            if request.get("assetPath") != FIXED_ASSET_PATH:
                raise LiveEditorError(
                    "graph-layout-test-only-asset-rejected",
                    f"P8 test-only apply accepts only {FIXED_ASSET_PATH}.",
                )
            payload = live_editor_service.call_method(
                APPLY_METHOD,
                {"request": request, "confirmation": confirmation},
                timeout_seconds=10.0,
            )
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response(APPLY_TOOL_NAME, exc, read_only=False)
        return {
            "schemaVersion": "1.0",
            "tool": APPLY_TOOL_NAME,
            "ok": True,
            "readOnly": False,
            "destructive": True,
            "testOnly": True,
            "fixedProject": str(FIXED_PROJECT_PATH),
            "fixedAsset": FIXED_ASSET_PATH,
            "source": "live-editor-memory",
            "result": payload,
        }
