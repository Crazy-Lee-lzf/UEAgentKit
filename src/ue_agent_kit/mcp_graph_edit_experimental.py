"""Conditional, explicitly opted-in Blueprint graph structure edit MCP surface.

The tools are registered only when the operator passes
``--enable-experimental-graphpatch-edit``.  They are never in
``TOOL_REGISTRY`` and never available by default.  The running Editor must
also opt in at project level with ``[UEAgentKit] ExperimentalGraphPatchEdit=True``
and advertise the matching bridge capability.
"""

from __future__ import annotations

import json
from typing import Any

from .editor_bridge import LiveEditorBridgeService, LiveEditorError
from .experimental_graph_edit import (
    APPLY_CAPABILITY,
    APPLY_METHOD,
    APPLY_TOOL_NAME,
    MAX_REQUEST_BYTES,
    PREVIEW_CAPABILITY,
    PREVIEW_METHOD,
    PREVIEW_TOOL_NAME,
    GraphEditError,
    build_apply_parameters,
    validate_preview_parameters,
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
            "graph-edit-experimental-disabled",
            "The running Editor project has not opted in to experimental graph structure edit. "
            "Set [UEAgentKit] ExperimentalGraphPatchEdit=True in the project config first.",
        )


def register_graph_edit_experimental_tools(
    *,
    server: Any,
    live_editor_service: LiveEditorBridgeService,
    tool_annotations_type: Any,
    error_response: Any,
) -> None:
    preview_annotations = tool_annotations_type(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
    apply_annotations = tool_annotations_type(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=False,
    )

    @server.tool(annotations=preview_annotations)
    def ue_preview_blueprint_graph_edit_experimental(
        assetPath: str,
        graphGuid: str,
        nodes: list[dict[str, Any]],
        connections: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Opt-in, read-only: validate a bounded createNode/connectPins plan against the live graph.

        Supported nodes are Branch, Sequence and the pure NOT Boolean function
        (not_bool). Supported connections are exec links from then/else/then_0/
        then_1 to execute, plus the exact bool link not_bool.ReturnValue ->
        branch.Condition. The result is read-only and returns the exact planId
        and requiredConfirmation needed by Apply. It never opens a transaction,
        dirties, compiles, or saves.
        """
        try:
            _capabilities(live_editor_service, PREVIEW_CAPABILITY)
            params = validate_preview_parameters(
                asset_path=assetPath,
                graph_guid=graphGuid,
                nodes=nodes,
                connections=connections,
            )
            encoded = json.dumps(params, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if len(encoded) > MAX_REQUEST_BYTES:
                raise LiveEditorError(
                    "graph-edit-request-too-large",
                    "The graph edit preview request exceeds the 60 KiB bridge boundary.",
                )
            payload = live_editor_service.call_method(
                PREVIEW_METHOD,
                params,
                timeout_seconds=30.0,
            )
        except (LiveEditorError, GraphEditError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response(PREVIEW_TOOL_NAME, exc, read_only=True)
        return {
            "schemaVersion": "1.0",
            "tool": PREVIEW_TOOL_NAME,
            "ok": True,
            "readOnly": True,
            "previewOnly": True,
            "experimental": True,
            "productionDefault": False,
            "source": "live-editor-memory",
            "result": payload,
        }

    @server.tool(annotations=apply_annotations)
    def ue_apply_blueprint_graph_edit_experimental(
        preview: dict[str, Any],
        confirmation: str,
    ) -> dict[str, Any]:
        """Opt-in: apply the exact previewed createNode/connectPins plan in one transaction.

        Pass the complete result object returned by
        ue_preview_blueprint_graph_edit_experimental and the exact
        'CREATE EXPERIMENTAL GRAPH NODES <planId>' confirmation. The Editor
        rechecks the exact focused graph, clean package, stopped PIE, current
        revision/fingerprint, creates only Branch/Sequence/not_bool nodes,
        connects only the previewed exec or exact bool pins, verifies readback
        and Undo/Redo, and never saves or compiles.
        """
        try:
            _capabilities(live_editor_service, APPLY_CAPABILITY)
            params = build_apply_parameters(preview, confirmation)
            encoded = json.dumps(params, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if len(encoded) > MAX_REQUEST_BYTES:
                raise LiveEditorError(
                    "graph-edit-request-too-large",
                    "The graph edit apply request exceeds the 60 KiB bridge boundary.",
                )
            payload = live_editor_service.call_method(
                APPLY_METHOD,
                params,
                timeout_seconds=60.0,
            )
        except (LiveEditorError, GraphEditError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
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


__all__ = ["register_graph_edit_experimental_tools"]
