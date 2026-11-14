#include "EditorBridge.h"
#include "EditorBridgeHandlerUtils.h"
#include "BlueprintGraphLayoutApply.h"

#include "BlueprintEditor.h"
#include "Dom/JsonObject.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraphSchema_K2.h"
#include "Editor.h"
#include "Editor/Transactor.h"
#include "Engine/Blueprint.h"
#include "HAL/PlatformProcess.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "ScopedTransaction.h"
#include "Subsystems/AssetEditorSubsystem.h"
#include "UObject/Package.h"
#include "UObject/UObjectGlobals.h"

using namespace UEAgentKitEditorBridgePrivate;

namespace
{
	constexpr int32 MaxNodeCoordinate = 1000000;
	constexpr const TCHAR* NodeMoveTransactionTitle = TEXT("UE Agent Kit: Move Blueprint Node");

	bool FailMove(FString& OutErrorCode, FString& OutErrorMessage, const TCHAR* Code, const FString& Message)
	{
		OutErrorCode = Code;
		OutErrorMessage = Message;
		return false;
	}

	bool ReadCoordinate(
		const TSharedPtr<FJsonObject>& Params,
		const TCHAR* Key,
		int32& OutValue,
		FString& ErrorCode,
		FString& ErrorMessage)
	{
		double Number = 0.0;
		if (!Params.IsValid()
			|| !Params->TryGetNumberField(Key, Number)
			|| !FMath::IsFinite(Number)
			|| Number != FMath::FloorToDouble(Number)
			|| Number < -MaxNodeCoordinate
			|| Number > MaxNodeCoordinate)
		{
			ErrorCode = TEXT("node-move-coordinate-invalid");
			ErrorMessage = FString::Printf(
				TEXT("%s must be an integer in [-1000000, 1000000]."),
				Key);
			return false;
		}
		OutValue = static_cast<int32>(Number);
		return true;
	}

	bool IsCanonicalGuid(const FString& Text, FGuid& OutGuid)
	{
		if (!FGuid::Parse(Text, OutGuid) || !OutGuid.IsValid())
		{
			return false;
		}
		return Text.Equals(
			OutGuid.ToString(EGuidFormats::DigitsWithHyphensLower),
			ESearchCase::CaseSensitive);
	}

	FString MapLayoutErrorCode(const FString& Code)
	{
		const FString Prefix(TEXT("graph-layout-"));
		if (Code.StartsWith(Prefix, ESearchCase::CaseSensitive))
		{
			return FString(TEXT("node-move-")) + Code.Mid(Prefix.Len());
		}
		return Code.IsEmpty() ? FString(TEXT("node-move-target-unavailable")) : Code;
	}

	FString DescribeNodeKind(const UEdGraphNode* Node)
	{
		if (Node == nullptr || Node->GetClass() == nullptr)
		{
			return TEXT("node");
		}
		const FString ClassPath = Node->GetClass()->GetPathName();
		if (ClassPath.EndsWith(TEXT(".EdGraphNode_Comment"), ESearchCase::CaseSensitive))
		{
			return TEXT("comment");
		}
		if (ClassPath.EndsWith(TEXT(".K2Node_Knot"), ESearchCase::CaseSensitive))
		{
			return TEXT("reroute");
		}
		return TEXT("node");
	}
}

bool FUEAgentKitEditorBridge::TryMoveBlueprintNodeResult(
	const TSharedPtr<FJsonObject>& Params,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	// Exact eight-field contract; unknown fields are rejected instead of ignored.
	if (!Params.IsValid() || Params->Values.Num() != 8)
	{
		return FailMove(
			OutErrorCode,
			OutErrorMessage,
			TEXT("node-move-invalid-parameters"),
			TEXT("Required: assetPath, graphGuid, nodeGuid, x, y, expectedX, expectedY, expectedDirty."));
	}
	for (const TPair<FString, TSharedPtr<FJsonValue>>& Entry : Params->Values)
	{
		if (Entry.Key != TEXT("assetPath")
			&& Entry.Key != TEXT("graphGuid")
			&& Entry.Key != TEXT("nodeGuid")
			&& Entry.Key != TEXT("x")
			&& Entry.Key != TEXT("y")
			&& Entry.Key != TEXT("expectedX")
			&& Entry.Key != TEXT("expectedY")
			&& Entry.Key != TEXT("expectedDirty"))
		{
			return FailMove(
				OutErrorCode,
				OutErrorMessage,
				TEXT("node-move-invalid-parameters"),
				TEXT("Unexpected Node Move parameter."));
		}
	}

	FString AssetPath;
	FString GraphGuidText;
	FString NodeGuidText;
	FGuid GraphGuid;
	FGuid NodeGuid;
	bool bExpectedDirty = false;
	if (!Params->TryGetStringField(TEXT("assetPath"), AssetPath)
		|| !IsSafeGameAssetPath(AssetPath)
		|| !Params->TryGetStringField(TEXT("graphGuid"), GraphGuidText)
		|| !IsCanonicalGuid(GraphGuidText, GraphGuid)
		|| !Params->TryGetStringField(TEXT("nodeGuid"), NodeGuidText)
		|| !IsCanonicalGuid(NodeGuidText, NodeGuid)
		|| !Params->TryGetBoolField(TEXT("expectedDirty"), bExpectedDirty))
	{
		return FailMove(
			OutErrorCode,
			OutErrorMessage,
			TEXT("node-move-invalid-parameters"),
			TEXT("assetPath must be one exact /Game Object Path; graphGuid/nodeGuid must be canonical lowercase GUIDs."));
	}

	int32 X = 0;
	int32 Y = 0;
	int32 ExpectedX = 0;
	int32 ExpectedY = 0;
	if (!ReadCoordinate(Params, TEXT("x"), X, OutErrorCode, OutErrorMessage)
		|| !ReadCoordinate(Params, TEXT("y"), Y, OutErrorCode, OutErrorMessage)
		|| !ReadCoordinate(Params, TEXT("expectedX"), ExpectedX, OutErrorCode, OutErrorMessage)
		|| !ReadCoordinate(Params, TEXT("expectedY"), ExpectedY, OutErrorCode, OutErrorMessage))
	{
		return false;
	}

	// Dirty ownership: the Bridge cannot currently prove that an already-dirty
	// package was changed only by this Agent.  In particular a session-scoped
	// path cache would keep allowing writes after a manual user edit, Undo/Redo,
	// Save, reload, or a different Editor session.  Fail closed and support only
	// the first clean -> dirty move.  Further Agent moves require an explicit
	// Save or revert between calls, so `changed` is never overstated.
	if (bExpectedDirty)
	{
		return FailMove(
			OutErrorCode,
			OutErrorMessage,
			TEXT("node-move-dirty-ownership-unproven"),
			TEXT("expectedDirty=true is unsupported: the Bridge cannot prove this session owns the dirty state. Save or revert the Blueprint, then move from a clean package."));
	}

	if (GEditor == nullptr)
	{
		return FailMove(
			OutErrorCode,
			OutErrorMessage,
			TEXT("node-move-editor-unavailable"),
			TEXT("The Unreal Editor is unavailable."));
	}
	if (GEditor->IsPlaySessionInProgress() || GEditor->PlayWorld != nullptr)
	{
		return FailMove(
			OutErrorCode,
			OutErrorMessage,
			TEXT("node-move-pie-active"),
			TEXT("Node movement is unavailable while PIE or SIE is active."));
	}
	if (GEditor->Trans == nullptr)
	{
		return FailMove(
			OutErrorCode,
			OutErrorMessage,
			TEXT("node-move-transaction-unavailable"),
			TEXT("The Editor transaction history is unavailable; the move cannot be one Undo/Redo unit."));
	}
	if (!GEditor->CanTransact())
	{
		return FailMove(
			OutErrorCode,
			OutErrorMessage,
			TEXT("node-move-transaction-unavailable"),
			TEXT("The Editor cannot begin a transaction in its current loading/post-load state."));
	}
	if (GEditor->Trans->IsActive())
	{
		return FailMove(
			OutErrorCode,
			OutErrorMessage,
			TEXT("node-move-transaction-active"),
			TEXT("Another Editor transaction is already active; the move must be its own Undo/Redo unit."));
	}

	// Reuse the existing fail-closed exact-target resolver: it never loads or
	// creates an asset, requires one exact /Game non-map Blueprint package and
	// exactly one graph with the requested GraphGuid on that Blueprint.
	UBlueprint* Blueprint = nullptr;
	UEdGraph* Graph = nullptr;
	if (!UEAgentKitBlueprintGraphLayout::ResolveExactTarget(
		AssetPath,
		GraphGuidText,
		Blueprint,
		Graph,
		OutErrorCode,
		OutErrorMessage))
	{
		OutErrorCode = MapLayoutErrorCode(OutErrorCode);
		return false;
	}

	UAssetEditorSubsystem* AssetEditorSubsystem = GEditor->GetEditorSubsystem<UAssetEditorSubsystem>();
	if (AssetEditorSubsystem == nullptr)
	{
		return FailMove(
			OutErrorCode,
			OutErrorMessage,
			TEXT("node-move-editor-unavailable"),
			TEXT("The Asset Editor Subsystem is unavailable."));
	}
	IAssetEditorInstance* EditorInstance = AssetEditorSubsystem->FindEditorForAsset(Blueprint, false);
	if (EditorInstance == nullptr || EditorInstance->GetEditorName() != FName(TEXT("BlueprintEditor")))
	{
		return FailMove(
			OutErrorCode,
			OutErrorMessage,
			TEXT("node-move-blueprint-not-open"),
			TEXT("The exact loaded Blueprint must already be open in an ordinary Blueprint Editor."));
	}
	FBlueprintEditor* BlueprintEditor = static_cast<FBlueprintEditor*>(EditorInstance);
	if (BlueprintEditor->GetBlueprintObj() != Blueprint)
	{
		return FailMove(
			OutErrorCode,
			OutErrorMessage,
			TEXT("node-move-editor-mismatch"),
			TEXT("The Blueprint Editor does not own the exact requested Blueprint."));
	}
	// The exact graph passed in must be the graph this Editor is showing.  This
	// prevents a stale GraphGuid (or a focus switch in another tab/window) from
	// moving a node in a graph the user is not looking at.
	if (BlueprintEditor->GetFocusedGraph() != Graph)
	{
		return FailMove(
			OutErrorCode,
			OutErrorMessage,
			TEXT("node-move-graph-not-focused"),
			TEXT("Open and focus the exact target GraphGuid before moving a node."));
	}
	if (!BlueprintEditor->IsEditable(Graph) || !Graph->bEditable)
	{
		return FailMove(
			OutErrorCode,
			OutErrorMessage,
			TEXT("node-move-graph-not-editable"),
			TEXT("The focused graph is not editable in the current Editor state."));
	}
	if (Cast<UEdGraphSchema_K2>(Graph->GetSchema()) == nullptr)
	{
		return FailMove(
			OutErrorCode,
			OutErrorMessage,
			TEXT("node-move-graph-not-k2"),
			TEXT("Node movement currently accepts only an ordinary K2 Blueprint graph."));
	}

	UEdGraphNode* Node = nullptr;
	for (UEdGraphNode* Candidate : Graph->Nodes)
	{
		if (!IsValid(Candidate) || Candidate->NodeGuid != NodeGuid)
		{
			continue;
		}
		if (Node != nullptr)
		{
			return FailMove(
				OutErrorCode,
				OutErrorMessage,
				TEXT("node-move-ambiguous-guid"),
				TEXT("More than one node in the exact graph has the requested NodeGuid."));
		}
		Node = Candidate;
	}
	if (Node == nullptr)
	{
		return FailMove(
			OutErrorCode,
			OutErrorMessage,
			TEXT("node-move-node-not-found"),
			TEXT("No node in the exact focused graph matches nodeGuid."));
	}

	// Optimistic concurrency: the caller must have read this exact position.
	if (Node->NodePosX != ExpectedX || Node->NodePosY != ExpectedY)
	{
		return FailMove(
			OutErrorCode,
			OutErrorMessage,
			TEXT("node-move-position-conflict"),
			FString::Printf(
				TEXT("The live node position is (%d, %d), not the expected (%d, %d)."),
				Node->NodePosX,
				Node->NodePosY,
				ExpectedX,
				ExpectedY));
	}

	UPackage* Package = Blueprint->GetOutermost();
	if (Package == nullptr)
	{
		return FailMove(
			OutErrorCode,
			OutErrorMessage,
			TEXT("node-move-package-unavailable"),
			TEXT("The exact Blueprint has no package."));
	}
	if (Package->IsDirty())
	{
		// This is deliberately not an ownership claim: any dirty package,
		// including one this session dirtied with an earlier move, is refused
		// until the user explicitly saves or reverts it.
		return FailMove(
			OutErrorCode,
			OutErrorMessage,
			TEXT("node-move-package-dirty"),
			TEXT("The exact Blueprint package is already dirty; save or revert it before the first Agent move."));
	}

	const bool bChanged = Node->NodePosX != X || Node->NodePosY != Y;
	if (!bChanged)
	{
		// A no-op must not open a transaction, mark the package dirty, or claim
		// that any change (or an Undo entry) was produced.
		TSharedRef<FJsonObject> NoOp = MakeShared<FJsonObject>();
		NoOp->SetStringField(TEXT("action"), TEXT("move-blueprint-node"));
		NoOp->SetStringField(TEXT("source"), TEXT("exact-open-blueprint-editor"));
		NoOp->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
		NoOp->SetStringField(
			TEXT("graphGuid"),
			Graph->GraphGuid.ToString(EGuidFormats::DigitsWithHyphensLower));
		NoOp->SetStringField(
			TEXT("nodeGuid"),
			Node->NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower));
		NoOp->SetStringField(
			TEXT("nodeClassPath"),
			Node->GetClass() != nullptr ? Node->GetClass()->GetPathName() : FString());
		NoOp->SetStringField(TEXT("nodeKind"), DescribeNodeKind(Node));
		NoOp->SetNumberField(TEXT("beforeX"), Node->NodePosX);
		NoOp->SetNumberField(TEXT("beforeY"), Node->NodePosY);
		NoOp->SetNumberField(TEXT("nodePosX"), Node->NodePosX);
		NoOp->SetNumberField(TEXT("nodePosY"), Node->NodePosY);
		NoOp->SetBoolField(TEXT("changed"), false);
		NoOp->SetBoolField(TEXT("packageDirtyBefore"), false);
		NoOp->SetBoolField(TEXT("packageDirtyAfter"), false);
		NoOp->SetBoolField(TEXT("singleTransaction"), false);
		NoOp->SetBoolField(TEXT("saved"), false);
		NoOp->SetBoolField(TEXT("compiled"), false);
		NoOp->SetStringField(TEXT("evidenceLevel"), TEXT("same-process"));
		NoOp->SetBoolField(TEXT("acceptanceEligible"), false);
		NoOp->SetStringField(TEXT("requiredAcceptanceEvidence"), TEXT("independent-reload"));
		NoOp->SetStringField(TEXT("editorSessionId"), SessionId);
		NoOp->SetNumberField(
			TEXT("editorProcessId"),
			static_cast<double>(FPlatformProcess::GetCurrentProcessId()));
		OutResult = NoOp;
		return true;
	}

	const TEnumAsByte<EBlueprintStatus> BlueprintStatusBefore = Blueprint->Status;
	const bool bCachedDependenciesBefore = Blueprint->bCachedDependenciesUpToDate;
	const int32 PreviousX = Node->NodePosX;
	const int32 PreviousY = Node->NodePosY;

	FScopedTransaction Transaction(
		TEXT("UEAgentKitNodeMove"),
		FText::FromString(NodeMoveTransactionTitle),
		Blueprint);
	if (!Transaction.IsOutstanding())
	{
		return FailMove(
			OutErrorCode,
			OutErrorMessage,
			TEXT("node-move-transaction-unavailable"),
			TEXT("The Editor did not open an Undo transaction; no node position was changed."));
	}

	Blueprint->Modify();
	Graph->Modify();
	Node->Modify();
	Node->NodePosX = X;
	Node->NodePosY = Y;

	auto RestoreAndCancel = [&]()
	{
		Node->NodePosX = PreviousX;
		Node->NodePosY = PreviousY;
		Blueprint->Status = BlueprintStatusBefore;
		Blueprint->bCachedDependenciesUpToDate = bCachedDependenciesBefore;
		Package->SetDirtyFlag(false);
		Transaction.Cancel();
	};

	if (Node->NodePosX != X || Node->NodePosY != Y)
	{
		RestoreAndCancel();
		return FailMove(
			OutErrorCode,
			OutErrorMessage,
			TEXT("node-move-readback-failed"),
			TEXT("Node position readback disagrees with the requested coordinates; the transaction was cancelled."));
	}

	FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
	if (!Package->IsDirty())
	{
		RestoreAndCancel();
		return FailMove(
			OutErrorCode,
			OutErrorMessage,
			TEXT("node-move-dirty-not-confirmed"),
			TEXT("The Editor did not confirm the Blueprint package became dirty; the transaction was cancelled."));
	}

	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("action"), TEXT("move-blueprint-node"));
	Result->SetStringField(TEXT("source"), TEXT("exact-open-blueprint-editor"));
	Result->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
	Result->SetStringField(
		TEXT("graphGuid"),
		Graph->GraphGuid.ToString(EGuidFormats::DigitsWithHyphensLower));
	Result->SetStringField(
		TEXT("nodeGuid"),
		Node->NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower));
	Result->SetStringField(
		TEXT("nodeClassPath"),
		Node->GetClass() != nullptr ? Node->GetClass()->GetPathName() : FString());
	Result->SetStringField(TEXT("nodeKind"), DescribeNodeKind(Node));
	Result->SetNumberField(TEXT("beforeX"), PreviousX);
	Result->SetNumberField(TEXT("beforeY"), PreviousY);
	Result->SetNumberField(TEXT("nodePosX"), Node->NodePosX);
	Result->SetNumberField(TEXT("nodePosY"), Node->NodePosY);
	Result->SetBoolField(TEXT("changed"), true);
	Result->SetBoolField(TEXT("packageDirtyBefore"), false);
	Result->SetBoolField(TEXT("packageDirtyAfter"), Package->IsDirty());
	Result->SetBoolField(TEXT("singleTransaction"), true);
	Result->SetStringField(TEXT("transactionTitle"), NodeMoveTransactionTitle);
	// Saving, compiling and strong verification are explicit follow-up actions,
	// never implicit side effects of a position-only move.
	Result->SetBoolField(TEXT("saved"), false);
	Result->SetBoolField(TEXT("compiled"), false);
	Result->SetStringField(TEXT("evidenceLevel"), TEXT("same-process"));
	Result->SetBoolField(TEXT("acceptanceEligible"), false);
	Result->SetStringField(TEXT("requiredAcceptanceEvidence"), TEXT("independent-reload"));
	Result->SetStringField(TEXT("editorSessionId"), SessionId);
	Result->SetNumberField(
		TEXT("editorProcessId"),
		static_cast<double>(FPlatformProcess::GetCurrentProcessId()));
	OutResult = Result;
	return true;
}
