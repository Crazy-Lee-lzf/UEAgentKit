#pragma once

#include "Engine/DataAsset.h"
#include "ReferenceWriteFixtureAsset.generated.h"

class AActor;
class UTexture2D;

UCLASS(BlueprintType)
class UEAGENTKITEDITOR_API UUEAgentKitReferenceWriteFixtureAsset : public UDataAsset
{
	GENERATED_BODY()

public:
	UUEAgentKitReferenceWriteFixtureAsset();

	UPROPERTY(EditAnywhere, Category = "Reference")
	TObjectPtr<UTexture2D> ObjectValue;

	UPROPERTY(EditAnywhere, Category = "Reference")
	TSubclassOf<AActor> ClassValue;

	UPROPERTY(EditAnywhere, Category = "Reference")
	TSoftObjectPtr<UTexture2D> SoftObjectValue;

	UPROPERTY(EditAnywhere, Category = "Reference")
	TSoftClassPtr<AActor> SoftClassValue;
};
