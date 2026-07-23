from __future__ import annotations

import json
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


class FakeFreshnessTracker:
    def __init__(self) -> None:
        self.state = "fresh"
        self.transitions: dict[str, dict[str, Any]] = {}

    def inspect_asset(self, asset_path: str) -> dict[str, Any]:
        return {
            "assetPath": asset_path,
            "state": self.state,
            "indexFresh": self.state == "fresh",
            "indexStale": self.state == "stale",
            "indexRevision": BEFORE_REVISION,
            "revisionExportRevision": BEFORE_REVISION,
            "diskRevision": BEFORE_REVISION if self.state == "fresh" else AFTER_REVISION,
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
                    "executorVersion": "0.5.0",
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
                    "executorVersion": "0.5.0",
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
        with self.assertRaisesRegex(WorkflowError, "rejected"):
            self.service.plan_patch(
                asset_path=ASSET_PATH,
                operation="setAssetProperty",
                target={"propertyPath": "ForbiddenValue"},
                value=True,
            )

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


if __name__ == "__main__":
    unittest.main()
