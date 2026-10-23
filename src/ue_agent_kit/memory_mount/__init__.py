"""Isolated read-only PoC for R7 Knowledge Packs.

This package is deliberately not imported by `ue_agent_kit.memory_service`,
`ue_agent_kit.mcp_server`, `ue_agent_kit.cli`, or any default service. It opens
only pack files and its own derived index cache.
"""

from __future__ import annotations

from .adapter import ReadOnlyMountAdapter
from .claims import (
    ClaimVerifier,
    DemoCallerContext,
    HmacClaimVerifier,
    TestClaimIssuer,
    VerifiedCallerContext,
)
from .errors import ErrorCode, MemoryMountError
from .integrity import (
    AUTHOR_AUTHENTICITY_NOT_ESTABLISHED,
    IntegrityReport,
    build_integrity_receipt,
)
from .limits import MountLimits
from .manifest import PackManifest
from .service import MemoryMountRegistry, MountInfo, QueryResult

__all__ = [
    "AUTHOR_AUTHENTICITY_NOT_ESTABLISHED",
    "ClaimVerifier",
    "DemoCallerContext",
    "ErrorCode",
    "HmacClaimVerifier",
    "IntegrityReport",
    "MemoryMountError",
    "MemoryMountRegistry",
    "MountInfo",
    "MountLimits",
    "PackManifest",
    "QueryResult",
    "ReadOnlyMountAdapter",
    "TestClaimIssuer",
    "VerifiedCallerContext",
    "build_integrity_receipt",
]
