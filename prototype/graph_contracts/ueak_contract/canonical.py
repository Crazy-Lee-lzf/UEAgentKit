"""Canonical JSON and domain-separated hashing for the G0.1 contract candidate.

The canonical form is intentionally identical to the 0.8.0 Patch implementation
(``ue_agent_kit.patches._canonical_json``): UTF-8, sorted keys, no insignificant
whitespace, ``ensure_ascii=False`` and ``.17g`` float formatting.  The offline
tests cross-check that parity so a future contract implementation does not
silently diverge from existing receipts.

No third-party dependencies.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

HASH_PREFIX = "sha256:"
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
FINGERPRINT_RE = re.compile(r"^sha256:[0-9a-f]{16}$")

# Domain separation labels.  Changing any label is a breaking contract change.
DOMAIN_SEMANTIC = "ueak.typedgraph.semantic/1"
DOMAIN_NODE = "ueak.typedgraph.node/1"
DOMAIN_LAYOUT = "ueak.typedgraph.layout/1"
DOMAIN_SIZE = "ueak.typedgraph.size/1"
DOMAIN_COMMENT = "ueak.typedgraph.comment/1"
DOMAIN_EDGE = "ueak.typedgraph.edge/1"
DOMAIN_ACTION = "ueak.node-action/1"
DOMAIN_DESCRIPTOR = "ueak.node-descriptor/1"
DOMAIN_OPERATION = "ueak.operation-descriptor/1"
DOMAIN_PAYLOAD = "ueak.graphpatch.payload/1"
DOMAIN_RECEIPT = "ueak.graphpatch.receipt/1"
DOMAIN_LAYOUT_PLAN = "ueak.layoutplan/1"
DOMAIN_ARTIFACT = "ueak.artifact/1"
DOMAIN_FIXTURE = "ueak.fixture/1"


class CanonicalizationError(ValueError):
    """Raised for values that cannot be represented by the canonical subset."""


def canonical_json(value: Any) -> str:
    """Return the canonical JSON text for a JSON-compatible value."""

    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalizationError("Non-finite JSON number")
        return "0" if value == 0.0 else format(value, ".17g")
    if isinstance(value, list):
        return "[" + ",".join(canonical_json(item) for item in value) + "]"
    if isinstance(value, dict):
        for key in value:
            if not isinstance(key, str):
                raise CanonicalizationError("JSON object keys must be strings")
        keys = sorted(value, key=lambda item: item.encode("utf-8"))
        return "{" + ",".join(
            json.dumps(key, ensure_ascii=False, separators=(",", ":")) + ":" + canonical_json(value[key])
            for key in keys
        ) + "}"
    raise CanonicalizationError(f"Unsupported JSON value: {type(value).__name__}")


def canonical_bytes(value: Any, domain: str) -> bytes:
    """Return ``domain + LF + canonical_json`` encoded as UTF-8.

    The domain line prevents cross-type hash confusion (for example a layout
    hash accidentally accepted as a semantic hash).
    """

    if not domain or "\n" in domain:
        raise CanonicalizationError("Hash domain must be a non-empty single line")
    return domain.encode("utf-8") + b"\n" + canonical_json(value).encode("utf-8")


def digest_hex(value: Any, domain: str) -> str:
    return hashlib.sha256(canonical_bytes(value, domain)).hexdigest()


def domain_hash(value: Any, domain: str) -> str:
    return HASH_PREFIX + digest_hex(value, domain)


def fingerprint(value: Any, domain: str, length: int = 16) -> str:
    if length < 8 or length > 64:
        raise ValueError("fingerprint length must be between 8 and 64")
    return HASH_PREFIX + digest_hex(value, domain)[:length]


def is_sha256(value: Any) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None


def is_fingerprint(value: Any) -> bool:
    return isinstance(value, str) and FINGERPRINT_RE.fullmatch(value) is not None


def short_digest(value: Any, domain: str, length: int = 16) -> str:
    """Return bare hex (used for deterministic ids), not a ``sha256:`` string."""

    return digest_hex(value, domain)[:length]
