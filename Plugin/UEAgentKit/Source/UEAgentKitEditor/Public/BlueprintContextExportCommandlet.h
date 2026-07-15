#pragma once

#include "Commandlets/Commandlet.h"
#include "BlueprintContextExportCommandlet.generated.h"

UCLASS()
class UEAGENTKITEDITOR_API UBlueprintContextExportCommandlet : public UCommandlet
{
	GENERATED_BODY()

public:
	UBlueprintContextExportCommandlet();

	virtual int32 Main(const FString& Params) override;
};
