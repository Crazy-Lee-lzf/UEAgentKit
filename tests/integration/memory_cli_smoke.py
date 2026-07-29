from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
PYTHON_TESTS = TOOL_ROOT / "tests" / "python"
for path in (SRC_ROOT, PYTHON_TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from test_indexer_queries import ASSET_A, REVISION_A, make_asset, write_export  # noqa: E402
from ue_agent_kit.database import open_database  # noqa: E402
from ue_agent_kit.indexer import build_index  # noqa: E402
from ue_agent_kit.memory_service import ProjectMemoryService  # noqa: E402
from ue_agent_kit.memory_tasks import TaskOutcome, TaskOutcomeDraft  # noqa: E402
from ue_agent_kit.project_memory import (  # noqa: E402
    MemoryRecordDraft,
    MemoryRecordType,
    MemoryRevision,
    MemoryScope,
    MemoryScopeType,
    MemorySourceKind,
)


PROJECT = "测试项目"


def _run_cli(*arguments: str) -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, str(TOOL_ROOT / "scripts" / "ue-agent.py"), *arguments],
        cwd=TOOL_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"CLI failed with exit code {result.returncode}: stdout={result.stdout!r}, stderr={result.stderr!r}"
        )
    if result.stderr.strip():
        raise RuntimeError(f"CLI wrote unexpected stderr: {result.stderr!r}")
    payload = json.loads(result.stdout)
    if not isinstance(payload, dict):
        raise RuntimeError(f"CLI response is not an object: {payload!r}")
    return payload


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="ueak_memory_cli_smoke_") as temporary_root:
        root = Path(temporary_root)
        memory_path = root / "memory" / "project-memory.sqlite3"
        index_path = root / "index" / "ueak.sqlite3"
        audit_path = root / "audit" / "memory-audit.json"
        export_root = root / "export"

        service = ProjectMemoryService(database_path=memory_path, project_key=PROJECT)
        rule = service.add_record(
            MemoryRecordDraft(
                project_key=PROJECT,
                record_type=MemoryRecordType.PROJECT_RULE,
                subject_key="rule:text-format",
                title="Text format",
                body="Tracked text files use UTF-8 without BOM and CRLF.",
                source_kind=MemorySourceKind.USER_CONFIRMED,
                source_ref="integration:user-confirmed",
                scopes=(MemoryScope(MemoryScopeType.PROJECT, PROJECT),),
            )
        )
        task = service.record_task_outcome(
            TaskOutcomeDraft(
                task_key="memory-cli-smoke",
                title="Validate Memory CLI",
                conclusion="The standalone Memory CLI workflow passed.",
                outcome=TaskOutcome.SUCCEEDED,
                patch_ref="patch:memory-cli-smoke",
                backup_manifest_ref="backup-manifest:memory-cli-smoke",
                validation_evidence_ref="validation-evidence:memory-cli-smoke",
                revision_set=(MemoryRevision(ASSET_A, f"sha256:{REVISION_A}"),),
                scopes=(MemoryScope(MemoryScopeType.ASSET, ASSET_A),),
            )
        )
        write_export(
            export_root,
            [make_asset(ASSET_A, profile="logic", revision=REVISION_A, rich=True)],
        )
        with open_database(index_path) as connection:
            index_result = build_index(connection, export_root, index_path)
        if (index_result.added, index_result.failed) != (1, 0):
            raise RuntimeError(f"Unexpected index result: {index_result}")

        fixed = [
            "--memory-database",
            str(memory_path),
            "--project-key",
            PROJECT,
        ]
        status = _run_cli("memory", "status", *fixed)
        search = _run_cli(
            "memory",
            "search",
            "Memory CLI",
            *fixed,
            "--record-type",
            "taskRecord",
        )
        fetched = _run_cli("memory", "get", task.record_id, *fixed)
        validated = _run_cli(
            "memory",
            "validate",
            *fixed,
            "--index-database",
            str(index_path),
        )
        exported = _run_cli(
            "memory",
            "export",
            *fixed,
            "--output",
            str(audit_path),
        )

        if status["recordCount"] != 2 or status["projectKey"] != PROJECT:
            raise RuntimeError(f"Memory status failed: {status}")
        if search["resultCount"] != 1 or search["items"][0]["record"]["recordId"] != task.record_id:
            raise RuntimeError(f"Memory search failed: {search}")
        if fetched["record"]["recordId"] != task.record_id:
            raise RuntimeError(f"Memory get failed: {fetched}")
        if validated["staleRecordIds"]:
            raise RuntimeError(f"Memory validation failed: {validated}")
        if task.record_id not in validated["checkedRecordIds"]:
            raise RuntimeError(f"Task Revision was not checked: {validated}")
        if not exported["exported"] or exported["recordCount"] != 2:
            raise RuntimeError(f"Memory export failed: {exported}")

        audit_bytes = audit_path.read_bytes()
        if audit_bytes.startswith(b"\xef\xbb\xbf") or b"\n" in audit_bytes.replace(b"\r\n", b""):
            raise RuntimeError("Memory audit is not UTF-8 without BOM and CRLF")
        audit = json.loads(audit_bytes.decode("utf-8"))
        if audit["recordCount"] != 2 or not audit["integrity"]["allRecordDigestsVerified"]:
            raise RuntimeError(f"Memory audit contract failed: {audit}")
        serialized = json.dumps(
            {"status": status, "search": search, "fetched": fetched, "audit": audit},
            ensure_ascii=False,
        )
        if str(memory_path) in serialized or str(index_path) in serialized:
            raise RuntimeError("Memory CLI exposed a configured database path")

        report = {
            "statusPassed": True,
            "searchPassed": True,
            "getPassed": True,
            "validationPassed": True,
            "exportPassed": True,
            "recordCount": audit["recordCount"],
            "statusEventCount": audit["statusEventCount"],
            "snapshotSha256": audit["integrity"]["snapshotSha256"],
            "ruleRecordId": rule.record_id,
            "taskRecordId": task.record_id,
            "databasePathsRedacted": True,
            "auditCrLf": True,
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
