from __future__ import annotations

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


PROJECT = "我的项目"
ASSET_PATH = "/Game/UEAgentKitWriteTests/ScalarRegression/DA_ScalarPatchTarget.DA_ScalarPatchTarget"
ASSET_CLASS = "/Script/UEAgentKitEditor.UEAgentKitScalarWriteFixtureAsset"
BEFORE_REVISION = "sha256:" + "a" * 64
AFTER_REVISION = "sha256:" + "b" * 64


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8", newline="\r\n")


class FakeIndexService:
    def check(self) -> dict[str, Any]:
        return {"ok": True, "projectKey": PROJECT}

    def get_asset(self, asset_path: str, **_: Any) -> dict[str, Any]:
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



if __name__ == "__main__":
    unittest.main()
