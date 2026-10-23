"""Pack file loading and record validation for R7 knowledge packs."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .errors import ErrorCode, MemoryMountError
from .integrity import IntegrityReport, verify_integrity_receipt
from .jsonutil import canonical_json_bytes, parse_utc, strict_json_loads
from .limits import MountLimits
from .manifest import PackManifest


_RECORD_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{2,95}$")
_RECORD_TYPES = {
    "projectFact",
    "projectRule",
    "decisionRecord",
    "knownIssue",
    "taskRecord",
    "runtimeEvidence",
}
_SOURCE_KINDS = {"user-confirmed", "tool-observed", "model-inferred"}
_STORED_STATUSES = {"valid", "unverified", "stale"}
_SCOPE_TYPES = {
    "project",
    "asset",
    "symbol",
    "graph",
    "node",
    "dataTableRow",
    "log",
    "file",
    "external",
}
_RECORD_KEYS = {
    "recordId",
    "recordType",
    "subjectKey",
    "title",
    "body",
    "sourceKind",
    "confidence",
    "status",
    "scopes",
    "updatedAtUtc",
    "details",
}
_SCOPE_KEYS = {"scopeType", "scopeKey", "details"}


def _fail(code: ErrorCode, message: str, **details: Any) -> None:
    raise MemoryMountError(code, message, details=details)


def _record_invalid(message: str, **details: Any) -> None:
    _fail(ErrorCode.PACK_RECORDS_INVALID, message, **details)


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _record_invalid(f"{field_name} must be a JSON object.", field=field_name)
    return value


def _reject_unknown(mapping: dict[str, Any], allowed: set[str], field_name: str) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        _record_invalid(
            f"{field_name} contains unsupported fields.",
            field=field_name,
            unknownFields=unknown,
        )


def _text(
    value: Any,
    field_name: str,
    *,
    minimum: int = 0,
    maximum: int,
) -> str:
    if not isinstance(value, str):
        _record_invalid(f"{field_name} must be a string.", field=field_name)
    if len(value) < minimum:
        _record_invalid(f"{field_name} is too short.", field=field_name)
    if len(value) > maximum:
        _record_invalid(f"{field_name} is too long.", field=field_name, maximum=maximum)
    return value


def _resolve_root(pack_root: str | Path) -> Path:
    candidate = Path(pack_root).expanduser()
    try:
        if candidate.is_symlink():
            _fail(ErrorCode.PACK_PATH_INVALID, "Pack root must not be a symlink.")
    except OSError as exc:
        _fail(ErrorCode.IO_ERROR, "Could not inspect pack root.", reason=exc.__class__.__name__)
    if not candidate.exists():
        _fail(ErrorCode.PACK_NOT_FOUND, "Pack directory does not exist.")
    try:
        root = candidate.resolve(strict=True)
    except OSError as exc:
        _fail(ErrorCode.PACK_PATH_INVALID, "Pack root could not be resolved.", reason=exc.__class__.__name__)
    if not root.is_dir():
        _fail(ErrorCode.PACK_PATH_INVALID, "Pack root must be a directory.")
    return root


def _resolve_child(root: Path, name: str) -> Path:
    candidate = root / name
    try:
        if candidate.is_symlink():
            _fail(ErrorCode.PACK_PATH_INVALID, f"{name} must not be a symlink.", field=name)
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError:
        _fail(ErrorCode.PACK_NOT_FOUND, f"Pack file is missing: {name}.", field=name)
    except OSError as exc:
        _fail(ErrorCode.IO_ERROR, f"Could not resolve {name}.", field=name, reason=exc.__class__.__name__)
    if resolved.parent != root:
        _fail(ErrorCode.PACK_PATH_INVALID, f"{name} escapes the pack root.", field=name)
    if not resolved.is_file():
        _fail(ErrorCode.PACK_PATH_INVALID, f"{name} must be a regular file.", field=name)
    return resolved


def _read_limited(path: Path, *, limit: int, field_name: str) -> bytes:
    try:
        size = path.stat().st_size
    except OSError as exc:
        _fail(ErrorCode.IO_ERROR, f"Could not stat {field_name}.", field=field_name, reason=exc.__class__.__name__)
    if size > limit:
        _fail(
            ErrorCode.PACK_TOO_LARGE,
            f"{field_name} exceeds the configured limit.",
            field=field_name,
            size=size,
            limit=limit,
        )
    try:
        data = path.read_bytes()
    except OSError as exc:
        _fail(ErrorCode.IO_ERROR, f"Could not read {field_name}.", field=field_name, reason=exc.__class__.__name__)
    if len(data) > limit:
        _fail(
            ErrorCode.PACK_TOO_LARGE,
            f"{field_name} exceeds the configured limit.",
            field=field_name,
            size=len(data),
            limit=limit,
        )
    return data


@dataclass(frozen=True)
class PackRecord:
    pack_id: str
    record_id: str
    record_type: str
    subject_key: str
    title: str
    body: str
    source_kind: str
    confidence: float
    status: str
    scopes: tuple[dict[str, Any], ...]
    updated_at_utc: str
    updated_epoch: float
    details: dict[str, Any]
    content_sha256: str

    @property
    def global_id(self) -> str:
        return f"{self.pack_id}:{self.record_id}"

    @classmethod
    def from_dict(
        cls,
        value: Any,
        *,
        pack_id: str,
        limits: MountLimits,
        line_number: int,
    ) -> PackRecord:
        data = _mapping(value, f"records[{line_number}]")
        _reject_unknown(data, _RECORD_KEYS, f"records[{line_number}]")
        record_id = _text(
            data.get("recordId"),
            f"records[{line_number}].recordId",
            minimum=3,
            maximum=96,
        )
        if not _RECORD_ID_PATTERN.fullmatch(record_id):
            _record_invalid(
                "recordId must match ^[a-z0-9][a-z0-9._-]{2,95}$.",
                line=line_number,
            )
        record_type = _text(
            data.get("recordType"),
            f"records[{line_number}].recordType",
            minimum=1,
            maximum=64,
        )
        if record_type not in _RECORD_TYPES:
            _record_invalid("Unsupported recordType.", line=line_number, recordType=record_type)
        subject_key = _text(
            data.get("subjectKey"),
            f"records[{line_number}].subjectKey",
            minimum=1,
            maximum=limits.max_subject_key_chars,
        )
        title = _text(
            data.get("title"),
            f"records[{line_number}].title",
            minimum=1,
            maximum=limits.max_title_chars,
        )
        body = _text(
            data.get("body"),
            f"records[{line_number}].body",
            minimum=1,
            maximum=limits.max_body_chars,
        )
        source_kind = _text(
            data.get("sourceKind"),
            f"records[{line_number}].sourceKind",
            minimum=1,
            maximum=32,
        )
        if source_kind not in _SOURCE_KINDS:
            _record_invalid("Unsupported sourceKind.", line=line_number, sourceKind=source_kind)
        confidence = data.get("confidence", 1.0)
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
            _record_invalid("confidence must be a number.", line=line_number)
        confidence = float(confidence)
        if not math.isfinite(confidence) or confidence < 0.0 or confidence > 1.0:
            _record_invalid("confidence must be between 0.0 and 1.0.", line=line_number)
        status = data.get("status")
        if status is None:
            status = "valid" if source_kind == "user-confirmed" else "unverified"
        status = _text(status, f"records[{line_number}].status", minimum=1, maximum=16)
        if status not in _STORED_STATUSES:
            _record_invalid(
                "Pack records may only store valid, unverified, or stale status.",
                line=line_number,
            )
        scopes = _parse_scopes(data.get("scopes", []), limits=limits, line_number=line_number)
        updated_at = _text(
            data.get("updatedAtUtc"),
            f"records[{line_number}].updatedAtUtc",
            minimum=1,
            maximum=64,
        )
        try:
            updated_epoch = parse_utc(updated_at, f"records[{line_number}].updatedAtUtc").timestamp()
        except ValueError as exc:
            _record_invalid(str(exc), line=line_number)
        details = data.get("details", {})
        if not isinstance(details, dict):
            _record_invalid("details must be a JSON object.", line=line_number)
        details_bytes = canonical_json_bytes(details)
        if len(details_bytes) > limits.max_details_bytes:
            _record_invalid(
                "details exceeds the configured limit.",
                line=line_number,
                limit=limits.max_details_bytes,
            )
        content_sha256 = record_content_sha256(
            record_type=record_type,
            subject_key=subject_key,
            title=title,
            body=body,
            scopes=scopes,
            details=details,
        )
        return cls(
            pack_id=pack_id,
            record_id=record_id,
            record_type=record_type,
            subject_key=subject_key,
            title=title,
            body=body,
            source_kind=source_kind,
            confidence=confidence,
            status=status,
            scopes=scopes,
            updated_at_utc=updated_at,
            updated_epoch=updated_epoch,
            details=details,
            content_sha256=content_sha256,
        )

    def to_index_values(self) -> tuple[Any, ...]:
        return (
            self.pack_id,
            self.record_id,
            self.global_id,
            self.record_type,
            self.subject_key,
            self.title,
            self.body,
            self.source_kind,
            self.confidence,
            self.status,
            canonical_json_bytes(list(self.scopes)).decode("utf-8"),
            canonical_json_bytes(self.details).decode("utf-8"),
            self.content_sha256,
            self.updated_at_utc,
            self.updated_epoch,
        )


def _parse_scopes(
    value: Any,
    *,
    limits: MountLimits,
    line_number: int,
) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list):
        _record_invalid("scopes must be an array.", line=line_number)
    if len(value) > limits.max_scopes_per_record:
        _record_invalid("scopes exceeds the configured limit.", line=line_number)
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(value):
        data = _mapping(item, f"records[{line_number}].scopes[{index}]")
        _reject_unknown(data, _SCOPE_KEYS, f"records[{line_number}].scopes[{index}]")
        scope_type = _text(
            data.get("scopeType"),
            f"records[{line_number}].scopes[{index}].scopeType",
            minimum=1,
            maximum=32,
        )
        if scope_type not in _SCOPE_TYPES:
            _record_invalid("Unsupported scopeType.", line=line_number, scopeType=scope_type)
        scope_key = _text(
            data.get("scopeKey"),
            f"records[{line_number}].scopes[{index}].scopeKey",
            minimum=1,
            maximum=limits.max_scope_key_chars,
        )
        key = (scope_type, scope_key)
        if key in seen:
            _record_invalid("Duplicate scopeType/scopeKey pair.", line=line_number)
        seen.add(key)
        details = data.get("details", {})
        if not isinstance(details, dict):
            _record_invalid("scope details must be a JSON object.", line=line_number)
        if len(canonical_json_bytes(details)) > limits.max_details_bytes:
            _record_invalid(
                "scope details exceeds the configured limit.",
                line=line_number,
                limit=limits.max_details_bytes,
            )
        normalized.append(
            {
                "scopeType": scope_type,
                "scopeKey": scope_key,
                "details": details,
            }
        )
    return tuple(sorted(normalized, key=lambda item: (item["scopeType"], item["scopeKey"])))


def record_content_sha256(
    *,
    record_type: str,
    subject_key: str,
    title: str,
    body: str,
    scopes: tuple[dict[str, Any], ...],
    details: dict[str, Any],
) -> str:
    payload = {
        "recordType": record_type,
        "subjectKey": subject_key,
        "title": title,
        "body": body,
        "scopes": list(scopes),
        "details": details,
    }
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


@dataclass(frozen=True)
class PackContent:
    root: Path
    manifest: PackManifest
    records: tuple[PackRecord, ...]
    records_bytes: bytes
    integrity: IntegrityReport

    @property
    def record_count(self) -> int:
        return len(self.records)


def load_pack(
    pack_root: str | Path,
    *,
    limits: MountLimits,
    authorize: Callable[[PackManifest], None] | None = None,
) -> PackContent:
    """Load and fully verify a pack without writing anything.

    ``authorize`` runs immediately after the manifest is parsed and before any
    record bytes are read, so an unauthorized caller cannot trigger content
    parsing for a personal or project pack.
    """

    root = _resolve_root(pack_root)
    manifest_path = _resolve_child(root, "pack.json")
    records_path = _resolve_child(root, "records.jsonl")

    manifest_raw = _read_limited(
        manifest_path,
        limit=limits.max_manifest_bytes,
        field_name="pack.json",
    )
    if manifest_raw.startswith(b"\xef\xbb\xbf"):
        _fail(ErrorCode.MANIFEST_INVALID, "pack.json must not contain a UTF-8 BOM.")
    try:
        manifest_text = manifest_raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        _fail(ErrorCode.MANIFEST_INVALID, "pack.json must be valid UTF-8.", offset=exc.start)
    try:
        manifest_data = strict_json_loads(manifest_text)
    except (ValueError, TypeError) as exc:
        _fail(ErrorCode.MANIFEST_INVALID, "pack.json is not strict JSON.", reason=str(exc))
    manifest = PackManifest.from_dict(manifest_data, limits=limits)
    if authorize is not None:
        authorize(manifest)
    integrity = verify_integrity_receipt(
        root,
        manifest_payload=manifest_data,
        manifest=manifest,
        limits=limits,
    )

    records_raw = _read_limited(
        records_path,
        limit=limits.max_pack_bytes,
        field_name="records.jsonl",
    )
    actual_hash = hashlib.sha256(records_raw).hexdigest()
    expected_hash = manifest.content_hash.removeprefix("sha256:")
    if actual_hash != expected_hash:
        _fail(
            ErrorCode.PACK_HASH_MISMATCH,
            "records.jsonl does not match manifest contentHash.",
            expected=manifest.content_hash,
            actual=f"sha256:{actual_hash}",
        )
    if records_raw.startswith(b"\xef\xbb\xbf"):
        _fail(ErrorCode.PACK_RECORDS_INVALID, "records.jsonl must not contain a UTF-8 BOM.")
    if manifest.record_count == 0:
        if records_raw != b"":
            _fail(
                ErrorCode.PACK_RECORDS_INVALID,
                "An empty pack must use a zero-byte records.jsonl.",
            )
        lines: list[str] = []
    else:
        if not records_raw.endswith(b"\n"):
            _fail(ErrorCode.PACK_RECORDS_INVALID, "records.jsonl must end with a single LF.")
        try:
            records_text = records_raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            _fail(
                ErrorCode.PACK_RECORDS_INVALID,
                "records.jsonl must be valid UTF-8.",
                offset=exc.start,
            )
        if "\r" in records_text:
            _fail(ErrorCode.PACK_RECORDS_INVALID, "records.jsonl must use LF line endings only.")
        lines = records_text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        if any(line == "" for line in lines):
            _fail(ErrorCode.PACK_RECORDS_INVALID, "records.jsonl must not contain blank lines.")
    if len(lines) != manifest.record_count:
        _fail(
            ErrorCode.PACK_RECORDS_INVALID,
            "records.jsonl line count does not match manifest recordCount.",
            expected=manifest.record_count,
            actual=len(lines),
        )

    records: list[PackRecord] = []
    seen_ids: set[str] = set()
    for index, line in enumerate(lines, start=1):
        try:
            parsed = strict_json_loads(line)
        except (ValueError, TypeError) as exc:
            _fail(
                ErrorCode.PACK_RECORDS_INVALID,
                "Record line is not strict JSON.",
                line=index,
                reason=str(exc),
            )
        if canonical_json_bytes(parsed).decode("utf-8") != line:
            _fail(
                ErrorCode.PACK_RECORDS_INVALID,
                "Record line is not canonical UTF-8 JSON.",
                line=index,
            )
        record = PackRecord.from_dict(
            parsed,
            pack_id=manifest.pack_id,
            limits=limits,
            line_number=index,
        )
        if record.record_id in seen_ids:
            _fail(
                ErrorCode.PACK_DUPLICATE_ID,
                "Duplicate recordId within pack.",
                recordId=record.record_id,
                line=index,
            )
        seen_ids.add(record.record_id)
        records.append(record)
    return PackContent(
        root=root,
        manifest=manifest,
        records=tuple(records),
        records_bytes=records_raw,
        integrity=integrity,
    )
