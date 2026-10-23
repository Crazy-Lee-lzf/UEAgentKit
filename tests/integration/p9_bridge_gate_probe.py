#!/usr/bin/env python3
"""P9 live bridge capability and negative-gate probe.

Runs against the minimized disposable P9 Editor.  It verifies that the P9-only
write methods are exposed by that exact host, that P8/production write methods
are absent, and that wrong asset/confirmation requests fail before any write.
No successful P9 apply or save is performed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ue_agent_kit import __version__  # noqa: E402
from ue_agent_kit.editor_bridge import (  # noqa: E402
    LiveEditorBridgeConfig,
    LiveEditorBridgeService,
    LiveEditorError,
)


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _request(plan_id: str) -> dict[str, Any]:
    return {
        "schemaVersion": "ueak.graphlayoutapply/1.0",
        "assetPath": "/Game/UEAgentKitP9/BP_LayoutP9.BP_LayoutP9",
        "graphGuid": "51e9a5e4-4654-f8a3-8179-8d83fc702c90",
        "requestedMode": "position-only",
        "allowSave": False,
        "allowCompile": False,
        "acceptanceEligible": False,
        "requiredAcceptanceEvidence": "independent-reload",
        "plan": {
            "schemaVersion": "ueak.layoutplan/1.0",
            "planId": plan_id,
            "positions": [],
            "movedNodeIds": [],
        },
    }


def _expect_error(
    service: LiveEditorBridgeService,
    method: str,
    params: dict[str, Any],
    code: str,
) -> dict[str, Any]:
    try:
        service.call_method(method, params)
    except LiveEditorError as exc:
        if exc.code != code:
            raise RuntimeError(f"{method}: expected {code}, got {exc.code}: {exc}") from exc
        return {"expectedErrorCode": code, "observedErrorCode": exc.code, "message": str(exc)}
    raise RuntimeError(f"{method}: expected {code}, but the call succeeded")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project",
        type=Path,
        default=ROOT / "Build" / "P9AutomationHost" / "P9AutomationHost.uproject",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "Build" / "P9AutomationHost" / "Evidence" / "p9-bridge-gate-result.json",
    )
    args = parser.parse_args(argv)
    service = LiveEditorBridgeService(
        LiveEditorBridgeConfig(project_path=args.project, timeout_seconds=15.0),
        server_version=__version__,
    )
    status = service.status()
    capabilities = set(status.get("capabilities", []))
    required = {
        "editor.applyBlueprintGraphLayoutP9TestOnly",
        "editor.saveBlueprintGraphLayoutP9TestOnly",
        "editor.getFocusedBlueprintGraphContext",
    }
    forbidden = {
        "editor.applyBlueprintGraphLayoutP8TestOnly",
        "editor.saveBlueprintGraphLayoutP8TestOnly",
        "editor.applyBlueprintGraphLayout",
    }
    missing = sorted(required - capabilities)
    leaked = sorted(forbidden & capabilities)
    if missing or leaked:
        raise RuntimeError(f"capability mismatch missing={missing} leaked={leaked}")

    before = service.call_method("editor.getBlueprintGraphLayoutPreflight", {})
    wrong_asset = _request("layout:p9-gate-probe")
    wrong_asset["assetPath"] = "/Game/UEAgentKitP8/BP_LayoutP8.BP_LayoutP8"
    asset_negative = _expect_error(
        service,
        "editor.applyBlueprintGraphLayoutP9TestOnly",
        {"request": wrong_asset, "confirmation": "APPLY P9 TEST-ONLY layout:p9-gate-probe"},
        "graph-layout-test-only-asset-rejected",
    )
    confirmation_negative = _expect_error(
        service,
        "editor.applyBlueprintGraphLayoutP9TestOnly",
        {"request": _request("layout:p9-gate-probe"), "confirmation": "APPLY P9 TEST-ONLY wrong"},
        "graph-layout-test-only-confirmation-required",
    )
    after = service.call_method("editor.getBlueprintGraphLayoutPreflight", {})
    unchanged = (
        before.get("packageRevision") == after.get("packageRevision")
        and before.get("graphFingerprint") == after.get("graphFingerprint")
        and before.get("packageDirty") is False
        and after.get("packageDirty") is False
    )
    if not unchanged:
        raise RuntimeError("preflight identity changed during negative gate probe")

    result = {
        "schemaVersion": "ueak.p9.bridgegate/1.0",
        "ok": True,
        "readOnlyProbe": True,
        "successfulApplyPerformed": False,
        "projectName": status.get("projectName"),
        "processId": status.get("processId"),
        "capabilities": sorted(capabilities),
        "requiredCapabilities": sorted(required),
        "forbiddenCapabilitiesAbsent": sorted(forbidden),
        "assetNegative": asset_negative,
        "confirmationNegative": confirmation_negative,
        "packageRevision": before.get("packageRevision"),
        "graphFingerprint": before.get("graphFingerprint"),
        "positionsUnchanged": unchanged,
    }
    _write_json(args.out, result)
    print(json.dumps({
        "ok": True,
        "out": str(args.out),
        "sha256": "sha256:" + hashlib.sha256(args.out.read_bytes()).hexdigest(),
        "capabilities": len(capabilities),
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
