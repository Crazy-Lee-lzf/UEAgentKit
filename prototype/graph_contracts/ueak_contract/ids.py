"""Identity grammar for Graph / Node / Pin / Edge.

Clause IDs used by the contract and the review result:

* ID-GRAPH  native ``g:<guid>``; read-only fallback ``g:name:<name>``
* ID-NODE   native ``n:<guid>``; read-only fallback ``n:obj:<object-name>``
* ID-PIN    native ``p:<guid>``; duplicate-disambiguated ``p:dup:<node-guid>:<pin-guid>``
* ID-EDGE   ``e:`` + source pin id + ``--`` + target pin id (pure function)
* ID-TEMP   ``tmp:<1..32 of [A-Za-z0-9_-]>`` (Patch-local only)
* ID-FMT    lowercase GUIDs, no braces, no uppercase hex
"""

from __future__ import annotations

import re

GUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
GRAPH_NATIVE_RE = re.compile(r"^g:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$")
GRAPH_FALLBACK_RE = re.compile(r"^g:name:([A-Za-z_][A-Za-z0-9_]{0,63})$")
NODE_NATIVE_RE = re.compile(r"^n:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$")
NODE_FALLBACK_RE = re.compile(r"^n:obj:([A-Za-z_][A-Za-z0-9_]{0,127})$")
PIN_NATIVE_RE = re.compile(r"^p:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$")
PIN_DUP_RE = re.compile(
    r"^p:dup:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}):"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$"
)
TEMP_ID_RE = re.compile(r"^tmp:[A-Za-z0-9_-]{1,32}$")
OPERATION_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
ASSET_PATH_RE = re.compile(r"^/Game/[A-Za-z0-9_./-]{1,240}$")


def is_guid(value: object) -> bool:
    return isinstance(value, str) and GUID_RE.fullmatch(value) is not None


def normalize_guid(value: str) -> str:
    """Normalise a GUID to the canonical lowercase hyphen form or raise.

    Readers may accept uppercase as input, but the canonical form (and every
    emitted identity) is lowercase; mixed case is not a second identity.
    """

    if not isinstance(value, str):
        raise ValueError("GUID must be a string")
    text = value.strip()
    if text.startswith("{") and text.endswith("}"):
        text = text[1:-1]
    lower = text.lower()
    if not is_guid(lower):
        raise ValueError(f"Invalid GUID: {value!r}")
    return lower


def graph_id(guid: str) -> str:
    return "g:" + normalize_guid(guid)


def graph_fallback_id(name: str) -> str:
    candidate = "g:name:" + name
    if GRAPH_FALLBACK_RE.fullmatch(candidate) is None:
        raise ValueError(f"Invalid graph name: {name!r}")
    return candidate


def node_id(guid: str) -> str:
    return "n:" + normalize_guid(guid)


def node_fallback_id(object_name: str) -> str:
    candidate = "n:obj:" + object_name
    if NODE_FALLBACK_RE.fullmatch(candidate) is None:
        raise ValueError(f"Invalid node object name: {object_name!r}")
    return candidate


def pin_id(guid: str) -> str:
    return "p:" + normalize_guid(guid)


def duplicate_pin_id(node_guid: str, pin_guid: str) -> str:
    return "p:dup:" + normalize_guid(node_guid) + ":" + normalize_guid(pin_guid)


def edge_id(source_pin_id: str, target_pin_id: str) -> str:
    if not is_pin_id(source_pin_id) or not is_pin_id(target_pin_id):
        raise ValueError("edge endpoints must be valid pin ids")
    return "e:" + source_pin_id + "--" + target_pin_id


def is_graph_id(value: object) -> bool:
    return isinstance(value, str) and (
        GRAPH_NATIVE_RE.fullmatch(value) is not None or GRAPH_FALLBACK_RE.fullmatch(value) is not None
    )


def is_native_graph_id(value: object) -> bool:
    return isinstance(value, str) and GRAPH_NATIVE_RE.fullmatch(value) is not None


def is_node_id(value: object) -> bool:
    return isinstance(value, str) and (
        NODE_NATIVE_RE.fullmatch(value) is not None or NODE_FALLBACK_RE.fullmatch(value) is not None
    )


def is_native_node_id(value: object) -> bool:
    return isinstance(value, str) and NODE_NATIVE_RE.fullmatch(value) is not None


def is_pin_id(value: object) -> bool:
    return isinstance(value, str) and (
        PIN_NATIVE_RE.fullmatch(value) is not None or PIN_DUP_RE.fullmatch(value) is not None
    )


def is_temp_id(value: object) -> bool:
    return isinstance(value, str) and TEMP_ID_RE.fullmatch(value) is not None


def is_operation_id(value: object) -> bool:
    return isinstance(value, str) and OPERATION_ID_RE.fullmatch(value) is not None


def is_asset_path(value: object) -> bool:
    return isinstance(value, str) and ASSET_PATH_RE.fullmatch(value) is not None


def pin_guid_from_id(value: str) -> str:
    """Return the GUID-shaped part of a native pin id; ``""`` otherwise."""

    match = PIN_NATIVE_RE.fullmatch(value) if isinstance(value, str) else None
    return match.group(1) if match else ""


def node_guid_from_id(value: str) -> str:
    match = NODE_NATIVE_RE.fullmatch(value) if isinstance(value, str) else None
    return match.group(1) if match else ""


def identity_kind(value: object) -> str:
    """Return a coarse kind used for diagnostics (never for hashing)."""

    if isinstance(value, str):
        if PIN_NATIVE_RE.fullmatch(value):
            return "pin-native"
        if PIN_DUP_RE.fullmatch(value):
            return "pin-duplicate"
        if NODE_NATIVE_RE.fullmatch(value):
            return "node-native"
        if NODE_FALLBACK_RE.fullmatch(value):
            return "node-fallback"
        if GRAPH_NATIVE_RE.fullmatch(value):
            return "graph-native"
        if GRAPH_FALLBACK_RE.fullmatch(value):
            return "graph-fallback"
        if TEMP_ID_RE.fullmatch(value):
            return "temp"
    return "unknown"


def split_edge_id(value: str) -> tuple[str, str]:
    """Return ``(source_pin_id, target_pin_id)`` or raise ``ValueError``."""

    if not isinstance(value, str) or not value.startswith("e:"):
        raise ValueError("edge id must start with 'e:'")
    body = value[2:]
    parts = body.split("--")
    if len(parts) != 2 or not is_pin_id(parts[0]) or not is_pin_id(parts[1]):
        raise ValueError(f"Invalid edge id: {value!r}")
    return parts[0], parts[1]


def is_edge_id(value: object) -> bool:
    try:
        split_edge_id(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return True
