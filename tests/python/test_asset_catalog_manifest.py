from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

TOOL_ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = TOOL_ROOT / "scripts" / "ValidateAssetCatalog.py"


def write_export(root: Path, *, include_sha: bool = True, success: bool = True, tamper: bool = False) -> Path:
    canonical = root / "canonical" / "Game" / "Test" / "SM_A_SM_A.json"
    canonical.parent.mkdir(parents=True, exist_ok=True)
    document: dict[str, Any] = {
        "schemaVersion": "1.1",
        "exporterVersion": "0.8.0",
        "engineVersion": "5.6.1-test",
        "projectName": "HostProject",
        "profile": "asset-index",
        "assetPath": "/Game/Test/SM_A.SM_A",
        "packageName": "/Game/Test/SM_A",
        "assetName": "SM_A",
        "assetClass": "/Script/Engine.StaticMesh",
        "assetReader": "static-mesh-v1",
        "assetReaderStatus": "not-handled",
        "assetReaderError": "",
        "assetDetails": {},
        "assetRegistry": {"assetClassPath": "/Script/Engine.StaticMesh"},
        "symbols": [
            {
                "id": "asset|/Game/Test/SM_A.SM_A",
                "kind": "asset",
                "assetPath": "/Game/Test/SM_A.SM_A",
                "assetReader": "static-mesh-v1",
                "assetReaderStatus": "not-handled",
                "assetDetails": {},
            }
        ],
        "references": [],
        "revision": {},
        "summary": {"symbols": 1, "references": 0},
    }
    canonical.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    digest = hashlib.sha256(canonical.read_bytes()).hexdigest()
    if tamper:
        canonical.write_text(canonical.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    entry: dict[str, Any] = {
        "assetPath": "/Game/Test/SM_A.SM_A",
        "success": success,
        "jsonPath": str(canonical),
        "bpctxPath": "",
        "assetReader": "static-mesh-v1",
        "assetReaderStatus": "not-handled" if success else "failed",
        "assetReaderError": "" if success else "template read failed",
    }
    if include_sha:
        entry["canonicalSha256"] = digest
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "schemaVersion": "1.1",
                "exporterVersion": "0.8.0",
                "projectName": "HostProject",
                "profile": "asset-index",
                "createdUtc": "2026.10.26-17.00.00",
                "successCount": 1 if success else 0,
                "failureCount": 0 if success else 1,
                "readerSuccessCount": 0,
                "readerFailureCount": 0 if success else 1,
                "assets": [entry],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return canonical


def run_validator(root: Path, *extra: str) -> tuple[int, dict[str, Any]]:
    completed = subprocess.run(
        [sys.executable, str(VALIDATOR), "--output", str(root), *extra],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    return completed.returncode, json.loads(completed.stdout)


class AssetCatalogManifestShapeTests(unittest.TestCase):
    def test_canonical_sha_shape_and_bytes_match(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_manifest_sha_") as temporary:
            root = Path(temporary)
            write_export(root)
            code, result = run_validator(root)
        self.assertEqual(code, 0, result["errors"])
        self.assertEqual(result["canonicalShaPresent"], 1)
        self.assertEqual(result["canonicalShaVerified"], 1)
        self.assertEqual(result["canonicalShaMissing"], 0)

    def test_old_manifest_without_hash_stays_compatible(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_manifest_old_") as temporary:
            root = Path(temporary)
            write_export(root, include_sha=False)
            code, result = run_validator(root)
        self.assertEqual(code, 0, result["errors"])
        self.assertEqual(result["canonicalShaPresent"], 0)
        self.assertEqual(result["canonicalShaMissing"], 1)

    def test_tampered_canonical_bytes_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_manifest_tamper_") as temporary:
            root = Path(temporary)
            write_export(root, tamper=True)
            code, result = run_validator(root)
        self.assertNotEqual(code, 0)
        self.assertTrue(any("canonicalSha256 mismatch" in error for error in result["errors"]))

    def test_failed_entry_must_not_advertise_hash(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_manifest_failed_") as temporary:
            root = Path(temporary)
            write_export(root, success=False)
            code, result = run_validator(root, "--allow-failures")
        self.assertNotEqual(code, 0)
        self.assertTrue(
            any("failed manifest entry advertises canonicalSha256" in error for error in result["errors"])
        )


if __name__ == "__main__":
    unittest.main()
