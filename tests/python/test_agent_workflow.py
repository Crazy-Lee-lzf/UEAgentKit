from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch


TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.agent_workflow import (  # noqa: E402
    PatchWorkflowConfig,
    PatchWorkflowService,
    ProcessResult,
    WorkflowError,
)
from ue_agent_kit.change_sets import ChangeSetOperationRecord, MAX_CHANGE_SET_RECEIPTS  # noqa: E402
from ue_agent_kit.editor_bridge import LiveEditorError  # noqa: E402
from ue_agent_kit.tool_registry import tool_names_for_mode  # noqa: E402


PROJECT = "我的项目"
TRANSACTION_ID = "12345678-1234-1234-1234-123456789abc"
ASSET_PATH = "/Game/UEAgentKitWriteTests/ScalarRegression/DA_ScalarPatchTarget.DA_ScalarPatchTarget"
ASSET_CLASS = "/Script/UEAgentKitEditor.UEAgentKitScalarWriteFixtureAsset"
REFERENCE_ASSET_PATH = "/Game/UEAgentKitWriteTests/References/DA_ReferenceLiveTarget.DA_ReferenceLiveTarget"
REFERENCE_ASSET_CLASS = "/Script/UEAgentKitEditor.UEAgentKitReferenceWriteFixtureAsset"
STRUCTURED_ASSET_PATH = "/Game/UEAgentKitWriteTests/Structured/DA_StructuredLiveTarget.DA_StructuredLiveTarget"
MATERIAL_ASSET_PATH = "/Game/UEAgentKitWriteTests/Materials/MI_LiveTarget.MI_LiveTarget"
MATERIAL_ASSET_CLASS = "/Script/Engine.MaterialInstanceConstant"
STRUCTURED_ASSET_CLASS = "/Script/UEAgentKitEditor.UEAgentKitStructuredWriteFixtureAsset"
DATA_TABLE_PATH = "/Game/UEAgentKitWriteTests/Tables/DT_Fixture.DT_Fixture"
DATA_TABLE_CLASS = "/Script/Engine.DataTable"
DATA_TABLE_ROW_STRUCT = "/Script/UEAgentKitEditor.UEAgentKitStructuredFixtureRecord"
STRUCTURED_STRUCT_VALUE = {
    "valueType": "Struct",
    "fields": {"Count": 42, "Label": "Live Write", "bEnabled": True},
}
STRUCTURED_DEFAULT_STRUCT_VALUE = {
    "valueType": "Struct",
    "fields": {"Count": 0, "Label": "", "bEnabled": False},
}
BEFORE_REVISION = "sha256:" + "a" * 64
AFTER_REVISION = "sha256:" + "b" * 64


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8", newline="\r\n")


class FakeIndexService:
    def check(self) -> dict[str, Any]:
        return {"ok": True, "projectKey": PROJECT}

    def get_asset(self, asset_path: str, **_: Any) -> dict[str, Any]:
        if asset_path == REFERENCE_ASSET_PATH:
            return {
                "found": True,
                "ok": True,
                "asset": {
                    "asset_path": REFERENCE_ASSET_PATH,
                    "asset_class": REFERENCE_ASSET_CLASS,
                    "revision_value": BEFORE_REVISION,
                },
            }
        if asset_path == STRUCTURED_ASSET_PATH:
            return {
                "found": True,
                "ok": True,
                "asset": {
                    "asset_path": STRUCTURED_ASSET_PATH,
                    "asset_class": STRUCTURED_ASSET_CLASS,
                    "revision_value": BEFORE_REVISION,
                },
            }
        if asset_path == MATERIAL_ASSET_PATH:
            return {
                "found": True,
                "ok": True,
                "asset": {
                    "asset_path": MATERIAL_ASSET_PATH,
                    "asset_class": MATERIAL_ASSET_CLASS,
                    "revision_value": BEFORE_REVISION,
                },
            }
        if asset_path == DATA_TABLE_PATH:
            return {
                "found": True,
                "ok": True,
                "asset": {
                    "asset_path": DATA_TABLE_PATH,
                    "asset_class": DATA_TABLE_CLASS,
                    "revision_value": BEFORE_REVISION,
                },
            }
        if asset_path != ASSET_PATH:
            return {"found": False, "ok": True}
        return {
            "found": True,
            "ok": True,
            "asset": {
                "asset_path": ASSET_PATH,
                "asset_class": ASSET_CLASS,
                "revision_value": BEFORE_REVISION,
            },
        }

    def get_revision_record(self, asset_path: str) -> dict[str, Any] | None:
        if asset_path != ASSET_PATH:
            return None
        return {
            "asset_path": ASSET_PATH,
            "package_name": ASSET_PATH.split(".", 1)[0],
            "asset_class": ASSET_CLASS,
            "revision_value": BEFORE_REVISION,
            "package_dirty": False,
        }

    def get_data_table_row_reference_impact(
        self,
        asset_path: str,
        row_name: str,
        *,
        sample_limit: int = 20,
    ) -> dict[str, Any]:
        return {
            "checked": True,
            "source": "immutable-sqlite-searchable-name",
            "assetPath": asset_path,
            "rowName": row_name,
            "targetPath": f"{asset_path}::{row_name}",
            "referenceCount": 0,
            "sampleLimit": sample_limit,
            "sampleTruncated": False,
            "referencers": [],
        }


class FakeFreshnessTracker:
    def __init__(self) -> None:
        self.state = "fresh"
        self.transitions: dict[str, dict[str, Any]] = {}

    def inspect_asset(self, asset_path: str) -> dict[str, Any]:
        disk_revision = BEFORE_REVISION if self.state == "fresh" else AFTER_REVISION
        return {
            "assetPath": asset_path,
            "state": self.state,
            "reason": "" if self.state == "fresh" else "index-disk-mismatch,revision-export-disk-mismatch",
            "indexFresh": self.state == "fresh",
            "indexStale": self.state == "stale",
            "indexRevision": BEFORE_REVISION,
            "revisionExportRevision": BEFORE_REVISION,
            "diskRevision": disk_revision,
            "comparisons": {
                "indexMatchesRevisionExport": True,
                "indexMatchesDisk": disk_revision == BEFORE_REVISION,
                "revisionExportMatchesDisk": disk_revision == BEFORE_REVISION,
            },
        }

    def project_status(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "indexFresh": self.state == "fresh",
            "indexStale": self.state == "stale",
            "sessionStaleAssets": self.session_stale_assets(),
        }

    def session_stale_assets(self) -> list[dict[str, Any]]:
        return [dict(value) for value in self.transitions.values()]

    def mark_commit(self, asset_path: str, before_revision: str, after_revision: str) -> dict[str, Any]:
        self.state = "stale"
        transition = {
            "assetPath": asset_path,
            "state": "stale",
            "reason": "commit-changed-package",
            "beforeRevision": before_revision,
            "afterRevision": after_revision,
        }
        self.transitions[asset_path] = transition
        return dict(transition)

    def mark_rollback(self, asset_path: str, restored_revision: str) -> dict[str, Any]:
        self.state = "fresh"
        self.transitions.pop(asset_path, None)
        return {
            "assetPath": asset_path,
            "state": "fresh",
            "indexFresh": True,
            "indexStale": False,
            "diskRevision": restored_revision,
            "sessionStateCleared": True,
        }


class FakeWorkflowRunner:
    def __init__(self) -> None:
        self.revision = BEFORE_REVISION
        self.calls: list[tuple[str, dict[str, str]]] = []

    @staticmethod
    def _arguments(arguments: list[str]) -> tuple[str, dict[str, str]]:
        script = Path(arguments[arguments.index("-File") + 1]).name
        values: dict[str, str] = {}
        index = arguments.index("-File") + 2
        while index < len(arguments):
            key = arguments[index]
            if key.startswith("-") and index + 1 < len(arguments):
                values[key] = arguments[index + 1]
                index += 2
            else:
                index += 1
        return script, values

    def __call__(self, arguments: list[str], cwd: Path, timeout_seconds: int) -> ProcessResult:
        self.assert_safe(arguments, cwd, timeout_seconds)
        script, values = self._arguments(arguments)
        self.calls.append((script, values))
        if script == "RunPatch.ps1":
            patch = json.loads(Path(values["-Patch"]).read_text(encoding="utf-8"))
            asset = patch["assets"][0]
            operation = asset["operations"][0]
            mode = values["-Mode"]
            if mode == "DryRun":
                report = {
                    "schemaVersion": "1.0",
                    "executorVersion": "0.5.1",
                    "mode": "DryRun",
                    "patchId": patch["patchId"],
                    "projectName": PROJECT,
                    "assetPath": ASSET_PATH,
                    "assetClass": ASSET_CLASS,
                    "operation": operation["operation"],
                    "target": operation["target"],
                    "beforeValue": "False",
                    "afterValue": "True",
                    "restoredValue": "False",
                    "beforeRevision": BEFORE_REVISION,
                    "afterRevision": BEFORE_REVISION,
                    "saved": False,
                    "rolledBack": True,
                    "rollbackValueMatch": True,
                    "diskUnchanged": True,
                    "backupPath": "",
                }
            else:
                self.revision = AFTER_REVISION
                report = {
                    "schemaVersion": "1.0",
                    "executorVersion": "0.5.1",
                    "mode": "Commit",
                    "patchId": patch["patchId"],
                    "projectName": PROJECT,
                    "assetPath": ASSET_PATH,
                    "assetClass": ASSET_CLASS,
                    "operation": operation["operation"],
                    "target": operation["target"],
                    "beforeValue": "False",
                    "afterValue": "True",
                    "restoredValue": "",
                    "beforeRevision": BEFORE_REVISION,
                    "afterRevision": AFTER_REVISION,
                    "saved": True,
                    "rolledBack": False,
                    "rollbackValueMatch": True,
                    "diskUnchanged": False,
                    "backupPath": str(cwd / "Backups" / "secret.uasset.bak"),
                }
                write_json(Path(values["-Manifest"]), {"schemaVersion": "1.0", "assetPath": ASSET_PATH})
            write_json(Path(values["-Report"]), report)
            write_json(Path(values["-ValidationReport"]), {"valid": True})
            return ProcessResult(0, "", "")

        if script == "RunAssetCatalog.ps1":
            output = Path(values["-Output"])
            write_json(output / "manifest.json", {"projectName": PROJECT, "failureCount": 0})
            write_json(
                output / "canonical" / "asset.json",
                {
                    "projectName": PROJECT,
                    "assetPath": ASSET_PATH,
                    "assetClass": ASSET_CLASS,
                    "revision": {"available": True, "packageDirty": False, "value": self.revision},
                },
            )
            return ProcessResult(0, "", "")

        if script == "RunRollback.ps1":
            mode = values["-Mode"]
            if mode == "DryRun":
                write_json(
                    Path(values["-Report"]),
                    {"valid": True, "wroteDisk": False, "restored": False, "assetPath": ASSET_PATH},
                )
            else:
                self.revision = BEFORE_REVISION
                write_json(
                    Path(values["-Report"]),
                    {"valid": True, "wroteDisk": True, "restored": True, "assetPath": ASSET_PATH},
                )
                write_json(
                    Path(values["-VerificationReport"]),
                    {"verified": True, "actualRevision": BEFORE_REVISION, "assetPath": ASSET_PATH},
                )
            return ProcessResult(0, "", "")

        return ProcessResult(1, "", "unexpected script")

    @staticmethod
    def assert_safe(arguments: list[str], cwd: Path, timeout_seconds: int) -> None:
        if arguments[0] != "powershell.exe" or cwd.name != "tool" or timeout_seconds != 1800:
            raise AssertionError("unsafe process invocation")


class AgentWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ueak_workflow_")
        self.root = Path(self.temporary.name)
        self.tool_root = self.root / "tool"
        self.engine_root = self.root / "engine"
        self.project_path = self.root / "project" / "我的项目.uproject"
        self.policy_path = self.tool_root / "config" / "policy.json"
        self.revision_export = self.tool_root / "Output" / "Revision"
        self.work_root = self.tool_root / "Output" / "McpWorkflow"
        self.backup_root = self.tool_root / "Backups" / "McpWorkflow"
        for script in ("RunPatch.ps1", "RunRollback.ps1", "RunAssetCatalog.ps1"):
            path = self.tool_root / "scripts" / script
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# fixture\n", encoding="utf-8")
        editor = self.engine_root / "Engine" / "Binaries" / "Win64" / "UnrealEditor-Cmd.exe"
        editor.parent.mkdir(parents=True, exist_ok=True)
        editor.write_bytes(b"fixture")
        self.project_path.parent.mkdir(parents=True, exist_ok=True)
        self.project_path.write_text("{}", encoding="utf-8")
        write_json(
            self.policy_path,
            {
                "schemaVersion": "1.0",
                "validationEnabled": True,
                "commitEnabled": True,
                "allowedProjectNames": [PROJECT],
                "allowedAssetRoots": ["/Game/UEAgentKitWriteTests"],
                "allowedReferenceRoots": [],
                "allowedReferenceClasses": [],
                "allowedOperations": ["setAssetProperty"],
                "allowedAssetClasses": [ASSET_CLASS],
                "allowedAssetProperties": [f"{ASSET_CLASS}#BoolValue"],
                "allowedMaterialParameters": [],
                "allowedDataTableFields": [],
                "requireRevision": True,
                "rejectDirtyPackages": True,
                "maxAssetsPerPatch": 1,
                "maxOperationsPerAsset": 1,
                "maxValueBytes": 65536,
            },
        )
        write_json(
            self.revision_export / "manifest.json",
            {"projectName": PROJECT, "failureCount": 0, "successCount": 1},
        )
        write_json(
            self.revision_export / "canonical" / "asset.json",
            {
                "projectName": PROJECT,
                "assetPath": ASSET_PATH,
                "packageName": ASSET_PATH.split(".", 1)[0],
                "assetClass": ASSET_CLASS,
                "revision": {"available": True, "packageDirty": False, "value": BEFORE_REVISION},
            },
        )
        self.runner = FakeWorkflowRunner()
        self.freshness = FakeFreshnessTracker()
        self.config = PatchWorkflowConfig(
            tool_root=self.tool_root,
            engine_root=self.engine_root,
            project_path=self.project_path,
            policy_path=self.policy_path,
            revision_export=self.revision_export,
            work_root=self.work_root,
            backup_root=self.backup_root,
            commit_enabled=True,
        )
        self.service = PatchWorkflowService(
            FakeIndexService(),
            self.config,
            process_runner=self.runner,
            freshness_tracker=self.freshness,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_asset_state_reports_synchronized_and_disk_newer_states(self) -> None:
        synchronized = self.service.get_asset_state(ASSET_PATH)
        self.assertEqual(synchronized["state"], "synchronized")
        self.assertFalse(synchronized["saveRequired"])
        self.assertFalse(synchronized["indexRefreshRequired"])
        self.assertEqual(synchronized["sources"]["memory"]["state"], "unavailable")
        self.assertFalse(synchronized["sources"]["memory"]["revisionAvailable"])
        self.assertEqual(synchronized["sources"]["disk"]["revision"], BEFORE_REVISION)

        self.freshness.state = "stale"
        stale = self.service.get_asset_state(ASSET_PATH)
        self.assertEqual(stale["state"], "disk-newer-than-snapshots")
        self.assertTrue(stale["indexRefreshRequired"])
        self.assertEqual(stale["recommendedAction"], "refresh-asset-index")

        with self.assertRaises(WorkflowError) as invalid:
            self.service.get_asset_state("Game/Invalid")
        self.assertEqual(invalid.exception.code, "asset-state-invalid-asset")

    def test_asset_state_prioritizes_dirty_editor_memory(self) -> None:
        class DirtyLiveService:
            def status(self) -> dict[str, Any]:
                return {"state": "available"}

            def call_tool(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
                if tool_name != "ue_inspect_asset_live" or params != {"assetPath": ASSET_PATH}:
                    raise AssertionError("unexpected Live Editor call")
                return {
                    "ok": True,
                    "result": {
                        "assetRegistry": {"found": True},
                        "memory": {
                            "state": "loaded-unsaved",
                            "loaded": True,
                            "packageDirty": True,
                            "openInAssetEditor": True,
                            "selected": False,
                            "loadedByBridge": False,
                        },
                    },
                }

        service = PatchWorkflowService(
            FakeIndexService(),
            self.config,
            process_runner=self.runner,
            freshness_tracker=FakeFreshnessTracker(),
            live_editor_service=DirtyLiveService(),
        )
        result = service.get_asset_state(ASSET_PATH)
        self.assertEqual(result["state"], "memory-dirty")
        self.assertTrue(result["saveRequired"])
        self.assertTrue(result["refreshBlockedByDirtyMemory"])
        self.assertEqual(result["recommendedAction"], "save-or-revert-memory")
        self.assertFalse(result["limitations"]["memoryRevisionAvailable"])

    def test_authorized_save_preview_requires_dirty_loaded_asset_and_exact_confirmation(self) -> None:
        class DirtyLiveService:
            def status(self) -> dict[str, Any]:
                return {
                    "state": "available",
                    "pieState": "stopped",
                    "sessionId": "session-1",
                    "processId": 1234,
                }

            def call_tool(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
                if tool_name != "ue_inspect_asset_live" or params != {"assetPath": ASSET_PATH}:
                    raise AssertionError("unexpected Live Editor call")
                return {
                    "ok": True,
                    "result": {
                        "assetRegistry": {"found": True, "classPath": ASSET_CLASS},
                        "memory": {
                            "loaded": True,
                            "packageDirty": True,
                            "loadedByBridge": False,
                        },
                    },
                }

            def call_method(self, method: str, params: dict[str, Any], *, timeout_seconds: float) -> dict[str, Any]:
                raise AssertionError(f"save should not execute during Preview or invalid confirmation: {method} {params} {timeout_seconds}")

        service = PatchWorkflowService(
            FakeIndexService(),
            self.config,
            process_runner=self.runner,
            freshness_tracker=FakeFreshnessTracker(),
            live_editor_service=DirtyLiveService(),
        )
        preview = service.save_authorized_asset(ASSET_PATH)
        self.assertEqual(preview["mode"], "Preview")
        self.assertTrue(preview["saveReceipt"].startswith("save_"))
        self.assertTrue(preview["packageDirty"] )
        self.assertFalse(preview["saved"])
        with self.assertRaises(WorkflowError) as invalid:
            service.save_authorized_asset(
                ASSET_PATH,
                mode="Commit",
                save_receipt=preview["saveReceipt"],
                confirmation="SAVE wrong",
            )
        self.assertEqual(invalid.exception.code, "save-confirmation-required")

    def test_live_asset_property_write_requires_plan_confirmation_and_keeps_disk_unchanged(self) -> None:
        class LiveWriteService:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, Any]]] = []

            def call_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
                self.calls.append((method, params))
                return {
                    "action": "apply-asset-property-live",
                    "assetPath": ASSET_PATH,
                    "propertyPath": "BoolValue",
                    "beforeValue": False,
                    "afterValue": True,
                    "changed": True,
                    "transactionRecorded": True,
                    "packageDirtyAfter": True,
                    "saved": False,
                }

        bridge = LiveWriteService()
        service = PatchWorkflowService(
            FakeIndexService(),
            self.config,
            process_runner=self.runner,
            freshness_tracker=FakeFreshnessTracker(),
            live_editor_service=bridge,
        )
        plan = service.plan_patch(
            asset_path=ASSET_PATH,
            operation="setAssetProperty",
            target={"propertyPath": "BoolValue"},
            value=True,
            description="Live Editor write test",
        )
        with self.assertRaises(WorkflowError) as invalid:
            service.apply_asset_property_live(plan["planId"], "LIVE APPLY wrong")
        self.assertEqual(invalid.exception.code, "live-editor-write-confirmation-required")
        self.assertEqual(bridge.calls, [])

        result = service.apply_asset_property_live(
            plan["planId"],
            f"LIVE APPLY {plan['planId']}",
        )
        self.assertEqual(result["mode"], "LiveApply")
        self.assertTrue(result["changed"])
        self.assertTrue(result["undoAvailableInEditor"])
        self.assertFalse(result["saved"])
        self.assertFalse(result["diskRevisionChanged"])
        self.assertEqual(result["expectedDiskRevision"], BEFORE_REVISION)
        self.assertEqual(result["operation"], "setAssetProperty")
        self.assertEqual(result["valueKind"], "scalar")
        self.assertEqual(
            bridge.calls,
            [
                (
                    "editor.applyAssetPropertyLive",
                    {
                        "operation": "setAssetProperty",
                        "assetPath": ASSET_PATH,
                        "propertyPath": "BoolValue",
                        "target": {"propertyPath": "BoolValue"},
                        "value": True,
                    },
                )
            ],
        )
        self.assertEqual(self.freshness.state, "fresh")

    def _write_reference_policy_and_export(self) -> None:
        write_json(
            self.policy_path,
            {
                "schemaVersion": "1.0",
                "validationEnabled": True,
                "commitEnabled": True,
                "allowedProjectNames": [PROJECT],
                "allowedAssetRoots": ["/Game/UEAgentKitWriteTests"],
                "allowedReferenceRoots": ["/Game/UEAgentKitWriteTests/References"],
                "allowedReferenceClasses": [
                    "/Script/Engine.Texture2D",
                    "/Script/Engine.Actor",
                ],
                "allowedOperations": ["setAssetReferenceProperty"],
                "allowedAssetClasses": [REFERENCE_ASSET_CLASS],
                "allowedAssetProperties": [
                    f"{REFERENCE_ASSET_CLASS}#ObjectValue",
                    f"{REFERENCE_ASSET_CLASS}#ClassValue",
                    f"{REFERENCE_ASSET_CLASS}#SoftObjectValue",
                    f"{REFERENCE_ASSET_CLASS}#SoftClassValue",
                ],
                "allowedMaterialParameters": [],
                "allowedDataTableFields": [],
                "requireRevision": True,
                "rejectDirtyPackages": True,
                "maxAssetsPerPatch": 1,
                "maxOperationsPerAsset": 1,
                "maxValueBytes": 65536,
            },
        )
        write_json(
            self.revision_export / "canonical" / "asset.json",
            {
                "projectName": PROJECT,
                "assetPath": REFERENCE_ASSET_PATH,
                "packageName": REFERENCE_ASSET_PATH.split(".", 1)[0],
                "assetClass": REFERENCE_ASSET_CLASS,
                "revision": {"available": True, "packageDirty": False, "value": BEFORE_REVISION},
                "assetDetails": {
                    "type": "data-asset",
                    "properties": [
                        {"name": "ObjectValue", "referenceType": "Object"},
                        {"name": "ClassValue", "referenceType": "Class"},
                        {"name": "SoftObjectValue", "referenceType": "SoftObject"},
                        {"name": "SoftClassValue", "referenceType": "SoftClass"},
                    ],
                },
            },
        )

    def _write_structured_policy_and_export(self) -> None:
        write_json(
            self.policy_path,
            {
                "schemaVersion": "1.0",
                "validationEnabled": True,
                "commitEnabled": True,
                "allowedProjectNames": [PROJECT],
                "allowedAssetRoots": ["/Game/UEAgentKitWriteTests"],
                "allowedReferenceRoots": [],
                "allowedReferenceClasses": [],
                "allowedOperations": ["setAssetStructuredProperty"],
                "allowedAssetClasses": [STRUCTURED_ASSET_CLASS],
                "allowedAssetProperties": [f"{STRUCTURED_ASSET_CLASS}#StructValue"],
                "allowedMaterialParameters": [],
                "allowedDataTableFields": [],
                "requireRevision": True,
                "rejectDirtyPackages": True,
                "maxAssetsPerPatch": 1,
                "maxOperationsPerAsset": 1,
                "maxValueBytes": 65536,
            },
        )
        write_json(
            self.revision_export / "canonical" / "structured_asset.json",
            {
                "projectName": PROJECT,
                "assetPath": STRUCTURED_ASSET_PATH,
                "packageName": STRUCTURED_ASSET_PATH.split(".", 1)[0],
                "assetClass": STRUCTURED_ASSET_CLASS,
                "revision": {"available": True, "packageDirty": False, "value": BEFORE_REVISION},
                "assetDetails": {
                    "type": "data-asset",
                    "properties": [
                        {
                            "name": "StructValue",
                            "structuredType": "Struct",
                            "structuredSupported": True,
                            "structuredSchema": {
                                "kind": "Struct",
                                "structPath": "/Script/UEAgentKitEditor.UEAgentKitStructuredFixtureRecord",
                                "fields": [
                                    {"name": "Count", "schema": {"kind": "Scalar", "scalarType": "Int32"}},
                                    {"name": "Label", "schema": {"kind": "Scalar", "scalarType": "String"}},
                                    {"name": "bEnabled", "schema": {"kind": "Scalar", "scalarType": "Bool"}},
                                ],
                            },
                        }
                    ],
                },
            },
        )

    def test_live_reference_property_write_passes_operation_and_preserves_json_value(self) -> None:
        class LiveReferenceWriteService:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, Any]]] = []

            def call_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
                self.calls.append((method, params))
                return {
                    "action": "apply-asset-property-live",
                    "operation": "setAssetReferenceProperty",
                    "assetPath": REFERENCE_ASSET_PATH,
                    "propertyPath": "ObjectValue",
                    "valueKind": "reference",
                    "referenceType": "Object",
                    "changed": True,
                    "transactionRecorded": True,
                    "packageDirtyAfter": True,
                    "dirtyAfter": True,
                    "saved": False,
                }

        self._write_reference_policy_and_export()
        bridge = LiveReferenceWriteService()
        service = PatchWorkflowService(
            FakeIndexService(),
            self.config,
            process_runner=self.runner,
            freshness_tracker=FakeFreshnessTracker(),
            live_editor_service=bridge,
        )
        reference_value = {
            "referenceType": "Object",
            "path": "/Game/UEAgentKitWriteTests/References/T_Target.T_Target",
        }
        plan = service.plan_patch(
            asset_path=REFERENCE_ASSET_PATH,
            operation="setAssetReferenceProperty",
            target={"propertyPath": "ObjectValue"},
            value=reference_value,
            description="Live reference write test",
        )
        with self.assertRaises(WorkflowError) as invalid:
            service.apply_asset_property_live(plan["planId"], "LIVE APPLY wrong")
        self.assertEqual(invalid.exception.code, "live-editor-write-confirmation-required")
        self.assertEqual(bridge.calls, [])

        result = service.apply_asset_property_live(
            plan["planId"],
            f"LIVE APPLY {plan['planId']}",
        )
        self.assertEqual(result["mode"], "LiveApply")
        self.assertEqual(result["operation"], "setAssetReferenceProperty")
        self.assertEqual(result["valueKind"], "reference")
        self.assertTrue(result["changed"])
        self.assertFalse(result["saved"])
        self.assertFalse(result["diskRevisionChanged"])
        self.assertEqual(
            bridge.calls,
            [
                (
                    "editor.applyAssetPropertyLive",
                    {
                        "operation": "setAssetReferenceProperty",
                        "assetPath": REFERENCE_ASSET_PATH,
                        "propertyPath": "ObjectValue",
                        "target": {"propertyPath": "ObjectValue"},
                        "value": reference_value,
                    },
                )
            ],
        )
        self.assertIsInstance(bridge.calls[0][1]["value"], dict)
        self.assertEqual(bridge.calls[0][1]["value"], reference_value)

    def test_live_reference_property_write_passes_json_null(self) -> None:
        class LiveNullWriteService:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, Any]]] = []

            def call_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
                self.calls.append((method, params))
                return {
                    "action": "apply-asset-property-live",
                    "operation": "setAssetReferenceProperty",
                    "assetPath": REFERENCE_ASSET_PATH,
                    "propertyPath": "SoftObjectValue",
                    "valueKind": "reference",
                    "referenceType": "SoftObject",
                    "referencePath": None,
                    "beforeValue": None,
                    "afterValue": None,
                    "changed": True,
                    "transactionRecorded": True,
                    "packageDirtyAfter": True,
                    "dirtyAfter": True,
                    "saved": False,
                }

        self._write_reference_policy_and_export()
        bridge = LiveNullWriteService()
        service = PatchWorkflowService(
            FakeIndexService(),
            self.config,
            process_runner=self.runner,
            freshness_tracker=FakeFreshnessTracker(),
            live_editor_service=bridge,
        )
        plan = service.plan_patch(
            asset_path=REFERENCE_ASSET_PATH,
            operation="setAssetReferenceProperty",
            target={"propertyPath": "SoftObjectValue"},
            value=None,
            description="Live reference clear test",
        )
        result = service.apply_asset_property_live(plan["planId"], f"LIVE APPLY {plan['planId']}")
        self.assertEqual(result["mode"], "LiveApply")
        self.assertEqual(result["operation"], "setAssetReferenceProperty")
        self.assertEqual(result["valueKind"], "reference")
        self.assertTrue(result["changed"])
        self.assertFalse(result["saved"])
        self.assertEqual(len(bridge.calls), 1)
        self.assertEqual(bridge.calls[0][0], "editor.applyAssetPropertyLive")
        self.assertEqual(bridge.calls[0][1]["operation"], "setAssetReferenceProperty")
        self.assertEqual(bridge.calls[0][1]["propertyPath"], "SoftObjectValue")
        self.assertIsNone(bridge.calls[0][1]["value"])

    def test_live_structured_property_write_passes_operation_and_preserves_json_value(self) -> None:
        class LiveStructuredWriteService:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, Any]]] = []

            def call_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
                self.calls.append((method, params))
                return {
                    "action": "apply-asset-property-live",
                    "operation": "setAssetStructuredProperty",
                    "assetPath": STRUCTURED_ASSET_PATH,
                    "propertyPath": "StructValue",
                    "valueKind": "structured",
                    "structuredKind": "Struct",
                    "structuredSchema": {
                        "kind": "Struct",
                        "structPath": "/Script/UEAgentKitEditor.UEAgentKitStructuredFixtureRecord",
                        "fields": [
                            {"name": "Count", "schema": {"kind": "Scalar", "scalarType": "Int32"}},
                            {"name": "Label", "schema": {"kind": "Scalar", "scalarType": "String"}},
                            {"name": "bEnabled", "schema": {"kind": "Scalar", "scalarType": "Bool"}},
                        ],
                    },
                    "beforeValue": STRUCTURED_DEFAULT_STRUCT_VALUE,
                    "afterValue": STRUCTURED_STRUCT_VALUE,
                    "diff": [
                        {"path": "Count", "before": 0, "after": 42},
                        {"path": "Label", "before": "", "after": "Live Write"},
                        {"path": "bEnabled", "before": False, "after": True},
                    ],
                    "diffTruncated": False,
                    "changed": True,
                    "transactionRecorded": True,
                    "transactionTitle": "UE Agent Kit: Set Asset Structured Property",
                    "assetOpen": True,
                    "loadedByBridge": False,
                    "packageDirtyBefore": False,
                    "packageDirtyAfter": True,
                    "dirtyBefore": False,
                    "dirtyAfter": True,
                    "saved": False,
                    "editorSessionId": "session-1",
                }

        self._write_structured_policy_and_export()
        bridge = LiveStructuredWriteService()
        service = PatchWorkflowService(
            FakeIndexService(),
            self.config,
            process_runner=self.runner,
            freshness_tracker=FakeFreshnessTracker(),
            live_editor_service=bridge,
        )
        plan = service.plan_patch(
            asset_path=STRUCTURED_ASSET_PATH,
            operation="setAssetStructuredProperty",
            target={"propertyPath": "StructValue"},
            value=STRUCTURED_STRUCT_VALUE,
            description="Live structured write test",
        )
        result = service.apply_asset_property_live(plan["planId"], f"LIVE APPLY {plan['planId']}")
        self.assertEqual(result["mode"], "LiveApply")
        self.assertEqual(result["operation"], "setAssetStructuredProperty")
        self.assertEqual(result["valueKind"], "structured")
        self.assertTrue(result["changed"])
        self.assertFalse(result["saved"])
        self.assertFalse(result["diskRevisionChanged"])
        self.assertTrue(result["undoAvailableInEditor"])
        self.assertEqual(
            bridge.calls,
            [
                (
                    "editor.applyAssetPropertyLive",
                    {
                        "operation": "setAssetStructuredProperty",
                        "assetPath": STRUCTURED_ASSET_PATH,
                        "propertyPath": "StructValue",
                        "target": {"propertyPath": "StructValue"},
                        "value": STRUCTURED_STRUCT_VALUE,
                    },
                )
            ],
        )
        self.assertIsInstance(bridge.calls[0][1]["value"], dict)
        self.assertEqual(bridge.calls[0][1]["value"], STRUCTURED_STRUCT_VALUE)
        self.assertEqual(result["result"]["structuredKind"], "Struct")
        self.assertEqual(result["result"]["packageDirtyAfter"], True)

    def test_live_structured_property_write_reports_noop(self) -> None:
        class LiveStructuredNoopService:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, Any]]] = []

            def call_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
                self.calls.append((method, params))
                return {
                    "action": "apply-asset-property-live",
                    "operation": "setAssetStructuredProperty",
                    "assetPath": STRUCTURED_ASSET_PATH,
                    "propertyPath": "StructValue",
                    "valueKind": "structured",
                    "structuredKind": "Struct",
                    "beforeValue": STRUCTURED_DEFAULT_STRUCT_VALUE,
                    "afterValue": STRUCTURED_DEFAULT_STRUCT_VALUE,
                    "diff": [],
                    "diffTruncated": False,
                    "changed": False,
                    "transactionRecorded": False,
                    "transactionTitle": "",
                    "assetOpen": True,
                    "loadedByBridge": False,
                    "packageDirtyBefore": False,
                    "packageDirtyAfter": False,
                    "dirtyBefore": False,
                    "dirtyAfter": False,
                    "saved": False,
                    "editorSessionId": "session-1",
                }

        self._write_structured_policy_and_export()
        bridge = LiveStructuredNoopService()
        service = PatchWorkflowService(
            FakeIndexService(),
            self.config,
            process_runner=self.runner,
            freshness_tracker=FakeFreshnessTracker(),
            live_editor_service=bridge,
        )
        plan = service.plan_patch(
            asset_path=STRUCTURED_ASSET_PATH,
            operation="setAssetStructuredProperty",
            target={"propertyPath": "StructValue"},
            value=STRUCTURED_DEFAULT_STRUCT_VALUE,
            description="Live structured noop test",
        )
        result = service.apply_asset_property_live(plan["planId"], f"LIVE APPLY {plan['planId']}")
        self.assertEqual(result["operation"], "setAssetStructuredProperty")
        self.assertEqual(result["valueKind"], "structured")
        self.assertFalse(result["changed"])
        self.assertFalse(result["undoAvailableInEditor"])
        self.assertEqual(result["nextStep"], "No value change was required.")
        self.assertEqual(len(bridge.calls), 1)
        self.assertEqual(bridge.calls[0][0], "editor.applyAssetPropertyLive")
        self.assertEqual(bridge.calls[0][1]["operation"], "setAssetStructuredProperty")
        self.assertEqual(bridge.calls[0][1]["propertyPath"], "StructValue")

    def test_live_material_parameter_write_passes_parameter_name_and_value(self) -> None:
        material_class = MATERIAL_ASSET_CLASS
        material_path = MATERIAL_ASSET_PATH
        write_json(
            self.policy_path,
            {
                "schemaVersion": "1.0",
                "validationEnabled": True,
                "commitEnabled": True,
                "allowedProjectNames": [PROJECT],
                "allowedAssetRoots": ["/Game/UEAgentKitWriteTests"],
                "allowedReferenceRoots": ["/Game/UEAgentKitWriteTests/Materials"],
                "allowedReferenceClasses": ["/Script/Engine.Texture2D"],
                "allowedOperations": [
                    "setMaterialInstanceScalarParameter",
                    "setMaterialInstanceVectorParameter",
                    "setMaterialInstanceTextureParameter",
                    "setMaterialInstanceStaticSwitchParameter",
                ],
                "allowedAssetClasses": [material_class],
                "allowedAssetProperties": [],
                "allowedMaterialParameters": [
                    f"{material_class}#Scalar#EmissiveIntensity",
                    f"{material_class}#Vector#TintColor",
                ],
                "allowedDataTableFields": [],
                "requireRevision": True,
                "rejectDirtyPackages": True,
                "maxAssetsPerPatch": 1,
                "maxOperationsPerAsset": 1,
                "maxValueBytes": 65536,
            },
        )
        write_json(
            self.revision_export / "canonical" / "material_asset.json",
            {
                "projectName": PROJECT,
                "assetPath": material_path,
                "packageName": material_path.split(".", 1)[0],
                "assetClass": material_class,
                "revision": {"available": True, "packageDirty": False, "value": BEFORE_REVISION},
                "assetDetails": {
                    "type": "material-instance",
                    "properties": [],
                },
            },
        )

        class LiveMaterialWriteService:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, Any]]] = []

            def call_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
                self.calls.append((method, params))
                return {
                    "action": "apply-asset-property-live",
                    "operation": "setMaterialInstanceScalarParameter",
                    "assetPath": material_path,
                    "parameterName": "EmissiveIntensity",
                    "parameterType": "Scalar",
                    "parameterAssociation": "Global",
                    "valueKind": "material-scalar",
                    "beforeValue": 0.25,
                    "afterValue": 0.75,
                    "changed": True,
                    "transactionRecorded": True,
                    "transactionTitle": "UE Agent Kit: Set Material Instance Parameter",
                    "assetOpen": True,
                    "loadedByBridge": False,
                    "packageDirtyBefore": False,
                    "packageDirtyAfter": True,
                    "dirtyBefore": False,
                    "dirtyAfter": True,
                    "saved": False,
                    "editorSessionId": "session-1",
                }

        bridge = LiveMaterialWriteService()
        service = PatchWorkflowService(
            FakeIndexService(),
            self.config,
            process_runner=self.runner,
            freshness_tracker=FakeFreshnessTracker(),
            live_editor_service=bridge,
        )
        plan = service.plan_patch(
            asset_path=material_path,
            operation="setMaterialInstanceScalarParameter",
            target={"parameterName": "EmissiveIntensity"},
            value=0.75,
            description="Live material write test",
        )
        with self.assertRaises(WorkflowError) as invalid:
            service.apply_asset_property_live(plan["planId"], "LIVE APPLY wrong")
        self.assertEqual(invalid.exception.code, "live-editor-write-confirmation-required")
        self.assertEqual(bridge.calls, [])

        result = service.apply_asset_property_live(
            plan["planId"],
            f"LIVE APPLY {plan['planId']}",
        )
        self.assertEqual(result["mode"], "LiveApply")
        self.assertEqual(result["operation"], "setMaterialInstanceScalarParameter")
        self.assertEqual(result["valueKind"], "material-scalar")
        self.assertEqual(result["propertyPath"], None)
        self.assertEqual(result["parameterName"], "EmissiveIntensity")
        self.assertTrue(result["changed"])
        self.assertTrue(result["undoAvailableInEditor"])
        self.assertFalse(result["saved"])
        self.assertFalse(result["diskRevisionChanged"])
        self.assertEqual(
            bridge.calls,
            [
                (
                    "editor.applyAssetPropertyLive",
                    {
                        "operation": "setMaterialInstanceScalarParameter",
                        "assetPath": material_path,
                        "target": {"parameterName": "EmissiveIntensity"},
                        "value": 0.75,
                        "parameterName": "EmissiveIntensity",
                    },
                )
            ],
        )

    def test_live_material_parameter_write_requires_parameter_name(self) -> None:
        material_class = MATERIAL_ASSET_CLASS
        material_path = MATERIAL_ASSET_PATH
        write_json(
            self.policy_path,
            {
                "schemaVersion": "1.0",
                "validationEnabled": True,
                "commitEnabled": True,
                "allowedProjectNames": [PROJECT],
                "allowedAssetRoots": ["/Game/UEAgentKitWriteTests"],
                "allowedReferenceRoots": [],
                "allowedReferenceClasses": [],
                "allowedOperations": ["setMaterialInstanceVectorParameter"],
                "allowedAssetClasses": [material_class],
                "allowedAssetProperties": [],
                "allowedMaterialParameters": [f"{material_class}#Vector#TintColor"],
                "allowedDataTableFields": [],
                "requireRevision": True,
                "rejectDirtyPackages": True,
                "maxAssetsPerPatch": 1,
                "maxOperationsPerAsset": 1,
                "maxValueBytes": 65536,
            },
        )
        write_json(
            self.revision_export / "canonical" / "material_asset.json",
            {
                "projectName": PROJECT,
                "assetPath": material_path,
                "packageName": material_path.split(".", 1)[0],
                "assetClass": material_class,
                "revision": {"available": True, "packageDirty": False, "value": BEFORE_REVISION},
                "assetDetails": {
                    "type": "material-instance",
                    "properties": [],
                },
            },
        )
        service = PatchWorkflowService(
            FakeIndexService(),
            self.config,
            process_runner=self.runner,
            freshness_tracker=FakeFreshnessTracker(),
            live_editor_service=object(),
        )
        plan = service.plan_patch(
            asset_path=material_path,
            operation="setMaterialInstanceVectorParameter",
            target={"parameterName": "TintColor"},
            value={"r": 1.0, "g": 0.5, "b": 0.25, "a": 1.0},
        )
        service._plans[plan["planId"]].patch["assets"][0]["operations"][0]["target"] = {"other": "field"}
        with self.assertRaises(WorkflowError) as rejected:
            service.apply_asset_property_live(plan["planId"], f"LIVE APPLY {plan['planId']}")
        self.assertEqual(rejected.exception.code, "plan-tampered")

    def test_live_data_table_cell_write_passes_row_and_field_names(self) -> None:
        write_json(
            self.policy_path,
            {
                "schemaVersion": "1.0",
                "validationEnabled": True,
                "commitEnabled": True,
                "allowedProjectNames": [PROJECT],
                "allowedAssetRoots": ["/Game/UEAgentKitWriteTests"],
                "allowedReferenceRoots": [],
                "allowedReferenceClasses": [],
                "allowedOperations": ["setDataTableCell", "renameDataTableRow"],
                "allowedAssetClasses": [DATA_TABLE_CLASS],
                "allowedAssetProperties": [],
                "allowedMaterialParameters": [],
                "allowedDataTableFields": [f"{DATA_TABLE_CLASS}#{DATA_TABLE_ROW_STRUCT}#Count"],
                "requireRevision": True,
                "rejectDirtyPackages": True,
                "maxAssetsPerPatch": 1,
                "maxOperationsPerAsset": 1,
                "maxValueBytes": 65536,
            },
        )
        write_json(
            self.revision_export / "canonical" / "data_table.json",
            {
                "projectName": PROJECT,
                "assetPath": DATA_TABLE_PATH,
                "packageName": DATA_TABLE_PATH.split(".", 1)[0],
                "assetClass": DATA_TABLE_CLASS,
                "revision": {"available": True, "packageDirty": False, "value": BEFORE_REVISION},
                "assetDetails": {
                    "type": "data-asset",
                    "rowStructPath": DATA_TABLE_ROW_STRUCT,
                    "rowNames": ["Row1"],
                    "properties": [],
                },
            },
        )

        class LiveDataTableCellService:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, Any]]] = []

            def call_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
                self.calls.append((method, params))
                return {
                    "action": "apply-asset-property-live",
                    "operation": "setDataTableCell",
                    "assetPath": DATA_TABLE_PATH,
                    "rowName": "Row1",
                    "fieldName": "Count",
                    "dataTableKind": "cell",
                    "rowStructPath": DATA_TABLE_ROW_STRUCT,
                    "valueKind": "data-table-cell",
                    "beforeValue": {"Count": 1, "Label": "Initial", "bEnabled": False},
                    "afterValue": {"Count": 42, "Label": "Initial", "bEnabled": False},
                    "changed": True,
                    "transactionRecorded": True,
                    "transactionTitle": "UE Agent Kit: Set DataTable Value",
                    "assetOpen": True,
                    "loadedByBridge": False,
                    "packageDirtyBefore": False,
                    "packageDirtyAfter": True,
                    "dirtyBefore": False,
                    "dirtyAfter": True,
                    "saved": False,
                    "editorSessionId": "session-1",
                }

        bridge = LiveDataTableCellService()
        service = PatchWorkflowService(
            FakeIndexService(),
            self.config,
            process_runner=self.runner,
            freshness_tracker=FakeFreshnessTracker(),
            live_editor_service=bridge,
        )
        plan = service.plan_patch(
            asset_path=DATA_TABLE_PATH,
            operation="setDataTableCell",
            target={"rowName": "Row1", "fieldName": "Count"},
            value=42,
            description="Live DataTable cell write test",
        )
        result = service.apply_asset_property_live(
            plan["planId"],
            f"LIVE APPLY {plan['planId']}",
        )
        self.assertEqual(result["mode"], "LiveApply")
        self.assertEqual(result["operation"], "setDataTableCell")
        self.assertEqual(result["valueKind"], "data-table-cell")
        self.assertEqual(result["propertyPath"], None)
        self.assertEqual(result["parameterName"], None)
        self.assertEqual(result["rowName"], "Row1")
        self.assertEqual(result["fieldName"], "Count")
        self.assertTrue(result["changed"])
        self.assertEqual(
            bridge.calls,
            [
                (
                    "editor.applyAssetPropertyLive",
                    {
                        "operation": "setDataTableCell",
                        "assetPath": DATA_TABLE_PATH,
                        "target": {"rowName": "Row1", "fieldName": "Count"},
                        "value": 42,
                        "rowName": "Row1",
                        "fieldName": "Count",
                    },
                )
            ],
        )

    def test_live_data_table_rename_passes_new_row_name(self) -> None:
        write_json(
            self.policy_path,
            {
                "schemaVersion": "1.0",
                "validationEnabled": True,
                "commitEnabled": True,
                "allowedProjectNames": [PROJECT],
                "allowedAssetRoots": ["/Game/UEAgentKitWriteTests"],
                "allowedReferenceRoots": [],
                "allowedReferenceClasses": [],
                "allowedOperations": ["renameDataTableRow"],
                "allowedAssetClasses": [DATA_TABLE_CLASS],
                "allowedAssetProperties": [],
                "allowedMaterialParameters": [],
                "allowedDataTableFields": [],
                "requireRevision": True,
                "rejectDirtyPackages": True,
                "maxAssetsPerPatch": 1,
                "maxOperationsPerAsset": 1,
                "maxValueBytes": 65536,
            },
        )
        write_json(
            self.revision_export / "canonical" / "data_table.json",
            {
                "projectName": PROJECT,
                "assetPath": DATA_TABLE_PATH,
                "packageName": DATA_TABLE_PATH.split(".", 1)[0],
                "assetClass": DATA_TABLE_CLASS,
                "revision": {"available": True, "packageDirty": False, "value": BEFORE_REVISION},
                "assetDetails": {
                    "type": "data-asset",
                    "rowStructPath": DATA_TABLE_ROW_STRUCT,
                    "rowNames": ["Row1"],
                    "properties": [],
                },
            },
        )
        class LiveDataTableRenameService:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, Any]]] = []

            def call_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
                self.calls.append((method, params))
                return {
                    "action": "apply-asset-property-live",
                    "operation": "renameDataTableRow",
                    "assetPath": DATA_TABLE_PATH,
                    "rowName": "Row1",
                    "newRowName": "RowRenamed",
                    "dataTableKind": "row-rename",
                    "rowStructPath": DATA_TABLE_ROW_STRUCT,
                    "valueKind": "data-table-row-rename",
                    "beforeValue": {"Count": 1, "Label": "Initial", "bEnabled": False},
                    "afterValue": {"Count": 1, "Label": "Initial", "bEnabled": False},
                    "changed": True,
                    "transactionRecorded": True,
                    "transactionTitle": "UE Agent Kit: Set DataTable Value",
                    "assetOpen": True,
                    "loadedByBridge": False,
                    "packageDirtyBefore": False,
                    "packageDirtyAfter": True,
                    "dirtyBefore": False,
                    "dirtyAfter": True,
                    "saved": False,
                    "editorSessionId": "session-1",
                }

        bridge = LiveDataTableRenameService()
        service = PatchWorkflowService(
            FakeIndexService(),
            self.config,
            process_runner=self.runner,
            freshness_tracker=FakeFreshnessTracker(),
            live_editor_service=bridge,
        )
        plan = service.plan_patch(
            asset_path=DATA_TABLE_PATH,
            operation="renameDataTableRow",
            target={"rowName": "Row1", "newRowName": "RowRenamed"},
            value=True,
        )
        self.assertEqual(plan["operation"], "renameDataTableRow")
        result = service.apply_asset_property_live(
            plan["planId"],
            f"LIVE APPLY {plan['planId']}",
        )
        self.assertEqual(result["mode"], "LiveApply")
        self.assertEqual(result["operation"], "renameDataTableRow")
        self.assertEqual(result["valueKind"], "data-table-row-rename")
        self.assertEqual(result["rowName"], "Row1")
        self.assertEqual(result["newRowName"], "RowRenamed")
        self.assertTrue(result["changed"])
        self.assertEqual(
            bridge.calls,
            [
                (
                    "editor.applyAssetPropertyLive",
                    {
                        "operation": "renameDataTableRow",
                        "assetPath": DATA_TABLE_PATH,
                        "target": {"rowName": "Row1", "newRowName": "RowRenamed"},
                        "value": True,
                        "rowName": "Row1",
                        "newRowName": "RowRenamed",
                    },
                )
            ],
        )

    def test_live_write_tool_count_and_names_are_unchanged(self) -> None:
        names = tool_names_for_mode(live_editor_enabled=True, workflow_enabled=True)
        self.assertEqual(len(names), 53)
        self.assertIn("ue_set_asset_property", names)
        self.assertIn("ue_set_asset_reference_property", names)
        self.assertIn("ue_apply_asset_property_live", names)
        self.assertIn("ue_undo_asset_property_live", names)
        self.assertIn("ue_discard_asset_property_live", names)
        self.assertIn("ue_verify_live_write", names)
        self.assertEqual(names.count("ue_set_asset_property"), 1)
        self.assertEqual(names.count("ue_set_asset_reference_property"), 1)
        self.assertEqual(names.count("ue_apply_asset_property_live"), 1)
        self.assertEqual(names.count("ue_undo_asset_property_live"), 1)
        self.assertEqual(names.count("ue_discard_asset_property_live"), 1)
        self.assertEqual(names.count("ue_verify_live_write"), 1)
        self.assertEqual(len(set(names)), len(names))

    def test_live_asset_property_write_requires_live_and_commit_modes(self) -> None:
        plan = self.service.plan_patch(
            asset_path=ASSET_PATH,
            operation="setAssetProperty",
            target={"propertyPath": "BoolValue"},
            value=True,
        )
        with self.assertRaises(WorkflowError) as no_live:
            self.service.apply_asset_property_live(plan["planId"], f"LIVE APPLY {plan['planId']}")
        self.assertEqual(no_live.exception.code, "live-editor-required")

        disabled_config = PatchWorkflowConfig(
            tool_root=self.config.tool_root,
            engine_root=self.config.engine_root,
            project_path=self.config.project_path,
            policy_path=self.config.policy_path,
            revision_export=self.config.revision_export,
            work_root=self.config.work_root,
            backup_root=self.config.backup_root,
            commit_enabled=False,
        )
        disabled_service = PatchWorkflowService(
            FakeIndexService(),
            disabled_config,
            process_runner=self.runner,
            freshness_tracker=FakeFreshnessTracker(),
            live_editor_service=object(),
        )
        disabled_plan = disabled_service.plan_patch(
            asset_path=ASSET_PATH,
            operation="setAssetProperty",
            target={"propertyPath": "BoolValue"},
            value=True,
        )
        with self.assertRaises(WorkflowError) as disabled:
            disabled_service.apply_asset_property_live(
                disabled_plan["planId"],
                f"LIVE APPLY {disabled_plan['planId']}",
            )
        self.assertEqual(disabled.exception.code, "live-editor-write-disabled")

    def test_complete_plan_dry_run_apply_verify_and_rollback_lifecycle(self) -> None:
        plan = self.service.plan_patch(
            asset_path=ASSET_PATH,
            operation="setAssetProperty",
            target={"propertyPath": "BoolValue"},
            value=True,
            description="MCP workflow test",
        )
        self.assertTrue(plan["ok"])
        self.assertEqual(plan["risk"], "medium")
        self.assertNotIn(str(self.tool_root), json.dumps(plan, ensure_ascii=False))

        dry_run = self.service.dry_run_patch(plan["planId"])
        self.assertTrue(all(dry_run["gates"].values()))
        self.assertEqual(self.runner.revision, BEFORE_REVISION)

        with self.assertRaisesRegex(WorkflowError, "confirmation"):
            self.service.apply_patch(plan["planId"], dry_run["dryRunReceipt"], "COMMIT wrong")

        applied = self.service.apply_patch(
            plan["planId"],
            dry_run["dryRunReceipt"],
            f"COMMIT {plan['planId']}",
        )
        self.assertEqual(applied["afterRevision"], AFTER_REVISION)
        self.assertEqual(applied["indexFreshness"]["state"], "stale")
        lifecycle = self.service.status()["indexLifecycle"]
        self.assertTrue(lifecycle["sessionStale"])
        self.assertTrue(lifecycle["sqliteIndexStale"])
        self.assertTrue(lifecycle["revisionExportStale"])
        with self.assertRaisesRegex(WorkflowError, "differs from"):
            self.service.plan_patch(
                asset_path=ASSET_PATH,
                operation="setAssetProperty",
                target={"propertyPath": "BoolValue"},
                value=False,
            )
        self.assertNotIn(str(self.tool_root), json.dumps(applied, ensure_ascii=False))
        with self.assertRaisesRegex(WorkflowError, "used, stale"):
            self.service.apply_patch(
                plan["planId"],
                dry_run["dryRunReceipt"],
                f"COMMIT {plan['planId']}",
            )

        verified = self.service.verify_asset(applied["applyReceipt"])
        self.assertTrue(verified["verified"])
        self.assertEqual(verified["actualRevision"], AFTER_REVISION)
        self.assertEqual(verified["indexFreshness"]["state"], "stale")
        evidence = verified["memoryTaskEvidence"]
        self.assertEqual(evidence["schemaVersion"], "1.0")
        self.assertEqual(evidence["tool"], "ue_memory_record_task")
        arguments = evidence["arguments"]
        self.assertEqual(arguments["task_key"], f"patch:{plan['planId']}")
        self.assertEqual(arguments["outcome"], "succeeded")
        self.assertEqual(arguments["patch_ref"], f"patch:{applied['patchDigest']}")
        self.assertEqual(
            arguments["backup_manifest_ref"],
            f"backup-manifest:{applied['manifestId']}",
        )
        self.assertEqual(
            arguments["validation_evidence_ref"],
            f"validation-evidence:{verified['reportId']}",
        )
        self.assertEqual(
            arguments["revision_set"],
            [
                {
                    "assetPath": ASSET_PATH,
                    "revision": AFTER_REVISION,
                    "revisionStable": True,
                }
            ],
        )
        self.assertEqual(
            arguments["patch_details"],
            {
                "planId": plan["planId"],
                "patchDigest": applied["patchDigest"],
                "beforeRevision": BEFORE_REVISION,
                "afterRevision": AFTER_REVISION,
            },
        )
        self.assertEqual(
            arguments["backup_manifest_details"],
            {"manifestId": applied["manifestId"]},
        )
        self.assertEqual(
            arguments["validation_evidence_details"],
            {
                "reportId": verified["reportId"],
                "independentReload": True,
                "verified": True,
                "expectedRevision": AFTER_REVISION,
                "actualRevision": AFTER_REVISION,
            },
        )
        self.assertNotIn("applyReceipt", json.dumps(evidence, ensure_ascii=False))
        self.assertNotIn(str(self.tool_root), json.dumps(evidence, ensure_ascii=False))
        self.assertIn("memoryTaskEvidence.arguments", verified["nextStep"])

        rollback_dry = self.service.rollback_patch(applied["applyReceipt"])
        self.assertEqual(rollback_dry["mode"], "DryRun")
        self.assertEqual(self.runner.revision, AFTER_REVISION)
        with self.assertRaisesRegex(WorkflowError, "confirmation"):
            self.service.rollback_patch(
                applied["applyReceipt"],
                mode="Commit",
                rollback_dry_run_receipt=rollback_dry["rollbackDryRunReceipt"],
                confirmation="ROLLBACK wrong",
            )
        restored = self.service.rollback_patch(
            applied["applyReceipt"],
            mode="Commit",
            rollback_dry_run_receipt=rollback_dry["rollbackDryRunReceipt"],
            confirmation=f"ROLLBACK {applied['applyReceipt']}",
        )
        self.assertTrue(restored["restored"])
        self.assertEqual(restored["indexFreshness"]["state"], "fresh")
        rollback_evidence = restored["memoryTaskEvidence"]
        self.assertEqual(rollback_evidence["tool"], "ue_memory_record_task")
        rollback_arguments = rollback_evidence["arguments"]
        self.assertEqual(rollback_arguments["task_key"], f"rollback:{plan['planId']}")
        self.assertEqual(rollback_arguments["outcome"], "rolledBack")
        self.assertEqual(rollback_arguments["patch_ref"], f"patch:{applied['patchDigest']}")
        self.assertEqual(
            rollback_arguments["backup_manifest_ref"],
            f"backup-manifest:{applied['manifestId']}",
        )
        self.assertEqual(
            rollback_arguments["validation_evidence_ref"],
            f"validation-evidence:{restored['verificationReportId']}",
        )
        self.assertEqual(
            rollback_arguments["revision_set"],
            [
                {
                    "assetPath": ASSET_PATH,
                    "revision": BEFORE_REVISION,
                    "revisionStable": True,
                }
            ],
        )
        self.assertEqual(
            rollback_arguments["patch_details"],
            {
                "planId": plan["planId"],
                "patchDigest": applied["patchDigest"],
                "committedRevision": AFTER_REVISION,
                "restoredRevision": BEFORE_REVISION,
            },
        )
        self.assertEqual(
            rollback_arguments["backup_manifest_details"],
            {"manifestId": applied["manifestId"], "restored": True},
        )
        self.assertEqual(
            rollback_arguments["validation_evidence_details"],
            {
                "rollbackReportId": restored["reportId"],
                "reportId": restored["verificationReportId"],
                "independentReload": True,
                "verified": True,
                "expectedRevision": BEFORE_REVISION,
                "actualRevision": BEFORE_REVISION,
            },
        )
        self.assertNotIn(applied["applyReceipt"], json.dumps(rollback_evidence, ensure_ascii=False))
        self.assertNotIn(str(self.tool_root), json.dumps(rollback_evidence, ensure_ascii=False))
        self.assertIn("memoryTaskEvidence.arguments", restored["nextStep"])
        self.assertFalse(self.service.status()["indexLifecycle"]["sessionStale"])
        self.assertEqual(self.runner.revision, BEFORE_REVISION)

    def test_plan_rejects_stale_or_unavailable_index_state(self) -> None:
        self.freshness.state = "stale"
        with self.assertRaisesRegex(WorkflowError, "differs from"):
            self.service.plan_patch(
                asset_path=ASSET_PATH,
                operation="setAssetProperty",
                target={"propertyPath": "BoolValue"},
                value=True,
            )
        self.freshness.state = "unavailable"
        with self.assertRaisesRegex(WorkflowError, "could not be compared"):
            self.service.plan_patch(
                asset_path=ASSET_PATH,
                operation="setAssetProperty",
                target={"propertyPath": "BoolValue"},
                value=True,
            )

    def test_plan_rejects_policy_mismatch_and_unindexed_assets(self) -> None:
        with self.assertRaisesRegex(WorkflowError, "not present"):
            self.service.plan_patch(
                asset_path="/Game/Missing/DA_None.DA_None",
                operation="setAssetProperty",
                target={"propertyPath": "BoolValue"},
                value=True,
            )
        with self.assertRaisesRegex(WorkflowError, "rejected") as rejected:
            self.service.plan_patch(
                asset_path=ASSET_PATH,
                operation="setAssetProperty",
                target={"propertyPath": "ForbiddenValue"},
                value=True,
            )
        self.assertEqual(rejected.exception.code, "policy-rejected")
        self.assertIn("asset-property-not-allowed", rejected.exception.details["issueCodes"])

    def test_structural_data_table_plan_rejects_exact_row_referencers(self) -> None:
        class DataTableIndexService(FakeIndexService):
            def __init__(self, reference_count: int) -> None:
                self.reference_count = reference_count

            def get_asset(self, asset_path: str, **_: Any) -> dict[str, Any]:
                if asset_path != ASSET_PATH:
                    return {"found": False, "ok": True}
                return {
                    "found": True,
                    "ok": True,
                    "asset": {
                        "asset_path": ASSET_PATH,
                        "asset_class": "/Script/Engine.DataTable",
                        "revision_value": BEFORE_REVISION,
                    },
                }

            def get_data_table_row_reference_impact(
                self,
                asset_path: str,
                row_name: str,
                *,
                sample_limit: int = 20,
            ) -> dict[str, Any]:
                referencers = []
                if self.reference_count:
                    referencers.append(
                        {
                            "source_asset_path": "/Game/UEAgentKitTests/BP_RowConsumer.BP_RowConsumer",
                            "target_path": f"{asset_path}::{row_name}",
                        }
                    )
                return {
                    "checked": True,
                    "source": "immutable-sqlite-searchable-name",
                    "assetPath": asset_path,
                    "rowName": row_name,
                    "targetPath": f"{asset_path}::{row_name}",
                    "referenceCount": self.reference_count,
                    "sampleLimit": sample_limit,
                    "sampleTruncated": False,
                    "referencers": referencers,
                }

        validation = {"valid": True, "commitAllowedByPolicy": True, "issues": []}
        referenced_service = PatchWorkflowService(
            DataTableIndexService(1),
            self.config,
            process_runner=self.runner,
            freshness_tracker=self.freshness,
        )
        with patch("ue_agent_kit.agent_workflow.validate_patch", return_value=validation):
            with self.assertRaises(WorkflowError) as rejected:
                referenced_service.plan_patch(
                    asset_path=ASSET_PATH,
                    operation="renameDataTableRow",
                    target={"rowName": "Row_Alpha", "newRowName": "Row_Beta"},
                    value=True,
                )
        self.assertEqual(rejected.exception.code, "data-table-row-referenced")
        self.assertEqual(rejected.exception.details["referenceCount"], 1)

        clear_service = PatchWorkflowService(
            DataTableIndexService(0),
            self.config,
            process_runner=self.runner,
            freshness_tracker=self.freshness,
        )
        with patch("ue_agent_kit.agent_workflow.validate_patch", return_value=validation):
            plan = clear_service.plan_patch(
                asset_path=ASSET_PATH,
                operation="removeDataTableRow",
                target={"rowName": "Row_Alpha"},
                value=True,
            )
        self.assertEqual(plan["referenceImpact"]["referenceCount"], 0)

    def test_plan_and_policy_are_locked_after_creation(self) -> None:
        plan = self.service.plan_patch(
            asset_path=ASSET_PATH,
            operation="setAssetProperty",
            target={"propertyPath": "BoolValue"},
            value=True,
        )
        record = self.service._plans[plan["planId"]]
        tampered = json.loads(record.patch_path.read_text(encoding="utf-8"))
        tampered["assets"][0]["operations"][0]["value"] = False
        write_json(record.patch_path, tampered)
        with self.assertRaisesRegex(WorkflowError, "changed after"):
            self.service.dry_run_patch(plan["planId"])

        record.patch_path.write_text(
            json.dumps(record.patch, ensure_ascii=False, indent=2),
            encoding="utf-8",
            newline="\r\n",
        )
        policy = json.loads(self.policy_path.read_text(encoding="utf-8"))
        policy["maxValueBytes"] = 8192
        write_json(self.policy_path, policy)
        with self.assertRaisesRegex(WorkflowError, "Policy changed"):
            self.service.dry_run_patch(plan["planId"])

    def test_commit_can_be_disabled_at_server_configuration(self) -> None:
        config = PatchWorkflowConfig(**{**self.config.__dict__, "commit_enabled": False})
        service = PatchWorkflowService(
            FakeIndexService(),
            config,
            process_runner=self.runner,
            freshness_tracker=FakeFreshnessTracker(),
        )
        plan = service.plan_patch(
            asset_path=ASSET_PATH,
            operation="setAssetProperty",
            target={"propertyPath": "BoolValue"},
            value=True,
        )
        dry_run = service.dry_run_patch(plan["planId"])
        with self.assertRaisesRegex(WorkflowError, "not enabled"):
            service.apply_patch(plan["planId"], dry_run["dryRunReceipt"], f"COMMIT {plan['planId']}")

    def test_runtime_boundary_rejects_reparse_components(self) -> None:
        with patch("ue_agent_kit.agent_workflow._is_reparse_point", return_value=True):
            with self.assertRaisesRegex(WorkflowError, "Junction or symbolic link"):
                self.service._assert_runtime_boundaries()

    def test_fixed_project_revision_export_and_index_must_match(self) -> None:
        write_json(self.revision_export / "manifest.json", {"projectName": "OtherProject"})
        with self.assertRaisesRegex(WorkflowError, "Revision Export projectName"):
            PatchWorkflowService(
                FakeIndexService(),
                self.config,
                process_runner=self.runner,
                freshness_tracker=FakeFreshnessTracker(),
            )

        write_json(self.revision_export / "manifest.json", {"projectName": PROJECT})

        class WrongIndex(FakeIndexService):
            def check(self) -> dict[str, Any]:
                return {"ok": True, "projectKey": "OtherProject"}

        with self.assertRaisesRegex(WorkflowError, "SQLite projectKey"):
            PatchWorkflowService(
                WrongIndex(),
                self.config,
                process_runner=self.runner,
                freshness_tracker=FakeFreshnessTracker(),
            )

    def test_work_and_backup_roots_must_stay_inside_tool_root(self) -> None:
        outside = self.root / "outside"
        config = PatchWorkflowConfig(**{**self.config.__dict__, "work_root": outside})
        with self.assertRaisesRegex(WorkflowError, "work_root"):
            PatchWorkflowService(
                FakeIndexService(),
                config,
                process_runner=self.runner,
                freshness_tracker=FakeFreshnessTracker(),
            )


    def test_high_level_change_defaults_to_plan_and_can_run_dry_run(self) -> None:
        planned = self.service.prepare_high_level_change(
            tool_name="ue_set_asset_property",
            mode="Plan",
            asset_path=ASSET_PATH,
            operation="setAssetProperty",
            target={"propertyPath": "BoolValue"},
            value=True,
            description="High-level Plan",
        )
        self.assertEqual(planned["tool"], "ue_set_asset_property")
        self.assertEqual(planned["mode"], "Plan")
        self.assertEqual(planned["underlyingTool"], "ue_plan_patch")
        record = self.service._plans[planned["planId"]]
        operation = record.patch["assets"][0]["operations"][0]
        self.assertEqual(operation["operation"], "setAssetProperty")
        self.assertEqual(operation["target"], {"propertyPath": "BoolValue"})

        dry_run = self.service.prepare_high_level_change(
            tool_name="ue_set_asset_property",
            mode="DryRun",
            asset_path=ASSET_PATH,
            operation="setAssetProperty",
            target={"propertyPath": "BoolValue"},
            value=False,
        )
        self.assertEqual(dry_run["tool"], "ue_set_asset_property")
        self.assertEqual(dry_run["mode"], "DryRun")
        self.assertEqual(dry_run["underlyingTools"], ["ue_plan_patch", "ue_dry_run_patch"])
        self.assertTrue(dry_run["dryRunReceipt"].startswith("dry_"))
        self.assertTrue(dry_run["reportId"].startswith("report_"))
        another_dry_run = self.service.prepare_high_level_change(
            tool_name="ue_set_asset_property",
            mode="DryRun",
            asset_path=ASSET_PATH,
            operation="setAssetProperty",
            target={"propertyPath": "BoolValue"},
            value=True,
        )
        self.assertNotEqual(dry_run["reportId"], another_dry_run["reportId"])

        with self.assertRaisesRegex(ValueError, "Plan or DryRun"):
            self.service.prepare_high_level_change(
                tool_name="ue_set_asset_property",
                mode="Commit",  # type: ignore[arg-type]
                asset_path=ASSET_PATH,
                operation="setAssetProperty",
                target={"propertyPath": "BoolValue"},
                value=True,
            )

    def test_dry_run_rechecks_freshness_after_plan_creation(self) -> None:
        plan = self.service.plan_patch(
            asset_path=ASSET_PATH,
            operation="setAssetProperty",
            target={"propertyPath": "BoolValue"},
            value=True,
        )
        call_count = len(self.runner.calls)
        self.freshness.state = "stale"
        with self.assertRaises(WorkflowError) as stale:
            self.service.dry_run_patch(plan["planId"])
        self.assertEqual(stale.exception.code, "index-stale")
        self.assertEqual(len(self.runner.calls), call_count)

    def test_stored_plan_revision_conflict_has_specific_code(self) -> None:
        plan = self.service.plan_patch(
            asset_path=ASSET_PATH,
            operation="setAssetProperty",
            target={"propertyPath": "BoolValue"},
            value=True,
        )
        canonical_path = self.revision_export / "canonical" / "asset.json"
        canonical = json.loads(canonical_path.read_text(encoding="utf-8"))
        canonical["revision"]["value"] = AFTER_REVISION
        write_json(canonical_path, canonical)
        with self.assertRaises(WorkflowError) as conflict:
            self.service.dry_run_patch(plan["planId"])
        self.assertEqual(conflict.exception.code, "revision-conflict")
        self.assertIn("revision-conflict", conflict.exception.details["issueCodes"])

    def test_unreal_crash_is_classified_with_sanitized_diagnostics(self) -> None:
        plan = self.service.plan_patch(
            asset_path=ASSET_PATH,
            operation="setAssetProperty",
            target={"propertyPath": "BoolValue"},
            value=True,
        )

        def crash_runner(arguments: list[str], cwd: Path, timeout_seconds: int) -> ProcessResult:
            del arguments, cwd, timeout_seconds
            return ProcessResult(
                -1073741819,
                f"Fatal error: failed below {self.tool_root}",
                "Unhandled Exception: EXCEPTION_ACCESS_VIOLATION",
            )

        self.service._runner = crash_runner
        with self.assertRaises(WorkflowError) as crashed:
            self.service.dry_run_patch(plan["planId"])
        self.assertEqual(crashed.exception.code, "ue-process-crashed")
        details = crashed.exception.details
        self.assertEqual(details["stage"], "patch-dry-run")
        self.assertTrue(details["diagnosticId"].startswith("diag_"))
        self.assertTrue(details["reportId"].startswith("report_"))
        self.assertNotIn(str(self.tool_root), json.dumps(details, ensure_ascii=False))

    def test_timeout_and_invalid_report_are_separate_errors(self) -> None:
        plan = self.service.plan_patch(
            asset_path=ASSET_PATH,
            operation="setAssetProperty",
            target={"propertyPath": "BoolValue"},
            value=True,
        )

        def timeout_runner(arguments: list[str], cwd: Path, timeout_seconds: int) -> ProcessResult:
            del cwd
            raise subprocess.TimeoutExpired(
                cmd=arguments,
                timeout=timeout_seconds,
                output="partial stdout",
                stderr="partial stderr",
            )

        self.service._runner = timeout_runner
        with self.assertRaises(WorkflowError) as timed_out:
            self.service.dry_run_patch(plan["planId"])
        self.assertEqual(timed_out.exception.code, "workflow-timeout")
        self.assertEqual(timed_out.exception.details["stage"], "patch-dry-run")

        def invalid_report_runner(arguments: list[str], cwd: Path, timeout_seconds: int) -> ProcessResult:
            del cwd, timeout_seconds
            _, values = FakeWorkflowRunner._arguments(arguments)
            report_path = Path(values["-Report"])
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text("{invalid", encoding="utf-8")
            return ProcessResult(0, "", "")

        self.service._runner = invalid_report_runner
        with self.assertRaises(WorkflowError) as invalid:
            self.service.dry_run_patch(plan["planId"])
        self.assertEqual(invalid.exception.code, "workflow-report-invalid")
        self.assertTrue(invalid.exception.details["reportId"].startswith("report_"))
        self.assertEqual(invalid.exception.details["stage"], "patch-dry-run")

    def test_missing_report_has_specific_error_and_report_id(self) -> None:
        plan = self.service.plan_patch(
            asset_path=ASSET_PATH,
            operation="setAssetProperty",
            target={"propertyPath": "BoolValue"},
            value=True,
        )
        self.service._runner = lambda arguments, cwd, timeout_seconds: ProcessResult(0, "", "")
        with self.assertRaises(WorkflowError) as missing:
            self.service.dry_run_patch(plan["planId"])
        self.assertEqual(missing.exception.code, "workflow-report-missing")
        self.assertTrue(missing.exception.details["reportId"].startswith("report_"))

    def test_undo_asset_property_live_passes_identity_and_returns_evidence(self) -> None:
        class LiveUndoService:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, Any]]] = []

            def call_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
                self.calls.append((method, params))
                return {
                    "action": "undo-asset-property-live",
                    "operation": "setAssetProperty",
                    "valueKind": "scalar",
                    "assetPath": ASSET_PATH,
                    "transactionId": "12345678-1234-1234-1234-123456789abc",
                    "changed": True,
                    "transactionRecorded": False,
                    "packageDirtyBefore": True,
                    "packageDirtyAfter": False,
                    "dirtyBefore": True,
                    "dirtyAfter": False,
                    "saved": False,
                    "beforeValue": True,
                    "afterValue": False,
                    "editorSessionId": "session-1",
                }

        bridge = LiveUndoService()
        service = PatchWorkflowService(
            FakeIndexService(),
            self.config,
            process_runner=self.runner,
            freshness_tracker=FakeFreshnessTracker(),
            live_editor_service=bridge,
        )
        result = service.undo_asset_property_live(
            ASSET_PATH,
            "12345678-1234-1234-1234-123456789abc",
            "session-1",
        )
        self.assertEqual(result["mode"], "LiveUndo")
        self.assertEqual(result["tool"], "ue_undo_asset_property_live")
        self.assertEqual(result["operation"], "setAssetProperty")
        self.assertEqual(result["valueKind"], "scalar")
        self.assertTrue(result["changed"])
        self.assertFalse(result["saved"])
        self.assertFalse(result["diskRevisionChanged"])
        self.assertEqual(
            bridge.calls,
            [
                (
                    "editor.undoAssetPropertyLive",
                    {
                        "assetPath": ASSET_PATH,
                        "transactionId": "12345678-1234-1234-1234-123456789abc",
                        "sessionId": "session-1",
                    },
                )
            ],
        )
        self.assertEqual(result["result"]["action"], "undo-asset-property-live")

    def test_discard_asset_property_live_passes_identity_and_returns_evidence(self) -> None:
        class LiveDiscardService:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, Any]]] = []

            def call_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
                self.calls.append((method, params))
                return {
                    "action": "discard-asset-property-live",
                    "operation": "setAssetStructuredProperty",
                    "valueKind": "structured",
                    "assetPath": STRUCTURED_ASSET_PATH,
                    "transactionId": "22345678-2234-2234-2234-223456789abc",
                    "changed": True,
                    "transactionRecorded": False,
                    "packageDirtyBefore": True,
                    "packageDirtyAfter": False,
                    "dirtyBefore": True,
                    "dirtyAfter": False,
                    "saved": False,
                    "beforeValue": {"StructValue": {"Count": 2}},
                    "afterValue": {"StructValue": {"Count": 1}},
                    "editorSessionId": "session-1",
                }

        bridge = LiveDiscardService()
        service = PatchWorkflowService(
            FakeIndexService(),
            self.config,
            process_runner=self.runner,
            freshness_tracker=FakeFreshnessTracker(),
            live_editor_service=bridge,
        )
        result = service.discard_asset_property_live(
            STRUCTURED_ASSET_PATH,
            "22345678-2234-2234-2234-223456789abc",
            "session-1",
        )
        self.assertEqual(result["mode"], "LiveDiscard")
        self.assertEqual(result["tool"], "ue_discard_asset_property_live")
        self.assertEqual(result["operation"], "setAssetStructuredProperty")
        self.assertEqual(result["valueKind"], "structured")
        self.assertEqual(
            bridge.calls,
            [
                (
                    "editor.discardAssetPropertyLive",
                    {
                        "assetPath": STRUCTURED_ASSET_PATH,
                        "transactionId": "22345678-2234-2234-2234-223456789abc",
                        "sessionId": "session-1",
                    },
                )
            ],
        )

    def test_live_write_revert_guards_and_validation(self) -> None:
        class LiveStubBridge:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, Any]]] = []

            def call_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
                self.calls.append((method, params))
                return {}

        bridge = LiveStubBridge()
        service = PatchWorkflowService(
            FakeIndexService(),
            self.config,
            process_runner=self.runner,
            freshness_tracker=FakeFreshnessTracker(),
            live_editor_service=bridge,
        )
        with self.assertRaises(WorkflowError) as invalid_id:
            service.undo_asset_property_live(ASSET_PATH, "not-a-guid", "session-1")
        self.assertEqual(invalid_id.exception.code, "live-editor-write-undo-invalid-transaction-id")
        self.assertEqual(bridge.calls, [])

        with self.assertRaises(WorkflowError) as invalid_path:
            service.discard_asset_property_live("/Game/NotAnObjectPath", TRANSACTION_ID, "session-1")
        self.assertEqual(invalid_path.exception.code, "snapshot-refresh-invalid-asset")

        with self.assertRaises(WorkflowError) as missing_session:
            service.undo_asset_property_live(ASSET_PATH, TRANSACTION_ID, "")
        self.assertEqual(missing_session.exception.code, "live-editor-write-undo-session-required")

        disabled_config = PatchWorkflowConfig(
            tool_root=self.config.tool_root,
            engine_root=self.config.engine_root,
            project_path=self.config.project_path,
            policy_path=self.config.policy_path,
            revision_export=self.config.revision_export,
            work_root=self.config.work_root,
            backup_root=self.config.backup_root,
            commit_enabled=False,
        )
        disabled_service = PatchWorkflowService(
            FakeIndexService(),
            disabled_config,
            process_runner=self.runner,
            freshness_tracker=FakeFreshnessTracker(),
            live_editor_service=bridge,
        )
        with self.assertRaises(WorkflowError) as disabled:
            disabled_service.undo_asset_property_live(ASSET_PATH, TRANSACTION_ID, "session-1")
        self.assertEqual(disabled.exception.code, "live-editor-write-disabled")

        no_live_service = PatchWorkflowService(
            FakeIndexService(),
            self.config,
            process_runner=self.runner,
            freshness_tracker=FakeFreshnessTracker(),
            live_editor_service=None,
        )
        with self.assertRaises(WorkflowError) as no_live:
            no_live_service.discard_asset_property_live(ASSET_PATH, TRANSACTION_ID, "session-1")
        self.assertEqual(no_live.exception.code, "live-editor-required")

    def test_live_write_revert_propagates_bridge_rejections(self) -> None:
        class LiveRejectingBridge:
            def call_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
                del method, params
                raise LiveEditorError(
                    "live-editor-write-undo-stack-mismatch",
                    "Other Editor changes are on top of the live write; undo them first or re-plan the write.",
                )

        service = PatchWorkflowService(
            FakeIndexService(),
            self.config,
            process_runner=self.runner,
            freshness_tracker=FakeFreshnessTracker(),
            live_editor_service=LiveRejectingBridge(),
        )
        with self.assertRaises(WorkflowError) as stack:
            service.undo_asset_property_live(ASSET_PATH, TRANSACTION_ID, "session-1")
        self.assertEqual(stack.exception.code, "live-editor-write-undo-stack-mismatch")

    class ClosedLoopLiveService:
        def __init__(self, dirty: bool = True, on_save: Any = None) -> None:
            self.dirty = dirty
            self.on_save = on_save
            self.calls: list[tuple[str, dict[str, Any]]] = []

        def status(self) -> dict[str, Any]:
            return {"state": "available", "pieState": "stopped", "sessionId": "session-1", "processId": 1234}

        def call_method(self, method: str, params: dict[str, Any], **_: Any) -> dict[str, Any]:
            self.calls.append((method, params))
            if method == "editor.applyAssetPropertyLive":
                return {
                    "action": "apply-asset-property-live",
                    "operation": "setAssetProperty",
                    "assetPath": ASSET_PATH,
                    "valueKind": "scalar",
                    "beforeValue": False,
                    "afterValue": True,
                    "changed": True,
                    "transactionRecorded": True,
                    "transactionId": TRANSACTION_ID,
                    "transactionTitle": "UE Agent Kit: Set Asset Property",
                    "assetOpen": True,
                    "loadedByBridge": False,
                    "packageDirtyBefore": False,
                    "packageDirtyAfter": True,
                    "dirtyBefore": False,
                    "dirtyAfter": True,
                    "saved": False,
                    "editorSessionId": "session-1",
                }
            if method in {"editor.undoAssetPropertyLive", "editor.discardAssetPropertyLive"}:
                self.dirty = False
                return {
                    "action": "undo-asset-property-live" if method == "editor.undoAssetPropertyLive" else "discard-asset-property-live",
                    "operation": "setAssetProperty",
                    "valueKind": "scalar",
                    "assetPath": ASSET_PATH,
                    "transactionId": TRANSACTION_ID,
                    "changed": True,
                    "transactionRecorded": False,
                    "packageDirtyBefore": True,
                    "packageDirtyAfter": False,
                    "dirtyBefore": True,
                    "dirtyAfter": False,
                    "saved": False,
                    "beforeValue": True,
                    "afterValue": False,
                    "editorSessionId": "session-1",
                }
            if method == "editor.saveAuthorizedAsset":
                if self.on_save is not None:
                    self.on_save()
                self.dirty = False
                return {
                    "action": "save-authorized-asset",
                    "assetPath": ASSET_PATH,
                    "saved": True,
                    "verified": True,
                    "editorSessionId": "session-1",
                }
            raise AssertionError(f"unexpected bridge method: {method}")

        def call_tool(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
            if tool_name != "ue_inspect_asset_live" or params != {"assetPath": ASSET_PATH}:
                raise AssertionError(f"unexpected Live Editor tool call: {tool_name} {params}")
            return {
                "ok": True,
                "result": {
                    "assetRegistry": {"found": True, "classPath": ASSET_CLASS},
                    "memory": {
                        "loaded": True,
                        "packageDirty": self.dirty,
                        "loadedByBridge": False,
                        "openInAssetEditor": True,
                    },
                },
            }

    def _apply_scalar_live_write(self, service: Any, bridge: Any) -> dict[str, Any]:
        plan = service.plan_patch(
            asset_path=ASSET_PATH,
            operation="setAssetProperty",
            target={"propertyPath": "BoolValue"},
            value=True,
            description="Live closed loop test",
        )
        self.assertTrue(plan["ok"])
        applied = service.apply_asset_property_live(plan["planId"], f"LIVE APPLY {plan['planId']}")
        self.assertTrue(applied["changed"])
        self.assertTrue(applied["liveApplyReceipt"].startswith("live_"))
        self.assertEqual(bridge.calls[0][0], "editor.applyAssetPropertyLive")
        return applied

    def test_live_write_verify_reports_unsaved_state_with_undo_available(self) -> None:
        bridge = AgentWorkflowTests.ClosedLoopLiveService(dirty=True)
        service = PatchWorkflowService(
            FakeIndexService(),
            self.config,
            process_runner=self.runner,
            freshness_tracker=self.freshness,
            live_editor_service=bridge,
        )
        applied = self._apply_scalar_live_write(service, bridge)
        verified = service.verify_live_write(ASSET_PATH)
        self.assertEqual(verified["mode"], "LiveVerify")
        self.assertEqual(verified["state"], "not-saved")
        self.assertEqual(verified["liveApplyReceipt"], applied["liveApplyReceipt"])
        self.assertEqual(verified["planId"], applied["planId"])
        self.assertEqual(verified["transactionId"], TRANSACTION_ID)
        self.assertTrue(verified["undoAvailable"])
        self.assertFalse(verified["saved"])
        self.assertFalse(verified["verified"])
        self.assertEqual(verified["diskRevision"], BEFORE_REVISION)
        self.assertFalse(verified["memoryRecorded"])
        evidence = verified["memoryTaskEvidence"]["arguments"]
        self.assertEqual(evidence["task_key"], f"live-write:{applied['planId']}")
        self.assertEqual(evidence["outcome"], "cancelled")
        self.assertEqual(evidence["revision_set"][0]["revision"], BEFORE_REVISION)
        self.assertTrue(evidence["patch_details"]["undoAvailable"])
        self.assertFalse(evidence["validation_evidence_details"]["independentReload"])

    def test_live_write_undo_closes_pending_workflow_record(self) -> None:
        bridge = AgentWorkflowTests.ClosedLoopLiveService(dirty=True)
        service = PatchWorkflowService(
            FakeIndexService(),
            self.config,
            process_runner=self.runner,
            freshness_tracker=self.freshness,
            live_editor_service=bridge,
        )
        applied = self._apply_scalar_live_write(service, bridge)
        result = service.undo_asset_property_live(ASSET_PATH, TRANSACTION_ID, "session-1")
        self.assertEqual(result["mode"], "LiveUndo")
        self.assertNotIn(ASSET_PATH, service._live_apply_by_asset)
        self.assertNotIn(applied["liveApplyReceipt"], service._live_applies)
        self.assertFalse(
            (self.work_root / "live-write-journal" / f"{applied['liveApplyReceipt']}.json").exists()
        )
        with self.assertRaises(WorkflowError) as missing:
            service.verify_live_write(ASSET_PATH)
        self.assertEqual(missing.exception.code, "live-write-verify-not-found")

    def test_live_write_verify_closes_loop_after_authorized_save(self) -> None:
        fresh_bytes = b"x" * 64
        saved_bytes = b"y" * 64
        fresh_revision = "sha256:" + hashlib.sha256(fresh_bytes).hexdigest()
        saved_revision = "sha256:" + hashlib.sha256(saved_bytes).hexdigest()

        class ClosedLoopTracker(FakeFreshnessTracker):
            def inspect_asset(self, asset_path: str) -> dict[str, Any]:
                result = super().inspect_asset(asset_path)
                result["diskRevision"] = saved_revision if self.state == "stale" else fresh_revision
                return result

        tracker = ClosedLoopTracker()
        package_file = (
            self.project_path.parent
            / "Content"
            / "UEAgentKitWriteTests"
            / "ScalarRegression"
            / "DA_ScalarPatchTarget.uasset"
        )
        package_file.parent.mkdir(parents=True, exist_ok=True)
        package_file.write_bytes(fresh_bytes)
        bridge = AgentWorkflowTests.ClosedLoopLiveService(
            dirty=True,
            on_save=lambda: package_file.write_bytes(saved_bytes),
        )

        def catalog_runner(arguments: list[str], cwd: Path, timeout_seconds: int) -> ProcessResult:
            del cwd, timeout_seconds
            _, values = FakeWorkflowRunner._arguments(arguments)
            output = Path(values["-Output"])
            write_json(
                output / "manifest.json",
                {
                    "projectName": PROJECT,
                    "assetCount": 1,
                    "successCount": 1,
                    "failureCount": 0,
                    "assets": [
                        {
                            "assetPath": ASSET_PATH,
                            "success": True,
                            "jsonPath": str(output / "canonical" / "asset.json"),
                        }
                    ],
                },
            )
            write_json(
                output / "canonical" / "asset.json",
                {
                    "projectName": PROJECT,
                    "assetPath": ASSET_PATH,
                    "packageName": ASSET_PATH.split(".", 1)[0],
                    "assetClass": ASSET_CLASS,
                    "revision": {"available": True, "packageDirty": False, "value": saved_revision},
                    "assetDetails": {
                        "type": "data-asset",
                        "properties": [{"name": "BoolValue", "value": True}],
                    },
                },
            )
            return ProcessResult(0, "", "")

        service = PatchWorkflowService(
            FakeIndexService(),
            self.config,
            process_runner=catalog_runner,
            freshness_tracker=tracker,
            live_editor_service=bridge,
        )
        change_set_id = service.create_change_set(title="Closed loop")["changeSetId"]
        plan = service.plan_patch(
            asset_path=ASSET_PATH,
            operation="setAssetProperty",
            target={"propertyPath": "BoolValue"},
            value=True,
            description="Closed loop Change Set",
        )
        applied = service.apply_asset_property_live(
            plan["planId"],
            f"LIVE APPLY {plan['planId']}",
            change_set_id=change_set_id,
        )
        preview = service.save_authorized_asset(ASSET_PATH, change_set_id=change_set_id)
        saved = service.save_authorized_asset(
            ASSET_PATH,
            mode="Commit",
            save_receipt=preview["saveReceipt"],
            confirmation=f"SAVE {preview['saveReceipt']}",
            change_set_id=change_set_id,
        )
        self.assertTrue(saved["saved"])
        self.assertEqual(saved["liveApplyReceipt"], applied["liveApplyReceipt"])
        self.assertTrue(saved["liveWriteSaved"])

        saved_change_set = service.get_change_set(change_set_id)
        self.assertEqual(saved_change_set["status"], "saved")
        self.assertEqual(saved_change_set["operations"][0]["status"], "saved")

        verified = service.verify_live_write(ASSET_PATH, change_set_id=change_set_id)
        self.assertEqual(verified["state"], "verified")
        self.assertEqual(verified["liveApplyReceipt"], applied["liveApplyReceipt"])
        self.assertEqual(verified["planId"], applied["planId"])
        self.assertFalse(verified["undoAvailable"])
        self.assertTrue(verified["saved"])
        self.assertTrue(verified["verified"])
        self.assertEqual(verified["actualRevision"], saved_revision)
        self.assertEqual(verified["exportedValue"], True)
        self.assertEqual(verified["expectedValue"], True)
        evidence = verified["memoryTaskEvidence"]["arguments"]
        self.assertEqual(evidence["task_key"], f"live-write:{applied['planId']}")
        self.assertEqual(evidence["outcome"], "succeeded")
        self.assertEqual(evidence["revision_set"][0]["revision"], saved_revision)
        self.assertEqual(
            evidence["backup_manifest_ref"],
            f"backup-manifest:live-save:{preview['saveReceipt']}",
        )
        self.assertFalse(evidence["patch_details"]["undoAvailable"])
        self.assertTrue(evidence["validation_evidence_details"]["independentReload"])
        verified_change_set = service.get_change_set(change_set_id)
        self.assertEqual(verified_change_set["status"], "verified")
        self.assertEqual(verified_change_set["validation"]["state"], "verified")
        self.assertEqual(verified_change_set["saveState"]["state"], "saved")
        self.assertEqual(verified_change_set["operations"][0]["status"], "verified")
        self.assertNotIn(applied["liveApplyReceipt"], service._live_applies)
        self.assertFalse(
            (self.work_root / "live-write-journal" / f"{applied['liveApplyReceipt']}.json").exists()
        )

    def test_live_write_journal_failure_does_not_hide_successful_editor_write(self) -> None:
        bridge = AgentWorkflowTests.ClosedLoopLiveService(dirty=True)
        service = PatchWorkflowService(
            FakeIndexService(),
            self.config,
            process_runner=self.runner,
            freshness_tracker=self.freshness,
            live_editor_service=bridge,
        )
        plan = service.plan_patch(
            asset_path=ASSET_PATH,
            operation="setAssetProperty",
            target={"propertyPath": "BoolValue"},
            value=True,
            description="Journal failure remains truthful",
        )
        with patch("ue_agent_kit.agent_workflow._write_json_atomic", side_effect=OSError("disk full")):
            applied = service.apply_asset_property_live(plan["planId"], f"LIVE APPLY {plan['planId']}")
        self.assertTrue(applied["changed"])
        self.assertFalse(applied["journalPersisted"])
        self.assertIn(applied["liveApplyReceipt"], service._live_applies)
        status = service.status()
        self.assertEqual(status["publishedVersion"], "0.7.0")
        self.assertEqual(status["developmentLine"], "0.7.0")
        self.assertEqual(status["liveWriteJournal"]["journalErrorCount"], 1)

    def test_live_write_journal_recovers_and_closes_exact_receipt(self) -> None:
        bridge = AgentWorkflowTests.ClosedLoopLiveService(dirty=True)
        service = PatchWorkflowService(
            FakeIndexService(),
            self.config,
            process_runner=self.runner,
            freshness_tracker=self.freshness,
            live_editor_service=bridge,
        )
        applied = self._apply_scalar_live_write(service, bridge)
        receipt = applied["liveApplyReceipt"]
        journal = self.work_root / "live-write-journal" / f"{receipt}.json"
        self.assertTrue(journal.is_file())

        recovered = PatchWorkflowService(
            FakeIndexService(),
            self.config,
            process_runner=self.runner,
            freshness_tracker=FakeFreshnessTracker(),
            live_editor_service=bridge,
        )
        self.assertIn(receipt, recovered._live_applies)
        self.assertEqual(recovered.status()["liveWriteJournal"]["pendingRecordCount"], 1)
        self.assertEqual(recovered.status()["liveWriteJournal"]["recoveredRecordCount"], 1)
        pending = recovered.verify_live_write(ASSET_PATH, receipt)
        self.assertEqual(pending["state"], "not-saved")
        self.assertEqual(pending["liveApplyReceipt"], receipt)

        recovered.undo_asset_property_live(ASSET_PATH, TRANSACTION_ID, "session-1")
        self.assertNotIn(receipt, recovered._live_applies)
        self.assertFalse(journal.exists())

    def test_live_write_exact_receipt_selects_older_same_asset_record(self) -> None:
        bridge = AgentWorkflowTests.ClosedLoopLiveService(dirty=True)
        service = PatchWorkflowService(
            FakeIndexService(),
            self.config,
            process_runner=self.runner,
            freshness_tracker=self.freshness,
            live_editor_service=bridge,
        )
        first = self._apply_scalar_live_write(service, bridge)
        second = self._apply_scalar_live_write(service, bridge)
        self.assertNotEqual(first["liveApplyReceipt"], second["liveApplyReceipt"])
        self.assertEqual(service._live_apply_by_asset[ASSET_PATH], second["liveApplyReceipt"])

        selected = service.verify_live_write(ASSET_PATH, first["liveApplyReceipt"])
        self.assertEqual(selected["liveApplyReceipt"], first["liveApplyReceipt"])
        latest = service.verify_live_write(ASSET_PATH)
        self.assertEqual(latest["liveApplyReceipt"], second["liveApplyReceipt"])

    def test_live_write_verify_guards_and_value_mismatch(self) -> None:
        bridge = AgentWorkflowTests.ClosedLoopLiveService(dirty=True)
        service = PatchWorkflowService(
            FakeIndexService(),
            self.config,
            process_runner=self.runner,
            freshness_tracker=self.freshness,
            live_editor_service=bridge,
        )
        with self.assertRaises(WorkflowError) as missing:
            service.verify_live_write(ASSET_PATH)
        self.assertEqual(missing.exception.code, "live-write-verify-not-found")

        self._apply_scalar_live_write(service, bridge)
        with self.assertRaises(WorkflowError) as not_saved:
            service.verify_live_write("/Game/UEAgentKitWriteTests/ScalarRegression/DA_Other.DA_Other")
        self.assertEqual(not_saved.exception.code, "live-write-verify-not-found")

        disabled_config = PatchWorkflowConfig(
            tool_root=self.config.tool_root,
            engine_root=self.config.engine_root,
            project_path=self.config.project_path,
            policy_path=self.config.policy_path,
            revision_export=self.config.revision_export,
            work_root=self.config.work_root,
            backup_root=self.config.backup_root,
            commit_enabled=False,
        )
        disabled_service = PatchWorkflowService(
            FakeIndexService(),
            disabled_config,
            process_runner=self.runner,
            freshness_tracker=FakeFreshnessTracker(),
            live_editor_service=bridge,
        )
        with self.assertRaises(WorkflowError) as disabled:
            disabled_service.verify_live_write(ASSET_PATH)
        self.assertEqual(disabled.exception.code, "live-editor-write-disabled")

        no_live_service = PatchWorkflowService(
            FakeIndexService(),
            self.config,
            process_runner=self.runner,
            freshness_tracker=FakeFreshnessTracker(),
            live_editor_service=None,
        )
        with self.assertRaises(WorkflowError) as no_live:
            no_live_service.verify_live_write(ASSET_PATH)
        self.assertEqual(no_live.exception.code, "live-editor-required")

        tracker = FakeFreshnessTracker()

        def mismatched_runner(arguments: list[str], cwd: Path, timeout_seconds: int) -> ProcessResult:
            del cwd, timeout_seconds
            _, values = FakeWorkflowRunner._arguments(arguments)
            output = Path(values["-Output"])
            write_json(output / "manifest.json", {"projectName": PROJECT, "failureCount": 0})
            write_json(
                output / "canonical" / "asset.json",
                {
                    "projectName": PROJECT,
                    "assetPath": ASSET_PATH,
                    "assetClass": ASSET_CLASS,
                    "revision": {"available": True, "packageDirty": False, "value": AFTER_REVISION},
                    "assetDetails": {
                        "type": "data-asset",
                        "properties": [{"name": "BoolValue", "value": False}],
                    },
                },
            )
            return ProcessResult(0, "", "")

        saved_service = PatchWorkflowService(
            FakeIndexService(),
            self.config,
            process_runner=mismatched_runner,
            freshness_tracker=tracker,
            live_editor_service=bridge,
        )
        applied = self._apply_scalar_live_write(saved_service, bridge)
        tracker.mark_commit(ASSET_PATH, BEFORE_REVISION, AFTER_REVISION)
        saved_service._live_applies[applied["liveApplyReceipt"]].saved = True
        bridge.dirty = False
        with self.assertRaises(WorkflowError) as value_mismatch:
            saved_service.verify_live_write(ASSET_PATH)
        self.assertEqual(value_mismatch.exception.code, "live-write-verify-value-mismatch")

    def _change_set_service(self, bridge: Any) -> PatchWorkflowService:
        return PatchWorkflowService(
            FakeIndexService(),
            self.config,
            process_runner=self.runner,
            freshness_tracker=self.freshness,
            live_editor_service=bridge,
        )

    def _bound_change_set(
        self,
        service: PatchWorkflowService,
        *,
        title: str = "Test Change Set",
        task_id: str = "",
    ) -> str:
        created = service.create_change_set(title=title, task_id=task_id)
        self.assertTrue(created["ok"])
        self.assertTrue(created["changeSetId"].startswith("cs_"))
        self.assertEqual(created["status"], "planned")
        return created["changeSetId"]

    def _apply_bound_change_set(
        self,
        service: PatchWorkflowService,
        change_set_id: str,
        *,
        description: str = "Change Set binding",
    ) -> dict[str, Any]:
        plan = service.plan_patch(
            asset_path=ASSET_PATH,
            operation="setAssetProperty",
            target={"propertyPath": "BoolValue"},
            value=True,
            description=description,
        )
        return service.apply_asset_property_live(
            plan["planId"],
            f"LIVE APPLY {plan['planId']}",
            change_set_id=change_set_id,
        )

    def test_change_set_create_is_journaled_and_reported(self) -> None:
        service = self._change_set_service(AgentWorkflowTests.ClosedLoopLiveService(dirty=True))
        created = service.create_change_set(title="Weapon diagnostic", task_id="task_weapon-diagnostic")
        change_set_id = created["changeSetId"]
        journal = self.work_root / "change-sets" / f"{change_set_id}.json"
        self.assertTrue(journal.is_file())
        self.assertEqual(created["taskId"], "task_weapon-diagnostic")
        self.assertEqual(created["editorSessionId"], "session-1")
        self.assertEqual(created["title"], "Weapon diagnostic")
        self.assertEqual(service.status()["liveWriteJournal"]["changeSetCount"], 1)

        details = service.get_change_set(change_set_id)
        self.assertEqual(details["tool"], "ue_get_change_set")
        self.assertEqual(details["status"], "planned")
        self.assertEqual(details["operationCount"], 0)
        self.assertEqual(details["affectedAssets"], [])
        self.assertEqual(details["validation"]["state"], "not-run")
        self.assertEqual(details["saveState"]["state"], "unsaved")

    def test_change_set_apply_binds_durable_operation(self) -> None:
        bridge = AgentWorkflowTests.ClosedLoopLiveService(dirty=True)
        service = self._change_set_service(bridge)
        change_set_id = self._bound_change_set(service)
        applied = self._apply_bound_change_set(service, change_set_id)
        self.assertTrue(applied["changed"])
        self.assertTrue(applied["changeSetBound"])

        details = service.get_change_set(change_set_id)
        self.assertEqual(details["status"], "applied")
        self.assertEqual(details["operationCount"], 1)
        self.assertEqual(details["activeReceiptCount"], 1)
        operation = details["operations"][0]
        self.assertEqual(operation["receipt"], applied["liveApplyReceipt"])
        self.assertEqual(operation["assetPath"], ASSET_PATH)
        self.assertEqual(operation["operation"], "setAssetProperty")
        self.assertEqual(operation["transactionId"], TRANSACTION_ID)
        self.assertEqual(operation["editorSessionId"], "session-1")
        self.assertEqual(operation["status"], "applied")
        self.assertEqual(details["affectedAssets"], [ASSET_PATH])
        self.assertEqual(details["transactionIds"], [TRANSACTION_ID])

    def test_change_set_apply_rejects_unknown_set_before_bridge_call(self) -> None:
        bridge = AgentWorkflowTests.ClosedLoopLiveService(dirty=True)
        service = self._change_set_service(bridge)
        plan = service.plan_patch(
            asset_path=ASSET_PATH,
            operation="setAssetProperty",
            target={"propertyPath": "BoolValue"},
            value=True,
            description="Change Set unknown",
        )
        with self.assertRaises(WorkflowError) as missing:
            service.apply_asset_property_live(
                plan["planId"],
                f"LIVE APPLY {plan['planId']}",
                change_set_id="cs_does-not-exist",
            )
        self.assertEqual(missing.exception.code, "change-set-not-found")
        self.assertEqual(bridge.calls, [])

    def test_change_set_apply_noop_binds_nothing(self) -> None:
        class NoopWriteService:
            def call_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
                del method, params
                return {
                    "changed": False,
                    "transactionRecorded": False,
                    "saved": False,
                }

        service = self._change_set_service(NoopWriteService())
        change_set_id = self._bound_change_set(service)
        applied = self._apply_bound_change_set(service, change_set_id, description="Change Set noop")
        self.assertFalse(applied["changed"])
        self.assertEqual(applied["liveApplyReceipt"], "")
        self.assertFalse(applied["changeSetBound"])
        self.assertEqual(service.get_change_set(change_set_id)["status"], "planned")

    def test_change_set_undo_preserves_terminal_history(self) -> None:
        bridge = AgentWorkflowTests.ClosedLoopLiveService(dirty=True)
        service = self._change_set_service(bridge)
        change_set_id = self._bound_change_set(service)
        applied = self._apply_bound_change_set(service, change_set_id, description="Change Set undo")
        undone = service.undo_asset_property_live(
            ASSET_PATH,
            TRANSACTION_ID,
            "session-1",
            change_set_id=change_set_id,
        )
        self.assertTrue(undone["changeSetUpdated"])
        self.assertEqual(undone["changeSetOperationStatus"], "undone")
        details = service.get_change_set(change_set_id)
        self.assertEqual(details["status"], "undone")
        self.assertEqual(details["receiptCount"], 1)
        self.assertEqual(details["activeReceiptCount"], 0)
        self.assertEqual(details["operations"][0]["status"], "undone")
        self.assertFalse(
            (self.work_root / "live-write-journal" / f"{applied['liveApplyReceipt']}.json").exists()
        )

    def test_change_set_discard_preserves_terminal_history(self) -> None:
        bridge = AgentWorkflowTests.ClosedLoopLiveService(dirty=True)
        service = self._change_set_service(bridge)
        change_set_id = self._bound_change_set(service)
        self._apply_bound_change_set(service, change_set_id, description="Change Set discard")
        discarded = service.discard_asset_property_live(
            ASSET_PATH,
            TRANSACTION_ID,
            "session-1",
            change_set_id=change_set_id,
        )
        self.assertTrue(discarded["changeSetUpdated"])
        details = service.get_change_set(change_set_id)
        self.assertEqual(details["status"], "discarded")
        self.assertEqual(details["operations"][0]["status"], "discarded")

    def test_change_set_revert_rejects_non_member_before_bridge_call(self) -> None:
        bridge = AgentWorkflowTests.ClosedLoopLiveService(dirty=True)
        service = self._change_set_service(bridge)
        change_set_id = self._bound_change_set(service)
        self._apply_scalar_live_write(service, bridge)
        self.assertEqual(len(bridge.calls), 1)
        with self.assertRaises(WorkflowError) as non_member:
            service.undo_asset_property_live(
                ASSET_PATH,
                TRANSACTION_ID,
                "session-1",
                change_set_id=change_set_id,
            )
        self.assertEqual(non_member.exception.code, "change-set-transaction-not-member")
        self.assertEqual(len(bridge.calls), 1)

    def test_change_set_verify_not_saved_keeps_applied_state(self) -> None:
        bridge = AgentWorkflowTests.ClosedLoopLiveService(dirty=True)
        service = self._change_set_service(bridge)
        change_set_id = self._bound_change_set(service)
        applied = self._apply_bound_change_set(service, change_set_id, description="Change Set verify")
        verified = service.verify_live_write(ASSET_PATH, change_set_id=change_set_id)
        self.assertEqual(verified["state"], "not-saved")
        self.assertEqual(verified["liveApplyReceipt"], applied["liveApplyReceipt"])
        self.assertEqual(service.get_change_set(change_set_id)["status"], "applied")

    def test_change_set_save_requires_member_asset(self) -> None:
        bridge = AgentWorkflowTests.ClosedLoopLiveService(dirty=True)
        service = self._change_set_service(bridge)
        change_set_id = self._bound_change_set(service)
        self._apply_scalar_live_write(service, bridge)
        with self.assertRaises(WorkflowError) as non_member:
            service.save_authorized_asset(ASSET_PATH, change_set_id=change_set_id)
        self.assertEqual(non_member.exception.code, "change-set-transaction-not-member")

        self._apply_bound_change_set(service, change_set_id, description="Change Set save")
        preview = service.save_authorized_asset(ASSET_PATH, change_set_id=change_set_id)
        self.assertEqual(preview["mode"], "Preview")
        self.assertEqual(preview["changeSetId"], change_set_id)

    def test_change_set_journal_recovers_matching_session(self) -> None:
        bridge = AgentWorkflowTests.ClosedLoopLiveService(dirty=True)
        service = self._change_set_service(bridge)
        change_set_id = self._bound_change_set(service)
        applied = self._apply_bound_change_set(service, change_set_id, description="Change Set recovery")
        recovered = self._change_set_service(bridge)
        details = recovered.get_change_set(change_set_id)
        self.assertEqual(details["status"], "applied")
        self.assertEqual(details["activeReceiptCount"], 1)
        self.assertEqual(details["operations"][0]["receipt"], applied["liveApplyReceipt"])

    def test_change_set_restart_marks_unprovable_state_unknown(self) -> None:
        bridge = AgentWorkflowTests.ClosedLoopLiveService(dirty=True)
        service = self._change_set_service(bridge)
        change_set_id = self._bound_change_set(service)
        self._apply_bound_change_set(service, change_set_id, description="Unknown recovery")

        class UnavailableBridge(AgentWorkflowTests.ClosedLoopLiveService):
            def status(self) -> dict[str, Any]:
                return {"state": "unavailable"}

        recovered = self._change_set_service(UnavailableBridge(dirty=True))
        details = recovered.get_change_set(change_set_id)
        self.assertEqual(details["status"], "unknown")
        self.assertEqual(details["operations"][0]["status"], "unknown")
        self.assertEqual(details["validation"]["state"], "unknown")
        self.assertEqual(details["saveState"]["state"], "unknown")

    def test_change_set_active_capacity_is_not_silently_pruned(self) -> None:
        service = self._change_set_service(AgentWorkflowTests.ClosedLoopLiveService(dirty=True))
        ids = [self._bound_change_set(service, title=f"Set {index}") for index in range(50)]
        with self.assertRaises(WorkflowError) as capacity:
            service.create_change_set(title="Set 51")
        self.assertEqual(capacity.exception.code, "change-set-capacity-reached")
        self.assertEqual(set(service._change_sets), set(ids))

    def test_change_set_terminal_record_can_be_pruned_for_capacity(self) -> None:
        bridge = AgentWorkflowTests.ClosedLoopLiveService(dirty=True)
        service = self._change_set_service(bridge)
        terminal_id = self._bound_change_set(service, title="Terminal")
        self._apply_bound_change_set(service, terminal_id)
        service.undo_asset_property_live(ASSET_PATH, TRANSACTION_ID, "session-1", change_set_id=terminal_id)
        for index in range(49):
            self._bound_change_set(service, title=f"Active {index}")
        replacement_id = self._bound_change_set(service, title="Replacement")
        self.assertNotIn(terminal_id, service._change_sets)
        self.assertIn(replacement_id, service._change_sets)
        self.assertEqual(len(service._change_sets), 50)

    def test_change_set_operation_cap_rejects_before_bridge_call(self) -> None:
        bridge = AgentWorkflowTests.ClosedLoopLiveService(dirty=True)
        service = self._change_set_service(bridge)
        change_set_id = self._bound_change_set(service)
        record = service._change_sets[change_set_id]
        now = "2026-08-01T00:00:00Z"
        record.operations = [
            ChangeSetOperationRecord(
                receipt=f"live_member_{index}",
                plan_id=f"plan_{index}",
                asset_path=f"/Game/Test/Asset{index}.Asset{index}",
                operation="setAssetProperty",
                transaction_id=f"transaction-{index}",
                editor_session_id="session-1",
                status="saved",
                created_at_utc=now,
                updated_at_utc=now,
                save_receipt=f"save_{index}",
            )
            for index in range(MAX_CHANGE_SET_RECEIPTS)
        ]
        service._persist_change_set(record)
        plan = service.plan_patch(
            asset_path=ASSET_PATH,
            operation="setAssetProperty",
            target={"propertyPath": "BoolValue"},
            value=True,
            description="Over capacity",
        )
        before_calls = len(bridge.calls)
        with self.assertRaises(WorkflowError) as full:
            service.apply_asset_property_live(
                plan["planId"],
                f"LIVE APPLY {plan['planId']}",
                change_set_id=change_set_id,
            )
        self.assertEqual(full.exception.code, "change-set-full")
        self.assertEqual(len(bridge.calls), before_calls)

    def test_change_set_unbound_live_write_response_has_no_change_set_keys(self) -> None:
        bridge = AgentWorkflowTests.ClosedLoopLiveService(dirty=True)
        service = self._change_set_service(bridge)
        applied = self._apply_scalar_live_write(service, bridge)
        self.assertNotIn("changeSetId", applied)
        undone = service.undo_asset_property_live(ASSET_PATH, TRANSACTION_ID, "session-1")
        self.assertNotIn("changeSetId", undone)
        self.assertEqual(service.status()["liveWriteJournal"]["changeSetCount"], 0)


if __name__ == "__main__":
    unittest.main()
