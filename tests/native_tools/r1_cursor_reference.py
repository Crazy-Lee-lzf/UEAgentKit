"""Reference implementation of the R1.4 opaque pagination cursor.

This is the offline parity target for ``NativeCursorCodec.cpp`` and
``FNativeNodeRegistry::ApplyPagination``.  It intentionally mirrors the C++
token format, canonical JSON, HMAC truncation, bounds and fail-closed checks so
the Python tests can validate the contract without launching Unreal Engine.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any

CURSOR_VERSION = 1
MAX_CURSOR_LENGTH = 4096
MAX_PAYLOAD_BYTES = 2048
MAX_PAGE_SIZE = 256
CURSOR_TTL_SECONDS = 900

PAYLOAD_KEYS = ("v", "k", "q", "s", "e", "o", "n", "p", "iat", "exp")


class CursorError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    if not text or "=" in text:
        raise CursorError("native-schema-invalid-arguments", "cursor encoding is invalid")
    padding = "=" * (-len(text) % 4)
    try:
        decoded = base64.urlsafe_b64decode(text + padding)
    except Exception as exc:  # noqa: BLE001 - mirrored as a contract error
        raise CursorError("native-schema-invalid-arguments", "cursor encoding is invalid") from exc
    # Reject non-canonical trailing padding bits (token malleability).
    if _b64url_encode(decoded) != text:
        raise CursorError("native-schema-invalid-arguments", "cursor encoding is invalid")
    return decoded


def canonical_payload(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def encode(payload: dict[str, Any], key: bytes) -> str:
    if len(key) < 32:
        raise CursorError("native-schema-invalid-arguments", "cursor key is too short")
    if payload.get("v") != CURSOR_VERSION:
        raise CursorError("native-schema-invalid-arguments", "cursor payload is invalid")
    if set(payload) != set(PAYLOAD_KEYS):
        raise CursorError("native-schema-invalid-arguments", "cursor payload has unknown fields")
    text = canonical_payload(payload)
    if not 0 < len(text) <= MAX_PAYLOAD_BYTES:
        raise CursorError("native-schema-invalid-arguments", "cursor payload is too large")
    payload_b64 = _b64url_encode(text)
    mac = hmac.new(key, b"v1." + payload_b64.encode("ascii"), hashlib.sha256).digest()[:16]
    token = f"v1.{payload_b64}.{_b64url_encode(mac)}"
    if len(token) > MAX_CURSOR_LENGTH:
        raise CursorError("native-schema-invalid-arguments", "cursor is too large")
    return token


def decode(token: str, key: bytes, now: int) -> dict[str, Any]:
    if not token or len(token) > MAX_CURSOR_LENGTH or len(key) < 32:
        raise CursorError("native-schema-invalid-arguments", "cursor is invalid")
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != "v1" or not parts[1] or not parts[2]:
        raise CursorError("native-schema-invalid-arguments", "cursor is malformed")
    payload_bytes = _b64url_decode(parts[1])
    mac_raw = _b64url_decode(parts[2])
    if len(mac_raw) != 16:
        raise CursorError("native-schema-invalid-arguments", "cursor is malformed")
    expected = hmac.new(key, b"v1." + parts[1].encode("ascii"), hashlib.sha256).digest()[:16]
    if not hmac.compare_digest(expected, mac_raw):
        raise CursorError("native-schema-invalid-arguments", "cursor authentication failed")
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise CursorError("native-schema-invalid-arguments", "cursor payload is invalid") from exc
    if not isinstance(payload, dict) or set(payload) != set(PAYLOAD_KEYS):
        raise CursorError("native-schema-invalid-arguments", "cursor payload has unknown fields")
    if payload["v"] != CURSOR_VERSION:
        raise CursorError("native-schema-invalid-arguments", "cursor payload is invalid")
    if payload["k"] not in ("catalog", "context"):
        raise CursorError("native-schema-invalid-arguments", "cursor payload is invalid")
    for field in ("q", "s", "e"):
        if not isinstance(payload[field], str) or not payload[field] or len(payload[field]) > 128:
            raise CursorError("native-schema-invalid-arguments", "cursor payload is invalid")
    for field in ("o", "n", "p", "iat", "exp"):
        if not isinstance(payload[field], int) or isinstance(payload[field], bool):
            raise CursorError("native-schema-invalid-arguments", "cursor payload is invalid")
    if payload["o"] < 0 or payload["n"] < 0 or payload["n"] > 1_000_000:
        raise CursorError("native-schema-invalid-arguments", "cursor payload is invalid")
    if payload["o"] > payload["n"]:
        raise CursorError("native-schema-invalid-arguments", "cursor payload is invalid")
    if not 1 <= payload["p"] <= MAX_PAGE_SIZE:
        raise CursorError("native-schema-invalid-arguments", "cursor payload is invalid")
    if payload["iat"] <= 0 or payload["exp"] < payload["iat"] or payload["exp"] - payload["iat"] > 86400:
        raise CursorError("native-schema-invalid-arguments", "cursor payload is invalid")
    if now > payload["exp"]:
        raise CursorError("native-schema-invalid-arguments", "cursor is expired")
    return payload


@dataclass
class Page:
    items: list[Any]
    offset: int
    page_size: int
    total: int
    has_more: bool
    exhausted: bool
    next_cursor: str | None
    payload: dict[str, Any]


def paginate(
    items: list[Any],
    *,
    key: bytes,
    now: int,
    kind: str,
    query_hash: str,
    set_fingerprint: str,
    epoch: str,
    page_size: int = 0,
    cursor: str = "",
) -> Page:
    if page_size and not 1 <= page_size <= MAX_PAGE_SIZE:
        raise CursorError("native-schema-invalid-arguments", "pageSize is out of range")
    size = page_size or 64
    offset = 0
    issued_at = now
    expires_at = now + CURSOR_TTL_SECONDS
    if cursor:
        payload = decode(cursor, key, now)
        if payload["k"] != kind or payload["q"] != query_hash:
            raise CursorError("native-schema-invalid-arguments", "The cursor does not match this query.")
        if payload["s"] != set_fingerprint or payload["e"] != epoch or payload["n"] != len(items):
            raise CursorError("native-gate-revision-conflict", "The cursor is stale for the current action set.")
        if page_size and payload["p"] != page_size:
            raise CursorError("native-schema-invalid-arguments", "pageSize does not match the cursor.")
        if payload["o"] > len(items):
            raise CursorError("native-schema-invalid-arguments", "The cursor offset is out of range.")
        size = payload["p"]
        offset = payload["o"]
        issued_at = payload["iat"]
        expires_at = payload["exp"]

    total = len(items)
    returned = max(0, min(size, total - offset))
    next_offset = offset + returned
    has_more = next_offset < total
    next_cursor = None
    if has_more:
        next_payload = {
            "v": CURSOR_VERSION,
            "k": kind,
            "q": query_hash,
            "s": set_fingerprint,
            "e": epoch,
            "o": next_offset,
            "n": total,
            "p": size,
            "iat": issued_at,
            "exp": expires_at,
        }
        next_cursor = encode(next_payload, key)
    return Page(
        items=items[offset : offset + returned],
        offset=offset,
        page_size=size,
        total=total,
        has_more=has_more,
        exhausted=not has_more,
        next_cursor=next_cursor,
        payload={
            "v": CURSOR_VERSION,
            "k": kind,
            "q": query_hash,
            "s": set_fingerprint,
            "e": epoch,
            "o": offset,
            "n": total,
            "p": size,
            "iat": issued_at,
            "exp": expires_at,
        },
    )


def collect(items: list[Any], *, key: bytes, now: int, kind: str, query_hash: str, set_fingerprint: str, epoch: str, page_size: int) -> list[Any]:
    page = paginate(
        items,
        key=key,
        now=now,
        kind=kind,
        query_hash=query_hash,
        set_fingerprint=set_fingerprint,
        epoch=epoch,
        page_size=page_size,
    )
    collected = list(page.items)
    while page.next_cursor:
        page = paginate(
            items,
            key=key,
            now=now,
            kind=kind,
            query_hash=query_hash,
            set_fingerprint=set_fingerprint,
            epoch=epoch,
            page_size=page_size,
            cursor=page.next_cursor,
        )
        collected.extend(page.items)
    return collected
