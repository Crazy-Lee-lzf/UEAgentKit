from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ue_agent_kit.backups import (
    create_backup_manifest,
    rollback_backup,
    validate_rollback,
    verify_rollback_export,
)
from ue_agent_kit.cli import build_parser


def revision(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\r\n",
    )


class BackupRollbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / "TestProject.uproject"
        write_json(self.project, {})
        self.target = self.root / "Content" / "Test" / "Asset.uasset"
        self.target.parent.mkdir(parents=True)
        self.before_bytes = b"before-package-v1"
        self.after_bytes = b"after-package-v2"
        self.target.write_bytes(self.after_bytes)
        self.before_revision = revision(self.before_bytes)
        self.after_revision = revision(self.after_bytes)

        self.backup_root = self.root / "Backups"
        self.backup_root.mkdir()
        self.backup = self.backup_root / "commit-asset.uasset.bak"
        self.backup.write_bytes(self.before_bytes)

        self.patch_path = self.root / "patch.json"
        self.policy_path = self.root / "policy.json"
        self.report_path = self.root / "commit-report.json"
        self.patch = {
            "schemaVersion": "1.0",
            "patchId": "backup-test",
            "projectName": "TestProject",
            "assets": [
                {
                    "assetPath": "/Game/Test/Asset.Asset",
                    "expectedRevision": self.before_revision,
                    "expectedAssetClass": "/Script/Engine.DataTable",
                    "operations": [
                        {
                            "operationId": "set-cell",
                            "operation": "setDataTableCell",
                            "target": {"rowName": "Row", "fieldName": "Value"},
                            "value": "after",
                        }
                    ],
                }
            ],
        }
        self.policy = {
            "schemaVersion": "1.0",
            "validationEnabled": True,
            "commitEnabled": True,
            "allowedProjectNames": ["TestProject"],
            "allowedAssetRoots": ["/Game/Test"],
            "allowedReferenceRoots": [],
            "allowedReferenceClasses": [],
            "allowedOperations": ["setDataTableCell"],
            "allowedAssetClasses": ["/Script/Engine.DataTable"],
            "allowedAssetProperties": [],
            "allowedMaterialParameters": [],
            "allowedDataTableFields": [
                "/Script/Engine.DataTable#/Script/Test.Row#Value"
            ],
            "requireRevision": True,
            "rejectDirtyPackages": True,
            "maxAssetsPerPatch": 1,
            "maxOperationsPerAsset": 1,
            "maxValueBytes": 4096,
        }
        self.report = {
            "schemaVersion": "1.0",
            "executorVersion": "0.5.1",
            "mode": "Commit",
            "patchId": "backup-test",
            "projectName": "TestProject",
            "assetPath": "/Game/Test/Asset.Asset",
            "assetClass": "/Script/Engine.DataTable",
            "operation": "setDataTableCell",
            "target": {"rowName": "Row", "fieldName": "Value"},
            "rowStructPath": "/Script/Test.Row",
            "beforeValue": "before",
            "afterValue": "after",
            "beforeRevision": self.before_revision,
            "afterRevision": self.after_revision,
            "saved": True,
            "backupPath": str(self.backup),
        }
        write_json(self.patch_path, self.patch)
        write_json(self.policy_path, self.policy)
        write_json(self.report_path, self.report)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def create_manifest(self) -> Path:
        result = create_backup_manifest(
            self.patch_path,
            self.policy_path,
            self.report_path,
            self.backup_root,
        )
        self.assertTrue(result["valid"])
        return Path(result["manifestPath"])

    @staticmethod
    def error_codes(result: dict) -> set[str]:
        return {item["code"] for item in result.get("errors", [])}

    def test_cli_exposes_manifest_rollback_and_verification_commands(self) -> None:
        parser = build_parser()
        manifest = parser.parse_args(
            [
                "patch",
                "manifest",
                "--patch",
                "patch.json",
                "--policy",
                "policy.json",
                "--report",
                "commit.json",
                "--backup-root",
                "Backups",
            ]
        )
        self.assertEqual(manifest.patch_command, "manifest")
        rollback = parser.parse_args(
            [
                "patch",
                "rollback",
                "--manifest",
                "backup.manifest.json",
                "--policy",
                "policy.json",
                "--project",
                "Project.uproject",
                "--backup-root",
                "Backups",
                "--mode",
                "Commit",
            ]
        )
        self.assertEqual(rollback.patch_command, "rollback")
        self.assertEqual(rollback.mode, "Commit")
        verify = parser.parse_args(
            [
                "patch",
                "verify-rollback",
                "--rollback-report",
                "rollback.json",
                "--export",
                "Export",
            ]
        )
        self.assertEqual(verify.patch_command, "verify-rollback")

    def test_create_backup_manifest_records_exact_revisions(self) -> None:
        manifest_path = self.create_manifest()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertTrue(manifest_path.is_relative_to(self.backup_root))
        self.assertEqual(manifest["beforeRevision"], self.before_revision)
        self.assertEqual(manifest["afterRevision"], self.after_revision)
        self.assertEqual(manifest["backup"]["revision"], self.before_revision)
        self.assertEqual(
            manifest["authorizationKey"],
            "/Script/Engine.DataTable#/Script/Test.Row#Value",
        )

    def test_manifest_derives_all_data_table_row_field_authorizations(self) -> None:
        fields = {"Tag": "UEAgentKit.Atomic.Row", "DevComment": "Atomic"}
        self.patch["assets"][0]["operations"][0] = {
            "operationId": "set-row-fields",
            "operation": "setDataTableRowFields",
            "target": {"rowName": "Row"},
            "value": fields,
        }
        self.policy["allowedOperations"] = ["setDataTableRowFields"]
        self.policy["allowedDataTableFields"] = [
            "/Script/Engine.DataTable#/Script/Test.Row#Tag",
            "/Script/Engine.DataTable#/Script/Test.Row#DevComment",
        ]
        self.report.update(
            {
                "operation": "setDataTableRowFields",
                "target": {"rowName": "Row"},
                "rowStructPath": "/Script/Test.Row",
                "beforeValues": {"Tag": "Before", "DevComment": "Before"},
                "afterValues": fields,
            }
        )
        write_json(self.patch_path, self.patch)
        write_json(self.policy_path, self.policy)
        write_json(self.report_path, self.report)

        manifest = json.loads(self.create_manifest().read_text(encoding="utf-8"))
        self.assertEqual(
            manifest["operations"][0]["authorizationKeys"],
            [
                "/Script/Engine.DataTable#/Script/Test.Row#DevComment",
                "/Script/Engine.DataTable#/Script/Test.Row#Tag",
            ],
        )

    def test_manifest_derives_all_added_data_table_row_authorizations(self) -> None:
        fields = {"Tag": "UEAgentKit.Row", "DevComment": "Added"}
        self.patch["assets"][0]["operations"][0] = {
            "operationId": "add-row",
            "operation": "addDataTableRow",
            "target": {"rowName": "AddedRow"},
            "value": fields,
        }
        self.policy["allowedOperations"] = ["addDataTableRow"]
        self.policy["allowedDataTableFields"] = [
            "/Script/Engine.DataTable#/Script/Test.Row#Tag",
            "/Script/Engine.DataTable#/Script/Test.Row#DevComment",
        ]
        self.report.update(
            {
                "operation": "addDataTableRow",
                "target": {"rowName": "AddedRow"},
                "rowStructPath": "/Script/Test.Row",
                "appliedValues": fields,
            }
        )
        write_json(self.patch_path, self.patch)
        write_json(self.policy_path, self.policy)
        write_json(self.report_path, self.report)

        manifest = json.loads(self.create_manifest().read_text(encoding="utf-8"))
        self.assertEqual(
            manifest["operations"][0]["authorizationKeys"],
            [
                "/Script/Engine.DataTable#/Script/Test.Row#DevComment",
                "/Script/Engine.DataTable#/Script/Test.Row#Tag",
            ],
        )

    def test_manifest_allows_data_table_row_remove_without_field_authorizations(self) -> None:
        self.patch["assets"][0]["operations"][0] = {
            "operationId": "remove-row",
            "operation": "removeDataTableRow",
            "target": {"rowName": "RemovedRow"},
        }
        self.policy["allowedOperations"] = ["removeDataTableRow"]
        self.policy["allowedDataTableFields"] = []
        self.report.update(
            {
                "operation": "removeDataTableRow",
                "target": {"rowName": "RemovedRow"},
                "rowStructPath": "/Script/Test.Row",
            }
        )
        write_json(self.patch_path, self.patch)
        write_json(self.policy_path, self.policy)
        write_json(self.report_path, self.report)

        manifest = json.loads(self.create_manifest().read_text(encoding="utf-8"))
        self.assertEqual(manifest["operations"][0]["authorizationKeys"], [])

    def test_backup_manifest_schema_declares_security_fields(self) -> None:
        schema_path = Path(__file__).resolve().parents[2] / "spec" / "backup-manifest.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        required = set(schema["required"])
        self.assertTrue(
            {
                "assetPath",
                "assetClass",
                "operation",
                "authorizationKey",
                "beforeRevision",
                "afterRevision",
                "packageKind",
                "backup",
                "source",
            }.issubset(required)
        )
        self.assertEqual(schema["properties"]["packageKind"]["const"], "single-uasset")
        self.assertEqual(schema["properties"]["operations"]["maxItems"], 32)
        self.assertIn("authorizationKeys", schema["$defs"]["manifestOperation"]["required"])

    def test_create_backup_manifest_rejects_target_mismatch(self) -> None:
        self.patch["assets"][0]["operations"][0]["target"]["fieldName"] = "OtherValue"
        write_json(self.patch_path, self.patch)
        with self.assertRaisesRegex(ValueError, "target values do not match"):
            create_backup_manifest(
                self.patch_path,
                self.policy_path,
                self.report_path,
                self.backup_root,
            )

    def test_create_backup_manifest_refuses_overwrite(self) -> None:
        manifest_path = self.create_manifest()
        with self.assertRaisesRegex(FileExistsError, "already exists"):
            create_backup_manifest(
                self.patch_path,
                self.policy_path,
                self.report_path,
                self.backup_root,
                output_path=manifest_path,
            )

    def test_rollback_dry_run_does_not_modify_package(self) -> None:
        manifest_path = self.create_manifest()
        result = rollback_backup(
            manifest_path,
            self.policy_path,
            self.project,
            self.backup_root,
        )
        self.assertTrue(result["valid"])
        self.assertFalse(result["willWriteDisk"])
        self.assertFalse(result["restored"])
        self.assertEqual(self.target.read_bytes(), self.after_bytes)

    def test_rollback_rejects_report_inside_project_content(self) -> None:
        manifest_path = self.create_manifest()
        with self.assertRaisesRegex(ValueError, "outside the Unreal project Content"):
            rollback_backup(
                manifest_path,
                self.policy_path,
                self.project,
                self.backup_root,
                report_path=self.target.parent / "rollback.json",
            )
        self.assertEqual(self.target.read_bytes(), self.after_bytes)

    def test_rollback_rejects_report_collision_with_manifest(self) -> None:
        manifest_path = self.create_manifest()
        with self.assertRaisesRegex(ValueError, "conflicts with the backup manifest"):
            rollback_backup(
                manifest_path,
                self.policy_path,
                self.project,
                self.backup_root,
                report_path=manifest_path,
            )
        self.assertEqual(self.target.read_bytes(), self.after_bytes)

    def test_rollback_commit_is_atomic_and_keeps_safety_backup(self) -> None:
        manifest_path = self.create_manifest()
        report_path = self.root / "rollback-report.json"
        result = rollback_backup(
            manifest_path,
            self.policy_path,
            self.project,
            self.backup_root,
            commit=True,
            report_path=report_path,
        )
        self.assertTrue(result["valid"])
        self.assertTrue(result["restored"])
        self.assertTrue(result["wroteDisk"])
        self.assertEqual(self.target.read_bytes(), self.before_bytes)
        safety = Path(result["preRollbackBackupPath"])
        receipt = Path(result["receiptPath"])
        self.assertEqual(safety.read_bytes(), self.after_bytes)
        self.assertTrue(receipt.is_file())
        self.assertTrue(report_path.is_file())
        self.assertEqual(result["afterRollbackRevision"], self.before_revision)

    def test_rollback_restores_safety_backup_when_audit_write_fails(self) -> None:
        manifest_path = self.create_manifest()
        with mock.patch(
            "ue_agent_kit.backups._write_json_atomic",
            side_effect=OSError("simulated audit write failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "restored automatically"):
                rollback_backup(
                    manifest_path,
                    self.policy_path,
                    self.project,
                    self.backup_root,
                    commit=True,
                )
        self.assertEqual(self.target.read_bytes(), self.after_bytes)
        safety_files = list((self.backup_root / "rollback-safety").glob("*.pre-rollback.bak"))
        self.assertEqual(len(safety_files), 1)
        self.assertEqual(safety_files[0].read_bytes(), self.after_bytes)
        receipts = list((self.backup_root / "rollback-receipts").glob("*.json"))
        self.assertEqual(receipts, [])

    def test_rollback_rejects_stale_current_revision(self) -> None:
        manifest_path = self.create_manifest()
        self.target.write_bytes(b"newer-package-v3")
        result = validate_rollback(
            manifest_path,
            self.policy_path,
            self.project,
            self.backup_root,
        )
        self.assertIn("current-revision-conflict", self.error_codes(result))

    def test_rollback_rejects_tampered_backup(self) -> None:
        manifest_path = self.create_manifest()
        self.backup.write_bytes(b"tampered-backup")
        result = validate_rollback(
            manifest_path,
            self.policy_path,
            self.project,
            self.backup_root,
        )
        self.assertIn("backup-revision-conflict", self.error_codes(result))

    def test_rollback_rejects_changed_policy(self) -> None:
        manifest_path = self.create_manifest()
        self.policy["allowedAssetRoots"] = ["/Game/Other"]
        write_json(self.policy_path, self.policy)
        result = validate_rollback(
            manifest_path,
            self.policy_path,
            self.project,
            self.backup_root,
        )
        codes = self.error_codes(result)
        self.assertIn("policy-revision-conflict", codes)
        self.assertIn("policy-not-authorized", codes)

    def test_rollback_rejects_manifest_outside_backup_root(self) -> None:
        manifest_path = self.create_manifest()
        outside = self.root / "outside-manifest.json"
        outside.write_bytes(manifest_path.read_bytes())
        result = validate_rollback(
            outside,
            self.policy_path,
            self.project,
            self.backup_root,
        )
        self.assertIn("manifest-outside-backup-root", self.error_codes(result))

    def test_rollback_rejects_asset_path_traversal(self) -> None:
        manifest_path = self.create_manifest()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["assetPath"] = "/Game/Test/../Outside.Outside"
        write_json(manifest_path, manifest)
        result = validate_rollback(
            manifest_path,
            self.policy_path,
            self.project,
            self.backup_root,
        )
        self.assertIn("asset-path-invalid", self.error_codes(result))

    def test_rollback_rejects_noncanonical_asset_path(self) -> None:
        manifest_path = self.create_manifest()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["assetPath"] = "/Game/Test/Asset"
        write_json(manifest_path, manifest)
        result = validate_rollback(
            manifest_path,
            self.policy_path,
            self.project,
            self.backup_root,
        )
        self.assertIn("asset-path-not-canonical", self.error_codes(result))

    def test_rollback_rejects_wrong_package_kind_and_backup_size(self) -> None:
        manifest_path = self.create_manifest()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["packageKind"] = "multi-file"
        manifest["backup"]["size"] += 1
        write_json(manifest_path, manifest)
        result = validate_rollback(
            manifest_path,
            self.policy_path,
            self.project,
            self.backup_root,
        )
        codes = self.error_codes(result)
        self.assertIn("package-kind-not-supported", codes)
        self.assertIn("backup-size-conflict", codes)

    def test_verify_rollback_export_checks_exact_package_revision(self) -> None:
        manifest_path = self.create_manifest()
        rollback_report = self.root / "rollback-report.json"
        rollback_backup(
            manifest_path,
            self.policy_path,
            self.project,
            self.backup_root,
            commit=True,
            report_path=rollback_report,
        )
        export_root = self.root / "Export"
        canonical = export_root / "canonical" / "Game" / "Test" / "Asset.json"
        write_json(
            canonical,
            {
                "projectName": "TestProject",
                "assetPath": "/Game/Test/Asset.Asset",
                "assetClass": "/Script/Engine.DataTable",
                "revision": {
                    "value": self.before_revision,
                    "packageDirty": False,
                },
            },
        )
        result = verify_rollback_export(rollback_report, export_root)
        self.assertTrue(result["verified"])
        value = json.loads(canonical.read_text(encoding="utf-8"))
        value["revision"]["value"] = self.after_revision
        write_json(canonical, value)
        rejected = verify_rollback_export(rollback_report, export_root)
        self.assertIn("rollback-verification-revision", self.error_codes(rejected))

    def test_multi_operation_manifest_records_each_authorization(self) -> None:
        second_operation = {
            "operationId": "set-other-cell",
            "operation": "setDataTableCell",
            "target": {"rowName": "Row", "fieldName": "OtherValue"},
            "value": "other-after",
        }
        self.patch["assets"][0]["operations"].append(second_operation)
        self.policy["maxOperationsPerAsset"] = 2
        self.policy["allowedDataTableFields"].append(
            "/Script/Engine.DataTable#/Script/Test.Row#OtherValue"
        )
        self.report.update(
            {
                "operation": "transaction",
                "target": {},
                "operationCount": 2,
                "operations": [
                    {
                        "operationId": "set-cell",
                        "operation": "setDataTableCell",
                        "target": {"rowName": "Row", "fieldName": "Value"},
                        "authorizationKeys": [
                            "/Script/Engine.DataTable#/Script/Test.Row#Value"
                        ],
                        "beforeValue": "before",
                        "afterValue": "after",
                    },
                    {
                        "operationId": "set-other-cell",
                        "operation": "setDataTableCell",
                        "target": {"rowName": "Row", "fieldName": "OtherValue"},
                        "authorizationKeys": [
                            "/Script/Engine.DataTable#/Script/Test.Row#OtherValue"
                        ],
                        "beforeValue": "other-before",
                        "afterValue": "other-after",
                    },
                ],
            }
        )
        write_json(self.patch_path, self.patch)
        write_json(self.policy_path, self.policy)
        write_json(self.report_path, self.report)

        manifest_path = self.create_manifest()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["operation"], "transaction")
        self.assertEqual(manifest["operationCount"], 2)
        self.assertEqual(len(manifest["operations"]), 2)
        self.assertEqual(
            manifest["operations"][1]["authorizationKeys"],
            ["/Script/Engine.DataTable#/Script/Test.Row#OtherValue"],
        )

    def test_multi_operation_manifest_rejects_report_order_mismatch(self) -> None:
        second_operation = {
            "operationId": "set-other-cell",
            "operation": "setDataTableCell",
            "target": {"rowName": "Row", "fieldName": "OtherValue"},
            "value": "other-after",
        }
        self.patch["assets"][0]["operations"].append(second_operation)
        self.report.update(
            {
                "operation": "transaction",
                "target": {},
                "operationCount": 2,
                "operations": [
                    {
                        "operationId": "set-other-cell",
                        "operation": "setDataTableCell",
                        "target": {"rowName": "Row", "fieldName": "OtherValue"},
                        "authorizationKeys": [],
                    },
                    {
                        "operationId": "set-cell",
                        "operation": "setDataTableCell",
                        "target": {"rowName": "Row", "fieldName": "Value"},
                        "authorizationKeys": [],
                    },
                ],
            }
        )
        write_json(self.patch_path, self.patch)
        write_json(self.report_path, self.report)
        with self.assertRaisesRegex(ValueError, "operationId values do not match"):
            create_backup_manifest(
                self.patch_path,
                self.policy_path,
                self.report_path,
                self.backup_root,
            )


if __name__ == "__main__":
    unittest.main()
