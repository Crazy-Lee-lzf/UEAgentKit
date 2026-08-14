#include "LiveWriteOperationRegistry.h"
#include "LiveWriteOperationCommon.h"
#include "StructuredPropertyJson.h"

#include "Animation/AnimCurveTypes.h"
#include "Animation/AnimData/IAnimationDataController.h"
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
#include "Engine/SkeletalMesh.h"
#include "Engine/World.h"
#include "Misc/MemStack.h"
#include "UObject/Package.h"
#include "UObject/UObjectGlobals.h"

using namespace UEAgentKitLiveWrite;

namespace
{
	constexpr double DefaultFinalScaleToleranceRatio = 0.01;

	TSharedRef<FJsonObject> VectorToJson(const FVector& Value)
	{
		TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
		Json->SetNumberField(TEXT("x"), Value.X);
		Json->SetNumberField(TEXT("y"), Value.Y);
		Json->SetNumberField(TEXT("z"), Value.Z);
		return Json;
	}

	FString RootMotionRootLockToString(const ERootMotionRootLock::Type Value)
	{
		switch (Value)
		{
		case ERootMotionRootLock::RefPose:
			return TEXT("RefPose");
		case ERootMotionRootLock::AnimFirstFrame:
			return TEXT("AnimFirstFrame");
		case ERootMotionRootLock::Zero:
			return TEXT("Zero");
		default:
			return TEXT("Unknown");
		}
	}

	bool TryParseRootMotionRootLock(
		const FString& Value,
		ERootMotionRootLock::Type& OutValue)
	{
		if (Value == TEXT("RefPose"))
		{
			OutValue = ERootMotionRootLock::RefPose;
			return true;
		}
		if (Value == TEXT("AnimFirstFrame"))
		{
			OutValue = ERootMotionRootLock::AnimFirstFrame;
			return true;
		}
		if (Value == TEXT("Zero"))
		{
			OutValue = ERootMotionRootLock::Zero;
			return true;
		}
		return false;
	}

	bool IsFinitePositiveScale(const FVector& Value)
	{
		return Value.X > 0.0 && Value.Y > 0.0 && Value.Z > 0.0
			&& FMath::IsFinite(Value.X)
			&& FMath::IsFinite(Value.Y)
			&& FMath::IsFinite(Value.Z);
	}

	bool TryReadUniformScale(
		const TSharedPtr<FJsonObject>& Value,
		FVector& OutScale,
		FString& OutErrorMessage)
	{
		double UniformScale = 0.0;
		if (!Value->TryGetNumberField(TEXT("uniformScale"), UniformScale)
			|| !FMath::IsFinite(UniformScale)
			|| UniformScale <= 0.0
			|| UniformScale > 1000000.0)
		{
			OutErrorMessage = TEXT("uniformScale must be one finite number greater than 0 and at most 1000000.");
			return false;
		}
		OutScale = FVector(UniformScale);
		return true;
	}

	bool TryEvaluateFinalRootScale(
		UAnimSequence* Sequence,
		const FName RootBone,
		FVector& OutScale,
		FString& OutStatus)
	{
		OutScale = FVector::ZeroVector;
		OutStatus = TEXT("unavailable");
		if (Sequence == nullptr || Sequence->GetSkeleton() == nullptr)
		{
			return false;
		}
		if (Sequence->IsValidAdditive())
		{
			OutStatus = TEXT("unsupported-additive-requires-base-pose");
			return false;
		}

		USkeletalMesh* PreviewMesh = Sequence->GetPreviewMesh(true);
		if (PreviewMesh == nullptr)
		{
			PreviewMesh = Sequence->GetSkeleton()->GetPreviewMesh(true);
		}
		if (PreviewMesh == nullptr)
		{
			OutStatus = TEXT("preview-mesh-unavailable");
			return false;
		}

		UWorld* EditorWorld = GEditor != nullptr ? GEditor->GetEditorWorldContext().World() : nullptr;
		if (EditorWorld == nullptr)
		{
			OutStatus = TEXT("editor-world-unavailable");
			return false;
		}

		USkeletalMeshComponent* Component = NewObject<USkeletalMeshComponent>(GetTransientPackage(), NAME_None, RF_Transient);
		if (Component == nullptr)
		{
			OutStatus = TEXT("component-create-failed");
			return false;
		}

		Component->SetVisibility(false, true);
		Component->SetHiddenInGame(true);
		Component->SetSkeletalMesh(PreviewMesh);
		Component->RegisterComponentWithWorld(EditorWorld);
		if (!Component->IsRegistered())
		{
			Component->MarkAsGarbage();
			OutStatus = TEXT("component-registration-failed");
			return false;
		}

		Component->SetUpdateAnimationInEditor(true);
		Component->VisibilityBasedAnimTickOption = EVisibilityBasedAnimTickOption::AlwaysTickPoseAndRefreshBones;
		Component->SetAnimationMode(EAnimationMode::AnimationSingleNode, true);
		Component->SetAnimation(Sequence);
		Component->SetPosition(0.0, false);
		Component->TickAnimation(0.0f, false);
		Component->RefreshBoneTransforms();
		Component->CompleteParallelAnimationEvaluation(true);
		Component->UpdateComponentToWorld();

		const int32 BoneIndex = Component->GetBoneIndex(RootBone);
		if (BoneIndex != INDEX_NONE)
		{
			OutScale = Component->GetBoneTransform(BoneIndex, FTransform::Identity).GetScale3D();
			OutStatus = TEXT("success");
		}
		else
		{
			OutStatus = TEXT("root-bone-not-found");
		}

		Component->SetAnimation(nullptr);
		Component->SetUpdateAnimationInEditor(false);
		Component->UnregisterComponent();
		Component->MarkAsGarbage();
		return OutStatus == TEXT("success");
	}

	// Forward declaration: defined after the additive Base Pose fix IO below.
	bool EvaluateAdditiveCombinedScale(
		UAnimSequence* Sequence,
		const FName BoneName,
		const double Fraction,
		FVector& OutBaseScale,
		FVector& OutDeltaLocalScale,
		FVector& OutCombinedScale,
		FString& OutStatus);

	struct FAnimationScaleFixSnapshot
	{
		bool bForceRootLock = false;
		bool bEnableRootMotion = false;
		bool bUseNormalizedRootMotionScale = false;
		ERootMotionRootLock::Type RootMotionRootLock = ERootMotionRootLock::RefPose;
		bool bRootTrackExists = false;
		TArray<FTransform> RootTrackTransforms;
	};

	class FLiveWriteAnimationScaleFixIO final : public ILiveWriteValueIO
	{
	public:
		FLiveWriteAnimationScaleFixIO(UAnimSequence* InSequence, const FName InRootBone)
			: Sequence(InSequence)
			, RootBone(InRootBone)
		{
		}

		bool CaptureSnapshot() override
		{
			if (Sequence == nullptr || Sequence->GetSkeleton() == nullptr || RootBone.IsNone())
			{
				return false;
			}
			const FReferenceSkeleton& ReferenceSkeleton = Sequence->GetSkeleton()->GetReferenceSkeleton();
			if (ReferenceSkeleton.FindBoneIndex(RootBone) == INDEX_NONE)
			{
				return false;
			}

			Snapshot.bForceRootLock = Sequence->bForceRootLock;
			Snapshot.bEnableRootMotion = Sequence->bEnableRootMotion;
			Snapshot.bUseNormalizedRootMotionScale = Sequence->bUseNormalizedRootMotionScale;
			Snapshot.RootMotionRootLock = Sequence->RootMotionRootLock;

			const IAnimationDataModel* Model = Sequence->GetDataModel();
			Snapshot.bRootTrackExists = Model != nullptr && Model->IsValidBoneTrackName(RootBone);
			Snapshot.RootTrackTransforms.Reset();
			if (Snapshot.bRootTrackExists)
			{
				Model->GetBoneTrackTransforms(RootBone, Snapshot.RootTrackTransforms);
			}
			bSnapshotValid = true;
			return true;
		}

		bool IsSnapshotValid() const override
		{
			return bSnapshotValid;
		}

		void RestoreSnapshot() override
		{
			if (!bSnapshotValid || Sequence == nullptr)
			{
				return;
			}
			Sequence->bForceRootLock = Snapshot.bForceRootLock;
			Sequence->bEnableRootMotion = Snapshot.bEnableRootMotion;
			Sequence->bUseNormalizedRootMotionScale = Snapshot.bUseNormalizedRootMotionScale;
			Sequence->RootMotionRootLock = Snapshot.RootMotionRootLock;
			if (Snapshot.bRootTrackExists && !Snapshot.RootTrackTransforms.IsEmpty())
			{
				WriteRootTrackTransforms(Snapshot.RootTrackTransforms);
			}
		}

		void ReleaseSnapshot() override
		{
			Snapshot.RootTrackTransforms.Reset();
			bSnapshotValid = false;
		}

		bool ReadBefore(
			TSharedPtr<FJsonValue>& OutValue,
			FString& OutErrorCode,
			FString& OutErrorMessage) override
		{
			return ReadState(OutValue, OutErrorCode, OutErrorMessage);
		}

		bool ApplyValue(
			const TSharedPtr<FJsonValue>& Value,
			FString& OutErrorCode,
			FString& OutErrorMessage) override
		{
			const TSharedPtr<FJsonObject> Requested = Value.IsValid() ? Value->AsObject() : nullptr;
			if (!Requested.IsValid() || Requested->Values.Num() > 8)
			{
				OutErrorCode = TEXT("live-editor-write-value-invalid");
				OutErrorMessage = TEXT("setAnimationScaleFix requires one JSON object with at most 8 fields.");
				return false;
			}

			static const TSet<FString> AllowedFields = {
				TEXT("forceRootLock"),
				TEXT("enableRootMotion"),
				TEXT("useNormalizedRootMotionScale"),
				TEXT("rootMotionRootLock"),
				TEXT("rootTrackScaleMode"),
				TEXT("uniformScale"),
				TEXT("expectedFinalScale"),
				TEXT("finalScaleTolerance")};
			for (const TPair<FString, TSharedPtr<FJsonValue>>& Pair : Requested->Values)
			{
				if (!AllowedFields.Contains(Pair.Key))
				{
					OutErrorCode = TEXT("live-editor-write-value-invalid");
					OutErrorMessage = FString::Printf(TEXT("Unsupported setAnimationScaleFix value field: %s."), *Pair.Key);
					return false;
				}
			}

			FString ScaleMode = TEXT("Keep");
			Requested->TryGetStringField(TEXT("rootTrackScaleMode"), ScaleMode);
			if (ScaleMode != TEXT("Keep") && ScaleMode != TEXT("ReferenceLocal") && ScaleMode != TEXT("Uniform"))
			{
				OutErrorCode = TEXT("live-editor-write-value-invalid");
				OutErrorMessage = TEXT("rootTrackScaleMode must be Keep, ReferenceLocal, or Uniform.");
				return false;
			}
			bool bAnyRequestedChange = ScaleMode != TEXT("Keep");
			bool BoolValue = false;
			if (Requested->TryGetBoolField(TEXT("forceRootLock"), BoolValue))
			{
				Sequence->bForceRootLock = BoolValue;
				bAnyRequestedChange = true;
			}
			if (Requested->TryGetBoolField(TEXT("enableRootMotion"), BoolValue))
			{
				Sequence->bEnableRootMotion = BoolValue;
				bAnyRequestedChange = true;
			}
			if (Requested->TryGetBoolField(TEXT("useNormalizedRootMotionScale"), BoolValue))
			{
				Sequence->bUseNormalizedRootMotionScale = BoolValue;
				bAnyRequestedChange = true;
			}

			FString RootLockValue;
			if (Requested->TryGetStringField(TEXT("rootMotionRootLock"), RootLockValue))
			{
				ERootMotionRootLock::Type ParsedRootLock = ERootMotionRootLock::RefPose;
				if (!TryParseRootMotionRootLock(RootLockValue, ParsedRootLock))
				{
					OutErrorCode = TEXT("live-editor-write-value-invalid");
					OutErrorMessage = TEXT("rootMotionRootLock must be RefPose, AnimFirstFrame, or Zero.");
					return false;
				}
				Sequence->RootMotionRootLock = ParsedRootLock;
				bAnyRequestedChange = true;
			}

			if (!bAnyRequestedChange)
			{
				OutErrorCode = TEXT("live-editor-write-value-invalid");
				OutErrorMessage = TEXT("setAnimationScaleFix requires at least one sequence setting or a non-Keep rootTrackScaleMode.");
				return false;
			}

			if (ScaleMode != TEXT("Keep"))
			{
				const IAnimationDataModel* Model = Sequence->GetDataModel();
				if (Model == nullptr || !Model->IsValidBoneTrackName(RootBone))
				{
					OutErrorCode = TEXT("retarget_root_track_not_found");
					OutErrorMessage = TEXT("The requested root bone has no animation track to repair.");
					return false;
				}
				TArray<FTransform> Transforms;
				Model->GetBoneTrackTransforms(RootBone, Transforms);
				if (Transforms.IsEmpty())
				{
					OutErrorCode = TEXT("retarget_root_track_not_found");
					OutErrorMessage = TEXT("The requested root bone track has no keys.");
					return false;
				}

				FVector TargetScale = FVector::OneVector;
				if (ScaleMode == TEXT("ReferenceLocal"))
				{
					const FReferenceSkeleton& ReferenceSkeleton = Sequence->GetSkeleton()->GetReferenceSkeleton();
					const int32 RootBoneIndex = ReferenceSkeleton.FindBoneIndex(RootBone);
					if (RootBoneIndex == INDEX_NONE)
					{
						OutErrorCode = TEXT("retarget_root_bone_not_found");
						OutErrorMessage = TEXT("The requested root bone does not exist in the Skeleton Reference Pose.");
						return false;
					}
					TargetScale = ReferenceSkeleton.GetRefBonePose()[RootBoneIndex].GetScale3D();
				}
				else if (!TryReadUniformScale(Requested, TargetScale, OutErrorMessage))
				{
					OutErrorCode = TEXT("live-editor-write-value-invalid");
					return false;
				}

				if (!IsFinitePositiveScale(TargetScale))
				{
					OutErrorCode = TEXT("retarget_target_scale_invalid");
					OutErrorMessage = TEXT("The requested target Root Scale is not finite and positive.");
					return false;
				}
				for (FTransform& Transform : Transforms)
				{
					Transform.SetScale3D(TargetScale);
				}
				if (!WriteRootTrackTransforms(Transforms))
				{
					OutErrorCode = TEXT("retarget_root_track_write_failed");
					OutErrorMessage = TEXT("The Editor rejected the Root Scale Track update.");
					return false;
				}
			}
			return true;
		}

		bool ReadAfter(
			const TSharedPtr<FJsonValue>& Requested,
			TSharedPtr<FJsonValue>& OutValue,
			FString& OutErrorCode,
			FString& OutErrorMessage) override
		{
			if (!ReadState(OutValue, OutErrorCode, OutErrorMessage))
			{
				return false;
			}
			const TSharedPtr<FJsonObject> RequestedObject = Requested.IsValid() ? Requested->AsObject() : nullptr;
			if (!RequestedObject.IsValid())
			{
				return true;
			}

			double ExpectedFinalScale = 0.0;
			if (!RequestedObject->TryGetNumberField(TEXT("expectedFinalScale"), ExpectedFinalScale))
			{
				return true;
			}
			if (!FMath::IsFinite(ExpectedFinalScale) || ExpectedFinalScale <= 0.0)
			{
				OutErrorCode = TEXT("live-editor-write-value-invalid");
				OutErrorMessage = TEXT("expectedFinalScale must be one finite number greater than 0.");
				return false;
			}
			FVector EvaluatedScale;
			FString EvaluationStatus;
			bool bScaleAvailable = false;
			if (Sequence->IsValidAdditive())
			{
				FVector BaseScale;
				FVector DeltaLocalScale;
				bScaleAvailable = EvaluateAdditiveCombinedScale(
					Sequence,
					RootBone,
					0.0,
					BaseScale,
					DeltaLocalScale,
					EvaluatedScale,
					EvaluationStatus);
			}
			else
			{
				bScaleAvailable = TryEvaluateFinalRootScale(Sequence, RootBone, EvaluatedScale, EvaluationStatus);
			}
			if (!bScaleAvailable)
			{
				OutErrorCode = TEXT("retarget_final_scale_evaluation_failed");
				OutErrorMessage = FString::Printf(TEXT("Final Root Scale evaluation failed: %s."), *EvaluationStatus);
				return false;
			}
			double Tolerance = FMath::Max(0.01, FMath::Abs(ExpectedFinalScale) * DefaultFinalScaleToleranceRatio);
			RequestedObject->TryGetNumberField(TEXT("finalScaleTolerance"), Tolerance);
			if (!FMath::IsFinite(Tolerance) || Tolerance < 0.0 || Tolerance > 1000000.0)
			{
				OutErrorCode = TEXT("live-editor-write-value-invalid");
				OutErrorMessage = TEXT("finalScaleTolerance must be one finite non-negative number at most 1000000.");
				return false;
			}
			if (!FMath::IsNearlyEqual(EvaluatedScale.X, ExpectedFinalScale, Tolerance)
				|| !FMath::IsNearlyEqual(EvaluatedScale.Y, ExpectedFinalScale, Tolerance)
				|| !FMath::IsNearlyEqual(EvaluatedScale.Z, ExpectedFinalScale, Tolerance))
			{
				OutErrorCode = TEXT("retarget_final_scale_mismatch");
				OutErrorMessage = FString::Printf(
					TEXT("Final Root Scale is (%g, %g, %g), expected %g +/- %g. The live write was rolled back."),
					EvaluatedScale.X,
					EvaluatedScale.Y,
					EvaluatedScale.Z,
					ExpectedFinalScale,
					Tolerance);
				return false;
			}
			return true;
		}

		bool SemanticEqual(
			const TSharedPtr<FJsonValue>& Left,
			const TSharedPtr<FJsonValue>& Right) override
		{
			return UEAgentKit::StructuredPropertyJson::JsonEqual(Left, Right);
		}

		void NotifyChanged() override
		{
			NotifySequenceChanged();
		}

		void NotifyRestored() override
		{
			NotifySequenceChanged();
		}

	private:
		bool WriteRootTrackTransforms(const TArray<FTransform>& Transforms)
		{
			if (Sequence == nullptr || Transforms.IsEmpty())
			{
				return false;
			}
			TArray<FVector> Positions;
			TArray<FQuat> Rotations;
			TArray<FVector> Scales;
			Positions.Reserve(Transforms.Num());
			Rotations.Reserve(Transforms.Num());
			Scales.Reserve(Transforms.Num());
			for (const FTransform& Transform : Transforms)
			{
				Positions.Add(Transform.GetLocation());
				Rotations.Add(Transform.GetRotation());
				Scales.Add(Transform.GetScale3D());
			}
			IAnimationDataController& Controller = Sequence->GetController();
			return Controller.SetBoneTrackKeys(RootBone, Positions, Rotations, Scales, true);
		}

		bool ReadState(
			TSharedPtr<FJsonValue>& OutValue,
			FString& OutErrorCode,
			FString& OutErrorMessage) const
		{
			if (Sequence == nullptr || Sequence->GetSkeleton() == nullptr)
			{
				OutErrorCode = TEXT("live-editor-write-apply-failed");
				OutErrorMessage = TEXT("The Animation Sequence or Skeleton became unavailable.");
				return false;
			}

			TSharedRef<FJsonObject> State = MakeShared<FJsonObject>();
			State->SetStringField(TEXT("rootBone"), RootBone.ToString());
			State->SetBoolField(TEXT("forceRootLock"), Sequence->bForceRootLock);
			State->SetBoolField(TEXT("enableRootMotion"), Sequence->bEnableRootMotion);
			State->SetBoolField(TEXT("useNormalizedRootMotionScale"), Sequence->bUseNormalizedRootMotionScale);
			State->SetStringField(TEXT("rootMotionRootLock"), RootMotionRootLockToString(Sequence->RootMotionRootLock));
			State->SetBoolField(TEXT("additive"), Sequence->IsValidAdditive());

			const FReferenceSkeleton& ReferenceSkeleton = Sequence->GetSkeleton()->GetReferenceSkeleton();
			const int32 RootBoneIndex = ReferenceSkeleton.FindBoneIndex(RootBone);
			if (RootBoneIndex != INDEX_NONE)
			{
				State->SetField(
					TEXT("referenceLocalScale"),
					MakeShared<FJsonValueObject>(VectorToJson(ReferenceSkeleton.GetRefBonePose()[RootBoneIndex].GetScale3D())));
			}

			const IAnimationDataModel* Model = Sequence->GetDataModel();
			const bool bTrackExists = Model != nullptr && Model->IsValidBoneTrackName(RootBone);
			State->SetBoolField(TEXT("rootTrackExists"), bTrackExists);
			TArray<FTransform> Transforms;
			if (bTrackExists)
			{
				Model->GetBoneTrackTransforms(RootBone, Transforms);
			}
			State->SetNumberField(TEXT("rootTrackKeyCount"), Transforms.Num());
			if (!Transforms.IsEmpty())
			{
				State->SetField(TEXT("rootTrackFirstScale"), MakeShared<FJsonValueObject>(VectorToJson(Transforms[0].GetScale3D())));
				State->SetField(TEXT("rootTrackMiddleScale"), MakeShared<FJsonValueObject>(VectorToJson(Transforms[Transforms.Num() / 2].GetScale3D())));
				State->SetField(TEXT("rootTrackLastScale"), MakeShared<FJsonValueObject>(VectorToJson(Transforms.Last().GetScale3D())));
			}

			FVector FinalScale;
			FString FinalStatus;
			bool bFinalScaleAvailable = false;
			if (Sequence->IsValidAdditive())
			{
				FVector BaseScale;
				FVector DeltaLocalScale;
				bFinalScaleAvailable = EvaluateAdditiveCombinedScale(
					Sequence,
					RootBone,
					0.0,
					BaseScale,
					DeltaLocalScale,
					FinalScale,
					FinalStatus);
			}
			else
			{
				bFinalScaleAvailable = TryEvaluateFinalRootScale(Sequence, RootBone, FinalScale, FinalStatus);
			}
			State->SetStringField(TEXT("finalEvaluationStatus"), FinalStatus);
			if (bFinalScaleAvailable)
			{
				State->SetField(TEXT("finalRootScale"), MakeShared<FJsonValueObject>(VectorToJson(FinalScale)));
			}
			OutValue = MakeShared<FJsonValueObject>(State);
			return true;
		}

		void NotifySequenceChanged()
		{
			if (Sequence != nullptr)
			{
				Sequence->PostEditChange();
			}
		}

		UAnimSequence* Sequence = nullptr;
		FName RootBone;
		FAnimationScaleFixSnapshot Snapshot;
		bool bSnapshotValid = false;
	};

	bool ApplyAnimationScaleFixOperation(
		const FLiveWriteOperationContext& Context,
		const FLiveWriteOperationRequest& Request,
		TSharedPtr<FLiveWriteTransactionRecord>& OutRecord,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		UAnimSequence* Sequence = Cast<UAnimSequence>(Context.Asset);
		if (Sequence == nullptr)
		{
			OutErrorCode = TEXT("retarget_asset_type_invalid");
			OutErrorMessage = TEXT("setAnimationScaleFix accepts only UAnimSequence assets.");
			return false;
		}
		const FName RootBone(*Request.Target->GetStringField(TEXT("rootBone")));
		if (RootBone.IsNone())
		{
			OutErrorCode = TEXT("live-editor-invalid-parameters");
			OutErrorMessage = TEXT("target.rootBone must be one exact non-empty bone name.");
			return false;
		USkeleton* Skeleton = Sequence->GetSkeleton();
		if (Skeleton == nullptr)
		{
			OutErrorCode = TEXT("retarget_skeleton_not_found");
			OutErrorMessage = TEXT("The Animation Sequence has no Skeleton.");
			return false;
		}
		const FReferenceSkeleton& ReferenceSkeleton = Skeleton->GetReferenceSkeleton();
		const int32 RootBoneIndex = ReferenceSkeleton.FindBoneIndex(RootBone);
		if (RootBoneIndex == INDEX_NONE)
		{
			OutErrorCode = TEXT("retarget_root_bone_not_found");
			OutErrorMessage = TEXT("target.rootBone does not exist in the Animation Sequence Skeleton.");
			return false;
		}
		if (ReferenceSkeleton.GetParentIndex(RootBoneIndex) != INDEX_NONE)
		{
			OutErrorCode = TEXT("retarget_root_bone_not_root");
			OutErrorMessage = TEXT("target.rootBone must identify the actual Skeleton root bone.");
			return false;
		}

		}

		TUniquePtr<ILiveWriteValueIO> IO = MakeUnique<FLiveWriteAnimationScaleFixIO>(Sequence, RootBone);
		FLiveWriteContext LiveContext;
		LiveContext.Asset = Sequence;
		LiveContext.Package = Context.Package;
		LiveContext.SessionId = Request.SessionId;
		LiveContext.TransactionTitle = TEXT("UE Agent Kit: Fix Animation Root Scale");
		LiveContext.AssetPath = Request.AssetPath;
		LiveContext.PropertyPath = RootBone.ToString();
		LiveContext.Value = Request.Value;

		FLiveWriteEvidence Evidence;
		if (!RunLiveWriteTransaction(LiveContext, IO, Evidence, OutErrorCode, OutErrorMessage))
		{
			return false;
		}
		OutRecord = BuildLiveWriteTransactionRecord(
			Sequence,
			Context.Package,
			Request.AssetPath,
			TEXT("setAnimationScaleFix"),
			TEXT("animation-scale-fix"),
			Request.SessionId,
			Evidence,
			IO);

		TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
		FillLiveWriteEvidence(
			Result,
			LiveContext,
			Evidence,
			TEXT("setAnimationScaleFix"),
			TEXT("animation-scale-fix"),
			TEXT("AnimSequenceRootScale"),
			true,
			true);
		Result->SetStringField(TEXT("rootBone"), RootBone.ToString());
		Result->SetBoolField(TEXT("finalScaleVerifiedDuringApply"),
			Request.Value.IsValid()
			&& Request.Value->AsObject().IsValid()
			&& Request.Value->AsObject()->HasField(TEXT("expectedFinalScale")));
		OutResult = Result;
		return true;
	}

	// ---- Additive Base Pose Fix ----

	bool TryParseAdditiveAnimationType(const FString& Value, EAdditiveAnimationType& OutType)
	{
		if (Value == TEXT("None"))
		{
			OutType = AAT_None;
			return true;
		}
		if (Value == TEXT("LocalSpaceBase"))
		{
			OutType = AAT_LocalSpaceBase;
			return true;
		}
		if (Value == TEXT("RotationOffsetMeshSpace"))
		{
			OutType = AAT_RotationOffsetMeshSpace;
			return true;
		}
		return false;
	}

	bool TryParseAdditiveBasePoseType(const FString& Value, EAdditiveBasePoseType& OutType)
	{
		if (Value == TEXT("None"))
		{
			OutType = ABPT_None;
			return true;
		}
		if (Value == TEXT("ReferencePose"))
		{
			OutType = ABPT_RefPose;
			return true;
		}
		if (Value == TEXT("AnimationScaled"))
		{
			OutType = ABPT_AnimScaled;
			return true;
		}
		if (Value == TEXT("AnimationFrame"))
		{
			OutType = ABPT_AnimFrame;
			return true;
		}
		if (Value == TEXT("LocalAnimationFrame"))
		{
			OutType = ABPT_LocalAnimFrame;
			return true;
		}
		return false;
	}

	const TCHAR* AdditiveFixAnimTypeToString(const EAdditiveAnimationType Value)
	{
		switch (Value)
		{
		case AAT_None: return TEXT("None");
		case AAT_LocalSpaceBase: return TEXT("LocalSpaceBase");
		case AAT_RotationOffsetMeshSpace: return TEXT("RotationOffsetMeshSpace");
		default: return TEXT("Unknown");
		}
	}

	const TCHAR* AdditiveFixBasePoseTypeToString(const EAdditiveBasePoseType Value)
	{
		switch (Value)
		{
		case ABPT_None: return TEXT("None");
		case ABPT_RefPose: return TEXT("ReferencePose");
		case ABPT_AnimScaled: return TEXT("AnimationScaled");
		case ABPT_AnimFrame: return TEXT("AnimationFrame");
		case ABPT_LocalAnimFrame: return TEXT("LocalAnimationFrame");
		default: return TEXT("Unknown");
		}
	}

	// Evaluates the combined Component Space Scale of one bone for an additive sequence by
	// accumulating the engine-resolved Base Pose with the additive delta. Read-only and used
	// to verify the final Root/Pelvis Scale after a Base Pose fix.
	bool EvaluateAdditiveCombinedScale(
		UAnimSequence* Sequence,
		const FName BoneName,
		const double Fraction,
		FVector& OutBaseScale,
		FVector& OutDeltaLocalScale,
		FVector& OutCombinedScale,
		FString& OutStatus)
	{
		OutBaseScale = FVector::ZeroVector;
		OutDeltaLocalScale = FVector::ZeroVector;
		OutCombinedScale = FVector::ZeroVector;
		OutStatus = TEXT("unavailable");

		USkeleton* Skeleton = Sequence != nullptr ? Sequence->GetSkeleton() : nullptr;
		if (Sequence == nullptr || Skeleton == nullptr)
		{
			return false;
		}

		const FReferenceSkeleton& ReferenceSkeleton = Skeleton->GetReferenceSkeleton();
		const int32 NumBones = ReferenceSkeleton.GetNum();
		if (NumBones <= 0)
		{
			OutStatus = TEXT("skeleton-empty");
			return false;
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
			return false;
		}

		const EAdditiveAnimationType AdditiveType =
			static_cast<EAdditiveAnimationType>(Sequence->AdditiveAnimType.GetValue());

		FMemMark Mark(FMemStack::Get());
		const double Time = Sequence->GetPlayLength() * Fraction;
		const FAnimExtractContext ExtractionContext(Time);

		FCompactPose BasePose;
		BasePose.SetBoneContainer(&RequiredBones);
		BasePose.ResetToRefPose();
		FBlendedCurve BaseCurve;
		BaseCurve.InitFrom(RequiredBones);
		UE::Anim::FStackAttributeContainer BaseAttributes;
		FAnimationPoseData BasePoseData(BasePose, BaseCurve, BaseAttributes);
		Sequence->GetAdditiveBasePose(BasePoseData, ExtractionContext);

		FCompactPose AdditivePose;
		AdditivePose.SetBoneContainer(&RequiredBones);
		AdditivePose.ResetToAdditiveIdentity();
		FBlendedCurve AdditiveCurve;
		AdditiveCurve.InitFrom(RequiredBones);
		UE::Anim::FStackAttributeContainer AdditiveAttributes;
		FAnimationPoseData AdditivePoseData(AdditivePose, AdditiveCurve, AdditiveAttributes);
		Sequence->GetBonePose_Additive(AdditivePoseData, ExtractionContext);

		FCSPose<FCompactPose> BaseComponentPose;
		BaseComponentPose.InitPose(BasePoseData.GetPose());
		FAnimationRuntime::AccumulateAdditivePose(BasePoseData, AdditivePoseData, 1.0f, AdditiveType);
		FCSPose<FCompactPose> CombinedComponentPose;
		CombinedComponentPose.InitPose(BasePoseData.GetPose());

		const int32 SkeletonBoneIndex = ReferenceSkeleton.FindBoneIndex(BoneName);
		if (SkeletonBoneIndex == INDEX_NONE)
		{
			OutStatus = TEXT("bone-not-found");
			return false;
		}
		const FCompactPoseBoneIndex CompactBoneIndex =
			RequiredBones.MakeCompactPoseIndex(FMeshPoseBoneIndex(SkeletonBoneIndex));
		if (!CompactBoneIndex.IsValid())
		{
			OutStatus = TEXT("bone-not-found");
			return false;
		}

		const FTransform BaseTransform = BaseComponentPose.GetComponentSpaceTransform(CompactBoneIndex);
		const FTransform CombinedTransform = CombinedComponentPose.GetComponentSpaceTransform(CompactBoneIndex);
		const FTransform AdditiveLocalTransform = AdditivePose[CompactBoneIndex];

		OutBaseScale = BaseTransform.GetScale3D();
		OutDeltaLocalScale = AdditiveLocalTransform.GetScale3D();
		OutCombinedScale = CombinedTransform.GetScale3D();
		OutStatus = TEXT("success");
		return true;
	}

	struct FAdditiveBasePoseFixSnapshot
	{
		TObjectPtr<UAnimSequence> RefPoseSeq;
		int32 RefFrameIndex = 0;
		TEnumAsByte<EAdditiveAnimationType> AdditiveAnimType = AAT_None;
		TEnumAsByte<EAdditiveBasePoseType> RefPoseType = ABPT_None;
	};

	class FLiveWriteAdditiveBasePoseFixIO final : public ILiveWriteValueIO
	{
	public:
		explicit FLiveWriteAdditiveBasePoseFixIO(UAnimSequence* InSequence)
			: Sequence(InSequence)
		{
		}

		bool CaptureSnapshot() override
		{
			if (Sequence == nullptr)
			{
				return false;
			}
			Snapshot.RefPoseSeq = Sequence->RefPoseSeq;
			Snapshot.RefFrameIndex = Sequence->RefFrameIndex;
			Snapshot.AdditiveAnimType = Sequence->AdditiveAnimType;
			Snapshot.RefPoseType = Sequence->RefPoseType;
			bSnapshotValid = true;
			return true;
		}

		bool IsSnapshotValid() const override
		{
			return bSnapshotValid;
		}

		void RestoreSnapshot() override
		{
			if (!bSnapshotValid || Sequence == nullptr)
			{
				return;
			}
			Sequence->RefPoseSeq = Snapshot.RefPoseSeq;
			Sequence->RefFrameIndex = Snapshot.RefFrameIndex;
			Sequence->AdditiveAnimType = Snapshot.AdditiveAnimType;
			Sequence->RefPoseType = Snapshot.RefPoseType;
		}

		void ReleaseSnapshot() override
		{
			Snapshot.RefPoseSeq = nullptr;
			bSnapshotValid = false;
		}

		bool ReadBefore(
			TSharedPtr<FJsonValue>& OutValue,
			FString& OutErrorCode,
			FString& OutErrorMessage) override
		{
			return ReadState(OutValue, OutErrorCode, OutErrorMessage);
		}

		bool ApplyValue(
			const TSharedPtr<FJsonValue>& Value,
			FString& OutErrorCode,
			FString& OutErrorMessage) override
		{
			if (Sequence == nullptr)
			{
				OutErrorCode = TEXT("live-editor-write-apply-failed");
				OutErrorMessage = TEXT("The Animation Sequence became unavailable.");
				return false;
			}
			const TSharedPtr<FJsonObject> Requested = Value.IsValid() ? Value->AsObject() : nullptr;
			if (!Requested.IsValid() || Requested->Values.Num() < 2 || Requested->Values.Num() > 7)
			{
				OutErrorCode = TEXT("live-editor-write-value-invalid");
				OutErrorMessage = TEXT("setAdditiveBasePoseFix requires one JSON object with 2 to 7 fields.");
				return false;
			}

			static const TSet<FString> AllowedFields = {
				TEXT("refSequencePath"),
				TEXT("refFrameIndex"),
				TEXT("additiveAnimType"),
				TEXT("additiveBasePoseType"),
				TEXT("expectedCombinedRootScale"),
				TEXT("combinedScaleTolerance"),
				TEXT("rootBone")};
			for (const TPair<FString, TSharedPtr<FJsonValue>>& Pair : Requested->Values)
			{
				if (!AllowedFields.Contains(Pair.Key))
				{
					OutErrorCode = TEXT("live-editor-write-value-invalid");
					OutErrorMessage = FString::Printf(TEXT("Unsupported setAdditiveBasePoseFix value field: %s."), *Pair.Key);
					return false;
				}
			}

			if (Sequence->GetAdditiveAnimType() == AAT_None)
			{
				OutErrorCode = TEXT("retarget_additive_requires_additive");
				OutErrorMessage = TEXT("setAdditiveBasePoseFix accepts only additive AnimSequence assets.");
				return false;
			}

			FString RefSequencePath;
			if (!Requested->TryGetStringField(TEXT("refSequencePath"), RefSequencePath) || RefSequencePath.IsEmpty())
			{
				OutErrorCode = TEXT("live-editor-write-value-invalid");
				OutErrorMessage = TEXT("refSequencePath must be one exact /Game base pose Object Path.");
				return false;
			}

			double RefFrameIndexNumber = 0.0;
			if (!Requested->TryGetNumberField(TEXT("refFrameIndex"), RefFrameIndexNumber)
				|| RefFrameIndexNumber < 0.0
				|| RefFrameIndexNumber > static_cast<double>(INT32_MAX)
				|| static_cast<double>(static_cast<int64>(RefFrameIndexNumber)) != RefFrameIndexNumber)
			{
				OutErrorCode = TEXT("live-editor-write-value-invalid");
				OutErrorMessage = TEXT("refFrameIndex must be one non-negative integer.");
				return false;
			}
			const int32 RefFrameIndex = static_cast<int32>(RefFrameIndexNumber);

			EAdditiveAnimationType AdditiveAnimType =
				static_cast<EAdditiveAnimationType>(Sequence->AdditiveAnimType.GetValue());
			FString AnimTypeValue;
			if (Requested->TryGetStringField(TEXT("additiveAnimType"), AnimTypeValue)
				&& !TryParseAdditiveAnimationType(AnimTypeValue, AdditiveAnimType))
			{
				OutErrorCode = TEXT("live-editor-write-value-invalid");
				OutErrorMessage = TEXT("additiveAnimType must be None, LocalSpaceBase, or RotationOffsetMeshSpace.");
				return false;
			}

			EAdditiveBasePoseType BasePoseType =
				static_cast<EAdditiveBasePoseType>(Sequence->RefPoseType.GetValue());
			FString BasePoseTypeValue;
			if (Requested->TryGetStringField(TEXT("additiveBasePoseType"), BasePoseTypeValue)
				&& !TryParseAdditiveBasePoseType(BasePoseTypeValue, BasePoseType))
			{
				OutErrorCode = TEXT("live-editor-write-value-invalid");
				OutErrorMessage = TEXT("additiveBasePoseType must be None, ReferencePose, AnimationScaled, AnimationFrame, or LocalAnimationFrame.");
				return false;
			}

			const bool bNeedsSequence = BasePoseType == ABPT_AnimScaled || BasePoseType == ABPT_AnimFrame;
			UAnimSequence* RefPoseSeq = nullptr;
			if (bNeedsSequence)
			{
				if (!RefSequencePath.StartsWith(TEXT("/Game/")))
				{
					OutErrorCode = TEXT("live-editor-write-value-invalid");
					OutErrorMessage = TEXT("refSequencePath must be one exact /Game Object Path.");
					return false;
				}
				RefPoseSeq = LoadObject<UAnimSequence>(nullptr, *RefSequencePath);
				if (RefPoseSeq == nullptr)
				{
					OutErrorCode = TEXT("retarget_base_pose_not_found");
					OutErrorMessage = TEXT("The referenced Base Pose animation could not be loaded.");
					return false;
				}
				if (RefPoseSeq == Sequence)
				{
					OutErrorCode = TEXT("retarget_base_pose_self_reference");
					OutErrorMessage = TEXT("The Base Pose animation must not reference the additive sequence itself.");
					return false;
				}
				if (RefPoseSeq->GetSkeleton() != Sequence->GetSkeleton())
				{
					OutErrorCode = TEXT("retarget_base_pose_skeleton_mismatch");
					OutErrorMessage = TEXT("The Base Pose animation must use the same Skeleton as the additive sequence.");
					return false;
				}
				const IAnimationDataModel* RefModel = RefPoseSeq->GetDataModel();
				const int32 RefFrameCount = RefModel != nullptr ? RefModel->GetNumberOfFrames() : 0;
				if (BasePoseType == ABPT_AnimFrame && RefFrameCount > 0 && RefFrameIndex >= RefFrameCount)
				{
					OutErrorCode = TEXT("retarget_base_pose_frame_out_of_range");
					OutErrorMessage = FString::Printf(
						TEXT("refFrameIndex %d is out of range [0, %d) for the Base Pose animation."),
						RefFrameIndex,
						RefFrameCount);
					return false;
				}
			}

			Sequence->RefPoseSeq = RefPoseSeq;
			Sequence->RefFrameIndex = RefFrameIndex;
			Sequence->AdditiveAnimType = AdditiveAnimType;
			Sequence->RefPoseType = BasePoseType;
			return true;
		}

		bool ReadAfter(
			const TSharedPtr<FJsonValue>& Requested,
			TSharedPtr<FJsonValue>& OutValue,
			FString& OutErrorCode,
			FString& OutErrorMessage) override
		{
			if (!ReadState(OutValue, OutErrorCode, OutErrorMessage))
			{
				return false;
			}
			const TSharedPtr<FJsonObject> RequestedObject = Requested.IsValid() ? Requested->AsObject() : nullptr;
			if (!RequestedObject.IsValid())
			{
				return true;
			}

			double ExpectedCombinedRootScale = 0.0;
			if (!RequestedObject->TryGetNumberField(TEXT("expectedCombinedRootScale"), ExpectedCombinedRootScale))
			{
				return true;
			}
			if (!FMath::IsFinite(ExpectedCombinedRootScale) || ExpectedCombinedRootScale <= 0.0)
			{
				OutErrorCode = TEXT("live-editor-write-value-invalid");
				OutErrorMessage = TEXT("expectedCombinedRootScale must be one finite number greater than 0.");
				return false;
			}

			FString RootBone;
			RequestedObject->TryGetStringField(TEXT("rootBone"), RootBone);
			if (RootBone.IsEmpty() && Sequence->GetSkeleton() != nullptr)
			{
				const FReferenceSkeleton& ReferenceSkeleton = Sequence->GetSkeleton()->GetReferenceSkeleton();
				if (ReferenceSkeleton.GetNum() > 0)
				{
					RootBone = ReferenceSkeleton.GetBoneName(0).ToString();
				}
			}

			FVector BaseScale;
			FVector DeltaLocalScale;
			FVector CombinedScale;
			FString EvaluationStatus;
			if (!EvaluateAdditiveCombinedScale(Sequence, FName(*RootBone), 0.0, BaseScale, DeltaLocalScale, CombinedScale, EvaluationStatus))
			{
				OutErrorCode = TEXT("retarget_additive_combined_evaluation_failed");
				OutErrorMessage = FString::Printf(TEXT("Combined additive evaluation failed: %s."), *EvaluationStatus);
				return false;
			}

			double Tolerance = FMath::Max(0.01, FMath::Abs(ExpectedCombinedRootScale) * DefaultFinalScaleToleranceRatio);
			RequestedObject->TryGetNumberField(TEXT("combinedScaleTolerance"), Tolerance);
			if (!FMath::IsFinite(Tolerance) || Tolerance < 0.0 || Tolerance > 1000000.0)
			{
				OutErrorCode = TEXT("live-editor-write-value-invalid");
				OutErrorMessage = TEXT("combinedScaleTolerance must be one finite non-negative number at most 1000000.");
				return false;
			}
			if (!FMath::IsNearlyEqual(CombinedScale.X, ExpectedCombinedRootScale, Tolerance)
				|| !FMath::IsNearlyEqual(CombinedScale.Y, ExpectedCombinedRootScale, Tolerance)
				|| !FMath::IsNearlyEqual(CombinedScale.Z, ExpectedCombinedRootScale, Tolerance))
			{
				OutErrorCode = TEXT("retarget_additive_combined_scale_mismatch");
				OutErrorMessage = FString::Printf(
					TEXT("Combined Root Scale is (%g, %g, %g), expected %g +/- %g. The live write was rolled back."),
					CombinedScale.X,
					CombinedScale.Y,
					CombinedScale.Z,
					ExpectedCombinedRootScale,
					Tolerance);
				return false;
			}
			return true;
		}

		bool SemanticEqual(
			const TSharedPtr<FJsonValue>& Left,
			const TSharedPtr<FJsonValue>& Right) override
		{
			return UEAgentKit::StructuredPropertyJson::JsonEqual(Left, Right);
		}

		void NotifyChanged() override
		{
			if (Sequence != nullptr)
			{
				Sequence->PostEditChange();
			}
		}

		void NotifyRestored() override
		{
			if (Sequence != nullptr)
			{
				Sequence->PostEditChange();
			}
		}

	private:
		bool ReadState(
			TSharedPtr<FJsonValue>& OutValue,
			FString& OutErrorCode,
			FString& OutErrorMessage) const
		{
			if (Sequence == nullptr || Sequence->GetSkeleton() == nullptr)
			{
				OutErrorCode = TEXT("live-editor-write-apply-failed");
				OutErrorMessage = TEXT("The Animation Sequence or Skeleton became unavailable.");
				return false;
			}

			TSharedRef<FJsonObject> State = MakeShared<FJsonObject>();
			State->SetStringField(
				TEXT("refSequencePath"),
				Sequence->RefPoseSeq != nullptr ? Sequence->RefPoseSeq->GetPathName() : FString());
			State->SetNumberField(TEXT("refFrameIndex"), Sequence->RefFrameIndex);
			State->SetStringField(
				TEXT("additiveAnimType"),
				AdditiveFixAnimTypeToString(static_cast<EAdditiveAnimationType>(Sequence->AdditiveAnimType.GetValue())));
			State->SetStringField(
				TEXT("additiveBasePoseType"),
				AdditiveFixBasePoseTypeToString(static_cast<EAdditiveBasePoseType>(Sequence->RefPoseType.GetValue())));
			OutValue = MakeShared<FJsonValueObject>(State);
			return true;
		}

		UAnimSequence* Sequence = nullptr;
		FAdditiveBasePoseFixSnapshot Snapshot;
		bool bSnapshotValid = false;
	};

	bool ApplyAdditiveBasePoseFixOperation(
		const FLiveWriteOperationContext& Context,
		const FLiveWriteOperationRequest& Request,
		TSharedPtr<FLiveWriteTransactionRecord>& OutRecord,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		UAnimSequence* Sequence = Cast<UAnimSequence>(Context.Asset);
		if (Sequence == nullptr)
		{
			OutErrorCode = TEXT("retarget_asset_type_invalid");
			OutErrorMessage = TEXT("setAdditiveBasePoseFix accepts only UAnimSequence assets.");
			return false;
		}

		TUniquePtr<ILiveWriteValueIO> IO = MakeUnique<FLiveWriteAdditiveBasePoseFixIO>(Sequence);
		FLiveWriteContext LiveContext;
		LiveContext.Asset = Sequence;
		LiveContext.Package = Context.Package;
		LiveContext.SessionId = Request.SessionId;
		LiveContext.TransactionTitle = TEXT("UE Agent Kit: Fix Additive Base Pose");
		LiveContext.AssetPath = Request.AssetPath;
		LiveContext.PropertyPath = TEXT("AdditiveBasePose");
		LiveContext.Value = Request.Value;

		FLiveWriteEvidence Evidence;
		if (!RunLiveWriteTransaction(LiveContext, IO, Evidence, OutErrorCode, OutErrorMessage))
		{
			return false;
		}
		OutRecord = BuildLiveWriteTransactionRecord(
			Sequence,
			Context.Package,
			Request.AssetPath,
			TEXT("setAdditiveBasePoseFix"),
			TEXT("additive-base-pose-fix"),
			Request.SessionId,
			Evidence,
			IO);

		TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
		FillLiveWriteEvidence(
			Result,
			LiveContext,
			Evidence,
			TEXT("setAdditiveBasePoseFix"),
			TEXT("additive-base-pose-fix"),
			TEXT("AnimSequenceAdditiveBasePose"),
			true,
			true);
		Result->SetBoolField(TEXT("combinedScaleVerifiedDuringApply"),
			Request.Value.IsValid()
			&& Request.Value->AsObject().IsValid()
			&& Request.Value->AsObject()->HasField(TEXT("expectedCombinedRootScale")));
		OutResult = Result;
		return true;
	}
}

namespace UEAgentKitLiveWrite
{
	void RegisterAnimationLiveWriteOperations(FLiveWriteOperationRegistry& Registry)
	{
		Registry.Register({
			TEXT("setAnimationScaleFix"),
			ELiveWriteTargetKind::Property,
			{TEXT("rootBone")},
			StandardAssetRequirements,
			&ApplyAnimationScaleFixOperation});
		Registry.Register({
			TEXT("setAdditiveBasePoseFix"),
			ELiveWriteTargetKind::Property,
			{},
			StandardAssetRequirements,
			&ApplyAdditiveBasePoseFixOperation});
	}
}
