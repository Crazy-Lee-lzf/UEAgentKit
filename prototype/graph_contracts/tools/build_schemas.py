"""Generate the machine-readable JSON Schemas for the G0.1 candidate.

All schemas use only the frozen stdlib subset validated by
``ueak_contract.schema.check_schema``.  Run:
    py -3.12 tools/build_schemas.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas"
SCHEMAS.mkdir(parents=True, exist_ok=True)

GUID = "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
SHA = "^sha256:[0-9a-f]{64}$"
FINGERPRINT = "^sha256:[0-9a-f]{16}$"
GRAPH_ID = "^g:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|name:[A-Za-z_][A-Za-z0-9_]{0,63})$"
NODE_ID = "^n:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|obj:[A-Za-z_][A-Za-z0-9_]{0,127})$"
PIN_ID = "^p:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|dup:[0-9a-f-]{36}:[0-9a-f-]{36})$"
EDGE_ID = "^e:p:(dup:)?[0-9a-f-]{36}(:[0-9a-f-]{36})?--p:(dup:)?[0-9a-f-]{36}(:[0-9a-f-]{36})?$"
TEMP_ID = "^tmp:[A-Za-z0-9_-]{1,32}$"
ASSET_PATH = "^/Game/[A-Za-z0-9_./-]{1,240}$"


def obj(properties: dict, required: list[str], *, additional: bool = False, **annotations) -> dict:
    schema = {"type": "object", "additionalProperties": additional}
    if properties:
        schema["properties"] = properties
    if required:
        schema["required"] = required
    schema.update(annotations)
    return schema


def arr(items: dict, **annotations) -> dict:
    schema = {"type": "array", "items": items}
    schema.update(annotations)
    return schema


def enum(values: list) -> dict:
    return {"enum": values}


def ref_string(pattern: str) -> dict:
    return {"type": "string", "pattern": pattern}


typ = lambda names: {"type": names}  # noqa: E731


def pin_type() -> dict:
    return obj(
        {
            "category": typ("string"),
            "subCategory": typ("string"),
            "subCategoryObject": typ("string"),
            "container": typ("string"),
            "isReference": typ("boolean"),
            "isConst": typ("boolean"),
            "isWeakPointer": typ("boolean"),
        },
        ["category", "subCategory", "subCategoryObject", "container", "isReference", "isConst", "isWeakPointer"],
    )


TYPED_GRAPH = obj(
    {
        "schemaVersion": ref_string("^ueak\\.typedgraph/1\\.[0-9]+$"),
        "graphId": ref_string(GRAPH_ID),
        "asset": obj(
            {
                "path": ref_string(ASSET_PATH),
                "class": typ("string"),
                "revision": ref_string("^(sha256:[0-9a-f]{64})?$"),
                "revisionPolicy": enum(["executor-computed", "index-snapshot"]),
                "dirty": typ("boolean"),
            },
            ["path", "class", "revision", "revisionPolicy", "dirty"],
        ),
        "graph": obj(
            {
                "name": typ("string"),
                "guid": ref_string(GUID),
                "kind": enum(["event", "function", "macro", "ubergraph", "delegate-signature", "other"]),
                "schema": typ("string"),
                "editable": typ("boolean"),
            },
            ["name", "guid", "kind", "schema", "editable"],
        ),
        "source": obj(
            {
                "kind": enum(["live-editor", "commandlet-export", "test-fixture"]),
                "sessionId": typ("string"),
                "exporterSchemaVersion": typ("string"),
                "pluginBuildHash": typ("string"),
                "engineVersion": typ("string"),
                "exportedAtUtc": typ("string"),
            },
            ["kind", "sessionId", "exporterSchemaVersion", "pluginBuildHash", "engineVersion", "exportedAtUtc"],
        ),
        "limits": obj(
            {
                "maxNodes": typ("integer"),
                "maxPins": typ("integer"),
                "includedNodeCount": typ("integer"),
                "omittedNodeCount": typ("integer"),
                "selection": enum(["nodeId-ascending"]),
                "truncated": typ("boolean"),
            },
            ["maxNodes", "maxPins", "truncated"],
        ),
        "nodes": arr(
            obj(
                {
                    "nodeId": ref_string(NODE_ID),
                    "nodeGuid": ref_string(GUID),
                    "objectPath": typ("string"),
                    "classPath": typ("string"),
                    "title": typ("string"),
                    "kind": enum(["event", "call", "variable-get", "variable-set", "macro", "cast", "comment", "reroute", "other"]),
                    "nodeIdSource": enum(["guid", "object-name"]),
                    "flags": obj(
                        {
                            "enabled": typ("boolean"),
                            "pure": typ("boolean"),
                            "reroute": typ("boolean"),
                            "comment": typ("boolean"),
                            "latent": typ("boolean"),
                        },
                        ["enabled", "pure", "reroute", "comment", "latent"],
                    ),
                    "position": obj({"x": typ("integer"), "y": typ("integer")}, ["x", "y"]),
                    "size": obj(
                        {
                            "width": typ("integer"),
                            "height": typ("integer"),
                            "source": enum(["measured", "stored", "estimated", "unknown"]),
                        },
                        ["width", "height", "source"],
                    ),
                    "pins": arr(ref_string(PIN_ID)),
                    "semanticHash": ref_string(SHA),
                },
                ["nodeId", "nodeGuid", "classPath", "kind", "flags", "position", "size", "pins"],
            )
        ),
        "pins": arr(
            obj(
                {
                    "pinId": ref_string(PIN_ID),
                    "nodeId": ref_string(NODE_ID),
                    "name": typ("string"),
                    "friendlyName": typ("string"),
                    "direction": enum(["input", "output"]),
                    "kind": enum(["exec", "data"]),
                    "type": pin_type(),
                    "index": typ("integer"),
                    "defaults": obj(
                        {"value": typ("string"), "object": typ("string"), "text": typ("string")},
                        ["value", "object", "text"],
                    ),
                    "flags": obj(
                        {
                            "hidden": typ("boolean"),
                            "advanced": typ("boolean"),
                            "connectable": typ("boolean"),
                            "orphaned": typ("boolean"),
                            "defaultReadOnly": typ("boolean"),
                            "defaultIgnored": typ("boolean"),
                        },
                        ["hidden", "advanced", "connectable", "orphaned", "defaultReadOnly", "defaultIgnored"],
                    ),
                    "links": arr(ref_string(PIN_ID)),
                    "pinIdSource": enum(["native", "derived"]),
                    "pinGuidTransient": typ("boolean"),
                },
                ["pinId", "nodeId", "name", "direction", "kind", "type", "index", "defaults", "flags", "links"],
            )
        ),
        "edges": arr(
            obj(
                {
                    "edgeId": ref_string(EDGE_ID),
                    "from": obj({"nodeId": ref_string(NODE_ID), "pinId": ref_string(PIN_ID)}, ["nodeId", "pinId"]),
                    "to": obj({"nodeId": ref_string(NODE_ID), "pinId": ref_string(PIN_ID)}, ["nodeId", "pinId"]),
                    "kind": enum(["exec", "data"]),
                    "origin": enum(["pin-link", "patch"]),
                    "status": enum(["resolved", "dangling", "duplicate", "wrong-kind"]),
                },
                ["edgeId", "from", "to", "kind", "origin", "status"],
            )
        ),
        "comments": arr(
            obj(
                {
                    "nodeId": ref_string(NODE_ID),
                    "text": typ("string"),
                    "bounds": obj(
                        {"x": typ("integer"), "y": typ("integer"), "width": typ("integer"), "height": typ("integer")},
                        ["x", "y", "width", "height"],
                    ),
                    "containedNodeIds": arr(ref_string(NODE_ID)),
                },
                ["nodeId", "text", "bounds", "containedNodeIds"],
            )
        ),
        "diagnostics": arr(
            obj(
                {
                    "code": typ("string"),
                    "severity": enum(["info", "warning", "error"]),
                    "message": typ("string"),
                    "path": typ("string"),
                    "context": obj({}, [], additional=True),
                },
                ["code", "severity", "message", "path"],
            )
        ),
        "semanticHash": ref_string(SHA),
        "layoutHash": ref_string(SHA),
    },
    [
        "schemaVersion",
        "graphId",
        "asset",
        "graph",
        "source",
        "limits",
        "nodes",
        "pins",
        "edges",
        "comments",
        "diagnostics",
        "semanticHash",
        "layoutHash",
    ],
)


def node_descriptor() -> dict:
    pin_template = obj(
        {
            "name": typ("string"),
            "direction": enum(["input", "output"]),
            "kind": enum(["exec", "data"]),
            "type": pin_type(),
            "optional": typ("boolean"),
            "dynamic": typ("boolean"),
            "variadic": typ("boolean"),
            "default": typ("string"),
            "hidden": typ("boolean"),
            "advanced": typ("boolean"),
            "connectable": typ("boolean"),
            "defaultReadOnly": typ("boolean"),
            "defaultIgnored": typ("boolean"),
        },
        ["name", "direction", "kind", "type"],
    )
    return obj(
        {
            "schemaVersion": ref_string("^ueak\\.node-descriptor/1\\.[0-9]+$"),
            "actionId": ref_string("^action:[0-9a-f]{32}$"),
            "discoveryKey": obj(
                {
                    "nodeClass": typ("string"),
                    "spawnerClass": typ("string"),
                    "graphSchema": typ("string"),
                    "actionKind": enum(
                        ["callFunction", "event", "variableGet", "variableSet", "macro", "cast", "delegate", "component", "special"]
                    ),
                    "payload": obj(
                        {
                            "functionPath": typ("string"),
                            "eventName": typ("string"),
                            "variableName": typ("string"),
                            "macroGraph": typ("string"),
                            "targetClass": typ("string"),
                            "delegateName": typ("string"),
                            "componentClass": typ("string"),
                            "specialKind": typ("string"),
                        },
                        [],
                    ),
                },
                ["nodeClass", "spawnerClass", "graphSchema", "actionKind", "payload"],
            ),
            "title": typ("string"),
            "menuName": typ("string"),
            "category": typ("string"),
            "keywords": arr(typ("string")),
            "searchTokens": arr(typ("string")),
            "graphKinds": arr(typ("string")),
            "graphSchemas": arr(typ("string")),
            "pins": arr(pin_template),
            "constraints": obj(
                {
                    "requiresBlueprintContext": typ("boolean"),
                    "requiresTarget": typ("boolean"),
                    "requiresWorldContext": typ("boolean"),
                    "pure": typ("boolean"),
                    "latent": typ("boolean"),
                    "variadic": typ("boolean"),
                    "hiddenByDefault": arr(typ("string")),
                },
                ["requiresBlueprintContext", "requiresTarget", "requiresWorldContext", "pure", "latent", "variadic", "hiddenByDefault"],
            ),
            "source": obj(
                {
                    "kind": enum(["FBlueprintActionDatabase"]),
                    "engineVersion": typ("string"),
                    "actionDatabaseFingerprint": ref_string(FINGERPRINT),
                    "pluginBuildHash": typ("string"),
                    "discoveredAtUtc": typ("string"),
                },
                ["kind", "engineVersion", "actionDatabaseFingerprint", "pluginBuildHash", "discoveredAtUtc"],
            ),
            "risk": enum(["R0", "R1", "R2", "R3"]),
            "capability": enum(["core", "extension-e1", "extension-e2", "extension-e3"]),
            "trustLevel": enum(["official", "project", "third-party"]),
            "descriptorDigest": ref_string(FINGERPRINT),
        },
        [
            "schemaVersion",
            "actionId",
            "discoveryKey",
            "title",
            "menuName",
            "category",
            "keywords",
            "searchTokens",
            "graphKinds",
            "graphSchemas",
            "pins",
            "constraints",
            "source",
            "risk",
            "capability",
            "trustLevel",
            "descriptorDigest",
        ],
    )


def operation_descriptor() -> dict:
    return obj(
        {
            "schemaVersion": ref_string("^ueak\\.operation/1\\.[0-9]+$"),
            "name": ref_string(r"^[a-z][a-z0-9]*(\.[a-z][a-zA-Z0-9_]*)+$"),
            "version": ref_string("^\\d+\\.\\d+\\.\\d+$"),
            "domain": enum(["graph", "asset", "memory", "layout", "extension"]),
            "targetKind": typ("string"),
            "parametersSchema": typ("object"),
            "outputsSchema": typ("object"),
            "capabilities": arr(typ("string")),
            "readOnly": typ("boolean"),
            "risk": enum(["R0", "R1", "R2", "R3"]),
            "requires": obj(
                {
                    "editor": typ("boolean"),
                    "transaction": typ("boolean"),
                    "compile": typ("boolean"),
                    "save": typ("boolean"),
                    "policyKeys": arr(typ("string")),
                    "revisionScope": enum(["none", "asset", "graph", "asset+graph"]),
                },
                ["editor", "transaction", "compile", "save", "policyKeys", "revisionScope"],
            ),
            "preconditions": arr(typ("string")),
            "postconditions": arr(typ("string")),
            "rollback": enum(["reverse-ops", "undo", "none"]),
            "idempotency": enum(["required", "optional", "none"]),
            "errorCodes": arr(typ("string")),
            "implementation": enum(["core", "extension-e1", "extension-e2", "extension-e3"]),
            "evidence": obj(
                {
                    "required": arr(typ("string")),
                    "optional": arr(typ("string")),
                },
                ["required", "optional"],
            ),
            "transaction": obj(
                {
                    "kind": enum(["value", "structure"]),
                    "snapshot": enum(["io", "graph"]),
                    "rollback": enum(["reverse-ops", "undo", "none"]),
                },
                ["kind", "snapshot", "rollback"],
            ),
            "limits": obj(
                {
                    "maxOperationsPerInvocation": typ("integer"),
                    "maxAffectedNodes": typ("integer"),
                    "timeoutMs": typ("integer"),
                },
                ["maxOperationsPerInvocation", "maxAffectedNodes", "timeoutMs"],
            ),
            "minHostVersion": typ("string"),
            "deprecated": typ("boolean"),
            "replacement": typ("string"),
            "requiresExplicitConfirmation": typ("boolean"),
        },
        [
            "schemaVersion",
            "name",
            "version",
            "domain",
            "targetKind",
            "parametersSchema",
            "outputsSchema",
            "capabilities",
            "readOnly",
            "risk",
            "requires",
            "preconditions",
            "postconditions",
            "rollback",
            "idempotency",
            "errorCodes",
            "implementation",
            "evidence",
            "transaction",
            "limits",
            "minHostVersion",
            "deprecated",
        ],
    )


def graphpatch() -> dict:
    common_props = {
        "operationId": ref_string("^[A-Za-z0-9_.:-]{1,64}$"),
        "dependsOn": arr(typ("string")),
    }

    def operation(operation_type: str, specific: dict, required_extra: list[str]) -> dict:
        properties = dict(common_props)
        properties.update({"type": {"const": operation_type}})
        properties.update(specific)
        return obj(properties, ["operationId", "type"] + required_extra)

    point = obj({"x": typ("integer"), "y": typ("integer")}, ["x", "y"])
    pin_ref = obj(
        {
            "node": typ("string"),
            "pin": typ("string"),
            "pinId": ref_string(PIN_ID),
            "direction": enum(["input", "output"]),
            "pinIndex": typ("integer"),
        },
        [],
    )
    operations = [
        operation(
            "createNode",
            {
                "tempId": ref_string(TEMP_ID),
                "descriptor": obj({"actionId": ref_string("^action:[0-9a-f]{32}$")}, ["actionId"]),
                "position": point,
                "pinDefaults": obj({}, [], additional=True),
            },
            ["tempId", "descriptor"],
        ),
        operation(
            "connectPins",
            {
                "from": pin_ref,
                "to": pin_ref,
                "connectionPolicy": enum(["reject-replacement"]),
                "allowImplicitConversion": typ("boolean"),
                "allowSelfLoop": typ("boolean"),
            },
            ["from", "to"],
        ),
        operation(
            "disconnectPins",
            {"from": pin_ref, "to": pin_ref, "expectedEdgeId": ref_string(EDGE_ID)},
            ["from", "to"],
        ),
        operation(
            "setPinDefault",
            {
                "pin": pin_ref,
                "value": typ("string"),
                "valueType": enum(
                    ["string", "text", "name", "int", "int64", "byte", "float", "double", "real", "bool", "object", "class", "struct", "enum"]
                ),
                "expectedOldValue": typ("string"),
            },
            ["pin", "value", "valueType"],
        ),
        operation("setNodePosition", {"nodeId": typ("string"), "x": typ("integer"), "y": typ("integer")}, ["nodeId", "x", "y"]),
        operation("setNodeComment", {"nodeId": typ("string"), "comment": typ("string")}, ["nodeId", "comment"]),
        operation(
            "setNodeEnabled",
            {"nodeId": typ("string"), "enabled": typ("boolean"), "confirmed": typ("boolean")},
            ["nodeId", "enabled", "confirmed"],
        ),
        operation(
            "setCommentBounds",
            {
                "nodeId": typ("string"),
                "bounds": obj(
                    {"x": typ("integer"), "y": typ("integer"), "width": typ("integer"), "height": typ("integer")},
                    ["x", "y", "width", "height"],
                ),
            },
            ["nodeId", "bounds"],
        ),
    ]
    return obj(
        {
            "schemaVersion": ref_string("^ueak\\.graphpatch/1\\.[0-9]+$"),
            "patchId": typ("string"),
            "idempotencyKey": typ("string"),
            "projectName": typ("string"),
            "target": obj(
                {
                    "assetPath": ref_string(ASSET_PATH),
                    "graphId": ref_string("^g:[0-9a-f-]{36}$"),
                    "graphGuid": ref_string(GUID),
                    "graphName": typ("string"),
                    "graphKind": typ("string"),
                    "baseRevision": ref_string("^(sha256:[0-9a-f]{64})?$"),
                    "baseGraphHash": ref_string(SHA),
                    "revisionPolicy": enum(["executor-computed", "index-snapshot"]),
                    "editorSessionId": typ("string"),
                    "graphpatchProfile": enum(["structure-default", "structure-g2", "layout-only"]),
                },
                ["assetPath", "graphId", "baseRevision", "baseGraphHash", "revisionPolicy", "editorSessionId"],
            ),
            "conflictPolicy": enum(["reject-if-changed"]),
            "externalDependencies": arr(
                obj(
                    {
                        "kind": enum(["asset", "changeSet", "editorSession"]),
                        "ref": typ("string"),
                        "expectedRevision": ref_string("^(sha256:[0-9a-f]{64})?$"),
                    },
                    ["kind", "ref"],
                )
            ),
            "operations": {"type": "array", "items": {"oneOf": operations}},
            "layout": obj(
                {
                    "mode": enum(["incremental", "selection", "full"]),
                    "scope": arr(typ("string")),
                    "options": obj({}, [], additional=True),
                },
                ["mode"],
            ),
            "expect": obj(
                {
                    "nodeCountDelta": typ("integer"),
                    "edgeCountDelta": typ("integer"),
                    "pinDefaultDelta": typ("integer"),
                },
                [],
            ),
            "allowDerivedPinIdentity": typ("boolean"),
            "metadata": obj({}, [], additional=True),
        },
        [
            "schemaVersion",
            "patchId",
            "idempotencyKey",
            "projectName",
            "target",
            "conflictPolicy",
            "externalDependencies",
            "operations",
            "metadata",
        ],
    )


def layout_plan() -> dict:
    return obj(
        {
            "schemaVersion": ref_string("^ueak\\.layoutplan/1\\.[0-9]+$"),
            "planId": typ("string"),
            "baseGraphHash": ref_string(SHA),
            "mode": enum(["incremental", "selection", "full"]),
            "seed": typ("integer"),
            "scopeNodeIds": arr(ref_string(NODE_ID)),
            "fixedNodeIds": arr(ref_string(NODE_ID)),
            "positions": arr(
                obj(
                    {
                        "nodeId": ref_string(NODE_ID),
                        "x": typ("integer"),
                        "y": typ("integer"),
                        "size": obj(
                            {
                                "width": typ("integer"),
                                "height": typ("integer"),
                                "source": enum(["measured", "stored", "estimated", "unknown"]),
                            },
                            ["width", "height", "source"],
                        ),
                    },
                    ["nodeId", "x", "y", "size"],
                )
            ),
            "movedNodeIds": arr(ref_string(NODE_ID)),
            "islands": arr(
                obj(
                    {
                        "islandId": typ("string"),
                        "nodeIds": arr(typ("string")),
                        "bbox": obj(
                            {"x": typ("integer"), "y": typ("integer"), "width": typ("integer"), "height": typ("integer")},
                            ["x", "y", "width", "height"],
                        ),
                    },
                    ["islandId", "nodeIds", "bbox"],
                )
            ),
            "sccs": arr(
                obj(
                    {
                        "sccId": typ("string"),
                        "islandId": typ("string"),
                        "nodeIds": arr(typ("string")),
                        "backEdges": arr(typ("string")),
                    },
                    ["sccId", "islandId", "nodeIds", "backEdges"],
                )
            ),
            "warnings": arr(
                obj(
                    {
                        "code": typ("string"),
                        "severity": enum(["info", "warning", "error"]),
                        "message": typ("string"),
                        "path": typ("string"),
                        "context": obj({}, [], additional=True),
                    },
                    ["code", "severity", "message", "path"],
                )
            ),
            "metrics": obj(
                {
                    "crossings": typ("integer"),
                    "edgeLength": typ("integer"),
                    "overlapCount": typ("integer"),
                    "area": typ("integer"),
                    "aspectRatio": typ("number"),
                    "bbox": obj({"width": typ("integer"), "height": typ("integer")}, ["width", "height"]),
                },
                ["crossings", "edgeLength", "overlapCount", "area", "aspectRatio", "bbox"],
            ),
            "packing": obj(
                {
                    "targetWidth": typ("integer"),
                    "spacing": typ("integer"),
                    "algorithm": enum(["skyline-v1"]),
                },
                ["targetWidth", "spacing", "algorithm"],
            ),
            "layoutHash": ref_string(SHA),
            "sizeBeforeHash": ref_string(SHA),
            "sizeAfterHash": ref_string(SHA),
            "status": enum(["ok", "rejected"]),
            "errorCode": typ("string"),
            "evidenceLevel": enum(["same-process", "independent-reload"]),
            "acceptanceEligible": typ("boolean"),
            "requiredAcceptanceEvidence": enum(["independent-reload"]),
            "timingMs": typ("integer"),
        },
        [
            "schemaVersion",
            "planId",
            "baseGraphHash",
            "mode",
            "seed",
            "scopeNodeIds",
            "fixedNodeIds",
            "positions",
            "movedNodeIds",
            "islands",
            "sccs",
            "warnings",
            "metrics",
            "packing",
            "layoutHash",
            "sizeBeforeHash",
            "sizeAfterHash",
            "status",
            "errorCode",
            "evidenceLevel",
            "acceptanceEligible",
            "requiredAcceptanceEvidence",
            "timingMs",
        ],
    )


def change_set_idempotency() -> dict:
    """D-07 candidate ChangeSet 2.1 optional idempotency block."""

    return obj(
        {
            "schemaVersion": {"const": "ueak.change-set-idempotency/1.0"},
            "changeSetSchemaVersion": {"const": "2.1"},
            "scopeKey": ref_string("^sha256:[0-9a-f]{64}$"),
            "projectName": typ("string"),
            "assetPath": ref_string(ASSET_PATH),
            "graphId": ref_string("^g:[0-9a-f-]{36}$"),
            "idempotencyKey": typ("string"),
            "patchId": typ("string"),
            "payloadHash": ref_string(SHA),
            "state": enum(["reserved", "applied", "failed", "indeterminate"]),
            "reservedAtUtc": typ("string"),
            "updatedAtUtc": typ("string"),
            "transactionId": typ("string"),
            "actualGraphHash": ref_string(SHA),
            "applyReceipt": typ("string"),
            "failureCode": typ("string"),
            "result": obj({}, [], additional=True),
        },
        [
            "schemaVersion",
            "changeSetSchemaVersion",
            "scopeKey",
            "projectName",
            "assetPath",
            "graphId",
            "idempotencyKey",
            "patchId",
            "payloadHash",
            "state",
            "reservedAtUtc",
            "updatedAtUtc",
        ],
    )


def write(name: str, schema: dict) -> None:
    (SCHEMAS / name).write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def main() -> None:
    write("typedgraph.schema.json", TYPED_GRAPH)
    write("node-descriptor.schema.json", node_descriptor())
    write("operation-descriptor.schema.json", operation_descriptor())
    write("graphpatch.schema.json", graphpatch())
    write("layout-plan.schema.json", layout_plan())
    write("change-set-idempotency.schema.json", change_set_idempotency())
    print("wrote 6 schemas")


if __name__ == "__main__":
    main()
