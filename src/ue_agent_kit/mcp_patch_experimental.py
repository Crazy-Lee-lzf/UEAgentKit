"""Opt-in MCP tools for the cross-writer atomic Blueprint patch (0-3.4).

The tools are only registered when the server flag is enabled and the live
Editor advertises the matching capability.  Preview is read-only.  Apply runs
every operation inside one Editor transaction and never saves; Save is a
separate explicit action bound to the exact Apply receipt.

A failed Apply still returns one result per operation: the Editor sends them as
the additive ``error.details`` payload, and this layer lifts them into the tool
response so a caller can see which operation failed and what was rolled back.
"""

from __future__ import annotations

import json
from typing import Any

from .editor_bridge import LiveEditorError
from .experimental_patch import (
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
    PatchError,
    build_apply_parameters,
    build_save_parameters,
    describe_rollback,
    validate_preview_parameters,
)


def _error_response(tool: str, exc: BaseException, *, read_only: bool) -> dict[str, Any]:
    code = getattr(exc, "code", "error")
    message = str(exc)
    response: dict[str, Any] = {
        "schemaVersion": "1.0",
        "tool": tool,
        "ok": False,
        "readOnly": read_only,
        "error": {"code": code, "message": message},
    }
    details = getattr(exc, "details", None)
    rollback = describe_rollback(details)
    if rollback:
        # A rejected patch must never look like an empty failure: the per-operation
        # statuses and the rollback outcome travel with the error.
        response["writeOccurred"] = rollback["writeOccurred"]
        response["transactionOpened"] = rollback["transactionOpened"]
        response["rolledBack"] = rollback["rolledBack"]
        response["results"] = rollback["results"]
    return response


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


def register_patch_experimental_tools(
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

    def _call_bounded(
        method: str,
        params: dict[str, Any],
        *,
        timeout_seconds: float,
        stage: str,
    ) -> dict[str, Any]:
        encoded = json.dumps(params, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_REQUEST_BYTES:
            raise LiveEditorError(
                "graph-patch-request-too-large",
                f"The Blueprint patch {stage} request exceeds the 60 KiB bridge boundary.",
            )
        return live_editor_service.call_method(
            method,
            params,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(annotations=preview_annotations)
    def ue_preview_blueprint_patch_experimental(
        asset_path: str,
        graph_guid: str,
        operations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Read-only preview of one atomic Blueprint patch.

        Each operation carries an opId plus its kind-specific fields. This build
        migrates only setPinDefault (nodeGuid, pinName, pinGuid, type, value), so
        a request that names any other kind is refused before it reaches the
        Editor. The result binds the exact before values, the graph and state
        fingerprints and the required Apply confirmation, and it lists one
        validated entry per operation.
        """
        try:
            _capabilities(live_editor_service, PREVIEW_CAPABILITY)
            params = validate_preview_parameters(
                asset_path=asset_path,
                graph_guid=graph_guid,
                operations=operations,
            )
            payload = _call_bounded(
                PREVIEW_METHOD,
                params,
                timeout_seconds=30.0,
                stage="preview",
            )
        except (LiveEditorError, PatchError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
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
    def ue_apply_blueprint_patch_experimental(
        plan_id: str,
        asset_path: str,
        graph_guid: str,
        operations: list[dict[str, Any]],
        confirmation: str = "",
    ) -> dict[str, Any]:
        """Apply the previewed patch as one transaction, never saving.

        All operations land in a single Editor transaction. If any operation
        fails, every operation already applied is restored, the transaction is
        cancelled and the response reports writeOccurred=false plus one status per
        operation instead of only the first error.
        """
        try:
            _capabilities(live_editor_service, APPLY_CAPABILITY)
            params = build_apply_parameters(
                plan_id=plan_id,
                asset_path=asset_path,
                graph_guid=graph_guid,
                operations=operations,
                confirmation=confirmation,
            )
            payload = _call_bounded(
                APPLY_METHOD,
                params,
                timeout_seconds=90.0,
                stage="apply",
            )
        except (LiveEditorError, PatchError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
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
            "saved": False,
            "result": payload,
        }

    @server.tool(annotations=save_annotations)
    def ue_save_blueprint_patch_experimental(
        apply_result: dict[str, Any],
        confirmation: str = "",
    ) -> dict[str, Any]:
        """Save the exact package produced by the preceding patch Apply."""
        try:
            _capabilities(live_editor_service, SAVE_CAPABILITY)
            params = build_save_parameters(apply_result, confirmation)
            payload = _call_bounded(
                SAVE_METHOD,
                params,
                timeout_seconds=60.0,
                stage="save",
            )
        except (LiveEditorError, PatchError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
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


__all__ = ["register_patch_experimental_tools"]
