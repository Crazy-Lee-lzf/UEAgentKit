from __future__ import annotations

import ast
import unittest
from pathlib import Path


TOOL_ROOT = Path(__file__).resolve().parents[2]


class WorkflowMemorySmokeContractTests(unittest.TestCase):
    def test_real_workflow_smoke_binds_fixed_memory_database(self) -> None:
        runner = (TOOL_ROOT / "scripts" / "TestMcpWorkflow.ps1").read_text(encoding="utf-8")
        client_path = TOOL_ROOT / "tests" / "integration" / "mcp_workflow_smoke.py"
        client = client_path.read_text(encoding="utf-8")

        ast.parse(client, filename=str(client_path))
        self.assertIn('$MemoryDatabase = Join-Path $Output "Memory\\project-memory.sqlite3"', runner)
        self.assertIn('--memory-database $MemoryDatabase', runner)
        self.assertIn('tool_names_for_mode(workflow_enabled=True, memory_enabled=True)', client)
        self.assertIn('"ue_memory_record_task after Verify"', client)
        self.assertIn('"ue_memory_record_task after Rollback"', client)
        self.assertIn('"ue_memory_validate after Rollback"', client)
        self.assertIn('build_memory_audit_report(memory_service)', client)
        self.assertIn('parser.add_argument("--memory-database", type=Path, required=True)', client)
        self.assertNotIn("args.asset_path", client)


if __name__ == "__main__":
    unittest.main()
