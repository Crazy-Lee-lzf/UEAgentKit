#pragma once

#include "CoreMinimal.h"
#include "Dom/JsonObject.h"

class USkeletalMesh;
class USkeleton;

namespace UEAgentKitRetarget
{
	// A single bone with its reference pose information.
	struct FRetargetBoneInfo
	{
		FName Name;
		int32 Index = INDEX_NONE;
		int32 ParentIndex = INDEX_NONE;
		int32 Depth = 0;
		FVector LocalPosition = FVector::ZeroVector;
		FVector ComponentPosition = FVector::ZeroVector;
	};

	// Reference skeleton snapshot used by the read-only analysis.
	struct FRetargetSkeletonSnapshot
	{
		FString SkeletonPath;
		FString MeshPath;
		FString MeshClassPath;
		FString RootBone;
		int32 BoneCount = 0;
		TArray<FRetargetBoneInfo> Bones;
		TMap<FName, int32> BoneIndices;
		bool bTruncated = false;
	};

	// Humanoid chain semantics.
	enum class ERetargetChainRequired : uint8
	{
		Required,
		Optional
	};

	enum class ERetargetChainSide : uint8
	{
		Center,
		Left,
		Right
	};

	// One scored candidate for a humanoid-v1 semantic chain.
	struct FRetargetChainCandidate
	{
		FString ChainName;
		ERetargetChainRequired Required = ERetargetChainRequired::Optional;
		ERetargetChainSide Side = ERetargetChainSide::Center;
		FName StartBone;
		FName EndBone;
		int32 StartIndex = INDEX_NONE;
		int32 EndIndex = INDEX_NONE;
		float NameScore = 0.0f;
		float HierarchyScore = 0.0f;
		float SideScore = 0.0f;
		float PositionScore = 0.0f;
		float LengthScore = 0.0f;
		float ParentContextScore = 0.0f;
		float Confidence = 0.0f;
		TArray<FString> Reasons;
	};

	struct FRetargetChainCandidateReport
	{
		FString ChainName;
		ERetargetChainRequired Required = ERetargetChainRequired::Optional;
		TArray<FRetargetChainCandidate> Candidates;
		bool bAmbiguous = false;
	};

	// Compatibility summary for one mesh pair.
	struct FRetargetCompatibilityReport
	{
		FString Compatibility; // compatible | compatible_with_warnings | needs_manual_mapping | blocked
		FString SourceSkeleton;
		FString TargetSkeleton;
		TArray<FString> SourceRetargetRootCandidates;
		TArray<FString> TargetRetargetRootCandidates;
		TArray<FRetargetChainCandidateReport> ChainCandidates;
		TArray<FString> UnmatchedRequiredChains;
		TArray<FString> UnmatchedOptionalChains;
		TArray<FString> Warnings;
		TArray<FString> BlockingIssues;
		bool bTruncated = false;
	};

	// Builds the reference skeleton snapshot from a loaded skeletal mesh.
	bool BuildSkeletonSnapshot(USkeletalMesh* Mesh, int32 MaxBones, FRetargetSkeletonSnapshot& OutSnapshot, FString& OutError);

	// Runs the humanoid-v1 read-only analysis for a source/target mesh pair.
	bool AnalyzeRetargetCompatibility(
		USkeletalMesh* SourceMesh,
		USkeletalMesh* TargetMesh,
		bool bIncludeOptionalChains,
		int32 MaxBoneDetails,
		FRetargetCompatibilityReport& OutReport,
		FString& OutError);

	// Serializes a snapshot for the JSON report.
	TSharedRef<FJsonObject> SkeletonSnapshotToJson(const FRetargetSkeletonSnapshot& Snapshot);
	TSharedRef<FJsonObject> CompatibilityReportToJson(const FRetargetCompatibilityReport& Report);
}
