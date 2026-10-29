#include "BlueprintGraphLayoutP8TestOnly.h"

#include "BlueprintContextSha256.h"
#include "BlueprintEditor.h"
#include "BlueprintGraphLayoutApply.h"

#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "Editor.h"
#include "Engine/Blueprint.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformProcess.h"
#include "Interfaces/IPluginManager.h"
#include "Misc/App.h"
#include "Misc/FileHelper.h"
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
			return Left.NodeGuid.ToString(EGuidFormats::Digits) < Right.NodeGuid.ToString(EGuidFormats::Digits);
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

	bool ReadPlanTargets(
		const TSharedPtr<FJsonObject>& Request,
		TMap<FGuid, FIntPoint>& OutTargets,
		FString& OutError)
	{
		const TSharedPtr<FJsonObject>* PlanField = nullptr;
		if (!Request->TryGetObjectField(TEXT("plan"), PlanField)
			|| PlanField == nullptr
			|| !PlanField->IsValid())
		{
			OutError = TEXT("Request has no plan object.");
			return false;
		}
		const TArray<TSharedPtr<FJsonValue>>* Positions = nullptr;
		if (!(*PlanField)->TryGetArrayField(TEXT("positions"), Positions) || Positions == nullptr)
		{
			OutError = TEXT("Request plan has no positions.");
			return false;
		}
		for (const TSharedPtr<FJsonValue>& Entry : *Positions)
		{
			const TSharedPtr<FJsonObject> Position = Entry->AsObject();
			if (!Position.IsValid())
			{
				OutError = TEXT("Request plan position is not an object.");
				return false;
			}
			const FString NodeId = Position->GetStringField(TEXT("nodeId"));
			FGuid Guid;
			if (!NodeId.StartsWith(TEXT("n:")) || !FGuid::Parse(NodeId.Mid(2), Guid))
			{
				OutError = TEXT("Request plan nodeId is invalid.");
				return false;
			}
			OutTargets.Add(
				Guid,
				FIntPoint(
					static_cast<int32>(Position->GetNumberField(TEXT("x"))),
					static_cast<int32>(Position->GetNumberField(TEXT("y")))));
		}
		return OutTargets.Num() > 0;
	}

	TArray<TSharedPtr<FJsonValue>> SerializePositions(const TMap<FGuid, FIntPoint>& Positions)
	{
		TArray<TSharedPtr<FJsonValue>> Values;
		for (const TPair<FGuid, FIntPoint>& Pair : Positions)
		{
			TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
			Item->SetStringField(TEXT("nodeId"), FString(TEXT("n:")) + Pair.Key.ToString(EGuidFormats::DigitsWithHyphensLower));
			Item->SetNumberField(TEXT("x"), Pair.Value.X);
			Item->SetNumberField(TEXT("y"), Pair.Value.Y);
			Values.Add(MakeShared<FJsonValueObject>(Item));
		}
		return Values;
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
				TEXT("graph-layout-test-only-invalid-parameters"),
				TEXT("The P8 test-only call requires a JSON parameter object."));
		}
		for (const TPair<FString, TSharedPtr<FJsonValue>>& Pair : Params->Values)
		{
			if (!Allowed.Contains(Pair.Key))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-layout-test-only-invalid-parameters"),
					FString::Printf(TEXT("Unsupported P8 test-only parameter: %s."), *Pair.Key));
			}
		}
		return true;
	}

	FString CurrentPluginBuildHash()
	{
		const TSharedPtr<IPlugin> Plugin = IPluginManager::Get().FindPlugin(TEXT("UEAgentKit"));
		if (!Plugin.IsValid())
		{
			return FString();
		}
		const FString BinaryPath = FPaths::Combine(
			Plugin->GetBaseDir(),
			TEXT("Binaries"),
			FPlatformProcess::GetBinariesSubdirectory(),
			TEXT("UnrealEditor-UEAgentKitEditor.dll"));
		FString Digest;
		if (!FPaths::FileExists(BinaryPath) || !FBlueprintContextSha256::HashFile(BinaryPath, Digest))
		{
			return FString();
		}
		return FString(TEXT("sha256:")) + Digest.ToLower();
	}

	bool ValidateBuildMarker(FString& OutErrorCode, FString& OutErrorMessage)
	{
		const FString MarkerPath = FPaths::Combine(
			FPaths::GetPath(UEAgentKitBlueprintGraphLayoutP8::GetFixedProjectPath()),
			TEXT("P8TestOnlyBuild.json"));
		FString MarkerText;
		if (!FFileHelper::LoadFileToString(MarkerText, *MarkerPath))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-test-only-build-rejected"),
				TEXT("The exact P8 build authorization marker is missing."));
		}
		TSharedPtr<FJsonObject> Marker;
		const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(MarkerText);
		if (!FJsonSerializer::Deserialize(Reader, Marker) || !Marker.IsValid())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-test-only-build-rejected"),
				TEXT("The exact P8 build authorization marker is not valid JSON."));
		}
		FString SchemaVersion;
		FString ProjectName;
		FString PluginBuildHash;
		if (!Marker->TryGetStringField(TEXT("schemaVersion"), SchemaVersion)
			|| !SchemaVersion.Equals(TEXT("ueak.p8.buildgate/1.0"), ESearchCase::CaseSensitive)
			|| !Marker->TryGetStringField(TEXT("projectName"), ProjectName)
			|| !ProjectName.Equals(UEAgentKitBlueprintGraphLayoutP8::FixedProjectName, ESearchCase::CaseSensitive)
			|| !Marker->TryGetStringField(TEXT("pluginBuildHash"), PluginBuildHash)
			|| !PluginBuildHash.StartsWith(TEXT("sha256:"), ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-test-only-build-rejected"),
				TEXT("The P8 build authorization marker has an invalid schema or project identity."));
		}
		const FString CurrentHash = CurrentPluginBuildHash();
		if (CurrentHash.IsEmpty() || !CurrentHash.Equals(PluginBuildHash, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-test-only-build-rejected"),
				TEXT("The loaded UEAgentKitEditor binary does not match the authorized P8 build hash."));
		}
		return true;
	}
}

namespace UEAgentKitBlueprintGraphLayoutP8
{
	FString GetFixedProjectPath()
	{
		const FString ProjectFilePath = FPaths::GetProjectFilePath();
		if (ProjectFilePath.IsEmpty())
		{
			return FString();
		}

		FString FullPath = FPaths::ConvertRelativePathToFull(ProjectFilePath);
		FPaths::NormalizeFilename(FullPath);
		FPaths::CollapseRelativeDirectories(FullPath);

		const FString ProjectDir = FPaths::GetPath(FullPath);
		const FString ProjectDirName = FPaths::GetCleanFilename(ProjectDir);
		const FString ParentDirName = FPaths::GetCleanFilename(FPaths::GetPath(ProjectDir));
		if (!ProjectDirName.Equals(FixedProjectName, ESearchCase::CaseSensitive)
			|| !ParentDirName.Equals(TEXT("Build"), ESearchCase::CaseSensitive))
		{
			return FString();
		}
		return FullPath;
	}

	bool IsFixedProjectIdentityForTest(
		const FString& ProjectFilePath,
		const FString& ProjectName,
		const FString& ProjectFileSha256,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		FString FullPath = FPaths::ConvertRelativePathToFull(ProjectFilePath);
		FPaths::NormalizeFilename(FullPath);
		FPaths::CollapseRelativeDirectories(FullPath);
		FString ComparablePath = FullPath;
		ComparablePath.ToLowerInline();
		FString ExpectedPath = GetFixedProjectPath();
		ExpectedPath.ToLowerInline();
		if (!ComparablePath.Equals(ExpectedPath, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-test-only-project-rejected"),
				TEXT("P8 test-only graph layout is bound to the exact disposable P8AutomationHost project path."));
		}
		if (!ProjectName.Equals(FixedProjectName, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-test-only-project-rejected"),
				TEXT("P8 test-only graph layout requires the exact P8AutomationHost project name."));
		}
		if (!ProjectFileSha256.Equals(FixedProjectFileSha256, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-test-only-project-rejected"),
				TEXT("The P8 disposable .uproject identity hash does not match the hardcoded test-only host."));
		}
		return true;
	}

	bool IsFixedProjectIdentity(FString& OutErrorCode, FString& OutErrorMessage)
	{
		const FString ProjectFilePath = FPaths::GetProjectFilePath();
		if (ProjectFilePath.IsEmpty())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-test-only-project-rejected"),
				TEXT("No project file is available for the P8 test-only write surface."));
		}
		FString Digest;
		if (!FBlueprintContextSha256::HashFile(ProjectFilePath, Digest))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-test-only-project-rejected"),
				TEXT("The P8 disposable .uproject identity hash is unavailable."));
		}
		Digest.ToLowerInline();
		if (!IsFixedProjectIdentityForTest(
			ProjectFilePath,
			FApp::GetProjectName(),
			Digest,
			OutErrorCode,
			OutErrorMessage))
		{
			return false;
		}
		return ValidateBuildMarker(OutErrorCode, OutErrorMessage);
	}

	bool IsFixedAssetPath(const FString& AssetPath)
	{
		return AssetPath.Equals(FixedAssetPath, ESearchCase::CaseSensitive);
	}

	bool IsTestOnlyWriteAvailable()
	{
		FString ErrorCode;
		FString ErrorMessage;
		return IsFixedProjectIdentity(ErrorCode, ErrorMessage);
	}

	bool TryApplyTestOnly(
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
		if (!IsFixedProjectIdentity(OutErrorCode, OutErrorMessage))
		{
			return false;
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
				TEXT("graph-layout-test-only-invalid-parameters"),
				TEXT("The P8 test-only call requires request and confirmation."));
		}
		const TSharedPtr<FJsonObject>& Request = *RequestField;

		FString AssetPath;
		FString GraphGuid;
		if (!Request->TryGetStringField(TEXT("assetPath"), AssetPath)
			|| !Request->TryGetStringField(TEXT("graphGuid"), GraphGuid))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-test-only-invalid-parameters"),
				TEXT("The P8 request must contain exact assetPath and graphGuid fields."));
		}
		if (!IsFixedAssetPath(AssetPath))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-test-only-asset-rejected"),
				TEXT("P8 test-only graph layout accepts only /Game/UEAgentKitP8/BP_LayoutP8.BP_LayoutP8."));
		}

		const TSharedPtr<FJsonObject>* PlanField = nullptr;
		FString PlanId;
		if (!Request->TryGetObjectField(TEXT("plan"), PlanField)
			|| PlanField == nullptr
			|| !PlanField->IsValid()
			|| !(*PlanField)->TryGetStringField(TEXT("planId"), PlanId)
			|| PlanId.IsEmpty())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-test-only-invalid-parameters"),
				TEXT("The P8 request needs one exact plan.planId for confirmation binding."));
		}
		const FString ExpectedConfirmation = FString(ApplyConfirmationPrefix) + PlanId;
		if (!Confirmation.Equals(ExpectedConfirmation, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-test-only-confirmation-required"),
				TEXT("Explicit confirmation must be exactly the documented APPLY P8 TEST-ONLY <planId> phrase."));
		}

		TSharedPtr<FJsonObject> Preflight;
		FString PreflightCode;
		FString PreflightMessage;
		if (!UEAgentKitBlueprintGraphLayout::BuildFocusedEditorPreflight(
			Preflight,
			PreflightCode,
			PreflightMessage))
		{
			return Fail(OutErrorCode, OutErrorMessage, *PreflightCode, PreflightMessage);
		}
		FString LivePieState;
		Preflight->TryGetStringField(TEXT("pieState"), LivePieState);
		if (!IsFixedAssetPath(Preflight->GetStringField(TEXT("assetPath")))
			|| !Preflight->GetStringField(TEXT("graphGuid")).Equals(GraphGuid, ESearchCase::CaseSensitive)
			|| !Preflight->GetBoolField(TEXT("editable"))
			|| Preflight->GetBoolField(TEXT("packageDirty"))
			|| (!LivePieState.IsEmpty() && !LivePieState.Equals(TEXT("stopped"), ESearchCase::CaseSensitive)))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-test-only-preflight-rejected"),
				TEXT("The focused P8 graph must be clean, editable, stopped, and exactly match the request."));
		}

		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = nullptr;
		FString ResolveCode;
		FString ResolveMessage;
		if (!UEAgentKitBlueprintGraphLayout::ResolveExactTarget(
			AssetPath,
			GraphGuid,
			Blueprint,
			Graph,
			ResolveCode,
			ResolveMessage))
		{
			return Fail(OutErrorCode, OutErrorMessage, *ResolveCode, ResolveMessage);
		}

		const TMap<FGuid, FIntPoint> BeforePositions = CapturePositions(Graph);
		const FString BeforeStructure = StructuralState(Graph);
		const FString BeforeTopology = Sha1Text(BeforeStructure);

		// UEdGraphNode_Comment is not reliably transactional in the live graph:
		// a direct NodePos change can survive Undo while every UEdGraphNode
		// reverts.  A create/delete transaction is not an acceptable substitute
		// for this position-only contract, so a P8 request that would move a
		// Comment fails closed before any write.
		TMap<FGuid, FIntPoint> RequestedTargets;
		FString RequestedTargetError;
		if (!ReadPlanTargets(Request, RequestedTargets, RequestedTargetError))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-test-only-invalid-parameters"),
				RequestedTargetError);
		}
		for (const TPair<FGuid, FIntPoint>& Pair : RequestedTargets)
		{
			const FIntPoint* Before = BeforePositions.Find(Pair.Key);
			UEdGraphNode* Node = nullptr;
			for (UEdGraphNode* Candidate : Graph->Nodes)
			{
				if (Candidate != nullptr && Candidate->NodeGuid == Pair.Key)
				{
					Node = Candidate;
					break;
				}
			}
			if (Node != nullptr
				&& Node->GetClass() != nullptr
				&& Node->GetClass()->GetPathName().EndsWith(TEXT(".EdGraphNode_Comment"), ESearchCase::CaseSensitive)
				&& (Before == nullptr || *Before != Pair.Value))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-layout-test-only-comment-move-unsupported"),
					TEXT("P8 test-only apply refuses to move a Comment because comment NodePos changes are not transactionally undoable; exclude the comment from the apply scope."));
			}
		}

		TSharedPtr<FJsonObject> Applied;
		if (!UEAgentKitBlueprintGraphLayout::TryApply(
			Request,
			Applied,
			OutErrorCode,
			OutErrorMessage))
		{
			return false;
		}
		if (!Applied->GetBoolField(TEXT("changed"))
			|| Applied->GetIntegerField(TEXT("movedNodeCount")) <= 0
			|| !Applied->GetBoolField(TEXT("singleTransaction")))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-test-only-no-change"),
				TEXT("P8 test-only apply requires at least one moved node in one transaction."));
		}

		TMap<FGuid, FIntPoint> Targets;
		FString TargetError;
		if (!ReadPlanTargets(Request, Targets, TargetError))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-test-only-invalid-parameters"),
				TargetError);
		}
		const TMap<FGuid, FIntPoint> AfterPositions = CapturePositions(Graph);
		const FString AfterStructure = StructuralState(Graph);
		const FString AfterTopology = Sha1Text(AfterStructure);
		const bool bStructuralInvariant = BeforeStructure.Equals(AfterStructure, ESearchCase::CaseSensitive);

		const bool bUndoCalled = GEditor != nullptr && GEditor->UndoTransaction();
		const TMap<FGuid, FIntPoint> AfterUndoPositions = CapturePositions(Graph);
		const bool bUndoPositions = PositionsEqual(AfterUndoPositions, BeforePositions);
		const bool bUndoStructure = StructuralState(Graph).Equals(BeforeStructure, ESearchCase::CaseSensitive);
		const bool bUndoVerified = bUndoCalled && bUndoPositions && bUndoStructure;
		const bool bRedoCalled = bUndoCalled && GEditor != nullptr && GEditor->RedoTransaction();
		const bool bRedoVerified = bRedoCalled
			&& PositionsEqual(CapturePositions(Graph), Targets)
			&& StructuralState(Graph).Equals(BeforeStructure, ESearchCase::CaseSensitive);

		if (!bStructuralInvariant || !bUndoVerified || !bRedoVerified)
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
				TEXT("graph-layout-test-only-undo-redo-failed"),
				FString::Printf(
					TEXT("P8 test-only verification failed: structural=%s undo=%s undoPositions=%s undoStructure=%s redo=%s diff=%s"),
					bStructuralInvariant ? TEXT("true") : TEXT("false"),
					bUndoVerified ? TEXT("true") : TEXT("false"),
					bUndoPositions ? TEXT("true") : TEXT("false"),
					bUndoStructure ? TEXT("true") : TEXT("false"),
					bRedoVerified ? TEXT("true") : TEXT("false"),
					*PositionDiff));
		}

		Applied->SetBoolField(TEXT("testOnly"), true);
		Applied->SetStringField(TEXT("testOnlyProject"), GetFixedProjectPath());
		Applied->SetStringField(TEXT("testOnlyAsset"), FixedAssetPath);
		Applied->SetStringField(TEXT("confirmation"), Confirmation);
		Applied->SetBoolField(TEXT("structuralInvariant"), bStructuralInvariant);
		Applied->SetBoolField(TEXT("undoVerified"), bUndoVerified);
		Applied->SetBoolField(TEXT("redoVerified"), bRedoVerified);
		Applied->SetStringField(TEXT("topologyHashBefore"), BeforeTopology);
		Applied->SetStringField(TEXT("topologyHashAfter"), AfterTopology);
		Applied->SetArrayField(TEXT("positionsBefore"), SerializePositions(BeforePositions));
		Applied->SetArrayField(TEXT("positionsAfter"), SerializePositions(AfterPositions));
		Applied->SetBoolField(TEXT("packageDirtyAfterRedo"), Blueprint->GetOutermost()->IsDirty());
		Applied->SetBoolField(TEXT("saved"), false);
		Applied->SetBoolField(TEXT("compiled"), false);
		Applied->SetBoolField(TEXT("autoSave"), false);
		Applied->SetStringField(TEXT("savePolicy"), TEXT("explicit-separate-test-only-save-after-mcp-apply"));
		OutResult = Applied;
		return true;
	}

	bool TrySaveTestOnly(
		const TSharedPtr<FJsonObject>& Params,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		if (!RejectUnknownParams(
			Params,
			{ TEXT("confirmation") },
			OutErrorCode,
			OutErrorMessage))
		{
			return false;
		}
		if (!IsFixedProjectIdentity(OutErrorCode, OutErrorMessage))
		{
			return false;
		}
		FString Confirmation;
		if (!Params->TryGetStringField(TEXT("confirmation"), Confirmation)
			|| !Confirmation.Equals(SaveConfirmation, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-test-only-confirmation-required"),
				TEXT("Explicit Save confirmation must be exactly SAVE P8 TEST-ONLY BP_LayoutP8."));
		}
		if (GEditor != nullptr && (GEditor->IsPlaySessionInProgress() || GEditor->PlayWorld != nullptr))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-pie-active"),
				TEXT("P8 test-only save is unavailable while PIE or SIE is active."));
		}

		UObject* Asset = StaticFindObject(UObject::StaticClass(), nullptr, FixedAssetPath, false);
		UBlueprint* Blueprint = Cast<UBlueprint>(Asset);
		if (Blueprint == nullptr || !Blueprint->GetPathName().Equals(FixedAssetPath, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-asset-not-loaded"),
				TEXT("The exact P8 disposable Blueprint must already be loaded for the explicit harness save."));
		}
		UPackage* Package = Blueprint->GetOutermost();
		if (Package == nullptr || !Package->IsDirty())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-package-not-dirty"),
				TEXT("The explicit P8 harness save requires the exact disposable package to be dirty."));
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
				TEXT("graph-layout-test-only-save-failed"),
				FString::Printf(TEXT("Explicit P8 SavePackage failed for %s."), *Filename));
		}
		if (Package->IsDirty())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-test-only-save-failed"),
				TEXT("The explicit P8 SavePackage did not clear the package dirty flag."));
		}

		FString AssetDigest;
		if (!FBlueprintContextSha256::HashFile(Filename, AssetDigest))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-layout-test-only-save-failed"),
				TEXT("The saved P8 asset hash is unavailable."));
		}
		TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
		Result->SetStringField(TEXT("action"), TEXT("save-P8-test-only-graph-layout"));
		Result->SetStringField(TEXT("assetPath"), FixedAssetPath);
		Result->SetStringField(TEXT("assetFilename"), Filename);
		Result->SetStringField(TEXT("assetSha256"), FString(TEXT("sha256:")) + AssetDigest.ToLower());
		Result->SetStringField(TEXT("confirmation"), Confirmation);
		Result->SetBoolField(TEXT("saved"), true);
		Result->SetBoolField(TEXT("packageDirty"), false);
		Result->SetBoolField(TEXT("sourceControlUsed"), false);
		OutResult = Result;
		return true;
	}
}
