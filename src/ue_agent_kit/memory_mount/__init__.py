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
from .context import (
    AgentContextProjection,
    AgentContextProjector,
    AgentContextRequest,
    ContextLimits,
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
from .staging import (
    HandoffLimits,
    HandoffResult,
    LocalAuthoritySnapshot,
    PackPin,
    StagedContextHandoff,
)

__all__ = [
    "AUTHOR_AUTHENTICITY_NOT_ESTABLISHED",
    "AgentContextProjection",
    "AgentContextProjector",
    "AgentContextRequest",
    "ClaimVerifier",
    "ContextLimits",
    "DemoCallerContext",
    "ErrorCode",
    "HandoffLimits",
    "HandoffResult",
    "HmacClaimVerifier",
    "IntegrityReport",
    "LocalAuthoritySnapshot",
    "MemoryMountError",
    "MemoryMountRegistry",
    "MountInfo",
    "MountLimits",
    "PackManifest",
    "PackPin",
    "QueryResult",
    "ReadOnlyMountAdapter",
    "StagedContextHandoff",
    "TestClaimIssuer",
    "VerifiedCallerContext",
    "build_integrity_receipt",
]
