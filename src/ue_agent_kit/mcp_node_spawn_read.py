"""Read-only Action Registry search for node creation (node-spawn phase A).

This module is a THIN wrapper: it validates arguments, calls the existing
``native.invoke`` bridge method with ``toolId=graph.nodeRegistry.catalog`` and
normalizes the descriptor list.  It adds no registry, no query language and no
write path of its own -- the catalog already enumerates the engine's action
database in bounded pages, and its server-side ``query`` narrows the set before
the 512-descriptor truncation.

Each returned action carries the identity a write needs (``actionId``), the
descriptor's own admission fields, and a derived ``canCreate`` /
``rejectReason`` pair so a caller can tell *before* submitting a patch why an
action will be refused.  The write path re-derives all of it in C++ from the
same descriptor; this file never decides what may be written.

Read-only: nothing here opens a transaction, modifies an asset or saves.
"""

from __future__ import annotations

from typing import Any

from .editor_bridge import LiveEditorError

NODE_ACTION_SEARCH_TOOL_NAME = "ue_search_node_actions"
NATIVE_INVOKE_METHOD = "native.invoke"
CATALOG_TOOL_ID = "graph.nodeRegistry.catalog"
CATALOG_TOOL_VERSION = "1.0.0"

MAX_LIMIT = 64
MAX_QUERY_LENGTH = 256
MAX_NODE_CLASS_LENGTH = 256
MAX_RESPONSE_BYTES = 512 * 1024

# Action kinds phase A can create.  This mirrors the C++ admission rule; it is
# the same descriptor field, not a second node-type whitelist.
CREATABLE_ACTION_KINDS = ("callFunction", "cast")

# Spawner families that need a binding or an editor selection this surface does
# not carry.  Refusing them is "this spawner needs context I do not have", not
# "this node type is banned".
BINDING_SPAWNER_CLASSES = (
    "/Script/BlueprintGraph.BlueprintBoundEventNodeSpawner",
    "/Script/BlueprintGraph.BlueprintComponentNodeSpawner",
    "/Script/BlueprintGraph.BlueprintDelegateNodeSpawner",
)

_CONSTRAINT_FIELDS = (
    ("pure", "pure"),
    ("latent", "latent"),
    ("variadic", "variadic"),
    ("requiresTarget", "requiresTarget"),
    ("requiresWorldContext", "requiresWorldContext"),
)

_PIN_FIELDS = ("name", "direction", "kind", "type")


def _shape_pin(pin: Any) -> dict[str, Any] | None:
    if not isinstance(pin, dict):
        return None
    shaped: dict[str, Any] = {}
    for field in _PIN_FIELDS:
        value = pin.get(field)
        if field == "type":
            if not isinstance(value, dict):
                return None
            shaped["type"] = {
                "category": str(value.get("category") or ""),
                "subCategory": str(value.get("subCategory") or ""),
                "subCategoryObject": str(value.get("subCategoryObject") or ""),
                "container": str(value.get("container") or ""),
            }
            continue
        if not isinstance(value, str):
            return None
        shaped[field] = value
    return shaped


def _reject_reason(action_kind: str, constraints: dict[str, Any], spawner_class: str) -> str | None:
    """The first field that makes an action non-creatable, or None.

    The order is the one the C++ admission uses: it decides which error code a
    refusal carries, so the search result and the write refusal agree.
    """
    for field in ("latent", "requiresWorldContext", "variadic"):
        if constraints.get(field) is True:
            return field
    if spawner_class in BINDING_SPAWNER_CLASSES:
        return "requires-binding"
    if action_kind not in CREATABLE_ACTION_KINDS:
        return f"action-kind:{action_kind or 'unknown'}"
    return None


def _shape_action(descriptor: Any) -> dict[str, Any] | None:
    if not isinstance(descriptor, dict):
        return None
    action_id = descriptor.get("actionId")
    if not isinstance(action_id, str) or not action_id.startswith("action:"):
        return None
    discovery_key = descriptor.get("discoveryKey")
    if not isinstance(discovery_key, dict):
        return None
    payload = discovery_key.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    action_kind = str(discovery_key.get("actionKind") or "")
    spawner_class = str(discovery_key.get("spawnerClass") or "")
    constraints_raw = descriptor.get("constraints")
    constraints_missing = not isinstance(constraints_raw, dict)
    constraints = (
        {field: bool(constraints_raw.get(field)) for field, _ in _CONSTRAINT_FIELDS}
        if isinstance(constraints_raw, dict)
        else {field: False for field, _ in _CONSTRAINT_FIELDS}
    )
    pins: list[dict[str, Any]] = []
    raw_pins = descriptor.get("pins")
    if isinstance(raw_pins, list):
        for raw_pin in raw_pins:
            shaped_pin = _shape_pin(raw_pin)
            if shaped_pin is not None:
                pins.append(shaped_pin)
    payload_value = ""
    for value in payload.values():
        if isinstance(value, str):
            payload_value = value
            break
    reject_reason = (
        "constraints-missing" if constraints_missing else _reject_reason(action_kind, constraints, spawner_class)
    )
    return {
        "actionId": action_id,
        "actionKind": action_kind,
        "nodeClass": str(discovery_key.get("nodeClass") or ""),
        "spawnerClass": spawner_class,
        "payload": payload_value,
        "title": str(descriptor.get("title") or ""),
        "menuName": str(descriptor.get("menuName") or ""),
        "category": str(descriptor.get("category") or ""),
        "trustLevel": str(descriptor.get("trustLevel") or ""),
        "descriptorDigest": str(descriptor.get("descriptorDigest") or ""),
        **{field: constraints[field] for field, _ in _CONSTRAINT_FIELDS},
        "pins": pins,
        "canCreate": reject_reason is None,
        "rejectReason": reject_reason,
    }


def _checked_text(value: Any, label: str, limit: int) -> str:
    if not isinstance(value, str):
        raise LiveEditorError(
            "native-schema-invalid-arguments",
            f"{label} must be a string.",
        )
    text = value.strip()
    if len(text) > limit:
        raise LiveEditorError(
            "native-schema-invalid-arguments",
            f"{label} exceeds {limit} characters.",
        )
    if any(ord(character) < 32 for character in text):
        raise LiveEditorError(
            "native-schema-invalid-arguments",
            f"{label} must not contain control characters.",
        )
    return text


def register_node_spawn_read_tools(
    *,
    server: Any,
    live_editor_service: Any,
    read_annotations: Any,
    error_response: Any,
) -> None:
    @server.tool(annotations=read_annotations)
    def ue_search_node_actions(
        query: str = "",
        node_class: str = "",
        limit: int = 20,
    ) -> dict[str, Any]:
        """Search the live Editor's Action Registry for node actions this build can create.

        Returns each action's actionId, class, title/category, registry pins and
        admission fields, plus canCreate/rejectReason. Pass the actionId to the
        Blueprint patch tool's createNode operation to create the node. Read-only;
        it never opens a transaction or writes an asset.

        A filtered search describes only the candidate actions, so a keyword that
        exists only in a cast target class needs node_class set as well (for
        example node_class="/Script/BlueprintGraph.K2Node_DynamicCast" when
        searching "Cast To Pawn").
        """
        try:
            if live_editor_service is None:
                raise LiveEditorError(
                    "live-editor-unavailable",
                    "No live Editor service is configured.",
                )
            if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
                raise LiveEditorError(
                    "native-schema-invalid-arguments",
                    f"limit must be an integer in [1, {MAX_LIMIT}].",
                )
            query_text = _checked_text(query, "query", MAX_QUERY_LENGTH)
            node_class_text = _checked_text(node_class, "node_class", MAX_NODE_CLASS_LENGTH)
            arguments: dict[str, Any] = {
                "maxItems": limit,
                "includeUnsupported": False,
            }
            if query_text:
                arguments["query"] = query_text
            if node_class_text:
                arguments["nodeClass"] = node_class_text
            payload = live_editor_service.call_method(
                NATIVE_INVOKE_METHOD,
                {
                    "toolId": CATALOG_TOOL_ID,
                    "toolVersion": CATALOG_TOOL_VERSION,
                    "arguments": arguments,
                },
            )
            if not isinstance(payload, dict):
                raise LiveEditorError(
                    "live-editor-invalid-response",
                    "The Editor Bridge returned an invalid native tool result.",
                )
            data = payload.get("data")
            if not isinstance(data, dict):
                raise LiveEditorError(
                    "live-editor-invalid-response",
                    "The Action Registry returned no data object.",
                )
            raw_descriptors = data.get("descriptors")
            actions: list[dict[str, Any]] = []
            if isinstance(raw_descriptors, list):
                for descriptor in raw_descriptors:
                    shaped = _shape_action(descriptor)
                    if shaped is not None:
                        actions.append(shaped)
            response: dict[str, Any] = {
                "schemaVersion": "1.0",
                "tool": NODE_ACTION_SEARCH_TOOL_NAME,
                "ok": True,
                "readOnly": True,
                "experimental": True,
                "source": "live-editor-memory",
                "registry": {
                    "mode": str(data.get("mode") or ""),
                    "actionDatabaseFingerprint": str(data.get("actionDatabaseFingerprint") or ""),
                    "totalDescriptorsAvailable": int(data.get("totalDescriptorsAvailable") or 0),
                    "matchedDescriptors": int(data.get("matchedDescriptors") or 0),
                    # A filtered search describes only the candidate slice, not the
                    # whole action database; say so instead of implying coverage.
                    "targeted": bool(data.get("targeted")),
                },
                "filters": {
                    "query": query_text,
                    "nodeClass": node_class_text,
                },
                "count": len(actions),
                "actions": actions,
            }
            return response
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response(NODE_ACTION_SEARCH_TOOL_NAME, exc, read_only=True)


__all__ = [
    "NODE_ACTION_SEARCH_TOOL_NAME",
    "register_node_spawn_read_tools",
]
