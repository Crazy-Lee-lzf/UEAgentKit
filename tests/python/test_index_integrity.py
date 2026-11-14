from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from typing import Any

SRC_ROOT = Path(__file__).resolve().parents[2] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.database import open_database  # noqa: E402
from ue_agent_kit.index_integrity import (  # noqa: E402
    STATUS_FAIL,
    STATUS_PARTIAL,
    STATUS_PASS,
    audit_export_integrity,
)
from ue_agent_kit.indexer import build_index  # noqa: E402


PROJECT = "审计项目"
MATERIAL = "/Game/Audit/M_Audit.M_Audit"
TEXTURE = "/Game/Audit/T_Audit.T_Audit"
WORLD = "/Game/Audit/Map_Audit.Map_Audit"


def make_canonical(
    asset_path: str,
    *,
    profile: str = "asset-index",
    project: str = PROJECT,
    reader_name: str = "material-v1",
    reader_status: str = "success",
    details: dict[str, Any] | None = None,
    extra_symbols: int = 1,
) -> dict[str, Any]:
    package_name, asset_name = asset_path.rsplit(".", 1)
    asset_symbol = {
        "id": f"asset|{asset_path}",
        "kind": "asset",
        "name": asset_name,
        "assetPath": asset_path,
        "path": asset_path,
        "class": "/Script/Engine.Material",
        "assetReader": reader_name,
        "assetReaderStatus": reader_status,
        "assetDetails": details if details is not None else {"type": "material", "readerVersion": 1},
        "assetRegistry": {"packagePath": str(Path(package_name).parent.as_posix()), "tags": {}},
    }
    symbols = [asset_symbol]
    for index in range(extra_symbols):
        symbols.append(
            {
                "id": f"graph|{asset_path}|audit-{index}",
                "kind": "graph",
                "name": f"Graph_{index}",
                "assetPath": asset_path,
                "ownerSymbolId": asset_symbol["id"],
            }
        )
    return {
        "schemaVersion": "1.1",
        "exporterVersion": "0.7.0",
        "engineVersion": "5.6.1-test",
        "profile": profile,
        "projectName": project,
        "assetPath": asset_path,
        "packageName": package_name,
        "assetName": asset_name,
        "assetClass": "/Script/Engine.Material",
        "blueprintType": "",
        "parentClass": "",
        "generatedClass": "",
        "skeletonGeneratedClass": "",
        "status": 0,
        "revision": {
            "strategy": "package-sha256-v1",
            "available": True,
            "packageDirty": False,
            "value": "sha256:" + "a" * 64,
            "packageGuid": "55555555-5555-5555-5555-555555555555",
            "fileSize": 1024,
            "modifiedUtc": "2026-07-15T00:00:00.000Z",
            "contentSha256": "a" * 64,
        },
        "assetReader": reader_name,
        "assetReaderStatus": reader_status,
        "assetReaderError": "" if reader_status != "failed" else "reader crashed while reading properties",
        "assetDetails": details if details is not None else {"type": "material", "readerVersion": 1},
        "interfaces": [],
        "variables": [],
        "components": [],
        "functions": [],
        "graphs": [],
        "symbols": symbols,
        "references": [],
        "summary": {
            "variables": 0,
            "components": 0,
            "graphs": 0,
            "nodes": 0,
            "pins": 0,
            "links": 0,
            "symbols": len(symbols),
            "references": 0,
            "registryTags": 0,
            "specializedDetails": 1 if reader_status == "success" else 0,
        },
    }


def write_export(
    export_root: Path,
    canonical_documents: list[dict[str, Any]],
    *,
    manifest_overrides: dict[str, Any] | None = None,
    entry_overrides: dict[str, dict[str, Any]] | None = None,
    include_reader_fields: bool = True,
) -> Path:
    canonical_root = export_root / "canonical"
    canonical_root.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    for canonical in canonical_documents:
        asset_path = str(canonical["assetPath"])
        package_name, asset_name = asset_path.rsplit(".", 1)
        relative = Path(package_name.lstrip("/"))
        canonical_path = canonical_root / relative.parent / f"{relative.name}_{asset_name}.json"
        canonical_path.parent.mkdir(parents=True, exist_ok=True)
        canonical_path.write_text(
            json.dumps(canonical, ensure_ascii=False, indent=2),
            encoding="utf-8",
            newline="\n",
        )
        entry: dict[str, Any] = {
            "assetPath": asset_path,
            "success": True,
            "jsonPath": str(canonical_path),
            "bpctxPath": "",
            "error": "",
            "symbols": canonical["summary"]["symbols"],
            "references": 0,
        }
        if include_reader_fields:
            entry.update(
                {
                    "assetReader": canonical.get("assetReader", ""),
                    "assetReaderStatus": canonical.get("assetReaderStatus", ""),
                    "assetReaderError": canonical.get("assetReaderError", ""),
                }
            )
        if entry_overrides and asset_path in entry_overrides:
            entry.update(entry_overrides[asset_path])
        entries.append(entry)

    manifest: dict[str, Any] = {
        "schemaVersion": "1.1",
        "exporterVersion": "0.7.0",
        "engineVersion": "5.6.1-test",
        "projectName": canonical_documents[0].get("projectName", PROJECT) if canonical_documents else PROJECT,
        "createdUtc": "2026-10-26T00:00:00.000Z",
        "profile": canonical_documents[0].get("profile", "asset-index") if canonical_documents else "asset-index",
        "root": "/Game",
        "includeBlueprints": False,
        "includeGenerated": False,
        "includeTags": True,
        "skippedBlueprintCount": 0,
        "skippedGeneratedCount": 0,
        "assetCount": len(entries),
        "successCount": sum(1 for entry in entries if entry.get("success")),
        "failureCount": sum(1 for entry in entries if not entry.get("success")),
        "assets": entries,
    }
    if include_reader_fields:
        manifest["readerSuccessCount"] = sum(
            1 for entry in entries if entry.get("assetReaderStatus") == "success"
        )
        manifest["readerFailureCount"] = sum(
            1 for entry in entries if entry.get("assetReaderStatus") == "failed"
        )
    if manifest_overrides:
        manifest.update(manifest_overrides)
    (export_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )
    return export_root


class IndexIntegrityAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="ueak_index_integrity_")
        self.root = Path(self._temporary.name)
        self.export_root = self.root / "export"
        self.database_path = self.root / "index.sqlite3"

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def _build(self, assets: list[dict[str, Any]] | None = None) -> None:
        documents = assets or [make_canonical(MATERIAL)]
        write_export(self.export_root, documents)
        with open_database(self.database_path) as connection:
            result = build_index(connection, self.export_root, self.database_path)
            self.assertEqual(result.failed, 0, result.errors)

    def test_clean_export_and_index_passes(self) -> None:
        self._build(
            [
                make_canonical(MATERIAL),
                make_canonical(
                    TEXTURE,
                    reader_name="texture-2d-v1",
                    details={"type": "texture-2d", "readerVersion": 1},
                ),
            ]
        )
        report = audit_export_integrity(self.export_root, index_db=self.database_path)
        self.assertEqual(report.status, STATUS_PASS, report.render_text())
        self.assertTrue(report.complete)
        self.assertEqual(report.counts["missingFromIndexCount"], 0)
        self.assertEqual(report.counts["indexedPercent"], 100.0)

    def test_missing_index_asset_fails_and_never_reports_100_percent(self) -> None:
        self._build([make_canonical(MATERIAL), make_canonical(TEXTURE)])
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute("DELETE FROM assets WHERE asset_path = ?", (TEXTURE,))
            connection.commit()
        report = audit_export_integrity(self.export_root, index_db=self.database_path)
        self.assertEqual(report.status, STATUS_FAIL)
        self.assertFalse(report.complete)
        self.assertEqual(report.counts["missingFromIndexCount"], 1)
        self.assertEqual(report.counts["indexedPercent"], 50.0)
        self.assertTrue(
            any(finding.code == "missing-from-index" and finding.asset_path == TEXTURE for finding in report.findings)
        )
        text = report.render_text()
        self.assertIn("Index integrity: FAIL", text)
        self.assertNotIn("100%", text)

    def test_reader_failure_but_success_is_rejected_by_indexer_and_audit(self) -> None:
        failed = make_canonical(
            MATERIAL,
            reader_status="failed",
            details={},
        )
        write_export(self.export_root, [failed])
        with open_database(self.database_path) as connection:
            result = build_index(connection, self.export_root, self.database_path)
            self.assertEqual(result.failed, 1)
            self.assertEqual(result.added, 0)
            self.assertFalse(result.to_dict()["valid"])
            self.assertTrue(any("Specialized reader failed" in error for error in result.errors))
            rows = connection.execute("SELECT COUNT(*) FROM assets").fetchone()
            self.assertEqual(int(rows[0]), 0)

        report = audit_export_integrity(self.export_root)
        self.assertEqual(report.status, STATUS_FAIL)
        self.assertEqual(report.counts["successfulWithFailedReader"], 1)
        self.assertTrue(
            any(finding.code == "successful-entry-with-failed-reader" for finding in report.findings)
        )

    def test_not_handled_reader_is_not_a_failure(self) -> None:
        document = make_canonical(
            TEXTURE,
            reader_name="generic",
            reader_status="not-handled",
            details={},
        )
        write_export(self.export_root, [document])
        with open_database(self.database_path) as connection:
            result = build_index(connection, self.export_root, self.database_path)
            self.assertEqual((result.added, result.failed), (1, 0), result.errors)
        report = audit_export_integrity(self.export_root, index_db=self.database_path)
        self.assertEqual(report.status, STATUS_PASS, report.render_text())
        self.assertEqual(report.counts["notHandledCount"], 1)
        self.assertEqual(report.counts["readerFailureCount"], 0)

    def test_legacy_export_without_reader_fields_is_partial_not_fail(self) -> None:
        document = make_canonical(MATERIAL)
        document.pop("assetReaderStatus")
        document.pop("assetReader")
        document.pop("assetDetails")
        write_export(self.export_root, [document], include_reader_fields=False)
        report = audit_export_integrity(self.export_root)
        self.assertEqual(report.status, STATUS_PARTIAL)
        self.assertEqual(report.counts["unknownReaderStatusCount"], 1)
        self.assertIn("reader-status", [check["code"] for check in report.checks])

    def test_truncation_is_partial_and_explained(self) -> None:
        details = {
            "type": "world",
            "readerVersion": 1,
            "loadedActorCount": 20005,
            "exportedActorCount": 20000,
            "actorListTruncated": True,
            "worldPartition": {"actorDescListTruncated": True},
            "description": "x" * 32 + "<truncated>",
        }
        document = make_canonical(WORLD, reader_name="world-v1", details=details)
        write_export(self.export_root, [document])
        report = audit_export_integrity(self.export_root)
        self.assertEqual(report.status, STATUS_PARTIAL)
        codes = {finding.code for finding in report.findings}
        self.assertIn("world-actors-truncated", codes)
        self.assertIn("world-actor-descs-truncated", codes)
        self.assertIn("value-truncated", codes)
        self.assertEqual(report.counts["truncatedAssetCount"], 1)
        self.assertEqual(report.counts["worldTruncationCount"], 2)

    def test_index_details_loss_is_detected(self) -> None:
        self._build([make_canonical(MATERIAL)])
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute("DELETE FROM symbols")
            connection.commit()
        report = audit_export_integrity(self.export_root, index_db=self.database_path)
        self.assertEqual(report.status, STATUS_FAIL)
        self.assertEqual(report.counts["indexDetailsIncompleteCount"], 1)
        self.assertTrue(
            any(finding.code == "index-details-incomplete" for finding in report.findings)
        )

    def test_index_profile_downgrade_is_detected(self) -> None:
        document = make_canonical(MATERIAL, profile="full")
        self._build([document])
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute("UPDATE assets SET profile = 'asset-index'")
            connection.commit()
        report = audit_export_integrity(self.export_root, index_db=self.database_path)
        self.assertEqual(report.status, STATUS_FAIL)
        self.assertEqual(report.counts["indexProfileDowngradeCount"], 1)
        self.assertTrue(
            any(finding.code == "index-profile-downgrade" for finding in report.findings)
        )

    def test_stale_index_canonical_is_detected(self) -> None:
        self._build([make_canonical(MATERIAL)])
        canonical_path = next((self.export_root / "canonical").rglob("*.json"))
        canonical_path.write_text(canonical_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        report = audit_export_integrity(self.export_root, index_db=self.database_path)
        self.assertEqual(report.status, STATUS_FAIL)
        self.assertEqual(report.counts["indexStaleCount"], 1)
        self.assertTrue(
            any(finding.code == "index-stale-canonical" for finding in report.findings)
        )

    def test_entry_naming_mismatch_is_partial(self) -> None:
        document = make_canonical(MATERIAL)
        write_export(self.export_root, [document])
        manifest_path = self.export_root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        original = Path(manifest["assets"][0]["jsonPath"])
        renamed = original.with_name("wrong_entry_name.json")
        original.rename(renamed)
        manifest["assets"][0]["jsonPath"] = str(renamed)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        report = audit_export_integrity(self.export_root)
        self.assertEqual(report.status, STATUS_PARTIAL)
        self.assertTrue(any(finding.code == "entry-naming-mismatch" for finding in report.findings))

    def test_manifest_reader_failure_count_mismatch_fails(self) -> None:
        document = make_canonical(MATERIAL)
        write_export(self.export_root, [document], manifest_overrides={"readerFailureCount": 2})
        report = audit_export_integrity(self.export_root)
        self.assertEqual(report.status, STATUS_FAIL)
        self.assertTrue(
            any(finding.code == "reader-failure-count-mismatch" for finding in report.findings)
        )

    def test_bpctx_truncation_is_partial(self) -> None:
        document = make_canonical(MATERIAL, profile="logic")
        write_export(self.export_root, [document])
        bpctx_path = self.export_root / "bpctx" / "M_Audit.bpctx"
        bpctx_path.parent.mkdir(parents=True)
        bpctx_path.write_text(
            "H|BPCTX|1\\r\\nP|Description|" + "x" * 32 + "<truncated>\\r\\n",
            encoding="utf-8",
        )
        manifest_path = self.export_root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["assets"][0]["bpctxPath"] = str(bpctx_path)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        report = audit_export_integrity(self.export_root)
        self.assertEqual(report.status, STATUS_PARTIAL)
        self.assertEqual(report.counts["bpctxTruncatedCount"], 1)
        self.assertTrue(any(finding.code == "bpctx-value-truncated" for finding in report.findings))

    def test_manifest_asset_count_mismatch_fails(self) -> None:
        write_export(self.export_root, [make_canonical(MATERIAL)], manifest_overrides={"assetCount": 3})
        report = audit_export_integrity(self.export_root)
        self.assertEqual(report.status, STATUS_FAIL)
        self.assertTrue(
            any(finding.code == "asset-count-mismatch" for finding in report.findings)
        )

    def test_missing_canonical_file_fails(self) -> None:
        write_export(self.export_root, [make_canonical(MATERIAL)])
        next((self.export_root / "canonical").rglob("*.json")).unlink()
        report = audit_export_integrity(self.export_root)
        self.assertEqual(report.status, STATUS_FAIL)
        self.assertTrue(any(finding.code == "canonical-missing" for finding in report.findings))

    def test_indexer_rejects_inconsistent_manifest_reader_failure_count(self) -> None:
        write_export(
            self.export_root,
            [make_canonical(MATERIAL)],
            manifest_overrides={"readerFailureCount": 2},
        )
        with open_database(self.database_path) as connection:
            result = build_index(connection, self.export_root, self.database_path)
            self.assertFalse(result.to_dict()["valid"])
            self.assertTrue(any("readerFailureCount" in error for error in result.errors))


    def test_reader_success_with_empty_details_is_rejected(self) -> None:
        document = make_canonical(MATERIAL, reader_status="success", details={})
        write_export(self.export_root, [document])
        with open_database(self.database_path) as connection:
            result = build_index(connection, self.export_root, self.database_path)
            self.assertEqual(result.failed, 1)
            self.assertTrue(any("assetDetails is empty" in error for error in result.errors))
        report = audit_export_integrity(self.export_root)
        self.assertEqual(report.status, STATUS_FAIL)
        self.assertEqual(report.counts["detailsEmptyCount"], 1)

    def test_failed_manifest_entry_is_audited_and_import_is_not_valid(self) -> None:
        failed = make_canonical(MATERIAL, reader_status="failed", details={})
        write_export(
            self.export_root,
            [make_canonical(TEXTURE), failed],
            entry_overrides={MATERIAL: {"success": False, "error": "reader failed"}},
            manifest_overrides={"successCount": 1, "failureCount": 1, "readerFailureCount": 1},
        )
        with open_database(self.database_path) as connection:
            result = build_index(connection, self.export_root, self.database_path)
            self.assertEqual((result.added, result.failed), (1, 0))
            self.assertFalse(result.to_dict()["valid"])
            self.assertTrue(any("failed asset" in error for error in result.errors))
        report = audit_export_integrity(self.export_root)
        self.assertEqual(report.status, STATUS_FAIL)
        self.assertTrue(
            any(finding.code == "export-entry-failed" and finding.asset_path == MATERIAL for finding in report.findings)
        )


class ExportSourceGuardTests(unittest.TestCase):
    COMMANDLET = (
        Path(__file__).resolve().parents[2]
        / "Plugin"
        / "UEAgentKit"
        / "Source"
        / "UEAgentKitEditor"
        / "Private"
        / "AssetCatalogExportCommandlet.cpp"
    )

    @unittest.skipUnless(COMMANDLET.is_file(), "AssetCatalogExportCommandlet.cpp is not present")
    def test_failed_reader_and_hash_failure_are_not_advertised_as_success(self) -> None:
        source = self.COMMANDLET.read_text(encoding="utf-8")
        self.assertIn("if (ReaderStatus == EAssetReaderStatus::Failed)", source)
        self.assertIn("Specialized asset reader failed", source)
        self.assertIn("if (!FBlueprintContextSha256::HashFile", source)
        self.assertIn("Failed to hash written Canonical JSON bytes.", source)
        self.assertIn('SetStringField(TEXT("canonicalSha256"), Result.CanonicalSha256)', source)
        reader_guard = source.index("if (ReaderStatus == EAssetReaderStatus::Failed)")
        hash_guard = source.index("if (!FBlueprintContextSha256::HashFile")
        success_assignment = source.index("OutResult.bSuccess = true;")
        self.assertLess(reader_guard, hash_guard)
        self.assertLess(hash_guard, success_assignment)
        self.assertIn("return false;", source[reader_guard:hash_guard])
        self.assertIn("return false;", source[hash_guard:success_assignment])


class RealExportSmokeTests(unittest.TestCase):
    """Optional smoke test against a developer-provided real AssetCatalog export.

    The export location is machine-specific, so it is supplied through
    ``UEAK_REAL_EXPORT_DIR`` and the test is skipped when it is unset or absent.
    """

    REAL_EXPORT_ENV = "UEAK_REAL_EXPORT_DIR"
    _CONFIGURED_EXPORT = os.environ.get(REAL_EXPORT_ENV, "").strip()
    REAL_EXPORT = Path(_CONFIGURED_EXPORT) if _CONFIGURED_EXPORT else None

    @unittest.skipUnless(
        REAL_EXPORT is not None and REAL_EXPORT.is_dir(),
        f"real AssetCatalog export not configured (set {REAL_EXPORT_ENV})",
    )
    def test_real_asset_catalog_export_is_not_failed_and_reports_not_handled(self) -> None:
        assert self.REAL_EXPORT is not None
        report = audit_export_integrity(self.REAL_EXPORT)
        self.assertNotEqual(report.status, STATUS_FAIL, report.render_text())
        self.assertEqual(report.counts["assetCount"], 426)
        self.assertEqual(report.counts["successCount"], 426)
        self.assertGreater(report.counts["notHandledCount"], 0)
        self.assertEqual(report.counts["readerFailureCount"], 0)


if __name__ == "__main__":
    unittest.main()
