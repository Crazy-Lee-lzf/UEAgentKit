"""Deterministically build the G0.1 fixtures, manifest and hash vectors.

Run from ``contract_candidate``:
    py -3.12 tools/build_fixtures.py

No randomness, no clock, no Unreal.  Re-running must produce byte-identical
JSON (tests assert the frozen hashes and layout vectors).
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ueak_contract import descriptors, graph_patch, ids, layout, typed_graph  # noqa: E402
from ueak_contract.canonical import canonical_json  # noqa: E402
from ueak_contract.schema import check_schema, validate_document  # noqa: E402

FIXTURES = ROOT / "fixtures"
VECTORS = ROOT / "vectors"
SCHEMAS = ROOT / "schemas"
for directory in (FIXTURES / "typedgraph", FIXTURES / "graphpatch", FIXTURES / "layout", FIXTURES / "descriptors", VECTORS):
    directory.mkdir(parents=True, exist_ok=True)

ASSET_PATH = "/Game/UEAgentKitTests/BP_GraphPatch_Target.BP_GraphPatch_Target"
SESSION = "editor-session-test"
PLUGIN_HASH = "0123456789abcdef"
EXPORTED_AT = "2026-10-22T00:00:00.000Z"
EVENT_CLASS = "/Script/BlueprintGraph.K2Node_Event"
CALL_CLASS = "/Script/BlueprintGraph.K2Node_CallFunction"
VARIABLE_GET_CLASS = "/Script/BlueprintGraph.K2Node_VariableGet"
COMMENT_CLASS = "/Script/UnrealEd.EdGraphNode_Comment"
REROUTE_CLASS = "/Script/BlueprintGraph.K2Node_Knot"
SEQUENCE_CLASS = "/Script/BlueprintGraph.K2Node_ExecutionSequence"
PROMOTABLE_CLASS = "/Script/BlueprintGraph.K2Node_PromotableOperator"
BRANCH_CLASS = "/Script/BlueprintGraph.K2Node_IfThenElse"
K2_SCHEMA = "/Script/BlueprintGraph.EdGraphSchema_K2"


def sha_of(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def graph_guid(seed: int) -> str:
    return f"{seed:08x}-0000-4000-8000-{seed:012x}"


class Builder:
    def __init__(self, seed: int, graph_name: str = "EventGraph", kind: str = "event") -> None:
        self.counter = 0
        guid = graph_guid(seed)
        self.graph: dict[str, Any] = {
            "schemaVersion": typed_graph.TYPEDGRAPH_VERSION,
            "graphId": "g:" + guid,
            "asset": {
                "path": ASSET_PATH,
                "class": "/Script/Engine.Blueprint",
                "revision": sha_of(ASSET_PATH + "|" + guid),
                "revisionPolicy": "index-snapshot",
                "dirty": False,
            },
            "graph": {
                "name": graph_name,
                "guid": guid,
                "kind": kind,
                "schema": K2_SCHEMA,
                "editable": True,
            },
            "source": {
                "kind": "test-fixture",
                "sessionId": SESSION,
                "exporterSchemaVersion": "ueak.typedgraph-export/1",
                "pluginBuildHash": PLUGIN_HASH,
                "engineVersion": "5.6.1",
                "exportedAtUtc": EXPORTED_AT,
            },
            "limits": {
                "maxNodes": 5000,
                "maxPins": 50000,
                "includedNodeCount": 0,
                "omittedNodeCount": 0,
                "selection": "nodeId-ascending",
                "truncated": False,
            },
            "nodes": [],
            "pins": [],
            "edges": [],
            "comments": [],
            "diagnostics": [],
            "semanticHash": "",
            "layoutHash": "",
        }

    def next_guid(self) -> str:
        self.counter += 1
        return f"{self.counter:08x}-0000-4000-8000-{self.counter:012x}"

    def node(
        self,
        class_path: str,
        kind: str,
        x: int,
        y: int,
        *,
        title: str = "",
        comment: str = "",
        width: int = 208,
        height: int = 120,
        size_source: str = "stored",
        pure: bool = False,
        reroute: bool = False,
        is_comment: bool = False,
    ) -> dict[str, Any]:
        guid = self.next_guid()
        node = {
            "nodeId": "n:" + guid,
            "nodeGuid": guid,
            "objectPath": f"{ASSET_PATH}:{self.graph['graph']['name']}.{class_path.rsplit('.', 1)[-1]}_{self.counter}",
            "classPath": class_path,
            "title": title,
            "kind": kind,
            "nodeIdSource": "guid",
            "flags": {
                "enabled": True,
                "pure": pure,
                "reroute": reroute,
                "comment": is_comment,
                "latent": False,
            },
            "position": {"x": x, "y": y},
            "size": {"width": width, "height": height, "source": size_source},
            "pins": [],
        }
        self.graph["nodes"].append(node)
        return node

    def pin(
        self,
        node: dict[str, Any],
        name: str,
        direction: str,
        kind: str,
        category: str,
        *,
        default: str = "",
        hidden: bool = False,
        advanced: bool = False,
        default_read_only: bool = False,
        derived: bool = False,
        friendly_name: str = "",
        sub_category: str = "",
        sub_category_object: str = "",
        container: str = "none",
    ) -> dict[str, Any]:
        pin = {
            "pinId": "p:" + self.next_guid(),
            "nodeId": node["nodeId"],
            "name": name,
            "friendlyName": friendly_name,
            "direction": direction,
            "kind": kind,
            "type": {
                "category": category,
                "subCategory": sub_category,
                "subCategoryObject": sub_category_object,
                "container": container,
                "isReference": False,
                "isConst": False,
                "isWeakPointer": False,
            },
            "index": len(node["pins"]),
            "defaults": {"value": default, "object": "", "text": ""},
            "flags": {
                "hidden": hidden,
                "advanced": advanced,
                "connectable": True,
                "orphaned": False,
                "defaultReadOnly": default_read_only,
                "defaultIgnored": False,
            },
            "links": [],
            "pinIdSource": "derived" if derived else "native",
            "pinGuidTransient": bool(derived),
        }
        self.graph["pins"].append(pin)
        node["pins"].append(pin["pinId"])
        return pin

    def edge(self, source: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
        edge = {
            "edgeId": ids.edge_id(source["pinId"], target["pinId"]),
            "from": {"nodeId": source["nodeId"], "pinId": source["pinId"]},
            "to": {"nodeId": target["nodeId"], "pinId": target["pinId"]},
            "kind": source["kind"],
            "origin": "pin-link",
            "status": "resolved",
        }
        self.graph["edges"].append(edge)
        source["links"] = sorted(set(source["links"]) | {target["pinId"]})
        target["links"] = sorted(set(target["links"]) | {source["pinId"]})
        return edge

    def comment(self, node: dict[str, Any], text: str, bounds: dict[str, int], contained: list[str]) -> None:
        node["position"] = {"x": bounds["x"], "y": bounds["y"]}
        node["size"] = {"width": bounds["width"], "height": bounds["height"], "source": "stored"}
        self.graph["comments"].append(
            {"nodeId": node["nodeId"], "text": text, "bounds": dict(bounds), "containedNodeIds": sorted(contained)}
        )

    def finish(self) -> dict[str, Any]:
        self.graph["limits"]["includedNodeCount"] = len(self.graph["nodes"])
        built = typed_graph.build_typed_graph(self.graph)
        report = typed_graph.validate(built, require_hashes=True, require_apply_ready=False)
        if report.errors:
            raise AssertionError(f"Fixture {self.graph['graphId']} invalid: {report.errors[:3]}")
        return built


def make_empty() -> dict[str, Any]:
    return Builder(0x101).finish()


def make_single_chinese() -> dict[str, Any]:
    builder = Builder(0x102)
    node = builder.node(
        CALL_CLASS,
        "call",
        120,
        80,
        title="打印字符串",
        comment="中文注释：用于本地化哈希稳定性测试",
        pure=False,
    )
    builder.pin(node, "execute", "input", "exec", "exec")
    builder.pin(node, "then", "output", "exec", "exec")
    builder.pin(node, "InString", "input", "data", "string", default="你好，UEAgentKit", friendly_name="字符串")
    return builder.finish()


def make_linear() -> dict[str, Any]:
    builder = Builder(0x103)
    event = builder.node(EVENT_CLASS, "event", 0, 0, title="Event BeginPlay")
    print_a = builder.node(CALL_CLASS, "call", 320, 0, title="Print String A")
    print_b = builder.node(CALL_CLASS, "call", 720, 0, title="Print String B")
    event_then = builder.pin(event, "then", "output", "exec", "exec")
    a_exec = builder.pin(print_a, "execute", "input", "exec", "exec")
    a_then = builder.pin(print_a, "then", "output", "exec", "exec")
    a_string = builder.pin(print_a, "ReturnValue", "output", "data", "string")
    a_in = builder.pin(print_a, "InString", "input", "data", "string")
    b_exec = builder.pin(print_b, "execute", "input", "exec", "exec")
    b_then = builder.pin(print_b, "then", "output", "exec", "exec")
    b_in = builder.pin(print_b, "InString", "input", "data", "string")
    builder.edge(event_then, a_exec)
    builder.edge(a_then, b_exec)
    builder.edge(a_string, b_in)
    return builder.finish()


def make_dynamic() -> dict[str, Any]:
    builder = Builder(0x104)
    sequence = builder.node(SEQUENCE_CLASS, "other", 0, 0, title="Sequence")
    source = builder.node(VARIABLE_GET_CLASS, "variable-get", 0, 320, title="Get SharedValue", pure=True)
    print_node = builder.node(CALL_CLASS, "call", 480, 0, title="Print String")
    print_node_two = builder.node(CALL_CLASS, "call", 480, 220, title="Print String 2")
    promotable = builder.node(PROMOTABLE_CLASS, "other", 0, 640, title="Promotable Operator")
    seq_exec = builder.pin(sequence, "execute", "input", "exec", "exec")
    then_pins = [builder.pin(sequence, "then", "output", "exec", "exec") for _ in range(3)]
    source_value = builder.pin(source, "Value", "output", "data", "string")
    print_exec = builder.pin(print_node, "execute", "input", "exec", "exec")
    builder.pin(print_node, "then", "output", "exec", "exec")
    builder.pin(print_node, "InString", "input", "data", "string")
    builder.pin(print_node_two, "execute", "input", "exec", "exec")
    builder.pin(print_node_two, "then", "output", "exec", "exec")
    builder.pin(print_node_two, "InString", "input", "data", "string")
    derived = builder.pin(promotable, "ErrorTolerance", "input", "data", "", hidden=True, default_read_only=True, derived=True)
    builder.pin(promotable, "Result", "output", "data", "string")
    builder.edge(then_pins[0], print_exec)
    builder.graph["diagnostics"].append(
        {
            "code": "graph-pin-guid-derived",
            "severity": "info",
            "message": "ErrorTolerance uses MD5(nodeGuid|direction|pinName|transient-promotable-pin); not a native PinId",
            "path": "$.pins",
            "context": {"pinId": derived["pinId"], "pinIdSource": "derived"},
        }
    )
    return builder.finish()


def make_exec_vs_data() -> dict[str, Any]:
    builder = Builder(0x105)
    event_a = builder.node(EVENT_CLASS, "event", 0, 0, title="Event A")
    call_a = builder.node(CALL_CLASS, "call", 320, 0, title="Print A")
    event_b = builder.node(EVENT_CLASS, "event", 0, 400, title="Event B")
    call_b = builder.node(CALL_CLASS, "call", 320, 400, title="Print B")
    source = builder.node(VARIABLE_GET_CLASS, "variable-get", 0, 800, title="Shared Value", pure=True)
    event_a_then = builder.pin(event_a, "then", "output", "exec", "exec")
    a_exec = builder.pin(call_a, "execute", "input", "exec", "exec")
    a_in = builder.pin(call_a, "InString", "input", "data", "string")
    event_b_then = builder.pin(event_b, "then", "output", "exec", "exec")
    b_exec = builder.pin(call_b, "execute", "input", "exec", "exec")
    b_in = builder.pin(call_b, "InString", "input", "data", "string")
    source_value = builder.pin(source, "Value", "output", "data", "string")
    builder.edge(event_a_then, a_exec)
    builder.edge(event_b_then, b_exec)
    builder.edge(source_value, a_in)
    builder.edge(source_value, b_in)
    return builder.finish()


def make_exec_cycle() -> dict[str, Any]:
    builder = Builder(0x106)
    event = builder.node(EVENT_CLASS, "event", 0, 0, title="Event BeginPlay")
    call_a = builder.node(CALL_CLASS, "call", 320, 0, title="A")
    call_b = builder.node(CALL_CLASS, "call", 640, 0, title="B")
    event_then = builder.pin(event, "then", "output", "exec", "exec")
    a_exec = builder.pin(call_a, "execute", "input", "exec", "exec")
    a_then = builder.pin(call_a, "then", "output", "exec", "exec")
    b_exec = builder.pin(call_b, "execute", "input", "exec", "exec")
    b_then = builder.pin(call_b, "then", "output", "exec", "exec")
    builder.edge(event_then, a_exec)
    builder.edge(a_then, b_exec)
    builder.edge(b_then, a_exec)
    return builder.finish()


def make_comment_reroute() -> dict[str, Any]:
    builder = Builder(0x107)
    event = builder.node(EVENT_CLASS, "event", 40, 40, title="Event BeginPlay")
    reroute = builder.node(REROUTE_CLASS, "reroute", 320, 40, title="Reroute", reroute=True)
    print_node = builder.node(CALL_CLASS, "call", 560, 40, title="Print String")
    comment_node = builder.node(COMMENT_CLASS, "comment", 0, 0, width=0, height=0, is_comment=True)
    event_then = builder.pin(event, "then", "output", "exec", "exec")
    reroute_in = builder.pin(reroute, "in", "input", "exec", "exec")
    reroute_out = builder.pin(reroute, "out", "output", "exec", "exec")
    print_exec = builder.pin(print_node, "execute", "input", "exec", "exec")
    builder.pin(print_node, "then", "output", "exec", "exec")
    builder.edge(event_then, reroute_in)
    builder.edge(reroute_out, print_exec)
    builder.comment(
        comment_node,
        "执行流：事件到打印",
        {"x": -40, "y": -40, "width": 840, "height": 280},
        [event["nodeId"], reroute["nodeId"], print_node["nodeId"]],
    )
    return builder.finish()


def make_large() -> dict[str, Any]:
    builder = Builder(0x108)
    for island in range(10):
        event = builder.node(EVENT_CLASS, "event", 0, island * 240, title=f"Event {island}")
        event_then = builder.pin(event, "then", "output", "exec", "exec")
        previous_then = event_then
        for index in range(9):
            call = builder.node(
                CALL_CLASS,
                "call",
                320 + index * 320,
                island * 240,
                title=f"Print {island}-{index}",
            )
            call_exec = builder.pin(call, "execute", "input", "exec", "exec")
            call_then = builder.pin(call, "then", "output", "exec", "exec")
            builder.pin(call, "InString", "input", "data", "string")
            builder.edge(previous_then, call_exec)
            previous_then = call_then
    return builder.finish()


def make_duplicate_raw() -> dict[str, Any]:
    builder = Builder(0x109)
    first = builder.node(CALL_CLASS, "call", 0, 0, title="First")
    second = builder.node(CALL_CLASS, "call", 320, 0, title="Second")
    first_pin = builder.pin(first, "then", "output", "exec", "exec")
    second_pin = builder.pin(second, "then", "output", "exec", "exec")
    second_pin["pinId"] = first_pin["pinId"]
    second["pins"] = [second_pin["pinId"]]
    builder.graph["semanticHash"] = ""
    builder.graph["layoutHash"] = ""
    builder.graph["limits"]["includedNodeCount"] = 2
    return builder.graph


GRAPHS = {
    "tg_empty": make_empty,
    "tg_single_chinese": make_single_chinese,
    "tg_linear_exec": make_linear,
    "tg_dynamic_pins": make_dynamic,
    "tg_exec_vs_data": make_exec_vs_data,
    "tg_exec_cycle": make_exec_cycle,
    "tg_comment_reroute": make_comment_reroute,
    "tg_large_100": make_large,
}


def write_json(path: Path, document: Any) -> None:
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def descriptor_pin(name: str, direction: str, kind: str, category: str, **extra: Any) -> dict[str, Any]:
    pin = {
        "name": name,
        "direction": direction,
        "kind": kind,
        "type": {
            "category": category,
            "subCategory": "",
            "subCategoryObject": "",
            "container": "none",
            "isReference": False,
            "isConst": False,
            "isWeakPointer": False,
        },
    }
    pin.update(extra)
    return pin


def make_descriptors(descriptor_dir: Path) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    discovery = {
        "print": {
            "nodeClass": CALL_CLASS,
            "spawnerClass": "/Script/BlueprintGraph.BlueprintNodeSpawner",
            "graphSchema": K2_SCHEMA,
            "actionKind": "callFunction",
            "payload": {"functionPath": "/Script/Engine.KismetSystemLibrary:PrintString"},
        },
        "branch": {
            "nodeClass": BRANCH_CLASS,
            "spawnerClass": "/Script/BlueprintGraph.BlueprintNodeSpawner",
            "graphSchema": K2_SCHEMA,
            "actionKind": "special",
            "payload": {"specialKind": "branch"},
        },
        "source": {
            "nodeClass": VARIABLE_GET_CLASS,
            "spawnerClass": "/Script/BlueprintGraph.BlueprintNodeSpawner",
            "graphSchema": K2_SCHEMA,
            "actionKind": "variableGet",
            "payload": {"variableName": "SharedValue"},
        },
    }
    action_ids = {key: descriptors.make_action_id(value) for key, value in discovery.items()}
    fingerprint = descriptors.action_database_fingerprint(list(action_ids.values()))
    common_source = {
        "kind": "FBlueprintActionDatabase",
        "engineVersion": "5.6.1",
        "actionDatabaseFingerprint": fingerprint,
        "pluginBuildHash": PLUGIN_HASH,
        "discoveredAtUtc": EXPORTED_AT,
    }

    def node_descriptor(key: str, title: str, keywords: list[str], tokens: list[str], pins: list[dict], risk: str, constraints: dict) -> dict:
        document = {
            "schemaVersion": descriptors.NODE_DESCRIPTOR_VERSION,
            "actionId": action_ids[key],
            "discoveryKey": discovery[key],
            "title": title,
            "menuName": title,
            "category": "Utilities|Text" if key == "print" else "Flow Control",
            "keywords": keywords,
            "searchTokens": tokens,
            "graphKinds": ["event", "function", "macro"],
            "graphSchemas": [K2_SCHEMA],
            "pins": pins,
            "constraints": constraints,
            "source": common_source,
            "risk": risk,
            "capability": "core",
            "trustLevel": "official",
            "descriptorDigest": "",
        }
        document["descriptorDigest"] = descriptors.node_descriptor_digest(document)
        return document

    print_doc = node_descriptor(
        "print",
        "Print String",
        ["print", "log", "string"],
        ["PrintString", "KismetSystemLibrary"],
        [
            descriptor_pin("execute", "input", "exec", "exec"),
            descriptor_pin("then", "output", "exec", "exec"),
            descriptor_pin("InString", "input", "data", "string"),
            descriptor_pin("Duration", "input", "data", "real"),
        ],
        "R1",
        {
            "requiresBlueprintContext": True,
            "requiresTarget": False,
            "requiresWorldContext": False,
            "pure": False,
            "latent": False,
            "variadic": False,
            "hiddenByDefault": [],
        },
    )
    branch_doc = node_descriptor(
        "branch",
        "Branch",
        ["branch", "if", "condition"],
        ["Branch", "IfThenElse"],
        [
            descriptor_pin("execute", "input", "exec", "exec"),
            descriptor_pin("then", "output", "exec", "exec"),
            descriptor_pin("else", "output", "exec", "exec"),
            descriptor_pin("Condition", "input", "data", "bool"),
        ],
        "R1",
        {
            "requiresBlueprintContext": True,
            "requiresTarget": False,
            "requiresWorldContext": False,
            "pure": False,
            "latent": False,
            "variadic": False,
            "hiddenByDefault": [],
        },
    )
    source_doc = node_descriptor(
        "source",
        "Get SharedValue",
        ["variable", "get", "string"],
        ["VariableGet", "SharedValue"],
        [descriptor_pin("Value", "output", "data", "string")],
        "R1",
        {
            "requiresBlueprintContext": True,
            "requiresTarget": False,
            "requiresWorldContext": False,
            "pure": True,
            "latent": False,
            "variadic": False,
            "hiddenByDefault": [],
        },
    )
    docs = {"print": print_doc, "branch": branch_doc, "source": source_doc}
    for key, document in docs.items():
        write_json(descriptor_dir / f"nd_{key}.json", document)
        report = descriptors.validate_node_descriptor(document)
        if report.errors:
            raise AssertionError(f"NodeDescriptor {key} invalid: {report.errors}")
    return [print_doc, branch_doc, source_doc], action_ids


def make_operation_descriptor(descriptor_dir: Path) -> dict[str, Any]:
    document = {
        "schemaVersion": "ueak.operation/1.0",
        "name": "graph.createNode",
        "version": "1.0.0",
        "domain": "graph",
        "targetKind": "blueprint-graph",
        "parametersSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["actionId"],
            "properties": {
                "actionId": {"type": "string", "pattern": "^action:[0-9a-f]{32}$"},
                "position": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["x", "y"],
                    "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}},
                },
            },
        },
        "outputsSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["nodeId"],
            "properties": {"nodeId": {"type": "string", "pattern": "^n:"}},
        },
        "capabilities": ["blueprint.graph.read", "blueprint.graph.write", "node.spawn"],
        "readOnly": False,
        "risk": "R2",
        "requires": {
            "editor": True,
            "transaction": True,
            "compile": True,
            "save": False,
            "policyKeys": ["allowedOperations", "allowedGraphs", "allowedNodeClasses"],
            "revisionScope": "asset+graph",
        },
        "preconditions": [
            "asset-loaded",
            "asset-open",
            "package-clean",
            "pie-stopped",
            "base-revision-match",
            "base-graph-hash-match",
            "graph-schema-supported",
            "editor-session-match",
            "recovery-marker-absent",
            "policy-allows-operation",
            "descriptor-resolved",
        ],
        "postconditions": [
            "node-guid-valid",
            "pin-map-complete",
            "graph-dirty",
            "transaction-recorded",
            "no-dangling-edge",
        ],
        "rollback": "reverse-ops",
        "idempotency": "required",
        "errorCodes": [
            "graphpatch-node-spawn-failed",
            "graphpatch-context-mismatch",
            "graphpatch-pin-identity-unstable",
            "graphpatch-base-graph-hash-stale",
        ],
        "implementation": "core",
        "evidence": {
            "required": ["plan", "apply-report", "typedgraph-before", "typedgraph-after", "receipt"],
            "optional": ["verify-report", "backup-manifest"],
        },
        "transaction": {"kind": "structure", "snapshot": "graph", "rollback": "reverse-ops"},
        "limits": {"maxOperationsPerInvocation": 200, "maxAffectedNodes": 100, "timeoutMs": 120000},
        "minHostVersion": "0.9.0",
        "deprecated": False,
    }
    write_json(descriptor_dir / "od_create_node.json", document)
    report = descriptors.validate_operation_descriptor(document)
    if report.errors:
        raise AssertionError(f"OperationDescriptor invalid: {report.errors}")

    enabled = copy.deepcopy(document)
    enabled.update(
        {
            "name": "graph.setNodeEnabled",
            "version": "1.0.0",
            "risk": "R2",
            "requiresExplicitConfirmation": True,
            "errorCodes": [
                "graphpatch-confirmation-required",
                "graphpatch-node-not-found",
                "graphpatch-base-graph-hash-stale",
            ],
        }
    )
    write_json(descriptor_dir / "od_set_node_enabled.json", enabled)
    enabled_report = descriptors.validate_operation_descriptor(enabled)
    if enabled_report.errors:
        raise AssertionError(f"OperationDescriptor enabled invalid: {enabled_report.errors}")
    return document


def base_target(graph: dict[str, Any]) -> dict[str, Any]:
    return {
        "assetPath": graph["asset"]["path"],
        "graphId": graph["graphId"],
        "graphGuid": graph["graph"]["guid"],
        "graphName": graph["graph"]["name"],
        "graphKind": graph["graph"]["kind"],
        "baseRevision": graph["asset"]["revision"],
        "baseGraphHash": graph["semanticHash"],
        "revisionPolicy": "index-snapshot",
        "editorSessionId": SESSION,
        "graphpatchProfile": "structure-default",
    }


def patch_document(graph: dict[str, Any], patch_id: str, key: str, operations: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    document = {
        "schemaVersion": "ueak.graphpatch/1.0",
        "patchId": patch_id,
        "idempotencyKey": key,
        "projectName": "FixtureProject",
        "target": base_target(graph),
        "conflictPolicy": "reject-if-changed",
        "externalDependencies": [],
        "operations": operations,
        "metadata": {
            "description": f"fixture {patch_id}",
            "createdBy": "contract_candidate/tools/build_fixtures.py",
            "createdAtUtc": EXPORTED_AT,
        },
    }
    document.update(extra)
    return document


def set_position_op(graph: dict[str, Any], operation_id: str = "op-1", x: int = 32, y: int = 48) -> dict[str, Any]:
    node_id = graph["nodes"][0]["nodeId"]
    return {"operationId": operation_id, "type": "setNodePosition", "nodeId": node_id, "x": x, "y": y}


def create_branch_operations(action_ids: dict[str, str]) -> list[dict[str, Any]]:
    return [
        {
            "operationId": "op-1",
            "type": "createNode",
            "tempId": "tmp:print",
            "descriptor": {"actionId": action_ids["print"]},
            "position": {"x": 400, "y": 0},
            "pinDefaults": {"InString": "UEAgentKit 图补丁"},
        },
        {
            "operationId": "op-2",
            "type": "createNode",
            "tempId": "tmp:branch",
            "descriptor": {"actionId": action_ids["branch"]},
            "position": {"x": 800, "y": 0},
        },
        {
            "operationId": "op-3",
            "type": "setPinDefault",
            "dependsOn": ["op-2"],
            "pin": {"node": "tmp:branch", "pin": "Condition", "direction": "input"},
            "value": "true",
            "valueType": "bool",
        },
        {
            "operationId": "op-4",
            "type": "connectPins",
            "dependsOn": ["op-1", "op-2"],
            "from": {"node": "tmp:print", "pin": "then", "direction": "output"},
            "to": {"node": "tmp:branch", "pin": "execute", "direction": "input"},
            "connectionPolicy": "reject-replacement",
            "allowImplicitConversion": False,
        },
    ]


def build_patches(descriptors_by_key: dict[str, dict[str, Any]], action_ids: dict[str, str]) -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    empty = GRAPHS["tg_empty"]
    linear = GRAPHS["tg_linear_exec"]
    dynamic = GRAPHS["tg_dynamic_pins"]
    event = linear["nodes"][0]
    print_a = linear["nodes"][1]
    print_b = linear["nodes"][2]
    a_execute = next(pin for pin in linear["pins"] if pin["nodeId"] == print_a["nodeId"] and pin["name"] == "execute")
    b_then = next(pin for pin in linear["pins"] if pin["nodeId"] == print_b["nodeId"] and pin["name"] == "then")
    a_in = next(pin for pin in linear["pins"] if pin["nodeId"] == print_a["nodeId"] and pin["name"] == "InString")
    b_in = next(pin for pin in linear["pins"] if pin["nodeId"] == print_b["nodeId"] and pin["name"] == "InString")
    sequence = dynamic["nodes"][0]
    print_node = dynamic["nodes"][2]
    print_node_two = dynamic["nodes"][3]
    source = dynamic["nodes"][1]
    promotable = dynamic["nodes"][4]
    source_value = next(pin for pin in dynamic["pins"] if pin["nodeId"] == source["nodeId"] and pin["name"] == "Value")
    derived_pin = next(pin for pin in dynamic["pins"] if pin["nodeId"] == promotable["nodeId"] and pin["name"] == "ErrorTolerance")

    patches: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    environment_ok = {
        "actualRevision": "",
        "dirty": False,
        "pieActive": False,
        "editorSessionId": SESSION,
        "recoveryPending": False,
        "sidecarFiles": [],
        "compileResult": {"result": "success", "succeeded": True, "diagnostics": []},
    }

    create_branch = patch_document(
        empty,
        "gp-create-branch-001",
        "lyra/empty/create-print-branch/01",
        create_branch_operations(action_ids),
        expect={"nodeCountDelta": 2, "edgeCountDelta": 1, "pinDefaultDelta": 2},
    )
    env = dict(environment_ok, actualRevision=empty["asset"]["revision"])
    patches["gp_create_branch"] = (create_branch, env)

    replace = patch_document(
        linear,
        "gp-replace-connection-001",
        "lyra/linear/replace-connection/01",
        [
            {
                "operationId": "op-1",
                "type": "connectPins",
                "from": {"node": print_b["nodeId"], "pin": "then", "direction": "output"},
                "to": {"node": print_a["nodeId"], "pin": "execute", "direction": "input"},
                "connectionPolicy": "reject-replacement",
            }
        ],
    )
    patches["gp_replace_connection"] = (replace, dict(environment_ok, actualRevision=linear["asset"]["revision"]))

    stale_revision = patch_document(
        linear,
        "gp-stale-revision-001",
        "lyra/linear/stale-revision/01",
        [set_position_op(linear, "op-1", 64, 64)],
    )
    stale_revision["target"]["baseRevision"] = "sha256:" + "0" * 64
    patches["gp_stale_revision"] = (stale_revision, dict(environment_ok, actualRevision=linear["asset"]["revision"]))

    stale_hash = patch_document(
        linear,
        "gp-stale-graph-hash-001",
        "lyra/linear/stale-graph-hash/01",
        [set_position_op(linear, "op-1", 64, 64)],
    )
    stale_hash["target"]["baseGraphHash"] = "sha256:" + "f" * 64
    patches["gp_stale_graph_hash"] = (stale_hash, dict(environment_ok, actualRevision=linear["asset"]["revision"]))

    recovery = patch_document(
        linear,
        "gp-recovery-marker-001",
        "lyra/linear/recovery-marker/01",
        [set_position_op(linear, "op-1", 64, 64)],
    )
    patches["gp_recovery_marker"] = (
        recovery,
        dict(environment_ok, actualRevision=linear["asset"]["revision"], recoveryPending=True),
    )

    order_conflict = patch_document(
        empty,
        "gp-order-conflict-001",
        "lyra/empty/order-conflict/01",
        [
            {
                "operationId": "op-1",
                "type": "createNode",
                "tempId": "tmp:print",
                "descriptor": {"actionId": action_ids["print"]},
                "position": {"x": 320, "y": 0},
            },
            {
                "operationId": "op-2",
                "type": "createNode",
                "tempId": "tmp:source",
                "descriptor": {"actionId": action_ids["source"]},
                "position": {"x": 0, "y": 0},
            },
            {
                "operationId": "op-3",
                "type": "connectPins",
                "dependsOn": ["op-1", "op-2"],
                "from": {"node": "tmp:source", "pin": "Value", "direction": "output"},
                "to": {"node": "tmp:print", "pin": "InString", "direction": "input"},
                "connectionPolicy": "reject-replacement",
            },
            {
                "operationId": "op-4",
                "type": "setPinDefault",
                "dependsOn": ["op-1"],
                "pin": {"node": "tmp:print", "pin": "InString", "direction": "input"},
                "value": "late",
                "valueType": "string",
            },
        ],
    )
    patches["gp_order_conflict"] = (order_conflict, dict(environment_ok, actualRevision=empty["asset"]["revision"]))

    derived = patch_document(
        dynamic,
        "gp-derived-pin-001",
        "lyra/dynamic/derived-pin/01",
        [
            {
                "operationId": "op-1",
                "type": "connectPins",
                "from": {"node": source["nodeId"], "pin": "Value", "direction": "output"},
                "to": {
                    "node": promotable["nodeId"],
                    "pin": "ErrorTolerance",
                    "direction": "input",
                    "pinId": derived_pin["pinId"],
                },
                "connectionPolicy": "reject-replacement",
            }
        ],
    )
    patches["gp_derived_pin"] = (derived, dict(environment_ok, actualRevision=dynamic["asset"]["revision"]))

    exec_vs_data = patch_document(
        linear,
        "gp-exec-vs-data-001",
        "lyra/linear/exec-vs-data/01",
        [
            {
                "operationId": "op-1",
                "type": "connectPins",
                "from": {"node": print_a["nodeId"], "pin": "then", "direction": "output"},
                "to": {"node": print_b["nodeId"], "pin": "InString", "direction": "input"},
                "connectionPolicy": "reject-replacement",
            }
        ],
    )
    patches["gp_exec_vs_data"] = (exec_vs_data, dict(environment_ok, actualRevision=linear["asset"]["revision"]))

    empty_ops = patch_document(linear, "gp-empty-001", "lyra/linear/empty/01", [])
    patches["gp_empty_operations"] = (empty_ops, dict(environment_ok, actualRevision=linear["asset"]["revision"]))

    bad_version = patch_document(linear, "gp-schema-version-001", "lyra/linear/schema-version/01", [set_position_op(linear)])
    bad_version["schemaVersion"] = "ueak.graphpatch/2.0"
    patches["gp_schema_version"] = (bad_version, dict(environment_ok, actualRevision=linear["asset"]["revision"]))

    dynamic_index = patch_document(
        dynamic,
        "gp-dynamic-index-001",
        "lyra/dynamic/index/01",
        [
            {
                "operationId": "op-1",
                "type": "connectPins",
                "from": {"node": sequence["nodeId"], "pin": "then", "direction": "output", "pinIndex": 1},
                "to": {"node": print_node_two["nodeId"], "pin": "execute", "direction": "input"},
                "connectionPolicy": "reject-replacement",
            }
        ],
    )
    patches["gp_dynamic_index"] = (dynamic_index, dict(environment_ok, actualRevision=dynamic["asset"]["revision"]))

    compile_failure = patch_document(
        empty,
        "gp-compile-failure-001",
        "lyra/empty/compile-failure/01",
        create_branch_operations(action_ids),
        expect={"nodeCountDelta": 2, "edgeCountDelta": 1, "pinDefaultDelta": 2},
    )
    patches["gp_compile_failure"] = (
        compile_failure,
        dict(environment_ok, actualRevision=empty["asset"]["revision"], compileResult={
            "result": "error",
            "succeeded": False,
            "diagnostics": [{"severity": "error", "message": "Simulated compile error: missing required pin", "code": "compile-error"}],
        }),
    )

    compile_warning = patch_document(
        empty,
        "gp-compile-warning-001",
        "lyra/empty/compile-warning/01",
        create_branch_operations(action_ids),
        expect={"nodeCountDelta": 2, "edgeCountDelta": 1, "pinDefaultDelta": 2},
    )
    patches["gp_compile_warning"] = (
        compile_warning,
        dict(environment_ok, actualRevision=empty["asset"]["revision"], compileResult={
            "result": "success-with-warnings",
            "succeeded": True,
            "diagnostics": [{"severity": "warning", "message": "Simulated compiler warning", "code": "compile-warning"}],
        }),
    )

    for name, (document, _) in patches.items():
        write_json(FIXTURES / "graphpatch" / f"{name}.json", document)
    return patches


def main() -> None:
    for name, factory in GRAPHS.items():
        document = factory()
        write_json(FIXTURES / "typedgraph" / f"{name}.json", document)
        GRAPHS[name] = document
    duplicate_raw = make_duplicate_raw()
    write_json(FIXTURES / "typedgraph" / "tg_duplicate_pin_raw.json", duplicate_raw)
    GRAPHS["tg_duplicate_pin_raw"] = duplicate_raw

    node_descriptors, action_ids = make_descriptors(FIXTURES / "descriptors")
    make_operation_descriptor(FIXTURES / "descriptors")
    patches = build_patches({}, action_ids)

    graph_by_fixture = {
        "tg_empty": GRAPHS["tg_empty"],
        "tg_linear_exec": GRAPHS["tg_linear_exec"],
        "tg_dynamic_pins": GRAPHS["tg_dynamic_pins"],
    }
    patch_graph = {
        "gp_create_branch": "tg_empty",
        "gp_compile_failure": "tg_empty",
        "gp_compile_warning": "tg_empty",
        "gp_order_conflict": "tg_empty",
        "gp_stale_revision": "tg_linear_exec",
        "gp_stale_graph_hash": "tg_linear_exec",
        "gp_recovery_marker": "tg_linear_exec",
        "gp_replace_connection": "tg_linear_exec",
        "gp_exec_vs_data": "tg_linear_exec",
        "gp_empty_operations": "tg_linear_exec",
        "gp_schema_version": "tg_linear_exec",
        "gp_derived_pin": "tg_dynamic_pins",
        "gp_dynamic_index": "tg_dynamic_pins",
    }
    patch_descriptor_keys = {
        "gp_create_branch": ["nd_print", "nd_branch"],
        "gp_compile_failure": ["nd_print", "nd_branch"],
        "gp_compile_warning": ["nd_print", "nd_branch"],
        "gp_order_conflict": ["nd_print", "nd_source"],
    }
    expectations = {
        "gp_create_branch": ("previewed", "", "applied", ""),
        "gp_compile_failure": ("previewed", "", "rolled-back", "graphpatch-compile-failed"),
        "gp_compile_warning": ("previewed", "", "applied", ""),
        "gp_replace_connection": ("rejected", "graphpatch-connection-replaced", "", ""),
        "gp_stale_revision": ("rejected", "graphpatch-base-revision-stale", "", ""),
        "gp_stale_graph_hash": ("rejected", "graphpatch-base-graph-hash-stale", "", ""),
        "gp_recovery_marker": ("rejected", "graphpatch-recovery-required", "", ""),
        "gp_order_conflict": ("rejected", "graphpatch-operation-order-conflict", "", ""),
        "gp_derived_pin": ("rejected", "graphpatch-pin-identity-unstable", "", ""),
        "gp_exec_vs_data": ("rejected", "graphpatch-pin-type-incompatible", "", ""),
        "gp_empty_operations": ("rejected", "graphpatch-patch-empty", "", ""),
        "gp_schema_version": ("rejected", "graphpatch-schema-version-unsupported", "", ""),
        "gp_dynamic_index": ("previewed", "", "", ""),
    }

    manifest_entries: list[dict[str, Any]] = []
    for name in ("tg_empty", "tg_single_chinese", "tg_linear_exec", "tg_dynamic_pins", "tg_exec_vs_data", "tg_exec_cycle", "tg_comment_reroute", "tg_large_100"):
        document = GRAPHS[name]
        entry = {
            "fixtureId": name.replace("_", "-"),
            "kind": "typedgraph",
            "file": f"typedgraph/{name}.json",
            "requiresEditor": False,
            "expected": {
                "status": "valid",
                "semanticHash": document["semanticHash"],
                "layoutHash": document["layoutHash"],
                "nodeCount": len(document["nodes"]),
                "edgeCount": len(document["edges"]),
                "pinCount": len(document["pins"]),
                "applyReady": typed_graph.apply_ready(document),
            },
        }
        manifest_entries.append(entry)

    manifest_entries.append(
        {
            "fixtureId": "tg-duplicate-pin-raw",
            "kind": "typedgraph-raw",
            "file": "typedgraph/tg_duplicate_pin_raw.json",
            "requiresEditor": False,
            "expected": {
                "status": "built-with-warning",
                "builderErrorCode": "graph-pin-guid-duplicate",
                "nodeCount": 2,
                "applyReady": False,
            },
        }
    )

    for name, (document, environment) in patches.items():
        graph_fixture = patch_graph[name]
        graph = graph_by_fixture[graph_fixture]
        descriptor_map = {"Print String": node_descriptors[0], "Branch": node_descriptors[1], "Get SharedValue": node_descriptors[2]}
        needed = patch_descriptor_keys.get(name, [])
        selected = [descriptor_map[{"nd_print": "Print String", "nd_branch": "Branch", "nd_source": "Get SharedValue"}[key]] for key in needed]
        preview = graph_patch.preview_patch(document, graph, environment, selected)
        preview_status, preview_code, apply_status, apply_code = expectations[name]
        if preview["status"] != preview_status or preview["errorCode"] != preview_code:
            raise AssertionError(
                f"{name}: preview expected {preview_status}/{preview_code}, got {preview['status']}/{preview['errorCode']} "
                f"errors={preview.get('errors')}"
            )
        applied: dict[str, Any] | None = None
        if apply_status:
            applied = graph_patch.simulate_apply(document, graph, environment, selected, include_result_graph=True)
            if applied["status"] != apply_status or applied["errorCode"] != apply_code:
                raise AssertionError(
                    f"{name}: apply expected {apply_status}/{apply_code}, got {applied['status']}/{applied['errorCode']} "
                    f"errors={applied.get('errors')}"
                )
            if applied["status"] == "applied":
                verified = graph_patch.verify_result(applied, applied["resultGraph"])
                if verified["status"] != "verified":
                    raise AssertionError(f"{name}: verify failed {verified.get('errors')}")
        entry = {
            "fixtureId": name.replace("gp_", "gp-").replace("_", "-"),
            "kind": "graphpatch",
            "file": f"graphpatch/{name}.json",
            "graphFixture": f"typedgraph/{graph_fixture}.json",
            "descriptorFixtures": [f"descriptors/{('nd_' + key.split('_')[1] + '.json')}" for key in needed],
            "requiresEditor": False,
            "environment": environment,
            "expected": {
                "previewStatus": preview_status,
                "previewErrorCode": preview_code,
                "applyStatus": apply_status,
                "applyErrorCode": apply_code,
                "verifyStatus": "verified" if apply_status == "applied" else "",
                "nodeCountDelta": preview.get("predicted", {}).get("nodeCountDelta"),
                "edgeCountDelta": preview.get("predicted", {}).get("edgeCountDelta"),
                "pinDefaultDelta": preview.get("predicted", {}).get("pinDefaultDelta"),
            },
        }
        manifest_entries.append(entry)

    # Layout fixtures and golden vectors.
    layout_specs = [
        ("layout_empty", "tg_empty", {"mode": "full", "options": {"spacingX": 80, "spacingY": 40, "seed": 0}}),
        ("layout_linear", "tg_linear_exec", {"mode": "full", "options": {"spacingX": 80, "spacingY": 40, "seed": 0}}),
        ("layout_islands_shared", "tg_exec_vs_data", {"mode": "full", "options": {"spacingX": 80, "spacingY": 40, "seed": 123}}),
        ("layout_cycle", "tg_exec_cycle", {"mode": "full", "options": {"spacingX": 80, "spacingY": 40, "seed": 0}}),
    ]
    layout_vectors: list[dict[str, Any]] = []
    for fixture_id, graph_key, request in layout_specs:
        graph = GRAPHS[graph_key]
        plan = layout.layout_graph(graph, request)
        if plan.status != "ok":
            raise AssertionError(f"{fixture_id}: layout rejected {plan.errors}")
        write_json(FIXTURES / "layout" / f"{fixture_id}.json", {"graphFixture": f"../typedgraph/{graph_key}.json", "request": request})
        entry = {
            "fixtureId": fixture_id.replace("_", "-"),
            "kind": "layout",
            "file": f"layout/{fixture_id}.json",
            "graphFixture": f"typedgraph/{graph_key}.json",
            "requiresEditor": False,
            "expected": {
                "status": "ok",
                "islandCount": len(plan.plan["islands"]),
                "overlapCount": plan.plan["metrics"]["overlapCount"],
                "layoutHash": plan.plan["layoutHash"],
                "semanticHash": graph["semanticHash"],
                "warningCodes": [warning["code"] for warning in plan.plan["warnings"]],
            },
        }
        manifest_entries.append(entry)
        layout_vectors.append(
            {
                "id": fixture_id,
                "fixture": f"typedgraph/{graph_key}.json",
                "request": request,
                "layoutHash": plan.plan["layoutHash"],
                "positions": [
                    {"nodeId": item["nodeId"], "x": item["x"], "y": item["y"]} for item in plan.plan["positions"]
                ],
                "metrics": plan.plan["metrics"],
                "islandCount": len(plan.plan["islands"]),
            }
        )

    for key, document in (("print", node_descriptors[0]), ("branch", node_descriptors[1]), ("source", node_descriptors[2])):
        entry = {
            "fixtureId": f"nd-{key}",
            "kind": "node-descriptor",
            "file": f"descriptors/nd_{key}.json",
            "requiresEditor": False,
            "expected": {"status": "valid", "actionId": document["actionId"]},
        }
        manifest_entries.append(entry)
    manifest_entries.append(
        {
            "fixtureId": "od-create-node",
            "kind": "operation-descriptor",
            "file": "descriptors/od_create_node.json",
            "requiresEditor": False,
            "expected": {"status": "valid", "name": "graph.createNode"},
        }
    )
    manifest_entries.append(
        {
            "fixtureId": "od-set-node-enabled",
            "kind": "operation-descriptor",
            "file": "descriptors/od_set_node_enabled.json",
            "requiresEditor": False,
            "expected": {"status": "valid", "name": "graph.setNodeEnabled"},
        }
    )

    manifest = {
        "schemaVersion": "ueak.fixture-manifest/1.0",
        "generatedBy": "contract_candidate/tools/build_fixtures.py",
        "hashAlgorithm": "sha256 over 'domain\\n' + canonical JSON (UTF-8, sorted keys, ensure_ascii=False)",
        "requiresEditor": False,
        "fixtures": manifest_entries,
    }
    write_json(FIXTURES / "manifest.json", manifest)

    vectors = {
        "schemaVersion": "ueak.hash-vectors/1.0",
        "generatedBy": "contract_candidate/tools/build_fixtures.py",
        "canonicalization": [
            {
                "id": "unicode-key-order",
                "input": {"z": 1, "a": "中文", "nested": {"b": True, "a": None}},
                "canonical": canonical_json({"z": 1, "a": "中文", "nested": {"b": True, "a": None}}),
            },
            {
                "id": "float-and-bool",
                "input": {"f": 1.5, "t": True, "n": -0.0},
                "canonical": canonical_json({"f": 1.5, "t": True, "n": -0.0}),
            },
        ],
        "semantic": [
            {
                "id": name,
                "fixture": f"typedgraph/{name}.json",
                "expected": GRAPHS[name]["semanticHash"],
            }
            for name in ("tg_empty", "tg_single_chinese", "tg_linear_exec", "tg_dynamic_pins", "tg_comment_reroute")
        ],
        "layout": [
            {
                "id": name,
                "fixture": f"typedgraph/{name}.json",
                "expected": GRAPHS[name]["layoutHash"],
            }
            for name in ("tg_empty", "tg_single_chinese", "tg_linear_exec", "tg_dynamic_pins")
        ],
        "layoutPlans": layout_vectors,
        "localizationInvariance": {
            "fixture": "typedgraph/tg_single_chinese.json",
            "mutations": [
                {"path": "nodes[0].title", "value": "Print String"},
                {"path": "nodes[0].comment", "value": "English comment"},
                {"path": "pins[2].friendlyName", "value": "In String"},
            ],
        },
    }
    write_json(VECTORS / "hash_vectors.json", vectors)
    print(f"wrote {len(GRAPHS)} typed graphs, {len(patches)} patches, {len(layout_specs)} layouts, {len(manifest_entries)} manifest entries")


if __name__ == "__main__":
    main()
