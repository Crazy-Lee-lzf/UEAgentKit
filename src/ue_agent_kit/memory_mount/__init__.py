"""Isolated read-only PoC for R7 Knowledge Packs.

This package is deliberately not imported by `ue_agent_kit.memory_service`,
`ue_agent_kit.mcp_server`, `ue_agent_kit.cli`, or any default service. It opens
only pack files and its own derived index cache.
"""

from __future__ import annotations

from .errors import ErrorCode, MemoryMountError
from .limits import MountLimits
from .manifest import PackManifest
from .service import MemoryMountRegistry, MountInfo, QueryResult

__all__ = [
    "ErrorCode",
    "MemoryMountError",
    "MemoryMountRegistry",
    "MountInfo",
    "MountLimits",
    "PackManifest",
    "QueryResult",
]
