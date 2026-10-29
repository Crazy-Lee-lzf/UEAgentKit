"""Optional manifest integrity receipt for R7 knowledge packs.

The receipt is a v1-compatible sidecar (``pack.integrity.json``). It covers the
manifest canonical digest and the scope/provenance/revision/priority invariants
that ``contentHash`` alone does not protect. It is **not** a signature and does
not establish a trusted author.

* No receipt: legacy v1 pack, records hash only; manifest is unverified.
* Matching digest receipt: accidental/local manifest tamper is detectable.
* Non-null signature: rejected in v1 (``r7-signature-unsupported``); no
  trusted-author claim is made.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ErrorCode, MemoryMountError
from .jsonutil import canonical_json_bytes, parse_utc, strict_json_loads
from .limits import MountLimits
from .manifest import PackManifest


RECEIPT_FILENAME = "pack.integrity.json"
RECEIPT_VERSION = 1
INTEGRITY_MODE_LEGACY = "records-only-unverified-manifest"
INTEGRITY_MODE_DIGEST = "manifest-digest-verified"
AUTHOR_AUTHENTICITY_NOT_ESTABLISHED = "not-established"
WARNING_MANIFEST_UNVERIFIED = "r7-manifest-integrity-unverified"
_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_REQUIRED_KEYS = {
    "receiptVersion",
    "packId",
    "contentHash",
    "revision",
    "priority",
    "recordCount",
    "manifestDigest",
    "scopeDigest",
    "provenanceDigest",
}
_OPTIONAL_KEYS = {"signature", "createdAtUtc"}
_CHECKED_INVARIANTS = (
    "manifestDigest",
    "packId",
    "contentHash",
    "revision",
    "priority",
    "recordCount",
    "scopeDigest",
    "provenanceDigest",
)


def _fail(code: ErrorCode, message: str, **details: Any) -> None:
    raise MemoryMountError(code, message, details=details)


def canonical_digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def build_integrity_receipt(manifest_payload: dict[str, Any]) -> dict[str, Any]:
    """Build a digest-only receipt for a parsed manifest (no signature)."""

    return {
        "receiptVersion": RECEIPT_VERSION,
        "packId": manifest_payload["packId"],
        "contentHash": manifest_payload["contentHash"],
        "revision": manifest_payload["revision"],
        "priority": manifest_payload["priority"],
        "recordCount": manifest_payload["recordCount"],
        "manifestDigest": canonical_digest(manifest_payload),
        "scopeDigest": canonical_digest(manifest_payload["scope"]),
        "provenanceDigest": canonical_digest(manifest_payload["provenance"]),
    }


@dataclass(frozen=True)
class IntegrityReport:
    receipt_present: bool
    receipt_verified: bool
    integrity_mode: str
    manifest_digest: str
    content_hash: str
    scope_digest: str
    provenance_digest: str
    revision: str
    priority: int
    record_count: int
    signed: bool
    author_authenticity: str
    checked_invariants: tuple[str, ...]
    warnings: tuple[str, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "receiptPresent": self.receipt_present,
            "receiptVerified": self.receipt_verified,
            "integrityMode": self.integrity_mode,
            "manifestDigest": self.manifest_digest,
            "contentHash": self.content_hash,
            "scopeDigest": self.scope_digest,
            "provenanceDigest": self.provenance_digest,
            "revision": self.revision,
            "priority": self.priority,
            "recordCount": self.record_count,
            "signed": self.signed,
            "authorAuthenticity": self.author_authenticity,
            "checkedInvariants": list(self.checked_invariants),
            "warnings": list(self.warnings),
        }


def verify_integrity_receipt(
    root: Path,
    *,
    manifest_payload: dict[str, Any],
    manifest: PackManifest,
    limits: MountLimits,
) -> IntegrityReport:
    manifest_digest = canonical_digest(manifest_payload)
    scope_digest = canonical_digest(manifest_payload["scope"])
    provenance_digest = canonical_digest(manifest_payload["provenance"])
    receipt_path = _resolve_receipt(root)
    if receipt_path is None:
        return IntegrityReport(
            receipt_present=False,
            receipt_verified=False,
            integrity_mode=INTEGRITY_MODE_LEGACY,
            manifest_digest=manifest_digest,
            content_hash=manifest.content_hash,
            scope_digest=scope_digest,
            provenance_digest=provenance_digest,
            revision=manifest.revision,
            priority=manifest.priority,
            record_count=manifest.record_count,
            signed=False,
            author_authenticity=AUTHOR_AUTHENTICITY_NOT_ESTABLISHED,
            checked_invariants=("records-content-hash",),
            warnings=(WARNING_MANIFEST_UNVERIFIED,),
        )
    raw = _read_receipt(receipt_path, limits=limits)
    if raw.startswith(b"\xef\xbb\xbf"):
        _fail(ErrorCode.INTEGRITY_RECEIPT_INVALID, "Integrity receipt must not contain a BOM.")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        _fail(
            ErrorCode.INTEGRITY_RECEIPT_INVALID,
            "Integrity receipt must be valid UTF-8.",
            offset=exc.start,
        )
    try:
        data = strict_json_loads(text)
    except (ValueError, TypeError) as exc:
        _fail(
            ErrorCode.INTEGRITY_RECEIPT_INVALID,
            "Integrity receipt is not strict JSON.",
            reason=str(exc),
        )
    if not isinstance(data, dict):
        _fail(ErrorCode.INTEGRITY_RECEIPT_INVALID, "Integrity receipt must be a JSON object.")
    unknown = sorted(set(data) - _REQUIRED_KEYS - _OPTIONAL_KEYS)
    missing = sorted(_REQUIRED_KEYS - set(data))
    if unknown or missing:
        _fail(
            ErrorCode.INTEGRITY_RECEIPT_INVALID,
            "Integrity receipt shape is invalid.",
            unknownFields=unknown,
            missingFields=missing,
        )
    if data.get("signature") is not None:
        _fail(
            ErrorCode.SIGNATURE_UNSUPPORTED,
            "Signed integrity receipts are not supported in v1; no author trust is claimed.",
            field="signature",
        )
    if "createdAtUtc" in data:
        try:
            parse_utc(data["createdAtUtc"], "createdAtUtc")
        except ValueError as exc:
            _fail(ErrorCode.INTEGRITY_RECEIPT_INVALID, str(exc), field="createdAtUtc")

    version = data["receiptVersion"]
    if not isinstance(version, int) or isinstance(version, bool) or version != RECEIPT_VERSION:
        _fail(
            ErrorCode.INTEGRITY_RECEIPT_INVALID,
            f"receiptVersion must be {RECEIPT_VERSION}.",
            field="receiptVersion",
        )
    _require_string(data["packId"], "packId", 64)
    _require_digest(data["contentHash"], "contentHash")
    _require_string(data["revision"], "revision", 128)
    _require_int(data["priority"], "priority")
    _require_int(data["recordCount"], "recordCount")
    _require_digest(data["manifestDigest"], "manifestDigest")
    _require_digest(data["scopeDigest"], "scopeDigest")
    _require_digest(data["provenanceDigest"], "provenanceDigest")

    invariants = {
        "packId": (data["packId"], manifest.pack_id),
        "contentHash": (data["contentHash"], manifest.content_hash),
        "revision": (data["revision"], manifest.revision),
        "priority": (data["priority"], manifest.priority),
        "recordCount": (data["recordCount"], manifest.record_count),
        "manifestDigest": (data["manifestDigest"], manifest_digest),
        "scopeDigest": (data["scopeDigest"], scope_digest),
        "provenanceDigest": (data["provenanceDigest"], provenance_digest),
    }
    for field, (actual, expected) in invariants.items():
        if actual != expected:
            _fail(
                ErrorCode.INTEGRITY_RECEIPT_MISMATCH,
                "Integrity receipt does not match the pack manifest.",
                field=field,
                expected=expected,
                actual=actual,
            )
    return IntegrityReport(
        receipt_present=True,
        receipt_verified=True,
        integrity_mode=INTEGRITY_MODE_DIGEST,
        manifest_digest=manifest_digest,
        content_hash=manifest.content_hash,
        scope_digest=scope_digest,
        provenance_digest=provenance_digest,
        revision=manifest.revision,
        priority=manifest.priority,
        record_count=manifest.record_count,
        signed=False,
        author_authenticity=AUTHOR_AUTHENTICITY_NOT_ESTABLISHED,
        checked_invariants=("records-content-hash", *_CHECKED_INVARIANTS),
        warnings=(),
    )


def _resolve_receipt(root: Path) -> Path | None:
    candidate = root / RECEIPT_FILENAME
    try:
        if candidate.is_symlink():
            _fail(ErrorCode.PACK_PATH_INVALID, "Integrity receipt must not be a symlink.")
        if not candidate.exists():
            return None
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError:
        return None
    except OSError as exc:
        _fail(
            ErrorCode.IO_ERROR,
            "Could not resolve the integrity receipt.",
            reason=exc.__class__.__name__,
        )
    if resolved.parent != root:
        _fail(ErrorCode.PACK_PATH_INVALID, "Integrity receipt escapes the pack root.")
    if not resolved.is_file():
        _fail(ErrorCode.PACK_PATH_INVALID, "Integrity receipt must be a regular file.")
    return resolved


def _read_receipt(path: Path, *, limits: MountLimits) -> bytes:
    limit = min(limits.max_manifest_bytes, 64 * 1024)
    try:
        size = path.stat().st_size
    except OSError as exc:
        _fail(
            ErrorCode.IO_ERROR,
            "Could not stat the integrity receipt.",
            reason=exc.__class__.__name__,
        )
    if size > limit:
        _fail(
            ErrorCode.INTEGRITY_RECEIPT_INVALID,
            "Integrity receipt exceeds the configured limit.",
            size=size,
            limit=limit,
        )
    try:
        return path.read_bytes()
    except OSError as exc:
        _fail(
            ErrorCode.IO_ERROR,
            "Could not read the integrity receipt.",
            reason=exc.__class__.__name__,
        )


def _require_string(value: Any, field_name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        _fail(
            ErrorCode.INTEGRITY_RECEIPT_INVALID,
            f"{field_name} is invalid.",
            field=field_name,
        )
    return value


def _require_int(value: Any, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        _fail(
            ErrorCode.INTEGRITY_RECEIPT_INVALID,
            f"{field_name} must be an integer.",
            field=field_name,
        )
    return value


def _require_digest(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _DIGEST_PATTERN.fullmatch(value):
        _fail(
            ErrorCode.INTEGRITY_RECEIPT_INVALID,
            f"{field_name} must be sha256:<64 lowercase hex>.",
            field=field_name,
        )
    return value
