#pragma once

#include "Commandlets/Commandlet.h"
#include "BlueprintWriteFixtureCommandlet.generated.h"

UCLASS()
class UEAGENTKITEDITOR_API UBlueprintWriteFixtureCommandlet : public UCommandlet
{
	GENERATED_BODY()

public:
	UBlueprintWriteFixtureCommandlet();

	virtual int32 Main(const FString& Params) override;
};
