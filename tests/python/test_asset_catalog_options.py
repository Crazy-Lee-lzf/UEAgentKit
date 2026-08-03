from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


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


if __name__ == "__main__":
    unittest.main()
