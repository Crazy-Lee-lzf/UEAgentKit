#pragma once

#include "CoreMinimal.h"
#include "Dom/JsonObject.h"
#include "Retarget/RetargetAnalysis.h"

class USkeletalMesh;
class UIKRigDefinition;

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

	// Serializes a plan chain list for the JSON report.
	TSharedRef<FJsonObject> PlanChainToJson(const FRetargetPlanChain& Chain);
	TSharedRef<FJsonObject> IKRigStateToJson(const FRetargetIKRigState& State);
	TSharedRef<FJsonObject> AssetChangeToJson(const FRetargetAssetChange& Change);
}
