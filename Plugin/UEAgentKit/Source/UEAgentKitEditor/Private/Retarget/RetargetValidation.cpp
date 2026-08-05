#include "Retarget/RetargetValidation.h"

#include "Animation/AnimSequence.h"
#include "Animation/AnimationAsset.h"
#include "Animation/AnimationTypes.h"
#include "Animation/Skeleton.h"
#include "Engine/SkeletalMesh.h"
#include "Rig/IKRigDefinition.h"
#include "IKRigEditor/Public/RigEditor/IKRigController.h"
#include "Retargeter/IKRetargetChainMapping.h"
#include "Retargeter/IKRetargeter.h"
#include "Retargeter/RetargetOps/FKChainsOp.h"
#include "RetargetEditor/IKRetargeterController.h"
#include "UObject/Package.h"

namespace UEAgentKitRetarget
{
	namespace
	{
		FString BoneOrNone(const FName& Name)
		{
			return Name.IsNone() ? FString() : Name.ToString();
		}

		// Resolves the major diagnostic bones from the target IK Rig chains.
		struct FMajorBones
		{
			FString Root;
			FString Pelvis;
			FString Head;
			FString LeftHand;
			FString RightHand;
			FString LeftFoot;
			FString RightFoot;
		};

		bool ResolveMajorBones(const UIKRigDefinition* TargetRig, FMajorBones& OutBones)
		{
			if (TargetRig == nullptr)
			{
				return false;
			}
			const UIKRigController* Controller = UIKRigController::GetController(TargetRig);
			if (Controller == nullptr)
			{
				return false;
			}
			for (const FBoneChain& Chain : Controller->GetRetargetChains())
			{
				const FString Name = Chain.ChainName.ToString();
				const FString Start = BoneOrNone(Chain.StartBone.BoneName);
				const FString End = BoneOrNone(Chain.EndBone.BoneName);
				if (Name == TEXT("Root")) { OutBones.Root = Start; }
				else if (Name == TEXT("Spine")) { OutBones.Pelvis = Start; }
				else if (Name == TEXT("Head")) { OutBones.Head = End; }
				else if (Name == TEXT("LeftHand")) { OutBones.LeftHand = End; }
				else if (Name == TEXT("RightHand")) { OutBones.RightHand = End; }
				else if (Name == TEXT("LeftFoot")) { OutBones.LeftFoot = End; }
				else if (Name == TEXT("RightFoot")) { OutBones.RightFoot = End; }
			}
			return true;
		}

		// Computes a bone component (world) transform by walking the reference
		// skeleton, using the sequence's local track when present.
		FTransform GetComponentTransform(
			const UAnimSequence* Sequence,
			const USkeleton* Skeleton,
			const FName& BoneName,
			float Time)
		{
			const FReferenceSkeleton& RefSkeleton = Skeleton->GetReferenceSkeleton();
			const int32 BoneIndex = RefSkeleton.FindBoneIndex(BoneName);
			if (BoneIndex == INDEX_NONE)
			{
				return FTransform(FQuat::Identity, FVector(-MAX_flt, -MAX_flt, -MAX_flt));
			}
			FTransform Component = FTransform::Identity;
			TArray<int32> Path;
			int32 Current = BoneIndex;
			while (Current != INDEX_NONE)
			{
				Path.Insert(Current, 0);
				Current = RefSkeleton.GetParentIndex(Current);
			}
			for (const int32 IndexOnPath : Path)
			{
				const FName LocalName = RefSkeleton.GetBoneName(IndexOnPath);
				FTransform Local = RefSkeleton.GetRefBonePose()[IndexOnPath];
				if (Sequence != nullptr && Sequence->GetSkeleton() == Skeleton)
				{
					FAnimExtractContext ExtractionContext(Time);
					FTransform TrackLocal;
					Sequence->GetBoneTransform(TrackLocal, FSkeletonPoseBoneIndex(IndexOnPath), ExtractionContext, false);
					if (!TrackLocal.GetRotation().ContainsNaN())
					{
						Local = TrackLocal;
					}
				}
				Component = Local * Component;
			}
			return Component;
		}

		bool QuatIsFinite(const FQuat& Q)
		{
			return FMath::IsFinite(Q.W) && FMath::IsFinite(Q.X) && FMath::IsFinite(Q.Y) && FMath::IsFinite(Q.Z);
		}

		bool VectorIsFinite(const FVector& V)
		{
			return FMath::IsFinite(V.X) && FMath::IsFinite(V.Y) && FMath::IsFinite(V.Z);
		}

		bool TransformIsInvalid(const FTransform& Transform)
		{
			return !QuatIsFinite(Transform.GetRotation())
				|| Transform.GetRotation().ContainsNaN()
				|| Transform.GetLocation().ContainsNaN()
				|| !VectorIsFinite(Transform.GetLocation());
		}
	}

	bool ValidateAnimationRetarget(
		UIKRetargeter* Retargeter,
		const TArray<FString>& AnimationPaths,
		TArray<FRetargetValidationIssue>& OutIssues,
		FString& OutVerdict,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		OutIssues.Empty();
		if (Retargeter == nullptr)
		{
			OutErrorCode = TEXT("retarget_asset_not_found");
			OutErrorMessage = TEXT("The IK Retargeter asset is required for validation.");
			return false;
		}

		// Structure validation.
		const UIKRigDefinition* SourceRig = Retargeter->GetIKRig(ERetargetSourceOrTarget::Source);
		const UIKRigDefinition* TargetRig = Retargeter->GetIKRig(ERetargetSourceOrTarget::Target);
		if (SourceRig == nullptr)
		{
			OutIssues.Add({TEXT("error"), TEXT("retarget_structure_missing_source_rig"), TEXT("The IK Retargeter does not reference a source IK Rig."), TEXT("structure"), FString(), FString(), 0.0f});
		}
		if (TargetRig == nullptr)
		{
			OutIssues.Add({TEXT("error"), TEXT("retarget_structure_missing_target_rig"), TEXT("The IK Retargeter does not reference a target IK Rig."), TEXT("structure"), FString(), FString(), 0.0f});
		}
		if (SourceRig == nullptr || TargetRig == nullptr)
		{
			OutVerdict = TEXT("failed");
			return true;
		}
		bool bHasMapping = false;
		TArray<FInstancedStruct>& Ops = const_cast<TArray<FInstancedStruct>&>(Retargeter->GetRetargetOps());
		for (FInstancedStruct& Op : Ops)
		{
			if (FIKRetargetFKChainsOp* FKOp = Op.GetMutablePtr<FIKRetargetFKChainsOp>())
			{
				const FRetargetChainMapping* Mapping = FKOp->GetChainMapping();
				if (Mapping != nullptr && Mapping->GetChainPairs().Num() > 0)
				{
					bHasMapping = true;
				}
				break;
			}
		}
		if (!bHasMapping)
		{
			OutIssues.Add({TEXT("error"), TEXT("retarget_structure_missing_mapping"), TEXT("The IK Retargeter has no chain mappings."), TEXT("structure"), FString(), FString(), 0.0f});
		}

		FMajorBones MajorBones;
		ResolveMajorBones(TargetRig, MajorBones);
		const TArray<FString> DiagnosticBones = {
			MajorBones.Root, MajorBones.Pelvis, MajorBones.Head,
			MajorBones.LeftHand, MajorBones.RightHand,
			MajorBones.LeftFoot, MajorBones.RightFoot,
		};

		USkeleton* TargetSkeleton = nullptr;
		if (USkeletalMesh* TargetMesh = Cast<USkeletalMesh>(TargetRig->GetPreviewMesh()))
		{
			TargetSkeleton = TargetMesh->GetSkeleton();
		}
		if (TargetSkeleton == nullptr)
		{
			OutIssues.Add({TEXT("error"), TEXT("retarget_metadata_missing_target_skeleton"), TEXT("The target IK Rig has no preview skeleton."), TEXT("metadata"), FString(), FString(), 0.0f});
			OutVerdict = TEXT("failed");
			return true;
		}

		// Animation metadata + motion diagnostics.
		for (const FString& AnimationPath : AnimationPaths)
		{
			UAnimSequence* Sequence = Cast<UAnimSequence>(LoadObject<UAnimSequence>(nullptr, *AnimationPath));
			if (Sequence == nullptr)
			{
				OutIssues.Add({TEXT("error"), TEXT("retarget_metadata_sequence_missing"), TEXT("The output animation could not be loaded."), TEXT("metadata"), AnimationPath, FString(), 0.0f});
				continue;
			}
			USkeleton* SequenceSkeleton = Sequence->GetSkeleton();
			if (SequenceSkeleton == nullptr)
			{
				OutIssues.Add({TEXT("error"), TEXT("retarget_metadata_null_skeleton"), TEXT("The output animation has no skeleton reference."), TEXT("metadata"), AnimationPath, FString(), 0.0f});
				continue;
			}
			if (SequenceSkeleton != TargetSkeleton)
			{
				OutIssues.Add({TEXT("error"), TEXT("retarget_metadata_skeleton_mismatch"), TEXT("The output animation skeleton does not match the target IK Rig skeleton."), TEXT("metadata"), AnimationPath, FString(), 0.0f});
			}
			const float PlayLength = Sequence->GetPlayLength();
			if (!FMath::IsFinite(PlayLength) || PlayLength <= 0.0f)
			{
				OutIssues.Add({TEXT("error"), TEXT("retarget_metadata_invalid_play_length"), TEXT("The output animation has no valid play length."), TEXT("metadata"), AnimationPath, FString(), 0.0f});
				continue;
			}

			// Sample major bones at 0/25/50/75/100%.
			const float SampleTimes[5] = {0.0f, 0.25f, 0.5f, 0.75f, 1.0f};
			TMap<FString, FTransform> PreviousComponents;
			for (int32 SampleIndex = 0; SampleIndex < 5; ++SampleIndex)
			{
				const float Time = SampleTimes[SampleIndex] * PlayLength;
				for (const FString& BoneName : DiagnosticBones)
				{
					if (BoneName.IsEmpty())
					{
						continue;
					}
					const FTransform Component = GetComponentTransform(Sequence, TargetSkeleton, FName(*BoneName), Time);
					if (TransformIsInvalid(Component))
					{
						OutIssues.Add({TEXT("error"), TEXT("retarget_motion_invalid_transform"), TEXT("A sampled bone transform is NaN or Inf."), TEXT("motion"), AnimationPath, BoneName, Time});
						continue;
					}
					const FTransform* Previous = PreviousComponents.Find(BoneName);
					if (Previous != nullptr && SampleIndex > 0)
					{
						const float PositionDelta = FVector::Dist(Previous->GetLocation(), Component.GetLocation());
						if (PositionDelta > 200.0f)
						{
							OutIssues.Add({TEXT("error"), TEXT("retarget_motion_extreme_jump"), TEXT("A sampled bone position jumped more than 200 cm between samples."), TEXT("motion"), AnimationPath, BoneName, Time});
						}
						const float RotationDelta = Previous->GetRotation().AngularDistance(Component.GetRotation());
						if (RotationDelta > FMath::DegreesToRadians(120.0f))
						{
							OutIssues.Add({TEXT("error"), TEXT("retarget_motion_extreme_rotation"), TEXT("A sampled bone rotation jumped more than 120 degrees between samples."), TEXT("motion"), AnimationPath, BoneName, Time});
						}
					}
					PreviousComponents.FindOrAdd(BoneName) = Component;
				}
			}
		}

		// Derive the verdict.
		bool bHasError = false;
		bool bHasWarning = false;
		for (const FRetargetValidationIssue& Issue : OutIssues)
		{
			if (Issue.Level == TEXT("error")) { bHasError = true; }
			else if (Issue.Level == TEXT("warning")) { bHasWarning = true; }
		}
		OutVerdict = bHasError ? TEXT("failed") : (bHasWarning ? TEXT("passed_with_warnings") : TEXT("passed"));
		return true;
	}

	TSharedRef<FJsonObject> ValidationIssueToJson(const FRetargetValidationIssue& Issue)
	{
		TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
		Json->SetStringField(TEXT("level"), Issue.Level);
		Json->SetStringField(TEXT("code"), Issue.Code);
		Json->SetStringField(TEXT("message"), Issue.Message);
		Json->SetStringField(TEXT("scope"), Issue.Scope);
		Json->SetStringField(TEXT("assetPath"), Issue.AssetPath);
		Json->SetStringField(TEXT("bone"), Issue.Bone);
		Json->SetNumberField(TEXT("timeSeconds"), Issue.TimeSeconds);
		return Json;
	}
}
