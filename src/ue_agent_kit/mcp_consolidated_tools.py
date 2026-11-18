"""Consolidated MCP tools (A0 / P1).

One tool per family instead of one tool per verb.  Each consolidated tool is a
thin dispatcher: it validates the action, builds the payload with the same
builder the original tool uses, and forwards to the same bridge call.  No
behaviour is re-implemented here.

The payload builders live in this module so both surfaces stay identical: the
per-verb PIE tools call them, and ``ue_pie`` dispatches to them.

When the consolidated surface is active, the replaced names are never
registered (see ``CONSOLIDATED_REPLACED_TOOLS``), so a client never sees both.
"""

from __future__ import annotations

import sqlite3
from typing import Any

PIE_ACTIONS: dict[str, str] = {
    "state": "ue_get_pie_state",
    "worlds": "ue_get_pie_world_contexts",
    "list_actors": "ue_list_pie_actors",
    "inspect_actor": "ue_inspect_pie_actor",
    "scalars": "ue_get_pie_scalars",
    "list_properties": "ue_list_pie_object_properties",
    "read_properties": "ue_read_pie_object_properties",
}

_PIE_ACTION_ALIASES = {
    "world_contexts": "worlds",
    "actors": "list_actors",
    "properties": "list_properties",
}


def pie_state_params() -> dict[str, Any]:
    return {}


def pie_world_contexts_params(max_contexts: int = 16) -> dict[str, Any]:
    return {"maxContexts": max_contexts}


def pie_list_actors_params(
    *,
    world_context_id: str = "",
    pie_instance_id: int = -1,
    offset: int = 0,
    limit: int = 50,
    name_contains: str = "",
    class_contains: str = "",
    tag_contains: str = "",
    world_path: str = "",
) -> dict[str, Any]:
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
    return params


def pie_inspect_actor_params(
    *,
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
    return {
        "pieInstanceId": pie_instance_id,
        "actorPath": actor_path,
        "actorGuid": actor_guid,
        "actorName": actor_name,
        "actorLabel": actor_label,
        "actorClassPath": actor_class_path,
        "worldPath": world_path,
        "maxComponents": max_components,
        "maxAssetsPerComponent": max_assets_per_component,
    }


def pie_scalars_params(
    *,
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
    return {
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
    }


def _object_identity_params(
    *,
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
) -> dict[str, Any]:
    params: dict[str, Any] = {"pieInstanceId": pie_instance_id}
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
    return params


def pie_list_object_properties_params(
    *,
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
    params = _object_identity_params(
        pie_instance_id=pie_instance_id,
        actor_path=actor_path,
        actor_guid=actor_guid,
        actor_name=actor_name,
        actor_label=actor_label,
        actor_class_path=actor_class_path,
        object_path=object_path,
        component_path=component_path,
        component_name=component_name,
        component_class_path=component_class_path,
        world_path=world_path,
    )
    params["maxProperties"] = max_properties
    if name_contains:
        params["nameContains"] = name_contains
    return params


def pie_read_object_properties_params(
    *,
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
    params = _object_identity_params(
        pie_instance_id=pie_instance_id,
        actor_path=actor_path,
        actor_guid=actor_guid,
        actor_name=actor_name,
        actor_label=actor_label,
        actor_class_path=actor_class_path,
        object_path=object_path,
        component_path=component_path,
        component_name=component_name,
        component_class_path=component_class_path,
        world_path=world_path,
    )
    params["propertyNames"] = property_names
    params["offset"] = offset
    params["limit"] = limit
    params["maxStringChars"] = max_string_chars
    return params


def _resolve_action(action: str, actions: dict[str, str], aliases: dict[str, str], tool: str) -> str:
    key = aliases.get(action, action)
    if key not in actions:
        raise ValueError(
            f"{tool}: unknown action {action!r}; expected one of {sorted(actions)}."
        )
    return key


def _pie_params(action: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Build the replaced tool's payload for one action."""

    if action == "state":
        return pie_state_params()
    if action == "worlds":
        return pie_world_contexts_params(max_contexts=int(raw.get("max_contexts", 16)))
    if action == "list_actors":
        return pie_list_actors_params(
            world_context_id=str(raw.get("world_context_id") or ""),
            pie_instance_id=int(raw.get("pie_instance_id", -1)),
            offset=int(raw.get("offset", 0)),
            limit=int(raw.get("limit", 50)),
            name_contains=str(raw.get("name_contains") or ""),
            class_contains=str(raw.get("class_contains") or ""),
            tag_contains=str(raw.get("tag_contains") or ""),
            world_path=str(raw.get("world_path") or ""),
        )
    if action == "inspect_actor":
        return pie_inspect_actor_params(
            pie_instance_id=int(raw.get("pie_instance_id", -1)),
            actor_path=str(raw.get("actor_path") or ""),
            actor_guid=str(raw.get("actor_guid") or ""),
            actor_name=str(raw.get("actor_name") or ""),
            actor_label=str(raw.get("actor_label") or ""),
            actor_class_path=str(raw.get("actor_class_path") or ""),
            world_path=str(raw.get("world_path") or ""),
            max_components=int(raw.get("max_components", 64)),
            max_assets_per_component=int(raw.get("max_assets_per_component", 8)),
        )
    identity = {
        "pie_instance_id": int(raw.get("pie_instance_id", -1)),
        "actor_path": str(raw.get("actor_path") or ""),
        "actor_guid": str(raw.get("actor_guid") or ""),
        "actor_name": str(raw.get("actor_name") or ""),
        "actor_label": str(raw.get("actor_label") or ""),
        "actor_class_path": str(raw.get("actor_class_path") or ""),
        "object_path": str(raw.get("object_path") or ""),
        "component_path": str(raw.get("component_path") or ""),
        "component_name": str(raw.get("component_name") or ""),
        "component_class_path": str(raw.get("component_class_path") or ""),
        "world_path": str(raw.get("world_path") or ""),
    }
    if action == "list_properties":
        return pie_list_object_properties_params(
            name_contains=str(raw.get("name_contains") or ""),
            max_properties=int(raw.get("max_properties", 128)),
            **identity,
        )
    names = [str(name) for name in (raw.get("property_names") or [])]
    if not names:
        raise ValueError(f"ue_pie(action={action!r}) needs property_names.")
    if action == "scalars":
        return pie_scalars_params(
            pie_instance_id=identity["pie_instance_id"],
            property_names=names,
            actor_path=identity["actor_path"],
            actor_guid=identity["actor_guid"],
            actor_name=identity["actor_name"],
            actor_label=identity["actor_label"],
            component_path=identity["component_path"],
            component_name=identity["component_name"],
            component_class_path=identity["component_class_path"],
            world_path=identity["world_path"],
            max_string_chars=int(raw.get("max_string_chars", 256)),
        )
    return pie_read_object_properties_params(
        property_names=names,
        offset=int(raw.get("offset", 0)),
        limit=int(raw.get("limit", 50)),
        max_string_chars=int(raw.get("max_string_chars", 256)),
        **identity,
    )


def register_consolidated_tools(
    *,
    server: Any,
    live_editor_service: Any,
    read_annotations: Any,
    error_response: Any,
) -> None:
    """Register one tool per consolidated family on the real server."""

    @server.tool(annotations=read_annotations)
    def ue_pie(
        action: str,
        world_context_id: str = "",
        pie_instance_id: int = -1,
        world_path: str = "",
        offset: int = 0,
        limit: int = 50,
        name_contains: str = "",
        class_contains: str = "",
        tag_contains: str = "",
        actor_path: str = "",
        actor_guid: str = "",
        actor_name: str = "",
        actor_label: str = "",
        actor_class_path: str = "",
        component_name: str = "",
        component_path: str = "",
        component_class_path: str = "",
        object_path: str = "",
        property_names: list[str] | None = None,
        max_contexts: int = 16,
        max_components: int = 64,
        max_assets_per_component: int = 8,
        max_properties: int = 128,
        max_string_chars: int = 256,
    ) -> dict[str, Any]:
        """One read-only PIE entry point: state, worlds, actors, one actor, scalars, or reflected properties of any object."""

        raw = {
            "world_context_id": world_context_id,
            "pie_instance_id": pie_instance_id,
            "world_path": world_path,
            "offset": offset,
            "limit": limit,
            "name_contains": name_contains,
            "class_contains": class_contains,
            "tag_contains": tag_contains,
            "actor_path": actor_path,
            "actor_guid": actor_guid,
            "actor_name": actor_name,
            "actor_label": actor_label,
            "actor_class_path": actor_class_path,
            "component_name": component_name,
            "component_path": component_path,
            "component_class_path": component_class_path,
            "object_path": object_path,
            "property_names": property_names or [],
            "max_contexts": max_contexts,
            "max_components": max_components,
            "max_assets_per_component": max_assets_per_component,
            "max_properties": max_properties,
            "max_string_chars": max_string_chars,
        }
        try:
            resolved = _resolve_action(action, PIE_ACTIONS, _PIE_ACTION_ALIASES, "ue_pie")
            target = PIE_ACTIONS[resolved]
            params = _pie_params(resolved, raw)
            result = (
                live_editor_service.call_tool(target, params)
                if params
                else live_editor_service.call_tool(target)
            )
            if isinstance(result, dict):
                result = dict(result)
                result.setdefault("action", resolved)
            return result
        except (ValueError, RuntimeError) as exc:
            return error_response("ue_pie", exc, read_only=True)


DATATABLE_ACTIONS: dict[str, str] = {
    "set_cell": "setDataTableCell",
    "set_row_fields": "setDataTableRowFields",
    "add_row": "addDataTableRow",
    "remove_row": "removeDataTableRow",
    "rename_row": "renameDataTableRow",
}

_DATATABLE_ACTION_ALIASES = {
    "cell": "set_cell",
    "row_fields": "set_row_fields",
    "add": "add_row",
    "remove": "remove_row",
    "rename": "rename_row",
}


def datatable_change(
    action: str,
    *,
    row_name: str,
    field_name: str = "",
    value: Any = None,
    values: dict[str, Any] | None = None,
    new_row_name: str = "",
) -> dict[str, Any]:
    """The prepared-change kwargs one DataTable edit needs: operation, target, value.

    This mirrors what the five per-verb tools sent to
    ``workflow_service.prepare_high_level_change`` before they were consolidated.
    """

    if action == "set_cell":
        if not field_name:
            raise ValueError("ue_datatable(action='set_cell') needs field_name.")
        if value is None:
            raise ValueError("ue_datatable(action='set_cell') needs value.")
        return {
            "operation": "setDataTableCell",
            "target": {"rowName": row_name, "fieldName": field_name},
            "value": value,
        }
    if action == "set_row_fields":
        if not values:
            raise ValueError(
                "ue_datatable(action='set_row_fields') needs a non-empty values object."
            )
        return {
            "operation": "setDataTableRowFields",
            "target": {"rowName": row_name},
            "value": dict(values),
        }
    if action == "add_row":
        return {
            "operation": "addDataTableRow",
            "target": {"rowName": row_name},
            "value": dict(values or {}),
        }
    if action == "remove_row":
        return {
            "operation": "removeDataTableRow",
            "target": {"rowName": row_name},
            "value": True,
        }
    if not new_row_name:
        raise ValueError("ue_datatable(action='rename_row') needs new_row_name.")
    return {
        "operation": "renameDataTableRow",
        "target": {"rowName": row_name, "newRowName": new_row_name},
        "value": True,
    }


def register_datatable_tool(
    *,
    server: Any,
    workflow_service: Any,
    planning_annotations: Any,
    error_response: Any,
) -> None:
    """Register ``ue_datatable`` where the five per-verb tools used to sit.

    It is registered from the workflow module so the client-visible order keeps
    following TOOL_REGISTRY.
    """

    @server.tool(annotations=planning_annotations)
    def ue_datatable(
        action: str,
        asset_path: str,
        row_name: str,
        field_name: str = "",
        value: Any = None,
        values: dict[str, Any] | None = None,
        new_row_name: str = "",
        mode: str = "Plan",
        description: str = "",
    ) -> dict[str, Any]:
        """One policy-gated DataTable row edit: set_cell, set_row_fields, add_row, remove_row, rename_row (Plan or DryRun)."""

        try:
            resolved = _resolve_action(
                action, DATATABLE_ACTIONS, _DATATABLE_ACTION_ALIASES, "ue_datatable"
            )
            change = datatable_change(
                resolved,
                row_name=row_name,
                field_name=field_name,
                value=value,
                values=values,
                new_row_name=new_row_name,
            )
            result = workflow_service.prepare_high_level_change(
                tool_name="ue_datatable",
                mode=mode,
                asset_path=asset_path,
                description=description,
                **change,
            )
            if isinstance(result, dict):
                result = dict(result)
                result.setdefault("action", resolved)
            return result
        except (FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return error_response("ue_datatable", exc, read_only=False)


# action -> the parameters the historical per-verb tool accepted.  The facade
# passes only the ones the caller actually supplied, so every default stays the
# original tool's default.
MEMORY_READ_ACTIONS: dict[str, tuple[str, ...]] = {
    "search": ("query", "record_types", "statuses", "scope_type", "scope_key", "limit"),
    "get": ("record_id",),
    "list_l0_events": ("event_kinds", "change_set_id", "distilled", "limit"),
    "get_l0_event": ("event_id",),
    "get_context": (
        "query",
        "node_path",
        "asset_paths",
        "detail_level",
        "budget_chars",
        "max_nodes",
        "max_records",
        "max_depth",
    ),
    "expand_node": (
        "path",
        "detail_level",
        "depth",
        "budget_chars",
        "max_nodes",
        "max_records",
        "max_depth",
    ),
    "get_evidence": ("record_id",),
}

MEMORY_READ_REQUIRED: dict[str, tuple[str, ...]] = {
    "search": ("query",),
    "get": ("record_id",),
    "expand_node": ("path",),
    "get_l0_event": ("event_id",),
    "get_evidence": ("record_id",),
}

MEMORY_WRITE_ACTIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "add_rule": (
        "ue_memory_add_rule",
        (
            "subject_key",
            "title",
            "body",
            "source_ref",
            "confidence",
            "observed_at_utc",
            "scopes",
            "revision_set",
            "artifacts",
            "details",
        ),
    ),
    "record_finding": (
        "ue_memory_record_finding",
        (
            "record_type",
            "subject_key",
            "title",
            "body",
            "source_kind",
            "source_ref",
            "confidence",
            "observed_at_utc",
            "scopes",
            "revision_set",
            "artifacts",
            "details",
        ),
    ),
    "record_task": (
        "ue_memory_record_task",
        (
            "task_key",
            "title",
            "conclusion",
            "outcome",
            "patch_ref",
            "backup_manifest_ref",
            "validation_evidence_ref",
            "revision_set",
            "scopes",
            "confidence",
            "observed_at_utc",
            "patch_details",
            "backup_manifest_details",
            "validation_evidence_details",
            "details",
        ),
    ),
    "mark_superseded": (
        "ue_memory_mark_superseded",
        ("record_id", "replacement_record_id", "reason"),
    ),
    "validate": ("ue_memory_validate", ()),
}

MEMORY_WRITE_REQUIRED: dict[str, tuple[str, ...]] = {
    "add_rule": ("subject_key", "title", "body"),
    "record_finding": ("record_type", "subject_key", "title", "body"),
    "record_task": (
        "task_key",
        "title",
        "conclusion",
        "outcome",
        "patch_ref",
        "backup_manifest_ref",
        "validation_evidence_ref",
        "revision_set",
    ),
    "mark_superseded": ("record_id", "replacement_record_id", "reason"),
}

MEMORY_KNOWLEDGE_ACTIONS: tuple[str, ...] = (
    "create_node",
    "update_node",
    "delete_node",
    "attach_record",
    "detach_record",
    "add_record",
)

MEMORY_WORK_ACTIONS: tuple[str, ...] = (
    "plan",
    "start",
    "get",
    "add_todo",
    "set_next_action",
    "block",
    "resume",
    "complete",
    "cancel",
    "set_links",
)

_MEMORY_READ_HANDLERS = {name: f"ue_memory_{name}" for name in MEMORY_READ_ACTIONS}


def _memory_read_call(raw: dict[str, Any], action: str) -> tuple[str, dict[str, Any]]:
    if action not in MEMORY_READ_ACTIONS:
        raise ValueError(
            f"ue_memory: unknown action {action!r}; expected one of "
            f"{sorted(MEMORY_READ_ACTIONS)}."
        )
    for required in MEMORY_READ_REQUIRED.get(action, ()):
        if raw.get(required) is None:
            raise ValueError(f"ue_memory(action={action!r}) needs {required}.")
    params = {
        name: raw[name]
        for name in MEMORY_READ_ACTIONS[action]
        if raw.get(name) is not None
    }
    return _MEMORY_READ_HANDLERS[action], params


def _memory_write_call(
    raw: dict[str, Any], action: str
) -> tuple[str, dict[str, Any]]:
    if action in MEMORY_KNOWLEDGE_ACTIONS:
        return "ue_memory_update_knowledge", {"action": action, "payload": raw.get("payload") or {}}
    if action in MEMORY_WORK_ACTIONS:
        return "ue_memory_update_work", {"action": action, "payload": raw.get("payload") or {}}
    if action not in MEMORY_WRITE_ACTIONS:
        raise ValueError(
            f"ue_memory_write: unknown action {action!r}; expected one of "
            f"{sorted(MEMORY_WRITE_ACTIONS) + list(MEMORY_KNOWLEDGE_ACTIONS) + list(MEMORY_WORK_ACTIONS)}."
        )
    for required in MEMORY_WRITE_REQUIRED.get(action, ()):
        if raw.get(required) is None:
            raise ValueError(f"ue_memory_write(action={action!r}) needs {required}.")
    handler, accepted = MEMORY_WRITE_ACTIONS[action]
    params = {name: raw[name] for name in accepted if raw.get(name) is not None}
    return handler, params


def register_memory_read_tool(
    *,
    server: Any,
    handlers: dict[str, Any],
    read_annotations: Any,
    error_response: Any,
) -> None:
    """Register ``ue_memory`` in front of the seven read-only memory tools."""

    @server.tool(annotations=read_annotations)
    def ue_memory(
        action: str,
        query: str | None = None,
        record_id: str | None = None,
        event_id: str | None = None,
        path: str | None = None,
        node_path: str | None = None,
        record_types: list[str] | None = None,
        statuses: list[str] | None = None,
        scope_type: str | None = None,
        scope_key: str | None = None,
        event_kinds: list[str] | None = None,
        change_set_id: str | None = None,
        distilled: bool | None = None,
        asset_paths: list[str] | None = None,
        detail_level: int | None = None,
        depth: int | None = None,
        budget_chars: int | None = None,
        max_nodes: int | None = None,
        max_records: int | None = None,
        max_depth: int | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """One read-only Project Memory entry point: search, get, list_l0_events, get_l0_event, get_context, expand_node, get_evidence."""

        try:
            handler_name, params = _memory_read_call(locals(), action)
            return handlers[handler_name](**params)
        except (TypeError, ValueError, RuntimeError) as exc:
            return error_response("ue_memory", exc, read_only=True)


def register_memory_write_tool(
    *,
    server: Any,
    handlers: dict[str, Any],
    planning_annotations: Any,
    error_response: Any,
) -> None:
    """Register ``ue_memory_write`` in front of the seven write-side memory tools."""

    @server.tool(annotations=planning_annotations)
    def ue_memory_write(
        action: str,
        payload: dict[str, Any] | None = None,
        subject_key: str | None = None,
        title: str | None = None,
        body: str | None = None,
        record_type: str | None = None,
        source_kind: str | None = None,
        source_ref: str | None = None,
        confidence: float | None = None,
        observed_at_utc: str | None = None,
        scopes: list[dict[str, Any]] | None = None,
        revision_set: list[dict[str, Any]] | None = None,
        artifacts: list[dict[str, Any]] | None = None,
        details: dict[str, Any] | None = None,
        task_key: str | None = None,
        conclusion: str | None = None,
        outcome: str | None = None,
        patch_ref: str | None = None,
        backup_manifest_ref: str | None = None,
        validation_evidence_ref: str | None = None,
        patch_details: dict[str, Any] | None = None,
        backup_manifest_details: dict[str, Any] | None = None,
        validation_evidence_details: dict[str, Any] | None = None,
        record_id: str | None = None,
        replacement_record_id: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """One Project Memory write entry point: add_rule, record_finding, record_task, mark_superseded, validate, and the knowledge/work sub-actions."""

        try:
            handler_name, params = _memory_write_call(locals(), action)
            return handlers[handler_name](**params)
        except (TypeError, ValueError, RuntimeError) as exc:
            return error_response("ue_memory_write", exc, read_only=False)


SCALE_FIX_ACTIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "plan": (
        "ue_plan_animation_scale_fix",
        (
            "asset_path",
            "root_bone",
            "expected_final_scale",
            "force_root_lock",
            "enable_root_motion",
            "use_normalized_root_motion_scale",
            "root_motion_root_lock",
            "root_track_scale_mode",
            "uniform_scale",
            "final_scale_tolerance",
            "description",
        ),
    ),
    "plan_batch": (
        "ue_plan_animation_scale_fix_batch",
        (
            "audit_task_id",
            "audit_report_id",
            "asset_paths",
            "expected_final_scale_overrides",
            "final_scale_tolerance",
            "description",
        ),
    ),
    "get": ("ue_get_animation_scale_fix_batch", ("batch_plan_id",)),
    "apply": (
        "ue_apply_animation_scale_fix_batch_live",
        ("batch_plan_id", "confirmation", "batch_apply_receipt", "max_assets"),
    ),
    "save": (
        "ue_save_animation_scale_fix_batch",
        ("batch_plan_id", "confirmation", "batch_save_receipt", "max_assets"),
    ),
    "verify": (
        "ue_verify_animation_scale_fix_batch",
        ("batch_plan_id", "batch_verify_receipt", "max_assets"),
    ),
    "refresh_index": (
        "ue_refresh_animation_scale_fix_batch_index",
        (
            "batch_plan_id",
            "mode",
            "confirmation",
            "batch_index_refresh_receipt",
            "max_assets",
        ),
    ),
    "rollback": (
        "ue_rollback_animation_scale_fix_batch",
        ("batch_plan_id", "mode", "confirmation", "batch_rollback_receipt", "max_assets"),
    ),
    "undo": (
        "ue_undo_animation_scale_fix_batch",
        ("batch_plan_id", "confirmation", "batch_undo_receipt", "max_assets"),
    ),
}

SCALE_FIX_REQUIRED: dict[str, tuple[str, ...]] = {
    "plan": ("asset_path", "root_bone", "expected_final_scale"),
    "plan_batch": ("audit_task_id", "audit_report_id", "asset_paths"),
    "get": ("batch_plan_id",),
    "apply": ("batch_plan_id",),
    "save": ("batch_plan_id",),
    "verify": ("batch_plan_id",),
    "refresh_index": ("batch_plan_id",),
    "rollback": ("batch_plan_id",),
    "undo": ("batch_plan_id",),
}

SCALE_FIX_ALIASES = {
    "refresh": "refresh_index",
    "refresh_index_batch": "refresh_index",
    "plan_batch_audit": "plan_batch",
}


def _scale_fix_call(
    raw: dict[str, Any], action: str
) -> tuple[str, dict[str, Any]]:
    resolved = SCALE_FIX_ALIASES.get(action, action)
    if resolved not in SCALE_FIX_ACTIONS:
        raise ValueError(
            f"ue_animation_scale_fix: unknown action {action!r}; expected one of "
            f"{sorted(SCALE_FIX_ACTIONS)}."
        )
    for required in SCALE_FIX_REQUIRED.get(resolved, ()):
        if raw.get(required) is None:
            raise ValueError(
                f"ue_animation_scale_fix(action={resolved!r}) needs {required}."
            )
    handler, accepted = SCALE_FIX_ACTIONS[resolved]
    params = {name: raw[name] for name in accepted if raw.get(name) is not None}
    return handler, params


def register_animation_scale_fix_tool(
    *,
    server: Any,
    handlers: dict[str, Any],
    annotations: Any,
    error_response: Any,
) -> None:
    """Register ``ue_animation_scale_fix`` in front of the nine scale-fix tools.

    The caller passes the destructive annotations: the family can apply, save,
    roll back and undo, so a plain planning hint would understate it.
    """

    @server.tool(annotations=annotations)
    def ue_animation_scale_fix(
        action: str,
        asset_path: str | None = None,
        root_bone: str | None = None,
        expected_final_scale: float | None = None,
        force_root_lock: bool | None = None,
        enable_root_motion: bool | None = None,
        use_normalized_root_motion_scale: bool | None = None,
        root_motion_root_lock: str | None = None,
        root_track_scale_mode: str | None = None,
        uniform_scale: float | None = None,
        final_scale_tolerance: float | None = None,
        description: str | None = None,
        audit_task_id: str | None = None,
        audit_report_id: str | None = None,
        asset_paths: list[str] | None = None,
        expected_final_scale_overrides: dict[str, float] | None = None,
        batch_plan_id: str | None = None,
        confirmation: str | None = None,
        max_assets: int | None = None,
        batch_apply_receipt: str | None = None,
        batch_save_receipt: str | None = None,
        batch_verify_receipt: str | None = None,
        batch_index_refresh_receipt: str | None = None,
        batch_rollback_receipt: str | None = None,
        batch_undo_receipt: str | None = None,
        mode: str | None = None,
    ) -> dict[str, Any]:
        """One animation scale-fix entry point: plan, plan_batch, get, apply, save, verify, refresh_index, rollback, undo."""

        try:
            handler_name, params = _scale_fix_call(locals(), action)
            return handlers[handler_name](**params)
        except (TypeError, ValueError, RuntimeError) as exc:
            return error_response("ue_animation_scale_fix", exc, read_only=False)


ASSET_PROPERTY_ACTIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "set": (
        "ue_set_asset_property",
        ("asset_path", "property_path", "value", "mode", "description"),
    ),
    "set_reference": (
        "ue_set_asset_reference_property",
        ("asset_path", "property_path", "value", "mode", "description"),
    ),
    "set_structured": (
        "ue_set_asset_structured_property",
        ("asset_path", "property_path", "value", "mode", "description"),
    ),
    "apply": ("ue_apply_asset_property_live", ("plan_id", "confirmation", "change_set_id")),
    "undo": (
        "ue_undo_asset_property_live",
        ("asset_path", "transaction_id", "editor_session_id", "change_set_id"),
    ),
    "discard": (
        "ue_discard_asset_property_live",
        ("asset_path", "transaction_id", "editor_session_id", "change_set_id"),
    ),
}

ASSET_PROPERTY_REQUIRED: dict[str, tuple[str, ...]] = {
    "set": ("asset_path", "property_path", "value"),
    "set_reference": ("asset_path", "property_path"),
    "set_structured": ("asset_path", "property_path", "value"),
    "apply": ("plan_id", "confirmation"),
    "undo": ("asset_path", "transaction_id", "editor_session_id"),
    "discard": ("asset_path", "transaction_id", "editor_session_id"),
}


def _asset_property_call(
    raw: dict[str, Any], action: str
) -> tuple[str, dict[str, Any]]:
    if action not in ASSET_PROPERTY_ACTIONS:
        raise ValueError(
            f"ue_asset_property: unknown action {action!r}; expected one of "
            f"{sorted(ASSET_PROPERTY_ACTIONS)}."
        )
    for required in ASSET_PROPERTY_REQUIRED.get(action, ()):
        if raw.get(required) is None:
            raise ValueError(f"ue_asset_property(action={action!r}) needs {required}.")
    handler, accepted = ASSET_PROPERTY_ACTIONS[action]
    params = {
        name: raw[name]
        for name in accepted
        if raw.get(name) is not None or (name == "value" and action == "set_reference")
    }
    return handler, params


def register_asset_property_tool(
    *,
    server: Any,
    handlers: dict[str, Any],
    annotations: Any,
    error_response: Any,
) -> None:
    """Register ``ue_asset_property`` in front of the six asset-property tools.

    ``set_reference`` keeps an explicit ``value=None`` meaningful: it clears the
    reference, so the facade forwards it instead of treating it as omitted.
    """

    @server.tool(annotations=annotations)
    def ue_asset_property(
        action: str,
        asset_path: str | None = None,
        property_path: str | None = None,
        value: Any = None,
        mode: str = "Plan",
        description: str | None = None,
        plan_id: str | None = None,
        confirmation: str | None = None,
        change_set_id: str | None = None,
        transaction_id: str | None = None,
        editor_session_id: str | None = None,
    ) -> dict[str, Any]:
        """One asset-property entry point: set, set_reference, set_structured (Plan/DryRun), apply, undo, discard."""

        try:
            handler_name, params = _asset_property_call(locals(), action)
            return handlers[handler_name](**params)
        except (TypeError, ValueError, RuntimeError) as exc:
            return error_response("ue_asset_property", exc, read_only=False)


RETARGET_ACTIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "plan": (
        "ue_plan_animation_retarget",
        ("sourceMesh", "targetMesh", "includeOptionalChains", "outputDirectory"),
    ),
    "apply_setup": (
        "ue_apply_animation_retarget_setup",
        ("planId", "confirmation", "changeSetId", "updateExisting", "allowLargePoseOffset"),
    ),
    "start_batch": (
        "ue_start_animation_retarget_batch",
        (
            "planId",
            "retargeter",
            "sourceAssets",
            "outputDirectory",
            "naming",
            "overwriteExisting",
            "includeReferencedAssets",
            "exportOnlyAnimatedBones",
            "retainAdditiveFlags",
        ),
    ),
    "get_batch": ("ue_get_animation_retarget_batch", ("taskId",)),
    "cancel_batch": ("ue_cancel_animation_retarget_batch", ("taskId",)),
    "save_batch": (
        "ue_save_animation_retarget_batch",
        ("taskId", "confirmation", "changeSetId"),
    ),
    "verify_batch": ("ue_verify_animation_retarget_batch", ("taskId",)),
    "rollback_batch": (
        "ue_rollback_animation_retarget_batch",
        ("taskId", "mode", "rollbackDryRunReceipt", "confirmation"),
    ),
    "start_postprocess": (
        "ue_start_animation_retarget_postprocess",
        ("retargetTaskId", "loadIfNeeded", "batchSize"),
    ),
    "get_postprocess": ("ue_get_animation_retarget_postprocess", ("postprocessId",)),
    "plan_postprocess": (
        "ue_plan_animation_retarget_postprocess",
        ("postprocessId", "description"),
    ),
    "reopen_postprocess": (
        "ue_reopen_animation_retarget_postprocess",
        ("planRelativePath",),
    ),
    "refresh_postprocess_index": (
        "ue_refresh_animation_retarget_postprocess_index",
        ("postprocessId", "mode", "confirmation", "refreshReceipt", "maxAssets"),
    ),
    "validate": ("ue_validate_animation_retarget", ("retargeter", "animationPaths")),
}

RETARGET_REQUIRED: dict[str, tuple[str, ...]] = {
    "plan": ("sourceMesh", "targetMesh"),
    "apply_setup": ("planId", "confirmation"),
    "start_batch": ("planId", "retargeter", "sourceAssets"),
    "get_batch": ("taskId",),
    "cancel_batch": ("taskId",),
    "save_batch": ("taskId", "confirmation"),
    "verify_batch": ("taskId",),
    "rollback_batch": ("taskId",),
    "start_postprocess": ("retargetTaskId",),
    "get_postprocess": ("postprocessId",),
    "plan_postprocess": ("postprocessId",),
    "reopen_postprocess": ("planRelativePath",),
    "refresh_postprocess_index": ("postprocessId",),
    "validate": ("retargeter", "animationPaths"),
}


def _retarget_call(raw: dict[str, Any], action: str) -> tuple[str, dict[str, Any]]:
    if action not in RETARGET_ACTIONS:
        raise ValueError(
            f"ue_animation_retarget: unknown action {action!r}; expected one of "
            f"{sorted(RETARGET_ACTIONS)}."
        )
    for required in RETARGET_REQUIRED.get(action, ()):
        if raw.get(required) is None:
            raise ValueError(f"ue_animation_retarget(action={action!r}) needs {required}.")
    handler, accepted = RETARGET_ACTIONS[action]
    params = {name: raw[name] for name in accepted if raw.get(name) is not None}
    return handler, params


def register_animation_retarget_tool(
    *,
    server: Any,
    handlers: dict[str, Any],
    annotations: Any,
    error_response: Any,
) -> None:
    """Register ``ue_animation_retarget`` in front of the fourteen retarget tools.

    The caller passes the destructive annotations: the family can apply a setup,
    run batches, save and roll back.
    """

    @server.tool(annotations=annotations)
    def ue_animation_retarget(
        action: str,
        sourceMesh: str | None = None,
        targetMesh: str | None = None,
        includeOptionalChains: bool | None = None,
        outputDirectory: str | None = None,
        planId: str | None = None,
        confirmation: str | None = None,
        changeSetId: str | None = None,
        updateExisting: bool | None = None,
        allowLargePoseOffset: bool | None = None,
        retargeter: str | None = None,
        sourceAssets: list[str] | None = None,
        naming: dict[str, Any] | None = None,
        overwriteExisting: bool | None = None,
        includeReferencedAssets: bool | None = None,
        exportOnlyAnimatedBones: bool | None = None,
        retainAdditiveFlags: bool | None = None,
        taskId: str | None = None,
        mode: str | None = None,
        rollbackDryRunReceipt: str | None = None,
        retargetTaskId: str | None = None,
        loadIfNeeded: bool | None = None,
        batchSize: int | None = None,
        postprocessId: str | None = None,
        description: str | None = None,
        planRelativePath: str | None = None,
        refreshReceipt: str | None = None,
        maxAssets: int | None = None,
        animationPaths: list[str] | None = None,
    ) -> dict[str, Any]:
        """One animation retarget entry point: plan, apply_setup, start/get/cancel/save/verify/rollback batch, and the postprocess actions."""

        try:
            handler_name, params = _retarget_call(locals(), action)
            return handlers[handler_name](**params)
        except (TypeError, ValueError, RuntimeError) as exc:
            return error_response("ue_animation_retarget", exc, read_only=False)


LIVE_WRITE_ACTIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "plan_batch": ("ue_plan_live_write_batch", ("assets", "description")),
    "apply_batch": (
        "ue_apply_live_write_batch",
        ("batch_plan_id", "confirmation", "change_set_id"),
    ),
    "recover_batch": (
        "ue_recover_live_write_batch",
        ("batch_execution_id", "mode", "confirmation"),
    ),
    "verify": ("ue_verify_live_write", ("asset_path", "live_apply_receipt", "change_set_id")),
    "verify_fast": (
        "ue_verify_live_write_fast",
        ("asset_path", "live_apply_receipt", "change_set_id"),
    ),
    "verify_checkpoint": (
        "ue_verify_live_write_checkpoint",
        ("checkpoint_id", "change_set_id", "asset_path"),
    ),
}

LIVE_WRITE_REQUIRED: dict[str, tuple[str, ...]] = {
    "plan_batch": ("assets",),
    "apply_batch": ("batch_plan_id", "confirmation", "change_set_id"),
    "recover_batch": ("batch_execution_id",),
    "verify": ("asset_path",),
    "verify_fast": ("asset_path",),
    "verify_checkpoint": ("checkpoint_id",),
}


def _live_write_call(raw: dict[str, Any], action: str) -> tuple[str, dict[str, Any]]:
    if action not in LIVE_WRITE_ACTIONS:
        raise ValueError(
            f"ue_live_write: unknown action {action!r}; expected one of "
            f"{sorted(LIVE_WRITE_ACTIONS)}."
        )
    for required in LIVE_WRITE_REQUIRED.get(action, ()):
        if raw.get(required) is None:
            raise ValueError(f"ue_live_write(action={action!r}) needs {required}.")
    handler, accepted = LIVE_WRITE_ACTIONS[action]
    params = {name: raw[name] for name in accepted if raw.get(name) is not None}
    return handler, params


def register_live_write_tool(
    *,
    server: Any,
    handlers: dict[str, Any],
    annotations: Any,
    error_response: Any,
) -> None:
    """Register ``ue_live_write`` in front of the six live-write tools.

    The caller passes the destructive annotations: the family can apply a batch,
    save, recover and roll back.
    """

    @server.tool(annotations=annotations)
    def ue_live_write(
        action: str,
        assets: list[dict[str, Any]] | None = None,
        description: str | None = None,
        batch_plan_id: str | None = None,
        confirmation: str | None = None,
        change_set_id: str | None = None,
        batch_execution_id: str | None = None,
        mode: str | None = None,
        asset_path: str | None = None,
        live_apply_receipt: str | None = None,
        checkpoint_id: str | None = None,
    ) -> dict[str, Any]:
        """One live-write entry point: plan_batch, apply_batch, recover_batch, verify, verify_fast, verify_checkpoint."""

        try:
            handler_name, params = _live_write_call(locals(), action)
            return handlers[handler_name](**params)
        except (TypeError, ValueError, RuntimeError) as exc:
            return error_response("ue_live_write", exc, read_only=False)


CHANGE_SET_ACTIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "create": ("ue_create_change_set", ("title", "task_id")),
    "get": ("ue_get_change_set", ("change_set_id",)),
    "save_checkpoint": (
        "ue_save_change_set_checkpoint",
        ("batch_execution_id", "mode", "confirmation"),
    ),
    "verify_checkpoint": (
        "ue_verify_change_set_checkpoint",
        ("checkpoint_set_id",),
    ),
}

CHANGE_SET_REQUIRED: dict[str, tuple[str, ...]] = {
    "create": (),
    "get": ("change_set_id",),
    "save_checkpoint": ("batch_execution_id", "mode"),
    "verify_checkpoint": ("checkpoint_set_id",),
}


def _change_set_call(raw: dict[str, Any], action: str) -> tuple[str, dict[str, Any]]:
    if action not in CHANGE_SET_ACTIONS:
        raise ValueError(
            f"ue_change_set: unknown action {action!r}; expected one of "
            f"{sorted(CHANGE_SET_ACTIONS)}."
        )
    for required in CHANGE_SET_REQUIRED.get(action, ()):
        if raw.get(required) is None:
            raise ValueError(f"ue_change_set(action={action!r}) needs {required}.")
    handler, accepted = CHANGE_SET_ACTIONS[action]
    params = {name: raw[name] for name in accepted if raw.get(name) is not None}
    return handler, params


def register_change_set_tool(
    *,
    server: Any,
    handlers: dict[str, Any],
    planning_annotations: Any,
    error_response: Any,
) -> None:
    """Register ``ue_change_set`` in front of create/get and the checkpoint pair.

    The checkpoint handlers come from the workflow registrar through the shared
    handler sink, so the facade can reach both modules' implementations.
    """

    @server.tool(annotations=planning_annotations)
    def ue_change_set(
        action: str,
        title: str | None = None,
        task_id: str | None = None,
        change_set_id: str | None = None,
        batch_execution_id: str | None = None,
        mode: str | None = None,
        confirmation: str | None = None,
        checkpoint_set_id: str | None = None,
    ) -> dict[str, Any]:
        """One Change Set entry point: create, get, save_checkpoint, verify_checkpoint."""

        try:
            handler_name, params = _change_set_call(locals(), action)
            return handlers[handler_name](**params)
        except (TypeError, ValueError, RuntimeError) as exc:
            return error_response("ue_change_set", exc, read_only=False)


PATCH_ACTIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "plan": (
        "ue_plan_patch",
        ("asset_path", "operation", "target", "value", "description"),
    ),
    "dry_run": ("ue_dry_run_patch", ("plan_id",)),
    "apply": (
        "ue_apply_patch",
        ("plan_id", "dry_run_receipt", "confirmation", "change_set_id"),
    ),
    "verify": ("ue_verify_asset", ("apply_receipt", "change_set_id")),
    "rollback": (
        "ue_rollback_patch",
        ("apply_receipt", "mode", "rollback_dry_run_receipt", "confirmation"),
    ),
    "state": ("ue_get_asset_state", ("asset_path",)),
    "refresh_index": ("ue_refresh_asset_index", ("asset_path", "mode")),
}

PATCH_REQUIRED: dict[str, tuple[str, ...]] = {
    "plan": ("asset_path", "operation"),
    "dry_run": ("plan_id",),
    "apply": ("plan_id", "dry_run_receipt", "confirmation"),
    "verify": ("apply_receipt",),
    "rollback": ("apply_receipt",),
    "state": ("asset_path",),
    "refresh_index": ("asset_path",),
}


def _patch_call(raw: dict[str, Any], action: str) -> tuple[str, dict[str, Any]]:
    if action not in PATCH_ACTIONS:
        raise ValueError(
            f"ue_patch: unknown action {action!r}; expected one of "
            f"{sorted(PATCH_ACTIONS)}."
        )
    for required in PATCH_REQUIRED.get(action, ()):
        if raw.get(required) is None:
            raise ValueError(f"ue_patch(action={action!r}) needs {required}.")
    handler, accepted = PATCH_ACTIONS[action]
    params = {name: raw[name] for name in accepted if raw.get(name) is not None}
    return handler, params


def register_patch_tool(
    *,
    server: Any,
    handlers: dict[str, Any],
    annotations: Any,
    error_response: Any,
) -> None:
    """Register ``ue_patch`` in front of plan/dry_run/apply/verify/rollback plus state helpers.

    The caller passes the destructive annotations: apply, rollback and index
    refresh can change disk state.
    """

    @server.tool(annotations=annotations)
    def ue_patch(
        action: str,
        asset_path: str | None = None,
        operation: str | None = None,
        target: dict[str, Any] | None = None,
        value: Any = None,
        description: str | None = None,
        plan_id: str | None = None,
        dry_run_receipt: str | None = None,
        confirmation: str | None = None,
        change_set_id: str | None = None,
        apply_receipt: str | None = None,
        mode: str | None = None,
        rollback_dry_run_receipt: str | None = None,
    ) -> dict[str, Any]:
        """One patch entry point: plan, dry_run, apply, verify, rollback, state, refresh_index."""

        try:
            handler_name, params = _patch_call(locals(), action)
            return handlers[handler_name](**params)
        except (TypeError, ValueError, RuntimeError) as exc:
            return error_response("ue_patch", exc, read_only=False)


SET_PROPERTY_ACTIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "blueprint_default": (
        "ue_set_blueprint_default",
        ("asset_path", "variable_name", "value", "mode", "description"),
    ),
    "component_property": (
        "ue_set_component_property",
        ("asset_path", "component_name", "property_path", "value", "mode", "description"),
    ),
    "pin_default": (
        "ue_set_pin_default",
        ("asset_path", "graph_guid", "node_guid", "pin_name", "value", "mode", "description"),
    ),
    "material_parameter": (
        "ue_set_material_parameter",
        ("asset_path", "parameter_name", "parameter_type", "value", "mode", "description"),
    ),
}

SET_PROPERTY_REQUIRED: dict[str, tuple[str, ...]] = {
    "blueprint_default": ("asset_path", "variable_name", "value"),
    "component_property": ("asset_path", "component_name", "property_path", "value"),
    "pin_default": ("asset_path", "graph_guid", "node_guid", "pin_name", "value"),
    "material_parameter": ("asset_path", "parameter_name", "parameter_type", "value"),
}

BLUEPRINT_EDIT_ACTIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "move_node": (
        "ue_move_blueprint_node",
        (
            "asset_path",
            "graph_guid",
            "node_guid",
            "x",
            "y",
            "expected_x",
            "expected_y",
            "expected_dirty",
        ),
    ),
    "move_nodes": (
        "ue_move_blueprint_nodes",
        ("asset_path", "graph_guid", "nodes", "edit_session_token"),
    ),
    "begin_session": (
        "ue_begin_blueprint_edit_session",
        ("asset_path", "graph_guid", "experimental"),
    ),
    "end_session": (
        "ue_end_blueprint_edit_session",
        ("edit_session_token", "mode"),
    ),
    "save": (
        "ue_save_authorized_asset",
        ("asset_path", "mode", "save_receipt", "confirmation", "change_set_id", "verification_mode"),
    ),
}

BLUEPRINT_EDIT_REQUIRED: dict[str, tuple[str, ...]] = {
    "move_node": ("asset_path", "graph_guid", "node_guid", "x", "y", "expected_x", "expected_y", "expected_dirty"),
    "move_nodes": ("asset_path", "graph_guid", "nodes"),
    "begin_session": ("asset_path", "graph_guid"),
    "end_session": ("edit_session_token",),
    "save": ("asset_path",),
}


def _dispatch_with_required(
    raw: dict[str, Any],
    action: str,
    actions: dict[str, tuple[str, tuple[str, ...]]],
    required: dict[str, tuple[str, ...]],
    tool: str,
) -> tuple[str, dict[str, Any]]:
    if action not in actions:
        raise ValueError(
            f"{tool}: unknown action {action!r}; expected one of {sorted(actions)}."
        )
    for name in required.get(action, ()):
        if raw.get(name) is None:
            raise ValueError(f"{tool}(action={action!r}) needs {name}.")
    handler, accepted = actions[action]
    params = {name: raw[name] for name in accepted if raw.get(name) is not None}
    return handler, params


def register_set_property_tool(
    *,
    server: Any,
    handlers: dict[str, Any],
    planning_annotations: Any,
    error_response: Any,
) -> None:
    """Register ``ue_set_property`` over the four policy-gated property writers."""

    @server.tool(annotations=planning_annotations)
    def ue_set_property(
        action: str,
        asset_path: str | None = None,
        variable_name: str | None = None,
        value: Any = None,
        mode: str = "Plan",
        description: str | None = None,
        component_name: str | None = None,
        property_path: str | None = None,
        graph_guid: str | None = None,
        node_guid: str | None = None,
        pin_name: str | None = None,
        parameter_name: str | None = None,
        parameter_type: str | None = None,
    ) -> dict[str, Any]:
        """One property entry point (Plan/DryRun): blueprint_default, component_property, pin_default, material_parameter."""

        try:
            handler_name, params = _dispatch_with_required(
                locals(), action, SET_PROPERTY_ACTIONS, SET_PROPERTY_REQUIRED, "ue_set_property"
            )
            return handlers[handler_name](**params)
        except (TypeError, ValueError, RuntimeError) as exc:
            return error_response("ue_set_property", exc, read_only=False)


def register_blueprint_edit_tool(
    *,
    server: Any,
    handlers: dict[str, Any],
    annotations: Any,
    error_response: Any,
) -> None:
    """Register ``ue_blueprint_edit`` over move, session and authorized save."""

    @server.tool(annotations=annotations)
    def ue_blueprint_edit(
        action: str,
        asset_path: str | None = None,
        graph_guid: str | None = None,
        node_guid: str | None = None,
        x: int | None = None,
        y: int | None = None,
        expected_x: int | None = None,
        expected_y: int | None = None,
        expected_dirty: bool | None = None,
        nodes: list[dict[str, Any]] | None = None,
        edit_session_token: str | None = None,
        experimental: bool | None = None,
        mode: str | None = None,
        save_receipt: str | None = None,
        confirmation: str | None = None,
        change_set_id: str | None = None,
        verification_mode: str | None = None,
    ) -> dict[str, Any]:
        """One Blueprint edit entry point: move_node, move_nodes, begin_session, end_session, save."""

        try:
            handler_name, params = _dispatch_with_required(
                locals(), action, BLUEPRINT_EDIT_ACTIONS, BLUEPRINT_EDIT_REQUIRED, "ue_blueprint_edit"
            )
            return handlers[handler_name](**params)
        except (TypeError, ValueError, RuntimeError) as exc:
            return error_response("ue_blueprint_edit", exc, read_only=False)


__all__ = [
    "ASSET_PROPERTY_ACTIONS",
    "ASSET_PROPERTY_REQUIRED",
    "CHANGE_SET_ACTIONS",
    "CHANGE_SET_REQUIRED",
    "BLUEPRINT_EDIT_ACTIONS",
    "BLUEPRINT_EDIT_REQUIRED",
    "DATATABLE_ACTIONS",
    "LIVE_WRITE_ACTIONS",
    "LIVE_WRITE_REQUIRED",
    "MEMORY_KNOWLEDGE_ACTIONS",
    "MEMORY_READ_ACTIONS",
    "MEMORY_READ_REQUIRED",
    "MEMORY_WORK_ACTIONS",
    "MEMORY_WRITE_ACTIONS",
    "MEMORY_WRITE_REQUIRED",
    "PATCH_ACTIONS",
    "PATCH_REQUIRED",
    "PIE_ACTIONS",
    "RETARGET_ACTIONS",
    "RETARGET_REQUIRED",
    "SCALE_FIX_ACTIONS",
    "SET_PROPERTY_ACTIONS",
    "SET_PROPERTY_REQUIRED",
    "SCALE_FIX_REQUIRED",
    "datatable_change",
    "pie_inspect_actor_params",
    "pie_list_actors_params",
    "pie_list_object_properties_params",
    "pie_read_object_properties_params",
    "pie_scalars_params",
    "pie_state_params",
    "pie_world_contexts_params",
    "register_animation_retarget_tool",
    "register_animation_scale_fix_tool",
    "register_asset_property_tool",
    "register_blueprint_edit_tool",
    "register_change_set_tool",
    "register_patch_tool",
    "register_set_property_tool",
    "register_consolidated_tools",
    "register_datatable_tool",
    "register_live_write_tool",
    "register_memory_read_tool",
    "register_memory_write_tool",
]
