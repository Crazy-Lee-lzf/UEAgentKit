"""Persisted deterministic L2/L3 context snapshots for automatic Task Context.

M5 core rule: automatic Task Context may only read persisted L3 plus
deterministically matched persisted L2. Nothing here may run an LLM, load a
vector model, compute embeddings, distill L0/L1, or rebuild a snapshot during a
task request. The offline explicit build path is the only writer of
``memory_context_state`` / ``memory_context_entries``.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .database import get_metadata, get_schema_version, open_database
from .memory_context import estimate_tokens
from .project_memory import open_project_memory_database

# ---------------------------------------------------------------------------
# Frozen M5 automatic injection budgets (M5 Detailed Plan sections 9-13).
# ---------------------------------------------------------------------------
L3_MAX_ESTIMATED_TOKENS = 400
L2_MAX_ESTIMATED_TOKENS = 400
COMBINED_MAX_ESTIMATED_TOKENS = 800
L2_MAX_INJECTED = 2
L2_BODY_MAX_CHARS = 200

# Deterministic build-side bounds (product constants; bounded knobs only).
MAX_L2_GROUPS = 8
MAX_L3_ENTRIES = 48
L3_ENTRY_BODY_MAX_CHARS = 320
L2_RECIPE_VERIFIED_MIN_SUPPORT = 3
L2_RECIPE_MIN_REJECTION_COUNT = 2
L2_RECIPE_MAX_REJECTION_CODES = 2
L3_NAMING_MIN_SAMPLE = 5
L3_NAMING_MIN_DOMINANT_SHARE = 0.80
L3_NAMING_MAX_CONVENTIONS = 3
L3_KNOWN_ISSUE_MIN_COUNT = 3
L3_KNOWN_ISSUE_MAX_PATTERNS = 3

# Deterministic M3 rule ids that prove a verified durable write event. A
# tool-observed L1 record only counts as an L2 verified-success source when its
# M3 distillation metadata carries one of these rule ids AND a deterministic
# operation; user-confirmed records count when they carry a deterministic
# operation and an exact /Game asset binding.
L2_VERIFIED_RULE_IDS = frozenset(
    {
        "l1.verified-write.v1",
        "l1.supersession.v1",
        "l1.semantic-diff.v1",
    }
)

L3_PRIORITY_RULE_USER = 1
L3_PRIORITY_RULE_TOOL = 2
L3_PRIORITY_CONVENTION = 3
L3_PRIORITY_KNOWN_ISSUE = 4

_SAFE_OPERATION = re.compile(r"^[A-Za-z0-9_.:\-]{1,128}$")
_TOKEN_SPLIT = re.compile(r"[A-Za-z0-9]+")
_ASSET_PATH_LIKE = re.compile(r"^/Game/")

_SNAPSHOT_REASON_MISSING = "context-snapshot-missing"
_SNAPSHOT_REASON_STALE = "context-snapshot-stale"
_SNAPSHOT_REASON_INDEX_MISMATCH = "index-snapshot-mismatch"


@dataclass(frozen=True)
class MemoryContextBuildResult:
    project_key: str
    source_generation: int
    built_generation: int
    snapshot_id: str
    index_snapshot_id: str
    l2_entries: int
    l3_entries: int
    content_chars: int
    estimated_tokens: int
    reused: bool
    elapsed_ms: float
    reason: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "schemaVersion": "1.0",
            "tool": "ue_memory_build_context",
            "projectKey": self.project_key,
            "sourceGeneration": self.source_generation,
            "builtGeneration": self.built_generation,
            "snapshotId": self.snapshot_id,
            "indexSnapshotId": self.index_snapshot_id,
            "l2Entries": self.l2_entries,
            "l3Entries": self.l3_entries,
            "contentChars": self.content_chars,
            "estimatedTokens": self.estimated_tokens,
            "reused": self.reused,
            "elapsedMs": round(self.elapsed_ms, 3),
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# Canonical serialization / digest helpers
# ---------------------------------------------------------------------------


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_text(value: str, *, prefix: str = "sha256:") -> str:
    return prefix + hashlib.sha256(value.encode("utf-8")).hexdigest()


def entry_id_for(project_key: str, layer: str, context_key: str) -> str:
    digest = hashlib.sha256(
        canonical_json({"projectKey": project_key, "layer": layer, "contextKey": context_key}).encode(
            "utf-8"
        )
    ).hexdigest()
    return f"ctx_{digest[:32]}"


def snapshot_id_for(
    *,
    project_key: str,
    source_generation: int,
    index_snapshot_id: str,
    ordered_content_digests: Sequence[tuple[str, int, str]],
) -> str:
    digest = hashlib.sha256(
        canonical_json(
            {
                "projectKey": project_key,
                "sourceGeneration": source_generation,
                "indexSnapshotId": index_snapshot_id,
                "entries": [
                    {"layer": layer, "ordinal": ordinal, "contentSha256": content_sha256}
                    for (layer, ordinal, content_sha256) in ordered_content_digests
                ],
            }
        ).encode("utf-8")
    ).hexdigest()
    return f"ctxsnap_{digest[:32]}"


def index_snapshot_id_from_database(path: Path) -> str:
    """Return the byte-identical immutable-index snapshot id used at request time.

    Mirrors the fixed-index snapshot id derivation exactly so a snapshot built
    against an index file stays injectable while that file is unchanged and
    becomes ``index-snapshot-mismatch`` as soon as the index changes.
    """
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Index database not found: {resolved}")
    stat = resolved.stat()
    with open_database(resolved, readonly=True, migrate=False, immutable=True) as connection:
        payload = {
            "size": stat.st_size,
            "modifiedNs": stat.st_mtime_ns,
            "schema": get_schema_version(connection),
            "projectKey": get_metadata(connection, "project_key", ""),
            "lastIndexedAtUtc": get_metadata(connection, "last_indexed_at_utc", ""),
        }
        canonical = canonical_json(payload)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Deterministic text / token helpers
# ---------------------------------------------------------------------------


def collapse_text(value: Any, *, maximum: int, field: str) -> str:
    if not isinstance(value, str):
        return ""
    normalized = " ".join(value.split())
    if len(normalized) > maximum:
        normalized = normalized[:maximum]
    if not normalized:
        return ""
    return normalized


def text_tokens(value: str) -> tuple[str, ...]:
    return tuple(sorted({token.casefold() for token in _TOKEN_SPLIT.findall(value) if token}))


def _slug(value: str) -> str:
    parts = [part for part in re.split(r"[^A-Za-z0-9_]+", value) if part]
    return "_".join(parts) or "item"


def _operation_of(details: dict[str, Any]) -> str:
    if not isinstance(details, dict):
        return ""
    distillation = details.get("distillation")
    raw = ""
    if isinstance(distillation, dict) and isinstance(distillation.get("operation"), str):
        raw = distillation["operation"]
    elif isinstance(details.get("operation"), str):
        raw = details["operation"]
    raw = raw.strip()
    if not raw or not _SAFE_OPERATION.match(raw):
        return ""
    return raw


def _rule_id_of(details: dict[str, Any]) -> str:
    if not isinstance(details, dict):
        return ""
    distillation = details.get("distillation")
    if isinstance(distillation, dict) and isinstance(distillation.get("ruleId"), str):
        return distillation["ruleId"].strip()
    return ""


def _error_code_of(details: dict[str, Any]) -> str:
    if not isinstance(details, dict):
        return ""
    distillation = details.get("distillation")
    if isinstance(distillation, dict) and isinstance(distillation.get("errorCode"), str):
        return distillation["errorCode"].strip()
    if isinstance(details.get("errorCode"), str):
        return details["errorCode"].strip()
    return ""


def _stable_target_of(details: dict[str, Any]) -> str:
    if not isinstance(details, dict):
        return ""
    distillation = details.get("distillation")
    if isinstance(distillation, dict) and isinstance(distillation.get("stableTargetKey"), str):
        return distillation["stableTargetKey"].strip()
    if isinstance(details.get("stableTargetKey"), str):
        return details["stableTargetKey"].strip()
    return ""


def _asset_path_of(details: dict[str, Any]) -> str:
    """Deterministic /Game asset path fallback for distillation provenance."""
    if not isinstance(details, dict):
        return ""
    distillation = details.get("distillation")
    if isinstance(distillation, dict) and isinstance(distillation.get("primaryAssetPath"), str):
        primary = distillation["primaryAssetPath"].strip()
        if _ASSET_PATH_LIKE.match(primary):
            return primary
    bindings = distillation.get("sourceBindings") if isinstance(distillation, dict) else None
    if isinstance(bindings, list):
        for binding in bindings:
            if not isinstance(binding, dict):
                continue
            key = str(binding.get("key", ""))
            if _ASSET_PATH_LIKE.match(key):
                return key
    return ""


# ---------------------------------------------------------------------------
# Index facts read once per offline build.
# ---------------------------------------------------------------------------


def _read_asset_classes(connection: sqlite3.Connection) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for row in connection.execute(
        "SELECT asset_path, asset_class FROM assets WHERE asset_class <> '' ORDER BY asset_path"
    ):
        mapping[str(row["asset_path"])] = str(row["asset_class"])
    return mapping


def _naming_conventions(connection: sqlite3.Connection) -> tuple[tuple[str, str, int, int], ...]:
    """Return ``(asset_class, prefix, count, class_total)`` tuples above threshold."""
    totals: dict[str, int] = {}
    prefixes: dict[tuple[str, str], int] = {}
    rows = connection.execute(
        "SELECT asset_class, asset_name FROM assets WHERE asset_class <> '' AND asset_name <> ''"
    ).fetchall()
    for row in rows:
        asset_class = str(row["asset_class"])
        asset_name = str(row["asset_name"])
        totals[asset_class] = totals.get(asset_class, 0) + 1
        underscore = asset_name.find("_")
        if underscore <= 0:
            continue
        prefix = asset_name[: underscore + 1]
        key = (asset_class, prefix)
        prefixes[key] = prefixes.get(key, 0) + 1
    candidates: list[tuple[float, int, str, str, str, str]] = []
    for (asset_class, prefix), count in prefixes.items():
        total = totals.get(asset_class, 0)
        if total < L3_NAMING_MIN_SAMPLE or count < L3_NAMING_MIN_SAMPLE:
            continue
        share = count / total
        if share < L3_NAMING_MIN_DOMINANT_SHARE:
            continue
        # stable rank: share DESC, count DESC, assetClass ASC, prefix ASC
        candidates.append(
            (-share, -count, asset_class.casefold(), prefix.casefold(), asset_class, prefix)
        )
    candidates.sort()
    return tuple((asset_class, prefix, count, totals[asset_class]) for _, _, _, _, asset_class, prefix in candidates[: L3_NAMING_MAX_CONVENTIONS])


@dataclass(frozen=True)
class ContextSourceRow:
    record_id: str
    record_type: str
    source_kind: str
    subject_key: str
    title: str
    body: str
    details: dict[str, Any]
    asset_paths: tuple[str, ...]


def _eligible_source_rows(connection: sqlite3.Connection, project_key: str) -> tuple[ContextSourceRow, ...]:
    rows = connection.execute(
        """
        SELECT record_id, record_type, source_kind, subject_key, title, body, details_json
        FROM memory_records
        WHERE project_key = ? AND status = 'valid'
          AND source_kind IN ('user-confirmed', 'tool-observed')
        ORDER BY record_id
        """,
        (project_key,),
    ).fetchall()
    scopes_rows = connection.execute(
        """
        SELECT record_id, scope_key
        FROM memory_scopes
        WHERE scope_type = 'asset' AND scope_key LIKE '/Game/%'
        ORDER BY record_id, scope_key
        """
    ).fetchall()
    scopes: dict[str, list[str]] = {}
    for row in scopes_rows:
        scopes.setdefault(str(row["record_id"]), []).append(str(row["scope_key"]))
    sources: list[ContextSourceRow] = []
    for row in rows:
        record_id = str(row["record_id"])
        try:
            details = json.loads(str(row["details_json"]))
        except (TypeError, ValueError):
            details = {}
        if not isinstance(details, dict):
            details = {}
        paths = tuple(scopes.get(record_id, ()))
        if not paths:
            primary = _asset_path_of(details)
            if primary:
                paths = (primary,)
        sources.append(
            ContextSourceRow(
                record_id=record_id,
                record_type=str(row["record_type"]),
                source_kind=str(row["source_kind"]),
                subject_key=str(row["subject_key"]),
                title=str(row["title"]),
                body=str(row["body"]),
                details=details,
                asset_paths=paths,
            )
        )
    return tuple(sources)


# ---------------------------------------------------------------------------
# L2 recipe building
# ---------------------------------------------------------------------------


def _l2_verified_success(row: ContextSourceRow) -> bool:
    if row.source_kind == "user-confirmed":
        return row.record_type in {"projectFact", "decisionRecord", "taskRecord"}
    if row.source_kind == "tool-observed":
        return _rule_id_of(row.details) in L2_VERIFIED_RULE_IDS
    return False


def _build_l2_recipes(
    connection: sqlite3.Connection,
    *,
    project_key: str,
    sources: Sequence[ContextSourceRow],
    asset_class_by_path: dict[str, str],
    max_groups: int,
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[ContextSourceRow]] = {}
    for row in sources:
        operation = _operation_of(row.details)
        if not operation:
            continue
        if not _l2_verified_success(row):
            continue
        path = next((candidate for candidate in row.asset_paths if _ASSET_PATH_LIKE.match(candidate)), "")
        if not path:
            continue
        asset_class = asset_class_by_path.get(path, "")
        if not asset_class:
            continue
        groups.setdefault((operation, asset_class), []).append(row)

    # Common durable rejection codes for the same operation + asset class from
    # valid deterministic knownIssue records.
    rejection_by_group: dict[tuple[str, str], dict[str, int]] = {}
    for row in sources:
        if row.record_type != "knownIssue":
            continue
        operation = _operation_of(row.details)
        code = _error_code_of(row.details)
        if not operation or not code:
            continue
        path = next((candidate for candidate in row.asset_paths if _ASSET_PATH_LIKE.match(candidate)), "")
        if not path:
            path = _asset_path_of(row.details)
        if not path:
            continue
        asset_class = asset_class_by_path.get(path, "")
        if not asset_class:
            continue
        counts = rejection_by_group.setdefault((operation, asset_class), {})
        counts[code] = counts.get(code, 0) + 1

    recipes: list[dict[str, Any]] = []
    for (operation, asset_class) in sorted(groups):
        group_rows = sorted(groups[(operation, asset_class)], key=lambda item: item.record_id)
        support = len(group_rows)
        if support < L2_RECIPE_VERIFIED_MIN_SUPPORT:
            continue
        target_counts: dict[str, int] = {}
        for row in group_rows:
            target = _stable_target_of(row.details)
            if target:
                target_counts[target] = target_counts.get(target, 0) + 1
        majority_target = ""
        if target_counts:
            top_target = max(target_counts, key=lambda key: (target_counts[key], key.casefold()))
            if target_counts[top_target] * 2 > support:
                majority_target = collapse_text(top_target, maximum=96, field="stableTargetKey")
        rejection_counts = rejection_by_group.get((operation, asset_class), {})
        common_codes = sorted(
            [
                (count, code.casefold(), code)
                for code, count in rejection_counts.items()
                if count >= L2_RECIPE_MIN_REJECTION_COUNT
            ],
            key=lambda item: (-item[0], item[1]),
        )[: L2_RECIPE_MAX_REJECTION_CODES]

        parts = [f"{asset_class} {operation}: {support} verified writes"]
        if majority_target:
            parts.append(f"target={majority_target}")
        for count, _, code in common_codes:
            parts.append(f"common rejection={code} ({count})")
        body = "; ".join(parts)
        if len(body) > L2_BODY_MAX_CHARS:
            # Deterministically drop optional rejection/target parts first.
            kept: list[str] = [f"{asset_class} {operation}: {support} verified writes"]
            if majority_target and len("; ".join(kept + [f"target={majority_target}"])) <= L2_BODY_MAX_CHARS:
                kept.append(f"target={majority_target}")
            body = "; ".join(kept)
            if len(body) > L2_BODY_MAX_CHARS:
                body = body[:L2_BODY_MAX_CHARS]
        tokens = text_tokens(" ".join([operation, asset_class, majority_target]))
        match_json = {
            "operation": operation,
            "assetClass": asset_class,
            "support": support,
            "tokens": list(tokens),
        }
        recipes.append(
            {
                "layer": "L2",
                "operation": operation,
                "assetClass": asset_class,
                "title": collapse_text(
                    f"Recipe: {operation} ({asset_class})", maximum=128, field="title"
                ),
                "body": body,
                "match": match_json,
                "support": support,
                "bindings": [
                    {"recordId": row.record_id, "sourceKind": row.source_kind}
                    for row in group_rows
                ],
            }
        )
    recipes.sort(key=lambda item: (item["assetClass"].casefold(), item["operation"].casefold()))
    return recipes[:max_groups]


# ---------------------------------------------------------------------------
# L3 entry building
# ---------------------------------------------------------------------------


def _l3_rule_entries(sources: Sequence[ContextSourceRow]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for row in sources:
        if row.record_type != "projectRule":
            continue
        if row.source_kind == "tool-observed" and not _rule_id_of(row.details):
            # Tool-observed project rules require deterministic provenance.
            continue
        priority = L3_PRIORITY_RULE_USER if row.source_kind == "user-confirmed" else L3_PRIORITY_RULE_TOOL
        title = collapse_text(row.title, maximum=200, field="title")
        body_text = collapse_text(row.body, maximum=L3_ENTRY_BODY_MAX_CHARS, field="body")
        if not title and not body_text:
            continue
        body = f"Rule: {title}. {body_text}" if body_text else f"Rule: {title}"
        if len(body) > L3_ENTRY_BODY_MAX_CHARS + 16:
            body = body[: L3_ENTRY_BODY_MAX_CHARS + 16]
        entries.append(
            {
                "layer": "L3",
                "priority": priority,
                "stable_key": (row.subject_key, row.record_id),
                "title": "project-rule",
                "body": body,
                "bindings": [{"recordId": row.record_id, "sourceKind": row.source_kind}],
            }
        )
    return entries


def _l3_known_issue_entries(sources: Sequence[ContextSourceRow]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in sources:
        if row.record_type != "knownIssue":
            continue
        code = _error_code_of(row.details)
        label = code if code else _slug(row.subject_key)
        if not label:
            continue
        bucket = grouped.setdefault(label, {"label": label, "record_ids": []})
        bucket["record_ids"].append(row.record_id)
    patterns = sorted(
        [
            (len(bucket["record_ids"]), bucket["label"].casefold(), bucket["label"])
            for bucket in grouped.values()
        ],
        key=lambda item: (-item[0], item[1]),
    )[: L3_KNOWN_ISSUE_MAX_PATTERNS]
    entries: list[dict[str, Any]] = []
    for count, _, label in patterns:
        if count < L3_KNOWN_ISSUE_MIN_COUNT:
            continue
        clean_label = collapse_text(label, maximum=160, field="issueLabel")
        entries.append(
            {
                "layer": "L3",
                "priority": L3_PRIORITY_KNOWN_ISSUE,
                "stable_key": ("knownIssue", clean_label),
                "title": "known-issue-pattern",
                "body": f"Issue: {clean_label} ({count} occurrences).",
                "bindings": [{"recordId": record_id, "sourceKind": "tool-observed"} for record_id in sorted(grouped[clean_label]["record_ids"])],
            }
        )
    return entries


# ---------------------------------------------------------------------------
# Snapshot persistence / freshness / injection
# ---------------------------------------------------------------------------


def _state_row(connection: sqlite3.Connection, project_key: str) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM memory_context_state WHERE project_key = ?",
        (project_key,),
    ).fetchone()
    if row is None:
        return None
    return {
        "source_generation": int(row["source_generation"]),
        "built_generation": int(row["built_generation"]),
        "snapshot_id": str(row["snapshot_id"]),
        "index_snapshot_id": str(row["index_snapshot_id"]),
        "source_digest": str(row["source_digest"]),
        "built_at_utc": str(row["built_at_utc"]),
    }


def snapshot_freshness(
    connection: sqlite3.Connection,
    *,
    project_key: str,
    index_snapshot_id: str,
) -> tuple[bool, str, dict[str, Any] | None]:
    """Return ``(usable, reason, state)``.

    A single ``memory_context_state`` row read is the entire request-path
    freshness cost. No records scan, no digest recomputation, no rebuild.
    """
    state = _state_row(connection, project_key)
    if state is None or not state["snapshot_id"]:
        return False, _SNAPSHOT_REASON_MISSING, state
    if state["built_generation"] != state["source_generation"]:
        return False, _SNAPSHOT_REASON_STALE, state
    if not index_snapshot_id or state["index_snapshot_id"] != index_snapshot_id:
        return False, _SNAPSHOT_REASON_INDEX_MISMATCH, state
    return True, "", state


def _read_entries(connection: sqlite3.Connection, project_key: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT layer, context_key, ordinal, title, body, match_json,
               source_bindings_json, content_sha256
        FROM memory_context_entries
        WHERE project_key = ?
        ORDER BY CASE layer WHEN 'L3' THEN 0 WHEN 'L2' THEN 1 ELSE 2 END,
                 ordinal, context_key
        """,
        (project_key,),
    ).fetchall()
    entries: list[dict[str, Any]] = []
    for row in rows:
        try:
            match = json.loads(str(row["match_json"]))
        except (TypeError, ValueError):
            match = {}
        if not isinstance(match, dict):
            match = {}
        entries.append(
            {
                "layer": str(row["layer"]),
                "context_key": str(row["context_key"]),
                "ordinal": int(row["ordinal"]),
                "title": str(row["title"]),
                "body": str(row["body"]),
                "match": match,
                "content_sha256": str(row["content_sha256"]),
            }
        )
    return entries


def _entries_equal(a: Sequence[dict[str, Any]], b: Sequence[dict[str, Any]]) -> bool:
    if len(a) != len(b):
        return False
    for left, right in zip(a, b):
        for key in ("layer", "context_key", "ordinal", "title", "body", "match", "content_sha256"):
            if left.get(key) != right.get(key):
                return False
    return True


def get_injection_context(
    connection: sqlite3.Connection,
    *,
    project_key: str,
    query: str = "",
    asset_classes: Sequence[str] = (),
    index_snapshot_id: str = "",
) -> dict[str, Any]:
    """Read the current valid snapshot and return a bounded automatic payload.

    Never writes, never rebuilds, never loads a model. A stale or missing
    snapshot returns empty text with a stable reason.
    """
    usable, reason, state = snapshot_freshness(
        connection, project_key=project_key, index_snapshot_id=index_snapshot_id
    )
    if not usable or state is None:
        return {
            "available": False,
            "stale": True,
            "reason": reason,
            "snapshotId": state["snapshot_id"] if state else "",
            "injectionHash": "",
            "text": "",
            "l3Count": 0,
            "l2Count": 0,
            "totalL3Count": 0,
            "totalL2Count": 0,
            "contentChars": 0,
            "estimatedTokens": 0,
        }

    entries = _read_entries(connection, project_key)
    l3_entries = [entry for entry in entries if entry["layer"] == "L3"]
    l2_candidates = [entry for entry in entries if entry["layer"] == "L2"]
    total_l3 = len(l3_entries)
    total_l2 = len(l2_candidates)

    query_tokens = set(text_tokens(query))
    class_set = {str(item) for item in asset_classes if isinstance(item, str) and item}

    def rank_key(entry: dict[str, Any]) -> tuple[int, int, int, str]:
        match = entry.get("match", {})
        asset_class = str(match.get("assetClass", ""))
        tokens = [str(token) for token in match.get("tokens", []) if isinstance(token, str)]
        overlap = len(query_tokens.intersection(tokens)) if query_tokens else 0
        exact_class = 1 if asset_class in class_set else 0
        support = int(match.get("support", 0))
        return (-exact_class, -overlap, -support, entry["context_key"])

    matched: list[dict[str, Any]] = []
    if query_tokens:
        scored: list[tuple[tuple[int, int, int, str], dict[str, Any]]] = []
        for entry in l2_candidates:
            match = entry.get("match", {})
            tokens = [str(token) for token in match.get("tokens", []) if isinstance(token, str)]
            overlap = len(query_tokens.intersection(tokens))
            if overlap <= 0:
                continue  # positive task-token evidence is required
            scored.append((rank_key(entry), entry))
        scored.sort(key=lambda item: item[0])
        matched = [entry for _, entry in scored[:L2_MAX_INJECTED]]

    lines: list[str] = []
    for entry in l3_entries:
        lines.append(f"L3: {entry['body']}")
    matched_lines: dict[str, str] = {}
    for entry in matched:
        line = f"L2: {entry['body']}"
        lines.append(line)
        matched_lines[entry["context_key"]] = line

    def combined_chars(candidate_lines: Sequence[str]) -> int:
        return sum(len(line) + 1 for line in candidate_lines)

    if estimate_tokens(combined_chars(lines)) > COMBINED_MAX_ESTIMATED_TOKENS:
        while matched and estimate_tokens(combined_chars(lines)) > COMBINED_MAX_ESTIMATED_TOKENS:
            removed = matched.pop()
            lines.remove(matched_lines[removed["context_key"]])
        while l3_entries and estimate_tokens(combined_chars(lines)) > COMBINED_MAX_ESTIMATED_TOKENS:
            removed = l3_entries.pop()
            lines.remove(f"L3: {removed['body']}")

    text = "\n".join(lines)
    content_chars = len(text)
    return {
        "available": True,
        "stale": False,
        "reason": "",
        "snapshotId": state["snapshot_id"],
        "injectionHash": sha256_text(text) if text else "",
        "text": text,
        "l3Count": len([line for line in lines if line.startswith("L3: ")]),
        "l2Count": len([line for line in lines if line.startswith("L2: ")]),
        "totalL3Count": total_l3,
        "totalL2Count": total_l2,
        "contentChars": content_chars,
        "estimatedTokens": estimate_tokens(content_chars) if content_chars else 0,
    }


def _entry_content_sha256(
    project_key: str,
    layer: str,
    context_key: str,
    title: str,
    body: str,
    match: dict[str, Any],
) -> str:
    return sha256_text(
        canonical_json(
            {
                "projectKey": project_key,
                "layer": layer,
                "contextKey": context_key,
                "title": title,
                "body": body,
                "match": match,
            }
        )
    )


def _canonical_context_key(
    project_key: str,
    layer: str,
    title: str,
    body: str,
    match: dict[str, Any],
) -> str:
    return sha256_text(
        canonical_json(
            {
                "projectKey": project_key,
                "layer": layer,
                "title": title,
                "body": body,
                "match": match,
            }
        ),
        prefix="ctxkey_",
    )


def build_memory_context_snapshot(
    *,
    memory_database: Path,
    project_key: str,
    index_database: Path,
    max_l2_groups: int = MAX_L2_GROUPS,
    max_l3_entries: int = MAX_L3_ENTRIES,
) -> MemoryContextBuildResult:
    """Offline deterministic rebuild of one project's L2/L3 snapshot.

    Atomic and restart-safe: entries are replaced and state is advanced inside a
    single BEGIN IMMEDIATE transaction. If the process dies before COMMIT, the
    previous snapshot remains intact.
    """
    started = time.perf_counter()
    if isinstance(max_l2_groups, bool) or not isinstance(max_l2_groups, int) or max_l2_groups < 0:
        raise ValueError("max_l2_groups must be a non-negative integer.")
    if isinstance(max_l3_entries, bool) or not isinstance(max_l3_entries, int) or max_l3_entries < 0:
        raise ValueError("max_l3_entries must be a non-negative integer.")
    effective_max_l2 = min(max_l2_groups, MAX_L2_GROUPS)
    effective_max_l3 = min(max_l3_entries, MAX_L3_ENTRIES)

    resolved_memory = memory_database.expanduser().resolve()
    resolved_index = index_database.expanduser().resolve()
    if not resolved_index.is_file():
        raise FileNotFoundError(f"Index database not found: {resolved_index}")

    with open_database(resolved_index, readonly=True, migrate=False, immutable=True) as index_connection:
        index_project_key = get_metadata(index_connection, "project_key", "")
        if index_project_key and index_project_key != project_key:
            raise ValueError(
                "Index database project does not match the Memory project: "
                f"expected {project_key!r}, found {index_project_key!r}."
            )
        index_id = index_snapshot_id_from_database(resolved_index)
        asset_class_by_path = _read_asset_classes(index_connection)
        conventions = _naming_conventions(index_connection)

    with open_project_memory_database(resolved_memory) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            state = _state_row(connection, project_key)
            source_generation = state["source_generation"] if state else 0

            sources = _eligible_source_rows(connection, project_key)
            l2_recipes = _build_l2_recipes(
                connection,
                project_key=project_key,
                sources=sources,
                asset_class_by_path=asset_class_by_path,
                max_groups=effective_max_l2,
            )
            l3_raw: list[dict[str, Any]] = []
            l3_raw.extend(_l3_rule_entries(sources))
            for asset_class, prefix, count, total in conventions:
                pct = int(round(100.0 * count / total))
                body = f"Convention: {asset_class} assets use {prefix} prefix ({pct}%, n={count})."
                l3_raw.append(
                    {
                        "layer": "L3",
                        "priority": L3_PRIORITY_CONVENTION,
                        "stable_key": ("convention", f"{asset_class} {prefix}"),
                        "title": "naming-convention",
                        "body": body,
                        "bindings": [],
                    }
                )
            l3_raw.extend(_l3_known_issue_entries(sources))

            def l3_sort_key(item: dict[str, Any]) -> tuple[int, str, str]:
                return (item["priority"], str(item["stable_key"][0]).casefold(), str(item["stable_key"][1]))

            l3_ordered = sorted(l3_raw, key=l3_sort_key)[:effective_max_l3]

            def l3_line(body: str) -> str:
                return f"L3: {body}"

            def l3_budget_lines(entries: Sequence[dict[str, Any]]) -> list[str]:
                return [l3_line(entry["body"]) for entry in entries]

            def l3_chars(entries: Sequence[dict[str, Any]]) -> int:
                return sum(len(line) + 1 for line in l3_budget_lines(entries))

            while l3_ordered and estimate_tokens(l3_chars(l3_ordered)) > L3_MAX_ESTIMATED_TOKENS:
                l3_ordered.pop()

            all_entries: list[dict[str, Any]] = []
            for index, item in enumerate(l3_ordered):
                body = item["body"]
                context_key = _canonical_context_key(project_key, "L3", item["title"], body, {})
                all_entries.append(
                    {
                        "layer": "L3",
                        "context_key": context_key,
                        "ordinal": index,
                        "title": item["title"],
                        "body": body,
                        "match": {},
                        "bindings": item.get("bindings", []),
                    }
                )
            for index, recipe in enumerate(l2_recipes):
                context_key = _canonical_context_key(
                    project_key,
                    "L2",
                    recipe["title"],
                    recipe["body"],
                    recipe["match"],
                )
                all_entries.append(
                    {
                        "layer": "L2",
                        "context_key": context_key,
                        "ordinal": index,
                        "title": recipe["title"],
                        "body": recipe["body"],
                        "match": recipe["match"],
                        "bindings": recipe.get("bindings", []),
                    }
                )
            l3_entries = [entry for entry in all_entries if entry["layer"] == "L3"]
            l2_entries = [entry for entry in all_entries if entry["layer"] == "L2"]
            for entry in all_entries:
                entry["content_sha256"] = _entry_content_sha256(
                    project_key,
                    entry["layer"],
                    entry["context_key"],
                    entry["title"],
                    entry["body"],
                    entry["match"],
                )
            ordered_digests = [
                (
                    entry["layer"],
                    entry["ordinal"],
                    entry["content_sha256"],
                )
                for entry in all_entries
            ]
            snapshot_id = snapshot_id_for(
                project_key=project_key,
                source_generation=source_generation,
                index_snapshot_id=index_id,
                ordered_content_digests=ordered_digests,
            )

            # Reuse when the persisted snapshot is byte-equivalent to the draft.
            reused = False
            if (
                state is not None
                and state["snapshot_id"] == snapshot_id
                and state["index_snapshot_id"] == index_id
                and state["built_generation"] == source_generation
            ):
                stored = _read_entries(connection, project_key)
                if _entries_equal(stored, all_entries):
                    reused = True

            if not reused:
                connection.execute(
                    "DELETE FROM memory_context_entries WHERE project_key = ?", (project_key,)
                )
                for entry in all_entries:
                    connection.execute(
                        """
                        INSERT INTO memory_context_entries(
                            entry_id, project_key, layer, context_key, ordinal, title,
                            body, match_json, source_bindings_json, content_sha256
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            entry_id_for(project_key, entry["layer"], entry["context_key"]),
                            project_key,
                            entry["layer"],
                            entry["context_key"],
                            entry["ordinal"],
                            entry["title"],
                            entry["body"],
                            canonical_json(entry["match"]),
                            canonical_json(entry["bindings"]),
                            entry["content_sha256"],
                        ),
                    )
                built_at = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
                connection.execute(
                    """
                    INSERT INTO memory_context_state(
                        project_key, source_generation, built_generation, snapshot_id,
                        index_snapshot_id, source_digest, built_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(project_key) DO UPDATE SET
                        built_generation = excluded.built_generation,
                        snapshot_id = excluded.snapshot_id,
                        index_snapshot_id = excluded.index_snapshot_id,
                        source_digest = excluded.source_digest,
                        built_at_utc = excluded.built_at_utc
                    """,
                    (
                        project_key,
                        source_generation,
                        source_generation,
                        snapshot_id,
                        index_id,
                        sha256_text(
                            canonical_json(
                                {
                                    "sourceGeneration": source_generation,
                                    "indexSnapshotId": index_id,
                                }
                            )
                        ),
                        built_at,
                    ),
                )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    # Representative injected text for reporting: L3 (full) + up to 2 L2 recipes.
    preview_entries = l3_entries + l2_entries[:L2_MAX_INJECTED]
    preview_text = "\n".join(
        f"{entry['layer']}: {entry['body']}" for entry in preview_entries
    )
    preview_chars = len(preview_text)
    return MemoryContextBuildResult(
        project_key=project_key,
        source_generation=source_generation,
        built_generation=source_generation,
        snapshot_id=snapshot_id,
        index_snapshot_id=index_id,
        l2_entries=len(l2_entries),
        l3_entries=len(l3_entries),
        content_chars=preview_chars,
        estimated_tokens=estimate_tokens(preview_chars) if preview_chars else 0,
        reused=reused,
        elapsed_ms=(time.perf_counter() - started) * 1000.0,
    )
