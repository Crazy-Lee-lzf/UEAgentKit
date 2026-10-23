"""NodeDescriptor / OperationDescriptor 1.0 candidate.

Clause IDs: ND-DISCOVERY-KEY, ND-ACTION-ID, ND-FINGERPRINT, ND-CAPABILITY,
ND-PIN-TEMPLATE, ND-LOCALIZED-BAN, OD-SCHEMA-SUBSET, OD-CODES, OD-EVIDENCE,
OD-REQUIRES, OD-LIMITS, OD-IMPLEMENTATION.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import ids
from .canonical import DOMAIN_ACTION, DOMAIN_DESCRIPTOR, DOMAIN_OPERATION, canonical_json, domain_hash, fingerprint, is_fingerprint
from .errors import load_registry, make_diagnostic
from .schema import SchemaError, check_schema, negotiate_version, validate_document
from .typed_graph import PIN_DIRECTIONS, PIN_TYPE_FIELDS, ValidationReport

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schemas"
CONDITION_CODES_PATH = SCHEMA_DIR / "condition-codes.json"

NODE_DESCRIPTOR_VERSION = "ueak.node-descriptor/1.0"
NODE_DESCRIPTOR_NAMESPACE = "ueak.node-descriptor"
OPERATION_DESCRIPTOR_VERSION = "ueak.operation/1.0"
OPERATION_DESCRIPTOR_NAMESPACE = "ueak.operation"

ACTION_KINDS = (
    "callFunction",
    "event",
    "variableGet",
    "variableSet",
    "macro",
    "cast",
    "delegate",
    "component",
    "special",
)
ACTION_PAYLOAD_KEYS = {
    "callFunction": ("functionPath",),
    "event": ("eventName",),
    "variableGet": ("variableName",),
    "variableSet": ("variableName",),
    "macro": ("macroGraph",),
    "cast": ("targetClass",),
    "delegate": ("delegateName",),
    "component": ("componentClass",),
    "special": ("specialKind",),
}
CAPABILITIES = ("core", "extension-e1", "extension-e2", "extension-e3")
TRUST_LEVELS = ("official", "project", "third-party")
RISK_LEVELS = ("R0", "R1", "R2", "R3")
LOCALIZED_FIELDS = ("title", "menuName", "category", "keywords", "tooltip", "friendlyName")
REVISION_SCOPES = ("none", "asset", "graph", "asset+graph")
TRANSACTION_KINDS = ("value", "structure")
SNAPSHOT_KINDS = ("io", "graph")
ROLLBACK_KINDS = ("reverse-ops", "undo", "none")
EVIDENCE_KINDS = (
    "plan",
    "apply-report",
    "verify-report",
    "backup-manifest",
    "typedgraph-before",
    "typedgraph-after",
    "receipt",
    "recovery-marker",
)
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
ACTION_ID_RE = re.compile(r"^action:[0-9a-f]{32}$")
OPERATION_NAME_RE = re.compile(r"^[a-z][a-z0-9]*(\.[a-z][a-zA-Z0-9_]*)+$")
PIN_TEMPLATE_REQUIRED = ("name", "direction", "kind", "type")


def load_condition_codes() -> dict[str, dict[str, str]]:
    with CONDITION_CODES_PATH.open("r", encoding="utf-8") as handle:
        document = json.load(handle)
    if document.get("schemaVersion") != "ueak.condition-codes/1.0":
        raise ValueError("Unsupported condition-code registry version")
    result: dict[str, dict[str, str]] = {}
    for entry in document["codes"]:
        result[entry["code"]] = entry
    return result


def discovery_key_projection(discovery_key: dict[str, Any]) -> dict[str, Any]:
    """Stable fields only; localized display text is structurally excluded."""

    payload = discovery_key.get("payload") if isinstance(discovery_key.get("payload"), dict) else {}
    return {
        "nodeClass": discovery_key.get("nodeClass", ""),
        "spawnerClass": discovery_key.get("spawnerClass", ""),
        "graphSchema": discovery_key.get("graphSchema", ""),
        "actionKind": discovery_key.get("actionKind", ""),
        "payload": {key: payload.get(key, "") for key in sorted(payload)},
    }


def make_action_id(discovery_key: dict[str, Any]) -> str:
    return "action:" + domain_hash(discovery_key_projection(discovery_key), DOMAIN_ACTION)[7:39]


def action_database_fingerprint(sorted_action_ids: list[str]) -> str:
    return fingerprint(sorted(sorted_action_ids), DOMAIN_ACTION + ".database", 16)


def descriptor_digest(descriptor: dict[str, Any]) -> str:
    projection = {key: value for key, value in descriptor.items() if key != "descriptorDigest"}
    source = projection.get("source")
    if isinstance(source, dict):
        projection["source"] = {key: value for key, value in source.items() if key != "discoveredAtUtc"}
    return domain_hash(projection, DOMAIN_OPERATION if "parametersSchema" in descriptor else DOMAIN_DESCRIPTOR)


def node_descriptor_digest(descriptor: dict[str, Any]) -> str:
    """Stable descriptor digest (excludes its own field and discovery timestamp)."""

    projection = {key: value for key, value in descriptor.items() if key != "descriptorDigest"}
    source = projection.get("source")
    if isinstance(source, dict):
        projection["source"] = {key: value for key, value in source.items() if key != "discoveredAtUtc"}
    return fingerprint(projection, DOMAIN_DESCRIPTOR, 16)


def _check_version(report: ValidationReport, document: dict[str, Any], *, namespace: str, minor: int, code: str) -> None:
    try:
        decision = negotiate_version(
            document.get("schemaVersion", ""),
            namespace=namespace,
            supported_minor=minor,
            unsupported_code=code,
        )
        report.diagnostics.extend(decision.diagnostics)
    except SchemaError as exc:
        code = "graphpatch-schema-version-unsupported" if exc.code == "contract-schema-invalid" else exc.code
        report.add(code, exc.message, path=exc.path)


def validate_node_descriptor(document: dict[str, Any]) -> ValidationReport:
    report = ValidationReport()
    if not isinstance(document, dict):
        report.add("contract-schema-invalid", "NodeDescriptor must be an object", path="$")
        return report
    _check_version(
        report,
        document,
        namespace=NODE_DESCRIPTOR_NAMESPACE,
        minor=0,
        code="graphpatch-schema-version-unsupported",
    )

    discovery_key = document.get("discoveryKey")
    if not isinstance(discovery_key, dict):
        report.add("contract-schema-required", "discoveryKey is required", path="$.discoveryKey")
        return report
    action_kind = discovery_key.get("actionKind")
    if action_kind not in ACTION_KINDS:
        report.add("graphpatch-context-mismatch", f"Unknown actionKind: {action_kind!r}", path="$.discoveryKey.actionKind")
    else:
        payload = discovery_key.get("payload") if isinstance(discovery_key.get("payload"), dict) else {}
        for key in ACTION_PAYLOAD_KEYS[action_kind]:
            if not isinstance(payload.get(key), str) or not payload.get(key):
                report.add(
                    "contract-schema-required",
                    f"discoveryKey.payload.{key} is required for actionKind={action_kind}",
                    path=f"$.discoveryKey.payload.{key}",
                )
    for field in LOCALIZED_FIELDS:
        if field in discovery_key or field in (discovery_key.get("payload") or {}):
            report.add(
                "graphpatch-context-mismatch",
                f"Localized/display field {field!r} must not appear in discoveryKey (ND-LOCALIZED-BAN)",
                path="$.discoveryKey",
            )
    expected_action_id = make_action_id(discovery_key)
    if document.get("actionId") != expected_action_id:
        report.add(
            "graphpatch-descriptor-not-found",
            "actionId does not match discoveryKey",
            path="$.actionId",
            context={"expected": expected_action_id, "actual": document.get("actionId")},
        )
    if document.get("risk") not in RISK_LEVELS:
        report.add("contract-schema-enum-mismatch", "risk must be R0..R3", path="$.risk")
    if document.get("capability") not in CAPABILITIES:
        report.add("contract-schema-enum-mismatch", "capability must match OperationDescriptor", path="$.capability")
    if document.get("trustLevel") not in TRUST_LEVELS:
        report.add("contract-schema-enum-mismatch", "trustLevel is missing or unknown", path="$.trustLevel")
    for field in ("title", "menuName", "category"):
        if not isinstance(document.get(field), str):
            report.add("contract-schema-type-mismatch", f"{field} must be a string", path=f"$.{field}")
    if not isinstance(document.get("keywords"), list) or not all(isinstance(item, str) for item in document["keywords"]):
        report.add("contract-schema-type-mismatch", "keywords must be strings", path="$.keywords")
    if not isinstance(document.get("searchTokens"), list) or not all(
        isinstance(item, str) and item.isascii() for item in document.get("searchTokens", [])
    ):
        report.add("contract-schema-type-mismatch", "searchTokens must be stable ASCII tokens", path="$.searchTokens")
    if not isinstance(document.get("graphKinds"), list) or not document.get("graphKinds"):
        report.add("contract-schema-required", "graphKinds must be a non-empty array", path="$.graphKinds")
    if not isinstance(document.get("graphSchemas"), list) or not document.get("graphSchemas"):
        report.add("contract-schema-required", "graphSchemas must be a non-empty array", path="$.graphSchemas")

    pins = document.get("pins")
    if not isinstance(pins, list) or not pins:
        report.add("contract-schema-required", "pins must be a non-empty template array", path="$.pins")
    else:
        for index, pin in enumerate(pins):
            path = f"$.pins[{index}]"
            if not isinstance(pin, dict):
                report.add("contract-schema-type-mismatch", "pin template must be an object", path=path)
                continue
            for key in PIN_TEMPLATE_REQUIRED:
                if key not in pin:
                    report.add("contract-schema-required", f"pin template requires {key}", path=path)
            if pin.get("direction") not in PIN_DIRECTIONS:
                report.add("graphpatch-pin-direction-mismatch", "pin template direction is invalid", path=f"{path}.direction")
            pin_type = pin.get("type") if isinstance(pin.get("type"), dict) else {}
            if pin.get("kind") == "exec" and pin_type.get("category") != "exec":
                report.add("graphpatch-pin-type-incompatible", "exec pins need type.category='exec'", path=f"{path}.type")
            if pin.get("kind") == "data" and pin_type.get("category") == "exec":
                report.add("graphpatch-pin-type-incompatible", "data pins must not use type.category='exec'", path=f"{path}.type")
            for key in PIN_TYPE_FIELDS:
                if key not in pin_type and key.startswith("is"):
                    report.add("contract-schema-required", f"pin type requires {key}", path=f"{path}.type")
            if not isinstance(pin.get("dynamic", False), bool) or not isinstance(pin.get("variadic", False), bool):
                report.add("contract-schema-type-mismatch", "dynamic/variadic must be boolean", path=path)

    source = document.get("source") if isinstance(document.get("source"), dict) else {}
    if source.get("kind") != "FBlueprintActionDatabase":
        report.add("graphpatch-descriptor-not-found", "source.kind must be FBlueprintActionDatabase", path="$.source.kind")
    if not is_fingerprint(source.get("actionDatabaseFingerprint")):
        report.add("graphpatch-descriptor-not-found", "source.actionDatabaseFingerprint must be sha256:<16 hex>", path="$.source.actionDatabaseFingerprint")
    if not is_fingerprint(document.get("descriptorDigest", "")):
        report.add("graphpatch-descriptor-not-found", "descriptorDigest must be sha256:<16 hex>", path="$.descriptorDigest")
    elif document.get("descriptorDigest") != node_descriptor_digest(document):
        report.add(
            "graphpatch-descriptor-not-found",
            "descriptorDigest does not match the descriptor projection",
            path="$.descriptorDigest",
            context={"expected": node_descriptor_digest(document)},
        )
    return report


def validate_operation_descriptor(document: dict[str, Any]) -> ValidationReport:
    report = ValidationReport()
    if not isinstance(document, dict):
        report.add("contract-schema-invalid", "OperationDescriptor must be an object", path="$")
        return report
    _check_version(
        report,
        document,
        namespace=OPERATION_DESCRIPTOR_NAMESPACE,
        minor=0,
        code="graphpatch-schema-version-unsupported",
    )
    if not isinstance(document.get("name"), str) or OPERATION_NAME_RE.fullmatch(document["name"]) is None:
        report.add("contract-schema-pattern", "name must look like domain.operation", path="$.name")
    if not isinstance(document.get("version"), str) or SEMVER_RE.fullmatch(document["version"]) is None:
        report.add("contract-schema-pattern", "version must be semver M.m.p", path="$.version")
    if document.get("domain") not in ("graph", "asset", "memory", "layout", "extension"):
        report.add("contract-schema-enum-mismatch", "domain is missing or unknown", path="$.domain")
    if not isinstance(document.get("targetKind"), str) or not document.get("targetKind"):
        report.add("contract-schema-required", "targetKind is required", path="$.targetKind")
    for schema_field in ("parametersSchema", "outputsSchema"):
        schema = document.get(schema_field)
        if not isinstance(schema, dict):
            report.add("contract-schema-required", f"{schema_field} is required", path=f"$.{schema_field}")
            continue
        try:
            check_schema(schema, path=f"$.{schema_field}")
        except SchemaError as exc:
            report.add(exc.code, exc.message, path=exc.path)
    if not isinstance(document.get("capabilities"), list) or not document.get("capabilities"):
        report.add("contract-schema-required", "capabilities must be non-empty", path="$.capabilities")
    if not isinstance(document.get("readOnly"), bool):
        report.add("contract-schema-type-mismatch", "readOnly must be boolean", path="$.readOnly")
    if document.get("risk") not in RISK_LEVELS:
        report.add("contract-schema-enum-mismatch", "risk must be R0..R3", path="$.risk")

    requires = document.get("requires") if isinstance(document.get("requires"), dict) else {}
    for key in ("editor", "transaction", "compile", "save"):
        if not isinstance(requires.get(key), bool):
            report.add("contract-schema-type-mismatch", f"requires.{key} must be boolean", path=f"$.requires.{key}")
    if requires.get("revisionScope") not in REVISION_SCOPES:
        report.add("contract-schema-enum-mismatch", "requires.revisionScope is invalid", path="$.requires.revisionScope")

    conditions = load_condition_codes()
    for field, kind in (("preconditions", "precondition"), ("postconditions", "postcondition")):
        values = document.get(field)
        if not isinstance(values, list) or not values:
            report.add("contract-schema-required", f"{field} must be a non-empty array", path=f"$.{field}")
            continue
        for index, code in enumerate(values):
            entry = conditions.get(str(code))
            if entry is None or entry.get("kind") != kind:
                report.add("graphpatch-operation-not-allowed", f"Unknown {kind} code: {code!r}", path=f"$.{field}[{index}]")
    registry = load_registry()
    errors = document.get("errorCodes")
    if not isinstance(errors, list) or not errors:
        report.add("contract-schema-required", "errorCodes must be non-empty", path="$.errorCodes")
    else:
        for index, code in enumerate(errors):
            if not registry.is_known(str(code)):
                report.add("contract-error-code-unknown", f"Unknown error code: {code!r}", path=f"$.errorCodes[{index}]")

    if document.get("rollback") not in ROLLBACK_KINDS:
        report.add("contract-schema-enum-mismatch", "rollback is invalid", path="$.rollback")
    if document.get("idempotency") not in ("required", "optional", "none"):
        report.add("contract-schema-enum-mismatch", "idempotency is invalid", path="$.idempotency")
    if document.get("implementation") not in CAPABILITIES:
        report.add("contract-schema-enum-mismatch", "implementation must match capability enum", path="$.implementation")

    evidence = document.get("evidence") if isinstance(document.get("evidence"), dict) else {}
    for field in ("required", "optional"):
        values = evidence.get(field, [])
        if not isinstance(values, list) or not all(value in EVIDENCE_KINDS for value in values):
            report.add("contract-schema-enum-mismatch", f"evidence.{field} contains an unknown artifact kind", path=f"$.evidence.{field}")
    transaction = document.get("transaction") if isinstance(document.get("transaction"), dict) else {}
    if transaction.get("kind") not in TRANSACTION_KINDS:
        report.add("contract-schema-enum-mismatch", "transaction.kind is invalid", path="$.transaction.kind")
    if transaction.get("snapshot") not in SNAPSHOT_KINDS:
        report.add("contract-schema-enum-mismatch", "transaction.snapshot is invalid", path="$.transaction.snapshot")
    if transaction.get("rollback") not in ROLLBACK_KINDS:
        report.add("contract-schema-enum-mismatch", "transaction.rollback is invalid", path="$.transaction.rollback")

    limits = document.get("limits") if isinstance(document.get("limits"), dict) else {}
    for key in ("maxOperationsPerInvocation", "maxAffectedNodes", "timeoutMs"):
        if not isinstance(limits.get(key), int) or isinstance(limits.get(key), bool) or limits.get(key) <= 0:
            report.add("contract-schema-type-mismatch", f"limits.{key} must be a positive integer", path=f"$.limits.{key}")
    if not isinstance(document.get("minHostVersion"), str) or not document.get("minHostVersion"):
        report.add("contract-schema-required", "minHostVersion is required", path="$.minHostVersion")
    if not isinstance(document.get("deprecated"), bool):
        report.add("contract-schema-type-mismatch", "deprecated must be boolean", path="$.deprecated")
    if "requiresExplicitConfirmation" in document and not isinstance(document["requiresExplicitConfirmation"], bool):
        report.add("contract-schema-type-mismatch", "requiresExplicitConfirmation must be boolean", path="$.requiresExplicitConfirmation")

    # Validate the descriptor against its own JSON Schema when available.
    schema_path = SCHEMA_DIR / "operation-descriptor.schema.json"
    if schema_path.exists():
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        for issue in validate_document(document, schema, strict=True):
            report.add(issue["code"], issue["message"], path=issue["path"], severity="error")
    return report


def canonical_discovery_key(document: dict[str, Any]) -> str:
    return canonical_json(discovery_key_projection(document.get("discoveryKey", {})))
