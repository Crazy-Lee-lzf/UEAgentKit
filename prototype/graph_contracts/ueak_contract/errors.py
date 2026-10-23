"""Stable error/diagnostic code registry.

The JSON file ``schemas/error-codes.json`` is the single source of truth.  Code
must not invent ad-hoc strings: implementations call :func:`raise_code` with a
registered code, and the offline test suite asserts every code emitted by the
prototype and every fixture expectation is registered.

Clause ID: EC-TABLE.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schemas"
ERROR_CODES_PATH = SCHEMA_DIR / "error-codes.json"

DOMAINS = ("graph", "graphpatch", "layout", "contract", "recovery", "verify")
SEVERITIES = ("error", "warning", "info")


@dataclass(frozen=True)
class ErrorCode:
    code: str
    domain: str
    severity: str
    retryable: bool
    owner_work_line: str
    summary: str
    contract_ref: str
    legacy_alias_of: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "domain": self.domain,
            "severity": self.severity,
            "retryable": self.retryable,
            "ownerWorkLine": self.owner_work_line,
            "summary": self.summary,
            "contractRef": self.contract_ref,
        }
        if self.legacy_alias_of:
            payload["legacyAliasOf"] = self.legacy_alias_of
        return payload


class ErrorCodeRegistry:
    def __init__(self, document: dict[str, Any]) -> None:
        if document.get("schemaVersion") != "ueak.error-codes/1.0":
            raise ValueError("Unsupported error-code registry version")
        entries = document.get("codes")
        if not isinstance(entries, list) or not entries:
            raise ValueError("Error-code registry must contain a non-empty 'codes' list")
        registry: dict[str, ErrorCode] = {}
        for raw in entries:
            code = ErrorCode(
                code=raw["code"],
                domain=raw["domain"],
                severity=raw["severity"],
                retryable=bool(raw["retryable"]),
                owner_work_line=raw["ownerWorkLine"],
                summary=raw["summary"],
                contract_ref=raw["contractRef"],
                legacy_alias_of=raw.get("legacyAliasOf", ""),
            )
            if code.code in registry:
                raise ValueError(f"Duplicate error code: {code.code}")
            if code.domain not in DOMAINS:
                raise ValueError(f"Unknown domain for {code.code}: {code.domain}")
            if code.severity not in SEVERITIES:
                raise ValueError(f"Unknown severity for {code.code}: {code.severity}")
            if not code.legacy_alias_of and not code.code.startswith(code.domain + "-"):
                raise ValueError(f"Code {code.code} does not match domain {code.domain}")
            registry[code.code] = code
        self._codes = registry

    def __contains__(self, code: object) -> bool:
        return code in self._codes

    def __len__(self) -> int:
        return len(self._codes)

    def get(self, code: str) -> ErrorCode:
        try:
            return self._codes[code]
        except KeyError as exc:
            raise KeyError(f"Unregistered error code: {code}") from exc

    def is_known(self, code: str) -> bool:
        return code in self._codes

    def codes(self) -> tuple[str, ...]:
        return tuple(sorted(self._codes))

    def by_domain(self, domain: str) -> tuple[ErrorCode, ...]:
        return tuple(self._codes[name] for name in sorted(self._codes) if self._codes[name].domain == domain)

    def canonical(self, code: str) -> str:
        """Follow a legacy alias to the canonical code."""

        entry = self.get(code)
        return entry.legacy_alias_of or entry.code


@lru_cache(maxsize=1)
def load_registry() -> ErrorCodeRegistry:
    with ERROR_CODES_PATH.open("r", encoding="utf-8") as handle:
        return ErrorCodeRegistry(json.load(handle))


class ContractError(Exception):
    """A structured contract failure carrying a stable code."""

    def __init__(self, code: str, message: str, *, path: str = "$", context: dict[str, Any] | None = None) -> None:
        if code not in load_registry():
            raise KeyError(f"Unregistered error code: {code}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.path = path
        self.context = dict(context or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "path": self.path,
            "context": self.context,
        }


def make_diagnostic(
    code: str,
    message: str,
    *,
    path: str = "$",
    severity: str | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    entry = load_registry().get(code)
    return {
        "code": code,
        "severity": severity or entry.severity,
        "message": message,
        "path": path,
        "context": dict(context or {}),
    }


def raise_code(code: str, message: str, *, path: str = "$", context: dict[str, Any] | None = None) -> None:
    raise ContractError(code, message, path=path, context=context)


def assert_registered(codes: Iterable[str]) -> None:
    registry = load_registry()
    unknown = sorted({code for code in codes if not registry.is_known(code)})
    if unknown:
        raise KeyError(f"Unregistered error codes: {', '.join(unknown)}")
