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


class GraphPatchPreviewError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        diagnostics: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.diagnostics = diagnostics or []


def _json_text(value: Any, label: str) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except RecursionError as exc:
        raise GraphPatchPreviewError(
            f"graphpatch-{label}-too-deep",
            f"{label} exceeds the maximum JSON depth of {MAX_DEPTH}.",
        ) from exc
    except (TypeError, ValueError) as exc:
        raise GraphPatchPreviewError(
            f"graphpatch-{label}-not-json",
            f"{label} must be a JSON value (no NaN, Infinity, or custom objects).",
        ) from exc


def _check_depth(value: Any, label: str) -> None:
    stack: list[tuple[Any, int]] = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        if depth > MAX_DEPTH:
            raise GraphPatchPreviewError(
                f"graphpatch-{label}-too-deep",
                f"{label} exceeds the maximum JSON depth of {MAX_DEPTH}.",
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
            f"graphpatch-{label}-too-large",
            f"{label} exceeds the {limit}-byte limit.",
        )
    _check_depth(value, label)
    return size


def _validate(kind: str, document: Any) -> dict[str, Any]:
    try:
        result = validate_contract_document(kind, document)
    except Exception as exc:  # schema/resource failures must stay structured
        raise GraphPatchPreviewError(
            f"{kind}-validation-failed",
            f"{kind} validation could not be completed.",
        ) from exc
    if not result.get("valid"):
        diagnostics = list(result.get("diagnostics") or [])
        raise GraphPatchPreviewError(
            f"{kind}-schema-invalid",
            f"{kind} does not satisfy the packaged contract schema.",
            diagnostics[:20],
        )
    return result


def _error(
    code: str,
    message: str,
    diagnostics: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "schemaVersion": GRAPH_PATCH_PREVIEW_SCHEMA_VERSION,
        "tool": GRAPH_PATCH_PREVIEW_TOOL_NAME,
        "ok": False,
        "readOnly": True,
        "previewOnly": True,
        "writeAuthorized": False,
        "editorVerified": False,
        "errorCode": code,
        "message": message,
        "diagnostics": list(diagnostics or [])[:20],
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
        return _error("graphpatch-patch-invalid", "patch must be a JSON object.")
    if not isinstance(graph, dict):
        return _error("graphpatch-graph-invalid", "graph must be a JSON object.")
    if environment is not None and not isinstance(environment, dict):
        return _error("graphpatch-environment-invalid", "environment must be a JSON object.")
    if descriptors is not None and (
        not isinstance(descriptors, list)
        or any(not isinstance(item, dict) for item in descriptors)
    ):
        return _error(
            "graphpatch-descriptors-invalid",
            "descriptors must be an array of JSON objects.",
        )

    try:
        patch_bytes = _measure(patch, "patch", MAX_PATCH_BYTES)
        graph_bytes = _measure(graph, "graph", MAX_GRAPH_BYTES)
        env_bytes = _measure(environment, "environment", MAX_ENVIRONMENT_BYTES)
        descriptor_bytes = _measure(descriptors, "descriptors", MAX_DESCRIPTORS_BYTES)
        if patch_bytes + graph_bytes + env_bytes + descriptor_bytes > MAX_TOTAL_BYTES:
            raise GraphPatchPreviewError(
                "graphpatch-payload-too-large",
                f"combined input exceeds the {MAX_TOTAL_BYTES}-byte limit.",
            )

        operations = patch.get("operations")
        if not isinstance(operations, list):
            raise GraphPatchPreviewError(
                "graphpatch-operations-invalid",
                "patch.operations must be an array.",
            )
        if len(operations) > MAX_OPERATIONS:
            raise GraphPatchPreviewError(
                "graphpatch-too-many-operations",
                f"patch.operations exceeds the limit of {MAX_OPERATIONS}.",
            )
        if any(
            isinstance(operation, dict) and operation.get("type") == "deleteNode"
            for operation in operations
        ):
            raise GraphPatchPreviewError(
                "graphpatch-delete-node-unsupported",
                "deleteNode is not part of GraphPatch V1.",
            )

        graph_nodes = graph.get("nodes")
        if isinstance(graph_nodes, list) and len(graph_nodes) > MAX_GRAPH_NODES:
            raise GraphPatchPreviewError(
                "graphpatch-too-many-graph-nodes",
                f"graph.nodes exceeds the limit of {MAX_GRAPH_NODES}.",
            )
        if descriptors is not None and len(descriptors) > MAX_DESCRIPTORS:
            raise GraphPatchPreviewError(
                "graphpatch-too-many-descriptors",
                f"descriptors exceeds the limit of {MAX_DESCRIPTORS}.",
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
                    "graphpatch-descriptor-invalid",
                    f"descriptors[{index}] matches neither node nor operation descriptor.",
                    diagnostics,
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
                "graphpatch-preview-not-json",
                "the preview result is not a JSON value.",
            ) from exc
        if output_size > MAX_OUTPUT_BYTES:
            raise GraphPatchPreviewError(
                "graphpatch-preview-output-too-large",
                f"the preview result exceeds the {MAX_OUTPUT_BYTES}-byte limit.",
            )
        return payload
    except GraphPatchPreviewError as exc:
        return _error(exc.code, exc.message, exc.diagnostics)
    except Exception:
        return _error(
            "graphpatch-preview-failed",
            "the preview failed safely; no write was attempted.",
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
