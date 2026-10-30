#include "BlueprintEditBatch.h"

#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "Editor.h"
#include "Editor/Transactor.h"
#include "Engine/Blueprint.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "ScopedTransaction.h"
#include "UObject/Package.h"

namespace UEAgentKitBlueprintEdit
{
	namespace
	{
		bool FailBatch(
			FString& OutErrorCode,
			FString& OutErrorMessage,
			const TCHAR* Code,
			const FString& Message)
		{
			OutErrorCode = Code;
			OutErrorMessage = Message;
			return false;
		}
	}

	bool IsNodeCoordinateInRange(const int64 Value)
	{
		return Value >= -static_cast<int64>(MaxNodeCoordinate)
			&& Value <= static_cast<int64>(MaxNodeCoordinate);
	}

	bool ApplyNodeMoveBatch(
		UBlueprint* Blueprint,
		UEdGraph* Graph,
		const TArray<FNodeMoveRequest>& Requests,
		TArray<FNodeMoveResult>& OutResults,
		bool& bOutChanged,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		OutResults.Reset();
		bOutChanged = false;

		// Every failure leaves no partial result list behind, so a caller can
		// never mistake a half-validated batch for a validated one.
		auto Fail = [&](
			FString& Code,
			FString& Message,
			const TCHAR* ErrorCode,
			const FString& ErrorMessage)
		{
			OutResults.Reset();
			bOutChanged = false;
			return FailBatch(Code, Message, ErrorCode, ErrorMessage);
		};

		if (Blueprint == nullptr || Graph == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("node-move-batch-graph-unavailable"),
				TEXT("The exact Blueprint and graph must both be loaded."));
		}
		if (Requests.IsEmpty())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("node-move-batch-invalid-parameters"),
				TEXT("A node move batch must contain at least one node."));
		}
		if (Requests.Num() > MaxNodeMovesPerBatch)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("node-move-batch-limit-exceeded"),
				FString::Printf(
					TEXT("A node move batch accepts at most %d nodes."),
					MaxNodeMovesPerBatch));
		}

		// Index the live graph first.  A duplicate or invalid live GUID makes
		// every downstream identity claim unreliable, so it fails the whole
		// batch before any request is considered.
		TMap<FGuid, UEdGraphNode*> LiveNodes;
		for (UEdGraphNode* Node : Graph->Nodes)
		{
			if (Node == nullptr)
			{
				continue;
			}
			if (!Node->NodeGuid.IsValid())
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("node-move-batch-invalid-live-node"),
					TEXT("The live graph contains a node without a valid NodeGuid."));
			}
			if (LiveNodes.Contains(Node->NodeGuid))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("node-move-batch-duplicate-live-node"),
					TEXT("The live graph contains duplicate NodeGuid values, so no exact target can be proven."));
			}
			LiveNodes.Add(Node->NodeGuid, Node);
		}

		struct FPendingMove
		{
			UEdGraphNode* Node = nullptr;
			FIntPoint Target = FIntPoint::ZeroValue;
			FIntPoint Before = FIntPoint::ZeroValue;
		};

		TSet<FGuid> SeenGuids;
		TArray<FPendingMove> PendingMoves;
		OutResults.Reserve(Requests.Num());

		for (const FNodeMoveRequest& Request : Requests)
		{
			if (!Request.NodeGuid.IsValid())
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("node-move-batch-invalid-parameters"),
					TEXT("Every node request needs one valid NodeGuid."));
			}
			if (SeenGuids.Contains(Request.NodeGuid))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("node-move-batch-duplicate-node"),
					FString::Printf(
						TEXT("Node %s appears more than once in the batch."),
						*Request.NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower)));
			}
			SeenGuids.Add(Request.NodeGuid);

			// Coordinates are re-checked here even though the transport layer
			// already bounded them, so the helper stays safe for future callers.
			if (!IsNodeCoordinateInRange(Request.ExpectedX)
				|| !IsNodeCoordinateInRange(Request.ExpectedY)
				|| !IsNodeCoordinateInRange(Request.ToX)
				|| !IsNodeCoordinateInRange(Request.ToY))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("node-move-batch-coordinate-invalid"),
					FString::Printf(
						TEXT("Node %s has a coordinate outside [-1000000, 1000000]."),
						*Request.NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower)));
			}

			UEdGraphNode* const* FoundNode = LiveNodes.Find(Request.NodeGuid);
			if (FoundNode == nullptr || *FoundNode == nullptr)
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("node-move-batch-node-not-found"),
					FString::Printf(
						TEXT("No node in the exact graph matches %s."),
						*Request.NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower)));
			}
			UEdGraphNode* Node = *FoundNode;
			if (Node->NodePosX != Request.ExpectedX || Node->NodePosY != Request.ExpectedY)
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("node-move-batch-position-conflict"),
					FString::Printf(
						TEXT("Node %s is at (%d, %d), not the expected (%d, %d)."),
						*Request.NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower),
						Node->NodePosX,
						Node->NodePosY,
						Request.ExpectedX,
						Request.ExpectedY));
			}

			// Node positions are allowed to overlap: UE graph layout treats a
			// shared destination as legal, so the batch only rejects duplicate
			// identities, never duplicate target coordinates.
			const FIntPoint Target(Request.ToX, Request.ToY);

			FNodeMoveResult Result;
			Result.NodeGuid = Node->NodeGuid;
			Result.BeforeX = Node->NodePosX;
			Result.BeforeY = Node->NodePosY;
			Result.AfterX = Request.ToX;
			Result.AfterY = Request.ToY;
			Result.bChanged = Node->NodePosX != Request.ToX || Node->NodePosY != Request.ToY;
			OutResults.Add(Result);

			if (Result.bChanged)
			{
				FPendingMove Move;
				Move.Node = Node;
				Move.Target = Target;
				Move.Before = FIntPoint(Node->NodePosX, Node->NodePosY);
				PendingMoves.Add(Move);
			}
		}

		// A fully no-op batch is a successful read-only outcome: no transaction,
		// no Modify calls and no Dirty transition.
		if (PendingMoves.IsEmpty())
		{
			OutErrorCode.Reset();
			OutErrorMessage.Reset();
			return true;
		}

		// Re-check the write environment immediately before opening the one
		// transaction.  The caller already performs these checks; this is the
		// helper-level defence for future callers.
		if (GEditor == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("node-move-batch-editor-unavailable"),
				TEXT("The Unreal Editor is unavailable."));
		}
		if (GEditor->IsPlaySessionInProgress() || GEditor->PlayWorld != nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("node-move-batch-pie-active"),
				TEXT("A node move batch is unavailable while PIE or SIE is active."));
		}
		if (GEditor->Trans == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("node-move-batch-transaction-unavailable"),
				TEXT("The Editor transaction history is unavailable; the batch cannot be one Undo/Redo unit."));
		}
		if (!GEditor->CanTransact())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("node-move-batch-transaction-unavailable"),
				TEXT("The Editor cannot begin a transaction in its current loading/post-load state."));
		}
		if (GEditor->Trans->IsActive())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("node-move-batch-transaction-active"),
				TEXT("Another Editor transaction is already active; the batch must be its own Undo/Redo unit."));
		}

		UPackage* Package = Blueprint->GetOutermost();
		if (Package == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("node-move-batch-package-unavailable"),
				TEXT("The exact Blueprint has no package."));
		}
		if (Package->IsDirty())
		{
			// No ownership inference: a dirty package may contain user edits, a
			// prior Agent batch, or another plugin's changes.  Only the first
			// clean -> dirty batch is supported.
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("node-move-batch-package-dirty"),
				TEXT("The exact Blueprint package is already dirty; save or revert it before the first Agent batch."));
		}

		const TEnumAsByte<EBlueprintStatus> BlueprintStatusBefore = Blueprint->Status;
		const bool bCachedDependenciesBefore = Blueprint->bCachedDependenciesUpToDate;
		const bool bPackageDirtyBefore = Package->IsDirty();

		FScopedTransaction Transaction(
			TEXT("UEAgentKitNodeMoveBatch"),
			FText::FromString(TEXT("UE Agent Kit: Move Blueprint Nodes")),
			Blueprint);
		if (!Transaction.IsOutstanding())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("node-move-batch-transaction-unavailable"),
				TEXT("The Editor did not open an Undo transaction; no node position was changed."));
		}

		Blueprint->Modify();
		Graph->Modify();
		for (const FPendingMove& Move : PendingMoves)
		{
			Move.Node->Modify();
		}
		for (const FPendingMove& Move : PendingMoves)
		{
			Move.Node->NodePosX = Move.Target.X;
			Move.Node->NodePosY = Move.Target.Y;
		}

		auto RestoreAllAndCancel = [&]()
		{
			for (const FPendingMove& Move : PendingMoves)
			{
				Move.Node->NodePosX = Move.Before.X;
				Move.Node->NodePosY = Move.Before.Y;
			}
			Blueprint->Status = BlueprintStatusBefore;
			Blueprint->bCachedDependenciesUpToDate = bCachedDependenciesBefore;
			Package->SetDirtyFlag(bPackageDirtyBefore);
			Transaction.Cancel();
		};

		for (const FPendingMove& Move : PendingMoves)
		{
			if (Move.Node->NodePosX != Move.Target.X || Move.Node->NodePosY != Move.Target.Y)
			{
				RestoreAllAndCancel();
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("node-move-batch-readback-failed"),
					FString::Printf(
						TEXT("Node %s did not read back at (%d, %d); the whole batch was restored and its transaction cancelled."),
						*Move.Node->NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower),
						Move.Target.X,
						Move.Target.Y));
			}
		}

		FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
		if (!Package->IsDirty())
		{
			RestoreAllAndCancel();
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("node-move-batch-dirty-not-confirmed"),
				TEXT("The Editor did not confirm the Blueprint package became dirty; the whole batch was restored and its transaction cancelled."));
		}

		bOutChanged = true;
		OutErrorCode.Reset();
		OutErrorMessage.Reset();
		return true;
	}
}
