from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SNAPSHOT_SCHEMA_VERSION = "1.0"
SNAPSHOT_POINTER_NAME = "active-snapshot.json"
GENERATION_ID_PATTERN = re.compile(r"^gen_[0-9]{8}T[0-9]{6}Z_[0-9a-f]{12}$")


class SnapshotLifecycleError(RuntimeError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass(frozen=True)
class ActiveSnapshot:
    configured_database: Path
    configured_revision_export: Path
    database: Path
    revision_export: Path
    work_root: Path
    pointer_path: Path
    generation_id: str
    project_name: str
    legacy: bool


@dataclass(frozen=True)
class FrozenSessionSnapshot:
    session_id: str
    root: Path
    database: Path
    revision_export: Path
    active: ActiveSnapshot
    owns_copy: bool

    def cleanup(self) -> None:
        if self.owns_copy:
            shutil.rmtree(self.root, ignore_errors=True)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_generation_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"gen_{timestamp}_{uuid.uuid4().hex[:12]}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def configuration_key(database: Path, revision_export: Path, project_name: str) -> str:
    payload = {
        "database": str(database.expanduser().resolve()).replace("\\", "/").casefold(),
        "revisionExport": str(revision_export.expanduser().resolve()).replace("\\", "/").casefold(),
        "projectName": project_name,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _is_link_like(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(callable(is_junction) and is_junction())


def _assert_regular_tree(root: Path) -> None:
    if not root.is_dir() or _is_link_like(root):
        raise SnapshotLifecycleError("snapshot-path-invalid", "The Revision Export root is not a regular directory.")
    for path in root.rglob("*"):
        if _is_link_like(path):
            raise SnapshotLifecycleError("snapshot-path-invalid", "Snapshot trees cannot contain symbolic links or junction entries.")


def assert_quiescent_database(database: Path) -> None:
    if not database.is_file() or _is_link_like(database):
        raise SnapshotLifecycleError("snapshot-database-invalid", "The configured SQLite snapshot is not a regular file.")
    sidecars = [Path(str(database) + suffix) for suffix in ("-wal", "-shm", "-journal")]
    if any(path.exists() for path in sidecars):
        raise SnapshotLifecycleError(
            "snapshot-database-active",
            "The configured SQLite snapshot has an active sidecar and cannot be frozen or switched.",
        )


def clone_tree(source: Path, destination: Path, *, prefer_hardlinks: bool) -> None:
    source = source.expanduser().resolve()
    destination = destination.expanduser().resolve()
    _assert_regular_tree(source)
    if destination.exists():
        raise SnapshotLifecycleError("snapshot-destination-exists", "A generated snapshot destination already exists.")
    destination.mkdir(parents=True, exist_ok=False)
    try:
        for source_path in source.rglob("*"):
            relative = source_path.relative_to(source)
            destination_path = destination / relative
            if source_path.is_dir():
                destination_path.mkdir(parents=True, exist_ok=True)
                continue
            if not source_path.is_file():
                raise SnapshotLifecycleError("snapshot-path-invalid", "Snapshot trees may contain only regular files and directories.")
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            if prefer_hardlinks:
                try:
                    os.link(source_path, destination_path)
                    continue
                except OSError:
                    pass
            shutil.copy2(source_path, destination_path)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def snapshot_pointer_path(work_root: Path) -> Path:
    return work_root.expanduser().resolve() / SNAPSHOT_POINTER_NAME


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SnapshotLifecycleError("snapshot-pointer-invalid", "The active snapshot pointer is unreadable or invalid JSON.") from exc
    if not isinstance(value, dict):
        raise SnapshotLifecycleError("snapshot-pointer-invalid", "The active snapshot pointer must contain one JSON object.")
    return value


def resolve_active_snapshot(
    configured_database: Path,
    configured_revision_export: Path,
    work_root: Path,
    project_name: str,
) -> ActiveSnapshot:
    configured_database = configured_database.expanduser().resolve()
    configured_revision_export = configured_revision_export.expanduser().resolve()
    work_root = work_root.expanduser().resolve()
    pointer_path = snapshot_pointer_path(work_root)
    expected_key = configuration_key(configured_database, configured_revision_export, project_name)
    if not pointer_path.is_file():
        assert_quiescent_database(configured_database)
        _assert_regular_tree(configured_revision_export)
        return ActiveSnapshot(
            configured_database,
            configured_revision_export,
            configured_database,
            configured_revision_export,
            work_root,
            pointer_path,
            "legacy",
            project_name,
            True,
        )

    pointer = _read_json(pointer_path)
    if pointer.get("schemaVersion") != SNAPSHOT_SCHEMA_VERSION:
        raise SnapshotLifecycleError("snapshot-pointer-invalid", "The active snapshot pointer schema is unsupported.")
    if pointer.get("configurationKey") != expected_key or pointer.get("projectName") != project_name:
        raise SnapshotLifecycleError("snapshot-pointer-mismatch", "The active snapshot pointer does not match the fixed project and snapshot configuration.")
    generation_id = str(pointer.get("generationId", ""))
    if not GENERATION_ID_PATTERN.fullmatch(generation_id):
        raise SnapshotLifecycleError("snapshot-pointer-invalid", "The active snapshot generation ID is invalid.")
    generation_root = (work_root / "snapshots" / generation_id).resolve()
    snapshots_root = (work_root / "snapshots").resolve()
    if not _is_within(generation_root, snapshots_root):
        raise SnapshotLifecycleError("snapshot-pointer-invalid", "The active snapshot generation escaped the fixed snapshot root.")
    database = generation_root / "index.sqlite3"
    revision_export = generation_root / "revision-export"
    assert_quiescent_database(database)
    _assert_regular_tree(revision_export)
    expected_database_sha = str(pointer.get("databaseSha256", ""))
    if expected_database_sha and sha256_file(database) != expected_database_sha:
        raise SnapshotLifecycleError("snapshot-pointer-invalid", "The active snapshot database hash does not match its pointer.")
    expected_manifest_sha = str(pointer.get("revisionExportManifestSha256", ""))
    manifest_path = revision_export / "manifest.json"
    if not manifest_path.is_file() or (
        expected_manifest_sha and sha256_file(manifest_path) != expected_manifest_sha
    ):
        raise SnapshotLifecycleError("snapshot-pointer-invalid", "The active Revision Export manifest hash does not match its pointer.")
    return ActiveSnapshot(
        configured_database,
        configured_revision_export,
        database,
        revision_export,
        work_root,
        pointer_path,
        generation_id,
        project_name,
        False,
    )


def freeze_active_snapshot(active: ActiveSnapshot) -> FrozenSessionSnapshot:
    session_id = "session_" + uuid.uuid4().hex
    if not active.legacy:
        return FrozenSessionSnapshot(
            session_id=session_id,
            root=active.database.parent,
            database=active.database,
            revision_export=active.revision_export,
            active=active,
            owns_copy=False,
        )
    sessions_root = (active.work_root / "sessions").resolve()
    root = sessions_root / session_id
    staging = sessions_root / ("." + session_id + ".staging")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=False)
    try:
        assert_quiescent_database(active.database)
        database = staging / "index.sqlite3"
        shutil.copy2(active.database, database)
        if sha256_file(database) != sha256_file(active.database):
            raise SnapshotLifecycleError("snapshot-freeze-failed", "The frozen session database hash does not match the active snapshot.")
        clone_tree(active.revision_export, staging / "revision-export", prefer_hardlinks=False)
        os.replace(staging, root)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(root, ignore_errors=True)
        raise
    return FrozenSessionSnapshot(
        session_id=session_id,
        root=root,
        database=root / "index.sqlite3",
        revision_export=root / "revision-export",
        active=active,
        owns_copy=True,
    )


def write_active_pointer(
    active: ActiveSnapshot,
    *,
    generation_id: str,
    database_sha256: str,
    revision_export_manifest_sha256: str,
    refreshed_asset_path: str,
    refreshed_revision: str,
) -> Path:
    if not GENERATION_ID_PATTERN.fullmatch(generation_id):
        raise SnapshotLifecycleError("snapshot-generation-invalid", "The generated snapshot ID is invalid.")
    active.pointer_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schemaVersion": SNAPSHOT_SCHEMA_VERSION,
        "configurationKey": configuration_key(
            active.configured_database,
            active.configured_revision_export,
            active.project_name,
        ),
        "projectName": active.project_name,
        "generationId": generation_id,
        "databaseSha256": database_sha256,
        "revisionExportManifestSha256": revision_export_manifest_sha256,
        "refreshedAssetPath": refreshed_asset_path,
        "refreshedRevision": refreshed_revision,
        "createdUtc": utc_now_iso(),
    }
    temporary = active.pointer_path.with_name(active.pointer_path.name + ".tmp-" + uuid.uuid4().hex)
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, active.pointer_path)
    finally:
        temporary.unlink(missing_ok=True)
    return active.pointer_path
