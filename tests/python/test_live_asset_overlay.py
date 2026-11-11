"""0-3.3 slice 1: live asset overlay storage and proof rules."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ue_agent_kit.live_asset_overlay import (  # noqa: E402
    BLUEPRINT_CLASS,
    OVERLAY_SCHEMA_VERSION,
    LiveAssetOverlay,
    LiveOverlayError,
    build_overlay_record,
    package_file_for_object_path,
)

PROJECT = Path(r"C:/Proj/Game.uproject")
BP = "/Game/N1/BP_A.BP_A"
SHA = "a" * 64
SESSION = "session-1"


def canonical(*, dirty: bool = False, available: bool = True, read_only: bool = True,
              revision: str = f"sha256:{SHA}") -> dict:
    return {
        "available": available,
        "readOnly": read_only,
        "packageDirty": dirty,
        "context": {"revision": {"value": revision}},
    }


def record(**overrides) -> dict:
    kwargs = dict(
        asset_path=BP,
        asset_class=BLUEPRINT_CLASS,
        expected_class=BLUEPRINT_CLASS,
        bridge_canonical=canonical(),
        disk_sha256=SHA,
        editor_session_id=SESSION,
        captured_at_utc="2026-11-12T00:00:00Z",
    )
    kwargs.update(overrides)
    return build_overlay_record(**kwargs)


class PackagePathTests(unittest.TestCase):
    def test_maps_object_path_to_uasset(self) -> None:
        path = package_file_for_object_path(PROJECT, "/Game/A/B/BP_C.BP_C")
        self.assertEqual(path.name, "BP_C.uasset")
        self.assertEqual(path.parent.name, "B")
        self.assertEqual(path.parent.parent.name, "A")

    def test_refuses_non_game_path(self) -> None:
        for bad in ("/Engine/x.x", "Game/x.x", "", "/Game/x"):
            with self.subTest(bad=bad):
                with self.assertRaises(LiveOverlayError) as ctx:
                    package_file_for_object_path(PROJECT, bad)
                self.assertEqual(ctx.exception.code, "live-index-refresh-invalid-asset")

    def test_refuses_traversal_and_separators(self) -> None:
        for bad in (
            "/Game/../Secret.BP",
            "/Game//BP.BP",
            "/Game/A\\B.BP",
            "/Game/C:/BP.BP",
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(LiveOverlayError):
                    package_file_for_object_path(PROJECT, bad)


class ProofTests(unittest.TestCase):
    def test_accepts_a_clean_proven_record(self) -> None:
        rec = record()
        self.assertEqual(rec["schemaVersion"], OVERLAY_SCHEMA_VERSION)
        self.assertEqual(rec["revision"], f"sha256:{SHA}")
        self.assertFalse(rec["indexFresh"])
        self.assertTrue(rec["overlayUpdated"])
        self.assertFalse(rec["restartRequired"])

    def test_refuses_dirty_package(self) -> None:
        with self.assertRaises(LiveOverlayError) as ctx:
            record(bridge_canonical=canonical(dirty=True))
        self.assertEqual(ctx.exception.code, "live-index-refresh-package-dirty")

    def test_refuses_identity_drift(self) -> None:
        with self.assertRaises(LiveOverlayError) as ctx:
            record(asset_class="/Script/Engine.Material")
        self.assertEqual(ctx.exception.code, "live-index-refresh-identity-drift")

    def test_refuses_unproven_revision(self) -> None:
        # The core rule: the overlay may only describe what is actually on disk.
        for bridge_rev in (f"sha256:{'b'*64}", "", "sha256:"):
            with self.subTest(bridge_rev=bridge_rev):
                with self.assertRaises(LiveOverlayError) as ctx:
                    record(bridge_canonical=canonical(revision=bridge_rev))
                self.assertEqual(ctx.exception.code, "live-index-refresh-stale-proof")

    def test_refuses_unavailable_or_not_readonly(self) -> None:
        with self.assertRaises(LiveOverlayError) as ctx:
            record(bridge_canonical=canonical(available=False))
        self.assertEqual(ctx.exception.code, "live-index-refresh-unavailable")
        with self.assertRaises(LiveOverlayError) as ctx2:
            record(bridge_canonical=canonical(read_only=False))
        self.assertEqual(ctx2.exception.code, "live-index-refresh-not-readonly")

    def test_refuses_missing_context(self) -> None:
        broken = {"available": True, "readOnly": True, "packageDirty": False}
        with self.assertRaises(LiveOverlayError) as ctx:
            record(bridge_canonical=broken)
        self.assertEqual(ctx.exception.code, "live-index-refresh-no-canonical")


class OverlayStoreTests(unittest.TestCase):
    def test_install_and_get_roundtrip_is_a_copy(self) -> None:
        overlay = LiveAssetOverlay()
        rec = record()
        overlay.install(rec)
        rec["assetPath"] = "mutated"
        got = overlay.get(BP)
        self.assertIsNotNone(got)
        self.assertEqual(got["assetPath"], BP)
        got["assetPath"] = "also-mutated"
        self.assertEqual(overlay.get(BP)["assetPath"], BP)

    def test_get_unknown_returns_none(self) -> None:
        self.assertIsNone(LiveAssetOverlay().get("/Game/x.y"))

    def test_reinstall_moves_to_the_end(self) -> None:
        overlay = LiveAssetOverlay(max_entries=2)
        a = record(asset_path="/Game/A.A")
        b = record(asset_path="/Game/B.B")
        overlay.install(a)
        overlay.install(b)
        overlay.install(a)
        overlay.install(record(asset_path="/Game/C.C"))
        # A was refreshed last before C, so B is the one evicted.
        self.assertIsNone(overlay.get("/Game/B.B"))
        self.assertIsNotNone(overlay.get("/Game/A.A"))
        self.assertIsNotNone(overlay.get("/Game/C.C"))

    def test_bounded_by_max_entries(self) -> None:
        overlay = LiveAssetOverlay(max_entries=2)
        for name in ("A", "B", "C"):
            overlay.install(record(asset_path=f"/Game/{name}.{name}"))
        self.assertEqual(overlay.stats()["entryCount"], 2)

    def test_drop_session_discards_other_sessions(self) -> None:
        overlay = LiveAssetOverlay()
        overlay.install(record())
        overlay.install(record(asset_path="/Game/Old.Old", editor_session_id="session-0"))
        self.assertEqual(overlay.drop_session(SESSION), 1)
        self.assertIsNone(overlay.get("/Game/Old.Old"))
        self.assertIsNotNone(overlay.get(BP))

    def test_clear_and_stats(self) -> None:
        overlay = LiveAssetOverlay()
        self.assertEqual(overlay.clear(), 0)
        overlay.install(record())
        stats = overlay.stats()
        self.assertEqual(stats["entryCount"], 1)
        self.assertEqual(stats["editorSessionIds"], [SESSION])
        self.assertEqual(overlay.clear(), 1)
        self.assertEqual(overlay.stats()["entryCount"], 0)

    def test_rejects_bad_max_entries(self) -> None:
        for bad in (0, -1, "2", None):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    LiveAssetOverlay(max_entries=bad)

    def test_install_rejects_record_without_asset_path(self) -> None:
        with self.assertRaises(ValueError):
            LiveAssetOverlay().install({"assetPath": ""})


if __name__ == "__main__":
    unittest.main()
