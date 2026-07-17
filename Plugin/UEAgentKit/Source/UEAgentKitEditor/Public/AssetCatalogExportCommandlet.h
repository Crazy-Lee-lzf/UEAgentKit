#pragma once

#include "Commandlets/Commandlet.h"
#include "AssetCatalogExportCommandlet.generated.h"

UCLASS()
class UEAGENTKITEDITOR_API UAssetCatalogExportCommandlet : public UCommandlet
{
	GENERATED_BODY()

public:
	UAssetCatalogExportCommandlet();

	virtual int32 Main(const FString& Params) override;
};
