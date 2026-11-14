"""Cross-writer atomic Blueprint patch contract (0-3.4).

One submission is one Editor transaction: every operation is validated against
the live graph before anything is written, the operations are applied in request
order inside a single ``FScopedTransaction``, and any failure restores the
operations already applied and reports ``writeOccurred=false`` together with one
result per operation.

This module never talks to Unreal.  It produces and checks the exact payloads the
live module (``BlueprintPatchExperimental.cpp``) consumes, so a caller cannot
build a bridge request of its own.

Writer migration is complete for this seam: ``setPinDefault``, ``createVariable``,
``createNode``, ``connect``, ``disconnect``, ``rewirePin`` and ``removeNode`` are
all migrated, so a request that names any other kind is refused before anything is
written.  The graph-edit owned literals, the variable accessors and the
``call_function`` family are outside this seam and stay single-writer only.

A Blueprint variable is asset-level, not graph-level.  The request still carries a
``graphGuid`` and it still has to name a graph that belongs to that Blueprint and
is the one the focused editor shows -- that GUID is the authorization and
provenance anchor, not a constraint on where the variable lands.  Variable
operations are validated against the Blueprint only.

A ``createNode`` operation creates one node and touches no edge.  It accepts
``nodeType``/``x``/``y`` and an optional ``tempId``; only the eight primitive
nodeTypes of the graph-edit writer are accepted.

A ``connect`` operation creates one edge.  An endpoint is either a canonical node
GUID (an existing node) or a ``tempId`` that an EARLIER ``createNode`` operation
of the same request declared: the tempId is a patch-local label that the Editor
resolves to the node it just created, so one request can create a node and wire
it in the same transaction.  Forward references, unknown labels and duplicate
labels are refused before anything is written.

A ``disconnect`` operation removes one edge and carries exactly the ``connect``
fields; the Editor additionally requires that edge to be the only link on its
target input, so its endpoints must be existing nodes.

A ``rewirePin`` operation moves one input from its current source to a new one and
carries the rewire writer's own nine fields (target / oldSource / newSource node
GUID, pin name and pin GUID).  Both kinds keep ``edgesFingerprint`` meaningful:
they change which pins are connected without necessarily changing how many links
a pin has.

A ``removeNode`` operation deletes one node together with the single incident edge
it is allowed to carry.  It takes the node-removal writer's own three fields
(``nodeGuid``, ``expectedNodeClass``, ``removedEdges``) but not its
``expectedEdgeSetHash``: ``edgesFingerprint`` already binds that same edge set into
the plan, so a per-operation copy would add no proof.  ``removedEdges`` has to
match the live edges one for one, and a rollback puts the same node object back
with its NodeGuid and pin GUIDs intact, then re-creates every edge it took.
"""

from __future__ import annotations

import re
from typing import Any

from .experimental_envelope import unwrap_tool_result as _unwrap_tool_result
from .experimental_graph_edit import (
    MAX_COORDINATE as MAX_NODE_COORDINATE,
    MAX_CONNECTIONS,
    MAX_CONNECTIONS_PER_NODE,
    MAX_NODES as MAX_CREATE_NODES,
    NODE_TYPES,
    VARIABLE_NODE_TYPES,
    GraphEditError,
    node_contract,
    validate_variable_accessor_fields,
)
from .experimental_pin_default_edit import (
    PinDefaultError,
    canonical_value,
    validate_preview_parameters as validate_pin_default_parameters,
)
from .experimental_variable_write import (
    MAX_VARIABLES as MAX_CREATE_VARIABLES,
    VariableWriteError,
    canonical_variable_name,
    canonical_variable_value,
)

PREVIEW_TOOL_NAME = "ue_preview_blueprint_patch_experimental"
APPLY_TOOL_NAME = "ue_apply_blueprint_patch_experimental"
SAVE_TOOL_NAME = "ue_save_blueprint_patch_experimental"
PREVIEW_CAPABILITY = "editor.previewBlueprintPatchExperimental"
APPLY_CAPABILITY = "editor.applyBlueprintPatchExperimental"
SAVE_CAPABILITY = "editor.saveBlueprintPatchExperimental"
PREVIEW_METHOD = "editor.previewBlueprintPatchExperimental"
APPLY_METHOD = "editor.applyBlueprintPatchExperimental"
SAVE_METHOD = "editor.saveBlueprintPatchExperimental"
APPLY_CONFIRMATION_PREFIX = "SET EXPERIMENTAL BLUEPRINT PATCH "
SAVE_CONFIRMATION_PREFIX = "SAVE EXPERIMENTAL BLUEPRINT PATCH "

PREVIEW_SCHEMA_VERSION = "ueak.blueprint-patch-preview/1.0"
APPLY_SCHEMA_VERSION = "ueak.blueprint-patch-apply/1.0"
MAX_REQUEST_BYTES = 60 * 1024
MAX_OPERATIONS = 16
MAX_OP_ID = 64
MAX_ASSET_PATH = 512

# Only these kinds are implemented by the live atomic patch.  Every kind this
# seam designed is migrated now, so a request that names anything else is refused
# before any write instead of being silently carried as a hybrid.
MIGRATED_KINDS = (
    "setPinDefault",
    "createVariable",
    "createNode",
    "connect",
    "disconnect",
    "rewirePin",
    "removeNode",
)

PIN_DEFAULT_FIELDS = ("nodeGuid", "pinName", "pinGuid", "type", "value")
CREATE_VARIABLE_FIELDS = ("name", "type", "defaultValue")
CREATE_NODE_FIELDS = ("nodeType", "x", "y")
# The three identity fields a variable accessor nodeType adds to createNode.  A
# primitive nodeType must not carry them (graph-edit-variable-field-not-allowed).
VARIABLE_ACCESSOR_FIELDS = ("variableName", "variableGuid", "variableType")
CONNECT_FIELDS = ("from", "fromPin", "to", "toPin")
# disconnect is the same four endpoint fields read the other way round.
DISCONNECT_FIELDS = CONNECT_FIELDS
# The node-removal writer's own three fields; the incident edges are enumerated
# separately so a removal can never silently take an edge with it.
REMOVE_NODE_FIELDS = ("nodeGuid", "expectedNodeClass", "removedEdges")
REMOVED_EDGE_FIELDS = (
    "fromNodeGuid",
    "fromPinName",
    "fromPinGuid",
    "toNodeGuid",
    "toPinName",
    "toPinGuid",
)
# The node-removal writer removes one node per call; a patch keeps that bound
# instead of letting one transaction delete a whole neighbourhood.
MAX_REMOVE_NODE_OPERATIONS = 1
# The writer breaks at most one incident edge per node removal.
MAX_REMOVED_EDGES_PER_NODE = 1
# The rewire writer's own nine field names, so one request object can move
# between the single-writer rewire tool and a patch operation.
REWIRE_FIELDS = (
    "targetNodeGuid",
    "targetPinName",
    "targetPinGuid",
    "oldSourceNodeGuid",
    "oldSourcePinName",
    "oldSourcePinGuid",
    "newSourceNodeGuid",
    "newSourcePinName",
    "newSourcePinGuid",
)
REWIRE_PIN_FIELDS = ("targetPinName", "oldSourcePinName", "newSourcePinName")
MAX_TEMP_ID_LENGTH = 64
PATCH_PIN_TYPES = ("exec", "bool", "int", "double", "string")
# Phase B: a PC_Wildcard endpoint (K2Node_Select / K2Node_PromotableOperator) has
# no type until Apply derives it, so a PREVIEW may honestly report `wildcard`.
# It is a distinct token, never "any type": at most one side may be wildcard and
# the APPLY result must carry the concrete type the schema derived.
PATCH_PREVIEW_PIN_TYPES = (*PATCH_PIN_TYPES, "wildcard")

_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_SHA1 = re.compile(r"^sha1:[0-9a-f]{40}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


class PatchError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PatchError("graph-patch-invalid-parameters", f"{label} must be an object.")
    return value


def _string(value: Any, label: str, *, maximum: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or (not allow_empty and not value):
        requirement = f"at most {maximum} characters"
        if not allow_empty:
            requirement = "non-empty " + requirement
        raise PatchError(
            "graph-patch-invalid-parameters",
            f"{label} must be a string of {requirement}.",
        )
    return value


def _reject_unknown(value: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise PatchError(
            "graph-patch-unknown-parameter",
            f"{label} contains unknown fields: {', '.join(unknown)}.",
        )


def _guid(value: Any, label: str) -> str:
    normalized = _string(value, label, maximum=36)
    if _GUID.fullmatch(normalized) is None:
        raise PatchError(
            "graph-patch-invalid-identifier",
            f"{label} must be one canonical lower-case GUID.",
        )
    return normalized


def _op_id(value: Any, index: int) -> str:
    normalized = _string(value, f"operations[{index}].opId", maximum=MAX_OP_ID)
    if any(ord(character) < 0x21 or ord(character) > 0x7E for character in normalized):
        raise PatchError(
            "graph-patch-invalid-parameters",
            f"operations[{index}].opId must be printable ASCII without spaces.",
        )
    return normalized


def _guid_with_code(value: Any, label: str, *, error_code: str) -> str:
    """Validate a GUID inside a result envelope, keeping the envelope's own code."""

    try:
        return _guid(value, label)
    except PatchError as exc:
        raise PatchError(error_code, exc.message) from exc


def _coordinate(value: Any, label: str, *, error_code: str) -> int:
    """One node coordinate, with the graph-edit writer's own bound and code."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PatchError(error_code, f"{label} must be an integer.")
    if isinstance(value, float) and not value.is_integer():
        raise PatchError(error_code, f"{label} must be an integer.")
    normalized = int(value)
    if normalized < -MAX_NODE_COORDINATE or normalized > MAX_NODE_COORDINATE:
        raise PatchError(
            error_code,
            f"{label} must be between -{MAX_NODE_COORDINATE} and {MAX_NODE_COORDINATE}.",
        )
    return normalized


def _temp_id(value: Any, label: str) -> str:
    """One patch-local label: ``tmp:`` plus 1-64 printable ASCII characters.

    Deliberately a superset of the single writer's own tempId rule: a patch
    tempId is resolved to a node GUID before Apply and is never forwarded, so the
    wider label cannot reach the writer.
    """

    if (
        not isinstance(value, str)
        or not value.startswith("tmp:")
        or not 5 <= len(value) <= 4 + MAX_TEMP_ID_LENGTH
        or any(ord(character) < 0x21 or ord(character) > 0x7E for character in value[4:])
    ):
        raise PatchError(
            "graph-edit-temp-id-invalid",
            f"{label} must be 'tmp:' followed by 1-{MAX_TEMP_ID_LENGTH} printable ASCII characters.",
        )
    return value


def _endpoint_ref(value: Any, label: str) -> tuple[str | None, str | None]:
    """One edge endpoint: (node_guid, temp_id); exactly one is not None."""

    if not isinstance(value, str) or not value:
        raise PatchError(
            "graph-patch-invalid-parameters",
            f"{label} must be a non-empty string.",
        )
    if value.startswith("tmp:"):
        return None, _temp_id(value, label)
    if _GUID.fullmatch(value) is None:
        raise PatchError(
            "graph-patch-invalid-identifier",
            f"{label} must be one canonical lower-case GUID or a 'tmp:' tempId.",
        )
    return value, None


def _action_id(value: Any, label: str) -> str:
    """One Action Registry identity: 'action:' + 32 lower-case hex characters.

    The id is a content hash the registry produces; this layer only checks the
    shape so a request cannot smuggle an arbitrary string into the Editor.
    """
    text = _string(value, label, maximum=39)
    if (
        not text.startswith("action:")
        or len(text) != 39
        or any(character not in "0123456789abcdef" for character in text[7:])
    ):
        raise PatchError(
            "graph-spawn-action-id-invalid",
            f"{label} must be 'action:' followed by 32 lower-case hex characters.",
        )
    return text


def _node_contract(node_type: Any, label: str, *, error_code: str) -> dict[str, Any]:
    """Resolve one primitive nodeType against the graph-edit writer's table."""

    if not isinstance(node_type, str) or node_type not in NODE_TYPES:
        raise PatchError(
            error_code,
            f"{label}.nodeType must be one of: {', '.join(sorted(NODE_TYPES))}.",
        )
    return NODE_TYPES[node_type]


def _function_matches(actual: Any, expected: str) -> bool:
    """Compare a per-operation CallFunction identity.

    The Editor reports the member name alone (`Not_PreBool`) while the node
    contract carries `Owner.Member` (`KismetMathLibrary.Not_PreBool`).  The owner
    is already pinned by `nodeClass` plus the nodeType whitelist, so accepting the
    bare member keeps the check meaningful instead of rejecting every node the
    Editor actually created (0-3.4 slice 13: this blocked Save for any patch that
    created a node).
    """

    if not isinstance(actual, str) or not actual:
        return False
    if actual == expected:
        return True
    return actual == expected.rsplit(".", 1)[-1]


def _canonical_node_fields(
    entry: dict[str, Any],
    label: str,
    *,
    error_code: str,
    expect_node_guid: bool,
) -> dict[str, Any]:
    """Validate one createNode request/result entry against the same table.

    The class, function, pin inventory and pin types are not taken on trust: they
    must equal the exact contract the single-writer graph edit tool declares for
    that nodeType, which is how a patch result cannot describe a node the writer
    would not have created.
    """

    node_type = entry.get("nodeType")
    action_id = entry.get("actionId")
    if action_id is not None:
        # Phase A action result: there is no nodeType table to check against.  The
        # identity is the action, the class is what the registry resolved to, and
        # the pins are the ones read from the LIVE node after creation -- so a
        # preview must not carry pins and an apply must.
        if node_type is not None:
            raise PatchError(
                error_code,
                f"{label} must not carry both nodeType and actionId.",
            )
        action_id = _action_id(action_id, f"{label}.actionId")
        node_class = _string(entry.get("nodeClass"), f"{label}.nodeClass", maximum=256)
        x = _coordinate(entry.get("x"), f"{label}.x", error_code=error_code)
        y = _coordinate(entry.get("y"), f"{label}.y", error_code=error_code)
        normalized: dict[str, Any] = {
            "actionId": action_id,
            "nodeClass": node_class,
            "x": x,
            "y": y,
        }
        for optional in ("function", "targetClass", "actionKind", "spawnerClass"):
            if entry.get(optional) is not None:
                normalized[optional] = _string(
                    entry.get(optional),
                    f"{label}.{optional}",
                    maximum=512,
                    allow_empty=True,
                )
        if not expect_node_guid:
            if entry.get("nodeGuid") is not None:
                raise PatchError(
                    error_code,
                    f"{label} must not carry a created nodeGuid before Apply.",
                )
            if entry.get("pins") is not None:
                raise PatchError(
                    error_code,
                    f"{label} must not carry pins before Apply; pins are read from the created node.",
                )
            return normalized
        normalized["nodeGuid"] = _guid_with_code(
            entry.get("nodeGuid"),
            f"{label}.nodeGuid",
            error_code=error_code,
        )
        pins = entry.get("pins")
        if not isinstance(pins, list) or not pins:
            raise PatchError(
                error_code,
                f"{label}.pins must list the created node's actual pins.",
            )
        normalized_pins: list[dict[str, Any]] = []
        for pin_index, raw_pin in enumerate(pins):
            pin = _object(raw_pin, f"{label}.pins[{pin_index}]")
            normalized_pin: dict[str, Any] = {}
            for field, maximum, allow_empty in (
                ("name", 256, False),
                ("direction", 8, False),
                ("kind", 8, False),
                ("category", 64, True),
                ("subCategory", 64, True),
                ("subCategoryObject", 512, True),
            ):
                normalized_pin[field] = _string(
                    pin.get(field),
                    f"{label}.pins[{pin_index}].{field}",
                    maximum=maximum,
                    allow_empty=allow_empty,
                )
            if normalized_pin["direction"] not in ("input", "output"):
                raise PatchError(
                    error_code,
                    f"{label}.pins[{pin_index}].direction must be input or output.",
                )
            hidden = pin.get("hidden")
            if hidden is not None:
                if not isinstance(hidden, bool):
                    raise PatchError(
                        error_code,
                        f"{label}.pins[{pin_index}].hidden must be a boolean.",
                    )
                normalized_pin["hidden"] = hidden
            normalized_pins.append(normalized_pin)
        normalized["pins"] = normalized_pins
        return normalized
    accessor = isinstance(node_type, str) and node_type in VARIABLE_NODE_TYPES
    if accessor:
        # The accessor's pin contract depends on the variable name and type, so it
        # comes from the single writer's own resolver instead of a static table.
        contract = node_contract(
            {
                "nodeType": node_type,
                "variableName": entry.get("variableName"),
                "variableType": entry.get("variableType"),
            }
        )
        if contract is None:
            raise PatchError(
                error_code,
                f"{label} is not a valid variable accessor declaration.",
            )
    else:
        contract = _node_contract(node_type, label, error_code=error_code)
    if entry.get("nodeClass") != contract["nodeClass"]:
        raise PatchError(
            error_code,
            f"{label}.nodeClass must be {contract['nodeClass']} for {entry.get('nodeType')}.",
        )
    expected_function = contract.get("function")
    if expected_function is None:
        if entry.get("function") not in (None, ""):
            raise PatchError(error_code, f"{label}.function is not part of this nodeType.")
    elif not _function_matches(entry.get("function"), expected_function):
        raise PatchError(
            error_code,
            f"{label}.function must be {expected_function} for {entry.get('nodeType')}.",
        )
    x = _coordinate(entry.get("x"), f"{label}.x", error_code=error_code)
    y = _coordinate(entry.get("y"), f"{label}.y", error_code=error_code)
    for pins_key, types_key in (
        ("inputPins", "inputPinTypes"),
        ("outputPins", "outputPinTypes"),
    ):
        expected_pins = list(contract[pins_key])
        if entry.get(pins_key) != expected_pins:
            raise PatchError(
                error_code,
                f"{label}.{pins_key} must be {expected_pins} for {entry.get('nodeType')}.",
            )
        expected_types = dict(contract[types_key])
        if entry.get(types_key) != expected_types:
            raise PatchError(
                error_code,
                f"{label}.{types_key} must be {expected_types} for {entry.get('nodeType')}.",
            )
    normalized: dict[str, Any] = {
        "nodeType": entry.get("nodeType"),
        "nodeClass": contract["nodeClass"],
        "x": x,
        "y": y,
    }
    if expected_function is not None:
        normalized["function"] = expected_function
    if accessor:
        try:
            normalized.update(validate_variable_accessor_fields(node_type, entry, label))
        except GraphEditError as exc:
            raise PatchError(exc.code, exc.message) from exc
        expected_access = VARIABLE_NODE_TYPES[node_type]["access"]
        if entry.get("variableAccess") != expected_access:
            raise PatchError(
                error_code,
                f"{label}.variableAccess must be {expected_access} for {node_type}.",
            )
        normalized["variableAccess"] = expected_access
    if expect_node_guid:
        normalized["nodeGuid"] = _guid_with_code(
            entry.get("nodeGuid"),
            f"{label}.nodeGuid",
            error_code=error_code,
        )
    elif entry.get("nodeGuid") is not None:
        raise PatchError(
            error_code,
            f"{label} must not carry a created nodeGuid before Apply.",
        )
    return normalized


def validate_preview_parameters(
    *,
    asset_path: Any,
    graph_guid: Any,
    operations: Any,
) -> dict[str, Any]:
    """Validate one bounded atomic patch request."""

    normalized_asset = _string(asset_path, "assetPath", maximum=MAX_ASSET_PATH)
    normalized_graph = _guid(graph_guid, "graphGuid")
    if not isinstance(operations, list) or not 1 <= len(operations) <= MAX_OPERATIONS:
        raise PatchError(
            "graph-patch-invalid-operations",
            f"operations must contain between 1 and {MAX_OPERATIONS} objects.",
        )

    normalized_operations: list[dict[str, Any]] = []
    seen_op_ids: set[str] = set()
    seen_targets: set[tuple[str, str, str]] = set()
    seen_variable_names: set[str] = set()
    seen_edge_targets: set[str] = set()
    declared_temp_ids: dict[str, int] = {}
    connection_counts: dict[str, int] = {}
    create_variable_count = 0
    create_node_count = 0
    remove_node_count = 0
    # connect, disconnect and rewirePin all move one edge, so they share the
    # single-writer edge bounds instead of each claiming its own.
    edge_operation_count = 0
    # Labels are collected up front so an edge that names a LATER createNode is
    # reported as a forward reference rather than as an unknown label (the
    # Editor makes the same distinction).
    for index, raw in enumerate(operations):
        if isinstance(raw, dict) and raw.get("kind") == "createNode" and "tempId" in raw:
            try:
                preview_temp = _temp_id(raw.get("tempId"), f"operations[{index}].tempId")
            except PatchError:
                continue  # the main pass reports the shape error with its own code
            if preview_temp in declared_temp_ids:
                raise PatchError(
                    "graph-patch-duplicate-temp-id",
                    f"Duplicate tempId: {preview_temp}.",
                )
            declared_temp_ids[preview_temp] = index
    for index, raw in enumerate(operations):
        item = _object(raw, f"operations[{index}]")
        op_id = _op_id(item.get("opId"), index)
        kind = _string(item.get("kind"), f"operations[{index}].kind", maximum=32)
        if op_id in seen_op_ids:
            raise PatchError(
                "graph-patch-duplicate-op-id",
                f"Duplicate opId: {op_id}.",
            )
        seen_op_ids.add(op_id)
        if kind not in MIGRATED_KINDS:
            # Every designed kind is migrated, so this is a typo or a future
            # feature, never "migrated later".
            raise PatchError(
                "graph-patch-kind-not-allowed",
                f"Operation kind '{kind}' is not an atomic patch kind; this build "
                f"accepts: {', '.join(MIGRATED_KINDS)}.",
            )
        if kind == "createVariable":
            _reject_unknown(
                item,
                {"opId", "kind", *CREATE_VARIABLE_FIELDS},
                f"operations[{index}]",
            )
            # Reuse the single-writer validators so the atomic patch and
            # ue_apply_blueprint_variable_create_experimental cannot disagree about
            # what a legal variable is.  Their error codes are kept verbatim; only
            # the exception type is normalized.
            try:
                name = canonical_variable_name(item.get("name"))
                variable_type, default_value = canonical_variable_value(
                    item.get("type"),
                    item.get("defaultValue"),
                )
            except VariableWriteError as exc:
                raise PatchError(exc.code, exc.message) from exc
            create_variable_count += 1
            if create_variable_count > MAX_CREATE_VARIABLES:
                # One patch may not create more variables than the single writer.
                raise PatchError(
                    "graph-patch-create-variable-limit",
                    f"A patch may create at most {MAX_CREATE_VARIABLES} variables, the "
                    "same bound as the single-writer variable tool.",
                )
            name_key = name.lower()
            if name_key in seen_variable_names:
                raise PatchError(
                    "graph-patch-duplicate-target",
                    f"Duplicate variable name: {name}.",
                )
            seen_variable_names.add(name_key)
            normalized_operations.append(
                {
                    "opId": op_id,
                    "kind": kind,
                    "name": name,
                    "type": variable_type,
                    "defaultValue": default_value,
                }
            )
            continue
        if kind == "createNode":
            _reject_unknown(
                item,
                {
                    "opId",
                    "kind",
                    *CREATE_NODE_FIELDS,
                    "actionId",
                    "tempId",
                    *VARIABLE_ACCESSOR_FIELDS,
                },
                f"operations[{index}]",
            )
            node_type = item.get("nodeType")
            action_id = item.get("actionId")
            if node_type is not None and action_id is not None:
                raise PatchError(
                    "graph-spawn-action-id-conflict",
                    f"operations[{index}] takes exactly one of nodeType and actionId.",
                )
            if node_type is None and action_id is None:
                raise PatchError(
                    "graph-edit-node-type-not-allowed",
                    f"operations[{index}] needs nodeType or actionId.",
                )
            if action_id is not None:
                # Phase A: an opaque registry identity replaces the nodeType
                # whitelist.  The Editor re-derives the class and the admission
                # fields from the registry; this layer only checks the shape.
                action_id = _action_id(action_id, f"operations[{index}].actionId")
                if any(field in item for field in VARIABLE_ACCESSOR_FIELDS):
                    raise PatchError(
                        "graph-edit-variable-field-not-allowed",
                        f"operations[{index}] actionId does not accept variableName/variableGuid/variableType.",
                    )
                accessor = False
                variable_fields = {}
            else:
                accessor = isinstance(node_type, str) and node_type in VARIABLE_NODE_TYPES
                # nodeType/x/y plus an optional patch-local tempId; the two accessor
                # nodeTypes additionally bind one EXISTING member variable by name,
                # GUID and type.  No literal is accepted (owned literals are still
                # unmigrated).
                if accessor:
                    try:
                        variable_fields = validate_variable_accessor_fields(
                            node_type,
                            item,
                            f"operations[{index}]",
                        )
                    except GraphEditError as exc:
                        raise PatchError(exc.code, exc.message) from exc
                else:
                    _node_contract(
                        node_type,
                        f"operations[{index}]",
                        error_code="graph-edit-node-type-not-allowed",
                    )
                    if any(field in item for field in VARIABLE_ACCESSOR_FIELDS):
                        raise PatchError(
                            "graph-edit-variable-field-not-allowed",
                            f"operations[{index}] nodeType {node_type} does not accept "
                            "variableName/variableGuid/variableType.",
                        )
                    variable_fields = {}
            x = _coordinate(
                item.get("x"),
                f"operations[{index}].x",
                error_code="graph-edit-invalid-parameters",
            )
            y = _coordinate(
                item.get("y"),
                f"operations[{index}].y",
                error_code="graph-edit-invalid-parameters",
            )
            create_node_count += 1
            if create_node_count > MAX_CREATE_NODES:
                # One patch may not create more nodes than the single writer.
                raise PatchError(
                    "graph-patch-create-node-limit",
                    f"A patch may create at most {MAX_CREATE_NODES} nodes, the same "
                    "bound as the single-writer graph edit tool.",
                )
            normalized_node: dict[str, Any] = {
                "opId": op_id,
                "kind": kind,
                "x": x,
                "y": y,
            }
            if action_id is not None:
                # The action IS the identity; nodeType is deliberately absent so
                # the Editor cannot see both and pick one.
                normalized_node["actionId"] = action_id
            else:
                normalized_node["nodeType"] = node_type
            if accessor:
                normalized_node.update(variable_fields)
            if "tempId" in item:
                temp_id = _temp_id(item.get("tempId"), f"operations[{index}].tempId")
                # Duplicates were already refused by the label pre-pass.
                normalized_node["tempId"] = temp_id
            normalized_operations.append(normalized_node)
            continue
        if kind in ("connect", "disconnect"):
            # Same four endpoint fields; the kind alone decides which way the edge
            # moves, so the shape and tempId rules are shared verbatim.
            _reject_unknown(
                item,
                {"opId", "kind", *CONNECT_FIELDS},
                f"operations[{index}]",
            )
            from_guid, from_temp = _endpoint_ref(item.get("from"), f"operations[{index}].from")
            to_guid, to_temp = _endpoint_ref(item.get("to"), f"operations[{index}].to")
            for temp_id, label in (
                (from_temp, f"operations[{index}].from"),
                (to_temp, f"operations[{index}].to"),
            ):
                if temp_id is None:
                    continue
                if temp_id not in declared_temp_ids:
                    raise PatchError(
                        "graph-patch-unknown-temp-id",
                        f"{label} references tempId '{temp_id}', which no createNode operation in this patch declares.",
                    )
                if declared_temp_ids[temp_id] > index:
                    # A label declared later in the same request is a forward
                    # reference: the node would not exist when the edge is applied.
                    raise PatchError(
                        "graph-patch-forward-temp-id",
                        f"{label} references tempId '{temp_id}' before the createNode operation that declares it.",
                    )
            from_pin = _string(
                item.get("fromPin"),
                f"operations[{index}].fromPin",
                maximum=128,
            )
            to_pin = _string(
                item.get("toPin"),
                f"operations[{index}].toPin",
                maximum=128,
            )
            edge_operation_count += 1
            if edge_operation_count > MAX_CONNECTIONS:
                # Unreachable while the patch is capped at 16 operations; kept so
                # the bound is explicit rather than implied by another limit.
                raise PatchError(
                    "graph-patch-connect-limit",
                    f"A patch may change at most {MAX_CONNECTIONS} connections, the same "
                    "bound as the single-writer graph edit tool.",
                )
            for node_ref in (
                from_guid if from_guid is not None else from_temp,
                to_guid if to_guid is not None else to_temp,
            ):
                assert node_ref is not None
                connection_counts[node_ref] = connection_counts.get(node_ref, 0) + 1
                if connection_counts[node_ref] > MAX_CONNECTIONS_PER_NODE:
                    raise PatchError(
                        "graph-edit-connection-limit",
                        f"{node_ref} would exceed the {MAX_CONNECTIONS_PER_NODE}-connection per-node limit.",
                    )
            target_key = f"{to_guid if to_guid is not None else to_temp}|{to_pin}"
            if target_key in seen_edge_targets:
                raise PatchError(
                    "graph-edit-connection-duplicate",
                    f"Input pin already has a connection in this patch: {target_key}.",
                )
            seen_edge_targets.add(target_key)
            normalized_edge: dict[str, Any] = {
                "opId": op_id,
                "kind": kind,
                "fromPin": from_pin,
                "toPin": to_pin,
            }
            if from_temp is not None:
                normalized_edge["from"] = from_temp
            else:
                normalized_edge["from"] = from_guid
            if to_temp is not None:
                normalized_edge["to"] = to_temp
            else:
                normalized_edge["to"] = to_guid
            normalized_operations.append(normalized_edge)
            continue
        if kind == "rewirePin":
            _reject_unknown(
                item,
                {"opId", "kind", *REWIRE_FIELDS},
                f"operations[{index}]",
            )
            normalized_rewire: dict[str, Any] = {"opId": op_id, "kind": kind}
            for field in REWIRE_FIELDS:
                if field in REWIRE_PIN_FIELDS:
                    normalized_rewire[field] = _string(
                        item.get(field),
                        f"operations[{index}].{field}",
                        maximum=128,
                    )
                else:
                    normalized_rewire[field] = _guid_with_code(
                        item.get(field),
                        f"operations[{index}].{field}",
                        error_code="graph-rewire-invalid-identifier",
                    )
            # One rewire moves one edge out and one in, so it counts against the
            # same per-patch edge bound as connect/disconnect.
            edge_operation_count += 1
            if edge_operation_count > MAX_CONNECTIONS:
                raise PatchError(
                    "graph-patch-connect-limit",
                    f"A patch may change at most {MAX_CONNECTIONS} connections, the same "
                    "bound as the single-writer graph edit tool.",
                )
            normalized_operations.append(normalized_rewire)
            continue
        if kind == "removeNode":
            _reject_unknown(
                item,
                {"opId", "kind", *REMOVE_NODE_FIELDS},
                f"operations[{index}]",
            )
            node_guid = _guid_with_code(
                item.get("nodeGuid"),
                f"operations[{index}].nodeGuid",
                error_code="graph-remove-invalid-identifier",
            )
            expected_node_class = _string(
                item.get("expectedNodeClass"),
                f"operations[{index}].expectedNodeClass",
                maximum=256,
            )
            raw_edges = item.get("removedEdges")
            if not isinstance(raw_edges, list):
                raise PatchError(
                    "graph-remove-invalid-parameters",
                    "removedEdges must be an array (empty for an isolated node).",
                )
            if len(raw_edges) > MAX_REMOVED_EDGES_PER_NODE:
                raise PatchError(
                    "graph-remove-removed-edges-invalid",
                    f"A node-removal operation may list at most {MAX_REMOVED_EDGES_PER_NODE} incident edge.",
                )
            normalized_edges: list[dict[str, str]] = []
            seen_edge_keys: set[tuple[str, ...]] = set()
            for edge_index, raw_edge in enumerate(raw_edges):
                edge = _object(raw_edge, f"operations[{index}].removedEdges[{edge_index}]")
                _reject_unknown(
                    edge,
                    set(REMOVED_EDGE_FIELDS),
                    f"operations[{index}].removedEdges[{edge_index}]",
                )
                normalized_edge: dict[str, str] = {}
                for field in REMOVED_EDGE_FIELDS:
                    if field.endswith("PinName"):
                        normalized_edge[field] = _string(
                            edge.get(field),
                            f"operations[{index}].removedEdges[{edge_index}].{field}",
                            maximum=128,
                        )
                    else:
                        normalized_edge[field] = _guid_with_code(
                            edge.get(field),
                            f"operations[{index}].removedEdges[{edge_index}].{field}",
                            error_code="graph-remove-removed-edges-invalid",
                        )
                edge_key = tuple(normalized_edge[field] for field in REMOVED_EDGE_FIELDS)
                if edge_key in seen_edge_keys:
                    raise PatchError(
                        "graph-remove-removed-edges-invalid",
                        "removedEdges must not contain duplicate entries.",
                    )
                seen_edge_keys.add(edge_key)
                normalized_edges.append(normalized_edge)
            # A patch may not remove more nodes than the single writer may.
            remove_node_count += 1
            if remove_node_count > MAX_REMOVE_NODE_OPERATIONS:
                raise PatchError(
                    "graph-patch-remove-node-limit",
                    f"A patch may remove at most {MAX_REMOVE_NODE_OPERATIONS} node, the "
                    "same bound as the single-writer node-removal tool.",
                )
            normalized_operations.append(
                {
                    "opId": op_id,
                    "kind": kind,
                    "nodeGuid": node_guid,
                    "expectedNodeClass": expected_node_class,
                    "removedEdges": normalized_edges,
                }
            )
            continue
        _reject_unknown(
            item,
            {"opId", "kind", *PIN_DEFAULT_FIELDS},
            f"operations[{index}]",
        )
        # Reuse the single-writer validator so the atomic patch and
        # ue_apply_blueprint_pin_defaults_experimental cannot disagree about what
        # a legal pin default is.  Its error keeps its own code; only the exception
        # type is normalized so this module has one failure type.
        try:
            pin_default = validate_pin_default_parameters(
                asset_path=normalized_asset,
                graph_guid=normalized_graph,
                defaults=[
                    {
                        "nodeGuid": item.get("nodeGuid"),
                        "pinName": item.get("pinName"),
                        "pinGuid": item.get("pinGuid"),
                        "type": item.get("type"),
                        "value": item.get("value"),
                    }
                ],
            )["defaults"][0]
        except PinDefaultError as exc:
            raise PatchError(exc.code, exc.message) from exc
        target_key = (
            pin_default["nodeGuid"],
            pin_default["pinName"],
            pin_default["pinGuid"],
        )
        if target_key in seen_targets:
            # Two operations writing one pin would make the read-back of the first
            # one fail by construction; refuse instead of leaving that to chance.
            raise PatchError(
                "graph-patch-duplicate-target",
                f"Duplicate pin default target: {target_key[0]}.{target_key[1]} ({target_key[2]}).",
            )
        seen_targets.add(target_key)
        normalized_operations.append(
            {
                "opId": op_id,
                "kind": kind,
                "nodeGuid": pin_default["nodeGuid"],
                "pinName": pin_default["pinName"],
                "pinGuid": pin_default["pinGuid"],
                "type": pin_default["type"],
                "value": pin_default["value"],
            }
        )
    return {
        "assetPath": normalized_asset,
        "graphGuid": normalized_graph,
        "operations": normalized_operations,
    }


def confirmation_for_plan(plan_id: str) -> str:
    return APPLY_CONFIRMATION_PREFIX + plan_id


def save_confirmation_for_plan(plan_id: str) -> str:
    return SAVE_CONFIRMATION_PREFIX + plan_id


def _canonical_variable_fields(
    entry: dict[str, Any],
    label: str,
    *,
    error_code: str,
) -> dict[str, str]:
    """Validate one createVariable result entry with the variable writer's rules."""

    try:
        name = canonical_variable_name(entry.get("name"))
        variable_type, default_value = canonical_variable_value(
            entry.get("type"),
            entry.get("defaultValue"),
        )
    except VariableWriteError as exc:
        raise PatchError(error_code, f"{label}: {exc.message}") from exc
    if entry.get("existedBefore") is not False:
        raise PatchError(error_code, f"{label}.existedBefore must be false.")
    return {"name": name, "type": variable_type, "defaultValue": default_value}


def _canonical_edge_preview_fields(
    entry: dict[str, Any],
    label: str,
    declared_temp_ids: dict[str, int],
) -> dict[str, Any]:
    """Validate one connect preview entry (identity not resolved yet)."""

    def endpoint(side: str) -> tuple[str | None, str | None, str | None]:
        guid_value = entry.get(f"{side}NodeGuid")
        temp_value = entry.get(f"{side}TempId")
        if (guid_value is None) == (temp_value is None):
            raise PatchError(
                "graph-patch-preview-invalid",
                f"{label}.{side} must carry exactly one of NodeGuid/TempId.",
            )
        if temp_value is not None:
            temp_id = _temp_id(temp_value, f"{label}.{side}TempId")
            if temp_id not in declared_temp_ids:
                raise PatchError(
                    "graph-patch-preview-invalid",
                    f"{label}.{side}TempId {temp_id} was not declared earlier.",
                )
            if entry.get(f"{side}PinGuid") is not None:
                raise PatchError(
                    "graph-patch-preview-invalid",
                    f"{label}.{side}PinGuid cannot be known before the node exists.",
                )
            return None, temp_id, None
        guid_value = _guid_with_code(
            guid_value,
            f"{label}.{side}NodeGuid",
            error_code="graph-patch-preview-invalid",
        )
        pin_guid = _guid_with_code(
            entry.get(f"{side}PinGuid"),
            f"{label}.{side}PinGuid",
            error_code="graph-patch-preview-invalid",
        )
        return guid_value, None, pin_guid

    from_guid, from_temp, from_pin_guid = endpoint("from")
    to_guid, to_temp, to_pin_guid = endpoint("to")
    from_pin = _string(entry.get("fromPin"), f"{label}.fromPin", maximum=128)
    to_pin = _string(entry.get("toPin"), f"{label}.toPin", maximum=128)
    from_type = _string(entry.get("fromType"), f"{label}.fromType", maximum=16)
    to_type = _string(entry.get("toType"), f"{label}.toType", maximum=16)
    if from_type not in PATCH_PREVIEW_PIN_TYPES or to_type not in PATCH_PREVIEW_PIN_TYPES:
        raise PatchError(
            "graph-patch-preview-invalid",
            f"{label} carries a pin type outside {', '.join(PATCH_PREVIEW_PIN_TYPES)}.",
        )
    if from_type != to_type and "wildcard" not in (from_type, to_type):
        raise PatchError(
            "graph-patch-preview-invalid",
            f"{label} connects {from_type} to {to_type}; data kinds must match.",
        )
    normalized: dict[str, Any] = {
        "fromPin": from_pin,
        "fromType": from_type,
        "toPin": to_pin,
        "toType": to_type,
    }
    if from_temp is not None:
        normalized["fromTempId"] = from_temp
    else:
        normalized["fromNodeGuid"] = from_guid
        normalized["fromPinGuid"] = from_pin_guid
    if to_temp is not None:
        normalized["toTempId"] = to_temp
    else:
        normalized["toNodeGuid"] = to_guid
        normalized["toPinGuid"] = to_pin_guid
    return normalized


def _canonical_edge_apply_fields(entry: dict[str, Any], label: str) -> dict[str, Any]:
    """Validate one connect apply entry (both endpoints are real nodes now)."""

    from_node = _guid_with_code(
        entry.get("fromNodeGuid"),
        f"{label}.fromNodeGuid",
        error_code="graph-patch-apply-invalid",
    )
    to_node = _guid_with_code(
        entry.get("toNodeGuid"),
        f"{label}.toNodeGuid",
        error_code="graph-patch-apply-invalid",
    )
    from_pin_guid = _guid_with_code(
        entry.get("fromPinGuid"),
        f"{label}.fromPinGuid",
        error_code="graph-patch-apply-invalid",
    )
    to_pin_guid = _guid_with_code(
        entry.get("toPinGuid"),
        f"{label}.toPinGuid",
        error_code="graph-patch-apply-invalid",
    )
    from_pin = _string(entry.get("fromPin"), f"{label}.fromPin", maximum=128)
    to_pin = _string(entry.get("toPin"), f"{label}.toPin", maximum=128)
    from_type = _string(entry.get("fromType"), f"{label}.fromType", maximum=16)
    to_type = _string(entry.get("toType"), f"{label}.toType", maximum=16)
    if from_type not in PATCH_PIN_TYPES or to_type not in PATCH_PIN_TYPES:
        raise PatchError(
            "graph-patch-apply-invalid",
            f"{label} carries a pin type outside {', '.join(PATCH_PIN_TYPES)}.",
        )
    if from_type != to_type:
        raise PatchError(
            "graph-patch-apply-invalid",
            f"{label} connects {from_type} to {to_type}; data kinds must match.",
        )
    return {
        "fromNodeGuid": from_node,
        "fromPin": from_pin,
        "fromPinGuid": from_pin_guid,
        "fromType": from_type,
        "toNodeGuid": to_node,
        "toPin": to_pin,
        "toPinGuid": to_pin_guid,
        "toType": to_type,
    }


def _canonical_rewire_fields(
    entry: dict[str, Any],
    label: str,
    *,
    error_code: str,
) -> dict[str, Any]:
    """Validate one rewirePin entry; preview and apply carry the same shape."""

    normalized: dict[str, Any] = {}
    for field in REWIRE_FIELDS:
        if field in REWIRE_PIN_FIELDS:
            normalized[field] = _string(
                entry.get(field),
                f"{label}.{field}",
                maximum=128,
            )
        else:
            normalized[field] = _guid_with_code(
                entry.get(field),
                f"{label}.{field}",
                error_code=error_code,
            )
    pin_kind = _string(entry.get("pinKind"), f"{label}.pinKind", maximum=16)
    if pin_kind not in PATCH_PIN_TYPES:
        raise PatchError(
            error_code,
            f"{label}.pinKind is outside {', '.join(PATCH_PIN_TYPES)}.",
        )
    normalized["pinKind"] = pin_kind
    return normalized


def _canonical_removed_edges(
    value: Any,
    label: str,
    *,
    error_code: str,
) -> list[dict[str, str]]:
    """Validate the enumerated incident edges of one removeNode result entry."""

    if not isinstance(value, list):
        raise PatchError(error_code, f"{label} must be an array.")
    if len(value) > MAX_REMOVED_EDGES_PER_NODE:
        raise PatchError(
            error_code,
            f"{label} may carry at most {MAX_REMOVED_EDGES_PER_NODE} incident edge.",
        )
    normalized: list[dict[str, str]] = []
    for index, raw in enumerate(value):
        entry = _object(raw, f"{label}[{index}]")
        _reject_unknown(
            entry,
            set(REMOVED_EDGE_FIELDS) | {"fromKind", "toKind"},
            f"{label}[{index}]",
        )
        item: dict[str, str] = {}
        for field in REMOVED_EDGE_FIELDS:
            if field.endswith("PinName"):
                item[field] = _string(
                    entry.get(field),
                    f"{label}[{index}].{field}",
                    maximum=128,
                )
            else:
                item[field] = _guid_with_code(
                    entry.get(field),
                    f"{label}[{index}].{field}",
                    error_code=error_code,
                )
        for side in ("fromKind", "toKind"):
            kind = _string(entry.get(side), f"{label}[{index}].{side}", maximum=16)
            if kind not in PATCH_PIN_TYPES:
                raise PatchError(
                    error_code,
                    f"{label}[{index}].{side} is outside {', '.join(PATCH_PIN_TYPES)}.",
                )
            item[side] = kind
        normalized.append(item)
    return normalized


def _canonical_remove_node_fields(
    entry: dict[str, Any],
    label: str,
    *,
    error_code: str,
) -> dict[str, Any]:
    """Validate one removeNode entry; preview and apply carry the same shape.

    Extra envelope fields (`opId`/`kind`/`status`/`rolledBack`) are tolerated here
    exactly like the other result validators; the request side is where unknown
    fields are refused.
    """

    normalized: dict[str, Any] = {
        "nodeGuid": _guid_with_code(
            entry.get("nodeGuid"),
            f"{label}.nodeGuid",
            error_code=error_code,
        ),
        "nodeClass": _string(entry.get("nodeClass"), f"{label}.nodeClass", maximum=256),
        "removedEdges": _canonical_removed_edges(
            entry.get("removedEdges"),
            f"{label}.removedEdges",
            error_code=error_code,
        ),
    }
    pin_count = entry.get("nodePinCount")
    if pin_count is not None:
        if isinstance(pin_count, bool) or not isinstance(pin_count, int) or pin_count < 0:
            raise PatchError(error_code, f"{label}.nodePinCount must be a non-negative integer.")
        normalized["nodePinCount"] = pin_count
    return normalized


def validate_preview_result(preview: Any) -> dict[str, Any]:
    item = _object(_unwrap_tool_result(preview, "planId"), "preview")
    operations = item.get("operations")
    if (
        item.get("schemaVersion") != PREVIEW_SCHEMA_VERSION
        or item.get("experimental") is not True
        or item.get("readOnly") is not True
        or item.get("wroteBlueprint") is not False
        or item.get("savedPackage") is not False
        or item.get("compiled") is not False
        or not isinstance(item.get("planId"), str)
        or _SHA256.fullmatch(item.get("planId")) is None
        or not isinstance(item.get("packageRevision"), str)
        or _SHA256.fullmatch(item.get("packageRevision")) is None
        or not isinstance(item.get("graphFingerprint"), str)
        or _SHA1.fullmatch(item.get("graphFingerprint")) is None
        or not isinstance(item.get("stateFingerprint"), str)
        or _SHA256.fullmatch(item.get("stateFingerprint")) is None
        or not isinstance(item.get("variablesFingerprint"), str)
        or _SHA256.fullmatch(item.get("variablesFingerprint")) is None
        or not isinstance(item.get("nodesFingerprint"), str)
        or _SHA256.fullmatch(item.get("nodesFingerprint")) is None
        or not isinstance(item.get("edgesFingerprint"), str)
        or _SHA256.fullmatch(item.get("edgesFingerprint")) is None
        or isinstance(item.get("variableCount"), bool)
        or not isinstance(item.get("variableCount"), int)
        or item.get("variableCount") < 0
        or not isinstance(operations, list)
        or not 1 <= len(operations) <= MAX_OPERATIONS
        or item.get("operationCount") != len(operations)
        or item.get("migratedKinds") != list(MIGRATED_KINDS)
    ):
        raise PatchError(
            "graph-patch-preview-invalid",
            "Preview envelope, planId, fingerprints, counts, kinds or operation list are invalid.",
        )
    normalized_asset = _string(item.get("assetPath"), "preview.assetPath", maximum=MAX_ASSET_PATH)
    normalized_graph = _guid(item.get("graphGuid"), "preview.graphGuid")
    normalized_operations: list[dict[str, Any]] = []
    seen_op_ids: set[str] = set()
    seen_targets: set[tuple[str, str, str]] = set()
    seen_variable_names: set[str] = set()
    declared_temp_ids: dict[str, int] = {}
    for index, raw in enumerate(operations):
        entry = _object(raw, f"preview.operations[{index}]")
        op_id = _op_id(entry.get("opId"), index)
        if op_id in seen_op_ids:
            raise PatchError(
                "graph-patch-preview-invalid",
                f"Preview repeats the operation id {op_id}.",
            )
        seen_op_ids.add(op_id)
        kind = _string(entry.get("kind"), f"preview.operations[{index}].kind", maximum=32)
        if kind not in MIGRATED_KINDS:
            raise PatchError(
                "graph-patch-preview-invalid",
                f"Preview contains kind '{kind}', which this build did not migrate.",
            )
        if entry.get("status") != "validated":
            raise PatchError(
                "graph-patch-preview-invalid",
                "Preview operations must all be validated.",
            )
        if kind == "createVariable":
            if entry.get("guid") is not None:
                raise PatchError(
                    "graph-patch-preview-invalid",
                    f"preview.operations[{index}] must not carry a created GUID before Apply.",
                )
            normalized = _canonical_variable_fields(
                entry,
                f"preview.operations[{index}]",
                error_code="graph-patch-preview-invalid",
            )
            if normalized["name"].lower() in seen_variable_names:
                raise PatchError(
                    "graph-patch-preview-invalid",
                    f"Preview repeats the variable name {normalized['name']}.",
                )
            seen_variable_names.add(normalized["name"].lower())
            normalized_operations.append({"opId": op_id, "kind": kind, **normalized})
            continue
        if kind == "createNode":
            normalized = _canonical_node_fields(
                entry,
                f"preview.operations[{index}]",
                error_code="graph-patch-preview-invalid",
                expect_node_guid=False,
            )
            if "tempId" in entry:
                temp_id = _temp_id(entry.get("tempId"), f"preview.operations[{index}].tempId")
                if temp_id in declared_temp_ids:
                    raise PatchError(
                        "graph-patch-preview-invalid",
                        f"Preview repeats the tempId {temp_id}.",
                    )
                declared_temp_ids[temp_id] = index
                normalized["tempId"] = temp_id
            normalized_operations.append({"opId": op_id, "kind": kind, **normalized})
            continue
        if kind in ("connect", "disconnect"):
            try:
                normalized = _canonical_edge_preview_fields(
                    entry,
                    f"preview.operations[{index}]",
                    declared_temp_ids,
                )
            except PatchError as exc:
                raise PatchError("graph-patch-preview-invalid", exc.message) from exc
            normalized_operations.append({"opId": op_id, "kind": kind, **normalized})
            continue
        if kind == "rewirePin":
            try:
                normalized = _canonical_rewire_fields(
                    entry,
                    f"preview.operations[{index}]",
                    error_code="graph-patch-preview-invalid",
                )
            except PatchError as exc:
                raise PatchError("graph-patch-preview-invalid", exc.message) from exc
            normalized_operations.append({"opId": op_id, "kind": kind, **normalized})
            continue
        if kind == "removeNode":
            try:
                normalized = _canonical_remove_node_fields(
                    entry,
                    f"preview.operations[{index}]",
                    error_code="graph-patch-preview-invalid",
                )
            except PatchError as exc:
                raise PatchError("graph-patch-preview-invalid", exc.message) from exc
            normalized_operations.append({"opId": op_id, "kind": kind, **normalized})
            continue
        node_guid = _guid(entry.get("nodeGuid"), f"preview.operations[{index}].nodeGuid")
        pin_guid = _guid(entry.get("pinGuid"), f"preview.operations[{index}].pinGuid")
        pin_name = _string(
            entry.get("pinName"),
            f"preview.operations[{index}].pinName",
            maximum=128,
        )
        normalized_type, after = canonical_value(entry.get("type"), entry.get("afterValue"))
        before = _string(
            entry.get("beforeValue"),
            f"preview.operations[{index}].beforeValue",
            maximum=1024,
            allow_empty=True,
        )
        target_key = (node_guid, pin_name, pin_guid)
        if target_key in seen_targets:
            raise PatchError(
                "graph-patch-preview-invalid",
                f"Preview repeats the pin target {node_guid}.{pin_name} ({pin_guid}).",
            )
        seen_targets.add(target_key)
        normalized_operations.append(
            {
                "opId": op_id,
                "kind": kind,
                "nodeGuid": node_guid,
                "pinName": pin_name,
                "pinGuid": pin_guid,
                "type": normalized_type,
                "beforeValue": before,
                "afterValue": after,
            }
        )
    if item.get("requiredConfirmation") != confirmation_for_plan(item.get("planId")):
        raise PatchError(
            "graph-patch-preview-invalid",
            "Preview requiredConfirmation does not match planId.",
        )
    return {
        "assetPath": normalized_asset,
        "graphGuid": normalized_graph,
        "planId": item.get("planId"),
        "packageRevision": item.get("packageRevision"),
        "graphFingerprint": item.get("graphFingerprint"),
        "stateFingerprint": item.get("stateFingerprint"),
        "variablesFingerprint": item.get("variablesFingerprint"),
        "nodesFingerprint": item.get("nodesFingerprint"),
        "edgesFingerprint": item.get("edgesFingerprint"),
        "variableCount": item.get("variableCount"),
        "operations": normalized_operations,
        "requiredConfirmation": item.get("requiredConfirmation"),
    }


def build_apply_parameters(
    *,
    plan_id: Any,
    asset_path: Any,
    graph_guid: Any,
    operations: Any,
    confirmation: Any = "",
) -> dict[str, Any]:
    """Build the Apply request from the plan fields alone.

    ``planId`` is a sha256 over the live before values and fingerprints, so the
    Editor still proves both "you previewed" and "the graph has not changed";
    handing the whole preview object back adds no check on top of that.
    """

    normalized = validate_preview_parameters(
        asset_path=asset_path,
        graph_guid=graph_guid,
        operations=operations,
    )
    if not isinstance(plan_id, str) or _SHA256.fullmatch(plan_id) is None:
        raise PatchError(
            "graph-patch-invalid-parameters",
            "planId must be the sha256:<64 lowercase hex> value returned by Preview.",
        )
    expected = confirmation_for_plan(plan_id)
    if confirmation in (None, ""):
        confirmation = expected
    if not isinstance(confirmation, str) or confirmation != expected:
        raise PatchError(
            "graph-patch-confirmation-required",
            f"Explicit Apply confirmation must be exactly {expected}.",
        )
    return {
        "assetPath": normalized["assetPath"],
        "graphGuid": normalized["graphGuid"],
        "operations": normalized["operations"],
        "planId": plan_id,
        "confirmation": confirmation,
    }


def validate_apply_result(apply_result: Any) -> dict[str, Any]:
    item = _object(_unwrap_tool_result(apply_result, "applied"), "applyResult")
    if (
        item.get("schemaVersion") != APPLY_SCHEMA_VERSION
        or item.get("experimental") is not True
        or item.get("applied") is not True
        or item.get("saved") is True
        or item.get("singleTransaction") is not True
        or item.get("transactionCount") != 1
        or not isinstance(item.get("planId"), str)
        or _SHA256.fullmatch(item.get("planId")) is None
        or not isinstance(item.get("graphFingerprintAfter"), str)
        or _SHA1.fullmatch(item.get("graphFingerprintAfter")) is None
        or not isinstance(item.get("stateFingerprintAfter"), str)
        or _SHA256.fullmatch(item.get("stateFingerprintAfter")) is None
        or not isinstance(item.get("variablesFingerprintBefore"), str)
        or _SHA256.fullmatch(item.get("variablesFingerprintBefore")) is None
        or not isinstance(item.get("variablesFingerprintAfter"), str)
        or _SHA256.fullmatch(item.get("variablesFingerprintAfter")) is None
        or not isinstance(item.get("nodesFingerprintBefore"), str)
        or _SHA256.fullmatch(item.get("nodesFingerprintBefore")) is None
        or not isinstance(item.get("nodesFingerprintAfter"), str)
        or _SHA256.fullmatch(item.get("nodesFingerprintAfter")) is None
        or not isinstance(item.get("edgesFingerprintBefore"), str)
        or _SHA256.fullmatch(item.get("edgesFingerprintBefore")) is None
        or not isinstance(item.get("edgesFingerprintAfter"), str)
        or _SHA256.fullmatch(item.get("edgesFingerprintAfter")) is None
    ):
        raise PatchError(
            "graph-patch-apply-invalid",
            "Apply envelope, planId, transaction or fingerprints are invalid.",
        )
    results = item.get("results")
    if not isinstance(results, list) or item.get("operationCount") != len(results):
        raise PatchError(
            "graph-patch-apply-invalid",
            "Apply results must carry exactly one entry per operation.",
        )
    normalized_asset = _string(item.get("assetPath"), "applyResult.assetPath", maximum=MAX_ASSET_PATH)
    normalized_graph = _guid(item.get("graphGuid"), "applyResult.graphGuid")
    normalized_results: list[dict[str, Any]] = []
    created_variables = 0
    created_nodes = 0
    removed_nodes = 0
    removed_node_edges = 0
    created_edges = 0
    removed_edges = 0
    for index, raw in enumerate(results):
        entry = _object(raw, f"applyResult.results[{index}]")
        if entry.get("status") != "applied" or entry.get("rolledBack") is not False:
            raise PatchError(
                "graph-patch-apply-invalid",
                "Every applied operation must report status=applied and rolledBack=false.",
            )
        op_id = _op_id(entry.get("opId"), index)
        kind = _string(entry.get("kind"), f"applyResult.results[{index}].kind", maximum=32)
        if kind not in MIGRATED_KINDS:
            raise PatchError(
                "graph-patch-apply-invalid",
                f"Apply result contains kind '{kind}', which this build did not migrate.",
            )
        if kind == "createVariable":
            normalized = _canonical_variable_fields(
                entry,
                f"applyResult.results[{index}]",
                error_code="graph-patch-apply-invalid",
            )
            guid = _guid_with_code(
                entry.get("guid"),
                f"applyResult.results[{index}].guid",
                error_code="graph-patch-apply-invalid",
            )
            created_variables += 1
            normalized_results.append({"opId": op_id, "kind": kind, **normalized, "guid": guid})
            continue
        if kind == "createNode":
            normalized = _canonical_node_fields(
                entry,
                f"applyResult.results[{index}]",
                error_code="graph-patch-apply-invalid",
                expect_node_guid=True,
            )
            created_nodes += 1
            normalized_results.append({"opId": op_id, "kind": kind, **normalized})
            continue
        if kind in ("connect", "disconnect"):
            try:
                normalized = _canonical_edge_apply_fields(
                    entry,
                    f"applyResult.results[{index}]",
                )
            except PatchError as exc:
                raise PatchError("graph-patch-apply-invalid", exc.message) from exc
            if kind == "connect":
                created_edges += 1
            else:
                removed_edges += 1
            normalized_results.append({"opId": op_id, "kind": kind, **normalized})
            continue
        if kind == "rewirePin":
            try:
                normalized = _canonical_rewire_fields(
                    entry,
                    f"applyResult.results[{index}]",
                    error_code="graph-patch-apply-invalid",
                )
            except PatchError as exc:
                raise PatchError("graph-patch-apply-invalid", exc.message) from exc
            normalized_results.append({"opId": op_id, "kind": kind, **normalized})
            continue
        if kind == "removeNode":
            try:
                normalized = _canonical_remove_node_fields(
                    entry,
                    f"applyResult.results[{index}]",
                    error_code="graph-patch-apply-invalid",
                )
            except PatchError as exc:
                raise PatchError("graph-patch-apply-invalid", exc.message) from exc
            removed_nodes += 1
            removed_node_edges += len(normalized["removedEdges"])
            normalized_results.append({"opId": op_id, "kind": kind, **normalized})
            continue
        normalized_results.append(
            {
                "opId": op_id,
                "kind": kind,
                "nodeGuid": _guid(entry.get("nodeGuid"), f"applyResult.results[{index}].nodeGuid"),
                "pinGuid": _guid(entry.get("pinGuid"), f"applyResult.results[{index}].pinGuid"),
            }
        )
    for key in ("nodeCountBefore", "nodeCountAfter", "linkCountBefore", "linkCountAfter"):
        if isinstance(item.get(key), bool) or not isinstance(item.get(key), int) or item.get(key) < 0:
            raise PatchError(
                "graph-patch-apply-invalid",
                f"applyResult.{key} must be a non-negative integer.",
            )
    for key in ("variableCountBefore", "variableCountAfter"):
        if isinstance(item.get(key), bool) or not isinstance(item.get(key), int) or item.get(key) < 0:
            raise PatchError(
                "graph-patch-apply-invalid",
                f"applyResult.{key} must be a non-negative integer.",
            )
    if item.get("variableCountAfter") != item.get("variableCountBefore") + created_variables:
        raise PatchError(
            "graph-patch-apply-invalid",
            "Apply variable counts must match the number of createVariable results.",
        )
    if (
        isinstance(item.get("createdNodeCount"), bool)
        or not isinstance(item.get("createdNodeCount"), int)
        or item.get("createdNodeCount") != created_nodes
        or isinstance(item.get("removedNodeCount"), bool)
        or not isinstance(item.get("removedNodeCount"), int)
        or item.get("removedNodeCount") != removed_nodes
        or isinstance(item.get("removedNodeEdgeCount"), bool)
        or not isinstance(item.get("removedNodeEdgeCount"), int)
        or item.get("removedNodeEdgeCount") != removed_node_edges
        or item.get("nodeCountAfter") != item.get("nodeCountBefore") + created_nodes - removed_nodes
    ):
        raise PatchError(
            "graph-patch-apply-invalid",
            "Apply node counts must match the createNode/removeNode results.",
        )
    if (
        isinstance(item.get("createdEdgeCount"), bool)
        or not isinstance(item.get("createdEdgeCount"), int)
        or item.get("createdEdgeCount") != created_edges
        or isinstance(item.get("removedEdgeCount"), bool)
        or not isinstance(item.get("removedEdgeCount"), int)
        or item.get("removedEdgeCount") != removed_edges
        or item.get("linkCountAfter")
        != item.get("linkCountBefore") + created_edges - removed_edges - removed_node_edges
    ):
        raise PatchError(
            "graph-patch-apply-invalid",
            "Apply link counts must match the connect/disconnect/removeNode "
            "results; a rewirePin keeps the count and is checked by the edge "
            "fingerprint.",
        )
    normalized = dict(item)
    normalized["assetPath"] = normalized_asset
    normalized["graphGuid"] = normalized_graph
    normalized["results"] = normalized_results
    return normalized


def build_save_parameters(apply_result: Any, confirmation: Any = "") -> dict[str, Any]:
    item = validate_apply_result(apply_result)
    plan_id = item["planId"]
    expected = save_confirmation_for_plan(plan_id)
    if confirmation in (None, ""):
        confirmation = expected
    if not isinstance(confirmation, str) or confirmation != expected:
        raise PatchError(
            "graph-patch-confirmation-required",
            f"Explicit Save confirmation must be exactly {expected}.",
        )
    return {
        "assetPath": item["assetPath"],
        "graphGuid": item["graphGuid"],
        "planId": plan_id,
        "expectedGraphFingerprint": item["graphFingerprintAfter"],
        "expectedStateFingerprint": item["stateFingerprintAfter"],
        "expectedNodeCount": item["nodeCountAfter"],
        "expectedLinkCount": item["linkCountAfter"],
        "confirmation": confirmation,
    }


def describe_rollback(details: Any) -> dict[str, Any]:
    """Extract the per-operation results a failed Apply carried as error details."""

    if not isinstance(details, dict):
        return {}
    results = details.get("results")
    if not isinstance(results, list):
        return {}
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(results):
        if not isinstance(raw, dict):
            continue
        item = {
            "opId": raw.get("opId"),
            "kind": raw.get("kind"),
            "status": raw.get("status"),
        }
        if isinstance(raw.get("errorCode"), str):
            item["errorCode"] = raw["errorCode"]
        if isinstance(raw.get("errorMessage"), str):
            item["errorMessage"] = raw["errorMessage"]
        if raw.get("rolledBack") is not None:
            item["rolledBack"] = raw.get("rolledBack") is True
        normalized.append(item)
    return {
        "writeOccurred": details.get("writeOccurred") is True,
        "transactionOpened": details.get("transactionOpened") is True,
        "rolledBack": details.get("rolledBack") is True,
        "results": normalized,
    }


__all__ = [
    "APPLY_CAPABILITY",
    "APPLY_CONFIRMATION_PREFIX",
    "APPLY_METHOD",
    "APPLY_SCHEMA_VERSION",
    "APPLY_TOOL_NAME",
    "CONNECT_FIELDS",
    "CREATE_NODE_FIELDS",
    "CREATE_VARIABLE_FIELDS",
    "DISCONNECT_FIELDS",
    "MIGRATED_KINDS",
    "MAX_OPERATIONS",
    "MAX_REQUEST_BYTES",
    "PREVIEW_CAPABILITY",
    "PREVIEW_METHOD",
    "PREVIEW_SCHEMA_VERSION",
    "PREVIEW_TOOL_NAME",
    "PatchError",
    "REWIRE_FIELDS",
    "SAVE_CAPABILITY",
    "SAVE_CONFIRMATION_PREFIX",
    "SAVE_METHOD",
    "SAVE_TOOL_NAME",
    "build_apply_parameters",
    "build_save_parameters",
    "confirmation_for_plan",
    "describe_rollback",
    "save_confirmation_for_plan",
    "validate_apply_result",
    "validate_preview_parameters",
    "validate_preview_result",
]
