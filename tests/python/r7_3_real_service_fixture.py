"""Real-service fixture for the R7.3 compatibility harness.

Uses the actual UEAgentKit IndexQueryService, TaskContextService and
ProjectMemoryService on disposable synthetic SQLite databases. No production
database, MCP server, DSH token, network, or UE runtime is used.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from test_indexer_queries import make_asset, write_export
from test_memory_injection import _BLUEPRINT_PATHS, _make_index
from test_task_context import ASSET_A, ASSET_B, PROJECT
from ue_agent_kit.agent_api import IndexQueryService
from ue_agent_kit.database import open_database
from ue_agent_kit.indexer import build_index
from ue_agent_kit.memory_service import ProjectMemoryService
from ue_agent_kit.memory_tree import KnowledgeNodeDraft
from ue_agent_kit.project_memory import MemoryRecordDraft, MemorySourceKind
from ue_agent_kit.task_context import TaskContextService


@dataclass(frozen=True)
class RealHost:
    project_key: str
    index_path: Path
    memory_path: Path
    index_service: IndexQueryService
    memory_service: ProjectMemoryService
    task_service: TaskContextService
    task_context: dict[str, Any]
    memory_snapshot: dict[str, Any]


def build_real_host(
    root: Path,
    *,
    project_key: str = PROJECT,
    query: str = "BP_TestActor",
    use_export: bool = True,
    create_dirs: bool = True,
) -> RealHost:
    root = root.expanduser().resolve()
    if create_dirs:
        root.mkdir(parents=True, exist_ok=True)
    index_path = root / "index.sqlite3"
    memory_path = root / "memory.sqlite3"
    export_root = root / "export"
    # The Build/R73 harness is intentionally disposable. Remove only this
    # fixture's known database files so reruns do not skip unchanged assets or
    # collide with an existing project root node.
    for path in (index_path, memory_path):
        for suffix in ("", "-wal", "-shm", "-journal"):
            Path(str(path) + suffix).unlink(missing_ok=True)

    if use_export:
        revision = hashlib.sha256(b"r73-synthetic-package").hexdigest()
        write_export(
            export_root,
            [
                make_asset(ASSET_A, profile="logic", revision=revision, rich=True),
                make_asset(ASSET_B, profile="logic", revision=revision, rich=False),
            ],
        )
        with open_database(index_path) as connection:
            result = build_index(connection, export_root, index_path)
        if (result.added, result.failed) != (2, 0):
            raise RuntimeError(f"Unexpected synthetic index result: {result}")
    else:
        _make_index(index_path, project_key, _BLUEPRINT_PATHS)

    memory_service = ProjectMemoryService(database_path=memory_path, project_key=project_key)
    memory_service.create_node(
        KnowledgeNodeDraft(
            project_key=project_key,
            path="/project",
            node_type="project",
            title=project_key,
            summary="R7.3 disposable synthetic host.",
        )
    )
    memory_service.add_record(
        MemoryRecordDraft(
            project_key=project_key,
            record_type="projectRule",
            subject_key="rule.local",
            title="本地项目规则",
            body="本地项目规则：挂载知识不得覆盖本地权威。",
            source_kind=MemorySourceKind.USER_CONFIRMED,
        )
    )
    memory_service.build_context(index_database=index_path)

    index_service = IndexQueryService(index_path)
    task_service = TaskContextService(
        index_service=index_service,
        memory_service=memory_service,
    )
    task_context = task_service.get_task_context(
        query=query,
        include_live_context=False,
    )
    if not task_context.get("ok"):
        raise RuntimeError("Real TaskContextService did not return ok")
    index_snapshot_id = str(task_context.get("project", {}).get("index", {}).get("snapshotId", ""))
    memory_snapshot = memory_service.get_injection_context(
        query=query,
        index_snapshot_id=index_snapshot_id,
    )
    if not memory_snapshot.get("available"):
        raise RuntimeError(
            "Real ProjectMemoryService.get_injection_context is unavailable: "
            f"{memory_snapshot.get('reason', '')}"
        )
    return RealHost(
        project_key=project_key,
        index_path=index_path,
        memory_path=memory_path,
        index_service=index_service,
        memory_service=memory_service,
        task_service=task_service,
        task_context=task_context,
        memory_snapshot=memory_snapshot,
    )
