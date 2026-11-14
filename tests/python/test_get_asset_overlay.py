"""0-3.3 slice 2: ue_get_asset reports a proven overlay without mixing sources."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ue_agent_kit.live_asset_overlay import (  # noqa: E402
    BLUEPRINT_CLASS,
    get_session_overlay,
    reset_session_overlay,
)
from ue_agent_kit.mcp_query_tools import register_query_tools  # noqa: E402

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


class _Index:
    def __init__(self, response: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        self.response = response if response is not None else {
            "schemaVersion": "1.0",
            "tool": "ue_get_asset",
            "ok": True,
            "assetPath": ASSET,
            "sections": {"identity": {"contentClass": "Blueprint"}},
        }
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def get_asset(self, asset_path: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"assetPath": asset_path, **kwargs})
        if self.error is not None:
            raise self.error
        return dict(self.response)


def _record() -> dict[str, Any]:
    return {
        "assetPath": ASSET,
        "assetClass": BLUEPRINT_CLASS,
        "revision": "sha256:" + "a" * 64,
        "canonical": {"graphs": []},
        "capturedAtUtc": "2026-11-12T00:00:00Z",
        "editorSessionId": "session-9",
        "source": "live-editor-canonical",
    }


class GetAssetOverlayTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_session_overlay()

    def tearDown(self) -> None:
        reset_session_overlay()

    def tool(self, index: _Index):
        server = _Server()
        register_query_tools(
            server=server,
            index_service=index,
            workflow_service=None,
            live_editor_service=None,
            read_annotations=_Annotations(readOnlyHint=True),
            error_response=_error_response,
            capabilities_response=lambda *a, **k: {},
            project_status_response=lambda *a, **k: {},
        )
        return server.tools["ue_get_asset"]

    def test_without_overlay_the_response_is_untouched(self) -> None:
        index = _Index()
        out = self.tool(index)(asset_path=ASSET)
        self.assertNotIn("overlay", out)
        self.assertNotIn("source", out)
        self.assertEqual(out["sections"], {"identity": {"contentClass": "Blueprint"}})

    def test_overlay_metadata_is_attached_with_explicit_provenance(self) -> None:
        get_session_overlay().install(_record())
        out = self.tool(_Index())(asset_path=ASSET)
        self.assertEqual(out["source"], "index+overlay")
        self.assertFalse(out["indexFresh"])
        self.assertEqual(out["overlay"]["revision"], "sha256:" + "a" * 64)
        # Index fields stay index fields; the canonical is not smuggled in.
        self.assertNotIn("canonical", out["overlay"])
        self.assertEqual(out["sections"]["identity"]["contentClass"], "Blueprint")

    def test_canonical_only_when_the_overlay_section_is_requested(self) -> None:
        get_session_overlay().install(_record())
        tool = self.tool(_Index())
        out = tool(asset_path=ASSET, sections=["symbols"])
        self.assertNotIn("canonical", out["overlay"])

    def test_overlay_section_alone_skips_the_index(self) -> None:
        get_session_overlay().install(_record())
        index = _Index()
        out = self.tool(index)(asset_path=ASSET, sections=["overlay"])
        self.assertEqual(index.calls, [])
        self.assertEqual(out["source"], "overlay")
        self.assertEqual(out["overlay"]["canonical"], {"graphs": []})

    def test_overlay_plus_other_sections_queries_the_index_without_overlay(self) -> None:
        get_session_overlay().install(_record())
        index = _Index()
        out = self.tool(index)(asset_path=ASSET, sections=["overlay", "symbols"])
        self.assertEqual(index.calls[0]["sections"], ["symbols"])
        self.assertEqual(out["overlay"]["canonical"], {"graphs": []})
        self.assertEqual(out["source"], "index+overlay")

    def test_missing_index_degrades_to_the_overlay_only(self) -> None:
        get_session_overlay().install(_record())
        out = self.tool(_Index(error=FileNotFoundError("no index")))(asset_path=ASSET)
        self.assertTrue(out["ok"])
        self.assertEqual(out["source"], "overlay")
        self.assertIn("index-unavailable", out["degradedReason"])

    def test_missing_index_without_overlay_still_errors(self) -> None:
        out = self.tool(_Index(error=FileNotFoundError("no index")))(asset_path=ASSET)
        self.assertFalse(out["ok"])

    def test_overlay_section_without_a_record_errors(self) -> None:
        out = self.tool(_Index())(asset_path=ASSET, sections=["overlay"])
        self.assertFalse(out["ok"])
        self.assertIn("No proven live overlay record", out["error"]["message"])


if __name__ == "__main__":
    unittest.main()
