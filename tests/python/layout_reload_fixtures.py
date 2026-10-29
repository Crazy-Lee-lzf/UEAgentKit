"""Fixtures for the independent-reload evidence harness tests.

The document shapes are not invented: the export is the real
``BlueprintContextExport`` schema (``tests/fixtures/graph_contracts/bpctx_export_linear.json``),
the apply/save results mirror the exact fields ``BlueprintGraphLayoutExperimental.cpp``
returns, and the save receipt keeps ``independentReloadVerified: false``.

Numbers are made mutually consistent (the synthetic ``.uasset`` bytes are the
source of truth for ``assetSha256`` and the ``package-sha256-v1`` revision) so a
test can change exactly one thing and prove the harness refuses that one thing.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
HARNESS_PATH = ROOT / "tools" / "layout_independent_reload_harness.py"


def load_harness():
    """Import the read-only harness module by path (tools/ is not a package)."""

    name = "ueak_layout_independent_reload_harness"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, HARNESS_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load harness from {HARNESS_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


harness = load_harness()
build_probe = harness.build_probe
layout_hash_from_positions = harness.layout_hash_from_positions
verify_independent_reload = harness.verify_independent_reload

GRAPH_GUID = "10000000-0000-4000-8000-000000000001"
ASSET_PATH = "/Game/UEAgentKitTests/BP_Layout.BP_Layout"
ASSET_FILENAME = "BP_Layout.uasset"
ENGINE_VERSION = "5.6.1"

EDITOR_PID = 1111
EDITOR_STARTED_UTC = "2026-10-20T02:00:00.000Z"
EDITOR_TERMINATED_UTC = "2026-10-20T02:10:00.000Z"
PROBE_PID = 4242
PROBE_STARTED_UTC = "2026-10-20T02:11:00.000Z"

# Post-layout positions: the plan moves the four real fixture nodes.
TARGET_POSITIONS = {
    "n:11000000-0000-4000-8000-000000000001": (0, 0),
    "n:11000000-0000-4000-8000-000000000002": (0, 240),
    "n:11000000-0000-4000-8000-000000000003": (320, 240),
    "n:11000000-0000-4000-8000-000000000004": (640, 240),
}


def asset_bytes() -> bytes:
    """Deterministic stand-in for a saved ``.uasset`` payload."""

    return b"UEAK-INDEPENDENT-RELOAD-FIXTURE\x00" + json.dumps(
        {"graph": GRAPH_GUID, "positions": TARGET_POSITIONS}, sort_keys=True
    ).encode("utf-8")


def asset_sha256(payload: bytes | None = None) -> str:
    return "sha256:" + hashlib.sha256(payload if payload is not None else asset_bytes()).hexdigest()


def write_asset(directory: Path, payload: bytes | None = None) -> Path:
    path = directory / ASSET_FILENAME
    path.write_bytes(payload if payload is not None else asset_bytes())
    return path


def export_document(positions: dict[str, tuple[int, int]] | None = None) -> dict[str, Any]:
    """A real-shape BlueprintContextExport document with the target layout."""

    layout = positions or TARGET_POSITIONS
    source = json.loads(
        (Path(__file__).resolve().parents[1] / "fixtures" / "graph_contracts" / "bpctx_export_linear.json").read_text(
            encoding="utf-8"
        )
    )
    graph = source["graphs"][0]
    metadata = {"n:" + node["guid"]: node for node in graph["nodes"]}
    nodes = []
    for node_id in sorted(layout):
        x, y = layout[node_id]
        template = metadata.get(node_id) or {
            "guid": node_id[2:],
            "name": "SyntheticNode",
            "class": "/Script/BlueprintGraph.K2Node_CallFunction",
            "title": "Synthetic",
            "comment": "",
            "pins": [],
        }
        node = dict(template)
        node["layout"] = {"x": x, "y": y, "width": 208, "height": 120}
        nodes.append(node)
    graph = dict(graph, guid=GRAPH_GUID, nodes=nodes)
    source["graphs"] = [graph]
    source["assetPath"] = ASSET_PATH
    source["engineVersion"] = ENGINE_VERSION
    source["revision"] = {
        "strategy": "package-sha256-v1",
        "available": True,
        "packageDirty": False,
        "value": asset_sha256(),
        "packageGuid": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        "fileSize": len(asset_bytes()),
        "modifiedUtc": "2026-10-20T02:10:00.000Z",
        "contentSha256": asset_sha256()[len("sha256:"):],
    }
    return source


def apply_result(positions: dict[str, tuple[int, int]] | None = None) -> dict[str, Any]:
    """Exact shape of the ``editor.applyBlueprintGraphLayoutExperimental`` result."""

    layout = positions or TARGET_POSITIONS
    ordered = [{"nodeId": node_id, "x": x, "y": y} for node_id, (x, y) in sorted(layout.items())]
    from_layout_hash = layout_hash(layout)
    return {
        "result": {
            "experimental": True,
            "applied": True,
            "noChange": False,
            "confirmation": "APPLY EXPERIMENTAL LAYOUT plan-layout-0001",
            "structuralInvariant": True,
            "undoVerified": True,
            "redoVerified": True,
            "layoutHashVerified": True,
            "layoutHash": from_layout_hash,
            "topologyHashBefore": "sha1:" + "1" * 40,
            "topologyHashAfter": "sha1:" + "2" * 40,
            "positionsBefore": [
                {"nodeId": node_id, "x": x + 100, "y": y + 100} for node_id, (x, y) in sorted(layout.items())
            ],
            "positionsAfter": ordered,
            "saved": False,
            "compiled": False,
            "autoSave": False,
            "savePolicy": "explicit-separate-experimental-save-bound-to-layoutHash",
            "graphFingerprintAfterApply": "sha1:" + "c" * 40,
            # The real Apply result carries no asset/graph identity: it is bound
            # to the graph through layoutHash only.
        }
    }


def save_result(positions: dict[str, tuple[int, int]] | None = None) -> dict[str, Any]:
    """Exact shape of the ``editor.saveBlueprintGraphLayoutExperimental`` result."""

    layout_hash_value = layout_hash(positions or TARGET_POSITIONS)
    return {
        "result": {
            "action": "save-experimental-graph-layout",
            "experimental": True,
            "assetPath": ASSET_PATH,
            "graphGuid": GRAPH_GUID,
            "layoutHash": layout_hash_value,
            "graphFingerprint": "sha1:" + "c" * 40,
            "assetFilename": "D:/Proj/Content/UEAgentKitTests/BP_Layout.uasset",
            "assetSha256": asset_sha256(),
            "confirmation": "SAVE EXPERIMENTAL LAYOUT " + layout_hash_value,
            "saved": True,
            "packageDirty": False,
            "readbackVerified": True,
            # Same-process Save is deliberately not acceptance evidence.
            "independentReloadVerified": False,
            "requiredAcceptanceEvidence": "independent-reload",
            "readback": {
                "assetPath": ASSET_PATH,
                "graphGuid": GRAPH_GUID,
                "assetSha256": asset_sha256(),
                "layoutHash": layout_hash_value,
                "packageRevision": asset_sha256(),
                "graphFingerprint": "sha1:" + "c" * 40,
                "packageDirty": False,
                "positionHashMatchesPlan": True,
                "independentReloadVerified": False,
                "requiredAcceptanceEvidence": "independent-reload",
            },
            "compiled": False,
            "autoSave": False,
        }
    }


def layout_hash(positions: dict[str, tuple[int, int]]) -> str:
    return layout_hash_from_positions(
        [{"nodeId": node_id, "x": x, "y": y} for node_id, (x, y) in positions.items()]
    )


def probe_document(
    *,
    positions: dict[str, tuple[int, int]] | None = None,
    pid: int = PROBE_PID,
    process_start_utc: str = PROBE_STARTED_UTC,
    started_at_utc: str = PROBE_STARTED_UTC,
    asset_sha: str | None = None,
    revision: str | None = None,
) -> dict[str, Any]:
    probe = build_probe(
        export_document=export_document(positions),
        asset_sha256=asset_sha or asset_sha256(),
        process_id=pid,
        process_start_utc=process_start_utc,
        started_at_utc=started_at_utc,
        command=(
            'UnrealEditor-Cmd.exe "D:/Proj/Proj.uproject" -run=BlueprintContextExport '
            f"-Asset={ASSET_PATH} -Output=D:/tmp/probe -Profile=logic -Format=json "
            "-IncludeLayout -unattended -nop4 -nosplash -NoSound -NullRHI -stdout"
        ),
        executable="D:/UE_5.6/Engine/Binaries/Win64/UnrealEditor-Cmd.exe",
        host_name="UEAK-P11-HOST",
        reloaded_at_utc=started_at_utc,
    )
    if revision is not None:
        probe["packageRevision"] = revision
    return probe


def editor_process() -> dict[str, Any]:
    return {
        "pid": EDITOR_PID,
        "startedAtUtc": EDITOR_STARTED_UTC,
        "terminatedAtUtc": EDITOR_TERMINATED_UTC,
    }
