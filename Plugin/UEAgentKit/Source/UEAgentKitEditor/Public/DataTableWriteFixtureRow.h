#pragma once

#include "Engine/DataTable.h"
#include "DataTableWriteFixtureRow.generated.h"

USTRUCT(BlueprintType)
struct UEAGENTKITEDITOR_API FUEAgentKitDataTableFixtureRow : public FTableRowBase
{
	GENERATED_BODY()

	UPROPERTY(EditAnywhere, Category = "DataTable")
	int32 Count = 0;

	UPROPERTY(EditAnywhere, Category = "DataTable")
	FString Label;

	UPROPERTY(EditAnywhere, Category = "DataTable")
	bool bEnabled = false;
};
