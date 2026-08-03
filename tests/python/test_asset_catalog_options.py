from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

VALIDATOR_PATH = ROOT / "scripts" / "ValidateAssetCatalog.py"
VALIDATOR_SPEC = importlib.util.spec_from_file_location("ueak_validate_asset_catalog", VALIDATOR_PATH)
assert VALIDATOR_SPEC is not None and VALIDATOR_SPEC.loader is not None
VALIDATOR = importlib.util.module_from_spec(VALIDATOR_SPEC)
VALIDATOR_SPEC.loader.exec_module(VALIDATOR)



class AssetCatalogOptionTests(unittest.TestCase):
    def test_no_asset_readers_is_forwarded_and_bypasses_specialized_readers(self) -> None:
        script = (ROOT / "scripts" / "RunAssetCatalog.ps1").read_text(encoding="utf-8")
        commandlet = (
            ROOT
            / "Plugin"
            / "UEAgentKit"
            / "Source"
            / "UEAgentKitEditor"
            / "Private"
            / "AssetCatalogExportCommandlet.cpp"
        ).read_text(encoding="utf-8")
        registry_header = (
            ROOT
            / "Plugin"
            / "UEAgentKit"
            / "Source"
            / "UEAgentKitEditor"
            / "Private"
            / "AssetReaders"
            / "AssetReaderRegistry.h"
        ).read_text(encoding="utf-8")
        registry_source = (
            ROOT
            / "Plugin"
            / "UEAgentKit"
            / "Source"
            / "UEAgentKitEditor"
            / "Private"
            / "AssetReaders"
            / "AssetReaderRegistry.cpp"
        ).read_text(encoding="utf-8")

        self.assertIn("[switch]$NoAssetReaders", script)
        self.assertIn('$Arguments += "-NoAssetReaders"', script)
        self.assertIn('FParse::Param(*Params, TEXT("NoAssetReaders"))', commandlet)
        self.assertIn("if (bIncludeAssetReaders)", commandlet)
        self.assertIn("FAssetReaderRegistry::ReadAssetDetails", commandlet)
        self.assertIn('SetBoolField(TEXT("includeAssetReaders"), bIncludeAssetReaders)', commandlet)
        self.assertIn("Disabled", registry_header)
        self.assertIn('return TEXT("disabled")', registry_source)

    def test_windows_extended_path_supports_drive_and_unc_paths(self) -> None:
        separator = chr(92)
        extended_prefix = separator * 2 + "?" + separator
        drive_path = "C:" + separator + "deep" + separator + "asset.json"
        unc_path = separator * 2 + "server" + separator + "share" + separator + "asset.json"

        self.assertEqual(VALIDATOR.windows_extended_path(drive_path), extended_prefix + drive_path)
        self.assertEqual(
            VALIDATOR.windows_extended_path(unc_path),
            extended_prefix + "UNC" + separator + unc_path[2:],
        )
        already_extended = extended_prefix + drive_path
        self.assertEqual(VALIDATOR.windows_extended_path(already_extended), already_extended)


if __name__ == "__main__":
    unittest.main()
