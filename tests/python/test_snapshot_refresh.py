from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.agent_api import IndexQueryService  # noqa: E402
from ue_agent_kit.agent_workflow import (  # noqa: E402
    PatchWorkflowConfig,
    PatchWorkflowService,
    ProcessResult,
    WorkflowError,
)
from ue_agent_kit.database import open_database  # noqa: E402
from ue_agent_kit.indexer import build_index  # noqa: E402
from ue_agent_kit.snapshot_lifecycle import (  # noqa: E402
    freeze_active_snapshot,
    resolve_active_snapshot,
)


PROJECT = "我的项目"
ASSET_PATH = "/Game/UEAgentKitWriteTests/Refresh/DA_RefreshTarget.DA_RefreshTarget"
PACKAGE_NAME = ASSET_PATH.split(".", 1)[0]
ASSET_CLASS = "/Script/Engine.PrimaryAssetLabel"


def sha256_revision(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8", newline="\r\n")


def canonical_payload(revision: str) -> dict[str, Any]:
    return {
        "schemaVersion": "1.1",
        "exporterVersion": "0.5.1",
        "engineVersion": "5.6.1",
        "projectName": PROJECT,
        "profile": "asset-index",
        "assetPath": ASSET_PATH,
        "packageName": PACKAGE_NAME,
        "assetName": "DA_RefreshTarget",
        "assetClass": ASSET_CLASS,
        "blueprintType": "",
        "parentClass": "",
        "generatedClass": "",
        "skeletonGeneratedClass": "",
        "status": 0,
        "revision": {
            "strategy": "package-sha256-v1",
            "available": True,
            "packageDirty": False,
            "value": revision,
        },
        "symbols": [],
        "references": [],
        "graphs": [],
    }


def write_export(root: Path, revision: str) -> None:
    canonical = root / "canonical" / "Game" / "UEAgentKitWriteTests" / "Refresh" / "DA_RefreshTarget.json"
    write_json(canonical, canonical_payload(revision))
    write_json(
        root / "manifest.json",
        {
            "schemaVersion": "1.1",
            "exporterVersion": "0.5.1",
            "engineVersion": "5.6.1",
            "projectName": PROJECT,
            "createdUtc": "2026-07-24T00:00:00.000Z",
            "profile": "asset-index",
            "assetCount": 1,
            "successCount": 1,
            "failureCount": 0,
            "readerSuccessCount": 1,
            "readerFailureCount": 0,
            "assets": [
                {
                    "assetPath": ASSET_PATH,
                    "success": True,
                    "jsonPath": str(canonical),
                    "symbols": 0,
                    "references": 0,
                    "graphs": 0,
                    "nodes": 0,
                }
            ],
        },
    )


class FakeLiveEditorService:
    def __init__(self, *, dirty: bool = False) -> None:
        self.dirty = dirty

    def status(self) -> dict[str, Any]:
        return {"state": "available"}

    def call_tool(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        if tool_name != "ue_inspect_asset_live" or params.get("assetPath") != ASSET_PATH:
            raise AssertionError("unexpected Live Editor request")
        return {
            "ok": True,
            "result": {
                "memory": {
                    "loaded": True,
                    "packageDirty": self.dirty,
                    "state": "loaded-unsaved" if self.dirty else "loaded-saved",
                }
            },
        }


class RefreshRunner:
    def __init__(self, package_file: Path, *, fail: bool = False, invalid_manifest: bool = False) -> None:
        self.package_file = package_file
        self.fail = fail
        self.invalid_manifest = invalid_manifest
        self.calls = 0

    def __call__(self, arguments: list[str], cwd: Path, timeout_seconds: int) -> ProcessResult:
        self.calls += 1
        self.assert_safe(arguments, cwd, timeout_seconds)
        script = Path(arguments[arguments.index("-File") + 1]).name
        if script != "RunAssetCatalog.ps1":
            return ProcessResult(1, "", "unexpected script")
        if self.fail:
            return ProcessResult(1, "", "injected export failure")
        values: dict[str, str] = {}
        index = arguments.index("-File") + 2
        while index < len(arguments):
            if arguments[index].startswith("-") and index + 1 < len(arguments):
                values[arguments[index]] = arguments[index + 1]
                index += 2
            else:
                index += 1
        output = Path(values["-Output"])
        write_export(output, sha256_revision(self.package_file))
        if self.invalid_manifest:
            manifest_path = output / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["assetCount"] = 2
            write_json(manifest_path, manifest)
        return ProcessResult(0, "", "")

    @staticmethod
    def assert_safe(arguments: list[str], cwd: Path, timeout_seconds: int) -> None:
        if arguments[0] != "powershell.exe" or cwd.name != "tool" or timeout_seconds != 1800:
            raise AssertionError("unsafe refresh process invocation")


class SnapshotRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ueak_refresh_")
        self.root = Path(self.temporary.name)
        self.tool_root = self.root / "tool"
        self.engine_root = self.root / "engine"
        self.project_path = self.root / "project" / f"{PROJECT}.uproject"
        self.policy_path = self.tool_root / "config" / "policy.json"
        self.revision_export = self.tool_root / "Output" / "Revision"
        self.work_root = self.tool_root / "Output" / "McpWorkflow"
        self.backup_root = self.tool_root / "Backups" / "McpWorkflow"
        self.database = self.tool_root / ".data" / "ue_agent_kit.sqlite3"
        for script in ("RunPatch.ps1", "RunRollback.ps1", "RunAssetCatalog.ps1"):
            script_path = self.tool_root / "scripts" / script
            script_path.parent.mkdir(parents=True, exist_ok=True)
            script_path.write_text("# fixture\n", encoding="utf-8")
        editor = self.engine_root / "Engine" / "Binaries" / "Win64" / "UnrealEditor-Cmd.exe"
        editor.parent.mkdir(parents=True, exist_ok=True)
        editor.write_bytes(b"fixture")
        self.project_path.parent.mkdir(parents=True, exist_ok=True)
        self.project_path.write_text("{}", encoding="utf-8")
        self.package_file = self.project_path.parent / "Content" / "UEAgentKitWriteTests" / "Refresh" / "DA_RefreshTarget.uasset"
        self.package_file.parent.mkdir(parents=True, exist_ok=True)
        self.package_file.write_bytes(b"package-before")
        self.before_revision = sha256_revision(self.package_file)
        write_export(self.revision_export, self.before_revision)
        (self.revision_export / "unchanged.txt").write_bytes(b"external-before")
        write_json(
            self.policy_path,
            {
                "schemaVersion": "1.0",
                "validationEnabled": True,
                "commitEnabled": True,
                "allowedProjectNames": [PROJECT],
                "allowedAssetRoots": ["/Game/UEAgentKitWriteTests"],
                "allowedOperations": ["setAssetProperty"],
                "allowedAssetClasses": [ASSET_CLASS],
                "allowedAssetProperties": [f"{ASSET_CLASS}#bIncludeRedirectors"],
                "allowedReferenceRoots": [],
                "allowedReferenceClasses": [],
                "allowedMaterialParameters": [],
                "allowedDataTableFields": [],
                "requireRevision": True,
                "rejectDirtyPackages": True,
                "maxAssetsPerPatch": 1,
                "maxOperationsPerAsset": 1,
                "maxValueBytes": 65536,
            },
        )
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with open_database(self.database) as connection:
            result = build_index(connection, self.revision_export, self.database)
        self.assertEqual((result.added, result.failed), (1, 0))
        self.active = resolve_active_snapshot(self.database, self.revision_export, self.work_root, PROJECT)
        self.frozen = freeze_active_snapshot(self.active)

    def tearDown(self) -> None:
        self.frozen.cleanup()
        self.temporary.cleanup()

    def make_service(
        self,
        *,
        runner: RefreshRunner | None = None,
        live: FakeLiveEditorService | None = None,
    ) -> PatchWorkflowService:
        index_service = IndexQueryService(self.frozen.database)
        config = PatchWorkflowConfig(
            tool_root=self.tool_root,
            engine_root=self.engine_root,
            project_path=self.project_path,
            policy_path=self.policy_path,
            revision_export=self.frozen.revision_export,
            work_root=self.work_root,
            backup_root=self.backup_root,
            commit_enabled=True,
            active_snapshot=self.active,
        )
        return PatchWorkflowService(
            index_service,
            config,
            process_runner=runner or RefreshRunner(self.package_file),
            live_editor_service=live or FakeLiveEditorService(),
        )

    def test_preview_does_not_switch_and_apply_is_visible_only_to_new_snapshot(self) -> None:
        service = self.make_service()
        self.package_file.write_bytes(b"package-after")
        after_revision = sha256_revision(self.package_file)

        preview = service.refresh_asset_index(ASSET_PATH, mode="Preview")
        self.assertFalse(preview["applied"])
        self.assertFalse(self.active.pointer_path.exists())
        self.assertEqual(preview["targetRevision"], after_revision)
        self.assertEqual(service.index_service.get_revision_record(ASSET_PATH)["revision_value"], self.before_revision)

        applied = service.refresh_asset_index(ASSET_PATH, mode="Apply")
        self.assertTrue(applied["applied"])
        self.assertTrue(applied["restartRequired"])
        generation_root = self.work_root / "snapshots" / applied["newGeneration"]["generationId"]
        self.revision_export.joinpath("unchanged.txt").write_bytes(b"external-after")
        self.assertEqual(
            (generation_root / "revision-export" / "unchanged.txt").read_bytes(),
            b"external-before",
        )
        self.assertTrue(applied["currentSessionUsesPreviousSnapshot"])
        self.assertTrue(self.active.pointer_path.is_file())
        self.assertEqual(service.index_service.get_revision_record(ASSET_PATH)["revision_value"], self.before_revision)

        resolved = resolve_active_snapshot(self.database, self.revision_export, self.work_root, PROJECT)
        new_index = IndexQueryService(resolved.database)
        self.assertEqual(new_index.get_revision_record(ASSET_PATH)["revision_value"], after_revision)
        with self.assertRaisesRegex(WorkflowError, "must be restarted"):
            service.refresh_asset_index(ASSET_PATH, mode="Preview")

    def test_dirty_live_asset_is_rejected_before_export(self) -> None:
        runner = RefreshRunner(self.package_file)
        service = self.make_service(runner=runner, live=FakeLiveEditorService(dirty=True))
        with self.assertRaises(WorkflowError) as context:
            service.refresh_asset_index(ASSET_PATH, mode="Apply")
        self.assertEqual(context.exception.code, "live-editor-asset-dirty")
        self.assertEqual(runner.calls, 0)
        self.assertFalse(self.active.pointer_path.exists())

    def test_export_failure_keeps_previous_active_pair(self) -> None:
        service = self.make_service(runner=RefreshRunner(self.package_file, fail=True))
        with self.assertRaises(WorkflowError):
            service.refresh_asset_index(ASSET_PATH, mode="Apply")
        self.assertFalse(self.active.pointer_path.exists())
        self.assertEqual(IndexQueryService(self.database).get_revision_record(ASSET_PATH)["revision_value"], self.before_revision)
        snapshots = self.work_root / "snapshots"
        self.assertFalse(snapshots.exists() and any(snapshots.iterdir()))

    def test_invalid_single_asset_manifest_is_rejected_without_switch(self) -> None:
        service = self.make_service(runner=RefreshRunner(self.package_file, invalid_manifest=True))
        with self.assertRaises(WorkflowError) as context:
            service.refresh_asset_index(ASSET_PATH, mode="Apply")
        self.assertEqual(context.exception.code, "snapshot-refresh-export-invalid")
        self.assertFalse(self.active.pointer_path.exists())
        self.assertEqual(IndexQueryService(self.database).get_revision_record(ASSET_PATH)["revision_value"], self.before_revision)


if __name__ == "__main__":
    unittest.main()
