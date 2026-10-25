"""Bounded, read-only offline GraphPatch preview MCP tool.

This tool validates a GraphPatch 1.x document and a TypedGraph document, then
returns the pure in-memory preview. It never opens Unreal, the Editor Bridge, a
transaction, a Change Set, or a file; it never saves or applies a patch.
"""

from __future__ import annotations

import json
from typing import Any

from .graph_contracts import preview_graph_patch, validate_contract_document

GRAPH_PATCH_PREVIEW_TOOL_NAME = "ue_preview_graph_patch"
GRAPH_PATCH_PREVIEW_SCHEMA_VERSION = "ueak.mcp.graphpatch-preview/1.0"

MAX_PATCH_BYTES = 256 * 1024
MAX_GRAPH_BYTES = 2 * 1024 * 1024
MAX_DESCRIPTORS_BYTES = 2 * 1024 * 1024
MAX_ENVIRONMENT_BYTES = 64 * 1024
MAX_TOTAL_BYTES = 6 * 1024 * 1024
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_DEPTH = 64
MAX_OPERATIONS = 300
MAX_GRAPH_NODES = 5000
MAX_DESCRIPTORS = 256

# Every errorCode this adapter emits must resolve in
# graph_contracts/schemas/error-codes.json.  Transport-level refusals reuse the
# registered contract codes for the same condition -- the precise cause travels
# in ``details`` so the vocabulary stays single-sourced:
#   * malformed / non-JSON / schema-invalid input -> EC-SCHEMA-SUBSET
#   * byte, depth and count limits               -> GP-LIMITS
#   * deleteNode is not a V1 operation           -> graphpatch-operation-unknown
# The two preview-only failures have no registered equivalent and are registered
# in the same table (they are reachable only through this bounded adapter).
SCHEMA_INVALID_CODE = "contract-schema-invalid"
LIMIT_EXCEEDED_CODE = "graphpatch-limit-exceeded"
OPERATION_UNKNOWN_CODE = "graphpatch-operation-unknown"
PREVIEW_NOT_JSON_CODE = "graphpatch-preview-not-json"
PREVIEW_FAILED_CODE = "graphpatch-preview-failed"


class GraphPatchPreviewError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        diagnostics: list[dict[str, Any]] | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.diagnostics = diagnostics or []
        self.details = details or {}


def _depth_details(label: str) -> dict[str, Any]:
    return {"argument": label, "limitName": "maxDepth", "limit": MAX_DEPTH}


def _json_text(value: Any, label: str) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except RecursionError as exc:
        raise GraphPatchPreviewError(
            LIMIT_EXCEEDED_CODE,
            f"{label} exceeds the maximum JSON depth of {MAX_DEPTH}.",
            details=_depth_details(label),
        ) from exc
    except (TypeError, ValueError) as exc:
        raise GraphPatchPreviewError(
            SCHEMA_INVALID_CODE,
            f"{label} must be a JSON value (no NaN, Infinity, or custom objects).",
            details={"argument": label, "reason": "not-json"},
        ) from exc


def _check_depth(value: Any, label: str) -> None:
    stack: list[tuple[Any, int]] = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        if depth > MAX_DEPTH:
            raise GraphPatchPreviewError(
                LIMIT_EXCEEDED_CODE,
                f"{label} exceeds the maximum JSON depth of {MAX_DEPTH}.",
                details=_depth_details(label),
            )
        if isinstance(item, dict):
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)


def _measure(value: Any, label: str, limit: int) -> int:
    text = _json_text(value, label)
    size = len(text.encode("utf-8"))
    if size > limit:
        raise GraphPatchPreviewError(
            LIMIT_EXCEEDED_CODE,
            f"{label} exceeds the {limit}-byte limit.",
            details={
                "argument": label,
                "limitName": "maxBytes",
                "limit": limit,
                "actualBytes": size,
            },
        )
    _check_depth(value, label)
    return size


def _validate(kind: str, document: Any) -> dict[str, Any]:
    try:
        result = validate_contract_document(kind, document)
    except Exception as exc:  # schema/resource failures must stay structured
        raise GraphPatchPreviewError(
            SCHEMA_INVALID_CODE,
            f"{kind} validation could not be completed.",
            details={"document": kind, "reason": "validation-unavailable"},
        ) from exc
    if not result.get("valid"):
        diagnostics = list(result.get("diagnostics") or [])
        raise GraphPatchPreviewError(
            SCHEMA_INVALID_CODE,
            f"{kind} does not satisfy the packaged contract schema.",
            diagnostics[:20],
            details={"document": kind, "reason": "schema-invalid"},
        )
    return result


def _error(
    code: str,
    message: str,
    diagnostics: list[dict[str, Any]] | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schemaVersion": GRAPH_PATCH_PREVIEW_SCHEMA_VERSION,
        "tool": GRAPH_PATCH_PREVIEW_TOOL_NAME,
        "ok": False,
        "readOnly": True,
        "previewOnly": True,
        "writeAuthorized": False,
        "editorVerified": False,
        "previewStatus": "rejected",
        "errorCode": code,
        "message": message,
        "diagnostics": list(diagnostics or [])[:20],
        "details": dict(details or {}),
        "writeEffects": {
            "memoryModified": False,
            "diskModified": False,
            "transactionOpened": False,
            "dirtyChanged": False,
            "idempotencyReserved": False,
        },
    }


def preview_graph_patch_document(
    patch: dict[str, Any],
    graph: dict[str, Any],
    environment: dict[str, Any] | None = None,
    descriptors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate and preview one GraphPatch without any side effect."""

    if not isinstance(patch, dict):
        return _error(
            SCHEMA_INVALID_CODE,
            "patch must be a JSON object.",
            details={"argument": "patch", "expected": "object"},
        )
    if not isinstance(graph, dict):
        return _error(
            SCHEMA_INVALID_CODE,
            "graph must be a JSON object.",
            details={"argument": "graph", "expected": "object"},
        )
    if environment is not None and not isinstance(environment, dict):
        return _error(
            SCHEMA_INVALID_CODE,
            "environment must be a JSON object.",
            details={"argument": "environment", "expected": "object"},
        )
    if descriptors is not None and (
        not isinstance(descriptors, list)
        or any(not isinstance(item, dict) for item in descriptors)
    ):
        return _error(
            SCHEMA_INVALID_CODE,
            "descriptors must be an array of JSON objects.",
            details={"argument": "descriptors", "expected": "array-of-objects"},
        )

    try:
        patch_bytes = _measure(patch, "patch", MAX_PATCH_BYTES)
        graph_bytes = _measure(graph, "graph", MAX_GRAPH_BYTES)
        env_bytes = _measure(environment, "environment", MAX_ENVIRONMENT_BYTES)
        descriptor_bytes = _measure(descriptors, "descriptors", MAX_DESCRIPTORS_BYTES)
        total_bytes = patch_bytes + graph_bytes + env_bytes + descriptor_bytes
        if total_bytes > MAX_TOTAL_BYTES:
            raise GraphPatchPreviewError(
                LIMIT_EXCEEDED_CODE,
                f"combined input exceeds the {MAX_TOTAL_BYTES}-byte limit.",
                details={
                    "argument": "combined",
                    "limitName": "maxTotalBytes",
                    "limit": MAX_TOTAL_BYTES,
                    "actualBytes": total_bytes,
                },
            )

        operations = patch.get("operations")
        if not isinstance(operations, list):
            raise GraphPatchPreviewError(
                SCHEMA_INVALID_CODE,
                "patch.operations must be an array.",
                details={"argument": "patch.operations", "expected": "array"},
            )
        if len(operations) > MAX_OPERATIONS:
            raise GraphPatchPreviewError(
                LIMIT_EXCEEDED_CODE,
                f"patch.operations exceeds the limit of {MAX_OPERATIONS}.",
                details={
                    "argument": "patch.operations",
                    "limitName": "maxOperations",
                    "limit": MAX_OPERATIONS,
                    "actual": len(operations),
                },
            )
        if any(
            isinstance(operation, dict) and operation.get("type") == "deleteNode"
            for operation in operations
        ):
            raise GraphPatchPreviewError(
                OPERATION_UNKNOWN_CODE,
                "deleteNode is not part of GraphPatch V1.",
                details={"operation": "deleteNode", "reason": "not-in-v1"},
            )

        graph_nodes = graph.get("nodes")
        if isinstance(graph_nodes, list) and len(graph_nodes) > MAX_GRAPH_NODES:
            raise GraphPatchPreviewError(
                LIMIT_EXCEEDED_CODE,
                f"graph.nodes exceeds the limit of {MAX_GRAPH_NODES}.",
                details={
                    "argument": "graph.nodes",
                    "limitName": "maxGraphNodes",
                    "limit": MAX_GRAPH_NODES,
                    "actual": len(graph_nodes),
                },
            )
        if descriptors is not None and len(descriptors) > MAX_DESCRIPTORS:
            raise GraphPatchPreviewError(
                LIMIT_EXCEEDED_CODE,
                f"descriptors exceeds the limit of {MAX_DESCRIPTORS}.",
                details={
                    "argument": "descriptors",
                    "limitName": "maxDescriptors",
                    "limit": MAX_DESCRIPTORS,
                    "actual": len(descriptors),
                },
            )

        _validate("graphpatch", patch)
        _validate("typedgraph", graph)

        if descriptors is not None:
            for index, descriptor in enumerate(descriptors):
                node = validate_contract_document("node-descriptor", descriptor)
                if node.get("valid"):
                    continue
                operation = validate_contract_document("operation-descriptor", descriptor)
                if operation.get("valid"):
                    continue
                diagnostics = list(node.get("diagnostics") or [])[:20]
                raise GraphPatchPreviewError(
                    SCHEMA_INVALID_CODE,
                    f"descriptors[{index}] matches neither node nor operation descriptor.",
                    diagnostics,
                    details={
                        "argument": f"descriptors[{index}]",
                        "reason": "not-a-node-or-operation-descriptor",
                    },
                )

        result = preview_graph_patch(patch, graph, environment, descriptors)
        status = str(result.get("status") or "")
        result["editorVerified"] = False
        result["writeAuthorized"] = False
        payload = {
            "schemaVersion": GRAPH_PATCH_PREVIEW_SCHEMA_VERSION,
            "tool": GRAPH_PATCH_PREVIEW_TOOL_NAME,
            "ok": status == "previewed",
            "readOnly": True,
            "previewOnly": True,
            "writeAuthorized": False,
            "editorVerified": False,
            "previewStatus": status,
            "errorCode": str(result.get("errorCode") or ""),
            "result": result,
        }
        try:
            output_size = len(
                json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
            )
        except (TypeError, ValueError) as exc:
            raise GraphPatchPreviewError(
                PREVIEW_NOT_JSON_CODE,
                "the preview result is not a JSON value.",
                details={"argument": "previewOutput", "reason": "not-json"},
            ) from exc
        if output_size > MAX_OUTPUT_BYTES:
            raise GraphPatchPreviewError(
                LIMIT_EXCEEDED_CODE,
                f"the preview result exceeds the {MAX_OUTPUT_BYTES}-byte limit.",
                details={
                    "argument": "previewOutput",
                    "limitName": "maxBytes",
                    "limit": MAX_OUTPUT_BYTES,
                    "actualBytes": output_size,
                },
            )
        return payload
    except GraphPatchPreviewError as exc:
        return _error(exc.code, exc.message, exc.diagnostics, exc.details)
    except Exception:
        return _error(
            PREVIEW_FAILED_CODE,
            "the preview failed safely; no write was attempted.",
            details={"reason": "unexpected-error"},
        )


def register_graph_patch_tools(*, server: Any, read_annotations: Any) -> None:
    @server.tool(annotations=read_annotations)
    def ue_preview_graph_patch(
        patch: dict[str, Any],
        graph: dict[str, Any],
        environment: dict[str, Any] | None = None,
        descriptors: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Preview a GraphPatch against a TypedGraph in memory; never writes, saves, or applies."""
        return preview_graph_patch_document(patch, graph, environment, descriptors)
