#include "BlueprintGraphLayoutExperimental.h"

#include "BlueprintContextSha256.h"
#include "BlueprintGraphLayoutApply.h"

#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "Editor.h"
#include "Engine/Blueprint.h"
#include "HAL/FileManager.h"
#include "Misc/ConfigCacheIni.h"
#include "Misc/PackageName.h"
#include "Misc/Paths.h"
#include "Misc/SecureHash.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"

namespace
{
	bool Fail(FString& OutErrorCode, FString& OutErrorMessage, const TCHAR* Code, const FString& Message)
	{
		OutErrorCode = Code;
		OutErrorMessage = Message;
		return false;
	}

	bool bTestOverrideSet = false;
	bool bTestOverrideEnabled = false;

	bool IsCanonicalSha1(const FString& Value)
	{
		if (!Value.StartsWith(TEXT("sha1:"), ESearchCase::CaseSensitive) || Value.Len() != 45)
		{
			return false;
		}
		for (int32 Index = 5; Index < Value.Len(); ++Index)
		{
			if (!FChar::IsHexDigit(Value[Index]))
			{
				return false;
			}
		}
		return true;
	}

	bool IsCanonicalSha256(const FString& Value)
	{
		if (!Value.StartsWith(TEXT("sha256:"), ESearchCase::CaseSensitive) || Value.Len() != 71)
		{
			return false;
		}
		for (int32 Index = 7; Index < Value.Len(); ++Index)
		{
			if (!FChar::IsHexDigit(Value[Index]))
			{
				return false;
			}
		}
		return true;
	}

	bool ParseNodeId(const FString& NodeId, FGuid& OutGuid)
	{
		if (!NodeId.StartsWith(TEXT("n:"), ESearchCase::CaseSensitive))
		{
			return false;
		}
		return FGuid::Parse(NodeId.Mid(2), OutGuid) && OutGuid.IsValid();
	}

	FString Sha1Text(const FString& Text)
	{
		FTCHARToUTF8 Utf8(*Text);
		uint8 Digest[20];
		FSHA1::HashBuffer(Utf8.Get(), static_cast<uint32>(Utf8.Length()), Digest);
		FString Hex;
		Hex.Reserve(40);
		for (const uint8 Byte : Digest)
		{
			Hex += FString::Printf(TEXT("%02x"), static_cast<uint32>(Byte));
		}
		return FString(TEXT("sha1:")) + Hex;
	}

	FString StructuralState(UEdGraph* Graph)
	{
		TArray<UEdGraphNode*> SortedNodes;
		for (UEdGraphNode* Node : Graph->Nodes)
		{
			if (Node != nullptr)
			{
				SortedNodes.Add(Node);
			}
		}
		SortedNodes.Sort([](const UEdGraphNode& Left, const UEdGraphNode& Right)
		{
			return Left.NodeGuid.ToString(EGuidFormats::Digits)
				< Right.NodeGuid.ToString(EGuidFormats::Digits);
		});

		FString State;
		for (const UEdGraphNode* Node : SortedNodes)
		{
			State += Node->NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower);
			State += TEXT("|");
			State += Node->GetClass() != nullptr ? Node->GetClass()->GetPathName() : FString();
			State += TEXT("|");
			State += Node->NodeComment;
			State += TEXT("|");
			State += FString::Printf(TEXT("%d|%d|"), Node->NodeWidth, Node->NodeHeight);
			TArray<FString> Pins;
			for (const UEdGraphPin* Pin : Node->Pins)
			{
				if (Pin == nullptr)
				{
					continue;
				}
				TArray<FString> Links;
				for (const UEdGraphPin* Linked : Pin->LinkedTo)
				{
					if (Linked != nullptr && Linked->GetOwningNode() != nullptr)
					{
						Links.Add(
							Linked->GetOwningNode()->NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower)
							+ TEXT("/")
							+ Linked->PinName.ToString());
					}
				}
				Links.Sort();
				Pins.Add(
					Pin->PinName.ToString()
					+ TEXT(":")
					+ Pin->PinType.PinCategory.ToString()
					+ TEXT(":")
					+ FString::FromInt(static_cast<int32>(Pin->Direction))
					+ TEXT("{")
					+ FString::Join(Links, TEXT(","))
					+ TEXT("}"));
			}
			Pins.Sort();
			State += FString::Join(Pins, TEXT(","));
			State += TEXT(";");
		}
		return State;
	}

	TMap<FGuid, FIntPoint> CapturePositions(UEdGraph* Graph)
	{
		TMap<FGuid, FIntPoint> Result;
		for (UEdGraphNode* Node : Graph->Nodes)
		{
			if (Node != nullptr)
			{
				Result.Add(Node->NodeGuid, FIntPoint(Node->NodePosX, Node->NodePosY));
			}
		}
		return Result;
	}

	bool PositionsEqual(
		const TMap<FGuid, FIntPoint>& Left,
		const TMap<FGuid, FIntPoint>& Right)
	{
		if (Left.Num() != Right.Num())
		{
			return false;
		}
		for (const TPair<FGuid, FIntPoint>& Pair : Left)
		{
			const FIntPoint* Other = Right.Find(Pair.Key);
			if (Other == nullptr || *Other != Pair.Value)
			{
				return false;
			}
		}
		return true;
	}

	TArray<TSharedPtr<FJsonValue>> SerializePositions(const TMap<FGuid, FIntPoint>& Positions)
	{
		TArray<TSharedPtr<FJsonValue>> Values;
		for (const TPair<FGuid, FIntPoint>& Pair : Positions)
		{
			TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
			Item->SetStringField(
				TEXT("nodeId"),
				FString(TEXT("n:")) + Pair.Key.ToString(EGuidFormats::DigitsWithHyphensLower));
			Item->SetNumberField(TEXT("x"), Pair.Value.X);
			Item->SetNumberField(TEXT("y"), Pair.Value.Y);
			Values.Add(MakeShared<FJsonValueObject>(Item));
		}
		return Values;
	}

	bool ReadPlanTargets(
		const TSharedPtr<FJsonObject>& Request,
		TMap<FGuid, FIntPoint>& OutTargets,
		FString& OutError)
	{
		const TSharedPtr<FJsonObject>* PlanField = nullptr;
		if (!Request.IsValid()
			|| !Request->TryGetObjectField(TEXT("plan"), PlanField)
			|| PlanField == nullptr
			|| !PlanField->IsValid())
		{
			OutError = TEXT("The request has no plan object.");
			return false;
		}
		const TArray<TSharedPtr<FJsonValue>>* Positions = nullptr;
		if (!(*PlanField)->TryGetArrayField(TEXT("positions"), Positions) || Positions == nullptr)
		{
			OutError = TEXT("The request plan has no positions array.");
			return false;
		}
		for (const TSharedPtr<FJsonValue>& Entry : *Positions)
		{
			const TSharedPtr<FJsonObject> Position =
				Entry.IsValid() && Entry->Type == EJson::Object ? Entry->AsObject() : nullptr;
			FString NodeId;
			FGuid Guid;
			int32 X = 0;
			int32 Y = 0;
			if (!Position.IsValid()
				|| !Position->TryGetStringField(TEXT("nodeId"), NodeId)
				|| !ParseNodeId(NodeId, Guid)
				|| !Position->TryGetNumberField(TEXT("x"), X)
				|| !Position->TryGetNumberField(TEXT("y"), Y))
			{
				OutError = TEXT("A plan position entry is missing its exact nodeId/x/y.");
				return false;
			}
			if (OutTargets.Contains(Guid))
			{
				OutError = TEXT("The plan contains duplicate node ids.");
				return false;
			}
			OutTargets.Add(Guid, FIntPoint(X, Y));
		}
		return OutTargets.Num() > 0;
	}

	bool ComputeLiveLayoutHash(UEdGraph* Graph, FString& OutHash)
	{
		TSharedRef<FJsonObject> Holder = MakeShared<FJsonObject>();
		TArray<TSharedPtr<FJsonValue>> Positions;
		for (const TPair<FGuid, FIntPoint>& Pair : CapturePositions(Graph))
		{
			TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
			Item->SetStringField(
				TEXT("nodeId"),
				FString(TEXT("n:")) + Pair.Key.ToString(EGuidFormats::DigitsWithHyphensLower));
			Item->SetNumberField(TEXT("x"), Pair.Value.X);
			Item->SetNumberField(TEXT("y"), Pair.Value.Y);
			Positions.Add(MakeShared<FJsonValueObject>(Item));
		}
		Holder->SetArrayField(TEXT("positions"), Positions);
		return UEAgentKitBlueprintGraphLayout::ComputeLayoutHash(Holder, OutHash);
	}

	bool RejectUnknownParams(
		const TSharedPtr<FJsonObject>& Params,
		const TSet<FString>& Allowed,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		if (!Params.IsValid())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-experimental-invalid-parameters"),
				TEXT("The experimental layout call requires a JSON parameter object."));
		}
		for (const TPair<FString, TSharedPtr<FJsonValue>>& Pair : Params->Values)
		{
			if (!Allowed.Contains(Pair.Key))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-layout-experimental-invalid-parameters"),
					FString::Printf(TEXT("Unsupported experimental layout parameter: %s."), *Pair.Key));
			}
		}
		return true;
	}

	bool ValidateFocusedTarget(
		const FString& AssetPath,
		const FString& GraphGuid,
		TSharedPtr<FJsonObject>& OutPreflight,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		if (!UEAgentKitBlueprintGraphLayout::BuildFocusedEditorPreflight(
			OutPreflight,
			OutErrorCode,
			OutErrorMessage))
		{
			return false;
		}
		if (!OutPreflight->GetStringField(TEXT("assetPath")).Equals(AssetPath, ESearchCase::CaseSensitive)
			|| !OutPreflight->GetStringField(TEXT("graphGuid")).Equals(GraphGuid, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-experimental-graph-mismatch"),
				TEXT("The focused Blueprint editor is not showing the exact assetPath/graphGuid from the request."));
		}
		return true;
	}
}

bool UEAgentKitBlueprintGraphLayoutExperimental::IsExperimentalWriteAvailable()
{
	if (bTestOverrideSet)
	{
		return bTestOverrideEnabled;
	}
	bool bEnabled = false;
	if (GConfig == nullptr)
	{
		return false;
	}
	return GConfig->GetBool(
			TEXT("UEAgentKit"),
			TEXT("ExperimentalGraphLayoutApply"),
			bEnabled,
			GEngineIni)
		&& bEnabled;
}

void UEAgentKitBlueprintGraphLayoutExperimental::SetExperimentalWriteEnabledForTest(bool bEnabled)
{
	bTestOverrideSet = true;
	bTestOverrideEnabled = bEnabled;
}

void UEAgentKitBlueprintGraphLayoutExperimental::ResetExperimentalWriteForTest()
{
	bTestOverrideSet = false;
	bTestOverrideEnabled = false;
}

bool UEAgentKitBlueprintGraphLayoutExperimental::TryApplyExperimental(
	const TSharedPtr<FJsonObject>& Params,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	if (!RejectUnknownParams(
		Params,
		{ TEXT("request"), TEXT("confirmation") },
		OutErrorCode,
		OutErrorMessage))
	{
		return false;
	}
	if (!IsExperimentalWriteAvailable())
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-layout-experimental-disabled"),
			TEXT("Experimental graph layout apply is not opted in for this project. "
				 "Set [UEAgentKit] ExperimentalGraphLayoutApply=True in the project config."));
	}
	if (GEditor == nullptr || GEditor->Trans == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-layout-transaction-unavailable"),
			TEXT("The Editor transaction history is unavailable."));
	}
	if (GEditor->IsPlaySessionInProgress() || GEditor->PlayWorld != nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-layout-pie-active"),
			TEXT("Experimental graph layout apply is unavailable while PIE or SIE is active."));
	}

	const TSharedPtr<FJsonObject>* RequestField = nullptr;
	FString Confirmation;
	if (!Params->TryGetObjectField(TEXT("request"), RequestField)
		|| RequestField == nullptr
		|| !RequestField->IsValid()
		|| !Params->TryGetStringField(TEXT("confirmation"), Confirmation))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-layout-experimental-invalid-parameters"),
			TEXT("Experimental apply requires one request object and one confirmation string."));
	}
	const TSharedPtr<FJsonObject>& Request = *RequestField;

	FString AssetPath;
	FString GraphGuid;
	FString ExpectedRevision;
	FString ExpectedFingerprint;
	const TSharedPtr<FJsonObject>* PlanField = nullptr;
	if (!Request->TryGetStringField(TEXT("assetPath"), AssetPath)
		|| !Request->TryGetStringField(TEXT("graphGuid"), GraphGuid)
		|| !Request->TryGetStringField(TEXT("expectedPackageRevision"), ExpectedRevision)
		|| !IsCanonicalSha256(ExpectedRevision)
		|| !Request->TryGetStringField(TEXT("expectedGraphFingerprint"), ExpectedFingerprint)
		|| !IsCanonicalSha1(ExpectedFingerprint)
		|| !Request->TryGetObjectField(TEXT("plan"), PlanField)
		|| PlanField == nullptr
		|| !PlanField->IsValid())
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-layout-experimental-invalid-parameters"),
			TEXT("The request must contain exact assetPath, graphGuid, revision, fingerprint, and plan."));
	}
	const TSharedPtr<FJsonObject>& Plan = *PlanField;
	FString PlanId;
	FString LayoutHash;
	if (!Plan->TryGetStringField(TEXT("planId"), PlanId)
		|| PlanId.IsEmpty()
		|| !Plan->TryGetStringField(TEXT("layoutHash"), LayoutHash)
		|| !IsCanonicalSha256(LayoutHash))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-layout-experimental-invalid-parameters"),
			TEXT("The plan must contain exact planId and layoutHash values."));
	}
	const FString ExpectedConfirmation =
		FString(ApplyConfirmationPrefix) + PlanId;
	if (!Confirmation.Equals(ExpectedConfirmation, ESearchCase::CaseSensitive))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-layout-experimental-confirmation-required"),
			FString::Printf(
				TEXT("Explicit confirmation must be exactly %s."),
				*ExpectedConfirmation));
	}

	TSharedPtr<FJsonObject> Preflight;
	if (!ValidateFocusedTarget(AssetPath, GraphGuid, Preflight, OutErrorCode, OutErrorMessage))
	{
		return false;
	}
	if (!Preflight->GetBoolField(TEXT("editable")))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-layout-experimental-graph-not-editable"),
			TEXT("The focused graph is marked read-only."));
	}
	if (Preflight->GetBoolField(TEXT("packageDirty")))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-layout-package-dirty"),
			TEXT("Save or revert the exact Blueprint package before applying graph layout."));
	}
	if (!Preflight->GetStringField(TEXT("packageRevision")).Equals(ExpectedRevision, ESearchCase::CaseSensitive))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-layout-revision-conflict"),
			TEXT("The focused package revision changed after the preview."));
	}
	if (!Preflight->GetStringField(TEXT("graphFingerprint")).Equals(ExpectedFingerprint, ESearchCase::CaseSensitive))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-layout-fingerprint-conflict"),
			TEXT("The focused graph changed after the preview; re-export and re-plan before applying."));
	}

	UBlueprint* Blueprint = nullptr;
	UEdGraph* Graph = nullptr;
	if (!UEAgentKitBlueprintGraphLayout::ResolveExactTarget(
		AssetPath,
		GraphGuid,
		Blueprint,
		Graph,
		OutErrorCode,
		OutErrorMessage))
	{
		return false;
	}

	TMap<FGuid, FIntPoint> Targets;
	FString TargetError;
	if (!ReadPlanTargets(Request, Targets, TargetError))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-layout-experimental-invalid-parameters"),
			TargetError);
	}

	const TMap<FGuid, FIntPoint> BeforePositions = CapturePositions(Graph);
	const FString BeforeStructure = StructuralState(Graph);
	const FString BeforeTopology = Sha1Text(BeforeStructure);

	TSharedPtr<FJsonObject> Applied;
	if (!UEAgentKitBlueprintGraphLayout::TryApply(
		Request,
		Applied,
		OutErrorCode,
		OutErrorMessage))
	{
		return false;
	}

	const FString PlanLayoutHash = LayoutHash;
	if (!Applied->GetBoolField(TEXT("changed")))
	{
		FString LiveHash;
		if (!ComputeLiveLayoutHash(Graph, LiveHash)
			|| !LiveHash.Equals(PlanLayoutHash, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-experimental-layout-hash-mismatch"),
				TEXT("The live graph already matched the plan targets, but its layout hash is not the plan hash."));
		}
		Applied->SetBoolField(TEXT("experimental"), true);
		Applied->SetBoolField(TEXT("applied"), false);
		Applied->SetBoolField(TEXT("noChange"), true);
		Applied->SetBoolField(TEXT("undoVerified"), false);
		Applied->SetBoolField(TEXT("redoVerified"), false);
		Applied->SetBoolField(TEXT("layoutHashVerified"), true);
		Applied->SetBoolField(TEXT("saved"), false);
		Applied->SetBoolField(TEXT("compiled"), false);
		Applied->SetBoolField(TEXT("autoSave"), false);
		Applied->SetStringField(TEXT("confirmation"), Confirmation);
		OutResult = Applied;
		return true;
	}

	const TMap<FGuid, FIntPoint> AfterPositions = CapturePositions(Graph);
	const FString AfterStructure = StructuralState(Graph);
	const FString AfterTopology = Sha1Text(AfterStructure);
	const bool bStructuralInvariant = BeforeStructure.Equals(AfterStructure, ESearchCase::CaseSensitive);
	const bool bAppliedPositions = PositionsEqual(AfterPositions, Targets);
	if (!bStructuralInvariant || !bAppliedPositions)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-layout-experimental-readback-failed"),
			FString::Printf(
				TEXT("Apply readback failed: structural=%s positions=%s."),
				bStructuralInvariant ? TEXT("true") : TEXT("false"),
				bAppliedPositions ? TEXT("true") : TEXT("false")));
	}

	const bool bUndoCalled = GEditor != nullptr && GEditor->UndoTransaction();
	const TMap<FGuid, FIntPoint> AfterUndoPositions = CapturePositions(Graph);
	const bool bUndoPositions = PositionsEqual(AfterUndoPositions, BeforePositions);
	const bool bUndoStructure =
		StructuralState(Graph).Equals(BeforeStructure, ESearchCase::CaseSensitive);
	const bool bUndoVerified = bUndoCalled && bUndoPositions && bUndoStructure;
	const bool bRedoCalled = bUndoCalled && GEditor != nullptr && GEditor->RedoTransaction();
	const bool bRedoPositions = PositionsEqual(CapturePositions(Graph), Targets);
	const bool bRedoStructure =
		StructuralState(Graph).Equals(BeforeStructure, ESearchCase::CaseSensitive);
	const bool bRedoVerified = bRedoCalled && bRedoPositions && bRedoStructure;

	if (!bUndoVerified || !bRedoVerified)
	{
		FString PositionDiff;
		for (const TPair<FGuid, FIntPoint>& Pair : BeforePositions)
		{
			const FIntPoint* Actual = AfterUndoPositions.Find(Pair.Key);
			if (Actual == nullptr || *Actual != Pair.Value)
			{
				PositionDiff += FString::Printf(
					TEXT("%s expected=(%d,%d) actual=(%d,%d);"),
					*Pair.Key.ToString(EGuidFormats::DigitsWithHyphensLower),
					Pair.Value.X,
					Pair.Value.Y,
					Actual != nullptr ? Actual->X : -999999,
					Actual != nullptr ? Actual->Y : -999999);
			}
		}
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-layout-experimental-undo-redo-failed"),
			FString::Printf(
				TEXT("Undo/Redo verification failed: undo=%s undoPositions=%s undoStructure=%s redo=%s diff=%s."),
				bUndoVerified ? TEXT("true") : TEXT("false"),
				bUndoPositions ? TEXT("true") : TEXT("false"),
				bUndoStructure ? TEXT("true") : TEXT("false"),
				bRedoVerified ? TEXT("true") : TEXT("false"),
				*PositionDiff));
	}

	FString LiveLayoutHash;
	if (!ComputeLiveLayoutHash(Graph, LiveLayoutHash)
		|| !LiveLayoutHash.Equals(PlanLayoutHash, ESearchCase::CaseSensitive))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-layout-experimental-layout-hash-mismatch"),
			TEXT("The live graph positions after Redo do not equal the plan.layoutHash."));
	}

	TSharedPtr<FJsonObject> PostApplyPreflight;
	FString PostCode;
	FString PostMessage;
	if (!UEAgentKitBlueprintGraphLayout::BuildPreflight(
		Blueprint,
		Graph,
		PostApplyPreflight,
		PostCode,
		PostMessage))
	{
		PostApplyPreflight.Reset();
	}

	Applied->SetBoolField(TEXT("experimental"), true);
	Applied->SetBoolField(TEXT("applied"), true);
	Applied->SetBoolField(TEXT("noChange"), false);
	Applied->SetStringField(TEXT("confirmation"), Confirmation);
	Applied->SetBoolField(TEXT("structuralInvariant"), bStructuralInvariant);
	Applied->SetBoolField(TEXT("undoVerified"), bUndoVerified);
	Applied->SetBoolField(TEXT("redoVerified"), bRedoVerified);
	Applied->SetBoolField(TEXT("layoutHashVerified"), true);
	Applied->SetStringField(TEXT("layoutHash"), PlanLayoutHash);
	Applied->SetStringField(TEXT("topologyHashBefore"), BeforeTopology);
	Applied->SetStringField(TEXT("topologyHashAfter"), AfterTopology);
	Applied->SetArrayField(TEXT("positionsBefore"), SerializePositions(BeforePositions));
	Applied->SetArrayField(TEXT("positionsAfter"), SerializePositions(AfterPositions));
	Applied->SetBoolField(TEXT("saved"), false);
	Applied->SetBoolField(TEXT("compiled"), false);
	Applied->SetBoolField(TEXT("autoSave"), false);
	Applied->SetStringField(
		TEXT("savePolicy"),
		TEXT("explicit-separate-experimental-save-bound-to-layoutHash"));
	if (PostApplyPreflight.IsValid())
	{
		Applied->SetStringField(
			TEXT("graphFingerprintAfterApply"),
			PostApplyPreflight->GetStringField(TEXT("graphFingerprint")));
	}
	OutResult = Applied;
	return true;
}

bool UEAgentKitBlueprintGraphLayoutExperimental::TrySaveExperimental(
	const TSharedPtr<FJsonObject>& Params,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	if (!RejectUnknownParams(
		Params,
		{ TEXT("assetPath"), TEXT("graphGuid"), TEXT("layoutHash"), TEXT("expectedGraphFingerprint"), TEXT("confirmation") },
		OutErrorCode,
		OutErrorMessage))
	{
		return false;
	}
	if (!IsExperimentalWriteAvailable())
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-layout-experimental-disabled"),
			TEXT("Experimental graph layout save is not opted in for this project. "
				 "Set [UEAgentKit] ExperimentalGraphLayoutApply=True in the project config."));
	}
	if (GEditor != nullptr && (GEditor->IsPlaySessionInProgress() || GEditor->PlayWorld != nullptr))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-layout-pie-active"),
			TEXT("Experimental graph layout save is unavailable while PIE or SIE is active."));
	}

	FString AssetPath;
	FString GraphGuid;
	FString LayoutHash;
	FString ExpectedGraphFingerprint;
	FString Confirmation;
	if (!Params->TryGetStringField(TEXT("assetPath"), AssetPath)
		|| !Params->TryGetStringField(TEXT("graphGuid"), GraphGuid)
		|| !Params->TryGetStringField(TEXT("layoutHash"), LayoutHash)
		|| !IsCanonicalSha256(LayoutHash)
		|| !Params->TryGetStringField(TEXT("expectedGraphFingerprint"), ExpectedGraphFingerprint)
		|| !IsCanonicalSha1(ExpectedGraphFingerprint)
		|| !Params->TryGetStringField(TEXT("confirmation"), Confirmation))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-layout-experimental-invalid-parameters"),
			TEXT("Save requires exact assetPath, graphGuid, sha256 layoutHash, sha1 graph fingerprint, and confirmation."));
	}
	const FString ExpectedConfirmation =
		FString(SaveConfirmationPrefix) + LayoutHash;
	if (!Confirmation.Equals(ExpectedConfirmation, ESearchCase::CaseSensitive))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-layout-experimental-confirmation-required"),
			FString::Printf(
				TEXT("Explicit Save confirmation must be exactly %s."),
				*ExpectedConfirmation));
	}

	TSharedPtr<FJsonObject> Preflight;
	if (!ValidateFocusedTarget(AssetPath, GraphGuid, Preflight, OutErrorCode, OutErrorMessage))
	{
		return false;
	}
	if (!Preflight->GetStringField(TEXT("graphFingerprint")).Equals(
			ExpectedGraphFingerprint,
			ESearchCase::CaseSensitive))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-layout-fingerprint-conflict"),
			TEXT("The focused graph changed after the layout Apply; refusing to save unrelated edits."));
	}
	if (Preflight->GetBoolField(TEXT("packageDirty")) == false)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-layout-package-not-dirty"),
			TEXT("Experimental Save requires the exact layout package to be dirty."));
	}

	UBlueprint* Blueprint = nullptr;
	UEdGraph* Graph = nullptr;
	if (!UEAgentKitBlueprintGraphLayout::ResolveExactTarget(
		AssetPath,
		GraphGuid,
		Blueprint,
		Graph,
		OutErrorCode,
		OutErrorMessage))
	{
		return false;
	}
	UPackage* Package = Blueprint->GetOutermost();
	if (Package == nullptr || !Package->IsDirty())
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-layout-package-not-dirty"),
			TEXT("Experimental Save requires the exact layout package to be dirty."));
	}

	FString LiveHashBefore;
	if (!ComputeLiveLayoutHash(Graph, LiveHashBefore)
		|| !LiveHashBefore.Equals(LayoutHash, ESearchCase::CaseSensitive))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-layout-experimental-layout-stale"),
			TEXT("The live graph positions no longer equal the layoutHash being saved. Re-apply or re-preview first."));
	}

	const FString Filename = FPackageName::LongPackageNameToFilename(
		Package->GetName(),
		FPackageName::GetAssetPackageExtension());
	IFileManager::Get().MakeDirectory(*FPaths::GetPath(Filename), true);
	FSavePackageArgs SaveArgs;
	SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
	SaveArgs.SaveFlags = SAVE_NoError;
	SaveArgs.Error = GError;
	if (!UPackage::SavePackage(Package, Blueprint, *Filename, SaveArgs))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-layout-experimental-save-failed"),
			FString::Printf(TEXT("SavePackage failed for %s."), *Filename));
	}
	if (Package->IsDirty())
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-layout-experimental-save-failed"),
			TEXT("SavePackage did not clear the package dirty flag."));
	}

	FString AssetDigest;
	if (!FBlueprintContextSha256::HashFile(Filename, AssetDigest))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-layout-experimental-save-failed"),
			TEXT("The saved package hash could not be read back from disk."));
	}

	FString LiveHashAfter;
	if (!ComputeLiveLayoutHash(Graph, LiveHashAfter)
		|| !LiveHashAfter.Equals(LayoutHash, ESearchCase::CaseSensitive))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-layout-experimental-readback-failed"),
			TEXT("The live graph layout hash changed across SavePackage."));
	}

	TSharedPtr<FJsonObject> PostSavePreflight;
	FString PostCode;
	FString PostMessage;
	if (!UEAgentKitBlueprintGraphLayout::BuildPreflight(
		Blueprint,
		Graph,
		PostSavePreflight,
		PostCode,
		PostMessage))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-layout-experimental-readback-failed"),
			FString::Printf(TEXT("Post-save preflight failed: %s %s."), *PostCode, *PostMessage));
	}
	if (PostSavePreflight->GetBoolField(TEXT("packageDirty")))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-layout-experimental-readback-failed"),
			TEXT("The post-save preflight still reports a dirty package."));
	}

	TSharedRef<FJsonObject> Readback = MakeShared<FJsonObject>();
	Readback->SetStringField(TEXT("assetPath"), AssetPath);
	Readback->SetStringField(TEXT("graphGuid"), GraphGuid);
	Readback->SetStringField(
		TEXT("assetSha256"),
		FString(TEXT("sha256:")) + AssetDigest.ToLower());
	Readback->SetStringField(TEXT("layoutHash"), LiveHashAfter);
	Readback->SetStringField(
		TEXT("packageRevision"),
		PostSavePreflight->GetStringField(TEXT("packageRevision")));
	Readback->SetStringField(
		TEXT("graphFingerprint"),
		PostSavePreflight->GetStringField(TEXT("graphFingerprint")));
	Readback->SetBoolField(TEXT("packageDirty"), false);
	Readback->SetBoolField(TEXT("positionHashMatchesPlan"), true);
	Readback->SetBoolField(TEXT("independentReloadVerified"), false);
	Readback->SetStringField(TEXT("requiredAcceptanceEvidence"), TEXT("independent-reload"));

	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("action"), TEXT("save-experimental-graph-layout"));
	Result->SetBoolField(TEXT("experimental"), true);
	Result->SetStringField(TEXT("assetPath"), AssetPath);
	Result->SetStringField(TEXT("graphGuid"), GraphGuid);
	Result->SetStringField(TEXT("layoutHash"), LayoutHash);
	Result->SetStringField(TEXT("graphFingerprint"), ExpectedGraphFingerprint);
	Result->SetStringField(TEXT("assetFilename"), Filename);
	Result->SetStringField(
		TEXT("assetSha256"),
		FString(TEXT("sha256:")) + AssetDigest.ToLower());
	Result->SetStringField(TEXT("confirmation"), Confirmation);
	Result->SetBoolField(TEXT("saved"), true);
	Result->SetBoolField(TEXT("packageDirty"), false);
	Result->SetBoolField(TEXT("readbackVerified"), true);
	Result->SetBoolField(TEXT("independentReloadVerified"), false);
	Result->SetStringField(TEXT("requiredAcceptanceEvidence"), TEXT("independent-reload"));
	Result->SetObjectField(TEXT("readback"), Readback);
	Result->SetBoolField(TEXT("compiled"), false);
	Result->SetBoolField(TEXT("autoSave"), false);
	OutResult = Result;
	return true;
}
