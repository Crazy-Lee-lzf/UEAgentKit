"""Minimal read-only adapter example for a verified host caller.

This module is deliberately not imported by production services. It shows the
shape a future integration would use:

* the host injects a ``VerifiedCallerContext``;
* the adapter accepts only confinement-safe pack directory names (untrusted);
* the adapter can require a manifest integrity receipt;
* nothing here connects to DSH, MCP, or Project Memory.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Sequence

from .claims import VerifiedCallerContext
from .errors import ErrorCode, MemoryMountError
from .limits import MountLimits
from .service import MemoryMountRegistry, MountInfo, QueryResult


_PACK_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$")


def _fail(code: ErrorCode, message: str, **details: Any) -> None:
    raise MemoryMountError(code, message, details=details)


class ReadOnlyMountAdapter:
    """Verified-caller-only, root-confined, read-only pack adapter."""

    def __init__(
        self,
        *,
        caller: VerifiedCallerContext,
        pack_root: str | Path,
        cache_dir: str | Path,
        limits: MountLimits = MountLimits(),
        require_integrity_receipt: bool = True,
        clock: Any = None,
        identity_clock: Any = None,
    ) -> None:
        if not isinstance(caller, VerifiedCallerContext):
            _fail(
                ErrorCode.IDENTITY_REQUIRED,
                "ReadOnlyMountAdapter requires a VerifiedCallerContext.",
            )
        root = Path(pack_root).expanduser()
        try:
            if root.is_symlink():
                _fail(ErrorCode.PACK_PATH_INVALID, "Pack root must not be a symlink.")
            resolved = root.resolve(strict=True)
        except FileNotFoundError:
            _fail(ErrorCode.PACK_NOT_FOUND, "Pack root does not exist.")
        except OSError as exc:
            _fail(
                ErrorCode.PACK_PATH_INVALID,
                "Pack root could not be resolved.",
                reason=exc.__class__.__name__,
            )
        if not resolved.is_dir():
            _fail(ErrorCode.PACK_PATH_INVALID, "Pack root must be a directory.")
        self.pack_root = resolved
        self.require_integrity_receipt = bool(require_integrity_receipt)
        self._registry = MemoryMountRegistry.from_verified(
            caller=caller,
            cache_dir=cache_dir,
            limits=limits,
            clock=clock,
            identity_clock=identity_clock,
        )

    @property
    def trust_mode(self) -> str:
        return "verified-claims"

    @property
    def is_verified(self) -> bool:
        return True

    @property
    def project_key(self) -> str:
        return self._registry.project_key

    def mount(self, pack_name: str) -> MountInfo:
        if not isinstance(pack_name, str) or not _PACK_NAME_PATTERN.fullmatch(pack_name):
            _fail(
                ErrorCode.PACK_PATH_INVALID,
                "packName must be a single confined directory name.",
            )
        candidate = self.pack_root / pack_name
        try:
            if candidate.is_symlink():
                _fail(ErrorCode.PACK_PATH_INVALID, "Pack directory must not be a symlink.")
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError:
            _fail(ErrorCode.PACK_NOT_FOUND, "Pack directory does not exist.")
        except OSError as exc:
            _fail(
                ErrorCode.PACK_PATH_INVALID,
                "Pack directory could not be resolved.",
                reason=exc.__class__.__name__,
            )
        if resolved.parent != self.pack_root:
            _fail(ErrorCode.PACK_PATH_INVALID, "Pack directory escapes the configured root.")
        if not resolved.is_dir():
            _fail(ErrorCode.PACK_PATH_INVALID, "Pack path must be a directory.")
        info = self._registry.mount(resolved)
        if self.require_integrity_receipt and not info.integrity.get("receiptVerified", False):
            self._registry.unmount(info.pack_id)
            _fail(
                ErrorCode.INTEGRITY_RECEIPT_REQUIRED,
                "This adapter requires a verified manifest integrity receipt.",
                packId=info.pack_id,
            )
        return info

    def list_mounts(self) -> tuple[MountInfo, ...]:
        return self._registry.list_mounts()

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
        return self._registry.query(
            query,
            record_types=record_types,
            scope_type=scope_type,
            scope_key=scope_key,
            limit=limit,
            include_conflicts=include_conflicts,
            body_preview_chars=body_preview_chars,
            timeout_ms=timeout_ms,
        )

    def close(self) -> None:
        self._registry.close()

    def __enter__(self) -> ReadOnlyMountAdapter:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()
