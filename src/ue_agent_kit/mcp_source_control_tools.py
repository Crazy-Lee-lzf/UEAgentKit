from __future__ import annotations

from typing import Any

from .source_control import (
    P4SourceControlService,
    SourceControlCommandError,
    SourceControlValidationError,
)


def register_source_control_tools(
    *,
    server: Any,
    source_control_service: P4SourceControlService,
    read_annotations: Any,
    tool_annotations_type: Any,
    error_response: Any,
) -> None:
    planning_annotations = tool_annotations_type(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )

    @server.tool(annotations=read_annotations)
    def ue_source_control_status(paths: list[str]) -> dict[str, Any]:
        """Report advisory Perforce collaboration state for up to 16 exact local or /Game file paths.

        Read-only. Provider unavailability degrades to an advisory response with
        provider.available=false; it never blocks local work.
        """
        try:
            result = source_control_service.status(paths)
        except SourceControlValidationError as exc:
            return error_response("ue_source_control_status", exc, read_only=True)
        except SourceControlCommandError as exc:
            return error_response("ue_source_control_status", exc, read_only=True)
        return result.to_payload()

    @server.tool(annotations=planning_annotations)
    def ue_source_control_prepare_write(
        paths: list[str],
        allow_local_writable_override: bool = False,
        request_safe_sync: bool = False,
    ) -> dict[str, Any]:
        """Prepare an exact file set for a local Writer operation: safe sync, p4 edit, and optional readonly override.

        Advisory assistance only. It never submits, reverts, deletes, or decides
        Writer safety. Provider unavailability degrades to an advisory response.
        """
        try:
            result = source_control_service.prepare_write(
                paths,
                allow_local_writable_override=allow_local_writable_override,
                request_safe_sync=request_safe_sync,
            )
        except SourceControlValidationError as exc:
            return error_response("ue_source_control_prepare_write", exc, read_only=False)
        except SourceControlCommandError as exc:
            return error_response("ue_source_control_prepare_write", exc, read_only=False)
        return result.to_payload()
