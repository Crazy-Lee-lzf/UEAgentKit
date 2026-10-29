"""The conditional, explicitly opted-in graph-layout apply/save MCP surface.

These tools are registered only when the MCP operator passes
``--enable-experimental-graph-layout-apply``.  They are never in
``TOOL_REGISTRY`` and never available by default.  The running Editor still has
to opt in at project level and advertise the matching bridge capability.
"""

from __future__ import annotations

import json
from typing import Any

from .editor_bridge import LiveEditorBridgeService, LiveEditorError
from .experimental_graph_layout import (
    APPLY_CAPABILITY,
    APPLY_METHOD,
    APPLY_TOOL_NAME,
    MAX_REQUEST_BYTES,
    SAVE_CAPABILITY,
    SAVE_METHOD,
    SAVE_TOOL_NAME,
    build_apply_request,
    confirmation_for_plan,
    validate_save_parameters,
)


def _capabilities(live_editor_service: LiveEditorBridgeService, capability: str) -> None:
    status = live_editor_service.status()
    if status.get("state") != "available":
        raise LiveEditorError(
            str(status.get("reasonCode") or "live-editor-unavailable"),
            str(status.get("reason") or "The fixed Live Editor is unavailable."),
        )
    capabilities = status.get("capabilities")
    if not isinstance(capabilities, list) or capability not in capabilities:
        raise LiveEditorError(
            "graph-layout-experimental-disabled",
            "The running Editor project has not opted in to experimental graph layout apply. "
            "Set [UEAgentKit] ExperimentalGraphLayoutApply=True in the project config first.",
        )


def register_graph_layout_experimental_tools(
    *,
    server: Any,
    live_editor_service: LiveEditorBridgeService,
    tool_annotations_type: Any,
    error_response: Any,
) -> None:
    annotations = tool_annotations_type(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=False,
    )

    @server.tool(annotations=annotations)
    def ue_apply_blueprint_graph_layout_experimental(
        preview: dict[str, Any],
        confirmation: str,
    ) -> dict[str, Any]:
        """Opt-in: apply one previewed LayoutPlan to the exact focused Blueprint graph, position-only.

        The caller must pass the complete result object returned by
        ue_preview_blueprint_graph_layout and the exact
        'APPLY EXPERIMENTAL LAYOUT <planId>' confirmation. The Editor verifies
        the focused graph identity, clean package, stopped PIE, current
        revision/fingerprint, opens one transaction, and proves Undo/Redo before
        returning. It never saves or compiles.
        """
        try:
            _capabilities(live_editor_service, APPLY_CAPABILITY)
            request = build_apply_request(preview)
            plan_id = str(request["plan"]["planId"])
            expected = confirmation_for_plan(plan_id)
            if not isinstance(confirmation, str) or confirmation != expected:
                raise LiveEditorError(
                    "graph-layout-experimental-confirmation-required",
                    f"Explicit Apply confirmation must be exactly {expected}.",
                )
            encoded = json.dumps(
                {"request": request, "confirmation": confirmation},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            if len(encoded) > MAX_REQUEST_BYTES:
                raise LiveEditorError(
                    "graph-layout-experimental-request-too-large",
                    "The canonical ApplyRequest exceeds the 60 KiB bridge request boundary.",
                )
            payload = live_editor_service.call_method(
                APPLY_METHOD,
                {"request": request, "confirmation": confirmation},
                timeout_seconds=60.0,
            )
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response(APPLY_TOOL_NAME, exc, read_only=False)
        return {
            "schemaVersion": "1.0",
            "tool": APPLY_TOOL_NAME,
            "ok": True,
            "readOnly": False,
            "destructive": True,
            "experimental": True,
            "explicitOptInRequired": True,
            "productionDefault": False,
            "source": "live-editor-memory",
            "result": payload,
        }

    @server.tool(annotations=annotations)
    def ue_save_blueprint_graph_layout_experimental(
        assetPath: str,
        graphGuid: str,
        layoutHash: str,
        expectedGraphFingerprint: str,
        confirmation: str,
    ) -> dict[str, Any]:
        """Opt-in: explicitly save the exact dirty Blueprint package after a layout Apply.

        Confirmation must be exactly
        'SAVE EXPERIMENTAL LAYOUT <layoutHash>' using the layoutHash returned by
        the Apply result. expectedGraphFingerprint must be the
        graphFingerprintAfterApply returned by that same Apply. The Editor
        verifies the exact focused graph, that the live positions still equal
        that layout, that the graph fingerprint still equals the post-Apply
        value, and that the package is dirty, then saves only that package and
        returns an on-disk readback receipt.
        """
        try:
            _capabilities(live_editor_service, SAVE_CAPABILITY)
            params = validate_save_parameters(
                asset_path=assetPath,
                graph_guid=graphGuid,
                layout_hash=layoutHash,
                expected_graph_fingerprint=expectedGraphFingerprint,
                confirmation=confirmation,
            )
            payload = live_editor_service.call_method(
                SAVE_METHOD,
                params,
                timeout_seconds=60.0,
            )
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response(SAVE_TOOL_NAME, exc, read_only=False)
        return {
            "schemaVersion": "1.0",
            "tool": SAVE_TOOL_NAME,
            "ok": True,
            "readOnly": False,
            "destructive": True,
            "experimental": True,
            "explicitOptInRequired": True,
            "productionDefault": False,
            "source": "live-editor-memory",
            "result": payload,
        }


__all__ = ["register_graph_layout_experimental_tools"]
