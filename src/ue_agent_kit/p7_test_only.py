"""Hardcoded P7 test-only graph-layout policy.

This module is intentionally narrow.  It is not a general allowlist and it does
not accept project paths, asset paths, endpoints, or save targets from an MCP
client.  Enabling it only registers one destructive lab tool when the MCP
server's already-fixed ``--project`` is this checkout's disposable P7 host.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXED_PROJECT_PATH = (REPO_ROOT / "Build" / "P7AutomationHost" / "P7AutomationHost.uproject").resolve()
FIXED_PROJECT_FILE_SHA256 = "20f86465e2296f232be047c22f7924b24be6c62fd3eeaf13f8c52936964211c2"
FIXED_ASSET_PATH = "/Game/UEAgentKitP7/BP_LayoutP7.BP_LayoutP7"
FIXED_GRAPH_NAMESPACE = "/Game/UEAgentKitP7/"
APPLY_TOOL_NAME = "ue_apply_blueprint_graph_layout_test_only"
APPLY_METHOD = "editor.applyBlueprintGraphLayoutTestOnly"
SAVE_METHOD = "editor.saveBlueprintGraphLayoutTestOnly"
APPLY_CONFIRMATION_PREFIX = "APPLY P7 TEST-ONLY "
SAVE_CONFIRMATION = "SAVE P7 TEST-ONLY BP_LayoutP7"
MAX_REQUEST_BYTES = 60 * 1024


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def is_fixed_project(project_path: Path) -> bool:
    """Return true only for the exact canonical P7 .uproject and its file hash."""
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
