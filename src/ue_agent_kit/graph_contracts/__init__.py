"""Read-only GraphPatch and layout contract entry points for UEAgentKit 0.9.

These functions validate/preview data in memory; they NEVER run Unreal, open
transactions, save assets, or change the existing ChangeSet 2.0 writer.
The G0.3 approved contract remains a candidate until real UE verification.
``preview_graph_layout`` is the offline LayoutPlan API,
``typed_graph_from_blueprint_context`` is the strict canonical-export importer,
and ``build_graph_layout_apply_request`` is the client-side fail-closed request
builder for the internal C++ position-only handler.  None of them writes an
asset or proves Editor acceptance.
"""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from importlib.resources import files
import json
from typing import Any, Iterable

from . import graph_patch
from .blueprint_context import BlueprintContextImportError, typed_graph_from_blueprint_context
from .layout import LayoutPlan, preview_graph_layout
from .layout_apply import GraphLayoutApplyError, build_graph_layout_apply_request
from .schema import SchemaError, check_schema, negotiate_version, validate_document

CONTRACT_SCHEMAS = {
    "graphpatch": ("graphpatch.schema.json", "ueak.graphpatch"),
    "typedgraph": ("typedgraph.schema.json", "ueak.typedgraph"),
    "layout-plan": ("layout-plan.schema.json", "ueak.layoutplan"),
    "layout-preflight": ("graph-layout-preflight.schema.json", "ueak.graphlayoutpreflight"),
    "layout-apply": ("graph-layout-apply.schema.json", "ueak.graphlayoutapply"),
    "node-descriptor": ("node-descriptor.schema.json", "ueak.node-descriptor"),
    "operation-descriptor": ("operation-descriptor.schema.json", "ueak.operation"),
    "change-set-idempotency": ("change-set-idempotency.schema.json", "ueak.change-set-idempotency"),
}


@lru_cache(maxsize=len(CONTRACT_SCHEMAS))
def _schema_for(kind: str) -> dict[str, Any]:
    filename, _ = CONTRACT_SCHEMAS[kind]
    resource = files("ue_agent_kit.graph_contracts").joinpath("schemas", filename)
    schema = json.loads(resource.read_text(encoding="utf-8"))
    check_schema(schema)
    return schema


def validate_contract_document(kind: str, document: Any) -> dict[str, Any]:
    """Validate a candidate contract with stdlib only; never write to disk.

    ``kind`` is an allowlisted name, not a filename.  Diagnostics are ordinary
    contract errors; consumers must not treat success as UE Editor acceptance.
    """
    if kind not in CONTRACT_SCHEMAS:
        raise ValueError(f"Unknown graph contract kind: {kind!r}")
    schema = _schema_for(kind)
    version = None
    issues: list[dict[str, Any]] = []
    if isinstance(document, dict):
        try:
            version = negotiate_version(
                document.get("schemaVersion", ""),
                namespace=CONTRACT_SCHEMAS[kind][1],
                unsupported_code=f"{kind}-schema-version-unsupported",
            )
        except SchemaError as exc:
            issues.append({"code": exc.code, "severity": "error", "message": exc.message, "path": exc.path})
    issues.extend(validate_document(document, schema, strict=not bool(version and version.minor_newer)))
    if version is not None:
        issues.extend(version.diagnostics)
    return {
        "kind": kind,
        "valid": not any(item.get("severity", "error") == "error" for item in issues),
        "diagnostics": issues,
        "editorVerified": False,
        "writeAuthorized": False,
    }


def preview_graph_patch(
    patch: dict[str, Any],
    graph: dict[str, Any],
    environment: graph_patch.Environment | dict[str, Any] | None = None,
    descriptors: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Pure GraphPatch 1.0 preview, including G0.3 profile and policy gates.

    No Unreal commands, no persistent idempotency reservations and no Save.
    An Editor executor requires separate implementation and authorization.
    """
    safe_descriptors = deepcopy(list(descriptors)) if descriptors is not None else None
    result = graph_patch.preview_patch(deepcopy(patch), deepcopy(graph), environment, safe_descriptors)
    result["editorVerified"] = False
    result["writeAuthorized"] = False
    return result


__all__ = [
    "BlueprintContextImportError",
    "CONTRACT_SCHEMAS",
    "GraphLayoutApplyError",
    "LayoutPlan",
    "build_graph_layout_apply_request",
    "preview_graph_layout",
    "preview_graph_patch",
    "typed_graph_from_blueprint_context",
    "validate_contract_document",
]
