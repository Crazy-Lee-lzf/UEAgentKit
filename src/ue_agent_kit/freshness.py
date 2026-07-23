from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FRESHNESS_SCHEMA_VERSION = "1.0"
MAX_FRESHNESS_SAMPLES = 20


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _sha256_revision(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _is_sha256_revision(value: str) -> bool:
    if not value.startswith("sha256:") or len(value) != 71:
        return False
    return all(character in "0123456789abcdefABCDEF" for character in value[7:])


def _safe_relative(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _package_candidates(project_path: Path, record: dict[str, Any]) -> tuple[list[Path], str]:
    package_name = str(record.get("package_name", ""))
    if not package_name.startswith("/Game/"):
        return [], "unsupported-mount"
    relative_text = package_name[len("/Game/") :]
    relative = Path(*[part for part in relative_text.split("/") if part])
    if not relative.parts or any(part in {".", ".."} for part in relative.parts):
        return [], "invalid-package-name"
    content_root = (project_path.parent / "Content").resolve()
    base = content_root / relative
    preferred = ".umap" if str(record.get("asset_class", "")) == "/Script/Engine.World" else ".uasset"
    suffixes = (preferred, ".uasset" if preferred == ".umap" else ".umap")
    candidates = [base.with_suffix(suffix) for suffix in suffixes]
    if any(not _safe_relative(candidate, content_root) for candidate in candidates):
        return [], "invalid-package-name"
    return candidates, ""


def _resolve_canonical_path(export_root: Path, record: dict[str, Any]) -> Path | None:
    recorded = str(record.get("canonical_relpath", "")).strip()
    if recorded:
        raw = Path(recorded)
        direct = raw if raw.is_absolute() else export_root / raw
        if direct.is_file() and _safe_relative(direct, export_root):
            return direct.resolve()
        normalized_parts = [part for part in raw.parts if part not in {raw.anchor, "\\", "/"}]
        lowered = [part.casefold() for part in normalized_parts]
        if "canonical" in lowered:
            index = lowered.index("canonical")
            relocated = export_root.joinpath(*normalized_parts[index:])
            if relocated.is_file() and _safe_relative(relocated, export_root):
                return relocated.resolve()
    return None


def _read_canonical_revision(path: Path) -> tuple[str, bool]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        return "", False
    revision = value.get("revision", {})
    if not isinstance(revision, dict):
        return "", False
    return str(revision.get("value", "")), bool(revision.get("packageDirty"))


class IndexFreshnessTracker:
    """Compare immutable SQLite revisions with Revision Export and fixed-project package files."""

    def __init__(self, index_service: Any, project_path: Path, revision_export: Path) -> None:
        self.index_service = index_service
        self.project_path = project_path.expanduser().resolve()
        self.revision_export = revision_export.expanduser().resolve()
        self._lock = threading.RLock()
        self._file_revision_cache: dict[Path, tuple[int, int, str]] = {}
        self._canonical_revision_cache: dict[Path, tuple[int, int, str, bool]] = {}
        self._canonical_asset_cache: dict[str, Path | None] = {}
        self._session_stale: dict[str, dict[str, Any]] = {}

    def _records(self) -> list[dict[str, Any]]:
        records = self.index_service.get_revision_records()
        return [dict(record) for record in records]

    def _record(self, asset_path: str) -> dict[str, Any] | None:
        record = self.index_service.get_revision_record(asset_path)
        return dict(record) if record is not None else None

    def _cached_file_revision(self, path: Path) -> str:
        stat = path.stat()
        cached = self._file_revision_cache.get(path)
        if cached is not None and cached[:2] == (stat.st_size, stat.st_mtime_ns):
            return cached[2]
        revision = _sha256_revision(path)
        self._file_revision_cache[path] = (stat.st_size, stat.st_mtime_ns, revision)
        return revision

    def _find_canonical_for_asset(self, record: dict[str, Any]) -> Path | None:
        asset_path = str(record.get("asset_path", ""))
        direct = _resolve_canonical_path(self.revision_export, record)
        if direct is not None:
            self._canonical_asset_cache[asset_path] = direct
            return direct
        if asset_path in self._canonical_asset_cache:
            return self._canonical_asset_cache[asset_path]
        canonical_root = self.revision_export / "canonical"
        if canonical_root.is_dir():
            for candidate in canonical_root.rglob("*.json"):
                try:
                    value = json.loads(candidate.read_text(encoding="utf-8-sig"))
                except (OSError, json.JSONDecodeError):
                    continue
                if isinstance(value, dict) and str(value.get("assetPath", "")) == asset_path:
                    resolved = candidate.resolve()
                    self._canonical_asset_cache[asset_path] = resolved
                    return resolved
        self._canonical_asset_cache[asset_path] = None
        return None

    def _cached_canonical_revision(self, path: Path) -> tuple[str, bool]:
        stat = path.stat()
        cached = self._canonical_revision_cache.get(path)
        if cached is not None and cached[:2] == (stat.st_size, stat.st_mtime_ns):
            return cached[2], cached[3]
        revision, package_dirty = _read_canonical_revision(path)
        self._canonical_revision_cache[path] = (stat.st_size, stat.st_mtime_ns, revision, package_dirty)
        return revision, package_dirty

    def inspect_asset(self, asset_path: str) -> dict[str, Any]:
        with self._lock:
            record = self._record(asset_path)
            if record is None:
                return {
                    "schemaVersion": FRESHNESS_SCHEMA_VERSION,
                    "assetPath": asset_path,
                    "state": "unavailable",
                    "reason": "asset-not-indexed",
                    "indexFresh": None,
                    "indexStale": None,
                    "comparedAtUtc": _utc_now_iso(),
                }
            return self._inspect_record(record)

    def _inspect_record(self, record: dict[str, Any]) -> dict[str, Any]:
        asset_path = str(record.get("asset_path", ""))
        index_revision = str(record.get("revision_value", ""))
        reasons: list[str] = []
        export_revision = ""
        disk_revision = ""

        if not _is_sha256_revision(index_revision):
            reasons.append("index-revision-unavailable")
        if bool(record.get("package_dirty")):
            reasons.append("index-package-dirty")

        canonical_path = self._find_canonical_for_asset(record)
        if canonical_path is None:
            reasons.append("revision-export-missing")
        else:
            try:
                export_revision, export_dirty = self._cached_canonical_revision(canonical_path)
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                reasons.append("revision-export-invalid")
            else:
                if not _is_sha256_revision(export_revision):
                    reasons.append("revision-export-revision-unavailable")
                if export_dirty:
                    reasons.append("revision-export-package-dirty")

        candidates, candidate_error = _package_candidates(self.project_path, record)
        if candidate_error:
            reasons.append(candidate_error)
        else:
            package_path = next((candidate for candidate in candidates if candidate.is_file()), None)
            if package_path is None:
                reasons.append("package-file-missing")
            else:
                try:
                    disk_revision = self._cached_file_revision(package_path)
                except OSError:
                    reasons.append("package-file-unreadable")

        available = all(
            _is_sha256_revision(value)
            for value in (index_revision, export_revision, disk_revision)
        ) and not any(reason.endswith("dirty") for reason in reasons)
        if not available:
            state = "unavailable"
        elif index_revision == export_revision == disk_revision:
            state = "fresh"
        else:
            state = "stale"
            if index_revision != export_revision:
                reasons.append("index-revision-export-mismatch")
            if index_revision != disk_revision:
                reasons.append("index-disk-mismatch")
            if export_revision != disk_revision:
                reasons.append("revision-export-disk-mismatch")

        session = self._session_stale.get(asset_path)
        if session is not None:
            state = "stale"
            reasons.append("session-commit-stale")

        unique_reasons = list(dict.fromkeys(reasons))
        result = {
            "schemaVersion": FRESHNESS_SCHEMA_VERSION,
            "assetPath": asset_path,
            "state": state,
            "reason": ",".join(unique_reasons),
            "indexFresh": state == "fresh" if state != "unavailable" else None,
            "indexStale": state == "stale" if state != "unavailable" else None,
            "indexRevision": index_revision,
            "revisionExportRevision": export_revision,
            "diskRevision": disk_revision,
            "comparisons": {
                "indexMatchesRevisionExport": index_revision == export_revision if export_revision else None,
                "indexMatchesDisk": index_revision == disk_revision if disk_revision else None,
                "revisionExportMatchesDisk": export_revision == disk_revision if export_revision and disk_revision else None,
            },
            "comparedAtUtc": _utc_now_iso(),
        }
        if session is not None:
            result["sessionTransition"] = dict(session)
        return result

    def project_status(self) -> dict[str, Any]:
        with self._lock:
            records = self._records()
            results = [self._inspect_record(record) for record in records]
            fresh = [result for result in results if result["state"] == "fresh"]
            stale = [result for result in results if result["state"] == "stale"]
            unavailable = [result for result in results if result["state"] == "unavailable"]
            if not results:
                state = "unavailable"
            elif stale:
                state = "stale"
            elif unavailable and not fresh:
                state = "unavailable"
            elif unavailable:
                state = "partial"
            else:
                state = "fresh"
            reason = ""
            if stale:
                reason = "One or more indexed package Revisions differ from Revision Export or disk."
            elif unavailable:
                reason = "Some indexed packages could not be compared across all three Revision sources."
            return {
                "schemaVersion": FRESHNESS_SCHEMA_VERSION,
                "state": state,
                "indexFresh": state == "fresh" if state != "unavailable" else None,
                "indexStale": bool(stale),
                "reason": reason,
                "comparisonMode": "sqlite-revision-export-disk-sha256",
                "comparedAssetCount": len(results),
                "freshAssetCount": len(fresh),
                "staleAssetCount": len(stale),
                "unavailableAssetCount": len(unavailable),
                "complete": True,
                "staleAssets": [self._summary(result) for result in stale[:MAX_FRESHNESS_SAMPLES]],
                "unavailableAssets": [self._summary(result) for result in unavailable[:MAX_FRESHNESS_SAMPLES]],
                "sessionStaleAssets": [dict(value) for value in self._session_stale.values()],
                "comparedAtUtc": _utc_now_iso(),
            }

    @staticmethod
    def _summary(result: dict[str, Any]) -> dict[str, Any]:
        return {
            "assetPath": result.get("assetPath", ""),
            "state": result.get("state", ""),
            "reason": result.get("reason", ""),
            "indexRevision": result.get("indexRevision", ""),
            "revisionExportRevision": result.get("revisionExportRevision", ""),
            "diskRevision": result.get("diskRevision", ""),
        }

    def mark_commit(self, asset_path: str, before_revision: str, after_revision: str) -> dict[str, Any]:
        with self._lock:
            transition = {
                "assetPath": asset_path,
                "state": "stale",
                "reason": "commit-changed-package",
                "beforeRevision": before_revision,
                "afterRevision": after_revision,
                "changedAtUtc": _utc_now_iso(),
            }
            self._session_stale[asset_path] = transition
            self._file_revision_cache.clear()
            return self.inspect_asset(asset_path)

    def mark_rollback(self, asset_path: str, restored_revision: str) -> dict[str, Any]:
        with self._lock:
            self._file_revision_cache.clear()
            previous = self._session_stale.pop(asset_path, None)
            result = self.inspect_asset(asset_path)
            if result.get("state") == "fresh" and result.get("diskRevision") == restored_revision:
                result.pop("sessionTransition", None)
                result["sessionStateCleared"] = True
            else:
                transition = previous or {
                    "assetPath": asset_path,
                    "state": "stale",
                    "reason": "rollback-did-not-restore-index-revision",
                    "changedAtUtc": _utc_now_iso(),
                }
                transition["restoredRevision"] = restored_revision
                self._session_stale[asset_path] = transition
                result["state"] = "stale"
                result["indexFresh"] = False
                result["indexStale"] = True
                result["sessionTransition"] = dict(transition)
                result["sessionStateCleared"] = False
            return result

    def session_stale_assets(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(value) for value in self._session_stale.values()]
