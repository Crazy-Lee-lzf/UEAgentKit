#include "Retarget/RetargetTypes.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetToolsModule.h"
#include "Engine/SkeletalMesh.h"
#include "Rig/IKRigDefinition.h"
#include "IKRigEditor/Public/RigEditor/IKRigController.h"
#include "Misc/PackageName.h"
#include "ScopedTransaction.h"
#include "UObject/Package.h"

#include "Retargeter/IKRetargetChainMapping.h"
#include "Retargeter/IKRetargetSettings.h"
#include "Retargeter/IKRetargeter.h"
#include "Retargeter/RetargetOps/FKChainsOp.h"
#include "RetargetEditor/IKRetargetFactory.h"
#include "RetargetEditor/IKRetargeterController.h"

namespace UEAgentKitRetarget
{
	namespace
	{
		FString MeshObjectPath(USkeletalMesh* Mesh)
		{
			const UPackage* Package = Mesh->GetOutermost();
			return FString::Printf(TEXT("%s.%s"), *Package->GetName(), *Mesh->GetName());
		}

		FName ChainRequiredToName(ERetargetChainRequired Required)
		{
			return Required == ERetargetChainRequired::Required ? TEXT("Required") : TEXT("Optional");
		}

		bool IsChainInRig(const UIKRigDefinition* Rig, const FString& ChainName, FName& OutStart, FName& OutEnd)
		{
			const UIKRigController* Controller = UIKRigController::GetController(Rig);
			if (Controller == nullptr)
			{
				return false;
			}
			for (const FBoneChain& Chain : Controller->GetRetargetChains())
			{
				if (Chain.ChainName.ToString() == ChainName)
				{
					OutStart = Chain.StartBone.BoneName;
					OutEnd = Chain.EndBone.BoneName;
					return true;
				}
			}
			return false;
		}
	}

	bool FindIKRigForMesh(USkeletalMesh* Mesh, FRetargetIKRigState& OutState)
	{
		OutState = FRetargetIKRigState();
		if (Mesh == nullptr)
		{
			return false;
		}
		const FString ExpectedObjectPath = MeshObjectPath(Mesh);
		// In-memory assets first: an IK Rig created but not yet saved is not
		// visible to the on-disk Asset Registry.
		UIKRigDefinition* FoundRig = nullptr;
		ForEachObjectOfClass(
			UIKRigDefinition::StaticClass(),
			[&FoundRig, &ExpectedObjectPath](UObject* Object)
			{
				if (FoundRig != nullptr)
				{
					return;
				}
				const UIKRigDefinition* Rig = Cast<UIKRigDefinition>(Object);
				if (Rig == nullptr || Rig->PreviewSkeletalMesh.ToSoftObjectPath().ToString() != ExpectedObjectPath)
				{
					return;
				}
				FoundRig = const_cast<UIKRigDefinition*>(Rig);
			},
			false,
			RF_NoFlags,
			EInternalObjectFlags::Garbage);
		if (FoundRig == nullptr)
		{
			const FAssetRegistryModule& AssetRegistryModule =
				FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry"));
			FARFilter Filter;
			Filter.ClassPaths.Add(UIKRigDefinition::StaticClass()->GetClassPathName());
			Filter.PackagePaths.Add(FName(TEXT("/Game")));
			Filter.bRecursivePaths = true;
			TArray<FAssetData> Assets;
			AssetRegistryModule.Get().GetAssets(Filter, Assets);
			for (const FAssetData& Asset : Assets)
			{
				FString TagValue;
				if (Asset.GetTagValue(TEXT("PreviewSkeletalMesh"), TagValue))
				{
					const FSoftObjectPath TaggedPath(TagValue);
					const FString TaggedObjectPath = TaggedPath.IsValid()
						? TaggedPath.ToString()
						: TagValue;
					if (TaggedObjectPath == ExpectedObjectPath)
					{
						FoundRig = Cast<UIKRigDefinition>(Asset.GetAsset());
						break;
					}
				}
			}
		}
		if (FoundRig != nullptr)
		{
			OutState.AssetPath = FoundRig->GetOutermost()->GetName() + TEXT(".") + FoundRig->GetName();
			const UIKRigController* Controller = UIKRigController::GetController(FoundRig);
			if (Controller != nullptr)
			{
				OutState.RetargetRoot = Controller->GetRetargetRoot().ToString();
				for (const FBoneChain& Chain : Controller->GetRetargetChains())
				{
					OutState.ChainNames.Add(Chain.ChainName.ToString());
				}
			}
			return true;
		}
		return false;
	}

	// Checks whether the plan configuration can be applied without creating or
	// modifying any asset. Conflict detection must not leave partial state behind.
	bool PreflightIKRigConfig(
		USkeletalMesh* Mesh,
		const FString& RetargetRoot,
		const TArray<FRetargetPlanChain>& Chains,
		bool bUpdateExisting,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		FRetargetIKRigState Existing;
		if (!FindIKRigForMesh(Mesh, Existing))
		{
			return true;
		}
		const FReferenceSkeleton& RefSkeleton = Mesh->GetSkeleton()->GetReferenceSkeleton();
		const FName RootBoneName(RetargetRoot.IsEmpty() ? RefSkeleton.GetBoneName(0) : *RetargetRoot);
		UIKRigDefinition* Rig = Cast<UIKRigDefinition>(LoadObject<UIKRigDefinition>(nullptr, *Existing.AssetPath));
		if (Rig == nullptr)
		{
			return true;
		}
		const UIKRigController* Controller = UIKRigController::GetController(Rig);
		if (Controller == nullptr)
		{
			return true;
		}
		if (!bUpdateExisting && Controller->GetRetargetRoot() != RootBoneName)
		{
			OutErrorCode = TEXT("retarget_asset_conflict");
			OutErrorMessage = FString::Printf(
				TEXT("Existing IK Rig root %s differs from the Plan root %s; updateExisting is required to change it."),
				*Controller->GetRetargetRoot().ToString(),
				*RootBoneName.ToString());
			return false;
		}
		for (const FRetargetPlanChain& Chain : Chains)
		{
			FName ExistingStart;
			FName ExistingEnd;
			if (IsChainInRig(Rig, Chain.ChainName, ExistingStart, ExistingEnd)
				&& (ExistingStart != FName(*Chain.StartBone) || ExistingEnd != FName(*Chain.EndBone))
				&& !bUpdateExisting)
			{
				OutErrorCode = TEXT("retarget_asset_conflict");
				OutErrorMessage = FString::Printf(
					TEXT("Chain %s exists with different bones and updateExisting is not enabled."),
					*Chain.ChainName);
				return false;
			}
		}
		return true;
	}

	bool ApplyIKRigConfig(
		USkeletalMesh* Mesh,
		const FString& DesiredAssetName,
		const FString& RetargetRoot,
		const TArray<FRetargetPlanChain>& Chains,
		bool bUpdateExisting,
		bool bAllowCreate,
		FRetargetAssetChange& OutChange,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		OutChange = FRetargetAssetChange();
		if (Mesh == nullptr)
		{
			OutErrorCode = TEXT("retarget_asset_not_found");
			OutErrorMessage = TEXT("The mesh for the IK Rig is not loaded.");
			return false;
		}
		const FReferenceSkeleton& RefSkeleton = Mesh->GetSkeleton()->GetReferenceSkeleton();
		if (RefSkeleton.GetNum() == 0)
		{
			OutErrorCode = TEXT("retarget_skeleton_invalid");
			OutErrorMessage = TEXT("The mesh has no Reference Skeleton to configure.");
			return false;
		}
		const FName RootBoneName(RetargetRoot.IsEmpty() ? RefSkeleton.GetBoneName(0) : *RetargetRoot);
		if (RefSkeleton.FindBoneIndex(RootBoneName) == INDEX_NONE)
		{
			OutErrorCode = TEXT("retarget_root_not_found");
			OutErrorMessage = FString::Printf(TEXT("Retarget Root bone does not exist: %s."), *RetargetRoot);
			return false;
		}
		for (const FRetargetPlanChain& Chain : Chains)
		{
			const int32 StartIndex = RefSkeleton.FindBoneIndex(FName(*Chain.StartBone));
			const int32 EndIndex = RefSkeleton.FindBoneIndex(FName(*Chain.EndBone));
			if (StartIndex == INDEX_NONE || EndIndex == INDEX_NONE)
			{
				OutErrorCode = TEXT("retarget_chain_ambiguous");
				OutErrorMessage = FString::Printf(TEXT("Chain %s references a bone that does not exist on the target skeleton."), *Chain.ChainName);
				return false;
			}
			if (StartIndex != EndIndex && RefSkeleton.GetDepthBetweenBones(EndIndex, StartIndex) < 0)
			{
				OutErrorCode = TEXT("retarget_chain_ambiguous");
				OutErrorMessage = FString::Printf(TEXT("Chain %s end bone is not a descendant of its start bone."), *Chain.ChainName);
				return false;
			}
		}

		FRetargetIKRigState Existing;
		const bool bHasExisting = FindIKRigForMesh(Mesh, Existing);
		UIKRigDefinition* Rig = nullptr;
		if (bHasExisting)
		{
			Rig = Cast<UIKRigDefinition>(LoadObject<UIKRigDefinition>(nullptr, *Existing.AssetPath));
			if (Rig == nullptr)
			{
				OutErrorCode = TEXT("retarget_asset_not_found");
				OutErrorMessage = TEXT("The existing IK Rig could not be loaded.");
				return false;
			}
		}
		else
		{
			if (!bAllowCreate)
			{
				OutErrorCode = TEXT("retarget_asset_conflict");
				OutErrorMessage = TEXT("No IK Rig exists for this mesh and creation is not enabled in the Plan.");
				return false;
			}
			if (!DesiredAssetName.IsEmpty())
			{
				const FString MeshPackagePath = FPackageName::GetLongPackagePath(Mesh->GetOutermost()->GetName());
				Rig = Cast<UIKRigDefinition>(IAssetTools::Get().CreateAsset(
					*DesiredAssetName,
					*MeshPackagePath,
					UIKRigDefinition::StaticClass(),
					nullptr));
			}
			if (Rig == nullptr)
			{
				OutErrorCode = TEXT("retarget_asset_conflict");
				OutErrorMessage = TEXT("The IK Rig asset could not be created.");
				return false;
			}
		}

		UIKRigController* Controller = UIKRigController::GetController(Rig);
		if (Controller == nullptr)
		{
			OutErrorCode = TEXT("retarget_skeleton_invalid");
			OutErrorMessage = TEXT("The IK Rig Controller is unavailable for the target asset.");
			return false;
		}
		if (!Controller->SetSkeletalMesh(Mesh))
		{
			OutErrorCode = TEXT("retarget_skeleton_invalid");
			OutErrorMessage = TEXT("SetSkeletalMesh failed for the IK Rig.");
			return false;
		}

		// Compare the existing configuration to detect no-op / update / conflict.
		const FName ExistingRoot = Controller->GetRetargetRoot();
		OutChange.AssetPath = Rig->GetOutermost()->GetName() + TEXT(".") + Rig->GetName();
		if (bHasExisting && !bUpdateExisting && ExistingRoot != RootBoneName)
		{
			OutErrorCode = TEXT("retarget_asset_conflict");
			OutErrorMessage = FString::Printf(
				TEXT("Existing IK Rig root %s differs from the Plan root %s; updateExisting is required to change it."),
				*ExistingRoot.ToString(),
				*RootBoneName.ToString());
			return false;
		}

		const FScopedTransaction Transaction(TEXT("UEAgentKitRetarget"), FText::FromString(TEXT("Retarget IK Rig Setup")), Rig);
		bool bChanged = false;
		if (ExistingRoot != RootBoneName)
		{
			Controller->SetRetargetRoot(RootBoneName);
			bChanged = true;
		}
		for (const FRetargetPlanChain& Chain : Chains)
		{
			FName ExistingStart;
			FName ExistingEnd;
			if (IsChainInRig(Rig, Chain.ChainName, ExistingStart, ExistingEnd))
			{
				const bool bSameBones = ExistingStart == FName(*Chain.StartBone) && ExistingEnd == FName(*Chain.EndBone);
				if (bSameBones)
				{
					continue;
				}
				if (!bUpdateExisting)
				{
					OutErrorCode = TEXT("retarget_asset_conflict");
					OutErrorMessage = FString::Printf(
						TEXT("Chain %s exists with different bones and updateExisting is not enabled."),
						*Chain.ChainName);
					return false;
				}
				Controller->RemoveRetargetChain(FName(*Chain.ChainName));
				Controller->AddRetargetChain(FName(*Chain.ChainName), FName(*Chain.StartBone), FName(*Chain.EndBone), NAME_None);
				bChanged = true;
			}
			else
			{
				Controller->AddRetargetChain(FName(*Chain.ChainName), FName(*Chain.StartBone), FName(*Chain.EndBone), NAME_None);
				bChanged = true;
			}
			OutChange.Details.Add(FString::Printf(
				TEXT("%s %s (%s..%s)"),
				Chain.Required == ERetargetChainRequired::Required ? TEXT("required") : TEXT("optional"),
				*Chain.ChainName,
				*Chain.StartBone,
				*Chain.EndBone));
		}
		OutChange.Action = bHasExisting ? (bChanged ? TEXT("update") : TEXT("no_op")) : TEXT("create");
		return true;
	}

	namespace
	{
		bool ChainExistsInRig(const UIKRigDefinition* Rig, const FString& ChainName)
		{
			if (Rig == nullptr)
			{
				return false;
			}
			const UIKRigController* Controller = UIKRigController::GetController(Rig);
			if (Controller == nullptr)
			{
				return false;
			}
			for (const FBoneChain& Chain : Controller->GetRetargetChains())
			{
				if (Chain.ChainName.ToString() == ChainName)
				{
					return true;
				}
			}
			return false;
		}

		FString ObjectPath(const UObject* Object)
		{
			return Object == nullptr ? FString() : FString::Printf(TEXT("%s.%s"), *Object->GetOutermost()->GetName(), *Object->GetName());
		}

		// The FK Chains op owns the explicit chain mapping table in UE 5.6.
		// The retargeter op stack is only exposed as a const array even though the
		// underlying instanced ops are mutable via the controller, so iterate a
		// const_cast view to reach the mapping table.
		FRetargetChainMapping* GetFKChainsMapping(UIKRetargeter* Retargeter)
		{
			if (Retargeter == nullptr)
			{
				return nullptr;
			}
			TArray<FInstancedStruct>& Ops = const_cast<TArray<FInstancedStruct>&>(Retargeter->GetRetargetOps());
			for (FInstancedStruct& Op : Ops)
			{
				if (FIKRetargetFKChainsOp* FKOp = Op.GetMutablePtr<FIKRetargetFKChainsOp>())
				{
					return FKOp->GetChainMapping();
				}
			}
			return nullptr;
		}

		bool QuatIsFinite(const FQuat& Q)
		{
			return FMath::IsFinite(Q.W) && FMath::IsFinite(Q.X) && FMath::IsFinite(Q.Y) && FMath::IsFinite(Q.Z);
		}

		bool PoseContentMatches(const FIKRetargetPose& Existing, const FRetargetPoseConfig& Pose)
		{
			if (Existing.GetRootTranslationDelta() != Pose.RootTranslationOffset)
			{
				return false;
			}
			const TMap<FName, FQuat>& Offsets = Existing.GetAllDeltaRotations();
			if (Offsets.Num() != Pose.BoneRotationOffsets.Num())
			{
				return false;
			}
			for (const FRetargetPoseBoneRotation& Bone : Pose.BoneRotationOffsets)
			{
				const FQuat* Found = Offsets.Find(FName(*Bone.BoneName));
				if (Found == nullptr || !Found->Equals(Bone.RotationOffset, 1e-3f))
				{
					return false;
				}
			}
			return true;
		}

		bool RetargeterPoseMatchesPlan(UIKRetargeterController* Controller, const FRetargetPoseConfig& Pose)
		{
			if (Pose.PoseName.IsEmpty())
			{
				return true;
			}
			const FName PoseName(*Pose.PoseName);
			const TMap<FName, FIKRetargetPose>& Poses = Controller->GetRetargetPoses(ERetargetSourceOrTarget::Target);
			const FIKRetargetPose* Found = Poses.Find(PoseName);
			return Found != nullptr && PoseContentMatches(*Found, Pose);
		}

		bool MappingMatchesPlan(const FRetargetChainMapping* Mapping, const TArray<FRetargetChainMappingItem>& Mappings)
		{
			if (Mapping == nullptr)
			{
				return Mappings.Num() == 0;
			}
			for (const FRetargetChainMappingItem& Item : Mappings)
			{
				const FName MappedSource = Mapping->GetChainMappedTo(FName(*Item.TargetChainName), ERetargetSourceOrTarget::Target);
				if (MappedSource != FName(*Item.SourceChainName))
				{
					return false;
				}
			}
			return Mapping->GetChainPairs().Num() == Mappings.Num();
		}

		bool ApplyPoseOffsets(
			UIKRetargeterController* Controller,
			const FRetargetPoseConfig& Pose,
			bool bAllowLargePoseOffset,
			FString& OutErrorCode,
			FString& OutErrorMessage)
		{
			Controller->SetRootOffsetInRetargetPose(Pose.RootTranslationOffset, ERetargetSourceOrTarget::Target);
			for (const FRetargetPoseBoneRotation& Bone : Pose.BoneRotationOffsets)
			{
				const FQuat& Rotation = Bone.RotationOffset;
				if (!QuatIsFinite(Rotation) || Rotation.ContainsNaN())
				{
					OutErrorCode = TEXT("retarget_pose_invalid");
					OutErrorMessage = FString::Printf(TEXT("Bone %s has a non-finite retarget pose rotation."), *Bone.BoneName);
					return false;
				}
				const FQuat Normalized = Rotation.GetNormalized();
				if (Normalized.ContainsNaN())
				{
					OutErrorCode = TEXT("retarget_pose_invalid");
					OutErrorMessage = FString::Printf(TEXT("Bone %s retarget pose rotation could not be normalized."), *Bone.BoneName);
					return false;
				}
				if (Normalized.GetAngle() > FMath::DegreesToRadians(120.0f) && !bAllowLargePoseOffset)
				{
					OutErrorCode = TEXT("retarget_pose_invalid");
					OutErrorMessage = FString::Printf(
						TEXT("Bone %s retarget pose rotation exceeds 120 degrees; allowLargePoseOffset is required."),
						*Bone.BoneName);
					return false;
				}
				Controller->SetRotationOffsetForRetargetPoseBone(FName(*Bone.BoneName), Normalized, ERetargetSourceOrTarget::Target);
			}
			return true;
		}
	}

	bool FindRetargeterForRigs(const UIKRigDefinition* SourceRig, const UIKRigDefinition* TargetRig, FString& OutAssetPath)
	{
		OutAssetPath.Empty();
		if (SourceRig == nullptr || TargetRig == nullptr)
		{
			return false;
		}
		const FString SourcePath = ObjectPath(SourceRig);
		const FString TargetPath = ObjectPath(TargetRig);
		UIKRetargeter* Found = nullptr;
		auto MatchesRigs = [&SourcePath, &TargetPath](const UIKRetargeter* Retargeter)
		{
			return Retargeter != nullptr
				&& ObjectPath(Retargeter->GetIKRig(ERetargetSourceOrTarget::Source)) == SourcePath
				&& ObjectPath(Retargeter->GetIKRig(ERetargetSourceOrTarget::Target)) == TargetPath;
		};
		// In-memory assets first: a retargeter created but not yet saved is not
		// visible to the on-disk Asset Registry.
		ForEachObjectOfClass(
			UIKRetargeter::StaticClass(),
			[&Found, &MatchesRigs](UObject* Object)
			{
				if (Found != nullptr)
				{
					return;
				}
				if (MatchesRigs(Cast<UIKRetargeter>(Object)))
				{
					Found = Cast<UIKRetargeter>(Object);
				}
			},
			false,
			RF_NoFlags,
			EInternalObjectFlags::Garbage);
		if (Found == nullptr)
		{
			const FAssetRegistryModule& AssetRegistryModule =
				FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry"));
			FARFilter Filter;
			Filter.ClassPaths.Add(UIKRetargeter::StaticClass()->GetClassPathName());
			Filter.PackagePaths.Add(FName(TEXT("/Game")));
			Filter.bRecursivePaths = true;
			TArray<FAssetData> Assets;
			AssetRegistryModule.Get().GetAssets(Filter, Assets);
			for (const FAssetData& Asset : Assets)
			{
				UIKRetargeter* Retargeter = Cast<UIKRetargeter>(Asset.GetAsset());
				if (MatchesRigs(Retargeter))
				{
					Found = Retargeter;
					break;
				}
			}
		}
		if (Found != nullptr)
		{
			OutAssetPath = ObjectPath(Found);
			return true;
		}
		return false;
	}

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
		FString& OutErrorMessage)
	{
		OutResult = FRetargeterSetupResult();
		if (SourceRig == nullptr || TargetRig == nullptr)
		{
			OutErrorCode = TEXT("retarget_asset_not_found");
			OutErrorMessage = TEXT("The source and target IK Rigs must exist before the IK Retargeter is configured.");
			return false;
		}

		// Validate the explicit mappings against the rig chains before any write.
		FRetargetMappingReport Report;
		TArray<FString> SeenTargetChains;
		for (const FRetargetChainMappingItem& Item : Mappings)
		{
			if (SeenTargetChains.Contains(Item.TargetChainName))
			{
				Report.DuplicateMappings.Add(Item.TargetChainName);
				continue;
			}
			SeenTargetChains.Add(Item.TargetChainName);
			const bool bTargetExists = ChainExistsInRig(TargetRig, Item.TargetChainName);
			const bool bSourceExists = ChainExistsInRig(SourceRig, Item.SourceChainName);
			if (!bTargetExists)
			{
				Report.UnmappedTargetChains.Add(Item.TargetChainName);
				continue;
			}
			if (!bSourceExists)
			{
				Report.UnmappedSourceChains.Add(Item.SourceChainName);
				continue;
			}
			if (Item.Required == ERetargetChainRequired::Required)
			{
				Report.MappedRequiredChains.Add(Item.TargetChainName);
			}
			else
			{
				Report.MappedOptionalChains.Add(Item.TargetChainName);
			}
		}
		for (const FRetargetChainMappingItem& Item : Mappings)
		{
			if (Item.Required == ERetargetChainRequired::Required
				&& (Report.UnmappedTargetChains.Contains(Item.TargetChainName)
					|| Report.UnmappedSourceChains.Contains(Item.SourceChainName)
					|| Report.DuplicateMappings.Contains(Item.TargetChainName)))
			{
				OutErrorCode = TEXT("retarget_mapping_incomplete");
				OutErrorMessage = FString::Printf(
					TEXT("Required chain %s could not be mapped between the IK Rigs."), *Item.TargetChainName);
				return false;
			}
		}

		FString ExistingPath;
		const bool bHasExisting = FindRetargeterForRigs(SourceRig, TargetRig, ExistingPath);
		UIKRetargeter* Retargeter = nullptr;
		if (bHasExisting)
		{
			Retargeter = Cast<UIKRetargeter>(LoadObject<UIKRetargeter>(nullptr, *ExistingPath));
			if (Retargeter == nullptr)
			{
				OutErrorCode = TEXT("retarget_asset_not_found");
				OutErrorMessage = TEXT("The existing IK Retargeter could not be loaded.");
				return false;
			}
		}
		else
		{
			if (!bAllowCreate)
			{
				OutErrorCode = TEXT("retarget_asset_conflict");
				OutErrorMessage = TEXT("No IK Retargeter exists for these IK Rigs and creation is not enabled in the Plan.");
				return false;
			}
			if (DesiredAssetName.IsEmpty())
			{
				OutErrorCode = TEXT("retarget_asset_conflict");
				OutErrorMessage = TEXT("The Plan did not specify an IK Retargeter asset name.");
				return false;
			}
			const FString PackagePath = FPackageName::GetLongPackagePath(SourceRig->GetOutermost()->GetName());
			UIKRetargetFactory* Factory = NewObject<UIKRetargetFactory>();
			Retargeter = Cast<UIKRetargeter>(IAssetTools::Get().CreateAsset(
				*DesiredAssetName,
				*PackagePath,
				UIKRetargeter::StaticClass(),
				Factory));
			if (Retargeter == nullptr)
			{
				OutErrorCode = TEXT("retarget_asset_conflict");
				OutErrorMessage = TEXT("The IK Retargeter asset could not be created.");
				return false;
			}
		}

		UIKRetargeterController* Controller = UIKRetargeterController::GetController(Retargeter);
		if (Controller == nullptr)
		{
			OutErrorCode = TEXT("retarget_skeleton_invalid");
			OutErrorMessage = TEXT("The IK Retargeter Controller is unavailable for the target asset.");
			return false;
		}

		// Detect no-op / conflict against the existing retargeter before writing.
		FRetargetChainMapping* ExistingMapping = GetFKChainsMapping(Retargeter);
		const bool bMappingsMatch = MappingMatchesPlan(ExistingMapping, Mappings);
		const bool bPoseMatches = RetargeterPoseMatchesPlan(Controller, Pose);
		const bool bPreviewMatches =
			(SourceMesh == nullptr || Controller->GetPreviewMesh(ERetargetSourceOrTarget::Source) == SourceMesh)
			&& (TargetMesh == nullptr || Controller->GetPreviewMesh(ERetargetSourceOrTarget::Target) == TargetMesh);
		if (bHasExisting && !bUpdateExisting && (!bMappingsMatch || !bPoseMatches || !bPreviewMatches))
		{
			OutErrorCode = TEXT("retarget_asset_conflict");
			OutErrorMessage = TEXT("The existing IK Retargeter differs from the Plan; updateExisting is required to change it.");
			return false;
		}
		const bool bNeedsChange = !bHasExisting || !bMappingsMatch || !bPoseMatches || !bPreviewMatches;

		if (bNeedsChange)
		{
			const FScopedTransaction Transaction(
				TEXT("UEAgentKitRetarget"),
				FText::FromString(TEXT("Retarget IK Retargeter Setup")),
				Retargeter);
			Controller->SetIKRig(ERetargetSourceOrTarget::Source, SourceRig);
			Controller->SetIKRig(ERetargetSourceOrTarget::Target, TargetRig);
			if (SourceMesh != nullptr)
			{
				Controller->SetPreviewMesh(ERetargetSourceOrTarget::Source, SourceMesh);
			}
			if (TargetMesh != nullptr)
			{
				Controller->SetPreviewMesh(ERetargetSourceOrTarget::Target, TargetMesh);
			}
			if (GetFKChainsMapping(Retargeter) == nullptr)
			{
				Controller->AddDefaultOps();
			}
			if (FRetargetChainMapping* Mapping = GetFKChainsMapping(Retargeter))
			{
				for (const FRetargetChainMappingItem& Item : Mappings)
				{
					Mapping->SetChainMapping(FName(*Item.TargetChainName), FName(*Item.SourceChainName));
				}
			}
			if (!Pose.PoseName.IsEmpty())
			{
				const FName PoseName(*Pose.PoseName);
				TMap<FName, FIKRetargetPose>& Poses = Controller->GetRetargetPoses(ERetargetSourceOrTarget::Target);
				if (!Poses.Contains(PoseName))
				{
					Controller->CreateRetargetPose(PoseName, ERetargetSourceOrTarget::Target);
				}
				Controller->SetCurrentRetargetPose(PoseName, ERetargetSourceOrTarget::Target);
				if (!ApplyPoseOffsets(Controller, Pose, bAllowLargePoseOffset, OutErrorCode, OutErrorMessage))
				{
					return false;
				}
				OutResult.PoseName = Pose.PoseName;
				OutResult.bPoseApplied = true;
			}
		}

		OutResult.Change.AssetPath = ObjectPath(Retargeter);
		OutResult.Change.Action = bHasExisting ? (bNeedsChange ? TEXT("update") : TEXT("no_op")) : TEXT("create");
		if (bNeedsChange)
		{
			for (const FString& Chain : Report.MappedRequiredChains)
			{
				OutResult.Change.Details.Add(TEXT("required ") + Chain);
			}
			for (const FString& Chain : Report.MappedOptionalChains)
			{
				OutResult.Change.Details.Add(TEXT("optional ") + Chain);
			}
			if (OutResult.bPoseApplied)
			{
				OutResult.Change.Details.Add(TEXT("pose ") + OutResult.PoseName);
			}
		}
		OutResult.Mapping = MoveTemp(Report);
		return true;
	}

	TSharedRef<FJsonObject> MappingReportToJson(const FRetargetMappingReport& Report)
	{
		TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
		auto ArrayOf = [](const TArray<FString>& Values)
		{
			TArray<TSharedPtr<FJsonValue>> Result;
			for (const FString& Value : Values)
			{
				Result.Add(MakeShared<FJsonValueString>(Value));
			}
			return Result;
		};
		Json->SetArrayField(TEXT("mappedRequiredChains"), ArrayOf(Report.MappedRequiredChains));
		Json->SetArrayField(TEXT("mappedOptionalChains"), ArrayOf(Report.MappedOptionalChains));
		Json->SetArrayField(TEXT("unmappedSourceChains"), ArrayOf(Report.UnmappedSourceChains));
		Json->SetArrayField(TEXT("unmappedTargetChains"), ArrayOf(Report.UnmappedTargetChains));
		Json->SetArrayField(TEXT("duplicateMappings"), ArrayOf(Report.DuplicateMappings));
		Json->SetNumberField(TEXT("mappingConfidence"), Report.MappingConfidence);
		return Json;
	}

	TSharedRef<FJsonObject> PoseConfigToJson(const FRetargetPoseConfig& Pose)
	{
		TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
		Json->SetStringField(TEXT("poseName"), Pose.PoseName);
		TArray<TSharedPtr<FJsonValue>> Root;
		Root.Add(MakeShared<FJsonValueNumber>(Pose.RootTranslationOffset.X));
		Root.Add(MakeShared<FJsonValueNumber>(Pose.RootTranslationOffset.Y));
		Root.Add(MakeShared<FJsonValueNumber>(Pose.RootTranslationOffset.Z));
		Json->SetArrayField(TEXT("rootTranslationOffset"), Root);
		TArray<TSharedPtr<FJsonValue>> BoneOffsets;
		for (const FRetargetPoseBoneRotation& Bone : Pose.BoneRotationOffsets)
		{
			TSharedRef<FJsonObject> BoneJson = MakeShared<FJsonObject>();
			BoneJson->SetStringField(TEXT("bone"), Bone.BoneName);
			TArray<TSharedPtr<FJsonValue>> Rotation;
			Rotation.Add(MakeShared<FJsonValueNumber>(Bone.RotationOffset.W));
			Rotation.Add(MakeShared<FJsonValueNumber>(Bone.RotationOffset.X));
			Rotation.Add(MakeShared<FJsonValueNumber>(Bone.RotationOffset.Y));
			Rotation.Add(MakeShared<FJsonValueNumber>(Bone.RotationOffset.Z));
			BoneJson->SetArrayField(TEXT("rotation"), Rotation);
			BoneOffsets.Add(MakeShared<FJsonValueObject>(BoneJson));
		}
		Json->SetArrayField(TEXT("boneRotationOffsets"), BoneOffsets);
		return Json;
	}

	TSharedRef<FJsonObject> PlanChainToJson(const FRetargetPlanChain& Chain)
	{
		TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
		Json->SetStringField(TEXT("chain"), Chain.ChainName);
		Json->SetStringField(TEXT("required"), ChainRequiredToName(Chain.Required).ToString());
		Json->SetStringField(TEXT("side"), Chain.Side == ERetargetChainSide::Left ? TEXT("Left") : (Chain.Side == ERetargetChainSide::Right ? TEXT("Right") : TEXT("Center")));
		Json->SetStringField(TEXT("startBone"), Chain.StartBone);
		Json->SetStringField(TEXT("endBone"), Chain.EndBone);
		return Json;
	}

	TSharedRef<FJsonObject> IKRigStateToJson(const FRetargetIKRigState& State)
	{
		TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
		if (State.AssetPath.IsEmpty())
		{
			Json->SetBoolField(TEXT("exists"), false);
			return Json;
		}
		Json->SetBoolField(TEXT("exists"), true);
		Json->SetStringField(TEXT("assetPath"), State.AssetPath);
		Json->SetStringField(TEXT("retargetRoot"), State.RetargetRoot);
		TArray<TSharedPtr<FJsonValue>> ChainNames;
		for (const FString& ChainName : State.ChainNames)
		{
			ChainNames.Add(MakeShared<FJsonValueString>(ChainName));
		}
		Json->SetArrayField(TEXT("chainNames"), ChainNames);
		return Json;
	}

	TSharedRef<FJsonObject> AssetChangeToJson(const FRetargetAssetChange& Change)
	{
		TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
		Json->SetStringField(TEXT("assetPath"), Change.AssetPath);
		Json->SetStringField(TEXT("action"), Change.Action);
		TArray<TSharedPtr<FJsonValue>> Details;
		for (const FString& Detail : Change.Details)
		{
			Details.Add(MakeShared<FJsonValueString>(Detail));
		}
		Json->SetArrayField(TEXT("details"), Details);
		return Json;
	}
}
