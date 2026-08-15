from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.config import (  # noqa: E402
    PROJECT_POLICY_MANIFEST,
    PROJECT_POLICY_MANIFEST_SCHEMA_VERSION,
    resolve_project_policy,
)
from ue_agent_kit.patches import _validate_policy  # noqa: E402


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\r\n",
    )


class ResolveProjectPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ueak_config_")
        self.root = Path(self.temporary.name)
        self.manifest = self.root / "manifest.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_manifest(self, projects: dict[str, object]) -> None:
        _write_json(
            self.manifest,
            {
                "schemaVersion": PROJECT_POLICY_MANIFEST_SCHEMA_VERSION,
                "projects": projects,
            },
        )

    def _write_policy(self, name: str) -> Path:
        path = self.root / name
        _write_json(path, {"schemaVersion": "1.0"})
        return path

    def _project(self, name: str) -> Path:
        return self.root / f"{name}.uproject"

    def test_resolves_default_by_project_name(self) -> None:
        policy = self._write_policy("a.json")
        self._write_manifest({"Proj": {"default": "a.json"}})
        self.assertEqual(
            resolve_project_policy(self._project("Proj"), manifest_path=self.manifest),
            policy.resolve(),
        )

    def test_resolves_profile_when_requested(self) -> None:
        self._write_policy("a.json")
        policy = self._write_policy("b.json")
        self._write_manifest(
            {"Proj": {"default": "a.json", "profiles": {"anim": "b.json"}}}
        )
        self.assertEqual(
            resolve_project_policy(
                self._project("Proj"), profile="anim", manifest_path=self.manifest
            ),
            policy.resolve(),
        )

    def test_returns_none_when_project_not_in_manifest(self) -> None:
        self._write_policy("a.json")
        self._write_manifest({"Other": {"default": "a.json"}})
        self.assertIsNone(
            resolve_project_policy(self._project("Proj"), manifest_path=self.manifest)
        )

    def test_returns_none_when_manifest_missing(self) -> None:
        self.assertIsNone(
            resolve_project_policy(self._project("Proj"), manifest_path=self.manifest)
        )

    def test_returns_none_when_project_path_is_none(self) -> None:
        self.assertIsNone(resolve_project_policy(None, manifest_path=self.manifest))

    def test_rejects_non_uproject_path(self) -> None:
        with self.assertRaisesRegex(ValueError, "uproject"):
            resolve_project_policy(self.root / "Proj.txt", manifest_path=self.manifest)

    def test_rejects_unknown_profile(self) -> None:
        self._write_policy("a.json")
        self._write_manifest({"Proj": {"default": "a.json"}})
        with self.assertRaisesRegex(ValueError, "Unknown policy profile"):
            resolve_project_policy(
                self._project("Proj"), profile="nope", manifest_path=self.manifest
            )

    def test_rejects_missing_policy_file(self) -> None:
        self._write_manifest({"Proj": {"default": "missing.json"}})
        with self.assertRaisesRegex(ValueError, "does not exist"):
            resolve_project_policy(self._project("Proj"), manifest_path=self.manifest)

    def test_rejects_malformed_manifest(self) -> None:
        self.manifest.parent.mkdir(parents=True, exist_ok=True)
        self.manifest.write_text("{not json", encoding="utf-8")
        with self.assertRaises(ValueError):
            resolve_project_policy(self._project("Proj"), manifest_path=self.manifest)


class RepoProjectPoliciesTests(unittest.TestCase):
    def _load_policy(self, filename: str) -> dict:
        return json.loads(
            (PROJECT_POLICY_MANIFEST.parent / filename).read_text(encoding="utf-8-sig")
        )

    def test_real_manifest_resolves_my_project_default(self) -> None:
        resolved = resolve_project_policy(Path("E:/WorkSpace/我的项目/我的项目.uproject"))
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.name, "my-project-write.json")

    def test_real_manifest_resolves_model_preview_default_read(self) -> None:
        resolved = resolve_project_policy(Path("E:/WorkSpace/ModelPreview/ModelPreview.uproject"))
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.name, "model-preview-read.json")

    def test_real_manifest_resolves_model_preview_animation_write_profile(self) -> None:
        resolved = resolve_project_policy(
            Path("E:/WorkSpace/ModelPreview/ModelPreview.uproject"),
            profile="animation-write",
        )
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.name, "model-preview-animation-write.json")

    def test_real_project_policies_pass_validate_policy(self) -> None:
        for filename in (
            "my-project-write.json",
            "model-preview-read.json",
            "model-preview-animation-write.json",
        ):
            with self.subTest(filename=filename):
                errors: list[dict[str, str]] = []
                _validate_policy(self._load_policy(filename), errors)
                self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
