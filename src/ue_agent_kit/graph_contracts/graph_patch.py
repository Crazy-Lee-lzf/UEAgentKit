"""GraphPatch IR 1.0 candidate: validation, planning, preview and offline simulation.

Clause IDs: GP-LIMITS, GP-IDEMPOTENCY, GP-DIRECTION, GP-ORDER, GP-NODE-HASH,
GP-EXPECT, GP-TARGET, GP-EXTERNAL-DEPS, GP-RECEIPT, TX-ROLLBACK, TX-COMPILE,
TX-PREVIEW-ZERO-WRITE.

This module never touches Unreal.  ``simulate_apply`` is an in-memory structural
simulation used to prove *contract* behaviour (ordering, zero-write preview,
reverse-op rollback, recovery marker).  It is explicitly labelled
``simulation: true`` and must not be presented as UE Editor integration.
"""

from __future__ import annotations

import copy
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from . import ids, typed_graph
from .canonical import (
    DOMAIN_ARTIFACT,
    DOMAIN_PAYLOAD,
    DOMAIN_RECEIPT,
    canonical_json,
    domain_hash,
    is_sha256,
    short_digest,
)
from .errors import ContractError, make_diagnostic
from .idempotency import GraphPatchIdempotencyStore
from .recovery import RecoveryStore, make_marker
from .schema import SchemaError, negotiate_version

GRAPH_PATCH_VERSION = "ueak.graphpatch/1.0"
GRAPH_PATCH_NAMESPACE = "ueak.graphpatch"
GRAPH_PATCH_SUPPORTED_MINOR = 0
RESULT_VERSION = "ueak.graphpatch-result/1.0"

# D-06 approved profiles.  structure-default keeps the historical 200 total.
GRAPH_PATCH_PROFILES: dict[str, dict[str, Any]] = {
    "structure-default": {"total": 200, "create": 100, "connect": 150, "position": 50, "allowMixed": True},
    "structure-g2": {"total": 300, "create": 100, "connect": 200, "position": 50, "allowMixed": True},
    "layout-only": {"total": 1000, "create": 0, "connect": 0, "position": 1000, "allowMixed": False},
}
DEFAULT_GRAPH_PATCH_PROFILE = "structure-default"
V1_APPLY_GRAPH_KINDS = ("event", "function", "macro")

OPERATION_TYPES = (
    "createNode",
    "connectPins",
    "disconnectPins",
    "setPinDefault",
    "setNodePosition",
    "setNodeComment",
    "setNodeEnabled",
    "setCommentBounds",
)
MAX_OPERATIONS_PER_PATCH = GRAPH_PATCH_PROFILES[DEFAULT_GRAPH_PATCH_PROFILE]["total"]
MAX_CREATE_NODE_PER_PATCH = GRAPH_PATCH_PROFILES[DEFAULT_GRAPH_PATCH_PROFILE]["create"]
# Historical constant: the layout-only ceiling.  The default structure profile's
# position cap is 50 (D-06).
MAX_POSITION_OPS_PER_PATCH = GRAPH_PATCH_PROFILES["layout-only"]["position"]
MAX_PATCH_BYTES = 4 * 1024 * 1024

VALUE_TYPES_BY_CATEGORY = {
    "bool": ("bool", "boolean"),
    "boolean": ("bool", "boolean"),
    "byte": ("byte", "int"),
    "int": ("int", "int64", "byte"),
    "int64": ("int64", "int"),
    "float": ("float", "double", "real"),
    "double": ("double", "float", "real"),
    "real": ("real", "float", "double"),
    "string": ("string", "text", "name"),
    "name": ("name", "string"),
    "text": ("text", "string"),
    "object": ("object", "class", "softobject"),
    "class": ("class", "object", "softclass"),
    "struct": ("struct",),
    "enum": ("enum", "byte"),
}
EVIDENCE_REQUIRED = ("plan", "apply-report", "typedgraph-before", "typedgraph-after", "receipt")


@dataclass
class Environment:
    actual_revision: str = ""
    dirty: bool = False
    pie_active: bool = False
    editor_session_id: str = ""
    recovery_pending: bool = False
    asset_loaded: bool = True
    graph_found: bool = True
    graph_schema_supported: bool = True
    sidecar_files: list[str] = field(default_factory=list)
    compile_result: dict[str, Any] | None = None
    save_error: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "Environment":
        values = dict(payload or {})
        aliases = {
            "actualRevision": "actual_revision",
            "pieActive": "pie_active",
            "editorSessionId": "editor_session_id",
            "recoveryPending": "recovery_pending",
            "assetLoaded": "asset_loaded",
            "graphFound": "graph_found",
            "graphSchemaSupported": "graph_schema_supported",
            "sidecarFiles": "sidecar_files",
            "compileResult": "compile_result",
            "saveError": "save_error",
        }
        known = {field_name for field_name in cls.__dataclass_fields__}
        normalised: dict[str, Any] = {}
        for key, value in values.items():
            canonical = aliases.get(key, key)
            if canonical in known:
                normalised[canonical] = value
        return cls(**normalised)


@dataclass
class PatchReport:
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)

    def error(self, code: str, message: str, *, path: str = "$", context: dict[str, Any] | None = None) -> None:
        self.errors.append(make_diagnostic(code, message, path=path, context=context))

    def warn(self, code: str, message: str, *, path: str = "$", context: dict[str, Any] | None = None) -> None:
        self.warnings.append(make_diagnostic(code, message, path=path, severity="warning", context=context))

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def primary_code(self) -> str:
        return self.errors[0]["code"] if self.errors else ""

    @property
    def codes(self) -> list[str]:
        return [item["code"] for item in self.errors] + [item["code"] for item in self.warnings]


@dataclass
class PatchPlan:
    report: PatchReport
    payload_hash: str = ""
    ordered_operations: list[dict[str, Any]] = field(default_factory=list)
    predicted: dict[str, Any] = field(default_factory=dict)
    temp_nodes: dict[str, dict[str, Any]] = field(default_factory=dict)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _artifact(path: str, payload: Any) -> dict[str, Any]:
    text = canonical_json(payload)
    return {
        "path": path,
        "sha256": domain_hash(payload, DOMAIN_ARTIFACT),
        "bytes": len(text.encode("utf-8")),
        "createdAtUtc": _utc_now(),
    }


def _stage_receipt(phase: str, payload: Any) -> dict[str, Any]:
    return {
        "phase": phase,
        "receiptSha256": domain_hash({"phase": phase, "payload": payload}, DOMAIN_RECEIPT),
        "createdAtUtc": _utc_now(),
    }


def _load_descriptors(descriptors: Iterable[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    for descriptor in descriptors or []:
        from .descriptors import make_action_id

        action_id = descriptor.get("actionId") or make_action_id(descriptor.get("discoveryKey", {}))
        catalog[str(action_id)] = descriptor
    return catalog


def patch_profile(patch: dict[str, Any]) -> str:
    target = patch.get("target") if isinstance(patch.get("target"), dict) else {}
    return str(target.get("graphpatchProfile", DEFAULT_GRAPH_PATCH_PROFILE))


def operation_counts(operations: Iterable[Any]) -> dict[str, int]:
    counts = {"create": 0, "connect": 0, "position": 0, "other": 0, "total": 0}
    for operation in operations:
        if not isinstance(operation, dict):
            counts["other"] += 1
            counts["total"] += 1
            continue
        operation_type = operation.get("type")
        counts["total"] += 1
        if operation_type == "createNode":
            counts["create"] += 1
        elif operation_type in ("connectPins", "disconnectPins"):
            counts["connect"] += 1
        elif operation_type == "setNodePosition":
            counts["position"] += 1
        else:
            counts["other"] += 1
    return counts


def validate_profile_limits(patch: dict[str, Any], profile_name: str, report: "PatchReport") -> dict[str, int]:
    """D-06: one explicit profile governs total and per-class limits.

    The total limit is authoritative and is checked before per-class limits.
    ``layout-only`` forbids create/connect operations (no mixed layout work).
    """

    profile = GRAPH_PATCH_PROFILES.get(profile_name)
    if profile is None:
        report.error(
            "graphpatch-profile-invalid",
            f"Unknown graphpatchProfile: {profile_name!r}",
            path="$.target.graphpatchProfile",
            context={"allowed": sorted(GRAPH_PATCH_PROFILES)},
        )
        return operation_counts(patch.get("operations", []))
    counts = operation_counts(patch.get("operations", []))
    if not profile["allowMixed"] and (counts["create"] or counts["connect"]):
        report.error(
            "graphpatch-limit-exceeded",
            "layout-only profile forbids createNode/connectPins/disconnectPins (mixed layout)",
            path="$.target.graphpatchProfile",
            context={"profile": profile_name, "operationCounts": counts},
        )
    if counts["total"] > profile["total"]:
        report.error(
            "graphpatch-limit-exceeded",
            f"operations exceed profile {profile_name} total {profile['total']}",
            path="$.operations",
            context={"profile": profile_name, "operationCounts": counts, "limits": dict(profile)},
        )
    for key, limit_key in (("create", "create"), ("connect", "connect"), ("position", "position")):
        if counts[key] > profile[limit_key]:
            report.error(
                "graphpatch-limit-exceeded",
                f"{key} operations exceed profile {profile_name} limit {profile[limit_key]}",
                path="$.operations",
                context={"profile": profile_name, "operationCounts": counts, "limits": dict(profile)},
            )
    return counts


def compile_result_failure(environment: "Environment") -> str:
    """D-11: only a trusted error result fails compilation; missing is fail-closed."""

    compile_result = environment.compile_result
    if not isinstance(compile_result, dict):
        return "graphpatch-compile-result-missing"
    result = compile_result.get("result")
    succeeded = compile_result.get("succeeded")
    diagnostics = compile_result.get("diagnostics")
    if result not in ("success", "success-with-warnings", "error"):
        return "graphpatch-compile-result-missing"
    if not isinstance(succeeded, bool):
        return "graphpatch-compile-result-missing"
    if not isinstance(diagnostics, list):
        return "graphpatch-compile-result-missing"
    if not succeeded or result == "error":
        return "graphpatch-compile-failed"
    return ""


def compile_warnings(environment: "Environment") -> list[dict[str, Any]]:
    compile_result = environment.compile_result if isinstance(environment.compile_result, dict) else {}
    diagnostics = compile_result.get("diagnostics") if isinstance(compile_result.get("diagnostics"), list) else []
    warnings: list[dict[str, Any]] = []
    for item in diagnostics:
        if isinstance(item, dict) and str(item.get("severity", "")).lower() == "warning":
            warnings.append(
                make_diagnostic(
                    "graphpatch-compile-warning",
                    str(item.get("message", "compiler warning")),
                    path="$.compileResult.diagnostics",
                    severity="warning",
                )
            )
    return warnings


def payload_hash(patch: dict[str, Any]) -> str:
    projection = {
        "schemaVersion": patch.get("schemaVersion", ""),
        "projectName": patch.get("projectName", ""),
        "target": patch.get("target", {}),
        "conflictPolicy": patch.get("conflictPolicy", ""),
        "externalDependencies": patch.get("externalDependencies", []),
        "operations": patch.get("operations", []),
        "layout": patch.get("layout"),
        "expect": patch.get("expect"),
    }
    return domain_hash(projection, DOMAIN_PAYLOAD)


class IdempotencyLedger:
    """Abstract ledger.  Persistence is D-07 (unapproved); this in-memory store is test-only."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str, str, str], dict[str, Any]] = {}

    @staticmethod
    def scope(patch: dict[str, Any]) -> tuple[str, str, str, str]:
        target = patch.get("target") or {}
        return (
            str(patch.get("projectName", "")),
            str(target.get("assetPath", "")),
            str(target.get("graphId", "")),
            str(patch.get("idempotencyKey", "")),
        )

    def check(self, patch: dict[str, Any], digest: str) -> tuple[str, dict[str, Any] | None]:
        record = self._records.get(self.scope(patch))
        if record is None:
            return "new", None
        if record["payloadHash"] != digest:
            return "conflict", record
        return "replay", record

    def store(self, patch: dict[str, Any], digest: str, result: dict[str, Any]) -> None:
        self._records[self.scope(patch)] = {
            "payloadHash": digest,
            "result": copy.deepcopy(result),
            "storedAtUtc": _utc_now(),
        }


def _precedence_rank(code: str) -> int:
    order = (
        "graphpatch-schema-version-unsupported",
        "graphpatch-target-asset-mismatch",
        "graphpatch-asset-not-loaded",
        "graphpatch-target-graph-mismatch",
        "graphpatch-graph-kind-not-allowed",
        "graphpatch-graph-not-found",
        "graphpatch-editor-session-mismatch",
        "graphpatch-editor-unavailable",
        "graphpatch-graph-schema-unsupported",
        "graphpatch-pie-active",
        "graphpatch-package-dirty",
        "graphpatch-recovery-required",
        "graphpatch-base-revision-stale",
        "graphpatch-base-graph-hash-stale",
        "graphpatch-profile-invalid",
        "graphpatch-patch-empty",
        "graphpatch-limit-exceeded",
        "graphpatch-operation-order-conflict",
        "graphpatch-pin-identity-unstable",
        "graphpatch-confirmation-required",
    )
    return order.index(code) if code in order else 100


def _finalize(report: PatchReport) -> None:
    report.errors.sort(key=lambda item: (_precedence_rank(item["code"]), item["path"]))


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _pin_type(pin: dict[str, Any]) -> dict[str, Any]:
    return typed_graph._pin_type(pin)  # shared projection keeps hash/validation aligned


def _types_compatible(source: dict[str, Any], target: dict[str, Any]) -> bool:
    if source.get("kind") != target.get("kind"):
        return False
    if source.get("kind") == "exec":
        return True
    source_type = _pin_type(source)
    target_type = _pin_type(target)
    if source_type["category"] in ("", "wildcard") or target_type["category"] in ("", "wildcard"):
        return True
    if source_type["category"] != target_type["category"]:
        return False
    if source_type["subCategory"] and target_type["subCategory"] and source_type["subCategory"] != target_type["subCategory"]:
        return False
    if (
        source_type["subCategoryObject"]
        and target_type["subCategoryObject"]
        and source_type["subCategoryObject"] != target_type["subCategoryObject"]
    ):
        return False
    if source_type["container"] != target_type["container"]:
        return False
    return True


def _value_type_compatible(pin: dict[str, Any], value_type: str) -> bool:
    category = _pin_type(pin)["category"]
    if category == "exec":
        return False
    if category in ("", "wildcard"):
        return True
    allowed = VALUE_TYPES_BY_CATEGORY.get(category)
    if allowed is None:
        return value_type in ("string", "text", "name")
    return value_type in allowed


class _WorkingGraph:
    def __init__(self, graph: dict[str, Any]) -> None:
        self.graph = copy.deepcopy(graph)
        self.graph.setdefault("nodes", [])
        self.graph.setdefault("pins", [])
        self.graph.setdefault("edges", [])
        self.node_by_id: dict[str, dict[str, Any]] = {str(node["nodeId"]): node for node in self.graph["nodes"]}
        self.pin_by_id: dict[str, dict[str, Any]] = {str(pin["pinId"]): pin for pin in self.graph["pins"]}
        self.pins_by_node: dict[str, list[dict[str, Any]]] = {}
        for pin in self.graph["pins"]:
            self.pins_by_node.setdefault(str(pin.get("nodeId", "")), []).append(pin)
        self.edge_by_id: dict[str, dict[str, Any]] = {str(edge["edgeId"]): edge for edge in self.graph["edges"]}
        self.temp_map: dict[str, str] = {}
        self.pin_id_map: dict[str, str] = {}

    def snapshot(self) -> str:
        return typed_graph.semantic_hash(self.graph)

    def add_node(self, node: dict[str, Any], pins: list[dict[str, Any]]) -> None:
        self.graph["nodes"].append(node)
        self.node_by_id[node["nodeId"]] = node
        for pin in pins:
            self.graph["pins"].append(pin)
            self.pin_by_id[pin["pinId"]] = pin
            self.pins_by_node.setdefault(pin["nodeId"], []).append(pin)
            node.setdefault("pins", []).append(pin["pinId"])

    def remove_node(self, node_id_value: str) -> None:
        node = self.node_by_id[node_id_value]
        incident = {
            str(edge.get("edgeId"))
            for edge in self.graph["edges"]
            if (edge.get("from") or {}).get("nodeId") == node_id_value
            or (edge.get("to") or {}).get("nodeId") == node_id_value
        }
        for edge_id_value in sorted(incident):
            if edge_id_value in self.edge_by_id:
                self.remove_edge(edge_id_value)
        pin_ids = set(node.get("pins", []))
        self.graph["nodes"] = [item for item in self.graph["nodes"] if item.get("nodeId") != node_id_value]
        self.graph["pins"] = [item for item in self.graph["pins"] if item.get("pinId") not in pin_ids]
        for pin_id_value in pin_ids:
            self.pin_by_id.pop(pin_id_value, None)
        self.pins_by_node.pop(node_id_value, None)
        self.rebuild_indexes()

    def rebuild_indexes(self) -> None:
        self.node_by_id = {str(node["nodeId"]): node for node in self.graph["nodes"]}
        self.pin_by_id = {str(pin["pinId"]): pin for pin in self.graph["pins"]}
        self.pins_by_node = {}
        for pin in self.graph["pins"]:
            self.pins_by_node.setdefault(str(pin.get("nodeId", "")), []).append(pin)
        self.edge_by_id = {str(edge["edgeId"]): edge for edge in self.graph["edges"]}

    def add_edge(self, edge: dict[str, Any]) -> None:
        self.graph["edges"].append(edge)
        self.edge_by_id[edge["edgeId"]] = edge
        source = edge["from"]["pinId"]
        target = edge["to"]["pinId"]
        for pin_id_value, link in ((source, target), (target, source)):
            pin = self.pin_by_id.get(pin_id_value)
            if pin is not None:
                links = set(pin.get("links", []))
                links.add(link)
                pin["links"] = sorted(links)

    def remove_edge(self, edge_id_value: str) -> dict[str, Any]:
        edge = self.edge_by_id.pop(edge_id_value)
        self.graph["edges"] = [item for item in self.graph["edges"] if item.get("edgeId") != edge_id_value]
        for pin_id_value, link in (
            (edge["from"]["pinId"], edge["to"]["pinId"]),
            (edge["to"]["pinId"], edge["from"]["pinId"]),
        ):
            pin = self.pin_by_id.get(pin_id_value)
            if pin is not None:
                pin["links"] = sorted(set(pin.get("links", [])) - {link})
        return edge

    def incoming_edges(self, pin_id_value: str) -> list[dict[str, Any]]:
        return [edge for edge in self.graph["edges"] if (edge.get("to") or {}).get("pinId") == pin_id_value]

    def incident_edges(self, node_id_value: str) -> list[dict[str, Any]]:
        return [
            edge
            for edge in self.graph["edges"]
            if (edge.get("from") or {}).get("nodeId") == node_id_value
            or (edge.get("to") or {}).get("nodeId") == node_id_value
        ]


def _resolve_node(working: _WorkingGraph, node_ref: str) -> str | None:
    if node_ref in working.temp_map:
        return working.temp_map[node_ref]
    if node_ref in working.node_by_id:
        return node_ref
    return None


def _resolve_pin(
    working: _WorkingGraph,
    ref: Any,
    required_direction: str,
    *,
    report: PatchReport,
    path: str,
) -> dict[str, Any] | None:
    if not isinstance(ref, dict):
        report.error("contract-schema-type-mismatch", "pin reference must be an object", path=path)
        return None
    explicit_direction = ref.get("direction")
    if explicit_direction is not None and explicit_direction not in typed_graph.PIN_DIRECTIONS:
        report.error("graphpatch-pin-direction-mismatch", "direction must be input/output", path=f"{path}.direction")
        return None
    if explicit_direction is not None and explicit_direction != required_direction:
        report.error(
            "graphpatch-pin-direction-mismatch",
            f"This reference must be {required_direction}; explicit direction {explicit_direction!r} conflicts",
            path=f"{path}.direction",
        )
        return None

    if ref.get("pinId"):
        pin = working.pin_by_id.get(str(ref["pinId"]))
        if pin is None:
            report.error("graphpatch-pin-not-found", f"Unknown pinId: {ref['pinId']}", path=f"{path}.pinId")
            return None
        if ref.get("node") and _resolve_node(working, str(ref["node"])) != pin.get("nodeId"):
            report.error("graphpatch-pin-not-found", "pinId does not belong to the referenced node", path=path)
            return None
        if pin.get("direction") != required_direction:
            report.error(
                "graphpatch-pin-direction-mismatch",
                f"Pin {pin.get('pinId')} is {pin.get('direction')}, expected {required_direction}",
                path=path,
            )
            return None
        return pin

    node_ref = ref.get("node")
    if not isinstance(node_ref, str):
        report.error("graphpatch-temp-id-unknown", "pin reference requires node or pinId", path=path)
        return None
    node_id_value = _resolve_node(working, node_ref)
    if node_id_value is None:
        if ids.is_temp_id(node_ref):
            report.error("graphpatch-temp-id-unknown", f"Undefined tempId: {node_ref}", path=f"{path}.node")
        else:
            report.error("graphpatch-node-not-found", f"Unknown node: {node_ref}", path=f"{path}.node")
        return None
    name = ref.get("pin")
    if not isinstance(name, str) or not name:
        report.error("contract-schema-required", "pin reference requires pin name", path=f"{path}.pin")
        return None
    candidates = [pin for pin in working.pins_by_node.get(node_id_value, []) if pin.get("name") == name]
    directional = [pin for pin in candidates if pin.get("direction") == required_direction]
    if not directional:
        code = "graphpatch-pin-direction-mismatch" if candidates else "graphpatch-pin-not-found"
        report.error(code, f"Pin {name!r} ({required_direction}) not found on {node_ref}", path=path)
        return None
    if len(directional) > 1:
        if not _is_int(ref.get("pinIndex")):
            report.error(
                "graphpatch-pin-ambiguous",
                f"Pin {name!r} has {len(directional)} candidates; pinIndex is required",
                path=path,
            )
            return None
        indexed = [pin for pin in directional if pin.get("index") == ref.get("pinIndex")]
        if len(indexed) != 1:
            report.error("graphpatch-pin-not-found", "pinIndex did not match a candidate", path=f"{path}.pinIndex")
            return None
        return indexed[0]
    return directional[0]


def _check_derived_pin(pin: dict[str, Any], *, operation_type: str, allow_derived: bool, report: PatchReport, path: str) -> None:
    if pin.get("pinIdSource") != "derived":
        return
    if operation_type == "setPinDefault" and allow_derived:
        return
    report.error(
        "graphpatch-pin-identity-unstable",
        "Derived PinId (promotable ErrorTolerance transient) may only be used by setPinDefault "
        "with allowDerivedPinIdentity=true",
        path=path,
        context={"pinId": pin.get("pinId")},
    )


def _instantiate_node(
    descriptor: dict[str, Any],
    *,
    patch_id: str,
    temp_id: str,
    position: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, str]]:
    node_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"ueak-offline-simulation:{patch_id}:{temp_id}"))
    node_id_value = "n:" + node_uuid
    class_path = str((descriptor.get("discoveryKey") or {}).get("nodeClass", ""))
    node = {
        "nodeId": node_id_value,
        "nodeGuid": node_uuid,
        "objectPath": "",
        "classPath": class_path,
        "title": descriptor.get("title", ""),
        "kind": typed_graph.classify_node_class(class_path),
        "nodeIdSource": "guid",
        "flags": {
            "enabled": True,
            "pure": bool((descriptor.get("constraints") or {}).get("pure", False)),
            "reroute": False,
            "comment": False,
            "latent": bool((descriptor.get("constraints") or {}).get("latent", False)),
        },
        "position": {"x": int((position or {}).get("x", 0)), "y": int((position or {}).get("y", 0))},
        "size": {"width": 0, "height": 0, "source": "unknown"},
        "pins": [],
    }
    pins: list[dict[str, Any]] = []
    pin_id_map: dict[str, str] = {}
    for index, template in enumerate(descriptor.get("pins", [])):
        name = str(template.get("name", ""))
        direction = str(template.get("direction", "input"))
        pin_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"ueak-offline-simulation:{patch_id}:{temp_id}:{name}:{direction}:{index}"))
        pin_type = dict(template.get("type") or {})
        for field_name in typed_graph.PIN_TYPE_FIELDS:
            pin_type.setdefault(field_name, False if field_name.startswith("is") else "")
        kind = "exec" if pin_type.get("category") == "exec" or template.get("kind") == "exec" else "data"
        pin = {
            "pinId": "p:" + pin_uuid,
            "nodeId": node_id_value,
            "name": name,
            "friendlyName": "",
            "direction": direction,
            "kind": kind,
            "type": pin_type,
            "index": index,
            "defaults": {"value": str(template.get("default", "")), "object": "", "text": ""},
            "flags": {
                "hidden": bool(template.get("hidden", False)),
                "advanced": bool(template.get("advanced", False)),
                "connectable": bool(template.get("connectable", True)),
                "orphaned": False,
                "defaultReadOnly": bool(template.get("defaultReadOnly", False)),
                "defaultIgnored": bool(template.get("defaultIgnored", False)),
            },
            "links": [],
            "pinIdSource": "native",
            "pinGuidTransient": False,
        }
        pins.append(pin)
        pin_id_map[f"{temp_id}:{name}:{direction}:{index}"] = pin["pinId"]
    return node, pins, pin_id_map


def _validate_patch_root(patch: dict[str, Any], report: PatchReport) -> None:
    if not isinstance(patch, dict):
        report.error("contract-schema-type-mismatch", "GraphPatch must be a JSON object", path="$")
        return
    try:
        decision = negotiate_version(
            patch.get("schemaVersion", ""),
            namespace=GRAPH_PATCH_NAMESPACE,
            supported_minor=GRAPH_PATCH_SUPPORTED_MINOR,
            unsupported_code="graphpatch-schema-version-unsupported",
        )
        report.warnings.extend(decision.diagnostics)
    except SchemaError as exc:
        code = "graphpatch-schema-version-unsupported" if exc.code == "contract-schema-invalid" else exc.code
        report.error(code, exc.message, path=exc.path)
    for field_name, max_length in (("patchId", 64), ("idempotencyKey", 128), ("projectName", 128)):
        value = patch.get(field_name)
        if not isinstance(value, str) or not value or len(value) > max_length:
            report.error("contract-schema-required", f"{field_name} must be a non-empty string <= {max_length}", path=f"$.{field_name}")
    if patch.get("conflictPolicy") != "reject-if-changed":
        report.error("graphpatch-operation-not-allowed", "conflictPolicy must be 'reject-if-changed' in V1", path="$.conflictPolicy")
    if not isinstance(patch.get("externalDependencies", []), list):
        report.error("contract-schema-type-mismatch", "externalDependencies must be an array", path="$.externalDependencies")
    for index, dependency in enumerate(patch.get("externalDependencies", []) or []):
        if not isinstance(dependency, dict) or dependency.get("kind") not in ("asset", "changeSet", "editorSession"):
            report.error("contract-schema-enum-mismatch", "external dependency kind is invalid", path=f"$.externalDependencies[{index}]")
        elif not isinstance(dependency.get("ref"), str) or not dependency.get("ref"):
            report.error("contract-schema-required", "external dependency requires ref", path=f"$.externalDependencies[{index}].ref")
    target = patch.get("target")
    if not isinstance(target, dict):
        report.error("contract-schema-required", "target is required", path="$.target")
        return
    if not ids.is_asset_path(target.get("assetPath")):
        report.error("graphpatch-target-asset-mismatch", "target.assetPath must be a /Game/... object path", path="$.target.assetPath")
    if not ids.is_native_graph_id(target.get("graphId")):
        report.error("graphpatch-target-graph-mismatch", "target.graphId must be native g:<guid> for Apply", path="$.target.graphId")
    graph_guid = target.get("graphGuid", "")
    if graph_guid and not ids.is_guid(graph_guid):
        report.error("graphpatch-target-graph-mismatch", "target.graphGuid must be a lowercase GUID", path="$.target.graphGuid")
    if graph_guid and target.get("graphId") != "g:" + graph_guid:
        report.error("graphpatch-target-graph-mismatch", "target.graphId and target.graphGuid disagree", path="$.target.graphId")
    if target.get("graphKind") and target.get("graphKind") not in V1_APPLY_GRAPH_KINDS:
        report.error(
            "graphpatch-graph-kind-not-allowed",
            f"V1 Apply does not accept graphKind={target.get('graphKind')!r}; EventGraph must be exported as 'event'",
            path="$.target.graphKind",
            context={"allowed": list(V1_APPLY_GRAPH_KINDS)},
        )
    profile_name = target.get("graphpatchProfile", DEFAULT_GRAPH_PATCH_PROFILE)
    if profile_name not in GRAPH_PATCH_PROFILES:
        report.error(
            "graphpatch-profile-invalid",
            f"Unknown graphpatchProfile: {profile_name!r}",
            path="$.target.graphpatchProfile",
            context={"allowed": sorted(GRAPH_PATCH_PROFILES)},
        )
    if target.get("revisionPolicy") not in typed_graph.REVISION_POLICIES:
        report.error("graph-invalid-revision", "target.revisionPolicy is missing or unknown", path="$.target.revisionPolicy")
    base_revision = target.get("baseRevision", "")
    if base_revision != "" and not is_sha256(base_revision):
        report.error("graph-invalid-revision", "target.baseRevision must be empty or sha256:<64 hex>", path="$.target.baseRevision")
    if target.get("revisionPolicy") == "index-snapshot" and not base_revision:
        report.error("graphpatch-base-revision-stale", "index-snapshot requires a concrete baseRevision", path="$.target.baseRevision")
    if not is_sha256(target.get("baseGraphHash")):
        report.error("graphpatch-base-graph-hash-stale", "target.baseGraphHash must be sha256:<64 hex>", path="$.target.baseGraphHash")
    if not isinstance(target.get("editorSessionId", ""), str):
        report.error("contract-schema-type-mismatch", "target.editorSessionId must be a string", path="$.target.editorSessionId")
    if not isinstance(patch.get("operations"), list):
        report.error("graphpatch-patch-empty", "operations must be an array", path="$.operations")
    elif not patch["operations"]:
        report.error("graphpatch-patch-empty", "A GraphPatch must contain at least one operation", path="$.operations")
    if not isinstance(patch.get("metadata", {}), dict):
        report.error("contract-schema-type-mismatch", "metadata must be an object", path="$.metadata")


def _validate_environment(patch: dict[str, Any], graph: dict[str, Any], env: Environment, report: PatchReport, recovery: RecoveryStore | None) -> None:
    target = patch.get("target") or {}
    if not env.asset_loaded:
        report.error("graphpatch-asset-not-loaded", "Target asset is not loaded", path="$.target.assetPath")
    if not env.graph_found:
        report.error("graphpatch-graph-not-found", "Target graph was not found", path="$.target.graphId")
    if not env.graph_schema_supported:
        report.error("graphpatch-graph-schema-unsupported", "Target graph schema is not in the V1 allowlist", path="$.target.graphId")
    if target.get("assetPath") != (graph.get("asset") or {}).get("path"):
        report.error("graphpatch-target-asset-mismatch", "target.assetPath does not match the loaded asset", path="$.target.assetPath")
    if target.get("graphId") != graph.get("graphId"):
        report.error("graphpatch-target-graph-mismatch", "target.graphId does not match the loaded graph", path="$.target.graphId")
    if target.get("graphGuid") and target.get("graphGuid") != (graph.get("graph") or {}).get("guid"):
        report.error("graphpatch-target-graph-mismatch", "target.graphGuid does not match the loaded graph", path="$.target.graphGuid")
    if target.get("graphName") and target.get("graphName") != (graph.get("graph") or {}).get("name"):
        report.error("graphpatch-target-graph-mismatch", "target.graphName does not match the loaded graph", path="$.target.graphName")
    if target.get("graphKind") and target.get("graphKind") != (graph.get("graph") or {}).get("kind"):
        report.error("graphpatch-target-graph-mismatch", "target.graphKind does not match the loaded graph", path="$.target.graphKind")
    if env.editor_session_id and target.get("editorSessionId") and env.editor_session_id != target.get("editorSessionId"):
        report.error(
            "graphpatch-editor-session-mismatch",
            "Editor session changed since Preview; re-run Preview",
            path="$.target.editorSessionId",
        )
    if env.pie_active:
        report.error("graphpatch-pie-active", "PIE/SIE is active; GraphPatch Apply is rejected", path="$")
    if env.dirty:
        report.error("graphpatch-package-dirty", "Target package is dirty and is not part of a continuing transaction", path="$.target.assetPath")
    pending = env.recovery_pending
    if not pending and recovery is not None:
        pending = recovery.is_pending(str(patch.get("projectName", "")), str(target.get("assetPath", "")))
    if pending:
        report.error(
            "graphpatch-recovery-required",
            "A pending recovery marker blocks Apply for this asset",
            path="$.target.assetPath",
        )
    if env.sidecar_files:
        report.error(
            "graphpatch-target-asset-mismatch",
            "Target has sidecar package files; hashing only the main .uasset is not allowed",
            path="$.target.assetPath",
            context={"sidecarFiles": list(env.sidecar_files)},
        )
    policy = target.get("revisionPolicy")
    base_revision = target.get("baseRevision", "")
    if policy == "index-snapshot":
        if not base_revision or base_revision != env.actual_revision:
            report.error(
                "graphpatch-base-revision-stale",
                "Disk revision changed since the index snapshot",
                path="$.target.baseRevision",
                context={"expected": base_revision, "actual": env.actual_revision},
            )
    elif policy == "executor-computed":
        if base_revision and base_revision != env.actual_revision:
            report.error(
                "graphpatch-base-revision-stale",
                "Disk revision changed before executor computed the current hash",
                path="$.target.baseRevision",
                context={"expected": base_revision, "actual": env.actual_revision},
            )
    if target.get("baseGraphHash") != typed_graph.semantic_hash(graph):
        report.error(
            "graphpatch-base-graph-hash-stale",
            "semanticHash changed since Preview; re-export the graph",
            path="$.target.baseGraphHash",
            context={"expected": target.get("baseGraphHash"), "actual": typed_graph.semantic_hash(graph)},
        )
    graph_report = typed_graph.validate(graph, require_hashes=True, require_apply_ready=True)
    if not graph_report.ok:
        for diagnostic in graph_report.errors:
            report.error(diagnostic["code"], diagnostic["message"], path=diagnostic["path"])


def _operation_node_refs(operation: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for key in ("from", "to", "pin"):
        value = operation.get(key)
        if isinstance(value, dict) and isinstance(value.get("node"), str):
            refs.append(value["node"])
    for key in ("node", "nodeId", "tempId"):
        value = operation.get(key)
        if isinstance(value, str):
            refs.append(value)
    return refs


def _topological_sort(operations: list[dict[str, Any]], report: PatchReport) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    order_index: dict[str, int] = {}
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            report.error("contract-schema-type-mismatch", "each operation must be a JSON object", path=f"$.operations[{index}]")
            continue
        operation_id = operation.get("operationId")
        if not isinstance(operation_id, str) or not ids.is_operation_id(operation_id):
            report.error("graphpatch-operation-duplicate-id", "operationId must be a stable non-empty id", path=f"$.operations[{index}].operationId")
            continue
        if operation_id in by_id:
            report.error("graphpatch-operation-duplicate-id", f"Duplicate operationId: {operation_id}", path=f"$.operations[{index}].operationId")
            continue
        by_id[operation_id] = operation
        order_index[operation_id] = index

    temp_owner: dict[str, str] = {}
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        if operation.get("type") != "createNode":
            continue
        temp_id = operation.get("tempId")
        if not isinstance(temp_id, str) or not ids.is_temp_id(temp_id):
            report.error("graphpatch-temp-id-invalid", f"Invalid tempId: {temp_id!r}", path=f"$.operations[?(@.operationId=='{operation.get('operationId')}')].tempId")
            continue
        if temp_id in temp_owner:
            report.error("graphpatch-temp-id-invalid", f"Duplicate tempId: {temp_id}", path=f"$.operations[?(@.operationId=='{operation.get('operationId')}')].tempId")
            continue
        temp_owner[temp_id] = str(operation.get("operationId"))

    dependencies: dict[str, set[str]] = {operation_id: set() for operation_id in by_id}
    for operation_id, operation in by_id.items():
        depends = operation.get("dependsOn", [])
        if not isinstance(depends, list):
            report.error("graphpatch-dependency-unknown", "dependsOn must be an array", path=f"$.operations[?(@.operationId=='{operation_id}')].dependsOn")
            continue
        for dependency in depends:
            if dependency not in by_id:
                report.error("graphpatch-dependency-unknown", f"Unknown dependency: {dependency!r}", path=f"$.operations[?(@.operationId=='{operation_id}')].dependsOn")
            elif dependency == operation_id:
                report.error("graphpatch-dependency-cycle", "An operation cannot depend on itself", path=f"$.operations[?(@.operationId=='{operation_id}')].dependsOn")
            else:
                dependencies[operation_id].add(dependency)
        for ref in _operation_node_refs(operation):
            if ref in temp_owner and temp_owner[ref] != operation_id:
                dependencies[operation_id].add(temp_owner[ref])

    indegree = {operation_id: len(deps) for operation_id, deps in dependencies.items()}
    dependents: dict[str, set[str]] = {operation_id: set() for operation_id in by_id}
    for operation_id, deps in dependencies.items():
        for dependency in deps:
            dependents[dependency].add(operation_id)
    ready = sorted(
        (order_index[operation_id], operation_id)
        for operation_id, degree in indegree.items()
        if degree == 0
    )
    ordered: list[dict[str, Any]] = []
    while ready:
        _, operation_id = ready.pop(0)
        ordered.append(by_id[operation_id])
        for dependent in sorted(dependents[operation_id], key=lambda item: order_index[item]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append((order_index[dependent], dependent))
        ready.sort()
    if len(ordered) != len(by_id):
        report.error("graphpatch-dependency-cycle", "Operation dependencies contain a cycle", path="$.operations")
        ordered = [by_id[operation_id] for operation_id in sorted(by_id, key=lambda item: order_index[item])]
    return ordered


def _validate_operations(
    patch: dict[str, Any],
    graph: dict[str, Any],
    descriptors: dict[str, dict[str, Any]],
    report: PatchReport,
) -> tuple[list[dict[str, Any]], _WorkingGraph, dict[str, Any]]:
    validate_profile_limits(patch, patch_profile(patch), report)

    ordered = _topological_sort(patch.get("operations", []), report)
    working = _WorkingGraph(graph)
    node_count_before = len(working.graph["nodes"])
    edge_count_before = len(working.graph["edges"])
    default_count_before = sum(
        1 for pin in working.graph["pins"] if pin.get("defaults", {}).get("value", "") != ""
    )
    temp_nodes: dict[str, dict[str, Any]] = {}
    allow_derived = bool(patch.get("allowDerivedPinIdentity", False))

    for index, operation in enumerate(ordered):
        operation_type = operation.get("type")
        path = f"$.operations[?(@.operationId=='{operation.get('operationId')}')]"
        if operation_type == "deleteNode":
            report.error(
                "graphpatch-operation-not-allowed",
                "V1 does not support deleteNode (D-08 approved); delete capability is deferred to a later milestone",
                path=f"{path}.type",
            )
            continue
        if operation_type not in OPERATION_TYPES:
            report.error("graphpatch-operation-unknown", f"Unknown operation type: {operation_type!r}", path=f"{path}.type")
            continue
        if operation_type == "createNode":
            temp_id = operation.get("tempId")
            descriptor_ref = operation.get("descriptor")
            action_id = descriptor_ref.get("actionId") if isinstance(descriptor_ref, dict) else None
            descriptor = descriptors.get(str(action_id))
            if descriptor is None:
                report.error("graphpatch-descriptor-not-found", f"Unknown actionId: {action_id!r}", path=f"{path}.descriptor.actionId")
                continue
            position = operation.get("position")
            if position is not None:
                if not isinstance(position, dict) or not all(_is_int(position.get(axis)) for axis in ("x", "y")):
                    report.error("contract-schema-type-mismatch", "position requires integer x/y", path=f"{path}.position")
                    continue
            node, pins, pin_id_map = _instantiate_node(
                descriptor,
                patch_id=str(patch.get("patchId", "")),
                temp_id=str(temp_id),
                position=position,
            )
            working.add_node(node, pins)
            working.temp_map[str(temp_id)] = node["nodeId"]
            working.pin_id_map.update(pin_id_map)
            temp_nodes[str(temp_id)] = {"nodeId": node["nodeId"], "pins": pin_id_map}
            pin_defaults = operation.get("pinDefaults", {})
            if pin_defaults and not isinstance(pin_defaults, dict):
                report.error("contract-schema-type-mismatch", "pinDefaults must be an object", path=f"{path}.pinDefaults")
            elif isinstance(pin_defaults, dict):
                for pin_name, value in pin_defaults.items():
                    candidates = [
                        pin for pin in working.pins_by_node.get(node["nodeId"], [])
                        if pin.get("name") == pin_name and pin.get("direction") == "input"
                    ]
                    if len(candidates) != 1:
                        report.error("graphpatch-pin-not-found", f"pinDefaults references unknown pin {pin_name!r}", path=f"{path}.pinDefaults")
                        continue
                    candidates[0]["defaults"]["value"] = str(value)
        elif operation_type == "connectPins":
            if operation.get("connectionPolicy", "reject-replacement") != "reject-replacement":
                report.error("graphpatch-operation-not-allowed", "V1 connectionPolicy must be reject-replacement", path=f"{path}.connectionPolicy")
                continue
            source = _resolve_pin(working, operation.get("from"), "output", report=report, path=f"{path}.from")
            target = _resolve_pin(working, operation.get("to"), "input", report=report, path=f"{path}.to")
            if source is None or target is None:
                continue
            _check_derived_pin(source, operation_type=operation_type, allow_derived=allow_derived, report=report, path=f"{path}.from")
            _check_derived_pin(target, operation_type=operation_type, allow_derived=allow_derived, report=report, path=f"{path}.to")
            if source["nodeId"] == target["nodeId"] and not operation.get("allowSelfLoop", False):
                report.error("graphpatch-self-loop", "connectPins self-loop requires allowSelfLoop=true", path=path)
                continue
            if not _types_compatible(source, target):
                report.error(
                    "graphpatch-pin-type-incompatible",
                    "Pin kinds/types are not compatible (Exec and Data are never interchangeable)",
                    path=path,
                    context={"fromKind": source.get("kind"), "toKind": target.get("kind")},
                )
                continue
            existing = working.incoming_edges(target["pinId"])
            if existing:
                report.error(
                    "graphpatch-connection-replaced",
                    "Target input already has a connection; V1 rejects replacement",
                    path=f"{path}.to",
                    context={"existingEdgeId": existing[0].get("edgeId")},
                )
                continue
            edge = {
                "edgeId": ids.edge_id(source["pinId"], target["pinId"]),
                "from": {"nodeId": source["nodeId"], "pinId": source["pinId"]},
                "to": {"nodeId": target["nodeId"], "pinId": target["pinId"]},
                "kind": source.get("kind"),
                "origin": "patch",
                "status": "resolved",
            }
            if edge["edgeId"] in working.edge_by_id:
                report.error("graphpatch-operation-duplicate-target", "The same edge is connected twice", path=path)
                continue
            working.add_edge(edge)
        elif operation_type == "disconnectPins":
            source = _resolve_pin(working, operation.get("from"), "output", report=report, path=f"{path}.from")
            target = _resolve_pin(working, operation.get("to"), "input", report=report, path=f"{path}.to")
            if source is None or target is None:
                continue
            edge_id_value = ids.edge_id(source["pinId"], target["pinId"])
            edge = working.edge_by_id.get(edge_id_value)
            if edge is None:
                report.error("graphpatch-connection-rejected", "No such edge to disconnect", path=path)
                continue
            if operation.get("expectedEdgeId") and operation.get("expectedEdgeId") != edge_id_value:
                report.error("graphpatch-connection-rejected", "expectedEdgeId does not match", path=f"{path}.expectedEdgeId")
                continue
            working.remove_edge(edge_id_value)
        elif operation_type == "setPinDefault":
            pin = _resolve_pin(working, operation.get("pin"), "input", report=report, path=f"{path}.pin")
            if pin is None:
                continue
            _check_derived_pin(pin, operation_type=operation_type, allow_derived=allow_derived, report=report, path=f"{path}.pin")
            flags = pin.get("flags") or {}
            if pin.get("kind") != "data" or flags.get("defaultReadOnly") or flags.get("defaultIgnored") or flags.get("orphaned"):
                report.error("graphpatch-pin-default-not-allowed", "Pin flags do not allow a default value", path=f"{path}.pin")
                continue
            if working.incoming_edges(pin["pinId"]) or pin.get("links"):
                report.error("graphpatch-pin-connected", "setPinDefault requires an unconnected input pin", path=f"{path}.pin")
                continue
            value_type = operation.get("valueType")
            if not isinstance(value_type, str) or not _value_type_compatible(pin, value_type):
                report.error("graphpatch-pin-type-incompatible", "valueType is not compatible with the pin type", path=f"{path}.valueType")
                continue
            expected_old = operation.get("expectedOldValue")
            current = (pin.get("defaults") or {}).get("value", "")
            if expected_old is not None and str(expected_old) != current:
                report.error(
                    "graphpatch-node-hash-stale",
                    "expectedOldValue does not match the current default",
                    path=f"{path}.expectedOldValue",
                    context={"expected": expected_old, "actual": current},
                )
                continue
            pin.setdefault("defaults", {})["value"] = str(operation.get("value", ""))
        elif operation_type == "setNodePosition":
            node_ref = operation.get("nodeId") or operation.get("node")
            node_id_value = _resolve_node(working, str(node_ref)) if isinstance(node_ref, str) else None
            if node_id_value is None:
                report.error("graphpatch-node-not-found", f"Unknown node: {node_ref!r}", path=f"{path}.nodeId")
                continue
            if not all(_is_int(operation.get(axis)) for axis in ("x", "y")):
                report.error("contract-schema-type-mismatch", "setNodePosition requires integer x/y", path=path)
                continue
            for axis in ("x", "y"):
                if not -(2**31) <= operation[axis] <= 2**31 - 1:
                    report.error("graph-invalid-coordinate", "Coordinate is outside int32", path=f"{path}.{axis}")
            working.node_by_id[node_id_value]["position"] = {"x": operation["x"], "y": operation["y"]}
        elif operation_type == "setNodeComment":
            node_ref = operation.get("nodeId") or operation.get("node")
            node_id_value = _resolve_node(working, str(node_ref)) if isinstance(node_ref, str) else None
            if node_id_value is None:
                report.error("graphpatch-node-not-found", f"Unknown node: {node_ref!r}", path=f"{path}.nodeId")
                continue
            if not isinstance(operation.get("comment"), str) or len(operation.get("comment", "")) > 4096:
                report.error("contract-schema-type-mismatch", "comment must be a string <= 4096", path=f"{path}.comment")
                continue
            working.node_by_id[node_id_value]["comment"] = operation["comment"]
        elif operation_type == "setNodeEnabled":
            node_ref = operation.get("nodeId") or operation.get("node")
            node_id_value = _resolve_node(working, str(node_ref)) if isinstance(node_ref, str) else None
            if node_id_value is None:
                report.error("graphpatch-node-not-found", f"Unknown node: {node_ref!r}", path=f"{path}.nodeId")
                continue
            if not isinstance(operation.get("enabled"), bool):
                report.error("contract-schema-type-mismatch", "enabled must be boolean", path=f"{path}.enabled")
                continue
            if operation.get("confirmed") is not True:
                report.error(
                    "graphpatch-confirmation-required",
                    "setNodeEnabled is R2 and requires confirmed=true in the Patch (D-09)",
                    path=f"{path}.confirmed",
                )
                continue
            working.node_by_id[node_id_value].setdefault("flags", {})["enabled"] = operation["enabled"]
        elif operation_type == "setCommentBounds":
            node_ref = operation.get("nodeId") or operation.get("node")
            node_id_value = _resolve_node(working, str(node_ref)) if isinstance(node_ref, str) else None
            if node_id_value is None:
                report.error("graphpatch-node-not-found", f"Unknown node: {node_ref!r}", path=f"{path}.nodeId")
                continue
            bounds = operation.get("bounds")
            if working.node_by_id[node_id_value].get("kind") != "comment":
                report.error("graphpatch-operation-not-allowed", "setCommentBounds requires a Comment node", path=path)
                continue
            if not isinstance(bounds, dict) or not all(_is_int(bounds.get(axis)) for axis in ("x", "y", "width", "height")):
                report.error("contract-schema-type-mismatch", "bounds requires integer x/y/width/height", path=f"{path}.bounds")
                continue
            if bounds["width"] < 0 or bounds["height"] < 0:
                report.error("graph-invalid-coordinate", "Comment bounds must be non-negative", path=f"{path}.bounds")
                continue
            for comment in working.graph.get("comments", []):
                if comment.get("nodeId") == node_id_value:
                    comment["bounds"] = dict(bounds)

    # CR-GP-04: setPinDefault must be ordered before any connection that consumes it.
    first_default: dict[str, int] = {}
    first_connect: dict[str, int] = {}
    for index, operation in enumerate(ordered):
        if operation.get("type") == "setPinDefault":
            pin = operation.get("pin") or {}
            key = str(pin.get("pinId") or f"{pin.get('node')}:{pin.get('pin')}:{pin.get('pinIndex')}")
            first_default.setdefault(key, index)
        elif operation.get("type") == "connectPins":
            target = operation.get("to") or {}
            key = str(target.get("pinId") or f"{target.get('node')}:{target.get('pin')}:{target.get('pinIndex')}")
            first_connect.setdefault(key, index)
    for key, connect_index in first_connect.items():
        if key in first_default and first_default[key] > connect_index:
            report.error(
                "graphpatch-operation-order-conflict",
                "setPinDefault for a pin must be ordered before connectPins that targets it",
                path="$.operations",
                context={"pinKey": key},
            )

    predicted = {
        "nodeCountBefore": node_count_before,
        "nodeCountAfter": len(working.graph["nodes"]),
        "edgeCountBefore": edge_count_before,
        "edgeCountAfter": len(working.graph["edges"]),
        "pinDefaultCountBefore": default_count_before,
        "pinDefaultCountAfter": sum(
            1 for pin in working.graph["pins"] if pin.get("defaults", {}).get("value", "") != ""
        ),
    }
    predicted["nodeCountDelta"] = predicted["nodeCountAfter"] - predicted["nodeCountBefore"]
    predicted["edgeCountDelta"] = predicted["edgeCountAfter"] - predicted["edgeCountBefore"]
    predicted["pinDefaultDelta"] = predicted["pinDefaultCountAfter"] - predicted["pinDefaultCountBefore"]

    expect = patch.get("expect")
    if isinstance(expect, dict):
        for key in ("nodeCountDelta", "edgeCountDelta", "pinDefaultDelta"):
            if key in expect and expect[key] != predicted[key]:
                report.error(
                    "graphpatch-expectation-mismatch",
                    f"expect.{key} does not match the predicted patch result",
                    path=f"$.expect.{key}",
                    context={"expected": expect[key], "actual": predicted[key]},
                )

    return ordered, working, {"predicted": predicted, "tempNodes": temp_nodes}


def plan_patch(
    patch: dict[str, Any],
    graph: dict[str, Any],
    env: Environment | dict[str, Any] | None = None,
    descriptors: Iterable[dict[str, Any]] | None = None,
    *,
    recovery: RecoveryStore | None = None,
) -> PatchPlan:
    environment = env if isinstance(env, Environment) else Environment.from_dict(env)
    report = PatchReport()
    if not isinstance(patch, dict):
        report.error("contract-schema-type-mismatch", "GraphPatch must be a JSON object", path="$")
        _finalize(report)
        return PatchPlan(report=report)
    _validate_patch_root(patch, report)
    try:
        digest = payload_hash(patch)
    except Exception:
        digest = ""
    if report.ok:
        _validate_environment(patch, graph, environment, report, recovery)
    ordered: list[dict[str, Any]] = []
    predicted: dict[str, Any] = {}
    temp_nodes: dict[str, dict[str, Any]] = {}
    if report.ok:
        ordered, _, extras = _validate_operations(patch, graph, _load_descriptors(descriptors), report)
        predicted = extras["predicted"]
        temp_nodes = extras["tempNodes"]
    _finalize(report)
    return PatchPlan(report=report, payload_hash=digest, ordered_operations=ordered, predicted=predicted, temp_nodes=temp_nodes)


def _result_base(patch: dict[str, Any], plan: PatchPlan, env: Environment) -> dict[str, Any]:
    target = patch.get("target") or {}
    return {
        "schemaVersion": RESULT_VERSION,
        "patchId": patch.get("patchId", ""),
        "planId": "plan:" + short_digest(plan.payload_hash, DOMAIN_PAYLOAD, 16) if plan.payload_hash else "",
        "changeSetId": "cs-simulated:" + short_digest(str(patch.get("patchId", "")), DOMAIN_RECEIPT, 12),
        "editorSessionId": env.editor_session_id,
        "baseRevision": target.get("baseRevision", ""),
        "actualRevision": env.actual_revision,
        "baseGraphHash": target.get("baseGraphHash", ""),
        "graphpatchProfile": patch_profile(patch),
        "operationCounts": operation_counts(patch.get("operations", [])),
        "compileResult": None,
        "commentHash": "",
        "nodeIdMap": {},
        "pinIdMap": {},
        "operationResults": [],
        "warnings": [],
        "evidence": {
            "planPath": "simulated/plan.json",
            "applyReportPath": "simulated/apply-report.json",
            "verifyReportPath": "",
            "backupManifestPath": "",
            "artifacts": {
                "plan": _artifact(
                    "simulated/plan.json",
                    {"schemaVersion": "ueak.graphpatch-plan/1.0", "patchId": patch.get("patchId", ""), "operations": patch.get("operations", [])},
                )
            },
        },
        "stageReceipts": {},
        "simulation": True,
        "previewOnly": False,
        "replayed": False,
    }


def preview_patch(
    patch: dict[str, Any],
    graph: dict[str, Any],
    env: Environment | dict[str, Any] | None = None,
    descriptors: Iterable[dict[str, Any]] | None = None,
    *,
    recovery: RecoveryStore | None = None,
) -> dict[str, Any]:
    """Pure Preview: no working-graph mutation, no transaction, no Dirty change."""

    environment = env if isinstance(env, Environment) else Environment.from_dict(env)
    before = typed_graph.semantic_hash(graph)
    plan = plan_patch(patch, graph, environment, descriptors, recovery=recovery)
    result = _result_base(patch, plan, environment)
    result["status"] = "previewed" if plan.report.ok else "rejected"
    result["errorCode"] = plan.report.primary_code
    result["errors"] = list(plan.report.errors)
    result["warnings"] = list(plan.report.warnings)
    result["predicted"] = dict(plan.predicted)
    result["tempIdPreview"] = {temp: data["nodeId"] for temp, data in plan.temp_nodes.items()}
    result["semanticHashBefore"] = before
    result["semanticHashAfter"] = before
    result["dirtyBefore"] = environment.dirty
    result["dirtyAfter"] = environment.dirty
    result["transactionId"] = ""
    result["writeEffects"] = {
        "memoryModified": False,
        "diskModified": False,
        "transactionOpened": False,
        "dirtyChanged": False,
        "idempotencyReserved": False,
    }
    result["commentHashBefore"] = typed_graph.comment_hash(graph)
    result["commentHashAfter"] = typed_graph.comment_hash(graph)
    result["previewOnly"] = True
    result["stageReceipts"] = {
        "previewed": _stage_receipt(
            "previewed", {"patchId": patch.get("patchId", ""), "payloadHash": plan.payload_hash, "predicted": plan.predicted}
        )
    }
    return result


@dataclass
class _Inverse:
    kind: str
    payload: dict[str, Any]
    reversible: bool = True


def _apply_operations(
    plan: PatchPlan,
    graph: dict[str, Any],
    patch: dict[str, Any],
    descriptors: dict[str, dict[str, Any]],
    *,
    fail_at: str | None = None,
    fail_after: str | None = None,
) -> tuple[_WorkingGraph, list[dict[str, Any]], list[_Inverse], str | None, str]:
    working = _WorkingGraph(graph)
    base_hash = typed_graph.semantic_hash(graph)
    inverses: list[_Inverse] = []
    results: list[dict[str, Any]] = []
    before_hash = base_hash
    for operation in plan.ordered_operations:
        operation_id = str(operation.get("operationId"))
        operation_type = operation.get("type")
        if fail_at and operation_id == fail_at:
            return working, results, inverses, "graphpatch-node-spawn-failed", before_hash
        status = "applied"
        try:
            if operation_type == "createNode":
                temp_id = str(operation.get("tempId"))
                action_id = (operation.get("descriptor") or {}).get("actionId")
                descriptor = descriptors[str(action_id)]
                node, pins, pin_id_map = _instantiate_node(
                    descriptor,
                    patch_id=str(patch.get("patchId", "")),
                    temp_id=temp_id,
                    position=operation.get("position"),
                )
                working.add_node(node, pins)
                working.temp_map[temp_id] = node["nodeId"]
                working.pin_id_map.update(pin_id_map)
                for pin_name, value in (operation.get("pinDefaults") or {}).items():
                    for pin in working.pins_by_node[node["nodeId"]]:
                        if pin.get("name") == pin_name and pin.get("direction") == "input":
                            pin["defaults"]["value"] = str(value)
                inverses.append(_Inverse("deleteNode", {"nodeId": node["nodeId"]}))
            elif operation_type == "connectPins":
                source = _resolve_pin(working, operation.get("from"), "output", report=PatchReport(), path="")
                target = _resolve_pin(working, operation.get("to"), "input", report=PatchReport(), path="")
                assert source is not None and target is not None
                edge = {
                    "edgeId": ids.edge_id(source["pinId"], target["pinId"]),
                    "from": {"nodeId": source["nodeId"], "pinId": source["pinId"]},
                    "to": {"nodeId": target["nodeId"], "pinId": target["pinId"]},
                    "kind": source.get("kind"),
                    "origin": "patch",
                    "status": "resolved",
                }
                working.add_edge(edge)
                inverses.append(_Inverse("disconnect", {"edgeId": edge["edgeId"]}))
            elif operation_type == "disconnectPins":
                source = _resolve_pin(working, operation.get("from"), "output", report=PatchReport(), path="")
                target = _resolve_pin(working, operation.get("to"), "input", report=PatchReport(), path="")
                assert source is not None and target is not None
                edge_id_value = ids.edge_id(source["pinId"], target["pinId"])
                edge = working.remove_edge(edge_id_value)
                inverses.append(_Inverse("reconnect", {"edge": copy.deepcopy(edge)}))
            elif operation_type == "setPinDefault":
                pin = _resolve_pin(working, operation.get("pin"), "input", report=PatchReport(), path="")
                assert pin is not None
                old_value = (pin.get("defaults") or {}).get("value", "")
                pin.setdefault("defaults", {})["value"] = str(operation.get("value", ""))
                inverses.append(_Inverse("restoreDefault", {"pinId": pin["pinId"], "value": old_value}))
            elif operation_type == "setNodePosition":
                node_ref = operation.get("nodeId") or operation.get("node")
                node_id_value = _resolve_node(working, str(node_ref))
                assert node_id_value is not None
                old_position = copy.deepcopy(working.node_by_id[node_id_value].get("position", {}))
                working.node_by_id[node_id_value]["position"] = {"x": operation["x"], "y": operation["y"]}
                inverses.append(_Inverse("restorePosition", {"nodeId": node_id_value, "position": old_position}))
            elif operation_type == "setNodeComment":
                node_ref = operation.get("nodeId") or operation.get("node")
                node_id_value = _resolve_node(working, str(node_ref))
                assert node_id_value is not None
                old_comment = working.node_by_id[node_id_value].get("comment", "")
                working.node_by_id[node_id_value]["comment"] = operation["comment"]
                inverses.append(_Inverse("restoreComment", {"nodeId": node_id_value, "comment": old_comment}))
            elif operation_type == "setNodeEnabled":
                node_ref = operation.get("nodeId") or operation.get("node")
                node_id_value = _resolve_node(working, str(node_ref))
                assert node_id_value is not None
                old_enabled = bool((working.node_by_id[node_id_value].get("flags") or {}).get("enabled", True))
                working.node_by_id[node_id_value].setdefault("flags", {})["enabled"] = operation["enabled"]
                inverses.append(_Inverse("restoreEnabled", {"nodeId": node_id_value, "enabled": old_enabled}))
            elif operation_type == "setCommentBounds":
                node_ref = operation.get("nodeId") or operation.get("node")
                node_id_value = _resolve_node(working, str(node_ref))
                assert node_id_value is not None
                old_bounds = None
                for comment in working.graph.get("comments", []):
                    if comment.get("nodeId") == node_id_value:
                        old_bounds = copy.deepcopy(comment.get("bounds", {}))
                        comment["bounds"] = dict(operation["bounds"])
                inverses.append(_Inverse("restoreCommentBounds", {"nodeId": node_id_value, "bounds": old_bounds}))
        except Exception:  # pragma: no cover - defensive
            return working, results, inverses, "graphpatch-node-spawn-failed", before_hash
        after_hash = typed_graph.semantic_hash(working.graph)
        results.append(
            {
                "operationId": operation_id,
                "status": status,
                "errorCode": "",
                "message": "",
                "beforeHash": before_hash,
                "afterHash": after_hash,
                "startedAtUtc": _utc_now(),
                "finishedAtUtc": _utc_now(),
                "receiptSha256": domain_hash(
                    {"operationId": operation_id, "beforeHash": before_hash, "afterHash": after_hash}, DOMAIN_RECEIPT
                ),
            }
        )
        before_hash = after_hash
        if fail_after and operation_id == fail_after:
            return working, results, inverses, "graphpatch-node-spawn-failed", before_hash
    return working, results, inverses, None, before_hash


def _execute_inverses(working: _WorkingGraph, inverses: list[_Inverse]) -> tuple[bool, list[str]]:
    unresolved: list[str] = []
    for inverse in reversed(inverses):
        if not inverse.reversible:
            unresolved.append(inverse.kind)
            continue
        payload = inverse.payload
        if inverse.kind == "deleteNode":
            if payload["nodeId"] in working.node_by_id:
                working.remove_node(payload["nodeId"])
        elif inverse.kind == "disconnect":
            if payload["edgeId"] in working.edge_by_id:
                working.remove_edge(payload["edgeId"])
        elif inverse.kind == "reconnect":
            edge = copy.deepcopy(payload["edge"])
            if edge["edgeId"] not in working.edge_by_id:
                working.add_edge(edge)
        elif inverse.kind == "restoreDefault":
            pin = working.pin_by_id.get(payload["pinId"])
            if pin is None:
                unresolved.append("restoreDefault:missing-pin")
            else:
                pin.setdefault("defaults", {})["value"] = payload["value"]
                if pin["defaults"]["value"] != payload["value"]:
                    unresolved.append("restoreDefault:readback")
        elif inverse.kind == "restorePosition":
            node = working.node_by_id.get(payload["nodeId"])
            if node is None:
                unresolved.append("restorePosition:missing-node")
            else:
                node["position"] = copy.deepcopy(payload["position"])
                if node.get("position") != payload["position"]:
                    unresolved.append("restorePosition:readback")
        elif inverse.kind == "restoreComment":
            node = working.node_by_id.get(payload["nodeId"])
            if node is None:
                unresolved.append("restoreComment:missing-node")
            else:
                node["comment"] = payload["comment"]
        elif inverse.kind == "restoreEnabled":
            node = working.node_by_id.get(payload["nodeId"])
            if node is None:
                unresolved.append("restoreEnabled:missing-node")
            else:
                node.setdefault("flags", {})["enabled"] = payload["enabled"]
        elif inverse.kind == "restoreCommentBounds":
            node = working.node_by_id.get(payload["nodeId"])
            if node is None:
                unresolved.append("restoreCommentBounds:missing-node")
            else:
                for comment in working.graph.get("comments", []):
                    if comment.get("nodeId") == payload["nodeId"]:
                        comment["bounds"] = copy.deepcopy(payload["bounds"])
    return not unresolved, unresolved


def _build_maps(working: _WorkingGraph, patch: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    node_map = dict(working.temp_map)
    pin_map = dict(working.pin_id_map)
    return node_map, pin_map


def simulate_apply(
    patch: dict[str, Any],
    graph: dict[str, Any],
    env: Environment | dict[str, Any] | None = None,
    descriptors: Iterable[dict[str, Any]] | None = None,
    *,
    recovery: RecoveryStore | None = None,
    ledger: IdempotencyLedger | None = None,
    idempotency_store: GraphPatchIdempotencyStore | None = None,
    fail_at: str | None = None,
    fail_after: str | None = None,
    include_result_graph: bool = False,
) -> dict[str, Any]:
    environment = env if isinstance(env, Environment) else Environment.from_dict(env)
    plan = plan_patch(patch, graph, environment, descriptors, recovery=recovery)
    result = _result_base(patch, plan, environment)
    if not plan.report.ok:
        result.update(
            {
                "status": "rejected",
                "errorCode": plan.report.primary_code,
                "errors": list(plan.report.errors),
                "warnings": list(plan.report.warnings),
                "actualGraphHash": typed_graph.semantic_hash(graph),
                "transactionId": "",
                "writeEffects": {
                    "memoryModified": False,
                    "diskModified": False,
                    "transactionOpened": False,
                    "dirtyChanged": False,
                    "idempotencyReserved": False,
                },
            }
        )
        return result

    digest = plan.payload_hash
    if idempotency_store is not None and ledger is not None:
        raise ContractError("graphpatch-idempotency-state-invalid", "Use either ledger or idempotency_store, not both")
    if idempotency_store is not None:
        outcome = idempotency_store.reserve(patch, digest)
        if outcome.state == "conflict":
            result.update(
                {
                    "status": "rejected",
                    "errorCode": "graphpatch-idempotency-conflict",
                    "errors": [make_diagnostic("graphpatch-idempotency-conflict", "Same idempotencyKey with a different payload", path="$.idempotencyKey")],
                    "transactionId": "",
                    "writeEffects": {
                        "memoryModified": False,
                        "diskModified": False,
                        "transactionOpened": False,
                        "dirtyChanged": False,
                        "idempotencyReserved": False,
                    },
                }
            )
            return result
        if outcome.state == "replay" and outcome.record is not None:
            stored = outcome.record.get("result")
            if isinstance(stored, dict):
                replayed = copy.deepcopy(stored)
                replayed["replayed"] = True
                replayed["idempotencyState"] = "applied"
                replayed["writeEffects"] = {
                    "memoryModified": False,
                    "diskModified": False,
                    "transactionOpened": False,
                    "dirtyChanged": False,
                    "idempotencyReserved": False,
                }
                return replayed
            result.update(
                {
                    "status": "rejected",
                    "errorCode": "graphpatch-idempotency-state-invalid",
                    "errors": [make_diagnostic("graphpatch-idempotency-state-invalid", "Applied record has no replay result", path="$.idempotencyKey")],
                    "transactionId": "",
                }
            )
            return result
        if outcome.state in ("locked", "indeterminate"):
            code = "graphpatch-idempotency-locked" if outcome.state == "locked" else "graphpatch-idempotency-indeterminate"
            result.update(
                {
                    "status": "rejected",
                    "errorCode": code,
                    "errors": [
                        make_diagnostic(
                            code,
                            "Idempotency scope is locked or in an indeterminate state; fail-closed",
                            path="$.idempotencyKey",
                        )
                    ],
                    "transactionId": "",
                    "writeEffects": {
                        "memoryModified": False,
                        "diskModified": False,
                        "transactionOpened": False,
                        "dirtyChanged": False,
                        "idempotencyReserved": False,
                    },
                }
            )
            return result

    if ledger is not None:
        state, record = ledger.check(patch, digest)
        if state == "conflict":
            result.update(
                {
                    "status": "rejected",
                    "errorCode": "graphpatch-idempotency-conflict",
                    "errors": [make_diagnostic("graphpatch-idempotency-conflict", "Same idempotencyKey with a different payload", path="$.idempotencyKey")],
                    "transactionId": "",
                    "writeEffects": {
                        "memoryModified": False,
                        "diskModified": False,
                        "transactionOpened": False,
                        "dirtyChanged": False,
                        "idempotencyReserved": False,
                    },
                }
            )
            return result
        if state == "replay" and record is not None:
            replayed = copy.deepcopy(record["result"])
            replayed["replayed"] = True
            replayed["writeEffects"] = {
                "memoryModified": False,
                "diskModified": False,
                "transactionOpened": False,
                "dirtyChanged": False,
                "idempotencyReserved": False,
            }
            return replayed

    base_hash = typed_graph.semantic_hash(graph)
    transaction_id = "txn-simulated:" + short_digest(
        {"patchId": patch.get("patchId", ""), "payloadHash": digest}, DOMAIN_RECEIPT, 16
    )
    catalog = _load_descriptors(descriptors)
    working, operation_results, inverses, failure, applied_hash = _apply_operations(
        plan, graph, patch, catalog, fail_at=fail_at, fail_after=fail_after
    )

    def rollback(reason: str) -> tuple[bool, list[str]]:
        ok, unresolved = _execute_inverses(working, inverses)
        restored = typed_graph.semantic_hash(working.graph)
        if restored != base_hash:
            ok = False
            unresolved.append("semanticHashMismatch")
        return ok, unresolved

    if failure is None:
        compile_failure = compile_result_failure(environment)
        if compile_failure:
            failure = compile_failure
        else:
            result["compileResult"] = copy.deepcopy(environment.compile_result)
            result["compileStatus"] = str(environment.compile_result.get("result", "success"))
            result["warnings"].extend(compile_warnings(environment))

    if failure is None and environment.save_error:
        # Apply never saves; a supplied save_error only affects the simulated saved phase.
        result["warnings"].append(make_diagnostic("graphpatch-save-failed", "Simulated Save failure recorded; Apply itself never saves", severity="warning"))

    if failure:
        ok, unresolved = rollback(failure)
        working.rebuild_indexes()
        result.update(
            {
                "status": "rolled-back" if ok else "manual-recovery",
                "errorCode": failure,
                "errors": [make_diagnostic(failure, "Patch failed and the transaction was compensated", path="$.operations")],
                "transactionId": transaction_id,
                "operationResults": operation_results,
                "nodeIdMap": {},
                "pinIdMap": {},
                "actualGraphHash": typed_graph.semantic_hash(working.graph),
                "writeEffects": {
                    "memoryModified": True,
                    "diskModified": False,
                    "transactionOpened": True,
                    "dirtyChanged": False,
                    "idempotencyReserved": False,
                },
            }
        )
        if not ok:
            result["unresolvedOperations"] = unresolved
            if recovery is not None:
                marker = make_marker(
                    project_name=str(patch.get("projectName", "")),
                    asset_path=str((patch.get("target") or {}).get("assetPath", "")),
                    patch_id=str(patch.get("patchId", "")),
                    transaction_id=transaction_id,
                    base_revision=str((patch.get("target") or {}).get("baseRevision", "")),
                    base_graph_hash=base_hash,
                    unresolved_operations=[{"kind": kind} for kind in unresolved],
                    evidence={
                        "recoveryEvidenceSha256": domain_hash(
                            {"patchId": patch.get("patchId", ""), "transactionId": transaction_id, "unresolved": unresolved},
                            DOMAIN_ARTIFACT,
                        )
                    },
                )
                marker_path = recovery.write(marker)
                result["recoveryMarkerPath"] = str(marker_path)
        if idempotency_store is not None:
            if ok:
                idempotency_store.mark_failed(patch, digest, failure)
                result["idempotencyState"] = "failed"
            else:
                idempotency_store.mark_indeterminate(patch, digest, failure)
                result["idempotencyState"] = "indeterminate"
        return result

    node_map, pin_map = _build_maps(working, patch)
    result.update(
        {
            "status": "applied",
            "errorCode": "",
            "transactionId": transaction_id,
            "operationResults": operation_results,
            "nodeIdMap": node_map,
            "pinIdMap": pin_map,
            "actualGraphHash": typed_graph.semantic_hash(working.graph),
            "semanticHashBefore": base_hash,
            "semanticHashAfter": typed_graph.semantic_hash(working.graph),
            "commentHashBefore": typed_graph.comment_hash(graph),
            "commentHash": typed_graph.comment_hash(working.graph),
            "writeEffects": {
                "memoryModified": True,
                "diskModified": False,
                "transactionOpened": True,
                "dirtyChanged": True,
                "idempotencyReserved": ledger is not None or idempotency_store is not None,
            },
        }
    )
    result["applyReceipt"] = "apply:" + short_digest(
        {"payloadHash": digest, "actualGraphHash": result["actualGraphHash"], "transactionId": transaction_id},
        DOMAIN_RECEIPT,
        16,
    )
    if include_result_graph:
        result["resultGraph"] = copy.deepcopy(working.graph)
    result["stageReceipts"] = {
        "previewed": _stage_receipt("previewed", {"patchId": patch.get("patchId", ""), "payloadHash": digest}),
        "applied": _stage_receipt(
            "applied",
            {
                "patchId": patch.get("patchId", ""),
                "actualGraphHash": result["actualGraphHash"],
                "operationCount": len(operation_results),
                "graphpatchProfile": result["graphpatchProfile"],
            },
        ),
        "compiled": _stage_receipt(
            "compiled",
            {
                "patchId": patch.get("patchId", ""),
                "compileStatus": result.get("compileStatus", ""),
                "actualGraphHash": result["actualGraphHash"],
            },
        ),
    }
    result["evidence"]["artifacts"].update(
        {
            "apply-report": _artifact(
                "simulated/apply-report.json",
                {"patchId": patch.get("patchId", ""), "status": result["status"], "operationResults": operation_results},
            ),
            "typedgraph-before": _artifact("simulated/typedgraph-before.json", graph),
            "typedgraph-after": _artifact("simulated/typedgraph-after.json", working.graph),
            "receipt": _artifact(
                "simulated/receipt.json",
                {"patchId": patch.get("patchId", ""), "actualGraphHash": result["actualGraphHash"], "transactionId": transaction_id},
            ),
        }
    )
    if idempotency_store is not None:
        idempotency_store.mark_applied(patch, digest, result)
        result["idempotencyState"] = "applied"
    if ledger is not None:
        ledger.store(patch, digest, result)
    return result


def verify_result(apply_result: dict[str, Any], reloaded_graph: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(apply_result)
    if apply_result.get("status") not in ("applied", "verified"):
        result["status"] = apply_result.get("status", "failed")
        result["verified"] = False
        return result
    errors: list[dict[str, Any]] = []
    reloaded_hash = typed_graph.semantic_hash(reloaded_graph)
    if reloaded_hash != apply_result.get("actualGraphHash"):
        errors.append(
            make_diagnostic(
                "graphpatch-verify-mismatch",
                "Independent reload semanticHash differs from the applied graph",
                path="$.actualGraphHash",
                context={"expected": apply_result.get("actualGraphHash"), "actual": reloaded_hash},
            )
        )
    if reloaded_graph.get("asset", {}).get("revision", "") != apply_result.get("actualRevision", ""):
        errors.append(
            make_diagnostic(
                "graphpatch-verify-mismatch",
                "Reloaded disk revision differs from the receipt",
                path="$.actualRevision",
            )
        )
    if apply_result.get("commentHash") and typed_graph.comment_hash(reloaded_graph) != apply_result.get("commentHash"):
        errors.append(
            make_diagnostic(
                "graphpatch-verify-mismatch",
                "Reloaded comment/annotation hash differs from the applied graph (D-09)",
                path="$.commentHash",
                context={"expected": apply_result.get("commentHash"), "actual": typed_graph.comment_hash(reloaded_graph)},
            )
        )
    result["status"] = "verified" if not errors else "verify-mismatch"
    result["verified"] = not errors
    result["errors"] = errors
    result["errorCode"] = errors[0]["code"] if errors else ""
    result["verifyReceipt"] = "verify:" + short_digest(
        {"actualGraphHash": reloaded_hash, "ok": not errors}, DOMAIN_RECEIPT, 16
    )
    result["evidence"] = dict(result.get("evidence", {}), verifyReportPath="simulated/verify-report.json")
    artifacts = result["evidence"].setdefault("artifacts", {})
    artifacts["verify-report"] = _artifact(
        "simulated/verify-report.json", {"actualGraphHash": reloaded_hash, "ok": not errors, "errors": errors}
    )
    receipts = result.setdefault("stageReceipts", {})
    receipts["verified"] = _stage_receipt("verified", {"actualGraphHash": reloaded_hash, "ok": not errors})
    return result


def load_patch(path: Path | str) -> dict[str, Any]:
    raw = Path(path).read_bytes()
    if len(raw) > MAX_PATCH_BYTES:
        raise ContractError("graphpatch-limit-exceeded", "GraphPatch file exceeds 4 MiB", path=str(path))
    return json.loads(raw.decode("utf-8"))
