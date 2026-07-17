#pragma once

#include "Commandlets/Commandlet.h"
#include "BlueprintPatchCommandlet.generated.h"

UCLASS()
class UEAGENTKITEDITOR_API UBlueprintPatchCommandlet : public UCommandlet
{
	GENERATED_BODY()

public:
	UBlueprintPatchCommandlet();

	virtual int32 Main(const FString& Params) override;
};
