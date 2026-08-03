#include "RetargetSpikeCommandlet.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetToolsModule.h"
#include "Dom/JsonObject.h"
#include "Animation/AnimSequence.h"
#include "Engine/SkeletalMesh.h"
#include "Animation/Skeleton.h"
#include "Factories/AnimSequenceFactory.h"
#include "Framework/Application/SlateApplication.h"
#include "HAL/PlatformFileManager.h"
#include "Misc/Paths.h"
#include "Rendering/SlateRenderer.h"
#include "Interfaces/ISlateRHIRendererModule.h"
#include "HAL/FileManager.h"
#include "IKRigEditor/Public/RetargetEditor/IKRetargetBatchOperation.h"
#include "IKRigEditor/Public/RetargetEditor/IKRetargeterController.h"
#include "IKRigEditor/Public/RigEditor/IKRigController.h"
#include "Misc/PackageName.h"
#include "Rig/IKRigDefinition.h"
#include "Retargeter/IKRetargeter.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "Subsystems/EditorAssetSubsystem.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"
#include "UObject/UObjectGlobals.h"

DEFINE_LOG_CATEGORY_STATIC(LogRetargetSpike, Log, All);

namespace
{
	constexpr const TCHAR* SpikeRoot = TEXT("/Game/UEAgentKitRetargetTests/Spike");
	constexpr const TCHAR* SourceMeshPath = TEXT("/Engine/EngineMeshes/SkeletalCube.SkeletalCube");
	constexpr const TCHAR* TargetMeshPath = TEXT("/Engine/EngineMeshes/SkeletalCube.SkeletalCube");

	struct FSpikeStep
	{
		FString Name;
		bool bOk = false;
		FString Detail;
	};

	FString LoadParam(const FString& Params, const FString& Key)
	{
		FString Value;
		if (FParse::Value(*Params, *Key, Value, false))
		{
			return Value;
		}
		return FString();
	}

	USkeletalMesh* LoadSkeletalMesh(const FString& ObjectPath, FString& OutError)
	{
		USkeletalMesh* Mesh = LoadObject<USkeletalMesh>(nullptr, *ObjectPath);
		if (Mesh == nullptr)
		{
			OutError = FString::Printf(TEXT("Could not load SkeletalMesh: %s"), *ObjectPath);
			return nullptr;
		}
		return Mesh;
	}

	void AppendBoneSummary(
		TSharedRef<FJsonObject> OutSkeletonJson,
		const FReferenceSkeleton& RefSkeleton,
		int32 MaxBones)
	{
		const int32 BoneCount = RefSkeleton.GetNum();
		OutSkeletonJson->SetNumberField(TEXT("boneCount"), BoneCount);
		OutSkeletonJson->SetStringField(TEXT("rootBone"), BoneCount > 0 ? RefSkeleton.GetBoneName(0).ToString() : FString());
		TArray<TSharedPtr<FJsonValue>> Bones;
		for (int32 Index = 0; Index < BoneCount && Index < MaxBones; ++Index)
		{
			const FName BoneName = RefSkeleton.GetBoneName(Index);
			const int32 ParentIndex = RefSkeleton.GetParentIndex(Index);
			const FTransform Local = RefSkeleton.GetRefBonePose()[Index];
			TSharedRef<FJsonObject> BoneJson = MakeShared<FJsonObject>();
			BoneJson->SetStringField(TEXT("name"), BoneName.ToString());
			BoneJson->SetNumberField(TEXT("index"), Index);
			BoneJson->SetNumberField(TEXT("parentIndex"), ParentIndex);
			BoneJson->SetStringField(TEXT("parent"), ParentIndex >= 0 ? RefSkeleton.GetBoneName(ParentIndex).ToString() : FString());
			BoneJson->SetNumberField(TEXT("depth"), RefSkeleton.GetDepthBetweenBones(0, Index));
			BoneJson->SetStringField(TEXT("position"), Local.GetLocation().ToString());
			Bones.Add(MakeShared<FJsonValueObject>(BoneJson));
		}
		OutSkeletonJson->SetArrayField(TEXT("bones"), Bones);
		OutSkeletonJson->SetBoolField(TEXT("truncated"), BoneCount > MaxBones);
	}

	FName FindBoneForChain(const FReferenceSkeleton& RefSkeleton, const TArray<FString>& PreferredNames)
	{
		for (const FString& Preferred : PreferredNames)
		{
			const FName BoneName(*Preferred);
			if (RefSkeleton.FindBoneIndex(BoneName) != INDEX_NONE)
			{
				return BoneName;
			}
		}
		return NAME_None;
	}

	// Finds the deepest descendant of the given bone (used to build a sample chain).
	FName FindDeepestDescendant(const FReferenceSkeleton& RefSkeleton, FName StartBone)
	{
		const int32 StartIndex = RefSkeleton.FindBoneIndex(StartBone);
		if (StartIndex == INDEX_NONE)
		{
			return NAME_None;
		}
		FName Deepest = StartBone;
		for (int32 Index = StartIndex + 1; Index < RefSkeleton.GetNum(); ++Index)
		{
			if (RefSkeleton.GetDepthBetweenBones(Index, StartIndex) >= 0)
			{
				Deepest = RefSkeleton.GetBoneName(Index);
			}
		}
		return Deepest;
	}

	UIKRigDefinition* CreateIKRigAsset(const FString& AssetName, USkeletalMesh* Mesh, FString& OutError)
	{
		IAssetTools& AssetTools = IAssetTools::Get();
		UIKRigDefinition* Rig = Cast<UIKRigDefinition>(
			AssetTools.CreateAsset(AssetName, SpikeRoot, UIKRigDefinition::StaticClass(), nullptr));
		if (Rig == nullptr)
		{
			OutError = TEXT("AssetTools could not create the UIKRigDefinition asset.");
			return nullptr;
		}
		UIKRigController* Controller = UIKRigController::GetController(Rig);
		if (Controller == nullptr)
		{
			OutError = TEXT("UIKRigController::GetController returned null.");
			return nullptr;
		}
		if (!Controller->SetSkeletalMesh(Mesh))
		{
			OutError = FString::Printf(TEXT("SetSkeletalMesh failed for %s."), *AssetName);
			return nullptr;
		}
		return Rig;
	}

	FName AddSampleChain(UIKRigDefinition* Rig, USkeletalMesh* Mesh)
	{
		const FReferenceSkeleton& RefSkeleton = Mesh->GetSkeleton()->GetReferenceSkeleton();
		UIKRigController* Controller = UIKRigController::GetController(Rig);
		const FName RootBone = RefSkeleton.GetBoneName(0);
		FName ChainStart = FindBoneForChain(RefSkeleton, {TEXT("pelvis"), TEXT("hips"), TEXT("root"), TEXT("Bone_01"), TEXT("Bone01")});
		if (ChainStart == NAME_None)
		{
			ChainStart = RootBone;
		}
		FName ChainEnd = FindDeepestDescendant(RefSkeleton, ChainStart);
		if (ChainEnd == NAME_None || ChainEnd == ChainStart)
		{
			return NAME_None;
		}
		Controller->SetRetargetRoot(RootBone);
		return Controller->AddRetargetChain(TEXT("SpikeChain"), ChainStart, ChainEnd, NAME_None);
	}
}

URetargetSpikeCommandlet::URetargetSpikeCommandlet()
{
	IsClient = false;
	IsEditor = true;
}

int32 URetargetSpikeCommandlet::Main(const FString& Params)
{
	// The batch retarget API notifies the Content Browser and the Editor
	// notification manager, which require a Slate application. A commandlet
	// session never creates one, so initialize a standalone Slate application
	// here to keep the official DuplicateAndRetarget path functional.
	if (!FSlateApplication::IsInitialized())
	{
		ISlateRHIRendererModule& SlateRendererModule =
			FModuleManager::Get().LoadModuleChecked<ISlateRHIRendererModule>(TEXT("SlateRHIRenderer"));
		const TSharedRef<FSlateRenderer> Renderer = SlateRendererModule.CreateSlateRHIRenderer();
		FSlateApplication::InitializeAsStandaloneApplication(Renderer);
	}

	const FString ReportPath = LoadParam(Params, TEXT("-Report="));
	TSharedRef<FJsonObject> Report = MakeShared<FJsonObject>();
	Report->SetStringField(TEXT("schemaVersion"), TEXT("1.0"));
	Report->SetStringField(TEXT("tool"), TEXT("retarget-api-spike"));
	Report->SetNumberField(TEXT("createdAtUtc"), 0.0);
	TArray<TSharedPtr<FJsonValue>> Steps;
	auto Step = [&Steps](const FString& Name, bool bOk, const FString& Detail)
	{
		TSharedRef<FJsonObject> StepJson = MakeShared<FJsonObject>();
		StepJson->SetStringField(TEXT("step"), Name);
		StepJson->SetBoolField(TEXT("ok"), bOk);
		StepJson->SetStringField(TEXT("detail"), Detail);
		Steps.Add(MakeShared<FJsonValueObject>(StepJson));
	};

	FString Error;
	USkeletalMesh* EngineSourceMesh = LoadSkeletalMesh(SourceMeshPath, Error);
	USkeletalMesh* SourceMesh = EngineSourceMesh;
	if (SourceMesh != nullptr)
	{
		// Duplicate the engine mesh into the spike fixture so the source and target
		// are distinct assets with the same bone chain.
		SourceMesh = Cast<USkeletalMesh>(
			IAssetTools::Get().DuplicateAsset(TEXT("SK_SpikeSource"), SpikeRoot, EngineSourceMesh));
	}
	if (SourceMesh == nullptr)
	{
		Step(TEXT("load-source-mesh"), false, Error.IsEmpty() ? TEXT("Could not duplicate the source SkeletalMesh.") : Error);
	}
	else
	{
		Step(TEXT("load-source-mesh"), true, SourceMesh->GetFullName());
	}
	USkeletalMesh* TargetMesh = LoadSkeletalMesh(TargetMeshPath, Error);
	if (TargetMesh == nullptr)
	{
		Step(TEXT("load-target-mesh"), false, Error);
	}
	else
	{
		Step(TEXT("load-target-mesh"), true, TargetMesh->GetFullName());
	}

	// Reference Skeleton summary.
	TSharedRef<FJsonObject> SourceSkeletonJson = MakeShared<FJsonObject>();
	TSharedRef<FJsonObject> TargetSkeletonJson = MakeShared<FJsonObject>();
	if (SourceMesh != nullptr && SourceMesh->GetSkeleton() != nullptr)
	{
		AppendBoneSummary(SourceSkeletonJson, SourceMesh->GetSkeleton()->GetReferenceSkeleton(), 512);
		Step(TEXT("source-skeleton-summary"), true, SourceSkeletonJson->GetStringField(TEXT("rootBone")));
	}
	else
	{
		Step(TEXT("source-skeleton-summary"), false, TEXT("Source skeleton is unavailable."));
	}
	if (TargetMesh != nullptr && TargetMesh->GetSkeleton() != nullptr)
	{
		AppendBoneSummary(TargetSkeletonJson, TargetMesh->GetSkeleton()->GetReferenceSkeleton(), 512);
		Step(TEXT("target-skeleton-summary"), true, TargetSkeletonJson->GetStringField(TEXT("rootBone")));
	}
	else
	{
		Step(TEXT("target-skeleton-summary"), false, TEXT("Target skeleton is unavailable."));
	}

	// Create the two IK Rigs.
	TArray<UObject*> CreatedAssets;
	UIKRigDefinition* SourceRig = nullptr;
	UIKRigDefinition* TargetRig = nullptr;
	if (SourceMesh != nullptr)
	{
		SourceRig = CreateIKRigAsset(TEXT("IKRig_SpikeSource"), SourceMesh, Error);
		if (SourceRig == nullptr)
		{
			Step(TEXT("create-source-ikrig"), false, Error);
		}
		else
		{
			CreatedAssets.Add(SourceRig);
			const FReferenceSkeleton& RefSkeleton = SourceMesh->GetSkeleton()->GetReferenceSkeleton();
			const FName RootBone = RefSkeleton.GetBoneName(0);
			const FName ChainName = AddSampleChain(SourceRig, SourceMesh);
			Step(
				TEXT("configure-source-ikrig"),
				ChainName != NAME_None,
				FString::Printf(TEXT("root=%s chain=%s"), *RootBone.ToString(), ChainName == NAME_None ? TEXT("?") : *ChainName.ToString()));
		}
	}
	if (TargetMesh != nullptr)
	{
		TargetRig = CreateIKRigAsset(TEXT("IKRig_SpikeTarget"), TargetMesh, Error);
		if (TargetRig == nullptr)
		{
			Step(TEXT("create-target-ikrig"), false, Error);
		}
		else
		{
			CreatedAssets.Add(TargetRig);
			const FReferenceSkeleton& RefSkeleton = TargetMesh->GetSkeleton()->GetReferenceSkeleton();
			const FName RootBone = RefSkeleton.GetBoneName(0);
			const FName ChainName = AddSampleChain(TargetRig, TargetMesh);
			Step(
				TEXT("configure-target-ikrig"),
				ChainName != NAME_None,
				FString::Printf(TEXT("root=%s chain=%s"), *RootBone.ToString(), ChainName == NAME_None ? TEXT("?") : *ChainName.ToString()));
		}
	}

	// Create the IK Retargeter and configure it through the official Controller.
	UIKRetargeter* Retargeter = nullptr;
	if (SourceRig != nullptr && TargetRig != nullptr)
	{
		Retargeter = Cast<UIKRetargeter>(
			IAssetTools::Get().CreateAsset(TEXT("RTG_Spike"), SpikeRoot, UIKRetargeter::StaticClass(), nullptr));
		if (Retargeter == nullptr)
		{
			Step(TEXT("create-retargeter"), false, TEXT("AssetTools could not create the UIKRetargeter asset."));
		}
		else
		{
			CreatedAssets.Add(Retargeter);
			UIKRetargeterController* Controller = UIKRetargeterController::GetController(Retargeter);
			Controller->SetIKRig(ERetargetSourceOrTarget::Source, SourceRig);
			Controller->SetIKRig(ERetargetSourceOrTarget::Target, TargetRig);
			Controller->SetPreviewMesh(ERetargetSourceOrTarget::Source, SourceMesh);
			Controller->SetPreviewMesh(ERetargetSourceOrTarget::Target, TargetMesh);
			Controller->AddDefaultOps();
			Controller->AutoMapChains(EAutoMapChainType::Fuzzy, false);
			const FRetargetChainMapping* ChainMapping = Controller->GetChainMapping(TEXT("Retarget FK Chains"));
			const int32 MappedChainCount = ChainMapping != nullptr ? ChainMapping->GetChainPairs().Num() : 0;
			Step(
				TEXT("retargeter-chain-mapping"),
				MappedChainCount > 0,
				FString::Printf(TEXT("mappedChainCount=%d"), MappedChainCount));
			const FName PoseName = Controller->CreateRetargetPose(TEXT("TargetPose_Spike"), ERetargetSourceOrTarget::Target);
			const bool bPoseSet = Controller->SetCurrentRetargetPose(PoseName, ERetargetSourceOrTarget::Target);
			Controller->SetRootOffsetInRetargetPose(FVector::ZeroVector, ERetargetSourceOrTarget::Target);
			const FReferenceSkeleton& TargetRefSkeleton = TargetMesh->GetSkeleton()->GetReferenceSkeleton();
			FName OffsetBone = FindBoneForChain(TargetRefSkeleton, {TEXT("pelvis"), TEXT("hips"), TEXT("root"), TEXT("Bone_01")});
			if (OffsetBone != NAME_None)
			{
				Controller->SetRotationOffsetForRetargetPoseBone(OffsetBone, FQuat::Identity, ERetargetSourceOrTarget::Target);
			}
			Step(
				TEXT("configure-retargeter"),
				PoseName != NAME_None && bPoseSet,
				FString::Printf(TEXT("pose=%s offsetBone=%s"), *PoseName.ToString(), OffsetBone == NAME_None ? TEXT("none") : *OffsetBone.ToString()));
		}
	}

	// Create a source AnimSequence bound to the source skeleton.
	UAnimSequence* SourceSequence = nullptr;
	if (SourceMesh != nullptr && SourceMesh->GetSkeleton() != nullptr)
	{
		UAnimSequenceFactory* Factory = NewObject<UAnimSequenceFactory>();
		Factory->TargetSkeleton = SourceMesh->GetSkeleton();
		SourceSequence = Cast<UAnimSequence>(
			IAssetTools::Get().CreateAsset(TEXT("AS_SpikeSource"), SpikeRoot, UAnimSequence::StaticClass(), Factory));
		if (SourceSequence == nullptr)
		{
			Step(TEXT("create-source-animsequence"), false, TEXT("AssetTools could not create the source AnimSequence."));
		}
		else
		{
			CreatedAssets.Add(SourceSequence);
			Step(TEXT("create-source-animsequence"), true, SourceSequence->GetFullName());
		}
	}

	// Run the official batch DuplicateAndRetarget.
	TArray<FAssetData> OutputAssets;
	if (SourceSequence != nullptr && SourceMesh != nullptr && TargetMesh != nullptr && Retargeter != nullptr)
	{
		TArray<FAssetData> AssetsToRetarget;
		AssetsToRetarget.Add(FAssetData(SourceSequence));
		OutputAssets = UIKRetargetBatchOperation::DuplicateAndRetarget(
			AssetsToRetarget,
			SourceMesh,
			TargetMesh,
			Retargeter,
			TEXT("SpikeSource"),
			TEXT("SpikeTarget"),
			TEXT(""),
			TEXT("_RTG"));
		if (OutputAssets.Num() == 1)
		{
			Step(TEXT("batch-retarget"), true, OutputAssets[0].GetObjectPathString());
		}
		else
		{
			Step(TEXT("batch-retarget"), false, FString::Printf(TEXT("Expected 1 output but got %d."), OutputAssets.Num()));
		}
	}

	// Save every newly created package, then reload and verify, then delete.
	bool bAllSaved = true;
	for (UObject* Asset : CreatedAssets)
	{
		if (Asset == nullptr)
		{
			continue;
		}
		UPackage* Package = Asset->GetOutermost();
		if (Package == nullptr || !Package->GetName().StartsWith(TEXT("/Game/UEAgentKitRetargetTests")))
		{
			bAllSaved = false;
			continue;
		}
		const FString Filename = FPackageName::LongPackageNameToFilename(Package->GetName(), FPackageName::GetAssetPackageExtension());
		FSavePackageArgs SaveArgs;
		SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
		if (!UPackage::SavePackage(Package, Asset, *Filename, SaveArgs))
		{
			bAllSaved = false;
		}
	}
	Step(TEXT("save-new-assets"), bAllSaved, FString::Printf(TEXT("packages=%d"), CreatedAssets.Num()));

	bool bReloaded = true;
	for (UObject* Asset : CreatedAssets)
	{
		const FString PackageName = Asset->GetOutermost()->GetName();
		UObject* Reloaded = StaticLoadObject(Asset->GetClass(), nullptr, *PackageName);
		if (Reloaded == nullptr)
		{
			bReloaded = false;
		}
	}
	Step(TEXT("reload-verify"), bReloaded, FString::Printf(TEXT("packages=%d"), CreatedAssets.Num()));

	bool bDeleted = true;
	{
		TArray<UObject*> AssetsToDelete;
		for (UObject* Asset : CreatedAssets)
		{
			AssetsToDelete.Add(Asset);
		}
		UEditorAssetSubsystem* EditorAssetSubsystem = GEditor != nullptr
			? GEditor->GetEditorSubsystem<UEditorAssetSubsystem>()
			: nullptr;
		if (EditorAssetSubsystem == nullptr || !EditorAssetSubsystem->DeleteLoadedAssets(AssetsToDelete))
		{
			bDeleted = false;
		}
	}
	if (bDeleted)
	{
		for (UObject* Asset : CreatedAssets)
		{
			const FString PackageName = Asset->GetOutermost()->GetName();
			if (StaticLoadObject(Asset->GetClass(), nullptr, *PackageName) != nullptr)
			{
				bDeleted = false;
			}
		}
	}
	Step(TEXT("delete-new-assets"), bDeleted, FString::Printf(TEXT("packages=%d"), CreatedAssets.Num()));

	Report->SetField(TEXT("sourceSkeleton"), MakeShared<FJsonValueObject>(SourceSkeletonJson));
	Report->SetField(TEXT("targetSkeleton"), MakeShared<FJsonValueObject>(TargetSkeletonJson));
	Report->SetArrayField(TEXT("steps"), Steps);
	bool bAllOk = Steps.Num() > 0;
	for (const TSharedPtr<FJsonValue>& Value : Steps)
	{
		const TSharedPtr<FJsonObject> StepObject = Value->AsObject();
		if (!StepObject.IsValid() || !StepObject->GetBoolField(TEXT("ok")))
		{
			bAllOk = false;
			break;
		}
	}
	Report->SetBoolField(TEXT("spikeSucceeded"), bAllOk);

	FString ReportText;
	const TSharedRef<TJsonWriter<TCHAR, TPrettyJsonPrintPolicy<TCHAR>>> Writer =
		TJsonWriterFactory<TCHAR, TPrettyJsonPrintPolicy<TCHAR>>::Create(&ReportText);
	FJsonSerializer::Serialize(Report, Writer);
	IFileManager::Get().MakeDirectory(*FPaths::GetPath(ReportPath), true);
	FFileHelper::SaveStringToFile(ReportText, *ReportPath, FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);

	UE_LOG(LogRetargetSpike, Display, TEXT("Retarget API Spike %s."), bAllOk ? TEXT("SUCCEEDED") : TEXT("FAILED"));
	return bAllOk ? 0 : 1;
}
