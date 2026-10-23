"""Client-side apply-request builder for the internal UE graph-layout handler.

This module is deliberately *not* an Editor executor.  It turns a read-only
LayoutPlan plus a live C++ preflight snapshot into the exact request consumed by
``BlueprintGraphLayoutApply.cpp`` and fails closed on identity, revision, scope,
comment and reroute violations before anything reaches Unreal.

The C++ handler independently repeats the same gates against live UObjects; this
Python layer exists to make the contract testable offline and to prevent a
caller from sending a stale or ambiguous plan.

No file is written, no Unreal command is executed, and no acceptance is claimed.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from .layout import recompute_layout_hash

PREFLIGHT_SCHEMA_VERSION = "ueak.graphlayoutpreflight/1.0"
APPLY_SCHEMA_VERSION = "ueak.graphlayoutapply/1.0"
MAX_MOVED_POSITIONS = 1000
INT32_MIN = -2_147_483_648
INT32_MAX = 2_147_483_647
# A legal knot chain is short in authored Blueprints; a longer walk is treated
# as unusable evidence rather than an invitation to recurse further.
MAX_KNOT_CHAIN = 64
MAX_KNOT_TYPE_CHARS = 64

_GUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SHA1_RE = re.compile(r"^sha1:[0-9a-f]{40}$")
_ASSET_PATH_RE = re.compile(r"^/Game/[A-Za-z0-9_./-]{1,240}$")


class GraphLayoutApplyError(ValueError):
    """A fail-closed client gate; never an Editor acceptance result."""

    def __init__(self, code: str, message: str, path: str = "$") -> None:
        super().__init__(message)
        self.code = code
        self.path = path

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": str(self), "path": self.path}


def _fail(code: str, message: str, path: str = "$") -> None:
    raise GraphLayoutApplyError(code, message, path)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _int32(value: Any, path: str) -> int:
    if not _is_int(value):
        _fail("layout-apply-coordinate-out-of-range", "coordinate must be an integer", path)
    if value < INT32_MIN or value > INT32_MAX:
        _fail("layout-apply-coordinate-out-of-range", "coordinate exceeds UE int32 NodePosX/Y", path)
    return value


def _canonical_guid(value: Any, path: str) -> str:
    if not isinstance(value, str) or _GUID_RE.fullmatch(value) is None:
        _fail("layout-apply-invalid-guid", "GUID must be canonical lowercase with hyphens", path)
    return value


def _node_id(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.startswith("n:"):
        _fail("layout-apply-invalid-guid", "nodeId must use the n:<guid> form", path)
    _canonical_guid(value[2:], path)
    return value


def _sha256(value: Any, path: str, *, code: str = "layout-apply-revision-mismatch") -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        _fail(code, "expected a lowercase sha256 value", path)
    return value


def _knot_evidence(value: Any, node_id: str, path: str) -> dict[str, Any]:
    """Parse the optional live pin evidence attached to a reroute node.

    Aggregate pin/link counts alone cannot distinguish a legal data fan-out
    knot from a broken knot, so a moved knot whose shape is not the historical
    one-in/one-out form must carry this evidence.  The evidence is a snapshot;
    the C++ handler still revalidates the live pins and links.
    """

    if not isinstance(value, dict):
        _fail("layout-apply-preflight-invalid", "knot evidence must be an object", path)
    unknown = set(value) - {"inputType", "outputType", "inputSources", "outputSinks"}
    if unknown:
        _fail("layout-apply-preflight-invalid", f"unknown knot evidence fields: {sorted(unknown)}", path)
    parsed: dict[str, Any] = {}
    for field in ("inputType", "outputType"):
        item = value.get(field)
        if not isinstance(item, str) or not item or len(item) > MAX_KNOT_TYPE_CHARS:
            _fail(
                "layout-apply-preflight-invalid",
                f"knot {field} must be a non-empty bounded pin type key",
                path + "." + field,
            )
        if any(character in item for character in "\r\n\t"):
            _fail("layout-apply-preflight-invalid", f"knot {field} contains control characters", path + "." + field)
        parsed[field] = item
    self_guid = node_id[2:]
    for field in ("inputSources", "outputSinks"):
        items = value.get(field)
        if not isinstance(items, list) or len(items) > MAX_KNOT_CHAIN:
            _fail(
                "layout-apply-preflight-invalid",
                f"knot {field} must be a bounded list of peer node guids",
                path + "." + field,
            )
        peers: list[str] = []
        for index, entry in enumerate(items):
            peer = _canonical_guid(entry, f"{path}.{field}[{index}]")
            if peer == self_guid:
                _fail("layout-apply-preflight-invalid", "a knot cannot link to itself", f"{path}.{field}[{index}]")
            # A repeated peer is legal: two links may come from two pins of the
            # same node (for example a Sequence feeding an exec merge twice).
            # Terminal counting below deduplicates by node, while the array
            # length must still equal the live link count.
            peers.append(peer)
        parsed[field] = peers
    return parsed


def _preflight_nodes(preflight: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if not isinstance(preflight, dict) or preflight.get("schemaVersion") != PREFLIGHT_SCHEMA_VERSION:
        _fail("layout-apply-preflight-invalid", "preflight schemaVersion is not supported")
    if not isinstance(preflight.get("assetPath"), str) or _ASSET_PATH_RE.fullmatch(preflight["assetPath"]) is None:
        _fail("layout-apply-preflight-invalid", "preflight assetPath must be an exact /Game object path", "$.assetPath")
    _canonical_guid(preflight.get("graphGuid"), "$.graphGuid")
    _sha256(preflight.get("packageRevision"), "$.packageRevision")
    if preflight.get("packageDirty") is not False:
        _fail("layout-apply-package-dirty", "preflight must prove a clean package", "$.packageDirty")
    fingerprint = preflight.get("graphFingerprint")
    if not isinstance(fingerprint, str) or _SHA1_RE.fullmatch(fingerprint) is None:
        _fail("layout-apply-preflight-invalid", "preflight graphFingerprint is missing", "$.graphFingerprint")
    if preflight.get("evidenceLevel") != "same-process" or preflight.get("acceptanceEligible") is not False:
        _fail("layout-apply-evidence-invalid", "preflight must remain same-process and not acceptance-eligible")

    raw_nodes = preflight.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        _fail("layout-apply-preflight-invalid", "preflight must contain the live nodes", "$.nodes")
    nodes: dict[str, dict[str, Any]] = {}
    for index, node in enumerate(raw_nodes):
        path = f"$.nodes[{index}]"
        if not isinstance(node, dict):
            _fail("layout-apply-preflight-invalid", "node entry must be an object", path)
        node_id = _node_id(node.get("nodeId"), path + ".nodeId")
        if _canonical_guid(node.get("guid"), path + ".guid") != node_id[2:]:
            _fail("layout-apply-invalid-guid", "preflight node guid does not match nodeId", path + ".guid")
        if node_id in nodes:
            _fail("layout-apply-duplicate-node", "preflight contains a duplicate nodeId", path)
        if node.get("kind") not in {"node", "comment", "reroute"}:
            _fail("layout-apply-preflight-invalid", "node kind is unknown", path + ".kind")
        counter_fields = (
            "x",
            "y",
            "width",
            "height",
            "inputPinCount",
            "outputPinCount",
            "inputLinkCount",
            "outputLinkCount",
        )
        for field in counter_fields:
            value = node.get(field)
            if not _is_int(value) or value < INT32_MIN or value > INT32_MAX:
                _fail("layout-apply-preflight-invalid", f"{field} must be a UE int32 integer", path + "." + field)
        if node["width"] < 0 or node["height"] < 0:
            _fail("layout-apply-preflight-invalid", "node size cannot be negative", path)
        if any(node[field] < 0 for field in counter_fields[4:]):
            _fail("layout-apply-preflight-invalid", "pin/link counts cannot be negative", path)
        raw_knot = node.get("knot")
        if raw_knot is not None:
            if node["kind"] != "reroute":
                _fail(
                    "layout-apply-preflight-invalid",
                    "knot pin evidence is only valid on a reroute node",
                    path + ".knot",
                )
            node = dict(node)
            node["knot"] = _knot_evidence(raw_knot, node_id, path + ".knot")
        nodes[node_id] = node
    if not _is_int(preflight.get("nodeCount")) or preflight["nodeCount"] != len(nodes):
        _fail("layout-apply-preflight-invalid", "nodeCount does not match the node list", "$.nodeCount")
    return nodes


def _plan_evidence(plan: dict[str, Any]) -> None:
    if not isinstance(plan, dict):
        _fail("layout-apply-plan-invalid", "plan must be an object")
    if not isinstance(plan.get("schemaVersion"), str) or not plan["schemaVersion"].startswith("ueak.layoutplan/1."):
        _fail("layout-apply-plan-invalid", "plan schemaVersion is not supported", "$.schemaVersion")
    if plan.get("status") != "ok" or plan.get("errorCode") != "":
        _fail("layout-apply-plan-rejected", "only an ok LayoutPlan can become an apply request")
    if plan.get("evidenceLevel") != "same-process" or plan.get("acceptanceEligible") is not False:
        _fail("layout-apply-evidence-invalid", "plan must remain same-process and not acceptance-eligible")
    if plan.get("requiredAcceptanceEvidence") != "independent-reload":
        _fail("layout-apply-evidence-invalid", "independent reload is still required for acceptance")
    if not isinstance(plan.get("planId"), str) or not plan["planId"]:
        _fail("layout-apply-plan-invalid", "planId is required", "$.planId")
    _sha256(plan.get("baseGraphHash"), "$.baseGraphHash", code="layout-apply-plan-invalid")
    _sha256(plan.get("layoutHash"), "$.layoutHash", code="layout-apply-plan-invalid")
    mode = plan.get("mode")
    if mode not in {"full", "selection", "incremental"}:
        _fail("layout-apply-plan-invalid", "mode must be full|selection|incremental", "$.mode")


def _comment_gate(
    moved: set[str],
    nodes: dict[str, dict[str, Any]],
    positions: dict[str, tuple[int, int]],
) -> None:
    """Reject a moved comment that leaves any formerly contained node outside.

    A layout may move a comment while a contained node keeps its exact
    coordinates (for example because it was already on the target grid).  That
    is safe as long as every node the comment used to frame is still inside the
    comment's new position.  Apply is position-only, so the comment's live
    width/height are unchanged and are used for both frames; a plan ``size``
    change is never applied by the C++ handler.
    """

    for comment_id in sorted(moved):
        comment = nodes[comment_id]
        if comment["kind"] != "comment":
            continue
        old_left = comment["x"]
        old_top = comment["y"]
        width = max(1, comment["width"])
        height = max(1, comment["height"])
        old_right = old_left + width
        old_bottom = old_top + height
        new_left, new_top = positions[comment_id]
        new_right = new_left + width
        new_bottom = new_top + height
        for other_id, other in nodes.items():
            if other_id == comment_id or other["kind"] == "comment":
                continue
            old_center_x = other["x"] + max(1, other["width"]) // 2
            old_center_y = other["y"] + max(1, other["height"]) // 2
            target_x, target_y = positions[other_id]
            new_center_x = target_x + max(1, other["width"]) // 2
            new_center_y = target_y + max(1, other["height"]) // 2
            was_inside = old_left <= old_center_x <= old_right and old_top <= old_center_y <= old_bottom
            still_inside = new_left <= new_center_x <= new_right and new_top <= new_center_y <= new_bottom
            if was_inside and not still_inside:
                _fail(
                    "layout-apply-comment-partial-scope",
                    f"comment {comment_id} leaves node {other_id} outside its moved frame",
                )


def _walk_knot_terminals(
    start_id: str,
    direction: str,
    nodes: dict[str, dict[str, Any]],
    evidence: dict[str, dict[str, Any]],
) -> int:
    """Count external (non-knot) endpoints reached through a knot chain.

    ``direction`` is ``"input"`` (follow sources backwards) or ``"output"``
    (follow sinks forwards).  Every intermediate knot must itself carry pin
    evidence, otherwise the chain cannot be proven and the whole move fails.
    """

    visited: set[str] = set()
    terminal_ids: set[str] = set()
    stack = [start_id]
    while stack:
        current = stack.pop()
        if current in visited:
            # Revisits are skipped, not treated as a cycle: a legal diamond may
            # reach the same knot twice.  A pure cycle then yields zero
            # terminals and still fails the shape rule.
            continue
        visited.add(current)
        if len(visited) > MAX_KNOT_CHAIN:
            _fail(
                "layout-apply-reroute-broken",
                "reroute chain is longer than the bounded evidence walk",
            )
        current_evidence = evidence.get(current)
        if current_evidence is None:
            _fail(
                "layout-apply-reroute-broken",
                f"reroute {current} in the chain has no pin evidence",
            )
        peers = current_evidence["inputSources" if direction == "input" else "outputSinks"]
        if not peers:
            _fail(
                "layout-apply-reroute-broken",
                f"reroute {current} is a dangling knot with no external {direction}",
            )
        for peer_guid in peers:
            peer_id = "n:" + peer_guid
            peer = nodes.get(peer_id)
            if peer is None:
                _fail(
                    "layout-apply-reroute-broken",
                    f"reroute {current} links to unknown node {peer_guid}",
                )
            if peer["kind"] == "reroute":
                stack.append(peer_id)
            else:
                terminal_ids.add(peer_id)
    return len(terminal_ids)


def _reroute_gate(moved: set[str], nodes: dict[str, dict[str, Any]]) -> None:
    evidence = {node_id: node["knot"] for node_id, node in nodes.items() if node.get("knot")}

    for node_id in sorted(moved):
        node = nodes[node_id]
        if node["kind"] != "reroute":
            continue

        # A knot is always authored with exactly one input pin and one output
        # pin; only the number of links on those pins may legally vary.
        if node["inputPinCount"] != 1 or node["outputPinCount"] != 1:
            _fail(
                "layout-apply-reroute-broken",
                f"reroute {node_id} is not a two-pin one-in/one-out knot",
            )

        # Historical shape: one link on each side.  This stays accepted even
        # when the preflight predates pin evidence (P8/P9 evidence, fixtures).
        legacy_shape = node["inputLinkCount"] == 1 and node["outputLinkCount"] == 1
        knot = node.get("knot")
        if knot is None:
            if legacy_shape:
                continue
            _fail(
                "layout-apply-reroute-broken",
                f"reroute {node_id} has a fan-out/merge shape but no pin evidence",
            )

        if len(knot["inputSources"]) != node["inputLinkCount"] or (
            len(knot["outputSinks"]) != node["outputLinkCount"]
        ):
            _fail(
                "layout-apply-reroute-broken",
                f"reroute {node_id} pin evidence does not match its link counts",
            )

        input_type = knot["inputType"]
        output_type = knot["outputType"]
        if input_type != output_type:
            _fail(
                "layout-apply-reroute-broken",
                f"reroute {node_id} input/output pin types differ",
            )
        if "wildcard" in input_type.lower() or input_type.lower() in {"", "unknown"}:
            _fail(
                "layout-apply-reroute-broken",
                f"reroute {node_id} pin type is unresolved and cannot be proven",
            )

        sources = _walk_knot_terminals(node_id, "input", nodes, evidence)
        sinks = _walk_knot_terminals(node_id, "output", nodes, evidence)
        if input_type == "exec":
            # Exec merge: several exec sources may converge, one sink leaves.
            if sources < 1 or sinks != 1:
                _fail(
                    "layout-apply-reroute-broken",
                    f"exec reroute {node_id} must merge >=1 source into exactly 1 sink",
                )
        else:
            # Data fan-out: one value source, one or more consumers.
            if sources != 1 or sinks < 1:
                _fail(
                    "layout-apply-reroute-broken",
                    f"data reroute {node_id} must fan one source out to >=1 sink",
                )


def _validate_plan(plan: dict[str, Any], nodes: dict[str, dict[str, Any]]) -> None:
    _plan_evidence(plan)

    raw_positions = plan.get("positions")
    if not isinstance(raw_positions, list) or not raw_positions:
        _fail("layout-apply-plan-invalid", "positions must be a non-empty list", "$.positions")
    positions: dict[str, tuple[int, int]] = {}
    for index, item in enumerate(raw_positions):
        path = f"$.positions[{index}]"
        if not isinstance(item, dict):
            _fail("layout-apply-plan-invalid", "position entry must be an object", path)
        node_id = _node_id(item.get("nodeId"), path + ".nodeId")
        if node_id in positions:
            _fail("layout-apply-duplicate-node", "duplicate nodeId in positions", path)
        if node_id not in nodes:
            _fail("layout-apply-node-unknown", f"plan node {node_id} is not in the preflight graph", path)
        positions[node_id] = (_int32(item.get("x"), path + ".x"), _int32(item.get("y"), path + ".y"))
        size = item.get("size")
        if not isinstance(size, dict) or not _is_int(size.get("width")) or not _is_int(size.get("height")):
            _fail("layout-apply-plan-invalid", "position size is required", path + ".size")
        if size["width"] < 0 or size["height"] < 0:
            _fail("layout-apply-plan-invalid", "position size cannot be negative", path + ".size")
        if size.get("source") not in {"measured", "stored", "estimated", "unknown"}:
            _fail("layout-apply-plan-invalid", "position size source is unknown", path + ".size.source")
    if set(positions) != set(nodes):
        missing = sorted(set(nodes) - set(positions))
        _fail("layout-apply-node-missing", f"plan does not cover every live node: {missing}", "$.positions")
    if plan["layoutHash"] != recompute_layout_hash(plan):
        _fail(
            "layout-apply-layout-hash-mismatch",
            "layoutHash does not match the canonical positions hash",
            "$.layoutHash",
        )

    raw_moved = plan.get("movedNodeIds")
    if not isinstance(raw_moved, list):
        _fail("layout-apply-plan-invalid", "movedNodeIds must be a list", "$.movedNodeIds")
    moved: set[str] = set()
    for index, item in enumerate(raw_moved):
        node_id = _node_id(item, f"$.movedNodeIds[{index}]")
        if node_id in moved:
            _fail("layout-apply-duplicate-node", "duplicate moved nodeId", f"$.movedNodeIds[{index}]")
        moved.add(node_id)
    if len(moved) > MAX_MOVED_POSITIONS:
        _fail("layout-apply-limit-exceeded", "LayoutPlan moves more than 1000 positions")

    expected_moved = {
        node_id
        for node_id, node in nodes.items()
        if positions[node_id] != (node["x"], node["y"])
    }
    if moved != expected_moved:
        _fail("layout-apply-moved-mismatch", "movedNodeIds does not match the positions that differ")

    raw_scope = plan.get("scopeNodeIds")
    raw_fixed = plan.get("fixedNodeIds")
    if not isinstance(raw_scope, list) or not isinstance(raw_fixed, list):
        _fail("layout-apply-plan-invalid", "scopeNodeIds and fixedNodeIds are required")
    scope = {_node_id(item, "$.scopeNodeIds") for item in raw_scope}
    fixed = {_node_id(item, "$.fixedNodeIds") for item in raw_fixed}
    if len(scope) != len(raw_scope) or len(fixed) != len(raw_fixed):
        _fail("layout-apply-duplicate-node", "scopeNodeIds/fixedNodeIds contain duplicates")
    unknown = (scope | fixed) - set(nodes)
    if unknown:
        _fail("layout-apply-node-unknown", f"scope/fixed contains unknown nodes: {sorted(unknown)}")

    mode = plan["mode"]
    content = {node_id for node_id, node in nodes.items() if node["kind"] != "comment"}
    if mode == "full":
        if fixed:
            _fail("layout-apply-scope-invalid", "full mode cannot contain fixed nodes")
        if not content <= scope:
            _fail("layout-apply-scope-invalid", "full mode must scope every content node")
    else:
        if fixed != content - scope:
            _fail("layout-apply-scope-invalid", "fixedNodeIds must equal content nodes outside scope")
        for node_id in moved:
            if nodes[node_id]["kind"] != "comment" and node_id not in scope:
                _fail("layout-apply-scope-invalid", "every moved content node must be inside scope")

    _comment_gate(moved, nodes, positions)
    _reroute_gate(moved, nodes)


def build_graph_layout_apply_request(
    plan: dict[str, Any],
    preflight: dict[str, Any],
    *,
    expected_package_revision: str | None = None,
) -> dict[str, Any]:
    """Build the exact internal C++ request or raise ``GraphLayoutApplyError``.

    The returned object is evidence for a *future* Editor call only.  Building it
    never authorizes a write, and ``acceptanceEligible`` stays false.
    """

    nodes = _preflight_nodes(preflight)
    _validate_plan(plan, nodes)

    revision = preflight["packageRevision"] if expected_package_revision is None else expected_package_revision
    _sha256(revision, "$.expectedPackageRevision")
    if revision != preflight["packageRevision"]:
        _fail(
            "layout-apply-revision-mismatch",
            "expectedPackageRevision does not match the preflight package revision",
            "$.expectedPackageRevision",
        )

    return {
        "schemaVersion": APPLY_SCHEMA_VERSION,
        "assetPath": preflight["assetPath"],
        "graphGuid": preflight["graphGuid"],
        "expectedPackageRevision": revision,
        "expectedGraphFingerprint": preflight["graphFingerprint"],
        "plan": copy.deepcopy(plan),
        "requestedMode": "position-only",
        "allowSave": False,
        "allowCompile": False,
        "acceptanceEligible": False,
        "requiredAcceptanceEvidence": "independent-reload",
    }


__all__ = [
    "APPLY_SCHEMA_VERSION",
    "GraphLayoutApplyError",
    "MAX_MOVED_POSITIONS",
    "PREFLIGHT_SCHEMA_VERSION",
    "build_graph_layout_apply_request",
]
