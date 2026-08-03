#include "EditorBridge.h"
#include "EditorBridgeHandlerUtils.h"
#include "Retarget/RetargetAnalysis.h"

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
