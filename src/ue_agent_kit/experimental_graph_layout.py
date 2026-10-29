"""Explicitly opted-in, position-only Blueprint graph layout apply/save.

This is the experimental *general* route between the read-only preview tool and
the existing P5 transaction engine.  It is intentionally not part of the public
tool registry and is never enabled by default:

* the MCP server operator must pass ``--enable-experimental-graph-layout-apply``;
* the running Editor project must opt in with
  ``[UEAgentKit] ExperimentalGraphLayoutApply=True`` and therefore advertise the
  ``editor.applyBlueprintGraphLayoutExperimental`` capability;
* the caller must pass the exact preview result plus the exact confirmation
  phrase bound to that plan.

The existing Editor-side gates (focused graph identity, clean package, stopped
PIE, current revision/fingerprint, one transaction, undo/redo readback) remain
authoritative.  This module only builds and validates the canonical request so
an agent cannot hand-write a looser one.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from .editor_bridge import LiveEditorError
from .graph_contracts.layout import recompute_layout_hash

APPLY_TOOL_NAME = "ue_apply_blueprint_graph_layout_experimental"
SAVE_TOOL_NAME = "ue_save_blueprint_graph_layout_experimental"
APPLY_METHOD = "editor.applyBlueprintGraphLayoutExperimental"
SAVE_METHOD = "editor.saveBlueprintGraphLayoutExperimental"
APPLY_CAPABILITY = APPLY_METHOD
SAVE_CAPABILITY = SAVE_METHOD
APPLY_CONFIRMATION_PREFIX = "APPLY EXPERIMENTAL LAYOUT "
SAVE_CONFIRMATION_PREFIX = "SAVE EXPERIMENTAL LAYOUT "
PREVIEW_SCHEMA = "ueak.graphlayoutpreview/1.0"
APPLY_SCHEMA = "ueak.graphlayoutapply/1.0"
MAX_REQUEST_BYTES = 60 * 1024

ALLOWED_MODES = ("full", "selection", "incremental")


def _invalid(message: str) -> LiveEditorError:
    return LiveEditorError("graph-layout-experimental-preview-invalid", message)


def _string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise _invalid(f"{name} must be one non-empty string.")
    return value


def _sha256(value: Any, name: str) -> str:
    text = _string(value, name)
    if not text.startswith("sha256:") or len(text) != 71:
        raise _invalid(f"{name} must be one canonical sha256 revision.")
    return text


def _sha1(value: Any, name: str) -> str:
    text = _string(value, name)
    if not text.startswith("sha1:") or len(text) != 45:
        raise _invalid(f"{name} must be one canonical sha1 graph fingerprint.")
    return text


def _node_id(value: Any) -> str:
    text = _string(value, "nodeId").strip().lower()
    if not text.startswith("n:"):
        raise _invalid("Every position nodeId must start with n:.")
    return text


def confirmation_for_plan(plan_id: str) -> str:
    """Return the exact user-facing Apply confirmation phrase."""

    return APPLY_CONFIRMATION_PREFIX + plan_id


def save_confirmation_for_layout(layout_hash: str) -> str:
    """Return the exact user-facing Save confirmation phrase."""

    return SAVE_CONFIRMATION_PREFIX + layout_hash


def _plan_positions(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    positions = plan.get("positions")
    if not isinstance(positions, list) or not positions:
        raise _invalid("preview.plan.positions must be a non-empty array.")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(positions):
        if not isinstance(item, Mapping):
            raise _invalid(f"preview.plan.positions[{index}] must be an object.")
        node_id = _node_id(item.get("nodeId"))
        if node_id in seen:
            raise _invalid(f"preview.plan.positions contains duplicate {node_id}.")
        seen.add(node_id)
        x = item.get("x")
        y = item.get("y")
        if not isinstance(x, int) or isinstance(x, bool) or not isinstance(y, int) or isinstance(y, bool):
            raise _invalid(f"preview.plan.positions[{index}] x/y must be int32 values.")
        normalized.append({"nodeId": node_id, "x": x, "y": y})
    return normalized


def _preview_positions(preview: Mapping[str, Any], expected: list[dict[str, Any]]) -> None:
    positions = preview.get("positions")
    if not isinstance(positions, list) or not positions:
        raise _invalid("preview.positions must be a non-empty array.")
    actual: dict[str, tuple[int, int]] = {}
    for index, item in enumerate(positions):
        if not isinstance(item, Mapping):
            raise _invalid(f"preview.positions[{index}] must be an object.")
        node_id = _node_id(item.get("nodeId"))
        for name in ("afterX", "afterY"):
            value = item.get(name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise _invalid(f"preview.positions[{index}].{name} must be an int32.")
        actual[node_id] = (int(item["afterX"]), int(item["afterY"]))
    expected_map = {item["nodeId"]: (item["x"], item["y"]) for item in expected}
    if actual != expected_map:
        raise LiveEditorError(
            "graph-layout-experimental-preview-mismatch",
            "preview.positions does not exactly match preview.plan.positions.",
        )


def build_apply_request(preview: Mapping[str, Any]) -> dict[str, Any]:
    """Build one canonical P5 ApplyRequest from a preview result.

    ``preview`` must be the ``result`` object returned by
    ``ue_preview_blueprint_graph_layout``.  Only position data and the exact
    identity it already carried are accepted.
    """

    if not isinstance(preview, Mapping):
        raise _invalid("preview must be one graph layout preview result object.")
    if preview.get("schemaVersion") != PREVIEW_SCHEMA:
        raise _invalid(f"preview.schemaVersion must be {PREVIEW_SCHEMA}.")
    if preview.get("readOnly") is not True:
        raise _invalid("preview.readOnly must be true.")
    if preview.get("wroteBlueprint") is not False or preview.get("savedPackage") is not False:
        raise _invalid("A preview that reports a write cannot be used as the Apply source.")
    if preview.get("packageDirty") is not False:
        raise _invalid("preview.packageDirty must be false.")
    if preview.get("editable") is not True or preview.get("applyEligible") is not True:
        raise _invalid("preview must report an editable, apply-eligible focused graph.")
    mode = preview.get("mode")
    if mode not in ALLOWED_MODES:
        raise _invalid("preview.mode must be full, selection, or incremental.")

    asset_path = _string(preview.get("assetPath"), "preview.assetPath")
    graph_guid = _string(preview.get("graphGuid"), "preview.graphGuid")
    package_revision = _sha256(preview.get("packageRevision"), "preview.packageRevision")
    graph_fingerprint = _sha1(preview.get("graphFingerprint"), "preview.graphFingerprint")
    plan_id = _string(preview.get("planId"), "preview.planId")
    layout_hash = _sha256(preview.get("layoutHash"), "preview.layoutHash")

    plan = preview.get("plan")
    if not isinstance(plan, Mapping) or not plan:
        raise _invalid("preview.plan must be one non-empty LayoutPlan object.")
    if _string(plan.get("planId"), "preview.plan.planId") != plan_id:
        raise LiveEditorError(
            "graph-layout-experimental-preview-mismatch",
            "preview.planId does not equal preview.plan.planId.",
        )
    if _string(plan.get("layoutHash"), "preview.plan.layoutHash") != layout_hash:
        raise LiveEditorError(
            "graph-layout-experimental-preview-mismatch",
            "preview.layoutHash does not equal preview.plan.layoutHash.",
        )

    positions = _plan_positions(plan)
    if len(positions) != preview.get("nodeCount"):
        raise LiveEditorError(
            "graph-layout-experimental-preview-mismatch",
            "preview.plan.positions must cover exactly preview.nodeCount nodes.",
        )
    _preview_positions(preview, positions)

    recomputed = recompute_layout_hash(dict(plan))
    if recomputed != layout_hash:
        raise LiveEditorError(
            "graph-layout-experimental-preview-hash-mismatch",
            "preview.layoutHash is not the canonical hash of preview.plan.positions.",
        )

    moved = plan.get("movedNodeIds")
    if not isinstance(moved, list) or any(not isinstance(item, str) for item in moved):
        raise _invalid("preview.plan.movedNodeIds must be an array of node ids.")
    known = {item["nodeId"] for item in positions}
    if any(item not in known for item in moved) or len(set(moved)) != len(moved):
        raise LiveEditorError(
            "graph-layout-experimental-preview-mismatch",
            "preview.plan.movedNodeIds must be unique ids from preview.plan.positions.",
        )

    request = {
        "schemaVersion": APPLY_SCHEMA,
        "assetPath": asset_path,
        "graphGuid": graph_guid,
        "expectedPackageRevision": package_revision,
        "expectedGraphFingerprint": graph_fingerprint,
        "requestedMode": "position-only",
        "allowSave": False,
        "allowCompile": False,
        "acceptanceEligible": False,
        "requiredAcceptanceEvidence": "independent-reload",
        "plan": dict(plan),
    }
    encoded = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_REQUEST_BYTES:
        raise LiveEditorError(
            "graph-layout-experimental-request-too-large",
            "The canonical ApplyRequest exceeds the 60 KiB bridge request boundary; "
            "use selection or incremental mode for this graph.",
        )
    return request


def validate_save_parameters(
    *,
    asset_path: Any,
    graph_guid: Any,
    layout_hash: Any,
    expected_graph_fingerprint: Any,
    confirmation: Any,
) -> dict[str, str]:
    """Validate the exact explicit save tuple before calling the Editor."""

    normalized = {
        "assetPath": _string(asset_path, "assetPath"),
        "graphGuid": _string(graph_guid, "graphGuid"),
        "layoutHash": _sha256(layout_hash, "layoutHash"),
        "expectedGraphFingerprint": _sha1(
            expected_graph_fingerprint,
            "expectedGraphFingerprint",
        ),
        "confirmation": _string(confirmation, "confirmation"),
    }
    expected = save_confirmation_for_layout(normalized["layoutHash"])
    if normalized["confirmation"] != expected:
        raise LiveEditorError(
            "graph-layout-experimental-confirmation-required",
            f"Explicit Save confirmation must be exactly {expected}.",
        )
    return normalized


__all__ = [
    "APPLY_CAPABILITY",
    "APPLY_CONFIRMATION_PREFIX",
    "APPLY_METHOD",
    "APPLY_SCHEMA",
    "APPLY_TOOL_NAME",
    "MAX_REQUEST_BYTES",
    "SAVE_CAPABILITY",
    "SAVE_CONFIRMATION_PREFIX",
    "SAVE_METHOD",
    "SAVE_TOOL_NAME",
    "build_apply_request",
    "confirmation_for_plan",
    "save_confirmation_for_layout",
    "validate_save_parameters",
]
