from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from t10_engine_manifest_recovery import (  # noqa: E402
    DEFAULT_BUILD_ID,
    DEFAULT_COVERAGE,
    DEFAULT_ENGINE_BIN,
    reconcile,
)


class EngineManifestRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.engine = self.root / "Engine" / "Binaries" / "Win64"
        self.engine.mkdir(parents=True)
        self.manifest = self.engine / "UnrealEditor.modules"
        self.target = self.engine / "UnrealEditor.target"
        self.coverage = self.root / "trust-coverage.json"
        self.dll = self.engine / "UnrealEditor-Engine.dll"
        self.dll.write_bytes(b"fixture")
        self._write_manifest({"Engine": "UnrealEditor-Engine.dll"})
        self._write_target({"Version": {"BuildId": DEFAULT_BUILD_ID}})
        self._write_coverage(
            modules=[
                {
                    "module": "Engine",
                    "moduleFile": "../../../Engine/Binaries/Win64/UnrealEditor-Engine.dll",
                    "candidateCategory": "B_core_engine_binary",
                    "moduleLoaded": True,
                    "owners": 1,
                    "spawners": 2,
                }
            ],
            impact={
                "describedTotal": 1,
                "describedCandidateRecovered": 1,
                "describedCandidateDeniedProject": 0,
                "describedCandidateDeniedOther": 0,
                "deniedModules": [],
            },
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_manifest(self, modules: dict[str, str], build_id: str = DEFAULT_BUILD_ID) -> None:
        self.manifest.write_text(
            json.dumps({"BuildId": build_id, "Modules": modules}), encoding="utf-8"
        )

    def _write_target(self, payload: dict) -> None:
        data = {
            "Version": {"BuildId": DEFAULT_BUILD_ID},
            "BuildProducts": [
                {
                    "Path": "$(EngineDir)/Binaries/Win64/UnrealEditor-Engine.dll",
                    "Type": "DynamicLibrary",
                }
            ],
            "BuildPlugins": [],
        }
        data.update(payload)
        self.target.write_text(json.dumps(data), encoding="utf-8")

    def _write_coverage(self, *, modules: list[dict], impact: dict) -> None:
        self.coverage.write_text(
            json.dumps(
                {
                    "modules": modules,
                    "candidateDescriptorImpact": impact,
                }
            ),
            encoding="utf-8",
        )

    def _run(self, **overrides):
        args = dict(
            engine_bin=self.engine,
            manifest_path=self.manifest,
            coverage_path=self.coverage,
            target_path=self.target,
            expected_build_id=DEFAULT_BUILD_ID,
            expected_total_modules=1,
            expected_core_modules=1,
            expected_unloaded_modules=0,
            expected_described=1,
        )
        args.update(overrides)
        return reconcile(**args)

    def test_exact_mapping_recovers_described_nodes(self) -> None:
        report = self._run()
        self.assertEqual(report["status"], "ok", report["errors"])
        self.assertEqual(report["moduleReconciliation"]["coreManifestExact"], 1)
        self.assertEqual(report["moduleReconciliation"]["coreManifestPresent"], 1)
        self.assertEqual(report["hypotheticalDescriptorRecovery"]["recovered"], 1)
        self.assertEqual(report["hypotheticalDescriptorRecovery"]["remaining"], 0)
        self.assertFalse(report["manifest"]["externallyTrusted"])
        self.assertEqual(report["manifest"]["trustStatus"], "locally-consistent-but-untrusted")

    def test_external_trust_flag_is_simulation_only(self) -> None:
        report = self._run(externally_trusted_manifest=True)
        self.assertEqual(report["status"], "ok", report["errors"])
        self.assertTrue(report["moduleReconciliation"]["rows"][0]["externallyTrusted"])
        self.assertTrue(report["manifest"]["externallyTrusted"])
        self.assertFalse(report["trustAssessment"]["externallyTrusted"])
        self.assertFalse(report["manifest"]["productionDecisionAllowed"])

    def test_missing_manifest_is_refused(self) -> None:
        self.manifest.unlink()
        report = self._run()
        self.assertEqual(report["status"], "refused")
        self.assertTrue(any("missing" in e or "input file" in e for e in report["errors"]))

    def test_fake_manifest_invalid_json_is_refused(self) -> None:
        self.manifest.write_text("{not-json", encoding="utf-8")
        report = self._run()
        self.assertEqual(report["status"], "refused")
        self.assertTrue(any("invalid JSON" in e for e in report["errors"]))

    def test_build_id_mismatch_is_refused(self) -> None:
        self._write_manifest({"Engine": "UnrealEditor-Engine.dll"}, build_id="999")
        report = self._run()
        self.assertEqual(report["status"], "refused")
        self.assertTrue(any("BuildId mismatch" in e for e in report["errors"]))

    def test_duplicate_manifest_filename_is_refused(self) -> None:
        self._write_manifest(
            {
                "Engine": "UnrealEditor-Engine.dll",
                "Other": "UnrealEditor-Engine.dll",
            }
        )
        report = self._run(expected_total_modules=2, expected_core_modules=1)
        self.assertEqual(report["status"], "refused")
        self.assertTrue(any("duplicate module filename" in e for e in report["errors"]))

    def test_duplicate_manifest_module_key_is_refused(self) -> None:
        self.manifest.write_text(
            '{"BuildId":"43139311","Modules":{"Engine":"UnrealEditor-Engine.dll",'
            '"Engine":"UnrealEditor-Engine.dll"}}',
            encoding="utf-8",
        )
        report = self._run()
        self.assertEqual(report["status"], "refused")
        self.assertTrue(any("duplicate JSON key" in e for e in report["errors"]))

    def test_unexpected_module_filename_is_refused(self) -> None:
        self._write_manifest({"Engine": "evil.dll"})
        report = self._run()
        self.assertEqual(report["status"], "refused")
        self.assertTrue(any("unexpected module filename" in e for e in report["errors"]))

    def test_manifest_path_traversal_is_refused(self) -> None:
        self._write_manifest({"Engine": "../UnrealEditor-Engine.dll"})
        report = self._run()
        self.assertEqual(report["status"], "refused")
        self.assertTrue(any("path traversal" in e for e in report["errors"]))

    def test_module_file_escape_is_refused(self) -> None:
        self._write_coverage(
            modules=[
                {
                    "module": "Engine",
                    "moduleFile": "../../../../Windows/System32/evil.dll",
                    "candidateCategory": "B_core_engine_binary",
                    "moduleLoaded": True,
                    "owners": 1,
                    "spawners": 1,
                }
            ],
            impact={
                "describedTotal": 1,
                "describedCandidateRecovered": 1,
                "describedCandidateDeniedProject": 0,
                "describedCandidateDeniedOther": 0,
                "deniedModules": [],
            },
        )
        report = self._run()
        self.assertEqual(report["status"], "refused")
        rows = report["moduleReconciliation"]["rows"]
        self.assertTrue(rows and rows[0]["pathTraversalSafe"] is False)
        self.assertTrue(any("path traversal" in e for e in report["errors"]))

    def test_missing_dll_is_refused(self) -> None:
        self.dll.unlink()
        report = self._run()
        self.assertEqual(report["status"], "refused")
        rows = report["moduleReconciliation"]["rows"]
        self.assertTrue(rows and rows[0]["fileExists"] is False)

    def test_target_build_id_mismatch_is_refused(self) -> None:
        self._write_target({"Version": {"BuildId": "123"}})
        report = self._run()
        self.assertEqual(report["status"], "refused")
        self.assertTrue(any("target BuildId mismatch" in e for e in report["errors"]))

    def test_manifest_exact_but_target_product_missing_is_reported(self) -> None:
        self.target.write_text(
            json.dumps(
                {
                    "Version": {"BuildId": DEFAULT_BUILD_ID},
                    "BuildProducts": [],
                    "BuildPlugins": [],
                }
            ),
            encoding="utf-8",
        )
        report = self._run()
        self.assertEqual(report["status"], "ok", report["errors"])
        self.assertEqual(report["target"]["coreProductMatches"], 0)

    @unittest.skipUnless(
        DEFAULT_ENGINE_BIN.is_dir() and DEFAULT_COVERAGE.is_file(),
        "live UE engine manifest/coverage evidence is unavailable",
    )
    def test_live_engine_manifest_recovery_is_105_105_26_26_512_512(self) -> None:
        report = reconcile(
            engine_bin=DEFAULT_ENGINE_BIN,
            manifest_path=DEFAULT_ENGINE_BIN / "UnrealEditor.modules",
            coverage_path=DEFAULT_COVERAGE,
            target_path=DEFAULT_ENGINE_BIN / "UnrealEditor.target",
            expected_build_id=DEFAULT_BUILD_ID,
            expected_total_modules=513,
            expected_core_modules=105,
            expected_unloaded_modules=26,
            expected_described=512,
        )
        self.assertEqual(report["status"], "ok", report["errors"])
        self.assertEqual(report["moduleReconciliation"]["coreManifestExact"], 105)
        self.assertEqual(report["moduleReconciliation"]["coreManifestPresent"], 105)
        self.assertEqual(report["moduleReconciliation"]["unloadedCoreManifestExact"], 26)
        self.assertEqual(report["hypotheticalDescriptorRecovery"]["recovered"], 512)
        self.assertEqual(report["hypotheticalDescriptorRecovery"]["remaining"], 0)
        self.assertFalse(report["manifest"]["externallyTrusted"])
        self.assertFalse(report["trustAssessment"]["productionDecisionAllowed"])
        self.assertEqual(report["target"]["coreProductMatches"], 105)


if __name__ == "__main__":
    unittest.main()
