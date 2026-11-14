"""0-3.3 slice 3: ue_refresh_asset_index_live installs a proven overlay record."""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ue_agent_kit.live_asset_overlay import BLUEPRINT_CLASS  # noqa: E402
from ue_agent_kit.mcp_live_action_tools import (  # noqa: E402
    get_session_overlay,
    register_live_action_tools,
    reset_session_overlay,
)

ASSET = "/Game/N1/BP_A.BP_A"


class _Annotations:
    def __init__(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)


class _Server:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, *, annotations: Any = None):
        def decorator(function):
            self.tools[function.__name__] = function
            return function

        return decorator


def _error_response(tool: str, exc: Exception, *, read_only: bool) -> dict[str, Any]:
    return {
        "ok": False,
        "tool": tool,
        "readOnly": read_only,
        "error": {"code": getattr(exc, "code", "ue-agent-kit-error"), "message": str(exc)},
    }


class _Service:
    def __init__(self, project: Path, payload: dict[str, Any]) -> None:
        self.config = type("Config", (), {"project_path": project})()
        self.payload = payload
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((method, dict(params)))
        return dict(self.payload)


class RefreshToolTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_session_overlay()
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name) / "Game.uproject"
        self.project.write_bytes(b"{}")
        package = Path(self._tmp.name) / "Content" / "N1" / "BP_A.uasset"
        package.parent.mkdir(parents=True)
        package.write_bytes(b"blueprint-bytes")
        self.sha = hashlib.sha256(b"blueprint-bytes").hexdigest()

    def tearDown(self) -> None:
        self._tmp.cleanup()
        reset_session_overlay()

    def payload(self, **overrides: Any) -> dict[str, Any]:
        base = {
            "available": True,
            "readOnly": True,
            "packageDirty": False,
            "editorSessionId": "session-9",
            "context": {
                "assetClass": BLUEPRINT_CLASS,
                "revision": {"value": f"sha256:{self.sha}"},
            },
        }
        base.update(overrides)
        return base

    def tool(self, payload: dict[str, Any] | None = None):
        server = _Server()
        service = _Service(self.project, payload if payload is not None else self.payload())
        register_live_action_tools(
            server=server,
            live_editor_service=service,
            tool_annotations_type=_Annotations,
            error_response=_error_response,
        )
        return server.tools["ue_refresh_asset_index_live"], service

    def test_installs_a_proven_record(self) -> None:
        tool, service = self.tool()
        out = tool(ASSET)
        self.assertTrue(out["ok"])
        self.assertEqual(out["revision"], f"sha256:{self.sha}")
        self.assertFalse(out["indexFresh"])
        self.assertTrue(out["overlayUpdated"])
        self.assertFalse(out["restartRequired"])
        self.assertEqual(out["overlayEntryCount"], 1)
        self.assertEqual(service.calls[0][0], "editor.getBlueprintGraphContext")
        self.assertIsNotNone(get_session_overlay().get(ASSET))

    def test_refuses_dirty_package(self) -> None:
        tool, _ = self.tool(self.payload(packageDirty=True))
        out = tool(ASSET)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "live-index-refresh-package-dirty")
        self.assertIsNone(get_session_overlay().get(ASSET))

    def test_refuses_unproven_revision(self) -> None:
        bad = self.payload()
        bad["context"]["revision"]["value"] = "sha256:" + "b" * 64
        tool, _ = self.tool(bad)
        out = tool(ASSET)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "live-index-refresh-stale-proof")

    def test_refuses_non_blueprint(self) -> None:
        bad = self.payload()
        bad["context"]["assetClass"] = "/Script/Engine.Material"
        tool, _ = self.tool(bad)
        out = tool(ASSET)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "live-index-refresh-identity-drift")

    def test_refuses_missing_package_file(self) -> None:
        tool, _ = self.tool()
        out = tool("/Game/N1/Nope.Nope")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "live-index-refresh-asset-file-missing")

    def test_second_refresh_replaces_in_place(self) -> None:
        tool, _ = self.tool()
        tool(ASSET)
        out = tool(ASSET)
        self.assertEqual(out["overlayEntryCount"], 1)
        self.assertEqual(get_session_overlay().stats()["entryCount"], 1)

    def test_tool_is_registered_in_live_mode(self) -> None:
        from ue_agent_kit.tool_registry import tool_names_for_mode

        self.assertIn(
            "ue_refresh_asset_index_live",
            tool_names_for_mode(live_editor_enabled=True),
        )
        self.assertNotIn(
            "ue_refresh_asset_index_live",
            tool_names_for_mode(workflow_enabled=True),
        )


if __name__ == "__main__":
    unittest.main()
