#!/usr/bin/env python3
"""Compare two independent Z1 runs for package/GUID determinism."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "Build" / "m2-native" / "Evidence" / "M33" / "Guid"
HOST = ROOT / "Build" / "m2-native" / "M32" / "P11AutomationHost"
DLL = ROOT / "Plugin" / "UEAgentKit" / "Binaries" / "Win64" / "UnrealEditor-UEAgentKitEditor.dll"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def graph_data(capture: dict) -> dict:
    graph = capture["envelope"]["context"]["graphs"][0]
    nodes = []
    for node in graph["nodes"]:
        nodes.append(
            {
                "guid": node["guid"],
                "class": node["class"],
                "pins": [
                    {
                        "id": pin["id"],
                        "name": pin["name"],
                        "links": sorted(
                            (link["targetNodeGuid"], link["targetPinId"])
                            for link in pin.get("links", [])
                        ),
                    }
                    for pin in node.get("pins", [])
                ],
            }
        )
    return {
        "graphGuid": graph.get("guid"),
        "nodes": nodes,
        "packageRevision": capture["preflight"].get("packageRevision"),
        "graphFingerprint": capture["preflight"].get("graphFingerprint"),
    }


def main() -> int:
    run1 = {p.stem.replace("capture-", ""): p for p in (BASE / "run1").glob("capture-*.json")}
    run2 = {p.stem.replace("capture-", ""): p for p in (BASE / "run2").glob("capture-*.json")}
    z1 = json.loads((BASE / "run1" / "z1.json").read_text(encoding="utf-8"))
    receipts = {g["kind"].replace("-", "_"): g["receipt"] for g in z1["graphs"]}
    results = []
    for key in sorted(set(run1) & set(run2)):
        a = json.loads(run1[key].read_text(encoding="utf-8"))
        b = json.loads(run2[key].read_text(encoding="utf-8"))
        ga = graph_data(a)
        gb = graph_data(b)
        asset_path = a["assetPath"].split(".")[0]  # /Game/M32/BP...
        relative = asset_path.removeprefix("/Game/")
        package_file = HOST / "Content" / f"{relative}.uasset"
        package_sha = sha256(package_file) if package_file.is_file() else None
        receipt = receipts.get(key, {})
        checks = {
            "graphGuidEqual": ga["graphGuid"] == gb["graphGuid"],
            "nodeGuidsAndOrderEqual": [n["guid"] for n in ga["nodes"]] == [n["guid"] for n in gb["nodes"]],
            "pinGuidsAndLinksEqual": [n["pins"] for n in ga["nodes"]] == [n["pins"] for n in gb["nodes"]],
            "packageRevisionEqual": ga["packageRevision"] == gb["packageRevision"],
            "graphFingerprintEqual": ga["graphFingerprint"] == gb["graphFingerprint"],
            "packageFileShaPresent": package_sha is not None,
            "receiptPlanEqual": receipt.get("planId") == z1["graphs"][
                next(i for i, g in enumerate(z1["graphs"]) if g["kind"].replace("-", "_") == key)
            ]["receipt"].get("planId"),
        }
        results.append(
            {
                "graph": key,
                "assetPath": a["assetPath"],
                "packageFileSha256": package_sha,
                "run1": ga,
                "run2": gb,
                "receiptPlanId": receipt.get("planId"),
                "checks": checks,
                "ok": all(checks.values()),
            }
        )
    output = {
        "schemaVersion": "ueak.m33.guid-repeatability/1.0",
        "generatedAtUtc": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "editorDllSha256": sha256(DLL),
        "graphs": results,
        "ok": len(results) == 3 and all(r["ok"] for r in results),
    }
    (BASE / "guid-repeatability.json").write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"ok": output["ok"], "graphs": len(results)}))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
