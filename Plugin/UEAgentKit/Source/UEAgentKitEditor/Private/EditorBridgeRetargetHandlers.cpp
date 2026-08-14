#include "AssetReaders/AssetReaderCommon.h"
#include "EditorBridge.h"
#include "EditorBridgeHandlerUtils.h"
#include "Retarget/RetargetAnalysis.h"
#include "Retarget/RetargetTypes.h"

#include "Animation/AnimCurveTypes.h"
#include "Animation/AnimData/IAnimationDataModel.h"
#include "Animation/AnimSequence.h"
#include "Animation/AnimationPoseData.h"
#include "Animation/AttributesContainer.h"
#include "Animation/Skeleton.h"
#include "AnimationRuntime.h"
#include "BoneContainer.h"
#include "BonePose.h"
#include "Components/SkeletalMeshComponent.h"
#include "Dom/JsonObject.h"
#include "Editor.h"
#include "Engine/World.h"
#include "Engine/SkeletalMesh.h"
#include "Misc/MemStack.h"
#include "Rig/IKRigDefinition.h"
#include "Retarget/RetargetValidation.h"
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
	TSharedRef<FJsonObject> VectorToJson(const FVector& Value)
	{
		TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
		Json->SetNumberField(TEXT("x"), Value.X);
		Json->SetNumberField(TEXT("y"), Value.Y);
		Json->SetNumberField(TEXT("z"), Value.Z);
		return Json;
	}

	FVector GetReferenceComponentScale(const USkeleton* Skeleton, const FName BoneName)
	{
		if (Skeleton == nullptr)
		{
			return FVector::ZeroVector;
		}
		const FReferenceSkeleton& ReferenceSkeleton = Skeleton->GetReferenceSkeleton();
		const int32 BoneIndex = ReferenceSkeleton.FindBoneIndex(BoneName);
		if (BoneIndex == INDEX_NONE)
		{
			return FVector::ZeroVector;
		}
		FTransform Component = FTransform::Identity;
		TArray<int32> Path;
		for (int32 Index = BoneIndex; Index != INDEX_NONE; Index = ReferenceSkeleton.GetParentIndex(Index))
		{
			Path.Insert(Index, 0);
		}
		for (const int32 Index : Path)
		{
			Component = ReferenceSkeleton.GetRefBonePose()[Index] * Component;
		}
		return Component.GetScale3D();
	}

	TSharedRef<FJsonObject> BuildTrackScaleSummary(
		const UAnimSequence* Sequence,
		const USkeleton* Skeleton,
		const FString& BoneName)
	{
		TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
		Json->SetStringField(TEXT("bone"), BoneName);
		const FName TrackName(*BoneName);
		const FReferenceSkeleton& ReferenceSkeleton = Skeleton->GetReferenceSkeleton();
		const int32 BoneIndex = ReferenceSkeleton.FindBoneIndex(TrackName);
		Json->SetBoolField(TEXT("boneExists"), BoneIndex != INDEX_NONE);
		if (BoneIndex != INDEX_NONE)
		{
			Json->SetField(TEXT("referenceLocalScale"), MakeShared<FJsonValueObject>(VectorToJson(ReferenceSkeleton.GetRefBonePose()[BoneIndex].GetScale3D())));
			Json->SetField(TEXT("referenceComponentScale"), MakeShared<FJsonValueObject>(VectorToJson(GetReferenceComponentScale(Skeleton, TrackName))));
		}

		const IAnimationDataModel* Model = Sequence->GetDataModel();
		Json->SetBoolField(TEXT("boneCompressedDataValid"), Sequence->IsBoneCompressedDataValid());
		const bool bTrackExists = Model != nullptr && Model->IsValidBoneTrackName(TrackName);
		Json->SetBoolField(TEXT("trackExists"), bTrackExists);
		if (!bTrackExists)
		{
			Json->SetNumberField(TEXT("trackKeyCount"), 0);
			return Json;
		}

		TArray<FTransform> Transforms;
		Model->GetBoneTrackTransforms(TrackName, Transforms);
		Json->SetNumberField(TEXT("trackKeyCount"), Transforms.Num());
		if (Transforms.IsEmpty())
		{
			return Json;
		}

		FVector Minimum = Transforms[0].GetScale3D();
		FVector Maximum = Minimum;
		for (const FTransform& Transform : Transforms)
		{
			const FVector Scale = Transform.GetScale3D();
			Minimum.X = FMath::Min(Minimum.X, Scale.X);
			Minimum.Y = FMath::Min(Minimum.Y, Scale.Y);
			Minimum.Z = FMath::Min(Minimum.Z, Scale.Z);
			Maximum.X = FMath::Max(Maximum.X, Scale.X);
			Maximum.Y = FMath::Max(Maximum.Y, Scale.Y);
			Maximum.Z = FMath::Max(Maximum.Z, Scale.Z);
		}
		Json->SetField(TEXT("firstScale"), MakeShared<FJsonValueObject>(VectorToJson(Transforms[0].GetScale3D())));
		Json->SetField(TEXT("middleScale"), MakeShared<FJsonValueObject>(VectorToJson(Transforms[Transforms.Num() / 2].GetScale3D())));
		Json->SetField(TEXT("lastScale"), MakeShared<FJsonValueObject>(VectorToJson(Transforms.Last().GetScale3D())));
		Json->SetField(TEXT("minimumScale"), MakeShared<FJsonValueObject>(VectorToJson(Minimum)));
		Json->SetField(TEXT("maximumScale"), MakeShared<FJsonValueObject>(VectorToJson(Maximum)));
		if (BoneIndex != INDEX_NONE && Sequence->IsBoneCompressedDataValid())
		{
			const double SampleTimes[] = {0.0, Sequence->GetPlayLength() * 0.5, Sequence->GetPlayLength()};
			const TCHAR* FieldNames[] = {
				TEXT("compressedFirstScale"),
				TEXT("compressedMiddleScale"),
				TEXT("compressedLastScale")};
			for (int32 SampleIndex = 0; SampleIndex < UE_ARRAY_COUNT(SampleTimes); ++SampleIndex)
			{
				FTransform CompressedTransform;
				const FAnimExtractContext ExtractContext(SampleTimes[SampleIndex]);
				Sequence->GetBoneTransform(
					CompressedTransform,
					FSkeletonPoseBoneIndex(BoneIndex),
					ExtractContext,
					false);
				Json->SetField(FieldNames[SampleIndex], MakeShared<FJsonValueObject>(VectorToJson(CompressedTransform.GetScale3D())));
			}
		}
		return Json;
	}
	TSharedRef<FJsonObject> RotatorToJson(const FRotator& Value)
	{
		TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
		Json->SetNumberField(TEXT("pitch"), Value.Pitch);
		Json->SetNumberField(TEXT("yaw"), Value.Yaw);
		Json->SetNumberField(TEXT("roll"), Value.Roll);
		return Json;
	}

	TArray<TSharedPtr<FJsonValue>> BuildPreviewSamples(
		UAnimSequence* Sequence,
		USkeleton* Skeleton,
		const TArray<FString>& BoneNames,
		bool bLoadIfNeeded,
		FString& OutStatus,
		FString& OutPreviewMeshPath)
	{
		TArray<TSharedPtr<FJsonValue>> Samples;
		OutStatus = TEXT("unavailable");
		OutPreviewMeshPath.Reset();
		if (Sequence == nullptr || Skeleton == nullptr)
		{
			return Samples;
		}
		if (Sequence->IsValidAdditive())
		{
			OutStatus = TEXT("unsupported-additive-requires-base-pose");
			return Samples;
		}

		USkeletalMesh* PreviewMesh = Sequence->GetPreviewMesh(bLoadIfNeeded);
		if (PreviewMesh == nullptr)
		{
			PreviewMesh = Skeleton->GetPreviewMesh(bLoadIfNeeded);
		}
		if (PreviewMesh == nullptr)
		{
			OutStatus = TEXT("preview-mesh-unavailable");
			return Samples;
		}
		OutPreviewMeshPath = PreviewMesh->GetPathName();

		UWorld* EditorWorld = GEditor != nullptr ? GEditor->GetEditorWorldContext().World() : nullptr;
		if (EditorWorld == nullptr)
		{
			OutStatus = TEXT("editor-world-unavailable");
			return Samples;
		}

		USkeletalMeshComponent* Component = NewObject<USkeletalMeshComponent>(GetTransientPackage(), NAME_None, RF_Transient);
		if (Component == nullptr)
		{
			OutStatus = TEXT("component-create-failed");
			return Samples;
		}

		Component->SetVisibility(false, true);
		Component->SetHiddenInGame(true);
		Component->SetSkeletalMesh(PreviewMesh);
		Component->RegisterComponentWithWorld(EditorWorld);
		if (!Component->IsRegistered())
		{
			Component->MarkAsGarbage();
			OutStatus = TEXT("component-registration-failed");
			return Samples;
		}
		Component->SetUpdateAnimationInEditor(true);
		Component->VisibilityBasedAnimTickOption = EVisibilityBasedAnimTickOption::AlwaysTickPoseAndRefreshBones;
		Component->SetAnimationMode(EAnimationMode::AnimationSingleNode, true);
		Component->SetAnimation(Sequence);

		const double Fractions[] = {0.0, 0.5, 1.0};
		for (const double Fraction : Fractions)
		{
			const double Time = Sequence->GetPlayLength() * Fraction;
			Component->SetPosition(Time, false);
			Component->TickAnimation(0.0f, false);
			Component->RefreshBoneTransforms();
			Component->CompleteParallelAnimationEvaluation(true);
			Component->UpdateComponentToWorld();
			Component->UpdateBounds();

			TSharedRef<FJsonObject> Sample = MakeShared<FJsonObject>();
			Sample->SetNumberField(TEXT("fraction"), Fraction);
			Sample->SetNumberField(TEXT("time"), Time);
			const FBoxSphereBounds Bounds = Component->Bounds;
			Sample->SetField(TEXT("boundsOrigin"), MakeShared<FJsonValueObject>(VectorToJson(Bounds.Origin)));
			Sample->SetField(TEXT("boundsExtent"), MakeShared<FJsonValueObject>(VectorToJson(Bounds.BoxExtent)));
			Sample->SetNumberField(TEXT("boundsSphereRadius"), Bounds.SphereRadius);
			const FDeltaTimeRecord RootMotionDelta(static_cast<float>(Time));
			const FAnimExtractContext RootMotionContext(0.0, true, RootMotionDelta, false);
			const FTransform RootMotion = Sequence->ExtractRootMotion(RootMotionContext);
			Sample->SetField(TEXT("extractedRootMotionTranslation"), MakeShared<FJsonValueObject>(VectorToJson(RootMotion.GetLocation())));
			Sample->SetField(TEXT("extractedRootMotionRotation"), MakeShared<FJsonValueObject>(RotatorToJson(RootMotion.Rotator())));
			Sample->SetField(TEXT("extractedRootMotionScale"), MakeShared<FJsonValueObject>(VectorToJson(RootMotion.GetScale3D())));

			TArray<TSharedPtr<FJsonValue>> BoneValues;
			for (const FString& BoneName : BoneNames)
			{
				TSharedRef<FJsonObject> Bone = MakeShared<FJsonObject>();
				Bone->SetStringField(TEXT("bone"), BoneName);
				const int32 BoneIndex = Component->GetBoneIndex(FName(*BoneName));
				Bone->SetBoolField(TEXT("boneExists"), BoneIndex != INDEX_NONE);
				if (BoneIndex != INDEX_NONE)
				{
					const FTransform Transform = Component->GetBoneTransform(BoneIndex, FTransform::Identity);
					Bone->SetField(TEXT("componentScale"), MakeShared<FJsonValueObject>(VectorToJson(Transform.GetScale3D())));
					Bone->SetField(TEXT("componentLocation"), MakeShared<FJsonValueObject>(VectorToJson(Transform.GetLocation())));
				}
				BoneValues.Add(MakeShared<FJsonValueObject>(Bone));
			}
			Sample->SetArrayField(TEXT("bones"), BoneValues);
			Samples.Add(MakeShared<FJsonValueObject>(Sample));
		}
		Component->SetAnimation(nullptr);
		Component->SetUpdateAnimationInEditor(false);
		Component->UnregisterComponent();
		Component->MarkAsGarbage();
		OutStatus = TEXT("success");
		return Samples;
	}

	// Evaluate an additive sequence as Base Pose + Additive Delta -> combined Component Pose.
	// This is read-only: it uses FCompactPose accumulation and never touches a component or package.
	TArray<TSharedPtr<FJsonValue>> BuildAdditiveEvaluationSamples(
		UAnimSequence* Sequence,
		USkeleton* Skeleton,
		const TArray<FString>& BoneNames,
		FString& OutStatus)
	{
		TArray<TSharedPtr<FJsonValue>> Samples;
		OutStatus = TEXT("unavailable");
		if (Sequence == nullptr || Skeleton == nullptr)
		{
			return Samples;
		}

		const FReferenceSkeleton& ReferenceSkeleton = Skeleton->GetReferenceSkeleton();
		const int32 NumBones = ReferenceSkeleton.GetNum();
		if (NumBones <= 0)
		{
			OutStatus = TEXT("skeleton-empty");
			return Samples;
		}

		TArray<FBoneIndexType> RequiredBoneIndices;
		RequiredBoneIndices.Reserve(NumBones);
		for (int32 Index = 0; Index < NumBones; ++Index)
		{
			RequiredBoneIndices.Add(static_cast<FBoneIndexType>(Index));
		}
		ReferenceSkeleton.EnsureParentsExistAndSort(RequiredBoneIndices);

		FBoneContainer RequiredBones;
		UE::Anim::FCurveFilterSettings CurveFilterSettings;
		RequiredBones.InitializeTo(RequiredBoneIndices, CurveFilterSettings, *Skeleton);
		if (!RequiredBones.IsValid())
		{
			OutStatus = TEXT("bone-container-invalid");
			return Samples;
		}

		const EAdditiveAnimationType AdditiveType = static_cast<EAdditiveAnimationType>(Sequence->AdditiveAnimType.GetValue());

		const double Fractions[] = {0.0, 0.5, 1.0};
		for (const double Fraction : Fractions)
		{
			FMemMark Mark(FMemStack::Get());
			const double Time = Sequence->GetPlayLength() * Fraction;
			const FAnimExtractContext ExtractionContext(Time);

			// Base Pose (absolute). ABPT_RefPose leaves the ref pose in place;
			// sequence-based base poses are resolved by GetAdditiveBasePose.
			FCompactPose BasePose;
			BasePose.SetBoneContainer(&RequiredBones);
			BasePose.ResetToRefPose();
			FBlendedCurve BaseCurve;
			BaseCurve.InitFrom(RequiredBones);
			UE::Anim::FStackAttributeContainer BaseAttributes;
			FAnimationPoseData BasePoseData(BasePose, BaseCurve, BaseAttributes);
			Sequence->GetAdditiveBasePose(BasePoseData, ExtractionContext);

			// Additive Delta (additive identity + compressed additive delta).
			FCompactPose AdditivePose;
			AdditivePose.SetBoneContainer(&RequiredBones);
			AdditivePose.ResetToAdditiveIdentity();
			FBlendedCurve AdditiveCurve;
			AdditiveCurve.InitFrom(RequiredBones);
			UE::Anim::FStackAttributeContainer AdditiveAttributes;
			FAnimationPoseData AdditivePoseData(AdditivePose, AdditiveCurve, AdditiveAttributes);
			Sequence->GetBonePose_Additive(AdditivePoseData, ExtractionContext);

			// Capture the true Base Pose component pose BEFORE AccumulateAdditivePose mutates
			// BasePoseData in place to hold the combined result (FCSPose::InitPose stores a
			// reference, so sampling after the accumulate would alias the combined pose).
			FCSPose<FCompactPose> BaseComponentPose;
			BaseComponentPose.InitPose(BasePoseData.GetPose());
			FCSPose<FCompactPose> AdditiveComponentPose;
			AdditiveComponentPose.InitPose(AdditivePoseData.GetPose());

			// Combined = Base Pose + Additive Delta.
			FAnimationRuntime::AccumulateAdditivePose(BasePoseData, AdditivePoseData, 1.0f, AdditiveType);

			FCSPose<FCompactPose> CombinedComponentPose;
			CombinedComponentPose.InitPose(BasePoseData.GetPose());

			TSharedRef<FJsonObject> Sample = MakeShared<FJsonObject>();
			Sample->SetNumberField(TEXT("fraction"), Fraction);
			Sample->SetNumberField(TEXT("time"), Time);

			TArray<TSharedPtr<FJsonValue>> BoneValues;
			for (const FString& BoneName : BoneNames)
			{
				TSharedRef<FJsonObject> Bone = MakeShared<FJsonObject>();
				Bone->SetStringField(TEXT("bone"), BoneName);
				const int32 SkeletonBoneIndex = ReferenceSkeleton.FindBoneIndex(FName(*BoneName));
				Bone->SetBoolField(TEXT("boneExists"), SkeletonBoneIndex != INDEX_NONE);
				if (SkeletonBoneIndex != INDEX_NONE)
				{
					const FCompactPoseBoneIndex CompactBoneIndex =
						RequiredBones.MakeCompactPoseIndex(FMeshPoseBoneIndex(SkeletonBoneIndex));
					if (CompactBoneIndex.IsValid())
					{
						const FTransform BaseTransform = BaseComponentPose.GetComponentSpaceTransform(CompactBoneIndex);
						const FTransform CombinedTransform = CombinedComponentPose.GetComponentSpaceTransform(CompactBoneIndex);
						const FTransform AdditiveLocalTransform = AdditivePose[CompactBoneIndex];
						const FTransform AdditiveComponentTransform = AdditiveComponentPose.GetComponentSpaceTransform(CompactBoneIndex);

						Bone->SetField(TEXT("baseComponentScale"), MakeShared<FJsonValueObject>(VectorToJson(BaseTransform.GetScale3D())));
						Bone->SetField(TEXT("baseComponentLocation"), MakeShared<FJsonValueObject>(VectorToJson(BaseTransform.GetLocation())));
						Bone->SetField(TEXT("additiveDeltaLocalScale"), MakeShared<FJsonValueObject>(VectorToJson(AdditiveLocalTransform.GetScale3D())));
						Bone->SetField(TEXT("additiveDeltaComponentScale"), MakeShared<FJsonValueObject>(VectorToJson(AdditiveComponentTransform.GetScale3D())));
						Bone->SetField(TEXT("combinedComponentScale"), MakeShared<FJsonValueObject>(VectorToJson(CombinedTransform.GetScale3D())));
						Bone->SetField(TEXT("combinedComponentLocation"), MakeShared<FJsonValueObject>(VectorToJson(CombinedTransform.GetLocation())));
					}
				}
				BoneValues.Add(MakeShared<FJsonValueObject>(Bone));
			}
			Sample->SetArrayField(TEXT("bones"), BoneValues);
			Samples.Add(MakeShared<FJsonValueObject>(Sample));
		}

		OutStatus = TEXT("success");
		return Samples;
	}
}

bool FUEAgentKitEditorBridge::TryDiagnoseAnimationScaleResult(
	const TArray<FString>& AnimationPaths,
	const TArray<FString>& BoneNames,
	bool bLoadIfNeeded,
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
		OutErrorMessage = TEXT("Animation scale diagnosis is unavailable while PIE or SIE is active.");
		return false;
	}
	if (AnimationPaths.IsEmpty() || AnimationPaths.Num() > 32 || BoneNames.IsEmpty() || BoneNames.Num() > 16)
	{
		OutErrorCode = TEXT("live-editor-invalid-parameters");
		OutErrorMessage = TEXT("Provide 1-32 animationPaths and 1-16 boneNames.");
		return false;
	}

	TArray<TSharedPtr<FJsonValue>> AssetValues;
	for (const FString& AnimationPath : AnimationPaths)
	{
		if (!UEAgentKitEditorBridgePrivate::IsSafeGameAssetPath(AnimationPath))
		{
			OutErrorCode = TEXT("retarget_asset_not_found");
			OutErrorMessage = TEXT("Each animation must be an exact /Game Object Path.");
			return false;
		}

		UObject* Existing = StaticFindObject(UObject::StaticClass(), nullptr, *AnimationPath, false);
		const bool bLoadedBefore = Existing != nullptr;
		UAnimSequence* Sequence = Cast<UAnimSequence>(Existing);
		if (Sequence == nullptr && bLoadIfNeeded)
		{
			Sequence = LoadObject<UAnimSequence>(nullptr, *AnimationPath);
		}

		TSharedRef<FJsonObject> AssetJson = MakeShared<FJsonObject>();
		AssetJson->SetStringField(TEXT("assetPath"), AnimationPath);
		AssetJson->SetBoolField(TEXT("loadedBefore"), bLoadedBefore);
		AssetJson->SetBoolField(TEXT("loadedByBridge"), !bLoadedBefore && Sequence != nullptr);
		if (Sequence == nullptr)
		{
			AssetJson->SetStringField(TEXT("status"), bLoadIfNeeded ? TEXT("not-an-animation-sequence") : TEXT("not-loaded"));
			AssetValues.Add(MakeShared<FJsonValueObject>(AssetJson));
			continue;
		}

		USkeleton* Skeleton = Sequence->GetSkeleton();
		if (Skeleton == nullptr)
		{
			AssetJson->SetStringField(TEXT("status"), TEXT("missing-skeleton"));
			AssetValues.Add(MakeShared<FJsonValueObject>(AssetJson));
			continue;
		}

		const IAnimationDataModel* Model = Sequence->GetDataModel();
		AssetJson->SetStringField(TEXT("status"), TEXT("success"));
		AssetJson->SetStringField(TEXT("skeletonPath"), Skeleton->GetPathName());
		AssetJson->SetNumberField(TEXT("playLength"), Sequence->GetPlayLength());
		AssetJson->SetNumberField(TEXT("additiveAnimType"), static_cast<int32>(Sequence->GetAdditiveAnimType()));
		AssetJson->SetNumberField(TEXT("additiveBasePoseType"), static_cast<int32>(Sequence->RefPoseType));
		AssetJson->SetNumberField(TEXT("additiveRefFrameIndex"), Sequence->RefFrameIndex);
		AssetJson->SetStringField(
			TEXT("additiveRefSequencePath"),
			Sequence->RefPoseSeq != nullptr ? Sequence->RefPoseSeq->GetPathName() : FString());
		AssetJson->SetStringField(TEXT("retargetSourceName"), Sequence->RetargetSource.ToString());
		AssetJson->SetStringField(
			TEXT("retargetTransformsSourceName"),
			Sequence->GetRetargetTransformsSourceName().ToString());
		AssetJson->SetStringField(
			TEXT("retargetSourceAssetPath"),
			Sequence->GetRetargetSourceAsset().ToSoftObjectPath().ToString());
		AssetJson->SetBoolField(TEXT("enableRootMotion"), Sequence->bEnableRootMotion);
		AssetJson->SetBoolField(TEXT("forceRootLock"), Sequence->bForceRootLock);
		AssetJson->SetBoolField(TEXT("useNormalizedRootMotionScale"), Sequence->bUseNormalizedRootMotionScale);
		AssetJson->SetNumberField(TEXT("rootMotionRootLock"), static_cast<int32>(Sequence->RootMotionRootLock));
		const TArray<FTransform>& RetargetTransforms = Sequence->GetRetargetTransforms();
		AssetJson->SetNumberField(TEXT("retargetTransformCount"), RetargetTransforms.Num());
		AssetJson->SetNumberField(
			TEXT("retargetSourceAssetReferencePoseCount"),
			Sequence->RetargetSourceAssetReferencePose.Num());
		if (!RetargetTransforms.IsEmpty())
		{
			AssetJson->SetField(
				TEXT("retargetRootScale"),
				MakeShared<FJsonValueObject>(VectorToJson(RetargetTransforms[0].GetScale3D())));
		}
		if (Model != nullptr)
		{
			AssetJson->SetNumberField(TEXT("frameRate"), Model->GetFrameRate().AsDecimal());
			AssetJson->SetNumberField(TEXT("frameCount"), Model->GetNumberOfFrames());
			AssetJson->SetNumberField(TEXT("keyCount"), Model->GetNumberOfKeys());
			AssetJson->SetNumberField(TEXT("boneTrackCount"), Model->GetNumBoneTracks());
		}

		TArray<TSharedPtr<FJsonValue>> TrackValues;
		for (const FString& BoneName : BoneNames)
		{
			TrackValues.Add(MakeShared<FJsonValueObject>(BuildTrackScaleSummary(Sequence, Skeleton, BoneName)));
		}
		AssetJson->SetArrayField(TEXT("tracks"), TrackValues);
		FString PreviewStatus;
		FString PreviewMeshPath;
		TArray<TSharedPtr<FJsonValue>> PreviewSamples = BuildPreviewSamples(
			Sequence,
			Skeleton,
			BoneNames,
			bLoadIfNeeded,
			PreviewStatus,
			PreviewMeshPath);
		AssetJson->SetStringField(TEXT("previewEvaluationStatus"), PreviewStatus);
		AssetJson->SetStringField(TEXT("previewMeshPath"), PreviewMeshPath);
		AssetJson->SetStringField(TEXT("previewEvaluationSource"), TEXT("editor-world-transient-component"));
		AssetJson->SetArrayField(
			TEXT("previewSamples"),
			PreviewSamples);
		AssetValues.Add(MakeShared<FJsonValueObject>(AssetJson));
	}

	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("action"), TEXT("diagnose-animation-scale"));
	Result->SetBoolField(TEXT("loadIfNeeded"), bLoadIfNeeded);
	Result->SetArrayField(TEXT("assets"), AssetValues);
	Result->SetStringField(TEXT("editorSessionId"), SessionId);
	OutResult = Result;
	return true;
}

bool FUEAgentKitEditorBridge::TryDiagnoseAdditiveAnimationResult(
	const TArray<FString>& AnimationPaths,
	bool bLoadIfNeeded,
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
		OutErrorMessage = TEXT("Additive animation diagnosis is unavailable while PIE or SIE is active.");
		return false;
	}
	if (AnimationPaths.IsEmpty() || AnimationPaths.Num() > 32)
	{
		OutErrorCode = TEXT("live-editor-invalid-parameters");
		OutErrorMessage = TEXT("Provide 1-32 animationPaths.");
		return false;
	}

	TArray<TSharedPtr<FJsonValue>> AssetValues;
	for (const FString& AnimationPath : AnimationPaths)
	{
		if (!UEAgentKitEditorBridgePrivate::IsSafeGameAssetPath(AnimationPath))
		{
			OutErrorCode = TEXT("retarget_asset_not_found");
			OutErrorMessage = TEXT("Each animation must be an exact /Game Object Path.");
			return false;
		}

		UObject* Existing = StaticFindObject(UObject::StaticClass(), nullptr, *AnimationPath, false);
		const bool bLoadedBefore = Existing != nullptr;
		UAnimSequence* Sequence = Cast<UAnimSequence>(Existing);
		if (Sequence == nullptr && bLoadIfNeeded)
		{
			Sequence = LoadObject<UAnimSequence>(nullptr, *AnimationPath);
		}

		TSharedRef<FJsonObject> AssetJson = MakeShared<FJsonObject>();
		AssetJson->SetStringField(TEXT("assetPath"), AnimationPath);
		AssetJson->SetBoolField(TEXT("loadedBefore"), bLoadedBefore);
		AssetJson->SetBoolField(TEXT("loadedByBridge"), !bLoadedBefore && Sequence != nullptr);
		if (Sequence == nullptr)
		{
			AssetJson->SetStringField(TEXT("status"), bLoadIfNeeded ? TEXT("not-an-animation-sequence") : TEXT("not-loaded"));
			AssetValues.Add(MakeShared<FJsonValueObject>(AssetJson));
			continue;
		}

		USkeleton* Skeleton = Sequence->GetSkeleton();
		if (Skeleton == nullptr)
		{
			AssetJson->SetStringField(TEXT("status"), TEXT("missing-skeleton"));
			AssetValues.Add(MakeShared<FJsonValueObject>(AssetJson));
			continue;
		}

		const EAdditiveAnimationType AdditiveType = static_cast<EAdditiveAnimationType>(Sequence->AdditiveAnimType.GetValue());
		const EAdditiveBasePoseType BasePoseType = static_cast<EAdditiveBasePoseType>(Sequence->RefPoseType.GetValue());
		UAnimSequence* RefPoseSeq = Sequence->RefPoseSeq.Get();

		AssetJson->SetStringField(TEXT("status"), TEXT("success"));
		AssetJson->SetStringField(TEXT("skeletonPath"), Skeleton->GetPathName());
		AssetJson->SetNumberField(TEXT("additiveAnimType"), static_cast<int32>(AdditiveType));
		AssetJson->SetStringField(TEXT("additiveTypeName"), AssetReaderRegistryPrivate::AdditiveAnimationTypeToString(AdditiveType));
		AssetJson->SetNumberField(TEXT("additiveBasePoseType"), static_cast<int32>(BasePoseType));
		AssetJson->SetStringField(TEXT("basePoseTypeName"), AssetReaderRegistryPrivate::AdditiveBasePoseTypeToString(BasePoseType));
		AssetJson->SetNumberField(TEXT("additiveRefFrameIndex"), Sequence->RefFrameIndex);
		AssetJson->SetStringField(
			TEXT("additiveRefSequencePath"),
			RefPoseSeq != nullptr ? RefPoseSeq->GetPathName() : FString());

		TSharedRef<FJsonObject> BasePoseJson = MakeShared<FJsonObject>();
		const bool bRefSequenceResolved = RefPoseSeq != nullptr;
		BasePoseJson->SetBoolField(TEXT("refSequenceResolved"), bRefSequenceResolved);
		if (bRefSequenceResolved)
		{
			USkeleton* RefSkeleton = RefPoseSeq->GetSkeleton();
			BasePoseJson->SetStringField(
				TEXT("skeletonPath"),
				RefSkeleton != nullptr ? RefSkeleton->GetPathName() : FString());
			BasePoseJson->SetBoolField(TEXT("skeletonCompatible"), RefSkeleton != nullptr && RefSkeleton == Skeleton);
			const IAnimationDataModel* RefModel = RefPoseSeq->GetDataModel();
			const int32 RefFrameCount = RefModel != nullptr ? RefModel->GetNumberOfFrames() : 0;
			BasePoseJson->SetNumberField(TEXT("frameCount"), RefFrameCount);
			const int32 RefFrameIndex = Sequence->RefFrameIndex;
			BasePoseJson->SetBoolField(TEXT("refFrameValid"), RefFrameIndex >= 0 && RefFrameIndex < RefFrameCount);
		}
		AssetJson->SetObjectField(TEXT("basePose"), BasePoseJson);

		AssetValues.Add(MakeShared<FJsonValueObject>(AssetJson));
	}

	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("action"), TEXT("diagnose-additive-animation"));
	Result->SetBoolField(TEXT("loadIfNeeded"), bLoadIfNeeded);
	Result->SetArrayField(TEXT("assets"), AssetValues);
	Result->SetStringField(TEXT("editorSessionId"), SessionId);
	OutResult = Result;
	return true;
}

bool FUEAgentKitEditorBridge::TryEvaluateAnimationWithBasePoseResult(
	const TArray<FString>& AnimationPaths,
	const TArray<FString>& BoneNames,
	bool bLoadIfNeeded,
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
		OutErrorMessage = TEXT("Additive evaluation is unavailable while PIE or SIE is active.");
		return false;
	}
	if (AnimationPaths.IsEmpty() || AnimationPaths.Num() > 32 || BoneNames.IsEmpty() || BoneNames.Num() > 16)
	{
		OutErrorCode = TEXT("live-editor-invalid-parameters");
		OutErrorMessage = TEXT("Provide 1-32 animationPaths and 1-16 boneNames.");
		return false;
	}

	TArray<TSharedPtr<FJsonValue>> AssetValues;
	for (const FString& AnimationPath : AnimationPaths)
	{
		if (!UEAgentKitEditorBridgePrivate::IsSafeGameAssetPath(AnimationPath))
		{
			OutErrorCode = TEXT("retarget_asset_not_found");
			OutErrorMessage = TEXT("Each animation must be an exact /Game Object Path.");
			return false;
		}

		UObject* Existing = StaticFindObject(UObject::StaticClass(), nullptr, *AnimationPath, false);
		const bool bLoadedBefore = Existing != nullptr;
		UAnimSequence* Sequence = Cast<UAnimSequence>(Existing);
		if (Sequence == nullptr && bLoadIfNeeded)
		{
			Sequence = LoadObject<UAnimSequence>(nullptr, *AnimationPath);
		}

		TSharedRef<FJsonObject> AssetJson = MakeShared<FJsonObject>();
		AssetJson->SetStringField(TEXT("assetPath"), AnimationPath);
		AssetJson->SetBoolField(TEXT("loadedBefore"), bLoadedBefore);
		AssetJson->SetBoolField(TEXT("loadedByBridge"), !bLoadedBefore && Sequence != nullptr);
		if (Sequence == nullptr)
		{
			AssetJson->SetStringField(TEXT("status"), bLoadIfNeeded ? TEXT("not-an-animation-sequence") : TEXT("not-loaded"));
			AssetValues.Add(MakeShared<FJsonValueObject>(AssetJson));
			continue;
		}

		USkeleton* Skeleton = Sequence->GetSkeleton();
		if (Skeleton == nullptr)
		{
			AssetJson->SetStringField(TEXT("status"), TEXT("missing-skeleton"));
			AssetValues.Add(MakeShared<FJsonValueObject>(AssetJson));
			continue;
		}

		const EAdditiveAnimationType AdditiveType = static_cast<EAdditiveAnimationType>(Sequence->AdditiveAnimType.GetValue());
		const EAdditiveBasePoseType BasePoseType = static_cast<EAdditiveBasePoseType>(Sequence->RefPoseType.GetValue());
		UAnimSequence* RefPoseSeq = Sequence->RefPoseSeq.Get();

		AssetJson->SetStringField(TEXT("status"), TEXT("success"));
		AssetJson->SetStringField(TEXT("skeletonPath"), Skeleton->GetPathName());
		AssetJson->SetNumberField(TEXT("additiveAnimType"), static_cast<int32>(AdditiveType));
		AssetJson->SetStringField(TEXT("additiveTypeName"), AssetReaderRegistryPrivate::AdditiveAnimationTypeToString(AdditiveType));
		AssetJson->SetNumberField(TEXT("additiveBasePoseType"), static_cast<int32>(BasePoseType));
		AssetJson->SetStringField(TEXT("basePoseTypeName"), AssetReaderRegistryPrivate::AdditiveBasePoseTypeToString(BasePoseType));
		AssetJson->SetNumberField(TEXT("additiveRefFrameIndex"), Sequence->RefFrameIndex);
		AssetJson->SetStringField(
			TEXT("additiveRefSequencePath"),
			RefPoseSeq != nullptr ? RefPoseSeq->GetPathName() : FString());

		bool bRefFrameValid = true;
		TSharedRef<FJsonObject> BasePoseJson = MakeShared<FJsonObject>();
		const bool bRefSequenceResolved = RefPoseSeq != nullptr;
		BasePoseJson->SetBoolField(TEXT("refSequenceResolved"), bRefSequenceResolved);
		if (bRefSequenceResolved)
		{
			USkeleton* RefSkeleton = RefPoseSeq->GetSkeleton();
			BasePoseJson->SetStringField(
				TEXT("skeletonPath"),
				RefSkeleton != nullptr ? RefSkeleton->GetPathName() : FString());
			BasePoseJson->SetBoolField(TEXT("skeletonCompatible"), RefSkeleton != nullptr && RefSkeleton == Skeleton);
			const IAnimationDataModel* RefModel = RefPoseSeq->GetDataModel();
			const int32 RefFrameCount = RefModel != nullptr ? RefModel->GetNumberOfFrames() : 0;
			BasePoseJson->SetNumberField(TEXT("frameCount"), RefFrameCount);
			const int32 RefFrameIndex = Sequence->RefFrameIndex;
			bRefFrameValid = RefFrameIndex >= 0 && RefFrameIndex < RefFrameCount;
			BasePoseJson->SetBoolField(TEXT("refFrameValid"), bRefFrameValid);
		}
		AssetJson->SetObjectField(TEXT("basePose"), BasePoseJson);

		TSharedRef<FJsonObject> EvaluationJson = MakeShared<FJsonObject>();
		const bool bAdditive = Sequence->IsValidAdditive();
		if (!bAdditive)
		{
			EvaluationJson->SetStringField(TEXT("status"), TEXT("skipped-non-additive"));
		}
		else
		{
			const bool bNeedsSequence = BasePoseType == ABPT_AnimScaled || BasePoseType == ABPT_AnimFrame;
			const bool bSkeletonCompatible = !bNeedsSequence
				|| (RefPoseSeq != nullptr && RefPoseSeq->GetSkeleton() == Skeleton);
			if (bNeedsSequence && RefPoseSeq == nullptr)
			{
				EvaluationJson->SetStringField(TEXT("status"), TEXT("skipped-missing-base-pose"));
			}
			else if (!bSkeletonCompatible)
			{
				EvaluationJson->SetStringField(TEXT("status"), TEXT("skipped-skeleton-mismatch"));
			}
			else
			{
				FString EvalStatus;
				TArray<TSharedPtr<FJsonValue>> Samples = BuildAdditiveEvaluationSamples(
					Sequence,
					Skeleton,
					BoneNames,
					EvalStatus);
				EvaluationJson->SetStringField(TEXT("status"), EvalStatus == TEXT("success") ? TEXT("evaluated") : EvalStatus);
				if (EvalStatus == TEXT("success"))
				{
					EvaluationJson->SetStringField(TEXT("source"), TEXT("editor-bone-pose-additive-accumulate"));
					const bool bFrameBased = BasePoseType == ABPT_AnimFrame || BasePoseType == ABPT_LocalAnimFrame;
					EvaluationJson->SetBoolField(TEXT("refFrameClamped"), bFrameBased && !bRefFrameValid);
					EvaluationJson->SetArrayField(TEXT("samples"), Samples);
				}
			}
		}
		AssetJson->SetObjectField(TEXT("evaluation"), EvaluationJson);

		AssetValues.Add(MakeShared<FJsonValueObject>(AssetJson));
	}

	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("action"), TEXT("evaluate-animation-with-base-pose"));
	Result->SetBoolField(TEXT("loadIfNeeded"), bLoadIfNeeded);
	Result->SetArrayField(TEXT("assets"), AssetValues);
	Result->SetStringField(TEXT("editorSessionId"), SessionId);
	OutResult = Result;
	return true;
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

bool FUEAgentKitEditorBridge::TryValidateAnimationRetargetResult(
	const FString& RetargeterPath,
	const TArray<FString>& AnimationPaths,
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
		OutErrorMessage = TEXT("Retarget validation is unavailable while PIE or SIE is active.");
		return false;
	}
	UIKRetargeter* Retargeter = Cast<UIKRetargeter>(LoadObject<UIKRetargeter>(nullptr, *RetargeterPath));
	if (Retargeter == nullptr)
	{
		OutErrorCode = TEXT("retarget_asset_not_found");
		OutErrorMessage = TEXT("The IK Retargeter asset is not loaded; load it first.");
		return false;
	}
	TArray<FString> LoadedPaths;
	for (const FString& Path : AnimationPaths)
	{
		if (!UEAgentKitEditorBridgePrivate::IsSafeGameAssetPath(Path))
		{
			OutErrorCode = TEXT("retarget_asset_not_found");
			OutErrorMessage = TEXT("Each validation animation must be an exact /Game object path.");
			return false;
		}
		LoadedPaths.Add(Path);
	}

	TArray<UEAgentKitRetarget::FRetargetValidationIssue> Issues;
	FString Verdict;
	if (!UEAgentKitRetarget::ValidateAnimationRetarget(
			Retargeter,
			LoadedPaths,
			Issues,
			Verdict,
			OutErrorCode,
			OutErrorMessage))
	{
		return false;
	}

	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("action"), TEXT("validate-animation-retarget"));
	Result->SetStringField(TEXT("retargeter"), RetargeterPath);
	Result->SetStringField(TEXT("verdict"), Verdict);
	Result->SetNumberField(TEXT("animationCount"), LoadedPaths.Num());
	TArray<TSharedPtr<FJsonValue>> IssueValues;
	for (const UEAgentKitRetarget::FRetargetValidationIssue& Issue : Issues)
	{
		IssueValues.Add(MakeShared<FJsonValueObject>(UEAgentKitRetarget::ValidationIssueToJson(Issue)));
	}
	Result->SetArrayField(TEXT("issues"), IssueValues);
	Result->SetStringField(TEXT("editorSessionId"), SessionId);
	OutResult = Result;
	return true;
}
