from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "Plugin" / "UEAgentKit" / "Source" / "UEAgentKitEditor" / "Private"
SRC = ROOT / "src" / "ue_agent_kit"


class LiveFastVerifyContractTests(unittest.TestCase):
    def test_bridge_accepts_transaction_id_and_routes_it(self) -> None:
        bridge = (PRIVATE / "EditorBridge.cpp").read_text(encoding="utf-8")
        self.assertIn(
            'Params->TryGetStringField(TEXT("transactionId"), TransactionId)',
            bridge,
        )
        self.assertIn(
            "TryVerifyAssetPropertyLiveFastResult(Operation, AssetPath, Target, TransactionId, Result, ErrorCode, ErrorMessage)",
            bridge,
        )

    def test_material_and_datatable_fast_verify_reads_retained_record(self) -> None:
        handler = (PRIVATE / "EditorBridgeWriteHandlers.cpp").read_text(
            encoding="utf-8"
        )
        for required in (
            "const FString& TransactionId",
            "LiveWriteTransactionRecords.Find(AssetPath)",
            "live-fast-verify-transaction-not-found",
            "Record->SessionId != SessionId",
            "Record->Operation != Operation",
            "Record->IO->RefreshTarget",
            "ReadWrittenValueForRevert",
            "MaterialParameter",
            "DataTableRow",
            "retained-live-write",
        ):
            self.assertIn(required, handler)
        # The record read must run before the unsupported fallback: an existing
        # Material/DataTable transaction can never be reported as unsupported.
        record_index = handler.index("Record->IO->ReadWrittenValueForRevert")
        unsupported_index = handler.index("live-fast-verify-operation-unsupported")
        self.assertLess(record_index, unsupported_index)

    def test_wrapper_forwards_transaction_id_for_both_fast_verify_paths(self) -> None:
        live = (SRC / "workflow_live.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(
            live.count('"transactionId": record.transaction_id'),
            2,
        )


if __name__ == "__main__":
    unittest.main()
