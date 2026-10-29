"""M2.0 offline conformance checks for the layout broker wire contract.

This module is additive: M1 production code does not import it and its behavior
is unchanged.  It provides machine-readable schema validation plus the semantic
checks JSON Schema cannot express (identity, canonical hashes/planId, 1 MiB
bound, token absence, plan shape).

``jsonschema`` is an optional development dependency; importing this module is
safe without it, but schema validation raises ``ConformanceUnavailable``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..graph_contracts.layout import recompute_layout_hash
from . import protocol
from .planid import PlanIdError, plan_id_from_result

SCHEMA_DIR = Path(__file__).resolve().parents[3] / "spec" / "native-tools" / "m2"
REQUEST_SCHEMA = "layoutbroker-request-1.0.schema.json"
RESPONSE_SCHEMA = "layoutbroker-response-1.0.schema.json"
DESCRIPTOR_SCHEMA = "layoutbroker-descriptor-1.0.schema.json"
REQUEST_V11_SCHEMA = "layoutbroker-request-1.1-draft.schema.json"


class ConformanceError(AssertionError):
    """The document does not conform to the M2.0 offline contract."""


class ConformanceUnavailable(RuntimeError):
    """The optional ``jsonschema`` development dependency is unavailable."""


def load_schema(name: str) -> dict[str, Any]:
    path = SCHEMA_DIR / name
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConformanceError(f"schema file is not readable: {name}") from exc
    except json.JSONDecodeError as exc:
        raise ConformanceError(f"schema file is not valid JSON: {name}") from exc
    if not isinstance(document, dict):
        raise ConformanceError(f"schema file is not a JSON object: {name}")
    return document


def _validator(schema_name: str):
    try:
        from jsonschema import Draft202012Validator, FormatChecker
    except ImportError as exc:  # pragma: no cover - developer environment only
        raise ConformanceUnavailable(
            "jsonschema is required for M2 conformance validation "
            "(install the 'dev' extra)"
        ) from exc
    schema = load_schema(schema_name)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def schema_errors(document: Any, schema_name: str) -> list[str]:
    """Return sorted human-readable schema violations (empty means valid)."""

    validator = _validator(schema_name)
    messages: list[str] = []
    for error in sorted(validator.iter_errors(document), key=lambda item: list(item.path)):
        path = "$" + "".join(f"[{part!r}]" for part in error.path)
        messages.append(f"{path}: {error.message}")
    return messages


def encoded_size(document: Any) -> int:
    payload = json.dumps(
        document,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return len(payload) + 1  # NDJSON newline


def check_message_size(document: Any, *, limit: int = protocol.MAX_MESSAGE_BYTES) -> int:
    size = encoded_size(document)
    if size > limit:
        raise ConformanceError(
            f"encoded message is {size} bytes, above the {limit} byte limit"
        )
    return size


def recompute_plan_id(
    result: Mapping[str, Any],
    *,
    spacing_x: int,
    spacing_y: int,
    target_width: int,
    seed: int,
) -> str:
    """Delegate to the shared canonical helper (no second implementation)."""

    try:
        return plan_id_from_result(
            result,
            spacing_x=spacing_x,
            spacing_y=spacing_y,
            target_width=target_width,
            seed=seed,
        )
    except PlanIdError as exc:
        raise ConformanceError(str(exc)) from exc


def validate_preview_result_conformance(
    result: Any,
    request: protocol.BrokerRequest,
    *,
    token: str | None = None,
) -> dict[str, Any]:
    """Validate an M1 preview result plus M2's canonical planId requirement."""

    accepted = protocol.validate_preview_result(result, request.preflight, token=token)
    expected_hash = recompute_layout_hash(accepted["plan"])
    if accepted["layoutHash"] != expected_hash:
        raise ConformanceError("layoutHash is not the canonical recomputation")
    expected_plan_id = recompute_plan_id(
        accepted,
        spacing_x=request.spacing_x,
        spacing_y=request.spacing_y,
        target_width=request.target_width,
        seed=request.seed,
    )
    if accepted["planId"] != expected_plan_id:
        raise ConformanceError(
            f"planId mismatch: response={accepted['planId']} expected={expected_plan_id}"
        )
    check_message_size(accepted)
    return accepted


def validate_request_conformance(
    request: Any,
    *,
    project_name: str,
    project_path_hash: str,
    token: str,
    expected_session_id: str | None = None,
) -> protocol.BrokerRequest:
    """Schema-check and semantically validate one broker request."""

    errors = schema_errors(request, REQUEST_SCHEMA)
    if errors:
        raise ConformanceError("request schema violations: " + "; ".join(errors))
    validated = protocol.validate_request(
        request,
        project_name=project_name,
        project_path_hash=project_path_hash,
        token=token,
        expected_session_id=expected_session_id,
    )
    check_message_size(request)
    return validated


def validate_response_conformance(
    payload: Any,
    request: protocol.BrokerRequest,
    *,
    token: str | None = None,
) -> dict[str, Any]:
    """Schema-check a wire response, then run M1 + M2 semantic validation."""

    errors = schema_errors(payload, RESPONSE_SCHEMA)
    if errors:
        raise ConformanceError("response schema violations: " + "; ".join(errors))
    check_message_size(payload)
    result = protocol.validate_response(payload, request, token=token)
    return validate_preview_result_conformance(result, request, token=token)


def validate_descriptor_conformance(
    descriptor: Any,
    *,
    project_name: str | None = None,
    project_path_hash: str | None = None,
) -> dict[str, Any]:
    errors = schema_errors(descriptor, DESCRIPTOR_SCHEMA)
    if errors:
        raise ConformanceError("descriptor schema violations: " + "; ".join(errors))
    from . import descriptor as descriptor_module

    return descriptor_module.validate_descriptor(
        descriptor,
        project_name=project_name,
        project_path_hash=project_path_hash,
    )


__all__ = [
    "ConformanceError",
    "ConformanceUnavailable",
    "DESCRIPTOR_SCHEMA",
    "REQUEST_SCHEMA",
    "REQUEST_V11_SCHEMA",
    "RESPONSE_SCHEMA",
    "SCHEMA_DIR",
    "check_message_size",
    "encoded_size",
    "load_schema",
    "recompute_plan_id",
    "schema_errors",
    "validate_descriptor_conformance",
    "validate_preview_result_conformance",
    "validate_request_conformance",
    "validate_response_conformance",
]
