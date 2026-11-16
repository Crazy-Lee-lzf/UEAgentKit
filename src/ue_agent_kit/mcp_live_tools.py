from __future__ import annotations

import json
import re
from typing import Any, Literal

from .editor_bridge import LiveEditorBridgeService, LiveEditorError

MAX_GRAPH_CONTEXT_BYTES = 700 * 1024
GRAPH_CONTEXT_TOOL_NAME = "ue_get_focused_blueprint_graph_context"
_GUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
_MAX_CONTEXT_OFFSET = 1000000
_MAX_CONTEXT_LIMIT = 500


def register_live_read_tools(
    *,
    server: Any,
    live_editor_service: LiveEditorBridgeService,
    index_service: Any,
    audit_report_root: Any,
    read_annotations: Any,
    tool_annotations_type: Any,
    error_response: Any,
    pure_live: bool = False,
) -> None:
    @server.tool(annotations=read_annotations)
    def ue_editor_status() -> dict[str, Any]:
        """Return fixed-project Live Editor availability, version, session, PIE, level, and Dirty summary."""
        return {
            "schemaVersion": "1.0",
            "tool": "ue_editor_status",
            "ok": True,
            "readOnly": True,
            "source": "live-editor-memory",
            "result": live_editor_service.status(),
        }

    @server.tool(annotations=read_annotations)
    def ue_get_selection() -> dict[str, Any]:
        """Return the bounded Actor, Component, Asset, and Object selection from the fixed Editor session."""
        try:
            return live_editor_service.call_tool("ue_get_selection")
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_get_selection", exc, read_only=True)

    @server.tool(annotations=read_annotations)
    def ue_get_open_assets() -> dict[str, Any]:
        """Return assets currently opened in registered asset editors in the fixed Editor session."""
        try:
            return live_editor_service.call_tool("ue_get_open_assets")
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_get_open_assets", exc, read_only=True)

    @server.tool(annotations=read_annotations)
    def ue_get_dirty_assets() -> dict[str, Any]:
        """Return bounded Dirty /Game packages and their Asset Registry paths from Editor memory."""
        try:
            return live_editor_service.call_tool("ue_get_dirty_assets")
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_get_dirty_assets", exc, read_only=True)

    @server.tool(annotations=read_annotations)
    def ue_get_current_level() -> dict[str, Any]:
        """Return the fixed Editor world, persistent/current level, World Partition, and Dirty state."""
        try:
            return live_editor_service.call_tool("ue_get_current_level")
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_get_current_level", exc, read_only=True)

    @server.tool(annotations=read_annotations)
    def ue_get_pie_state() -> dict[str, Any]:
        """Return whether the fixed Editor is stopped, playing, or simulating in editor."""
        try:
            return live_editor_service.call_tool("ue_get_pie_state")
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_get_pie_state", exc, read_only=True)

    @server.tool(annotations=read_annotations)
    def ue_get_pie_world_contexts(max_contexts: int = 16) -> dict[str, Any]:
        """List bounded live PIE worlds with unique PIE instance ids and Server/Client identification."""
        try:
            return live_editor_service.call_tool(
                "ue_get_pie_world_contexts",
                {"maxContexts": max_contexts},
            )
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_get_pie_world_contexts", exc, read_only=True)

    @server.tool(annotations=read_annotations)
    def ue_list_pie_actors(
        world_context_id: str = "",
        pie_instance_id: int = -1,
        offset: int = 0,
        limit: int = 50,
        name_contains: str = "",
        class_contains: str = "",
        tag_contains: str = "",
        world_path: str = "",
    ) -> dict[str, Any]:
        """List a bounded page of Actors inside exactly one PIE world (Server/Client kept apart), filtered by name/class/tag and ordered by object path."""
        try:
            params: dict[str, Any] = {
                "offset": offset,
                "limit": limit,
                "nameContains": name_contains,
                "classContains": class_contains,
                "tagContains": tag_contains,
                "worldPath": world_path,
            }
            if world_context_id:
                params["worldContextId"] = world_context_id
            else:
                params["pieInstanceId"] = pie_instance_id
            return live_editor_service.call_tool("ue_list_pie_actors", params)
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_list_pie_actors", exc, read_only=True)

    @server.tool(annotations=read_annotations)
    def ue_inspect_pie_actor(
        pie_instance_id: int,
        actor_path: str = "",
        actor_guid: str = "",
        actor_name: str = "",
        actor_label: str = "",
        actor_class_path: str = "",
        world_path: str = "",
        max_components: int = 64,
        max_assets_per_component: int = 8,
    ) -> dict[str, Any]:
        """Inspect one exact-world PIE Actor (path, class, transform, NetRole) plus bounded component class/path/assets."""
        try:
            return live_editor_service.call_tool(
                "ue_inspect_pie_actor",
                {
                    "pieInstanceId": pie_instance_id,
                    "actorPath": actor_path,
                    "actorGuid": actor_guid,
                    "actorName": actor_name,
                    "actorLabel": actor_label,
                    "actorClassPath": actor_class_path,
                    "worldPath": world_path,
                    "maxComponents": max_components,
                    "maxAssetsPerComponent": max_assets_per_component,
                },
            )
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_inspect_pie_actor", exc, read_only=True)

    @server.tool(annotations=read_annotations)
    def ue_get_pie_scalars(
        pie_instance_id: int,
        property_names: list[str],
        actor_path: str = "",
        actor_guid: str = "",
        actor_name: str = "",
        actor_label: str = "",
        component_path: str = "",
        component_name: str = "",
        component_class_path: str = "",
        world_path: str = "",
        max_string_chars: int = 256,
    ) -> dict[str, Any]:
        """Read up to 8 named immediate scalar fields from one exact PIE Actor or owned Component (read-only)."""
        try:
            return live_editor_service.call_tool(
                "ue_get_pie_scalars",
                {
                    "pieInstanceId": pie_instance_id,
                    "actorPath": actor_path,
                    "actorGuid": actor_guid,
                    "actorName": actor_name,
                    "actorLabel": actor_label,
                    "componentPath": component_path,
                    "componentName": component_name,
                    "componentClassPath": component_class_path,
                    "worldPath": world_path,
                    "propertyNames": property_names,
                    "maxStringChars": max_string_chars,
                },
            )
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_get_pie_scalars", exc, read_only=True)

    @server.tool(annotations=read_annotations)
    def ue_list_pie_object_properties(
        pie_instance_id: int,
        actor_path: str = "",
        actor_guid: str = "",
        actor_name: str = "",
        actor_label: str = "",
        actor_class_path: str = "",
        object_path: str = "",
        component_path: str = "",
        component_name: str = "",
        component_class_path: str = "",
        world_path: str = "",
        name_contains: str = "",
        max_properties: int = 128,
    ) -> dict[str, Any]:
        """List reflected properties of one exact PIE object with kind and per-type support; no Edit/BlueprintVisible gate."""
        try:
            params: dict[str, Any] = {
                "pieInstanceId": pie_instance_id,
                "maxProperties": max_properties,
            }
            for field, value in (
                ("actorPath", actor_path),
                ("actorGuid", actor_guid),
                ("actorName", actor_name),
                ("actorLabel", actor_label),
                ("actorClassPath", actor_class_path),
                ("objectPath", object_path),
                ("componentPath", component_path),
                ("componentName", component_name),
                ("componentClassPath", component_class_path),
                ("worldPath", world_path),
                ("nameContains", name_contains),
            ):
                if value:
                    params[field] = value
            return live_editor_service.call_tool("ue_list_pie_object_properties", params)
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_list_pie_object_properties", exc, read_only=True)

    @server.tool(annotations=read_annotations)
    def ue_read_pie_object_properties(
        pie_instance_id: int,
        property_names: list[str],
        actor_path: str = "",
        actor_guid: str = "",
        actor_name: str = "",
        actor_label: str = "",
        actor_class_path: str = "",
        object_path: str = "",
        component_path: str = "",
        component_name: str = "",
        component_class_path: str = "",
        world_path: str = "",
        offset: int = 0,
        limit: int = 50,
        max_string_chars: int = 256,
    ) -> dict[str, Any]:
        """Read up to 16 named reflected property values from one exact PIE object; containers are paged and object references are identity only."""
        try:
            params: dict[str, Any] = {
                "pieInstanceId": pie_instance_id,
                "propertyNames": property_names,
                "offset": offset,
                "limit": limit,
                "maxStringChars": max_string_chars,
            }
            for field, value in (
                ("actorPath", actor_path),
                ("actorGuid", actor_guid),
                ("actorName", actor_name),
                ("actorLabel", actor_label),
                ("actorClassPath", actor_class_path),
                ("objectPath", object_path),
                ("componentPath", component_path),
                ("componentName", component_name),
                ("componentClassPath", component_class_path),
                ("worldPath", world_path),
            ):
                if value:
                    params[field] = value
            return live_editor_service.call_tool("ue_read_pie_object_properties", params)
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_read_pie_object_properties", exc, read_only=True)

    @server.tool(annotations=read_annotations)
    def ue_get_output_log(
        category: str = "",
        minimum_verbosity: Literal[
            "fatal",
            "error",
            "warning",
            "display",
            "log",
            "verbose",
            "veryverbose",
        ] = "log",
        keyword: str = "",
        since_sequence: int = 0,
        since_utc: str = "",
        until_utc: str = "",
        pie_session_id: int = -1,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Read bounded current-session Output Log entries using category, severity, text, UTC, PIE, and sequence filters."""
        try:
            return live_editor_service.call_tool(
                "ue_get_output_log",
                {
                    "category": category,
                    "minimumVerbosity": minimum_verbosity,
                    "keyword": keyword,
                    "sinceSequence": since_sequence,
                    "sinceUtc": since_utc,
                    "untilUtc": until_utc,
                    "pieSessionId": pie_session_id,
                    "limit": limit,
                },
            )
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_get_output_log", exc, read_only=True)

    @server.tool(annotations=read_annotations)
    def ue_get_compile_errors(
        asset_path: str = "",
        since_sequence: int = 0,
        pie_session_id: int = -1,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Return captured compiler-related warnings/errors plus current loaded Blueprint compile status."""
        try:
            return live_editor_service.call_tool(
                "ue_get_compile_errors",
                {
                    "assetPath": asset_path,
                    "sinceSequence": since_sequence,
                    "pieSessionId": pie_session_id,
                    "limit": limit,
                },
            )
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_get_compile_errors", exc, read_only=True)

    @server.tool(annotations=read_annotations)
    def ue_inspect_asset_live(asset_path: str) -> dict[str, Any]:
        """Inspect one exact /Game asset in Asset Registry and current Editor memory without loading or modifying it."""
        try:
            return live_editor_service.call_tool(
                "ue_inspect_asset_live",
                {"assetPath": asset_path},
            )
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_inspect_asset_live", exc, read_only=True)

    @server.tool(annotations=read_annotations)
    def ue_get_niagara_module_stack(
        system_path: str,
        include_inputs: bool = True,
        max_modules: int = 128,
        max_inputs_per_module: int = 32,
    ) -> dict[str, Any]:
        """Read a Niagara System's real Module Stack by execution stage with module order and input value sources, read-only."""
        try:
            if (
                not isinstance(system_path, str)
                or not system_path.startswith("/Game/")
                or "\\" in system_path
                or ".." in system_path
                or ":" in system_path
                or any(ord(character) < 32 for character in system_path)
            ):
                raise LiveEditorError(
                    "live-editor-invalid-parameters",
                    "system_path must be one exact /Game package or object path.",
                )
            if not isinstance(include_inputs, bool):
                raise LiveEditorError(
                    "live-editor-invalid-parameters",
                    "include_inputs must be a boolean.",
                )
            if type(max_modules) is not int or not 1 <= max_modules <= 1024:
                raise LiveEditorError(
                    "live-editor-invalid-parameters",
                    "max_modules must be an integer in [1, 1024].",
                )
            if type(max_inputs_per_module) is not int or not 0 <= max_inputs_per_module <= 256:
                raise LiveEditorError(
                    "live-editor-invalid-parameters",
                    "max_inputs_per_module must be an integer in [0, 256].",
                )
            return live_editor_service.call_tool(
                "ue_get_niagara_module_stack",
                {
                    "systemPath": system_path,
                    "includeInputs": include_inputs,
                    "maxModules": max_modules,
                    "maxInputsPerModule": max_inputs_per_module,
                },
            )
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_get_niagara_module_stack", exc, read_only=True)

    @server.tool(annotations=read_annotations)
    def ue_get_blueprint_graph_selection() -> dict[str, Any]:
        """Return the focused Graph and bounded selected Nodes from the most recently active ordinary Blueprint Editor."""
        try:
            return live_editor_service.call_tool("ue_get_blueprint_graph_selection")
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_get_blueprint_graph_selection", exc, read_only=True)

    @server.tool(annotations=read_annotations)
    def ue_get_blueprint_graph_layout_preflight() -> dict[str, Any]:
        """Read the exact focused Blueprint graph revision, fingerprint, node IDs, and positions for layout planning only."""
        try:
            return live_editor_service.call_tool("ue_get_blueprint_graph_layout_preflight")
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_get_blueprint_graph_layout_preflight", exc, read_only=True)

    @server.tool(annotations=read_annotations)
    def ue_get_focused_blueprint_graph_context() -> dict[str, Any]:
        """Return the exact focused ordinary Blueprint graph canonical context, read-only, without opening or focusing assets."""
        try:
            payload = live_editor_service.call_tool(GRAPH_CONTEXT_TOOL_NAME)
            result = payload.get("result")
            if not isinstance(result, dict):
                raise LiveEditorError(
                    "live-editor-invalid-response",
                    "The Editor Bridge returned an invalid graph context object.",
                )
            if result.get("available") is not True:
                raise LiveEditorError(
                    str(result.get("reasonCode") or "blueprint-graph-context-unavailable"),
                    str(result.get("reason") or "No focused ordinary Blueprint graph is available."),
                )
            if result.get("readOnly") is not True:
                raise LiveEditorError(
                    "blueprint-graph-context-not-readonly",
                    "The Editor Bridge did not mark the graph context as read-only.",
                )
            encoded = json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")
            if len(encoded) > MAX_GRAPH_CONTEXT_BYTES:
                raise LiveEditorError(
                    "blueprint-graph-context-too-large",
                    "The focused graph context exceeds the 700 KiB read-only boundary.",
                )
            payload["writeAuthorized"] = False
            payload["editorVerified"] = False
            payload["contextOnly"] = True
            return payload
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            response = error_response(GRAPH_CONTEXT_TOOL_NAME, exc, read_only=True)
            response["writeAuthorized"] = False
            response["editorVerified"] = False
            response["contextOnly"] = True
            return response

    @server.tool(annotations=read_annotations)
    def ue_get_blueprint_graph_context(
        asset_path: str,
        graph_guid: str = "",
        offset: int = 0,
        limit: int = 200,
    ) -> dict[str, Any]:
        """Read any Blueprint's canonical graph context by asset path (optional graph, paginated nodes) without opening or focusing it."""
        try:
            if (
                not isinstance(asset_path, str)
                or not asset_path.startswith("/Game/")
                or "." not in asset_path
                or "\\" in asset_path
                or ".." in asset_path
                or ":" in asset_path
                or any(ord(character) < 32 for character in asset_path)
            ):
                raise LiveEditorError(
                    "live-editor-invalid-parameters",
                    "asset_path must be one exact /Game Object Path.",
                )
            if graph_guid:
                if (
                    not isinstance(graph_guid, str)
                    or _GUID_PATTERN.fullmatch(graph_guid) is None
                ):
                    raise LiveEditorError(
                        "live-editor-invalid-parameters",
                        "graph_guid must be one canonical lowercase hyphenated GUID.",
                    )
            if type(offset) is not int or not 0 <= offset <= _MAX_CONTEXT_OFFSET:
                raise LiveEditorError(
                    "live-editor-invalid-parameters",
                    f"offset must be an integer in [0, {_MAX_CONTEXT_OFFSET}].",
                )
            if type(limit) is not int or not 1 <= limit <= _MAX_CONTEXT_LIMIT:
                raise LiveEditorError(
                    "live-editor-invalid-parameters",
                    f"limit must be an integer in [1, {_MAX_CONTEXT_LIMIT}].",
                )
            request: dict[str, Any] = {
                "assetPath": asset_path,
                "offset": offset,
                "limit": limit,
            }
            if graph_guid:
                request["graphGuid"] = graph_guid
            payload = live_editor_service.call_method(
                "editor.getBlueprintGraphContext", request
            )
            # call_method returns the bridge's result object directly; only call_tool
            # wraps it under a "result" key. Reading payload["result"] here always
            # yielded None and surfaced live-editor-invalid-response for every asset.
            result = payload
            if not isinstance(result, dict):
                raise LiveEditorError(
                    "live-editor-invalid-response",
                    "The Editor Bridge returned an invalid graph context object.",
                )
            if result.get("available") is not True:
                raise LiveEditorError(
                    str(result.get("reasonCode") or "blueprint-graph-context-unavailable"),
                    str(result.get("reason") or "The graph context is unavailable."),
                )
            if result.get("readOnly") is not True:
                raise LiveEditorError(
                    "blueprint-graph-context-not-readonly",
                    "The Editor Bridge did not mark the graph context as read-only.",
                )
            encoded = json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")
            if len(encoded) > MAX_GRAPH_CONTEXT_BYTES:
                raise LiveEditorError(
                    "blueprint-graph-context-too-large",
                    "The graph context exceeds the 700 KiB read-only boundary.",
                )
            payload["writeAuthorized"] = False
            payload["editorVerified"] = False
            payload["contextOnly"] = True
            return payload
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            response = error_response("ue_get_blueprint_graph_context", exc, read_only=True)
            response["writeAuthorized"] = False
            response["editorVerified"] = False
            response["contextOnly"] = True
            return response

    # Pure-live mode exposes only the bounded read set above: graph-layout
    # preview and the retarget readers are deliberately not registered.
    if pure_live:
        return

    from .mcp_graph_layout_preview import register_graph_layout_preview_tool

    register_graph_layout_preview_tool(
        server=server,
        live_editor_service=live_editor_service,
        read_annotations=read_annotations,
        error_response=error_response,
    )

    # Retarget read-only tools keep the live-read group order matching the
    # Tool Registry while living in their own module.
    from .mcp_retarget_tools import register_retarget_tools

    register_retarget_tools(
        server=server,
        live_editor_service=live_editor_service,
        index_service=index_service,
        report_root=audit_report_root,
        read_annotations=read_annotations,
        tool_annotations_type=tool_annotations_type,
        error_response=error_response,
    )
