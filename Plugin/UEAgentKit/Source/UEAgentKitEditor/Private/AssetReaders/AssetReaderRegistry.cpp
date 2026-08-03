#include "AssetReaders/AssetReaderRegistry.h"

#include "AssetReaders/AssetReaderCommon.h"
#include "AssetReaders/AssetReaderImplementations.h"

namespace AssetReaderRegistryPrivate
{
	using FReadAssetDetailsFunction = EAssetReaderStatus (*)(
		const FAssetData& AssetData,
		TSharedRef<FJsonObject>& OutDetails,
		FString& OutError);

	struct FAssetReaderBinding
	{
		const UClass* AssetClass = nullptr;
		const TCHAR* ReaderName = nullptr;
		FReadAssetDetailsFunction Read = nullptr;
		bool bIncludeDerivedClasses = false;
	};

	const TArray<FAssetReaderBinding>& GetAssetReaderBindings()
	{
		static const TArray<FAssetReaderBinding> Bindings = {
			{UStaticMesh::StaticClass(), TEXT("static-mesh-v1"), &ReadStaticMesh, false},
			{USkeletalMesh::StaticClass(), TEXT("skeletal-mesh-v1"), &ReadSkeletalMesh, false},
			{USkeleton::StaticClass(), TEXT("skeleton-v1"), &ReadSkeleton, false},
			{UPhysicsAsset::StaticClass(), TEXT("physics-asset-v1"), &ReadPhysicsAsset, false},
			{UMaterialFunction::StaticClass(), TEXT("material-function-v1"), &ReadMaterialFunction, false},
			{UMaterial::StaticClass(), TEXT("material-v1"), &ReadMaterial, false},
			{UMaterialInstanceConstant::StaticClass(), TEXT("material-instance-v1"), &ReadMaterialInstance, false},
			{UTexture2D::StaticClass(), TEXT("texture-2d-v1"), &ReadTexture2D, false},
			{UAnimSequence::StaticClass(), TEXT("anim-sequence-v1"), &ReadAnimSequence, false},
			{UAnimMontage::StaticClass(), TEXT("anim-montage-v1"), &ReadAnimMontage, false},
			{UBlendSpace::StaticClass(), TEXT("blend-space-v1"), &ReadBlendSpace, true},
			{UDataTable::StaticClass(), TEXT("data-table-v1"), &ReadDataTable, false},
			{UNiagaraSystem::StaticClass(), TEXT("niagara-system-v1"), &ReadNiagaraSystem, false},
			{UWorld::StaticClass(), TEXT("world-v1"), &ReadWorld, false},
			{UDataAsset::StaticClass(), TEXT("data-asset-v1"), &ReadDataAsset, true},
		};
		return Bindings;
	}
}

EAssetReaderStatus FAssetReaderRegistry::ReadAssetDetails(
	const FAssetData& AssetData,
	TSharedRef<FJsonObject>& OutDetails,
	FString& OutReaderName,
	FString& OutError)
{
	OutDetails = MakeShared<FJsonObject>();
	OutReaderName = TEXT("generic");
	OutError.Reset();

	const UClass* AssetClass = nullptr;
	for (const AssetReaderRegistryPrivate::FAssetReaderBinding& Binding : AssetReaderRegistryPrivate::GetAssetReaderBindings())
	{
		bool bMatches = AssetData.AssetClassPath == Binding.AssetClass->GetClassPathName();
		if (!bMatches && Binding.bIncludeDerivedClasses)
		{
			AssetClass = AssetClass != nullptr ? AssetClass : AssetData.GetClass();
			bMatches = AssetClass != nullptr && AssetClass->IsChildOf(Binding.AssetClass);
		}
		if (!bMatches)
		{
			continue;
		}

		OutReaderName = Binding.ReaderName;
		return Binding.Read(AssetData, OutDetails, OutError);
	}
	return EAssetReaderStatus::NotHandled;
}

const TCHAR* FAssetReaderRegistry::StatusToString(const EAssetReaderStatus Status)
{
	switch (Status)
	{
	case EAssetReaderStatus::Disabled:
		return TEXT("disabled");
	case EAssetReaderStatus::Success:
		return TEXT("success");
	case EAssetReaderStatus::Failed:
		return TEXT("failed");
	default:
		return TEXT("not-handled");
	}
}
