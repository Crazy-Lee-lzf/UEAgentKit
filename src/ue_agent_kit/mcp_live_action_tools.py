from __future__ import annotations

from typing import Any

from .editor_bridge import LiveEditorBridgeService, LiveEditorError


def register_live_action_tools(
    *,
    server: Any,
    live_editor_service: LiveEditorBridgeService,
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
        try:
            return live_editor_service.call_tool(tool_name, params)
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
