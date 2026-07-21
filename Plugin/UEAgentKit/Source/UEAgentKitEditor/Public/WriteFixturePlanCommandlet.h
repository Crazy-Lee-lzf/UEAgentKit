#pragma once

#include "Commandlets/Commandlet.h"
#include "WriteFixturePlanCommandlet.generated.h"

UCLASS()
class UEAGENTKITEDITOR_API UWriteFixturePlanCommandlet : public UCommandlet
{
	GENERATED_BODY()

public:
	UWriteFixturePlanCommandlet();

	virtual int32 Main(const FString& Params) override;
};
