#include "Retarget/RetargetAnalysis.h"

#include "Engine/SkeletalMesh.h"
#include "Animation/Skeleton.h"

namespace UEAgentKitRetarget
{
	namespace
	{
		FString NormalizeBoneName(const FString& Input)
		{
			FString Normalized;
			for (const TCHAR Character : Input)
			{
				if (FChar::IsAlnum(Character))
				{
					Normalized.AppendChar(FChar::ToLower(Character));
				}
			}
			return Normalized;
		}

		// Splits "alias1,alias2" into normalized alias set.
		TArray<FString> SplitAliases(const TCHAR* Aliases)
		{
			TArray<FString> Parts;
			FString(Aliases).ParseIntoArray(Parts, TEXT(","), true);
			TArray<FString> Normalized;
			for (FString& Part : Parts)
			{
				const FString Value = NormalizeBoneName(Part);
				if (!Value.IsEmpty())
				{
					Normalized.Add(Value);
				}
			}
			return Normalized;
		}

		ERetargetChainSide DetectSide(const FString& BoneName)
		{
			// bip001 style: "Bip001 L UpperArm" / "Bip001 R Hand"
			if (BoneName.Contains(TEXT(" L ")) || BoneName.EndsWith(TEXT(" L")))
			{
				return ERetargetChainSide::Left;
			}
			if (BoneName.Contains(TEXT(" R ")) || BoneName.EndsWith(TEXT(" R")))
			{
				return ERetargetChainSide::Right;
			}
			const FString Normalized = NormalizeBoneName(BoneName);
			if (Normalized.EndsWith(TEXT("l")) || Normalized.Contains(TEXT("left")))
			{
				return ERetargetChainSide::Left;
			}
			if (Normalized.EndsWith(TEXT("r")) || Normalized.Contains(TEXT("right")))
			{
				return ERetargetChainSide::Right;
			}
			return ERetargetChainSide::Center;
		}

		float PositionSideScore(const FVector& ComponentPosition, ERetargetChainSide ExpectedSide, const FVector& RootPosition)
		{
			const float RelativeY = ComponentPosition.Y - RootPosition.Y;
			const float AbsoluteY = FMath::Abs(RelativeY);
			switch (ExpectedSide)
			{
			case ERetargetChainSide::Center:
				return AbsoluteY < 1.0f ? 1.0f : FMath::Clamp(1.0f - AbsoluteY * 0.1f, 0.0f, 1.0f);
			case ERetargetChainSide::Left:
				return RelativeY < -1.0f ? 1.0f : FMath::Clamp(1.0f - FMath::Abs(RelativeY + 1.0f) * 0.2f, 0.0f, 1.0f);
			case ERetargetChainSide::Right:
				return RelativeY > 1.0f ? 1.0f : FMath::Clamp(1.0f - FMath::Abs(RelativeY - 1.0f) * 0.2f, 0.0f, 1.0f);
			}
			return 0.0f;
		}

		bool IsHighRiskBone(const FString& BoneName)
		{
			const FString Normalized = NormalizeBoneName(BoneName);
			static const TArray<FString> RiskKeywords = {
				TEXT("hair"), TEXT("tail"), TEXT("ear"), TEXT("skirt"), TEXT("cloth"),
				TEXT("ribbon"), TEXT("piao"), TEXT("accessory"), TEXT("weapon")
			};
			for (const FString& Keyword : RiskKeywords)
			{
				if (Normalized.Contains(Keyword))
				{
					return true;
				}
			}
			return false;
		}

		struct FChainProfile
		{
			const TCHAR* Name;
			bool bRequired;
			ERetargetChainSide Side;
			const TCHAR* StartAliases;
			const TCHAR* EndAliases;
		};

		const FChainProfile HumanoidV1Chains[] = {
			{ TEXT("Root"), true, ERetargetChainSide::Center, TEXT("root,hips,pelvis,bip001"), TEXT("root,hips,pelvis,bip001") },
			{ TEXT("Spine"), true, ERetargetChainSide::Center, TEXT("pelvis,hips,root,bip001pelvis"), TEXT("chest,upperchest,spine_03,spine3,chest_01,spine_02,bip001chest,bip001spine1,bip001spine2") },
			{ TEXT("Neck"), true, ERetargetChainSide::Center, TEXT("neck,neck_01,bip001neck"), TEXT("neck,neck_01,head,bip001neck,bip001head") },
			{ TEXT("Head"), true, ERetargetChainSide::Center, TEXT("head,bip001head"), TEXT("head,bip001head") },
			{ TEXT("LeftArm"), true, ERetargetChainSide::Left, TEXT("upperarm_l,arm_l,shoulder_l,upper_arm_l,bip001lupperarm"), TEXT("hand_l,wrist_l,bip001lhand") },
			{ TEXT("RightArm"), true, ERetargetChainSide::Right, TEXT("upperarm_r,arm_r,shoulder_r,upper_arm_r,bip001rupperarm"), TEXT("hand_r,wrist_r,bip001rhand") },
			{ TEXT("LeftLeg"), true, ERetargetChainSide::Left, TEXT("thigh_l,leg_l,upperleg_l,bip001lthigh"), TEXT("foot_l,ankle_l,bip001lfoot") },
			{ TEXT("RightLeg"), true, ERetargetChainSide::Right, TEXT("thigh_r,leg_r,upperleg_r,bip001rthigh"), TEXT("foot_r,ankle_r,bip001rfoot") },
			{ TEXT("LeftClavicle"), false, ERetargetChainSide::Left, TEXT("clavicle_l,collar_l,bip001lclavicle"), TEXT("clavicle_l,collar_l,bip001lclavicle") },
			{ TEXT("RightClavicle"), false, ERetargetChainSide::Right, TEXT("clavicle_r,collar_r,bip001rclavicle"), TEXT("clavicle_r,collar_r,bip001rclavicle") },
			{ TEXT("LeftHand"), false, ERetargetChainSide::Left, TEXT("hand_l,wrist_l,bip001lhand"), TEXT("hand_l,wrist_l,bip001lhand") },
			{ TEXT("RightHand"), false, ERetargetChainSide::Right, TEXT("hand_r,wrist_r,bip001rhand"), TEXT("hand_r,wrist_r,bip001rhand") },
			{ TEXT("LeftFoot"), false, ERetargetChainSide::Left, TEXT("foot_l,ankle_l,bip001lfoot"), TEXT("ball_l,toe_l,bip001ltoe0") },
			{ TEXT("RightFoot"), false, ERetargetChainSide::Right, TEXT("foot_r,ankle_r,bip001rfoot"), TEXT("ball_r,toe_r,bip001rtoe0") },
			{ TEXT("LeftToe"), false, ERetargetChainSide::Left, TEXT("ball_l,toe_l,bip001ltoe0"), TEXT("ball_l,toe_l,bip001ltoe0") },
			{ TEXT("RightToe"), false, ERetargetChainSide::Right, TEXT("ball_r,toe_r,bip001rtoe0"), TEXT("ball_r,toe_r,bip001rtoe0") },
			{ TEXT("LeftThumb"), false, ERetargetChainSide::Left, TEXT("thumb_l,bip001lthumb"), TEXT("thumb_l,bip001lthumb") },
			{ TEXT("RightThumb"), false, ERetargetChainSide::Right, TEXT("thumb_r,bip001rthumb"), TEXT("thumb_r,bip001rthumb") },
			{ TEXT("LeftIndex"), false, ERetargetChainSide::Left, TEXT("index_l,bip001lindex1"), TEXT("index_l,bip001lindex1") },
			{ TEXT("RightIndex"), false, ERetargetChainSide::Right, TEXT("index_r,bip001rindex1"), TEXT("index_r,bip001rindex1") },
		};

		int32 ExpectedChainLength(const FChainProfile& Profile)
		{
			// Center short chains and hands/feet are 0-1 bones; arms/legs are 2-4.
			const FString Name(Profile.Name);
			if (Name.Equals(TEXT("Arm")) || Name.EndsWith(TEXT("Arm")) || Name.EndsWith(TEXT("Leg")))
			{
				return 3;
			}
			if (Name.EndsWith(TEXT("Hand")) || Name.EndsWith(TEXT("Foot")))
			{
				return 1;
			}
			if (Name.Equals(TEXT("Root")))
			{
				return 0;
			}
			return 1;
		}

		float BoneNameMatch(const FString& BoneName, const TArray<FString>& Aliases)
		{
			const FString Normalized = NormalizeBoneName(BoneName);
			for (const FString& Alias : Aliases)
			{
				if (Normalized == Alias)
				{
					return 1.0f;
				}
			}
			// "bip001" is a shared prefix of many unrelated bones; it only counts
			// as an exact match. Other aliases may match as a prefix (>= 5 chars)
			// or as a contained token (>= 6 chars).
			const bool bGenericPrefix = Normalized == TEXT("bip001") || Normalized.StartsWith(TEXT("bip001"));
			if (!bGenericPrefix)
			{
				for (const FString& Alias : Aliases)
				{
					if (Alias.Len() >= 5 && Normalized.StartsWith(Alias))
					{
						return 0.7f;
					}
				}
				for (const FString& Alias : Aliases)
				{
					if (Alias.Len() >= 6 && Normalized.Contains(Alias))
					{
						return 0.5f;
					}
				}
			}
			return 0.0f;
		}

		float ParentContextScoreFor(const FString& BoneName, const TArray<FString>& Aliases, const FRetargetSkeletonSnapshot& Snapshot)
		{
			const int32* IndexPtr = Snapshot.BoneIndices.Find(FName(*BoneName));
			if (IndexPtr == nullptr || *IndexPtr <= 0)
			{
				return 0.5f;
			}
			const FString ParentName = Snapshot.Bones[*IndexPtr - 1].Name.ToString();
			const FString NormalizedParent = NormalizeBoneName(ParentName);
			const FString NormalizedBone = NormalizeBoneName(BoneName);
			static const TArray<TArray<FString>> ContextHints = {
				{ TEXT("clavicle"), TEXT("shoulder"), TEXT("upperarm"), TEXT("arm") },
				{ TEXT("pelvis"), TEXT("hips"), TEXT("thigh"), TEXT("leg") },
				{ TEXT("chest"), TEXT("spine"), TEXT("neck") },
				{ TEXT("neck"), TEXT("head"), TEXT("skull") },
			};
			for (const TArray<FString>& Hints : ContextHints)
			{
				if (Aliases.ContainsByPredicate([&NormalizedBone, &Hints](const FString& Alias)
					{
						return NormalizedBone.Contains(Alias) && Hints.Num() > 0;
					}))
				{
					for (const FString& Hint : Hints)
					{
						if (NormalizedParent.Contains(Hint))
						{
							return 1.0f;
						}
					}
					return 0.5f;
				}
			}
			return 0.5f;
		}
	}

	bool BuildSkeletonSnapshot(USkeletalMesh* Mesh, int32 MaxBones, FRetargetSkeletonSnapshot& OutSnapshot, FString& OutError)
	{
		if (Mesh == nullptr || Mesh->GetSkeleton() == nullptr)
		{
			OutError = TEXT("The target is not a SkeletalMesh with a valid Skeleton.");
			return false;
		}
		USkeleton* Skeleton = Mesh->GetSkeleton();
		const FReferenceSkeleton& RefSkeleton = Skeleton->GetReferenceSkeleton();
		const TArray<FTransform>& RefPose = RefSkeleton.GetRefBonePose();
		OutSnapshot.SkeletonPath = Skeleton->GetPathName();
		OutSnapshot.MeshPath = Mesh->GetPathName();
		OutSnapshot.MeshClassPath = Mesh->GetClass()->GetPathName();
		OutSnapshot.BoneCount = RefSkeleton.GetNum();
		OutSnapshot.bTruncated = RefSkeleton.GetNum() > MaxBones;
		OutSnapshot.Bones.Reset();
		OutSnapshot.BoneIndices.Reset();
		const int32 BoneCount = FMath::Min(RefSkeleton.GetNum(), MaxBones);
		TArray<FTransform> ComponentTransforms;
		ComponentTransforms.SetNumUninitialized(FMath::Max(BoneCount, 1));
		for (int32 Index = 0; Index < BoneCount; ++Index)
		{
			FRetargetBoneInfo Bone;
			Bone.Name = RefSkeleton.GetBoneName(Index);
			Bone.Index = Index;
			Bone.ParentIndex = RefSkeleton.GetParentIndex(Index);
			Bone.Depth = RefSkeleton.GetDepthBetweenBones(Index, 0);
			const FTransform Local = Index < RefPose.Num() ? RefPose[Index] : FTransform::Identity;
			Bone.LocalPosition = Local.GetLocation();
			ComponentTransforms[Index] = Bone.ParentIndex >= 0 ? ComponentTransforms[Bone.ParentIndex] * Local : Local;
			Bone.ComponentPosition = ComponentTransforms[Index].GetLocation();
			OutSnapshot.BoneIndices.Add(Bone.Name, Index);
			if (Index == 0)
			{
				OutSnapshot.RootBone = Bone.Name.ToString();
			}
			OutSnapshot.Bones.Add(Bone);
		}
		return true;
	}

	bool AnalyzeRetargetCompatibility(
		USkeletalMesh* SourceMesh,
		USkeletalMesh* TargetMesh,
		bool bIncludeOptionalChains,
		int32 MaxBoneDetails,
		FRetargetCompatibilityReport& OutReport,
		FString& OutError)
	{
		if (SourceMesh == nullptr || TargetMesh == nullptr)
		{
			OutError = TEXT("Source and target Skeletal Mesh assets are required.");
			return false;
		}
		if (SourceMesh == TargetMesh)
		{
			OutError = TEXT("Source and target must be different assets.");
			return false;
		}

		FRetargetSkeletonSnapshot SourceSnapshot;
		FRetargetSkeletonSnapshot TargetSnapshot;
		if (!BuildSkeletonSnapshot(SourceMesh, MaxBoneDetails, SourceSnapshot, OutError)
			|| !BuildSkeletonSnapshot(TargetMesh, MaxBoneDetails, TargetSnapshot, OutError))
		{
			return false;
		}
		OutReport.SourceSkeleton = SourceSnapshot.SkeletonPath;
		OutReport.TargetSkeleton = TargetSnapshot.SkeletonPath;
		OutReport.bTruncated = SourceSnapshot.bTruncated || TargetSnapshot.bTruncated;

		// Retarget root candidates: root bone plus any pelvis/hips-named bone.
		auto CollectRootCandidates = [](const FRetargetSkeletonSnapshot& Snapshot, TArray<FString>& OutCandidates)
		{
			OutCandidates.Add(Snapshot.RootBone);
			for (const FRetargetBoneInfo& Bone : Snapshot.Bones)
			{
				const FString Normalized = NormalizeBoneName(Bone.Name.ToString());
				if (Normalized == TEXT("pelvis") || Normalized == TEXT("hips"))
				{
					OutCandidates.AddUnique(Bone.Name.ToString());
				}
			}
		};
		CollectRootCandidates(SourceSnapshot, OutReport.SourceRetargetRootCandidates);
		CollectRootCandidates(TargetSnapshot, OutReport.TargetRetargetRootCandidates);
		if (OutReport.SourceRetargetRootCandidates.IsEmpty() || OutReport.TargetRetargetRootCandidates.IsEmpty())
		{
			OutReport.BlockingIssues.Add(TEXT("No usable retarget root candidate was found."));
		}

		// Chain candidates.
		const FVector SourceRootPosition = SourceSnapshot.Bones.Num() > 0 ? SourceSnapshot.Bones[0].ComponentPosition : FVector::ZeroVector;
		const FVector TargetRootPosition = TargetSnapshot.Bones.Num() > 0 ? TargetSnapshot.Bones[0].ComponentPosition : FVector::ZeroVector;
		for (const FChainProfile& Profile : HumanoidV1Chains)
		{
			if (!bIncludeOptionalChains && !Profile.bRequired)
			{
				continue;
			}
			const FString ChainName(Profile.Name);
			FRetargetChainCandidateReport ChainReport;
			ChainReport.ChainName = ChainName;
			ChainReport.Required = Profile.bRequired ? ERetargetChainRequired::Required : ERetargetChainRequired::Optional;
			const TArray<FString> StartAliases = SplitAliases(Profile.StartAliases);
			const TArray<FString> EndAliases = SplitAliases(Profile.EndAliases);

			// Evaluate on the target skeleton; the source skeleton is scored the same way
			// in Phase 2 when building explicit chains. Phase 1 reports target-side candidates.
			const FRetargetSkeletonSnapshot& Snapshot = TargetSnapshot;
			const FVector& RootPosition = TargetRootPosition;
			for (int32 StartIndex = 0; StartIndex < Snapshot.Bones.Num(); ++StartIndex)
			{
				const FString StartName = Snapshot.Bones[StartIndex].Name.ToString();
				const float StartNameScore = BoneNameMatch(StartName, StartAliases);
				if (StartNameScore <= 0.0f || IsHighRiskBone(StartName))
				{
					continue;
				}
				if (ChainName == TEXT("Root") && Snapshot.Bones[StartIndex].Depth != 0)
				{
					continue;
				}
				// Keep only the deepest matching end per start so equivalent
				// endpoints of the same semantic chain are not reported as
				// ambiguous competing candidates.
				int32 DeepestEndIndex = StartIndex;
				bool bFoundEnd = false;
				for (int32 EndIndex = StartIndex; EndIndex < Snapshot.Bones.Num(); ++EndIndex)
				{
					const FString EndName = Snapshot.Bones[EndIndex].Name.ToString();
					if (BoneNameMatch(EndName, EndAliases) <= 0.0f)
					{
						continue;
					}
					if (EndIndex > StartIndex && Snapshot.Bones[EndIndex].Depth <= Snapshot.Bones[StartIndex].Depth)
					{
						continue;
					}
					if (IsHighRiskBone(EndName))
					{
						continue;
					}
					DeepestEndIndex = EndIndex;
					bFoundEnd = true;
				}
				if (!bFoundEnd)
				{
					continue;
				}
				const ERetargetChainSide Side = Profile.Side;
				const ERetargetChainSide BoneSide = DetectSide(StartName);
				FRetargetChainCandidate Candidate;
				Candidate.ChainName = ChainName;
				Candidate.Required = ChainReport.Required;
				Candidate.Side = Side;
				Candidate.StartBone = Snapshot.Bones[StartIndex].Name;
				Candidate.EndBone = Snapshot.Bones[DeepestEndIndex].Name;
				Candidate.StartIndex = StartIndex;
				Candidate.EndIndex = DeepestEndIndex;
				Candidate.NameScore = FMath::Max(
					StartNameScore,
					BoneNameMatch(Candidate.EndBone.ToString(), EndAliases));
				const bool bSingleBone = DeepestEndIndex == StartIndex;
				Candidate.HierarchyScore = 1.0f;
				if (bSingleBone && ExpectedChainLength(Profile) > 1)
				{
					Candidate.HierarchyScore = 0.5f;
				}
				Candidate.SideScore = BoneSide == Side
					? 1.0f
					: (Side == ERetargetChainSide::Center ? 0.5f : 0.1f);
				Candidate.PositionScore = PositionSideScore(
					Snapshot.Bones[StartIndex].ComponentPosition,
					Side,
					RootPosition);
				const int32 ChainLength = Snapshot.Bones[DeepestEndIndex].Depth - Snapshot.Bones[StartIndex].Depth;
				const int32 ExpectedLength = ExpectedChainLength(Profile);
				Candidate.LengthScore = FMath::Clamp(
					1.0f - FMath::Abs(ChainLength - ExpectedLength) * 0.25f,
					0.0f,
					1.0f);
				Candidate.ParentContextScore = ParentContextScoreFor(StartName, StartAliases, Snapshot);
				Candidate.Confidence =
					0.30f * Candidate.NameScore
					+ 0.15f * Candidate.HierarchyScore
					+ 0.10f * Candidate.SideScore
					+ 0.15f * Candidate.PositionScore
					+ 0.15f * Candidate.LengthScore
					+ 0.15f * Candidate.ParentContextScore;
				Candidate.Reasons.Add(FString::Printf(TEXT("start=%s end=%s"), *Candidate.StartBone.ToString(), *Candidate.EndBone.ToString()));
				ChainReport.Candidates.Add(Candidate);
			}
			ChainReport.Candidates.Sort([](const FRetargetChainCandidate& Left, const FRetargetChainCandidate& Right)
			{
				return Left.Confidence > Right.Confidence;
			});
			if (ChainReport.Candidates.Num() > 3)
			{
				ChainReport.Candidates.SetNum(3);
			}
			if (ChainReport.Candidates.Num() >= 2
				&& ChainReport.Candidates[0].Confidence >= 0.7f
				&& ChainReport.Candidates[1].Confidence >= 0.7f
				&& ChainReport.Candidates[0].Confidence - ChainReport.Candidates[1].Confidence < 0.05f)
			{
				ChainReport.bAmbiguous = true;
			}
			OutReport.ChainCandidates.Add(ChainReport);
		}

		// Required chain matching summary.
		for (const FRetargetChainCandidateReport& Chain : OutReport.ChainCandidates)
		{
			const bool bMatched = Chain.Candidates.Num() > 0 && Chain.Candidates[0].Confidence >= 0.7f && !Chain.bAmbiguous;
			if (Chain.Required == ERetargetChainRequired::Required)
			{
				if (!bMatched)
				{
					OutReport.UnmatchedRequiredChains.Add(Chain.ChainName);
				}
			}
			else if (!bMatched)
			{
				OutReport.UnmatchedOptionalChains.Add(Chain.ChainName);
			}
		}

		// Compatibility verdict.
		if (OutReport.UnmatchedRequiredChains.Num() == 0 && OutReport.BlockingIssues.IsEmpty())
		{
			OutReport.Compatibility = OutReport.UnmatchedOptionalChains.IsEmpty()
				? TEXT("compatible")
				: TEXT("compatible_with_warnings");
		}
		else
		{
			const bool bRootOrSpineMissing =
				OutReport.UnmatchedRequiredChains.Contains(TEXT("Root"))
				|| OutReport.UnmatchedRequiredChains.Contains(TEXT("Spine"));
			if (bRootOrSpineMissing || OutReport.UnmatchedRequiredChains.Num() >= 3)
			{
				OutReport.Compatibility = TEXT("blocked");
			}
			else
			{
				OutReport.Compatibility = TEXT("needs_manual_mapping");
			}
		}
		for (const FString& Unmatched : OutReport.UnmatchedRequiredChains)
		{
			OutReport.Warnings.Add(FString::Printf(TEXT("Required chain %s is unmatched."), *Unmatched));
		}
		return true;
	}

	TSharedRef<FJsonObject> SkeletonSnapshotToJson(const FRetargetSkeletonSnapshot& Snapshot)
	{
		TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
		Json->SetStringField(TEXT("skeletonPath"), Snapshot.SkeletonPath);
		Json->SetStringField(TEXT("meshPath"), Snapshot.MeshPath);
		Json->SetStringField(TEXT("meshClassPath"), Snapshot.MeshClassPath);
		Json->SetStringField(TEXT("rootBone"), Snapshot.RootBone);
		Json->SetNumberField(TEXT("boneCount"), Snapshot.BoneCount);
		Json->SetBoolField(TEXT("truncated"), Snapshot.bTruncated);
		TArray<TSharedPtr<FJsonValue>> Bones;
		for (const FRetargetBoneInfo& Bone : Snapshot.Bones)
		{
			TSharedRef<FJsonObject> BoneJson = MakeShared<FJsonObject>();
			BoneJson->SetStringField(TEXT("name"), Bone.Name.ToString());
			BoneJson->SetNumberField(TEXT("index"), Bone.Index);
			BoneJson->SetNumberField(TEXT("parentIndex"), Bone.ParentIndex);
			BoneJson->SetNumberField(TEXT("depth"), Bone.Depth);
			BoneJson->SetStringField(TEXT("localPosition"), Bone.LocalPosition.ToString());
			BoneJson->SetStringField(TEXT("componentPosition"), Bone.ComponentPosition.ToString());
			Bones.Add(MakeShared<FJsonValueObject>(BoneJson));
		}
		Json->SetArrayField(TEXT("bones"), Bones);
		return Json;
	}

	TSharedRef<FJsonObject> CompatibilityReportToJson(const FRetargetCompatibilityReport& Report)
	{
		TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
		Json->SetStringField(TEXT("compatibility"), Report.Compatibility);
		Json->SetStringField(TEXT("sourceSkeleton"), Report.SourceSkeleton);
		Json->SetStringField(TEXT("targetSkeleton"), Report.TargetSkeleton);
		TArray<TSharedPtr<FJsonValue>> SourceRoots;
		for (const FString& Root : Report.SourceRetargetRootCandidates)
		{
			SourceRoots.Add(MakeShared<FJsonValueString>(Root));
		}
		Json->SetArrayField(TEXT("sourceRetargetRootCandidates"), SourceRoots);
		TArray<TSharedPtr<FJsonValue>> TargetRoots;
		for (const FString& Root : Report.TargetRetargetRootCandidates)
		{
			TargetRoots.Add(MakeShared<FJsonValueString>(Root));
		}
		Json->SetArrayField(TEXT("targetRetargetRootCandidates"), TargetRoots);

		TArray<TSharedPtr<FJsonValue>> Chains;
		for (const FRetargetChainCandidateReport& Chain : Report.ChainCandidates)
		{
			TSharedRef<FJsonObject> ChainJson = MakeShared<FJsonObject>();
			ChainJson->SetStringField(TEXT("chain"), Chain.ChainName);
			ChainJson->SetStringField(TEXT("required"), Chain.Required == ERetargetChainRequired::Required ? TEXT("required") : TEXT("optional"));
			ChainJson->SetBoolField(TEXT("ambiguous"), Chain.bAmbiguous);
			TArray<TSharedPtr<FJsonValue>> Candidates;
			for (const FRetargetChainCandidate& Candidate : Chain.Candidates)
			{
				TSharedRef<FJsonObject> CandidateJson = MakeShared<FJsonObject>();
				CandidateJson->SetStringField(TEXT("startBone"), Candidate.StartBone.ToString());
				CandidateJson->SetStringField(TEXT("endBone"), Candidate.EndBone.ToString());
				CandidateJson->SetNumberField(TEXT("nameScore"), Candidate.NameScore);
				CandidateJson->SetNumberField(TEXT("hierarchyScore"), Candidate.HierarchyScore);
				CandidateJson->SetNumberField(TEXT("sideScore"), Candidate.SideScore);
				CandidateJson->SetNumberField(TEXT("positionScore"), Candidate.PositionScore);
				CandidateJson->SetNumberField(TEXT("lengthScore"), Candidate.LengthScore);
				CandidateJson->SetNumberField(TEXT("parentContextScore"), Candidate.ParentContextScore);
				CandidateJson->SetNumberField(TEXT("confidence"), Candidate.Confidence);
				TArray<TSharedPtr<FJsonValue>> Reasons;
				for (const FString& Reason : Candidate.Reasons)
				{
					Reasons.Add(MakeShared<FJsonValueString>(Reason));
				}
				CandidateJson->SetArrayField(TEXT("reason"), Reasons);
				Candidates.Add(MakeShared<FJsonValueObject>(CandidateJson));
			}
			ChainJson->SetArrayField(TEXT("candidates"), Candidates);
			Chains.Add(MakeShared<FJsonValueObject>(ChainJson));
		}
		Json->SetArrayField(TEXT("chainCandidates"), Chains);

		auto Strings = [](const TArray<FString>& Values)
		{
			TArray<TSharedPtr<FJsonValue>> Result;
			for (const FString& Value : Values)
			{
				Result.Add(MakeShared<FJsonValueString>(Value));
			}
			return Result;
		};
		Json->SetArrayField(TEXT("unmatchedRequiredChains"), Strings(Report.UnmatchedRequiredChains));
		Json->SetArrayField(TEXT("unmatchedOptionalChains"), Strings(Report.UnmatchedOptionalChains));
		Json->SetArrayField(TEXT("warnings"), Strings(Report.Warnings));
		Json->SetArrayField(TEXT("blockingIssues"), Strings(Report.BlockingIssues));
		Json->SetBoolField(TEXT("truncated"), Report.bTruncated);
		return Json;
	}
}
