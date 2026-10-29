"""Manifest parsing and validation for R7 knowledge packs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .errors import ErrorCode, MemoryMountError
from .jsonutil import parse_utc
from .limits import MountLimits


READER_VERSION = 1
SUPPORTED_FEATURES = frozenset({"fts5-trigram", "cjk-substring-fallback"})
SUPPORTED_MEMORY_SCHEMA = 6
_PACK_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
_CONTENT_HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_MANIFEST_KEYS = {
    "schemaVersion",
    "packId",
    "contentHash",
    "revision",
    "provenance",
    "priority",
    "scope",
    "compatibility",
    "recordCount",
    "title",
    "description",
}
_PROVENANCE_KEYS = {"kind", "producer", "sourceRef", "createdAtUtc", "signature"}
_SCOPE_KEYS = {"kind", "projectKey", "owner", "visibility"}
_COMPATIBILITY_KEYS = {
    "minReaderVersion",
    "maxReaderVersion",
    "memorySchema",
    "tokenizer",
    "requiredFeatures",
}


def _fail(code: ErrorCode, message: str, **details: Any) -> None:
    raise MemoryMountError(code, message, details=details)


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(ErrorCode.MANIFEST_INVALID, f"{field_name} must be a JSON object.", field=field_name)
    return value


def _reject_unknown(mapping: dict[str, Any], allowed: set[str], field_name: str) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        _fail(
            ErrorCode.MANIFEST_INVALID,
            f"{field_name} contains unsupported fields.",
            field=field_name,
            unknownFields=unknown,
        )


def _string(
    value: Any,
    field_name: str,
    *,
    minimum: int = 0,
    maximum: int | None = None,
) -> str:
    if not isinstance(value, str):
        _fail(ErrorCode.MANIFEST_INVALID, f"{field_name} must be a string.", field=field_name)
    if len(value) < minimum:
        _fail(
            ErrorCode.MANIFEST_INVALID,
            f"{field_name} must contain at least {minimum} characters.",
            field=field_name,
        )
    if maximum is not None and len(value) > maximum:
        _fail(
            ErrorCode.MANIFEST_INVALID,
            f"{field_name} must contain at most {maximum} characters.",
            field=field_name,
        )
    return value


def _integer(value: Any, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        _fail(ErrorCode.MANIFEST_INVALID, f"{field_name} must be an integer.", field=field_name)
    return value


@dataclass(frozen=True)
class PackScope:
    kind: str
    project_key: str = ""
    owner: str = ""
    visibility: str = ""

    @classmethod
    def from_dict(cls, value: Any) -> PackScope:
        data = _mapping(value, "scope")
        _reject_unknown(data, _SCOPE_KEYS, "scope")
        kind = _string(data.get("kind"), "scope.kind", minimum=1, maximum=16)
        if kind not in {"personal", "project", "shared"}:
            _fail(
                ErrorCode.MANIFEST_INVALID,
                "scope.kind must be personal, project, or shared.",
                field="scope.kind",
            )
        project_key = data.get("projectKey", "")
        owner = data.get("owner", "")
        if kind == "project":
            project_key = _string(project_key, "scope.projectKey", minimum=1, maximum=128)
            if owner:
                _fail(
                    ErrorCode.MANIFEST_INVALID,
                    "scope.owner is not allowed for project packs.",
                    field="scope.owner",
                )
        elif kind == "personal":
            owner = _string(owner, "scope.owner", minimum=1, maximum=128)
            if project_key:
                _fail(
                    ErrorCode.MANIFEST_INVALID,
                    "scope.projectKey is not allowed for personal packs.",
                    field="scope.projectKey",
                )
        else:
            if project_key or owner:
                _fail(
                    ErrorCode.MANIFEST_INVALID,
                    "shared packs must not declare projectKey or owner.",
                    field="scope",
                )
        visibility = data.get("visibility", kind)
        visibility = _string(visibility, "scope.visibility", minimum=1, maximum=16)
        if visibility != kind:
            _fail(
                ErrorCode.MANIFEST_INVALID,
                "scope.visibility must equal scope.kind in v1.",
                field="scope.visibility",
            )
        return cls(kind=kind, project_key=project_key, owner=owner, visibility=visibility)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"kind": self.kind, "visibility": self.visibility}
        if self.project_key:
            payload["projectKey"] = self.project_key
        if self.owner:
            payload["owner"] = self.owner
        return payload


@dataclass(frozen=True)
class PackProvenance:
    kind: str
    producer: str
    source_ref: str
    created_at_utc: str

    @classmethod
    def from_dict(cls, value: Any) -> PackProvenance:
        data = _mapping(value, "provenance")
        _reject_unknown(data, _PROVENANCE_KEYS, "provenance")
        if data.get("signature") is not None:
            _fail(
                ErrorCode.SIGNATURE_UNSUPPORTED,
                "Signed packs are not supported in R7 v1; signature must be null.",
                field="provenance.signature",
            )
        kind = _string(data.get("kind"), "provenance.kind", minimum=1, maximum=16)
        if kind not in {"authored", "exported", "derived", "imported"}:
            _fail(
                ErrorCode.MANIFEST_INVALID,
                "provenance.kind must be authored, exported, derived, or imported.",
                field="provenance.kind",
            )
        producer = _string(data.get("producer"), "provenance.producer", minimum=1, maximum=128)
        source_ref = _string(data.get("sourceRef"), "provenance.sourceRef", maximum=512)
        created_at = _string(
            data.get("createdAtUtc"),
            "provenance.createdAtUtc",
            minimum=1,
            maximum=64,
        )
        try:
            parse_utc(created_at, "provenance.createdAtUtc")
        except ValueError as exc:
            _fail(ErrorCode.MANIFEST_INVALID, str(exc), field="provenance.createdAtUtc")
        return cls(
            kind=kind,
            producer=producer,
            source_ref=source_ref,
            created_at_utc=created_at,
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "producer": self.producer,
            "sourceRef": self.source_ref,
            "createdAtUtc": self.created_at_utc,
        }


@dataclass(frozen=True)
class PackCompatibility:
    min_reader_version: int
    max_reader_version: int
    memory_schema: int | None
    tokenizer: str
    required_features: tuple[str, ...]

    @classmethod
    def from_dict(cls, value: Any) -> PackCompatibility:
        data = _mapping(value, "compatibility")
        _reject_unknown(data, _COMPATIBILITY_KEYS, "compatibility")
        minimum = _integer(data.get("minReaderVersion"), "compatibility.minReaderVersion")
        maximum = _integer(data.get("maxReaderVersion"), "compatibility.maxReaderVersion")
        if minimum < 1 or maximum < minimum:
            _fail(
                ErrorCode.MANIFEST_INCOMPATIBLE,
                "compatibility reader-version range is invalid.",
                minReaderVersion=minimum,
                maxReaderVersion=maximum,
            )
        if not (minimum <= READER_VERSION <= maximum):
            _fail(
                ErrorCode.MANIFEST_INCOMPATIBLE,
                f"Pack requires reader versions {minimum}..{maximum}; reader is {READER_VERSION}.",
            )
        memory_schema = data.get("memorySchema")
        if memory_schema is not None:
            memory_schema = _integer(memory_schema, "compatibility.memorySchema")
            if memory_schema != SUPPORTED_MEMORY_SCHEMA:
                _fail(
                    ErrorCode.MANIFEST_INCOMPATIBLE,
                    f"Only memory schema {SUPPORTED_MEMORY_SCHEMA} is supported.",
                    memorySchema=memory_schema,
                )
        tokenizer = _string(
            data.get("tokenizer"),
            "compatibility.tokenizer",
            minimum=1,
            maximum=32,
        )
        if tokenizer != "trigram":
            _fail(
                ErrorCode.MANIFEST_INCOMPATIBLE,
                "Only the trigram tokenizer is supported in v1.",
                tokenizer=tokenizer,
            )
        raw_features = data.get("requiredFeatures")
        if not isinstance(raw_features, list):
            _fail(
                ErrorCode.MANIFEST_INVALID,
                "compatibility.requiredFeatures must be an array.",
                field="compatibility.requiredFeatures",
            )
        if len(raw_features) > 8:
            _fail(
                ErrorCode.MANIFEST_INVALID,
                "compatibility.requiredFeatures must contain at most 8 items.",
                field="compatibility.requiredFeatures",
            )
        features: list[str] = []
        for index, item in enumerate(raw_features):
            feature = _string(
                item,
                f"compatibility.requiredFeatures[{index}]",
                minimum=1,
                maximum=64,
            )
            if feature not in SUPPORTED_FEATURES:
                _fail(
                    ErrorCode.MANIFEST_INCOMPATIBLE,
                    f"Unsupported required feature: {feature}.",
                    feature=feature,
                )
            if feature in features:
                _fail(
                    ErrorCode.MANIFEST_INVALID,
                    "compatibility.requiredFeatures must not contain duplicates.",
                    feature=feature,
                )
            features.append(feature)
        return cls(
            min_reader_version=minimum,
            max_reader_version=maximum,
            memory_schema=memory_schema,
            tokenizer=tokenizer,
            required_features=tuple(features),
        )

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "minReaderVersion": self.min_reader_version,
            "maxReaderVersion": self.max_reader_version,
            "tokenizer": self.tokenizer,
            "requiredFeatures": list(self.required_features),
        }
        if self.memory_schema is not None:
            payload["memorySchema"] = self.memory_schema
        return payload


@dataclass(frozen=True)
class PackManifest:
    schema_version: int
    pack_id: str
    content_hash: str
    revision: str
    provenance: PackProvenance
    priority: int
    scope: PackScope
    compatibility: PackCompatibility
    record_count: int
    title: str = ""
    description: str = ""

    @classmethod
    def from_dict(cls, value: Any, *, limits: MountLimits) -> PackManifest:
        data = _mapping(value, "manifest")
        _reject_unknown(data, _MANIFEST_KEYS, "manifest")
        schema_version = _integer(data.get("schemaVersion"), "schemaVersion")
        if schema_version != 1:
            _fail(
                ErrorCode.MANIFEST_UNSUPPORTED_SCHEMA,
                f"Unsupported manifest schemaVersion: {schema_version}.",
                schemaVersion=schema_version,
            )
        pack_id = _string(data.get("packId"), "packId", minimum=3, maximum=64)
        if not _PACK_ID_PATTERN.fullmatch(pack_id):
            _fail(
                ErrorCode.MANIFEST_INVALID,
                "packId must match ^[a-z0-9][a-z0-9._-]{2,63}$.",
                field="packId",
            )
        content_hash = _string(data.get("contentHash"), "contentHash", minimum=71, maximum=71)
        if not _CONTENT_HASH_PATTERN.fullmatch(content_hash):
            _fail(
                ErrorCode.MANIFEST_INVALID,
                "contentHash must be sha256:<64 lowercase hex characters>.",
                field="contentHash",
            )
        revision = _string(data.get("revision"), "revision", minimum=1, maximum=128)
        if revision != revision.strip() or any(ord(char) < 0x20 or ord(char) == 0x7F for char in revision):
            _fail(
                ErrorCode.MANIFEST_INVALID,
                "revision must be trimmed printable text without control characters.",
                field="revision",
            )
        priority = _integer(data.get("priority"), "priority")
        if priority < 0 or priority > 100:
            _fail(
                ErrorCode.MANIFEST_INVALID,
                "priority must be between 0 and 100.",
                field="priority",
            )
        record_count = _integer(data.get("recordCount"), "recordCount")
        if record_count < 0 or record_count > limits.max_records_per_pack:
            _fail(
                ErrorCode.PACK_TOO_LARGE,
                f"recordCount must be between 0 and {limits.max_records_per_pack}.",
                recordCount=record_count,
            )
        title_raw = data.get("title", "")
        title = "" if title_raw == "" else _string(title_raw, "title", minimum=1, maximum=200)
        description_raw = data.get("description", "")
        description = (
            ""
            if description_raw == ""
            else _string(description_raw, "description", maximum=2_000)
        )
        return cls(
            schema_version=schema_version,
            pack_id=pack_id,
            content_hash=content_hash,
            revision=revision,
            provenance=PackProvenance.from_dict(data.get("provenance")),
            priority=priority,
            scope=PackScope.from_dict(data.get("scope")),
            compatibility=PackCompatibility.from_dict(data.get("compatibility")),
            record_count=record_count,
            title=str(title),
            description=str(description),
        )

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schemaVersion": self.schema_version,
            "packId": self.pack_id,
            "contentHash": self.content_hash,
            "revision": self.revision,
            "provenance": self.provenance.to_payload(),
            "priority": self.priority,
            "scope": self.scope.to_payload(),
            "compatibility": self.compatibility.to_payload(),
            "recordCount": self.record_count,
        }
        if self.title:
            payload["title"] = self.title
        if self.description:
            payload["description"] = self.description
        return payload
