"""Candidate-only ChangeSet 2.1 idempotency block + durable scope store.

D-07 is APPROVED as a design, not implemented in the real ChangeSet/Memory
schema.  This module is an isolated prototype: it persists one JSON block per
``(projectName, assetPath, graphId, idempotencyKey)`` scope, uses an OS-level
create-exclusive scope lock, and never claims exactly-once.

States: reserved -> applied | failed | indeterminate.
A crash after Apply but before ``mark_applied`` leaves ``reserved``; a retry must
fail closed (``graphpatch-idempotency-indeterminate``) until a human reconciles
or a fresh Preview proves otherwise.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .canonical import DOMAIN_ARTIFACT, is_sha256, short_digest
from .errors import ContractError, make_diagnostic
from .schema import check_schema, validate_document

IDEMPOTENCY_SCHEMA_VERSION = "ueak.change-set-idempotency/1.0"
CHANGE_SET_SCHEMA_VERSION = "2.1"
STATES = ("reserved", "applied", "failed", "indeterminate")
BLOCKING_STATES = ("reserved", "indeterminate")
DEFAULT_STALE_LOCK_SECONDS = 900


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def scope_key(patch: dict[str, Any]) -> str:
    target = patch.get("target") or {}
    projection = {
        "projectName": patch.get("projectName", ""),
        "assetPath": target.get("assetPath", ""),
        "graphId": target.get("graphId", ""),
        "idempotencyKey": patch.get("idempotencyKey", ""),
    }
    return "sha256:" + short_digest(projection, DOMAIN_ARTIFACT, 64)


def scope_projection(patch: dict[str, Any]) -> dict[str, Any]:
    target = patch.get("target") or {}
    return {
        "projectName": str(patch.get("projectName", "")),
        "assetPath": str(target.get("assetPath", "")),
        "graphId": str(target.get("graphId", "")),
        "idempotencyKey": str(patch.get("idempotencyKey", "")),
    }


@dataclass
class IdempotencyOutcome:
    state: str
    record: dict[str, Any] | None = None
    diagnostics: list[dict[str, Any]] | None = None


class GraphPatchIdempotencyStore:
    """File-backed ChangeSet 2.1 candidate block store (not the real ChangeSet)."""

    def __init__(self, root: Path | str, *, schema_path: Path | str | None = None, stale_lock_seconds: int = DEFAULT_STALE_LOCK_SECONDS) -> None:
        self.root = Path(root)
        self.schema_path = Path(schema_path) if schema_path else Path(__file__).resolve().parents[1] / "schemas" / "change-set-idempotency.schema.json"
        if not self.schema_path.exists():
            raise FileNotFoundError(f"Missing idempotency schema: {self.schema_path}")
        with self.schema_path.open("r", encoding="utf-8") as handle:
            self.schema = json.load(handle)
        check_schema(self.schema, path=str(self.schema_path))
        self.stale_lock_seconds = stale_lock_seconds
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, patch: dict[str, Any]) -> Path:
        return self.root / (scope_key(patch).replace(":", "-") + ".json")

    def _lock_path(self, patch: dict[str, Any]) -> Path:
        return self.root / (scope_key(patch).replace(":", "-") + ".lock")

    @contextmanager
    def _scope_lock(self, patch: dict[str, Any]) -> Iterator[bool]:
        lock_path = self._lock_path(patch)
        descriptor = None
        acquired = False
        try:
            descriptor = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(descriptor, f"{os.getpid()}\n{time.time()}\n".encode("ascii"))
            os.fsync(descriptor)
            acquired = True
        except FileExistsError:
            acquired = False
        try:
            yield acquired
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if acquired:
                try:
                    lock_path.unlink()
                except FileNotFoundError:
                    pass

    def load(self, patch: dict[str, Any]) -> dict[str, Any] | None:
        path = self._path(patch)
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as handle:
            record = json.load(handle)
        issues = validate_document(record, self.schema)
        if issues:
            raise ContractError(
                "graphpatch-idempotency-state-invalid",
                "Stored idempotency block does not satisfy ChangeSet 2.1 candidate schema",
                path=str(path),
            )
        return record

    def _write(self, patch: dict[str, Any], record: dict[str, Any]) -> None:
        issues = validate_document(record, self.schema)
        if issues:
            raise ContractError(
                "graphpatch-idempotency-state-invalid",
                "Refusing to write an invalid idempotency block",
                path=str(self._path(patch)),
            )
        path = self._path(patch)
        descriptor, temporary = tempfile.mkstemp(prefix=".idem-", suffix=".tmp", dir=str(self.root))
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(record, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            try:
                dir_fd = os.open(str(self.root), os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def reserve(self, patch: dict[str, Any], payload_hash: str) -> IdempotencyOutcome:
        with self._scope_lock(patch) as acquired:
            if not acquired:
                return IdempotencyOutcome("locked")
            record = self.load(patch)
            if record is not None:
                if record.get("payloadHash") != payload_hash:
                    return IdempotencyOutcome("conflict", record)
                state = str(record.get("state", ""))
                if state == "applied":
                    return IdempotencyOutcome("replay", record)
                if state in BLOCKING_STATES:
                    return IdempotencyOutcome("indeterminate", record)
                if state == "failed":
                    # A failed attempt may be retried, but it must start from a
                    # fresh reservation rather than silently reuse the record.
                    record = None
            now = _utc_now()
            record = {
                "schemaVersion": IDEMPOTENCY_SCHEMA_VERSION,
                "changeSetSchemaVersion": CHANGE_SET_SCHEMA_VERSION,
                "scopeKey": scope_key(patch),
                **scope_projection(patch),
                "patchId": str(patch.get("patchId", "")),
                "payloadHash": payload_hash,
                "state": "reserved",
                "reservedAtUtc": now,
                "updatedAtUtc": now,
            }
            self._write(patch, record)
            return IdempotencyOutcome("reserved", record)

    def mark_applied(self, patch: dict[str, Any], payload_hash: str, result: dict[str, Any]) -> dict[str, Any]:
        with self._scope_lock(patch) as acquired:
            if not acquired:
                raise ContractError("graphpatch-idempotency-locked", "Scope is locked by another process")
            record = self.load(patch)
            if record is None or record.get("payloadHash") != payload_hash or record.get("state") != "reserved":
                raise ContractError("graphpatch-idempotency-state-invalid", "No matching reserved record")
            record.update(
                {
                    "state": "applied",
                    "transactionId": str(result.get("transactionId", "")),
                    "actualGraphHash": str(result.get("actualGraphHash", "")),
                    "applyReceipt": str(result.get("applyReceipt", "")),
                    "updatedAtUtc": _utc_now(),
                    "result": result,
                }
            )
            self._write(patch, record)
            return record

    def mark_failed(self, patch: dict[str, Any], payload_hash: str, failure_code: str) -> dict[str, Any]:
        return self._mark_terminal(patch, payload_hash, "failed", failure_code=failure_code)

    def mark_indeterminate(self, patch: dict[str, Any], payload_hash: str, failure_code: str) -> dict[str, Any]:
        return self._mark_terminal(patch, payload_hash, "indeterminate", failure_code=failure_code)

    def _mark_terminal(self, patch: dict[str, Any], payload_hash: str, state: str, *, failure_code: str) -> dict[str, Any]:
        with self._scope_lock(patch) as acquired:
            if not acquired:
                raise ContractError("graphpatch-idempotency-locked", "Scope is locked by another process")
            record = self.load(patch)
            if record is None or record.get("payloadHash") != payload_hash or record.get("state") != "reserved":
                raise ContractError("graphpatch-idempotency-state-invalid", "No matching reserved record")
            record.update({"state": state, "failureCode": failure_code, "updatedAtUtc": _utc_now()})
            self._write(patch, record)
            return record

    def reject_indeterminate(self) -> list[dict[str, Any]]:
        return [
            make_diagnostic(
                "graphpatch-idempotency-indeterminate",
                "A prior Apply is in reserved/indeterminate state; fail-closed until human reconciliation",
                path="$.idempotencyKey",
            )
        ]
