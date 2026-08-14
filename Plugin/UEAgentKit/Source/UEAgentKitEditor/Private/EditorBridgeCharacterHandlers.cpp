#include "AssetReaders/AssetReaderCommon.h"
#include "EditorBridge.h"
#include "EditorBridgeHandlerUtils.h"

#include "Animation/AnimSequence.h"
#include "Animation/AnimTypes.h"
#include "Animation/Skeleton.h"
#include "Components/CapsuleComponent.h"
#include "Components/SkeletalMeshComponent.h"
#include "Dom/JsonObject.h"
#include "Editor.h"
#include "Engine/Blueprint.h"
#include "Engine/SkeletalMesh.h"
#include "Engine/World.h"
#include "GameFramework/Character.h"
#include "UObject/UObjectGlobals.h"

namespace
{
	TSharedRef<FJsonObject> BuildBoneTransformJson(
		const USkeletalMeshComponent* Component,
		const FString& BoneName)
	{
		TSharedRef<FJsonObject> Bone = MakeShared<FJsonObject>();
		Bone->SetStringField(TEXT("bone"), BoneName);
		const int32 BoneIndex = Component->GetBoneIndex(FName(*BoneName));
		Bone->SetBoolField(TEXT("boneExists"), BoneIndex != INDEX_NONE);
		if (BoneIndex != INDEX_NONE)
		{
			const FTransform Transform = Component->GetBoneTransform(BoneIndex, FTransform::Identity);
			Bone->SetField(
				TEXT("componentScale"),
				MakeShared<FJsonValueObject>(AssetReaderRegistryPrivate::VectorToJson(Transform.GetScale3D())));
			Bone->SetField(
				TEXT("componentLocation"),
				MakeShared<FJsonValueObject>(AssetReaderRegistryPrivate::VectorToJson(Transform.GetLocation())));
		}
		return Bone;
	}

	float GetFootToCapsuleBottom(
		const UCapsuleComponent* Capsule,
		const FTransform& MeshRelative,
		const FVector& FootComponentLocation)
	{
		const float CapsuleBottomZ =
			Capsule->GetRelativeLocation().Z - Capsule->GetScaledCapsuleHalfHeight();
		const FVector FootActorLocation = MeshRelative.TransformPosition(FootComponentLocation);
		return FootActorLocation.Z - CapsuleBottomZ;
	}
}

bool FUEAgentKitEditorBridge::TryDiagnoseCharacterGroundContactResult(
	const FString& CharacterPath,
	const FString& AnimationPath,
	const FString& RootBone,
	const FString& PelvisBone,
	const FString& LeftFootBone,
	const FString& RightFootBone,
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
		OutErrorMessage = TEXT("Character ground-contact diagnosis is unavailable while PIE or SIE is active.");
		return false;
	}
	if (CharacterPath.IsEmpty() || !UEAgentKitEditorBridgePrivate::IsSafeGameAssetPath(CharacterPath))
	{
		OutErrorCode = TEXT("retarget_asset_not_found");
		OutErrorMessage = TEXT("characterPath must be an exact /Game Object Path.");
		return false;
	}
	if (!AnimationPath.IsEmpty() && !UEAgentKitEditorBridgePrivate::IsSafeGameAssetPath(AnimationPath))
	{
		OutErrorCode = TEXT("retarget_asset_not_found");
		OutErrorMessage = TEXT("animationPath must be an exact /Game Object Path.");
		return false;
	}

	UObject* ExistingCharacter = StaticFindObject(UObject::StaticClass(), nullptr, *CharacterPath, false);
	const bool bCharacterLoadedBefore = ExistingCharacter != nullptr;
	UBlueprint* CharacterBlueprint = Cast<UBlueprint>(ExistingCharacter);
	if (CharacterBlueprint == nullptr && bLoadIfNeeded)
	{
		CharacterBlueprint = LoadObject<UBlueprint>(nullptr, *CharacterPath);
	}

	UClass* CharacterClass =
		CharacterBlueprint != nullptr ? CharacterBlueprint->GeneratedClass.Get() : nullptr;
	ACharacter* CharacterCDO =
		CharacterClass != nullptr ? Cast<ACharacter>(CharacterClass->GetDefaultObject(false)) : nullptr;
	if (CharacterCDO == nullptr)
	{
		OutErrorCode = TEXT("character-load-failed");
		OutErrorMessage = TEXT("characterPath must resolve to a compiled ACharacter Blueprint.");
		return false;
	}

	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("action"), TEXT("diagnose-character-ground-contact"));
	Result->SetBoolField(TEXT("loadIfNeeded"), bLoadIfNeeded);

	TSharedRef<FJsonObject> CharacterJson = MakeShared<FJsonObject>();
	CharacterJson->SetStringField(TEXT("path"), CharacterPath);
	CharacterJson->SetBoolField(TEXT("loadedBefore"), bCharacterLoadedBefore);
	CharacterJson->SetBoolField(TEXT("loadedByBridge"), !bCharacterLoadedBefore && CharacterBlueprint != nullptr);
	CharacterJson->SetStringField(TEXT("classPath"), CharacterClass != nullptr ? CharacterClass->GetPathName() : FString());

	UCapsuleComponent* Capsule = CharacterCDO->GetCapsuleComponent();
	TSharedRef<FJsonObject> CapsuleJson = MakeShared<FJsonObject>();
	CapsuleJson->SetBoolField(TEXT("present"), Capsule != nullptr);
	if (Capsule != nullptr)
	{
		CapsuleJson->SetNumberField(TEXT("radius"), Capsule->GetScaledCapsuleRadius());
		CapsuleJson->SetNumberField(TEXT("halfHeight"), Capsule->GetScaledCapsuleHalfHeight());
		CapsuleJson->SetField(
			TEXT("relativeLocation"),
			MakeShared<FJsonValueObject>(AssetReaderRegistryPrivate::VectorToJson(Capsule->GetRelativeLocation())));
	}
	CharacterJson->SetObjectField(TEXT("capsule"), CapsuleJson);

	USkeletalMeshComponent* MeshComponent = CharacterCDO->GetMesh();
	USkeletalMesh* CharacterMesh = nullptr;
	USkeleton* CharacterSkeleton = nullptr;
	TSharedRef<FJsonObject> MeshJson = MakeShared<FJsonObject>();
	MeshJson->SetBoolField(TEXT("present"), MeshComponent != nullptr);
	if (MeshComponent != nullptr)
	{
		MeshJson->SetField(
			TEXT("relativeTransform"),
			MakeShared<FJsonValueObject>(AssetReaderRegistryPrivate::TransformToJson(MeshComponent->GetRelativeTransform())));
		CharacterMesh = MeshComponent->GetSkeletalMeshAsset();
		MeshJson->SetStringField(
			TEXT("skeletalMeshPath"),
			CharacterMesh != nullptr ? CharacterMesh->GetPathName() : FString());
		CharacterSkeleton = CharacterMesh != nullptr ? CharacterMesh->GetSkeleton() : nullptr;
		MeshJson->SetStringField(
			TEXT("skeletonPath"),
			CharacterSkeleton != nullptr ? CharacterSkeleton->GetPathName() : FString());
	}
	CharacterJson->SetObjectField(TEXT("mesh"), MeshJson);
	Result->SetObjectField(TEXT("character"), CharacterJson);

	TArray<FString> BoneNames;
	BoneNames.Add(RootBone);
	BoneNames.Add(PelvisBone);
	BoneNames.Add(LeftFootBone);
	BoneNames.Add(RightFootBone);

	TSharedRef<FJsonObject> BoneNamesJson = MakeShared<FJsonObject>();
	BoneNamesJson->SetStringField(TEXT("root"), RootBone);
	BoneNamesJson->SetStringField(TEXT("pelvis"), PelvisBone);
	BoneNamesJson->SetStringField(TEXT("leftFoot"), LeftFootBone);
	BoneNamesJson->SetStringField(TEXT("rightFoot"), RightFootBone);
	Result->SetObjectField(TEXT("boneNames"), BoneNamesJson);

	// Resolve the optional animation and classify its evaluation feasibility.
	UAnimSequence* Sequence = nullptr;
	bool bAnimationLoadedBefore = false;
	bool bAnimationLoadedByBridge = false;
	if (!AnimationPath.IsEmpty())
	{
		UObject* ExistingAnimation = StaticFindObject(UObject::StaticClass(), nullptr, *AnimationPath, false);
		bAnimationLoadedBefore = ExistingAnimation != nullptr;
		Sequence = Cast<UAnimSequence>(ExistingAnimation);
		if (Sequence == nullptr && bLoadIfNeeded)
		{
			Sequence = LoadObject<UAnimSequence>(nullptr, *AnimationPath);
		}
		bAnimationLoadedByBridge = !bAnimationLoadedBefore && Sequence != nullptr;
	}

	USkeleton* AnimationSkeleton = Sequence != nullptr ? Sequence->GetSkeleton() : nullptr;
	const bool bSkeletonCompatible =
		AnimationSkeleton != nullptr && CharacterSkeleton != nullptr && AnimationSkeleton == CharacterSkeleton;

	FString AnimationStatus;
	if (Sequence == nullptr)
	{
		AnimationStatus = AnimationPath.IsEmpty()
			? TEXT("skipped-no-animation")
			: (bLoadIfNeeded ? TEXT("not-an-animation-sequence") : TEXT("not-loaded"));
	}
	else if (AnimationSkeleton == nullptr)
	{
		AnimationStatus = TEXT("missing-skeleton");
	}
	else if (CharacterSkeleton == nullptr)
	{
		AnimationStatus = TEXT("character-mesh-unavailable");
	}
	else if (!bSkeletonCompatible)
	{
		AnimationStatus = TEXT("skeleton-mismatch");
	}
	else if (Sequence->IsValidAdditive())
	{
		AnimationStatus = TEXT("unsupported-additive");
	}
	else
	{
		AnimationStatus = TEXT("evaluated");
	}

	TSharedRef<FJsonObject> AnimationJson = MakeShared<FJsonObject>();
	AnimationJson->SetStringField(TEXT("path"), AnimationPath);
	AnimationJson->SetBoolField(TEXT("loadedBefore"), bAnimationLoadedBefore);
	AnimationJson->SetBoolField(TEXT("loadedByBridge"), bAnimationLoadedByBridge);
	AnimationJson->SetStringField(TEXT("status"), AnimationStatus);
	AnimationJson->SetStringField(
		TEXT("skeletonPath"),
		AnimationSkeleton != nullptr ? AnimationSkeleton->GetPathName() : FString());
	AnimationJson->SetBoolField(TEXT("skeletonCompatible"), bSkeletonCompatible);

	USkeletalMesh* PreviewMesh = CharacterMesh;
	if (PreviewMesh == nullptr && Sequence != nullptr)
	{
		PreviewMesh = Sequence->GetPreviewMesh(bLoadIfNeeded);
	}
	if (PreviewMesh == nullptr && AnimationSkeleton != nullptr)
	{
		PreviewMesh = AnimationSkeleton->GetPreviewMesh(bLoadIfNeeded);
	}

	TSharedRef<FJsonObject> SkeletonReferenceJson = MakeShared<FJsonObject>();
	Result->SetObjectField(TEXT("skeletonReference"), SkeletonReferenceJson);

	if (PreviewMesh == nullptr)
	{
		SkeletonReferenceJson->SetStringField(TEXT("status"), TEXT("preview-mesh-unavailable"));
	}
	else
	{
		AnimationJson->SetStringField(TEXT("previewMeshPath"), PreviewMesh->GetPathName());

		UWorld* EditorWorld = GEditor != nullptr ? GEditor->GetEditorWorldContext().World() : nullptr;
		if (EditorWorld == nullptr)
		{
			SkeletonReferenceJson->SetStringField(TEXT("status"), TEXT("editor-world-unavailable"));
		}
		else
		{
			USkeletalMeshComponent* Component =
				NewObject<USkeletalMeshComponent>(GetTransientPackage(), NAME_None, RF_Transient);
			if (Component == nullptr)
			{
				SkeletonReferenceJson->SetStringField(TEXT("status"), TEXT("component-create-failed"));
			}
			else
			{
				Component->SetVisibility(false, true);
				Component->SetHiddenInGame(true);
				Component->SetSkeletalMesh(PreviewMesh);
				Component->RegisterComponentWithWorld(EditorWorld);
				if (!Component->IsRegistered())
				{
					Component->MarkAsGarbage();
					SkeletonReferenceJson->SetStringField(TEXT("status"), TEXT("component-registration-failed"));
				}
				else
				{
					Component->SetUpdateAnimationInEditor(true);
					Component->VisibilityBasedAnimTickOption =
						EVisibilityBasedAnimTickOption::AlwaysTickPoseAndRefreshBones;

					// Skeleton Reference Pose (no animation assigned).
					Component->RefreshBoneTransforms();
					TArray<TSharedPtr<FJsonValue>> ReferenceBones;
					for (const FString& BoneName : BoneNames)
					{
						ReferenceBones.Add(MakeShared<FJsonValueObject>(BuildBoneTransformJson(Component, BoneName)));
					}
					SkeletonReferenceJson->SetStringField(TEXT("status"), TEXT("evaluated"));
					SkeletonReferenceJson->SetArrayField(TEXT("bones"), ReferenceBones);

					const bool bCanAnimate = AnimationStatus == TEXT("evaluated");
					if (bCanAnimate)
					{
						Component->SetAnimationMode(EAnimationMode::AnimationSingleNode, true);
						Component->SetAnimation(Sequence);
					}

					TArray<TSharedPtr<FJsonValue>> SampleValues;
					if (bCanAnimate)
					{
						const FTransform MeshRelative =
							MeshComponent != nullptr ? MeshComponent->GetRelativeTransform() : FTransform::Identity;
						const bool bCanComputeDistance = Capsule != nullptr && MeshComponent != nullptr;
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
							Sample->SetField(
								TEXT("boundsOrigin"),
								MakeShared<FJsonValueObject>(AssetReaderRegistryPrivate::VectorToJson(Bounds.Origin)));
							Sample->SetField(
								TEXT("boundsExtent"),
								MakeShared<FJsonValueObject>(AssetReaderRegistryPrivate::VectorToJson(Bounds.BoxExtent)));

							FVector LeftFootLocation(FVector::ZeroVector);
							FVector RightFootLocation(FVector::ZeroVector);
							bool bLeftFootFound = false;
							bool bRightFootFound = false;

							TArray<TSharedPtr<FJsonValue>> SampleBones;
							for (const FString& BoneName : BoneNames)
							{
								const int32 BoneIndex = Component->GetBoneIndex(FName(*BoneName));
								TSharedRef<FJsonObject> Bone = MakeShared<FJsonObject>();
								Bone->SetStringField(TEXT("bone"), BoneName);
								Bone->SetBoolField(TEXT("boneExists"), BoneIndex != INDEX_NONE);
								if (BoneIndex != INDEX_NONE)
								{
									const FTransform Transform =
										Component->GetBoneTransform(BoneIndex, FTransform::Identity);
									Bone->SetField(
										TEXT("componentScale"),
										MakeShared<FJsonValueObject>(
											AssetReaderRegistryPrivate::VectorToJson(Transform.GetScale3D())));
									Bone->SetField(
										TEXT("componentLocation"),
										MakeShared<FJsonValueObject>(
											AssetReaderRegistryPrivate::VectorToJson(Transform.GetLocation())));
									if (BoneName == LeftFootBone)
									{
										LeftFootLocation = Transform.GetLocation();
										bLeftFootFound = true;
									}
									if (BoneName == RightFootBone)
									{
										RightFootLocation = Transform.GetLocation();
										bRightFootFound = true;
									}
								}
								SampleBones.Add(MakeShared<FJsonValueObject>(Bone));
							}
							Sample->SetArrayField(TEXT("bones"), SampleBones);

							if (bLeftFootFound)
							{
								Sample->SetNumberField(TEXT("leftFootLowestZ"), LeftFootLocation.Z);
							}
							if (bRightFootFound)
							{
								Sample->SetNumberField(TEXT("rightFootLowestZ"), RightFootLocation.Z);
							}
							if (bCanComputeDistance)
							{
								if (bLeftFootFound)
								{
									Sample->SetNumberField(
										TEXT("leftFootToCapsuleBottom"),
										GetFootToCapsuleBottom(Capsule, MeshRelative, LeftFootLocation));
								}
								if (bRightFootFound)
								{
									Sample->SetNumberField(
										TEXT("rightFootToCapsuleBottom"),
										GetFootToCapsuleBottom(Capsule, MeshRelative, RightFootLocation));
								}
							}

							SampleValues.Add(MakeShared<FJsonValueObject>(Sample));
						}

						const FDeltaTimeRecord RootMotionDelta(static_cast<float>(Sequence->GetPlayLength()));
						const FAnimExtractContext RootMotionContext(0.0, true, RootMotionDelta, false);
						const FTransform RootMotion = Sequence->ExtractRootMotion(RootMotionContext);
						AnimationJson->SetNumberField(TEXT("rootMotionZ"), RootMotion.GetLocation().Z);
						AnimationJson->SetField(
							TEXT("rootMotionTranslation"),
							MakeShared<FJsonValueObject>(
								AssetReaderRegistryPrivate::VectorToJson(RootMotion.GetLocation())));
					}
					AnimationJson->SetArrayField(TEXT("samples"), SampleValues);

					Component->SetAnimation(nullptr);
					Component->SetUpdateAnimationInEditor(false);
					Component->UnregisterComponent();
					Component->MarkAsGarbage();
				}
			}
		}
	}

	Result->SetObjectField(TEXT("animation"), AnimationJson);
	Result->SetStringField(TEXT("editorSessionId"), SessionId);
	OutResult = Result;
	return true;
}
