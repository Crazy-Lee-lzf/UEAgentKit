"""Versioned, bounded NDJSON protocol for the M1 layout preview broker.

This module is the single source of truth for the request/response contract used
by the offline broker, its client fixture, and the tests.  It intentionally does
no I/O: validation is a pure transformation of JSON-compatible values.

The implementation enforces these bounded protocol constraints. The related
experimental request schemas live under ``spec/native-tools/m2/``:

* schema ``ueak.layoutbroker/1.0``, one JSON object per line, max 1 MiB;
* the request carries identity (project, session, algorithm) and an inline
  snapshot only - never a file path, command, URL, or executable name;
* the response carries the exact ``build_preview`` result, a request id, and a
  stable error code; the token never appears in a response.
"""

from __future__ import annotations

import hmac
import json
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..graph_contracts.layout import recompute_layout_hash
from .planid import PlanIdError, plan_id_from_result

PROTOCOL_SCHEMA_VERSION = "ueak.layoutbroker/1.0"
DESCRIPTOR_SCHEMA_VERSION = "ueak.layoutbroker-descriptor/1.0"
PREVIEW_SCHEMA_VERSION = "ueak.graphlayoutpreview/1.0"

ALGORITHM_ID = "p0-layered"
ALGORITHM_VERSION = "1.0.0"
ALGORITHM = {"id": ALGORITHM_ID, "version": ALGORITHM_VERSION}

BROKER_VERSION = "0.1.0-m1"
DESCRIPTOR_FILENAME = "LayoutBroker.json"

MAX_MESSAGE_BYTES = 1024 * 1024
MAX_DEADLINE_MS = 5000
MIN_DEADLINE_MS = 100
MAX_CONCURRENT_JOBS = 1
DEFAULT_RATE_LIMIT = 10
RATE_LIMIT_WINDOW_SECONDS = 60.0
DESCRIPTOR_MAX_AGE_SECONDS = 12 * 60 * 60
MAX_COORDINATE = 10_000_000
MAX_TOKEN_HEX = 128
MIN_TOKEN_HEX = 32
MAX_DEDUP_ENTRIES = 1024

# Stable error codes.  The names in the ``native-preview-*`` namespace are the
# ones the Editor side maps to UI/receipt state; the broker only emits these.
ERROR_DESCRIPTOR_MISSING = "native-preview-broker-descriptor-missing"
ERROR_DESCRIPTOR_INVALID = "native-preview-broker-descriptor-invalid"
ERROR_BROKER_UNAVAILABLE = "native-preview-broker-unavailable"
ERROR_TOKEN_REJECTED = "native-preview-token-rejected"
ERROR_PROJECT_MISMATCH = "native-preview-project-mismatch"
ERROR_VERSION_UNSUPPORTED = "native-preview-version-unsupported"
ERROR_REQUEST_TOO_LARGE = "native-preview-request-too-large"
ERROR_REQUEST_INVALID = "native-preview-request-invalid"
ERROR_UNSUPPORTED_FIELD = "native-preview-unsupported-field"
ERROR_DUPLICATE_REQUEST = "native-preview-duplicate-request"
ERROR_BUSY = "native-preview-busy"
ERROR_RATE_LIMITED = "native-preview-rate-limited"
ERROR_TIMEOUT = "native-preview-timeout"
ERROR_CANCELLED = "native-preview-cancelled"
ERROR_WORKER_FAILED = "native-preview-worker-failed"
ERROR_IDENTITY_MISMATCH = "native-preview-identity-mismatch"
ERROR_RESPONSE_TOO_LARGE = "native-preview-response-too-large"
ERROR_RESPONSE_INVALID = "native-preview-response-invalid"
ERROR_PLAN_ID_MISMATCH = "native-preview-plan-id-mismatch"
ERROR_PLAN_HASH_MISMATCH = "native-preview-plan-hash-mismatch"
ERROR_PLAN_INVALID = "native-preview-plan-invalid"
ERROR_DISABLED = "native-preview-disabled"

BROKER_ERROR_CODES = frozenset(
    {
        ERROR_TOKEN_REJECTED,
        ERROR_PROJECT_MISMATCH,
        ERROR_VERSION_UNSUPPORTED,
        ERROR_REQUEST_TOO_LARGE,
        ERROR_REQUEST_INVALID,
        ERROR_UNSUPPORTED_FIELD,
        ERROR_DUPLICATE_REQUEST,
        ERROR_BUSY,
        ERROR_RATE_LIMITED,
        ERROR_TIMEOUT,
        ERROR_CANCELLED,
        ERROR_WORKER_FAILED,
        ERROR_IDENTITY_MISMATCH,
        ERROR_RESPONSE_TOO_LARGE,
    }
)

CLIENT_ERROR_CODES = BROKER_ERROR_CODES | frozenset(
    {
        ERROR_DESCRIPTOR_MISSING,
        ERROR_DESCRIPTOR_INVALID,
        ERROR_BROKER_UNAVAILABLE,
        ERROR_RESPONSE_INVALID,
        ERROR_PLAN_ID_MISMATCH,
        ERROR_PLAN_HASH_MISMATCH,
        ERROR_PLAN_INVALID,
    }
)

_REQUEST_KEYS = frozenset(
    {
        "schemaVersion",
        "requestId",
        "sessionId",
        "projectName",
        "projectPathHash",
        "token",
        "sentAtUtc",
        "deadlineMs",
        "algorithm",
        "options",
        "snapshot",
    }
)
_ALGORITHM_KEYS = frozenset({"id", "version"})
_OPTION_KEYS = frozenset({"mode", "spacingX", "spacingY", "targetWidth", "seed"})
_SNAPSHOT_KEYS = frozenset({"preflight", "contextEnvelope"})

_SUCCESS_KEYS = frozenset(
    {"schemaVersion", "requestId", "ok", "readOnly", "algorithm", "durationMs", "result"}
)
_ERROR_KEYS = frozenset({"schemaVersion", "requestId", "ok", "error"})
_ERROR_OBJECT_KEYS = frozenset({"code", "message", "retryable"})

_RESULT_KEYS = frozenset(
    {
        "schemaVersion",
        "readOnly",
        "source",
        "selectionPolicy",
        "focusScope",
        "assetPath",
        "graphGuid",
        "graphName",
        "packageRevision",
        "packageDirty",
        "graphFingerprint",
        "sessionId",
        "pluginBuildHash",
        "editable",
        "mode",
        "scopeNodeIds",
        "planId",
        "layoutHash",
        "baseGraphHash",
        "nodeCount",
        "pinCount",
        "edgeCount",
        "commentCount",
        "movedCount",
        "movedNodeIds",
        "positions",
        "metrics",
        "warnings",
        "islands",
        "sccs",
        "plan",
        "applyEligible",
        "evidenceLevel",
        "acceptanceEligible",
        "requiredAcceptanceEvidence",
        "openAssets",
        "wroteBlueprint",
        "savedPackage",
    }
)
_POSITION_KEYS = frozenset(
    {
        "nodeId",
        "nodeGuid",
        "kind",
        "beforeX",
        "beforeY",
        "afterX",
        "afterY",
        "deltaX",
        "deltaY",
        "width",
        "height",
    }
)
_PLAN_POSITION_KEYS = frozenset({"nodeId", "x", "y"})
_PLAN_POSITION_ALLOWED_KEYS = frozenset({"nodeId", "x", "y", "size"})
_PLAN_SIZE_KEYS = frozenset({"width", "height", "source"})
_PLAN_REQUIRED_KEYS = frozenset({"planId", "layoutHash", "baseGraphHash", "positions"})

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_PROJECT_HASH_RE = re.compile(r"^sha1:[0-9a-f]{40}$")
_TOKEN_RE = re.compile(r"^[0-9a-f]{32,128}$")
_LAYOUT_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_PLAN_ID_RE = re.compile(r"^layout:[0-9a-f]{16}$")
_ALGORITHM_TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_DRIVE_PATH_RE = re.compile(r"(?i)\b[a-z]:[\\/][^\s\"']+")
_UNC_PATH_RE = re.compile(r"(?:\\\\|//)[^\s\"']+")

# The request schema is closed at every level this broker owns.  These exact key
# names are refused outright even though they are not part of any valid level,
# so a caller cannot smuggle an execution intent into the envelope.
_FORBIDDEN_KEYS = frozenset(
    {
        "path",
        "file",
        "filepath",
        "filename",
        "command",
        "cmd",
        "exec",
        "execute",
        "executable",
        "shell",
        "script",
        "url",
        "uri",
        "args",
        "argv",
        "env",
        "cwd",
        "process",
        "spawn",
    }
)


class BrokerProtocolError(Exception):
    """A stable, token-free protocol failure."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "retryable": self.retryable}


@dataclass(frozen=True)
class BrokerRequest:
    """A fully validated request.  The token is deliberately not retained."""

    request_id: str
    session_id: str
    project_name: str
    project_path_hash: str
    deadline_ms: int
    mode: str
    spacing_x: int
    spacing_y: int
    target_width: int
    seed: int
    preflight: dict[str, Any]
    context_envelope: dict[str, Any]


def _fail(code: str, message: str, *, retryable: bool = False) -> None:
    raise BrokerProtocolError(code, message, retryable=retryable)


def safe_key(value: Any) -> str:
    text = _CONTROL_RE.sub("?", str(value))
    return text[:64]


def sanitize_message(text: Any, token: str | None = None) -> str:
    """Bound and redact a diagnostic before it leaves the broker.

    The session token, local absolute paths, and control characters are removed.
    ``/Game/...` virtual content paths are intentionally preserved because they
    identify the asset and are not host paths.
    """

    message = str(text)
    if token:
        message = message.replace(token, "[redacted]")
    message = _DRIVE_PATH_RE.sub("<path>", message)
    message = _UNC_PATH_RE.sub("<path>", message)
    message = _CONTROL_RE.sub(" ", message).strip()
    if not message:
        message = "The layout broker rejected the request."
    return message[:400]


def encode_message(payload: Mapping[str, Any], *, max_bytes: int = MAX_MESSAGE_BYTES) -> bytes:
    try:
        text = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise BrokerProtocolError(
            ERROR_RESPONSE_INVALID, "The broker could not serialize its response."
        ) from exc
    data = text.encode("utf-8") + b"\n"
    if len(data) > max_bytes:
        raise BrokerProtocolError(
            ERROR_RESPONSE_TOO_LARGE,
            "The encoded message exceeds the 1 MiB limit.",
        )
    return data


def decode_message(data: bytes) -> dict[str, Any]:
    if len(data) > MAX_MESSAGE_BYTES:
        _fail(ERROR_REQUEST_TOO_LARGE, "The request exceeds the 1 MiB limit.")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        _fail(ERROR_REQUEST_INVALID, "The request is not valid UTF-8.")
        raise AssertionError from exc
    if not text.strip():
        _fail(ERROR_REQUEST_INVALID, "The request line is empty.")
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, RecursionError) as exc:
        _fail(ERROR_REQUEST_INVALID, "The request is not valid JSON.")
        raise AssertionError from exc
    if not isinstance(payload, dict):
        _fail(ERROR_REQUEST_INVALID, "The request must be a JSON object.")
    return payload


def echo_request_id(payload: Any) -> str | None:
    """Return a request id only when it is itself well formed."""

    if isinstance(payload, dict):
        candidate = payload.get("requestId")
        if isinstance(candidate, str) and _UUID_RE.match(candidate):
            return candidate
    return None


def _expect_object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(ERROR_REQUEST_INVALID, f"{name} must be a JSON object.")
    return value


def _expect_exact_keys(value: Mapping[str, Any], expected: frozenset[str], name: str) -> None:
    keys = set(value)
    unknown = sorted(keys - expected)
    if unknown:
        _fail(
            ERROR_UNSUPPORTED_FIELD,
            f"{name} contains unsupported field(s): "
            + ", ".join(safe_key(item) for item in unknown[:4]),
        )
    missing = sorted(expected - keys)
    if missing:
        _fail(
            ERROR_REQUEST_INVALID,
            f"{name} is missing required field(s): " + ", ".join(missing),
        )
    for key in value:
        if str(key).casefold() in _FORBIDDEN_KEYS:
            _fail(
                ERROR_UNSUPPORTED_FIELD,
                f"{name} may not carry a {safe_key(key)} field.",
            )


def _expect_str(value: Any, name: str, *, minimum: int = 1, maximum: int = 256) -> str:
    if not isinstance(value, str):
        _fail(ERROR_REQUEST_INVALID, f"{name} must be a string.")
    if not minimum <= len(value) <= maximum:
        _fail(ERROR_REQUEST_INVALID, f"{name} must be {minimum}-{maximum} characters.")
    if _CONTROL_RE.search(value):
        _fail(ERROR_REQUEST_INVALID, f"{name} contains control characters.")
    return value


def _expect_int(value: Any, name: str, *, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        _fail(ERROR_REQUEST_INVALID, f"{name} must be an integer.")
    if not minimum <= value <= maximum:
        _fail(ERROR_REQUEST_INVALID, f"{name} must be from {minimum} through {maximum}.")
    return value


def _parse_utc(value: Any, name: str) -> datetime:
    text = _expect_str(value, name, maximum=40)
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        _fail(ERROR_REQUEST_INVALID, f"{name} must be an ISO-8601 UTC timestamp.")
        raise AssertionError from None
    if parsed.tzinfo is None:
        _fail(ERROR_REQUEST_INVALID, f"{name} must include a UTC offset.")
    return parsed


def _validate_token(value: Any, expected: str) -> None:
    token = _expect_str(value, "token", maximum=MAX_TOKEN_HEX)
    if not _TOKEN_RE.match(token):
        _fail(ERROR_TOKEN_REJECTED, "The session token is not valid.")
    if not hmac.compare_digest(token.encode("ascii"), expected.encode("ascii")):
        _fail(ERROR_TOKEN_REJECTED, "The session token does not match this broker.")


def _validate_snapshot(payload: Mapping[str, Any], request: BrokerRequest) -> tuple[dict[str, Any], dict[str, Any]]:
    snapshot = _expect_object(payload.get("snapshot"), "snapshot")
    _expect_exact_keys(snapshot, _SNAPSHOT_KEYS, "snapshot")
    preflight = _expect_object(snapshot.get("preflight"), "snapshot.preflight")
    envelope = _expect_object(snapshot.get("contextEnvelope"), "snapshot.contextEnvelope")
    asset_path = _expect_str(preflight.get("assetPath"), "snapshot.preflight.assetPath", maximum=1024)
    if not asset_path.startswith("/"):
        _fail(
            ERROR_REQUEST_INVALID,
            "snapshot.preflight.assetPath must be a virtual content path.",
        )
    _expect_str(preflight.get("graphGuid"), "snapshot.preflight.graphGuid", maximum=128)
    _expect_int(
        preflight.get("nodeCount"),
        "snapshot.preflight.nodeCount",
        minimum=0,
        maximum=1_000_000,
    )
    if preflight.get("sessionId") != request.session_id:
        _fail(
            ERROR_IDENTITY_MISMATCH,
            "snapshot.preflight.sessionId does not match the request sessionId.",
        )
    if "sessionId" in envelope and envelope.get("sessionId") != request.session_id:
        _fail(
            ERROR_IDENTITY_MISMATCH,
            "snapshot.contextEnvelope.sessionId does not match the request sessionId.",
        )
    for name, document in (
        ("snapshot.preflight", preflight),
        ("snapshot.contextEnvelope", envelope),
    ):
        for key in document:
            if str(key).casefold() in _FORBIDDEN_KEYS:
                _fail(
                    ERROR_UNSUPPORTED_FIELD,
                    f"{name} may not carry a {safe_key(key)} field.",
                )
    return preflight, envelope


def validate_request(
    payload: Mapping[str, Any],
    *,
    project_name: str,
    project_path_hash: str,
    token: str,
    expected_session_id: str | None = None,
) -> BrokerRequest:
    """Validate one wire request against this broker's identity.

    Order matters: structural shape first (so a well-formed request id can be
    echoed), then the token, then version, project, session, and algorithm.  No
    field from the payload is ever executed or resolved as a path.
    """

    if not isinstance(payload, dict):
        _fail(ERROR_REQUEST_INVALID, "The request must be a JSON object.")
    _expect_exact_keys(payload, _REQUEST_KEYS, "request")
    request_id = _expect_str(payload.get("requestId"), "requestId", maximum=36)
    if not _UUID_RE.match(request_id):
        _fail(ERROR_REQUEST_INVALID, "requestId must be a canonical lowercase UUID.")

    _validate_token(payload.get("token"), token)

    schema_version = _expect_str(payload.get("schemaVersion"), "schemaVersion", maximum=64)
    if schema_version != PROTOCOL_SCHEMA_VERSION:
        _fail(
            ERROR_VERSION_UNSUPPORTED,
            f"Unsupported protocol schemaVersion; expected {PROTOCOL_SCHEMA_VERSION}.",
        )

    if _expect_str(payload.get("projectName"), "projectName", maximum=128) != project_name:
        _fail(ERROR_PROJECT_MISMATCH, "The request projectName does not match this broker.")
    path_hash = _expect_str(payload.get("projectPathHash"), "projectPathHash", maximum=64)
    if not _PROJECT_HASH_RE.match(path_hash):
        _fail(ERROR_REQUEST_INVALID, "projectPathHash must be sha1:<40 lowercase hex>.")
    if path_hash != project_path_hash:
        _fail(ERROR_PROJECT_MISMATCH, "The request projectPathHash does not match this broker.")

    session_id = _expect_str(payload.get("sessionId"), "sessionId", maximum=128)
    if not _SESSION_RE.match(session_id):
        _fail(ERROR_REQUEST_INVALID, "sessionId has an invalid format.")
    if expected_session_id is not None and session_id != expected_session_id:
        _fail(ERROR_IDENTITY_MISMATCH, "The request sessionId is not the bound Editor session.")

    _parse_utc(payload.get("sentAtUtc"), "sentAtUtc")
    deadline_ms = _expect_int(
        payload.get("deadlineMs"), "deadlineMs", minimum=MIN_DEADLINE_MS, maximum=MAX_DEADLINE_MS
    )

    algorithm = _expect_object(payload.get("algorithm"), "algorithm")
    _expect_exact_keys(algorithm, _ALGORITHM_KEYS, "algorithm")
    algorithm_id = _expect_str(algorithm.get("id"), "algorithm.id", maximum=64)
    algorithm_version = _expect_str(algorithm.get("version"), "algorithm.version", maximum=64)
    if not _ALGORITHM_TOKEN_RE.match(algorithm_id) or not _ALGORITHM_TOKEN_RE.match(
        algorithm_version
    ):
        _fail(ERROR_REQUEST_INVALID, "algorithm id/version have an invalid format.")
    if (algorithm_id, algorithm_version) != (ALGORITHM_ID, ALGORITHM_VERSION):
        _fail(
            ERROR_VERSION_UNSUPPORTED,
            f"Unsupported algorithm; this broker implements {ALGORITHM_ID}/{ALGORITHM_VERSION}.",
        )

    options = _expect_object(payload.get("options"), "options")
    _expect_exact_keys(options, _OPTION_KEYS, "options")
    mode = _expect_str(options.get("mode"), "options.mode", maximum=16)
    if mode != "full":
        _fail(
            ERROR_REQUEST_INVALID,
            "M1 supports only mode=full; selection/incremental remain on the legacy path.",
        )
    spacing_x = _expect_int(options.get("spacingX"), "options.spacingX", minimum=1, maximum=4096)
    spacing_y = _expect_int(options.get("spacingY"), "options.spacingY", minimum=1, maximum=4096)
    target_width = _expect_int(
        options.get("targetWidth"), "options.targetWidth", minimum=256, maximum=16384
    )
    seed = _expect_int(options.get("seed"), "options.seed", minimum=0, maximum=2_147_483_647)

    request = BrokerRequest(
        request_id=request_id,
        session_id=session_id,
        project_name=project_name,
        project_path_hash=project_path_hash,
        deadline_ms=deadline_ms,
        mode=mode,
        spacing_x=spacing_x,
        spacing_y=spacing_y,
        target_width=target_width,
        seed=seed,
        preflight={},
        context_envelope={},
    )
    preflight, envelope = _validate_snapshot(payload, request)
    return BrokerRequest(
        request_id=request.request_id,
        session_id=request.session_id,
        project_name=request.project_name,
        project_path_hash=request.project_path_hash,
        deadline_ms=request.deadline_ms,
        mode=request.mode,
        spacing_x=request.spacing_x,
        spacing_y=request.spacing_y,
        target_width=request.target_width,
        seed=request.seed,
        preflight=preflight,
        context_envelope=envelope,
    )


def success_response(request_id: str, result: Mapping[str, Any], duration_ms: int) -> dict[str, Any]:
    return {
        "schemaVersion": PROTOCOL_SCHEMA_VERSION,
        "requestId": request_id,
        "ok": True,
        "readOnly": True,
        "algorithm": dict(ALGORITHM),
        "durationMs": int(duration_ms),
        "result": dict(result),
    }


def error_response(
    request_id: str | None,
    code: str,
    message: str,
    *,
    retryable: bool = False,
) -> dict[str, Any]:
    return {
        "schemaVersion": PROTOCOL_SCHEMA_VERSION,
        "requestId": request_id,
        "ok": False,
        "error": {
            "code": code,
            "message": sanitize_message(message),
            "retryable": bool(retryable),
        },
    }


def _check_plan(result: Mapping[str, Any]) -> None:
    plan = result.get("plan")
    if not isinstance(plan, dict):
        _fail(ERROR_PLAN_INVALID, "result.plan must be a JSON object.")
    missing = sorted(_PLAN_REQUIRED_KEYS - set(plan))
    if missing:
        _fail(ERROR_PLAN_INVALID, "result.plan is missing required fields: " + ", ".join(missing))
    positions = plan.get("positions")
    if not isinstance(positions, list):
        _fail(ERROR_PLAN_INVALID, "result.plan.positions must be an array.")
    seen: set[str] = set()
    for entry in positions:
        if not isinstance(entry, dict) or not _PLAN_POSITION_KEYS <= set(entry):
            _fail(ERROR_PLAN_INVALID, "result.plan.positions entries have an invalid shape.")
        if set(entry) - _PLAN_POSITION_ALLOWED_KEYS:
            _fail(ERROR_PLAN_INVALID, "result.plan.positions entries carry unsupported fields.")
        node_id = entry.get("nodeId")
        if not isinstance(node_id, str) or not node_id or node_id in seen:
            _fail(ERROR_PLAN_INVALID, "result.plan.positions must have unique nodeId strings.")
        seen.add(node_id)
        for axis in ("x", "y"):
            value = entry.get(axis)
            if not isinstance(value, int) or isinstance(value, bool):
                _fail(ERROR_PLAN_INVALID, f"result.plan.positions {axis} must be integers.")
            if abs(value) > MAX_COORDINATE:
                _fail(ERROR_PLAN_INVALID, "result.plan.positions contain out-of-range coordinates.")
        if "size" in entry:
            size = entry["size"]
            if not isinstance(size, dict) or set(size) != _PLAN_SIZE_KEYS:
                _fail(ERROR_PLAN_INVALID, "result.plan.positions size entries are invalid.")
    if plan.get("layoutHash") != result.get("layoutHash"):
        _fail(ERROR_PLAN_HASH_MISMATCH, "result.plan.layoutHash is not the result layoutHash.")
    if plan.get("baseGraphHash") != result.get("baseGraphHash"):
        _fail(ERROR_PLAN_INVALID, "result.plan.baseGraphHash is not the result baseGraphHash.")
    try:
        recomputed = recompute_layout_hash(plan)
    except Exception as exc:  # noqa: BLE001 - any failure here is a rejected plan
        raise BrokerProtocolError(
            ERROR_PLAN_INVALID, "The layout plan hash could not be recomputed."
        ) from exc
    if recomputed != result.get("layoutHash"):
        _fail(ERROR_PLAN_HASH_MISMATCH, "The layout plan hash is not canonical.")


def validate_preview_result(
    result: Any,
    preflight: Mapping[str, Any],
    *,
    token: str | None = None,
) -> dict[str, Any]:
    """Validate a ``build_preview`` result before accepting or sending it."""

    if not isinstance(result, dict):
        _fail(ERROR_PLAN_INVALID, "result must be a JSON object.")
    unknown = sorted(set(result) - _RESULT_KEYS)
    if unknown:
        _fail(
            ERROR_PLAN_INVALID,
            "result contains unsupported field(s): " + ", ".join(safe_key(item) for item in unknown[:4]),
        )
    missing = sorted(_RESULT_KEYS - set(result))
    if missing:
        _fail(ERROR_PLAN_INVALID, "result is missing required fields: " + ", ".join(missing))
    if result.get("schemaVersion") != PREVIEW_SCHEMA_VERSION:
        _fail(ERROR_PLAN_INVALID, "result.schemaVersion is not the preview schema.")
    if result.get("readOnly") is not True:
        _fail(ERROR_PLAN_INVALID, "result.readOnly must be true.")
    for flag in ("wroteBlueprint", "savedPackage", "openAssets"):
        if result.get(flag) is not False:
            _fail(ERROR_PLAN_INVALID, f"result.{flag} must be false.")
    if result.get("packageDirty") is not False:
        _fail(ERROR_PLAN_INVALID, "result.packageDirty must be false.")

    if result.get("assetPath") != preflight.get("assetPath"):
        _fail(ERROR_IDENTITY_MISMATCH, "result.assetPath does not match the snapshot.")
    if result.get("graphGuid") != preflight.get("graphGuid"):
        _fail(ERROR_IDENTITY_MISMATCH, "result.graphGuid does not match the snapshot.")
    if result.get("sessionId") != preflight.get("sessionId"):
        _fail(ERROR_IDENTITY_MISMATCH, "result.sessionId does not match the snapshot.")

    node_count = result.get("nodeCount")
    if not isinstance(node_count, int) or isinstance(node_count, bool) or node_count < 0:
        _fail(ERROR_PLAN_INVALID, "result.nodeCount must be a non-negative integer.")
    if node_count != preflight.get("nodeCount"):
        _fail(ERROR_IDENTITY_MISMATCH, "result.nodeCount does not match the snapshot.")
    moved_count = result.get("movedCount")
    if not isinstance(moved_count, int) or isinstance(moved_count, bool):
        _fail(ERROR_PLAN_INVALID, "result.movedCount must be an integer.")
    moved_ids = result.get("movedNodeIds")
    if not isinstance(moved_ids, list) or any(not isinstance(item, str) for item in moved_ids):
        _fail(ERROR_PLAN_INVALID, "result.movedNodeIds must be an array of strings.")
    if len(moved_ids) != moved_count:
        _fail(ERROR_PLAN_INVALID, "result.movedCount does not equal len(movedNodeIds).")
    if len(set(moved_ids)) != len(moved_ids):
        _fail(ERROR_PLAN_INVALID, "result.movedNodeIds contains duplicates.")

    positions = result.get("positions")
    if not isinstance(positions, list):
        _fail(ERROR_PLAN_INVALID, "result.positions must be an array.")
    if len(positions) != node_count:
        _fail(ERROR_PLAN_INVALID, "result.positions does not cover every node.")
    node_ids: set[str] = set()
    after: dict[str, tuple[int, int]] = {}
    for entry in positions:
        if not isinstance(entry, dict) or set(entry) != _POSITION_KEYS:
            _fail(ERROR_PLAN_INVALID, "result.positions entries have an invalid shape.")
        node_id = entry.get("nodeId")
        if not isinstance(node_id, str) or not node_id or node_id in node_ids:
            _fail(ERROR_PLAN_INVALID, "result.positions must have unique nodeId strings.")
        node_ids.add(node_id)
        for key in ("beforeX", "beforeY", "afterX", "afterY", "deltaX", "deltaY", "width", "height"):
            value = entry.get(key)
            if not isinstance(value, int) or isinstance(value, bool):
                _fail(ERROR_PLAN_INVALID, f"result.positions {key} must be integers.")
            if key in ("beforeX", "beforeY", "afterX", "afterY") and abs(value) > MAX_COORDINATE:
                _fail(ERROR_PLAN_INVALID, "result.positions contain out-of-range coordinates.")
        if entry["deltaX"] != entry["afterX"] - entry["beforeX"]:
            _fail(ERROR_PLAN_INVALID, "result.positions deltaX is inconsistent.")
        if entry["deltaY"] != entry["afterY"] - entry["beforeY"]:
            _fail(ERROR_PLAN_INVALID, "result.positions deltaY is inconsistent.")
        after[node_id] = (entry["afterX"], entry["afterY"])
    if not set(moved_ids) <= node_ids:
        _fail(ERROR_PLAN_INVALID, "result.movedNodeIds contains nodes outside the result.")

    layout_hash = result.get("layoutHash")
    if not isinstance(layout_hash, str) or not _LAYOUT_HASH_RE.match(layout_hash):
        _fail(ERROR_PLAN_INVALID, "result.layoutHash is not sha256:<64 lowercase hex>.")
    plan_id = result.get("planId")
    if not isinstance(plan_id, str) or not _PLAN_ID_RE.match(plan_id):
        _fail(ERROR_PLAN_INVALID, "result.planId has an invalid format.")
    base_hash = result.get("baseGraphHash")
    if not isinstance(base_hash, str) or not _SHA256_RE.match(base_hash):
        _fail(ERROR_PLAN_INVALID, "result.baseGraphHash is not sha256:<64 lowercase hex>.")

    _check_plan(result)
    plan = result["plan"]
    plan_positions = {entry["nodeId"]: (entry["x"], entry["y"]) for entry in plan["positions"]}
    if set(plan_positions) != node_ids:
        _fail(ERROR_PLAN_INVALID, "result.plan does not cover the same node set.")
    for node_id, coordinates in plan_positions.items():
        if after.get(node_id) != coordinates:
            _fail(ERROR_PLAN_INVALID, "result.plan coordinates disagree with result.positions.")
    for key in ("metrics", "warnings", "islands", "sccs", "scopeNodeIds"):
        value = result.get(key)
        if not isinstance(value, (dict, list)):
            _fail(ERROR_PLAN_INVALID, f"result.{key} has an invalid type.")

    if token:
        serialized = json.dumps(result, ensure_ascii=False, sort_keys=True)
        if token in serialized:
            _fail(ERROR_RESPONSE_INVALID, "The response contains the session token.")
    return dict(result)


def validate_plan_id(
    result: Mapping[str, Any],
    *,
    spacing_x: int,
    spacing_y: int,
    target_width: int,
    seed: int,
) -> str:
    """Reject a forged ``planId`` even when the rest of the plan is canonical.

    ``layoutHash`` covers positions only; ``planId`` additionally commits to the
    request options and packing.  M1 originally validated only ``layoutHash``,
    so a well-formed forged ``planId`` could pass.  Both the broker self-check
    and the client call this function with the original request options.
    """

    if not isinstance(result, Mapping):
        _fail(ERROR_PLAN_INVALID, "The preview result is not a JSON object.")
    try:
        expected = plan_id_from_result(
            result,
            spacing_x=spacing_x,
            spacing_y=spacing_y,
            target_width=target_width,
            seed=seed,
        )
    except PlanIdError as exc:
        raise BrokerProtocolError(
            ERROR_PLAN_INVALID, "The layout planId could not be recomputed."
        ) from exc
    actual = result.get("planId")
    if actual != expected:
        _fail(
            ERROR_PLAN_ID_MISMATCH,
            "The layout planId is not canonical for the request options.",
        )
    return expected


def validate_response(
    payload: Any,
    request: BrokerRequest,
    *,
    token: str | None = None,
) -> dict[str, Any]:
    """Validate a broker response for exactly one request and return its result."""

    if not isinstance(payload, dict):
        _fail(ERROR_RESPONSE_INVALID, "The broker response must be a JSON object.")
    schema_version = payload.get("schemaVersion")
    if schema_version != PROTOCOL_SCHEMA_VERSION:
        _fail(ERROR_RESPONSE_INVALID, "The broker response schemaVersion is unsupported.")
    if payload.get("requestId") != request.request_id:
        _fail(ERROR_RESPONSE_INVALID, "The broker response requestId does not match the request.")
    if not isinstance(payload.get("ok"), bool):
        _fail(ERROR_RESPONSE_INVALID, "The broker response ok field must be a boolean.")
    if payload.get("ok") is False:
        error = payload.get("error")
        if set(payload) != _ERROR_KEYS or not isinstance(error, dict):
            _fail(ERROR_RESPONSE_INVALID, "The broker error response has an invalid shape.")
        if set(error) != _ERROR_OBJECT_KEYS:
            _fail(ERROR_RESPONSE_INVALID, "The broker error object has an invalid shape.")
        code = error.get("code")
        message = error.get("message")
        if not isinstance(code, str) or code not in CLIENT_ERROR_CODES:
            _fail(ERROR_RESPONSE_INVALID, "The broker error code is not recognized.")
        if not isinstance(message, str) or len(message) > 400:
            _fail(ERROR_RESPONSE_INVALID, "The broker error message is invalid.")
        if not isinstance(error.get("retryable"), bool):
            _fail(ERROR_RESPONSE_INVALID, "The broker error retryable flag is invalid.")
        if token and token in json.dumps(payload, ensure_ascii=False):
            _fail(ERROR_RESPONSE_INVALID, "The broker response contains the session token.")
        raise BrokerProtocolError(code, sanitize_message(message, token), retryable=error["retryable"])

    if set(payload) != _SUCCESS_KEYS:
        _fail(ERROR_RESPONSE_INVALID, "The broker success response has an invalid shape.")
    if payload.get("readOnly") is not True:
        _fail(ERROR_RESPONSE_INVALID, "The broker success response must be readOnly.")
    if payload.get("algorithm") != ALGORITHM:
        _fail(ERROR_VERSION_UNSUPPORTED, "The broker response algorithm does not match.")
    duration_ms = payload.get("durationMs")
    if (
        not isinstance(duration_ms, int)
        or isinstance(duration_ms, bool)
        or not 0 <= duration_ms <= MAX_DEADLINE_MS + 1000
    ):
        _fail(ERROR_RESPONSE_INVALID, "The broker durationMs is invalid.")
    if token and token in json.dumps(payload, ensure_ascii=False):
        _fail(ERROR_RESPONSE_INVALID, "The broker response contains the session token.")
    accepted = validate_preview_result(payload.get("result"), request.preflight, token=token)
    validate_plan_id(
        accepted,
        spacing_x=request.spacing_x,
        spacing_y=request.spacing_y,
        target_width=request.target_width,
        seed=request.seed,
    )
    return accepted


def build_project_path_hash(project_path: str) -> str:
    """Mirror EditorBridge's ``NormalizeProjectPath`` + SHA-1 lowercase hash."""

    import hashlib
    import ntpath
    import os

    normalized = os.path.abspath(project_path)
    if os.name == "nt":
        normalized = ntpath.normpath(normalized)
    normalized = normalized.replace("\\", "/").lower()
    digest = hashlib.sha1(normalized.encode("utf-8"), usedforsecurity=False).hexdigest()
    return f"sha1:{digest}"


def new_request_id() -> str:
    return str(uuid.uuid4())


__all__ = [
    "ALGORITHM",
    "ALGORITHM_ID",
    "ALGORITHM_VERSION",
    "BROKER_ERROR_CODES",
    "BROKER_VERSION",
    "BrokerProtocolError",
    "BrokerRequest",
    "CLIENT_ERROR_CODES",
    "DEFAULT_RATE_LIMIT",
    "DESCRIPTOR_FILENAME",
    "DESCRIPTOR_MAX_AGE_SECONDS",
    "DESCRIPTOR_SCHEMA_VERSION",
    "ERROR_BUSY",
    "ERROR_CANCELLED",
    "ERROR_DESCRIPTOR_INVALID",
    "ERROR_DESCRIPTOR_MISSING",
    "ERROR_DUPLICATE_REQUEST",
    "ERROR_IDENTITY_MISMATCH",
    "ERROR_PLAN_HASH_MISMATCH",
    "ERROR_PLAN_ID_MISMATCH",
    "ERROR_PLAN_INVALID",
    "ERROR_PROJECT_MISMATCH",
    "ERROR_RATE_LIMITED",
    "ERROR_REQUEST_INVALID",
    "ERROR_REQUEST_TOO_LARGE",
    "ERROR_RESPONSE_INVALID",
    "ERROR_RESPONSE_TOO_LARGE",
    "ERROR_TIMEOUT",
    "ERROR_TOKEN_REJECTED",
    "ERROR_UNSUPPORTED_FIELD",
    "ERROR_VERSION_UNSUPPORTED",
    "ERROR_WORKER_FAILED",
    "MAX_CONCURRENT_JOBS",
    "MAX_DEADLINE_MS",
    "MAX_MESSAGE_BYTES",
    "MAX_TOKEN_HEX",
    "MIN_DEADLINE_MS",
    "MIN_TOKEN_HEX",
    "PREVIEW_SCHEMA_VERSION",
    "PROTOCOL_SCHEMA_VERSION",
    "RATE_LIMIT_WINDOW_SECONDS",
    "build_project_path_hash",
    "decode_message",
    "echo_request_id",
    "encode_message",
    "error_response",
    "new_request_id",
    "safe_key",
    "sanitize_message",
    "success_response",
    "validate_preview_result",
    "validate_plan_id",
    "validate_request",
    "validate_response",
]
