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

    @server.tool(annotations=read_annotations)
    def ue_source_control_changelists(changelist_id: str = "") -> dict[str, Any]:
        """Inspect bounded pending changelist state for the current user/client.

        Read-only. Lists the current client's pending changelists, or returns
        one exact pending changelist spec when changelist_id is provided.
        Submission and revert remain human-only; this tool never submits or
        reverts anything.
        """
        try:
            result = source_control_service.changelists(changelist_id)
        except SourceControlValidationError as exc:
            return error_response("ue_source_control_changelists", exc, read_only=True)
        except SourceControlCommandError as exc:
            return error_response("ue_source_control_changelists", exc, read_only=True)
        return result.to_payload()

    @server.tool(annotations=planning_annotations)
    def ue_source_control_prepare_changelist(
        paths: list[str],
        description: str,
        changelist_id: str = "",
        change_set_id: str = "",
        manual_final_action: str = "none",
    ) -> dict[str, Any]:
        """Create/update one current-user pending changelist and move exact already-opened files into it.

        Bounded changelist preparation only. Files must already be opened by the
        current user/client. Optional change_set_id is validated as an exact
        UEAgentKit Change Set id and linked through the durable audit receipt.
        manual_final_action may be none|submit|revert|delete as handoff metadata only.
        The Agent never submits, reverts, or deletes: final actions stay manual.
        """
        try:
            result = source_control_service.prepare_changelist(
                paths,
                description,
                changelist_id=changelist_id or None,
                change_set_id=change_set_id,
                manual_final_action=manual_final_action,
            )
        except SourceControlValidationError as exc:
            return error_response("ue_source_control_prepare_changelist", exc, read_only=False)
        except SourceControlCommandError as exc:
            return error_response("ue_source_control_prepare_changelist", exc, read_only=False)
        return result.to_payload()

    @server.tool(annotations=read_annotations)
    def ue_source_control_resolve_status(paths: list[str]) -> dict[str, Any]:
        """Preview resolve state for up to 16 exact files (read-only).

        Reports needsResolve / resolveKind / mergeable text eligibility and
        flags Unreal binary packages that require UE-level reconciliation.
        Provider errors fail closed to resolveStateUnknown; nothing is resolved.
        """
        try:
            result = source_control_service.resolve_status(paths)
        except SourceControlValidationError as exc:
            return error_response("ue_source_control_resolve_status", exc, read_only=True)
        except SourceControlCommandError as exc:
            return error_response("ue_source_control_resolve_status", exc, read_only=True)
        return result.to_payload()

    @server.tool(annotations=planning_annotations)
    def ue_source_control_resolve_text(
        paths: list[str],
        changelist_id: str = "",
    ) -> dict[str, Any]:
        """Run bounded conflict-free automatic text resolve on exact eligible files only.

        Only mergeable text files (.cpp .h .ini .json .csv .py) that are opened
        by the current user/client and need resolve are processed with
        ``p4 resolve -am``. Content conflicts stay unresolved for the human;
        Unreal binary packages are never auto-resolved. The Agent never
        submits, reverts, or deletes.
        """
        try:
            result = source_control_service.resolve_text(
                paths,
                changelist_id=changelist_id or None,
            )
        except SourceControlValidationError as exc:
            return error_response("ue_source_control_resolve_text", exc, read_only=False)
        except SourceControlCommandError as exc:
            return error_response("ue_source_control_resolve_text", exc, read_only=False)
        return result.to_payload()
