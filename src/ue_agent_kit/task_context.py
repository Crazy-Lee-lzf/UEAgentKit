from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Sequence

from .agent_api import IDENTITY_FIELDS, IndexQueryService
from .memory_context import MAX_CONTEXT_CHARS, MIN_CONTEXT_CHARS, ContextBudget, RecallBudget
from .memory_service import ProjectMemoryService, ProjectMemoryServiceError
from .query_protocol import (
    DEFAULT_OUTPUT_TOKEN_BUDGET,
    estimate_json_tokens,
    normalize_output_token_budget,
)

TASK_CONTEXT_SCHEMA_VERSION = "1.2"
MAX_TASK_CONTEXT_ASSETS = 10
MAX_QUERY_CHARS = 2048
MAX_ASSET_PATH_CHARS = 512
MAX_WORK_ITEM_ID_CHARS = 128
MAX_CHANGE_SET_ID_CHARS = 64
MAX_TASK_CONTEXT_EXPANSIONS = 10
MAX_MEMORY_STALE_SAMPLES = 5
MEMORY_BUDGET_FRACTION = 0.35
BUDGET_ENVELOPE_SLACK_CHARS = 128
CHANGE_SET_TERMINAL_STATUSES = {"undone", "discarded", "verified", "no-op", "failed"}
MAX_TASK_CONTEXT_CANDIDATES = 8
MAX_CANDIDATE_SEARCH_TERMS = 8
MAX_CANDIDATES_PER_TERM = 8
CANDIDATE_MATCH_KIND_RANK = {
    "asset-name-exact": 0,
    "symbol-name-exact": 1,
    "asset-name-prefix": 2,
    "symbol-name-prefix": 3,
    "asset-name-substring": 4,
    "symbol-name-substring": 5,
    "asset-path-substring": 6,
    "asset-search-hit": 7,
    "symbol-search-hit": 8,
}

# R0.3 Cross-source Correlation bounds. Every join is read-only, computed per
# request, and derives from exact keys only (session id, asset path, change set
# id literal). No Memory/Change Set schema is extended and no workflow journal
# is written; the private _change_sets mapping is never scanned.
MAX_CORRELATION_LINKS = 16
MAX_CORRELATION_WORK_ITEMS = 5
MAX_CORRELATION_WORK_ASSETS = 4
MAX_CORRELATION_CHANGE_SET_ASSETS = 8
MAX_CORRELATION_EVIDENCE_PER_ASSET = 3
MAX_CORRELATION_EVIDENCE_LOOKUPS = 12
CORRELATION_METHOD = "deterministic-key-matching"
CORRELATION_KIND_RANK = {
    "change-set-editor-session": 0,
    "change-set-asset-in-editor": 1,
    "change-set-asset-memory-evidence": 2,
    "work-change-set-asset-overlap": 3,
    "work-references-change-set": 4,
    "work-asset-in-editor": 5,
    "work-asset-memory-evidence": 6,
}

_SEARCH_TOKEN_PATTERN = re.compile(r"[\w./:-]+", flags=re.UNICODE)


def _candidate_terms(query: str) -> list[str]:
    """Split a query into deterministic search terms for candidate discovery.

    Terms preserve query order and are deduplicated. Terms without any
    alphanumeric character are dropped because a pure-punctuation term would
    match the whole index through the SQL LIKE fallback.
    """
    terms: list[str] = []
    for term in _SEARCH_TOKEN_PATTERN.findall(query):
        if not term or not any(character.isalnum() for character in term):
            continue
        if term not in terms:
            terms.append(term)
    return terms[:MAX_CANDIDATE_SEARCH_TERMS]


def _asset_match_kind(term: str, hit: dict[str, Any]) -> str:
    folded = term.casefold()
    name = str(hit.get("asset_name", "")).casefold()
    if name == folded:
        return "asset-name-exact"
    if name.startswith(folded):
        return "asset-name-prefix"
    if folded in name:
        return "asset-name-substring"
    path = str(hit.get("asset_path", "")).casefold()
    package = str(hit.get("package_name", "")).casefold()
    if folded in path or folded in package:
        return "asset-path-substring"
    return "asset-search-hit"


def _symbol_match_kind(term: str, hit: dict[str, Any]) -> str:
    folded = term.casefold()
    name = str(hit.get("name", "")).casefold()
    if name == folded:
        return "symbol-name-exact"
    if name.startswith(folded):
        return "symbol-name-prefix"
    if folded in name:
        return "symbol-name-substring"
    return "symbol-search-hit"


def _better_match_kind(current: str, incoming: str) -> str:
    current_rank = CANDIDATE_MATCH_KIND_RANK.get(current, 100)
    incoming_rank = CANDIDATE_MATCH_KIND_RANK.get(incoming, 100)
    return incoming if incoming_rank < current_rank else current


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _clean_text(value: str, *, name: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string.")
    cleaned = value.strip()
    if len(cleaned) > maximum:
        raise ValueError(f"{name} must not exceed {maximum} characters.")
    if any(ord(character) < 32 for character in cleaned):
        raise ValueError(f"{name} must not contain control characters.")
    return cleaned


def _validate_query(value: str) -> str:
    cleaned = _clean_text(value, name="query", maximum=MAX_QUERY_CHARS)
    if not cleaned:
        raise ValueError("query is required.")
    return cleaned


def _validate_asset_paths(value: Sequence[str] | None) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError("asset_paths must be an array of strings.")
    if len(value) > MAX_TASK_CONTEXT_ASSETS:
        raise ValueError(f"asset_paths must not exceed {MAX_TASK_CONTEXT_ASSETS} entries.")
    normalized: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        cleaned = _clean_text(item, name=f"asset_paths[{index}]", maximum=MAX_ASSET_PATH_CHARS)
        if not cleaned.startswith("/Game/"):
            raise ValueError(f"asset_paths[{index}] must be an exact /Game Object Path.")
        if cleaned in seen:
            raise ValueError("asset_paths must not contain duplicates.")
        seen.add(cleaned)
        normalized.append(cleaned)
    return tuple(normalized)


def _validate_flag(value: bool, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be boolean.")
    return value


class TaskContextService:
    """Aggregate deterministic Index, Revision, Memory, Live Editor, and Change Set facts into one bounded, read-only Task Context.

    R0.3 additionally joins the included sources into a `correlation` section:
    Active Work, the explicitly requested Change Set, the Live Editor session,
    and Memory Evidence are correlated through exact keys only (session id,
    asset path, change set id literal). The correlation is computed per request,
    writes nothing back to Memory or the workflow journal, performs no model
    inference, never scans the workflow's private change set store, and never
    discovers Change Sets that were not explicitly requested.
    """

    def __init__(
        self,
        *,
        index_service: IndexQueryService,
        memory_service: ProjectMemoryService | None = None,
        live_editor_service: Any | None = None,
        workflow_service: Any | None = None,
        freshness_tracker: Any | None = None,
    ) -> None:
        self.index_service = index_service
        self.memory_service = memory_service
        self.live_editor_service = live_editor_service
        self.workflow_service = workflow_service
        self.freshness = freshness_tracker

    def get_task_context(
        self,
        *,
        query: str,
        asset_paths: Sequence[str] | None = None,
        work_item_id: str = "",
        change_set_id: str = "",
        include_live_context: bool = True,
        include_memory: bool = True,
        max_output_tokens: int = DEFAULT_OUTPUT_TOKEN_BUDGET,
    ) -> dict[str, Any]:
        normalized_query = _validate_query(query)
        normalized_assets = _validate_asset_paths(asset_paths)
        normalized_work_item = _clean_text(
            work_item_id,
            name="work_item_id",
            maximum=MAX_WORK_ITEM_ID_CHARS,
        )
        normalized_change_set = _clean_text(
            change_set_id,
            name="change_set_id",
            maximum=MAX_CHANGE_SET_ID_CHARS,
        )
        include_live_context = _validate_flag(include_live_context, "include_live_context")
        include_memory = _validate_flag(include_memory, "include_memory")
        max_output_tokens = normalize_output_token_budget(max_output_tokens)

        response: dict[str, Any] = {
            "schemaVersion": TASK_CONTEXT_SCHEMA_VERSION,
            "tool": "ue_get_task_context",
            "ok": True,
            "readOnly": True,
            "request": {
                "query": normalized_query,
                "assetPaths": list(normalized_assets),
                "workItemId": normalized_work_item,
                "changeSetId": normalized_change_set,
                "includeLiveContext": include_live_context,
                "includeMemory": include_memory,
                "maxOutputTokens": max_output_tokens,
            },
        }
        response["project"] = self._build_project()
        response["targetAssets"] = self._build_target_assets(normalized_assets)
        relevant_assets, relevant_failures = self._build_relevant_assets(
            normalized_query,
            excluded_paths=set(normalized_assets),
        )
        response["relevantAssets"] = relevant_assets
        memory_section, active_work_section = self._build_memory_and_work(
            normalized_query,
            normalized_assets,
            include_memory,
            max_output_tokens,
            normalized_work_item,
        )
        response["memory"] = memory_section
        response["activeWork"] = active_work_section
        response["liveEditor"] = self._build_live_editor(include_live_context)
        response["revisionState"] = self._build_revision_state(normalized_assets)
        response["changeSet"] = self._build_change_set(normalized_change_set)
        correlation_result = self._build_correlation(
            active_work_section=active_work_section,
            change_set_section=response["changeSet"],
            live_section=response["liveEditor"],
            memory_included=bool(memory_section.get("included")),
        )
        response["correlation"] = correlation_result["section"]
        response["risks"], risk_summary = self._build_risks(
            query=normalized_query,
            asset_paths=normalized_assets,
            target_assets=response["targetAssets"],
            relevant_assets=response["relevantAssets"],
            relevant_failures=relevant_failures,
            memory_section=memory_section,
            active_work_section=active_work_section,
            live_section=response["liveEditor"],
            revision_state=response["revisionState"],
            change_set_section=response["changeSet"],
            work_item_id=normalized_work_item,
            correlation=correlation_result,
        )
        response["riskSummary"] = risk_summary
        response["nextExpansions"] = self._build_expansions(
            asset_paths=normalized_assets,
            relevant_assets=response["relevantAssets"],
            memory_section=memory_section,
            change_set_section=response["changeSet"],
        )
        response["degradedSources"] = self._degraded_sources(
            response,
            relevant_failures=relevant_failures,
        )
        self._finalize_budget(response, max_output_tokens)
        return response

    def _project_name(self, project_key: str) -> str:
        if self.workflow_service is not None:
            name = getattr(self.workflow_service, "project_name", "")
            if name:
                return name
        if self.live_editor_service is not None:
            config = getattr(self.live_editor_service, "config", None)
            name = getattr(config, "project_name", "")
            if name:
                return name
        return project_key

    def _build_project(self) -> dict[str, Any]:
        status = self.index_service.check()
        metadata = status.get("indexMetadata", {})
        project_key = str(status.get("projectKey", ""))
        return {
            "projectKey": project_key,
            "projectName": self._project_name(project_key),
            "index": {
                "snapshotId": metadata.get("snapshotId", ""),
                "lastIndexedAtUtc": metadata.get("lastIndexedAtUtc", ""),
                "databaseSchemaVersion": status.get("databaseSchemaVersion"),
                "immutable": bool(metadata.get("immutable")),
                "quiescent": bool(metadata.get("quiescent")),
            },
            "stats": status.get("stats", {}),
            "sources": {
                "index": True,
                "revisionFreshness": self.freshness is not None,
                "memory": self.memory_service is not None,
                "liveEditor": self.live_editor_service is not None,
                "changeSet": self.workflow_service is not None,
            },
        }

    def _build_target_assets(self, asset_paths: Sequence[str]) -> list[dict[str, Any]]:
        targets: list[dict[str, Any]] = []
        for asset_path in asset_paths:
            section = self.index_service.get_asset(
                asset_path,
                sections=("identity", "summary", "metadata"),
                max_output_tokens=DEFAULT_OUTPUT_TOKEN_BUDGET,
            )
            target: dict[str, Any] = {
                "assetPath": asset_path,
                "found": bool(section.get("found")),
                "whyIncluded": "explicit-asset-path",
                "source": "immutable-sqlite-index",
            }
            payload = section.get("asset")
            if isinstance(payload, dict):
                identity = {key: payload[key] for key in IDENTITY_FIELDS if key in payload}
                metadata = {
                    key: value
                    for key, value in payload.items()
                    if key not in IDENTITY_FIELDS and key != "summary"
                }
                if identity:
                    target["identity"] = identity
                if "summary" in payload:
                    target["summary"] = payload["summary"]
                if metadata:
                    target["metadata"] = metadata
            else:
                target["reason"] = "asset-not-indexed"
            targets.append(target)
        return targets

    @staticmethod
    def _search_hits(response: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not isinstance(response, dict):
            return []
        results = response.get("results")
        return [item for item in results if isinstance(item, dict)] if isinstance(results, list) else []

    def _candidate_asset_class(self, asset_path: str) -> str:
        try:
            section = self.index_service.get_asset(
                asset_path,
                sections=("identity",),
                max_output_tokens=DEFAULT_OUTPUT_TOKEN_BUDGET,
            )
        except (OSError, ValueError, RuntimeError, TypeError, sqlite3.Error):
            return ""
        payload = section.get("asset") if isinstance(section, dict) else None
        return str(payload.get("asset_class", "")) if isinstance(payload, dict) else ""

    def _build_relevant_assets(
        self,
        query: str,
        *,
        excluded_paths: set[str],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Deterministic small relevant-asset candidate set from immutable Index search only.

        Query terms (in order, deduplicated) drive Asset Search first and a
        bounded Symbol Search supplement second. Results are merged on exact
        asset path, explicit target assets are never duplicated, ordering is
        fixed (more matched terms first, then first matched term position, then
        asset path), and the list is bounded by MAX_TASK_CONTEXT_CANDIDATES.
        No score, confidence, or model inference is produced.
        """
        terms = _candidate_terms(query)
        failures: list[str] = []
        merged: dict[str, dict[str, Any]] = {}
        for term_index, term in enumerate(terms):
            try:
                asset_response = self.index_service.search(
                    query=term,
                    scope="assets",
                    limit=MAX_CANDIDATES_PER_TERM,
                )
            except (OSError, ValueError, RuntimeError, TypeError, sqlite3.Error):
                asset_response = None
                failures.append("asset-search-failed")
            for hit in self._search_hits(asset_response):
                asset_path = str(hit.get("asset_path", "")).strip()
                if not asset_path or asset_path in excluded_paths:
                    continue
                existing = merged.get(asset_path)
                match_kind = _asset_match_kind(term, hit)
                if existing is None:
                    merged[asset_path] = {
                        "assetPath": asset_path,
                        "assetClass": str(hit.get("asset_class", "")),
                        "source": "immutable-sqlite-index",
                        "whyIncluded": "asset-search-query-term",
                        "matchKind": match_kind,
                        "matchedTerms": [term],
                        "matchCount": 1,
                        "firstTermIndex": term_index,
                    }
                    continue
                existing["whyIncluded"] = "asset-search-query-term"
                existing["matchKind"] = _better_match_kind(existing["matchKind"], match_kind)
                if term not in existing["matchedTerms"]:
                    existing["matchedTerms"].append(term)
                existing["matchCount"] = len(existing["matchedTerms"])
                existing["firstTermIndex"] = min(int(existing["firstTermIndex"]), term_index)

            try:
                symbol_response = self.index_service.search(
                    query=term,
                    scope="symbols",
                    limit=MAX_CANDIDATES_PER_TERM,
                )
            except (OSError, ValueError, RuntimeError, TypeError, sqlite3.Error):
                symbol_response = None
                failures.append("symbol-search-failed")
            for hit in self._search_hits(symbol_response):
                asset_path = str(hit.get("asset_path", "")).strip()
                if not asset_path or asset_path in excluded_paths:
                    continue
                existing = merged.get(asset_path)
                match_kind = _symbol_match_kind(term, hit)
                matched_symbol = {
                    "name": str(hit.get("name", "")),
                    "kind": str(hit.get("kind", "")),
                }
                if existing is None:
                    merged[asset_path] = {
                        "assetPath": asset_path,
                        "assetClass": "",
                        "source": "immutable-sqlite-index",
                        "whyIncluded": "symbol-search-query-term",
                        "matchKind": match_kind,
                        "matchedTerms": [term],
                        "matchCount": 1,
                        "firstTermIndex": term_index,
                        "matchedSymbol": matched_symbol,
                    }
                    continue
                existing["matchKind"] = _better_match_kind(existing["matchKind"], match_kind)
                if term not in existing["matchedTerms"]:
                    existing["matchedTerms"].append(term)
                existing["matchCount"] = len(existing["matchedTerms"])
                existing["firstTermIndex"] = min(int(existing["firstTermIndex"]), term_index)
                if "matchedSymbol" not in existing:
                    existing["matchedSymbol"] = matched_symbol

        for candidate in merged.values():
            if not candidate.get("assetClass"):
                candidate["assetClass"] = self._candidate_asset_class(candidate["assetPath"])

        ordered = sorted(
            merged.values(),
            key=lambda candidate: (
                -int(candidate.get("matchCount", 0)),
                int(candidate.get("firstTermIndex", 0)),
                str(candidate.get("assetPath", "")).casefold(),
            ),
        )
        for candidate in ordered:
            candidate.pop("firstTermIndex", None)
        return ordered[:MAX_TASK_CONTEXT_CANDIDATES], failures

    def _build_revision_state(self, asset_paths: Sequence[str]) -> dict[str, Any]:
        if self.freshness is None:
            return {
                "available": False,
                "source": "unavailable",
                "reason": "revision-export-not-configured",
                "overall": "unavailable",
                "assets": {
                    path: {"state": "unavailable", "reason": "revision-export-not-configured"}
                    for path in asset_paths
                },
                "comparedAtUtc": _utc_now_iso(),
            }
        assets: dict[str, Any] = {}
        for path in asset_paths:
            result = self._freshness_for_asset(path)
            entry: dict[str, Any] = {
                "state": result.get("state", "unavailable"),
                "reason": result.get("reason", ""),
                "indexRevision": result.get("indexRevision", ""),
                "revisionExportRevision": result.get("revisionExportRevision", ""),
                "diskRevision": result.get("diskRevision", ""),
                "comparedAtUtc": result.get("comparedAtUtc", ""),
            }
            comparisons = result.get("comparisons")
            if comparisons is not None:
                entry["comparisons"] = comparisons
            assets[path] = entry
        states = {str(item["state"]) for item in assets.values()}
        if "stale" in states:
            overall = "stale"
        elif "unavailable" in states:
            overall = "partial" if "fresh" in states else "unavailable"
        elif states == {"fresh"}:
            overall = "fresh"
        else:
            overall = "unavailable"
        return {
            "available": True,
            "source": "sqlite-revision-export-disk-sha256",
            "overall": overall,
            "assets": assets,
            "comparedAtUtc": _utc_now_iso(),
        }

    def _freshness_for_asset(self, asset_path: str) -> dict[str, Any]:
        try:
            return self.freshness.inspect_asset(asset_path)
        except (OSError, ValueError, RuntimeError, TypeError) as exc:
            return {
                "assetPath": asset_path,
                "state": "unavailable",
                "reason": "freshness-inspection-failed",
                "error": str(exc),
            }

    def _memory_budget_chars(self, max_output_tokens: int) -> int:
        chars = int(max_output_tokens * 4 * MEMORY_BUDGET_FRACTION)
        return max(MIN_CONTEXT_CHARS, min(MAX_CONTEXT_CHARS, chars))

    def _memory_stale_record_count(self) -> int:
        if self.memory_service is None:
            return 0
        try:
            counts = self.memory_service.status()
        except (ProjectMemoryServiceError, OSError, TypeError, ValueError, RuntimeError, sqlite3.Error):
            return 0
        statuses = getattr(counts, "counts_by_status", {})
        return int(statuses.get("stale", 0)) if isinstance(statuses, dict) else 0

    def _build_memory_and_work(
        self,
        query: str,
        asset_paths: Sequence[str],
        include_memory: bool,
        max_output_tokens: int,
        work_item_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        memory_section: dict[str, Any] = {
            "available": self.memory_service is not None,
            "included": False,
            "source": "project-memory",
        }
        active_work: dict[str, Any] = {
            "available": self.memory_service is not None,
            "included": False,
            "source": "project-memory-active-work",
        }
        if self.memory_service is None:
            memory_section["reason"] = "memory-disabled"
            active_work["reason"] = "memory-disabled"
            self._attach_requested_work(active_work, work_item_id, unavailable_reason="memory-disabled")
            return memory_section, active_work
        memory_section["staleRecordCount"] = self._memory_stale_record_count()
        if not include_memory:
            memory_section["reason"] = "include-memory-false"
            active_work["reason"] = "include-memory-false"
            self._attach_requested_work(active_work, work_item_id, unavailable_reason="include-memory-false")
            return memory_section, active_work
        try:
            context = self.memory_service.get_context(
                query=query,
                asset_paths=tuple(asset_paths),
                detail_level=2,
                budget=ContextBudget(max_chars=self._memory_budget_chars(max_output_tokens)),
                recall_budget=RecallBudget(),
            )
        except (
            ProjectMemoryServiceError,
            KeyError,
            OSError,
            TypeError,
            ValueError,
            RuntimeError,
            sqlite3.Error,
        ) as exc:
            memory_section["reason"] = "memory-context-failed"
            memory_section["error"] = str(exc)
            active_work["reason"] = "memory-context-failed"
            self._attach_requested_work(active_work, work_item_id, unavailable_reason="memory-context-failed")
            return memory_section, active_work
        memory_section["included"] = True
        memory_section["summary"] = {
            "projectProfile": context.get("projectProfile", {}),
            "detailLevel": context.get("detailLevel", 1),
            "nodes": context.get("nodes", []),
            "records": context.get("records", []),
            "truncated": bool(context.get("truncated")),
            "truncationReasons": context.get("truncationReasons", []),
            "recalledItemCount": context.get("recalledItemCount", 0),
            "contentChars": context.get("contentChars", 0),
            "estimatedTokens": context.get("estimatedTokens", 0),
            "recallBudget": context.get("recallBudget", {}),
            "usage": context.get("usage", {}),
            "nextActions": context.get("nextActions", []),
        }
        active_work["included"] = True
        active_work["items"] = context.get("activeWork", [])
        active_work["truncated"] = bool(context.get("truncated"))
        self._attach_requested_work(active_work, work_item_id, unavailable_reason="")
        return memory_section, active_work

    def _attach_requested_work(
        self,
        active_work: dict[str, Any],
        work_item_id: str,
        *,
        unavailable_reason: str,
    ) -> None:
        if not work_item_id:
            return
        if unavailable_reason:
            active_work["requestedWorkItem"] = {
                "requested": True,
                "found": False,
                "reason": unavailable_reason,
            }
            return
        active_work["requestedWorkItem"] = self._work_item_detail(work_item_id)

    def _work_item_detail(self, work_item_id: str) -> dict[str, Any]:
        if self.memory_service is None:
            return {"requested": True, "found": False, "reason": "memory-disabled"}
        try:
            work = self.memory_service.get_work(work_item_id)
        except (
            KeyError,
            ProjectMemoryServiceError,
            OSError,
            TypeError,
            ValueError,
            RuntimeError,
            sqlite3.Error,
        ) as exc:
            code = getattr(exc, "code", None)
            if not code and isinstance(exc, KeyError):
                code = "memory-work-not-found"
            return {
                "requested": True,
                "found": False,
                "reason": code or "memory-work-unavailable",
            }
        return {
            "requested": True,
            "found": True,
            "source": "explicit-work-item-id",
            "work": {
                "workItemId": work.work_item_id,
                "title": work.title,
                "status": work.status.value,
                "priority": work.priority,
                "description": work.description,
                "nextAction": work.next_action,
                "blockedReason": work.blocked_reason,
                "owner": work.owner,
                "updatedAtUtc": work.updated_at_utc,
                "nodeIds": list(work.node_ids),
                "assetPaths": list(work.asset_paths),
            },
        }

    def _build_live_editor(self, include_live_context: bool) -> dict[str, Any]:
        section: dict[str, Any] = {
            "available": self.live_editor_service is not None,
            "included": False,
            "source": "live-editor-memory",
        }
        if self.live_editor_service is None:
            section["reason"] = "live-editor-disabled"
            return section
        if not include_live_context:
            section["reason"] = "include-live-context-false"
            return section
        try:
            payload = self.live_editor_service.call_tool("ue_get_editor_context")
        except Exception as exc:
            section["reason"] = "live-editor-unavailable"
            section["errorCode"] = getattr(exc, "code", type(exc).__name__)
            return section
        if not isinstance(payload, dict) or not payload.get("ok"):
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            code = error.get("code") if isinstance(error, dict) else None
            section["reason"] = "live-editor-unavailable"
            section["errorCode"] = code or "live-editor-unavailable"
            return section
        result = payload.get("result", payload)
        if not isinstance(result, dict):
            result = {}
        editor = result.get("editor")
        session_id = ""
        if isinstance(editor, dict):
            session_id = str(editor.get("sessionId", ""))
        section["included"] = True
        section["editorSessionId"] = session_id
        section["summary"] = result
        return section

    def _build_change_set(self, change_set_id: str) -> dict[str, Any]:
        section: dict[str, Any] = {
            "requested": bool(change_set_id),
            "available": self.workflow_service is not None,
            "source": "change-set-journal",
        }
        if not change_set_id:
            return section
        section["changeSetId"] = change_set_id
        if self.workflow_service is None:
            section["found"] = False
            section["reason"] = "workflow-disabled"
            return section
        try:
            summary = self.workflow_service.get_change_set(change_set_id)
        except Exception as exc:
            section["found"] = False
            section["reason"] = getattr(exc, "code", "change-set-unavailable")
            section["error"] = str(exc)
            return section
        section["found"] = True
        section["summary"] = summary if isinstance(summary, dict) else {"payload": summary}
        return section

    @staticmethod
    def _live_asset_sets(live_section: dict[str, Any]) -> tuple[set[str], set[str]]:
        summary = live_section.get("summary")
        if not isinstance(summary, dict):
            return set(), set()
        dirty: set[str] = set()
        for item in TaskContextService._section_items(summary, "dirtyPackages"):
            dirty.update(TaskContextService._item_asset_paths(item))
        opened: set[str] = set()
        for item in TaskContextService._section_items(summary, "openAssets"):
            opened.update(TaskContextService._item_asset_paths(item))
        return dirty, opened

    @staticmethod
    def _correlation_work_items(active_work_section: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
        """Ordered, deduplicated work items considered for correlation.

        The explicitly requested work item comes first, followed by the memory
        context's related active work items, skipping duplicate work item ids.
        Returns the considered items plus the deduplicated total count of
        available work items (requested + related, capped only for consideration).
        """
        ordered: list[dict[str, Any]] = []
        seen: set[str] = set()
        total = 0
        requested = active_work_section.get("requestedWorkItem")
        if isinstance(requested, dict) and requested.get("found"):
            work = requested.get("work")
            if isinstance(work, dict):
                ordered.append(work)
                seen.add(str(work.get("workItemId", "")))
                total += 1
        items = active_work_section.get("items")
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                work_item_id = str(item.get("workItemId", ""))
                if work_item_id and work_item_id in seen:
                    continue
                seen.add(work_item_id)
                total += 1
                if len(ordered) < MAX_CORRELATION_WORK_ITEMS:
                    ordered.append(item)
        return ordered, total

    @staticmethod
    def _work_asset_paths(work: dict[str, Any], limit: int) -> tuple[list[str], bool]:
        listed = work.get("assetPaths")
        paths = [str(entry) for entry in listed if isinstance(entry, str) and entry] if isinstance(listed, list) else []
        return paths[:limit], len(paths) > limit

    @staticmethod
    def _work_text_fields(work: dict[str, Any]) -> dict[str, str]:
        fields: dict[str, str] = {}
        for key in ("title", "description", "nextAction", "blockedReason"):
            value = work.get(key)
            if isinstance(value, str) and value:
                fields[key] = value
        details = work.get("details")
        if isinstance(details, dict) and details:
            fields["details"] = " ".join(f"{key}: {value}" for key, value in details.items())
        return fields

    @staticmethod
    def _evidence_query_tokens(asset_path: str) -> str:
        leaf = asset_path.rsplit("/", 1)[-1]
        name = leaf.split(".", 1)[0]
        return " ".join(_SEARCH_TOKEN_PATTERN.findall(name))[:80]

    def _asset_evidence_refs(self, asset_path: str) -> list[dict[str, Any]]:
        """Compact read-only Memory Evidence references scoped to one asset.

        Reuses the public scoped search path (FTS token match on the asset
        name + asset scope filter, all statuses). A miss only means "not
        matched by this deterministic lookup", never "no records exist".
        """
        if self.memory_service is None:
            return []
        query = self._evidence_query_tokens(asset_path)
        if not query:
            return []
        try:
            hits = self.memory_service.search_records(
                query=query,
                statuses=(),
                scope_type="asset",
                scope_key=asset_path,
                limit=MAX_CORRELATION_EVIDENCE_PER_ASSET,
            )
        except (ProjectMemoryServiceError, OSError, TypeError, ValueError, RuntimeError, sqlite3.Error):
            return []
        refs: list[dict[str, Any]] = []
        for hit in hits:
            record = hit.record
            refs.append(
                {
                    "recordId": record.record_id,
                    "status": record.status.value,
                    "recordType": record.record_type.value,
                    "title": record.title,
                }
            )
        return refs

    def _build_correlation(
        self,
        *,
        active_work_section: dict[str, Any],
        change_set_section: dict[str, Any],
        live_section: dict[str, Any],
        memory_included: bool,
    ) -> dict[str, Any]:
        """Build the read-only, non-persistent Cross-source Correlation section.

        Joins are exact-key matches only (editor session id, asset path, change
        set id literal); no model inference and no workflow journal writes. The
        Change Set is only ever correlated when explicitly requested and found;
        the private _change_sets mapping is never scanned.
        """
        change_set_id = str(change_set_section.get("changeSetId", ""))
        change_set_found = bool(change_set_section.get("found")) and bool(change_set_id)
        change_set_summary = change_set_section.get("summary")
        if not isinstance(change_set_summary, dict):
            change_set_summary = {}
        live_included = bool(live_section.get("included"))
        work_items, work_items_total = self._correlation_work_items(active_work_section)

        links: list[dict[str, Any]] = []
        summary: dict[str, Any] = {
            "linkCount": 0,
            "countsByKind": {},
            "workItemsConsidered": len(work_items),
            "workItemsTotal": work_items_total,
            "changeSetAffectedAssetsSampled": 0,
            "changeSetAffectedAssetsTotal": 0,
            "changeSetAffectedAssetsTruncated": False,
            "evidenceLookups": 0,
            "evidenceLookupsTruncated": False,
            "linksTruncated": False,
        }
        session_mismatch: dict[str, Any] | None = None

        dirty_candidates: set[str] = set()
        open_candidates: set[str] = set()
        if live_included:
            dirty_candidates, open_candidates = self._live_asset_sets(live_section)

        if change_set_found and live_included:
            change_set_session = str(change_set_summary.get("editorSessionId", ""))
            live_session = str(live_section.get("editorSessionId", ""))
            if change_set_session and live_session:
                matches = change_set_session == live_session
                links.append(
                    {
                        "kind": "change-set-editor-session",
                        "sources": ["change-set-journal", "live-editor-memory"],
                        "changeSetId": change_set_id,
                        "details": {
                            "changeSetEditorSessionId": change_set_session,
                            "liveEditorSessionId": live_session,
                            "matches": matches,
                        },
                    }
                )
                if not matches:
                    session_mismatch = {
                        "changeSetEditorSessionId": change_set_session,
                        "liveEditorSessionId": live_session,
                    }

        affected = change_set_summary.get("affectedAssets") if change_set_found else None
        affected_assets = [str(entry) for entry in affected if isinstance(entry, str) and entry] if isinstance(affected, list) else []
        affected_set = set(affected_assets)
        summary["changeSetAffectedAssetsTotal"] = len(affected_assets)
        summary["changeSetAffectedAssetsSampled"] = min(len(affected_assets), MAX_CORRELATION_CHANGE_SET_ASSETS)
        summary["changeSetAffectedAssetsTruncated"] = len(affected_assets) > MAX_CORRELATION_CHANGE_SET_ASSETS

        evidence_lookups = 0

        def fetch_evidence(asset_path: str) -> list[dict[str, Any]]:
            nonlocal evidence_lookups
            if evidence_lookups >= MAX_CORRELATION_EVIDENCE_LOOKUPS:
                return []
            evidence_lookups += 1
            return self._asset_evidence_refs(asset_path)

        for asset_path in affected_assets[:MAX_CORRELATION_CHANGE_SET_ASSETS]:
            if live_included:
                if self._matches_asset(asset_path, dirty_candidates):
                    links.append(
                        {
                            "kind": "change-set-asset-in-editor",
                            "sources": ["change-set-journal", "live-editor-memory"],
                            "assetPath": asset_path,
                            "changeSetId": change_set_id,
                            "details": {"observedVia": "editor-dirty-packages"},
                        }
                    )
                if self._matches_asset(asset_path, open_candidates):
                    links.append(
                        {
                            "kind": "change-set-asset-in-editor",
                            "sources": ["change-set-journal", "live-editor-memory"],
                            "assetPath": asset_path,
                            "changeSetId": change_set_id,
                            "details": {"observedVia": "editor-open-assets"},
                        }
                    )
            if memory_included:
                for ref in fetch_evidence(asset_path):
                    links.append(
                        {
                            "kind": "change-set-asset-memory-evidence",
                            "sources": ["change-set-journal", "project-memory"],
                            "assetPath": asset_path,
                            "changeSetId": change_set_id,
                            "details": ref,
                        }
                    )

        for work in work_items:
            work_item_id = str(work.get("workItemId", ""))
            work_paths, _ = self._work_asset_paths(work, MAX_CORRELATION_WORK_ASSETS)
            if change_set_found:
                overlap = [asset_path for asset_path in work_paths if asset_path in affected_set]
                for asset_path in overlap:
                    links.append(
                        {
                            "kind": "work-change-set-asset-overlap",
                            "sources": ["project-memory-active-work", "change-set-journal"],
                            "assetPath": asset_path,
                            "workItemId": work_item_id,
                            "changeSetId": change_set_id,
                            "details": {},
                        }
                    )
                matched_fields = [
                    field for field, text in self._work_text_fields(work).items() if change_set_id in text
                ]
                if matched_fields:
                    links.append(
                        {
                            "kind": "work-references-change-set",
                            "sources": ["project-memory-active-work", "change-set-journal"],
                            "workItemId": work_item_id,
                            "changeSetId": change_set_id,
                            "details": {"matchedIn": matched_fields},
                        }
                    )
            if live_included:
                for asset_path in work_paths:
                    if self._matches_asset(asset_path, dirty_candidates):
                        links.append(
                            {
                                "kind": "work-asset-in-editor",
                                "sources": ["project-memory-active-work", "live-editor-memory"],
                                "assetPath": asset_path,
                                "workItemId": work_item_id,
                                "details": {"observedVia": "editor-dirty-packages"},
                            }
                        )
                    if self._matches_asset(asset_path, open_candidates):
                        links.append(
                            {
                                "kind": "work-asset-in-editor",
                                "sources": ["project-memory-active-work", "live-editor-memory"],
                                "assetPath": asset_path,
                                "workItemId": work_item_id,
                                "details": {"observedVia": "editor-open-assets"},
                            }
                        )
            if memory_included:
                for asset_path in work_paths:
                    for ref in fetch_evidence(asset_path):
                        links.append(
                            {
                                "kind": "work-asset-memory-evidence",
                                "sources": ["project-memory-active-work", "project-memory"],
                                "assetPath": asset_path,
                                "workItemId": work_item_id,
                                "details": ref,
                            }
                        )

        summary["evidenceLookups"] = evidence_lookups
        summary["evidenceLookupsTruncated"] = evidence_lookups >= MAX_CORRELATION_EVIDENCE_LOOKUPS

        def link_sort_key(link: dict[str, Any]) -> tuple[Any, ...]:
            details = link.get("details")
            if not isinstance(details, dict):
                details = {}
            return (
                CORRELATION_KIND_RANK.get(str(link.get("kind", "")), 99),
                str(link.get("assetPath", "")).casefold(),
                str(link.get("workItemId") or link.get("changeSetId") or ""),
                str(details.get("recordId", "")),
                str(details.get("observedVia", "")),
            )

        links.sort(key=link_sort_key)
        summary["linksTruncated"] = len(links) > MAX_CORRELATION_LINKS
        links = links[:MAX_CORRELATION_LINKS]
        for link in links:
            kind = str(link["kind"])
            summary["countsByKind"][kind] = summary["countsByKind"].get(kind, 0) + 1
        summary["linkCount"] = len(links)

        pair_present = (
            (change_set_found and live_included)
            or (change_set_found and memory_included)
            or (live_included and bool(work_items))
        )
        if links or pair_present:
            return {
                "section": {
                    "available": True,
                    "method": CORRELATION_METHOD,
                    "summary": summary,
                    "links": links,
                },
                "sessionMismatch": session_mismatch,
            }
        return {
            "section": {
                "available": False,
                "method": CORRELATION_METHOD,
                "reason": "insufficient-correlatable-sources",
            },
            "sessionMismatch": None,
        }

    @staticmethod
    def _section_items(summary: dict[str, Any], field: str) -> list[dict[str, Any]]:
        value = summary.get(field)
        if not isinstance(value, dict):
            return []
        items = value.get("items")
        if not isinstance(items, list):
            return []
        return [item for item in items if isinstance(item, dict)]

    @staticmethod
    def _item_asset_paths(item: dict[str, Any]) -> set[str]:
        paths: set[str] = set()
        listed = item.get("assetPaths")
        if isinstance(listed, list):
            paths.update(str(entry) for entry in listed)
        for key in ("packageName", "path"):
            value = item.get(key)
            if isinstance(value, str) and value:
                paths.add(value)
        return paths

    @staticmethod
    def _matches_asset(asset_path: str, candidates: set[str]) -> bool:
        if asset_path in candidates:
            return True
        return any(asset_path.startswith(candidate + ".") for candidate in candidates)

    def _stale_record_ids(self, query: str, asset_path: str) -> list[str]:
        if self.memory_service is None:
            return []
        terms = _SEARCH_TOKEN_PATTERN.findall(query)
        if not terms:
            return []
        try:
            hits = self.memory_service.search_records(
                query=" ".join(terms[:8]),
                statuses=("stale",),
                scope_type="asset",
                scope_key=asset_path,
                limit=MAX_MEMORY_STALE_SAMPLES,
            )
        except (ProjectMemoryServiceError, OSError, TypeError, ValueError, RuntimeError, sqlite3.Error):
            return []
        return [hit.record.record_id for hit in hits]

    def _build_risks(
        self,
        *,
        query: str,
        asset_paths: Sequence[str],
        target_assets: list[dict[str, Any]],
        relevant_assets: list[dict[str, Any]],
        relevant_failures: Sequence[str],
        memory_section: dict[str, Any],
        active_work_section: dict[str, Any],
        live_section: dict[str, Any],
        revision_state: dict[str, Any],
        change_set_section: dict[str, Any],
        work_item_id: str,
        correlation: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        risks: list[dict[str, Any]] = []

        if not revision_state.get("available"):
            risks.append(
                {
                    "kind": "revision-state-unavailable",
                    "severity": "info",
                    "source": "task-context",
                    "details": {"reason": revision_state.get("reason", "")},
                }
            )
        for asset_path in asset_paths:
            state = revision_state.get("assets", {}).get(asset_path, {})
            asset_state = str(state.get("state", "unavailable"))
            reason = str(state.get("reason", ""))
            target = next((item for item in target_assets if item["assetPath"] == asset_path), {})
            if not target.get("found"):
                risks.append(
                    {
                        "assetPath": asset_path,
                        "kind": "target-not-indexed",
                        "severity": "high",
                        "source": "immutable-sqlite-index",
                        "details": {"reason": "asset-not-indexed"},
                    }
                )
                continue
            if asset_state == "stale":
                risks.append(
                    {
                        "assetPath": asset_path,
                        "kind": "asset-stale",
                        "severity": "high",
                        "source": revision_state.get("source", ""),
                        "details": {"reason": reason or "revision-mismatch"},
                    }
                )
            elif asset_state == "unavailable":
                dirty = "dirty" in reason
                risks.append(
                    {
                        "assetPath": asset_path,
                        "kind": "asset-revision-dirty" if dirty else "asset-revision-unavailable",
                        "severity": "high" if dirty else "medium",
                        "source": revision_state.get("source", ""),
                        "details": {"reason": reason or "revision-unavailable"},
                    }
                )

        if live_section.get("included"):
            summary = live_section.get("summary", {})
            if not isinstance(summary, dict):
                summary = {}
            dirty_candidates: set[str] = set()
            for item in self._section_items(summary, "dirtyPackages"):
                dirty_candidates.update(self._item_asset_paths(item))
            open_candidates: set[str] = set()
            for item in self._section_items(summary, "openAssets"):
                open_candidates.update(self._item_asset_paths(item))
            for asset_path in asset_paths:
                if self._matches_asset(asset_path, dirty_candidates):
                    risks.append(
                        {
                            "assetPath": asset_path,
                            "kind": "target-dirty-in-editor",
                            "severity": "high",
                            "source": "live-editor-memory",
                            "details": {"observedVia": "editor-dirty-packages"},
                        }
                    )
                if self._matches_asset(asset_path, open_candidates):
                    risks.append(
                        {
                            "assetPath": asset_path,
                            "kind": "target-open-in-editor",
                            "severity": "info",
                            "source": "live-editor-memory",
                            "details": {"observedVia": "editor-open-assets"},
                        }
                    )
        elif live_section.get("available") and live_section.get("reason") == "live-editor-unavailable":
            risks.append(
                {
                    "kind": "live-editor-unavailable",
                    "severity": "info",
                    "source": "live-editor-bridge",
                    "details": {"errorCode": live_section.get("errorCode", "live-editor-unavailable")},
                }
            )

        if memory_section.get("included"):
            records = memory_section.get("summary", {}).get("records", [])
            if isinstance(records, list):
                conflicted_ids = [
                    str(record.get("recordId"))
                    for record in records
                    if isinstance(record, dict) and record.get("status") == "conflicted" and record.get("recordId")
                ]
                if conflicted_ids:
                    risks.append(
                        {
                            "kind": "memory-conflicted-records",
                            "severity": "medium",
                            "source": "project-memory",
                            "details": {
                                "recordIds": conflicted_ids[:MAX_MEMORY_STALE_SAMPLES],
                                "sampleTruncated": len(conflicted_ids) > MAX_MEMORY_STALE_SAMPLES,
                            },
                        }
                    )
            for asset_path in asset_paths:
                stale_ids = self._stale_record_ids(query, asset_path)
                if stale_ids:
                    risks.append(
                        {
                            "assetPath": asset_path,
                            "kind": "memory-stale-records",
                            "severity": "medium",
                            "source": "project-memory",
                            "details": {
                                "recordIds": stale_ids,
                                "sampleTruncated": len(stale_ids) >= MAX_MEMORY_STALE_SAMPLES,
                            },
                        }
                    )
        elif memory_section.get("available") and memory_section.get("reason") == "memory-context-failed":
            risks.append(
                {
                    "kind": "memory-context-failed",
                    "severity": "info",
                    "source": "project-memory",
                    "details": {"error": memory_section.get("error", "")},
                }
            )

        if change_set_section.get("requested"):
            if not change_set_section.get("found"):
                risks.append(
                    {
                        "kind": "change-set-not-found",
                        "severity": "medium",
                        "source": "change-set-journal",
                        "details": {
                            "changeSetId": change_set_section.get("changeSetId", ""),
                            "reason": change_set_section.get("reason", ""),
                        },
                    }
                )
            else:
                summary = change_set_section.get("summary", {})
                status = str(summary.get("status", "")) if isinstance(summary, dict) else ""
                if status in CHANGE_SET_TERMINAL_STATUSES:
                    risks.append(
                        {
                            "kind": "change-set-terminal",
                            "severity": "medium",
                            "source": "change-set-journal",
                            "details": {"status": status},
                        }
                    )
                elif status == "unknown":
                    risks.append(
                        {
                            "kind": "change-set-unknown",
                            "severity": "info",
                            "source": "change-set-journal",
                            "details": {"status": status},
                        }
                    )
                session_mismatch = (correlation or {}).get("sessionMismatch")
                if isinstance(session_mismatch, dict):
                    risks.append(
                        {
                            "kind": "change-set-editor-session-mismatch",
                            "severity": "medium",
                            "source": "cross-source-correlation",
                            "details": {
                                "changeSetEditorSessionId": session_mismatch.get(
                                    "changeSetEditorSessionId", ""
                                ),
                                "liveEditorSessionId": session_mismatch.get("liveEditorSessionId", ""),
                            },
                        }
                    )

        requested_work = active_work_section.get("requestedWorkItem")
        if (
            isinstance(requested_work, dict)
            and requested_work.get("requested")
            and not requested_work.get("found")
        ):
            risks.append(
                {
                    "kind": "work-item-not-found",
                    "severity": "medium",
                    "source": "project-memory-active-work",
                    "details": {
                        "workItemId": work_item_id,
                        "reason": requested_work.get("reason", ""),
                    },
                }
            )

        if relevant_failures and not relevant_assets:
            risks.append(
                {
                    "kind": "relevant-assets-search-failed",
                    "severity": "info",
                    "source": "immutable-sqlite-index",
                    "details": {"failedSources": list(relevant_failures)},
                }
            )

        severity_counts: dict[str, int] = {"high": 0, "medium": 0, "info": 0}
        for risk in risks:
            severity = str(risk.get("severity", "info"))
            severity_counts[severity] = severity_counts.get(severity, 0) + 1
        risk_summary = {
            "count": len(risks),
            "highCount": severity_counts.get("high", 0),
            "mediumCount": severity_counts.get("medium", 0),
            "infoCount": severity_counts.get("info", 0),
        }
        return risks, risk_summary

    def _build_expansions(
        self,
        *,
        asset_paths: Sequence[str],
        relevant_assets: Sequence[dict[str, Any]],
        memory_section: dict[str, Any],
        change_set_section: dict[str, Any],
    ) -> list[dict[str, Any]]:
        expansions: list[dict[str, Any]] = []
        for asset_path in asset_paths[:2]:
            expansions.append(
                {
                    "tool": "ue_get_asset",
                    "reason": "expand-target-asset-sections",
                    "arguments": {
                        "asset_path": asset_path,
                        "sections": ["symbols", "references", "graphs", "nodes"],
                    },
                }
            )
            expansions.append(
                {
                    "tool": "ue_find_references",
                    "reason": "target-reference-edges",
                    "arguments": {"asset_path": asset_path, "direction": "both", "depth": 1},
                }
            )
        if asset_paths:
            expansions.append(
                {
                    "tool": "ue_analyze_change_impact",
                    "reason": "impact-analysis-explicit-targets",
                    "arguments": {
                        "target_asset_paths": list(asset_paths[:2]),
                        "max_depth": 2,
                    },
                }
            )
        elif relevant_assets:
            first = relevant_assets[0]
            expansions.append(
                {
                    "tool": "ue_analyze_change_impact",
                    "reason": "impact-analysis-relevant-asset-hint",
                    "arguments": {
                        "target_asset_paths": [str(first.get("assetPath", ""))],
                        "max_depth": 2,
                    },
                }
            )
        if memory_section.get("included"):
            summary = memory_section.get("summary", {})
            records = summary.get("records", []) if isinstance(summary, dict) else []
            if records:
                expansions.append(
                    {
                        "tool": "ue_memory_get_evidence",
                        "reason": "evidence-available-on-demand",
                        "arguments": {"record_id": records[0].get("recordId", "")},
                    }
                )
            next_actions = summary.get("nextActions", []) if isinstance(summary, dict) else []
            for action in next_actions:
                if isinstance(action, dict) and action.get("tool"):
                    expansions.append(
                        {
                            "tool": str(action.get("tool", "")),
                            "reason": str(action.get("reason", "")),
                            "arguments": action.get("arguments") or {},
                        }
                    )
        if change_set_section.get("requested") and change_set_section.get("found"):
            expansions.append(
                {
                    "tool": "ue_get_change_set",
                    "reason": "change-set-detail",
                    "arguments": {"change_set_id": change_set_section.get("changeSetId", "")},
                }
            )
            expansions.append(
                {
                    "tool": "ue_analyze_semantic_diff",
                    "reason": "semantic-diff-explicit-change-set",
                    "arguments": {"change_set_id": change_set_section.get("changeSetId", "")},
                }
            )
            expansions.append(
                {
                    "tool": "ue_build_verification_plan",
                    "reason": "verification-plan-explicit-change-set",
                    "arguments": {"change_set_id": change_set_section.get("changeSetId", "")},
                }
            )
            expansions.append(
                {
                    "tool": "ue_evaluate_trust_verdict",
                    "reason": "trust-verdict-explicit-change-set",
                    "arguments": {"change_set_id": change_set_section.get("changeSetId", "")},
                }
            )
        return expansions[:MAX_TASK_CONTEXT_EXPANSIONS]

    @staticmethod
    def _degraded_sources(
        response: dict[str, Any],
        *,
        relevant_failures: Sequence[str] = (),
    ) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for failure in relevant_failures:
            entries.append({"section": "relevantAssets", "reason": failure})
        revision = response.get("revisionState", {})
        if not revision.get("available"):
            entries.append({"section": "revisionState", "reason": revision.get("reason", "")})
        memory = response.get("memory", {})
        if not memory.get("included"):
            entries.append({"section": "memory", "reason": memory.get("reason", "")})
        active = response.get("activeWork", {})
        if not active.get("included"):
            entries.append({"section": "activeWork", "reason": active.get("reason", "")})
        live = response.get("liveEditor", {})
        if not live.get("included"):
            entries.append({"section": "liveEditor", "reason": live.get("reason", "")})
        change_set = response.get("changeSet", {})
        if change_set.get("requested") and not change_set.get("found"):
            entries.append({"section": "changeSet", "reason": change_set.get("reason", "")})
        return entries

    @staticmethod
    def _add_expansion(response: dict[str, Any], action: dict[str, Any]) -> None:
        expansions = response.setdefault("nextExpansions", [])
        if any(
            item.get("tool") == action.get("tool") and item.get("reason") == action.get("reason")
            for item in expansions
        ):
            return
        if len(expansions) >= MAX_TASK_CONTEXT_EXPANSIONS:
            expansions.pop(0)
        expansions.append(action)

    @staticmethod
    def _record_reason(reasons: list[str], reason: str) -> bool:
        if reason not in reasons:
            reasons.append(reason)
        return True

    def _trim_next(self, response: dict[str, Any], reasons: list[str]) -> bool:
        change_set = response.get("changeSet", {})
        summary = change_set.get("summary")
        if isinstance(summary, dict) and summary.get("operations"):
            summary["operations"] = []
            summary["operationsOmittedByBudget"] = True
            self._add_expansion(
                response,
                {
                    "tool": "ue_get_change_set",
                    "reason": "change-set-operations-omitted-by-budget",
                    "arguments": {"change_set_id": change_set.get("changeSetId", "")},
                },
            )
            return self._record_reason(reasons, "change-set-operations")
        live = response.get("liveEditor", {})
        if live.get("included") and not live.get("omittedDueToBudget"):
            live["summary"] = {"omittedDueToBudget": True}
            live["omittedDueToBudget"] = True
            self._add_expansion(
                response,
                {
                    "tool": "ue_get_editor_context",
                    "reason": "live-editor-context-omitted-by-budget",
                    "arguments": {},
                },
            )
            return self._record_reason(reasons, "live-editor-summary")
        memory = response.get("memory", {})
        memory_summary = memory.get("summary")
        if isinstance(memory_summary, dict):
            records = memory_summary.get("records")
            if records:
                records.pop()
                memory_summary["truncated"] = True
                return self._record_reason(reasons, "memory-records")
            nodes = memory_summary.get("nodes")
            if nodes:
                nodes.pop()
                memory_summary["truncated"] = True
                return self._record_reason(reasons, "memory-nodes")
        active = response.get("activeWork", {})
        items = active.get("items")
        if items:
            items.pop()
            active["truncated"] = True
            return self._record_reason(reasons, "active-work-items")
        relevant = response.get("relevantAssets")
        if isinstance(relevant, list):
            for candidate in reversed(relevant):
                if not isinstance(candidate, dict):
                    continue
                removable = [
                    key
                    for key in ("matchedTerms", "matchCount", "matchedSymbol")
                    if key in candidate
                ]
                if removable:
                    for key in removable:
                        candidate.pop(key)
                    return self._record_reason(reasons, "relevant-assets-metadata")
            if relevant:
                relevant.pop()
                return self._record_reason(reasons, "relevant-assets-count")
        correlation = response.get("correlation")
        if isinstance(correlation, dict):
            correlation_links = correlation.get("links")
            if correlation_links:
                correlation_links.pop()
                if "summary" in correlation:
                    correlation["summary"]["linkCount"] = len(correlation_links)
                return self._record_reason(reasons, "correlation-links")
            if correlation.get("available") and not correlation.get("omittedDueToBudget"):
                response["correlation"] = {
                    "available": True,
                    "method": CORRELATION_METHOD,
                    "omittedDueToBudget": True,
                }
                return self._record_reason(reasons, "correlation-summary")
        for target in reversed(response.get("targetAssets", [])):
            if "metadata" in target:
                target.pop("metadata")
                return self._record_reason(reasons, "target-asset-metadata")
        for target in reversed(response.get("targetAssets", [])):
            if "summary" in target:
                target.pop("summary")
                return self._record_reason(reasons, "target-asset-summary")
        revision = response.get("revisionState", {})
        assets = revision.get("assets")
        if isinstance(assets, dict):
            for state in reversed(list(assets.values())):
                if isinstance(state, dict) and "comparisons" in state:
                    state.pop("comparisons")
                    return self._record_reason(reasons, "revision-comparisons")
        project = response.get("project", {})
        if "stats" in project:
            project.pop("stats")
            return self._record_reason(reasons, "project-stats")
        expansions = response.get("nextExpansions")
        if expansions:
            expansions.pop()
            return self._record_reason(reasons, "next-expansions")
        risks = response.get("risks")
        if isinstance(risks, list):
            for risk in reversed(risks):
                if isinstance(risk, dict) and risk.get("details"):
                    risk["details"] = {}
                    return self._record_reason(reasons, "risk-details")
        revision = response.get("revisionState", {})
        assets = revision.get("assets")
        if isinstance(assets, dict):
            for state in reversed(list(assets.values())):
                if isinstance(state, dict):
                    removable = [
                        key
                        for key in ("indexRevision", "revisionExportRevision", "diskRevision", "comparedAtUtc")
                        if key in state
                    ]
                    if removable:
                        for key in removable:
                            state.pop(key)
                        return self._record_reason(reasons, "revision-asset-details")
        for target in reversed(response.get("targetAssets", [])):
            identity = target.get("identity")
            if isinstance(identity, dict):
                removable = [key for key in list(identity) if key not in ("asset_path", "asset_class")]
                if removable:
                    for key in removable:
                        identity.pop(key)
                    return self._record_reason(reasons, "target-asset-identity")
        degraded = response.get("degradedSources")
        if degraded:
            degraded.clear()
            return self._record_reason(reasons, "degraded-sources")
        request = response.get("request", {})
        if "workItemId" in request:
            request.pop("workItemId")
            request.pop("changeSetId", None)
            return self._record_reason(reasons, "request-details")
        project = response.get("project", {})
        if "sources" in project:
            project.pop("sources")
            return self._record_reason(reasons, "project-sources")
        index_info = project.get("index")
        if isinstance(index_info, dict) and "snapshotId" in index_info:
            index_info.pop("snapshotId")
            return self._record_reason(reasons, "project-index-details")
        memory = response.get("memory", {})
        memory_summary = memory.get("summary")
        if isinstance(memory_summary, dict):
            if "projectProfile" in memory_summary:
                memory_summary.pop("projectProfile")
                return self._record_reason(reasons, "memory-project-profile")
            if "recallBudget" in memory_summary:
                memory_summary.pop("recallBudget")
                return self._record_reason(reasons, "memory-recall-budget")
            if "usage" in memory_summary:
                memory_summary.pop("usage")
                return self._record_reason(reasons, "memory-usage")
            if "nextActions" in memory_summary:
                memory_summary.pop("nextActions")
                return self._record_reason(reasons, "memory-next-actions")
        active = response.get("activeWork", {})
        if "requestedWorkItem" in active:
            active.pop("requestedWorkItem")
            return self._record_reason(reasons, "requested-work-item")
        return False

    def _finalize_budget(self, response: dict[str, Any], max_output_tokens: int) -> None:
        response["outputBudget"] = {
            "maxTokens": max_output_tokens,
            "estimatedTokens": 0,
            "truncated": False,
            "truncationReason": "",
        }
        reasons: list[str] = []
        while estimate_json_tokens(response) > max_output_tokens - BUDGET_ENVELOPE_SLACK_CHARS:
            if not self._trim_next(response, reasons):
                break
        estimated = estimate_json_tokens(response)
        truncated = bool(reasons)
        if estimated > max_output_tokens:
            truncated = True
            if "minimal-envelope-exceeds-token-budget" not in reasons:
                reasons.append("minimal-envelope-exceeds-token-budget")
        response["outputBudget"] = {
            "maxTokens": max_output_tokens,
            "estimatedTokens": estimate_json_tokens(response),
            "truncated": truncated,
            "truncationReason": ",".join(reasons),
        }


def register_task_context_tools(
    *,
    server: Any,
    task_context_service: TaskContextService,
    read_annotations: Any,
    error_response: Any,
) -> None:
    @server.tool(annotations=read_annotations)
    def ue_get_task_context(
        query: str = "",
        asset_paths: list[str] | None = None,
        work_item_id: str = "",
        change_set_id: str = "",
        include_live_context: bool = True,
        include_memory: bool = True,
        max_output_tokens: int = DEFAULT_OUTPUT_TOKEN_BUDGET,
    ) -> dict[str, Any]:
        """Aggregate bounded Index, Revision, Memory, Live Editor, and Change Set facts for one task in a single read-only request.

        R0.3 adds a deterministic Cross-source Correlation section that joins
        Active Work, the explicitly requested Change Set, the Live Editor
        session, and Memory Evidence through exact keys only. Correlation is
        computed per request, persists nothing, and never discovers Change Sets
        that were not explicitly provided via change_set_id.
        """
        try:
            return task_context_service.get_task_context(
                query=query,
                asset_paths=asset_paths or (),
                work_item_id=work_item_id,
                change_set_id=change_set_id,
                include_live_context=include_live_context,
                include_memory=include_memory,
                max_output_tokens=max_output_tokens,
            )
        except (FileNotFoundError, OSError, ValueError, RuntimeError, TypeError, sqlite3.Error) as exc:
            return error_response("ue_get_task_context", exc, read_only=True)
