from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .editor_bridge import LiveEditorBridgeService, LiveEditorError
from .live_asset_overlay import (
    BLUEPRINT_CLASS,
    LiveOverlayError,
    get_session_overlay,
    prove_and_install,
    reset_session_overlay,
)

__all__ = [
    "get_session_overlay",
    "register_live_action_tools",
    "reset_session_overlay",
]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def register_live_action_tools(
    *,
    server: Any,
    live_editor_service: LiveEditorBridgeService,
    verification_evidence_store: Any | None = None,
    tool_annotations_type: Any,
    error_response: Any,
) -> None:
    action_annotations = tool_annotations_type(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )

    def call(tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        capture_token = (
            verification_evidence_store.begin_registered_tool(tool_name, params)
            if verification_evidence_store is not None
            else None
        )
        try:
            response = live_editor_service.call_tool(tool_name, params)
            if verification_evidence_store is not None:
                verification_evidence_store.finish_registered_tool(capture_token, response)
            return response
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response(tool_name, exc, read_only=False)

    @server.tool(annotations=action_annotations)
    def ue_open_asset(asset_path: str) -> dict[str, Any]:
        """Open one exact Asset Registry asset in its registered editor without saving it."""
        return call("ue_open_asset", {"assetPath": asset_path})

    @server.tool(annotations=action_annotations)
    def ue_focus_asset(asset_path: str) -> dict[str, Any]:
        """Bring an already-open exact asset editor to the front without loading another asset."""
        return call("ue_focus_asset", {"assetPath": asset_path})

    @server.tool(annotations=action_annotations)
    def ue_sync_content_browser(asset_path: str) -> dict[str, Any]:
        """Synchronize the Content Browser to one exact Asset Registry asset without loading it."""
        return call("ue_sync_content_browser", {"assetPath": asset_path})

    @server.tool(annotations=action_annotations)
    def ue_focus_actor(actor_guid: str) -> dict[str, Any]:
        """Select and frame one ActorGuid in the current Editor World; PIE worlds are unsupported."""
        return call("ue_focus_actor", {"actorGuid": actor_guid})

    @server.tool(annotations=action_annotations)
    def ue_compile_blueprint(asset_path: str) -> dict[str, Any]:
        """Load and compile one exact Blueprint in memory without saving its package."""
        return call("ue_compile_blueprint", {"assetPath": asset_path})

    @server.tool(annotations=action_annotations)
    def ue_validate_asset(asset_path: str, max_issues: int = 100) -> dict[str, Any]:
        """Run Unreal Data Validation for one exact asset with bounded returned diagnostics."""
        return call(
            "ue_validate_asset",
            {"assetPath": asset_path, "maxIssues": max_issues},
        )

    @server.tool(annotations=action_annotations)
    def ue_validate_folder(
        package_path: str,
        recursive: bool = True,
        max_assets: int = 100,
        max_issues: int = 100,
    ) -> dict[str, Any]:
        """Run bounded Unreal Data Validation below one non-root /Game package path."""
        return call(
            "ue_validate_folder",
            {
                "packagePath": package_path,
                "recursive": recursive,
                "maxAssets": max_assets,
                "maxIssues": max_issues,
            },
        )

    @server.tool(annotations=action_annotations)
    def ue_run_automation_test(
        test_name: str,
        timeout_seconds: int = 120,
        max_entries: int = 100,
    ) -> dict[str, Any]:
        """Run one exact registered single-participant Unreal Automation Test without filters or command strings."""
        return call(
            "ue_run_automation_test",
            {
                "testName": test_name,
                "timeoutSeconds": timeout_seconds,
                "maxEntries": max_entries,
            },
        )

    def _invalid(message: str) -> dict[str, Any]:
        return error_response(
            "ue_set_niagara_module_input",
            LiveEditorError("live-editor-invalid-parameters", message),
            read_only=False,
        )

    @server.tool(annotations=action_annotations)
    def ue_set_niagara_module_input(
        system_path: str,
        module_name: str,
        input_name: str,
        value: Any = None,
        emitter_name: str = "",
        emitter_handle_id: str = "",
        stage: str = "",
        module_occurrence: int = 0,
        expected_value_mode: str = "Local",
        expected_old_value: Any = None,
        verify_undo: bool = True,
        compile_timeout_seconds: int = 30,
    ) -> dict[str, Any]:
        """Set one Local Niagara module input constant; refuses Linked/Dynamic sources and never saves."""
        if (
            not isinstance(system_path, str)
            or not system_path.startswith("/Game/")
            or "\\" in system_path
            or ".." in system_path
            or ":" in system_path
            or any(ord(character) < 32 for character in system_path)
        ):
            return _invalid("system_path must be one exact /Game package or object path.",
            )
        if not isinstance(module_name, str) or not module_name:
            return _invalid("module_name is required."
            )
        if not isinstance(input_name, str) or not input_name:
            return _invalid("input_name is required."
            )
        if value is None:
            return _invalid("value is required."
            )
        if type(module_occurrence) is not int or module_occurrence < 0:
            return _invalid("module_occurrence must be a non-negative integer."
            )
        if type(compile_timeout_seconds) is not int or not 1 <= compile_timeout_seconds <= 300:
            return _invalid("compile_timeout_seconds must be an integer in [1, 300].",
            )
        params: dict[str, Any] = {
            "systemPath": system_path,
            "moduleName": module_name,
            "inputName": input_name,
            "value": value,
            "moduleOccurrence": module_occurrence,
            "expectedValueMode": expected_value_mode,
            "verifyUndo": verify_undo,
            "compileTimeoutSeconds": compile_timeout_seconds,
        }
        if emitter_name:
            params["emitterName"] = emitter_name
        if emitter_handle_id:
            params["emitterHandleId"] = emitter_handle_id
        if stage:
            params["stage"] = stage
        if expected_old_value is not None:
            params["expectedOldValue"] = expected_old_value
        return call("ue_set_niagara_module_input", params)
    @server.tool(annotations=action_annotations)
    def ue_refresh_asset_index_live(asset_path: str) -> dict[str, Any]:
        """Prove one saved Blueprint's live canonical record and install it in this session's overlay without writing the index, saving, or restarting."""
        try:
            stored = prove_and_install(
                project_path=getattr(live_editor_service.config, "project_path", ""),
                asset_path=asset_path,
                expected_class=BLUEPRINT_CLASS,
                call_method=live_editor_service.call_method,
                captured_at_utc=_utc_now_iso(),
            )
            overlay = get_session_overlay()
            return {
                "schemaVersion": "1.0",
                "tool": "ue_refresh_asset_index_live",
                "ok": True,
                "readOnly": False,
                "assetPath": asset_path,
                "revision": stored["revision"],
                # Local overlay only: the immutable index was not touched, so the
                # snapshot is not fresh and no restart is implied.
                "indexFresh": False,
                "overlayUpdated": True,
                # 0-3.3 slice 4: this record came from the explicit tool, not from
                # the read path's disk-change detection.
                "overlayCause": "explicit",
                "restartRequired": False,
                "overlayEntryCount": overlay.stats()["entryCount"],
            }
        except (
            LiveOverlayError,
            LiveEditorError,
            FileNotFoundError,
            OSError,
            ValueError,
            RuntimeError,
        ) as exc:
            return error_response("ue_refresh_asset_index_live", exc, read_only=False)
