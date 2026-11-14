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
        helper = (PRIVATE / "EditorBridgeRetainedLiveWrite.cpp").read_text(
            encoding="utf-8"
        )
        header = (PRIVATE / "EditorBridgeRetainedLiveWrite.h").read_text(
            encoding="utf-8"
        )
        for required in (
            "TransactionId",
            "Records.Find(AssetPath)",
            "live-fast-verify-transaction-not-found",
            "Record->SessionId != SessionId",
            "Record->Operation != Operation",
            "Record->IO->RefreshTarget",
            "ReadWrittenValueForRevert",
            "MaterialParameter",
            "DataTableRow",
            "retained-live-write",
        ):
            self.assertIn(required, helper + header)
        # The retained read must remain read-only: no transaction, Modify, save
        # or package mutation belongs in this helper.
        for forbidden in (
            "FScopedTransaction",
            "->Modify(",
            "SavePackage",
            "MarkPackageDirty",
            "MarkBlueprintAsModified",
        ):
            self.assertNotIn(forbidden, helper)

    def test_helper_result_parameter_accepts_shared_ref(self) -> None:
        # The handler owns FJsonObject as TSharedRef; a TSharedPtr<FJsonObject>&
        # parameter would not bind and would fail to compile (found by the
        # static native header review; no build has been run).
        header = (PRIVATE / "EditorBridgeRetainedLiveWrite.h").read_text(
            encoding="utf-8"
        )
        helper = (PRIVATE / "EditorBridgeRetainedLiveWrite.cpp").read_text(
            encoding="utf-8"
        )
        self.assertIn("const TSharedRef<FJsonObject>& OutResult", header)
        self.assertIn("const TSharedRef<FJsonObject>& OutResult", helper)
        self.assertNotIn("TSharedPtr<FJsonObject>& OutResult", header + helper)

    def test_retained_success_does_not_fall_through_to_unsupported(self) -> None:
        # N1-DEF-02: the branch used to be a single compound test
        #   else if (!TransactionId.IsEmpty() && !ReadRetainedLiveWriteFastValue(...))
        # so a *successful* retained read evaluated the condition to false and
        # control reached the final else that reports "operation-unsupported".
        handler = (PRIVATE / "EditorBridgeWriteHandlers.cpp").read_text(
            encoding="utf-8"
        )
        branch = handler.index("else if (!TransactionId.IsEmpty()")
        unsupported = handler.index("live-fast-verify-operation-unsupported")
        self.assertLess(branch, unsupported)
        region = handler[branch:unsupported]

        # The guard must be the transaction-presence test alone; the retained
        # read belongs inside the branch body, where returning false means
        # failure and falling through means success.
        brace = region.index("{")
        guard = region[:brace].strip()
        self.assertEqual(guard, "else if (!TransactionId.IsEmpty())")

        body = region[brace:]
        self.assertIn("if (!ReadRetainedLiveWriteFastValue(", body)
        self.assertIn("return false;", body)
        # The failure path must surface the helper's own precise code and must
        # not substitute a different one.
        fail_path = body[: body.index("return false;")]
        self.assertNotIn("OutErrorCode =", fail_path)

    def test_supported_operations_and_stateless_unsupported_are_unchanged(
        self,
    ) -> None:
        handler = (PRIVATE / "EditorBridgeWriteHandlers.cpp").read_text(
            encoding="utf-8"
        )
        # The four stateless operations keep their own branches and the
        # no-transaction case still fails closed as unsupported.
        for operation in (
            "setVariableDefault",
            "setComponentProperty",
            "setPinDefault",
            "setAssetProperty",
        ):
            self.assertIn(operation, handler)
        self.assertIn(
            'OutErrorCode = TEXT("live-fast-verify-operation-unsupported");', handler
        )
        # The unsupported error name only appears once, after the transaction
        # branch, so it cannot pre-empt a retained read.
        self.assertEqual(handler.count("live-fast-verify-operation-unsupported"), 1)

    def test_retained_failure_codes_stay_fail_closed_in_the_helper(self) -> None:
        helper = (PRIVATE / "EditorBridgeRetainedLiveWrite.cpp").read_text(
            encoding="utf-8"
        )
        for code in (
            "live-fast-verify-transaction-not-found",
            "live-fast-verify-target-changed",
            "live-fast-verify-target-not-found",
            "live-fast-verify-read-failed",
        ):
            self.assertIn(code, helper)
        # A successful retained read still has to be reported as verified by
        # the shared tail of the handler.
        handler = (PRIVATE / "EditorBridgeWriteHandlers.cpp").read_text(
            encoding="utf-8"
        )
        tail = handler[handler.index("live-fast-verify-operation-unsupported") :]
        self.assertIn('Result->SetField(TEXT("value")', tail)
        self.assertIn("OutResult = Result;", tail)
        self.assertIn("return true;", tail)

    def test_wrapper_forwards_transaction_id_for_both_fast_verify_paths(self) -> None:
        live = (SRC / "workflow_live.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(
            live.count('"transactionId": record.transaction_id'),
            2,
        )


if __name__ == "__main__":
    unittest.main()
