"""0-3.3 slice 2: ue_get_asset reports a proven overlay without mixing sources."""

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


class _LiveService:
    """The minimal live service shape the read path needs for disk detection."""

    def __init__(
        self,
        project: Path,
        payload: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.config = type("Config", (), {"project_path": project})()
        self.payload = payload or {}
        self.error = error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((method, dict(params)))
        if self.error is not None:
            raise self.error
        return dict(self.payload)


class UserSaveDetectTests(unittest.TestCase):
    """0-3.3 slice 4: a read follows the disk, or says the record is stale."""

    def setUp(self) -> None:
        reset_session_overlay()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.project = self.root / "Game.uproject"
        self.project.write_bytes(b"{}")
        self.package = self.root / "Content" / "N1" / "BP_A.uasset"
        self.package.parent.mkdir(parents=True)
        self.package.write_bytes(b"v1")
        self.disk_sha = hashlib.sha256(b"v1").hexdigest()

    def tearDown(self) -> None:
        self._tmp.cleanup()
        reset_session_overlay()

    def _install(self, revision: str | None = None) -> None:
        record = _record()
        record["revision"] = f"sha256:{revision if revision is not None else self.disk_sha}"
        get_session_overlay().install(record)

    def _payload(
        self,
        sha: str | None = None,
        *,
        dirty: bool = False,
    ) -> dict[str, Any]:
        return {
            "available": True,
            "readOnly": True,
            "packageDirty": dirty,
            "editorSessionId": "session-10",
            "context": {
                "assetClass": BLUEPRINT_CLASS,
                "revision": {"value": f"sha256:{sha if sha is not None else self.disk_sha}"},
            },
        }

    def _tool(self, service: _LiveService):
        server = _Server()
        register_query_tools(
            server=server,
            index_service=_Index(),
            workflow_service=None,
            live_editor_service=service,
            read_annotations=_Annotations(readOnlyHint=True),
            error_response=_error_response,
            capabilities_response=lambda *a, **k: {},
            project_status_response=lambda *a, **k: {},
        )
        return server.tools["ue_get_asset"]

    def _save_new_bytes(self) -> str:
        self.package.write_bytes(b"v2")
        return hashlib.sha256(b"v2").hexdigest()

    def test_unchanged_disk_does_not_call_the_bridge(self) -> None:
        self._install()
        service = _LiveService(self.project, self._payload())
        out = self._tool(service)(asset_path=ASSET)
        self.assertEqual(service.calls, [])
        self.assertFalse(out["overlay"]["overlayRefreshed"])
        self.assertFalse(out["overlay"]["overlayStale"])
        self.assertEqual(out["overlay"]["overlayCause"], "installed")
        self.assertIsNone(out["overlay"]["staleCause"])

    def test_changed_disk_refreshes_through_the_bridge(self) -> None:
        self._install()
        new_sha = self._save_new_bytes()
        service = _LiveService(self.project, self._payload(new_sha))
        out = self._tool(service)(asset_path=ASSET)
        self.assertEqual(len(service.calls), 1)
        self.assertEqual(service.calls[0][0], "editor.getBlueprintGraphContext")
        self.assertTrue(out["overlay"]["overlayRefreshed"])
        self.assertFalse(out["overlay"]["overlayStale"])
        self.assertEqual(out["overlay"]["overlayCause"], "disk-changed")
        self.assertEqual(out["overlay"]["revision"], f"sha256:{new_sha}")
        self.assertEqual(get_session_overlay().get(ASSET)["revision"], f"sha256:{new_sha}")

    def test_dirty_package_marks_the_record_stale(self) -> None:
        self._install()
        new_sha = self._save_new_bytes()
        service = _LiveService(self.project, self._payload(new_sha, dirty=True))
        out = self._tool(service)(asset_path=ASSET, sections=["overlay"])
        self.assertTrue(out["overlay"]["overlayStale"])
        self.assertEqual(out["overlay"]["staleCause"], "live-index-refresh-package-dirty")
        self.assertFalse(out["overlay"]["overlayRefreshed"])
        # The old canonical is never presented as the current disk state.
        self.assertNotIn("canonical", out["overlay"])

    def test_bridge_revision_mismatch_marks_stale(self) -> None:
        self._install()
        self._save_new_bytes()
        service = _LiveService(self.project, self._payload("f" * 64))
        out = self._tool(service)(asset_path=ASSET)
        self.assertTrue(out["overlay"]["overlayStale"])
        self.assertEqual(out["overlay"]["staleCause"], "live-index-refresh-stale-proof")
        self.assertEqual(out["overlay"]["revision"], f"sha256:{self.disk_sha}")

    def test_missing_package_file_marks_stale_without_raising(self) -> None:
        self._install()
        self.package.unlink()
        service = _LiveService(self.project, self._payload())
        out = self._tool(service)(asset_path=ASSET, sections=["overlay"])
        self.assertTrue(out["ok"])
        self.assertTrue(out["overlay"]["overlayStale"])
        self.assertEqual(out["overlay"]["staleCause"], "package-file-missing")
        self.assertEqual(service.calls, [])
        self.assertNotIn("canonical", out["overlay"])


if __name__ == "__main__":
    unittest.main()
