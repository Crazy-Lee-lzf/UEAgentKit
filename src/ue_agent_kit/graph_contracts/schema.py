"""A deliberately small JSON-Schema subset with a stdlib-only validator.

Frozen subset (CR-OD-01 / EC-SCHEMA-SUBSET):

    type, properties, required, additionalProperties, items, enum, const,
    oneOf, minimum, maximum, minLength, maxLength, pattern

Explicitly unsupported and rejected at schema-load time: ``$ref``, ``format``,
``if``/``then``/``else``, ``allOf``/``anyOf``/``not``, ``patternProperties``,
``dependencies``, ``contains``, boolean schemas, and schema-valued
``additionalProperties``.  Rejecting unknown keywords is what prevents an
implementation from smuggling a second validation dialect past the contract.

Version negotiation (EC-VERSION):

* different major -> reject with a domain-specific ``*-schema-version-unsupported``
* newer minor     -> accept, ignore unknown optional fields, warn ``contract-schema-minor-newer``
* same/older minor-> accept and validate strictly (unknown fields are errors)

No third-party dependency; ``jsonschema`` is not imported at runtime.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .errors import make_diagnostic

SUPPORTED_KEYWORDS = {
    "type",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "enum",
    "const",
    "oneOf",
    "minimum",
    "maximum",
    "minLength",
    "maxLength",
    "pattern",
}
ANNOTATION_KEYWORDS = {
    "$schema",
    "$id",
    "title",
    "description",
    "default",
    "examples",
    "deprecated",
    "readOnly",
}
JSON_TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}
VERSION_RE = re.compile(r"^(?P<namespace>[a-z][a-z0-9-]*(?:\.[a-z0-9-]+)*)/(?P<major>\d+)\.(?P<minor>\d+)$")
MINOR_NEWER_WARNING = "contract-schema-minor-newer"


class SchemaError(ValueError):
    def __init__(self, code: str, message: str, path: str = "$") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.path = path


@dataclass
class VersionDecision:
    version: str
    namespace: str
    major: int
    minor: int
    accepted: bool
    minor_newer: bool
    diagnostics: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "namespace": self.namespace,
            "major": self.major,
            "minor": self.minor,
            "accepted": self.accepted,
            "minorNewer": self.minor_newer,
            "diagnostics": list(self.diagnostics),
        }


def parse_schema_version(version: str) -> tuple[str, int, int]:
    if not isinstance(version, str):
        raise SchemaError("contract-schema-invalid", "schemaVersion must be a string", "$.schemaVersion")
    match = VERSION_RE.fullmatch(version)
    if not match:
        raise SchemaError(
            "contract-schema-invalid",
            f"schemaVersion must look like 'namespace/M.major.minor': {version!r}",
            "$.schemaVersion",
        )
    return match.group("namespace"), int(match.group("major")), int(match.group("minor"))


def negotiate_version(
    version: str,
    *,
    namespace: str,
    supported_major: int = 1,
    supported_minor: int = 0,
    unsupported_code: str,
    path: str = "$.schemaVersion",
) -> VersionDecision:
    """Apply the reader rule for one schema document."""

    parsed_namespace, major, minor = parse_schema_version(version)
    if parsed_namespace != namespace:
        raise SchemaError(unsupported_code, f"Expected schema namespace {namespace!r}, got {version!r}", path)
    if major != supported_major:
        raise SchemaError(
            unsupported_code,
            f"Unsupported major version {major} for {namespace} (supported {supported_major}.x)",
            path,
        )
    decision = VersionDecision(
        version=version,
        namespace=namespace,
        major=major,
        minor=minor,
        accepted=True,
        minor_newer=minor > supported_minor,
    )
    if decision.minor_newer:
        decision.diagnostics.append(
            make_diagnostic(
                MINOR_NEWER_WARNING,
                f"{namespace} minor {minor} is newer than reader minor {supported_minor}; "
                "unknown optional fields were ignored",
                path=path,
                context={"documentMinor": minor, "readerMinor": supported_minor},
            )
        )
    return decision


def _is_json_type(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return False


def _json_equal(left: Any, right: Any) -> bool:
    """JSON equality that keeps true/false distinct from 1/0."""

    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, list):
        return len(left) == len(right) and all(_json_equal(a, b) for a, b in zip(left, right))
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_json_equal(left[key], right[key]) for key in left)
    return left == right


def check_schema(schema: Any, *, path: str = "$") -> None:
    """Meta-validate a schema document against the frozen keyword subset."""

    if not isinstance(schema, dict):
        raise SchemaError("contract-schema-invalid", "Schema nodes must be JSON objects", path)
    for keyword, value in schema.items():
        keyword_path = f"{path}.{keyword}"
        if keyword.startswith("x-") or keyword in ANNOTATION_KEYWORDS:
            continue
        if keyword not in SUPPORTED_KEYWORDS:
            raise SchemaError(
                "contract-schema-keyword-unsupported",
                f"Keyword {keyword!r} is outside the frozen stdlib schema subset",
                keyword_path,
            )
        if keyword == "type":
            if not isinstance(value, str) or value not in JSON_TYPES:
                raise SchemaError("contract-schema-invalid", "type must be one of the JSON type names", keyword_path)
        elif keyword == "properties":
            if not isinstance(value, dict):
                raise SchemaError("contract-schema-invalid", "properties must be an object", keyword_path)
            for name, subschema in value.items():
                if not isinstance(name, str) or not name:
                    raise SchemaError("contract-schema-invalid", "property names must be non-empty strings", keyword_path)
                check_schema(subschema, path=f"{keyword_path}.{name}")
        elif keyword == "required":
            if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
                raise SchemaError("contract-schema-invalid", "required must be a list of non-empty strings", keyword_path)
            if len(set(value)) != len(value):
                raise SchemaError("contract-schema-invalid", "required must not contain duplicates", keyword_path)
        elif keyword == "additionalProperties":
            if not isinstance(value, bool):
                raise SchemaError(
                    "contract-schema-keyword-unsupported",
                    "schema-valued additionalProperties is not supported",
                    keyword_path,
                )
        elif keyword == "items":
            check_schema(value, path=keyword_path)
        elif keyword == "enum":
            if not isinstance(value, list) or not value:
                raise SchemaError("contract-schema-invalid", "enum must be a non-empty list", keyword_path)
        elif keyword == "const":
            pass
        elif keyword == "oneOf":
            if not isinstance(value, list) or not value:
                raise SchemaError("contract-schema-invalid", "oneOf must be a non-empty list", keyword_path)
            for index, subschema in enumerate(value):
                check_schema(subschema, path=f"{keyword_path}[{index}]")
        elif keyword in ("minimum", "maximum"):
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise SchemaError("contract-schema-invalid", f"{keyword} must be a number", keyword_path)
        elif keyword in ("minLength", "maxLength"):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise SchemaError("contract-schema-invalid", f"{keyword} must be a non-negative integer", keyword_path)
        elif keyword == "pattern":
            if not isinstance(value, str) or not value:
                raise SchemaError("contract-schema-invalid", "pattern must be a non-empty string", keyword_path)
            try:
                re.compile(value)
            except re.error as exc:
                raise SchemaError("contract-schema-invalid", f"invalid pattern: {exc}", keyword_path) from exc


def _validate(instance: Any, schema: dict[str, Any], *, strict: bool, path: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []

    if "type" in schema:
        expected = schema["type"]
        if not _is_json_type(instance, expected):
            issues.append(
                make_diagnostic(
                    "contract-schema-type-mismatch",
                    f"Expected JSON type {expected}",
                    path=path,
                    context={"expected": expected, "actual": type(instance).__name__},
                )
            )
            return issues

    if "const" in schema and not _json_equal(instance, schema["const"]):
        issues.append(make_diagnostic("contract-schema-const-mismatch", "Value does not equal const", path=path))

    if "enum" in schema and not any(_json_equal(instance, candidate) for candidate in schema["enum"]):
        issues.append(
            make_diagnostic(
                "contract-schema-enum-mismatch",
                "Value is not one of the allowed enum values",
                path=path,
                context={"allowed": list(schema["enum"])},
            )
        )

    if "oneOf" in schema:
        matches = 0
        branch_issues: list[list[dict[str, Any]]] = []
        for subschema in schema["oneOf"]:
            branch = _validate(instance, subschema, strict=strict, path=path)
            branch_issues.append(branch)
            if not branch:
                matches += 1
        if matches != 1:
            issues.append(
                make_diagnostic(
                    "contract-schema-oneof-mismatch",
                    f"Value must match exactly one oneOf branch (matched {matches})",
                    path=path,
                    context={"matched": matches},
                )
            )
            # Keep branch detail for diagnostics without flooding callers.
            for index, branch in enumerate(branch_issues):
                if len(branch) <= 2:
                    for issue in branch:
                        issue["context"] = dict(issue.get("context", {}), oneOfBranch=index)
                        issues.append(issue)

    if isinstance(instance, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        for name in required:
            if name not in instance:
                issues.append(
                    make_diagnostic("contract-schema-required", f"Missing required property {name!r}", path=path)
                )
        for name, value in instance.items():
            if name in properties:
                issues.extend(_validate(value, properties[name], strict=strict, path=f"{path}.{name}"))
            elif schema.get("additionalProperties") is False and strict:
                issues.append(
                    make_diagnostic(
                        "contract-schema-additional-property",
                        f"Property {name!r} is not allowed",
                        path=f"{path}.{name}",
                    )
                )

    if isinstance(instance, list) and "items" in schema:
        for index, item in enumerate(instance):
            issues.extend(_validate(item, schema["items"], strict=strict, path=f"{path}[{index}]"))

    if isinstance(instance, str):
        min_length = schema.get("minLength")
        max_length = schema.get("maxLength")
        if min_length is not None and len(instance) < min_length:
            issues.append(make_diagnostic("contract-schema-min-length", "String is shorter than minLength", path=path))
        if max_length is not None and len(instance) > max_length:
            issues.append(make_diagnostic("contract-schema-max-length", "String is longer than maxLength", path=path))
        pattern = schema.get("pattern")
        if pattern is not None and re.search(pattern, instance) is None:
            issues.append(
                make_diagnostic(
                    "contract-schema-pattern",
                    f"String does not match pattern {pattern!r}",
                    path=path,
                )
            )

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if minimum is not None and instance < minimum:
            issues.append(make_diagnostic("contract-schema-minimum", "Number is below minimum", path=path))
        if maximum is not None and instance > maximum:
            issues.append(make_diagnostic("contract-schema-maximum", "Number is above maximum", path=path))

    return issues


def validate_document(
    instance: Any,
    schema: dict[str, Any],
    *,
    strict: bool = True,
) -> list[dict[str, Any]]:
    """Validate a document.  Call :func:`check_schema` once when loading schemas."""

    return _validate(instance, schema, strict=strict, path="$")
