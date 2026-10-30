#include "EditorBridge.h"
#include "EditorBridgeHandlerUtils.h"
#include "BlueprintEditBatch.h"
#include "BlueprintEditSession.h"
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
#include "Subsystems/AssetEditorSubsystem.h"
#include "UObject/Package.h"
#include "UObject/UObjectGlobals.h"

using namespace UEAgentKitEditorBridgePrivate;

namespace
{
	bool FailBatchMove(
		FString& OutErrorCode,
		FString& OutErrorMessage,
		const TCHAR* Code,
		const FString& Message)
	{
		OutErrorCode = Code;
		OutErrorMessage = Message;
		return false;
	}

	bool IsCanonicalBatchGuid(const FString& Text, FGuid& OutGuid)
	{
		if (!FGuid::Parse(Text, OutGuid) || !OutGuid.IsValid())
		{
			return false;
		}
		return Text.Equals(
			OutGuid.ToString(EGuidFormats::DigitsWithHyphensLower),
			ESearchCase::CaseSensitive);
	}

	bool ReadBatchCoordinate(
		const TSharedPtr<FJsonObject>& Entry,
		const TCHAR* Key,
		int32& OutValue)
	{
		double Number = 0.0;
		if (!Entry.IsValid()
			|| !Entry->TryGetNumberField(Key, Number)
			|| !FMath::IsFinite(Number)
			|| Number != FMath::FloorToDouble(Number)
			|| Number < -static_cast<double>(UEAgentKitBlueprintEdit::MaxNodeCoordinate)
			|| Number > static_cast<double>(UEAgentKitBlueprintEdit::MaxNodeCoordinate)
			|| !UEAgentKitBlueprintEdit::IsNodeCoordinateInRange(
				static_cast<int64>(Number)))
		{
			return false;
		}
		OutValue = static_cast<int32>(Number);
		return true;
	}

	FString MapBatchLayoutErrorCode(const FString& Code)
	{
		const FString Prefix(TEXT("graph-layout-"));
		if (Code.StartsWith(Prefix, ESearchCase::CaseSensitive))
		{
			return FString(TEXT("node-move-batch-")) + Code.Mid(Prefix.Len());
		}
		return Code.IsEmpty() ? FString(TEXT("node-move-batch-target-unavailable")) : Code;
	}
}

bool FUEAgentKitEditorBridge::TryMoveBlueprintNodesResult(
	const TSharedPtr<FJsonObject>& Params,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	// Exact contract: assetPath, graphGuid, nodes and an optional
	// editSessionToken that only a live edit-session token can supply.
	if (!Params.IsValid()
		|| (Params->Values.Num() != 3 && Params->Values.Num() != 4))
	{
		return FailBatchMove(
			OutErrorCode,
			OutErrorMessage,
			TEXT("node-move-batch-invalid-parameters"),
			TEXT("Required: assetPath, graphGuid, nodes with optional editSessionToken."));
	}
	for (const TPair<FString, TSharedPtr<FJsonValue>>& Entry : Params->Values)
	{
		if (Entry.Key != TEXT("assetPath")
			&& Entry.Key != TEXT("graphGuid")
			&& Entry.Key != TEXT("nodes")
			&& Entry.Key != TEXT("editSessionToken"))
		{
			return FailBatchMove(
				OutErrorCode,
				OutErrorMessage,
				TEXT("node-move-batch-invalid-parameters"),
				TEXT("Unexpected node move batch parameter."));
		}
	}

	FString AssetPath;
	FString GraphGuidText;
	FGuid GraphGuid;
	if (!Params->TryGetStringField(TEXT("assetPath"), AssetPath)
		|| !IsSafeGameAssetPath(AssetPath)
		|| !Params->TryGetStringField(TEXT("graphGuid"), GraphGuidText)
		|| !IsCanonicalBatchGuid(GraphGuidText, GraphGuid))
	{
		return FailBatchMove(
			OutErrorCode,
			OutErrorMessage,
			TEXT("node-move-batch-invalid-parameters"),
			TEXT("assetPath must be one exact /Game Object Path and graphGuid must be a canonical lowercase GUID."));
	}

	FString EditSessionToken;
	if (Params->HasField(TEXT("editSessionToken"))
		&& (!Params->TryGetStringField(TEXT("editSessionToken"), EditSessionToken)
			|| !UEAgentKitBlueprintEdit::IsCanonicalEditSessionToken(EditSessionToken)))
	{
		return FailBatchMove(
			OutErrorCode,
			OutErrorMessage,
			TEXT("node-move-batch-invalid-parameters"),
			TEXT("editSessionToken must be the exact 64-character lowercase hex token returned by editor.beginBlueprintEditSession."));
	}
	const bool bEditSessionBatch = !EditSessionToken.IsEmpty();

	const TArray<TSharedPtr<FJsonValue>>* Nodes = nullptr;
	if (!Params->TryGetArrayField(TEXT("nodes"), Nodes) || Nodes == nullptr)
	{
		return FailBatchMove(
			OutErrorCode,
			OutErrorMessage,
			TEXT("node-move-batch-invalid-parameters"),
			TEXT("nodes must be an array of exact node move requests."));
	}
	if (Nodes->IsEmpty())
	{
		return FailBatchMove(
			OutErrorCode,
			OutErrorMessage,
			TEXT("node-move-batch-invalid-parameters"),
			TEXT("nodes must contain at least one node."));
	}
	if (Nodes->Num() > UEAgentKitBlueprintEdit::MaxNodeMovesPerBatch)
	{
		return FailBatchMove(
			OutErrorCode,
			OutErrorMessage,
			TEXT("node-move-batch-limit-exceeded"),
			FString::Printf(
				TEXT("A node move batch accepts at most %d nodes."),
				UEAgentKitBlueprintEdit::MaxNodeMovesPerBatch));
	}

	TArray<UEAgentKitBlueprintEdit::FNodeMoveRequest> Requests;
	Requests.Reserve(Nodes->Num());
	for (int32 Index = 0; Index < Nodes->Num(); ++Index)
	{
		TSharedPtr<FJsonObject> Entry;
		if ((*Nodes)[Index].IsValid() && (*Nodes)[Index]->Type == EJson::Object)
		{
			Entry = (*Nodes)[Index]->AsObject();
		}
		if (!Entry.IsValid() || Entry->Values.Num() != 5)
		{
			return FailBatchMove(
				OutErrorCode,
				OutErrorMessage,
				TEXT("node-move-batch-invalid-parameters"),
				FString::Printf(
					TEXT("nodes[%d] must contain exactly nodeGuid, expectedX, expectedY, x, y."),
					Index));
		}
		for (const TPair<FString, TSharedPtr<FJsonValue>>& Field : Entry->Values)
		{
			if (Field.Key != TEXT("nodeGuid")
				&& Field.Key != TEXT("expectedX")
				&& Field.Key != TEXT("expectedY")
				&& Field.Key != TEXT("x")
				&& Field.Key != TEXT("y"))
			{
				return FailBatchMove(
					OutErrorCode,
					OutErrorMessage,
					TEXT("node-move-batch-invalid-parameters"),
					FString::Printf(
						TEXT("nodes[%d] has an unexpected field."),
						Index));
			}
		}

		FString NodeGuidText;
		FGuid NodeGuid;
		UEAgentKitBlueprintEdit::FNodeMoveRequest Request;
		if (!Entry->TryGetStringField(TEXT("nodeGuid"), NodeGuidText)
			|| !IsCanonicalBatchGuid(NodeGuidText, NodeGuid))
		{
			return FailBatchMove(
				OutErrorCode,
				OutErrorMessage,
				TEXT("node-move-batch-invalid-parameters"),
				FString::Printf(
					TEXT("nodes[%d].nodeGuid must be one canonical lowercase hyphenated GUID."),
					Index));
		}
		if (!ReadBatchCoordinate(Entry, TEXT("expectedX"), Request.ExpectedX)
			|| !ReadBatchCoordinate(Entry, TEXT("expectedY"), Request.ExpectedY)
			|| !ReadBatchCoordinate(Entry, TEXT("x"), Request.ToX)
			|| !ReadBatchCoordinate(Entry, TEXT("y"), Request.ToY))
		{
			return FailBatchMove(
				OutErrorCode,
				OutErrorMessage,
				TEXT("node-move-batch-coordinate-invalid"),
				FString::Printf(
					TEXT("nodes[%d] must use integer coordinates in [-1000000, 1000000]."),
					Index));
		}
		Request.NodeGuid = NodeGuid;
		Requests.Add(Request);
	}

	if (GEditor == nullptr)
	{
		return FailBatchMove(
			OutErrorCode,
			OutErrorMessage,
			TEXT("node-move-batch-editor-unavailable"),
			TEXT("The Unreal Editor is unavailable."));
	}
	if (GEditor->IsPlaySessionInProgress() || GEditor->PlayWorld != nullptr)
	{
		return FailBatchMove(
			OutErrorCode,
			OutErrorMessage,
			TEXT("node-move-batch-pie-active"),
			TEXT("Node movement is unavailable while PIE or SIE is active."));
	}
	if (GEditor->Trans == nullptr)
	{
		return FailBatchMove(
			OutErrorCode,
			OutErrorMessage,
			TEXT("node-move-batch-transaction-unavailable"),
			TEXT("The Editor transaction history is unavailable; the batch cannot be one Undo/Redo unit."));
	}
	if (!GEditor->CanTransact())
	{
		return FailBatchMove(
			OutErrorCode,
			OutErrorMessage,
			TEXT("node-move-batch-transaction-unavailable"),
			TEXT("The Editor cannot begin a transaction in its current loading/post-load state."));
	}
	if (GEditor->Trans->IsActive())
	{
		return FailBatchMove(
			OutErrorCode,
			OutErrorMessage,
			TEXT("node-move-batch-transaction-active"),
			TEXT("Another Editor transaction is already active; the batch must be its own Undo/Redo unit."));
	}

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
		OutErrorCode = MapBatchLayoutErrorCode(OutErrorCode);
		return false;
	}

	UAssetEditorSubsystem* AssetEditorSubsystem = GEditor->GetEditorSubsystem<UAssetEditorSubsystem>();
	if (AssetEditorSubsystem == nullptr)
	{
		return FailBatchMove(
			OutErrorCode,
			OutErrorMessage,
			TEXT("node-move-batch-editor-unavailable"),
			TEXT("The Asset Editor Subsystem is unavailable."));
	}
	IAssetEditorInstance* EditorInstance = AssetEditorSubsystem->FindEditorForAsset(Blueprint, false);
	if (EditorInstance == nullptr || EditorInstance->GetEditorName() != FName(TEXT("BlueprintEditor")))
	{
		return FailBatchMove(
			OutErrorCode,
			OutErrorMessage,
			TEXT("node-move-batch-blueprint-not-open"),
			TEXT("The exact loaded Blueprint must already be open in an ordinary Blueprint Editor."));
	}
	FBlueprintEditor* BlueprintEditor = static_cast<FBlueprintEditor*>(EditorInstance);
	if (BlueprintEditor->GetBlueprintObj() != Blueprint)
	{
		return FailBatchMove(
			OutErrorCode,
			OutErrorMessage,
			TEXT("node-move-batch-editor-mismatch"),
			TEXT("The Blueprint Editor does not own the exact requested Blueprint."));
	}
	if (BlueprintEditor->GetFocusedGraph() != Graph)
	{
		return FailBatchMove(
			OutErrorCode,
			OutErrorMessage,
			TEXT("node-move-batch-graph-not-focused"),
			TEXT("Open and focus the exact target GraphGuid before moving nodes."));
	}
	if (!BlueprintEditor->IsEditable(Graph) || !Graph->bEditable)
	{
		return FailBatchMove(
			OutErrorCode,
			OutErrorMessage,
			TEXT("node-move-batch-graph-not-editable"),
			TEXT("The focused graph is not editable in the current Editor state."));
	}
	if (Cast<UEdGraphSchema_K2>(Graph->GetSchema()) == nullptr)
	{
		return FailBatchMove(
			OutErrorCode,
			OutErrorMessage,
			TEXT("node-move-batch-graph-not-k2"),
			TEXT("Node movement currently accepts only an ordinary K2 Blueprint graph."));
	}

	TArray<UEAgentKitBlueprintEdit::FNodeMoveResult> Results;
	bool bChanged = false;
	UPackage* Package = Blueprint->GetOutermost();
	const bool bPackageDirtyBefore = Package != nullptr && Package->IsDirty();

	if (bEditSessionBatch)
	{
		// A token batch is authorized only by the live session manager, which
		// proves the bound target, the expected content fingerprint, every node
		// position and the package Dirty state before any write.  There is no
		// request parameter that selects the dirty gate directly.
		if (!BlueprintEditSessionManager.IsValid())
		{
			return FailBatchMove(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-edit-session-token-invalid"),
				TEXT("The Blueprint edit session manager is unavailable; the token cannot be validated."));
		}
		if (!BlueprintEditSessionManager->ValidateBatch(
			Blueprint,
			Graph,
			AssetPath,
			GraphGuid,
			SessionId,
			EditSessionToken,
			Requests,
			OutErrorCode,
			OutErrorMessage))
		{
			return false;
		}
	}
	else if (BlueprintEditSessionManager.IsValid()
		&& BlueprintEditSessionManager->HasActiveSessionFor(AssetPath, GraphGuid))
	{
		// An active session for this exact target must own every further batch,
		// otherwise a token-less clean->dirty batch could silently invalidate
		// the session's expected content fingerprint.
		return FailBatchMove(
			OutErrorCode,
			OutErrorMessage,
			TEXT("blueprint-edit-session-active-token-required"),
			TEXT("An Agent edit session is active for this exact Blueprint graph; every further batch must pass its editSessionToken."));
	}

	if (!UEAgentKitBlueprintEdit::ApplyNodeMoveBatch(
		Blueprint,
		Graph,
		Requests,
		Results,
		bChanged,
		OutErrorCode,
		OutErrorMessage,
		bEditSessionBatch
			? UEAgentKitBlueprintEdit::EPackageDirtyGate::AllowSessionOwnedDirty
			: UEAgentKitBlueprintEdit::EPackageDirtyGate::RequireClean))
	{
		return false;
	}
	if (bEditSessionBatch)
	{
		if (!BlueprintEditSessionManager->CommitBatch(
			Blueprint,
			Graph,
			bChanged,
			OutErrorCode,
			OutErrorMessage))
		{
			return false;
		}
	}
	TArray<TSharedPtr<FJsonValue>> NodeValues;
	NodeValues.Reserve(Results.Num());
	int32 ChangedCount = 0;
	for (const UEAgentKitBlueprintEdit::FNodeMoveResult& Item : Results)
	{
		if (Item.bChanged)
		{
			++ChangedCount;
		}
		TSharedRef<FJsonObject> NodeValue = MakeShared<FJsonObject>();
		NodeValue->SetStringField(
			TEXT("nodeGuid"),
			Item.NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower));
		NodeValue->SetNumberField(TEXT("beforeX"), Item.BeforeX);
		NodeValue->SetNumberField(TEXT("beforeY"), Item.BeforeY);
		NodeValue->SetNumberField(TEXT("nodePosX"), Item.AfterX);
		NodeValue->SetNumberField(TEXT("nodePosY"), Item.AfterY);
		NodeValue->SetBoolField(TEXT("changed"), Item.bChanged);
		NodeValues.Add(MakeShared<FJsonValueObject>(NodeValue));
	}

	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("action"), TEXT("move-blueprint-nodes"));
	Result->SetStringField(TEXT("source"), TEXT("exact-open-blueprint-editor"));
	Result->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
	Result->SetStringField(
		TEXT("graphGuid"),
		Graph->GraphGuid.ToString(EGuidFormats::DigitsWithHyphensLower));
	Result->SetNumberField(TEXT("nodeCount"), Results.Num());
	Result->SetNumberField(TEXT("changedNodeCount"), ChangedCount);
	Result->SetArrayField(TEXT("nodes"), NodeValues);
	Result->SetBoolField(TEXT("changed"), bChanged);
	Result->SetBoolField(TEXT("packageDirtyBefore"), bPackageDirtyBefore);
	Result->SetBoolField(
		TEXT("packageDirtyAfter"),
		Package != nullptr && Package->IsDirty());
	Result->SetBoolField(TEXT("singleTransaction"), bChanged);
	Result->SetBoolField(TEXT("editSessionUsed"), bEditSessionBatch);
	// No UI lock exists: a session authorizes Agent batches by content
	// fingerprint only, it does not block a person from editing the graph.
	Result->SetBoolField(TEXT("exclusiveUI"), false);
	Result->SetBoolField(TEXT("humanEditingBlocked"), false);
	Result->SetStringField(
		TEXT("transactionTitle"),
		TEXT("UE Agent Kit: Move Blueprint Nodes"));
	// Saving, compiling and strong verification are explicit follow-up actions,
	// never implicit side effects of a position-only batch.
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
