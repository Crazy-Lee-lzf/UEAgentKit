"""Session-scoped read-only mount registry for R7 knowledge packs."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Sequence

from .claims import DemoCallerContext, VerifiedCallerContext
from .errors import ErrorCode, MemoryMountError
from .index import INDEX_FORMAT_VERSION, IndexHit, PackIndex
from .jsonutil import canonical_json_bytes
from .limits import MountLimits
from .manifest import PackManifest, PackScope
from .records import PackContent, PackRecord, load_pack


_SCOPE_TIERS = {"shared": 0, "personal": 1, "project": 2}
_STATUS_RANKS = {"stale": 0, "unverified": 1, "valid": 2}
_RECORD_TYPES = {
    "projectFact",
    "projectRule",
    "decisionRecord",
    "knownIssue",
    "taskRecord",
    "runtimeEvidence",
}
_SCOPE_TYPES = {
    "project",
    "asset",
    "symbol",
    "graph",
    "node",
    "dataTableRow",
    "log",
    "file",
    "external",
}
_WARNING_NO_MOUNTS = "r7-no-mounted-packs"
_WARNING_CANDIDATE_CAP = "r7-result-candidate-cap"
_WARNING_RESULT_LIMIT = "r7-result-limit-truncated"
_WARNING_OUTPUT_TRUNCATED = "r7-output-truncated"
_WARNING_UNRESOLVED_CONFLICT = "r7-conflict-unresolved"


def _fail(code: ErrorCode, message: str, **details: Any) -> None:
    raise MemoryMountError(code, message, details=details)


@dataclass(frozen=True)
class MountInfo:
    pack_id: str
    revision: str
    content_hash: str
    priority: int
    scope: PackScope
    record_count: int
    index_format_version: int
    index_reused: bool
    already_mounted: bool
    integrity: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return {
            "packId": self.pack_id,
            "revision": self.revision,
            "contentHash": self.content_hash,
            "priority": self.priority,
            "scope": self.scope.to_payload(),
            "recordCount": self.record_count,
            "indexFormatVersion": self.index_format_version,
            "indexReused": self.index_reused,
            "alreadyMounted": self.already_mounted,
            "integrity": dict(self.integrity),
        }


@dataclass(frozen=True)
class QueryResult:
    items: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]
    retrieval_mode: str
    candidate_count: int
    conflict_count: int
    mounted_pack_count: int
    truncated: bool

    def to_payload(self) -> dict[str, Any]:
        return {
            "ok": True,
            "retrievalMode": self.retrieval_mode,
            "mountedPackCount": self.mounted_pack_count,
            "candidateCount": self.candidate_count,
            "conflictCount": self.conflict_count,
            "truncated": self.truncated,
            "warnings": list(self.warnings),
            "items": list(self.items),
        }


@dataclass(frozen=True)
class _MountedPack:
    content: PackContent
    manifest: PackManifest
    info: MountInfo
    index: PackIndex


@dataclass(frozen=True)
class _Candidate:
    record: PackRecord
    manifest: PackManifest
    match_reason: str
    score: float


class MemoryMountRegistry:
    """One caller/project session. Mount state is never persisted.

    The registry accepts only a ``VerifiedCallerContext`` or an explicit
    ``DemoCallerContext``. Raw CLI strings never become a verified identity.
    """

    def __init__(
        self,
        *,
        caller: VerifiedCallerContext | DemoCallerContext,
        cache_dir: str | Path,
        limits: MountLimits = MountLimits(),
        clock: Callable[[], float] | None = None,
        identity_clock: Callable[[], float] | None = None,
    ) -> None:
        if isinstance(caller, VerifiedCallerContext):
            self._caller = caller
            self._identity_clock = identity_clock or time.time
            caller.assert_valid(now=self._identity_clock())
            self.principal = caller.principal
            self.project_key = caller.project_key
            self.trust_mode = "verified-claims"
        elif isinstance(caller, DemoCallerContext):
            if not isinstance(caller.principal, str) or not caller.principal.strip():
                _fail(ErrorCode.PERMISSION_DENIED, "A non-empty demo principal is required.")
            if not isinstance(caller.project_key, str):
                _fail(ErrorCode.QUERY_INVALID, "project_key must be a string.")
            self._caller = caller
            self._identity_clock = identity_clock or time.time
            self.principal = caller.principal.strip()
            self.project_key = caller.project_key.strip()
            self.trust_mode = "untrusted-demo"
        else:
            raise TypeError(
                "caller must be VerifiedCallerContext or DemoCallerContext; "
                "raw principal strings are not accepted."
            )
        self.cache_dir = Path(cache_dir).expanduser().resolve()
        self.limits = limits.validated()
        self._clock = clock or time.monotonic
        self._mounts: dict[str, _MountedPack] = {}

    @property
    def is_verified(self) -> bool:
        return isinstance(self._caller, VerifiedCallerContext)

    @classmethod
    def for_demo(
        cls,
        *,
        principal: str,
        project_key: str,
        cache_dir: str | Path,
        session_id: str = "demo-session",
        limits: MountLimits = MountLimits(),
        clock: Callable[[], float] | None = None,
        identity_clock: Callable[[], float] | None = None,
    ) -> MemoryMountRegistry:
        """Explicit untrusted demo constructor; never use for production auth."""

        if not isinstance(principal, str) or not principal.strip():
            _fail(ErrorCode.PERMISSION_DENIED, "A non-empty demo principal is required.")
        if not isinstance(project_key, str):
            _fail(ErrorCode.QUERY_INVALID, "project_key must be a string.")
        return cls(
            caller=DemoCallerContext(
                principal=principal.strip(),
                project_key=project_key.strip(),
                session_id=session_id,
            ),
            cache_dir=cache_dir,
            limits=limits,
            clock=clock,
            identity_clock=identity_clock,
        )

    @classmethod
    def from_verified(
        cls,
        *,
        caller: VerifiedCallerContext,
        cache_dir: str | Path,
        limits: MountLimits = MountLimits(),
        clock: Callable[[], float] | None = None,
        identity_clock: Callable[[], float] | None = None,
    ) -> MemoryMountRegistry:
        if not isinstance(caller, VerifiedCallerContext):
            _fail(
                ErrorCode.IDENTITY_REQUIRED,
                "A VerifiedCallerContext is required for this entry point.",
            )
        return cls(
            caller=caller,
            cache_dir=cache_dir,
            limits=limits,
            clock=clock,
            identity_clock=identity_clock,
        )

    def _assert_caller(self) -> None:
        if isinstance(self._caller, VerifiedCallerContext):
            self._caller.assert_valid(now=self._identity_clock())

    def mount(self, pack_path: str | Path) -> MountInfo:
        self._assert_caller()
        content = load_pack(pack_path, limits=self.limits, authorize=self._authorize)
        manifest = content.manifest
        if self.cache_dir == content.root or self.cache_dir.is_relative_to(content.root):
            _fail(
                ErrorCode.PACK_PATH_INVALID,
                "The index cache directory must not be inside a pack root.",
                packId=manifest.pack_id,
            )
        existing = self._mounts.get(manifest.pack_id)
        if existing is not None:
            if existing.manifest.content_hash == manifest.content_hash:
                if existing.content.root == content.root:
                    return replace(existing.info, already_mounted=True, index_reused=True)
                _fail(
                    ErrorCode.PACK_ALREADY_MOUNTED,
                    "The same pack content is already mounted from a different path.",
                    packId=manifest.pack_id,
                )
            _fail(
                ErrorCode.PACK_REVISION_CONFLICT,
                "A different content revision is already mounted for this packId.",
                packId=manifest.pack_id,
                mountedContentHash=existing.manifest.content_hash,
                requestedContentHash=manifest.content_hash,
            )
        if len(self._mounts) >= self.limits.max_mounted_packs:
            _fail(
                ErrorCode.MOUNT_LIMIT,
                "Mounted-pack limit reached.",
                limit=self.limits.max_mounted_packs,
            )
        total_records = sum(item.manifest.record_count for item in self._mounts.values())
        if total_records + manifest.record_count > self.limits.max_total_records:
            _fail(
                ErrorCode.MOUNT_LIMIT,
                "Total mounted-record limit reached.",
                limit=self.limits.max_total_records,
            )
        index = PackIndex.open_or_build(
            content,
            cache_dir=self.cache_dir,
            limits=self.limits,
        )
        info = MountInfo(
            pack_id=manifest.pack_id,
            revision=manifest.revision,
            content_hash=manifest.content_hash,
            priority=manifest.priority,
            scope=manifest.scope,
            record_count=manifest.record_count,
            index_format_version=INDEX_FORMAT_VERSION,
            index_reused=index.reused,
            already_mounted=False,
            integrity=content.integrity.to_payload(),
        )
        self._mounts[manifest.pack_id] = _MountedPack(
            content=content,
            manifest=manifest,
            info=info,
            index=index,
        )
        return info

    def unmount(self, pack_id: str) -> None:
        if not isinstance(pack_id, str) or not pack_id:
            _fail(ErrorCode.PACK_NOT_MOUNTED, "packId is required.", packId=pack_id)
        mounted = self._mounts.pop(pack_id, None)
        if mounted is None:
            _fail(ErrorCode.PACK_NOT_MOUNTED, "Pack is not mounted.", packId=pack_id)
        mounted.index.close()

    def list_mounts(self) -> tuple[MountInfo, ...]:
        return tuple(self._mounts[pack_id].info for pack_id in sorted(self._mounts))

    def close(self) -> None:
        """Close every index connection. Mount state is discarded."""
        for mounted in list(self._mounts.values()):
            mounted.index.close()
        self._mounts.clear()

    def __enter__(self) -> MemoryMountRegistry:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def query(
        self,
        query: str,
        *,
        record_types: Sequence[str] = (),
        scope_type: str = "",
        scope_key: str = "",
        limit: int = 20,
        include_conflicts: bool = False,
        body_preview_chars: int | None = None,
        timeout_ms: int | None = None,
    ) -> QueryResult:
        self._assert_caller()
        normalized_query = self._normalize_query(query)
        normalized_types = self._normalize_record_types(record_types)
        normalized_scope_type, normalized_scope_key = self._normalize_scope(
            scope_type,
            scope_key,
        )
        if not isinstance(limit, int) or isinstance(limit, bool) or not (
            1 <= limit <= self.limits.max_result_limit
        ):
            _fail(
                ErrorCode.QUERY_INVALID,
                f"limit must be between 1 and {self.limits.max_result_limit}.",
            )
        if body_preview_chars is None:
            body_preview_chars = self.limits.default_body_preview_chars
        if not isinstance(body_preview_chars, int) or isinstance(body_preview_chars, bool) or not (
            0 <= body_preview_chars <= self.limits.max_body_preview_chars
        ):
            _fail(
                ErrorCode.QUERY_INVALID,
                f"bodyPreviewChars must be between 0 and {self.limits.max_body_preview_chars}.",
            )
        if timeout_ms is None:
            timeout_ms = self.limits.default_timeout_ms
        if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or not (
            1 <= timeout_ms <= self.limits.max_timeout_ms
        ):
            _fail(
                ErrorCode.QUERY_INVALID,
                f"timeoutMs must be between 1 and {self.limits.max_timeout_ms}.",
            )
        if not isinstance(include_conflicts, bool):
            _fail(ErrorCode.QUERY_INVALID, "includeConflicts must be boolean.")

        if not self._mounts:
            return QueryResult(
                items=(),
                warnings=(_WARNING_NO_MOUNTS,),
                retrieval_mode="empty",
                candidate_count=0,
                conflict_count=0,
                mounted_pack_count=0,
                truncated=False,
            )

        deadline = self._clock() + (timeout_ms / 1_000.0)
        candidate_limit = min(
            max(limit * 4, limit + 10),
            self.limits.max_candidate_cap,
        )
        candidates: list[_Candidate] = []
        candidate_cap_hit = False
        for pack_id in sorted(self._mounts):
            mounted = self._mounts[pack_id]
            self._authorize(mounted.manifest)
            if self._clock() > deadline:
                _fail(ErrorCode.QUERY_TIMEOUT, "Pack query exceeded its deadline.")
            hits: tuple[IndexHit, ...] = mounted.index.search(
                normalized_query,
                limit=candidate_limit,
                record_types=normalized_types,
                scope_type=normalized_scope_type,
                scope_key=normalized_scope_key,
                deadline=deadline,
                max_short_scan=self.limits.max_short_query_scan_records,
            )
            if self._clock() > deadline:
                _fail(ErrorCode.QUERY_TIMEOUT, "Pack query exceeded its deadline.")
            if len(hits) >= candidate_limit:
                candidate_cap_hit = True
            candidates.extend(
                _Candidate(
                    record=hit.record,
                    manifest=mounted.manifest,
                    match_reason=hit.match_reason,
                    score=hit.score,
                )
                for hit in hits
            )

        groups: dict[tuple[str, str], list[_Candidate]] = {}
        for candidate in candidates:
            key = (candidate.record.record_type, candidate.record.subject_key)
            groups.setdefault(key, []).append(candidate)

        entries: list[dict[str, Any]] = []
        conflict_count = 0
        for key in sorted(groups, key=lambda item: self._group_sort_key(groups[item])):
            group = sorted(groups[key], key=self._rank_key)
            unique: list[_Candidate] = []
            seen_content: set[str] = set()
            for candidate in group:
                if candidate.record.content_sha256 not in seen_content:
                    seen_content.add(candidate.record.content_sha256)
                    unique.append(candidate)
            winner = unique[0]
            is_conflict = len(unique) > 1
            if is_conflict:
                conflict_count += 1
            conflict_payload = None
            if is_conflict:
                conflict_payload = {
                    "groupId": _group_id(key),
                    "state": "unresolved",
                    "alternativeCount": len(unique) - 1,
                    "alternativePackIds": sorted(
                        {
                            candidate.manifest.pack_id
                            for candidate in group
                            if candidate.record.content_sha256
                            != winner.record.content_sha256
                        }
                    ),
                    "resolution": f"{winner.manifest.scope.kind}-tier-wins",
                }
            if include_conflicts and is_conflict:
                for index, candidate in enumerate(unique):
                    entries.append(
                        self._item_payload(
                            candidate,
                            body_preview_chars=body_preview_chars,
                            conflict=conflict_payload,
                            selected=index == 0,
                            include_selection=True,
                        )
                    )
            else:
                entries.append(
                    self._item_payload(
                        winner,
                        body_preview_chars=body_preview_chars,
                        conflict=conflict_payload,
                        selected=True,
                        include_selection=False,
                    )
                )

        truncated = len(entries) > limit
        items = entries[:limit]
        warnings: list[str] = []
        if candidate_cap_hit:
            warnings.append(_WARNING_CANDIDATE_CAP)
        if conflict_count:
            warnings.append(_WARNING_UNRESOLVED_CONFLICT)
        if truncated:
            warnings.append(_WARNING_RESULT_LIMIT)
        retrieval_mode = (
            "empty"
            if not candidates
            else (
                "fts5-trigram+substring-fallback"
                if any(
                    candidate.match_reason == "cjk-substring-fallback"
                    for candidate in candidates
                )
                else "fts5-trigram"
            )
        )

        def build_result(
            item_list: list[dict[str, Any]],
            warning_list: list[str],
            truncated_flag: bool,
        ) -> QueryResult:
            return QueryResult(
                items=tuple(item_list),
                warnings=tuple(warning_list),
                retrieval_mode=retrieval_mode,
                candidate_count=len(candidates),
                conflict_count=conflict_count,
                mounted_pack_count=len(self._mounts),
                truncated=truncated_flag,
            )

        result = build_result(items, warnings, truncated)
        while len(
            json.dumps(result.to_payload(), ensure_ascii=False, separators=(",", ":"))
        ) > self.limits.max_output_chars:
            if len(items) <= 1:
                _fail(
                    ErrorCode.OUTPUT_TOO_LARGE,
                    "A single query item exceeds the output-size limit.",
                    limit=self.limits.max_output_chars,
                )
            items = items[:-1]
            truncated = True
            if _WARNING_OUTPUT_TRUNCATED not in warnings:
                warnings.append(_WARNING_OUTPUT_TRUNCATED)
            result = build_result(items, warnings, truncated)
        return result

    def _authorize(self, manifest: PackManifest) -> None:
        scope = manifest.scope
        if scope.kind == "personal":
            if scope.owner != self.principal:
                _fail(
                    ErrorCode.PERMISSION_DENIED,
                    "Personal pack is not owned by this principal.",
                    packId=manifest.pack_id,
                )
            return
        if scope.kind == "project":
            if not self.project_key or scope.project_key != self.project_key:
                _fail(
                    ErrorCode.PROJECT_MISMATCH,
                    "Project pack does not match the active project.",
                    packId=manifest.pack_id,
                )
            return
        if scope.kind == "shared":
            return
        _fail(ErrorCode.INTERNAL, "Unknown pack scope kind.", scopeKind=scope.kind)

    def _normalize_query(self, query: str) -> str:
        if not isinstance(query, str):
            _fail(ErrorCode.QUERY_INVALID, "query must be a string.")
        if any(ord(char) < 0x20 or ord(char) == 0x7F for char in query):
            _fail(ErrorCode.QUERY_INVALID, "query must not contain control characters.")
        normalized = " ".join(query.split())
        if not normalized:
            _fail(ErrorCode.QUERY_INVALID, "query must not be empty.")
        if len(normalized) > self.limits.max_query_chars:
            _fail(
                ErrorCode.QUERY_INVALID,
                f"query must not exceed {self.limits.max_query_chars} characters.",
            )
        return normalized

    def _normalize_record_types(self, record_types: Sequence[str]) -> tuple[str, ...]:
        if not isinstance(record_types, (list, tuple)):
            _fail(ErrorCode.QUERY_INVALID, "recordTypes must be a sequence.")
        normalized: list[str] = []
        for value in record_types:
            if not isinstance(value, str) or value not in _RECORD_TYPES:
                _fail(ErrorCode.QUERY_INVALID, "Unsupported recordType filter.", value=value)
            if value not in normalized:
                normalized.append(value)
        return tuple(normalized)

    def _normalize_scope(self, scope_type: str, scope_key: str) -> tuple[str, str]:
        if not isinstance(scope_type, str) or not isinstance(scope_key, str):
            _fail(ErrorCode.QUERY_INVALID, "scopeType and scopeKey must be strings.")
        if scope_key and not scope_type:
            _fail(ErrorCode.QUERY_INVALID, "scopeKey requires scopeType.")
        if scope_type and scope_type not in _SCOPE_TYPES:
            _fail(ErrorCode.QUERY_INVALID, "Unsupported scopeType filter.", value=scope_type)
        if len(scope_key) > self.limits.max_scope_key_chars:
            _fail(ErrorCode.QUERY_INVALID, "scopeKey is too long.")
        return scope_type, scope_key

    def _rank_key(self, candidate: _Candidate) -> tuple[Any, ...]:
        tier = _SCOPE_TIERS[candidate.manifest.scope.kind]
        status_rank = _STATUS_RANKS[candidate.record.status]
        return (
            -tier,
            -candidate.manifest.priority,
            -status_rank,
            -candidate.record.updated_epoch,
            candidate.manifest.pack_id,
            candidate.record.record_id,
        )

    def _group_sort_key(self, group: list[_Candidate]) -> tuple[Any, ...]:
        winner = min(group, key=self._rank_key)
        rank = list(self._rank_key(winner))
        rank.insert(4, -winner.score)
        return tuple(rank)

    def _item_payload(
        self,
        candidate: _Candidate,
        *,
        body_preview_chars: int,
        conflict: dict[str, Any] | None,
        selected: bool,
        include_selection: bool,
    ) -> dict[str, Any]:
        record = candidate.record
        body = record.body
        if body_preview_chars == 0:
            preview = ""
            body_truncated = bool(body)
        else:
            preview = body[:body_preview_chars]
            body_truncated = len(body) > body_preview_chars
        payload: dict[str, Any] = {
            "globalId": record.global_id,
            "packId": candidate.manifest.pack_id,
            "recordId": record.record_id,
            "recordType": record.record_type,
            "subjectKey": record.subject_key,
            "title": record.title,
            "bodyPreview": preview,
            "bodyTruncated": body_truncated,
            "sourceKind": record.source_kind,
            "confidence": record.confidence,
            "status": record.status,
            "scopes": [
                {"scopeType": scope["scopeType"], "scopeKey": scope["scopeKey"]}
                for scope in record.scopes
            ],
            "updatedAtUtc": record.updated_at_utc,
            "contentSha256": record.content_sha256,
            "matchReason": candidate.match_reason,
            "score": candidate.score,
            "rank": {
                "scopeTier": _SCOPE_TIERS[candidate.manifest.scope.kind],
                "priority": candidate.manifest.priority,
                "statusRank": _STATUS_RANKS[record.status],
            },
            "provenance": {
                "kind": candidate.manifest.provenance.kind,
                "producer": candidate.manifest.provenance.producer,
                "sourceRef": candidate.manifest.provenance.source_ref,
            },
        }
        if conflict is not None:
            payload["conflict"] = dict(conflict)
        if conflict is not None and include_selection:
            payload["selected"] = selected
        return payload


def _group_id(key: tuple[str, str]) -> str:
    digest = hashlib.sha256(canonical_json_bytes({"recordType": key[0], "subjectKey": key[1]})).hexdigest()
    return f"sha256:{digest[:16]}"
