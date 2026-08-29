#pragma once

#include "Commandlets/Commandlet.h"
#include "PerformanceFixtureCommandlet.generated.h"

UCLASS()
class UEAGENTKITEDITOR_API UPerformanceFixtureCommandlet : public UCommandlet
{
	GENERATED_BODY()

public:
	UPerformanceFixtureCommandlet();

	virtual int32 Main(const FString& Params) override;
};
