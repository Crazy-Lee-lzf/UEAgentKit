#pragma once

#include "Commandlets/Commandlet.h"
#include "AssetPatchCommandlet.generated.h"

UCLASS()
class UEAGENTKITEDITOR_API UAssetPatchCommandlet : public UCommandlet
{
	GENERATED_BODY()

public:
	UAssetPatchCommandlet();

	virtual int32 Main(const FString& Params) override;
};
