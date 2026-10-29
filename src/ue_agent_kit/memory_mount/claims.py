"""Authenticated caller-context injection for the R7 read-only PoC.

This module is a **wiring contract**, not a production identity provider. It
defines the boundary between:

* untrusted input (CLI labels, pack names, query text), and
* a ``VerifiedCallerContext`` that an embedding host creates only after its own
  authentication/authorization has succeeded.

The included ``TestClaimIssuer`` / ``HmacClaimVerifier`` pair is a local,
test-only HMAC example. It is deliberately not connected to DSH tokens,
the Session whitelist, MCP authentication, or any real credential store.
"""

from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from .errors import ErrorCode, MemoryMountError
from .jsonutil import canonical_json_bytes, strict_json_loads


_VERIFIED_TOKEN = object()
_MAX_CLOCK_SKEW_SECONDS = 60.0
_ENVELOPE_KEYS = {"payload", "signature"}
_PAYLOAD_KEYS = {
    "audience",
    "claimId",
    "exp",
    "iat",
    "issuer",
    "principal",
    "projectKey",
    "sessionId",
}


def _fail(code: ErrorCode, message: str, **details: Any) -> None:
    raise MemoryMountError(code, message, details=details)


def _text(value: Any, field_name: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(ErrorCode.IDENTITY_INVALID, f"{field_name} must be a non-empty string.")
    if value != value.strip():
        _fail(ErrorCode.IDENTITY_INVALID, f"{field_name} must not have outer whitespace.")
    if len(value) > maximum:
        _fail(ErrorCode.IDENTITY_INVALID, f"{field_name} is too long.", maximum=maximum)
    return value


def _epoch(value: Any, field_name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        _fail(ErrorCode.IDENTITY_INVALID, f"{field_name} must be a numeric epoch.")
    result = float(value)
    if result != result or result in (float("inf"), float("-inf")):
        _fail(ErrorCode.IDENTITY_INVALID, f"{field_name} must be finite.")
    return result


@dataclass(frozen=True)
class VerifiedCallerContext:
    """Claims produced by a trusted verifier, never by CLI strings.

    The private token makes accidental ``VerifiedCallerContext(...)`` creation
    fail. It is not a Python security boundary against adversarial code.
    """

    principal: str
    session_id: str
    project_key: str
    issuer: str
    audience: str
    issued_at_epoch: float
    expires_at_epoch: float
    claim_id: str
    _token: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._token is not _VERIFIED_TOKEN:
            raise TypeError(
                "VerifiedCallerContext must be created by a ClaimVerifier, "
                "not constructed directly."
            )
        if self.expires_at_epoch <= self.issued_at_epoch:
            _fail(ErrorCode.IDENTITY_INVALID, "Claim expiry must be after issue time.")

    @property
    def is_verified(self) -> bool:
        return True

    @property
    def trust_mode(self) -> str:
        return "verified-claims"

    def assert_valid(
        self,
        *,
        now: float,
        audience: str = "",
        project_key: str | None = None,
    ) -> None:
        if audience and self.audience != audience:
            _fail(
                ErrorCode.IDENTITY_AUDIENCE_MISMATCH,
                "Verified claims are for a different audience.",
            )
        if project_key is not None and self.project_key != project_key:
            _fail(
                ErrorCode.IDENTITY_PROJECT_MISMATCH,
                "Verified claims are for a different project.",
            )
        if now > self.expires_at_epoch:
            _fail(ErrorCode.IDENTITY_EXPIRED, "Verified claims have expired.")
        if self.issued_at_epoch > now + _MAX_CLOCK_SKEW_SECONDS:
            _fail(
                ErrorCode.IDENTITY_INVALID,
                "Verified claims were issued too far in the future.",
            )


@dataclass(frozen=True)
class DemoCallerContext:
    """Explicitly untrusted demo identity.

    ``ReadOnlyMountAdapter`` rejects this type. Base ``MemoryMountRegistry``
    accepts it only through ``for_demo`` so a demo cannot masquerade as verified.
    """

    principal: str
    project_key: str
    session_id: str = "demo-session"

    @property
    def is_verified(self) -> bool:
        return False

    @property
    def trust_mode(self) -> str:
        return "untrusted-demo"


class ClaimVerifier(Protocol):
    def verify(self, envelope: str) -> VerifiedCallerContext: ...


class HmacClaimVerifier:
    """Local HMAC verifier used to exercise the injection boundary.

    This is not a production token format. A real host must replace it with its
    own authenticated session/claim source.
    """

    def __init__(
        self,
        *,
        secret: bytes,
        issuer: str,
        audience: str,
        clock: Callable[[], float] | None = None,
        leeway_seconds: float = 30.0,
        max_ttl_seconds: float = 3_600.0,
    ) -> None:
        if not isinstance(secret, bytes) or len(secret) < 16:
            raise ValueError("secret must be at least 16 bytes for the test verifier.")
        if not issuer or not audience:
            raise ValueError("issuer and audience are required.")
        if leeway_seconds < 0 or max_ttl_seconds <= 0:
            raise ValueError("invalid verifier time bounds.")
        self._secret = secret
        self._issuer = issuer
        self._audience = audience
        self._clock = clock or time.time
        self._leeway = float(leeway_seconds)
        self._max_ttl = float(max_ttl_seconds)

    @property
    def audience(self) -> str:
        return self._audience

    def verify(self, envelope: str) -> VerifiedCallerContext:
        if not isinstance(envelope, str) or not envelope:
            _fail(ErrorCode.IDENTITY_INVALID, "Claim envelope must be a non-empty string.")
        try:
            data = strict_json_loads(envelope)
        except (ValueError, TypeError) as exc:
            _fail(ErrorCode.IDENTITY_INVALID, "Claim envelope is not strict JSON.", reason=str(exc))
        if not isinstance(data, dict) or set(data) != _ENVELOPE_KEYS:
            _fail(ErrorCode.IDENTITY_INVALID, "Claim envelope shape is invalid.")
        payload = data.get("payload")
        signature = data.get("signature")
        if not isinstance(payload, dict) or set(payload) != _PAYLOAD_KEYS:
            _fail(ErrorCode.IDENTITY_INVALID, "Claim payload shape is invalid.")
        if not isinstance(signature, str) or len(signature) != 64:
            _fail(ErrorCode.IDENTITY_INVALID, "Claim signature must be 64 hex characters.")
        expected = hmac.new(
            self._secret,
            canonical_json_bytes(payload),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            _fail(ErrorCode.IDENTITY_SIGNATURE_INVALID, "Claim signature is invalid.")

        issuer = _text(payload["issuer"], "issuer", maximum=128)
        audience = _text(payload["audience"], "audience", maximum=128)
        if issuer != self._issuer:
            _fail(ErrorCode.IDENTITY_ISSUER_MISMATCH, "Claim issuer is not trusted.")
        if audience != self._audience:
            _fail(ErrorCode.IDENTITY_AUDIENCE_MISMATCH, "Claim audience is not accepted.")
        principal = _text(payload["principal"], "principal", maximum=128)
        session_id = _text(payload["sessionId"], "sessionId", maximum=128)
        project_key = _text(payload["projectKey"], "projectKey", maximum=128)
        claim_id = _text(payload["claimId"], "claimId", maximum=128)
        issued_at = _epoch(payload["iat"], "iat")
        expires_at = _epoch(payload["exp"], "exp")
        now = float(self._clock())
        if issued_at > now + _MAX_CLOCK_SKEW_SECONDS:
            _fail(ErrorCode.IDENTITY_INVALID, "Claim issue time is in the future.")
        if expires_at <= now:
            _fail(ErrorCode.IDENTITY_EXPIRED, "Claim has expired.")
        if expires_at <= issued_at:
            _fail(ErrorCode.IDENTITY_INVALID, "Claim expiry must be after issue time.")
        if expires_at - issued_at > self._max_ttl + self._leeway:
            _fail(ErrorCode.IDENTITY_INVALID, "Claim lifetime exceeds the configured maximum.")
        return VerifiedCallerContext(
            principal=principal,
            session_id=session_id,
            project_key=project_key,
            issuer=issuer,
            audience=audience,
            issued_at_epoch=issued_at,
            expires_at_epoch=expires_at,
            claim_id=claim_id,
            _token=_VERIFIED_TOKEN,
        )


class TestClaimIssuer:
    """Test-only issuer for the HMAC verifier above.

    Never use this as a production identity source; it has no key management,
    revocation, DSH integration, or Session-whitelist semantics.
    """

    def __init__(
        self,
        *,
        secret: bytes,
        issuer: str,
        audience: str,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if not isinstance(secret, bytes) or len(secret) < 16:
            raise ValueError("secret must be at least 16 bytes.")
        self._secret = secret
        self._issuer = issuer
        self._audience = audience
        self._clock = clock or time.time

    def issue(
        self,
        *,
        principal: str,
        project_key: str,
        session_id: str,
        ttl_seconds: float = 600.0,
        now: float | None = None,
        claim_id: str | None = None,
    ) -> str:
        if ttl_seconds <= 0 or ttl_seconds > 3_600:
            raise ValueError("ttl_seconds must be in (0, 3600] for the test issuer.")
        issued_at = float(self._clock() if now is None else now)
        payload = {
            "audience": self._audience,
            "claimId": claim_id or uuid.uuid4().hex,
            "exp": issued_at + float(ttl_seconds),
            "iat": issued_at,
            "issuer": self._issuer,
            "principal": principal,
            "projectKey": project_key,
            "sessionId": session_id,
        }
        signature = hmac.new(
            self._secret,
            canonical_json_bytes(payload),
            hashlib.sha256,
        ).hexdigest()
        envelope = {"payload": payload, "signature": signature}
        return canonical_json_bytes(envelope).decode("utf-8")
