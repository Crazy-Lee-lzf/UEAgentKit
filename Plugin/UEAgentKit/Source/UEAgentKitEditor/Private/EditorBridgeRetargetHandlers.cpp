#include "EditorBridge.h"
#include "EditorBridgeHandlerUtils.h"
#include "Retarget/RetargetAnalysis.h"
#include "Retarget/RetargetTypes.h"

#include "Dom/JsonObject.h"
#include "Editor.h"
#include "Engine/SkeletalMesh.h"
#include "Rig/IKRigDefinition.h"
#include "Retargeter/IKRetargeter.h"
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

	bool ParseChainMappings(
		const TArray<TSharedPtr<FJsonValue>>& Mappings,
		TArray<UEAgentKitRetarget::FRetargetChainMappingItem>& OutMappings,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		OutMappings.Empty();
		for (const TSharedPtr<FJsonValue>& Value : Mappings)
		{
			const TSharedPtr<FJsonObject> MappingJson = Value.IsValid() ? Value->AsObject() : nullptr;
			if (!MappingJson.IsValid())
			{
				OutErrorCode = TEXT("retarget_chain_ambiguous");
				OutErrorMessage = TEXT("Each plan chain mapping must be a JSON object.");
				return false;
			}
			UEAgentKitRetarget::FRetargetChainMappingItem Item;
			Item.TargetChainName = MappingJson->GetStringField(TEXT("targetChain"));
			Item.SourceChainName = MappingJson->GetStringField(TEXT("sourceChain"));
			const FString Required = MappingJson->GetStringField(TEXT("required"));
			Item.Required = Required.ToLower() == TEXT("required")
				? UEAgentKitRetarget::ERetargetChainRequired::Required
				: UEAgentKitRetarget::ERetargetChainRequired::Optional;
			if (Item.TargetChainName.IsEmpty() || Item.SourceChainName.IsEmpty())
			{
				OutErrorCode = TEXT("retarget_chain_ambiguous");
				OutErrorMessage = TEXT("Each plan chain mapping requires targetChain and sourceChain fields.");
				return false;
			}
			OutMappings.Add(Item);
		}
		return true;
	}

	bool ParsePoseConfig(
		const TSharedPtr<FJsonObject>& PoseJson,
		UEAgentKitRetarget::FRetargetPoseConfig& OutPose,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		OutPose = UEAgentKitRetarget::FRetargetPoseConfig();
		if (!PoseJson.IsValid())
		{
			return true;
		}
		OutPose.PoseName = PoseJson->GetStringField(TEXT("poseName"));
		if (OutPose.PoseName.IsEmpty())
		{
			return true;
		}
		const TArray<TSharedPtr<FJsonValue>>* RootValue = nullptr;
		if (PoseJson->TryGetArrayField(TEXT("rootTranslationOffset"), RootValue) && RootValue->Num() == 3)
		{
			OutPose.RootTranslationOffset.X = (*RootValue)[0]->AsNumber();
			OutPose.RootTranslationOffset.Y = (*RootValue)[1]->AsNumber();
			OutPose.RootTranslationOffset.Z = (*RootValue)[2]->AsNumber();
		}
		const TArray<TSharedPtr<FJsonValue>>* BoneValues = nullptr;
		if (PoseJson->TryGetArrayField(TEXT("boneRotationOffsets"), BoneValues))
		{
			for (const TSharedPtr<FJsonValue>& BoneValue : *BoneValues)
			{
				const TSharedPtr<FJsonObject> BoneJson = BoneValue.IsValid() ? BoneValue->AsObject() : nullptr;
				if (!BoneJson.IsValid())
				{
					OutErrorCode = TEXT("retarget_pose_invalid");
					OutErrorMessage = TEXT("Each pose bone rotation offset must be a JSON object.");
					return false;
				}
				UEAgentKitRetarget::FRetargetPoseBoneRotation Bone;
				Bone.BoneName = BoneJson->GetStringField(TEXT("bone"));
				if (Bone.BoneName.IsEmpty())
				{
					OutErrorCode = TEXT("retarget_pose_invalid");
					OutErrorMessage = TEXT("Each pose bone rotation offset requires a bone field.");
					return false;
				}
				const TArray<TSharedPtr<FJsonValue>>* RotationValue = nullptr;
				if (BoneJson->TryGetArrayField(TEXT("rotation"), RotationValue) && RotationValue->Num() == 4)
				{
					Bone.RotationOffset.W = (*RotationValue)[0]->AsNumber();
					Bone.RotationOffset.X = (*RotationValue)[1]->AsNumber();
					Bone.RotationOffset.Y = (*RotationValue)[2]->AsNumber();
					Bone.RotationOffset.Z = (*RotationValue)[3]->AsNumber();
				}
				OutPose.BoneRotationOffsets.Add(Bone);
			}
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
	const FString& RetargeterName,
	const TArray<TSharedPtr<FJsonValue>>& Mappings,
	const TSharedPtr<FJsonObject>& PoseConfig,
	bool bAllowLargePoseOffset,
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
	TArray<UEAgentKitRetarget::FRetargetChainMappingItem> ParsedMappings;
	UEAgentKitRetarget::FRetargetPoseConfig ParsedPose;
	if (!ParseChainMappings(Mappings, ParsedMappings, OutErrorCode, OutErrorMessage)
		|| !ParsePoseConfig(PoseConfig, ParsedPose, OutErrorCode, OutErrorMessage))
	{
		return false;
	}

	TArray<UEAgentKitRetarget::FRetargetAssetChange> Changes;
	// Preflight both rigs before mutating anything so conflict detection never
	// leaves partial in-memory assets behind.
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

	if (!ApplyRig(Pair.SourceMesh, SourceRigName, SourceRetargetRoot, ParsedSourceChains, OutErrorCode, OutErrorMessage)
		|| !ApplyRig(Pair.TargetMesh, TargetRigName, TargetRetargetRoot, ParsedTargetChains, OutErrorCode, OutErrorMessage))
	{
		return false;
	}

	// Load the rigs that were created or updated so the retargeter can reference
	// them. The rig location is resolved by mesh reference, which finds both
	// freshly-created in-memory assets and pre-existing on-disk rigs (which can
	// live in a different folder than the mesh).
	auto LoadRigForChange = [](USkeletalMesh* Mesh, const UEAgentKitRetarget::FRetargetAssetChange& Change) -> UIKRigDefinition*
	{
		if (Change.Action == TEXT("create") || Change.Action == TEXT("update"))
		{
			if (UIKRigDefinition* Rig = Cast<UIKRigDefinition>(LoadObject<UIKRigDefinition>(nullptr, *Change.AssetPath)))
			{
				return Rig;
			}
		}
		UEAgentKitRetarget::FRetargetIKRigState State;
		if (UEAgentKitRetarget::FindIKRigForMesh(Mesh, State))
		{
			return Cast<UIKRigDefinition>(LoadObject<UIKRigDefinition>(nullptr, *State.AssetPath));
		}
		return nullptr;
	};

	UEAgentKitRetarget::FRetargeterSetupResult RetargeterResult;
	FString RetargeterErrorCode;
	FString RetargeterErrorMessage;
	const bool bRetargeterApplied = UEAgentKitRetarget::ApplyRetargeterConfig(
		LoadRigForChange(Pair.SourceMesh, Changes[0]),
		LoadRigForChange(Pair.TargetMesh, Changes[1]),
		Pair.SourceMesh,
		Pair.TargetMesh,
		RetargeterName,
		ParsedMappings,
		ParsedPose,
		bUpdateExisting,
		RetargeterName.IsEmpty() ? false : true,
		bAllowLargePoseOffset,
		RetargeterResult,
		RetargeterErrorCode,
		RetargeterErrorMessage);
	if (!bRetargeterApplied)
	{
		OutErrorCode = RetargeterErrorCode;
		OutErrorMessage = RetargeterErrorMessage;
		return false;
	}
	Changes.Add(RetargeterResult.Change);

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
	Result->SetField(TEXT("mappingReport"), MakeShared<FJsonValueObject>(UEAgentKitRetarget::MappingReportToJson(RetargeterResult.Mapping)));
	Result->SetBoolField(TEXT("poseApplied"), RetargeterResult.bPoseApplied);
	Result->SetStringField(TEXT("poseName"), RetargeterResult.PoseName);
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

bool FUEAgentKitEditorBridge::TryRetargetBatchStepResult(
	const FString& SourceMeshPath,
	const FString& TargetMeshPath,
	const FString& RetargeterPath,
	const TArray<FString>& SourceAssetPaths,
	const FString& OutputDirectory,
	const TSharedPtr<FJsonObject>& Naming,
	bool bOverwriteExisting,
	bool bIncludeReferencedAssets,
	bool bExportOnlyAnimatedBones,
	bool bRetainAdditiveFlags,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage) const
{
	FLoadedMeshPair Pair;
	if (!LoadRetargetMeshPair(SourceMeshPath, TargetMeshPath, Pair, OutErrorCode, OutErrorMessage))
	{
		return false;
	}
	UIKRetargeter* Retargeter = Cast<UIKRetargeter>(LoadObject<UIKRetargeter>(nullptr, *RetargeterPath));
	if (Retargeter == nullptr)
	{
		OutErrorCode = TEXT("retarget_asset_not_found");
		OutErrorMessage = TEXT("The IK Retargeter asset is not loaded; load it first.");
		return false;
	}

	UEAgentKitRetarget::FRetargetBatchNaming ParsedNaming;
	if (Naming.IsValid())
	{
		ParsedNaming.Search = Naming->GetStringField(TEXT("search"));
		ParsedNaming.Replace = Naming->GetStringField(TEXT("replace"));
		ParsedNaming.Prefix = Naming->GetStringField(TEXT("prefix"));
		ParsedNaming.Suffix = Naming->GetStringField(TEXT("suffix"));
	}

	TArray<UEAgentKitRetarget::FRetargetBatchOutputAsset> Outputs;
	if (!UEAgentKitRetarget::RunRetargetBatchStep(
			SourceAssetPaths,
			Pair.SourceMesh,
			Pair.TargetMesh,
			Retargeter,
			OutputDirectory,
			ParsedNaming,
			bOverwriteExisting,
			bIncludeReferencedAssets,
			bExportOnlyAnimatedBones,
			bRetainAdditiveFlags,
			Outputs,
			OutErrorCode,
			OutErrorMessage))
	{
		return false;
	}

	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("action"), TEXT("retarget-batch-step"));
	Result->SetStringField(TEXT("sourceMesh"), SourceMeshPath);
	Result->SetStringField(TEXT("targetMesh"), TargetMeshPath);
	Result->SetStringField(TEXT("retargeter"), RetargeterPath);
	TArray<TSharedPtr<FJsonValue>> OutputValues;
	for (const UEAgentKitRetarget::FRetargetBatchOutputAsset& Output : Outputs)
	{
		OutputValues.Add(MakeShared<FJsonValueObject>(UEAgentKitRetarget::BatchOutputToJson(Output)));
	}
	Result->SetArrayField(TEXT("outputs"), OutputValues);
	Result->SetBoolField(TEXT("transactionCreated"), !Outputs.IsEmpty());
	Result->SetBoolField(TEXT("assetDirty"), !Outputs.IsEmpty());
	Result->SetStringField(TEXT("editorSessionId"), SessionId);
	OutResult = Result;
	return true;
}
