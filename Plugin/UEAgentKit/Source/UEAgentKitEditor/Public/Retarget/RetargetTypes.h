#pragma once

#include "CoreMinimal.h"
#include "Dom/JsonObject.h"
#include "Retarget/RetargetAnalysis.h"

class USkeletalMesh;
class UIKRigDefinition;
class UIKRetargeter;

namespace UEAgentKitRetarget
{
	// One requested retarget chain from a plan (semantic name + explicit bones).
	struct FRetargetPlanChain
	{
		FString ChainName;
		ERetargetChainRequired Required = ERetargetChainRequired::Optional;
		ERetargetChainSide Side = ERetargetChainSide::Center;
		FString StartBone;
		FString EndBone;
	};

	// State of an existing IK Rig found for a mesh.
	struct FRetargetIKRigState
	{
		FString AssetPath;
		FString RetargetRoot;
		TArray<FString> ChainNames;
	};

	// Per-asset result of one setup apply step.
	struct FRetargetAssetChange
	{
		FString AssetPath;
		FString Action; // create | update | no_op
		TArray<FString> Details;
	};

	struct FRetargetSetupResult
	{
		TArray<FRetargetAssetChange> Changes;
		TArray<FString> Warnings;
		bool bTransactionCreated = false;
		bool bAssetDirty = false;
	};

	// Looks up an existing IK Rig that references the given mesh via the Asset Registry.
	bool FindIKRigForMesh(USkeletalMesh* Mesh, FRetargetIKRigState& OutState);

	// Checks whether the plan configuration can be applied without creating or
	// modifying any asset. Conflict detection must not leave partial state behind.
	bool PreflightIKRigConfig(
		USkeletalMesh* Mesh,
		const FString& RetargetRoot,
		const TArray<FRetargetPlanChain>& Chains,
		bool bUpdateExisting,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	// Creates or updates the IK Rig bound to the given mesh, matching the plan chains.
	// Returns the asset path and the resulting change action (create/update/no_op).
	bool ApplyIKRigConfig(
		USkeletalMesh* Mesh,
		const FString& DesiredAssetName,
		const FString& RetargetRoot,
		const TArray<FRetargetPlanChain>& Chains,
		bool bUpdateExisting,
		bool bAllowCreate,
		FRetargetAssetChange& OutChange,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	// One explicit source/target chain mapping from a plan. Target chains are the
	// semantic chain names written into the target IK Rig; source chains name the
	// matching chain on the source IK Rig.
	struct FRetargetChainMappingItem
	{
		FString TargetChainName;
		FString SourceChainName;
		ERetargetChainRequired Required = ERetargetChainRequired::Optional;
	};

	// One bone rotation offset in a retarget pose (Target skeleton, local space).
	struct FRetargetPoseBoneRotation
	{
		FString BoneName;
		FQuat RotationOffset = FQuat::Identity;
	};

	// Retarget pose configuration carried by a Plan.
	struct FRetargetPoseConfig
	{
		FString PoseName;
		FVector RootTranslationOffset = FVector::ZeroVector;
		TArray<FRetargetPoseBoneRotation> BoneRotationOffsets;
	};

	// Report of applying the plan chain mappings to the IK Retargeter.
	struct FRetargetMappingReport
	{
		TArray<FString> MappedRequiredChains;
		TArray<FString> MappedOptionalChains;
		TArray<FString> UnmappedSourceChains;
		TArray<FString> UnmappedTargetChains;
		TArray<FString> DuplicateMappings;
		float MappingConfidence = 1.0f;
	};

	// Result of applying the IK Retargeter configuration.
	struct FRetargeterSetupResult
	{
		FRetargetAssetChange Change;
		FRetargetMappingReport Mapping;
		FString PoseName;
		bool bPoseApplied = false;
	};

	// Looks up an existing IK Retargeter that references the given source and
	// target IK Rigs, both in memory and via the Asset Registry.
	bool FindRetargeterForRigs(const UIKRigDefinition* SourceRig, const UIKRigDefinition* TargetRig, FString& OutAssetPath);

	// Creates or updates the IK Retargeter bound to the source/target IK Rigs,
	// applies the explicit chain mappings and the retarget pose. Returns the
	// resulting change action (create/update/no_op) and the mapping report.
	bool ApplyRetargeterConfig(
		UIKRigDefinition* SourceRig,
		UIKRigDefinition* TargetRig,
		USkeletalMesh* SourceMesh,
		USkeletalMesh* TargetMesh,
		const FString& DesiredAssetName,
		const TArray<FRetargetChainMappingItem>& Mappings,
		const FRetargetPoseConfig& Pose,
		bool bUpdateExisting,
		bool bAllowCreate,
		bool bAllowLargePoseOffset,
		FRetargeterSetupResult& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	// Naming rule applied to batch retarget outputs.
	struct FRetargetBatchNaming
	{
		FString Search;
		FString Replace;
		FString Prefix;
		FString Suffix;
	};

	// One batch retarget output.
	struct FRetargetBatchOutputAsset
	{
		FString InputPath;
		FString OutputPath;
		FString AssetClass;
		FString AssetType;
		FString SkeletonPath;
	};

	// Runs one batch retarget step over the given source animation assets using
	// the configured IK Retargeter. Validates input classes and skeleton match,
	// denies overwriting existing outputs unless enabled, and returns the output
	// asset object paths.
	bool RunRetargetBatchStep(
		const TArray<FString>& SourceAssetPaths,
		USkeletalMesh* SourceMesh,
		USkeletalMesh* TargetMesh,
		UIKRetargeter* Retargeter,
		const FString& OutputDirectory,
		const FRetargetBatchNaming& Naming,
		bool bOverwriteExisting,
		bool bIncludeReferencedAssets,
		bool bExportOnlyAnimatedBones,
		bool bRetainAdditiveFlags,
		TArray<FRetargetBatchOutputAsset>& OutOutputs,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	// Serializes a plan chain list for the JSON report.
	TSharedRef<FJsonObject> PlanChainToJson(const FRetargetPlanChain& Chain);
	TSharedRef<FJsonObject> IKRigStateToJson(const FRetargetIKRigState& State);
	TSharedRef<FJsonObject> AssetChangeToJson(const FRetargetAssetChange& Change);
	TSharedRef<FJsonObject> MappingReportToJson(const FRetargetMappingReport& Report);
	TSharedRef<FJsonObject> PoseConfigToJson(const FRetargetPoseConfig& Pose);
	TSharedRef<FJsonObject> BatchOutputToJson(const FRetargetBatchOutputAsset& Output);
}
