#!/usr/bin/env python3
"""Compare M35 stress runs (three independent Editor processes)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "Build" / "m2-native" / "Evidence" / "M35"
HOST = ROOT / "Build" / "m2-native" / "M35" / "P11AutomationHost"
DLL = ROOT / "Plugin" / "UEAgentKit" / "Binaries" / "Win64" / "UnrealEditor-UEAgentKitEditor.dll"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def nodes(capture: dict) -> list[dict]:
    graph = capture["envelope"]["context"]["graphs"][0]
    return [
        {
            "guid": node["guid"],
            "pins": [
                {
                    "id": pin["id"],
                    "name": pin["name"],
                    "links": sorted((link["targetNodeGuid"], link["targetPinId"]) for link in pin["links"]),
                }
                for pin in node["pins"]
            ],
        }
        for node in graph["nodes"]
    ]


def main() -> int:
    captures = [json.loads((BASE / "Captures" / f"stress-{i}.json").read_text(encoding="utf-8")) for i in (1, 2, 3)]
    receipts = [
        json.loads((HOST / "Evidence" / f"m32-menu-stress_{i}.json").read_text(encoding="utf-8"))["receipt"]
        for i in (1, 2, 3)
    ]
    package = HOST / "Content" / "M35" / "BP_M35_Stress.uasset"
    package_sha = sha256(package) if package.is_file() else None
    base_graph = captures[0]["envelope"]["context"]["graphs"][0]
    checks = {
        "sameGraphGuid": all(c["envelope"]["context"]["graphs"][0]["guid"] == base_graph["guid"] for c in captures),
        "sameNodeGuidsOrder": all(nodes(c) == nodes(captures[0]) for c in captures),
        "samePackageRevision": all(c["preflight"]["packageRevision"] == captures[0]["preflight"]["packageRevision"] for c in captures),
        "sameFingerprint": all(c["preflight"]["graphFingerprint"] == captures[0]["preflight"]["graphFingerprint"] for c in captures),
        "samePlan": all(r["planId"] == receipts[0]["planId"] and r["layoutHash"] == receipts[0]["layoutHash"] for r in receipts),
        "sameMoved": all(sorted(r.get("movedNodeIds", [])) == sorted(receipts[0].get("movedNodeIds", [])) for r in receipts),
        "packageShaPresent": package_sha is not None,
    }
    output = {
        "schemaVersion": "ueak.m35.repeatability/1.0",
        "generatedAtUtc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "editorDllSha256": sha256(DLL),
        "packageFileSha256": package_sha,
        "graphGuid": base_graph["guid"],
        "nodeCount": len(base_graph["nodes"]),
        "runs": 3,
        "planId": receipts[0]["planId"],
        "layoutHash": receipts[0]["layoutHash"],
        "checks": checks,
        "ok": all(checks.values()),
    }
    (BASE / "repeatability.json").write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"ok": output["ok"], "checks": checks}))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
