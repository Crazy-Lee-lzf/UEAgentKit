from __future__ import annotations

import re
import unittest
from pathlib import Path


TOOL_ROOT = Path(__file__).resolve().parents[2]
READER_ROOT = TOOL_ROOT / "Plugin" / "UEAgentKit" / "Source" / "UEAgentKitEditor" / "Private" / "AssetReaders"

READER_IMPLEMENTATIONS = {
    "ReadStaticMesh": "MeshAssetReaders.cpp",
    "ReadSkeletalMesh": "MeshAssetReaders.cpp",
    "ReadSkeleton": "MeshAssetReaders.cpp",
    "ReadPhysicsAsset": "MeshAssetReaders.cpp",
    "ReadMaterialFunction": "MaterialAssetReaders.cpp",
    "ReadMaterial": "MaterialAssetReaders.cpp",
    "ReadMaterialInstance": "MaterialAssetReaders.cpp",
    "ReadTexture2D": "MaterialAssetReaders.cpp",
    "ReadAnimSequence": "AnimationDataAssetReaders.cpp",
    "ReadAnimMontage": "AnimationDataAssetReaders.cpp",
    "ReadBlendSpace": "AnimationDataAssetReaders.cpp",
    "ReadDataTable": "AnimationDataAssetReaders.cpp",
    "ReadDataAsset": "AnimationDataAssetReaders.cpp",
    "ReadNiagaraSystem": "NiagaraSystemAssetReader.cpp",
    "ReadWorld": "WorldAssetReader.cpp",
}

READER_NAMES = {
    "static-mesh-v1",
    "skeletal-mesh-v1",
    "skeleton-v1",
    "physics-asset-v1",
    "material-function-v1",
    "material-v1",
    "material-instance-v1",
    "texture-2d-v1",
    "anim-sequence-v1",
    "anim-montage-v1",
    "blend-space-v1",
    "data-table-v1",
    "data-asset-v1",
    "niagara-system-v1",
    "world-v1",
}


class ReaderArchitectureTests(unittest.TestCase):
    def test_registry_remains_dispatch_only(self) -> None:
        registry = (READER_ROOT / "AssetReaderRegistry.cpp").read_text(encoding="utf-8")
        self.assertLessEqual(len(registry.splitlines()), 150)
        self.assertIn("GetAssetReaderBindings", registry)
        self.assertIn("return EAssetReaderStatus::NotHandled;", registry)
        for reader_name in READER_NAMES:
            self.assertIn(f'TEXT("{reader_name}")', registry)
        for function_name in READER_IMPLEMENTATIONS:
            definition = re.compile(rf"EAssetReaderStatus\s+{function_name}\s*\([^;]*\)\s*\{{", re.DOTALL)
            self.assertIsNone(definition.search(registry), function_name)

    def test_reader_implementations_are_unique_and_grouped(self) -> None:
        cpp_files = list(READER_ROOT.glob("*.cpp"))
        for function_name, expected_file in READER_IMPLEMENTATIONS.items():
            definition = re.compile(rf"EAssetReaderStatus\s+{function_name}\s*\([^;]*\)\s*\{{", re.DOTALL)
            matches = [path.name for path in cpp_files if definition.search(path.read_text(encoding="utf-8"))]
            self.assertEqual(matches, [expected_file], function_name)

    def test_reader_modules_stay_bounded(self) -> None:
        expected_files = {
            "AssetReaderCommon.cpp",
            "AssetReaderCommon.h",
            "AssetReaderImplementations.h",
            "AssetReaderRegistry.cpp",
            "AssetReaderRegistry.h",
            "MeshAssetReaders.cpp",
            "MaterialAssetReaders.cpp",
            "AnimationDataAssetReaders.cpp",
            "NiagaraSystemAssetReader.cpp",
            "WorldAssetReader.cpp",
        }
        self.assertTrue(expected_files.issubset({path.name for path in READER_ROOT.iterdir()}))
        for filename in expected_files:
            line_count = len((READER_ROOT / filename).read_text(encoding="utf-8").splitlines())
            self.assertLessEqual(line_count, 1000, filename)


if __name__ == "__main__":
    unittest.main()
