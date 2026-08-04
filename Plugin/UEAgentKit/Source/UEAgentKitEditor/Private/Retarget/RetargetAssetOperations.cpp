#include "Retarget/RetargetTypes.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetToolsModule.h"
#include "Engine/SkeletalMesh.h"
#include "Rig/IKRigDefinition.h"
#include "IKRigEditor/Public/RigEditor/IKRigController.h"
#include "Misc/PackageName.h"
#include "ScopedTransaction.h"
#include "UObject/Package.h"

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
