#pragma once

#include "AssetRegistry/AssetData.h"
#include "CoreMinimal.h"
#include "Dom/JsonObject.h"

enum class EAssetReaderStatus : uint8
{
	NotHandled,
	Success,
	Failed
};

class FAssetReaderRegistry
{
public:
	static EAssetReaderStatus ReadAssetDetails(
		const FAssetData& AssetData,
		TSharedRef<FJsonObject>& OutDetails,
		FString& OutReaderName,
		FString& OutError);

	static const TCHAR* StatusToString(EAssetReaderStatus Status);
};
