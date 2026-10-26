"""Opt-in MCP tools for bounded Blueprint single-edge rewire.

The tools are only registered when the server flag is enabled and the live
Editor advertises the matching capability.  Preview is read-only.  Apply and
Save require the complete preview/apply result plus an exact confirmation; the
caller cannot construct a raw bridge request.
"""

from __future__ import annotations

import json
from typing import Any

from .editor_bridge import LiveEditorError
from .experimental_rewire_write import (
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
    RewireWriteError,
    build_apply_parameters,
    build_save_parameters,
    validate_preview_parameters,
)


def _error_response(tool: str, exc: BaseException, *, read_only: bool) -> dict[str, Any]:
    code = getattr(exc, "code", "error")
    message = str(exc)
    return {
        "schemaVersion": "1.0",
        "tool": tool,
        "ok": False,
        "readOnly": read_only,
        "error": {"code": code, "message": message},
    }


def _capabilities(live_editor_service: Any, required: str) -> None:
    if live_editor_service is None:
        raise LiveEditorError(
            "live-editor-unavailable",
            "No live Editor service is configured.",
        )
    status = live_editor_service.status()
    observed = status.get("capabilities") if isinstance(status, dict) else None
    if not isinstance(observed, list) or required not in observed:
        raise LiveEditorError(
            "live-editor-capability-missing",
            f"The live Editor does not advertise {required}.",
        )


def _encoded_size(params: dict[str, Any]) -> int:
    return len(json.dumps(params, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def register_rewire_write_experimental_tools(
    *,
    server: Any,
    live_editor_service: Any,
    tool_annotations_type: Any,
    error_response: Any = _error_response,
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
    def ue_preview_blueprint_rewire_experimental(
        asset_path: str,
        graph_guid: str,
        operation: str,
        target_node_guid: str,
        target_pin_name: str,
        target_pin_guid: str,
        old_source_node_guid: str,
        old_source_pin_name: str,
        old_source_pin_guid: str,
        expected_edge_set_hash: str,
        new_source_node_guid: str | None = None,
        new_source_pin_name: str | None = None,
        new_source_pin_guid: str | None = None,
    ) -> dict[str, Any]:
        """Read-only preview of one exact single-edge rewire or disconnect.

        The request binds the focused event graph, the complete before edge-set
        hash and the exact old-source/target/new-source node GUID + pin name +
        pin GUID triples. The result reports the exact removed/added edges, the
        untouched edge count and hash, and the required Apply confirmation.
        Nothing is written, compiled or saved.
        """
        try:
            _capabilities(live_editor_service, PREVIEW_CAPABILITY)
            params = validate_preview_parameters(
                asset_path=asset_path,
                graph_guid=graph_guid,
                operation=operation,
                target_node_guid=target_node_guid,
                target_pin_name=target_pin_name,
                target_pin_guid=target_pin_guid,
                old_source_node_guid=old_source_node_guid,
                old_source_pin_name=old_source_pin_name,
                old_source_pin_guid=old_source_pin_guid,
                expected_edge_set_hash=expected_edge_set_hash,
                new_source_node_guid=new_source_node_guid,
                new_source_pin_name=new_source_pin_name,
                new_source_pin_guid=new_source_pin_guid,
            )
            if _encoded_size(params) > MAX_REQUEST_BYTES:
                raise LiveEditorError(
                    "blueprint-rewire-request-too-large",
                    "The Blueprint rewire preview request exceeds the 60 KiB bridge boundary.",
                )
            payload = live_editor_service.call_method(
                PREVIEW_METHOD,
                params,
                timeout_seconds=30.0,
            )
        except (
            LiveEditorError,
            RewireWriteError,
            FileNotFoundError,
            OSError,
            ValueError,
            RuntimeError,
        ) as exc:
            return error_response(PREVIEW_TOOL_NAME, exc, read_only=True)
        return {
            "schemaVersion": "1.0",
            "tool": PREVIEW_TOOL_NAME,
            "ok": True,
            "readOnly": True,
            "destructive": False,
            "experimental": True,
            "explicitOptInRequired": True,
            "productionDefault": False,
            "source": "live-editor-memory",
            "result": payload,
        }

    @server.tool(annotations=apply_annotations)
    def ue_apply_blueprint_rewire_experimental(
        preview_result: dict[str, Any],
        confirmation: str,
    ) -> dict[str, Any]:
        """Replace the one previewed edge in one transaction, never saving or compiling."""
        try:
            _capabilities(live_editor_service, APPLY_CAPABILITY)
            params = build_apply_parameters(preview_result, confirmation)
            if _encoded_size(params) > MAX_REQUEST_BYTES:
                raise LiveEditorError(
                    "blueprint-rewire-request-too-large",
                    "The Blueprint rewire apply request exceeds the 60 KiB bridge boundary.",
                )
            payload = live_editor_service.call_method(
                APPLY_METHOD,
                params,
                timeout_seconds=60.0,
            )
        except (
            LiveEditorError,
            RewireWriteError,
            FileNotFoundError,
            OSError,
            ValueError,
            RuntimeError,
        ) as exc:
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
    def ue_save_blueprint_rewire_experimental(
        apply_result: dict[str, Any],
        confirmation: str,
    ) -> dict[str, Any]:
        """Save the exact package produced by the preceding rewire Apply."""
        try:
            _capabilities(live_editor_service, SAVE_CAPABILITY)
            params = build_save_parameters(apply_result, confirmation)
            if _encoded_size(params) > MAX_REQUEST_BYTES:
                raise LiveEditorError(
                    "blueprint-rewire-request-too-large",
                    "The Blueprint rewire save request exceeds the 60 KiB bridge boundary.",
                )
            payload = live_editor_service.call_method(
                SAVE_METHOD,
                params,
                timeout_seconds=60.0,
            )
        except (
            LiveEditorError,
            RewireWriteError,
            FileNotFoundError,
            OSError,
            ValueError,
            RuntimeError,
        ) as exc:
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


__all__ = ["register_rewire_write_experimental_tools"]
