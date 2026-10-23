"""Canonical LayoutPlan id recomputation shared by broker and client.

The projection is the exact one used by ``graph_contracts.layout`` when it
creates ``planId``:

    baseGraphHash, mode, positions[nodeId,x,y], options[spacingX,spacingY,seed],
    packing

``spacingY`` is not present in the result, so callers must supply the original
request options.  This module has no broker imports and therefore cannot create
an import cycle with ``protocol``, ``server`` or ``client``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..graph_contracts.canonical import DOMAIN_LAYOUT_PLAN, short_digest


class PlanIdError(ValueError):
    """The result is missing the data needed to recompute ``planId``."""


def plan_id_from_result(
    result: Mapping[str, Any],
    *,
    spacing_x: int,
    spacing_y: int,
    target_width: int,
    seed: int,
) -> str:
    """Return the canonical ``layout:<16 hex>`` for ``result``.

    Raises :class:`PlanIdError` when the plan/positions/packing shape is not
    sufficient.  It intentionally does not validate the full wire contract;
    ``protocol.validate_preview_result`` does that first.
    """

    if not isinstance(result, Mapping):
        raise PlanIdError("result must be a mapping")
    plan = result.get("plan")
    if not isinstance(plan, Mapping):
        raise PlanIdError("result.plan is missing")
    positions = plan.get("positions")
    if not isinstance(positions, list):
        raise PlanIdError("result.plan.positions is missing")
    packing = plan.get("packing")
    if not isinstance(packing, Mapping):
        raise PlanIdError("result.plan.packing is missing")
    expected_packing = {
        "targetWidth": int(target_width),
        "spacing": int(spacing_x),
        "algorithm": "skyline-v1",
    }
    if dict(packing) != expected_packing:
        raise PlanIdError("result.plan.packing does not match the request options")
    if plan.get("seed") != seed:
        raise PlanIdError("result.plan.seed does not match the request seed")
    base_graph_hash = result.get("baseGraphHash")
    if not isinstance(base_graph_hash, str) or not base_graph_hash:
        raise PlanIdError("result.baseGraphHash is missing")
    mode = plan.get("mode")
    if not isinstance(mode, str) or not mode:
        raise PlanIdError("result.plan.mode is missing")

    projection_positions: list[dict[str, Any]] = []
    for entry in positions:
        if not isinstance(entry, Mapping):
            raise PlanIdError("result.plan.positions entries must be objects")
        node_id = entry.get("nodeId")
        x = entry.get("x")
        y = entry.get("y")
        if not isinstance(node_id, str) or not node_id:
            raise PlanIdError("result.plan.positions nodeId is invalid")
        if not isinstance(x, int) or isinstance(x, bool):
            raise PlanIdError("result.plan.positions x must be an integer")
        if not isinstance(y, int) or isinstance(y, bool):
            raise PlanIdError("result.plan.positions y must be an integer")
        projection_positions.append({"nodeId": node_id, "x": x, "y": y})

    projection = {
        "baseGraphHash": base_graph_hash,
        "mode": mode,
        "positions": projection_positions,
        "options": {
            "spacingX": int(spacing_x),
            "spacingY": int(spacing_y),
            "seed": int(seed),
        },
        "packing": expected_packing,
    }
    return "layout:" + short_digest(projection, DOMAIN_LAYOUT_PLAN, 16)


__all__ = ["PlanIdError", "plan_id_from_result"]
