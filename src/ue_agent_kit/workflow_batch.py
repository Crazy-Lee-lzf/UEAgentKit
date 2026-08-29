from __future__ import annotations

import json
import os
import secrets
import shutil
import uuid
from pathlib import Path
from typing import Any, Literal
from .workflow_common import (
    WORKFLOW_SCHEMA_VERSION,
    WorkflowError,
    _read_json,
)
from .database import (
    CURRENT_SCHEMA_VERSION,
    assert_fts5_available,
    get_metadata,
    get_schema_version,
    open_database,
    set_metadata,
)
from .indexer import (
    build_index,
)
from .snapshot_lifecycle import (
    SnapshotLifecycleError,
    assert_quiescent_database,
    clone_tree,
    new_generation_id,
    sha256_file,
    utc_now_iso,
    write_active_pointer,
)

def _write_json_atomic(*args: Any, **kwargs: Any) -> Any:
    from . import agent_workflow as _agent_workflow_compat
    return _agent_workflow_compat._write_json_atomic(*args, **kwargs)


class WorkflowBatchMixin:
    """D1 workflow split mixin/base; method bodies are pure moves from agent_workflow.py."""

    def _export_refresh_candidate(self, asset_path: str, output: Path, *, include_blueprint: bool = False) -> dict[str, Any]:
        if output.exists():
            shutil.rmtree(output)
        output.mkdir(parents=True, exist_ok=False)
        package_path = asset_path.split(".", 1)[0]
        if include_blueprint:
            export_script = "RunExport.ps1"
            export_arguments = [
                "-EngineRoot", str(self.config.engine_root),
                "-ProjectPath", str(self.config.project_path),
                "-Asset", package_path,
                "-Output", str(output),
                "-Profile", "full",
                "-Format", "json",
                "-IncludeUnchangedDefaults",
            ]
        else:
            export_script = "RunAssetCatalog.ps1"
            export_arguments = [
                "-EngineRoot", str(self.config.engine_root),
                "-ProjectPath", str(self.config.project_path),
                "-Asset", package_path,
                "-Output", str(output),
            ]
        result = self._run_script(
            export_script,
            export_arguments,
            stage="snapshot-refresh-export",
            report_path=output / "manifest.json",
        )
        if result.exit_code != 0:
            self._raise_process_failure(
                stage="snapshot-refresh-export",
                result=result,
                report_path=output / "manifest.json",
                fallback_code="snapshot-refresh-export-failed",
                fallback_message="The independent Unreal export for snapshot refresh failed.",
            )
        manifest = _read_json(output / "manifest.json", stage="snapshot-refresh-export")
        manifest_assets = manifest.get("assets", [])
        if (
            manifest.get("projectName") != self.project_name
            or int(manifest.get("assetCount", -1)) != 1
            or int(manifest.get("successCount", -1)) != 1
            or int(manifest.get("failureCount", -1)) != 0
            or not isinstance(manifest_assets, list)
            or len(manifest_assets) != 1
            or not isinstance(manifest_assets[0], dict)
            or not manifest_assets[0].get("success")
            or manifest_assets[0].get("assetPath") != asset_path
        ):
            raise WorkflowError(
                "snapshot-refresh-export-invalid",
                "The refresh Manifest must confirm exactly the requested asset in the fixed project with zero failures.",
            )
        canonical_files = list((output / "canonical").rglob("*.json"))
        if len(canonical_files) != 1:
            raise WorkflowError("snapshot-refresh-export-invalid", "The refresh export must contain exactly one Canonical asset.")
        canonical_path = canonical_files[0]
        canonical = _read_json(canonical_path, stage="snapshot-refresh-canonical")
        if canonical.get("projectName") != self.project_name or canonical.get("assetPath") != asset_path:
            raise WorkflowError("snapshot-refresh-export-invalid", "The refresh Canonical asset does not match the fixed project and requested asset.")
        revision = canonical.get("revision", {})
        if not isinstance(revision, dict):
            raise WorkflowError("snapshot-refresh-export-invalid", "The refresh Canonical asset has no Revision object.")
        revision_value = str(revision.get("value", ""))
        revision_digest = revision_value.removeprefix("sha256:")
        if (
            not revision.get("available")
            or revision.get("packageDirty")
            or not revision_value.startswith("sha256:")
            or len(revision_digest) != 64
            or any(character not in "0123456789abcdefABCDEF" for character in revision_digest)
        ):
            raise WorkflowError("snapshot-refresh-export-invalid", "The refresh Canonical asset has no clean SHA-256 Package Revision.")
        asset_class = str(canonical.get("assetClass", ""))
        package_name = str(canonical.get("packageName", ""))
        expected_package_name = asset_path.split(".", 1)[0]
        if not asset_class or package_name != expected_package_name:
            raise WorkflowError(
                "snapshot-refresh-export-invalid",
                "The refresh Canonical asset has no matching class and package identity.",
            )
        self._assert_refresh_policy(asset_path, asset_class)
        package_file = self._package_file(self.config.project_path, package_name, asset_class)
        disk_revision = "sha256:" + sha256_file(package_file)
        if revision_value != disk_revision:
            raise WorkflowError(
                "snapshot-refresh-revision-mismatch",
                "The staged Canonical Revision does not match the current disk Package SHA-256.",
                details={"canonicalRevision": revision_value, "diskRevision": disk_revision},
            )
        entry = dict(manifest_assets[0])
        manifest_json_path = Path(str(entry.get("jsonPath", "")))
        if not manifest_json_path.is_absolute():
            manifest_json_path = output / manifest_json_path
        if manifest_json_path.resolve() != canonical_path.resolve():
            raise WorkflowError(
                "snapshot-refresh-export-invalid",
                "The refresh Manifest Canonical path does not match the requested staged asset.",
            )
        bpctx_files = list((output / "bpctx").rglob("*.bpctx")) if (output / "bpctx").is_dir() else []
        bpctx_path = bpctx_files[0] if len(bpctx_files) == 1 else None
        return {
            "manifest": manifest,
            "manifestEntry": entry,
            "canonical": canonical,
            "canonicalPath": canonical_path,
            "bpctxPath": bpctx_path,
            "revision": revision_value,
            "assetClass": asset_class,
            "packageName": package_name,
            "diskFileSize": package_file.stat().st_size,
        }


    @staticmethod
    def _tree_size(root: Path) -> int:
        total = 0
        for path in root.rglob("*"):
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
        return total


    @staticmethod
    def _find_export_canonical(export_root: Path, asset_path: str) -> list[Path]:
        matches: list[Path] = []
        canonical_root = export_root / "canonical"
        if not canonical_root.is_dir():
            return matches
        for candidate in canonical_root.rglob("*.json"):
            try:
                value = json.loads(candidate.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(value, dict) and value.get("assetPath") == asset_path:
                matches.append(candidate)
        return matches


    def _replace_refresh_export_candidate(
        self,
        next_export: Path,
        candidate_root: Path,
        candidate: dict[str, Any],
    ) -> None:
        asset_path = str(candidate["canonical"].get("assetPath", ""))
        old_canonical = self._find_export_canonical(next_export, asset_path)
        for path in old_canonical:
            try:
                relative = path.relative_to(next_export / "canonical")
            except ValueError:
                relative = None
            path.unlink()
            if relative is not None:
                (next_export / "bpctx" / relative.with_suffix(".bpctx")).unlink(missing_ok=True)

        source_canonical = Path(candidate["canonicalPath"])
        relative = source_canonical.relative_to(candidate_root / "canonical")
        destination_canonical = next_export / "canonical" / relative
        destination_canonical.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_canonical, destination_canonical)
        destination_bpctx: Path | None = None
        if candidate.get("bpctxPath") is not None:
            source_bpctx = Path(candidate["bpctxPath"])
            bpctx_relative = source_bpctx.relative_to(candidate_root / "bpctx")
            destination_bpctx = next_export / "bpctx" / bpctx_relative
            destination_bpctx.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_bpctx, destination_bpctx)

        manifest_path = next_export / "manifest.json"
        manifest = _read_json(manifest_path) if manifest_path.is_file() else {}
        entries = [dict(item) for item in manifest.get("assets", []) if isinstance(item, dict)]
        replacement = dict(candidate["manifestEntry"])
        replacement["assetPath"] = asset_path
        replacement["success"] = True
        replacement["jsonPath"] = str(destination_canonical)
        if destination_bpctx is not None:
            replacement["bpctxPath"] = str(destination_bpctx)
        else:
            replacement.pop("bpctxPath", None)
        replaced = False
        for index, entry in enumerate(entries):
            if entry.get("assetPath") == asset_path:
                entries[index] = replacement
                replaced = True
                break
        if not replaced:
            entries.append(replacement)
        successful = [entry for entry in entries if entry.get("success")]
        manifest.update(
            {
                "projectName": self.project_name,
                "createdUtc": utc_now_iso(),
                "assetCount": len(entries),
                "successCount": len(successful),
                "failureCount": len(entries) - len(successful),
                "readerSuccessCount": len(successful),
                "readerFailureCount": len(entries) - len(successful),
                "assets": entries,
            }
        )
        _write_json_atomic(manifest_path, manifest)


    def _merge_refresh_export(
        self,
        active_export: Path,
        next_export: Path,
        candidate_root: Path,
        candidate: dict[str, Any],
    ) -> None:
        clone_tree(
            active_export,
            next_export,
            prefer_hardlinks=bool(self.active_snapshot and not self.active_snapshot.legacy),
        )
        self._replace_refresh_export_candidate(next_export, candidate_root, candidate)


    def _validate_next_database_assets(
        self,
        database: Path,
        expected_revisions: dict[str, str],
    ) -> dict[str, Any]:
        assert_quiescent_database(database)
        with open_database(database, readonly=True, migrate=False, immutable=True) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or str(integrity[0]).casefold() != "ok":
                raise WorkflowError("snapshot-refresh-database-invalid", "The next SQLite generation failed integrity_check.")
            if get_schema_version(connection) != CURRENT_SCHEMA_VERSION:
                raise WorkflowError("snapshot-refresh-database-invalid", "The next SQLite generation has the wrong schema version.")
            assert_fts5_available(connection)
            if get_metadata(connection, "project_key", "") != self.project_name:
                raise WorkflowError("snapshot-refresh-database-invalid", "The next SQLite generation has the wrong project identity.")
            for asset_path, expected_revision in expected_revisions.items():
                row = connection.execute(
                    "SELECT revision_value, package_dirty, canonical_relpath FROM assets WHERE asset_path = ?",
                    (asset_path,),
                ).fetchone()
                if row is None or str(row["revision_value"]) != expected_revision or bool(row["package_dirty"]):
                    raise WorkflowError(
                        "snapshot-refresh-database-invalid",
                        "The next SQLite generation does not contain every clean refreshed Revision.",
                        details={"assetPath": asset_path, "expectedRevision": expected_revision},
                    )
            asset_count = int(connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0])
        return {
            "assetCount": asset_count,
            "targetRevisions": [
                {"assetPath": asset_path, "revision": revision}
                for asset_path, revision in expected_revisions.items()
            ],
        }


    def _validate_next_database(self, database: Path, asset_path: str, expected_revision: str) -> dict[str, Any]:
        validation = self._validate_next_database_assets(database, {asset_path: expected_revision})
        return {"assetCount": validation["assetCount"], "targetRevision": expected_revision}


    def _build_snapshot_generation_batch(
        self,
        prepared_candidates: list[tuple[Path, dict[str, Any]]],
    ) -> dict[str, Any]:
        if self.active_snapshot is None:
            raise WorkflowError("snapshot-refresh-unavailable", "This workflow session was not started from a frozen active snapshot pair.")
        if not prepared_candidates:
            raise WorkflowError("snapshot-refresh-batch-empty", "At least one prepared refresh candidate is required.")
        active = self.active_snapshot
        expected_revisions: dict[str, str] = {}
        for _, candidate in prepared_candidates:
            asset_path = str(candidate.get("canonical", {}).get("assetPath", ""))
            revision = str(candidate.get("revision", ""))
            if not asset_path or asset_path in expected_revisions or not revision.startswith("sha256:"):
                raise WorkflowError("snapshot-refresh-batch-invalid", "Prepared refresh candidates must have unique exact assets and SHA-256 Revisions.")
            expected_revisions[asset_path] = revision

        generation_id = new_generation_id()
        snapshots_root = self._safe_work_path("snapshots")
        snapshots_root.mkdir(parents=True, exist_ok=True)
        staging = snapshots_root / ("." + generation_id + ".staging")
        final_root = snapshots_root / generation_id
        if staging.exists() or final_root.exists():
            raise WorkflowError("snapshot-refresh-generation-exists", "The generated snapshot ID already exists.")
        candidate_bytes = sum(self._tree_size(root) for root, _ in prepared_candidates)
        required_bytes = (
            self._tree_size(active.revision_export)
            + active.database.stat().st_size * 2
            + candidate_bytes
            + 64 * 1024 * 1024
        )
        free_bytes = shutil.disk_usage(snapshots_root).free
        if free_bytes < required_bytes:
            raise WorkflowError(
                "snapshot-refresh-disk-space",
                "There is not enough free disk space to build and validate the next snapshot generation.",
                details={"requiredBytes": required_bytes, "freeBytes": free_bytes},
            )
        staging.mkdir(parents=True, exist_ok=False)
        pointer_written = False
        try:
            next_export = staging / "revision-export"
            clone_tree(
                active.revision_export,
                next_export,
                prefer_hardlinks=bool(self.active_snapshot and not self.active_snapshot.legacy),
            )
            for candidate_root, candidate in prepared_candidates:
                self._replace_refresh_export_candidate(next_export, candidate_root, candidate)

            next_database = staging / "index.sqlite3"
            assert_quiescent_database(active.database)
            shutil.copy2(active.database, next_database)
            build_summaries: list[dict[str, Any]] = []
            with open_database(next_database) as connection:
                for candidate_root, candidate in prepared_candidates:
                    build_result = build_index(
                        connection,
                        candidate_root,
                        next_database,
                        force=True,
                        project_key=self.project_name,
                    )
                    if build_result.failed or build_result.errors or build_result.updated + build_result.added != 1:
                        raise WorkflowError(
                            "snapshot-refresh-index-build-failed",
                            "The next SQLite generation did not update exactly one requested asset for every Batch candidate.",
                            details={"build": build_result.to_dict(include_assets=False)},
                        )
                    build_summaries.append(build_result.to_dict(include_assets=False))
                set_metadata(connection, "last_export_root", str(final_root / "revision-export"))

            database_validation = self._validate_next_database_assets(next_database, expected_revisions)
            manifest_path = next_export / "manifest.json"
            manifest_sha = sha256_file(manifest_path)
            database_sha = sha256_file(next_database)
            os.replace(staging, final_root)
            refreshed_assets = [
                {"assetPath": asset_path, "revision": revision}
                for asset_path, revision in expected_revisions.items()
            ]
            first = refreshed_assets[0]
            write_active_pointer(
                active,
                generation_id=generation_id,
                database_sha256=database_sha,
                revision_export_manifest_sha256=manifest_sha,
                refreshed_asset_path=first["assetPath"],
                refreshed_revision=first["revision"],
                refreshed_assets=refreshed_assets,
            )
            pointer_written = True
            return {
                "generationId": generation_id,
                "databaseSha256": "sha256:" + database_sha,
                "revisionExportManifestSha256": "sha256:" + manifest_sha,
                "assetCount": database_validation["assetCount"],
                "refreshedAssetCount": len(refreshed_assets),
                "targetRevisions": refreshed_assets,
                "builds": build_summaries,
            }
        except SnapshotLifecycleError as exc:
            raise WorkflowError(exc.code, str(exc), details=exc.details) from exc
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            if final_root.exists() and not pointer_written:
                shutil.rmtree(final_root, ignore_errors=True)


    def _build_snapshot_generation(self, asset_path: str, candidate_root: Path, candidate: dict[str, Any]) -> dict[str, Any]:
        generation = self._build_snapshot_generation_batch([(candidate_root, candidate)])
        generation["targetRevision"] = candidate["revision"]
        return generation


    def prepare_batch_index_refresh_candidate(self, asset_path: str) -> dict[str, Any]:
        """Prepare one short-path, independently exported candidate for an atomic Batch snapshot refresh."""
        with self._lock:
            self._assert_session_current()
            asset_path = self._validate_refresh_asset_path(asset_path)
            self._assert_refresh_policy(asset_path)
            live_state = self._inspect_refresh_live_state(asset_path)
            candidate_id = "irc_" + secrets.token_urlsafe(10)
            candidate_root = self._safe_work_path("ir", candidate_id)
            try:
                candidate = self._export_refresh_candidate(asset_path, candidate_root)
                freshness = self.freshness.inspect_asset(asset_path)
                disk_revision = str(freshness.get("diskRevision", ""))
                if disk_revision and disk_revision != str(candidate.get("revision", "")):
                    raise WorkflowError(
                        "snapshot-refresh-revision-mismatch",
                        "The prepared Batch refresh candidate does not match the current disk Package Revision.",
                    )
                self._index_refresh_candidates[candidate_id] = (candidate_root, candidate)
                return {
                    "candidateId": candidate_id,
                    "assetPath": asset_path,
                    "assetClass": candidate["assetClass"],
                    "revision": candidate["revision"],
                    "diskFileSize": candidate["diskFileSize"],
                    "liveState": live_state,
                }
            except Exception:
                shutil.rmtree(candidate_root, ignore_errors=True)
                raise


    def discard_batch_index_refresh_candidates(self, candidate_ids: list[str]) -> None:
        with self._lock:
            for candidate_id in candidate_ids:
                prepared = self._index_refresh_candidates.pop(candidate_id, None)
                if prepared is not None:
                    shutil.rmtree(prepared[0], ignore_errors=True)


    def apply_batch_index_refresh(self, candidate_ids: list[str]) -> dict[str, Any]:
        """Atomically activate one paired snapshot generation containing every prepared Batch candidate."""
        with self._lock:
            self._assert_session_current()
            if not candidate_ids or len(candidate_ids) != len(set(candidate_ids)):
                raise WorkflowError("snapshot-refresh-batch-invalid", "Batch refresh candidate IDs must be non-empty and unique.")
            prepared_candidates: list[tuple[Path, dict[str, Any]]] = []
            for candidate_id in candidate_ids:
                prepared = self._index_refresh_candidates.get(candidate_id)
                if prepared is None:
                    raise WorkflowError("snapshot-refresh-batch-candidate-missing", "A prepared Batch refresh candidate is missing from this MCP session.")
                candidate_root, candidate = prepared
                package_file = self._package_file(
                    self.config.project_path,
                    str(candidate["packageName"]),
                    str(candidate["assetClass"]),
                )
                current_revision = "sha256:" + sha256_file(package_file)
                if current_revision != str(candidate["revision"]):
                    raise WorkflowError(
                        "snapshot-refresh-revision-mismatch",
                        "A Batch refresh target changed on disk after candidate preparation.",
                        details={"assetPath": candidate["canonical"].get("assetPath", "")},
                    )
                prepared_candidates.append((candidate_root, candidate))

            generation = self._build_snapshot_generation_batch(prepared_candidates)
            for candidate_id in candidate_ids:
                prepared = self._index_refresh_candidates.pop(candidate_id, None)
                if prepared is not None:
                    shutil.rmtree(prepared[0], ignore_errors=True)
            invalidated = {
                "planCount": len(self._plans),
                "dryRunReceiptCount": len(self._dry_runs),
                "applyReceiptCount": len(self._applies),
                "rollbackReceiptCount": len(self._rollback_dry_runs),
            }
            self._plans.clear()
            self._dry_runs.clear()
            self._applies.clear()
            self._rollback_dry_runs.clear()
            self._refresh_applied = True
            return {
                "applied": True,
                "activeSnapshotChanged": True,
                "newGeneration": generation,
                "invalidated": invalidated,
                "currentSessionUsesPreviousSnapshot": True,
                "restartRequired": True,
            }


    def get_asset_state(self, asset_path: str) -> dict[str, Any]:
        """Combine Editor memory with the frozen SQLite, Revision Export, and current disk Package state."""
        with self._lock:
            try:
                asset_path = self._validate_refresh_asset_path(asset_path)
            except WorkflowError as exc:
                raise WorkflowError("asset-state-invalid-asset", "asset_path must be one exact /Game Object Path.") from exc

            record = self.index_service.get_revision_record(asset_path)
            freshness = self.freshness.inspect_asset(asset_path)
            index_revision = str(freshness.get("indexRevision", ""))
            export_revision = str(freshness.get("revisionExportRevision", ""))
            disk_revision = str(freshness.get("diskRevision", ""))
            reasons = [item for item in str(freshness.get("reason", "")).split(",") if item]

            sqlite_state = {
                "state": "available" if record is not None and index_revision else "missing",
                "revision": index_revision,
                "packageName": str(record.get("package_name", "")) if record else "",
                "assetClass": str(record.get("asset_class", "")) if record else "",
                "packageDirty": bool(record.get("package_dirty")) if record else None,
                "snapshotGenerationId": self.active_snapshot.generation_id if self.active_snapshot is not None else "",
            }
            revision_export_state = {
                "state": "available" if export_revision else (
                    "missing" if "revision-export-missing" in reasons else "unavailable"
                ),
                "revision": export_revision,
                "packageDirty": "revision-export-package-dirty" in reasons,
            }
            disk_state = {
                "state": "available" if disk_revision else (
                    "missing" if "package-file-missing" in reasons else "unavailable"
                ),
                "revision": disk_revision,
                "revisionAlgorithm": "sha256" if disk_revision else "",
            }

            memory_state: dict[str, Any] = {
                "configured": self.live_editor_service is not None,
                "state": "unavailable",
                "loaded": None,
                "packageDirty": None,
                "openInAssetEditor": None,
                "selected": None,
                "revisionAvailable": False,
                "reasonCode": "live-editor-disabled" if self.live_editor_service is None else "live-editor-unavailable",
            }
            if self.live_editor_service is not None:
                try:
                    live_status = self.live_editor_service.status()
                    if live_status.get("state") == "available":
                        payload = self.live_editor_service.call_tool(
                            "ue_inspect_asset_live",
                            {"assetPath": asset_path},
                        )
                        result = payload.get("result", {}) if isinstance(payload, dict) else {}
                        memory = result.get("memory", {}) if isinstance(result, dict) else {}
                        registry = result.get("assetRegistry", {}) if isinstance(result, dict) else {}
                        if isinstance(memory, dict):
                            memory_state.update(
                                {
                                    "state": str(memory.get("state", "unknown")),
                                    "loaded": bool(memory.get("loaded")),
                                    "packageDirty": bool(memory.get("packageDirty")),
                                    "openInAssetEditor": bool(memory.get("openInAssetEditor")),
                                    "selected": bool(memory.get("selected")),
                                    "loadedByBridge": bool(memory.get("loadedByBridge")),
                                    "registryFound": bool(registry.get("found")) if isinstance(registry, dict) else None,
                                    "reasonCode": "",
                                }
                            )
                    else:
                        memory_state["reasonCode"] = str(live_status.get("reasonCode", "live-editor-unavailable"))
                except Exception:
                    memory_state["reasonCode"] = "live-editor-status-unavailable"

            memory_dirty = memory_state.get("packageDirty") is True
            if memory_dirty:
                state = "memory-dirty"
                recommended_action = "save-or-revert-memory"
            elif freshness.get("state") == "fresh":
                state = "synchronized"
                recommended_action = "none"
            elif freshness.get("state") == "unavailable":
                state = "incomplete"
                recommended_action = "restore-missing-source"
            elif index_revision == export_revision and disk_revision and disk_revision != index_revision:
                state = "disk-newer-than-snapshots"
                recommended_action = "refresh-asset-index"
            elif disk_revision == export_revision and index_revision != disk_revision:
                state = "sqlite-outdated"
                recommended_action = "refresh-asset-index"
            elif disk_revision == index_revision and export_revision != disk_revision:
                state = "revision-export-outdated"
                recommended_action = "refresh-asset-index"
            else:
                state = "persistent-sources-diverged"
                recommended_action = "inspect-and-refresh"

            return {
                "schemaVersion": WORKFLOW_SCHEMA_VERSION,
                "tool": "ue_get_asset_state",
                "ok": True,
                "readOnly": True,
                "assetPath": asset_path,
                "state": state,
                "sources": {
                    "memory": memory_state,
                    "disk": disk_state,
                    "revisionExport": revision_export_state,
                    "sqlite": sqlite_state,
                },
                "comparisons": dict(freshness.get("comparisons", {})),
                "freshness": freshness,
                "saveRequired": memory_dirty,
                "indexRefreshRequired": freshness.get("state") == "stale",
                "refreshBlockedByDirtyMemory": memory_dirty,
                "currentSessionUsesFrozenSnapshot": self.active_snapshot is not None,
                "restartRequired": self._refresh_applied,
                "recommendedAction": recommended_action,
                "limitations": {
                    "memoryRevisionAvailable": False,
                    "memoryCleanMeans": "The loaded package is not Dirty; it is not a cryptographic equality proof against disk.",
                },
            }


    def refresh_asset_index(self, asset_path: str, *, mode: Literal["Preview", "Apply"] = "Preview") -> dict[str, Any]:
        with self._lock:
            self._assert_session_current()
            asset_path = self._validate_refresh_asset_path(asset_path)
            if mode not in {"Preview", "Apply"}:
                raise WorkflowError("snapshot-refresh-invalid-mode", "mode must be Preview or Apply.")
            if self.active_snapshot is None:
                raise WorkflowError("snapshot-refresh-unavailable", "Snapshot refresh is unavailable because this session has no frozen active snapshot pair.")
            self._assert_refresh_policy(asset_path)
            live_state = self._inspect_refresh_live_state(asset_path)
            current_record = self.index_service.get_revision_record(asset_path)
            asset_class = str((current_record or {}).get("asset_class", ""))
            operation_root = self._safe_work_path("refresh", uuid.uuid4().hex)
            candidate_root = operation_root / "candidate"
            try:
                candidate = self._export_refresh_candidate(
                    asset_path,
                    candidate_root,
                    include_blueprint=("Blueprint" in asset_class),
                )
                action = "add" if current_record is None else "update"
                base = {
                    "schemaVersion": WORKFLOW_SCHEMA_VERSION,
                    "tool": "ue_refresh_asset_index",
                    "ok": True,
                    "mode": mode,
                    "assetPath": asset_path,
                    "assetClass": candidate["assetClass"],
                    "action": action,
                    "currentSessionGenerationId": self.active_snapshot.generation_id,
                    "targetRevision": candidate["revision"],
                    "diskFileSize": candidate["diskFileSize"],
                    "liveState": live_state,
                    "currentSessionUsesFrozenSnapshot": True,
                }
                if mode == "Preview":
                    base.update(
                        {
                            "applied": False,
                            "activeSnapshotChanged": False,
                            "restartRequired": False,
                            "wouldInvalidate": {
                                "planCount": len(self._plans),
                                "dryRunReceiptCount": len(self._dry_runs),
                                "applyReceiptCount": len(self._applies),
                                "rollbackReceiptCount": len(self._rollback_dry_runs),
                            },
                            "nextStep": "Review the target Revision, then call ue_refresh_asset_index with mode=Apply. No active snapshot changed.",
                        }
                    )
                    return base
                invalidated = {
                    "planCount": len(self._plans),
                    "dryRunReceiptCount": len(self._dry_runs),
                    "applyReceiptCount": len(self._applies),
                    "rollbackReceiptCount": len(self._rollback_dry_runs),
                }
                generation = self._build_snapshot_generation(asset_path, candidate_root, candidate)
                self._plans.clear()
                self._dry_runs.clear()
                self._applies.clear()
                self._rollback_dry_runs.clear()
                self._refresh_applied = True
                base.update(
                    {
                        "applied": True,
                        "activeSnapshotChanged": True,
                        "newGeneration": generation,
                        "invalidated": invalidated,
                        "currentSessionUsesPreviousSnapshot": True,
                        "restartRequired": True,
                        "nextStep": "Restart the MCP server. The new session will freeze and validate the new paired SQLite and Revision Export generation.",
                    }
                )
                return base
            finally:
                shutil.rmtree(operation_root, ignore_errors=True)


    def prepare_asset_for_disk_rollback(self, asset_path: str) -> dict[str, Any]:
        """Close/unload one exact clean resident asset, then prove the W3 rollback precondition."""
        descriptor = self.config.project_path.parent / "Saved" / "UEAgentKit" / "EditorBridge.json"
        if self.live_editor_service is None:
            return {"state": "offline", "assetPath": asset_path, "prepared": False}
        try:
            status = self.live_editor_service.status()
        except Exception as exc:
            raise WorkflowError(
                "rollback-live-editor-status-unavailable",
                "Live Editor state could not be checked before rollback preparation.",
            ) from exc
        if not isinstance(status, dict) or status.get("state") != "available":
            if descriptor.is_file():
                raise WorkflowError(
                    "rollback-live-editor-status-unavailable",
                    "The fixed Editor Bridge descriptor exists but rollback preparation cannot prove the target state.",
                )
            return {"state": "offline", "assetPath": asset_path, "prepared": False}
        try:
            prepared = self.live_editor_service.prepare_asset_for_disk_rollback(asset_path)
        except Exception as exc:
            raise WorkflowError(
                "rollback-live-editor-prepare-failed",
                "The fixed Editor session could not safely close and unload the rollback target.",
                details={"assetPath": asset_path, "cause": getattr(exc, "code", exc.__class__.__name__)},
            ) from exc
        if (
            not isinstance(prepared, dict)
            or prepared.get("readyForDiskRollback") is not True
            or prepared.get("loadedAfter") is not False
            or prepared.get("openAfter") is not False
            or prepared.get("packageDirtyAfter") is not False
        ):
            raise WorkflowError(
                "rollback-live-editor-prepare-invalid",
                "Rollback preparation did not prove the exact target is unloaded, closed, and clean.",
                details={"assetPath": asset_path},
            )
        verified = self._inspect_rollback_live_state(asset_path)
        return {
            "state": "prepared",
            "assetPath": asset_path,
            "prepared": True,
            "editorSessionId": verified.get("editorSessionId", ""),
            "editorProcessId": verified.get("editorProcessId", 0),
            "bridgeResult": prepared,
        }


    def bind_asset_for_batch(self, asset_path: str) -> dict[str, Any]:
        """Bind one exact indexed asset Class and SHA-256 Revision for W4 Batch planning."""
        with self._lock:
            self._assert_policy_unchanged()
            asset_path = self._validate_refresh_asset_path(asset_path)
            asset_result = self.index_service.get_asset(
                asset_path,
                symbol_limit=1,
                reference_limit=1,
                graph_limit=1,
                node_limit=1,
            )
            if not asset_result.get("found") or not isinstance(asset_result.get("asset"), dict):
                raise WorkflowError("asset-not-indexed", "The requested asset is not present in the fixed SQLite index.")
            asset = asset_result["asset"]
            self._assert_asset_fresh(asset_path)
            revision = asset.get("revision_value")
            asset_class = asset.get("asset_class")
            if not isinstance(revision, str) or not revision.startswith("sha256:"):
                raise WorkflowError("revision-unavailable", "The indexed asset has no usable SHA-256 Revision.")
            if not isinstance(asset_class, str) or not asset_class:
                raise WorkflowError("asset-class-unavailable", "The indexed asset has no usable Asset Class.")
            return {
                "assetPath": asset_path,
                "assetClass": asset_class,
                "expectedRevision": revision,
            }


    def assert_plan_available_for_batch(self, plan_id: str) -> None:
        """Fail closed unless an existing child Plan is present and unconsumed."""
        with self._lock:
            if not isinstance(plan_id, str) or not plan_id.startswith("plan_"):
                raise WorkflowError("plan-not-found", "The child Plan identity is invalid.")
            record = self._plans.get(plan_id)
            if record is None:
                raise WorkflowError("plan-not-found", "The child Plan is not active in this MCP session.")
            if record.consumed:
                raise WorkflowError("plan-consumed", "The child Plan has already been consumed.")
