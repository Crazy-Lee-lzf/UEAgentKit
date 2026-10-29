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
    SAVE_CAPABILITY,
    SAVE_METHOD,
    SAVE_TOOL_NAME,
    GraphEditError,
    build_apply_parameters,
    build_save_parameters,
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
    save_annotations = tool_annotations_type(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )

    @server.tool(annotations=preview_annotations)
    def ue_preview_blueprint_graph_edit_experimental(
        assetPath: str,
        graphGuid: str,
        nodes: list[dict[str, Any]],
        connections: list[dict[str, Any]],
        operation: str = "connect",
    ) -> dict[str, Any]:
        """Opt-in, read-only: validate a bounded createNode/connectPins plan against the live graph.

        Supported nodes are Branch, Sequence, Boolean NOT/OR, integer add/equal,
        double add, string concat, and self-context member-variable Get/Set
        accessors for an existing plain bool/int/double/string variable (bound by
        GUID + name; nothing is created, renamed or retyped). Supported
        connections are exact exec or exact bool/int/double/string data links
        between the declared pins; one output may fan out to several distinct
        input pins. One endpoint may be an exact existing K2 pin supplied as
        fromExisting/toExisting (nodeGuid + pinGuid + name + direction +
        category); an existing input must be unlinked and the schema must return
        MAKE (no BreakOthers/conversion/promotion). A plan with nodes=[] may add
        exactly one existing output -> existing unlinked input edge, or with
        operation="disconnect" remove exactly one existing edge. call_function
        accepts only the fixed allowlist of static BlueprintPure engine functions
        with an exact owner/function/signatureDigest/pin schema; arbitrary paths,
        world/latent/network/receiver/ref/const/container calls are rejected. The
        result is read-only and returns the exact planId and requiredConfirmation
        needed by Apply. It never opens a transaction, dirties, compiles, or saves.
        """
        try:
            _capabilities(live_editor_service, PREVIEW_CAPABILITY)
            params = validate_preview_parameters(
                asset_path=assetPath,
                graph_guid=graphGuid,
                nodes=nodes,
                connections=connections,
                operation=operation,
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
        revision/fingerprint, creates only the allowlisted deterministic nodes
        (including the exact existing member-variable Get/Set accessors),
        connects only the previewed exact-typed pins (including output fan-out),
        verifies readback and Undo/Redo, and never saves or compiles.
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


    @server.tool(annotations=save_annotations)
    def ue_save_blueprint_graph_edit_experimental(
        apply_result: dict[str, Any],
        confirmation: str,
    ) -> dict[str, Any]:
        """Opt-in: save the exact package produced by the preceding graph edit Apply.

        Pass the complete Apply result and the exact
        'SAVE EXPERIMENTAL GRAPH NODES <planId>' confirmation. Save is bound to
        the Apply plan, post-Apply graph fingerprint and exact node/link counts.
        It fails closed if the focused graph changed, the package is not dirty,
        or the counts/fingerprint differ. It never compiles or auto-saves.
        """
        try:
            _capabilities(live_editor_service, SAVE_CAPABILITY)
            params = build_save_parameters(apply_result, confirmation)
            encoded = json.dumps(params, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if len(encoded) > MAX_REQUEST_BYTES:
                raise LiveEditorError(
                    "graph-edit-request-too-large",
                    "The graph edit save request exceeds the 60 KiB bridge boundary.",
                )
            payload = live_editor_service.call_method(
                SAVE_METHOD,
                params,
                timeout_seconds=60.0,
            )
        except (LiveEditorError, GraphEditError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response(SAVE_TOOL_NAME, exc, read_only=False)
        return {
            "schemaVersion": "1.0",
            "tool": SAVE_TOOL_NAME,
            "ok": True,
            "readOnly": False,
            "destructive": False,
            "experimental": True,
            "explicitOptInRequired": True,
            "productionDefault": False,
            "source": "live-editor-memory",
            "result": payload,
        }


__all__ = ["register_graph_edit_experimental_tools"]
