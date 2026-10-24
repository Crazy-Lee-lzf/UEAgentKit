"""Generate the R1 node-registry golden parity fixture.

The fixture is produced with the *existing* Python contract implementation
(``ue_agent_kit.graph_contracts.descriptors`` + ``canonical``).  The native C++
registry must reproduce ``actionId``, ``descriptorDigest`` and the canonical
JSON bytes exactly; ``tests/native_tools/test_r1_native_contract.py`` checks the
fixture itself and the UE automation test ``UEAgentKit.Native.R1DescriptorParity``
checks the C++ side against it.

Two modes:

* default -- 200 deterministic synthetic samples that cover every actionKind,
  pin shape and JSON string-escaping edge case.  Marked ``synthetic-parity``.
* ``--from-export <catalog-export.json>`` -- use real descriptors exported by a
  UE 5.6 editor run (``ueak.native.catalog-export/1.0``), validated by the
  repository validator, then padded with synthetic samples to reach 200.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ue_agent_kit.graph_contracts import descriptors  # noqa: E402
from ue_agent_kit.graph_contracts.canonical import (  # noqa: E402
    DOMAIN_ACTION,
    DOMAIN_DESCRIPTOR,
    canonical_json,
    domain_hash,
)

GOLDEN_VERSION = "ueak.native-golden/1.0"
TARGET_SAMPLES = 200
SCHEMA = "ueak.node-descriptor/1.0"
ENGINE_VERSION = "5.6.1"
FIXED_UTC = "1970-01-01T00:00:00.000Z"
PLUGIN_BUILD_HASH = "sha256:" + "0" * 64
DATABASE_FINGERPRINT = "sha256:0000000000000000"

NODE_CLASSES = {
    "callFunction": "/Script/BlueprintGraph.K2Node_CallFunction",
    "event": "/Script/BlueprintGraph.K2Node_Event",
    "variableGet": "/Script/BlueprintGraph.K2Node_VariableGet",
    "variableSet": "/Script/BlueprintGraph.K2Node_VariableSet",
    "macro": "/Script/BlueprintGraph.K2Node_MacroInstance",
    "cast": "/Script/BlueprintGraph.K2Node_DynamicCast",
    "delegate": "/Script/BlueprintGraph.K2Node_AssignDelegate",
    "component": "/Script/BlueprintGraph.K2Node_AddComponent",
    "special": "/Script/BlueprintGraph.K2Node_Knot",
}

SPAWNER_CLASSES = {
    "callFunction": "/Script/BlueprintGraph.BlueprintFunctionNodeSpawner",
    "event": "/Script/BlueprintGraph.BlueprintEventNodeSpawner",
    "variableGet": "/Script/BlueprintGraph.BlueprintVariableNodeSpawner",
    "variableSet": "/Script/BlueprintGraph.BlueprintVariableNodeSpawner",
    "macro": "/Script/BlueprintGraph.BlueprintNodeSpawner",
    "cast": "/Script/BlueprintGraph.BlueprintNodeSpawner",
    "delegate": "/Script/BlueprintGraph.BlueprintDelegateNodeSpawner",
    "component": "/Script/BlueprintGraph.BlueprintComponentNodeSpawner",
    "special": "/Script/BlueprintGraph.BlueprintNodeSpawner",
}

PAYLOAD_KEY = {
    "callFunction": "functionPath",
    "event": "eventName",
    "variableGet": "variableName",
    "variableSet": "variableName",
    "macro": "macroGraph",
    "cast": "targetClass",
    "delegate": "delegateName",
    "component": "componentClass",
    "special": "specialKind",
}

GRAPH_SCHEMA = "/Script/BlueprintGraph.EdGraphSchema_K2"


def pin(
    name: str,
    direction: str,
    category: str,
    *,
    sub_category: str = "",
    sub_object: str = "",
    container: str = "none",
    is_reference: bool = False,
    is_const: bool = False,
    is_weak: bool = False,
) -> dict[str, Any]:
    return {
        "name": name,
        "direction": direction,
        "kind": "exec" if category == "exec" else "data",
        "type": {
            "category": category,
            "subCategory": sub_category,
            "subCategoryObject": sub_object,
            "container": container,
            "isReference": is_reference,
            "isConst": is_const,
            "isWeakPointer": is_weak,
        },
    }


def exec_pins() -> list[dict[str, Any]]:
    return [
        pin("execute", "input", "exec"),
        pin("then", "output", "exec"),
    ]


def data_pins(index: int) -> list[dict[str, Any]]:
    pool = [
        [pin("InString", "input", "string"), pin("Duration", "input", "real")],
        [pin("bEnabled", "input", "bool"), pin("Result", "output", "bool")],
        [pin("Target", "input", "object", sub_object="/Script/Engine.Actor")],
        [pin("Values", "input", "int", container="array"), pin("Count", "output", "int")],
        [pin("Entries", "input", "struct", sub_object="/Script/CoreUObject.Guid")],
        [pin("OnChanged", "output", "delegate", sub_object="/Script/Engine.ActorComponent")],
        [pin("Amount", "input", "real", is_reference=True, is_const=True)],
        [pin("WeakRef", "input", "object", is_weak=True, sub_object="/Script/Engine.Object")],
    ]
    return pool[index % len(pool)]


def constraints(index: int) -> dict[str, Any]:
    return {
        "requiresBlueprintContext": True,
        "requiresTarget": index % 3 == 0,
        "requiresWorldContext": index % 4 == 0,
        "pure": index % 5 == 0,
        "latent": index % 7 == 0,
        "variadic": index % 11 == 0,
        "hiddenByDefault": [] if index % 2 == 0 else ["self"],
    }


def make_descriptor(
    *,
    kind: str,
    payload_value: str,
    node_class: str | None = None,
    spawner_class: str | None = None,
    graph_schema: str = GRAPH_SCHEMA,
    title: str = "Node",
    menu_name: str | None = None,
    category: str = "Utilities",
    keywords: list[str] | None = None,
    search_tokens: list[str] | None = None,
    graph_kinds: list[str] | None = None,
    graph_schemas: list[str] | None = None,
    pins: list[dict[str, Any]] | None = None,
    index: int = 0,
    payload_extra: dict[str, str] | None = None,
) -> dict[str, Any]:
    discovery_key = {
        "nodeClass": node_class or NODE_CLASSES[kind],
        "spawnerClass": spawner_class or SPAWNER_CLASSES[kind],
        "graphSchema": graph_schema,
        "actionKind": kind,
        "payload": {PAYLOAD_KEY[kind]: payload_value, **(payload_extra or {})},
    }
    document: dict[str, Any] = {
        "schemaVersion": SCHEMA,
        "actionId": descriptors.make_action_id(discovery_key),
        "discoveryKey": discovery_key,
        "title": title,
        "menuName": menu_name if menu_name is not None else title,
        "category": category,
        "keywords": keywords or [],
        "searchTokens": search_tokens or ["node"],
        "graphKinds": graph_kinds or ["event", "function"],
        "graphSchemas": graph_schemas or [graph_schema],
        "pins": pins or exec_pins(),
        "constraints": constraints(index),
        "source": {
            "kind": "FBlueprintActionDatabase",
            "engineVersion": ENGINE_VERSION,
            "actionDatabaseFingerprint": DATABASE_FINGERPRINT,
            "pluginBuildHash": PLUGIN_BUILD_HASH,
            "discoveredAtUtc": FIXED_UTC,
        },
        "risk": "R2",
        "capability": "core",
        "trustLevel": "official",
    }
    document["descriptorDigest"] = descriptors.node_descriptor_digest(document)
    return document


def sample(sample_id: str, document: dict[str, Any], source: str = "synthetic-parity") -> dict[str, Any]:
    discovery_key = document["discoveryKey"]
    projection = descriptors.discovery_key_projection(discovery_key)
    return {
        "sampleId": sample_id,
        "source": source,
        "discoveryKey": discovery_key,
        "expectedCanonicalDiscoveryKey": canonical_json(projection),
        "expectedActionId": descriptors.make_action_id(discovery_key),
        "expectedActionProjectionHash": domain_hash(projection, DOMAIN_ACTION),
        "descriptor": document,
        "expectedDescriptorDigest": descriptors.node_descriptor_digest(document),
        "expectedDescriptorHash": domain_hash(
            {
                key: value
                for key, value in document.items()
                if key not in ("descriptorDigest",)
            }
            | {
                "source": {
                    key: value
                    for key, value in document["source"].items()
                    if key != "discoveredAtUtc"
                }
            },
            DOMAIN_DESCRIPTOR,
        ),
    }


def assert_valid(document: dict[str, Any], label: str) -> None:
    report = descriptors.validate_node_descriptor(document)
    if report.diagnostics:
        details = "\n".join(f"  {item['code']} {item['path']}: {item['message']}" for item in report.diagnostics)
        raise SystemExit(f"{label} is not contract-valid:\n{details}")


def realistic_samples() -> list[dict[str, Any]]:
    kinds = list(NODE_CLASSES)
    function_names = [
        ("/Script/Engine.KismetSystemLibrary:PrintString", "Print String", "Utilities|String"),
        ("/Script/Engine.KismetMathLibrary:Add_IntInt", "Add (Integer)", "Math|Integer"),
        ("/Script/Engine.GameplayStatics:GetPlayerCharacter", "Get Player Character", "Game|Player"),
        ("/Script/Engine.Actor:K2_GetActorLocation", "Get Actor Location", "Transformation|Location"),
        ("/Script/Engine.BlueprintLibrary:MakeTransform", "Make Transform", "Utilities|Struct"),
        ("/Script/Engine.LatentActionManager:Test", "Latent Test", "Utilities"),
    ]
    variable_names = ["Health", "bIsDead", "MoveSpeed", "TargetActor", "CachedTransform", "Score"]
    event_names = ["ReceiveBeginPlay", "ReceiveTick", "ReceiveAnyDamage", "OnOverlapBegin"]
    macro_graphs = ["ForEachLoop", "WhileLoop", "DoOnce", "Gate", "IsValid"]
    target_classes = ["/Script/Engine.Actor", "/Script/Engine.Pawn", "/Script/Engine.PlayerController"]
    delegate_names = ["OnClicked", "OnComponentBeginOverlap", "OnHealthChanged"]
    component_classes = [
        "/Script/Engine.StaticMeshComponent",
        "/Script/Engine.PointLightComponent",
        "/Script/Engine.CapsuleComponent",
    ]
    special_kinds = ["knot", "sequence", "select", "multigate"]

    samples: list[dict[str, Any]] = []
    for index in range(160):
        kind = kinds[index % len(kinds)]
        payload_key = PAYLOAD_KEY[kind]
        if kind == "callFunction":
            payload_value, title, category = function_names[index % len(function_names)]
        elif kind in ("variableGet", "variableSet"):
            payload_value = variable_names[index % len(variable_names)]
            title = payload_value
            category = "Variables"
        elif kind == "event":
            payload_value = event_names[index % len(event_names)]
            title = payload_value
            category = "Events"
        elif kind == "macro":
            payload_value = macro_graphs[index % len(macro_graphs)]
            title = payload_value
            category = "Macros"
        elif kind == "cast":
            payload_value = target_classes[index % len(target_classes)]
            title = "Cast To " + payload_value.rsplit(".", 1)[-1]
            category = "Casting"
        elif kind == "delegate":
            payload_value = delegate_names[index % len(delegate_names)]
            title = "Bind " + payload_value
            category = "Delegates"
        elif kind == "component":
            payload_value = component_classes[index % len(component_classes)]
            title = "Add " + payload_value.rsplit(".", 1)[-1]
            category = "Components"
        else:
            payload_value = special_kinds[index % len(special_kinds)]
            title = payload_value.title()
            category = "Flow Control"

        graph_kind_sets = [
            ["event", "function"],
            ["function"],
            ["event", "function", "macro"],
            ["other"],
        ]
        document = make_descriptor(
            kind=kind,
            payload_value=payload_value,
            title=title,
            category=category,
            keywords=["keyword", kind, str(index)],
            search_tokens=[
                token
                for token in (
                    title.replace(" ", "").replace("(", "").replace(")", ""),
                    payload_value.rsplit(".", 1)[-1].replace(":", ""),
                    kind,
                )
                if token.isascii()
            ],
            graph_kinds=graph_kind_sets[index % len(graph_kind_sets)],
            pins=exec_pins() + data_pins(index),
            index=index,
            payload_extra={"macroGraph": f"sample-{index:03d}"} if kind == "special" else None,
        )
        assert payload_key in document["discoveryKey"]["payload"]
        samples.append(sample(f"synthetic-{index:03d}", document))
    return samples


def edge_samples() -> list[dict[str, Any]]:
    # JSON string escaping, UTF-16 surrogate pairs, key ordering and long values.
    edge_payloads = [
        ('quote"inside', "Quote"),
        ("back\\slash", "Backslash"),
        ("line\nbreak", "Newline"),
        ("tab\tchar", "Tab"),
        ("control\x01char", "Control"),
        ("中文函数", "中文标题"),
        ("emoji-😀-value", "Emoji 😀"),
        ("zero\u200bwidth", "Zero Width"),
        ("key:colon", "Colon"),
        ("a" * 300, "Long Value"),
        ("mixed \"quotes\" and \\slashes\\ and\nnewlines", "Mixed \"Everything\" \\ 中文"),
        ("value\u007fdel", "Del"),
    ]
    samples: list[dict[str, Any]] = []
    kinds = list(NODE_CLASSES)
    for index in range(40):
        kind = kinds[index % len(kinds)]
        payload_value, title = edge_payloads[index % len(edge_payloads)]
        document = make_descriptor(
            kind=kind,
            payload_value=payload_value,
            title=title,
            menu_name=title,
            category="Edge|" + ("中文" if index % 3 == 0 else "Raw"),
            keywords=[title, "edge", str(index), "キーワード"],
            search_tokens=["edge", f"case{index}"],
            graph_kinds=["event", "function", "macro", "other"],
            pins=exec_pins() + data_pins(index),
            index=index,
        )
        samples.append(sample(f"edge-{index:03d}", document))
    return samples


def load_export(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if payload.get("schemaVersion") != "ueak.native.catalog-export/1.0":
        raise SystemExit("catalog export has an unsupported schemaVersion")
    descriptors_out = payload.get("descriptors")
    if not isinstance(descriptors_out, list):
        raise SystemExit("catalog export has no descriptors array")
    return descriptors_out


def build_fixture(export_path: Path | None) -> dict[str, Any]:
    samples: list[dict[str, Any]] = []
    sources: dict[str, int] = {}
    seen_kinds: set[str] = set()
    if export_path is not None:
        exported = load_export(export_path)
        # Keep headroom for synthetic coverage of kinds/escaping the live action
        # database does not contain (macros/components/special nodes are schema
        # context actions, not FBlueprintActionDatabase entries).
        real_budget = TARGET_SAMPLES - 40
        for index, document in enumerate(exported):
            assert_valid(document, f"exported descriptor {index}")
            samples.append(sample(f"ue-{index:03d}", document, source="ue-catalog"))
            seen_kinds.add(document["discoveryKey"]["actionKind"])
            if len(samples) >= real_budget:
                break
        sources["ue-catalog"] = len(samples)

    synthetic = realistic_samples() + edge_samples()
    # First fill missing actionKinds, then add escaping/edge cases, then the rest.
    missing_kind_samples = [
        item for item in synthetic if item["discoveryKey"]["actionKind"] not in seen_kinds
    ]
    edge_only = [item for item in synthetic if item["sampleId"].startswith("edge-")]
    ordered = missing_kind_samples + edge_only + synthetic
    for document in ordered:
        if len(samples) >= TARGET_SAMPLES:
            break
        if any(item["sampleId"] == document["sampleId"] for item in samples):
            continue
        samples.append(document)
        sources[document["source"]] = sources.get(document["source"], 0) + 1

    if len(samples) < TARGET_SAMPLES:
        raise SystemExit(f"only produced {len(samples)} of {TARGET_SAMPLES} requested samples")

    for item in samples:
        assert_valid(item["descriptor"], item["sampleId"])

    return {
        "schemaVersion": GOLDEN_VERSION,
        "generatedBy": "tests/native_tools/generate_native_golden.py",
        "contract": {
            "actionDomain": DOMAIN_ACTION,
            "descriptorDomain": DOMAIN_DESCRIPTOR,
            "nodeDescriptorVersion": SCHEMA,
            "canonicalization": "utf8-sorted-keys,no-whitespace,ensure_ascii=false",
        },
        "sampleCount": len(samples),
        "sourceCounts": sources,
        "samples": samples,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--from-export",
        type=Path,
        default=None,
        help="Optional ueak.native.catalog-export/1.0 JSON produced by the UE automation run.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "Plugin" / "UEAgentKit" / "Resources" / "NativeTools" / "Golden" / "node-descriptor-golden.v1.json",
    )
    args = parser.parse_args()

    fixture = build_fixture(args.from_export)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(fixture, handle, ensure_ascii=False, indent=2, sort_keys=False)
        handle.write("\n")
    print(f"wrote {fixture['sampleCount']} samples to {args.out}")
    print(f"sources: {fixture['sourceCounts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
