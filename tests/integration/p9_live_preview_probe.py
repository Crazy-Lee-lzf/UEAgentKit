#!/usr/bin/env python3
"""P9 headless read-only preview through the live Editor Bridge.

This runs while the disposable P9 Editor is minimized.  It uses the same
canonical context and preflight objects as the Editor menu, then calls the P0
Python preview path.  It never calls an apply, save, or compile method.
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
)
from ue_agent_kit.graph_contracts.layout import recompute_layout_hash  # noqa: E402
from ue_agent_kit.graph_layout_preview import build_preview  # noqa: E402


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project",
        type=Path,
        default=ROOT / "Build" / "P9AutomationHost" / "P9AutomationHost.uproject",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=ROOT / "Build" / "P9AutomationHost" / "Evidence",
    )
    args = parser.parse_args(argv)
    out_dir: Path = args.out_dir

    service = LiveEditorBridgeService(
        LiveEditorBridgeConfig(project_path=args.project, timeout_seconds=15.0),
        server_version=__version__,
    )
    context = service.call_method("editor.getFocusedBlueprintGraphContext", {})
    preflight = service.call_method("editor.getBlueprintGraphLayoutPreflight", {})
    _require(context.get("available") is True, "context is unavailable")
    _require(context.get("readOnly") is True, "context is not read-only")
    _require(preflight.get("available") is True, "preflight is unavailable")
    _require(preflight.get("complete") is True, "preflight is truncated")
    _require(preflight.get("pieState") == "stopped", "PIE/SIE is not stopped")
    _require(preflight.get("packageDirty") is False, "package is dirty")

    # The Editor menu copies these transport fields from the context response
    # into the preflight because the P6 preflight intentionally omits them.
    preflight["sessionId"] = context["sessionId"]
    preflight["pluginBuildHash"] = context["pluginBuildHash"]

    result = build_preview(
        preflight,
        context,
        mode="full",
        spacing_x=96,
        spacing_y=64,
        target_width=1800,
        seed=0,
    )
    plan = result["plan"]
    _require(plan["layoutHash"] == recompute_layout_hash(plan), "layout hash mismatch")
    _require(plan["planId"].startswith("layout:"), "plan id is not canonical")

    preview_path = out_dir / "p9-live-preview.json"
    preflight_path = out_dir / "p9-live-preview-preflight.json"
    context_path = out_dir / "p9-live-preview-context.json"
    _write_json(preview_path, {
        "schemaVersion": "ueak.p9.live-preview/1.0",
        "ok": True,
        "readOnly": True,
        "applied": False,
        "saved": False,
        "compiled": False,
        "preview": result,
    })
    _write_json(preflight_path, preflight)
    _write_json(context_path, context)
    preflight_bytes = preflight_path.read_bytes()
    context_bytes = context_path.read_bytes()
    print(json.dumps({
        "ok": True,
        "planId": plan["planId"],
        "layoutHash": plan["layoutHash"],
        "nodeCount": len(plan["positions"]),
        "movedCount": len(plan.get("movedNodeIds", [])),
        "metrics": plan.get("metrics", {}),
        "previewPath": str(preview_path),
        "previewSha256": _sha256_bytes(preview_path.read_bytes()),
        "preflightSha256": _sha256_bytes(preflight_bytes),
        "contextSha256": _sha256_bytes(context_bytes),
        "pieState": preflight.get("pieState"),
        "packageDirty": preflight.get("packageDirty"),
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
