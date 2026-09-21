from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .code_symbols import extract_code_file_facts, rebuild_code_semantics
from .database import set_metadata, utc_now_iso


CODE_PROFILE = "code"
CPP_SOURCE_ASSET_CLASS = "CppSourceFile"
CODE_INDEX_SCHEMA_VERSION = "code-index-1.0"
CODE_INDEX_EXPORTER_VERSION = "ue-agent-kit-code-index-v1"
DEFAULT_SOURCE_ROOTS = ("Source",)
CPP_SOURCE_EXTENSIONS = frozenset({".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".inl"})


@dataclass
class CodeFileIndexResult:
    asset_path: str
    status: str
    reason: str = ""


@dataclass
class CodeIndexResult:
    project_root: str
    database: str
    source_roots: list[str]
    added: int = 0
    updated: int = 0
    skipped: int = 0
    deleted: int = 0
    failed: int = 0
    symbols: int = 0
    references: int = 0
    files: list[CodeFileIndexResult] = field(default_factory=list)
    semantic_warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self, *, include_files: bool = True) -> dict[str, Any]:
        result = asdict(self)
        if not include_files:
            result.pop("files", None)
        result["valid"] = self.failed == 0 and not self.errors
        return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _modified_utc(path: Path) -> str:
    stat = path.stat()
    return datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _normalize_source_root(project_root: Path, source_root: str) -> tuple[Path, str]:
    cleaned = source_root.strip().replace("\\", "/").strip("/")
    if not cleaned:
        raise ValueError("source_root must not be empty")
    parts = tuple(part for part in cleaned.split("/") if part)
    if any(part in {".", ".."} for part in parts):
        raise ValueError(f"source_root must stay inside the project root: {source_root}")
    resolved = project_root.joinpath(*parts).resolve()
    try:
        resolved.relative_to(project_root)
    except ValueError as exc:
        raise ValueError(f"source_root must stay inside the project root: {source_root}") from exc
    return resolved, "/".join(parts)


def _iter_source_files(source_root: Path) -> Iterable[Path]:
    if not source_root.is_dir():
        return ()
    return (
        path
        for path in sorted(source_root.rglob("*"), key=lambda value: value.as_posix().casefold())
        if path.is_file() and path.suffix.casefold() in CPP_SOURCE_EXTENSIONS
    )


def _existing_code_asset(connection: sqlite3.Connection, asset_path: str) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT id, asset_class, profile, revision_value, file_size, modified_utc
        FROM assets
        WHERE asset_path = ?
        """,
        (asset_path,),
    ).fetchone()


def _code_asset_values(
    *,
    asset_path: str,
    source_path: Path,
    digest: str,
    modified_utc: str,
) -> tuple[Any, ...]:
    stat = source_path.stat()
    summary = {
        "language": "cpp",
        "sourcePath": asset_path,
        "extension": source_path.suffix.casefold(),
    }
    return (
        source_path.name,
        CPP_SOURCE_ASSET_CLASS,
        "sha256:" + digest,
        int(stat.st_size),
        modified_utc,
        digest,
        CODE_INDEX_SCHEMA_VERSION,
        CODE_INDEX_EXPORTER_VERSION,
        CODE_PROFILE,
        digest,
        asset_path,
        json.dumps(summary, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        utc_now_iso(),
    )


def _insert_code_asset(
    connection: sqlite3.Connection,
    *,
    asset_path: str,
    source_path: Path,
    digest: str,
    modified_utc: str,
) -> int:
    values = _code_asset_values(
        asset_path=asset_path,
        source_path=source_path,
        digest=digest,
        modified_utc=modified_utc,
    )
    cursor = connection.execute(
        """
        INSERT INTO assets(
            asset_path,
            package_name,
            asset_name,
            asset_class,
            blueprint_type,
            parent_class,
            generated_class,
            skeleton_generated_class,
            status,
            revision_value,
            package_guid,
            file_size,
            modified_utc,
            content_sha256,
            package_dirty,
            schema_version,
            exporter_version,
            profile,
            canonical_sha256,
            canonical_relpath,
            bpctx_relpath,
            summary_json,
            indexed_at_utc
        ) VALUES (?, '', ?, ?, '', '', '', '', 0, ?, '', ?, ?, ?, 0, ?, ?, ?, ?, ?, '', ?, ?)
        """,
        (asset_path, *values),
    )
    return int(cursor.lastrowid)


def _update_code_asset(
    connection: sqlite3.Connection,
    *,
    asset_id: int,
    asset_path: str,
    source_path: Path,
    digest: str,
    modified_utc: str,
) -> None:
    values = _code_asset_values(
        asset_path=asset_path,
        source_path=source_path,
        digest=digest,
        modified_utc=modified_utc,
    )
    connection.execute(
        """
        UPDATE assets
        SET package_name = '',
            asset_name = ?,
            asset_class = ?,
            blueprint_type = '',
            parent_class = '',
            generated_class = '',
            skeleton_generated_class = '',
            status = 0,
            revision_value = ?,
            package_guid = '',
            file_size = ?,
            modified_utc = ?,
            content_sha256 = ?,
            package_dirty = 0,
            schema_version = ?,
            exporter_version = ?,
            profile = ?,
            canonical_sha256 = ?,
            canonical_relpath = ?,
            bpctx_relpath = '',
            summary_json = ?,
            indexed_at_utc = ?
        WHERE id = ? AND asset_path = ?
        """,
        (*values, asset_id, asset_path),
    )


def _prune_code_assets(
    connection: sqlite3.Connection,
    *,
    source_root_names: tuple[str, ...],
    seen_asset_paths: set[str],
) -> list[str]:
    rows = connection.execute(
        """
        SELECT id, asset_path
        FROM assets
        WHERE profile = ? AND asset_class = ?
        ORDER BY asset_path
        """,
        (CODE_PROFILE, CPP_SOURCE_ASSET_CLASS),
    ).fetchall()
    prefixes = tuple(root.rstrip("/") + "/" for root in source_root_names)
    deleted: list[str] = []
    for row in rows:
        asset_path = str(row["asset_path"])
        in_scanned_root = any(
            asset_path == root or asset_path.startswith(prefix)
            for root, prefix in zip(source_root_names, prefixes)
        )
        if not in_scanned_root or asset_path in seen_asset_paths:
            continue
        connection.execute("DELETE FROM assets WHERE id = ?", (int(row["id"]),))
        deleted.append(asset_path)
    return deleted


def build_code_index(
    connection: sqlite3.Connection,
    project_root: Path,
    database_path: Path,
    *,
    source_roots: Iterable[str] = DEFAULT_SOURCE_ROOTS,
    force: bool = False,
    prune: bool = True,
    project_key: str = "",
) -> CodeIndexResult:
    resolved_project = project_root.expanduser().resolve()
    if resolved_project.is_file():
        resolved_project = resolved_project.parent
    if not resolved_project.is_dir():
        raise FileNotFoundError(f"Project root not found: {resolved_project}")

    normalized_roots: list[tuple[Path, str]] = []
    seen_root_names: set[str] = set()
    for source_root in source_roots:
        resolved_root, root_name = _normalize_source_root(resolved_project, str(source_root))
        if root_name in seen_root_names:
            continue
        if not resolved_root.is_dir():
            raise FileNotFoundError(f"Source root not found: {resolved_root}")
        seen_root_names.add(root_name)
        normalized_roots.append((resolved_root, root_name))
    if not normalized_roots:
        raise ValueError("At least one source_root is required")

    result = CodeIndexResult(
        project_root=str(resolved_project),
        database=str(database_path.expanduser().resolve()),
        source_roots=[root_name for _, root_name in normalized_roots],
    )
    seen_asset_paths: set[str] = set()
    semantic_facts = []

    for source_root, _ in normalized_roots:
        for source_path in _iter_source_files(source_root):
            asset_path = source_path.relative_to(resolved_project).as_posix()
            seen_asset_paths.add(asset_path)
            try:
                digest = _sha256(source_path)
                modified_utc = _modified_utc(source_path)
                stat = source_path.stat()
                file_facts = extract_code_file_facts(source_path, asset_path)
                revision_value = "sha256:" + digest
                existing = _existing_code_asset(connection, asset_path)
                if existing is not None:
                    if (
                        str(existing["profile"]) != CODE_PROFILE
                        or str(existing["asset_class"]) != CPP_SOURCE_ASSET_CLASS
                    ):
                        raise ValueError(
                            f"Indexed path collision for {asset_path}: "
                            f"{existing['profile']}/{existing['asset_class']}"
                        )
                    unchanged = (
                        not force
                        and str(existing["revision_value"]) == revision_value
                        and int(existing["file_size"]) == int(stat.st_size)
                        and str(existing["modified_utc"]) == modified_utc
                    )
                    if unchanged:
                        semantic_facts.append(file_facts)
                        result.skipped += 1
                        result.files.append(
                            CodeFileIndexResult(asset_path=asset_path, status="skipped", reason="unchanged")
                        )
                        continue
                    status = "updated"
                else:
                    status = "added"

                with connection:
                    if existing is None:
                        _insert_code_asset(
                            connection,
                            asset_path=asset_path,
                            source_path=source_path,
                            digest=digest,
                            modified_utc=modified_utc,
                        )
                    else:
                        _update_code_asset(
                            connection,
                            asset_id=int(existing["id"]),
                            asset_path=asset_path,
                            source_path=source_path,
                            digest=digest,
                            modified_utc=modified_utc,
                        )
                semantic_facts.append(file_facts)
                if status == "added":
                    result.added += 1
                else:
                    result.updated += 1
                result.files.append(CodeFileIndexResult(asset_path=asset_path, status=status))
            except (OSError, ValueError, sqlite3.Error) as exc:
                result.failed += 1
                result.errors.append(f"{asset_path}: {exc}")
                result.files.append(CodeFileIndexResult(asset_path=asset_path, status="failed", reason=str(exc)))

    try:
        with connection:
            if prune and result.failed == 0:
                deleted = _prune_code_assets(
                    connection,
                    source_root_names=tuple(root_name for _, root_name in normalized_roots),
                    seen_asset_paths=seen_asset_paths,
                )
                result.deleted = len(deleted)
                result.files.extend(
                    CodeFileIndexResult(asset_path=asset_path, status="deleted", reason="source-file-missing")
                    for asset_path in deleted
                )

            if semantic_facts:
                semantic_result = rebuild_code_semantics(
                    connection,
                    semantic_facts,
                    code_profile=CODE_PROFILE,
                    source_asset_class=CPP_SOURCE_ASSET_CLASS,
                )
                result.symbols = semantic_result.symbols
                result.references = semantic_result.references
                result.semantic_warnings.extend(semantic_result.warnings)

            if project_key.strip():
                set_metadata(connection, "project_key", project_key.strip())
            set_metadata(connection, "last_code_project_root", str(resolved_project))
            set_metadata(connection, "last_code_indexed_at_utc", utc_now_iso())
            set_metadata(connection, "last_code_file_count", str(len(seen_asset_paths)))
            set_metadata(connection, "last_code_symbol_count", str(result.symbols))
            set_metadata(connection, "last_code_reference_count", str(result.references))
    except sqlite3.Error as exc:
        result.failed += 1
        result.errors.append(f"semantic index transaction failed: {exc}")

    return result
