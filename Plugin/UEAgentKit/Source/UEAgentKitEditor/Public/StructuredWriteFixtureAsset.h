#pragma once

#include "Engine/DataAsset.h"
#include "StructuredWriteFixtureAsset.generated.h"

USTRUCT(BlueprintType)
struct UEAGENTKITEDITOR_API FUEAgentKitStructuredFixtureRecord
{
	GENERATED_BODY()

	UPROPERTY(EditAnywhere, Category = "Structured")
	int32 Count = 0;

	UPROPERTY(EditAnywhere, Category = "Structured")
	FString Label;

	UPROPERTY(EditAnywhere, Category = "Structured")
	bool bEnabled = false;
};

UCLASS(BlueprintType)
class UEAGENTKITEDITOR_API UUEAgentKitStructuredWriteFixtureAsset : public UDataAsset
{
	GENERATED_BODY()

public:
	UUEAgentKitStructuredWriteFixtureAsset();

	UPROPERTY(EditAnywhere, Category = "Structured")
	FUEAgentKitStructuredFixtureRecord StructValue;

	UPROPERTY(EditAnywhere, Category = "Structured")
	TArray<int32> ArrayValue;

	UPROPERTY(EditAnywhere, Category = "Structured")
	TSet<FName> SetValue;

	UPROPERTY(EditAnywhere, Category = "Structured")
	TMap<FName, FUEAgentKitStructuredFixtureRecord> MapValue;

	UPROPERTY(EditAnywhere, Category = "Structured")
	int32 FixedArrayValue[3] = {0, 0, 0};
};
