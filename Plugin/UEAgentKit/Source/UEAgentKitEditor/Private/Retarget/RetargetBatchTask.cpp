#include "Retarget/RetargetTypes.h"

#include "Animation/AnimSequence.h"
#include "Animation/BlendSpace.h"
#include "Animation/AnimMontage.h"
#include "AssetToolsModule.h"
#include "EditorAnimUtils.h"
#include "Engine/SkeletalMesh.h"
#include "Misc/PackageName.h"
#include "Retargeter/IKRetargeter.h"
#include "RetargetEditor/IKRetargetBatchOperation.h"
#include "UObject/Package.h"

namespace UEAgentKitRetarget
{
	namespace
	{
		bool IsRetargetableAnimationAsset(UObject* Asset)
		{
			return Asset != nullptr
				&& (Asset->IsA<UAnimSequence>() || Asset->IsA<UAnimMontage>() || Asset->IsA<UBlendSpace>());
		}

		FString OutputObjectPath(
			const FString& InputObjectPath,
			const FString& OutputDirectory,
			const EditorAnimUtils::FNameDuplicationRule& NameRule)
		{
			const FString PackagePath = FPackageName::ObjectPathToPackageName(InputObjectPath);
			const FString Folder = OutputDirectory.IsEmpty()
				? FPackageName::GetLongPackagePath(PackagePath)
				: OutputDirectory;
			const FString ShortName = FPackageName::GetShortName(PackagePath);
			FString NewName = ShortName.Replace(*NameRule.ReplaceFrom, *NameRule.ReplaceTo);
			NewName = NameRule.Prefix + NewName + NameRule.Suffix;
			return Folder + TEXT("/") + NewName + TEXT(".") + NewName;
		}
	}

	bool RunRetargetBatchStep(
		const TArray<FString>& SourceAssetPaths,
		USkeletalMesh* SourceMesh,
		USkeletalMesh* TargetMesh,
		UIKRetargeter* Retargeter,
		const FString& OutputDirectory,
		const FRetargetBatchNaming& Naming,
		bool bOverwriteExisting,
		bool bIncludeReferencedAssets,
		bool bExportOnlyAnimatedBones,
		bool bRetainAdditiveFlags,
		TArray<FRetargetBatchOutputAsset>& OutOutputs,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		OutOutputs.Empty();
		if (SourceMesh == nullptr || TargetMesh == nullptr || Retargeter == nullptr)
		{
			OutErrorCode = TEXT("retarget_batch_invalid");
			OutErrorMessage = TEXT("The source mesh, target mesh and IK Retargeter are required for a batch retarget.");
			return false;
		}
		if (SourceMesh == TargetMesh)
		{
			OutErrorCode = TEXT("retarget_batch_invalid");
			OutErrorMessage = TEXT("The source and target meshes must be different assets.");
			return false;
		}
		if (Retargeter->GetIKRig(ERetargetSourceOrTarget::Source) == nullptr
			|| Retargeter->GetIKRig(ERetargetSourceOrTarget::Target) == nullptr)
		{
			OutErrorCode = TEXT("retarget_batch_invalid");
			OutErrorMessage = TEXT("The IK Retargeter must reference both a source and a target IK Rig.");
			return false;
		}
		if (!OutputDirectory.IsEmpty() && !OutputDirectory.StartsWith(TEXT("/Game")))
		{
			OutErrorCode = TEXT("retarget_output_path_denied");
			OutErrorMessage = TEXT("The batch output directory must be a /Game package path.");
			return false;
		}
		if (SourceAssetPaths.Num() > 100)
		{
			OutErrorCode = TEXT("retarget_batch_invalid");
			OutErrorMessage = TEXT("A batch retarget is limited to 100 source assets.");
			return false;
		}

		EditorAnimUtils::FNameDuplicationRule NameRule;
		NameRule.Prefix = Naming.Prefix;
		NameRule.Suffix = Naming.Suffix;
		NameRule.ReplaceFrom = Naming.Search;
		NameRule.ReplaceTo = Naming.Replace;
		NameRule.FolderPath = OutputDirectory.IsEmpty() ? TEXT("/Game") : OutputDirectory;

		// Load and validate the source assets before writing anything.
		TArray<UObject*> AssetsToRetarget;
		TSet<FString> OutputNames;
		TArray<FRetargetBatchOutputAsset> PredictedOutputs;
		for (const FString& SourcePath : SourceAssetPaths)
		{
			UObject* Asset = LoadObject<UObject>(nullptr, *SourcePath);
			if (!IsRetargetableAnimationAsset(Asset))
			{
				OutErrorCode = TEXT("retarget_batch_invalid");
				OutErrorMessage = FString::Printf(
					TEXT("Batch input %s is not an AnimSequence, AnimMontage or BlendSpace."), *SourcePath);
				return false;
			}
			if (USkeleton* Skeleton = Cast<UAnimationAsset>(Asset)->GetSkeleton())
			{
				if (Skeleton != SourceMesh->GetSkeleton())
				{
					OutErrorCode = TEXT("retarget_batch_invalid");
					OutErrorMessage = FString::Printf(
						TEXT("Batch input %s skeleton does not match the source mesh skeleton."), *SourcePath);
					return false;
				}
			}
			const FString Predicted = OutputObjectPath(SourcePath, OutputDirectory, NameRule);
			FString NameKey;
			FString PackageKey;
			Predicted.Split(TEXT("."), &PackageKey, &NameKey);
			if (OutputNames.Contains(NameKey))
			{
				OutErrorCode = TEXT("retarget_batch_invalid");
				OutErrorMessage = FString::Printf(
					TEXT("Batch naming rule produced duplicate output name %s."), *NameKey);
				return false;
			}
			OutputNames.Add(NameKey);
			AssetsToRetarget.Add(Asset);
			PredictedOutputs.Add({SourcePath, Predicted});
		}

		// Deny overwriting an existing output unless explicitly enabled. A batch
		// output that was just created lives only in memory, so check both the
		// in-memory object graph and the on-disk package registry.
		if (!bOverwriteExisting)
		{
			for (const FRetargetBatchOutputAsset& Output : PredictedOutputs)
			{
				const bool bInMemory = FindObject<UObject>(nullptr, *Output.OutputPath) != nullptr;
				const bool bOnDisk = FPackageName::DoesPackageExist(
					FPackageName::ObjectPathToPackageName(Output.OutputPath));
				if (bInMemory || bOnDisk)
				{
					OutErrorCode = TEXT("retarget_overwrite_denied");
					OutErrorMessage = FString::Printf(
						TEXT("Batch output %s already exists; overwriteExisting is required."), *Output.OutputPath);
					return false;
				}
			}
		}

		FIKRetargetBatchOperationContext Context;
		for (UObject* Asset : AssetsToRetarget)
		{
			Context.AssetsToRetarget.Add(Asset);
		}
		Context.SourceMesh = SourceMesh;
		Context.TargetMesh = TargetMesh;
		Context.IKRetargetAsset = Retargeter;
		Context.NameRule = NameRule;
		Context.bUseSourcePath = OutputDirectory.IsEmpty();
		Context.bOverwriteExistingFiles = bOverwriteExisting;
		Context.bIncludeReferencedAssets = bIncludeReferencedAssets;
		Context.bExportOnlyAnimatedBones = bExportOnlyAnimatedBones;
		Context.bRetainAdditiveFlags = bRetainAdditiveFlags;

		UIKRetargetBatchOperation* BatchOperation = NewObject<UIKRetargetBatchOperation>();
		BatchOperation->AddToRoot();
		BatchOperation->RunRetarget(Context);
		BatchOperation->RemoveFromRoot();

		// Verify each predicted output was created and reports the target skeleton.
		for (const FRetargetBatchOutputAsset& Output : PredictedOutputs)
		{
			UObject* Created = LoadObject<UObject>(nullptr, *Output.OutputPath);
			if (Created == nullptr)
			{
				OutErrorCode = TEXT("retarget_batch_invalid");
				OutErrorMessage = FString::Printf(
					TEXT("Batch retarget did not produce the expected output %s."), *Output.OutputPath);
				return false;
			}

			FRetargetBatchOutputAsset VerifiedOutput = Output;
			VerifiedOutput.AssetClass = Created->GetClass()->GetPathName();
			VerifiedOutput.AssetType = TEXT("Other");
			if (UAnimationAsset* CreatedAnim = Cast<UAnimationAsset>(Created))
			{
				if (CreatedAnim->GetSkeleton() != TargetMesh->GetSkeleton())
				{
					OutErrorCode = TEXT("retarget_batch_invalid");
					OutErrorMessage = FString::Printf(
						TEXT("Batch output %s was not retargeted to the target skeleton."), *Output.OutputPath);
					return false;
				}
				VerifiedOutput.SkeletonPath = CreatedAnim->GetSkeleton() != nullptr
					? CreatedAnim->GetSkeleton()->GetPathName()
					: FString();
				if (Created->IsA<UAnimSequence>())
				{
					VerifiedOutput.AssetType = TEXT("AnimSequence");
				}
				else if (Created->IsA<UAnimMontage>())
				{
					VerifiedOutput.AssetType = TEXT("AnimMontage");
				}
				else if (Created->IsA<UBlendSpace>())
				{
					VerifiedOutput.AssetType = Created->GetClass()->GetName().Contains(TEXT("AimOffset"))
						? TEXT("AimOffset")
						: TEXT("BlendSpace");
				}
				else
				{
					VerifiedOutput.AssetType = TEXT("AnimationAsset");
				}
			}
			OutOutputs.Add(MoveTemp(VerifiedOutput));
		}
		return true;
	}

	TSharedRef<FJsonObject> BatchOutputToJson(const FRetargetBatchOutputAsset& Output)
	{
		TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
		Json->SetStringField(TEXT("inputPath"), Output.InputPath);
		Json->SetStringField(TEXT("outputPath"), Output.OutputPath);
		Json->SetStringField(TEXT("assetClass"), Output.AssetClass);
		Json->SetStringField(TEXT("assetType"), Output.AssetType);
		Json->SetStringField(TEXT("skeletonPath"), Output.SkeletonPath);
		return Json;
	}
}
