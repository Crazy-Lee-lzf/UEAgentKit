from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from typing import Any

from .query_protocol import estimate_json_tokens

"""Deterministic reverse-reference impact analysis (R1).

`ue_analyze_change_impact` walks the immutable SQLite reference index in the
consumer -> target direction using exact-key joins only:

    Target T <- Direct Consumer A <- Consumer C of A

A/B are depth=1 consumers of T and C is a depth=2 consumer. The module never
loads Unreal objects, never writes, and never performs model inference; every
fact in the response traces back to an explicit references_table row.

Direction contract (fixed by the existing index schema): `references_table`
rows are owned by the *consumer* asset (`asset_id` -> `assets`), and
`target_asset_path` is the referenced target. A static reference never proves
a runtime semantic impact, which is why `runtimeSensitiveConsumers` stays
empty unless the index itself would carry explicit runtime classification
evidence (it currently does not).
"""

MAX_IMPACT_TARGETS = 8
MAX_IMPACT_DEPTH = 3
DEFAULT_IMPACT_DEPTH = 2
MAX_IMPACT_CONSUMERS = 100
MAX_IMPACT_EDGES = 1000
DEFAULT_IMPACT_EDGES = 500
MAX_IMPACT_PATHS = 100
DEFAULT_IMPACT_PATHS = 50
MAX_IMPACT_SUBJECT_CHARS = 2048
MAX_IMPACT_ASSET_PATH_CHARS = 512
IMPACT_HIGH_FANOUT_THRESHOLD = 15
FRONTIER_QUERY_CHUNK = 500
MAX_IMPACT_PATH_SWEEPS = 64

IMPACT_METHOD = "reverse-reference-bfs-exact-key"
IMPACT_SOURCE = "immutable-sqlite-index"

SUPPORTED_SUBJECT_KINDS = ("asset-level", "blueprint-symbol")
UNSUPPORTED_SUBJECT_KINDS = (
    "data-table-row",
    "searchable-name",
    "data-asset-object",
    "material-instance-parent",
    "material-instance-parameter",
    "blueprint-member",
)
ALL_SUBJECT_KINDS = SUPPORTED_SUBJECT_KINDS + UNSUPPORTED_SUBJECT_KINDS

# Deterministic raw kind -> normalized category mapping. The mapping is based
# only on the semantics the exporter writes into references_table; unknown
# kinds are preserved verbatim and normalized to `unknown-reference`.
REFERENCE_KIND_CATEGORIES = {
    "inherits": "parent-reference",
    "depends-hard-package": "asset-reference",
    "implements": "class-reference",
    "casts": "class-reference",
    "calls": "blueprint-symbol-reference",
    "macro-calls": "blueprint-symbol-reference",
    "interface-calls": "blueprint-symbol-reference",
    "reads": "blueprint-symbol-reference",
    "writes": "blueprint-symbol-reference",
    "returns": "blueprint-symbol-reference",
    "delegate-binds": "blueprint-symbol-reference",
    "delegate-broadcasts": "blueprint-symbol-reference",
    "delegate-creates": "blueprint-symbol-reference",
    "delegate-unbinds": "blueprint-symbol-reference",
}
NORMALIZED_REFERENCE_CATEGORIES = (
    "asset-reference",
    "soft-reference",
    "class-reference",
    "blueprint-symbol-reference",
    "searchable-name-reference",
    "parent-reference",
    "unknown-reference",
)

_TRIM_REASONS = (
    "impact-paths",
    "consumer-evidence",
    "indirect-consumers",
    "consumer-reference-kinds",
    "validation-targets",
    "target-details",
    "analysis-gaps",
)


class ImpactAnalysisError(ValueError):
    """Raised for impact-analysis specific request failures with a stable error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _clean_text(value: Any, *, name: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string.")
    cleaned = value.strip()
    if len(cleaned) > maximum:
        raise ValueError(f"{name} must not exceed {maximum} characters.")
    if any(ord(character) < 32 for character in cleaned):
        raise ValueError(f"{name} must not contain control characters.")
    return cleaned


def validate_target_paths(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("target_asset_paths must be an array of strings.")
    if not value:
        raise ValueError("target_asset_paths must contain at least one entry.")
    if len(value) > MAX_IMPACT_TARGETS:
        raise ValueError(f"target_asset_paths must not exceed {MAX_IMPACT_TARGETS} entries.")
    normalized: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        cleaned = _clean_text(
            item,
            name=f"target_asset_paths[{index}]",
            maximum=MAX_IMPACT_ASSET_PATH_CHARS,
        )
        if not cleaned.startswith("/Game/"):
            raise ValueError(f"target_asset_paths[{index}] must be an exact /Game Object Path.")
        if cleaned in seen:
            raise ValueError("target_asset_paths must not contain duplicates.")
        seen.add(cleaned)
        normalized.append(cleaned)
    return tuple(normalized)


def validate_subject_kind(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("subject_kind must be a string.")
    cleaned = value.strip()
    if cleaned not in ALL_SUBJECT_KINDS:
        raise ValueError(
            f"subject_kind must be one of {', '.join(ALL_SUBJECT_KINDS)}."
        )
    return cleaned


def normalize_subject(subject_kind: str, subject: Any) -> str:
    if subject_kind == "asset-level":
        if subject not in ("", None):
            raise ValueError("subject is available only for structured subject kinds.")
        return ""
    if subject_kind in UNSUPPORTED_SUBJECT_KINDS:
        raise ImpactAnalysisError(
            "unsupported-impact-subject",
            f"subject_kind '{subject_kind}' is not provable with the current immutable index evidence.",
        )
    cleaned = _clean_text(subject, name="subject", maximum=MAX_IMPACT_SUBJECT_CHARS)
    if not cleaned:
        raise ValueError("subject is required for structured subject kinds.")
    return cleaned


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _incoming_rows(
    connection: sqlite3.Connection,
    asset_paths: Sequence[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    ordered = sorted(set(asset_paths))
    for start in range(0, len(ordered), FRONTIER_QUERY_CHUNK):
        chunk = ordered[start : start + FRONTIER_QUERY_CHUNK]
        placeholders = ",".join("?" for _ in chunk)
        rows.extend(
            _row_dict(row)
            for row in connection.execute(
                f"""
                SELECT r.stable_id, r.kind, a.asset_path,
                       r.source_symbol_id, r.target_symbol_id, r.target_kind,
                       r.target_name, r.target_asset_path, r.target_path,
                       r.node_class, r.node_title, r.graph_name
                FROM references_table AS r
                JOIN assets AS a ON a.id = r.asset_id
                WHERE r.target_asset_path IN ({placeholders})
                """,
                chunk,
            )
        )
    rows.sort(
        key=lambda item: (
            str(item["asset_path"]),
            str(item["kind"]),
            str(item["target_name"]),
            str(item["stable_id"]),
        )
    )
    return rows


def _symbol_rows(connection: sqlite3.Connection, stable_id: str) -> list[dict[str, Any]]:
    rows = [
        _row_dict(row)
        for row in connection.execute(
            """
            SELECT r.stable_id, r.kind, a.asset_path,
                   r.source_symbol_id, r.target_symbol_id, r.target_kind,
                   r.target_name, r.target_asset_path, r.target_path,
                   r.node_class, r.node_title, r.graph_name
            FROM references_table AS r
            JOIN assets AS a ON a.id = r.asset_id
            WHERE r.target_symbol_id = ?
            """,
            (stable_id,),
        )
    ]
    rows.sort(
        key=lambda item: (
            str(item["asset_path"]),
            str(item["kind"]),
            str(item["target_name"]),
            str(item["stable_id"]),
        )
    )
    return rows


def _asset_identity(
    connection: sqlite3.Connection,
    asset_paths: Sequence[str],
) -> dict[str, dict[str, Any]]:
    identity: dict[str, dict[str, Any]] = {}
    ordered = sorted(set(asset_paths))
    for start in range(0, len(ordered), FRONTIER_QUERY_CHUNK):
        chunk = ordered[start : start + FRONTIER_QUERY_CHUNK]
        placeholders = ",".join("?" for _ in chunk)
        for row in connection.execute(
            f"""
            SELECT asset_path, asset_name, asset_class, parent_class, generated_class
            FROM assets
            WHERE asset_path IN ({placeholders})
            """,
            chunk,
        ):
            payload = _row_dict(row)
            identity[str(payload["asset_path"])] = payload
    return identity


def _resolve_symbol(
    connection: sqlite3.Connection,
    stable_id: str,
) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT stable_id, kind, name, symbol_asset_path
        FROM symbols
        WHERE stable_id = ?
        """,
        (stable_id,),
    ).fetchone()
    if row is None:
        return None
    payload = _row_dict(row)
    return {
        "stableId": str(payload["stable_id"]),
        "kind": str(payload["kind"]),
        "name": str(payload["name"]),
        "assetPath": str(payload["symbol_asset_path"]),
    }


def _record_reason(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def analyze_impact_graph(
    connection: sqlite3.Connection,
    *,
    target_paths: Sequence[str],
    subject_kind: str,
    subject: str,
    max_depth: int,
    max_consumers: int,
    max_edges: int,
    max_paths: int,
) -> dict[str, Any]:
    """Run the bounded incoming BFS and return the semantic impact body."""
    identity = _asset_identity(connection, target_paths)
    targets: list[dict[str, Any]] = []
    found_paths: list[str] = []
    for path in target_paths:
        info = identity.get(path)
        if info is None:
            targets.append(
                {
                    "assetPath": path,
                    "found": False,
                    "reason": "target-not-indexed",
                }
            )
        else:
            targets.append({"assetPath": path, "found": True, "identity": info})
            found_paths.append(path)

    if subject_kind == "blueprint-symbol":
        symbol = _resolve_symbol(connection, subject)
        if symbol is None:
            raise ImpactAnalysisError(
                "impact-subject-not-found",
                "The blueprint-symbol subject stable ID was not found in the immutable index.",
            )
        owner = str(symbol["assetPath"])
        if len(target_paths) != 1:
            raise ValueError("structured subjects require exactly one target_asset_paths entry.")
        if owner != target_paths[0]:
            raise ImpactAnalysisError(
                "impact-subject-asset-mismatch",
                "The blueprint-symbol subject does not belong to the requested target asset.",
            )
        for target in targets:
            if target["found"]:
                target["subject"] = symbol
        depth1_targets = [owner] if identity.get(owner) else []

    else:
        depth1_targets = list(found_paths)

    truncated_reasons: list[str] = []
    frontier_omitted = 0
    edge_omitted = 0
    path_omitted = 0
    visited_edge_count = 0
    max_depth_reached = 0

    # node_paths[asset] = {target_asset_path: {"hops": [consumers...], "depth": n}}
    # Paths propagate backward through the BFS so every consumer keeps a
    # deterministic shortest impact path per impacted target.
    node_paths: dict[str, dict[str, dict[str, Any]]] = {
        target: {target: {"hops": [], "depth": 0}} for target in found_paths
    }
    consumers: dict[str, dict[str, Any]] = {}
    discovered_assets: set[str] = set()
    visited_assets: set[str] = set(found_paths)
    evidence_budget = max_edges
    path_budget = max_paths

    if not found_paths and subject_kind == "asset-level":
        return _empty_graph_body(
            targets=targets,
            truncated_reasons=truncated_reasons,
            max_depth=max_depth,
            max_consumers=max_consumers,
            max_edges=max_edges,
            max_paths=max_paths,
        )

    depth1_rows = (
        _incoming_rows(connection, found_paths)
        if subject_kind == "asset-level"
        else _symbol_rows(connection, subject)
    )
    frontier = set(found_paths)

    for depth in range(1, max_depth + 1):
        rows = (
            depth1_rows
            if depth == 1
            else _incoming_rows(connection, sorted(frontier))
        )
        visited_edge_count += len(rows)
        discovered: set[str] = set()
        for row in rows:
            consumer = str(row["asset_path"])
            if depth == 1 and subject_kind == "blueprint-symbol":
                target = depth1_targets[0] if depth1_targets else ""
            else:
                target = str(row["target_asset_path"])
            if not target or consumer == target:
                continue
            discovered_assets.add(consumer)
            if consumer not in consumers:
                if len(consumers) >= max_consumers:
                    frontier_omitted += 1
                    _record_reason(truncated_reasons, "consumer-limit")
                    continue
                consumers[consumer] = {
                    "assetPath": consumer,
                    "assetClass": "",
                    "depth": 0,
                    "paths": {},
                    "referenceKinds": {},
                    "evidence": [],
                }
                if consumer not in found_paths:
                    discovered.add(consumer)
            record = consumers[consumer]
            reference_kinds = record["referenceKinds"]
            category = REFERENCE_KIND_CATEGORIES.get(str(row["kind"]), "unknown-reference")
            key = (str(row["kind"]), category)
            reference_kinds[key] = reference_kinds.get(key, 0) + 1
            if evidence_budget > 0:
                evidence_budget -= 1
                record["evidence"].append(
                    {
                        "stableId": str(row["stable_id"]),
                        "kind": str(row["kind"]),
                        "targetKind": str(row["target_kind"]),
                        "targetName": str(row["target_name"]),
                        "targetPath": str(row["target_path"]),
                        "nodeClass": str(row["node_class"]),
                        "nodeTitle": str(row["node_title"]),
                        "graphName": str(row["graph_name"]),
                    }
                )
            else:
                edge_omitted += 1
                _record_reason(truncated_reasons, "edge-limit")

        if discovered:
            max_depth_reached = depth
        visited_assets.update(discovered)

        # Relax path propagation until no target gains a shorter path. Only
        # inter-target reference rows can chain within one layer; each sweep
        # either adds a new entry or strictly shortens an existing one, so the
        # process converges deterministically. The sweep cap is a defensive
        # bound far above the maximum useful number of relaxations.
        for _sweep in range(MAX_IMPACT_PATH_SWEEPS):
            changed = False
            for row in rows:
                consumer = str(row["asset_path"])
                if depth == 1 and subject_kind == "blueprint-symbol":
                    target = depth1_targets[0] if depth1_targets else ""
                else:
                    target = str(row["target_asset_path"])
                if not target or consumer == target or consumer not in consumers:
                    continue
                parent_paths = node_paths.get(target)
                if not parent_paths:
                    continue
                consumer_paths = node_paths.setdefault(consumer, {})
                for impacted_target, parent_path in parent_paths.items():
                    parent_depth = int(parent_path["depth"])
                    if parent_depth == 0:
                        hops: list[str] = []
                    else:
                        hops = [str(hop) for hop in parent_path["hops"]] + [target]
                    path_depth = len(hops) + 1
                    if path_depth > max_depth:
                        continue
                    existing = consumer_paths.get(impacted_target)
                    if existing is not None:
                        if path_depth < int(existing["depth"]):
                            consumer_paths[impacted_target] = {"hops": hops, "depth": path_depth}
                            changed = True
                        continue
                    if path_budget <= 0:
                        path_omitted += 1
                        _record_reason(truncated_reasons, "path-limit")
                        continue
                    path_budget -= 1
                    consumer_paths[impacted_target] = {"hops": hops, "depth": path_depth}
                    changed = True
            if not changed:
                break
        else:
            _record_reason(truncated_reasons, "path-propagation-limit")
        if len(consumers) >= max_consumers:
            break
        frontier = {consumer for consumer in discovered if consumer not in found_paths}
        if not frontier:
            break

    # Finalize consumer records in deterministic order.
    direct_consumers: list[dict[str, Any]] = []
    indirect_consumers: list[dict[str, Any]] = []
    for consumer, record in consumers.items():
        paths = node_paths.get(consumer, {})
        record["paths"] = [
            {
                "targetAssetPath": impacted_target,
                "depth": int(path["depth"]),
                "hops": [str(hop) for hop in path["hops"]],
            }
            for impacted_target, path in sorted(paths.items(), key=lambda item: str(item[0]).casefold())
            if int(path["depth"]) >= 1
        ]
        record["impactedTargets"] = [entry["targetAssetPath"] for entry in record["paths"]]
        record["referenceKinds"] = [
            {
                "rawReferenceKind": raw_kind,
                "normalizedReferenceKind": category,
                "source": IMPACT_SOURCE,
                "edgeCount": int(count),
            }
            for (raw_kind, category), count in sorted(record["referenceKinds"].items(), key=lambda item: item[0][0].casefold())
        ]
        record["depth"] = min((entry["depth"] for entry in record["paths"]), default=0)
        record["whyIncluded"] = (
            "reference-edge-to-subject-symbol"
            if subject_kind == "blueprint-symbol"
            else "reference-edge-to-target"
        )
        if record["depth"] == 1:
            direct_consumers.append(record)
        elif record["depth"] > 1:
            indirect_consumers.append(record)
    direct_consumers.sort(key=lambda item: str(item["assetPath"]).casefold())
    indirect_consumers.sort(
        key=lambda item: (int(item["depth"]), str(item["assetPath"]).casefold())
    )
    for record in [*direct_consumers, *indirect_consumers]:
        record["impactedTargets"] = [
            str(path) for path in sorted(set(record["impactedTargets"]), key=str.casefold)
        ]

    asset_classes = _asset_identity(connection, list(consumers))
    for record in [*direct_consumers, *indirect_consumers]:
        info = asset_classes.get(str(record["assetPath"]))
        record["assetClass"] = str(info.get("asset_class", "")) if info else ""

    all_consumers = [*direct_consumers, *indirect_consumers]
    for target in targets:
        if not target.get("found"):
            continue
        target_path = str(target["assetPath"])
        per_target_depths = [
            int(path["depth"])
            for record in all_consumers
            for path in record["paths"]
            if path["targetAssetPath"] == target_path
        ]
        target["directConsumerCount"] = sum(1 for depth in per_target_depths if depth == 1)
        target["indirectConsumerCount"] = sum(1 for depth in per_target_depths if depth >= 2)

    unknown_kinds = sorted(
        {
            raw_kind
            for record in [*direct_consumers, *indirect_consumers]
            for raw_kind, category in ((item["rawReferenceKind"], item["normalizedReferenceKind"]) for item in record["referenceKinds"])
            if category == "unknown-reference"
        },
        key=str.casefold,
    )
    path_count = sum(
        len(record["paths"]) for record in [*direct_consumers, *indirect_consumers]
    )

    return {
        "direction": "consumer-to-target",
        "method": IMPACT_METHOD,
        "summary": {
            "targetCount": len(targets),
            "foundTargetCount": len(found_paths),
            "notIndexedTargetCount": len(targets) - len(found_paths),
            "visitedAssetCount": len(visited_assets | discovered_assets),
            "visitedEdgeCount": visited_edge_count,
            "directConsumerCount": len(direct_consumers),
            "indirectConsumerCount": len(indirect_consumers),
            "maxDepthRequested": max_depth,
            "maxDepthReached": max_depth_reached,
            "consumerLimit": max_consumers,
            "edgeLimit": max_edges,
            "pathLimit": max_paths,
            "truncated": bool(truncated_reasons),
            "truncationReasons": list(truncated_reasons),
            "frontierOmittedCount": frontier_omitted,
            "omittedEdgeCount": edge_omitted,
            "omittedPathCount": path_omitted,
            "pathCount": path_count,
            "unknownReferenceKindCount": len(unknown_kinds),
            "runtimeSensitiveConsumerCount": 0,
            "subjectKind": subject_kind,
        },
        "targets": targets,
        "directConsumers": direct_consumers,
        "indirectConsumers": indirect_consumers,
        "runtimeSensitiveConsumers": {
            "classificationState": "not-proven-with-current-evidence",
            "items": [],
        },
        "analysisGaps": _analysis_gaps(
            targets=targets,
            direct_consumers=direct_consumers,
            indirect_consumers=indirect_consumers,
            unknown_kinds=unknown_kinds,
            truncated_reasons=truncated_reasons,
        ),
        "validationTargets": _validation_targets(
            targets=targets,
            direct_consumers=direct_consumers,
            indirect_consumers=indirect_consumers,
        ),
        "risks": _risks(
            targets=targets,
            direct_consumers=direct_consumers,
            unknown_kinds=unknown_kinds,
            truncated_reasons=truncated_reasons,
        ),
        "riskSummary": _risk_summary(
            targets=targets,
            direct_consumers=direct_consumers,
            unknown_kinds=unknown_kinds,
            truncated_reasons=truncated_reasons,
        ),
        "nextActions": _next_actions(
            targets=targets,
            direct_consumers=direct_consumers,
            max_depth=max_depth,
            max_depth_reached=max_depth_reached,
        ),
    }


def _empty_graph_body(
    *,
    targets: list[dict[str, Any]],
    truncated_reasons: list[str],
    max_depth: int,
    max_consumers: int,
    max_edges: int,
    max_paths: int,
) -> dict[str, Any]:
    direct_consumers: list[dict[str, Any]] = []
    indirect_consumers: list[dict[str, Any]] = []
    unknown_kinds: list[str] = []
    return {
        "direction": "consumer-to-target",
        "method": IMPACT_METHOD,
        "summary": {
            "targetCount": len(targets),
            "foundTargetCount": 0,
            "notIndexedTargetCount": len(targets),
            "visitedAssetCount": 0,
            "visitedEdgeCount": 0,
            "directConsumerCount": 0,
            "indirectConsumerCount": 0,
            "maxDepthRequested": max_depth,
            "maxDepthReached": 0,
            "consumerLimit": max_consumers,
            "edgeLimit": max_edges,
            "pathLimit": max_paths,
            "truncated": bool(truncated_reasons),
            "truncationReasons": list(truncated_reasons),
            "frontierOmittedCount": 0,
            "omittedEdgeCount": 0,
            "omittedPathCount": 0,
            "pathCount": 0,
            "unknownReferenceKindCount": 0,
            "runtimeSensitiveConsumerCount": 0,
            "subjectKind": "asset-level",
        },
        "targets": targets,
        "directConsumers": direct_consumers,
        "indirectConsumers": indirect_consumers,
        "runtimeSensitiveConsumers": {
            "classificationState": "not-proven-with-current-evidence",
            "items": [],
        },
        "analysisGaps": _analysis_gaps(
            targets=targets,
            direct_consumers=direct_consumers,
            indirect_consumers=indirect_consumers,
            unknown_kinds=unknown_kinds,
            truncated_reasons=truncated_reasons,
        ),
        "validationTargets": _validation_targets(
            targets=targets,
            direct_consumers=direct_consumers,
            indirect_consumers=indirect_consumers,
        ),
        "risks": _risks(
            targets=targets,
            direct_consumers=direct_consumers,
            unknown_kinds=unknown_kinds,
            truncated_reasons=truncated_reasons,
        ),
        "riskSummary": _risk_summary(
            targets=targets,
            direct_consumers=direct_consumers,
            unknown_kinds=unknown_kinds,
            truncated_reasons=truncated_reasons,
        ),
        "nextActions": _next_actions(
            targets=targets,
            direct_consumers=direct_consumers,
            max_depth=max_depth,
            max_depth_reached=0,
        ),
    }


def _analysis_gaps(
    *,
    targets: list[dict[str, Any]],
    direct_consumers: list[dict[str, Any]],
    indirect_consumers: list[dict[str, Any]],
    unknown_kinds: list[str],
    truncated_reasons: list[str],
) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    impacted_targets = {
        path
        for record in [*direct_consumers, *indirect_consumers]
        for path in record.get("impactedTargets", [])
    }
    for target in targets:
        if not target.get("found"):
            continue
        path = str(target["assetPath"])
        if path not in impacted_targets:
            gaps.append(
                {
                    "kind": "no-consumer-evidence-in-index",
                    "targetAssetPath": path,
                    "message": (
                        "No incoming reference rows exist in the immutable index for this target. "
                        "Absence of evidence is not proof of safety."
                    ),
                }
            )
    if unknown_kinds:
        gaps.append(
            {
                "kind": "unknown-reference-kind",
                "kinds": list(unknown_kinds),
                "message": (
                    "Some reference kinds are not covered by the deterministic kind normalization "
                    "table and are reported verbatim with normalizedReferenceKind unknown-reference."
                ),
            }
        )
    gaps.append(
        {
            "kind": "runtime-sensitivity-not-proven",
            "message": (
                "The immutable index carries no runtime-vs-editor consumption classification, so "
                "runtimeSensitiveConsumers stays empty by design; runtime execution chains belong to R5."
            ),
        }
    )
    if truncated_reasons:
        gaps.append(
            {
                "kind": "frontier-truncated",
                "truncationReasons": list(truncated_reasons),
                "message": "The bounded traversal stopped early; expand with ue_find_references for full paged edges.",
            }
        )
    return gaps


def _validation_targets(
    *,
    targets: list[dict[str, Any]],
    direct_consumers: list[dict[str, Any]],
    indirect_consumers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for target in targets:
        if not target.get("found"):
            continue
        path = str(target["assetPath"])
        entries.append(
            {
                "assetPath": path,
                "tier": 0,
                "depth": 0,
                "reason": "modified-target-self",
                "impactedTargets": [path],
                "referenceKinds": [],
                "source": IMPACT_SOURCE,
            }
        )
    for record in direct_consumers:
        entries.append(
            {
                "assetPath": str(record["assetPath"]),
                "tier": 1,
                "depth": 1,
                "reason": "direct-consumer",
                "impactedTargets": list(record["impactedTargets"]),
                "referenceKinds": [item["rawReferenceKind"] for item in record["referenceKinds"]],
                "source": IMPACT_SOURCE,
            }
        )
    for record in indirect_consumers:
        entries.append(
            {
                "assetPath": str(record["assetPath"]),
                "tier": 2,
                "depth": int(record["depth"]),
                "reason": f"indirect-consumer-depth-{record['depth']}",
                "impactedTargets": list(record["impactedTargets"]),
                "referenceKinds": [item["rawReferenceKind"] for item in record["referenceKinds"]],
                "source": IMPACT_SOURCE,
            }
        )
    for index, entry in enumerate(entries):
        entry["priorityOrder"] = index
    return entries


def _risks(
    *,
    targets: list[dict[str, Any]],
    direct_consumers: list[dict[str, Any]],
    unknown_kinds: list[str],
    truncated_reasons: list[str],
) -> list[dict[str, Any]]:
    risks: list[dict[str, Any]] = []
    for target in targets:
        if not target.get("found"):
            risks.append(
                {
                    "kind": "impact-target-not-indexed",
                    "severity": "medium",
                    "source": IMPACT_SOURCE,
                    "details": {
                        "assetPath": str(target["assetPath"]),
                        "reason": str(target.get("reason", "target-not-indexed")),
                    },
                }
            )
    fanout = max(
        (
            int(target.get("directConsumerCount", 0))
            for target in targets
            if target.get("found")
        ),
        default=0,
    )
    if fanout >= IMPACT_HIGH_FANOUT_THRESHOLD:
        fanout_targets = [
            str(target["assetPath"])
            for target in targets
            if target.get("found") and int(target.get("directConsumerCount", 0)) >= IMPACT_HIGH_FANOUT_THRESHOLD
        ]
        risks.append(
            {
                "kind": "high-fanout-target",
                "severity": "medium",
                "source": IMPACT_SOURCE,
                "details": {
                    "assetPaths": fanout_targets,
                    "directConsumerCount": fanout,
                    "threshold": IMPACT_HIGH_FANOUT_THRESHOLD,
                },
            }
        )
    if truncated_reasons:
        risks.append(
            {
                "kind": "impact-analysis-truncated",
                "severity": "medium",
                "source": IMPACT_SOURCE,
                "details": {"truncationReasons": list(truncated_reasons)},
            }
        )
    if unknown_kinds:
        risks.append(
            {
                "kind": "unknown-reference-kind",
                "severity": "info",
                "source": IMPACT_SOURCE,
                "details": {"kinds": list(unknown_kinds)},
            }
        )
    return risks


def _risk_summary(
    *,
    targets: list[dict[str, Any]],
    direct_consumers: list[dict[str, Any]],
    unknown_kinds: list[str],
    truncated_reasons: list[str],
) -> dict[str, int]:
    risks = _risks(
        targets=targets,
        direct_consumers=direct_consumers,
        unknown_kinds=unknown_kinds,
        truncated_reasons=truncated_reasons,
    )
    counts = {"high": 0, "medium": 0, "info": 0}
    for risk in risks:
        severity = str(risk.get("severity", "info"))
        counts[severity] = counts.get(severity, 0) + 1
    return {
        "count": len(risks),
        "highCount": counts.get("high", 0),
        "mediumCount": counts.get("medium", 0),
        "infoCount": counts.get("info", 0),
    }


def _next_actions(
    *,
    targets: list[dict[str, Any]],
    direct_consumers: list[dict[str, Any]],
    max_depth: int,
    max_depth_reached: int,
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    found_paths = [str(target["assetPath"]) for target in targets if target.get("found")]
    if found_paths:
        actions.append(
            {
                "tool": "ue_find_references",
                "reason": "paged-incoming-reference-edges",
                "arguments": {
                    "asset_path": found_paths[0],
                    "direction": "incoming",
                    "depth": 1,
                },
            }
        )
    if direct_consumers:
        actions.append(
            {
                "tool": "ue_get_asset",
                "reason": "expand-consumer-sections",
                "arguments": {
                    "asset_path": str(direct_consumers[0]["assetPath"]),
                    "sections": ["symbols", "references"],
                },
            }
        )
    if max_depth_reached >= max_depth and max_depth < MAX_IMPACT_DEPTH:
        actions.append(
            {
                "tool": "ue_analyze_change_impact",
                "reason": "expand-indirect-depth",
                "arguments": {
                    "target_asset_paths": found_paths,
                    "max_depth": max_depth + 1,
                },
            }
        )
    return actions


def trim_impact_response(response: dict[str, Any], max_output_tokens: int) -> dict[str, Any]:
    """Apply the fixed deterministic trimming ladder until the response fits the budget."""
    reasons: list[str] = []
    for _round in range(len(_TRIM_REASONS) + 1):
        estimate = estimate_json_tokens(response)
        if estimate <= max_output_tokens:
            break
        applied = False
        consumers = [*response.get("directConsumers", []), *response.get("indirectConsumers", [])]
        if any(record.get("paths") for record in consumers):
            for record in consumers:
                record.pop("paths", None)
            applied = True
            reason = "impact-paths"
        elif any(record.get("evidence") for record in consumers):
            for record in consumers:
                record.pop("evidence", None)
            applied = True
            reason = "consumer-evidence"
        elif response.get("indirectConsumers"):
            response["indirectConsumers"] = []
            applied = True
            reason = "indirect-consumers"
        elif any(record.get("referenceKinds") for record in response.get("directConsumers", [])):
            for record in response.get("directConsumers", []):
                record.pop("referenceKinds", None)
            applied = True
            reason = "consumer-reference-kinds"
        elif response.get("validationTargets"):
            response["validationTargets"] = []
            applied = True
            reason = "validation-targets"
        elif any(target.get("identity") for target in response.get("targets", [])):
            for target in response.get("targets", []):
                target.pop("identity", None)
            applied = True
            reason = "target-details"
        elif response.get("analysisGaps"):
            response["analysisGaps"] = []
            applied = True
            reason = "analysis-gaps"
        if not applied:
            break
        if reason not in reasons:
            reasons.append(reason)
    graph_summary = response.get("summary", {})
    graph_truncated = bool(graph_summary.get("truncated"))
    response["outputBudget"] = {
        "maxTokens": max_output_tokens,
        "estimatedTokens": estimate_json_tokens(response),
        "truncated": graph_truncated or bool(reasons),
        "truncationReasons": list(reasons),
    }
    return response
