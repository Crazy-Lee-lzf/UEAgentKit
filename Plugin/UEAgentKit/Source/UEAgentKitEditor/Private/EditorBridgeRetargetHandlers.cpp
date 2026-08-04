#include "EditorBridge.h"
#include "EditorBridgeHandlerUtils.h"
#include "Retarget/RetargetAnalysis.h"
#include "Retarget/RetargetTypes.h"

#include "Dom/JsonObject.h"
#include "Editor.h"
#include "Engine/SkeletalMesh.h"
#include "UObject/UObjectGlobals.h"

bool FUEAgentKitEditorBridge::TryAnalyzeAnimationRetargetResult(
	const FString& SourceMeshPath,
	const FString& TargetMeshPath,
	bool bIncludeOptionalChains,
	int32 MaxBoneDetails,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage) const
{
	if (GEditor == nullptr)
	{
		OutErrorCode = TEXT("live-editor-unavailable");
		OutErrorMessage = TEXT("The Unreal Editor is unavailable.");
		return false;
	}
	if (GEditor->PlayWorld != nullptr)
	{
		OutErrorCode = TEXT("retarget_editor_state_invalid");
		OutErrorMessage = TEXT("Retarget analysis is unavailable while PIE or SIE is active.");
		return false;
	}
	if (!UEAgentKitEditorBridgePrivate::IsSafeGameAssetPath(SourceMeshPath)
		|| !UEAgentKitEditorBridgePrivate::IsSafeGameAssetPath(TargetMeshPath))
	{
		OutErrorCode = TEXT("retarget_asset_not_found");
		OutErrorMessage = TEXT("Source and target must be exact /Game Skeletal Mesh Object Paths.");
		return false;
	}
	if (MaxBoneDetails < 64 || MaxBoneDetails > 4096)
	{
		OutErrorCode = TEXT("retarget_asset_not_found");
		OutErrorMessage = TEXT("maxBoneDetails must be between 64 and 4096.");
		return false;
	}
	USkeletalMesh* SourceMesh = LoadObject<USkeletalMesh>(nullptr, *SourceMeshPath);
	if (SourceMesh == nullptr)
	{
		OutErrorCode = TEXT("retarget_asset_not_found");
		OutErrorMessage = TEXT("The source Skeletal Mesh is not loaded; load it first.");
		return false;
	}
	USkeletalMesh* TargetMesh = LoadObject<USkeletalMesh>(nullptr, *TargetMeshPath);
	if (TargetMesh == nullptr)
	{
		OutErrorCode = TEXT("retarget_asset_not_found");
		OutErrorMessage = TEXT("The target Skeletal Mesh is not loaded; load it first.");
		return false;
	}
	if (SourceMesh == TargetMesh)
	{
		OutErrorCode = TEXT("retarget_asset_type_invalid");
		OutErrorMessage = TEXT("Source and target must be different assets.");
		return false;
	}
	if (SourceMesh->GetSkeleton() == nullptr || TargetMesh->GetSkeleton() == nullptr)
	{
		OutErrorCode = TEXT("retarget_skeleton_invalid");
		OutErrorMessage = TEXT("Both Skeletal Meshes must have a valid Skeleton with a Reference Skeleton.");
		return false;
	}

	UEAgentKitRetarget::FRetargetCompatibilityReport Compatibility;
	FString AnalysisError;
	if (!UEAgentKitRetarget::AnalyzeRetargetCompatibility(
			SourceMesh,
			TargetMesh,
			bIncludeOptionalChains,
			MaxBoneDetails,
			Compatibility,
			AnalysisError))
	{
		OutErrorCode = TEXT("retarget_skeleton_invalid");
		OutErrorMessage = AnalysisError;
		return false;
	}

	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("action"), TEXT("analyze-animation-retarget"));
	Result->SetStringField(TEXT("sourceMesh"), SourceMeshPath);
	Result->SetStringField(TEXT("targetMesh"), TargetMeshPath);
	Result->SetField(TEXT("analysis"), MakeShared<FJsonValueObject>(UEAgentKitRetarget::CompatibilityReportToJson(Compatibility)));
	Result->SetStringField(TEXT("editorSessionId"), SessionId);
	OutResult = Result;
	return true;
}

namespace
{
	struct FLoadedMeshPair
	{
		USkeletalMesh* SourceMesh = nullptr;
		USkeletalMesh* TargetMesh = nullptr;
	};

	bool LoadRetargetMeshPair(
		const FString& SourceMeshPath,
		const FString& TargetMeshPath,
		FLoadedMeshPair& OutPair,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		if (GEditor == nullptr)
		{
			OutErrorCode = TEXT("live-editor-unavailable");
			OutErrorMessage = TEXT("The Unreal Editor is unavailable.");
			return false;
		}
		if (GEditor->PlayWorld != nullptr)
		{
			OutErrorCode = TEXT("retarget_editor_state_invalid");
			OutErrorMessage = TEXT("Retarget operations are unavailable while PIE or SIE is active.");
			return false;
		}
		if (!UEAgentKitEditorBridgePrivate::IsSafeGameAssetPath(SourceMeshPath)
			|| !UEAgentKitEditorBridgePrivate::IsSafeGameAssetPath(TargetMeshPath))
		{
			OutErrorCode = TEXT("retarget_asset_not_found");
			OutErrorMessage = TEXT("Source and target must be exact /Game Skeletal Mesh Object Paths.");
			return false;
		}
		OutPair.SourceMesh = LoadObject<USkeletalMesh>(nullptr, *SourceMeshPath);
		if (OutPair.SourceMesh == nullptr)
		{
			OutErrorCode = TEXT("retarget_asset_not_found");
			OutErrorMessage = TEXT("The source Skeletal Mesh is not loaded; load it first.");
			return false;
		}
		OutPair.TargetMesh = LoadObject<USkeletalMesh>(nullptr, *TargetMeshPath);
		if (OutPair.TargetMesh == nullptr)
		{
			OutErrorCode = TEXT("retarget_asset_not_found");
			OutErrorMessage = TEXT("The target Skeletal Mesh is not loaded; load it first.");
			return false;
		}
		if (OutPair.SourceMesh == OutPair.TargetMesh)
		{
			OutErrorCode = TEXT("retarget_asset_type_invalid");
			OutErrorMessage = TEXT("Source and target must be different assets.");
			return false;
		}
		if (OutPair.SourceMesh->GetSkeleton() == nullptr || OutPair.TargetMesh->GetSkeleton() == nullptr)
		{
			OutErrorCode = TEXT("retarget_skeleton_invalid");
			OutErrorMessage = TEXT("Both Skeletal Meshes must have a valid Skeleton with a Reference Skeleton.");
			return false;
		}
		return true;
	}

	bool ParsePlanChains(
		const TArray<TSharedPtr<FJsonValue>>& Chains,
		TArray<UEAgentKitRetarget::FRetargetPlanChain>& OutChains,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		OutChains.Empty();
		for (const TSharedPtr<FJsonValue>& Value : Chains)
		{
			const TSharedPtr<FJsonObject> ChainJson = Value.IsValid() ? Value->AsObject() : nullptr;
			if (!ChainJson.IsValid())
			{
				OutErrorCode = TEXT("retarget_chain_ambiguous");
				OutErrorMessage = TEXT("Each plan chain must be a JSON object.");
				return false;
			}
			UEAgentKitRetarget::FRetargetPlanChain Chain;
			Chain.ChainName = ChainJson->GetStringField(TEXT("chain"));
			Chain.StartBone = ChainJson->GetStringField(TEXT("startBone"));
			Chain.EndBone = ChainJson->GetStringField(TEXT("endBone"));
			const FString Required = ChainJson->GetStringField(TEXT("required"));
			Chain.Required = Required.ToLower() == TEXT("required")
				? UEAgentKitRetarget::ERetargetChainRequired::Required
				: UEAgentKitRetarget::ERetargetChainRequired::Optional;
			const FString Side = ChainJson->GetStringField(TEXT("side"));
			Chain.Side = Side == TEXT("Left") ? UEAgentKitRetarget::ERetargetChainSide::Left
				: (Side == TEXT("Right") ? UEAgentKitRetarget::ERetargetChainSide::Right : UEAgentKitRetarget::ERetargetChainSide::Center);
			if (Chain.ChainName.IsEmpty() || Chain.StartBone.IsEmpty() || Chain.EndBone.IsEmpty())
			{
				OutErrorCode = TEXT("retarget_chain_ambiguous");
				OutErrorMessage = TEXT("Each plan chain requires chain, startBone and endBone fields.");
				return false;
			}
			OutChains.Add(Chain);
		}
		return true;
	}
}

bool FUEAgentKitEditorBridge::TryPlanAnimationRetargetResult(
	const FString& SourceMeshPath,
	const FString& TargetMeshPath,
	bool bIncludeOptionalChains,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage) const
{
	FLoadedMeshPair Pair;
	if (!LoadRetargetMeshPair(SourceMeshPath, TargetMeshPath, Pair, OutErrorCode, OutErrorMessage))
	{
		return false;
	}

	UEAgentKitRetarget::FRetargetCompatibilityReport Compatibility;
	FString AnalysisError;
	if (!UEAgentKitRetarget::AnalyzeRetargetCompatibility(
			Pair.SourceMesh,
			Pair.TargetMesh,
			bIncludeOptionalChains,
			512,
			Compatibility,
			AnalysisError))
	{
		OutErrorCode = TEXT("retarget_skeleton_invalid");
		OutErrorMessage = AnalysisError;
		return false;
	}

	UEAgentKitRetarget::FRetargetIKRigState SourceRigState;
	UEAgentKitRetarget::FRetargetIKRigState TargetRigState;
	UEAgentKitRetarget::FindIKRigForMesh(Pair.SourceMesh, SourceRigState);
	UEAgentKitRetarget::FindIKRigForMesh(Pair.TargetMesh, TargetRigState);

	TSharedRef<FJsonObject> Existing = MakeShared<FJsonObject>();
	Existing->SetField(TEXT("sourceIKRig"), MakeShared<FJsonValueObject>(UEAgentKitRetarget::IKRigStateToJson(SourceRigState)));
	Existing->SetField(TEXT("targetIKRig"), MakeShared<FJsonValueObject>(UEAgentKitRetarget::IKRigStateToJson(TargetRigState)));

	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("action"), TEXT("plan-animation-retarget"));
	Result->SetStringField(TEXT("sourceMesh"), SourceMeshPath);
	Result->SetStringField(TEXT("targetMesh"), TargetMeshPath);
	Result->SetField(TEXT("analysis"), MakeShared<FJsonValueObject>(UEAgentKitRetarget::CompatibilityReportToJson(Compatibility)));
	Result->SetField(TEXT("existingAssets"), MakeShared<FJsonValueObject>(Existing));
	Result->SetStringField(TEXT("editorSessionId"), SessionId);
	OutResult = Result;
	return true;
}

bool FUEAgentKitEditorBridge::TryApplyAnimationRetargetSetupResult(
	const FString& SourceMeshPath,
	const FString& TargetMeshPath,
	const FString& SourceRigName,
	const FString& TargetRigName,
	const FString& SourceRetargetRoot,
	const FString& TargetRetargetRoot,
	const TArray<TSharedPtr<FJsonValue>>& SourceChains,
	const TArray<TSharedPtr<FJsonValue>>& TargetChains,
	bool bUpdateExisting,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage) const
{
	FLoadedMeshPair Pair;
	if (!LoadRetargetMeshPair(SourceMeshPath, TargetMeshPath, Pair, OutErrorCode, OutErrorMessage))
	{
		return false;
	}
	TArray<UEAgentKitRetarget::FRetargetPlanChain> ParsedSourceChains;
	TArray<UEAgentKitRetarget::FRetargetPlanChain> ParsedTargetChains;
	if (!ParsePlanChains(SourceChains, ParsedSourceChains, OutErrorCode, OutErrorMessage)
		|| !ParsePlanChains(TargetChains, ParsedTargetChains, OutErrorCode, OutErrorMessage))
	{
		return false;
	}

	TArray<UEAgentKitRetarget::FRetargetAssetChange> Changes;
	const bool bSourceAllowedCreate = SourceRigName.IsEmpty() ? false : true;
	const bool bTargetAllowedCreate = TargetRigName.IsEmpty() ? false : true;
	// Preflight both rigs before mutating anything so conflict detection never
	// leaves partial in-memory assets behind.
	FString PreflightError;
	if (!UEAgentKitRetarget::PreflightIKRigConfig(
			Pair.SourceMesh,
			SourceRetargetRoot,
			ParsedSourceChains,
			bUpdateExisting,
			OutErrorCode,
			OutErrorMessage)
		|| !UEAgentKitRetarget::PreflightIKRigConfig(
			Pair.TargetMesh,
			TargetRetargetRoot,
			ParsedTargetChains,
			bUpdateExisting,
			OutErrorCode,
			OutErrorMessage))
	{
		return false;
	}
	auto ApplyRig = [&](
					   USkeletalMesh* Mesh,
					   const FString& RigName,
					   const FString& RetargetRoot,
					   const TArray<UEAgentKitRetarget::FRetargetPlanChain>& PlanChains,
					   const TCHAR* Label,
					   FString& ErrorCode,
					   FString& ErrorMessage) -> bool
	{
		UEAgentKitRetarget::FRetargetAssetChange Change;
		if (!UEAgentKitRetarget::ApplyIKRigConfig(
				Mesh,
				RigName,
				RetargetRoot,
				PlanChains,
				bUpdateExisting,
				RigName.IsEmpty() ? false : true,
				Change,
				ErrorCode,
				ErrorMessage))
		{
			return false;
		}
		Changes.Add(Change);
		return true;
	};

	FString Error;
	if (!ApplyRig(Pair.SourceMesh, SourceRigName, SourceRetargetRoot, ParsedSourceChains, TEXT("source"), OutErrorCode, OutErrorMessage)
		|| !ApplyRig(Pair.TargetMesh, TargetRigName, TargetRetargetRoot, ParsedTargetChains, TEXT("target"), OutErrorCode, OutErrorMessage))
	{
		return false;
	}

	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("action"), TEXT("apply-animation-retarget-setup"));
	Result->SetStringField(TEXT("sourceMesh"), SourceMeshPath);
	Result->SetStringField(TEXT("targetMesh"), TargetMeshPath);
	TArray<TSharedPtr<FJsonValue>> ChangeValues;
	for (const UEAgentKitRetarget::FRetargetAssetChange& Change : Changes)
	{
			ChangeValues.Add(MakeShared<FJsonValueObject>(UEAgentKitRetarget::AssetChangeToJson(Change)));
	}
	Result->SetArrayField(TEXT("changes"), ChangeValues);
	bool bTransactionCreated = false;
	bool bAssetDirty = false;
	for (const UEAgentKitRetarget::FRetargetAssetChange& Change : Changes)
	{
		if (Change.Action == TEXT("create") || Change.Action == TEXT("update"))
		{
			bTransactionCreated = true;
			bAssetDirty = true;
		}
	}
	Result->SetBoolField(TEXT("transactionCreated"), bTransactionCreated);
	Result->SetBoolField(TEXT("assetDirty"), bAssetDirty);
	Result->SetStringField(TEXT("editorSessionId"), SessionId);
	OutResult = Result;
	return true;
}
