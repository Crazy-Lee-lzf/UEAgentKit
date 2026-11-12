from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .editor_bridge import LiveEditorBridgeService, LiveEditorError
from .live_asset_overlay import (
    BLUEPRINT_CLASS,
    LiveAssetOverlay,
    LiveOverlayError,
    build_overlay_record,
    package_file_for_object_path,
    sha256_file,
)

_SESSION_OVERLAY = LiveAssetOverlay()


def get_session_overlay() -> LiveAssetOverlay:
    """The MCP process's overlay: one process serves one fixed project."""
    return _SESSION_OVERLAY


def reset_session_overlay() -> LiveAssetOverlay:
    """Test-only: drop every record and hand back the fresh store."""
    _SESSION_OVERLAY.clear()
    return _SESSION_OVERLAY


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
    @server.tool(annotations=action_annotations)
    def ue_refresh_asset_index_live(asset_path: str) -> dict[str, Any]:
        """Prove one saved Blueprint's live canonical record and install it in this session's overlay without writing the index, saving, or restarting."""
        try:
            project_path = Path(str(getattr(live_editor_service.config, "project_path", "")))
            if not str(project_path):
                raise LiveOverlayError(
                    "live-index-refresh-no-project",
                    "The live Editor service has no fixed project path.",
                )
            package_file = package_file_for_object_path(project_path, asset_path)
            if not package_file.is_file():
                raise LiveOverlayError(
                    "live-index-refresh-asset-file-missing",
                    "There is no package file on disk for this asset path.",
                )
            payload = live_editor_service.call_method(
                "editor.getBlueprintGraphContext",
                {"assetPath": asset_path, "offset": 0, "limit": 1},
            )
            if not isinstance(payload, dict):
                raise LiveOverlayError(
                    "live-index-refresh-no-canonical",
                    "The Editor Bridge returned no canonical object for this asset.",
                )
            context = payload.get("context")
            asset_class = str(context.get("assetClass", "")) if isinstance(context, dict) else ""
            record = build_overlay_record(
                asset_path=asset_path,
                asset_class=asset_class,
                expected_class=BLUEPRINT_CLASS,
                bridge_canonical=payload,
                disk_sha256=sha256_file(package_file),
                editor_session_id=str(payload.get("editorSessionId", "")),
                captured_at_utc=_utc_now_iso(),
            )
            overlay = get_session_overlay()
            stored = overlay.install(record)
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
