#pragma once

#include "Commandlets/Commandlet.h"
#include "ReflectionExportCommandlet.generated.h"

UCLASS()
class UEAGENTKITEDITOR_API UReflectionExportCommandlet : public UCommandlet
{
	GENERATED_BODY()

public:
	UReflectionExportCommandlet();

	virtual int32 Main(const FString& Params) override;
};
