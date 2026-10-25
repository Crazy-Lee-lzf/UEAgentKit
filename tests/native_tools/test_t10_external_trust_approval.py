from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from t10_external_trust_approval import (  # noqa: E402
    DEFAULT_BUILD_ID,
    ManifestError,
    candidate_digest,
    generate_candidate,
    verify_candidate,
)


class ExternalApprovalToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.engine = self.root / "Engine"
        self.engine_bin = self.engine / "Binaries" / "Win64"
        self.engine_bin.mkdir(parents=True)
        self.plugin_dir = self.engine / "Plugins" / "TestPlugin"
        self.plugin_dir.mkdir(parents=True)
        self.core_dll = self.engine_bin / "UnrealEditor-Engine.dll"
        self.plugin_dll = self.plugin_dir / "Binaries" / "Win64" / "UnrealEditor-TestPlugin.dll"
        self.descriptor = self.plugin_dir / "TestPlugin.uplugin"
        self.core_dll.parent.mkdir(parents=True, exist_ok=True)
        self.plugin_dll.parent.mkdir(parents=True, exist_ok=True)
        self.core_dll.write_bytes(b"core-binary")
        self.plugin_dll.write_bytes(b"plugin-binary")
        self.descriptor.write_text(
            json.dumps({"FileVersion": 3, "FriendlyName": "Test Plugin"}), encoding="utf-8"
        )
        self.manifest = self.engine_bin / "UnrealEditor.modules"
        self.target = self.engine_bin / "UnrealEditor.target"
        self.coverage = self.root / "coverage.json"
        self.candidate_path = self.root / "candidate.json"
        self._write_manifest()
        self._write_target()
        self._write_coverage()
        self.candidate = generate_candidate(
            engine_bin=self.engine_bin,
            coverage_path=self.coverage,
            output_path=self.candidate_path,
            expected_core_modules=1,
            expected_plugins=1,
            expected_plugin_modules=1,
            max_files=10,
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_manifest(self, modules: dict[str, str] | None = None) -> None:
        modules = modules or {"Engine": "UnrealEditor-Engine.dll"}
        self.manifest.write_text(
            json.dumps({"BuildId": DEFAULT_BUILD_ID, "Modules": modules}), encoding="utf-8"
        )

    def _write_target(self) -> None:
        self.target.write_text(
            json.dumps(
                {
                    "Version": {
                        "MajorVersion": 5,
                        "MinorVersion": 6,
                        "PatchVersion": 1,
                        "Changelist": 44394996,
                        "CompatibleChangelist": 43139311,
                        "BranchName": "++UE5+Release-5.6",
                        "BuildId": DEFAULT_BUILD_ID,
                    }
                }
            ),
            encoding="utf-8",
        )

    def _write_coverage(self) -> None:
        descriptor_sha = hashlib.sha256(self.descriptor.read_bytes()).hexdigest()
        self.coverage.write_text(
            json.dumps(
                {
                    "modules": [
                        {
                            "module": "Engine",
                            "moduleFile": "../../../Engine/Binaries/Win64/UnrealEditor-Engine.dll",
                            "candidateCategory": "B_core_engine_binary",
                            "moduleLoaded": True,
                        },
                        {
                            "module": "TestPlugin",
                            "moduleFile": "../../../Engine/Plugins/TestPlugin/Binaries/Win64/UnrealEditor-TestPlugin.dll",
                            "pluginName": "TestPlugin",
                            "pluginBaseDir": "../../../Engine/Plugins/TestPlugin",
                            "pluginDescriptorFile": "../../../Engine/Plugins/TestPlugin/TestPlugin.uplugin",
                        },
                    ],
                    "candidateManifest": {
                        "plugins": [
                            {
                                "plugin": "TestPlugin",
                                "baseDir": "../../../Engine/Plugins/TestPlugin",
                                "descriptorFile": "../../../Engine/Plugins/TestPlugin/TestPlugin.uplugin",
                                "descriptorDigest": f"sha256:{descriptor_sha}",
                                "version": 1,
                                "versionName": "1.0",
                                "modules": ["TestPlugin"],
                            }
                        ]
                    },
                }
            ),
            encoding="utf-8",
        )

    def _approval(self, candidate: dict | None = None, *, status: str = "synthetic-demo-not-production") -> dict:
        candidate = candidate or self.candidate
        return {
            "schemaVersion": "ueak.t10.external-approval/1.0",
            "status": status,
            "approvalId": "synthetic-demo-not-production",
            "engine": {
                "buildId": candidate["buildId"],
                "modulesManifestSha256": candidate["modulesManifestSha256"],
                "targetSha256": candidate["targetSha256"],
            },
            "candidateDigest": candidate["candidateDigest"],
            "coreModules": [
                {"module": item["module"], "sha256": item["sha256"]}
                for item in candidate["coreModules"]
            ],
            "plugins": [
                {
                    "plugin": item["plugin"],
                    "descriptorSha256": item["descriptorSha256"],
                    "modules": [
                        {"module": module["module"], "sha256": module["sha256"]}
                        for module in item["modules"]
                    ],
                }
                for item in candidate["plugins"]
            ],
        }

    def _approval_hash(self, approval: dict) -> str:
        data = json.dumps(approval, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(data).hexdigest()

    def _verify(self, approval: dict | None, approval_hash: str = "", expected_hash: str = ""):
        return verify_candidate(
            candidate=self.candidate,
            approval=approval,
            approval_sha256=approval_hash,
            expected_approval_sha256=expected_hash,
        )

    def test_candidate_is_untrusted_and_deterministic(self) -> None:
        first = self.candidate_path.read_bytes()
        self.assertFalse(self.candidate["productionEnabled"])
        self.assertFalse(self.candidate["trusted"])
        self.assertEqual(self.candidate["counts"]["coreModules"], 1)
        self.assertEqual(self.candidate["counts"]["plugins"], 1)
        second_path = self.root / "candidate2.json"
        generate_candidate(
            engine_bin=self.engine_bin,
            coverage_path=self.coverage,
            output_path=second_path,
            expected_core_modules=1,
            expected_plugins=1,
            expected_plugin_modules=1,
            max_files=10,
        )
        self.assertEqual(first, second_path.read_bytes())

    def test_no_approval_is_forbidden(self) -> None:
        result = self._verify(None)
        self.assertEqual(result["status"], "FORBIDDEN")
        self.assertFalse(result["verified"])
        self.assertFalse(result["productionEnabled"])

    def test_approval_requires_out_of_band_digest(self) -> None:
        approval = self._approval()
        approval_sha = self._approval_hash(approval)
        result = self._verify(approval, approval_sha, "")
        self.assertEqual(result["status"], "FORBIDDEN")
        self.assertTrue(any("expected-approval-sha256" in e for e in result["errors"]))

    def test_wrong_approval_digest_is_forbidden(self) -> None:
        approval = self._approval()
        result = self._verify(approval, "0" * 64, "1" * 64)
        self.assertEqual(result["status"], "FORBIDDEN")

    def test_wrong_buildid_is_forbidden(self) -> None:
        approval = self._approval()
        approval["engine"]["buildId"] = "999"
        result = self._verify(approval, self._approval_hash(approval), self._approval_hash(approval))
        self.assertEqual(result["status"], "FORBIDDEN")
        self.assertTrue(any("BuildId" in e for e in result["errors"]))

    def test_tampered_candidate_is_forbidden(self) -> None:
        approval = self._approval()
        tampered = copy.deepcopy(self.candidate)
        tampered["coreModules"][0]["sha256"] = "f" * 64
        result = verify_candidate(
            candidate=tampered,
            approval=approval,
            approval_sha256=self._approval_hash(approval),
            expected_approval_sha256=self._approval_hash(approval),
        )
        self.assertEqual(result["status"], "FORBIDDEN")
        self.assertTrue(any("candidateDigest" in e for e in result["errors"]))

    def test_module_digest_change_is_forbidden(self) -> None:
        approval = self._approval()
        approval["coreModules"][0]["sha256"] = "a" * 64
        result = self._verify(approval, self._approval_hash(approval), self._approval_hash(approval))
        self.assertEqual(result["status"], "FORBIDDEN")

    def test_plugin_descriptor_change_is_forbidden(self) -> None:
        approval = self._approval()
        approval["plugins"][0]["descriptorSha256"] = "b" * 64
        result = self._verify(approval, self._approval_hash(approval), self._approval_hash(approval))
        self.assertEqual(result["status"], "FORBIDDEN")

    def test_plugin_module_change_is_forbidden(self) -> None:
        approval = self._approval()
        approval["plugins"][0]["modules"][0]["sha256"] = "c" * 64
        result = self._verify(approval, self._approval_hash(approval), self._approval_hash(approval))
        self.assertEqual(result["status"], "FORBIDDEN")

    def test_duplicate_approval_core_entry_is_forbidden(self) -> None:
        approval = self._approval()
        approval["coreModules"].append(dict(approval["coreModules"][0]))
        result = self._verify(approval, self._approval_hash(approval), self._approval_hash(approval))
        self.assertEqual(result["status"], "FORBIDDEN")
        self.assertTrue(any("duplicate approval core" in e for e in result["errors"]))

    def test_duplicate_candidate_core_entry_is_forbidden(self) -> None:
        candidate = copy.deepcopy(self.candidate)
        candidate["coreModules"].append(dict(candidate["coreModules"][0]))
        candidate["candidateDigest"] = f"sha256:{candidate_digest(candidate)}"
        approval = self._approval()
        approval["candidateDigest"] = candidate["candidateDigest"]
        result = verify_candidate(
            candidate=candidate,
            approval=approval,
            approval_sha256=self._approval_hash(approval),
            expected_approval_sha256=self._approval_hash(approval),
        )
        self.assertEqual(result["status"], "FORBIDDEN")
        self.assertTrue(any("duplicate core module" in e for e in result["errors"]))

    def test_extra_approval_plugin_is_forbidden(self) -> None:
        approval = self._approval()
        approval["plugins"].append(
            {"plugin": "NotInCandidate", "descriptorSha256": "d" * 64, "modules": []}
        )
        result = self._verify(approval, self._approval_hash(approval), self._approval_hash(approval))
        self.assertEqual(result["status"], "FORBIDDEN")

    def test_unlisted_additional_plugin_is_untrusted_partial(self) -> None:
        candidate = copy.deepcopy(self.candidate)
        candidate["plugins"].append(
            {
                "plugin": "ExtraPlugin",
                "version": 1,
                "versionName": "1.0",
                "descriptorPath": "Plugins/ExtraPlugin/ExtraPlugin.uplugin",
                "descriptorSha256": "e" * 64,
                "declaredModuleCount": 1,
                "modules": [
                    {
                        "module": "ExtraModule",
                        "basename": "UnrealEditor-ExtraModule.dll",
                        "path": "Plugins/ExtraPlugin/Binaries/Win64/UnrealEditor-ExtraModule.dll",
                        "sha256": "f" * 64,
                    }
                ],
            }
        )
        candidate["counts"]["plugins"] += 1
        candidate["candidateDigest"] = f"sha256:{candidate_digest(candidate)}"
        approval = self._approval()  # only TestPlugin was approved
        approval["candidateDigest"] = candidate["candidateDigest"]
        result = verify_candidate(
            candidate=candidate,
            approval=approval,
            approval_sha256=self._approval_hash(approval),
            expected_approval_sha256=self._approval_hash(approval),
        )
        self.assertEqual(result["status"], "PARTIAL")
        self.assertFalse(result["productionEnabled"])
        self.assertEqual(len(result["unapproved"]["plugins"]), 1)
        self.assertEqual(result["unapproved"]["plugins"][0]["plugin"], "ExtraPlugin")

    def test_valid_synthetic_pinned_approval_is_verified_but_not_production(self) -> None:
        approval = self._approval()
        approval_sha = self._approval_hash(approval)
        result = self._verify(approval, approval_sha, approval_sha)
        self.assertEqual(result["status"], "VERIFIED", result["errors"])
        self.assertTrue(result["verified"])
        self.assertTrue(result["syntheticDemo"])
        self.assertFalse(result["productionEnabled"])
        self.assertFalse(result["trustedForProduction"])

    def test_path_escape_is_refused(self) -> None:
        data = json.loads(self.coverage.read_text(encoding="utf-8"))
        data["modules"][0]["moduleFile"] = "../../../../outside.dll"
        self.coverage.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(ManifestError):
            generate_candidate(
                engine_bin=self.engine_bin,
                coverage_path=self.coverage,
                output_path=self.root / "escaped.json",
                expected_core_modules=1,
                expected_plugins=1,
                expected_plugin_modules=1,
                max_files=10,
            )

    def test_missing_file_is_refused(self) -> None:
        self.core_dll.unlink()
        with self.assertRaises(ManifestError):
            generate_candidate(
                engine_bin=self.engine_bin,
                coverage_path=self.coverage,
                output_path=self.root / "missing.json",
                expected_core_modules=1,
                expected_plugins=1,
                expected_plugin_modules=1,
                max_files=10,
            )

    def test_manifest_mismatch_is_refused(self) -> None:
        self._write_manifest({"Engine": "UnrealEditor-Other.dll"})
        with self.assertRaises(ManifestError):
            generate_candidate(
                engine_bin=self.engine_bin,
                coverage_path=self.coverage,
                output_path=self.root / "mismatch.json",
                expected_core_modules=1,
                expected_plugins=1,
                expected_plugin_modules=1,
                max_files=10,
            )

    def test_symlink_is_refused(self) -> None:
        real = self.root / "real.dll"
        real.write_bytes(b"linked")
        self.core_dll.unlink()
        try:
            os.symlink(real, self.core_dll)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink unavailable: {exc}")
        with self.assertRaises(ManifestError) as raised:
            generate_candidate(
                engine_bin=self.engine_bin,
                coverage_path=self.coverage,
                output_path=self.root / "symlink.json",
                expected_core_modules=1,
                expected_plugins=1,
                expected_plugin_modules=1,
                max_files=10,
            )
        self.assertIn("symlink", str(raised.exception).lower())

    def test_junction_is_refused_if_available(self) -> None:
        if sys.platform != "win32":
            self.skipTest("junction test is Windows-only")
        real = self.root / "RealPlugin"
        self.plugin_dir.rename(real)
        junction = self.plugin_dir
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(real)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            self.skipTest(f"junction unavailable: {result.stderr or result.stdout}")
        try:
            with self.assertRaises(ManifestError):
                generate_candidate(
                    engine_bin=self.engine_bin,
                    coverage_path=self.coverage,
                    output_path=self.root / "junction.json",
                    expected_core_modules=1,
                    expected_plugins=1,
                    expected_plugin_modules=1,
                    max_files=10,
                )
        finally:
            try:
                junction.rmdir()
            except OSError:
                pass

    def test_duplicate_manifest_module_entry_is_refused(self) -> None:
        self.manifest.write_text(
            '{"BuildId":"43139311","Modules":{"Engine":"UnrealEditor-Engine.dll",'
            '"Engine":"UnrealEditor-Engine.dll"}}',
            encoding="utf-8",
        )
        with self.assertRaises(ManifestError) as raised:
            generate_candidate(
                engine_bin=self.engine_bin,
                coverage_path=self.coverage,
                output_path=self.root / "dupe.json",
                expected_core_modules=1,
                expected_plugins=1,
                expected_plugin_modules=1,
                max_files=10,
            )
        self.assertIn("duplicate", str(raised.exception).lower())


if __name__ == "__main__":
    unittest.main()
