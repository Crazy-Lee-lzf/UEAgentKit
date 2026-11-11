"""0-3.3 slice 1: session-scoped canonical overlay for live-refreshed assets.

This is the storage-and-proof foundation for in-process index refresh.  It answers
one question: given what the Editor Bridge just reported about a saved asset, is
that report strong enough to be treated as fresher than the frozen index?

It is deliberately **not** wired into the offline read paths yet.  See
``docs/INDEX_LIVE_HOT_UPDATE_DESIGN_20261105.md`` for the remaining work (read-path
audit, non-Blueprint canonical readers, user-save detection).  Until that wiring
lands, this module cannot change what any query returns.

Design constraints it preserves:

* the immutable SQLite index is never written -- the overlay lives in memory for
  the life of one MCP session;
* a bad record can only ever affect the single asset it names;
* nothing here sets ``snapshot-refresh-restart-required``; refreshing one asset
  must never cost an Editor restart.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

OVERLAY_SCHEMA_VERSION = "ueak.liveassetoverlay/1.0"
MAX_OVERLAY_ENTRIES = 64
BLUEPRINT_CLASS = "/Script/Engine.Blueprint"


class LiveOverlayError(ValueError):
    """A refused overlay operation, carrying a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def package_file_for_object_path(project_path: Path, asset_path: str) -> Path:
    """Map one exact /Game Object Path to its on-disk ``.uasset`` file.

    Only plain single-file packages are supported.  World Partition external
    actors and other multi-file packages are refused rather than guessed at.
    """
    if not isinstance(asset_path, str) or not asset_path.startswith("/Game/"):
        raise LiveOverlayError(
            "live-index-refresh-invalid-asset",
            "asset_path must be one exact /Game Object Path.",
        )
    if "." not in asset_path or "\\" in asset_path or ":" in asset_path:
        raise LiveOverlayError(
            "live-index-refresh-invalid-asset",
            "asset_path must be a Package.Object path with no separators or drive prefix.",
        )
    package, _separator, _object = asset_path.partition(".")
    relative = package[len("/Game/") :]
    parts = relative.split("/")
    if not relative or any(part in ("", ".", "..") for part in parts):
        raise LiveOverlayError(
            "live-index-refresh-invalid-asset",
            "asset_path must not contain empty, '.' or '..' segments.",
        )
    return project_path.resolve().parent / "Content" / Path(*parts).with_suffix(".uasset")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_overlay_record(
    *,
    asset_path: str,
    asset_class: str,
    expected_class: str,
    bridge_canonical: dict[str, Any],
    disk_sha256: str,
    editor_session_id: str,
    captured_at_utc: str,
) -> dict[str, Any]:
    """Prove one bridge report and turn it into an overlay record.

    Every refusal is a precise code; nothing is silently accepted.
    """
    if bridge_canonical.get("available") is not True:
        raise LiveOverlayError(
            "live-index-refresh-unavailable",
            "The Bridge did not report an available canonical record for this asset.",
        )
    if bridge_canonical.get("readOnly") is not True:
        raise LiveOverlayError(
            "live-index-refresh-not-readonly",
            "The Bridge did not mark the canonical record as read-only.",
        )
    if bridge_canonical.get("packageDirty") is not False:
        raise LiveOverlayError(
            "live-index-refresh-package-dirty",
            "The package is dirty; save it before refreshing the index so the "
            "overlay cannot describe unsaved memory as disk state.",
        )
    if asset_class != expected_class:
        raise LiveOverlayError(
            "live-index-refresh-identity-drift",
            f"The Bridge reported class {asset_class!r} but the index records "
            f"{expected_class!r}.",
        )
    context = bridge_canonical.get("context")
    if not isinstance(context, dict):
        raise LiveOverlayError(
            "live-index-refresh-no-canonical",
            "The Bridge report has no canonical context object.",
        )
    revision = context.get("revision")
    bridge_revision = ""
    if isinstance(revision, dict):
        bridge_revision = str(revision.get("value", ""))
    if not disk_sha256 or bridge_revision != f"sha256:{disk_sha256}":
        # The whole point of the overlay is that it describes what is on disk.
        # If the two readings disagree we cannot tell which one is stale.
        raise LiveOverlayError(
            "live-index-refresh-stale-proof",
            "The Bridge revision and the on-disk SHA-256 disagree; refusing to "
            "install an overlay record that cannot be proven fresh.",
        )
    return {
        "schemaVersion": OVERLAY_SCHEMA_VERSION,
        "assetPath": asset_path,
        "assetClass": asset_class,
        "revision": f"sha256:{disk_sha256}",
        "canonical": context,
        "editorSessionId": editor_session_id,
        "capturedAtUtc": captured_at_utc,
        "source": "live-editor-canonical",
        "indexFresh": False,
        "overlayUpdated": True,
        # Refreshing one asset must never require an Editor or MCP restart.
        "restartRequired": False,
    }


class LiveAssetOverlay:
    """One MCP session's overlay, bounded and dropped on generation change."""

    def __init__(self, max_entries: int = MAX_OVERLAY_ENTRIES) -> None:
        if not isinstance(max_entries, int) or max_entries < 1:
            raise ValueError("max_entries must be a positive integer.")
        self._max_entries = max_entries
        self._records: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []

    def install(self, record: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(record, dict):
            raise ValueError("record must be a dict.")
        asset_path = record.get("assetPath")
        if not isinstance(asset_path, str) or not asset_path:
            raise ValueError("record must carry a non-empty assetPath.")
        if asset_path in self._records:
            self._order.remove(asset_path)
        self._records[asset_path] = dict(record)
        self._order.append(asset_path)
        while len(self._order) > self._max_entries:
            self._records.pop(self._order.pop(0), None)
        return self._records[asset_path]

    def get(self, asset_path: str) -> dict[str, Any] | None:
        record = self._records.get(asset_path)
        return dict(record) if record is not None else None

    def drop_session(self, editor_session_id: str) -> int:
        """Drop every record captured under a different Editor session."""
        stale = [
            path
            for path, record in self._records.items()
            if str(record.get("editorSessionId", "")) != editor_session_id
        ]
        for path in stale:
            self._records.pop(path, None)
            if path in self._order:
                self._order.remove(path)
        return len(stale)

    def clear(self) -> int:
        count = len(self._records)
        self._records.clear()
        self._order.clear()
        return count

    def stats(self) -> dict[str, Any]:
        sessions = sorted(
            {str(record.get("editorSessionId", "")) for record in self._records.values()}
        )
        return {
            "schemaVersion": OVERLAY_SCHEMA_VERSION,
            "entryCount": len(self._records),
            "maxEntries": self._max_entries,
            "editorSessionIds": sessions,
            "assetPaths": list(self._order),
        }
