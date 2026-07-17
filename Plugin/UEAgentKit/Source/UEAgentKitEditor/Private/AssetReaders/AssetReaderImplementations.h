#pragma once

#include "AssetReaders/AssetReaderRegistry.h"

namespace AssetReaderRegistryPrivate
{
	EAssetReaderStatus ReadStaticMesh(
		const FAssetData& AssetData,
		TSharedRef<FJsonObject>& OutDetails,
		FString& OutError);

	EAssetReaderStatus ReadSkeletalMesh(
		const FAssetData& AssetData,
		TSharedRef<FJsonObject>& OutDetails,
		FString& OutError);

	EAssetReaderStatus ReadSkeleton(
		const FAssetData& AssetData,
		TSharedRef<FJsonObject>& OutDetails,
		FString& OutError);

	EAssetReaderStatus ReadPhysicsAsset(
		const FAssetData& AssetData,
		TSharedRef<FJsonObject>& OutDetails,
		FString& OutError);

	EAssetReaderStatus ReadMaterialFunction(
		const FAssetData& AssetData,
		TSharedRef<FJsonObject>& OutDetails,
		FString& OutError);

	EAssetReaderStatus ReadMaterial(
		const FAssetData& AssetData,
		TSharedRef<FJsonObject>& OutDetails,
		FString& OutError);

	EAssetReaderStatus ReadMaterialInstance(
		const FAssetData& AssetData,
		TSharedRef<FJsonObject>& OutDetails,
		FString& OutError);

	EAssetReaderStatus ReadTexture2D(
		const FAssetData& AssetData,
		TSharedRef<FJsonObject>& OutDetails,
		FString& OutError);

	EAssetReaderStatus ReadAnimSequence(
		const FAssetData& AssetData,
		TSharedRef<FJsonObject>& OutDetails,
		FString& OutError);

	EAssetReaderStatus ReadAnimMontage(
		const FAssetData& AssetData,
		TSharedRef<FJsonObject>& OutDetails,
		FString& OutError);

	EAssetReaderStatus ReadBlendSpace(
		const FAssetData& AssetData,
		TSharedRef<FJsonObject>& OutDetails,
		FString& OutError);

	EAssetReaderStatus ReadDataTable(
		const FAssetData& AssetData,
		TSharedRef<FJsonObject>& OutDetails,
		FString& OutError);

	EAssetReaderStatus ReadDataAsset(
		const FAssetData& AssetData,
		TSharedRef<FJsonObject>& OutDetails,
		FString& OutError);

	EAssetReaderStatus ReadNiagaraSystem(
		const FAssetData& AssetData,
		TSharedRef<FJsonObject>& OutDetails,
		FString& OutError);

	EAssetReaderStatus ReadWorld(
		const FAssetData& AssetData,
		TSharedRef<FJsonObject>& OutDetails,
		FString& OutError);

}
