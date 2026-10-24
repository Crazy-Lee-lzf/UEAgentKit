"""Stable R7 memory-mount error codes.

This module is intentionally standalone. It is not imported by any production
service, MCP tool, or memory path.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    MANIFEST_INVALID = "r7-manifest-invalid"
    MANIFEST_UNSUPPORTED_SCHEMA = "r7-manifest-unsupported-schema"
    MANIFEST_INCOMPATIBLE = "r7-manifest-incompatible"
    SIGNATURE_UNSUPPORTED = "r7-signature-unsupported"
    PACK_PATH_INVALID = "r7-pack-path-invalid"
    PACK_NOT_FOUND = "r7-pack-not-found"
    PACK_NOT_MOUNTED = "r7-pack-not-mounted"
    PACK_TOO_LARGE = "r7-pack-too-large"
    PACK_HASH_MISMATCH = "r7-pack-hash-mismatch"
    PACK_RECORDS_INVALID = "r7-pack-records-invalid"
    PACK_DUPLICATE_ID = "r7-pack-duplicate-id"
    PACK_REVISION_CONFLICT = "r7-pack-revision-conflict"
    PACK_ALREADY_MOUNTED = "r7-pack-already-mounted"
    MOUNT_LIMIT = "r7-mount-limit"
    PERMISSION_DENIED = "r7-permission-denied"
    PROJECT_MISMATCH = "r7-project-mismatch"
    IDENTITY_REQUIRED = "r7-identity-required"
    IDENTITY_INVALID = "r7-identity-claims-invalid"
    IDENTITY_SIGNATURE_INVALID = "r7-identity-signature-invalid"
    IDENTITY_EXPIRED = "r7-identity-expired"
    IDENTITY_ISSUER_MISMATCH = "r7-identity-issuer-mismatch"
    IDENTITY_AUDIENCE_MISMATCH = "r7-identity-audience-mismatch"
    IDENTITY_PROJECT_MISMATCH = "r7-identity-project-mismatch"
    INTEGRITY_RECEIPT_INVALID = "r7-integrity-receipt-invalid"
    INTEGRITY_RECEIPT_MISMATCH = "r7-integrity-receipt-mismatch"
    INTEGRITY_RECEIPT_REQUIRED = "r7-integrity-receipt-required"
    CONTEXT_REQUEST_INVALID = "r7-context-request-invalid"
    CONTEXT_PACK_LIMIT = "r7-context-pack-limit"
    CONTEXT_OUTPUT_TOO_LARGE = "r7-context-output-too-large"
    CONTEXT_INVALID_RECORD = "r7-context-invalid-record"
    HANDOFF_INVALID = "r7-handoff-invalid"
    HANDOFF_LOCAL_CONTEXT_INVALID = "r7-handoff-local-context-invalid"
    HANDOFF_LOCAL_TOO_LARGE = "r7-handoff-local-too-large"
    HANDOFF_OUTPUT_TOO_LARGE = "r7-handoff-output-too-large"
    HANDOFF_PIN_MISMATCH = "r7-handoff-pin-mismatch"
    HANDOFF_PROJECT_MISMATCH = "r7-handoff-project-mismatch"
    QUERY_INVALID = "r7-query-invalid"
    QUERY_TOO_BROAD = "r7-query-too-broad"
    QUERY_TIMEOUT = "r7-query-timeout"
    OUTPUT_TOO_LARGE = "r7-output-too-large"
    INDEX_INCOMPATIBLE = "r7-index-incompatible"
    FTS5_TRIGRAM_UNAVAILABLE = "r7-fts5-trigram-unavailable"
    IO_ERROR = "r7-io-error"
    INTERNAL = "r7-internal"


EXIT_CODES: dict[ErrorCode, int] = {
    ErrorCode.MANIFEST_INVALID: 2,
    ErrorCode.MANIFEST_UNSUPPORTED_SCHEMA: 2,
    ErrorCode.MANIFEST_INCOMPATIBLE: 2,
    ErrorCode.SIGNATURE_UNSUPPORTED: 2,
    ErrorCode.PACK_PATH_INVALID: 2,
    ErrorCode.PACK_NOT_FOUND: 3,
    ErrorCode.PACK_NOT_MOUNTED: 3,
    ErrorCode.PACK_TOO_LARGE: 6,
    ErrorCode.PACK_HASH_MISMATCH: 7,
    ErrorCode.PACK_RECORDS_INVALID: 7,
    ErrorCode.PACK_DUPLICATE_ID: 7,
    ErrorCode.PACK_REVISION_CONFLICT: 5,
    ErrorCode.PACK_ALREADY_MOUNTED: 5,
    ErrorCode.MOUNT_LIMIT: 6,
    ErrorCode.PERMISSION_DENIED: 4,
    ErrorCode.PROJECT_MISMATCH: 4,
    ErrorCode.IDENTITY_REQUIRED: 4,
    ErrorCode.IDENTITY_INVALID: 4,
    ErrorCode.IDENTITY_SIGNATURE_INVALID: 4,
    ErrorCode.IDENTITY_EXPIRED: 4,
    ErrorCode.IDENTITY_ISSUER_MISMATCH: 4,
    ErrorCode.IDENTITY_AUDIENCE_MISMATCH: 4,
    ErrorCode.IDENTITY_PROJECT_MISMATCH: 4,
    ErrorCode.INTEGRITY_RECEIPT_INVALID: 7,
    ErrorCode.INTEGRITY_RECEIPT_MISMATCH: 7,
    ErrorCode.INTEGRITY_RECEIPT_REQUIRED: 6,
    ErrorCode.CONTEXT_REQUEST_INVALID: 2,
    ErrorCode.CONTEXT_PACK_LIMIT: 6,
    ErrorCode.CONTEXT_OUTPUT_TOO_LARGE: 6,
    ErrorCode.CONTEXT_INVALID_RECORD: 7,
    ErrorCode.HANDOFF_INVALID: 2,
    ErrorCode.HANDOFF_LOCAL_CONTEXT_INVALID: 2,
    ErrorCode.HANDOFF_LOCAL_TOO_LARGE: 6,
    ErrorCode.HANDOFF_OUTPUT_TOO_LARGE: 6,
    ErrorCode.HANDOFF_PIN_MISMATCH: 4,
    ErrorCode.HANDOFF_PROJECT_MISMATCH: 4,
    ErrorCode.QUERY_INVALID: 2,
    ErrorCode.QUERY_TOO_BROAD: 6,
    ErrorCode.QUERY_TIMEOUT: 6,
    ErrorCode.OUTPUT_TOO_LARGE: 6,
    ErrorCode.INDEX_INCOMPATIBLE: 7,
    ErrorCode.FTS5_TRIGRAM_UNAVAILABLE: 7,
    ErrorCode.IO_ERROR: 1,
    ErrorCode.INTERNAL: 1,
}


class MemoryMountError(RuntimeError):
    """An R7 error with a stable code and an exit code for CLI callers."""

    def __init__(
        self,
        code: ErrorCode | str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = ErrorCode(code)
        self.message = message
        self.details = dict(details or {})
        super().__init__(message)

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.code]

    def to_payload(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": {
                "code": self.code.value,
                "message": self.message,
                "details": self.details,
            },
        }
