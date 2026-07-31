from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class LiveWriteSmokeContractTests(unittest.TestCase):
    def test_harness_runs_live_workflow_and_preserves_disk(self) -> None:
        powershell = (ROOT / "scripts" / "TestMcpLiveWrite.ps1").read_text(encoding="utf-8")
        client = (ROOT / "tests" / "integration" / "mcp_live_write_smoke.py").read_text(encoding="utf-8")
        wrapper = (ROOT / "scripts" / "TestMcpLiveWrite.cmd").read_text(encoding="utf-8")

        self.assertIn("-EnableLiveEditor", client)
        self.assertIn("-EnableWriteTools", client)
        self.assertIn("-EnableCommitTools", client)
        self.assertIn('"ue_apply_asset_property_live"', client)
        self.assertIn('f"LIVE APPLY {plan_id}"', client)
        self.assertIn("diskPackageHashUnchanged", client)
        self.assertIn("databaseHashUnchanged", client)
        self.assertIn("Get-FileHash", powershell)
        self.assertIn("Stop-Process -Id $EditorProcess.Id -Force", powershell)
        self.assertIn("MCP Live Editor Write smoke test passed", powershell)
        self.assertIn("TestMcpLiveWrite.ps1", wrapper)


if __name__ == "__main__":
    unittest.main()
