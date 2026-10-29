"""Descriptor publishing/removal for the M1 layout broker.

The descriptor is the only rendezvous file.  It is written atomically (temp file
in the same directory, fsync, ``os.replace``) and then its local permissions are
hardened and *verified*.  If verification cannot prove a current-user-only file,
the descriptor is removed and startup fails closed unless the operator opted
into ``allow_unverified_acl`` for a disposable host.

The descriptor necessarily contains the session token.  It is never logged.
"""

from __future__ import annotations

import hmac
import json
import os
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from . import protocol
from .protocol import build_project_path_hash
from .security import FileSecurityStatus, restrict_file_permissions, verify_file_permissions

_MAX_DESCRIPTOR_BYTES = 64 * 1024
_DESCRIPTOR_KEYS = frozenset(
    {
        "schemaVersion",
        "address",
        "port",
        "token",
        "projectName",
        "projectPathHash",
        "brokerVersion",
        "supportedContractVersions",
        "algorithms",
        "startedAtUtc",
        "processId",
        "maxConcurrentJobs",
    }
)
_ALGORITHM_KEYS = frozenset({"id", "version"})


@dataclass(frozen=True)
class DescriptorWriteResult:
    path: Path
    security: FileSecurityStatus


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_descriptor(
    *,
    port: int,
    token: str,
    project_name: str,
    project_path_hash: str,
    process_id: int | None = None,
    started_at: datetime | None = None,
) -> dict[str, Any]:
    started = started_at or utc_now()
    return {
        "schemaVersion": protocol.DESCRIPTOR_SCHEMA_VERSION,
        "address": "127.0.0.1",
        "port": int(port),
        "token": token,
        "projectName": project_name,
        "projectPathHash": project_path_hash,
        "brokerVersion": protocol.BROKER_VERSION,
        "supportedContractVersions": [protocol.PROTOCOL_SCHEMA_VERSION],
        "algorithms": [dict(protocol.ALGORITHM)],
        "startedAtUtc": format_utc(started),
        "processId": int(os.getpid() if process_id is None else process_id),
        "maxConcurrentJobs": protocol.MAX_CONCURRENT_JOBS,
    }


def _parse_started(value: Any) -> datetime:
    if not isinstance(value, str) or not value or len(value) > 40:
        raise protocol.BrokerProtocolError(
            protocol.ERROR_DESCRIPTOR_INVALID, "Descriptor startedAtUtc is missing or invalid."
        )
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise protocol.BrokerProtocolError(
            protocol.ERROR_DESCRIPTOR_INVALID, "Descriptor startedAtUtc is not ISO-8601."
        ) from exc
    if parsed.tzinfo is None:
        raise protocol.BrokerProtocolError(
            protocol.ERROR_DESCRIPTOR_INVALID, "Descriptor startedAtUtc has no UTC offset."
        )
    return parsed


def validate_descriptor(
    payload: Any,
    *,
    project_name: str | None = None,
    project_path_hash: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate a descriptor before connecting to or trusting the broker."""

    if not isinstance(payload, dict):
        raise protocol.BrokerProtocolError(
            protocol.ERROR_DESCRIPTOR_INVALID, "The broker descriptor must be a JSON object."
        )
    keys = set(payload)
    if keys != _DESCRIPTOR_KEYS:
        unknown = sorted(keys - _DESCRIPTOR_KEYS)
        missing = sorted(_DESCRIPTOR_KEYS - keys)
        detail = "unknown=" + ",".join(protocol.safe_key(item) for item in unknown[:4])
        if missing:
            detail += " missing=" + ",".join(missing[:4])
        raise protocol.BrokerProtocolError(
            protocol.ERROR_DESCRIPTOR_INVALID, f"Descriptor fields are not exact ({detail})."
        )
    if payload.get("schemaVersion") != protocol.DESCRIPTOR_SCHEMA_VERSION:
        raise protocol.BrokerProtocolError(
            protocol.ERROR_DESCRIPTOR_INVALID, "Descriptor schemaVersion is unsupported."
        )
    if payload.get("address") != "127.0.0.1":
        raise protocol.BrokerProtocolError(
            protocol.ERROR_DESCRIPTOR_INVALID, "Descriptor address must be 127.0.0.1."
        )
    port = payload.get("port")
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise protocol.BrokerProtocolError(
            protocol.ERROR_DESCRIPTOR_INVALID, "Descriptor port is invalid."
        )
    token = payload.get("token")
    if not isinstance(token, str) or not protocol._TOKEN_RE.match(token):  # noqa: SLF001 - shared constant
        raise protocol.BrokerProtocolError(
            protocol.ERROR_DESCRIPTOR_INVALID, "Descriptor token is not 32-128 lowercase hex."
        )
    if not isinstance(payload.get("projectName"), str) or not payload["projectName"]:
        raise protocol.BrokerProtocolError(
            protocol.ERROR_DESCRIPTOR_INVALID, "Descriptor projectName is invalid."
        )
    path_hash = payload.get("projectPathHash")
    if not isinstance(path_hash, str) or not protocol._PROJECT_HASH_RE.match(path_hash):  # noqa: SLF001
        raise protocol.BrokerProtocolError(
            protocol.ERROR_DESCRIPTOR_INVALID, "Descriptor projectPathHash is invalid."
        )
    if project_name is not None and payload["projectName"] != project_name:
        raise protocol.BrokerProtocolError(
            protocol.ERROR_PROJECT_MISMATCH, "Descriptor projectName does not match the client."
        )
    if project_path_hash is not None and path_hash != project_path_hash:
        raise protocol.BrokerProtocolError(
            protocol.ERROR_PROJECT_MISMATCH, "Descriptor projectPathHash does not match the client."
        )
    if not isinstance(payload.get("brokerVersion"), str) or not payload["brokerVersion"]:
        raise protocol.BrokerProtocolError(
            protocol.ERROR_DESCRIPTOR_INVALID, "Descriptor brokerVersion is invalid."
        )
    supported = payload.get("supportedContractVersions")
    if (
        not isinstance(supported, list)
        or not supported
        or any(not isinstance(item, str) for item in supported)
        or protocol.PROTOCOL_SCHEMA_VERSION not in supported
    ):
        raise protocol.BrokerProtocolError(
            protocol.ERROR_VERSION_UNSUPPORTED,
            f"Descriptor does not support {protocol.PROTOCOL_SCHEMA_VERSION}.",
        )
    algorithms = payload.get("algorithms")
    if not isinstance(algorithms, list) or not algorithms:
        raise protocol.BrokerProtocolError(
            protocol.ERROR_DESCRIPTOR_INVALID, "Descriptor algorithms are missing."
        )
    found = False
    for entry in algorithms:
        if not isinstance(entry, dict) or set(entry) != _ALGORITHM_KEYS:
            raise protocol.BrokerProtocolError(
                protocol.ERROR_DESCRIPTOR_INVALID, "Descriptor algorithm entries are invalid."
            )
        if not isinstance(entry.get("id"), str) or not isinstance(entry.get("version"), str):
            raise protocol.BrokerProtocolError(
                protocol.ERROR_DESCRIPTOR_INVALID, "Descriptor algorithm id/version are invalid."
            )
        if entry["id"] == protocol.ALGORITHM_ID and entry["version"] == protocol.ALGORITHM_VERSION:
            found = True
    if not found:
        raise protocol.BrokerProtocolError(
            protocol.ERROR_VERSION_UNSUPPORTED,
            f"Descriptor does not implement {protocol.ALGORITHM_ID}/{protocol.ALGORITHM_VERSION}.",
        )
    started = _parse_started(payload.get("startedAtUtc"))
    current = now or utc_now()
    age = current - started
    if age < timedelta(minutes=-5) or age > timedelta(seconds=protocol.DESCRIPTOR_MAX_AGE_SECONDS):
        raise protocol.BrokerProtocolError(
            protocol.ERROR_DESCRIPTOR_INVALID, "Descriptor is older than 12 hours or from the future."
        )
    process_id = payload.get("processId")
    if not isinstance(process_id, int) or isinstance(process_id, bool) or process_id <= 0:
        raise protocol.BrokerProtocolError(
            protocol.ERROR_DESCRIPTOR_INVALID, "Descriptor processId is invalid."
        )
    if payload.get("maxConcurrentJobs") != protocol.MAX_CONCURRENT_JOBS:
        raise protocol.BrokerProtocolError(
            protocol.ERROR_DESCRIPTOR_INVALID, "Descriptor maxConcurrentJobs must be 1."
        )
    return dict(payload)


def write_descriptor_atomic(
    path: str | os.PathLike[str],
    descriptor: Mapping[str, Any],
    *,
    allow_unverified_acl: bool = False,
    now: datetime | None = None,
) -> DescriptorWriteResult:
    """Atomically publish ``descriptor`` with restrictive, verified permissions."""

    target = Path(path)
    validate_descriptor(dict(descriptor), now=now)
    try:
        payload = (
            json.dumps(dict(descriptor), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise protocol.BrokerProtocolError(
            protocol.ERROR_DESCRIPTOR_INVALID, "Descriptor could not be serialized."
        ) from exc
    if len(payload) > _MAX_DESCRIPTOR_BYTES:
        raise protocol.BrokerProtocolError(
            protocol.ERROR_DESCRIPTOR_INVALID, "Descriptor is larger than 64 KiB."
        )
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise protocol.BrokerProtocolError(
            protocol.ERROR_DESCRIPTOR_INVALID, "Descriptor directory could not be created."
        ) from exc

    handle, temporary_name = tempfile.mkstemp(
        prefix=".LayoutBroker-", suffix=".tmp", dir=str(target.parent)
    )
    temporary = Path(temporary_name)
    status: FileSecurityStatus
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        status = restrict_file_permissions(temporary)
        # Replacing the target is the atomic publish step; the ACL travels with
        # the file on Windows, and is re-verified below.
        os.replace(temporary, target)
        status = verify_file_permissions(target)
    except BaseException:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise
    if not status.proven and not allow_unverified_acl:
        try:
            target.unlink()
        except OSError:
            pass
        raise protocol.BrokerProtocolError(
            protocol.ERROR_DESCRIPTOR_INVALID,
            "Descriptor permissions could not be verified; refusing to publish "
            "(pass --allow-unverified-acl only on a disposable host).",
        )
    return DescriptorWriteResult(path=target, security=status)


def read_descriptor_file(
    path: str | os.PathLike[str],
    *,
    project_name: str | None = None,
    project_path_hash: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Read and validate a descriptor file without following links."""

    target = Path(path)
    if not target.is_file():
        raise protocol.BrokerProtocolError(
            protocol.ERROR_DESCRIPTOR_MISSING, "The broker descriptor is missing."
        )
    if target.is_symlink():
        raise protocol.BrokerProtocolError(
            protocol.ERROR_DESCRIPTOR_INVALID, "The broker descriptor must not be a link."
        )
    try:
        size = target.stat().st_size
    except OSError as exc:
        raise protocol.BrokerProtocolError(
            protocol.ERROR_DESCRIPTOR_INVALID, "The broker descriptor could not be read."
        ) from exc
    if size <= 0 or size > _MAX_DESCRIPTOR_BYTES:
        raise protocol.BrokerProtocolError(
            protocol.ERROR_DESCRIPTOR_INVALID, "The broker descriptor size is invalid."
        )
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise protocol.BrokerProtocolError(
            protocol.ERROR_DESCRIPTOR_INVALID, "The broker descriptor is not valid JSON."
        ) from exc
    return validate_descriptor(
        payload,
        project_name=project_name,
        project_path_hash=project_path_hash,
        now=now,
    )


def remove_own_descriptor(path: str | os.PathLike[str], token: str) -> bool:
    """Remove the descriptor only when it still carries this broker's token."""

    target = Path(path)
    try:
        if target.is_symlink():
            return False
        if not target.is_file():
            return False
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict) or not isinstance(payload.get("token"), str):
        return False
    if not hmac.compare_digest(payload["token"].encode("utf-8"), token.encode("utf-8")):
        return False
    try:
        target.unlink()
    except OSError:
        return False
    return True


def descriptor_age_seconds(descriptor: Mapping[str, Any], *, now: datetime | None = None) -> float:
    started = _parse_started(descriptor.get("startedAtUtc"))
    return ((now or utc_now()) - started).total_seconds()


def wait_for_descriptor_removal(path: str | os.PathLike[str], timeout: float = 2.0) -> bool:
    """Test/CLI helper: wait until the descriptor disappears."""

    target = Path(path)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not target.exists():
            return True
        time.sleep(0.02)
    return not target.exists()


__all__ = [
    "DescriptorWriteResult",
    "build_descriptor",
    "build_project_path_hash",
    "descriptor_age_seconds",
    "format_utc",
    "read_descriptor_file",
    "remove_own_descriptor",
    "utc_now",
    "validate_descriptor",
    "wait_for_descriptor_removal",
    "write_descriptor_atomic",
]
