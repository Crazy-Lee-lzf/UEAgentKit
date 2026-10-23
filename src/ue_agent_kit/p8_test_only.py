"""Hardcoded P8 disposable graph-layout write policy.

This is not a general allowlist.  It recognizes one checkout-local disposable
host, one exact Blueprint, one exact GraphGuid, one explicit confirmation, and
one plugin build that is authorized by both the host marker and the MCP flag.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXED_PROJECT_PATH = (REPO_ROOT / "Build" / "P8AutomationHost" / "P8AutomationHost.uproject").resolve()
# Filled after the disposable host is created; a mismatch fails closed.
FIXED_PROJECT_FILE_SHA256 = "3c9ea038dd3cb184ef4b6c1f76cf4c2de8c1c08e34536a43b7618493dace1624"
FIXED_ASSET_PATH = "/Game/UEAgentKitP8/BP_LayoutP8.BP_LayoutP8"
FIXED_GRAPH_NAMESPACE = "/Game/UEAgentKitP8/"
APPLY_TOOL_NAME = "ue_apply_blueprint_graph_layout_p8_test_only"
APPLY_METHOD = "editor.applyBlueprintGraphLayoutP8TestOnly"
SAVE_METHOD = "editor.saveBlueprintGraphLayoutP8TestOnly"
APPLY_CONFIRMATION_PREFIX = "APPLY P8 TEST-ONLY "
SAVE_CONFIRMATION = "SAVE P8 TEST-ONLY BP_LayoutP8"
MAX_REQUEST_BYTES = 60 * 1024


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def is_fixed_project(project_path: Path) -> bool:
    """Return true only for the exact canonical P8 .uproject and its file hash."""
    try:
        resolved = project_path.resolve(strict=True)
    except (FileNotFoundError, OSError):
        return False
    if resolved != FIXED_PROJECT_PATH:
        return False
    try:
        return file_sha256(resolved) == FIXED_PROJECT_FILE_SHA256
    except OSError:
        return False


def confirmation_for_plan(plan_id: str) -> str:
    return APPLY_CONFIRMATION_PREFIX + plan_id
